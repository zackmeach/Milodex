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


# An order is "open" (in flight, still able to fill more) while PENDING or
# PARTIALLY_FILLED; FILLED / CANCELLED / REJECTED are terminal. The risk layer
# uses this to count in-flight BUY occupancy/notional toward the caps.
_OPEN_ORDER_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED})


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

    @property
    def is_open(self) -> bool:
        """True while the order is in flight (PENDING / PARTIALLY_FILLED).

        Terminal statuses (FILLED / CANCELLED / REJECTED) return False. Used by
        the risk layer to count in-flight orders toward the caps; a filled order
        is already a broker position and is counted there instead.
        """
        return self.status in _OPEN_ORDER_STATUSES

    @property
    def notional(self) -> float | None:
        """Best-effort economic value of the (full) order, or None if unpriced.

        Price preference: the broker's ``filled_avg_price`` once any fill has
        priced the order, else the ``limit_price``. A pending *market* order has
        neither until it fills, so its notional is unknowable here and returns
        None — the risk layer skips it for exposure while still counting it as a
        concurrent-position slot (Phase 1 is market-only, ADR 0013). Uses the
        full order ``quantity`` (the committed exposure), the conservative
        direction for a risk cap.
        """
        price = self.filled_avg_price if self.filled_avg_price is not None else self.limit_price
        if price is None:
            return None
        return self.quantity * price

    @property
    def remaining_notional(self) -> float | None:
        """Economic value of the still-unfilled portion, or None if unpriced.

        ``(quantity - filled_quantity) * price`` with the same price preference
        as :attr:`notional` (``filled_avg_price`` else ``limit_price``). The
        already-filled portion is a broker ``Position`` counted in
        ``positions.market_value``; this is the in-flight exposure NOT yet
        reflected there, so summing it with positions double-counts nothing. A
        fully-unfilled order returns its full notional; an unpriced pending
        market order returns None (Phase 1 is market-only, ADR 0013). Clamps a
        bad over-fill (``filled_quantity > quantity``) to zero rather than
        emitting negative exposure.
        """
        price = self.filled_avg_price if self.filled_avg_price is not None else self.limit_price
        if price is None:
            return None
        remaining_qty = max(0.0, self.quantity - (self.filled_quantity or 0.0))
        return remaining_qty * price


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
