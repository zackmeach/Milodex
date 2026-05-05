"""Tests for the Donchian channel breakout strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.breakout_donchian import BreakoutDonchianStrategy

# ---------------------------------------------------------------------------
# Entry-side coverage
# ---------------------------------------------------------------------------


def test_breakout_enters_on_close_above_prior_channel_high() -> None:
    """Happy path: today's close clears the prior 20-day high, MA filter is
    bullish, sizing produces shares > 0 → BUY intent emitted.
    """
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_then_breakout(base=100.0)

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    intents = decision.intents
    assert len(intents) == 1
    assert intents[0].side == OrderSide.BUY
    assert intents[0].symbol == "XLK"
    assert intents[0].order_type == OrderType.MARKET
    assert decision.reasoning.rule == "breakout.channel_entry"


def test_breakout_excludes_latest_bar_from_channel_high() -> None:
    """The look-ahead-safe family invariant: the entry channel max is computed
    over bars STRICTLY PRIOR to the latest bar. If today's high were included
    in the reference, the trigger would be trivially true on every breakout
    day. This test pins the exclusion: the latest bar's high is set to a
    new all-time peak; the strategy must compare today's close against the
    prior 20 highs only.
    """
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_then_breakout(base=100.0)
    # Make today's high enormous; the close is unchanged. If the channel max
    # incorrectly included today's high, the close would not clear it and
    # the strategy would emit no signal. With today's high excluded, the
    # close still clears the prior 20-day max → trigger fires.
    last_open, _last_high, last_low, last_close = bars[-1]
    bars[-1] = (last_open, 10_000.0, last_low, last_close)

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert len(decision.intents) == 1, (
        "Channel max must exclude latest bar's high (look-ahead safety invariant)"
    )


def test_breakout_skips_close_at_or_below_channel_high() -> None:
    """A bar whose close fails to clear the prior channel high must be
    rejected — the breakout trigger is the only entry gate beyond the MA
    filter.
    """
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_no_breakout(base=100.0)

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.intents == []
    rejections = {
        entry["symbol"]: entry["reason"] for entry in decision.reasoning.rejected_alternatives
    }
    assert "XLK" in rejections
    assert "channel" in rejections["XLK"].lower()


def test_breakout_skips_close_below_ma_filter() -> None:
    """Even on a fresh channel breakout, a close below the MA filter must
    block entry. The MA filter is the family's bullish-regime gate.
    """
    strategy = BreakoutDonchianStrategy()
    bars = _bearish_breakout(base=100.0)

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.intents == []
    rejections = {
        entry["symbol"]: entry["reason"] for entry in decision.reasoning.rejected_alternatives
    }
    assert "XLK" in rejections
    assert "SMA" in rejections["XLK"] or "not above" in rejections["XLK"]


def test_breakout_ranks_by_breakout_strength_descending() -> None:
    """When more candidates qualify than capacity, the strongest breakout
    (largest overshoot of its own channel high) wins. Two symbols both
    break out on the same bar; strategy picks the stronger.
    """
    strategy = BreakoutDonchianStrategy()
    weaker = _ramp_then_breakout(base=100.0, breakout_overshoot=0.005)
    stronger = _ramp_then_breakout(base=100.0, breakout_overshoot=0.020)

    context = _context(
        universe=("XLF", "XLK"),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=1,
        ranking_enabled=True,
        bars_by_symbol={
            "XLF": _ohlc_barset(weaker),
            "XLK": _ohlc_barset(stronger),
        },
    )

    decision = strategy.evaluate(_ohlc_barset(stronger), context)
    assert [i.symbol for i in decision.intents] == ["XLK"]
    rejections = {
        entry["symbol"]: entry["reason"] for entry in decision.reasoning.rejected_alternatives
    }
    assert "XLF" in rejections
    assert "ranked below" in rejections["XLF"]


def test_breakout_ranking_disabled_keeps_universe_order() -> None:
    """ranking_enabled=False → first qualifying candidate in universe order
    wins.
    """
    strategy = BreakoutDonchianStrategy()
    bars_a = _ramp_then_breakout(base=100.0, breakout_overshoot=0.005)
    bars_b = _ramp_then_breakout(base=100.0, breakout_overshoot=0.020)  # would win on rank

    context = _context(
        universe=("XLF", "XLK"),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=1,
        ranking_enabled=False,
        bars_by_symbol={"XLF": _ohlc_barset(bars_a), "XLK": _ohlc_barset(bars_b)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars_a), context)
    assert [i.symbol for i in decision.intents] == ["XLF"]


# ---------------------------------------------------------------------------
# Exit-side coverage — one test per exit rule
# ---------------------------------------------------------------------------


def test_breakout_exits_on_atr_stop() -> None:
    """The ATR stop fires when close drops by more than
    `atr_stop_multiplier × live ATR` from entry price. Most-specific rule:
    ATR stop dominates percent stop, max_hold, and channel exit.
    """
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_then_breakdown(base=100.0, breakdown_pct=0.05)

    context = _context(
        universe=("XLK",),
        positions={"XLK": 10.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
        entry_state={"XLK": {"entry_price": float(bars[-2][3]), "held_days": 1}},
        # tighten ATR multiplier so the breakdown breaches it cleanly
        override_parameters={"atr_stop_multiplier": 1.0},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert [(i.side.value, i.symbol, i.quantity) for i in decision.intents] == [
        ("sell", "XLK", 10.0),
    ]
    assert decision.reasoning.rule == "breakout.atr_stop"


def test_breakout_exits_on_percent_stop_when_atr_window_too_short() -> None:
    """When ATR cannot be computed (insufficient history), the percent stop
    still fires on a meaningful loss. This is the fallback ladder.
    """
    strategy = BreakoutDonchianStrategy()
    # Series too short for ATR (need atr_lookback+1 = 21 bars). 15 is enough
    # for the exit_channel and entry_channel (both tighter in the override
    # below) but under the ATR window.
    bars = _short_breakdown_series()

    context = _context(
        universe=("XLK",),
        positions={"XLK": 10.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
        entry_state={"XLK": {"entry_price": 100.0, "held_days": 1}},
        override_parameters={
            "entry_channel_length": 5,
            "exit_channel_length": 3,
            "ma_filter_length": 5,
            "atr_lookback": 50,  # too long → ATR returns None → percent stop wins
            "stop_loss_pct": 0.05,
        },
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.reasoning.rule == "breakout.stop_loss"


def test_breakout_exits_on_max_hold_days() -> None:
    """Time stop: held_days >= max_hold_days fires when no other exit
    triggers (i.e. close above stops, close above channel low).
    """
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_flat_at_top(base=100.0)

    context = _context(
        universe=("XLK",),
        positions={"XLK": 10.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
        entry_state={"XLK": {"entry_price": float(bars[-1][3]) - 0.1, "held_days": 5}},
        # set big atr so the live atr stop can't trigger from tiny noise
        override_parameters={"atr_stop_multiplier": 100.0},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert [(i.side.value, i.symbol, i.quantity) for i in decision.intents] == [
        ("sell", "XLK", 10.0),
    ]
    assert decision.reasoning.rule == "breakout.max_hold"


def test_breakout_exits_on_channel_low_break() -> None:
    """Channel-low exit: today's close falls below the prior
    exit_channel_length-bar low.
    """
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_then_channel_low_break(base=100.0)

    context = _context(
        universe=("XLK",),
        positions={"XLK": 10.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
        # held_days small, entry_price high enough that no stop fires; only
        # the channel-low rule should trigger.
        entry_state={"XLK": {"entry_price": 130.0, "held_days": 1}},
        override_parameters={
            "stop_loss_pct": 0.50,  # extremely loose — won't fire
            "atr_stop_multiplier": 100.0,  # won't fire
            "max_hold_days": 50,
        },
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.reasoning.rule == "breakout.channel_exit"


# ---------------------------------------------------------------------------
# Universe scoping & no-signal coverage
# ---------------------------------------------------------------------------


def test_breakout_ignores_positions_outside_declared_universe() -> None:
    """A position on a symbol outside the breakout universe belongs to
    another strategy. Breakout must not emit an exit for it.
    """
    strategy = BreakoutDonchianStrategy()
    xlk_bars = _ramp_then_breakout(base=100.0)
    spy_bars = _ramp_then_channel_low_break(base=400.0)

    context = _context(
        universe=("XLK",),
        positions={"SPY": 10.0},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(xlk_bars), "SPY": _ohlc_barset(spy_bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(xlk_bars), context)
    sells = [i for i in decision.intents if i.side == OrderSide.SELL]
    assert sells == []


def test_breakout_returns_no_signal_reasoning_when_nothing_qualifies() -> None:
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_no_breakout(base=100.0)

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_breakout_market_regime_filter_blocks_entries_in_bearish_market() -> None:
    """When SPY is below its regime MA, entries are suppressed even if the
    candidate's own channel breakout fires.
    """
    strategy = BreakoutDonchianStrategy()
    xlk_bars = _ramp_then_breakout(base=100.0)
    spy_bars = _bear_market_series()

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(xlk_bars), "SPY": _ohlc_barset(spy_bars)},
        override_parameters={"market_regime_symbol": "SPY", "market_regime_ma_length": 200},
    )

    decision = strategy.evaluate(_ohlc_barset(xlk_bars), context)
    assert decision.intents == []
    assert "bearish" in decision.reasoning.narrative


# ---------------------------------------------------------------------------
# Reasoning + parameter validation + loader registration
# ---------------------------------------------------------------------------


def test_breakout_entry_reasoning_captures_strength_and_channel() -> None:
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_then_breakout(base=100.0)

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
    )

    decision = strategy.evaluate(_ohlc_barset(bars), context)

    assert decision.reasoning.rule == "breakout.channel_entry"
    triggers = decision.reasoning.triggering_values
    assert "selected_channel_high" in triggers
    assert "selected_breakout_strength" in triggers
    assert triggers["selected_breakout_strength"] > 0


def test_breakout_rejects_invalid_exit_channel_geq_entry_channel() -> None:
    """exit_channel_length >= entry_channel_length is a parameter-shape
    violation per the family contract — the exit channel must be tighter so
    winners can run further than the trigger.
    """
    strategy = BreakoutDonchianStrategy()
    bars = _ramp_then_breakout(base=100.0)

    context = _context(
        universe=("XLK",),
        positions={},
        equity=10_000.0,
        max_concurrent_positions=2,
        ranking_enabled=True,
        bars_by_symbol={"XLK": _ohlc_barset(bars)},
        override_parameters={"entry_channel_length": 10, "exit_channel_length": 10},
    )

    with pytest.raises(ValueError, match="exit_channel_length must be less than"):
        strategy.evaluate(_ohlc_barset(bars), context)


def test_default_strategy_loader_resolves_breakout_strategy() -> None:
    """The Donchian breakout strategy is registered in the default loader's
    registry and its config validates end-to-end.
    """
    loader = StrategyLoader()

    loaded = loader.load(Path("configs/breakout_daily_donchian_20_10_sector_etfs_v1.yaml"))

    assert isinstance(loaded.strategy, BreakoutDonchianStrategy)
    assert loaded.context.strategy_id == "breakout.daily.donchian_20_10.sector_etfs.v1"
    assert loaded.context.universe_ref == "universe.sector_etfs_spdr.v1"
    assert "XLK" in loaded.context.universe
    assert "XLF" in loaded.context.universe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        "entry_channel_length": 20,
        "exit_channel_length": 10,
        "ma_filter_length": 100,
        "atr_lookback": 20,
        "atr_stop_multiplier": 2.0,
        "stop_loss_pct": 0.10,
        "max_hold_days": 5,
        "max_concurrent_positions": max_concurrent_positions,
        "sizing_rule": "equal_notional",
        "per_position_notional_pct": 0.20,
        "ranking_enabled": ranking_enabled,
        "ranking_metric": "breakout_strength_descending",
        "market_regime_symbol": "",
        "market_regime_ma_length": 200,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="breakout.daily.donchian_20_10.sector_etfs.v1",
        family="breakout",
        template="daily.donchian_20_10",
        variant="sector_etfs",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=universe,
        universe_ref="universe.sector_etfs_spdr.v1",
        disable_conditions=(),
        config_path="configs/breakout_daily_donchian_20_10_sector_etfs_v1.yaml",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _ohlc_barset(rows: list[tuple[float, float, float, float]]) -> BarSet:
    timestamps = pd.date_range("2025-01-01", periods=len(rows), freq="D", tz=UTC)
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "volume": [1_000_000] * len(rows),
            "vwap": [row[3] for row in rows],
        }
    )
    return BarSet(df)


def _ramp_then_breakout(
    *,
    base: float,
    length: int = 130,
    breakout_overshoot: float = 0.010,
) -> list[tuple[float, float, float, float]]:
    """Series that ramps gently for `length-1` bars, then on the final bar
    pierces the prior 20-day rolling channel high by `breakout_overshoot`.

    The ramp keeps each bar within a tight 0.5%-of-base intraday range so
    the channel-high trajectory is monotone — making the breakout
    unambiguous.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length - 1):
        c = base + idx * 0.20
        h = c + base * 0.001
        low = c - base * 0.001
        rows.append((c, h, low, c))
    # Establish prior 20-day channel high based on the last 20 of the ramp
    # (excluding the final bar we're about to add).
    prior_window_high = max(row[1] for row in rows[-20:])
    final_close = prior_window_high * (1.0 + breakout_overshoot)
    final_high = final_close * 1.001
    final_low = final_close * 0.998
    final_open = rows[-1][3]
    rows.append((final_open, final_high, final_low, final_close))
    return rows


