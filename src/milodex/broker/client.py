"""Abstract interface for broker clients.

All trade execution flows through this interface -- never through a
specific broker implementation. The risk layer, strategies, and CLI
depend on this ABC. To add a new broker, implement this without
changing any consuming code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

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
        client_order_id: str | None = None,
    ) -> Order:
        """Submit an order. Returns the order with initial status.

        ``client_order_id`` is the caller-generated idempotency key from the
        execution outbox (P1-02); brokers that support it should attach it to
        the order so a crashed attempt can be reconciled exactly.
        """

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

    def latest_completed_session(self, now: datetime) -> date | None:
        """Return the calendar date of the latest exchange session whose close
        is at or before ``now``, or ``None`` when it cannot be resolved.

        This is the authoritative session identity for the 1D (daily)
        data-staleness gate (D-1 queue-at-open): a daily bar is fresh iff its
        session date equals this value. Resolution failure or ambiguity MUST
        return ``None`` so the risk layer fails closed (treats the bar as
        stale) rather than trusting an unverifiable wall clock.

        Not abstract: the base returns ``None`` so legacy / non-equity broker
        implementations that have no calendar default to the safe (fail-closed)
        behavior without being forced to implement it. Concrete equity brokers
        override this.
        """
        return None
