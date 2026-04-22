"""Tests for strategy loading and config hashing."""

from __future__ import annotations

from pathlib import Path

import pytest

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies.base import Strategy, StrategyContext, StrategyParameterSpec
from milodex.strategies.loader import (
    StrategyConfig,
    StrategyLoader,
    StrategyRegistry,
    compute_config_hash,
)


class DummyStrategy(Strategy):
    family = "dummy"
    template = "daily.test"
    parameter_specs = (
        StrategyParameterSpec("required_threshold", expected_types=(int, float)),
        StrategyParameterSpec("optional_flag", expected_types=(bool,), required=False),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> list[TradeIntent]:
        return [
            TradeIntent(
                symbol="SPY",
                side=OrderSide.BUY,
                quantity=1.0,
                order_type=OrderType.MARKET,
            )
        ]


@pytest.fixture()
def registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(DummyStrategy)
    return registry


@pytest.fixture()
def valid_strategy_config(tmp_path: Path) -> Path:
    path = tmp_path / "dummy_strategy.yaml"
    path.write_text(
        """
strategy:
  id: "dummy.daily.test.paper.v1"
  family: "dummy"
  template: "daily.test"
  variant: "paper"
  version: 1
  description: "Dummy strategy for tests."
  enabled: true
  universe:
    - "SPY"
  parameters:
    required_threshold: 10
    optional_flag: true
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional:
    - "manual_test_pause"
""".strip(),
        encoding="utf-8",
    )
    return path


def test_loader_rejects_unknown_strategy_id(tmp_path: Path):
    path = tmp_path / "unknown_strategy.yaml"
    path.write_text(
        """
strategy:
  id: "unknown.daily.test.paper.v1"
  family: "unknown"
  template: "daily.test"
  variant: "paper"
  version: 1
  description: "Unknown strategy."
  enabled: true
  universe:
    - "SPY"
  parameters:
    lookback: 10
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )

    loader = StrategyLoader()

    with pytest.raises(ValueError, match="No strategy is registered"):
        loader.load(path)


def test_loader_rejects_missing_required_params(
    valid_strategy_config: Path, registry: StrategyRegistry
):
    contents = valid_strategy_config.read_text(encoding="utf-8").replace(
        "required_threshold: 10\n",
        "",
    )
    valid_strategy_config.write_text(contents, encoding="utf-8")

    loader = StrategyLoader(registry=registry)

    with pytest.raises(ValueError, match="missing required parameter"):
        loader.load(valid_strategy_config)


def test_identical_configs_hash_identically(tmp_path: Path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(
        """
strategy:
  id: "dummy.daily.test.paper.v1"
  family: "dummy"
  template: "daily.test"
  variant: "paper"
  version: 1
  description: "Dummy strategy."
  enabled: true
  universe:
    - "SPY"
  parameters:
    required_threshold: 10
    optional_flag: true
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )
    second.write_text(
        """
strategy:
  description: "Dummy strategy."
  version: 1
  variant: "paper"
  family: "dummy"
  id: "dummy.daily.test.paper.v1"
  template: "daily.test"
  enabled: true
  parameters:
    optional_flag: true
    required_threshold: 10
  universe:
    - "SPY"
  risk:
    stop_loss_pct: 0.05
    max_positions: 1
    daily_loss_cap_pct: 0.02
    max_position_pct: 0.10
  tempo:
    max_hold_days: 5
    bar_size: "1D"
    min_hold_days: 1
  stage: "backtest"
  disable_conditions_additional: []
  backtest:
    min_trades_required: 30
    commission_per_trade: 0.00
    slippage_pct: 0.001
""".strip(),
        encoding="utf-8",
    )

    assert compute_config_hash(first) == compute_config_hash(second)


def test_loader_builds_strategy_context(valid_strategy_config: Path, registry: StrategyRegistry):
    loader = StrategyLoader(registry=registry)

    loaded = loader.load(valid_strategy_config)

    assert isinstance(loaded.strategy, DummyStrategy)
    assert isinstance(loaded.config, StrategyConfig)
    assert loaded.config.strategy_id == "dummy.daily.test.paper.v1"
    assert loaded.context.config_hash == compute_config_hash(valid_strategy_config)
    assert loaded.context.disable_conditions == ("manual_test_pause",)
    assert loaded.context.universe == ("SPY",)
