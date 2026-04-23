"""Tests for SimulatedBroker."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from milodex.broker.models import (
    AccountInfo,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.broker.simulated import SimulatedBroker


def make_broker(slippage_pct: float = 0.001, commission: float = 0.0) -> SimulatedBroker:
    broker = SimulatedBroker(slippage_pct=slippage_pct, commission_per_trade=commission)
    broker.set_simulation_day(
        day=datetime(2024, 3, 15, tzinfo=UTC),
        closes={"SPY": 500.0, "SHY": 80.0},
    )
    broker.update_account(
        AccountInfo(
            equity=100_000.0,
            cash=100_000.0,
            buying_power=100_000.0,
            portfolio_value=100_000.0,
            daily_pnl=0.0,
        )
    )
    return broker


def test_buy_fill_price_applies_positive_slippage():
    broker = make_broker(slippage_pct=0.001)
    price = broker.fill_price_for("SPY", OrderSide.BUY)
    assert price == pytest.approx(500.0 * 1.001)


def test_sell_fill_price_applies_negative_slippage():
    broker = make_broker(slippage_pct=0.002)
    price = broker.fill_price_for("SPY", OrderSide.SELL)
    assert price == pytest.approx(500.0 * 0.998)


def test_fill_price_returns_none_for_unknown_symbol():
    broker = make_broker()
    assert broker.fill_price_for("AAPL", OrderSide.BUY) is None


def test_submit_order_returns_filled_order_at_fill_price():
    broker = make_broker(slippage_pct=0.001)
    order = broker.submit_order(
        symbol="SPY",
        side=OrderSide.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == 10
    assert order.filled_avg_price == pytest.approx(500.0 * 1.001)
    assert order.symbol == "SPY"
    assert order.side == OrderSide.BUY


def test_submit_order_on_unknown_symbol_raises():
    broker = make_broker()
    with pytest.raises(ValueError, match="cannot simulate fill"):
        broker.submit_order(
            symbol="AAPL",
            side=OrderSide.BUY,
            quantity=1,
        )


def test_get_account_reflects_engine_injected_state():
    broker = make_broker()
    broker.update_account(
        AccountInfo(
            equity=95_000.0,
            cash=45_000.0,
            buying_power=45_000.0,
            portfolio_value=95_000.0,
            daily_pnl=-500.0,
        )
    )
    account = broker.get_account()
    assert account.equity == 95_000.0
    assert account.daily_pnl == -500.0


def test_get_positions_reflects_engine_injected_state():
    broker = make_broker()
    broker.set_positions(
        [
            Position(
                symbol="SPY",
                quantity=10,
                avg_entry_price=500.5,
                current_price=510.0,
                market_value=5100.0,
                unrealized_pnl=95.0,
                unrealized_pnl_pct=0.019,
            ),
        ]
    )
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "SPY"
    assert broker.get_position("SPY") is not None
    assert broker.get_position("AAPL") is None


def test_get_orders_returns_submitted_orders_in_order():
    broker = make_broker()
    broker.submit_order(symbol="SPY", side=OrderSide.BUY, quantity=1)
    broker.submit_order(symbol="SHY", side=OrderSide.BUY, quantity=2)
    orders = broker.get_orders()
    assert [o.symbol for o in orders] == ["SPY", "SHY"]


def test_get_orders_open_filter_returns_empty_since_all_fill():
    broker = make_broker()
    broker.submit_order(symbol="SPY", side=OrderSide.BUY, quantity=1)
    assert broker.get_orders(status="open") == []


def test_get_order_by_id_returns_submitted_order():
    broker = make_broker()
    order = broker.submit_order(symbol="SPY", side=OrderSide.BUY, quantity=1)
    assert broker.get_order(order.id).id == order.id


def test_get_order_unknown_id_raises():
    broker = make_broker()
    with pytest.raises(ValueError, match="not found"):
        broker.get_order("nonexistent")


def test_cancel_order_returns_false_since_all_fill_immediately():
    broker = make_broker()
    order = broker.submit_order(symbol="SPY", side=OrderSide.BUY, quantity=1)
    assert broker.cancel_order(order.id) is False


def test_is_market_open_always_true_during_backtest():
    broker = make_broker()
    assert broker.is_market_open() is True


def test_advancing_simulation_day_changes_fill_price():
    broker = make_broker()
    day1_price = broker.fill_price_for("SPY", OrderSide.BUY)
    broker.set_simulation_day(
        day=datetime(2024, 3, 16, tzinfo=UTC),
        closes={"SPY": 510.0},
    )
    day2_price = broker.fill_price_for("SPY", OrderSide.BUY)
    assert day2_price != day1_price
    assert day2_price == pytest.approx(510.0 * 1.001)
