"""Risk Layer Throughput funnel state exposed to QML.

Owns Today / Week / Month / YTD / All-Paper slices of the paper-scoped
Evaluations → Filled funnel computed from ``explanations`` and ``trades``
(SQLite).

Threading model
---------------
Identical to :mod:`milodex.gui.performance_state`:

- A :class:`QTimer` fires every ``refresh_interval_ms`` (default 30 s) on the
  main thread.
- The timer schedules a :class:`QRunnable` on a *per-instance*
  :class:`QThreadPool` (``maxThreadCount=1``).
- Results flow back via :class:`Qt.ConnectionType.QueuedConnection`.
- :meth:`stop` drains in-flight workers via ``waitForDone(2000)`` and
  defensively disconnects signals.

Read-only guarantee
-------------------
All SQLite connections are opened ``file:<path>?mode=ro`` (URI mode) to ensure
the throughput worker never mutates the event store.

Paper scope
-----------
Uses :const:`milodex.gui._dashboard_scope.EXPLANATION_PAPER_SQL` and
:const:`milodex.gui._dashboard_scope.TRADE_PAPER_SQL` to exclude backtest rows.
Submitted/Filled are joined back to the paper-scoped explanation set via
``trades.explanation_id = explanations.id``.

Slices
------
``SLICES = ("Today", "Week", "Month", "YTD", "All-Paper")``.

- ``Today`` — start-of-day UTC → now  (computed, not a placeholder)
- ``Week`` — ``[now - 7 days, now]``
- ``Month`` — ``[now - 30 days, now]``
- ``YTD`` — ``[Jan 1 of current year, now]``
- ``All-Paper`` — ``[earliest paper explanation, now]``
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
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

from milodex.gui._dashboard_scope import EXPLANATION_PAPER_SQL

logger = logging.getLogger(__name__)

SLICES = ("Today", "Week", "Month", "YTD", "All-Paper")

# ---------------------------------------------------------------------------
# Funnel stage definitions (ordered — Evaluations first, Filled last)
# ---------------------------------------------------------------------------

_FUNNEL_STAGES = [
    {
        "key": "evaluations",
        "label": "Evaluations",
    },
    {
        "key": "signals",
        "label": "Signals",
    },
    {
        "key": "orders_proposed",
        "label": "Orders Proposed",
    },
    {
        "key": "risk_approved",
        "label": "Risk-Approved",
    },
    {
        "key": "rejected",
        "label": "Rejected",
    },
    {
        "key": "submitted",
        "label": "Submitted",
    },
    {
        "key": "filled",
        "label": "Filled",
    },
]

# ---------------------------------------------------------------------------
# Slice window helper
# ---------------------------------------------------------------------------


def _slice_windows(now: datetime) -> dict[str, tuple[datetime, datetime]]:
    """Return {slice_name: (start, end)} for all slices including Today.

    All-Paper start is set to epoch; the SQL query handles the real minimum
    by using a >= filter that returns all rows (epoch is safely before any
    real data).  The actual minimum recorded_at is not needed here because
    we count rows in the window, not compute a period return from the first.
    """
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    jan1 = datetime(now.year, 1, 1, tzinfo=UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return {
        "Today": (today_start, now),
        "Week": (now - timedelta(days=7), now),
        "Month": (now - timedelta(days=30), now),
        "YTD": (jan1, now),
        "All-Paper": (epoch, now),
    }


# ---------------------------------------------------------------------------
# SQL query
# ---------------------------------------------------------------------------


def _count_stage(
    conn: sqlite3.Connection,
    stage_key: str,
    start_iso: str,
    end_iso: str,
) -> int:
    """Count rows for a single funnel stage within [start_iso, end_iso]."""
    time_filter = "recorded_at >= :start AND recorded_at <= :end"
    params: dict[str, Any] = {"start": start_iso, "end": end_iso}

    if stage_key == "evaluations":
        sql = f"""
            SELECT COUNT(*) FROM explanations
            WHERE {EXPLANATION_PAPER_SQL}
              AND {time_filter}
        """
    elif stage_key == "signals":
        sql = f"""
            SELECT COUNT(*) FROM explanations
            WHERE {EXPLANATION_PAPER_SQL}
              AND status != 'no_signal'
              AND {time_filter}
        """
    elif stage_key == "orders_proposed":
        sql = f"""
            SELECT COUNT(*) FROM explanations
            WHERE {EXPLANATION_PAPER_SQL}
              AND decision_type IN ('submit', 'preview')
              AND {time_filter}
        """
    elif stage_key == "risk_approved":
        sql = f"""
            SELECT COUNT(*) FROM explanations
            WHERE {EXPLANATION_PAPER_SQL}
              AND decision_type IN ('submit', 'preview')
              AND risk_allowed = 1
              AND {time_filter}
        """
    elif stage_key == "rejected":
        sql = f"""
            SELECT COUNT(*) FROM explanations
            WHERE {EXPLANATION_PAPER_SQL}
              AND risk_allowed = 0
              AND {time_filter}
        """
    elif stage_key == "submitted":
        # Trades paper-scoped AND whose explanation is in paper scope (join-back)
        sql = """
            SELECT COUNT(DISTINCT t.id)
            FROM trades t
            JOIN explanations e ON t.explanation_id = e.id
            WHERE t.strategy_stage IN ('paper','micro_live','live')
              AND t.backtest_run_id IS NULL
              AND e.strategy_stage IN ('paper','micro_live','live')
              AND e.decision_type != 'backtest_fill'
              AND t.status = 'submitted'
              AND t.recorded_at >= :start AND t.recorded_at <= :end
        """
    elif stage_key == "filled":
        # Trades paper-scoped AND whose explanation is in paper scope (join-back)
        sql = """
            SELECT COUNT(DISTINCT t.id)
            FROM trades t
            JOIN explanations e ON t.explanation_id = e.id
            WHERE t.strategy_stage IN ('paper','micro_live','live')
              AND t.backtest_run_id IS NULL
              AND e.strategy_stage IN ('paper','micro_live','live')
              AND e.decision_type != 'backtest_fill'
              AND t.broker_status = 'filled'
              AND t.recorded_at >= :start AND t.recorded_at <= :end
        """
    else:
        return 0

    row = conn.execute(sql, params).fetchone()
    return row[0] if row else 0


def _query_throughput(db_path: Path, now: datetime) -> dict[str, Any]:
    """Query explanations+trades and compute funnel counts per slice.

    Opens a read-only SQLite connection.  Raises on missing / unreadable DB.

    Returns a dict with key:
    - ``bySlice``: {slice_name → ordered list of stage dicts}
      Each stage dict: {"key": str, "label": str, "value": int}
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        windows = _slice_windows(now)
        by_slice: dict[str, list[dict[str, Any]]] = {}

        for slice_name in SLICES:
            start, end = windows[slice_name]
            start_iso = start.isoformat()
            end_iso = end.isoformat()

            stages = []
            for stage in _FUNNEL_STAGES:
                count = _count_stage(conn, stage["key"], start_iso, end_iso)
                stages.append({"key": stage["key"], "label": stage["label"], "value": count})

            by_slice[slice_name] = stages
    finally:
        conn.close()

    return {"bySlice": by_slice}


