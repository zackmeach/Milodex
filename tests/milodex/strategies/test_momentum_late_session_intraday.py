"""Tests for the late-session momentum intraday strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd
from milodex.strategies.momentum_late_session_intraday import (
    MomentumLateSessionIntradayStrategy,
)

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
#
# Parameters: opening_range_minutes=300 (entry window starts at 14:30 ET),
#             entry_window_minutes=60  (window = [14:30, 15:30) ET),
#             momentum_lookback_bars=6, stop_loss_pct=0.01,
#             exit_minutes_before_close=5, per_position_notional_pct=0.10.
#
# 300min after 9:30 ET = 14:30 ET. Entry window [14:30, 15:30) ET.
# The strategy reads closes from regular_session_bars to check momentum:
#   latest_close > close[-(lookback+1)] over the session bars.


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


def _climbing_session(
    date_et: str = "2024-01-15",
    extra: list[tuple[str, float, float, float, float]] | None = None,
) -> BarSet:
    """A session where price climbs through the day.

    Provides enough bars so the lookback (6) is satisfiable at 14:30 ET.
    Closes climb from 500 at 9:30 to ~510 by mid-afternoon.
    """
    rows: list[tuple[str, float, float, float, float]] = [
        ("09:30", 500.0, 500.5, 499.5, 500.0),
        ("09:35", 500.5, 501.0, 500.0, 500.5),
        ("09:40", 501.0, 501.5, 500.5, 501.0),
        ("09:45", 501.5, 502.0, 501.0, 501.5),
        ("09:50", 502.0, 502.5, 501.5, 502.0),
        ("09:55", 502.5, 503.0, 502.0, 502.5),
        ("10:00", 503.0, 503.5, 502.5, 503.0),
        ("14:00", 505.0, 505.5, 504.5, 505.0),
        ("14:05", 505.5, 506.0, 505.0, 505.5),
        ("14:10", 506.0, 506.5, 505.5, 506.0),
        ("14:15", 506.5, 507.0, 506.0, 506.5),
        ("14:20", 507.0, 507.5, 506.5, 507.0),
        ("14:25", 507.5, 508.0, 507.0, 507.5),
        # 14:30 ET = offset 300 min → first in-window bar
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
        "opening_range_minutes": 300,
        "entry_window_minutes": 60,
        "momentum_lookback_bars": 6,
        "stop_loss_pct": 0.01,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)
    return StrategyContext(
        strategy_id="momentum.late_session.intraday.spy.v1",
        family="momentum",
        template="late_session.intraday",
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


def test_entry_buys_on_late_momentum() -> None:
    """In-window bar with close > close 6 bars ago → BUY."""
    strategy = MomentumLateSessionIntradayStrategy()
    # At 14:30, the close (508.0) > close 6 bars ago (14:00 bar = 505.0).
    bars = _climbing_session(
        extra=[("14:30", 508.0, 508.5, 507.5, 508.0)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side == OrderSide.BUY
    assert intent.symbol == "SPY"
    assert decision.reasoning.rule == "momentum.late_session.entry"
    triggers = decision.reasoning.triggering_values
    assert "latest_close" in triggers
    assert "prior_close" in triggers
    assert triggers["latest_close"] > triggers["prior_close"]


def test_no_entry_when_no_positive_momentum() -> None:
    """Close <= close 6 bars ago (no momentum) → no entry."""
    strategy = MomentumLateSessionIntradayStrategy()
    # Session where price is flat/declining into 14:30.
    rows: list[tuple[str, float, float, float, float]] = [
        ("09:30", 510.0, 510.5, 509.5, 510.0),
        ("09:35", 509.5, 510.0, 509.0, 509.5),
        ("09:40", 509.0, 509.5, 508.5, 509.0),
        ("09:45", 508.5, 509.0, 508.0, 508.5),
        ("09:50", 508.0, 508.5, 507.5, 508.0),
        ("09:55", 507.5, 508.0, 507.0, 507.5),
        ("10:00", 507.0, 507.5, 506.5, 507.0),
        ("14:00", 507.0, 507.5, 506.5, 507.0),
        ("14:05", 507.0, 507.5, 506.5, 507.0),
        ("14:10", 507.0, 507.5, 506.5, 507.0),
        ("14:15", 507.0, 507.5, 506.5, 507.0),
        ("14:20", 507.0, 507.5, 506.5, 507.0),
        ("14:25", 507.0, 507.5, 506.5, 507.0),
        # 14:30: close == close 6 bars ago (507.0) → no positive momentum
        ("14:30", 507.0, 507.5, 506.5, 507.0),
    ]
    bars = _session_bars("2024-01-15", rows)
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "momentum" in decision.reasoning.narrative.lower()


def test_no_entry_outside_window() -> None:
    """A bar with positive momentum but BEFORE the late entry window → no entry."""
    strategy = MomentumLateSessionIntradayStrategy()
    # 10:30 ET = offset 60 min, window starts at 300 min (14:30 ET)
    bars = _climbing_session(
        extra=[("10:30", 506.0, 506.5, 505.5, 506.0)],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "outside entry window" in decision.reasoning.narrative


def test_one_entry_per_session() -> None:
    """Two qualifying in-window bars with positions={}: second emits no_signal."""
    strategy = MomentumLateSessionIntradayStrategy()
    bars = _climbing_session(
        extra=[
            ("14:30", 508.0, 508.5, 507.5, 508.0),  # 1st qualifying bar
            ("14:35", 508.5, 509.0, 508.0, 508.5),  # 2nd qualifying bar
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "one entry per session" in decision.reasoning.narrative


# ---------------------------------------------------------------------------
# Exit tests
# ---------------------------------------------------------------------------


def test_stop_loss_exits_position() -> None:
    """With open position, close <= entry_price * (1 - stop_loss_pct) → SELL."""
    strategy = MomentumLateSessionIntradayStrategy()
    entry_price = 508.0
    stop_close = entry_price * (1 - 0.01) - 0.01

    bars = _climbing_session(
        extra=[("14:30", entry_price, entry_price + 0.5, stop_close - 0.1, stop_close)],
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
    assert decision.reasoning.rule == "momentum.late_session.stop_loss"


def test_time_stop_forces_exit() -> None:
    """At 15:55 ET (time-stop bar), open position is closed."""
    strategy = MomentumLateSessionIntradayStrategy()
    bars = _climbing_session(
        extra=[("15:55", 509.0, 509.5, 508.5, 509.0)],
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": 508.0}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert "time_stop" in decision.reasoning.rule


# ---------------------------------------------------------------------------
# max_lookback_periods
# ---------------------------------------------------------------------------


def test_max_lookback_periods_is_78() -> None:
    """Late-session momentum is session-reset; one RTH session (78 bars) suffices."""
    # ponytail: no cross-session reads; 390min / 5min = 78
    assert MomentumLateSessionIntradayStrategy().max_lookback_periods() == 78
