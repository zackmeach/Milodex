"""Intraday backtest engine correctness tests.

See docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md §5.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def test_event_timeline_for_single_symbol_5min_session() -> None:
    """For a full 9:30-16:00 ET session of 5min SPY bars, the event timeline
    is the chronological union of fill events (every bar's start) and
    decision events (every bar's completion).
    """
    from milodex.backtesting.engine import _build_intraday_event_timeline

    per_symbol_ts_utc = _build_synthetic_5min_session_ts_only("2024-01-15", ["SPY"])

    timeline = _build_intraday_event_timeline(
        per_symbol_ts_utc=per_symbol_ts_utc,
        day=date(2024, 1, 15),
        bar_size_minutes=5,
    )

    # Expected: 79 unique events — first event is 9:30 (pure fill event for
    # the 9:30 bar; no decision event because that's the previous session's
    # close); subsequent events are unions; last event is 16:00 (pure decision
    # event for the 15:55 bar; no fill event because there's no 16:00 bar).
    timestamps = [t for t, _meta in timeline]
    assert len(timestamps) == 79
    # First event = 9:30 ET = 14:30 UTC
    assert timestamps[0] == pd.Timestamp("2024-01-15 14:30:00+00:00")
    # Last event = 16:00 ET = 21:00 UTC
    assert timestamps[-1] == pd.Timestamp("2024-01-15 21:00:00+00:00")

    # Spot-check the metadata: the 9:30 bar fills (no decision yet); the
    # 14:35 UTC timestamp is BOTH the decision_time of the 9:30 bar AND the
    # fill event of the 9:35 bar.
    first_ts, first_meta = timeline[0]
    assert first_meta["fill_symbols"] == ["SPY"]
    assert first_meta["decision_symbols"] == []

    second_ts, second_meta = timeline[1]
    assert second_ts == pd.Timestamp("2024-01-15 14:35:00+00:00")
    assert second_meta["fill_symbols"] == ["SPY"]
    assert second_meta["decision_symbols"] == ["SPY"]

    # Last event (16:00 ET = 21:00 UTC): pure decision event
    last_ts, last_meta = timeline[-1]
    assert last_meta["fill_symbols"] == []
    assert last_meta["decision_symbols"] == ["SPY"]


def _build_synthetic_5min_session_ts_only(
    date_str: str,
    symbols: list[str],
) -> dict[str, pd.DatetimeIndex]:
    """Build a full 9:30-16:00 ET session of 5min UTC timestamps for each symbol.

    Returns the precomputed per-symbol UTC timestamp index that the Phase D
    helpers expect (no OHLC, no DataFrames).
    """
    open_et = pd.Timestamp(f"{date_str} 09:30:00").tz_localize("America/New_York")
    open_utc = open_et.tz_convert("UTC")
    ts_list = [open_utc + pd.Timedelta(minutes=5 * i) for i in range(78)]
    ts_index = pd.DatetimeIndex(ts_list)
    return {symbol: ts_index for symbol in symbols}