# ---------------------------------------------------------------------------
# Worker scaffold
# ---------------------------------------------------------------------------


class _ThroughputRefreshSignals(QObject):
    """Signal carrier for the RiskThroughputState refresh worker."""

    completed = Signal(dict)
    failed = Signal(str)


class _ThroughputRefreshRunnable(QRunnable):
    """One-shot refresh executed on a QThreadPool worker thread."""

    def __init__(
        self,
        db_path: Path,
        signals: _ThroughputRefreshSignals,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover — exercised via tests with fixture DBs
        try:
            now = datetime.now(tz=UTC)
            result = _query_throughput(self._db_path, now)
            result["refreshed_at"] = now.isoformat()
            self._signals.completed.emit(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RiskThroughputState: DB refresh failed: %s", exc)
            self._signals.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# RiskThroughputState
# ---------------------------------------------------------------------------


class RiskThroughputState(QObject):
    """Paper-scoped Evaluations→Filled funnel state exposed to QML as Q_PROPERTYs.

    See module docstring for threading model, slice definitions, and paper
    scoping.
    """

    bySliceChanged = Signal()  # noqa: N815
    lastRefreshedAtChanged = Signal()  # noqa: N815
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

        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)

        # State backing fields
        self._by_slice: dict[str, Any] = {}
        self._last_refreshed_at: str = ""
        self._data_status: str = "loading"
        self._data_error_message: str = ""

        # QTimer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self._refresh_interval_ms)
        self._refresh_timer.timeout.connect(self._kick_refresh)

        # Signal carrier
        self._refresh_signals = _ThroughputRefreshSignals(self)
        self._refresh_signals.completed.connect(
            self._on_refresh_complete, Qt.ConnectionType.QueuedConnection
        )
        self._refresh_signals.failed.connect(
            self._on_refresh_failed, Qt.ConnectionType.QueuedConnection
        )

        self._refresh_in_flight: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin periodic DB polling with an immediate first refresh."""
        self._kick_refresh()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def stop(self) -> None:
        """Halt polling and drain any in-flight DB worker."""
        self._refresh_timer.stop()
        self._thread_pool.waitForDone(2000)
        try:
            self._refresh_signals.completed.disconnect(self._on_refresh_complete)
            self._refresh_signals.failed.disconnect(self._on_refresh_failed)
        except (RuntimeError, TypeError):
            pass

    # ------------------------------------------------------------------
    # Worker scheduling
    # ------------------------------------------------------------------

    def _kick_refresh(self) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        runnable = _ThroughputRefreshRunnable(self._db_path, self._refresh_signals)
        self._thread_pool.start(runnable)

    @Slot(dict)
    def _on_refresh_complete(self, result: dict[str, Any]) -> None:
        self._refresh_in_flight = False

        by_slice_changed = result["bySlice"] != self._by_slice
        self._by_slice = result["bySlice"]
        self._last_refreshed_at = result["refreshed_at"]

        self.lastRefreshedAtChanged.emit()
        if by_slice_changed:
            self.bySliceChanged.emit()

        if self._data_status != "ready" or self._data_error_message:
            self._data_status = "ready"
            self._data_error_message = ""
            self.dataStatusChanged.emit()

    @Slot(str)
    def _on_refresh_failed(self, message: str) -> None:
        self._refresh_in_flight = False
        if self._data_status != "error" or self._data_error_message != message:
            self._data_status = "error"
            self._data_error_message = message
            self.dataStatusChanged.emit()

    # ------------------------------------------------------------------
    # Q_PROPERTY accessors
    # ------------------------------------------------------------------

    def _get_by_slice(self) -> dict:
        return self._by_slice

    def _get_last_refreshed_at(self) -> str:
        return self._last_refreshed_at

    def _get_data_status(self) -> str:
        return self._data_status

    def _get_data_error_message(self) -> str:
        return self._data_error_message

    bySlice = Property(  # noqa: N815
        "QVariantMap", _get_by_slice, notify=bySliceChanged
    )
    lastRefreshedAt = Property(  # noqa: N815
        str, _get_last_refreshed_at, notify=lastRefreshedAtChanged
    )
    dataStatus = Property(str, _get_data_status, notify=dataStatusChanged)  # noqa: N815
    dataErrorMessage = Property(  # noqa: N815
        str, _get_data_error_message, notify=dataStatusChanged
    )
