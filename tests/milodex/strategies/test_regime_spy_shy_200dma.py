"""Tests for the SPY/SHY 200-DMA regime strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.regime_spy_shy_200dma import RegimeSpyShy200DmaStrategy


def test_regime_strategy_matches_golden_signal_sequence():
    strategy = RegimeSpyShy200DmaStrategy()
    closes = [10.0, 10.0, 10.0, 12.0, 13.0, 8.0, 7.0]
    # Equity 10,000 and allocation_pct 1.0 → shares = floor(equity / close):
    #   idx 2  close 10 → 1000 SHY
    #   idx 3  close 12 → sell 1000 SHY, buy floor(10000/12) = 833 SPY
    #   idx 5  close  8 → sell 833 SPY,  buy floor(10000/8)  = 1250 SHY
    expected = [
        [],
        [],
        [("buy", "SHY", 1000.0)],
        [("sell", "SHY", 1000.0), ("buy", "SPY", 833.0)],
        [],
        [("sell", "SPY", 833.0), ("buy", "SHY", 1250.0)],
        [],
    ]
    positions: dict[str, float] = {}

    actual: list[list[tuple[str, str, float]]] = []
    for idx in range(len(closes)):
        context = build_strategy_context(
            positions=positions,
            ma_filter_length=3,
            allocation_pct=1.0,
            equity=10_000.0,
        )
        intents = strategy.evaluate(build_barset(closes[: idx + 1]), context)
        actual.append([(intent.side.value, intent.symbol, intent.quantity) for intent in intents])
        positions = apply_position_changes(positions, intents)

    assert actual == expected


def test_regime_strategy_requires_no_rebalance_when_already_in_target():
    strategy = RegimeSpyShy200DmaStrategy()
    context = build_strategy_context(
        positions={"SPY": 833.0},
        ma_filter_length=3,
        allocation_pct=1.0,
        equity=10_000.0,
    )

    intents = strategy.evaluate(build_barset([10.0, 10.0, 10.0, 12.0]), context)

    assert intents == []


def test_regime_strategy_skips_buy_when_equity_cannot_afford_a_share():
    strategy = RegimeSpyShy200DmaStrategy()
    context = build_strategy_context(
        positions={},
        ma_filter_length=3,
        allocation_pct=1.0,
        equity=5.0,
    )

    intents = strategy.evaluate(build_barset([10.0, 10.0, 10.0]), context)

    assert intents == []


def test_default_strategy_loader_resolves_regime_strategy():
    loader = StrategyLoader()

    loaded = loader.load(Path("configs/spy_shy_200dma_v1.yaml"))

    assert isinstance(loaded.strategy, RegimeSpyShy200DmaStrategy)
    assert loaded.context.strategy_id == "regime.daily.sma200_rotation.spy_shy.v1"


def build_strategy_context(
    *,
    positions: dict[str, float],
    ma_filter_length: int,
    allocation_pct: float,
    equity: float = 10_000.0,
) -> StrategyContext:
    return StrategyContext(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        family="regime",
        template="daily.sma200_rotation",
        variant="spy_shy",
        version=1,
        config_hash="hash",
        parameters={
            "ma_filter_length": ma_filter_length,
            "risk_on_symbol": "SPY",
            "risk_off_symbol": "SHY",
            "allocation_pct": allocation_pct,
        },
        universe=("SPY", "SHY"),
        universe_ref=None,
        disable_conditions=(),
        config_path="configs/spy_shy_200dma_v1.yaml",
        manifest={},
        positions=positions,
        equity=equity,
    )


def build_barset(closes: list[float]) -> BarSet:
    timestamps = pd.date_range("2025-01-01", periods=len(closes), freq="D", tz=UTC)
    dataframe = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000] * len(closes),
            "vwap": closes,
        }
    )
    return BarSet(dataframe)


def apply_position_changes(
    positions: dict[str, float], intents: list
) -> dict[str, float]:
    updated = dict(positions)
    for intent in intents:
        if intent.side == OrderSide.SELL:
            updated.pop(intent.symbol, None)
        elif intent.side == OrderSide.BUY:
            assert intent.order_type == OrderType.MARKET
            updated[intent.symbol] = intent.quantity
    return updated
