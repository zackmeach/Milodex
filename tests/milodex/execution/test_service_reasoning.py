"""Tests for strategy reasoning threading into ``ExplanationEvent.context``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from milodex.core.event_store import EventStore
from milodex.data.models import Bar
from milodex.execution import ExecutionService, TradeIntent
from milodex.execution.state import KillSwitchStateStore
from milodex.strategies.base import DecisionReasoning

from .test_service import StubBroker, StubProvider


@pytest.fixture()
def risk_defaults_file(tmp_path: Path) -> Path:
    path = tmp_path / "risk_defaults.yaml"
    path.write_text(
        """
kill_switch:
  enabled: true
  max_drawdown_pct: 0.10
  require_manual_reset: true
portfolio:
  max_single_position_pct: 0.20
  max_concurrent_positions: 3
  max_total_exposure_pct: 0.80
daily_limits:
  max_daily_loss_pct: 0.03
  max_trades_per_day: 20
order_safety:
  max_order_value_pct: 0.15
  duplicate_order_window_seconds: 60
  max_data_staleness_seconds: 300
""".strip(),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def latest_bar() -> Bar:
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=30),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )


@pytest.fixture()
def sample_account() -> AccountInfo:
    return AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=0.0,
    )


@pytest.fixture()
def submitted_order() -> Order:
    return Order(
        id="order-reasoning-1",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=5.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.PENDING,
        submitted_at=datetime.now(tz=UTC),
    )


def _build_service_with_store(
    tmp_path: Path,
    risk_defaults_file: Path,
    latest_bar: Bar,
    sample_account: AccountInfo,
    submitted_order: Order,
) -> tuple[ExecutionService, EventStore]:
    broker = StubBroker(account=sample_account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
    )
    return service, event_store


def test_submit_paper_threads_reasoning_into_context(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    service, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )

    reasoning = DecisionReasoning(
        rule="regime.ma_filter_cross",
        narrative="latest close 100.00 above 200-DMA 90.00 → rotate to SPY",
        triggering_values={"latest_close": 100.0, "ma_200": 90.0},
        threshold={"ma_200": 90.0},
    )
    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    service.submit_paper(intent, reasoning=reasoning)

    explanations = event_store.list_explanations()
    assert len(explanations) == 1
    payload = explanations[0].context.get("reasoning")
    assert payload is not None
    assert payload["rule"] == "regime.ma_filter_cross"
    assert payload["narrative"].startswith("latest close 100.00")
    assert payload["triggering_values"]["ma_200"] == 90.0


def test_submit_backtest_threads_reasoning_and_replaces_fill_simulation(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    from milodex.risk import NullRiskEvaluator

    broker = StubBroker(account=sample_account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
        risk_evaluator=NullRiskEvaluator(),
    )

    reasoning = DecisionReasoning(
        rule="meanrev.rsi_entry",
        narrative="RSI 2.4 below entry threshold 5 — buy AAPL",
        triggering_values={"selected_rsi": 2.4},
        threshold={"rsi_entry_threshold": 5.0},
    )

    service.submit_backtest(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        reasoning=reasoning,
        backtest_run_id=None,
    )

    explanations = event_store.list_explanations()
    assert len(explanations) == 1
    context = explanations[0].context
    # The old "fill_simulation" hardcoded rule block is replaced by the real
    # reasoning payload from the strategy.
    assert "rule" not in context or context.get("rule") != "fill_simulation"
    assert context["reasoning"]["rule"] == "meanrev.rsi_entry"


def test_submit_without_reasoning_leaves_context_reasoning_absent(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    service, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )

    service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    explanations = event_store.list_explanations()
    assert "reasoning" not in explanations[0].context
