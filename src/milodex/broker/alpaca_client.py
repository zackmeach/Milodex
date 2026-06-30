# src/milodex/broker/alpaca_client.py
"""Alpaca implementation of BrokerClient.

This is the ONLY file in the broker layer that imports alpaca-py.
All Alpaca-specific types are translated to milodex models before
being returned to callers. Retries on Alpaca 429 rate-limit responses;
raises immediately on all other errors.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TypeVar
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

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

# Map our enums to Alpaca's
_SIDE_MAP = {
    OrderSide.BUY: AlpacaOrderSide.BUY,
    OrderSide.SELL: AlpacaOrderSide.SELL,
}

_TIF_MAP = {
    TimeInForce.DAY: AlpacaTimeInForce.DAY,
    TimeInForce.GTC: AlpacaTimeInForce.GTC,
}

# Map Alpaca status strings to our OrderStatus. Explicit and exhaustive over
# Alpaca's documented order-status set (alpaca.trading.enums.OrderStatus) so a
# terminal status never silently reports as still-PENDING — that would inject
# phantom open exposure into the risk layer's caps (PR-5b). "done_for_day" and
# "replaced" are terminal for THIS order (Alpaca opens a new order id on
# replace), so both map to CANCELLED rather than PENDING. "stopped" (a fill is
# guaranteed but not yet posted) and "suspended" (paused, may resume) are NOT
# terminal — both map to PENDING so they keep counting as in-flight exposure;
# mapping them CANCELLED would undercount risk caps during the transient
# window (risk-reviewer CONCERN, PR-5b follow-up).
_STATUS_MAP = {
    "new": OrderStatus.PENDING,
    "accepted": OrderStatus.PENDING,
    "pending_new": OrderStatus.PENDING,
    "accepted_for_bidding": OrderStatus.PENDING,
    "pending_replace": OrderStatus.PENDING,
    "pending_review": OrderStatus.PENDING,
    "calculated": OrderStatus.PENDING,
    "held": OrderStatus.PENDING,
    "pending_cancel": OrderStatus.CANCELLED,
    "filled": OrderStatus.FILLED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "done_for_day": OrderStatus.CANCELLED,
    "replaced": OrderStatus.CANCELLED,
    "stopped": OrderStatus.PENDING,
    "suspended": OrderStatus.PENDING,
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

        if order_type_str not in _ORDER_TYPE_MAP:
            logger.warning(
                "Unrecognized Alpaca order_type %r on order %s; reporting as MARKET",
                order_type_str,
                alpaca_order.id,
            )
        if status_str not in _STATUS_MAP:
            # Conservative: an unrecognized status is reported PENDING (still
            # open) rather than dropped — over-counting open exposure in the
            # risk layer's caps is safe, under-counting is not.
            logger.warning(
                "Unrecognized Alpaca order status %r on order %s; reporting as PENDING",
                status_str,
                alpaca_order.id,
            )

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

    def _read_call(self, op: str, call: Callable[[], _T]) -> _T:
        """Run an idempotent read-path broker call, translating vendor exceptions
        into the broker-agnostic hierarchy so callers never see a raw ``APIError``
        (the broker-boundary contract, mirroring :meth:`submit_order`).

        Pure, MINIMAL exception translation — it does NOT alter order/position/
        account data flow or any money path; on success it returns the call's
        result unchanged. Scope is deliberately narrow to keep the blast radius
        small: a 401 / forbidden / auth ``APIError`` becomes an actionable
        :class:`BrokerAuthError`, and a connect/timeout failure becomes a
        :class:`BrokerConnectionError`. EVERY OTHER exception — including a
        non-auth ``APIError`` such as a 429 that exhausted its retries — is
        re-raised UNCHANGED, preserving the existing read-path contract (and
        never swallowing a real bug).
        """
        try:
            return call()
        except APIError as exc:
            text = str(exc).lower()
            if "unauthorized" in text or "forbidden" in text or "auth" in text:
                raise BrokerAuthError(
                    f"Broker authentication failed during {op}: check "
                    "ALPACA_API_KEY / ALPACA_SECRET_KEY in .env "
                    "(and that TRADING_MODE matches the key type)."
                ) from exc
            raise
        except Exception as exc:
            text = str(exc).lower()
            if "connect" in text or "timeout" in text:
                raise BrokerConnectionError(f"{op} could not reach the broker: {exc}") from exc
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
        except APIError as exc:
            logger.warning("cancel_order(%s) failed: %s", order_id, exc)
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
        alpaca_orders = self._read_call(
            "get_orders",
            lambda: call_with_retry_on_transient(lambda: self._client.get_orders(request)),
        )
        return [self._translate_order(o) for o in alpaca_orders]

    def get_positions(self) -> list[Position]:
        """Get all open positions from Alpaca."""
        alpaca_positions = self._read_call(
            "get_positions",
            lambda: call_with_retry_on_transient(lambda: self._client.get_all_positions()),
        )
        return [self._translate_position(p) for p in alpaca_positions]

    def get_position(self, symbol: str) -> Position | None:
        """Get position for a symbol, or None if not held."""
        try:
            alpaca_pos = call_with_retry_on_transient(
                lambda: self._client.get_open_position(symbol)
            )
            return self._translate_position(alpaca_pos)
        except Exception as exc:
            logger.warning("get_position(%s) failed: %s", symbol, exc)
            return None

    def get_account(self) -> AccountInfo:
        """Get account summary from Alpaca."""
        acct = self._read_call(
            "get_account",
            lambda: call_with_retry_on_transient(lambda: self._client.get_account()),
        )
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
        clock = self._read_call(
            "is_market_open",
            lambda: call_with_retry_on_transient(lambda: self._client.get_clock()),
        )
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
            calendar = call_with_retry_on_transient(lambda: self._client.get_calendar(request))
        except Exception as exc:
            # Any transport/parse failure -> fail closed (None). The risk layer
            # blocks the 1D submit rather than trusting an unverified session.
            logger.warning("latest_completed_session(%s) failed: %s", now, exc)
            return None

        if not calendar:
            return None

        eastern = ZoneInfo("America/New_York")
        latest: date | None = None
        for session in calendar:
            close = session.close
            session_date = session.date
            if close is None or session_date is None:
                # ANY malformed row poisons the latest-session determination:
                # if the genuinely-latest row is the ambiguous one, a
                # skip-and-fall-back would return an OLDER session and bless a
                # bar dated to it as fresh (fail-OPEN). The whole calendar
                # response is untrustworthy -> fail closed (None) so the risk
                # layer blocks the 1D submit. Alpaca's official calendar is
                # clean; a malformed row is pathological and blocking is safe.
                return None
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