def _ramp_no_breakout(
    *,
    base: float,
    length: int = 130,
) -> list[tuple[float, float, float, float]]:
    """Same shape as _ramp_then_breakout but the final close stays beneath
    the prior channel high. Used to confirm the trigger requires a strict
    overshoot.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length - 1):
        c = base + idx * 0.20
        h = c + base * 0.001
        low = c - base * 0.001
        rows.append((c, h, low, c))
    # Final bar exactly matches the prior peak — strict ">" triggers fail.
    prior_window_high = max(row[1] for row in rows[-20:])
    final_close = prior_window_high * 0.999  # below
    rows.append((rows[-1][3], final_close * 1.001, final_close * 0.998, final_close))
    return rows


def _bearish_breakout(
    *,
    base: float,
    length: int = 130,
) -> list[tuple[float, float, float, float]]:
    """Series where price falls relentlessly so close < SMA(100), then a
    final bar that *does* clear the prior 20-day channel high (because the
    channel itself is collapsing). The MA filter must reject the entry.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length - 1):
        c = base - idx * 0.30
        h = c + base * 0.002
        low = c - base * 0.002
        rows.append((c, h, low, c))
    prior_window_high = max(row[1] for row in rows[-20:])
    final_close = prior_window_high * 1.005
    final_high = final_close * 1.001
    final_low = final_close * 0.998
    rows.append((rows[-1][3], final_high, final_low, final_close))
    return rows


