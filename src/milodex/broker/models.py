"""Standardized types for brokerage operations.

These types are the contract between the broker layer and the rest of
the system. No Alpaca-specific types leak past this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OrderSide(Enum):
    """Buy or sell."""

    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order execution type."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    """Lifecycle status of an order."""

    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class TimeInForce(Enum):
    """How long an order remains active."""

    DAY = "day"
    GTC = "gtc"


@dataclass(frozen=True)
class Order:
    """A trade order."""

    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    time_in_force: TimeInForce
    status: OrderStatus
    submitted_at: datetime
    limit_price: float | None = None
    stop_price: float | None = None
    filled_quantity: float | None = None
    filled_avg_price: float | None = None
    filled_at: datetime | None = None


@dataclass(frozen=True)
class Position:
    """An open position."""

    symbol: str
    quantity: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


@dataclass(frozen=True)
class AccountInfo:
    """Account summary."""

    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    daily_pnl: float
