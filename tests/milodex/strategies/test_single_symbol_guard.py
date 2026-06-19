"""Tests for the single_symbol cardinality guard (A1) and multi-symbol regression (A2)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext, single_symbol
from milodex.strategies.bench_time_of_day_null import BenchTimeOfDayNullStrategy
from milodex.strategies.bench_unconditional_intraday_long import (
    BenchUnconditionalIntradayLongStrategy,
)
from milodex.strategies.meanrev_rsi2_intraday import MeanrevRsi2IntradayStrategy
from milodex.strategies.meanrev_vwap_reversion_intraday import (
    MeanrevVwapReversionIntradayStrategy,
)

# ---------------------------------------------------------------------------
# A1: unit tests for single_symbol()
# ---------------------------------------------------------------------------


def test_single_symbol_returns_sole_symbol() -> None:
    assert single_symbol(("spy",)) == "SPY"
    assert single_symbol(["XLF"]) == "XLF"


def test_single_symbol_none_on_empty() -> None:
    assert single_symbol(()) is None
    assert single_symbol([]) is None


def test_single_symbol_raises_on_multi() -> None:
    with pytest.raises(ValueError, match="single-symbol strategy received"):
        single_symbol(("SPY", "QQQ"))


def test_single_symbol_dedups_then_counts_distinct() -> None:
    # case-insensitive dedup of one logical symbol is size 1, not multi
    assert single_symbol(("SPY", "spy")) == "SPY"


# ---------------------------------------------------------------------------
# A2: regression — each strategy's evaluate() raises on a 2-symbol universe.
# Each test inlines a full StrategyContext (copy of the template at
# test_bench_unconditional_intraday_long.py:164-185) with universe=("SPY","QQQ").
# Valid parameters are required so _validated_parameters() passes and the guard
# is actually reached on the trade path.
# ---------------------------------------------------------------------------


def _minimal_bars(date_et: str = "2024-01-15", time_et: str = "10:00") -> BarSet:
    et_ts = pd.Timestamp(f"{date_et} {time_et}:00").tz_localize("America/New_York")
    utc_ts = et_ts.tz_convert("UTC")
    row: dict[str, Any] = {
        "timestamp": utc_ts,
        "open": 500.0,
        "high": 500.5,
        "low": 499.5,
        "close": 500.0,
        "volume": 1_000_000,
        "vwap": 500.0,
    }
    return BarSet(pd.DataFrame([row]))


def test_bench_unconditional_intraday_long_multi_symbol_universe_raises() -> None:
    bars = _minimal_bars()
    ctx = StrategyContext(
        strategy_id="benchmark.unconditional_intraday_long.spy.v1",
        family="benchmark",
        template="unconditional_intraday_long",
        variant="spy",
        version=1,
        config_hash="test_hash",
        parameters={
            "opening_range_minutes": 30,
            "exit_minutes_before_close": 5,
            "per_position_notional_pct": 0.10,
        },
        universe=("SPY", "QQQ"),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions={},
        equity=10_000.0,
        bars_by_symbol={"SPY": bars},
        entry_state={},
    )
    with pytest.raises(ValueError, match="single-symbol strategy received"):
        BenchUnconditionalIntradayLongStrategy().evaluate(bars, ctx)


def test_bench_time_of_day_null_multi_symbol_universe_raises() -> None:
    bars = _minimal_bars(time_et="12:30")
    ctx = StrategyContext(
        strategy_id="benchmark.time_of_day_null.spy.v1",
        family="benchmark",
        template="time_of_day_null",
        variant="spy",
        version=1,
        config_hash="test_hash",
        parameters={
            "entry_offset_minutes": 180,
            "exit_minutes_before_close": 5,
            "per_position_notional_pct": 0.10,
        },
        universe=("SPY", "QQQ"),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions={},
        equity=10_000.0,
        bars_by_symbol={"SPY": bars},
        entry_state={},
    )
    with pytest.raises(ValueError, match="single-symbol strategy received"):
        BenchTimeOfDayNullStrategy().evaluate(bars, ctx)


def test_meanrev_rsi2_intraday_multi_symbol_universe_raises() -> None:
    bars = _minimal_bars()
    ctx = StrategyContext(
        strategy_id="meanrev.rsi2.intraday.spy.v1",
        family="meanrev",
        template="rsi2.intraday",
        variant="spy",
        version=1,
        config_hash="test_hash",
        parameters={
            "opening_range_minutes": 30,
            "entry_window_minutes": 300,
            "rsi_lookback": 2,
            "rsi_entry_threshold": 10.0,
            "rsi_exit_threshold": 60.0,
            "stop_loss_pct": 0.005,
            "exit_minutes_before_close": 5,
            "per_position_notional_pct": 0.10,
        },
        universe=("SPY", "QQQ"),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions={},
        equity=10_000.0,
        bars_by_symbol={"SPY": bars},
        entry_state={},
    )
    with pytest.raises(ValueError, match="single-symbol strategy received"):
        MeanrevRsi2IntradayStrategy().evaluate(bars, ctx)


def test_meanrev_vwap_reversion_intraday_multi_symbol_universe_raises() -> None:
    bars = _minimal_bars()
    ctx = StrategyContext(
        strategy_id="meanrev.vwap_reversion.intraday.spy.v1",
        family="meanrev",
        template="vwap_reversion.intraday",
        variant="spy",
        version=1,
        config_hash="test_hash",
        parameters={
            "opening_range_minutes": 30,
            "entry_window_minutes": 300,
            "entry_deviation_pct": 0.005,
            "stop_loss_pct": 0.005,
            "exit_minutes_before_close": 5,
            "per_position_notional_pct": 0.10,
        },
        universe=("SPY", "QQQ"),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions={},
        equity=10_000.0,
        bars_by_symbol={"SPY": bars},
        entry_state={},
    )
    with pytest.raises(ValueError, match="single-symbol strategy received"):
        MeanrevVwapReversionIntradayStrategy().evaluate(bars, ctx)
