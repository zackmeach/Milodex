"""Tests for the gap-continuation intraday strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest
from milodex.strategies.gap_continuation_intraday import GapContinuationIntradayStrategy

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_PRIOR_CLOSE = 500.0  # prior session's last regular bar close
_TODAY_OPEN = 502.0  # gap-up open (gap_pct ~0.004, above min_gap_pct 0.003)


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


def _two_session_bars(
    prior_date: str,
    prior_rows: list[tuple[str, float, float, float, float]],
    today_date: str,
    today_rows: list[tuple[str, float, float, float, float]],
) -> BarSet:
    """Combine two sessions into one BarSet (gap uses prior session close)."""
    prior = _session_bars(prior_date, prior_rows)
    today = _session_bars(today_date, today_rows)
    df = (
        pd.concat([prior.to_dataframe(), today.to_dataframe()], ignore_index=True)
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    return BarSet(df)


def _prior_session_bars() -> list[tuple[str, float, float, float, float]]:
    """A minimal prior session ending with close = _PRIOR_CLOSE (500.0)."""
    return [
        ("09:30", 498.0, 500.0, 497.5, 499.0),
        ("09:35", 499.0, 500.5, 498.5, 499.5),
        ("09:40", 499.5, 501.0, 499.0, 500.0),  # last bar = prior_close
        # A full-session stub — more bars keeps regular_session_bars happy
        ("10:00", 500.0, 501.0, 499.5, 500.0),
        ("15:55", 500.0, 500.5, 499.5, 500.0),  # final bar of prior session
    ]


def _today_gap_bars(
    open_price: float = _TODAY_OPEN,
    extra: list[tuple[str, float, float, float, float]] | None = None,
) -> list[tuple[str, float, float, float, float]]:
    """Minimal today session: 3 opening-range bars (15min) + optional extras."""
    # opening_range_minutes=15 → first 3 5-min bars form the range
    bars = [
        ("09:30", open_price, open_price + 0.5, open_price - 0.2, open_price + 0.3),
        ("09:35", open_price + 0.3, open_price + 0.8, open_price + 0.1, open_price + 0.5),
        ("09:40", open_price + 0.5, open_price + 1.0, open_price + 0.2, open_price + 0.7),
        # First post-range bar at 09:45 (offset 15 min)
    ]
    if extra:
        bars.extend(extra)
    return bars


def _context(
    *,
    positions: dict[str, float],
    equity: float,
    bars: BarSet,
    override_parameters: dict[str, Any] | None = None,
    entry_state: dict[str, Any] | None = None,
) -> StrategyContext:
    parameters: dict[str, Any] = {
        "opening_range_minutes": 15,
        "entry_window_minutes": 60,
        "min_gap_pct": 0.003,
        "stop_loss_pct": 0.01,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.10,
    }
    if override_parameters is not None:
        parameters.update(override_parameters)
    return StrategyContext(
        strategy_id="gap.gap_continuation.intraday.spy.v1",
        family="gap",
        template="gap_continuation.intraday",
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


def test_entry_buys_on_gap_continuation() -> None:
    """With a gap up and close > today_open (holding), BUY is emitted."""
    strategy = GapContinuationIntradayStrategy()
    bars = _two_session_bars(
        "2024-01-15",
        _prior_session_bars(),
        "2024-01-16",
        _today_gap_bars(
            open_price=502.0,
            extra=[
                # entry-window bar: offset 15 → in [15, 75), close > today_open 502
                ("09:45", 502.5, 503.0, 502.0, 502.8),
            ],
        ),
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.side == OrderSide.BUY
    assert intent.symbol == "SPY"
    assert decision.reasoning.rule == "gap.gap_continuation.entry"
    triggers = decision.reasoning.triggering_values
    assert "gap_pct" in triggers
    assert triggers["gap_pct"] > 0.003
    assert triggers["today_open"] == pytest.approx(502.0, rel=1e-6)


def test_no_entry_when_gap_too_small() -> None:
    """Gap below min_gap_pct produces no entry (gap not large enough)."""
    strategy = GapContinuationIntradayStrategy()
    tiny_gap_open = _PRIOR_CLOSE * 1.001  # 0.1% gap — below min 0.3%
    bars = _two_session_bars(
        "2024-01-15",
        _prior_session_bars(),
        "2024-01-16",
        _today_gap_bars(
            open_price=tiny_gap_open,
            extra=[
                (
                    "09:45",
                    tiny_gap_open + 0.1,
                    tiny_gap_open + 0.3,
                    tiny_gap_open,
                    tiny_gap_open + 0.2,
                )
            ],
        ),
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "gap" in decision.reasoning.narrative.lower()


def test_no_entry_when_gap_fills() -> None:
    """Even with a qualifying gap, if close <= today_open (gap filled/filling), no entry."""
    strategy = GapContinuationIntradayStrategy()
    bars = _two_session_bars(
        "2024-01-15",
        _prior_session_bars(),
        "2024-01-16",
        _today_gap_bars(
            open_price=502.0,
            extra=[
                # close < today_open → gap filling, not continuing
                ("09:45", 502.0, 502.2, 501.0, 501.5),
            ],
        ),
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    narrative = decision.reasoning.narrative.lower()
    assert "gap fill" in narrative or "gap" in narrative


def test_no_entry_outside_window() -> None:
    """A qualifying gap bar AFTER the entry window produces no entry."""
    strategy = GapContinuationIntradayStrategy()
    bars = _two_session_bars(
        "2024-01-15",
        _prior_session_bars(),
        "2024-01-16",
        _today_gap_bars(
            open_price=502.0,
            extra=[
                # 11:30 is offset 120 min, past [15, 75)
                ("11:30", 502.5, 503.0, 502.0, 502.8),
            ],
        ),
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "outside entry window" in decision.reasoning.narrative


def test_one_entry_per_session() -> None:
    """Two consecutive qualifying in-window bars with positions={}: second is no_signal."""
    strategy = GapContinuationIntradayStrategy()
    bars = _two_session_bars(
        "2024-01-15",
        _prior_session_bars(),
        "2024-01-16",
        _today_gap_bars(
            open_price=502.0,
            extra=[
                # First in-window bar: qualifying (close > today_open)
                ("09:45", 502.5, 503.0, 502.0, 502.8),
                # Second bar: also qualifying — but one-entry rule should block
                ("09:50", 502.8, 503.5, 502.5, 503.0),
            ],
        ),
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "one entry per session" in decision.reasoning.narrative


# ---------------------------------------------------------------------------
# Exit tests
# ---------------------------------------------------------------------------


def test_stop_loss_exits_position() -> None:
    """With open position, close <= entry_price * (1 - stop_loss_pct) triggers SELL."""
    strategy = GapContinuationIntradayStrategy()
    entry_price = 502.8
    stop_close = entry_price * (1 - 0.01) - 0.01  # just below stop

    bars = _two_session_bars(
        "2024-01-15",
        _prior_session_bars(),
        "2024-01-16",
        _today_gap_bars(
            open_price=502.0,
            extra=[
                ("09:45", entry_price, entry_price + 0.5, stop_close - 0.1, stop_close),
            ],
        ),
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
    assert decision.reasoning.rule == "gap.gap_continuation.stop_loss"


def test_gap_fill_invalidation_exits_position() -> None:
    """With open position, close < today_open triggers exit (gap-fill invalidation)."""
    strategy = GapContinuationIntradayStrategy()
    today_open = 502.0

    bars = _two_session_bars(
        "2024-01-15",
        _prior_session_bars(),
        "2024-01-16",
        _today_gap_bars(
            open_price=today_open,
            extra=[
                # close < today_open: gap filled → invalidation
                ("09:45", today_open, today_open + 0.2, today_open - 0.8, today_open - 0.5),
            ],
        ),
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": today_open + 0.3}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert "gap_continuation.invalidation" in decision.reasoning.rule


def test_time_stop_forces_exit() -> None:
    """At the time-stop bar (15:55 ET), open position is closed."""
    strategy = GapContinuationIntradayStrategy()
    today_open = 502.0

    bars = _two_session_bars(
        "2024-01-15",
        _prior_session_bars(),
        "2024-01-16",
        _today_gap_bars(
            open_price=today_open,
            extra=[
                ("15:55", today_open + 1.0, today_open + 1.5, today_open + 0.8, today_open + 1.2),
            ],
        ),
    )
    context = _context(
        positions={"SPY": 5.0},
        equity=100_000.0,
        bars=bars,
        entry_state={"SPY": {"entry_price": today_open + 0.5}},
    )

    decision = strategy.evaluate(bars, context)

    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert "time_stop" in decision.reasoning.rule


def test_no_prior_session_emits_no_signal() -> None:
    """If no prior session data exists, the strategy cannot compute prior_close → no_signal."""
    strategy = GapContinuationIntradayStrategy()
    # Only today's bars — no prior session
    bars = _session_bars(
        "2024-01-16",
        _today_gap_bars(
            open_price=502.0,
            extra=[("09:45", 502.5, 503.0, 502.0, 502.8)],
        ),
    )
    context = _context(positions={}, equity=100_000.0, bars=bars)

    decision = strategy.evaluate(bars, context)

    assert decision.intents == []
    assert "prior session" in decision.reasoning.narrative.lower()


# ---------------------------------------------------------------------------
# max_lookback_periods
# ---------------------------------------------------------------------------


def test_max_lookback_periods_is_156() -> None:
    """Gap strategy reads the prior session's close → needs 2 full sessions = 156."""
    # ponytail: 78 would only warm one session; prior_close would always be None
    assert GapContinuationIntradayStrategy().max_lookback_periods() == 156
