"""Abstract interface for market data providers.

All data consumers (strategies, backtesting, analytics) depend on this
interface -- never on a specific provider implementation. To add a new
data source, implement this ABC without changing any consuming code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING

from milodex.data.models import Bar, BarSet, Timeframe

if TYPE_CHECKING:
    from collections.abc import Callable

    from milodex.core.event_store import OperatorAlertEvent


class DataConnectivityError(Exception):
    """Transient connectivity failure reaching the market-data source.

    Raised by provider implementations when an idempotent data read (bars
    fetch / latest-bar fetch) exhausts its bounded in-process transient
    retries — TLS teardown (SSLEOFError), connection reset, remote
    disconnect, connect/read timeout. The data-plane analogue of the broker
    layer's ``BrokerConnectionError``: callers with their own poll cadence
    (the strategy runner) treat it as a failed poll and retry within their
    outage budget instead of crashing. Non-transient errors are never
    translated into this class — they propagate unchanged.
    """


class DataProvider(ABC):
    """Abstract market data provider."""

    def set_alert_sink(self, sink: Callable[[OperatorAlertEvent], None]) -> None:
        """Install a durable operator-alert sink. Base implementation: ignore.

        Providers that can detect operator-worthy anomalies (e.g. sustained
        cache-write contention) override this to store ``sink`` and call it
        with a fully-built ``OperatorAlertEvent``. The default is a no-op so
        wiring sites can install a sink on any ``DataProvider`` without
        caring which implementation they hold, and every provider works
        identically with no sink configured (backtests and CLI one-offs
        construct providers bare).
        """

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