def _ramp_then_breakdown(
    *,
    base: float,
    length: int = 130,
    breakdown_pct: float = 0.05,
) -> list[tuple[float, float, float, float]]:
    """Ramp upward, then a final bar where close drops by ``breakdown_pct``
    from the second-to-last close. Used for ATR-stop coverage. ATR is
    computed live, so the breakdown bar's range matters for the stop calc.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length - 1):
        c = base + idx * 0.5
        h = c + base * 0.005
        low = c - base * 0.005
        rows.append((c, h, low, c))
    prior_close = rows[-1][3]
    final_close = prior_close * (1 - breakdown_pct)
    final_high = prior_close * 1.001
    final_low = final_close * 0.99
    rows.append((prior_close, final_high, final_low, final_close))
    return rows


def _ramp_then_channel_low_break(
    *,
    base: float,
    length: int = 130,
) -> list[tuple[float, float, float, float]]:
    """Ramp upward to make the MA pass, then a final bar where close falls
    below the prior 10-day low.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length - 1):
        c = base + idx * 0.5
        h = c + base * 0.001
        low = c - base * 0.001
        rows.append((c, h, low, c))
    prior_window_low = min(row[2] for row in rows[-10:])
    final_close = prior_window_low * 0.99
    final_high = final_close * 1.001
    final_low = final_close * 0.999
    rows.append((rows[-1][3], final_high, final_low, final_close))
    return rows


