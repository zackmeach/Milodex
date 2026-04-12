"""Abstract interface for market data providers.

All data consumers (strategies, backtesting, analytics) depend on this
interface -- never on a specific provider implementation. To add a new
data source, implement this ABC without changing any consuming code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from milodex.data.models import Bar, BarSet, Timeframe


class DataProvider(ABC):
    """Abstract market data provider."""

    @abstractmethod
    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, BarSet]:
        """Fetch OHLCV bars for one or more symbols.

        Args:
            symbols: List of ticker symbols (e.g., ["AAPL", "SPY"]).
            timeframe: Bar timeframe (e.g., Timeframe.DAY_1).
            start: Start date (inclusive).
            end: End date (inclusive).

        Returns:
            Dict mapping each symbol to its BarSet.
        """

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Bar:
        """Fetch the most recent bar for a symbol.

        When the market is closed, returns the last available bar
        (e.g., Friday's close on a Saturday). Does not raise.
        Callers should check is_market_open() if they need to
        distinguish "latest" from "live."
        """

    @abstractmethod
    def get_tradeable_assets(self) -> list[str]:
        """Return ticker symbols available for trading.

        Returns the full broker-eligible universe with no filtering.
        Strategy-level universe filtering is the strategy layer's
        responsibility.
        """
