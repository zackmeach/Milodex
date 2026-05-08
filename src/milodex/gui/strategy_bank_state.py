"""Live strategy-bank model exposed to QML.

Queries the Milodex event store (SQLite) for the canonical strategy bank
state — which strategies have graduated to paper and which are blocked at
backtest — and exposes them as Q_PROPERTYs for the Strategy Bank surface
to render reactively.

This is the second instance of the OperationalState architectural pattern
established in :mod:`milodex.gui.operational_state`.  The threading model
is identical:

- A :class:`QTimer` fires every ``refresh_interval_ms`` (default 30 s) on
  the main thread.
- The timer schedules a :class:`QRunnable` on a *per-instance*
  :class:`QThreadPool` (``maxThreadCount=1``) that runs the SQL queries on
  a background thread.
- Results flow back to the main thread via signals connected with
  :class:`Qt.ConnectionType.QueuedConnection` so property updates and
  ``notify`` signals always run on the thread that owns the GUI bindings.
- :meth:`stop` drains in-flight workers via ``waitForDone(2000)`` and
  defensively disconnects signals to prevent teardown-race on Windows
  shutdown — the same contract OperationalState carries.

Tolerance
---------

- If ``data/milodex.db`` does not exist (fresh checkout, CI), the surface
  renders in ``dataStatus = "loading"`` → ``dataStatus = "error"`` with an
  explanatory message.  Lists stay empty; the surface shows the error banner.
- On transient SQLite failure after a successful load, ``dataStatus`` flips
  to ``"error"`` but the last-known paper/blocked lists are *preserved* so
  the operator still sees the previous snapshot.
- First attempt failure sets ``dataStatus = "error"``.  The object never
  starts in ``"error"`` — it always starts in ``"loading"`` and transitions
  on the first refresh attempt.

Constructor
-----------

``__init__(self, db_path: Path | None = None, refresh_interval_ms: int = 30000)``

``db_path`` defaults to the canonical ``data/milodex.db`` resolved via
:func:`milodex.config.get_data_dir`, which walks pyproject.toml upward from
the package root.  Tests inject a tmp-path for hermeticity.

SQL queries
-----------

The two SQL queries are copied verbatim from ``docs/STRATEGY_BANK.md``
lines 30–72.  If that document's queries change, the constants here must
be updated to match.  The cross-reference comment is deliberate.

Gate codes
----------

For each blocked row, ``gateFailures`` is a list of short codes per ADR 0009:

- ``"S"`` — Sharpe < 0.5 (:data:`~milodex.promotion.state_machine.MIN_SHARPE`)
- ``"D"`` — MaxDD >= 15% (:data:`~milodex.promotion.state_machine.MAX_DRAWDOWN_PCT`)
- ``"N"`` — trade count < 30 (:data:`~milodex.promotion.state_machine.MIN_TRADES`)

Constants are imported from :mod:`milodex.promotion.state_machine` rather
than duplicated here.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (  # pragma: no cover
    Property,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)

from milodex.promotion.state_machine import MAX_DRAWDOWN_PCT, MIN_SHARPE, MIN_TRADES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL — copied verbatim from docs/STRATEGY_BANK.md "How to refresh" section.
# If the doc's queries change, update these constants to match.
# ---------------------------------------------------------------------------

# Paper-stage strategies (the runnable list).
# Cross-reference: docs/STRATEGY_BANK.md lines 30–48.
_SQL_PAPER = """
SELECT p.strategy_id,
       p.recorded_at            AS promoted_at,
       p.backtest_run_id        AS evidence_run_id,
       p.promotion_type,
       p.sharpe_ratio,
       p.max_drawdown_pct,
       p.trade_count
