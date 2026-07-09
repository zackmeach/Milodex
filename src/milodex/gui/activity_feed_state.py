"""Activity feed state exposed to QML.

Owns a UNION of three sources: paper-scoped ``explanations``-derived rows,
``trades``-derived rows, and completed ``backtest_runs`` rows; normalized to a
common shape and ordered newest-first, capped at :const:`_FEED_CAP` rows.

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
the feed worker never mutates the event store.

Paper scope
-----------
Uses :const:`milodex.gui._dashboard_scope.EXPLANATION_PAPER_SQL` and
:const:`milodex.gui._dashboard_scope.TRADE_PAPER_SQL` to exclude backtest rows.
Each constant is applied to its own table's SELECT in the UNION — no join
between tables is performed, so unqualified column names are unambiguous.
Backtest rows come from ``backtest_runs WHERE status = 'completed'``.

Feed shape
----------
Each normalized row is a dict with keys:
- ``time``     — ISO timestamp from the source row
- ``strategy`` — ``strategy_name`` (explanations/trades) or ``strategy_id`` (backtests)
- ``kind``     — one of: ``rejection``, ``signal``, ``order``, ``fill``, ``backtest``
- ``detail``   — concise human string (see formats below)
- ``symbol``   — ``symbol`` (empty string for backtest rows)
- ``tone``     — one of: ``positive``, ``negative``, ``data``, ``muted``
- ``reason``   — vetoing rule(s), comma-joined from ``explanations.reason_codes_json``
  (e.g. ``"max_concurrent_positions_exceeded"``); populated only for
  ``kind='rejection'`` rows, ``""`` for every other kind (uniform row shape)

Detail string formats:
- explanations: ``"{decision_type}/{status}"``  (e.g. ``"submit/submitted"``)
- trades: ``"{side} {quantity} @ {status}/{broker_status}"``
  (e.g. ``"buy 10 @ submitted/pending"``;  broker_status renders as ``"pending"`` if NULL)
- backtests: metric summary (e.g. ``"Sharpe 0.72 · max-dd 8.5% · n=120"``)

Kind derivation:
- explanations: ``kind='rejection'`` if ``risk_allowed = 0``; else ``kind='signal'``
- trades: ``kind='order'`` if ``status = 'submitted'``; ``kind='fill'`` if
  ``broker_status = 'filled'``.  Trades that are neither are excluded.
- backtests: ``kind='backtest'`` for all completed rows.

Client-side filtering
---------------------
The QML layer filters All / Orders / Rejections / Signals / Fills / BACKTESTS
client-side on the ``kind`` field.  No server-side filtering is performed here —
the full capped feed is always returned.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, Signal  # pragma: no cover

from milodex.gui import _event_queries
from milodex.gui._dashboard_scope import EXPLANATION_PAPER_SQL, TRADE_PAPER_SQL
from milodex.gui.polling_lifecycle import PollingReadModel

logger = logging.getLogger(__name__)

# Maximum number of rows returned in the feed (newest-first).
_FEED_CAP = 200

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _row_tone(kind: str, *, risk_allowed: int | None = None) -> str:  # noqa: ARG001
    """Return the abstract tone token for a feed row kind.

    Tone tokens are remapped to editorial Theme tokens in QML (PR 8).

    +------------+------------+
    | kind       | tone       |
    +============+============+
    | fill       | positive   |
    | rejection  | negative   |
    | order      | data       |
    | signal     | muted      |
    +------------+------------+
    """
    match kind:
        case "fill":
            return "positive"
        case "rejection":
            return "negative"
        case "order":
            return "data"
        case "signal":
            return "muted"
        case "backtest":
            return "data"
        case _:
            return "muted"


# ---------------------------------------------------------------------------
# SQL — each SELECT references exactly one table, so EXPLANATION_PAPER_SQL /
# TRADE_PAPER_SQL column references are unambiguous.
# ---------------------------------------------------------------------------

_SQL_EXPLANATIONS = f"""
SELECT
    recorded_at                               AS time,
    strategy_name                             AS strategy,
    CASE WHEN risk_allowed = 0
         THEN 'rejection'
         ELSE 'signal'
    END                                       AS kind,
    decision_type || '/' || status            AS detail,
    symbol                                    AS symbol,
    risk_allowed                              AS risk_allowed,
    reason_codes_json                         AS reason_codes_json
