"""Tests for the unconditional intraday long benchmark."""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.bench_unconditional_intraday_long import (
    BenchUnconditionalIntradayLongStrategy,
)


def test_buys_at_post_opening_range_bar() -> None:
    """At exactly 10:00 ET (opening_range_minutes=30), benchmark emits BUY."""
    strategy = BenchUnconditionalIntradayLongStrategy()
    bars = _intraday_bars(
        date_et="2024-01-15",
        times_et=["09:30", "09:35", "09:40", "09:45", "09:50", "09:55", "10:00"],
        close=500.0,
    )
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.BUY
    assert decision.intents[0].symbol == "SPY"
    assert decision.reasoning.rule == "benchmark.intraday_long.entry"


def test_no_buy_at_other_bars() -> None:
    """Bars before or after 10:00 ET produce no entry intent."""
    strategy = BenchUnconditionalIntradayLongStrategy()

    # 9:55 ET — pre-window
    bars_early = _intraday_bars(
        date_et="2024-01-15",
        times_et=["09:30", "09:35", "09:40", "09:45", "09:50", "09:55"],
        close=500.0,
    )
    decision_early = strategy.evaluate(
        bars_early, _context(positions={}, equity=10_000.0, bars=bars_early)
    )
    assert decision_early.intents == []

    # 10:15 ET — past the entry signal bar (which is 10:00)
    bars_late = _intraday_bars(
        date_et="2024-01-15",
        times_et=[
            "09:30",
            "09:35",
            "09:40",
            "09:45",
            "09:50",
            "09:55",
            "10:00",
            "10:05",
            "10:10",
            "10:15",
        ],
        close=500.0,
    )
    decision_late = strategy.evaluate(
        bars_late, _context(positions={}, equity=10_000.0, bars=bars_late)
    )
    assert decision_late.intents == []


def test_sells_at_time_stop_bar() -> None:
    """At 15:55 ET (exit_minutes_before_close=5), benchmark emits SELL on the
    open position.
    """
    strategy = BenchUnconditionalIntradayLongStrategy()
    bars = _intraday_bars(
        date_et="2024-01-15",
        times_et=["15:55"],
        close=505.0,
    )
    context = _context(positions={"SPY": 2.0}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.intents[0].quantity == 2.0
    assert decision.reasoning.rule == "benchmark.intraday_long.exit"


def test_half_day_session_skipped() -> None:
    """On a half-day, no entries, no exits unless defensive close."""
    strategy = BenchUnconditionalIntradayLongStrategy()
    # 2024-11-29 is a known half-day
    bars = _intraday_bars(
        date_et="2024-11-29",
        times_et=["09:30", "09:35", "09:40", "09:45", "09:50", "09:55", "10:00"],
        close=500.0,
    )
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "half-day" in decision.reasoning.narrative


def test_two_consecutive_sessions_produce_two_round_trips() -> None:
    """Day 1: BUY at 10:00; ... SELL at 15:55. Day 2: BUY at 10:00 again.
    Verify the strategy decides correctly at each of those four key bars.
    """
    strategy = BenchUnconditionalIntradayLongStrategy()

    # Day 1 BUY at 10:00
    day1_buy_bars = _intraday_bars(
        date_et="2024-01-15",
        times_et=["09:30", "09:35", "09:40", "09:45", "09:50", "09:55", "10:00"],
        close=500.0,
    )
    decision_d1_buy = strategy.evaluate(
        day1_buy_bars, _context(positions={}, equity=10_000.0, bars=day1_buy_bars)
    )
    assert len(decision_d1_buy.intents) == 1
    assert decision_d1_buy.intents[0].side == OrderSide.BUY

    # Day 1 SELL at 15:55 (with position open)
    day1_sell_bars = _intraday_bars(
        date_et="2024-01-15",
        times_et=["15:55"],
        close=505.0,
    )
    decision_d1_sell = strategy.evaluate(
        day1_sell_bars, _context(positions={"SPY": 2.0}, equity=10_010.0, bars=day1_sell_bars)
    )
    assert len(decision_d1_sell.intents) == 1
    assert decision_d1_sell.intents[0].side == OrderSide.SELL

    # Day 2 BUY at 10:00 (no position, new session)
    day2_buy_bars = _intraday_bars(
        date_et="2024-01-16",
        times_et=["09:30", "09:35", "09:40", "09:45", "09:50", "09:55", "10:00"],
        close=505.0,
    )
    decision_d2_buy = strategy.evaluate(
        day2_buy_bars, _context(positions={}, equity=10_010.0, bars=day2_buy_bars)
    )
    assert len(decision_d2_buy.intents) == 1
    assert decision_d2_buy.intents[0].side == OrderSide.BUY


def test_generalizes_to_non_spy_single_symbol() -> None:
    """Pointed at a single-symbol XLB universe, the benchmark trades XLB (no SPY
    hardcoding). This is the "across 17 ETFs" mechanism: per-symbol resolution via
    17 single-symbol backtests, NOT new multi-symbol strategy logic (decision #2).
    """
    strategy = BenchUnconditionalIntradayLongStrategy()
    bars = _intraday_bars(
        date_et="2024-01-15",
        times_et=["09:30", "09:35", "09:40", "09:45", "09:50", "09:55", "10:00"],
        close=80.0,
    )
    context = StrategyContext(
        strategy_id="benchmark.unconditional_intraday_long.xlb.v1",
        family="benchmark",
        template="unconditional_intraday_long",
        variant="xlb",
        version=1,
        config_hash="test_hash",
        parameters={
            "opening_range_minutes": 30,
            "exit_minutes_before_close": 5,
            "per_position_notional_pct": 0.10,
        },
        universe=("XLB",),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions={},
        equity=10_000.0,
        bars_by_symbol={"XLB": bars},
        entry_state={},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.BUY
    assert decision.intents[0].symbol == "XLB"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(
    *,
    positions: dict[str, float],
    equity: float,
    bars: BarSet,
    override_parameters: dict[str, Any] | None = None,
) -> StrategyContext:
    parameters: dict[str, Any] = {
        "opening_range_minutes": 30,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="benchmark.unconditional_intraday_long.spy.v1",
        family="benchmark",
        template="unconditional_intraday_long",
        variant="spy",
        version=1,
        config_hash="test_hash",
        parameters=parameters,
        universe=("SPY",),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol={"SPY": bars},
        entry_state={},
    )


def _intraday_bars(*, date_et: str, times_et: list[str], close: float) -> BarSet:
    rows: list[dict[str, Any]] = []
    for time_str in times_et:
        et_ts = pd.Timestamp(f"{date_et} {time_str}:00").tz_localize("America/New_York")
        utc_ts = et_ts.tz_convert("UTC")
        rows.append(
            {
                "timestamp": utc_ts,
                "open": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1_000_000,
                "vwap": close,
            }
        )
    return BarSet(pd.DataFrame(rows))