FROM promotions p
INNER JOIN (
    SELECT strategy_id, MAX(id) AS max_id
    FROM promotions
    WHERE to_stage = 'paper'
    GROUP BY strategy_id
) latest ON p.strategy_id = latest.strategy_id AND p.id = latest.max_id
WHERE p.to_stage = 'paper'
ORDER BY p.recorded_at;
"""

# Backtest-stage strategies (blocked).
# Cross-reference: docs/STRATEGY_BANK.md lines 53–71.
_SQL_BLOCKED = """
SELECT br.strategy_id,
       br.run_id,
       br.started_at,
       json_extract(br.metadata_json, '$.oos_aggregate.sharpe')            AS wf_sharpe,
       json_extract(br.metadata_json, '$.oos_aggregate.max_drawdown_pct')  AS wf_max_dd,
       json_extract(br.metadata_json, '$.oos_aggregate.trade_count')       AS wf_trades
FROM backtest_runs br
INNER JOIN (
    SELECT strategy_id, MAX(id) AS max_id
    FROM backtest_runs
    WHERE status = 'completed'
    GROUP BY strategy_id
) latest ON br.strategy_id = latest.strategy_id AND br.id = latest.max_id
WHERE br.strategy_id NOT IN (
    SELECT strategy_id FROM promotions WHERE to_stage = 'paper'
)
AND br.status = 'completed'
ORDER BY br.strategy_id;
"""

# Strategy IDs that carry a manual audit flag (ADR 0032 audit trail).
# Static for now; will become a join against an audit_notes table when one exists.
_AUDIT_FLAGGED: frozenset[str] = frozenset(
    {
        "meanrev.daily.pullback_rsi2.curated_largecap.v1",
    }
)

# Strategies that are flagged-not-retired at backtest stage (see STRATEGY_BANK.md
# dual_absolute callout).  These carry both auditFlag and flagFailingNotRetired.
_FLAGGED_NOT_RETIRED: frozenset[str] = frozenset(
    {
        "momentum.daily.dual_absolute.gem_weekly.v1",
    }
)


# ---------------------------------------------------------------------------
# Gate-failure computation
# ---------------------------------------------------------------------------


def _compute_gate_failures(
    sharpe: float | None,
    max_dd: float | None,
    trade_count: int | None,
) -> list[str]:
    """Derive gate-failure codes from raw backtest metrics.

    Returns a list of short strings per ADR 0009:
    - ``"S"`` — Sharpe < MIN_SHARPE
    - ``"D"`` — MaxDD >= MAX_DRAWDOWN_PCT
    - ``"N"`` — trade count < MIN_TRADES
    """
    failures: list[str] = []
    if sharpe is None or sharpe < MIN_SHARPE:
        failures.append("S")
    if max_dd is None or max_dd >= MAX_DRAWDOWN_PCT:
        failures.append("D")
    if trade_count is None or trade_count < MIN_TRADES:
        failures.append("N")
    return failures


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class _BankRefreshSignals(QObject):
    """Signal carrier for the refresh worker.

    QRunnable cannot emit signals itself; a separate QObject owns the
    signals.  Parented to StrategyBankState so it lives as long as the
    polling lifecycle.
    """

    completed = Signal(dict)  # {"paper": [...], "blocked": [...], "refreshed_at": "..."}
    failed = Signal(str)  # error message on failure


class _BankRefreshRunnable(QRunnable):
    """One-shot DB refresh executed on a QThreadPool worker thread.

    Runs the two SQL queries against the SQLite DB, builds the paper and
    blocked lists, and emits results via the signal carrier.  Does not
    access any QObject state directly — all data flows back to the main
    thread via QueuedConnection.
    """

    def __init__(self, db_path: Path, signals: _BankRefreshSignals) -> None:
        super().__init__()
        self._db_path = db_path
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover — exercised via tests with fixture DBs
        try:
            paper, blocked = _query_bank(self._db_path)
            self._signals.completed.emit(
                {
                    "paper": paper,
                    "blocked": blocked,
                    "refreshed_at": datetime.now(tz=UTC).isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001 — SQLite errors vary
            logger.warning("StrategyBankState: DB refresh failed: %s", exc)
            self._signals.failed.emit(str(exc))


def _query_bank(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run both SQL queries and return (paper_list, blocked_list).

    Extracted as a module-level helper so it is testable without Qt.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        paper = _fetch_paper(conn)
        blocked = _fetch_blocked(conn)
    finally:
        conn.close()
    return paper, blocked


def _fetch_paper(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(_SQL_PAPER).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        strategy_id = row["strategy_id"]
        result.append(
            {
                "strategyId": strategy_id,
                "promotionType": row["promotion_type"] or "statistical",
                "sharpeRatio": row["sharpe_ratio"],
                "maxDrawdownPct": row["max_drawdown_pct"],
                "tradeCount": row["trade_count"] or 0,
                "promotedAt": row["promoted_at"] or "",
                "evidenceRunId": row["evidence_run_id"] or "",
                # ADR 0032 audit trail: static flag until an audit_notes table exists.
                "auditFlag": strategy_id in _AUDIT_FLAGGED,
            }
        )
    return result


def _fetch_blocked(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(_SQL_BLOCKED).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        strategy_id = row["strategy_id"]
        sharpe = row["wf_sharpe"]
        max_dd = row["wf_max_dd"]
        trade_count = row["wf_trades"]
        result.append(
            {
                "strategyId": strategy_id,
                "sharpeRatio": sharpe,
                "maxDrawdownPct": max_dd,
                "tradeCount": trade_count or 0,
                "gateFailures": _compute_gate_failures(sharpe, max_dd, trade_count),
                "startedAt": row["started_at"] or "",
                "runId": row["run_id"] or "",
                # ADR 0032 audit trail: static flag.
                "auditFlag": strategy_id in _AUDIT_FLAGGED,
                # flagFailingNotRetired: strategy is kept at backtest by governance
                # decision; the dual_absolute callout in STRATEGY_BANK.md explains why.
                "flagFailingNotRetired": strategy_id in _FLAGGED_NOT_RETIRED,
            }
        )
    return result


# ---------------------------------------------------------------------------
# StrategyBankState
# ---------------------------------------------------------------------------


class StrategyBankState(QObject):
    """Strategy bank state exposed to QML as Q_PROPERTYs.

    See module docstring for threading model, tolerance behaviour, and SQL
    query sourcing.
    """

    # Signals — camelCase per Qt convention.
    paperStrategiesChanged = Signal()  # noqa: N815
    blockedStrategiesChanged = Signal()  # noqa: N815
    refreshedAtChanged = Signal()  # noqa: N815
    dataStatusChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path | None = None,
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        if db_path is None:
            from milodex.config import get_data_dir

            db_path = get_data_dir() / "milodex.db"
        self._db_path = db_path
        self._refresh_interval_ms = max(1, refresh_interval_ms)

        # Per-instance pool (maxThreadCount=1 — DB reads are sequential by
        # design).  Per-instance pool means waitForDone() in stop() drains
        # ONLY our workers; sharing globalInstance() would block unrelated
        # pool users (OperationalState, Attribution, etc.).
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)

        # State backing fields.
        self._paper_strategies: list[dict[str, Any]] = []
        self._blocked_strategies: list[dict[str, Any]] = []
        self._last_refreshed_at: str = ""
        self._data_status: str = "loading"
        self._data_error_message: str = ""

        # QTimer for periodic refresh.
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self._refresh_interval_ms)
        self._refresh_timer.timeout.connect(self._kick_refresh)

        # Signal carrier: worker -> main-thread via QueuedConnection.
        self._refresh_signals = _BankRefreshSignals(self)
        self._refresh_signals.completed.connect(
            self._on_refresh_complete, Qt.ConnectionType.QueuedConnection
        )
        self._refresh_signals.failed.connect(
            self._on_refresh_failed, Qt.ConnectionType.QueuedConnection
        )

        # In-flight guard — mirrors _broker_poll_in_flight from OperationalState.
        self._refresh_in_flight: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin periodic DB polling.

        Fires an immediate refresh so the surface has real data on the
        first frame.  The timer continues at ``refresh_interval_ms``
        thereafter.  Calling start() more than once is safe — the timer's
        ``isActive()`` guard prevents double-start.
        """
        self._kick_refresh()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def stop(self) -> None:
        """Halt polling and drain any in-flight DB worker.

        Idempotent: safe to call before start() or after stop().  Drains
        the per-instance pool before disconnecting signals so a queued
        worker result cannot fire on a torn-down receiver during interpreter
        shutdown — the same Windows-shutdown contract as OperationalState.
        """
        self._refresh_timer.stop()
        self._thread_pool.waitForDone(2000)
        try:
            self._refresh_signals.completed.disconnect(self._on_refresh_complete)
            self._refresh_signals.failed.disconnect(self._on_refresh_failed)
        except (RuntimeError, TypeError):
            pass  # already disconnected — idempotent stop

    # ------------------------------------------------------------------
    # Worker scheduling
    # ------------------------------------------------------------------

    def _kick_refresh(self) -> None:
        """Schedule a DB refresh on the thread pool, dropping if one is in flight."""
        if self._refresh_in_flight:
            # Don't pile up workers if the DB is slow.
            return
        self._refresh_in_flight = True
        runnable = _BankRefreshRunnable(self._db_path, self._refresh_signals)
        self._thread_pool.start(runnable)

    @Slot(dict)
    def _on_refresh_complete(self, result: dict[str, Any]) -> None:
        """Apply a successful DB snapshot on the main thread."""
        self._refresh_in_flight = False

        paper_changed = result["paper"] != self._paper_strategies
        blocked_changed = result["blocked"] != self._blocked_strategies

        self._paper_strategies = result["paper"]
        self._blocked_strategies = result["blocked"]
        self._last_refreshed_at = result["refreshed_at"]
        self.refreshedAtChanged.emit()

        if paper_changed:
            self.paperStrategiesChanged.emit()
        if blocked_changed:
            self.blockedStrategiesChanged.emit()

        if self._data_status != "ready" or self._data_error_message:
            self._data_status = "ready"
            self._data_error_message = ""
            self.dataStatusChanged.emit()

    @Slot(str)
    def _on_refresh_failed(self, message: str) -> None:
        """Record a DB failure without losing last-known strategy lists.

        Per module docstring: preserve the last-known paper/blocked lists
        so the surface still shows the most recent snapshot even when the
        DB is transiently unreachable.
        """
        self._refresh_in_flight = False
        if self._data_status != "error" or self._data_error_message != message:
            self._data_status = "error"
            self._data_error_message = message
            self.dataStatusChanged.emit()

    # ------------------------------------------------------------------
    # Q_PROPERTY accessors
    # ------------------------------------------------------------------

    def _get_paper_strategies(self) -> list:
        return self._paper_strategies

    def _get_blocked_strategies(self) -> list:
        return self._blocked_strategies

    def _get_last_refreshed_at(self) -> str:
        return self._last_refreshed_at

    def _get_data_status(self) -> str:
        return self._data_status

    def _get_data_error_message(self) -> str:
        return self._data_error_message

    paperStrategies = Property(  # noqa: N815
        "QVariantList", _get_paper_strategies, notify=paperStrategiesChanged
    )
    blockedStrategies = Property(  # noqa: N815
        "QVariantList", _get_blocked_strategies, notify=blockedStrategiesChanged
    )
    lastRefreshedAt = Property(  # noqa: N815
        str, _get_last_refreshed_at, notify=refreshedAtChanged
    )
    dataStatus = Property(str, _get_data_status, notify=dataStatusChanged)  # noqa: N815
    dataErrorMessage = Property(  # noqa: N815
        str, _get_data_error_message, notify=dataStatusChanged
    )
