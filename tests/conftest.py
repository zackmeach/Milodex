# tests/conftest.py
"""Shared test fixtures for Milodex."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.data.models import Bar, BarSet


@pytest.fixture()
def sample_bar():
    """A single AAPL daily bar."""
    return Bar(
        timestamp=datetime(2025, 1, 15, 5, 0, tzinfo=UTC),
        open=150.0,
        high=152.0,
        low=149.5,
        close=151.0,
        volume=1000000,
        vwap=150.8,
    )


@pytest.fixture()
def sample_barset():
    """A 3-day AAPL BarSet."""
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-13", "2025-01-14", "2025-01-15"], utc=True),
            "open": [148.0, 149.0, 150.0],
            "high": [149.0, 150.0, 152.0],
            "low": [147.0, 148.5, 149.5],
            "close": [148.5, 149.5, 151.0],
            "volume": [900000, 950000, 1000000],
            "vwap": [148.3, 149.2, 150.8],
        }
    )
    return BarSet(df)


@pytest.fixture()
def sample_order():
    """A filled AAPL market buy order."""
    return Order(
        id="order-test-123",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.FILLED,
        submitted_at=datetime(2025, 1, 15, 14, 30, tzinfo=UTC),
        filled_quantity=10.0,
        filled_avg_price=151.25,
        filled_at=datetime(2025, 1, 15, 14, 30, 5, tzinfo=UTC),
    )


@pytest.fixture()
def sample_position():
    """An open AAPL position."""
    return Position(
        symbol="AAPL",
        quantity=10.0,
        avg_entry_price=150.0,
        current_price=155.0,
        market_value=1550.0,
        unrealized_pnl=50.0,
        unrealized_pnl_pct=0.0333,
    )


@pytest.fixture()
def sample_account():
    """A paper trading account."""
    return AccountInfo(
        equity=10000.0,
        cash=5000.0,
        buying_power=5000.0,
        portfolio_value=10000.0,
        daily_pnl=150.0,
    )