FROM explanations
WHERE {EXPLANATION_PAPER_SQL}
ORDER BY recorded_at DESC
LIMIT {_FEED_CAP}
"""

_SQL_TRADES = f"""
SELECT
    recorded_at                               AS time,
    strategy_name                             AS strategy,
    CASE
        WHEN status = 'submitted'       THEN 'order'
        WHEN broker_status = 'filled'   THEN 'fill'
        ELSE NULL
    END                                       AS kind,
    side || ' ' || quantity || ' @ ' || status || '/' || COALESCE(broker_status, 'pending')
                                              AS detail,
    symbol                                    AS symbol,
    NULL                                      AS risk_allowed
FROM trades
WHERE {TRADE_PAPER_SQL}
  AND (status = 'submitted' OR broker_status = 'filled')
ORDER BY recorded_at DESC
LIMIT {_FEED_CAP}
"""

_SQL_BACKTESTS = f"""
SELECT
    ended_at     AS time,
    strategy_id  AS strategy,
    metadata_json
FROM backtest_runs
WHERE status = 'completed'
  AND ended_at IS NOT NULL
ORDER BY ended_at DESC
LIMIT {_FEED_CAP}
"""


def _coerce_iso(time_raw: str) -> str:
    """Coerce tz-naive ISO timestamp → UTC-aware ISO string."""
    try:
        dt = datetime.fromisoformat(time_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    except Exception:  # noqa: BLE001
        return time_raw


def _reason_from_codes(reason_codes_json: str | None) -> str:
    """Parse ``reason_codes_json`` into a short human string.

    Joins the reason codes with ", " (e.g. ``"max_concurrent_positions_exceeded"``).
    Malformed / NULL / non-list JSON falls back to ``""`` — never raises.
    """
    if not reason_codes_json:
        return ""
    try:
        codes = json.loads(reason_codes_json)
    except (TypeError, ValueError):
        return ""
    if not isinstance(codes, list):
        return ""
    return ", ".join(str(c) for c in codes)


def _backtest_detail(sharpe: float | None, max_dd: float | None, n: int | None) -> str:
    """Build a concise metric summary for a backtest feed row."""
    parts = []
    if sharpe is not None:
        parts.append(f"Sharpe {sharpe:.2f}")
    if max_dd is not None:
        parts.append(f"max-dd {abs(max_dd) * 100:.1f}%")
    if n is not None:
        parts.append(f"n={n}")
    return " · ".join(parts) or "completed"


def _query_feed(db_path: Path) -> list[dict[str, Any]]:
    """Return the normalized activity feed (paper scope + completed backtests).

    Opens a read-only SQLite connection.  Raises on missing / unreadable DB.

    Returns a list of normalized dicts (see module docstring), ordered by
    timestamp DESC, capped at :const:`_FEED_CAP` entries.

    Bounded reads: each source SELECT carries ``ORDER BY <time> DESC LIMIT
    _FEED_CAP`` so at most ``_FEED_CAP`` rows per source cross into Python —
    not the entire paper history (the re-appearing OOM anti-pattern). The
    per-source bound is sufficient for a correct global cap: any row in the
    global newest-_FEED_CAP set ranks within the newest-_FEED_CAP of its own
    source, so the merge below (union of ≤3·_FEED_CAP rows, re-sorted, re-
    capped) yields output identical to bounding only at the end.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        exp_rows = conn.execute(_SQL_EXPLANATIONS).fetchall()
        trade_rows = conn.execute(_SQL_TRADES).fetchall()
        try:
            bt_rows = conn.execute(_SQL_BACKTESTS).fetchall()
        except sqlite3.OperationalError:
            # backtest_runs table may not exist in legacy test DBs
            bt_rows = []
    finally:
        conn.close()

    feed: list[dict[str, Any]] = []

    for row in exp_rows:
        kind = row["kind"]
        feed.append(
            {
                "time": _coerce_iso(row["time"]),
                "strategy": row["strategy"],
                "kind": kind,
                "detail": row["detail"],
                "symbol": row["symbol"],
                "tone": _row_tone(kind, risk_allowed=row["risk_allowed"]),
                "reason": (
                    _reason_from_codes(row["reason_codes_json"]) if kind == "rejection" else ""
                ),
            }
        )

    for row in trade_rows:
        kind = row["kind"]
        if kind is None:
            continue  # defensive — WHERE clause should already exclude these
        feed.append(
            {
                "time": _coerce_iso(row["time"]),
                "strategy": row["strategy"],
                "kind": kind,
                "detail": row["detail"],
                "symbol": row["symbol"],
                "tone": _row_tone(kind),
                "reason": "",
            }
        )

    for row in bt_rows:
        metrics = _event_queries.oos_aggregate_metrics(row["metadata_json"])
        feed.append(
            {
                "time": _coerce_iso(row["time"]),
                "strategy": row["strategy"],
                "kind": "backtest",
                "detail": _backtest_detail(
                    metrics["sharpe"], metrics["max_drawdown_pct"], metrics["trade_count"]
                ),
                "symbol": "",
                "tone": _row_tone("backtest"),
                "reason": "",
            }
        )

    # Sort newest-first and cap.
    feed.sort(key=lambda r: r["time"], reverse=True)
    return feed[:_FEED_CAP]


