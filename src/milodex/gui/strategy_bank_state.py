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

- ``"S"`` — Sharpe <= 0.5 (:data:`~milodex.promotion.state_machine.MIN_SHARPE`)
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

from PySide6.QtCore import Property, QObject, Signal  # pragma: no cover

from milodex.gui import _event_queries
from milodex.gui.polling_lifecycle import PollingReadModel
from milodex.promotion.state_machine import MAX_DRAWDOWN_PCT, MIN_SHARPE, MIN_TRADES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL — copied verbatim from docs/STRATEGY_BANK.md "How to refresh" section.
# If the doc's queries change, update these constants to match.
# ---------------------------------------------------------------------------

# Paper-stage strategies (the runnable list).
# Cross-reference: docs/STRATEGY_BANK.md lines 30–48.
#
# Diverges from STRATEGY_BANK.md's reference query: this version COALESCEs
# promotion-record metrics with backtest_runs.metadata_json so lifecycle_exempt
# promotions (where sharpe_ratio / max_drawdown_pct / trade_count are NULL in
# the promotions table) display correctly.  The doc's reference query renders
# those columns as NULL for regime — this fix surfaces the actual walk-forward
# figures from the re-baseline run (e.g. Sharpe 1.19, MaxDD 0.95, 27 trades
# for regime.daily.sma200_rotation.spy_shy.v1 per STRATEGY_BANK.md lines 49, 95).
# DEFAULT (demotion_aware=False) — "ever-promoted-to-paper" membership: each
# strategy's LATEST paper-promotion row (MAX(id) WHERE to_stage='paper'). A later
# demotion does NOT drop the strategy from this set. attention_state._query_attention
# relies on this exact membership for its underperformance / needsReview case-(c)
# computation (a demoted underperformer must STAY counted as underperforming, with
# the demotion handled separately). Do NOT change this default without re-checking
# tests/milodex/gui/test_attention_state.py.
_SQL_PAPER = """
SELECT p.strategy_id,
       p.recorded_at                AS promoted_at,
       p.backtest_run_id            AS evidence_run_id,
       p.promotion_type,
       COALESCE(p.sharpe_ratio,
           json_extract(br.metadata_json, '$.oos_aggregate.sharpe'))        AS sharpe_ratio,
       COALESCE(p.max_drawdown_pct,
           json_extract(br.metadata_json, '$.oos_aggregate.max_drawdown_pct')) AS max_drawdown_pct,
       COALESCE(p.trade_count,
           json_extract(br.metadata_json, '$.oos_aggregate.trade_count'))   AS trade_count
FROM promotions p
INNER JOIN (
    SELECT strategy_id, MAX(id) AS max_id
    FROM promotions
    WHERE to_stage = 'paper'
    GROUP BY strategy_id
) latest ON p.strategy_id = latest.strategy_id AND p.id = latest.max_id
LEFT JOIN (
    SELECT br1.strategy_id, br1.metadata_json
    FROM backtest_runs br1
    WHERE br1.id = (
        SELECT MAX(br2.id)
        FROM backtest_runs br2
        WHERE br2.strategy_id = br1.strategy_id AND br2.status = 'completed'
    )
) br ON p.strategy_id = br.strategy_id
WHERE p.to_stage = 'paper'
ORDER BY p.recorded_at;
"""

# demotion_aware=True — "current-stage = paper" membership for the GUI Strategy
# Bank card.  Restrict to each strategy's LATEST-OVERALL promotion row (across ALL
# promotion types), ordered recorded_at DESC then id DESC — the same ordering as
# EventStore.get_latest_promotion_for_strategy. The latest row is the one for which
# NO later (recorded_at, id) row exists; a demotion therefore supersedes the prior
# paper row (MAX(id) WHERE to_stage='paper' could not see it). Ordering by
# recorded_at — not id — is deliberate: a backdated audit_backfill demotion must not
# be mis-picked as latest.
_SQL_PAPER_CURRENT_STAGE = """
SELECT p.strategy_id,
       p.recorded_at                AS promoted_at,
       p.backtest_run_id            AS evidence_run_id,
       p.promotion_type,
       COALESCE(p.sharpe_ratio,
           json_extract(br.metadata_json, '$.oos_aggregate.sharpe'))        AS sharpe_ratio,
       COALESCE(p.max_drawdown_pct,
           json_extract(br.metadata_json, '$.oos_aggregate.max_drawdown_pct')) AS max_drawdown_pct,
       COALESCE(p.trade_count,
           json_extract(br.metadata_json, '$.oos_aggregate.trade_count'))   AS trade_count
FROM promotions p
INNER JOIN (
    SELECT p1.strategy_id, p1.id AS latest_id
    FROM promotions p1
    WHERE NOT EXISTS (
        SELECT 1 FROM promotions p2
        WHERE p2.strategy_id = p1.strategy_id
          AND (p2.recorded_at > p1.recorded_at
               OR (p2.recorded_at = p1.recorded_at AND p2.id > p1.id))
    )
) latest ON p.strategy_id = latest.strategy_id AND p.id = latest.latest_id
LEFT JOIN (
    SELECT br1.strategy_id, br1.metadata_json
    FROM backtest_runs br1
    WHERE br1.id = (
        SELECT MAX(br2.id)
        FROM backtest_runs br2
        WHERE br2.strategy_id = br1.strategy_id AND br2.status = 'completed'
    )
) br ON p.strategy_id = br.strategy_id
WHERE p.to_stage = 'paper'
ORDER BY p.recorded_at;
"""

