"""Market data acquisition and storage.

Handles fetching, caching, and serving OHLCV market data via a pluggable
provider interface. Phase one uses Alpaca as the sole data source.
The interface supports adding alternative providers (e.g., Yahoo Finance)
without changing consuming code.
"""

from milodex.data.models import Bar, BarSet, Timeframe
from milodex.data.provider import DataProvider

__all__ = ["Bar", "BarSet", "DataProvider", "Timeframe"]
