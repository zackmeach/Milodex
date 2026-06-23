# src/milodex/broker/alpaca_client.py
"""Alpaca implementation of BrokerClient.

This is the ONLY file in the broker layer that imports alpaca-py.
All Alpaca-specific types are translated to milodex models before
being returned to callers. Retries on Alpaca 429 rate-limit responses;
raises immediately on all other errors.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import (
    GetCalendarRequest,
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
from milodex.config import get_alpaca_credentials, get_trading_mode
from milodex.core._alpaca_retry import call_with_retry_on_429, call_with_retry_on_transient

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
        client_order_id: str | None = None,
    ) -> Order:
        """Submit an order to Alpaca.

        ``client_order_id`` (the execution outbox's pre-generated idempotency
        key, P1-02) is forwarded to Alpaca, which stores it on the order —
        a crashed attempt can then be matched exactly against the broker's
        order list instead of heuristically by symbol/side/time.
        """
        alpaca_side = _SIDE_MAP[side]
        alpaca_tif = _TIF_MAP[time_in_force]

        try:
            if order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    client_order_id=client_order_id,
                )
            elif order_type == OrderType.LIMIT:
                request = LimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    limit_price=limit_price,
                    client_order_id=client_order_id,
                )
            elif order_type == OrderType.STOP:
                request = StopOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
            elif order_type == OrderType.STOP_LIMIT:
                request = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    client_order_id=client_order_id,
                )
            else:
                msg = f"Unsupported order type: {order_type}"
                raise ValueError(msg)

            # Retry on 429 is safe: Alpaca returns 429 BEFORE any state change, so
            # no duplicate order can result from a rate-limit retry.
            alpaca_order = call_with_retry_on_429(lambda: self._client.submit_order(request))
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
        """Get order status from Alpaca.

        Translates Alpaca ``APIError`` (e.g. a purged/unknown order id) into a
        broker-agnostic ``BrokerError`` so callers (e.g. reconciliation order
        sync) never see vendor exceptions — the broker boundary contract.
        """
        try:
            alpaca_order = call_with_retry_on_transient(
                lambda: self._client.get_order_by_id(order_id)
            )
        except APIError as exc:
            raise BrokerError(f"get_order({order_id}) failed: {exc}") from exc
        return self._translate_order(alpaca_order)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order. Returns True if successful."""
        try:
            call_with_retry_on_429(lambda: self._client.cancel_order_by_id(order_id))
            return True
        except APIError:
            return False

    def cancel_all_orders(self) -> list[Order]:
        """Cancel all open orders."""
        cancelled = call_with_retry_on_429(lambda: self._client.cancel_orders())
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
        alpaca_orders = call_with_retry_on_transient(lambda: self._client.get_orders(request))
        return [self._translate_order(o) for o in alpaca_orders]

    def get_positions(self) -> list[Position]:
        """Get all open positions from Alpaca."""
        alpaca_positions = call_with_retry_on_transient(lambda: self._client.get_all_positions())
        return [self._translate_position(p) for p in alpaca_positions]

    def get_position(self, symbol: str) -> Position | None:
        """Get position for a symbol, or None if not held."""
        try:
            alpaca_pos = call_with_retry_on_transient(
                lambda: self._client.get_open_position(symbol)
            )
            return self._translate_position(alpaca_pos)
        except Exception:
            return None

    def get_account(self) -> AccountInfo:
        """Get account summary from Alpaca."""
        acct = call_with_retry_on_transient(lambda: self._client.get_account())
        equity = float(acct.equity)
        prev_close_raw = getattr(acct, "equity_previous_close", None)
        if prev_close_raw is None:
            prev_close_raw = getattr(acct, "last_equity", equity)
        prev_close = float(prev_close_raw)
        return AccountInfo(
            equity=equity,
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            portfolio_value=float(acct.portfolio_value),
            daily_pnl=equity - prev_close,
        )

    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        clock = call_with_retry_on_transient(lambda: self._client.get_clock())
        return clock.is_open

    def latest_completed_session(self, now: datetime) -> date | None:
        """Latest exchange session whose close is at or before ``now``.

        Queries the Alpaca trading calendar over a short trailing window
        (``now`` minus 14 calendar days, generous enough to bridge holiday
        clusters) and returns the date of the most recent session that has
        already closed. Returns ``None`` on any failure or ambiguity so the
        risk layer fails closed (treats the bar as stale) rather than trusting
        an unverifiable wall clock.

        Alpaca's ``Calendar.close`` is a tz-naive datetime in US/Eastern wall
        time; it is localized to ``America/New_York`` (DST-correct) before the
        comparison against ``now``. ``Calendar.date`` is the session's calendar
        date and is what daily bars are stamped with (00:00 ET == 04:00/05:00
        UTC of that date), so it maps directly onto the bar's session date.
        """
        try:
            request = GetCalendarRequest(
                start=(now - timedelta(days=14)).date(),
                end=now.date(),
            )
            calendar = call_with_retry_on_transient(
                lambda: self._client.get_calendar(request)
            )
        except Exception:
            # Any transport/parse failure -> fail closed (None). The risk layer
            # blocks the 1D submit rather than trusting an unverified session.
            return None

        if not calendar:
            return None

        eastern = ZoneInfo("America/New_York")
        latest: date | None = None
        for session in calendar:
            close = session.close
            session_date = session.date
            if close is None or session_date is None:
                # Ambiguous row -> skip; never guess a session boundary.
                continue
            if close.tzinfo is None:
                close = close.replace(tzinfo=eastern)
            if close <= now and (latest is None or session_date > latest):
                latest = session_date
        return latest

    def is_symbol_tradable(self, symbol: str) -> bool | None:
        """Read Alpaca's asset status for ``symbol``.

        ``True`` iff Alpaca reports ``asset.tradable`` AND status == "active".
        ``False`` if the asset exists but is halted/inactive/not tradable.
        Exceptions (APIError for unknown symbol, transient network) are NOT
        caught here — the drain-time helper wraps this call and maps any raise
        to a conservative DROP. Keeping the broker boundary a thin read keeps
        the catch policy in one place (the drain policy), not duplicated here.
        """
        asset = call_with_retry_on_transient(lambda: self._client.get_asset(symbol))
        status = asset.status.value if hasattr(asset.status, "value") else asset.status
        return bool(asset.tradable) and str(status) == "active"
