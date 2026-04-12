"""Tests for broker layer models."""

from datetime import UTC, datetime

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)


class TestEnums:
    def test_order_side_members(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_order_type_members(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.STOP.value == "stop"
        assert OrderType.STOP_LIMIT.value == "stop_limit"

    def test_order_status_members(self):
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.PARTIALLY_FILLED.value == "partially_filled"
        assert OrderStatus.CANCELLED.value == "cancelled"
        assert OrderStatus.REJECTED.value == "rejected"

    def test_time_in_force_members(self):
        assert TimeInForce.DAY.value == "day"
        assert TimeInForce.GTC.value == "gtc"


class TestOrder:
    def test_create_market_order(self):
        now = datetime.now(tz=UTC)
        order = Order(
            id="order-123",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.PENDING,
            submitted_at=now,
        )
        assert order.id == "order-123"
        assert order.limit_price is None
        assert order.stop_price is None
        assert order.filled_quantity is None
        assert order.filled_avg_price is None
        assert order.filled_at is None

    def test_create_limit_order(self):
        now = datetime.now(tz=UTC)
        order = Order(
            id="order-456",
            symbol="SPY",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=5.0,
            limit_price=450.50,
            time_in_force=TimeInForce.GTC,
            status=OrderStatus.FILLED,
            submitted_at=now,
            filled_quantity=5.0,
            filled_avg_price=450.45,
            filled_at=now,
        )
        assert order.limit_price == 450.50
        assert order.filled_quantity == 5.0


class TestPosition:
    def test_create_position(self):
        pos = Position(
            symbol="AAPL",
            quantity=10.0,
            avg_entry_price=150.0,
            current_price=155.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=0.0333,
        )
        assert pos.symbol == "AAPL"
        assert pos.unrealized_pnl == 50.0


class TestAccountInfo:
    def test_create_account_info(self):
        acct = AccountInfo(
            equity=10000.0,
            cash=5000.0,
            buying_power=5000.0,
            portfolio_value=10000.0,
            daily_pnl=150.0,
        )
        assert acct.equity == 10000.0
        assert acct.daily_pnl == 150.0