# ---------------------------------------------------------------------------
# Worker scaffold
# ---------------------------------------------------------------------------


def _build_feed_snapshot(db_path: Path) -> dict[str, Any]:
    """Adapter for ``PollingReadModel`` — packs activity feed query into polling dict."""
    rows = _query_feed(db_path)
    return {"rows": rows, "lastRefreshedAt": datetime.now(tz=UTC).isoformat()}


# ---------------------------------------------------------------------------
# ActivityFeedState
# ---------------------------------------------------------------------------


class ActivityFeedState(PollingReadModel):
    """Paper-scoped activity feed state exposed to QML as Q_PROPERTYs.

    Inherits the canonical polling lifecycle from
    :class:`milodex.gui.polling_lifecycle.PollingReadModel`. See module
    docstring for feed shape and paper scoping.

    Q_PROPERTYs
    -----------
    - ``rows``             — QVariantList of normalized dicts, newest-first, ≤200
    - ``lastRefreshedAt``  — inherited (ISO timestamp of last successful refresh)
    - ``dataStatus``       — inherited (``loading`` / ``ready`` / ``error``)
    - ``dataErrorMessage`` — inherited (empty unless dataStatus == ``error``)

    Client-side filtering note
    --------------------------
    QML PR 8 filters All / Orders / Rejections / Signals / Fills client-side
    on the ``kind`` field. The full capped feed is always emitted here.
    """

    rowsChanged = Signal()  # noqa: N815

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
        self._rows: list[dict[str, Any]] = []
        super().__init__(
            builder=lambda: _build_feed_snapshot(db_path),
            refresh_interval_ms=refresh_interval_ms,
            parent=parent,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        rows_changed = result["rows"] != self._rows
        self._rows = result["rows"]
        if rows_changed:
            self.rowsChanged.emit()

    def _get_rows(self) -> list:
        return self._rows

    rows = Property("QVariantList", _get_rows, notify=rowsChanged)

    # dataStatus, dataErrorMessage, lastRefreshedAt — inherited from PollingReadModel
