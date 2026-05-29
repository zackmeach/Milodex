"""Tests for the RSI(2) intraday mean-reversion strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.meanrev_rsi2_intraday import (
    MeanrevRsi2IntradayStrategy,
    _wilder_rsi_series,
)


def test_wilder_rsi_series_matches_hand_computation() -> None:
    """RSI(2) over a rising-then-falling close path matches the recursive Wilder
    computation (seed = mean of first 2 deltas)."""
    closes = pd.Series([500.0, 501.0, 502.0, 503.0, 504.0, 505.0, 504.0, 502.0, 500.0])
    rsi = _wilder_rsi_series(closes, 2)
    # First two entries undefined; index 2..5 are pure gains -> RSI 100.
    assert pd.isna(rsi.iloc[0]) and pd.isna(rsi.iloc[1])
    assert rsi.iloc[5] == 100.0
    # Final bar is deeply oversold after three down moves.
    assert rsi.iloc[-1] < 10.0


def test_entry_buys_when_oversold() -> None:
    strategy = MeanrevRsi2IntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        [
            ("09:30", 500.0),
            ("09:35", 501.0),
            ("09:40", 502.0),
            ("09:45", 503.0),
            ("09:50", 504.0),
            ("09:55", 505.0),
            ("10:00", 504.0),
            ("10:05", 502.0),
            ("10:10", 500.0),  # latest: RSI(2) ~7 -> oversold
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.BUY
    assert decision.intents[0].order_type == OrderType.MARKET
    assert decision.reasoning.rule == "meanrev.rsi2.entry"


def test_no_entry_when_not_oversold() -> None:
    strategy = MeanrevRsi2IntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        [
            ("09:30", 500.0),
            ("09:35", 501.0),
            ("09:40", 502.0),
            ("09:45", 503.0),
            ("09:50", 504.0),
            ("09:55", 505.0),
            ("10:00", 506.0),  # RSI(2) high -> not oversold
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "not oversold" in decision.reasoning.narrative


def test_no_entry_outside_window() -> None:
    strategy = MeanrevRsi2IntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        [
            ("09:30", 500.0),
            ("09:35", 501.0),
            ("09:40", 502.0),
            ("09:45", 503.0),
            ("09:50", 504.0),
            ("09:55", 505.0),
            ("15:30", 500.0),  # oversold-ish but past [10:00, 15:00) window
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "outside entry window" in decision.reasoning.narrative


def test_stop_loss_exit_below_entry_price() -> None:
    strategy = MeanrevRsi2IntradayStrategy()
    # entry 506, stop at 503.47; latest close 503 <= 503.47.
    bars = _session_bars(
        "2024-01-15",
        [
            ("09:30", 506.0),
            ("09:35", 505.0),
            ("09:40", 504.0),
            ("10:00", 503.0),
        ],
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
    assert decision.reasoning.rule == "meanrev.rsi2.stop_loss"


def test_rsi_exit_on_reversion() -> None:
    """RSI reverting above the exit threshold takes profit."""
    strategy = MeanrevRsi2IntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        [
            ("09:30", 500.0),
            ("09:35", 501.0),
            ("09:40", 502.0),
            ("09:45", 503.0),
            ("09:50", 504.0),
            ("10:00", 505.0),  # RSI(2) ~100 -> above exit threshold
        ],
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
    assert decision.reasoning.rule == "meanrev.rsi2.rsi_exit"


def test_time_stop_forces_exit() -> None:
    strategy = MeanrevRsi2IntradayStrategy()
    # RSI ~50 (neither stop nor reverted), above entry stop -> only time-stop.
    bars = _session_bars(
        "2024-01-15",
        [
            ("09:30", 500.0),
            ("09:35", 501.0),
            ("09:40", 502.0),
            ("09:45", 503.0),
            ("09:50", 504.0),
            ("09:55", 505.0),
            ("15:55", 504.0),  # RSI(2) ~50
        ],
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
    assert decision.reasoning.rule == "meanrev.rsi2.time_stop"


def test_one_entry_per_session_blocks_re_entry() -> None:
    strategy = MeanrevRsi2IntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        [
            ("09:30", 500.0),
            ("09:35", 501.0),
            ("09:40", 502.0),
            ("09:45", 503.0),
            ("09:50", 504.0),
            ("09:55", 505.0),
            ("10:00", 504.0),
            ("10:05", 502.0),
            ("10:10", 500.0),  # prior oversold entry signal (in window)
            ("10:15", 498.0),  # would-be re-entry, still oversold
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "one entry per session" in decision.reasoning.narrative


def test_half_day_session_skipped() -> None:
    strategy = MeanrevRsi2IntradayStrategy()
    bars = _session_bars(
        "2024-11-29",
        [
            ("09:30", 505.0),
            ("09:35", 504.0),
            ("09:40", 503.0),
            ("10:00", 500.0),
        ],
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "half-day" in decision.reasoning.narrative


def test_rsi_undefined_yields_no_signal() -> None:
    """Fewer than rsi_lookback+1 session closes -> RSI undefined -> no signal."""
    strategy = MeanrevRsi2IntradayStrategy()
    bars = _session_bars(
        "2024-01-15",
        [("10:00", 500.0), ("10:05", 499.0)],  # only 2 closes, lookback 2
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "RSI undefined" in decision.reasoning.narrative


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
        "rsi_lookback": 2,
        "rsi_entry_threshold": 10.0,
        "rsi_exit_threshold": 60.0,
        "stop_loss_pct": 0.005,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)

    return StrategyContext(
        strategy_id="meanrev.rsi2.intraday.spy.v1",
        family="meanrev",
        template="rsi2.intraday",
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


def _session_bars(date_et: str, rows: list[tuple[str, float]]) -> BarSet:
    """Build a BarSet from (time_et "HH:MM", close) rows. high=close+0.5, low=close-0.5."""
    records: list[dict[str, Any]] = []
    for time_str, c in rows:
        et_ts = pd.Timestamp(f"{date_et} {time_str}:00").tz_localize("America/New_York")
        utc_ts = et_ts.tz_convert("UTC")
        records.append(
            {
                "timestamp": utc_ts,
                "open": float(c),
                "high": float(c) + 0.5,
                "low": float(c) - 0.5,
                "close": float(c),
                "volume": 1_000_000.0,
                "vwap": float(c),
            }
        )
    return BarSet(pd.DataFrame(records))
