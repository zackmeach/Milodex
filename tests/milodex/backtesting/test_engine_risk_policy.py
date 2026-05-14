"""Backtest risk-policy mode tests.

Raw research mode remains the default.  The opt-in constrained mode enforces
structural risk checks during historical replay without applying wall-clock
runtime checks such as stale data or market hours.
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.broker.models import OrderSide, OrderType
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.risk import RiskPolicy
from milodex.strategies.base import DecisionReasoning, StrategyContext, StrategyDecision


def _decision(intents: list[TradeIntent]) -> StrategyDecision:
    return StrategyDecision(
        intents=list(intents),
        reasoning=DecisionReasoning(rule="test", narrative="test"),
    )


def _make_barset(symbol_price: float, start: date, days: int = 5) -> BarSet:
    rows = []
    for offset in range(days):
        rows.append(
            {
                "timestamp": pd.Timestamp(start + timedelta(days=offset), tz="UTC"),
                "open": symbol_price,
                "high": symbol_price,
                "low": symbol_price,
                "close": symbol_price,
                "volume": 1_000,
                "vwap": symbol_price,
            }
        )
    return BarSet(pd.DataFrame(rows))


def _write_strategy_yaml(tmp_dir: Path, *, max_position_pct: float, max_positions: int) -> Path:
    path = tmp_dir / "strategy.yaml"
    path.write_text(
        f"""
strategy:
  id: "test.risk_policy.v1"
  name: "test.risk_policy.v1"
  family: "test"
  template: "daily.test"
  variant: "v"
  version: 1
  description: "Risk-policy mode fixture."
  enabled: true
  universe: ["SPY", "AAPL"]
  parameters: {{}}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: {max_position_pct}
    max_positions: {max_positions}
    daily_loss_cap_pct: 0.05
    stop_loss_pct: null
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: 30
""".strip(),
        encoding="utf-8",
    )
    return path


def _write_risk_defaults(tmp_dir: Path) -> Path:
    path = tmp_dir / "risk_defaults.yaml"
    path.write_text(
        """
kill_switch:
  enabled: true
  max_drawdown_pct: 0.10
  require_manual_reset: true
portfolio:
  max_single_position_pct: 1.00
  max_concurrent_positions: 10
  max_total_exposure_pct: 1.00
daily_limits:
  max_daily_loss_pct: 0.03
  max_trades_per_day: 20
order_safety:
  max_order_value_pct: 1.00
  duplicate_order_window_seconds: 60
  max_data_staleness_seconds: 300
backtesting:
  min_universe_coverage_pct: 1.00
  slippage_pct_default: 0.0
