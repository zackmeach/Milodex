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

from PySide6.QtCore import Property, QObject, Signal  # pragma: no cover

from milodex.gui._dashboard_scope import EXPLANATION_PAPER_SQL, TRADE_PAPER_SQL
from milodex.gui.polling_lifecycle import PollingReadModel

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
        # TRADE_PAPER_SQL applied inside subquery over trades alone (unqualified columns
        # are unambiguous there), then joined back to paper-scoped explanations.
        sql = f"""
            SELECT COUNT(DISTINCT t.id)
            FROM (SELECT id, explanation_id, recorded_at
                  FROM trades
                  WHERE {TRADE_PAPER_SQL}
                    AND status = 'submitted'
                    AND recorded_at >= :start AND recorded_at <= :end) t
            JOIN explanations e ON t.explanation_id = e.id
            WHERE {EXPLANATION_PAPER_SQL}
        """
    elif stage_key == "filled":
        # TRADE_PAPER_SQL applied inside subquery over trades alone (unqualified columns
        # are unambiguous there), then joined back to paper-scoped explanations.
        sql = f"""
            SELECT COUNT(DISTINCT t.id)
            FROM (SELECT id, explanation_id, recorded_at
                  FROM trades
                  WHERE {TRADE_PAPER_SQL}
                    AND broker_status = 'filled'
                    AND recorded_at >= :start AND recorded_at <= :end) t
            JOIN explanations e ON t.explanation_id = e.id
            WHERE {EXPLANATION_PAPER_SQL}
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
# Builder
# ---------------------------------------------------------------------------


def _build_throughput_snapshot(db_path: Path) -> dict[str, Any]:
    """Adapter for ``PollingReadModel`` — packs throughput query into polling dict."""
    now = datetime.now(tz=UTC)
    result = _query_throughput(db_path, now)
    result["lastRefreshedAt"] = now.isoformat()
    return result


# ---------------------------------------------------------------------------
# RiskThroughputState
# ---------------------------------------------------------------------------


class RiskThroughputState(PollingReadModel):
    """Paper-scoped Evaluations→Filled funnel state exposed to QML as Q_PROPERTYs.

    Inherits the canonical polling lifecycle from
    :class:`milodex.gui.polling_lifecycle.PollingReadModel`. See module
    docstring for slice definitions and paper scoping. Per-module state
    (``bySlice``) and its change signal live here; lifecycle plumbing,
    ``dataStatus`` / ``dataErrorMessage`` / ``lastRefreshedAt`` /
    last-known-data preservation on error all live on the base.
    """

    bySliceChanged = Signal()  # noqa: N815

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
        self._by_slice: dict[str, Any] = {}
        super().__init__(
            builder=lambda: _build_throughput_snapshot(db_path),
            refresh_interval_ms=refresh_interval_ms,
            parent=parent,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        by_slice_changed = result["bySlice"] != self._by_slice
        self._by_slice = result["bySlice"]
        if by_slice_changed:
            self.bySliceChanged.emit()

    def _get_by_slice(self) -> dict:
        return self._by_slice

    bySlice = Property(  # noqa: N815
        "QVariantMap", _get_by_slice, notify=bySliceChanged
    )

    # dataStatus, dataErrorMessage, lastRefreshedAt — inherited from PollingReadModel
