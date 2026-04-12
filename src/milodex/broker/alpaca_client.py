# src/milodex/broker/alpaca_client.py
"""Alpaca implementation of BrokerClient.

This is the ONLY file in the broker layer that imports alpaca-py.
All Alpaca-specific types are translated to milodex models before
being returned to callers. Does not retry on failure — raises immediately.
"""

from __future__ import annotations

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)

from milodex.broker.client import BrokerClient
from milodex.broker.exceptions import (
    BrokerAuthError,
    BrokerConnectionError,
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
from milodex.config import get_alpaca_credentials, get_trading_mode

# Map our enums to Alpaca's
_SIDE_MAP = {
    OrderSide.BUY: AlpacaOrderSide.BUY,
    OrderSide.SELL: AlpacaOrderSide.SELL,
}

_TIF_MAP = {
    TimeInForce.DAY: AlpacaTimeInForce.DAY,
    TimeInForce.GTC: AlpacaTimeInForce.GTC,
}

# Map Alpaca status strings to our OrderStatus
_STATUS_MAP = {
    "new": OrderStatus.PENDING,
    "accepted": OrderStatus.PENDING,
    "pending_new": OrderStatus.PENDING,
    "pending_cancel": OrderStatus.CANCELLED,
    "filled": OrderStatus.FILLED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}

_ORDER_TYPE_MAP = {
    "market": OrderType.MARKET,
    "limit": OrderType.LIMIT,
    "stop": OrderType.STOP,
    "stop_limit": OrderType.STOP_LIMIT,
}


class AlpacaBrokerClient(BrokerClient):
    """Broker client backed by Alpaca's Trading API.

    TRADING_MODE=paper uses Alpaca's paper trading environment.
    TRADING_MODE=live uses real money. This distinction lives only here.
    """

    def __init__(self) -> None:
        api_key, secret_key = get_alpaca_credentials()
        paper = get_trading_mode() == "paper"
        self._client = TradingClient(api_key, secret_key, paper=paper)

    def _translate_order(self, alpaca_order) -> Order:
        """Convert an Alpaca order object to our Order model."""
        status_str = str(alpaca_order.status)
        # Handle enum or string status
        if hasattr(alpaca_order.status, "value"):
            status_str = alpaca_order.status.value
        order_type_str = str(alpaca_order.type)
        if hasattr(alpaca_order.type, "value"):
            order_type_str = alpaca_order.type.value
        side_str = str(alpaca_order.side)
        if hasattr(alpaca_order.side, "value"):
            side_str = alpaca_order.side.value
        tif_str = str(alpaca_order.time_in_force)
        if hasattr(alpaca_order.time_in_force, "value"):
            tif_str = alpaca_order.time_in_force.value

        return Order(
            id=str(alpaca_order.id),
            symbol=alpaca_order.symbol,
            side=OrderSide.BUY if side_str == "buy" else OrderSide.SELL,
            order_type=_ORDER_TYPE_MAP.get(order_type_str, OrderType.MARKET),
            quantity=float(alpaca_order.qty),
            time_in_force=(TimeInForce.GTC if tif_str == "gtc" else TimeInForce.DAY),
            status=_STATUS_MAP.get(status_str, OrderStatus.PENDING),
            submitted_at=alpaca_order.submitted_at,
            limit_price=(float(alpaca_order.limit_price) if alpaca_order.limit_price else None),
            stop_price=(float(alpaca_order.stop_price) if alpaca_order.stop_price else None),
            filled_quantity=(float(alpaca_order.filled_qty) if alpaca_order.filled_qty else None),
            filled_avg_price=(
                float(alpaca_order.filled_avg_price) if alpaca_order.filled_avg_price else None
            ),
            filled_at=alpaca_order.filled_at,
        )

    def _translate_position(self, alpaca_pos) -> Position:
        """Convert an Alpaca position object to our Position model."""
        return Position(
            symbol=alpaca_pos.symbol,
            quantity=float(alpaca_pos.qty),
            avg_entry_price=float(alpaca_pos.avg_entry_price),
            current_price=float(alpaca_pos.current_price),
            market_value=float(alpaca_pos.market_value),
            unrealized_pnl=float(alpaca_pos.unrealized_pl),
            unrealized_pnl_pct=float(alpaca_pos.unrealized_plpc),
        )

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
        """Submit an order to Alpaca."""
        alpaca_side = _SIDE_MAP[side]
        alpaca_tif = _TIF_MAP[time_in_force]

        try:
            if order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                )
            elif order_type == OrderType.LIMIT:
                request = LimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    limit_price=limit_price,
                )
            elif order_type == OrderType.STOP:
                request = StopOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    stop_price=stop_price,
                )
            elif order_type == OrderType.STOP_LIMIT:
                request = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    limit_price=limit_price,
                    stop_price=stop_price,
                )
            else:
                msg = f"Unsupported order type: {order_type}"
                raise ValueError(msg)

            alpaca_order = self._client.submit_order(request)
            return self._translate_order(alpaca_order)

        except APIError as e:
            error_msg = str(e).lower()
            if "forbidden" in error_msg or "auth" in error_msg:
                raise BrokerAuthError(str(e)) from e
            if "insufficient" in error_msg or "buying power" in error_msg:
                raise InsufficientFundsError(str(e)) from e
            raise OrderRejectedError(str(e)) from e
        except Exception as e:
            if "connect" in str(e).lower() or "timeout" in str(e).lower():
                raise BrokerConnectionError(str(e)) from e
            raise

    def get_order(self, order_id: str) -> Order:
        """Get order status from Alpaca."""
        alpaca_order = self._client.get_order_by_id(order_id)
        return self._translate_order(alpaca_order)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order. Returns True if successful."""
        try:
            self._client.cancel_order_by_id(order_id)
            return True
        except APIError:
            return False

    def cancel_all_orders(self) -> list[Order]:
        """Cancel all open orders."""
        cancelled = self._client.cancel_orders()
        return [self._translate_order(o) for o in cancelled]

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        """Get recent orders from Alpaca."""
        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        request = GetOrdersRequest(
            status=status_map.get(status, QueryOrderStatus.ALL),
            limit=limit,
        )
        alpaca_orders = self._client.get_orders(request)
        return [self._translate_order(o) for o in alpaca_orders]

    def get_positions(self) -> list[Position]:
        """Get all open positions from Alpaca."""
        alpaca_positions = self._client.get_all_positions()
        return [self._translate_position(p) for p in alpaca_positions]

    def get_position(self, symbol: str) -> Position | None:
        """Get position for a symbol, or None if not held."""
        try:
            alpaca_pos = self._client.get_open_position(symbol)
            return self._translate_position(alpaca_pos)
        except Exception:
            return None

    def get_account(self) -> AccountInfo:
        """Get account summary from Alpaca."""
        acct = self._client.get_account()
        equity = float(acct.equity)
        prev_close = float(acct.equity_previous_close)
        return AccountInfo(
            equity=equity,
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            portfolio_value=float(acct.portfolio_value),
            daily_pnl=equity - prev_close,
        )

    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        clock = self._client.get_clock()
        return clock.is_open
