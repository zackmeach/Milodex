"""Simulated broker for backtest execution.

Implements :class:`milodex.broker.BrokerClient` against engine-owned
simulation state. The backtest engine constructs one of these, advances
it day-by-day (:meth:`set_simulation_day`), and submits intents through
the shared :class:`milodex.execution.service.ExecutionService` — the
same orchestration path the live runner uses. That is the structural
realization of the VISION.md "same strategy code runs historical and
live with no branches" guarantee.

The broker is **stateless over its own bookkeeping**: account equity,
cash, and positions live on the engine side and are injected via
:meth:`update_account` / :meth:`set_positions`. The broker's only
authoritative state is a log of the synthetic orders it has produced,
used to answer ``get_order`` / ``get_orders`` queries the
ExecutionService may make during an intent submission.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from milodex.broker.client import BrokerClient
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)


class SimulatedBroker(BrokerClient):
    """In-memory broker for backtests. Fills at current simulation-day close.

    The fill price model is:

    - ``BUY  fill = day_close * (1 + slippage_pct) + commission`` as a cost
    - ``SELL fill = day_close * (1 - slippage_pct) - commission`` as proceeds

    Commission is reported by returning an order whose ``filled_avg_price``
    already incorporates slippage. The engine is still authoritative for
    cash/position bookkeeping — the broker reports what *would* have
    filled, and the engine reconciles.
    """

    def __init__(self, *, slippage_pct: float, commission_per_trade: float) -> None:
        self._slippage_pct = slippage_pct
        self._commission = commission_per_trade

        self._current_day: datetime | None = None
        self._current_closes: dict[str, float] = {}

        self._account = AccountInfo(
            equity=0.0,
            cash=0.0,
            buying_power=0.0,
            portfolio_value=0.0,
            daily_pnl=0.0,
        )
        self._positions: list[Position] = []
        self._orders: list[Order] = []

    # ------------------------------------------------------------------
    # Simulation-state injection (called by engine each day)
    # ------------------------------------------------------------------

    def set_simulation_day(self, day: datetime, closes: dict[str, float]) -> None:
        """Set the current simulation day and latest closes for each symbol."""
        self._current_day = day
        self._current_closes = {sym.upper(): float(p) for sym, p in closes.items()}

    def update_account(self, account: AccountInfo) -> None:
        """Replace the account snapshot the broker reports."""
        self._account = account

    def set_positions(self, positions: list[Position]) -> None:
        """Replace the positions list the broker reports."""
        self._positions = list(positions)

    # ------------------------------------------------------------------
    # Fill price model (engine reconciles cash/position from this)
    # ------------------------------------------------------------------

    def fill_price_for(self, symbol: str, side: OrderSide) -> float | None:
        """Return the simulated fill price for (symbol, side) on the current day.

        Returns ``None`` if the symbol has no close on the current day. The
        engine uses this to decide whether a fill is possible before calling
        :meth:`submit_order`.
        """
        close = self._current_closes.get(symbol.upper())
        if close is None or close <= 0:
            return None
        if side is OrderSide.BUY:
            return close * (1.0 + self._slippage_pct)
        return close * (1.0 - self._slippage_pct)

    @property
    def commission_per_trade(self) -> float:
        return self._commission

    @property
    def slippage_pct(self) -> float:
        return self._slippage_pct

    # ------------------------------------------------------------------
    # BrokerClient protocol
    # ------------------------------------------------------------------

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
        normalized_symbol = symbol.strip().upper()
        fill_price = self.fill_price_for(normalized_symbol, side)
        if fill_price is None:
            msg = (
                f"No close available for {normalized_symbol} on "
                f"{self._current_day}; cannot simulate fill."
            )
            raise ValueError(msg)

        timestamp = self._current_day or datetime.now(tz=UTC)
        order = Order(
            id=str(uuid.uuid4()),
            symbol=normalized_symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            time_in_force=time_in_force,
            status=OrderStatus.FILLED,
            submitted_at=timestamp,
            limit_price=limit_price,
            stop_price=stop_price,
            filled_quantity=quantity,
            filled_avg_price=fill_price,
            filled_at=timestamp,
        )
        self._orders.append(order)
        return order

    def get_order(self, order_id: str) -> Order:
        for order in self._orders:
            if order.id == order_id:
                return order
        msg = f"Order {order_id} not found in simulated broker."
        raise ValueError(msg)

    def cancel_order(self, order_id: str) -> bool:
        # All simulated orders fill immediately; nothing to cancel.
        return False

    def cancel_all_orders(self) -> list[Order]:
        return []

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:  # noqa: ARG002
        if status in {"all", "closed"}:
            return list(self._orders[-limit:])
        return []

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def get_position(self, symbol: str) -> Position | None:
        normalized = symbol.strip().upper()
        for position in self._positions:
            if position.symbol.upper() == normalized:
                return position
        return None

    def get_account(self) -> AccountInfo:
        return self._account

    def is_market_open(self) -> bool:
        # The engine only iterates trading days, so from the simulation's
        # point of view the market is always open. Risk checks that care
        # about this are bypassed in backtest mode (NullRiskEvaluator).
        return True
