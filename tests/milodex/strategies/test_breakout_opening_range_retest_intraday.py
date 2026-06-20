"""Tests for the opening-range retest intraday strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd
from milodex.strategies.breakout_opening_range_retest_intraday import (
    BreakoutOpeningRangeRetestIntradayStrategy,
)

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
#
# Parameters: opening_range_minutes=30 (6 bars), entry_window_minutes=90,
#             retest_band_pct=0.003, stop_loss_pct=0.01,
#             exit_minutes_before_close=5, per_position_notional_pct=0.10.
#
# Entry window = [30, 120) min after 9:30 ET → [10:00, 11:30) ET.
# range_high from max(high) of the first 6 bars; range_low from min(low).
#
# 3-phase entry condition:
#   (a) a prior in-window bar broke above range_high (close > range_high)
#   (b) a bar after that dipped to <= range_high (retest)
#   (c) latest_close > range_high AND latest_low >= range_high*(1-retest_band_pct)
#
# One-entry re-scan key: a prior in-window bar completed all three phases.

_RANGE_HIGH = 505.0
_RANGE_LOW = 499.0


def _session_bars(date_et: str, rows: list[tuple[str, float, float, float, float]]) -> BarSet:
    """Build a BarSet for a single session.

    Each row is (time_et "HH:MM", open, high, low, close). Volume fixed at 1M.
    """
    records: list[dict[str, Any]] = []
    for time_str, o, h, low, c in rows:
        et = pd.Timestamp(f"{date_et} {time_str}:00").tz_localize("America/New_York")
        records.append(
            {
                "timestamp": et.tz_convert("UTC"),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": 1_000_000,
                "vwap": float(c),
            }
        )
    return BarSet(pd.DataFrame(records))


def _orb_session(
    date_et: str = "2024-01-15",
    extra: list[tuple[str, float, float, float, float]] | None = None,
) -> BarSet:
    """A session with a standard 30-min opening range (range_high=505, range_low=499).

    Six opening-range bars at 9:30-9:55 ET, then optional post-range bars.
    """
    rows: list[tuple[str, float, float, float, float]] = [
        ("09:30", 500.0, 501.0, 499.0, 500.0),
        ("09:35", 500.0, 502.0, 499.5, 501.0),
        ("09:40", 501.0, 503.0, 500.0, 502.0),
        ("09:45", 502.0, 504.0, 500.5, 503.0),
        ("09:50", 503.0, 505.0, 501.0, 504.0),
        ("09:55", 504.0, 505.0, 502.0, 503.5),
        # range_high = 505.0 (max of highs), range_low = 499.0 (min of lows)
    ]
    if extra:
        rows.extend(extra)
    return _session_bars(date_et, rows)


def _context(
    *,
    positions: dict[str, float],
    equity: float,
    bars: BarSet,
    override_parameters: dict[str, Any] | None = None,
    entry_state: dict[str, Any] | None = None,
) -> StrategyContext:
    parameters: dict[str, Any] = {
        "opening_range_minutes": 30,
        "entry_window_minutes": 90,
        "retest_band_pct": 0.003,
        "stop_loss_pct": 0.01,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)
    return StrategyContext(
        strategy_id="breakout.opening_range_retest.intraday.spy.v1",
        family="breakout",
        template="opening_range_retest.intraday",
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
        entry_state=entry_state or {},
    )


# ---------------------------------------------------------------------------
# Entry tests
# ---------------------------------------------------------------------------


def test_entry_buys_on_breakout_retest_reclaim() -> None:
    """Full 3-phase retest sequence → BUY on the reclaim bar."""
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    # Phase (a): 10:00 ET bar breaks out (close 506 > range_high 505)
    # Phase (b): 10:05 ET bar dips back to range_high (close 505.0 = range_high)
    # Phase (c): 10:10 ET bar reclaims (close 506.5 > range_high, low 504.9 >= 505*(1-0.003)=503.5)
    bars = _orb_session(
        extra=[
            ("10:00", 505.5, 507.0, 505.0, 506.0),  # breakout
            ("10:05", 505.8, 506.0, 504.5, 505.0),  # retest dip (close = range_high)
            ("10:10", 505.2, 507.0, 504.9, 506.5),  # reclaim bar
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side == OrderSide.BUY
    assert intent.symbol == "SPY"
    assert decision.reasoning.rule == "breakout.orb_retest.entry"
    triggers = decision.reasoning.triggering_values
    assert "range_high" in triggers
    assert triggers["range_high"] == _RANGE_HIGH


def test_no_entry_without_initial_breakout() -> None:
    """If no prior bar broke above range_high, phase (a) fails → no entry."""
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    # In-window bars that never exceed range_high
    bars = _orb_session(
        extra=[
            ("10:00", 504.0, 504.5, 503.5, 504.0),  # close below range_high
            ("10:05", 504.5, 505.0, 504.0, 504.5),  # still below
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "no prior breakout" in decision.reasoning.narrative.lower()


def test_no_entry_without_retest() -> None:
    """Breakout happened but price never pulled back to <= range_high → no entry."""
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    # Price breaks out and keeps running — never retests
    bars = _orb_session(
        extra=[
            ("10:00", 505.5, 507.0, 505.0, 506.0),  # breakout
            ("10:05", 506.5, 508.0, 506.0, 507.5),  # keeps running, no dip
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "no retest" in decision.reasoning.narrative.lower()


def test_no_entry_outside_window() -> None:
    """Full 3-phase setup but the latest bar is after the entry window → no entry."""
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    # 11:30 is offset 120 min, past [30, 120)
    bars = _orb_session(
        extra=[
            ("10:00", 505.5, 507.0, 505.0, 506.0),  # breakout
            ("10:05", 505.8, 506.0, 504.5, 505.0),  # retest
            ("11:30", 505.2, 507.0, 504.9, 506.5),  # reclaim but outside window
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "outside entry window" in decision.reasoning.narrative


def test_one_entry_per_session() -> None:
    """Two qualifying reclaim bars with positions={}: second emits no_signal."""
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    bars = _orb_session(
        extra=[
            ("10:00", 505.5, 507.0, 505.0, 506.0),  # breakout
            ("10:05", 505.8, 506.0, 504.5, 505.0),  # retest
            ("10:10", 505.2, 507.0, 504.9, 506.5),  # first reclaim (one-entry shot)
            ("10:15", 505.5, 507.5, 505.0, 507.0),  # second reclaim — blocked
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "one entry per session" in decision.reasoning.narrative


# ---------------------------------------------------------------------------
# Exit tests
# ---------------------------------------------------------------------------


def test_structural_stop_on_range_low_breach() -> None:
    """With open position, latest_low <= range_low triggers structural SELL."""
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    bars = _orb_session(
        extra=[
            ("10:10", 500.0, 501.0, 498.0, 499.5),  # low 498 < range_low 499
        ],
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 506.0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert "structural_stop" in decision.reasoning.rule


def test_stop_loss_pct_exits_position() -> None:
    """With open position, close <= entry_price*(1-stop_loss_pct) → SELL."""
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    entry_price = 506.0
    stop_close = entry_price * (1 - 0.01) - 0.01  # just below 1% stop

    bars = _orb_session(
        extra=[
            # low stays above range_low 499; only the stop_loss_pct fires
            ("10:10", entry_price, entry_price + 0.5, stop_close - 0.05, stop_close),
        ],
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": entry_price}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert "stop_loss" in decision.reasoning.rule


def test_time_stop_forces_exit() -> None:
    """At 15:55 ET (time-stop bar), open position is closed."""
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    bars = _orb_session(
        extra=[("15:55", 507.0, 507.5, 506.5, 507.0)],
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 506.0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert "time_stop" in decision.reasoning.rule


# ---------------------------------------------------------------------------
# max_lookback_periods
# ---------------------------------------------------------------------------


def test_max_lookback_periods_is_78() -> None:
    """Retest strategy is session-reset; one RTH session (78 bars) suffices."""
    # ponytail: no cross-session reads; 390min / 5min = 78
    assert BreakoutOpeningRangeRetestIntradayStrategy().max_lookback_periods() == 78
