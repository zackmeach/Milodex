"""Market tape state exposed to QML.

Provides latest daily close, prior close, percent-change, and timestamp
for a fixed set of market symbols (SPY, QQQ, IWM, TLT, VIX).  Data is
sourced exclusively from the Parquet market cache — no database, no network.

Design decisions (locked)
--------------------------
- Timestamp-only: no isStale / staleAsOf concept.  The tape is decorative
  context; decisions are strategy/risk-gated elsewhere.
- Missing symbol: one absent symbol produces a no-data row (close=None,
  pctChange=None, asOf=None); the other symbols are unaffected.
- Missing cache dir / no vN version dir: the whole refresh fails with
  dataStatus='error'.  This is a configuration/infrastructure problem, not
  a per-symbol data gap.

Threading model
---------------
Identical to :mod:`milodex.gui.performance_state`:
- QTimer fires every ``refresh_interval_ms`` (default 60 s) on the main thread.
- Per-instance QThreadPool(maxThreadCount=1) runs the worker.
- Results flow back via QueuedConnection.
- stop() drains in-flight workers via waitForDone(2000).
"""

from __future__ import annotations

import logging
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

from milodex.config import get_cache_dir
from milodex.gui._market_cache import _latest_cache_version  # noqa: PLC2701

logger = logging.getLogger(__name__)

SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "VIX")

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _pct_change(latest: float | None, prior: float | None) -> float | None:
    """Return latest/prior - 1, or None if prior is 0/None or latest is None."""
    if latest is None or prior is None or prior == 0:
        return None
    return latest / prior - 1


# ---------------------------------------------------------------------------
# Cache read function
# ---------------------------------------------------------------------------


def _read_tape(cache_dir: Path) -> list[dict[str, Any]]:
    """Read the latest daily bar for each symbol in SYMBOLS from the cache.

    Raises ``RuntimeError`` if ``cache_dir`` does not exist or contains no
    ``vN`` version directory.

    Per-symbol exceptions are caught so one missing ticker cannot sink the
    whole refresh; that symbol's entry will have close/pctChange/asOf=None.

    Returns a list of dicts in SYMBOLS order, each with keys:
    ``symbol``, ``close`` (float|None), ``pctChange`` (float|None),
    ``asOf`` (str|None).
    """
    import pandas as pd

    from milodex.data.cache import ParquetCache
    from milodex.data.models import Timeframe

    if not cache_dir.exists():
        logger.warning("MarketTapeState: cache directory does not exist: %s", cache_dir)
        raise RuntimeError("Market data unavailable — cache not found")

    version = _latest_cache_version(cache_dir)
    if version is None:
        logger.warning(
            "MarketTapeState: no vN version directory found in cache: %s", cache_dir
        )
        raise RuntimeError("Market data unavailable — no versioned cache found")

    cache = ParquetCache(cache_dir, version=version)
    rows: list[dict[str, Any]] = []

    for symbol in SYMBOLS:
        try:
            df = cache.read(symbol, Timeframe.DAY_1)
            if df is None or df.empty:
                rows.append({"symbol": symbol, "close": None, "pctChange": None, "asOf": None})
                continue

            df = df.sort_values("timestamp").reset_index(drop=True)
            latest_row = df.iloc[-1]
            latest_close = float(latest_row["close"])
            as_of = str(pd.to_datetime(latest_row["timestamp"]).date())

            prior_close: float | None = None
            if len(df) >= 2:
                prior_close = float(df.iloc[-2]["close"])

            pct = _pct_change(latest_close, prior_close)
            rows.append(
                {
                    "symbol": symbol,
                    "close": latest_close,
                    "pctChange": pct,
                    "asOf": as_of,
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("MarketTapeState: failed to read %s: %s", symbol, exc)
            rows.append({"symbol": symbol, "close": None, "pctChange": None, "asOf": None})

    return rows


# ---------------------------------------------------------------------------
# Worker scaffold
# ---------------------------------------------------------------------------


class _MarketTapeRefreshSignals(QObject):
    """Signal carrier for the MarketTapeState refresh worker."""

    completed = Signal(list)
    failed = Signal(str)


class _MarketTapeRefreshRunnable(QRunnable):
    """One-shot cache read executed on a QThreadPool worker thread."""

    def __init__(
        self,
        cache_dir: Path,
        signals: _MarketTapeRefreshSignals,
    ) -> None:
        super().__init__()
        self._cache_dir = cache_dir
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover — exercised via tests with fixture caches
        try:
            rows = _read_tape(self._cache_dir)
            self._signals.completed.emit(rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("MarketTapeState: cache refresh failed: %s", exc)
            self._signals.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# MarketTapeState
# ---------------------------------------------------------------------------


class MarketTapeState(QObject):
    """Market tape state exposed to QML as Q_PROPERTYs.

    See module docstring for threading model and design decisions.
    """

    rowsChanged = Signal()  # noqa: N815
    lastRefreshedAtChanged = Signal()  # noqa: N815
    dataStatusChanged = Signal()  # noqa: N815

    def __init__(
        self,
        cache_dir: Path | None = None,
        refresh_interval_ms: int = 60_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        if cache_dir is None:
            cache_dir = get_cache_dir()
        self._cache_dir = cache_dir

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
        self._refresh_signals = _MarketTapeRefreshSignals(self)
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
        """Begin periodic cache polling with an immediate first refresh."""
        self._kick_refresh()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def stop(self) -> None:
        """Halt polling and drain any in-flight cache worker."""
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
        runnable = _MarketTapeRefreshRunnable(self._cache_dir, self._refresh_signals)
        self._thread_pool.start(runnable)

    @Slot(list)
    def _on_refresh_complete(self, rows: list[dict[str, Any]]) -> None:
        self._refresh_in_flight = False

        rows_changed = rows != self._rows
        self._rows = rows
        self._last_refreshed_at = datetime.now(tz=UTC).isoformat()

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
