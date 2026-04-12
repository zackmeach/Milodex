"""Abstract interface for broker clients.

All trade execution flows through this interface -- never through a
specific broker implementation. The risk layer, strategies, and CLI
depend on this ABC. To add a new broker, implement this without
changing any consuming code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderType,
    Position,
    TimeInForce,
)


class BrokerClient(ABC):
    """Abstract broker client."""

    @abstractmethod
    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> Order:
        """Submit an order. Returns the order with initial status."""

    @abstractmethod
    def get_order(self, order_id: str) -> Order:
        """Get current status of an order."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successful."""

    @abstractmethod
    def cancel_all_orders(self) -> list[Order]:
        """Cancel all open orders. Used by kill switch for emergency halt."""

    @abstractmethod
    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        """Get recent orders. Supports filtering by status (open/closed/all)."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Get all open positions."""

    @abstractmethod
    def get_position(self, symbol: str) -> Position | None:
        """Get position for a specific symbol, or None if not held."""

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get account summary."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if the market is currently open for trading."""
