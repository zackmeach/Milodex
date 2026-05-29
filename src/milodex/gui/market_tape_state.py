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

from PySide6.QtCore import Property, QObject, Signal  # pragma: no cover

from milodex.config import get_cache_dir
from milodex.gui._market_cache import _latest_cache_version  # noqa: PLC2701
from milodex.gui.polling_lifecycle import PollingReadModel

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
        logger.warning("MarketTapeState: no vN version directory found in cache: %s", cache_dir)
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


def _build_market_tape_snapshot(cache_dir: Path) -> dict[str, Any]:
    """Adapter for ``PollingReadModel`` — wraps the list payload `_read_tape`
    returns into the dict shape the polling lifecycle expects (Opus B1 fix).

    Pre-RM-007 PR C, the worker emitted ``Signal(list)`` and the
    ``_on_refresh_complete`` slot accepted a bare list. ``PollingReadModel``
    expects a dict with an optional ``lastRefreshedAt`` key — this adapter
    converts the list into ``{"rows": [...], "lastRefreshedAt": "..."}``.
    """
    rows = _read_tape(cache_dir)
    return {"rows": rows, "lastRefreshedAt": datetime.now(tz=UTC).isoformat()}


# ---------------------------------------------------------------------------
# MarketTapeState
# ---------------------------------------------------------------------------


class MarketTapeState(PollingReadModel):
    """Market tape state exposed to QML as Q_PROPERTYs.

    Inherits the canonical polling lifecycle from
    :class:`milodex.gui.polling_lifecycle.PollingReadModel`. See module
    docstring for cache-read design decisions.

    Worker payload was ``Signal(list)`` pre-migration — adapted in
    :func:`_build_market_tape_snapshot` to the dict shape the polling
    lifecycle expects (Opus B1 fix). ``_apply_result`` reads
    ``result["rows"]`` to restore the list-of-row shape.
    """

    rowsChanged = Signal()  # noqa: N815

    def __init__(
        self,
        cache_dir: Path | None = None,
        refresh_interval_ms: int = 60_000,
        parent: QObject | None = None,
    ) -> None:
        if cache_dir is None:
            cache_dir = get_cache_dir()
        self._cache_dir = cache_dir
        self._rows: list[dict[str, Any]] = []
        super().__init__(
            builder=lambda: _build_market_tape_snapshot(cache_dir),
            refresh_interval_ms=refresh_interval_ms,
            parent=parent,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        rows = result["rows"]
        rows_changed = rows != self._rows
        self._rows = rows
        if rows_changed:
            self.rowsChanged.emit()

    def _get_rows(self) -> list:
        return self._rows

    rows = Property("QVariantList", _get_rows, notify=rowsChanged)

    # dataStatus, dataErrorMessage, lastRefreshedAt — inherited from PollingReadModel
