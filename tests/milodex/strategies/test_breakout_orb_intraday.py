"""Tests for the Opening Range Breakout intraday strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.breakout_orb_intraday import BreakoutOrbIntradayStrategy


def test_opening_range_correctly_computed_from_first_six_bars() -> None:
    """The opening range is the max(high) / min(low) of the first 6 5min bars
    (9:30-9:55 ET). The strategy should compute this faithfully and react to
    the latest bar's close vs the range.
    """
    strategy = BreakoutOrbIntradayStrategy()

    # First 6 bars (9:30-9:55) form the opening range. Highs span 501-505.
    # Lows span 499-503. So range_high=505, range_low=499. The 10:10 bar
    # closes at 510 — clearly above range_high.
    bars = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        post_range=[
            ("10:00", 503.5, 504.0, 503.0, 503.5),
            ("10:05", 504.0, 504.5, 503.5, 504.0),
            ("10:10", 506.0, 510.0, 506.0, 510.0),  # latest bar — breakout
        ],
    )
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side == OrderSide.BUY
    assert intent.symbol == "SPY"
    assert decision.reasoning.rule == "breakout.orb.entry"
    triggers = decision.reasoning.triggering_values
    assert triggers["range_high"] == 505.0
    assert triggers["range_low"] == 499.0  # lowest low in the opening range
    assert triggers["latest_close"] == 510.0


def test_entry_emits_buy_intent_on_breakout() -> None:
    """A bar within the entry window with close > range_high produces BUY."""
    strategy = BreakoutOrbIntradayStrategy()
    bars = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        post_range=[("10:15", 506.0, 507.0, 505.5, 506.5)],  # close 506.5 > range_high 505
    )
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.BUY
    assert decision.intents[0].order_type == OrderType.MARKET


def test_no_entry_outside_window() -> None:
    """A breakout AFTER the entry window closes produces no intent."""
    strategy = BreakoutOrbIntradayStrategy()
    bars = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        # Breakout at 11:30 — past the [10:00, 11:00) window.
        post_range=[("11:30", 510.0, 511.0, 509.0, 510.5)],
    )
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert "outside entry window" in decision.reasoning.narrative


def test_stop_on_range_re_entry() -> None:
    """With an open position, low <= range_low triggers a SELL."""
    strategy = BreakoutOrbIntradayStrategy()
    bars = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        post_range=[
            ("10:00", 503.5, 506.0, 503.0, 505.5),  # entry would have fired here
            ("10:30", 504.0, 504.5, 497.0, 498.5),  # latest: low 497 < range_low 498
        ],
    )
    # We have an open SPY position (presume the entry fired earlier).
    context = _context(positions={"SPY": 5.0}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side == OrderSide.SELL
    assert intent.quantity == 5.0
    assert decision.reasoning.rule == "breakout.orb.stop_loss"


def test_time_stop_forces_exit_at_15_55_et() -> None:
    """The time-stop bar (15:55 ET) forces an exit regardless of price."""
    strategy = BreakoutOrbIntradayStrategy()
    bars = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        # The 15:55 bar shows price still above the range — only the
        # time-stop should trigger exit, not the stop_loss.
        post_range=[("15:55", 508.0, 509.0, 507.5, 508.5)],
    )
    context = _context(positions={"SPY": 5.0}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.reasoning.rule == "breakout.orb.time_stop"


def test_one_entry_per_session_no_re_entry_after_stop() -> None:
    """If an earlier bar in the entry window already broke above range_high,
    the strategy refuses to re-enter even if the position has been closed.
    """
    strategy = BreakoutOrbIntradayStrategy()
    bars = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        post_range=[
            ("10:00", 503.5, 506.0, 503.0, 505.5),  # prior breakout (close > 505)
            ("10:15", 506.5, 507.0, 506.0, 506.5),  # would-be re-breakout
        ],
    )
    # Position closed (e.g., entry filled and stopped out elsewhere).
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "one entry per session" in decision.reasoning.narrative


def test_cross_session_resets_opening_range() -> None:
    """On a new session, the strategy computes a fresh opening range from
    that session's first 6 bars — NOT from the previous session's bars.
    """
    strategy = BreakoutOrbIntradayStrategy()

    # Build two sessions: Day 1 has a very high range (highs up to 600).
    # Day 2 has a low range (highs up to 505). The latest bar is on Day 2.
    day1 = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[598, 599, 600, 599, 598, 597],
        opening_range_lows=[595, 596, 597, 596, 595, 594],
        opening_range_closes=[597, 598, 599, 598, 597, 596],
        post_range=[],
    )
    day2 = _orb_session_bars(
        date_et="2024-01-16",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        post_range=[("10:15", 506.0, 507.0, 505.5, 506.5)],
    )
    combined_df = (
        pd.concat([day1.to_dataframe(), day2.to_dataframe()], ignore_index=True)
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    combined = BarSet(combined_df)
    context = _context(positions={}, equity=10_000.0, bars=combined)

    decision = strategy.evaluate(combined, context)

    # Day 2's range_high is 505 (not 600). Close of 506.5 broke that, not Day 1's range.
    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.BUY
    assert decision.reasoning.triggering_values["range_high"] == 505.0


def test_half_day_session_skipped() -> None:
    """On a known half-day (e.g., 2024-11-29 = day after Thanksgiving),
    the strategy emits no entries regardless of price action.
    """
    strategy = BreakoutOrbIntradayStrategy()
    bars = _orb_session_bars(
        date_et="2024-11-29",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        post_range=[("10:15", 510.0, 511.0, 509.0, 510.5)],
    )
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "half-day" in decision.reasoning.narrative


def test_gap_up_open_does_not_break_opening_range_computation() -> None:
    """A first bar that opens way above the prior session's high is still
    valid input; range_high simply reflects today's actual range.
    """
    strategy = BreakoutOrbIntradayStrategy()
    # First bar opens at 510 (gap up). Range spans 510-515.
    bars = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[513, 514, 515, 514, 513, 512],
        opening_range_lows=[510, 511, 512, 511, 510, 509],  # range_low = 509
        opening_range_closes=[512, 513, 514, 513, 512, 511],
        post_range=[("10:15", 516.0, 517.0, 515.5, 516.5)],  # close 516.5 > range_high 515
    )
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.BUY
    assert decision.reasoning.triggering_values["range_high"] == 515.0
    assert decision.reasoning.triggering_values["range_low"] == 509.0


def test_signal_at_last_window_bar_emits_intent() -> None:
    """A breakout signal on the 10:55 ET bar (last in-window bar) emits an
    intent. A breakout on the 11:00 ET bar does NOT (outside window).
    """
    strategy = BreakoutOrbIntradayStrategy()

    # 10:55 bar — should fire
    bars_in_window = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        post_range=[("10:55", 506.0, 507.0, 505.5, 506.5)],
    )
    context_in = _context(positions={}, equity=10_000.0, bars=bars_in_window)
    decision_in = strategy.evaluate(bars_in_window, context_in)
    assert len(decision_in.intents) == 1
    assert decision_in.intents[0].side == OrderSide.BUY

    # 11:00 bar — should NOT fire (window-end is exclusive)
    bars_out_window = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503, 504, 505, 504],
        opening_range_lows=[499, 500, 501, 502, 503, 502],
        opening_range_closes=[500, 501, 502, 503, 504, 503],
        post_range=[("11:00", 506.0, 507.0, 505.5, 506.5)],
    )
    context_out = _context(positions={}, equity=10_000.0, bars=bars_out_window)
    decision_out = strategy.evaluate(bars_out_window, context_out)
    assert decision_out.intents == []
    assert "outside entry window" in decision_out.reasoning.narrative


def test_still_in_opening_range_emits_no_signal() -> None:
    """A bar timestamped before 10:00 ET (still inside the opening range)
    cannot trigger an entry — the range isn't formed yet.
    """
    strategy = BreakoutOrbIntradayStrategy()
    # Only 3 bars into the session; latest is at 9:40 ET.
    bars = _orb_session_bars(
        date_et="2024-01-15",
        opening_range_highs=[501, 502, 503],
        opening_range_lows=[499, 500, 501],
        opening_range_closes=[500, 501, 502],
        post_range=[],
    )
    context = _context(positions={}, equity=10_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "still in opening range" in decision.reasoning.narrative


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
        "entry_window_minutes": 60,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="breakout.orb.intraday.spy.v1",
        family="breakout",
        template="orb.intraday",
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


def _orb_session_bars(
    *,
    date_et: str,
    opening_range_highs: list[float],
    opening_range_lows: list[float],
    opening_range_closes: list[float],
    post_range: list[tuple[str, float, float, float, float]],
) -> BarSet:
    """Build a BarSet for a single ORB session.

    Opening range bars are timestamped at 9:30, 9:35, ... in ET converted to
    UTC. ``post_range`` rows are (time_et "HH:MM", open, high, low, close).
    """
    rows: list[dict[str, Any]] = []

    # Determine the UTC offset for this date. Use a quick conversion via
    # pandas — 9:30 ET on the target date in UTC.
    open_et = pd.Timestamp(f"{date_et} 09:30:00").tz_localize("America/New_York")
    open_utc = open_et.tz_convert("UTC")

    # Opening range bars at 5min spacing
    for i, (h, low, c) in enumerate(
        zip(opening_range_highs, opening_range_lows, opening_range_closes, strict=True)
    ):
        ts = open_utc + pd.Timedelta(minutes=5 * i)
        rows.append(
            {
                "timestamp": ts,
                "open": c,  # placeholder — IBS-style; close used for open
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": 1_000_000,
                "vwap": float(c),
            }
        )

    # Post-range bars (specific HH:MM ET)
    for time_str, o, h, low, c in post_range:
        et_ts = pd.Timestamp(f"{date_et} {time_str}:00").tz_localize("America/New_York")
        utc_ts = et_ts.tz_convert("UTC")
        rows.append(
            {
                "timestamp": utc_ts,
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": 1_000_000,
                "vwap": float(c),
            }
        )

    df = pd.DataFrame(rows)
    return BarSet(df)
