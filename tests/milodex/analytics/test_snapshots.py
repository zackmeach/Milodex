"""Tests for the portfolio and backtest equity snapshot recorders."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from milodex.analytics.snapshots import record_backtest_equity_snapshot, record_daily_snapshot
from milodex.broker.models import AccountInfo, Position
from milodex.broker.simulated import SimulatedBroker
from milodex.core.event_store import EventStore


def _make_broker(
    *, equity: float, cash: float, positions: list[Position] | None = None
) -> SimulatedBroker:
    broker = SimulatedBroker(slippage_pct=0.0, commission_per_trade=0.0)
    broker.update_account(
        AccountInfo(
            equity=equity,
            cash=cash,
            buying_power=cash,
            portfolio_value=equity,
            daily_pnl=0.0,
        )
    )
    broker.set_positions(positions or [])
    return broker


def test_record_daily_snapshot_writes_row(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    broker = _make_broker(equity=100_000.0, cash=100_000.0)

    snap = record_daily_snapshot(
        store,
        broker,
        session_id="sess-1",
        strategy_id="regime.v1",
        recorded_at=datetime(2024, 1, 2, 16, 0),
    )

    assert snap.equity == 100_000.0
    assert snap.cash == 100_000.0
    assert snap.strategy_id == "regime.v1"
    assert snap.positions == []

    rows = store.list_portfolio_snapshots_for_session("sess-1")
    assert len(rows) == 1
    assert rows[0].equity == 100_000.0
    assert rows[0].strategy_id == "regime.v1"
    assert rows[0].session_id == "sess-1"
    assert rows[0].positions == []


def test_record_daily_snapshot_captures_positions(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    pos = Position(
        symbol="SPY",
        quantity=10.0,
        avg_entry_price=400.0,
        current_price=420.0,
        market_value=4200.0,
        unrealized_pnl=200.0,
        unrealized_pnl_pct=0.05,
    )
    broker = _make_broker(equity=105_000.0, cash=95_800.0, positions=[pos])

    record_daily_snapshot(
        store,
        broker,
        session_id="sess-x",
        strategy_id="regime.v1",
        recorded_at=datetime(2024, 2, 1, 16, 0),
    )

    rows = store.list_portfolio_snapshots_for_session("sess-x")
    assert len(rows) == 1
    assert rows[0].positions == [
        {
            "symbol": "SPY",
            "quantity": 10.0,
            "avg_entry_price": 400.0,
            "current_price": 420.0,
            "market_value": 4200.0,
            "unrealized_pnl": 200.0,
            "unrealized_pnl_pct": 0.05,
        }
    ]


def test_record_daily_snapshot_appends_distinct_rows(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    broker = _make_broker(equity=100_000.0, cash=100_000.0)

    record_daily_snapshot(
        store,
        broker,
        session_id="sess-1",
        strategy_id="regime.v1",
        recorded_at=datetime(2024, 1, 2, 16, 0),
    )
    record_daily_snapshot(
        store,
        broker,
        session_id="sess-1",
        strategy_id="regime.v1",
        recorded_at=datetime(2024, 1, 3, 16, 0),
    )

    rows = store.list_portfolio_snapshots_for_session("sess-1")
    assert len(rows) == 2
    assert rows[0].recorded_at == datetime(2024, 1, 2, 16, 0)
    assert rows[1].recorded_at == datetime(2024, 1, 3, 16, 0)


def test_list_portfolio_snapshots_for_strategy(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "milodex.db")
    broker = _make_broker(equity=100_000.0, cash=100_000.0)

    record_daily_snapshot(
        store,
        broker,
        session_id="sess-A",
        strategy_id="regime.v1",
        recorded_at=datetime(2024, 1, 2, 16, 0),
    )
    record_daily_snapshot(
        store,
        broker,
        session_id="sess-B",
        strategy_id="regime.v1",
        recorded_at=datetime(2024, 1, 3, 16, 0),
    )
    record_daily_snapshot(
        store,
        broker,
        session_id="sess-C",
        strategy_id="other.v1",
        recorded_at=datetime(2024, 1, 3, 16, 0),
    )

    regime_rows = store.list_portfolio_snapshots_for_strategy("regime.v1")
    other_rows = store.list_portfolio_snapshots_for_strategy("other.v1")
    assert {r.session_id for r in regime_rows} == {"sess-A", "sess-B"}
    assert len(other_rows) == 1
    assert other_rows[0].session_id == "sess-C"


# ---------------------------------------------------------------------------
# record_backtest_equity_snapshot (ADR 0053)
# ---------------------------------------------------------------------------


def test_record_backtest_equity_snapshot_writes_to_backtest_table(tmp_path: Path) -> None:
    """record_backtest_equity_snapshot writes to backtest_equity_snapshots only."""
    store = EventStore(tmp_path / "milodex.db")
    broker = _make_broker(equity=5000.0, cash=1000.0)

    snap = record_backtest_equity_snapshot(
        store,
        broker,
        session_id="run-abc:w0",
        strategy_id="test.strat.v1",
        backtest_run_id=None,
        recorded_at=datetime(2025, 12, 15, 16, 0),
    )

    assert snap.equity == 5000.0
    assert snap.strategy_id == "test.strat.v1"
    assert snap.daily_pnl is None
    assert snap.backtest_run_id is None

    # Must appear in backtest table
    bt_rows = store.list_backtest_equity_snapshots_for_strategy("test.strat.v1")
    assert len(bt_rows) == 1
    assert bt_rows[0].equity == 5000.0
    assert bt_rows[0].session_id == "run-abc:w0"
    assert bt_rows[0].daily_pnl is None

    # Must NOT touch portfolio_snapshots
    broker_rows = store.list_portfolio_snapshots_for_strategy("test.strat.v1")
    assert broker_rows == []
