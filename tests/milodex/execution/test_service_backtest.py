"""Tests for the submit_backtest seam on ExecutionService.

Covers the unified execution path: a backtest intent flows through the
same :meth:`_submit` body the live paper path uses, but with a
:class:`NullRiskEvaluator` injected so no real risk check fires, and
with ``source='backtest'`` + ``backtest_run_id`` recorded on the
resulting :class:`TradeEvent`.
"""

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
from milodex.core.event_store import BacktestRunEvent, EventStore
from milodex.data.models import Bar
from milodex.execution import ExecutionService, ExecutionStatus, TradeIntent
from milodex.execution.state import KillSwitchStateStore
from milodex.risk import BYPASS_SUMMARY, NullRiskEvaluator


class _Broker:
    def __init__(self, account: AccountInfo, filled: Order) -> None:
        self.account = account
        self.filled = filled
        self.submit_calls: list[dict[str, object]] = []

    def get_account(self) -> AccountInfo:
        return self.account

    def get_positions(self):
        return []

    def get_orders(self, status: str = "all", limit: int = 100):
        return []

    def is_market_open(self) -> bool:
        return True

    def submit_order(self, **kwargs) -> Order:
        self.submit_calls.append(kwargs)
        return self.filled

    def get_order(self, order_id: str) -> Order:
        return self.filled

    def cancel_order(self, order_id: str) -> bool:
        return False


class _Provider:
    def __init__(self, bar: Bar) -> None:
        self.bar = bar

    def get_latest_bar(self, symbol: str) -> Bar:
        return self.bar


class _SpyEvaluator(NullRiskEvaluator):
    """NullRiskEvaluator that records how many times it was consulted."""

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, context):  # type: ignore[override]
        self.calls += 1
        return super().evaluate(context)


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


def _make_service(
    tmp_path: Path,
    risk_defaults_file: Path,
    evaluator: NullRiskEvaluator | None = None,
) -> tuple[ExecutionService, _Broker, EventStore]:
    account = AccountInfo(
        equity=100_000.0,
        cash=100_000.0,
        buying_power=100_000.0,
        portfolio_value=100_000.0,
        daily_pnl=0.0,
    )
    bar = Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=30),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000_000,
        vwap=100.0,
    )
    filled = Order(
        id="sim-1",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=5.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.FILLED,
        submitted_at=datetime.now(tz=UTC),
        filled_quantity=5.0,
        filled_avg_price=100.0,
        filled_at=datetime.now(tz=UTC),
    )
    broker = _Broker(account, filled)
    provider = _Provider(bar)
    store = EventStore(tmp_path / "milodex.db")
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=KillSwitchStateStore(event_store=store),
        risk_evaluator=evaluator or NullRiskEvaluator(),
        event_store=store,
    )
    return service, broker, store


def _seed_backtest_run(store: EventStore, run_id: str = "bt-run-1") -> int:
    now = datetime.now(tz=UTC)
    return store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id="test.strategy.v1",
            config_path=None,
            config_hash=None,
            start_date=now,
            end_date=now,
            started_at=now,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={},
        )
    )


def test_submit_backtest_returns_submitted_result(tmp_path, risk_defaults_file):
    service, broker, store = _make_service(tmp_path, risk_defaults_file)
    run_row_id = _seed_backtest_run(store)
    result = service.submit_backtest(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        session_id="bt-session",
        backtest_run_id=run_row_id,
    )
    assert result.status == ExecutionStatus.SUBMITTED
    assert result.risk_decision.allowed is True
    assert result.risk_decision.summary == BYPASS_SUMMARY
    assert broker.submit_calls, "broker should have been called"


def test_submit_backtest_writes_trade_row_with_backtest_source(tmp_path, risk_defaults_file):
    service, _, store = _make_service(tmp_path, risk_defaults_file)
    run_row_id = _seed_backtest_run(store)
    service.submit_backtest(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        session_id="bt-session",
        backtest_run_id=run_row_id,
    )
    trades = store.list_trades()
    assert len(trades) == 1
    trade = trades[0]
    assert trade.source == "backtest"
    assert trade.backtest_run_id == run_row_id


def test_submit_backtest_consults_null_evaluator_not_real_risk(tmp_path, risk_defaults_file):
    evaluator = _SpyEvaluator()
    service, _, store = _make_service(tmp_path, risk_defaults_file, evaluator=evaluator)
    run_row_id = _seed_backtest_run(store)
    service.submit_backtest(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        session_id="bt-session",
        backtest_run_id=run_row_id,
    )
    assert evaluator.calls == 1


def test_submit_backtest_enriches_explanation_context(tmp_path, risk_defaults_file):
    """R-XC-008: backtest explanation rows carry rule/config_hash/bar_timestamp."""
    service, _, store = _make_service(tmp_path, risk_defaults_file)
    run_row_id = _seed_backtest_run(store)
    service.submit_backtest(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        session_id="bt-session",
        backtest_run_id=run_row_id,
    )
    explanations = store.list_explanations()
    assert len(explanations) == 1
    context = explanations[0].context
    assert context["rule"] == "fill_simulation"
    assert "config_hash" in context
    assert context["bar_timestamp"] is not None


def test_submit_paper_omits_backtest_context_fields(tmp_path, risk_defaults_file):
    """Paper-path explanations should NOT carry the backtest-only context keys."""
    service, _, store = _make_service(tmp_path, risk_defaults_file)
    service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        session_id="paper-session",
    )
    explanations = store.list_explanations()
    assert len(explanations) == 1
    context = explanations[0].context
    assert "rule" not in context
    assert "bar_timestamp" not in context


def test_submit_paper_still_writes_source_paper(tmp_path, risk_defaults_file):
    service, _, store = _make_service(tmp_path, risk_defaults_file)
    service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        session_id="paper-session",
    )
    trades = store.list_trades()
    assert len(trades) == 1
    assert trades[0].source == "paper"
    assert trades[0].backtest_run_id is None
