"""Standardized data types for market data.

These types are the contract between the data layer and the rest of the system.
No Alpaca-specific types leak past this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import pandas as pd

# Required columns in every BarSet DataFrame
BARSET_REQUIRED_COLUMNS = frozenset({
    "timestamp", "open", "high", "low", "close", "volume",
})
# vwap is always present but nullable
BARSET_ALL_COLUMNS = BARSET_REQUIRED_COLUMNS | {"vwap"}


class Timeframe(Enum):
    """Supported bar timeframes.

    Values match Alpaca's naming convention for easy translation,
    but consumers should use the enum members, not the string values.
    """

    MINUTE_1 = "1Min"
    MINUTE_5 = "5Min"
    MINUTE_15 = "15Min"
    HOUR_1 = "1Hour"
    DAY_1 = "1Day"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None


class BarSet:
    """A collection of OHLCV bars backed by a pandas DataFrame.

    Column contract:
    - Always present: timestamp, open, high, low, close, volume, vwap
    - Price columns: float64
    - volume: int64
    - timestamp: datetime64[ns, UTC]
    - vwap: float64, nullable (may contain NaN)
    """

    def __init__(self, df: pd.DataFrame) -> None:
        missing = BARSET_REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(
                f"BarSet missing required column(s): {', '.join(sorted(missing))}"
            )

        # Ensure vwap column exists (nullable)
        if "vwap" not in df.columns:
            df = df.copy()
            df["vwap"] = pd.NA

        self._df = df.copy()

    def to_dataframe(self) -> pd.DataFrame:
        """Return a copy of the underlying DataFrame."""
        return self._df.copy()

    def latest(self) -> Bar:
        """Return the most recent bar.

        Raises:
            ValueError: If the BarSet is empty.
        """
        if self._df.empty:
            raise ValueError("Cannot get latest bar from an empty BarSet.")

        row = self._df.iloc[-1]
        vwap_val = row["vwap"] if pd.notna(row["vwap"]) else None
        return Bar(
            timestamp=row["timestamp"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
            vwap=float(vwap_val) if vwap_val is not None else None,
        )

    def __len__(self) -> int:
        return len(self._df)

    def __repr__(self) -> str:
        return f"BarSet({len(self._df)} bars)"
