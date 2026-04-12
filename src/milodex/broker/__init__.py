"""Brokerage API integration.

Handles connection to brokers (starting with Alpaca), order submission,
position queries, and account status. The rest of the system interacts
with brokers exclusively through this module's interface.
"""

from milodex.broker.client import BrokerClient
from milodex.broker.exceptions import (
    BrokerAuthError,
    BrokerConnectionError,
    BrokerError,
    InsufficientFundsError,
    OrderRejectedError,
)
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)

__all__ = [
    "AccountInfo",
    "BrokerAuthError",
    "BrokerClient",
    "BrokerConnectionError",
    "BrokerError",
    "InsufficientFundsError",
    "Order",
    "OrderRejectedError",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Position",
    "TimeInForce",
]
