"""Tests for the ATR/Keltner channel breakout strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.breakout_atr_channel import BreakoutAtrChannelStrategy


def test_atr_channel_enters_when_close_clears_upper_channel() -> None:
    strategy = BreakoutAtrChannelStrategy()
    bars = _ramp_then_channel_break()
    context = _context(bars_by_symbol={"XLK": _barset(bars), "SPY": _barset(_bullish_spy())})

    decision = strategy.evaluate(_barset(bars), context)

    assert [(i.side, i.symbol) for i in decision.intents] == [(OrderSide.BUY, "XLK")]
    assert decision.reasoning.rule == "breakout.atr_channel_entry"


def test_atr_channel_regime_filter_blocks_entries() -> None:
    strategy = BreakoutAtrChannelStrategy()
    bars = _ramp_then_channel_break()
    context = _context(bars_by_symbol={"XLK": _barset(bars), "SPY": _barset(_bearish_spy())})

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_atr_channel_exits_when_close_below_ema() -> None:
    strategy = BreakoutAtrChannelStrategy()
    bars = _ramp_then_channel_break()
    bars[-1] = (90.0, 91.0, 89.0, 90.0)
    context = _context(
        positions={"XLK": 10.0},
        bars_by_symbol={"XLK": _barset(bars), "SPY": _barset(_bullish_spy())},
        entry_state={"XLK": {"entry_price": 100.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_barset(bars), context)

    assert [(i.side, i.symbol, i.quantity) for i in decision.intents] == [
        (OrderSide.SELL, "XLK", 10.0)
    ]
    assert decision.reasoning.rule == "breakout.atr_channel_exit"


def test_atr_channel_exits_on_atr_stop() -> None:
    strategy = BreakoutAtrChannelStrategy()
    bars = _ramp_then_channel_break()
    bars[-1] = (70.0, 71.0, 69.0, 70.0)
    context = _context(
        positions={"XLK": 10.0},
        bars_by_symbol={"XLK": _barset(bars), "SPY": _barset(_bullish_spy())},
        entry_state={"XLK": {"entry_price": 100.0, "held_days": 1}},
        override_parameters={"atr_stop_multiplier": 1.0},
    )

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.rule == "breakout.atr_stop"
    assert decision.intents[0].side == OrderSide.SELL


def test_atr_channel_entry_payload_is_rich() -> None:
    """Entry triggering_values include selected_close and selected_breakout_strength."""
    strategy = BreakoutAtrChannelStrategy()
    bars = _ramp_then_channel_break()
    context = _context(bars_by_symbol={"XLK": _barset(bars), "SPY": _barset(_bullish_spy())})

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.rule == "breakout.atr_channel_entry"
    tv = decision.reasoning.triggering_values
    assert "selected_symbol" in tv
    assert "selected_close" in tv
    assert "selected_breakout_strength" in tv
    assert tv["selected_symbol"] == "XLK"
    assert isinstance(tv["selected_close"], float)
    assert isinstance(tv["selected_breakout_strength"], float)
    # Threshold must expose the entry-rule-defining parameters
    assert "atr_entry_multiplier" in decision.reasoning.threshold
    assert "ema_length" in decision.reasoning.threshold
    assert "atr_lookback" in decision.reasoning.threshold


def test_atr_channel_ranking_payload_populated() -> None:
    """When ranking_enabled, the ranking list contains the signal label."""
    strategy = BreakoutAtrChannelStrategy()
    bars = _ramp_then_channel_break()
    context = _context(bars_by_symbol={"XLK": _barset(bars), "SPY": _barset(_bullish_spy())})

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.ranking is not None
    assert len(decision.reasoning.ranking) >= 1
    first = decision.reasoning.ranking[0]
    assert "symbol" in first
    assert "breakout_strength" in first
    assert "latest_close" in first


def test_atr_channel_at_capacity_takes_precedence_over_regime_bearish() -> None:
    """Shared helper order: capacity-zero fires before regime-bearish check.

    The OLD atr_channel code checked regime BEFORE capacity. The shared
    evaluate_pre_entry_gates helper checks capacity FIRST. When both conditions
    hold (positions full AND regime bearish), the shared helper returns the
    at-capacity decision — not the regime-bearish decision. This test pins
    the new (correct) ordering.
    """
    strategy = BreakoutAtrChannelStrategy()
    bars = _ramp_then_channel_break()
    # XLK position open, max_concurrent_positions=1 → capacity=0 AND regime bearish
    context = _context(
        positions={"XLK": 10.0},
        bars_by_symbol={
            "XLK": _barset(bars),
            "SPY": _barset(_bearish_spy()),  # regime IS bearish
        },
        override_parameters={"max_concurrent_positions": 1},
    )

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.rule == "no_signal"
    # Must be the capacity message, not the regime message
    assert "capacity" in decision.reasoning.narrative
    assert "bearish" not in decision.reasoning.narrative


def test_loader_resolves_atr_channel_strategy_config() -> None:
    loaded = StrategyLoader().load(Path("configs/breakout_daily_atr_channel_sector_etfs_v1.yaml"))

    assert isinstance(loaded.strategy, BreakoutAtrChannelStrategy)
    assert loaded.context.strategy_id == "breakout.daily.atr_channel.sector_etfs.v1"


def _context(
    *,
    positions: dict[str, float] | None = None,
    bars_by_symbol: dict[str, BarSet],
    entry_state: dict[str, dict[str, object]] | None = None,
    override_parameters: dict[str, object] | None = None,
) -> StrategyContext:
    parameters: dict[str, object] = {
        "ema_length": 5,
        "atr_lookback": 5,
        "atr_entry_multiplier": 1.0,
        "atr_stop_multiplier": 1.5,
        "max_hold_days": 5,
        "max_concurrent_positions": 2,
        "sizing_rule": "equal_notional",
        "per_position_notional_pct": 0.20,
        "ranking_enabled": True,
        "ranking_metric": "atr_channel_strength_descending",
        "market_regime_symbol": "SPY",
        "market_regime_ma_length": 5,
    }
    if override_parameters:
        parameters.update(override_parameters)
    return StrategyContext(
        strategy_id="breakout.daily.atr_channel.sector_etfs.v1",
        family="breakout",
        template="daily.atr_channel",
        variant="sector_etfs",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=("XLK",),
        universe_ref="universe.sector_etfs_spdr.v1",
        disable_conditions=(),
        config_path="configs/breakout_daily_atr_channel_sector_etfs_v1.yaml",
        manifest={},
        positions=positions or {},
        equity=10_000.0,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _barset(bars: list[tuple[float, float, float, float]]) -> BarSet:
    timestamps = pd.date_range("2025-01-01", periods=len(bars), freq="D", tz=UTC)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": [b[0] for b in bars],
                "high": [b[1] for b in bars],
                "low": [b[2] for b in bars],
                "close": [b[3] for b in bars],
                "volume": [1_000_000] * len(bars),
                "vwap": [b[3] for b in bars],
            }
        )
    )


def _ramp_then_channel_break() -> list[tuple[float, float, float, float]]:
    bars = [(100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i) for i in range(20)]
    bars[-1] = (124.0, 126.0, 123.0, 126.0)
    return bars


def _bullish_spy() -> list[tuple[float, float, float, float]]:
    return [(100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i) for i in range(20)]


def _bearish_spy() -> list[tuple[float, float, float, float]]:
    return [(120.0 - i, 121.0 - i, 119.0 - i, 120.0 - i) for i in range(20)]
