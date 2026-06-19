"""No-trade baseline strategy."""

from __future__ import annotations

import pandas as pd

from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.bench_no_trade import BenchNoTradeStrategy
from milodex.strategies.loader import build_default_registry


def _empty_barset() -> BarSet:
    return BarSet(pd.DataFrame(
        columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"]
    ))


def _ctx() -> StrategyContext:
    return StrategyContext(
        strategy_id="benchmark.no_trade.spy.v1", family="benchmark",
        template="no_trade", variant="spy", version=1, config_hash="x",
        parameters={}, universe=("SPY",), universe_ref="universe.spy_only.v1",
        disable_conditions=(), config_path="x", manifest={},
    )


def test_no_trade_never_signals():
    decision = BenchNoTradeStrategy().evaluate(_empty_barset(), _ctx())
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_no_trade_is_registered():
    registry = build_default_registry()
    assert registry.resolve("benchmark", "no_trade") is BenchNoTradeStrategy
