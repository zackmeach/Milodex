# tests/conftest.py
"""Shared test fixtures for Milodex."""

from datetime import UTC, datetime
from pathlib import Path

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


@pytest.fixture(autouse=True)
def _isolate_milodex_data_dirs(tmp_path, monkeypatch):
    """Force every test into a tmp_path-based data root.

    Without this, any test that constructs ``ExecutionService`` /
    ``KillSwitchStateStore`` without an explicit ``event_store=`` ends
    up writing to the real ``data/milodex.db`` (see service.py default
    path). That pollutes the operator's audit trail every CI run. This
    fixture redirects the three known config knobs and verifies the
    redirect actually took effect — if a future code path adds a new
    default-path leak, this guard fires.
    """
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"
    locks_dir = data_dir / "locks"
    monkeypatch.setenv("MILODEX_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MILODEX_LOG_DIR", str(log_dir))
    monkeypatch.setenv("MILODEX_LOCKS_DIR", str(locks_dir))

    from milodex.config import get_data_dir, get_locks_dir, get_logs_dir

    assert get_data_dir() == data_dir, "MILODEX_DATA_DIR override failed"
    assert get_logs_dir() == log_dir, "MILODEX_LOG_DIR override failed"
    assert get_locks_dir() == locks_dir, "MILODEX_LOCKS_DIR override failed"
    yield


@pytest.fixture(autouse=True)
def _guard_real_event_store_untouched():
    """Snapshot the real event store before each test, restore if changed.

    Belt-and-braces: the env-var fixture above should prevent every leak,
    but if a test bypasses ``get_data_dir()`` (e.g. constructs an
    EventStore with a hardcoded ``data/milodex.db`` path), this guard
    catches it on next run and fails loudly.
    """
    real_db = Path(__file__).resolve().parent.parent / "data" / "milodex.db"
    if real_db.exists():
        before_mtime = real_db.stat().st_mtime_ns
        before_size = real_db.stat().st_size
    else:
        before_mtime = None
        before_size = None
    yield
    if real_db.exists() and before_mtime is not None:
        after_mtime = real_db.stat().st_mtime_ns
        after_size = real_db.stat().st_size
        assert (before_mtime, before_size) == (after_mtime, after_size), (
            f"Test wrote to real production event store {real_db}. "
            "Pass an explicit isolated event_store to ExecutionService "
            "or use the autouse _isolate_milodex_data_dirs fixture."
        )


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
