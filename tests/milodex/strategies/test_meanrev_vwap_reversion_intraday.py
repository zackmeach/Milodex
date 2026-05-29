"""Tests for the VWAP mean-reversion intraday strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.meanrev_vwap_reversion_intraday import (
    MeanrevVwapReversionIntradayStrategy,
)

# Six opening-range bars (9:30-9:55 ET) all at price 500 → session VWAP starts at 500.
_OPENING_RANGE_AT_500 = [
    ("09:30", 500.0, 500.5, 499.5, 500.0, 1_000_000),
    ("09:35", 500.0, 500.5, 499.5, 500.0, 1_000_000),
    ("09:40", 500.0, 500.5, 499.5, 500.0, 1_000_000),
    ("09:45", 500.0, 500.5, 499.5, 500.0, 1_000_000),
    ("09:50", 500.0, 500.5, 499.5, 500.0, 1_000_000),
    ("09:55", 500.0, 500.5, 499.5, 500.0, 1_000_000),
]


def test_entry_buys_when_stretched_below_vwap() -> None:
    """A bar in the entry window that closes meaningfully below session VWAP
    produces a BUY."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    # Opening range at 500; a 10:30 bar closing at 495 is ~0.86% below the
    # cumulative VWAP (≈499.29) — past the 0.4% entry threshold.
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_AT_500 + [("10:30", 497.0, 497.5, 494.5, 495.0, 1_000_000)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side == OrderSide.BUY
    assert intent.symbol == "SPY"
    assert intent.order_type == OrderType.MARKET
    assert decision.reasoning.rule == "meanrev.vwap.entry"


def test_no_entry_when_deviation_insufficient() -> None:
    """A bar barely below VWAP (under the threshold) produces no entry."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_AT_500 + [("10:30", 499.5, 500.0, 499.0, 499.5, 1_000_000)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert "not stretched enough" in decision.reasoning.narrative


def test_no_entry_outside_window() -> None:
    """A below-VWAP stretch after the entry window closes produces no entry."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    # 15:30 ET is past the [10:00, 15:00) window.
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_AT_500 + [("15:30", 497.0, 497.5, 494.5, 495.0, 1_000_000)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "outside entry window" in decision.reasoning.narrative


def test_target_exit_on_reversion_to_vwap() -> None:
    """With an open position, a close at/above session VWAP takes profit."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    # Opening range at 498 → VWAP ≈ 498.29 after the 10:30 bar; close 500 >= VWAP.
    opening_at_498 = [(t, 498.0, 498.5, 497.5, 498.0, 1_000_000) for t, *_ in _OPENING_RANGE_AT_500]
    bars = _session_bars(
        "2024-01-15",
        opening_at_498 + [("10:30", 499.0, 500.5, 499.0, 500.0, 1_000_000)],
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 497.0, "held_days": 0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.intents[0].quantity == 5.0
    assert decision.reasoning.rule == "meanrev.vwap.target"


def test_stop_loss_exit_below_entry_price() -> None:
    """A close below entry_price * (1 - stop_loss_pct) triggers the stop."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    # entry 500, stop at 500 * 0.995 = 497.5; latest close 497 <= 497.5.
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_AT_500 + [("10:30", 498.0, 498.5, 496.5, 497.0, 1_000_000)],
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 500.0, "held_days": 0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.reasoning.rule == "meanrev.vwap.stop_loss"


def test_time_stop_forces_exit() -> None:
    """The time-stop bar (15:55 ET) forces an exit when neither stop nor
    target has fired."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    # close 499 is below VWAP (no target) and above stop (entry 495) — only the
    # time-stop should trigger.
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_AT_500 + [("15:55", 499.0, 499.5, 498.5, 499.0, 1_000_000)],
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 495.0, "held_days": 0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.reasoning.rule == "meanrev.vwap.time_stop"


def test_one_entry_per_session_blocks_re_entry() -> None:
    """If an earlier in-window bar already stretched below VWAP, the strategy
    refuses a second entry even while flat."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_AT_500
        + [
            ("10:30", 497.0, 497.5, 494.5, 495.0, 1_000_000),  # prior entry signal
            ("11:30", 497.0, 497.5, 494.5, 495.0, 1_000_000),  # would-be re-entry
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "one entry per session" in decision.reasoning.narrative


def test_half_day_session_skipped() -> None:
    """On a known half-day, no entry regardless of price action."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    bars = _session_bars(
        "2024-11-29",  # day after Thanksgiving — half-day
        _OPENING_RANGE_AT_500 + [("10:30", 497.0, 497.5, 494.5, 495.0, 1_000_000)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "half-day" in decision.reasoning.narrative


def test_zero_volume_session_yields_no_signal() -> None:
    """When session VWAP is undefined (no volume), the flat path emits no signal."""
    strategy = MeanrevVwapReversionIntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        [("10:30", 500.0, 500.5, 499.5, 495.0, 0)],  # zero volume → VWAP undefined
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "VWAP undefined" in decision.reasoning.narrative


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(
    *,
    positions: dict[str, float],
    equity: float,
    bars: BarSet,
    entry_state: dict[str, dict[str, Any]] | None = None,
    override_parameters: dict[str, Any] | None = None,
) -> StrategyContext:
    parameters: dict[str, Any] = {
        "opening_range_minutes": 30,
        "entry_window_minutes": 300,
        "entry_deviation_pct": 0.004,
        "stop_loss_pct": 0.005,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="meanrev.vwap_reversion.intraday.spy.v1",
        family="meanrev",
        template="vwap_reversion.intraday",
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


def _session_bars(
    date_et: str,
    rows: list[tuple[str, float, float, float, float, float]],
) -> BarSet:
    """Build a BarSet from (time_et "HH:MM", open, high, low, close, volume) rows.

    ET times are converted to UTC for the target date (DST handled by pandas).
    """
    records: list[dict[str, Any]] = []
    for time_str, o, h, low, c, vol in rows:
        et_ts = pd.Timestamp(f"{date_et} {time_str}:00").tz_localize("America/New_York")
        utc_ts = et_ts.tz_convert("UTC")
        records.append(
            {
                "timestamp": utc_ts,
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": float(vol),
                "vwap": float(c),
            }
        )
    return BarSet(pd.DataFrame(records))
