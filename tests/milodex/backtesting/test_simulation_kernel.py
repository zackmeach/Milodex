"""Tests for the shared backtest simulation kernel helpers."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from milodex.backtesting import engine as engine_module
from milodex.backtesting.simulation_kernel import (
    BacktestSimulationKernel,
    IntradayPendingOrder,
    MissingOpenPolicy,
    PendingOrder,
)
from milodex.broker.models import OrderSide, OrderType
from milodex.core.event_store import BacktestRunEvent, EventStore
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.risk import NullRiskEvaluator
from milodex.strategies.base import DecisionReasoning


def _barset(symbol_start: float = 100.0) -> BarSet:
    return BarSet(
        pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2024-01-02", tz="UTC"),
                    "open": symbol_start,
                    "high": symbol_start + 1.0,
                    "low": symbol_start - 1.0,
                    "close": symbol_start,
                    "volume": 1000,
                    "vwap": symbol_start,
                }
            ]
        )
    )


def _event_store() -> EventStore:
    return EventStore(Path(tempfile.mktemp(suffix=".db")))


def _append_run(store: EventStore, *, run_id: str = "kernel-run") -> int:
    now = datetime.now(tz=UTC)
    return store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id="kernel.test.v1",
            config_path=None,
            config_hash="kernel-hash",
            start_date=now,
            end_date=now,
            started_at=now,
            status="running",
            slippage_pct=0.0,
            commission_per_trade=0.0,
            metadata={},
        )
    )


def _strategy_config_path() -> Path:
    tmp_dir = Path(tempfile.mkdtemp())
    path = tmp_dir / "kernel_strategy.yaml"
    path.write_text(
        """\
strategy:
  name: "kernel_test"
  version: 1
  description: "Kernel test strategy."
  enabled: true
  universe: ["SPY", "QQQ"]
  parameters: {}
  tempo:
    bar_size: "1D"
    min_hold_days: 0
    max_hold_days: 1
  risk:
    max_position_pct: 0.50
    max_positions: 4
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: 1
""",
        encoding="utf-8",
    )
    return path


def _kernel(store: EventStore, *, initial_cash: float = 1_000.0) -> BacktestSimulationKernel:
    return BacktestSimulationKernel(
        event_store=store,
        all_bars={"SPY": _barset(100.0), "QQQ": _barset(200.0)},
        strategy_id="kernel.test.v1",
        strategy_stage="backtest",
        strategy_config_path=_strategy_config_path(),
        config_hash="kernel-hash",
        risk_defaults_path=Path("configs/risk_defaults.yaml"),
        risk_evaluator=NullRiskEvaluator(),
        slippage_pct=0.0,
        commission_per_trade=0.0,
        initial_cash=initial_cash,
        max_positions=4,
        max_position_pct=0.50,
        daily_loss_cap_pct=0.05,
    )


def _intent(symbol: str, side: OrderSide, quantity: float) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )


def _reasoning() -> DecisionReasoning:
    return DecisionReasoning(rule="kernel_test", narrative="kernel test")


def test_kernel_drains_sells_before_buys_and_updates_state() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store, initial_cash=100.0)
    kernel.positions["SPY"] = (1.0, 90.0)
    kernel.entry_state["SPY"] = {"entry_price": 90.0, "held_days": 2}
    kernel.sym_fills["SPY"] = {"buys": 1, "sells": 0}

    result = kernel.drain_pending_orders(
        pending=[
            PendingOrder(_intent("QQQ", OrderSide.BUY, 1.0), _reasoning()),
            PendingOrder(_intent("SPY", OrderSide.SELL, 1.0), _reasoning()),
        ],
        opens={"SPY": 100.0, "QQQ": 150.0},
        day=date(2024, 1, 2),
        session_id="kernel-session",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.SKIP,
    )

    assert result.sell_count == 1
    assert result.buy_count == 1
    assert result.skipped_count == 0
    assert result.remaining == []
    assert kernel.cash == 50.0
    assert "SPY" not in kernel.positions
    assert kernel.positions["QQQ"] == (1.0, 150.0)
    assert "SPY" not in kernel.entry_state
    assert kernel.entry_state["QQQ"] == {"entry_price": 150.0, "held_days": 0}
    assert kernel.round_trip_count() == 1


def test_daily_missing_next_open_records_existing_skip_reason() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)

    result = kernel.drain_pending_orders(
        pending=[PendingOrder(_intent("QQQ", OrderSide.BUY, 1.0), _reasoning())],
        opens={},
        day=date(2024, 1, 2),
        session_id="kernel-session",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.SKIP,
    )

    assert result.skipped_count == 1
    assert result.remaining == []
    skipped = [event for event in store.list_explanations() if event.status == "skipped"]
    assert skipped[0].reason_codes == ["backtest_missing_next_open"]


def test_intraday_missing_open_retains_pending_without_skip_audit() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)
    pending = [
        IntradayPendingOrder(
            intent=_intent("QQQ", OrderSide.BUY, 1.0),
            reasoning=_reasoning(),
            decision_timestamp=pd.Timestamp("2024-01-02 15:00:00+00:00"),
        )
    ]

    result = kernel.drain_pending_orders(
        pending=pending,
        opens={},
        day=date(2024, 1, 2),
        session_id="kernel-session",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.RETAIN,
    )

    assert result.buy_count == 0
    assert result.sell_count == 0
    assert result.skipped_count == 0
    assert result.remaining == pending
    assert [event for event in store.list_explanations() if event.status == "skipped"] == []


def test_stranded_pending_orders_record_no_next_bar_skip() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)

    skipped = kernel.record_stranded_orders(
        pending=[PendingOrder(_intent("SPY", OrderSide.BUY, 1.0), _reasoning())],
        day=date(2024, 1, 2),
        latest_closes={"SPY": 100.0},
        session_id="kernel-session",
        db_run_id=db_run_id,
    )

    assert skipped == 1
    skipped_events = [event for event in store.list_explanations() if event.status == "skipped"]
    assert skipped_events[0].reason_codes == ["backtest_no_next_bar"]


def test_final_snapshot_uses_backtest_snapshot_table_only() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)

    kernel.record_final_snapshot(
        session_id="kernel-session",
        db_run_id=db_run_id,
        recorded_at=datetime(2024, 1, 2, tzinfo=UTC),
    )

    assert store.list_backtest_equity_snapshots_for_strategy("kernel.test.v1")
    assert store.list_portfolio_snapshots_for_strategy("kernel.test.v1") == []


def test_backtest_engine_delegates_shared_simulation_mechanics_to_kernel() -> None:
    source = Path(engine_module.__file__).read_text(encoding="utf-8")

    for token in (
        "def _drain_pending(",
        "def _drain_pending_at_timestamp(",
        "def _record_skipped_order(",
        "def _sync_broker_state(",
        "record_backtest_equity_snapshot",
    ):
        assert token not in source
