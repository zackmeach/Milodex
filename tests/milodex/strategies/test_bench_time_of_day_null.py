"""Time-of-day null baseline strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.bench_time_of_day_null import BenchTimeOfDayNullStrategy
from milodex.strategies.loader import build_default_registry


def _bars(*, date_et: str, times_et: list[str], close: float = 500.0) -> BarSet:
    rows: list[dict[str, Any]] = []
    for t in times_et:
        et = pd.Timestamp(f"{date_et} {t}:00").tz_localize("America/New_York")
        rows.append({
            "timestamp": et.tz_convert("UTC"), "open": close, "high": close + 0.5,
            "low": close - 0.5, "close": close, "volume": 1_000_000, "vwap": close,
        })
    return BarSet(pd.DataFrame(rows))


def _ctx(*, positions: dict[str, float], bars: BarSet, entry_offset: int = 180) -> StrategyContext:
    return StrategyContext(
        strategy_id="benchmark.time_of_day_null.spy.v1", family="benchmark",
        template="time_of_day_null", variant="spy", version=1, config_hash="x",
        parameters={
            "entry_offset_minutes": entry_offset,
            "exit_minutes_before_close": 5,
            "per_position_notional_pct": 0.10,
        },
        universe=("SPY",), universe_ref="universe.spy_only.v1",
        disable_conditions=(), config_path="x", manifest={},
        positions=positions, equity=10_000.0, bars_by_symbol={"SPY": bars},
    )


def test_buys_at_entry_offset_bar():
    # entry_offset_minutes=180 -> 12:30 ET.
    bars = _bars(date_et="2024-01-16", times_et=["09:30", "12:30"])
    decision = BenchTimeOfDayNullStrategy().evaluate(bars, _ctx(positions={}, bars=bars))
    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.BUY
    assert decision.intents[0].symbol == "SPY"


def test_no_buy_off_entry_bar():
    bars = _bars(date_et="2024-01-16", times_et=["09:30", "11:00"])
    decision = BenchTimeOfDayNullStrategy().evaluate(bars, _ctx(positions={}, bars=bars))
    assert decision.intents == []


def test_sells_at_time_stop():
    bars = _bars(date_et="2024-01-16", times_et=["15:55"])
    decision = BenchTimeOfDayNullStrategy().evaluate(
        bars, _ctx(positions={"SPY": 2.0}, bars=bars)
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL


def test_half_day_skipped():
    bars = _bars(date_et="2024-11-29", times_et=["09:30", "12:30"])  # known half-day
    decision = BenchTimeOfDayNullStrategy().evaluate(bars, _ctx(positions={}, bars=bars))
    assert decision.intents == []


def test_registered():
    assert build_default_registry().resolve("benchmark", "time_of_day_null") is (
        BenchTimeOfDayNullStrategy
    )
