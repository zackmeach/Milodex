"""Tests for the VWAP trend-continuation intraday strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.momentum_vwap_trend_intraday import (
    MomentumVwapTrendIntradayStrategy,
)

# Six climbing opening-range bars (9:30-9:55 ET): closes 500..505, equal volume.
# After a 7th bar the cumulative VWAP sits near the middle of the range.
_OPENING_RANGE_CLIMB = [
    ("09:30", 500.0, 500.5, 499.5, 500.0, 1_000_000),
    ("09:35", 501.0, 501.5, 500.5, 501.0, 1_000_000),
    ("09:40", 502.0, 502.5, 501.5, 502.0, 1_000_000),
    ("09:45", 503.0, 503.5, 502.5, 503.0, 1_000_000),
    ("09:50", 504.0, 504.5, 503.5, 504.0, 1_000_000),
    ("09:55", 505.0, 505.5, 504.5, 505.0, 1_000_000),
]


def test_entry_buys_on_above_vwap_momentum_volume() -> None:
    """A bar above VWAP with positive momentum and volume confirmation BUYs."""
    strategy = MomentumVwapTrendIntradayStrategy()
    # 10:00 bar at 506 with 2x volume: above VWAP (~503), close > close 6 bars
    # ago (500), volume 2M > 1.2 x 1M prior mean.
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_CLIMB + [("10:00", 506.0, 506.5, 505.5, 506.0, 2_000_000)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side == OrderSide.BUY
    assert intent.symbol == "SPY"
    assert decision.reasoning.rule == "momentum.vwap_trend.entry"


def test_no_entry_when_not_above_vwap() -> None:
    """A bar barely above VWAP (under min_above_vwap_pct) produces no entry."""
    strategy = MomentumVwapTrendIntradayStrategy()
    flat = [(t, 500.0, 500.5, 499.5, 500.0, 1_000_000) for t, *_ in _OPENING_RANGE_CLIMB]
    bars = _session_bars(
        "2024-01-15",
        flat + [("10:00", 500.2, 500.7, 499.7, 500.2, 2_000_000)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "not extended" in decision.reasoning.narrative


def test_no_entry_without_positive_momentum() -> None:
    """Above VWAP but no positive momentum (close <= close N bars ago) → no entry."""
    strategy = MomentumVwapTrendIntradayStrategy()
    # Declining range then a bar that is above VWAP but below the close 6 bars ago.
    declining = [
        ("09:30", 506.0, 506.5, 505.5, 506.0, 1_000_000),
        ("09:35", 505.0, 505.5, 504.5, 505.0, 1_000_000),
        ("09:40", 504.0, 504.5, 503.5, 504.0, 1_000_000),
        ("09:45", 503.0, 503.5, 502.5, 503.0, 1_000_000),
        ("09:50", 502.0, 502.5, 501.5, 502.0, 1_000_000),
        ("09:55", 501.0, 501.5, 500.5, 501.0, 1_000_000),
    ]
    bars = _session_bars(
        "2024-01-15",
        declining + [("10:00", 505.0, 505.5, 504.5, 505.0, 2_000_000)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "no positive momentum" in decision.reasoning.narrative


def test_no_entry_without_volume_confirmation() -> None:
    """Above VWAP with momentum but low volume → no entry."""
    strategy = MomentumVwapTrendIntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_CLIMB + [("10:00", 506.0, 506.5, 505.5, 506.0, 1_000_000)],  # same volume
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "no volume confirmation" in decision.reasoning.narrative


def test_stop_loss_exit_below_entry_price() -> None:
    strategy = MomentumVwapTrendIntradayStrategy()
    # entry 506, stop at 503.47; latest close 503 <= 503.47.
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_CLIMB + [("10:30", 504.0, 504.5, 502.5, 503.0, 1_000_000)],
    )
    context = _context(
        positions={"SPY": 10.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 506.0, "held_days": 0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.reasoning.rule == "momentum.vwap_trend.stop_loss"


def test_invalidation_exit_on_close_below_vwap() -> None:
    """A close back below session VWAP invalidates the trend → exit."""
    strategy = MomentumVwapTrendIntradayStrategy()
    # VWAP ≈ 502.7 after the 10:30 bar; close 502 < VWAP; entry 500 (no stop).
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_CLIMB + [("10:30", 502.5, 503.0, 501.5, 502.0, 1_000_000)],
    )
    context = _context(
        positions={"SPY": 10.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 500.0, "held_days": 0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.reasoning.rule == "momentum.vwap_trend.invalidation"


def test_time_stop_forces_exit() -> None:
    strategy = MomentumVwapTrendIntradayStrategy()
    # 15:55 bar above VWAP (no invalidation), above stop (entry 500) → time-stop.
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_CLIMB + [("15:55", 506.0, 506.5, 505.5, 506.0, 1_000_000)],
    )
    context = _context(
        positions={"SPY": 10.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 500.0, "held_days": 0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.reasoning.rule == "momentum.vwap_trend.time_stop"


def test_one_entry_per_session_blocks_re_entry() -> None:
    strategy = MomentumVwapTrendIntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        _OPENING_RANGE_CLIMB
        + [
            ("10:00", 506.0, 506.5, 505.5, 506.0, 2_000_000),  # prior above-VWAP signal
            ("10:30", 507.0, 507.5, 506.5, 507.0, 2_000_000),  # would-be re-entry
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "one entry per session" in decision.reasoning.narrative


def test_half_day_session_skipped() -> None:
    strategy = MomentumVwapTrendIntradayStrategy()
    bars = _session_bars(
        "2024-11-29",
        _OPENING_RANGE_CLIMB + [("10:00", 506.0, 506.5, 505.5, 506.0, 2_000_000)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "half-day" in decision.reasoning.narrative


def test_zero_volume_session_yields_no_signal() -> None:
    strategy = MomentumVwapTrendIntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        [("10:00", 506.0, 506.5, 505.5, 506.0, 0)],
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
        "entry_window_minutes": 120,
        "min_above_vwap_pct": 0.001,
        "momentum_lookback_bars": 6,
        "volume_factor": 1.2,
        "stop_loss_pct": 0.005,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="momentum.vwap_trend.intraday.spy.v1",
        family="momentum",
        template="vwap_trend.intraday",
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
    """Build a BarSet from (time_et "HH:MM", open, high, low, close, volume) rows."""
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
