"""Tests for the daily time-series momentum strategy.

The structural shape mirrors ``test_meanrev_rsi2_pullback`` deliberately:
both families share the cross-sectional / ranked / regime-filtered /
equal-notional-sized harness contract, and Phase 3's test for "does the
harness carry a second research thread?" is exactly that the same test
pattern works for two materially-different signal shapes.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.momentum_daily_tsmom import MomentumDailyTsmomStrategy


def test_momentum_emits_buy_when_momentum_above_threshold_and_close_above_ma() -> None:
    strategy = MomentumDailyTsmomStrategy()
    universe = ("AAA",)

    closes = _strong_uptrend(base=100.0)

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(closes)},
    )

    intents = strategy.evaluate(_barset([1.0]), context).intents

    assert len(intents) == 1
    assert intents[0].symbol == "AAA"
    assert intents[0].side == OrderSide.BUY
    assert intents[0].order_type == OrderType.MARKET


def test_momentum_selects_strongest_momentum_above_ma_and_sizes_with_equity() -> None:
    strategy = MomentumDailyTsmomStrategy()
    universe = ("AAA", "BBB", "CCC", "DDD")

    aaa_closes = _strong_uptrend(base=100.0, end_gain_pct=0.06)
    bbb_closes = _strong_uptrend(base=100.0, end_gain_pct=0.10)  # strongest
    ccc_closes = _strong_uptrend(base=100.0, end_gain_pct=0.08)
    ddd_closes = _flat_below_ma(base=50.0)  # below MA, rejected

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

    intents = strategy.evaluate(_barset([1.0]), context).intents

    intent_tuples = [(intent.side.value, intent.symbol, intent.quantity) for intent in intents]
    assert intent_tuples == [
        ("buy", "BBB", _expected_shares(10_000.0, 0.25, bbb_closes[-1])),
        ("buy", "CCC", _expected_shares(10_000.0, 0.25, ccc_closes[-1])),
    ]
    for intent in intents:
        assert intent.order_type == OrderType.MARKET


def test_momentum_exits_when_momentum_below_exit_threshold() -> None:
    strategy = MomentumDailyTsmomStrategy()
    universe = ("EEE",)
    closes = _downtrend(base=100.0)

    context = _context(
        universe=universe,
        positions={"EEE": 100.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"EEE": _barset(closes)},
    )

    intents = strategy.evaluate(_barset([1.0]), context).intents

    assert [(intent.side.value, intent.symbol, intent.quantity) for intent in intents] == [
        ("sell", "EEE", 100.0),
    ]


def test_momentum_ignores_positions_outside_declared_universe() -> None:
    """Mirrors the meanrev regression: a position on a symbol outside this
    strategy's universe belongs to some other strategy. Momentum must not
    treat it as its own and emit an exit. Phase 2 ADR 0024 + ADR 0022 set
    this contract account-wide; the strategy-level enforcement is the
    universe scope check.
    """
    strategy = MomentumDailyTsmomStrategy()
    universe = ("AAA",)
    aaa_closes = _flat_series(100.0, length=260)
    spy_closes = _downtrend(base=100.0)  # would trigger momentum_exit if SPY were ours

    context = _context(
        universe=universe,
        positions={"SPY": 13.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={
            "AAA": _barset(aaa_closes),
            "SPY": _barset(spy_closes),
        },
    )

    decision = strategy.evaluate(_barset([1.0]), context)
    sell_intents = [intent for intent in decision.intents if intent.side == OrderSide.SELL]
    assert sell_intents == [], (
        f"momentum emitted exit intents for out-of-universe positions: {sell_intents}"
    )


def test_momentum_exits_on_stop_loss_from_entry_state() -> None:
    strategy = MomentumDailyTsmomStrategy()
    universe = ("FFF",)
    closes = _flat_series(92.0, length=260)  # below 100 * (1 - 0.07) = 93

    context = _context(
        universe=universe,
        positions={"FFF": 50.0},
        equity=5_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"FFF": _barset(closes)},
        entry_state={"FFF": {"entry_price": 100.0, "held_days": 1}},
    )

    intents = strategy.evaluate(_barset([1.0]), context).intents

    assert [(intent.side.value, intent.symbol, intent.quantity) for intent in intents] == [
        ("sell", "FFF", 50.0),
    ]


def test_momentum_exits_on_max_hold_days_from_entry_state() -> None:
    strategy = MomentumDailyTsmomStrategy()
    universe = ("GGG",)
    closes = _strong_uptrend(base=100.0)  # momentum still positive — only max_hold should fire

    context = _context(
        universe=universe,
        positions={"GGG": 10.0},
        equity=5_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"GGG": _barset(closes)},
        entry_state={"GGG": {"entry_price": 100.0, "held_days": 10}},
    )

    intents = strategy.evaluate(_barset([1.0]), context).intents

    assert [(intent.side.value, intent.symbol, intent.quantity) for intent in intents] == [
        ("sell", "GGG", 10.0),
    ]


def test_momentum_ranking_disabled_keeps_universe_order() -> None:
    strategy = MomentumDailyTsmomStrategy()
    universe = ("AAA", "BBB", "CCC")
    bars = {
        "AAA": _barset(_strong_uptrend(base=100.0, end_gain_pct=0.06)),
        "BBB": _barset(_strong_uptrend(base=100.0, end_gain_pct=0.10)),
        "CCC": _barset(_strong_uptrend(base=100.0, end_gain_pct=0.08)),
    }

    context = _context(
        universe=universe,
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=False,
        bars_by_symbol=bars,
    )

    intents = strategy.evaluate(_barset([1.0]), context).intents

    assert [intent.symbol for intent in intents] == ["AAA", "BBB"]


def test_momentum_skips_symbols_below_entry_threshold() -> None:
    strategy = MomentumDailyTsmomStrategy()
    closes_flat = _flat_series(100.0, length=260)  # 0% momentum, below 5% threshold

    context = _context(
        universe=("HHH",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"HHH": _barset(closes_flat)},
    )

    assert strategy.evaluate(_barset([1.0]), context).intents == []


def test_momentum_rejects_invalid_parameters() -> None:
    strategy = MomentumDailyTsmomStrategy()
    context = _context(
        universe=("AAA",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(_flat_series(100.0, length=260))},
        override_parameters={"momentum_entry_threshold": -0.01, "momentum_exit_threshold": 0.0},
    )

    with pytest.raises(ValueError, match="momentum_entry_threshold must be greater than"):
        strategy.evaluate(_barset([1.0]), context)


def test_momentum_regime_filter_blocks_entries_in_bearish_market() -> None:
    strategy = MomentumDailyTsmomStrategy()
    spy_closes = [200.0 - (idx * 0.5) for idx in range(200)]  # bearish broad market
    aaa_closes = _strong_uptrend(base=100.0)

    context = _context(
        universe=("AAA",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(aaa_closes), "SPY": _barset(spy_closes)},
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    assert strategy.evaluate(_barset([1.0]), context).intents == []


def test_momentum_regime_filter_bearish_still_allows_exits() -> None:
    strategy = MomentumDailyTsmomStrategy()
    spy_closes = [200.0 - (idx * 0.5) for idx in range(200)]  # bearish
    eee_closes = _downtrend(base=100.0)  # would trigger momentum_exit

    context = _context(
        universe=("EEE",),
        positions={"EEE": 50.0},
        equity=5_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"EEE": _barset(eee_closes), "SPY": _barset(spy_closes)},
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    intents = strategy.evaluate(_barset([1.0]), context).intents
    assert len(intents) == 1
    assert intents[0].side == OrderSide.SELL


def test_momentum_regime_filter_bullish_allows_entries() -> None:
    strategy = MomentumDailyTsmomStrategy()
    spy_closes = [100.0 + (idx * 0.5) for idx in range(200)]  # bullish broad market
    aaa_closes = _strong_uptrend(base=100.0)

    context = _context(
        universe=("AAA",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(aaa_closes), "SPY": _barset(spy_closes)},
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    intents = strategy.evaluate(_barset([1.0]), context).intents
    assert len(intents) == 1
    assert intents[0].side == OrderSide.BUY


def test_momentum_regime_filter_missing_data_fails_open() -> None:
    strategy = MomentumDailyTsmomStrategy()
    aaa_closes = _strong_uptrend(base=100.0)

    context = _context(
        universe=("AAA",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"AAA": _barset(aaa_closes)},  # no SPY bars
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    intents = strategy.evaluate(_barset([1.0]), context).intents
    assert len(intents) == 1, "Regime filter should fail-open when regime bars are absent"


def test_momentum_entry_reasoning_captures_ranking_and_rejections() -> None:
    strategy = MomentumDailyTsmomStrategy()
    universe = ("AAA", "BBB", "CCC", "DDD")

    aaa_closes = _strong_uptrend(base=100.0, end_gain_pct=0.06)
    bbb_closes = _strong_uptrend(base=100.0, end_gain_pct=0.10)
    ccc_closes = _strong_uptrend(base=100.0, end_gain_pct=0.08)
    ddd_closes = _flat_below_ma(base=50.0)  # rejected: close < SMA

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

    decision = strategy.evaluate(_barset([1.0]), context)

    assert decision.reasoning.rule == "momentum.tsmom_entry"
    assert decision.reasoning.ranking is not None
    assert len(decision.reasoning.ranking) >= 1
    rejections = {
        entry["symbol"]: entry["reason"] for entry in decision.reasoning.rejected_alternatives
    }
    assert "DDD" in rejections
    assert "SMA" in rejections["DDD"] or "not above" in rejections["DDD"]
    assert "momentum_entry_threshold" in decision.reasoning.threshold


def test_momentum_returns_no_signal_reasoning_when_nothing_qualifies() -> None:
    strategy = MomentumDailyTsmomStrategy()
    context = _context(
        universe=("HHH",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"HHH": _barset(_flat_series(100.0, length=260))},
    )

    decision = strategy.evaluate(_barset([1.0]), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_momentum_exit_reasoning_names_the_rule() -> None:
    strategy = MomentumDailyTsmomStrategy()
    closes = _flat_series(92.0, length=260)  # stop_loss territory

    context = _context(
        universe=("FFF",),
        positions={"FFF": 50.0},
        equity=5_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"FFF": _barset(closes)},
        entry_state={"FFF": {"entry_price": 100.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_barset([1.0]), context)

    assert decision.reasoning.rule == "momentum.stop_loss"
    assert "FFF" in decision.reasoning.narrative


def test_default_strategy_loader_resolves_momentum_strategy() -> None:
    loader = StrategyLoader()

    loaded = loader.load(Path("configs/momentum_daily_tsmom_v1.yaml"))

    assert isinstance(loaded.strategy, MomentumDailyTsmomStrategy)
    assert loaded.context.strategy_id == "momentum.daily.tsmom.curated_largecap.v1"
    assert loaded.context.universe_ref == "universe.phase1.curated.v1"
    assert "SPY" in loaded.context.universe
    assert "AAPL" in loaded.context.universe


def test_momentum_matches_golden_signal_sequence() -> None:
    """Golden-output test over a fixed 12-bar window with hand-computed signals.

    Parameters chosen for hand-computability:
        ``momentum_lookback = 3``, ``momentum_entry_threshold = 0.05``,
        ``momentum_exit_threshold = 0.0``, ``ma_filter_length = 2``,
        ``max_hold_days = 2``, ``max_concurrent_positions = 1``,
        ``per_position_notional_pct = 0.20``, ``equity = 10_000``.

    Production config (``lookback=20``, ``entry=0.05``, ``ma_len=200``)
    is exercised in walk-forward and integration tests; this golden test
    exercises identical *logic* with smaller parameter values whose
    signals can be hand-computed.

    Momentum signal at bar t: ``mom(t) = close[t] / close[t-3] - 1``
    (computable starting at bar 3). SMA(2) at bar t: ``(close[t-1] +
    close[t]) / 2`` (computable starting at bar 1).

    bar  close  mom(3)              SMA(2)              action
    ---  -----  ------              ------              ------
     0   10.00  n/a                 n/a                 warmup (no SMA, no mom)
     1   11.00  n/a                 10.500              warmup (mom needs 4 bars)
     2   12.00  n/a                 11.500              warmup
     3   13.00  +30.0%              12.500              ENTRY: mom>=5%, close>SMA
     4   13.50  +22.7% (13.5/11)    13.250              held; held_days=1
     5   14.00  +16.7% (14/12)      13.750              held; held_days=2 → MAX-HOLD EXIT
     6   13.00  0% (13/13)          13.500              no entry: mom 0% < 5%
     7   12.50  -7.4% (12.5/13.5)   12.750              no entry: mom < 5%
     8   12.00  -14.3% (12/14)      12.250              no entry
     9   11.50  -11.5% (11.5/13)    11.750              no entry
    10   13.00  +4.0% (13/12.5)     12.250              no entry: mom 4% < 5%
    11   14.00  +16.7% (14/12)      13.500              ENTRY: mom 16.7% >= 5%, close>SMA

    Expected shares (floor(equity * pct / price)):
        bar 3 BUY  AAA at 13.00: floor(10_000 * 0.20 / 13.00) = 153
        bar 5 SELL AAA at 14.00: 153 (same as held)
        bar 11 BUY AAA at 14.00: floor(10_000 * 0.20 / 14.00) = 142
    """
    strategy = MomentumDailyTsmomStrategy()

    closes = [10.0, 11.0, 12.0, 13.0, 13.5, 14.0, 13.0, 12.5, 12.0, 11.5, 13.0, 14.0]

    expected: list[list[tuple[str, str, float]]] = [
        [],  # bar 0: warmup
        [],  # bar 1: warmup (mom not computable yet)
        [],  # bar 2: warmup (mom needs at least 4 bars)
        [("buy", "AAA", 153.0)],  # bar 3: ENTRY
        [],  # bar 4: held
        [("sell", "AAA", 153.0)],  # bar 5: MAX-HOLD EXIT
        [],  # bar 6: 0% momentum < 5%
        [],  # bar 7
        [],  # bar 8
        [],  # bar 9
        [],  # bar 10: 4% < 5%
        [("buy", "AAA", 142.0)],  # bar 11: re-entry
    ]

    positions: dict[str, float] = {}
    entry_state: dict[str, dict[str, object]] = {}
    actual: list[list[tuple[str, str, float]]] = []

    for idx in range(len(closes)):
        for sym in list(entry_state):
            if sym in positions:
                entry_state[sym]["held_days"] = int(entry_state[sym]["held_days"]) + 1

        context = _golden_context(
            positions=positions,
            entry_state=entry_state,
            bars_by_symbol={"AAA": _barset(closes[: idx + 1])},
        )
        intents = strategy.evaluate(_barset([1.0]), context).intents
        actual.append([(intent.side.value, intent.symbol, intent.quantity) for intent in intents])
        positions, entry_state = _apply_golden_intents(
            positions=positions,
            entry_state=entry_state,
            intents=intents,
            bar_closes={"AAA": closes[idx]},
        )

    assert actual == expected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _golden_context(
    *,
    positions: dict[str, float],
    entry_state: dict[str, dict[str, object]],
    bars_by_symbol: dict[str, BarSet],
) -> StrategyContext:
    return StrategyContext(
        strategy_id="momentum.daily.tsmom.curated_largecap.v1",
        family="momentum",
        template="daily.tsmom",
        variant="curated_largecap",
        version=1,
        config_hash="hash",
        parameters={
            "momentum_lookback": 3,
            "momentum_entry_threshold": 0.05,
            "momentum_exit_threshold": 0.0,
            "ma_filter_length": 2,
            "stop_loss_pct": 0.07,
            "max_hold_days": 2,
            "max_concurrent_positions": 1,
            "sizing_rule": "equal_notional",
            "per_position_notional_pct": 0.20,
            "ranking_enabled": True,
            "ranking_metric": "momentum_descending",
        },
        universe=("AAA",),
        universe_ref="universe.phase1.curated.v1",
        disable_conditions=(),
        config_path="configs/momentum_daily_tsmom_v1.yaml",
        manifest={},
        positions=positions,
        equity=10_000.0,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state,
    )


def _apply_golden_intents(
    *,
    positions: dict[str, float],
    entry_state: dict[str, dict[str, object]],
    intents: list,
    bar_closes: dict[str, float],
) -> tuple[dict[str, float], dict[str, dict[str, object]]]:
    updated_positions = dict(positions)
    updated_state = {sym: dict(state) for sym, state in entry_state.items()}
    for intent in intents:
        if intent.side == OrderSide.SELL:
            updated_positions.pop(intent.symbol, None)
            updated_state.pop(intent.symbol, None)
        elif intent.side == OrderSide.BUY:
            updated_positions[intent.symbol] = intent.quantity
            updated_state[intent.symbol] = {
                "entry_price": bar_closes[intent.symbol],
                "held_days": 0,
            }
    return updated_positions, updated_state


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
        "momentum_lookback": 20,
        "momentum_entry_threshold": 0.05,
        "momentum_exit_threshold": 0.0,
        "ma_filter_length": 200,
        "stop_loss_pct": 0.07,
        "max_hold_days": 10,
        "max_concurrent_positions": max_concurrent_positions,
        "sizing_rule": "equal_notional",
        "per_position_notional_pct": 0.25,
        "ranking_enabled": ranking_enabled,
        "ranking_metric": "momentum_descending",
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="momentum.daily.tsmom.curated_largecap.v1",
        family="momentum",
        template="daily.tsmom",
        variant="curated_largecap",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=universe,
        universe_ref="universe.phase1.curated.v1",
        disable_conditions=(),
        config_path="configs/momentum_daily_tsmom_v1.yaml",
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


def _strong_uptrend(
    *, base: float = 100.0, end_gain_pct: float = 0.10, length: int = 260
) -> list[float]:
    """Flat at ``base`` for first ``length-21`` bars, then linear ramp to
    ``base * (1 + end_gain_pct)`` over last 21 bars.

    With default ``momentum_lookback=20``: close[-1] / close[-21] - 1 =
    end_gain_pct, so the strategy sees a momentum signal of exactly that
    magnitude. SMA(200) of the last 200 bars is dominated by the flat
    baseline, so close[-1] > SMA(200).
    """
    flat = [base for _ in range(length - 21)]
    end_value = base * (1.0 + end_gain_pct)
    ramp = [base + ((idx + 1) * (end_value - base) / 21) for idx in range(21)]
    return flat + ramp


def _downtrend(*, base: float = 100.0, length: int = 260) -> list[float]:
    """Flat then declining; final 21 bars drop 5%, producing momentum
    signal of -5% (below 0% exit threshold).
    """
    flat = [base for _ in range(length - 21)]
    end_value = base * 0.95
    ramp = [base - ((idx + 1) * (base - end_value) / 21) for idx in range(21)]
    return flat + ramp


def _flat_series(value: float, *, length: int) -> list[float]:
    return [value for _ in range(length)]


def _flat_below_ma(*, base: float, length: int = 260) -> list[float]:
    """High historical baseline, current close below SMA. Same shape as the
    meanrev helper of the same name.
    """
    ramp_up = [base * 1.5 + (idx * 0.1) for idx in range(length // 2)]
    below = [base for _ in range(length - len(ramp_up))]
    return ramp_up + below


def _expected_shares(equity: float, notional_pct: float, unit_price: float) -> float:
    import math

    return float(max(0, math.floor((equity * notional_pct) / unit_price)))