""".strip(),
        encoding="utf-8",
    )
    return path


def _make_loaded(config_path: Path, *, universe: tuple[str, ...] = ("SPY", "AAPL")):
    config = MagicMock()
    config.strategy_id = "test.risk_policy.v1"
    config.family = "test"
    config.template = "daily.test"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.risk = {
        "max_position_pct": 0.10,
        "max_positions": 1,
        "daily_loss_cap_pct": 0.05,
        "stop_loss_pct": None,
    }
    config.universe = universe

    context = StrategyContext(
        strategy_id=config.strategy_id,
        family="test",
        template="daily.test",
        variant="v",
        version=1,
        config_hash="risk-policy-test",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path=str(config_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = _decision([])

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def _make_engine(
    *,
    loaded,
    risk_defaults_path: Path,
    risk_policy: RiskPolicy = RiskPolicy.BYPASS,
    initial_equity: float = 10_000.0,
) -> tuple[BacktestEngine, EventStore, date]:
    start = date(2024, 1, 2)
    provider = MagicMock()
    provider.get_bars.return_value = {
        "SPY": _make_barset(100.0, start),
        "AAPL": _make_barset(100.0, start),
    }
    store = EventStore(Path(tempfile.mktemp(suffix=".db")))
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=initial_equity,
        risk_defaults_path=risk_defaults_path,
        risk_policy=risk_policy,
    )
    return engine, store, start


def test_backtest_engine_defaults_to_bypass_policy(tmp_path: Path) -> None:
    config_path = _write_strategy_yaml(tmp_path, max_position_pct=0.10, max_positions=1)
    risk_defaults = _write_risk_defaults(tmp_path)
    loaded = _make_loaded(config_path)

    engine, _, _ = _make_engine(loaded=loaded, risk_defaults_path=risk_defaults)

    assert engine.risk_policy is RiskPolicy.BYPASS


def test_enforce_policy_blocks_oversized_buy_and_records_block(tmp_path: Path) -> None:
    config_path = _write_strategy_yaml(tmp_path, max_position_pct=0.10, max_positions=1)
    risk_defaults = _write_risk_defaults(tmp_path)
    loaded = _make_loaded(config_path)
    calls = [0]

    def evaluate(_bars, _context):
        calls[0] += 1
        if calls[0] == 1:
            return _decision([_intent("SPY", 20.0)])
        return _decision([])

    loaded.strategy.evaluate.side_effect = evaluate
    engine, store, start = _make_engine(
        loaded=loaded,
        risk_defaults_path=risk_defaults,
        risk_policy=RiskPolicy.ENFORCE,
    )

    result = engine.run(start, start + timedelta(days=2))

    assert result.risk_policy is RiskPolicy.ENFORCE
    assert result.trade_count == 0
    assert result.final_equity == pytest.approx(10_000.0)
    trades = store.list_trades_for_backtest_run(result.db_id)
    assert len(trades) == 1
    assert trades[0].status == "blocked"
    assert trades[0].broker_order_id is None
    explanations = store.list_explanations()
    assert any("max_single_position_exceeded" in e.reason_codes for e in explanations)


def test_bypass_policy_allows_same_oversized_buy_when_cash_permits(tmp_path: Path) -> None:
    config_path = _write_strategy_yaml(tmp_path, max_position_pct=0.10, max_positions=1)
    risk_defaults = _write_risk_defaults(tmp_path)
    loaded = _make_loaded(config_path)
    calls = [0]

    def evaluate(_bars, _context):
        calls[0] += 1
        if calls[0] == 1:
            return _decision([_intent("SPY", 20.0)])
        return _decision([])

    loaded.strategy.evaluate.side_effect = evaluate
    engine, store, start = _make_engine(
        loaded=loaded,
        risk_defaults_path=risk_defaults,
        risk_policy=RiskPolicy.BYPASS,
    )

    result = engine.run(start, start + timedelta(days=2))

    assert result.risk_policy is RiskPolicy.BYPASS
    assert result.trade_count == 1
    trades = store.list_trades_for_backtest_run(result.db_id)
    assert len(trades) == 1
    assert trades[0].status == "submitted"


def test_enforce_policy_blocks_second_position_after_first_fill(tmp_path: Path) -> None:
    config_path = _write_strategy_yaml(tmp_path, max_position_pct=0.50, max_positions=1)
    risk_defaults = _write_risk_defaults(tmp_path)
    loaded = _make_loaded(config_path)

    def evaluate(bars, context):
        current_day = pd.to_datetime(bars.to_dataframe()["timestamp"], utc=True).dt.date.max()
        if current_day == start:
            return _decision([_intent("SPY", 10.0)])
        if current_day == start + timedelta(days=1):
            return _decision([_intent("AAPL", 10.0)])
        return _decision([])

    engine, store, start = _make_engine(
        loaded=loaded,
        risk_defaults_path=risk_defaults,
        risk_policy=RiskPolicy.ENFORCE,
    )
    loaded.strategy.evaluate.side_effect = evaluate

    result = engine.run(start, start + timedelta(days=3))

    assert result.buy_count == 1
    assert result.trade_count == 1
    trades = store.list_trades_for_backtest_run(result.db_id)
    assert [trade.status for trade in trades] == ["submitted", "blocked"]
    assert [trade.symbol for trade in trades] == ["SPY", "AAPL"]
    explanations = store.list_explanations()
    assert any("max_strategy_positions_exceeded" in e.reason_codes for e in explanations)


def _intent(symbol: str, quantity: float) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )
