"""Local Parquet cache for market data.

Stores OHLCV bars as Parquet files organized by timeframe and symbol.
Layout: {cache_dir}/{timeframe_value}/{SYMBOL}.parquet

The cache is append-only for historical data. Today's bar is always
considered stale (re-fetched) since the market may still be open.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from milodex.data.models import Timeframe


class ParquetCache:
    """File-based Parquet cache for market data bars."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: Timeframe) -> Path:
        """Return the Parquet file path for a symbol/timeframe pair."""
        dir_path = self._cache_dir / timeframe.value
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path / f"{symbol.upper()}.parquet"

    def read(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame | None:
        """Read cached bars for a symbol/timeframe. Returns None if no cache exists."""
        path = self._path(symbol, timeframe)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    def write(self, symbol: str, timeframe: Timeframe, df: pd.DataFrame) -> None:
        """Write bars to cache, replacing any existing data."""
        path = self._path(symbol, timeframe)
        df.to_parquet(path, index=False)

    def get_cached_range(
        self, symbol: str, timeframe: Timeframe
    ) -> tuple[date, date] | None:
        """Return the (start, end) date range of cached data. None if no cache."""
        df = self.read(symbol, timeframe)
        if df is None or df.empty:
            return None
        timestamps = pd.to_datetime(df["timestamp"])
        return timestamps.min().date(), timestamps.max().date()

    def merge(
        self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame
    ) -> None:
        """Merge new data into existing cache.

        Algorithm: load existing -> concatenate -> deduplicate by timestamp
        (keeping the newest row) -> sort by timestamp -> write back.
        """
        existing = self.read(symbol, timeframe)
        if existing is None:
            self.write(symbol, timeframe, new_data)
            return

        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
        combined = combined.sort_values("timestamp").reset_index(drop=True)
        self.write(symbol, timeframe, combined)