def _ramp_flat_at_top(
    *,
    base: float,
    length: int = 130,
) -> list[tuple[float, float, float, float]]:
    """Ramp upward then plateau on the final bars. No exit signal, no stop —
    used for max_hold-only coverage.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length - 1):
        c = base + idx * 0.20
        h = c + base * 0.001
        low = c - base * 0.001
        rows.append((c, h, low, c))
    last = rows[-1][3]
    rows.append((last, last + base * 0.001, last - base * 0.001, last))
    return rows


def _short_breakdown_series() -> list[tuple[float, float, float, float]]:
    """A 15-bar OHLC series with a final-bar close beneath the configured
    percent stop. Used to test the percent-stop fallback when ATR is
    unavailable due to lookback exceeding the series length.
    """
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(14):
        c = 100.0 + idx * 0.1
        rows.append((c, c + 0.05, c - 0.05, c))
    rows.append((100.0, 100.5, 92.0, 92.0))  # close 92 < entry 100 * 0.95 = 95
    return rows


def _bear_market_series(length: int = 250) -> list[tuple[float, float, float, float]]:
    """SPY-like series in a bear market — used for the regime filter test."""
    rows: list[tuple[float, float, float, float]] = []
    for idx in range(length):
        c = 500.0 - idx * 0.5
        rows.append((c, c + 0.5, c - 0.5, c))
    return rows
