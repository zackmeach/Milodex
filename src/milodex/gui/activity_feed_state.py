"""Activity feed state exposed to QML.

Owns a UNION of paper-scoped ``explanations``-derived rows and
``trades``-derived rows, normalized to a common shape and ordered newest-first,
capped at :const:`_FEED_CAP` rows.

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

Feed shape
----------
Each normalized row is a dict with keys:
- ``time``     — ``recorded_at`` ISO text from the source row
- ``strategy`` — ``strategy_name``
- ``kind``     — one of: ``rejection``, ``signal``, ``order``, ``fill``
- ``detail``   — concise human string (see formats below)
- ``symbol``   — ``symbol``
- ``tone``     — one of: ``positive``, ``negative``, ``data``, ``muted``

Detail string formats:
- explanations: ``"{decision_type}/{status}"``  (e.g. ``"submit/submitted"``)
- trades: ``"{side} {quantity} @ {status}/{broker_status}"``
  (e.g. ``"buy 10 @ submitted/pending"``;  broker_status may be ``"None"`` if NULL)

Kind derivation:
- explanations: ``kind='rejection'`` if ``risk_allowed = 0``; else ``kind='signal'``
- trades: ``kind='order'`` if ``status = 'submitted'``; ``kind='fill'`` if
  ``broker_status = 'filled'``.  Trades that are neither are excluded.

Client-side filtering
---------------------
The QML layer (PR 8) filters All / Orders / Rejections / Signals / Fills
client-side on the ``kind`` field.  No server-side filtering is performed here —
the full capped feed is always returned.
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

from milodex.gui._dashboard_scope import EXPLANATION_PAPER_SQL, TRADE_PAPER_SQL

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
    risk_allowed                              AS risk_allowed
FROM explanations
WHERE {EXPLANATION_PAPER_SQL}
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
    side || ' ' || quantity || ' @ ' || status || '/' || COALESCE(broker_status, 'None')
                                              AS detail,
    symbol                                    AS symbol,
    NULL                                      AS risk_allowed
FROM trades
WHERE {TRADE_PAPER_SQL}
  AND (status = 'submitted' OR broker_status = 'filled')
"""


def _query_feed(db_path: Path) -> list[dict[str, Any]]:
    """Return the normalized activity feed for the paper scope.

    Opens a read-only SQLite connection.  Raises on missing / unreadable DB.

    Returns a list of normalized dicts (see module docstring), ordered by
    ``recorded_at`` DESC, capped at :const:`_FEED_CAP` entries.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        exp_rows = conn.execute(_SQL_EXPLANATIONS).fetchall()
        trade_rows = conn.execute(_SQL_TRADES).fetchall()
    finally:
        conn.close()

    feed: list[dict[str, Any]] = []

    for row in exp_rows:
        time_raw = row["time"]
        # Coerce tz-naive → UTC
        try:
            dt = datetime.fromisoformat(time_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            time_iso = dt.isoformat()
        except Exception:  # noqa: BLE001
            time_iso = time_raw

        kind = row["kind"]
        feed.append(
            {
                "time": time_iso,
                "strategy": row["strategy"],
                "kind": kind,
                "detail": row["detail"],
                "symbol": row["symbol"],
                "tone": _row_tone(kind, risk_allowed=row["risk_allowed"]),
            }
        )

    for row in trade_rows:
        kind = row["kind"]
        if kind is None:
            continue  # defensive — WHERE clause should already exclude these

        time_raw = row["time"]
        try:
            dt = datetime.fromisoformat(time_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            time_iso = dt.isoformat()
        except Exception:  # noqa: BLE001
            time_iso = time_raw

        feed.append(
            {
                "time": time_iso,
                "strategy": row["strategy"],
                "kind": kind,
                "detail": row["detail"],
                "symbol": row["symbol"],
                "tone": _row_tone(kind),
            }
        )

    # Sort newest-first and cap.
    feed.sort(key=lambda r: r["time"], reverse=True)
    return feed[:_FEED_CAP]


# ---------------------------------------------------------------------------
# Worker scaffold
# ---------------------------------------------------------------------------


class _ActivityFeedRefreshSignals(QObject):
    """Signal carrier for the ActivityFeedState refresh worker."""

    completed = Signal(dict)
    failed = Signal(str)


class _ActivityFeedRefreshRunnable(QRunnable):
    """One-shot refresh executed on a QThreadPool worker thread."""

    def __init__(
        self,
        db_path: Path,
        signals: _ActivityFeedRefreshSignals,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover — exercised via tests with fixture DBs
        try:
            rows = _query_feed(self._db_path)
            now = datetime.now(tz=UTC)
            self._signals.completed.emit({"rows": rows, "refreshed_at": now.isoformat()})
        except Exception as exc:  # noqa: BLE001
            logger.warning("ActivityFeedState: DB refresh failed: %s", exc)
            self._signals.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# ActivityFeedState
# ---------------------------------------------------------------------------


class ActivityFeedState(QObject):
    """Paper-scoped activity feed state exposed to QML as Q_PROPERTYs.

    See module docstring for threading model, feed shape, and paper scoping.

    Q_PROPERTYs
    -----------
    - ``rows``             — QVariantList of normalized dicts, newest-first, ≤200
    - ``lastRefreshedAt``  — ISO timestamp of last successful refresh
    - ``dataStatus``       — one of: ``loading``, ``ready``, ``error``
    - ``dataErrorMessage`` — error message (empty unless dataStatus == ``error``)

    Client-side filtering note
    --------------------------
    QML PR 8 filters All / Orders / Rejections / Signals / Fills client-side
    on the ``kind`` field.  The full capped feed is always emitted here.
    """

    rowsChanged = Signal()  # noqa: N815
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
        self._rows: list[dict[str, Any]] = []
        self._last_refreshed_at: str = ""
        self._data_status: str = "loading"
        self._data_error_message: str = ""

        # QTimer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self._refresh_interval_ms)
        self._refresh_timer.timeout.connect(self._kick_refresh)

        # Signal carrier
        self._refresh_signals = _ActivityFeedRefreshSignals(self)
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
        runnable = _ActivityFeedRefreshRunnable(self._db_path, self._refresh_signals)
        self._thread_pool.start(runnable)

    @Slot(dict)
    def _on_refresh_complete(self, result: dict[str, Any]) -> None:
        self._refresh_in_flight = False

        rows_changed = result["rows"] != self._rows
        self._rows = result["rows"]
        self._last_refreshed_at = result["refreshed_at"]

        self.lastRefreshedAtChanged.emit()
        if rows_changed:
            self.rowsChanged.emit()

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

    def _get_rows(self) -> list:
        return self._rows

    def _get_last_refreshed_at(self) -> str:
        return self._last_refreshed_at

    def _get_data_status(self) -> str:
        return self._data_status

    def _get_data_error_message(self) -> str:
        return self._data_error_message

    rows = Property("QVariantList", _get_rows, notify=rowsChanged)
    lastRefreshedAt = Property(  # noqa: N815
        str, _get_last_refreshed_at, notify=lastRefreshedAtChanged
    )
    dataStatus = Property(str, _get_data_status, notify=dataStatusChanged)  # noqa: N815
    dataErrorMessage = Property(  # noqa: N815
        str, _get_data_error_message, notify=dataStatusChanged
    )
