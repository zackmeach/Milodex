"""Tests for turn-of-month seasonality."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.seasonality_turn_of_month import SeasonalityTurnOfMonthStrategy


def test_turn_of_month_enters_on_last_business_day() -> None:
    strategy = SeasonalityTurnOfMonthStrategy()
    barset = _barset(pd.bdate_range("2025-01-01", "2025-01-31", tz=UTC))
    context = _context(bars_by_symbol={"SPY": barset})

    decision = strategy.evaluate(barset, context)

    assert [(i.side, i.symbol) for i in decision.intents] == [(OrderSide.BUY, "SPY")]
    assert decision.reasoning.rule == "seasonality.turn_of_month_entry"


def test_turn_of_month_no_signal_before_last_business_day() -> None:
    strategy = SeasonalityTurnOfMonthStrategy()
    barset = _barset(pd.bdate_range("2025-01-01", "2025-01-30", tz=UTC))
    context = _context(bars_by_symbol={"SPY": barset})

    decision = strategy.evaluate(barset, context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_turn_of_month_exits_on_third_trading_day_of_new_month() -> None:
    strategy = SeasonalityTurnOfMonthStrategy()
    barset = _barset(pd.bdate_range("2025-02-01", "2025-02-05", tz=UTC))
    context = _context(positions={"SPY": 10.0}, bars_by_symbol={"SPY": barset})

    decision = strategy.evaluate(barset, context)

    assert [(i.side, i.symbol, i.quantity) for i in decision.intents] == [
        (OrderSide.SELL, "SPY", 10.0)
    ]
    assert decision.reasoning.rule == "seasonality.turn_of_month_exit"


def test_loader_resolves_turn_of_month_config() -> None:
    loaded = StrategyLoader().load(Path("configs/seasonality_daily_turn_of_month_spy_v1.yaml"))

    assert isinstance(loaded.strategy, SeasonalityTurnOfMonthStrategy)
    assert loaded.context.strategy_id == "seasonality.daily.turn_of_month.spy.v1"


def _context(
    *,
    positions: dict[str, float] | None = None,
    bars_by_symbol: dict[str, BarSet],
) -> StrategyContext:
    return StrategyContext(
        strategy_id="seasonality.daily.turn_of_month.spy.v1",
        family="seasonality",
        template="daily.turn_of_month",
        variant="spy",
        version=1,
        config_hash="hash",
        parameters={
            "target_symbol": "SPY",
            "entry_trading_day_offset": 0,
            "exit_trading_day_of_month": 3,
            "allocation_pct": 1.0,
            "sizing_rule": "single_asset_full_allocation",
            "low_cadence_exemption": True,
        },
        universe=("SPY",),
        universe_ref="universe.spy_only.v1",
        disable_conditions=(),
        config_path="configs/seasonality_daily_turn_of_month_spy_v1.yaml",
        manifest={},
        positions=positions or {},
        equity=10_000.0,
        bars_by_symbol=bars_by_symbol,
        entry_state={},
    )


def _barset(timestamps: pd.DatetimeIndex) -> BarSet:
    closes = [100.0 + idx for idx in range(len(timestamps))]
    return BarSet(
        pd.DataFrame(
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
    )
