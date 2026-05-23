"""Tests for the NR7 volatility-contraction breakout strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.breakout_nr7_inside import BreakoutNr7InsideStrategy


def test_nr7_enters_on_narrowest_range_green_close_above_sma() -> None:
    strategy = BreakoutNr7InsideStrategy()
    bars = _nr7_entry_bars()
    context = _context(bars_by_symbol={"AAPL": _barset(bars)}, equity=10_000.0)

    decision = strategy.evaluate(_barset(bars), context)

    assert [(i.side, i.symbol) for i in decision.intents] == [(OrderSide.BUY, "AAPL")]
    assert decision.reasoning.rule == "breakout.nr7_entry"


def test_nr7_rejects_when_latest_range_is_not_narrowest_of_seven() -> None:
    strategy = BreakoutNr7InsideStrategy()
    bars = _nr7_entry_bars()
    open_, high, low, close = bars[-1]
    bars[-1] = (open_, high + 5.0, low, close)
    context = _context(bars_by_symbol={"AAPL": _barset(bars)}, equity=10_000.0)

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert "NR7" in decision.reasoning.rejected_alternatives[0]["reason"]


def test_nr7_exits_on_time_stop() -> None:
    strategy = BreakoutNr7InsideStrategy()
    bars = _flat_bars()
    context = _context(
        positions={"AAPL": 10.0},
        bars_by_symbol={"AAPL": _barset(bars)},
        entry_state={"AAPL": {"entry_price": 100.0, "held_days": 3}},
    )

    decision = strategy.evaluate(_barset(bars), context)

    assert [(i.side, i.symbol, i.quantity) for i in decision.intents] == [
        (OrderSide.SELL, "AAPL", 10.0)
    ]
    assert decision.reasoning.rule == "breakout.nr7_max_hold"


def test_nr7_exits_on_prior_low_trail_once_profitable() -> None:
    strategy = BreakoutNr7InsideStrategy()
    bars = _flat_bars()
    bars[-2] = (104.0, 105.0, 103.0, 104.0)
    bars[-1] = (102.0, 103.0, 101.0, 102.0)
    context = _context(
        positions={"AAPL": 10.0},
        bars_by_symbol={"AAPL": _barset(bars)},
        entry_state={"AAPL": {"entry_price": 100.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.rule == "breakout.nr7_trailing_low"
    assert decision.intents[0].side == OrderSide.SELL


def test_nr7_entry_payload_is_rich() -> None:
    """Entry triggering_values include selected_close and selected_range_value."""
    strategy = BreakoutNr7InsideStrategy()
    bars = _nr7_entry_bars()
    context = _context(bars_by_symbol={"AAPL": _barset(bars)}, equity=10_000.0)

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.rule == "breakout.nr7_entry"
    tv = decision.reasoning.triggering_values
    assert "selected_symbol" in tv
    assert "selected_close" in tv
    assert "selected_range_value" in tv
    assert tv["selected_symbol"] == "AAPL"
    assert isinstance(tv["selected_close"], float)
    assert isinstance(tv["selected_range_value"], float)
    assert "range_lookback" in decision.reasoning.threshold
    assert "ma_filter_length" in decision.reasoning.threshold


def test_nr7_ranking_payload_populated() -> None:
    """When ranking_enabled, ranking list contains range_value and latest_close."""
    strategy = BreakoutNr7InsideStrategy()
    bars = _nr7_entry_bars()
    context = _context(bars_by_symbol={"AAPL": _barset(bars)}, equity=10_000.0)

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.ranking is not None
    assert len(decision.reasoning.ranking) >= 1
    first = decision.reasoning.ranking[0]
    assert "symbol" in first
    assert "range_value" in first
    assert "latest_close" in first


def test_loader_resolves_nr7_strategy_config() -> None:
    loaded = StrategyLoader().load(
        Path("configs/breakout_daily_nr7_inside_liquid_largecap_v1.yaml")
    )

    assert isinstance(loaded.strategy, BreakoutNr7InsideStrategy)
    assert loaded.context.strategy_id == "breakout.daily.nr7_inside.liquid_largecap.v1"


def _context(
    *,
    positions: dict[str, float] | None = None,
    bars_by_symbol: dict[str, BarSet],
    equity: float = 10_000.0,
    entry_state: dict[str, dict[str, object]] | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id="breakout.daily.nr7_inside.liquid_largecap.v1",
        family="breakout",
        template="daily.nr7_inside",
        variant="liquid_largecap",
        version=1,
        config_hash="hash",
        parameters={
            "range_lookback": 7,
            "ma_filter_length": 5,
            "max_hold_days": 3,
            "max_concurrent_positions": 2,
            "sizing_rule": "equal_notional",
            "per_position_notional_pct": 0.20,
            "ranking_enabled": True,
            "ranking_metric": "nr7_range_ascending",
        },
        universe=("AAPL",),
        universe_ref="universe.sp100_liquid.v1",
        disable_conditions=(),
        config_path="configs/breakout_daily_nr7_inside_liquid_largecap_v1.yaml",
        manifest={},
        positions=positions or {},
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _barset(bars: list[tuple[float, float, float, float]]) -> BarSet:
    timestamps = pd.date_range("2025-01-01", periods=len(bars), freq="D", tz=UTC)
    dataframe = pd.DataFrame(
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
    return BarSet(dataframe)


def _nr7_entry_bars() -> list[tuple[float, float, float, float]]:
    bars = [(100.0 + i, 103.0 + i, 99.0 + i, 102.0 + i) for i in range(10)]
    bars[-7:] = [
        (110.0, 114.0, 109.0, 113.0),
        (111.0, 116.0, 110.0, 115.0),
        (112.0, 117.0, 111.0, 116.0),
        (113.0, 118.0, 112.0, 117.0),
        (114.0, 119.0, 113.0, 118.0),
        (115.0, 120.0, 114.0, 119.0),
        (119.0, 120.0, 118.5, 119.5),
    ]
    return bars


def _flat_bars() -> list[tuple[float, float, float, float]]:
    return [(100.0, 101.0, 99.0, 100.0) for _ in range(10)]
