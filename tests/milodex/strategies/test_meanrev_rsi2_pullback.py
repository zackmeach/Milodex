"""Tests for the mean-reversion RSI(2) pullback strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.meanrev_rsi2_pullback import MeanrevRsi2PullbackStrategy


def test_meanrev_selects_lowest_rsi_above_ma_and_sizes_with_equity() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    universe = ("AAA", "BBB", "CCC", "DDD")

    aaa_closes = _ramp_with_drop(base=100.0, drop_pct=0.04)
    bbb_closes = _ramp_with_drop(base=100.0, drop_pct=0.02)
    ccc_closes = _ramp_with_drop(base=100.0, drop_pct=0.06)
    ddd_closes = _flat_below_ma(base=50.0)

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={
            "AAA": _barset(aaa_closes),
            "BBB": _barset(bbb_closes),
            "CCC": _barset(ccc_closes),
            "DDD": _barset(ddd_closes),
        },
    )

    intents = strategy.evaluate(_barset([1.0]), context)

    intent_tuples = [(intent.side.value, intent.symbol, intent.quantity) for intent in intents]
    assert intent_tuples == [
        ("buy", "CCC", _expected_shares(10_000.0, 0.25, ccc_closes[-1])),
        ("buy", "AAA", _expected_shares(10_000.0, 0.25, aaa_closes[-1])),
    ]
    for intent in intents:
        assert intent.order_type == OrderType.MARKET


def test_meanrev_exits_when_rsi_above_exit_threshold() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    universe = ("EEE",)
    closes = _ramp_with_recovery(base=100.0)

    context = _context(
        universe=universe,
        positions={"EEE": 100.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"EEE": _barset(closes)},
    )

    intents = strategy.evaluate(_barset([1.0]), context)

    assert [(intent.side.value, intent.symbol, intent.quantity) for intent in intents] == [
        ("sell", "EEE", 100.0),
    ]


def test_meanrev_exits_on_stop_loss_from_entry_state() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    universe = ("FFF",)
    closes = _flat_series(94.0, length=260)

    context = _context(
        universe=universe,
        positions={"FFF": 50.0},
        equity=5_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"FFF": _barset(closes)},
        entry_state={"FFF": {"entry_price": 100.0, "held_days": 1}},
    )

    intents = strategy.evaluate(_barset([1.0]), context)

    assert [(intent.side.value, intent.symbol, intent.quantity) for intent in intents] == [
        ("sell", "FFF", 50.0),
    ]


def test_meanrev_exits_on_max_hold_days_from_entry_state() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    universe = ("GGG",)
    closes = _flat_series(100.0, length=260)

    context = _context(
        universe=universe,
        positions={"GGG": 10.0},
        equity=5_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"GGG": _barset(closes)},
        entry_state={"GGG": {"entry_price": 100.0, "held_days": 5}},
    )

    intents = strategy.evaluate(_barset([1.0]), context)

    assert [(intent.side.value, intent.symbol, intent.quantity) for intent in intents] == [
        ("sell", "GGG", 10.0),
    ]


def test_meanrev_ranking_disabled_keeps_universe_order() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    universe = ("AAA", "BBB", "CCC")
    bars = {
        "AAA": _barset(_ramp_with_drop(base=100.0, drop_pct=0.02)),
        "BBB": _barset(_ramp_with_drop(base=100.0, drop_pct=0.04)),
        "CCC": _barset(_ramp_with_drop(base=100.0, drop_pct=0.06)),
    }

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=False,
        bars_by_symbol=bars,
    )

    intents = strategy.evaluate(_barset([1.0]), context)

    assert [intent.symbol for intent in intents] == ["AAA", "BBB"]


def test_meanrev_skips_symbols_above_rsi_entry_threshold() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    closes_above = _flat_series(100.0, length=260)

    context = _context(
        universe=("HHH",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"HHH": _barset(closes_above)},
    )

    assert strategy.evaluate(_barset([1.0]), context) == []


def test_meanrev_rejects_invalid_parameters() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    context = _context(
        universe=("AAA",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(_flat_series(100.0, length=260))},
        override_parameters={"rsi_entry_threshold": 80.0, "rsi_exit_threshold": 40.0},
    )

    with pytest.raises(ValueError, match="rsi_entry_threshold must be less than"):
        strategy.evaluate(_barset([1.0]), context)


def test_meanrev_regime_filter_blocks_entries_in_bearish_market() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    spy_closes = [200.0 - (idx * 0.5) for idx in range(200)]
    aaa_closes = _ramp_with_drop(base=100.0, drop_pct=0.04)

    context = _context(
        universe=("AAA",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(aaa_closes), "SPY": _barset(spy_closes)},
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    assert strategy.evaluate(_barset([1.0]), context) == []


def test_meanrev_regime_filter_bearish_still_allows_exits() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    spy_closes = [200.0 - (idx * 0.5) for idx in range(200)]
    eee_closes = _ramp_with_recovery(base=100.0)

    context = _context(
        universe=("EEE",),
        positions={"EEE": 50.0},
        equity=5_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"EEE": _barset(eee_closes), "SPY": _barset(spy_closes)},
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    intents = strategy.evaluate(_barset([1.0]), context)
    assert len(intents) == 1
    assert intents[0].side.value == "sell"


def test_meanrev_regime_filter_bullish_allows_entries() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    spy_closes = [100.0 + (idx * 0.5) for idx in range(200)]
    aaa_closes = _ramp_with_drop(base=100.0, drop_pct=0.04)

    context = _context(
        universe=("AAA",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(aaa_closes), "SPY": _barset(spy_closes)},
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    intents = strategy.evaluate(_barset([1.0]), context)
    assert len(intents) == 1
    assert intents[0].side.value == "buy"


def test_meanrev_regime_filter_missing_data_fails_open() -> None:
    strategy = MeanrevRsi2PullbackStrategy()
    aaa_closes = _ramp_with_drop(base=100.0, drop_pct=0.04)

    context = _context(
        universe=("AAA",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(aaa_closes)},
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    intents = strategy.evaluate(_barset([1.0]), context)
    assert len(intents) == 1, "Regime filter should fail-open when regime bars are absent"


def test_default_strategy_loader_resolves_meanrev_strategy() -> None:
    loader = StrategyLoader()

    loaded = loader.load(Path("configs/meanrev_daily_rsi2pullback_v1.yaml"))

    assert isinstance(loaded.strategy, MeanrevRsi2PullbackStrategy)
    assert loaded.context.strategy_id == "meanrev.daily.pullback_rsi2.curated_largecap.v1"
    assert loaded.context.universe_ref == "universe.phase1.curated.v1"
    assert "SPY" in loaded.context.universe
    assert "AAPL" in loaded.context.universe


def _context(
    *,
    universe: tuple[str, ...],
    positions: dict[str, float],
    equity: float,
    max_concurrent_positions: int,
    ranking_enabled: bool,
    bars_by_symbol: dict[str, BarSet],
    entry_state: dict[str, dict[str, object]] | None = None,
    override_parameters: dict[str, object] | None = None,
) -> StrategyContext:
    parameters: dict[str, object] = {
        "rsi_lookback": 2,
        "rsi_entry_threshold": 10,
        "rsi_exit_threshold": 50,
        "ma_filter_length": 200,
        "stop_loss_pct": 0.05,
        "max_hold_days": 5,
        "max_concurrent_positions": max_concurrent_positions,
        "sizing_rule": "equal_notional",
        "per_position_notional_pct": 0.25,
        "ranking_enabled": ranking_enabled,
        "ranking_metric": "rsi_ascending",
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="meanrev.daily.pullback_rsi2.curated_largecap.v1",
        family="meanrev",
        template="daily.pullback_rsi2",
        variant="curated_largecap",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=universe,
        universe_ref="universe.phase1.curated.v1",
        disable_conditions=(),
        config_path="configs/meanrev_daily_rsi2pullback_v1.yaml",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _barset(closes: list[float]) -> BarSet:
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


def _ramp_with_drop(*, base: float, drop_pct: float, length: int = 260) -> list[float]:
    closes = [base + (index * 0.25) for index in range(length - 1)]
    peak = closes[-1]
    closes.append(peak * (1.0 - drop_pct))
    return closes


def _ramp_with_recovery(*, base: float, length: int = 260) -> list[float]:
    closes = [base + (index * 0.25) for index in range(length - 2)]
    closes.append(closes[-1] * 0.90)
    closes.append(closes[-2] * 1.05)
    return closes


def _flat_below_ma(*, base: float, length: int = 260) -> list[float]:
    ramp_up = [base * 1.5 + (index * 0.1) for index in range(length // 2)]
    below = [base for _ in range(length - len(ramp_up))]
    return ramp_up + below


def _flat_series(value: float, *, length: int) -> list[float]:
    return [value for _ in range(length)]


def _expected_shares(equity: float, notional_pct: float, unit_price: float) -> float:
    import math

    return float(max(0, math.floor((equity * notional_pct) / unit_price)))