# Blocked-exclusion paper-membership SQL, parallel to the two _SQL_PAPER forms.
# DEFAULT (demotion_aware=False) = "ever-paper": any strategy ever promoted to paper
# is excluded from blocked. demotion_aware=True = "currently-at-paper": only strategies
# whose LATEST-OVERALL promotion row is to_stage='paper' are excluded, so a demoted
# strategy resurfaces in blocked (it retains its completed backtest evidence).
_SQL_BLOCKED_EXCLUDE_PAPER_EVER = (
    "SELECT DISTINCT strategy_id FROM promotions WHERE to_stage = 'paper'"
)
_SQL_BLOCKED_EXCLUDE_PAPER_CURRENT = """
SELECT p1.strategy_id
FROM promotions p1
WHERE p1.to_stage = 'paper'
  AND NOT EXISTS (
      SELECT 1 FROM promotions p2
      WHERE p2.strategy_id = p1.strategy_id
        AND (p2.recorded_at > p1.recorded_at
             OR (p2.recorded_at = p1.recorded_at AND p2.id > p1.id))
  )
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
    family: str = "",
) -> list[str]:
    """Derive gate-failure codes from raw backtest metrics.

    Returns a list of short strings per ADR 0009:
    - ``"S"`` — Sharpe <= MIN_SHARPE
    - ``"D"`` — MaxDD >= MAX_DRAWDOWN_PCT
    - ``"N"`` — trade count < MIN_TRADES
    """
    # Regime strategies are lifecycle-proof and exempt from statistical gate thresholds
    # (CLAUDE.md "Strategy bank, two roles"; SRS R-PRM-004). This is a read-model
    # DISPLAY heuristic; the durable-governance lifecycle-proof identity is
    # ACTIVE_PROMOTION_POLICY.lifecycle_gate.applies_to (ADR 0058), which the
    # promotion orchestrator enforces. Display surfaces may lag; no behavior change.
    if family == "regime":
        return []
    failures: list[str] = []
    if sharpe is None or sharpe <= MIN_SHARPE:
        failures.append("S")
    if max_dd is None or max_dd >= MAX_DRAWDOWN_PCT:
        failures.append("D")
    if trade_count is None or trade_count < MIN_TRADES:
        failures.append("N")
    return failures


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _build_bank_snapshot(db_path: Path) -> dict[str, Any]:
    """Adapter for ``PollingReadModel`` — wraps ``_query_bank``'s tuple return.

    ``_query_bank`` retains its ``tuple[list, list]`` signature for external
    callers (``milodex.gui.attention_state`` consumes it directly). This
    shim packs the tuple into the ``dict`` payload the polling lifecycle
    expects, including the ``lastRefreshedAt`` ISO timestamp.

    The GUI Strategy Bank surface uses the demotion-aware "current stage"
    membership (``demotion_aware=True``): a demoted strategy leaves the paper
    card and resurfaces in blocked.  attention_state deliberately keeps the
    default ("ever-promoted-to-paper") membership for underperformance
    monitoring — see ``_query_bank``.
    """
    paper, blocked = _query_bank(db_path, demotion_aware=True)
    return {
        "paper": paper,
        "blocked": blocked,
        "lastRefreshedAt": datetime.now(tz=UTC).isoformat(),
    }


def _query_bank(
    db_path: Path, *, demotion_aware: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run both SQL queries and return (paper_list, blocked_list).

    Extracted as a module-level helper so it is testable without Qt.

    ``demotion_aware`` selects the paper-membership semantics (default ``False``):

    - ``False`` (default) — "ever-promoted-to-paper": a strategy's LATEST
      paper-promotion row defines membership; a later demotion does NOT drop it.
      This is the contract ``milodex.gui.attention_state`` relies on for its
      underperformance / needsReview case-(c) logic — a demoted underperformer
      stays counted as underperforming and the demotion is handled separately.
    - ``True`` — "current stage = paper": a strategy's LATEST-OVERALL promotion
      row (recorded_at DESC, id DESC) defines membership; a demotion supersedes
      the prior paper row, so the strategy leaves paper and (with backtest
      evidence) resurfaces in blocked.  This is what the GUI Strategy Bank card uses.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        paper = _fetch_paper(conn, demotion_aware=demotion_aware)
        blocked = _fetch_blocked(conn, demotion_aware=demotion_aware)
    finally:
        conn.close()
    return paper, blocked


def _fetch_paper(conn: sqlite3.Connection, *, demotion_aware: bool = False) -> list[dict[str, Any]]:
    sql = _SQL_PAPER_CURRENT_STAGE if demotion_aware else _SQL_PAPER
    rows = conn.execute(sql).fetchall()
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


def _fetch_blocked(
    conn: sqlite3.Connection, *, demotion_aware: bool = False
) -> list[dict[str, Any]]:
    # Obtain the full latest-completed-run map then exclude paper-promoted strategies
    # (replicates the former SQL NOT-IN-paper filter in Python).
    all_metrics = _event_queries.latest_backtest_metrics(conn)

    # Paper-exclusion membership mirrors _fetch_paper's:
    # - demotion_aware=False (DEFAULT): "ever-paper" — any strategy ever promoted to
    #   paper is excluded from blocked.
    # - demotion_aware=True: "currently-at-paper" — only strategies whose
    #   LATEST-OVERALL promotion row (recorded_at DESC, id DESC, matching
    #   EventStore.get_latest_promotion_for_strategy) has to_stage='paper' are
    #   excluded; a demoted strategy keeps its completed backtest evidence and
    #   therefore resurfaces in blocked.
    exclude_sql = (
        _SQL_BLOCKED_EXCLUDE_PAPER_CURRENT if demotion_aware else _SQL_BLOCKED_EXCLUDE_PAPER_EVER
    )
    paper_strategy_ids: set[str] = {r["strategy_id"] for r in conn.execute(exclude_sql).fetchall()}

    result: list[dict[str, Any]] = []
    for strategy_id, m in all_metrics.items():
        if strategy_id in paper_strategy_ids:
            continue  # replicate NOT IN (paper) — family arg omitted per original comment
        sharpe = m["sharpe"]
        max_dd = m["max_drawdown_pct"]
        trade_count = m["trade_count"]
        result.append(
            {
                "strategyId": strategy_id,
                "sharpeRatio": sharpe,
                "maxDrawdownPct": max_dd,
                "tradeCount": trade_count or 0,
                # family arg intentionally omitted — paper-promoted strategies are excluded
                # above, and the lifecycle-exempt regime strategy lives at paper.
                "gateFailures": _compute_gate_failures(sharpe, max_dd, trade_count),
                "startedAt": m["started_at"] or "",
                "runId": m["run_id"] or "",
                # ADR 0032 audit trail: static flag.
                "auditFlag": strategy_id in _AUDIT_FLAGGED,
                # flagFailingNotRetired: strategy is kept at backtest by governance
                # decision; the dual_absolute callout in STRATEGY_BANK.md explains why.
                "flagFailingNotRetired": strategy_id in _FLAGGED_NOT_RETIRED,
            }
        )
    # Replicate ORDER BY br.strategy_id from the former _SQL_BLOCKED.
    result.sort(key=lambda r: r["strategyId"])
    return result


# ---------------------------------------------------------------------------
# StrategyBankState
# ---------------------------------------------------------------------------


class StrategyBankState(PollingReadModel):
    """Strategy bank state exposed to QML as Q_PROPERTYs.

    Inherits the canonical polling lifecycle from
    :class:`milodex.gui.polling_lifecycle.PollingReadModel` — see module
    docstring for tolerance behaviour and SQL query sourcing. Lifecycle
    concerns (timer, thread pool, in-flight drop, error preservation,
    ``waitForDone(2000)`` shutdown) live on the base. Per-strategy
    state (paper / blocked lists) and their Q_PROPERTYs / change signals
    live here.
    """

    paperStrategiesChanged = Signal()  # noqa: N815
    blockedStrategiesChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path | None = None,
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        if db_path is None:
            from milodex.config import get_data_dir

            db_path = get_data_dir() / "milodex.db"
        self._db_path = db_path
        self._paper_strategies: list[dict[str, Any]] = []
        self._blocked_strategies: list[dict[str, Any]] = []
        super().__init__(
            builder=lambda: _build_bank_snapshot(db_path),
            refresh_interval_ms=refresh_interval_ms,
            parent=parent,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        """Update paper/blocked lists and emit change signals when shapes change.

        Last-known data preservation on error is handled by the base — when
        a refresh fails after a previous success, this method is not invoked
        and the lists stay as they were. The base also manages
        ``lastRefreshedAt`` (from the builder's ``lastRefreshedAt`` key) and
        ``dataStatus`` transitions.
        """
        paper_changed = result["paper"] != self._paper_strategies
        blocked_changed = result["blocked"] != self._blocked_strategies
        self._paper_strategies = result["paper"]
        self._blocked_strategies = result["blocked"]
        if paper_changed:
            self.paperStrategiesChanged.emit()
        if blocked_changed:
            self.blockedStrategiesChanged.emit()

    def _get_paper_strategies(self) -> list:
        return self._paper_strategies

    def _get_blocked_strategies(self) -> list:
        return self._blocked_strategies

    paperStrategies = Property(  # noqa: N815
        "QVariantList", _get_paper_strategies, notify=paperStrategiesChanged
    )
    blockedStrategies = Property(  # noqa: N815
        "QVariantList", _get_blocked_strategies, notify=blockedStrategiesChanged
    )

    # dataStatus, dataErrorMessage, lastRefreshedAt — inherited from PollingReadModel
