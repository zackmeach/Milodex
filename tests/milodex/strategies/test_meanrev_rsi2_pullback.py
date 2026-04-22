"""Tests for the mean-reversion RSI(2) pullback strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderSide, OrderType
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


def test_meanrev_matches_golden_signal_sequence() -> None:
    """Golden-output test over a fixed bar window with hand-computed RSI/SMA.

    Parameters are chosen so that each of the five strategy behaviours under test
    fires on a distinct bar of a single 12-bar window, making the entire per-bar
    intent sequence asserted at once:

        ``rsi_lookback = 2``, ``rsi_entry_threshold = 70``,
        ``rsi_exit_threshold = 80``, ``ma_filter_length = 3``,
        ``max_hold_days = 2``, ``max_concurrent_positions = 1``,
        ``per_position_notional_pct = 0.20``, ``equity = 10_000``.

    These thresholds are deliberately looser than the production config
    (``entry=10``, ``exit=50``, ``ma_len=200``). That is a *scale* compromise,
    not a *logic* compromise: verifying the ``RSI<entry AND close>SMA(N)``
    gate with production values would require a 200+ bar series nobody can
    hand-verify. The strategy's parameter specs accept any consistent values
    (the existing ``test_meanrev_rejects_invalid_parameters`` covers schema
    validation), so the logic exercised here is identical to production.

    Wilder-smoothed RSI with ``n=2`` on a series ``c = [c_0, c_1, ...]``:

        deltas[i] = c[i] - c[i-1]
        seed: avg_gain = mean(gains[0:2]), avg_loss = mean(losses[0:2])
        step: avg_gain = (prev_avg_gain * (n-1) + gain) / n
              avg_loss = (prev_avg_loss * (n-1) + loss) / n
        rsi = 100 - 100/(1 + avg_gain/avg_loss)

    Each expected value below is hand-computed from this formula and the
    arithmetic is written out in comments for auditability. The expected
    share counts use ``floor(equity * pct / price)`` per
    ``shares_for_notional_pct`` (not imported — computed by hand to keep the
    test independent of the implementation under test).
    """
    strategy = MeanrevRsi2PullbackStrategy()

    # ----- bar-by-bar walkthrough (AAA = target symbol, BBB = ranking rival) -----
    #
    # idx  AAA close  AAA SMA(3)   AAA RSI(2)   BBB close  BBB SMA(3)   BBB RSI(2)
    # ---  ---------  ----------   ----------   ---------  ----------   ----------
    #  0    10.00      n/a           n/a         20.00      n/a           n/a
    #  1    11.00      n/a           n/a         21.00      n/a           n/a
    #  2    12.00      11.000        100.000     22.00      21.000        100.000
    #  3    10.00      11.000         33.333     23.00      22.000        100.000
    #  4    11.50      11.167         66.667     24.00      23.000        100.000   <- ENTRY(AAA)
    #  5    15.00      12.167         90.000     25.00      24.000        100.000   <- RSI-EXIT(AAA)
    #  6    12.00      12.833         40.909     22.00      23.667         25.000
    #  7    14.00      13.667         65.789     24.00      23.667         62.500   <- RANK>BBB
    #  8    14.00      13.333         65.789     25.00      23.667         75.000
    #  9    13.50      13.833         46.296     24.50      24.500         56.250   <- TIMEOUT BBB
    # 10    13.50      13.667         46.296     25.00      24.833         70.833
    # 11    10.00      12.333          4.980     25.00      24.833         70.833   <- SMA-BLOCK
    #
    # Key arithmetic (a few representative bars; full RSI recursion carries
    # through the window):
    #
    # AAA bar 4: deltas = [1, 1, -2, 1.5]. Seed at [1, 1]: ag=1, al=0.
    #   step -2 -> ag=0.5, al=1.0; step +1.5 -> ag=(0.5+1.5)/2=1.0,
    #   al=(1.0+0)/2=0.5; rs=2.0 -> RSI = 100 - 100/3 = 66.667.
    #   SMA(3) = (12 + 10 + 11.5)/3 = 11.167; close 11.5 > 11.167 -> ENTRY.
    #
    # AAA bar 5: delta +3.5. ag=(1.0+3.5)/2=2.25, al=(0.5+0)/2=0.25;
    #   rs=9.0 -> RSI = 100 - 100/10 = 90.0 > 80 -> RSI-EXIT.
    #
    # AAA bar 7: delta +2 (from 12 to 14). After bar 6 (c=12, delta -3):
    #   ag=(2.25+0)/2=1.125, al=(0.25+3)/2=1.625. Bar 7: ag=(1.125+2)/2=1.5625,
    #   al=(1.625+0)/2=0.8125; rs=1.9231 -> RSI = 100 - 100/2.9231 = 65.789.
    #   SMA(3) = (15 + 12 + 14)/3 = 13.667; close 14 > 13.667 -> qualifies.
    #
    # BBB bar 7: deltas end [... +1, -3, +2]. Prior to bar 6 the series was a
    #   run of +1s, so ag saturates at 1.0, al stays 0. Bar 6 delta -3 ->
    #   ag=(1+0)/2=0.5, al=(0+3)/2=1.5. Bar 7 delta +2 -> ag=(0.5+2)/2=1.25,
    #   al=(1.5+0)/2=0.75; rs=1.6667 -> RSI = 100 - 100/2.6667 = 62.500.
    #   BBB qualifies AND has lower RSI than AAA (62.500 < 65.789) so the
    #   rsi_ascending ranking picks BBB. With max_concurrent_positions=1 and
    #   no prior holdings, only BBB is bought; AAA is rejected silently.
    #
    # BBB bar 9: entered at bar 7. Harness stamps held_days=1 at bar 8,
    #   held_days=2 at bar 9. The strategy reads held_days >= max_hold_days=2
    #   and emits a timeout SELL. (RSI 56.250 < 80 so there is no RSI-exit
    #   pressure; entry_price=24.00, close=24.50 > 24*(1-0.05)=22.80 so no
    #   stop-loss either — the timeout is the sole exit trigger.)
    #
    # AAA bar 11: delta -3.5 (from 13.5 to 10). Carrying the recursion forward
    #   from bar 10 (ag=0.1953, al=0.2266): ag=(0.1953+0)/2=0.0977,
    #   al=(0.2266+3.5)/2=1.8633; rs=0.0524 -> RSI = 100 - 100/1.0524 = 4.98.
    #   RSI 4.98 < 70 so the RSI filter passes, but SMA(3) =
    #   (13.5+13.5+10)/3 = 12.333 and close 10 < 12.333 -> SMA-BLOCK. No intent.

    aaa_closes = [10.0, 11.0, 12.0, 10.0, 11.5, 15.0, 12.0, 14.0, 14.0, 13.5, 13.5, 10.0]
    bbb_closes = [20.0, 21.0, 22.0, 23.0, 24.0, 25.0, 22.0, 24.0, 25.0, 24.5, 25.0, 25.0]

    # Expected shares:
    #   bar 4 BUY AAA  = floor(10_000 * 0.20 / 11.5) = floor(173.913) = 173
    #   bar 5 SELL AAA = 173
    #   bar 7 BUY BBB  = floor(10_000 * 0.20 / 24.0) = floor( 83.333) =  83
    #   bar 9 SELL BBB = 83
    expected: list[list[tuple[str, str, float]]] = [
        [],  # bar 0: warmup
        [],  # bar 1: warmup
        [],  # bar 2: rsi=100 everywhere, no entry
        [],  # bar 3: AAA rsi<70 but close<sma (block); BBB rsi=100
        [("buy", "AAA", 173.0)],  # bar 4: AAA entry
        [("sell", "AAA", 173.0)],  # bar 5: AAA RSI-exit
        [],  # bar 6: no position; AAA/BBB both blocked by sma
        [("buy", "BBB", 83.0)],  # bar 7: ranking picks BBB over AAA (lower rsi)
        [],  # bar 8: BBB held (rsi=75, still <exit=80)
        [("sell", "BBB", 83.0)],  # bar 9: BBB timeout (held_days == max_hold_days)
        [],  # bar 10: no position; AAA blocked by sma
        [],  # bar 11: AAA SMA-block (rsi=4.98 but close<sma)
    ]

    positions: dict[str, float] = {}
    entry_state: dict[str, dict[str, object]] = {}
    actual: list[list[tuple[str, str, float]]] = []

    for idx in range(len(aaa_closes)):
        # Harness stamps held_days at the *start* of each bar for any held
        # position. A BUY intent at bar N results in entry_state[sym] having
        # held_days=0 after bar N's bookkeeping; at bar N+1 the harness
        # increments to 1; at bar N+2 to 2; etc. That matches the convention
        # "held_days is the count of completed bars since entry, inclusive of
        # this evaluation."
        for sym in list(entry_state):
            if sym in positions:
                entry_state[sym]["held_days"] = int(entry_state[sym]["held_days"]) + 1

        context = _golden_context(
            positions=positions,
            entry_state=entry_state,
            bars_by_symbol={
                "AAA": _barset(aaa_closes[: idx + 1]),
                "BBB": _barset(bbb_closes[: idx + 1]),
            },
        )
        intents = strategy.evaluate(_barset([1.0]), context)
        actual.append([(intent.side.value, intent.symbol, intent.quantity) for intent in intents])
        positions, entry_state = _apply_golden_intents(
            positions=positions,
            entry_state=entry_state,
            intents=intents,
            bar_closes={"AAA": aaa_closes[idx], "BBB": bbb_closes[idx]},
        )

    assert actual == expected


def _golden_context(
    *,
    positions: dict[str, float],
    entry_state: dict[str, dict[str, object]],
    bars_by_symbol: dict[str, BarSet],
) -> StrategyContext:
    """Golden-test StrategyContext with the hand-computed parameter set."""
    return StrategyContext(
        strategy_id="meanrev.daily.pullback_rsi2.curated_largecap.v1",
        family="meanrev",
        template="daily.pullback_rsi2",
        variant="curated_largecap",
        version=1,
        config_hash="hash",
        parameters={
            "rsi_lookback": 2,
            "rsi_entry_threshold": 70,
            "rsi_exit_threshold": 80,
            "ma_filter_length": 3,
            "stop_loss_pct": 0.05,
            "max_hold_days": 2,
            "max_concurrent_positions": 1,
            "sizing_rule": "equal_notional",
            "per_position_notional_pct": 0.20,
            "ranking_enabled": True,
            "ranking_metric": "rsi_ascending",
        },
        universe=("AAA", "BBB"),
        universe_ref="universe.phase1.curated.v1",
        disable_conditions=(),
        config_path="configs/meanrev_daily_rsi2pullback_v1.yaml",
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
    """Mutate positions + entry_state by the intents produced on a bar.

    On BUY: record entry_price at the current bar's close and held_days=0.
    On SELL: drop the symbol from both positions and entry_state.

    The strategy module itself has no writer for entry_state; a real runner
    owns that. This helper is the minimum runner-shaped bookkeeping needed
    to drive the golden sequence without pulling in StrategyRunner (which is
    the next ticket, §4.1.5).
    """
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
