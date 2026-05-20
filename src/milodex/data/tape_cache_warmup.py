"""Market-tape cache warmup: ensures VIX is present in the ParquetCache.

The market tape (Section VI of the GUI) displays SPY / QQQ / IWM / TLT / VIX.
The first four are equities that land in cache naturally whenever a strategy or
backtest calls ``AlpacaDataProvider.get_bars``.  VIX (^VIX) is a CBOE index —
Alpaca free tier does not supply it, so it is fetched separately from Yahoo
Finance via :mod:`milodex.data.yahoo_provider`.

This module provides a single entry point::

    from milodex.data.tape_cache_warmup import warmup_vix_cache
    warmup_vix_cache()               # uses defaults from config
    warmup_vix_cache(lookback_days=365, cache_dir=Path("market_cache"))

Callers:

* ``python -m milodex data warmup-tape`` — operator-facing refresh
* ``milodex.gui.app.run_app`` — GUI calls this on startup so the tape shows VIX
  on first open without requiring a separate CLI step
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from milodex.config import get_cache_dir
from milodex.data.alpaca_provider import CACHE_VERSION
from milodex.data.cache import ParquetCache
from milodex.data.models import Timeframe
from milodex.data.yahoo_provider import fetch_vix_history

_logger = logging.getLogger(__name__)

# Default number of calendar days of VIX history to (re)fetch on each warmup.
# 365 covers a full year of daily bars — more than enough for the tape's
# "latest close + pctChange" display.  Longer windows cost proportionally more
# Yahoo bandwidth; 365 is a good balance.
_DEFAULT_LOOKBACK_DAYS: int = 365


def warmup_vix_cache(
    *,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    cache_dir: Path | None = None,
    cache_version: str = CACHE_VERSION,
) -> bool:
    """Fetch recent VIX history from Yahoo Finance and write it to the ParquetCache.

    Behaviour
    ---------
    * Fetches daily bars for the window ``[today - lookback_days, today]``.
    * Merges into the existing VIX parquet (appends new rows, deduplicates).
      If no VIX parquet exists yet, writes a fresh one.
    * Returns ``True`` on success (at least one row written), ``False`` if the
      fetch returned no data or a write error occurred.

    Failure mode
    ------------
    Any exception from Yahoo Finance or the cache write is caught, logged as a
    WARNING, and results in ``False`` being returned.  Callers (GUI startup, CLI
    warmup command) MUST NOT crash on a ``False`` return — a missing VIX parquet
    simply means the tape renders VIX as "—" until the next successful warmup.

    Args:
        lookback_days:  Number of calendar days to fetch (default 365).
        cache_dir:      Root cache directory (default: ``config.get_cache_dir()``).
        cache_version:  ParquetCache version string (default: ``CACHE_VERSION``
                        from :mod:`milodex.data.alpaca_provider`).

    Returns:
        ``True`` if VIX data was successfully fetched and written; ``False``
        otherwise.
    """
    if cache_dir is None:
        cache_dir = get_cache_dir()

    today = datetime.now(tz=UTC).date()
    start = today - timedelta(days=lookback_days)

    _logger.info(
        "warmup_vix_cache: fetching VIX from %s to %s (lookback=%d days)",
        start,
        today,
        lookback_days,
    )

    df = fetch_vix_history(start=start, end=today)

    if df.empty:
        _logger.warning(
            "warmup_vix_cache: Yahoo returned no VIX data — cache not updated"
        )
        return False

    try:
        cache = ParquetCache(cache_dir, version=cache_version)
        cache.merge("VIX", Timeframe.DAY_1, df)
        _logger.info(
            "warmup_vix_cache: wrote %d VIX rows to cache (%s / %s / VIX.parquet)",
            len(df),
            cache_version,
            Timeframe.DAY_1.value,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("warmup_vix_cache: failed to write VIX to cache: %s", exc)
        return False


def get_vix_cache_state(
    *,
    cache_dir: Path | None = None,
    cache_version: str = CACHE_VERSION,
) -> dict[str, object]:
    """Return a diagnostic dict describing the current VIX cache state.

    Keys:
        exists      — bool: True if VIX.parquet is present and non-empty
        row_count   — int: number of rows (0 if absent)
        latest_date — str | None: ISO date of the most recent bar, or None
    """
    if cache_dir is None:
        cache_dir = get_cache_dir()

    cache = ParquetCache(cache_dir, version=cache_version)
    df = cache.read("VIX", Timeframe.DAY_1)

    if df is None or df.empty:
        return {"exists": False, "row_count": 0, "latest_date": None}

    import pandas as pd

    latest = pd.to_datetime(df["timestamp"], utc=True).max().date()
    return {
        "exists": True,
        "row_count": len(df),
        "latest_date": latest.isoformat(),
    }
