"""Tests for the intraday session-time helpers shared by ORB and benchmark."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from milodex.strategies._session_intraday import (
    MARKET_CLOSE_ET_FULL,
    MARKET_CLOSE_ET_HALF,
    MARKET_OPEN_ET,
    in_entry_window,
    in_opening_range,
    is_entry_signal_bar,
    is_half_day,
    is_session_start_bar,
    is_time_stop_bar,
    latest_session_date_et,
    opening_range_bars,
    session_bars_et,
    session_close_offset_minutes,
    session_date_et,
    session_vwap,
    session_vwap_series,
    to_eastern,
)


def test_market_open_constants() -> None:
    """Sanity: the open and close constants match the published US equities schedule."""
    assert MARKET_OPEN_ET.hour == 9 and MARKET_OPEN_ET.minute == 30
    assert MARKET_CLOSE_ET_FULL.hour == 16 and MARKET_CLOSE_ET_FULL.minute == 0
    assert MARKET_CLOSE_ET_HALF.hour == 13 and MARKET_CLOSE_ET_HALF.minute == 0


def test_to_eastern_handles_standard_time() -> None:
    """A January UTC timestamp converts with EST offset (UTC-5)."""
    # 2024-01-15 14:30 UTC == 2024-01-15 09:30 EST
    ts = datetime(2024, 1, 15, 14, 30, tzinfo=UTC)
    et = to_eastern(ts)
    assert et.year == 2024 and et.month == 1 and et.day == 15
    assert et.hour == 9 and et.minute == 30


def test_to_eastern_handles_daylight_time() -> None:
    """A July UTC timestamp converts with EDT offset (UTC-4)."""
    # 2024-07-15 13:30 UTC == 2024-07-15 09:30 EDT
    ts = datetime(2024, 7, 15, 13, 30, tzinfo=UTC)
    et = to_eastern(ts)
    assert et.year == 2024 and et.month == 7 and et.day == 15
    assert et.hour == 9 and et.minute == 30


def test_to_eastern_accepts_naive_timestamp_as_utc() -> None:
    """Naive timestamps are interpreted as UTC, not local."""
    et = to_eastern(datetime(2024, 1, 15, 14, 30))
    assert et.hour == 9 and et.minute == 30


def test_session_date_et_uses_eastern_date() -> None:
    """A 23:00 ET bar still belongs to its ET date even though UTC has rolled."""
    # 2024-01-16 02:00 UTC == 2024-01-15 21:00 EST
    ts = datetime(2024, 1, 16, 2, 0, tzinfo=UTC)
    assert session_date_et(ts) == date(2024, 1, 15)


def test_is_session_start_bar_true_at_9_30_et() -> None:
    """The 9:30 ET bar (= 14:30 UTC in winter) is the session start."""
    assert is_session_start_bar(datetime(2024, 1, 15, 14, 30, tzinfo=UTC))


def test_is_session_start_bar_false_at_9_35_et() -> None:
    assert not is_session_start_bar(datetime(2024, 1, 15, 14, 35, tzinfo=UTC))


def test_is_half_day_known_dates() -> None:
    """Known US half-days return True."""
    assert is_half_day(date(2024, 11, 29))  # Day after Thanksgiving
    assert is_half_day(date(2024, 12, 24))  # Christmas Eve
    assert is_half_day(date(2025, 7, 3))  # Day before July 4


def test_is_half_day_normal_dates() -> None:
    """Normal trading days return False."""
    assert not is_half_day(date(2024, 1, 15))
    assert not is_half_day(date(2024, 7, 15))


def test_in_opening_range_winter() -> None:
    """A 9:55 ET bar is inside a 30-min opening range; 10:00 ET is not."""
    # 9:55 EST = 14:55 UTC
    assert in_opening_range(datetime(2024, 1, 15, 14, 55, tzinfo=UTC), opening_range_minutes=30)
    # 10:00 EST = 15:00 UTC
    assert not in_opening_range(datetime(2024, 1, 15, 15, 0, tzinfo=UTC), opening_range_minutes=30)


def test_in_opening_range_summer() -> None:
    """DST: 9:55 EDT = 13:55 UTC; verify the helper handles summer correctly."""
    assert in_opening_range(datetime(2024, 7, 15, 13, 55, tzinfo=UTC), opening_range_minutes=30)
    assert not in_opening_range(datetime(2024, 7, 15, 14, 0, tzinfo=UTC), opening_range_minutes=30)


def test_in_entry_window_open_closed_semantics() -> None:
    """Entry window is [opening_range_end, opening_range_end + entry_window_minutes)."""
    # With (30, 60): window is [10:00 ET, 11:00 ET)
    # 10:00 ET in winter = 15:00 UTC
    assert in_entry_window(
        datetime(2024, 1, 15, 15, 0, tzinfo=UTC),
        opening_range_minutes=30,
        entry_window_minutes=60,
    )
    # 10:55 ET = 15:55 UTC — still inside
    assert in_entry_window(
        datetime(2024, 1, 15, 15, 55, tzinfo=UTC),
        opening_range_minutes=30,
        entry_window_minutes=60,
    )
    # 11:00 ET = 16:00 UTC — outside (window-end is exclusive)
    assert not in_entry_window(
        datetime(2024, 1, 15, 16, 0, tzinfo=UTC),
        opening_range_minutes=30,
        entry_window_minutes=60,
    )
    # 9:55 ET = 14:55 UTC — pre-window (still in opening range)
    assert not in_entry_window(
        datetime(2024, 1, 15, 14, 55, tzinfo=UTC),
        opening_range_minutes=30,
        entry_window_minutes=60,
    )


def test_is_entry_signal_bar_at_post_range_open() -> None:
    """Benchmark entry signal fires exactly at the first bar after the opening range."""
    # 10:00 ET = 15:00 UTC in winter
    assert is_entry_signal_bar(datetime(2024, 1, 15, 15, 0, tzinfo=UTC), opening_range_minutes=30)
    # 10:05 ET = 15:05 UTC — not a signal bar
    assert not is_entry_signal_bar(
        datetime(2024, 1, 15, 15, 5, tzinfo=UTC), opening_range_minutes=30
    )


def test_is_time_stop_bar_full_session() -> None:
    """Time-stop bar on a full session = 15:55 ET when minutes_before_close=5."""
    # 15:55 EST = 20:55 UTC
    assert is_time_stop_bar(datetime(2024, 1, 15, 20, 55, tzinfo=UTC), minutes_before_close=5)
    # 15:50 EST = 20:50 UTC — not yet
    assert not is_time_stop_bar(datetime(2024, 1, 15, 20, 50, tzinfo=UTC), minutes_before_close=5)
    # 16:00 EST = 21:00 UTC — too late
    assert not is_time_stop_bar(datetime(2024, 1, 15, 21, 0, tzinfo=UTC), minutes_before_close=5)


def test_is_time_stop_bar_half_day() -> None:
    """Time-stop bar on a half-day = 12:55 ET (close is 13:00, not 16:00)."""
    # 2024-11-29 is a known half-day. 12:55 EST = 17:55 UTC
    assert is_time_stop_bar(datetime(2024, 11, 29, 17, 55, tzinfo=UTC), minutes_before_close=5)
    # 15:55 EST on a half-day is NOT the time-stop bar (market already closed)
    assert not is_time_stop_bar(datetime(2024, 11, 29, 20, 55, tzinfo=UTC), minutes_before_close=5)


def test_latest_session_date_et_empty_df() -> None:
    df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])
    assert latest_session_date_et(df) is None


def test_latest_session_date_et_returns_et_date_of_last_bar() -> None:
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-15 14:30:00+00:00", "2024-01-15 15:00:00+00:00"]),
            "open": [500.0, 501.0],
            "high": [501.0, 502.0],
            "low": [499.0, 500.0],
            "close": [500.5, 501.5],
            "volume": [1_000_000, 1_100_000],
            "vwap": [500.25, 501.25],
        }
    )
    assert latest_session_date_et(df) == date(2024, 1, 15)


def test_session_bars_et_filters_to_one_day() -> None:
    """Multi-day DataFrame: session_bars_et returns only the requested ET date."""
    df = _build_intraday_df(
        [
            ("2024-01-15 14:30:00+00:00", 500.0),
            ("2024-01-15 14:35:00+00:00", 500.5),
            ("2024-01-15 14:40:00+00:00", 501.0),
            ("2024-01-16 14:30:00+00:00", 502.0),
            ("2024-01-16 14:35:00+00:00", 502.5),
        ]
    )
    out = session_bars_et(df, date(2024, 1, 15))
    assert len(out) == 3
    assert all(
        d == date(2024, 1, 15)
        for d in pd.DatetimeIndex(out["timestamp"]).tz_convert("America/New_York").date
    )


def test_opening_range_bars_returns_first_six_5min_bars() -> None:
    """With a 30-min opening range and 5min bars: returns exactly 6 rows."""
    rows = []
    # 12 bars covering 9:30 ET through 10:25 ET (i.e., the opening range + 6 more bars)
    for i in range(12):
        ts = pd.Timestamp("2024-01-15 14:30:00+00:00") + pd.Timedelta(minutes=5 * i)
        rows.append((ts.isoformat(), 500.0 + i * 0.1))
    df = _build_intraday_df(rows)
    out = opening_range_bars(df, date(2024, 1, 15), opening_range_minutes=30)
    assert len(out) == 6
    # The 6 bars should be timestamped 9:30, 9:35, 9:40, 9:45, 9:50, 9:55 ET
    expected_minutes = [30, 35, 40, 45, 50, 55]
    actual_minutes = [to_eastern(ts).minute for ts in out["timestamp"]]
    assert actual_minutes == expected_minutes


def test_opening_range_bars_empty_before_session() -> None:
    """If the session hasn't started yet, opening_range_bars returns empty."""
    df = _build_intraday_df(
        [
            ("2024-01-15 14:00:00+00:00", 500.0),  # 9:00 EST — pre-market
            ("2024-01-15 14:25:00+00:00", 500.5),  # 9:25 EST — pre-market
        ]
    )
    out = opening_range_bars(df, date(2024, 1, 15), opening_range_minutes=30)
    assert out.empty


def _build_intraday_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """Build a minimal intraday DataFrame from (utc_iso_timestamp, close) rows.

    ``high = close + 0.5`` and ``low = close - 0.5`` so the VWAP typical price
    ``(high + low + close) / 3`` equals ``close`` exactly — convenient for VWAP
    assertions.
    """
    timestamps = pd.to_datetime([r[0] for r in rows], utc=True)
    closes = [r[1] for r in rows]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(rows),
            "vwap": closes,
        }
    )


# ---------------------------------------------------------------------------
# Session VWAP helpers
# ---------------------------------------------------------------------------


def test_session_close_offset_minutes_full_and_half_day() -> None:
    """Full session spans 390 minutes (9:30->16:00); half-day 210 (9:30->13:00)."""
    assert session_close_offset_minutes(date(2024, 1, 15)) == 390
    assert session_close_offset_minutes(date(2024, 11, 29)) == 210  # known half-day


def test_session_vwap_single_bar_equals_typical_price() -> None:
    """With one bar, VWAP equals that bar's typical price (= close in this fixture)."""
    df = _build_intraday_df([("2024-01-15 14:30:00+00:00", 500.0)])
    assert session_vwap(df, date(2024, 1, 15)) == 500.0


def test_session_vwap_equal_volume_is_mean_of_typical_prices() -> None:
    """Equal volume across bars -> VWAP is the simple mean of typical prices."""
    df = _build_intraday_df(
        [
            ("2024-01-15 14:30:00+00:00", 500.0),
            ("2024-01-15 14:35:00+00:00", 502.0),
        ]
    )
    # typical prices 500 and 502, equal volume -> 501.0
    assert session_vwap(df, date(2024, 1, 15)) == 501.0


def test_session_vwap_is_volume_weighted() -> None:
    """Unequal volume pulls VWAP toward the heavier-volume bar's typical price."""
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2024-01-15 14:30:00+00:00", "2024-01-15 14:35:00+00:00"], utc=True
            ),
            "open": [500.0, 510.0],
            "high": [500.0, 510.0],
            "low": [500.0, 510.0],
            "close": [500.0, 510.0],
            "volume": [1_000_000.0, 3_000_000.0],  # 3x weight on the 510 bar
            "vwap": [500.0, 510.0],
        }
    )
    # (500*1 + 510*3) / 4 = 507.5
    assert session_vwap(df, date(2024, 1, 15)) == 507.5


def test_session_vwap_series_last_value_matches_session_vwap() -> None:
    """The final ``vwap_cum`` of the series equals the scalar ``session_vwap``."""
    rows = [
        ("2024-01-15 14:30:00+00:00", 500.0),
        ("2024-01-15 14:35:00+00:00", 502.0),
        ("2024-01-15 14:40:00+00:00", 498.0),
    ]
    df = _build_intraday_df(rows)
    series = session_vwap_series(df, date(2024, 1, 15))
    assert len(series) == 3
    assert "vwap_cum" in series.columns
    assert float(series["vwap_cum"].iloc[-1]) == session_vwap(df, date(2024, 1, 15))
    # First cumulative VWAP is just the first bar's typical price.
    assert float(series["vwap_cum"].iloc[0]) == 500.0


def test_session_vwap_excludes_pre_market_bars() -> None:
    """Pre-market bars (before 9:30 ET) do not contribute to the regular-session VWAP."""
    df = _build_intraday_df(
        [
            ("2024-01-15 14:00:00+00:00", 400.0),  # 9:00 EST pre-market — excluded
            ("2024-01-15 14:30:00+00:00", 500.0),  # 9:30 EST — included
        ]
    )
    # Only the 9:30 bar counts; the 9:00 pre-market 400 is excluded.
    assert session_vwap(df, date(2024, 1, 15)) == 500.0
    # With regular_session_only=False both bars count -> mean 450.
    assert session_vwap(df, date(2024, 1, 15), regular_session_only=False) == 450.0


def test_session_vwap_returns_none_for_absent_session() -> None:
    df = _build_intraday_df([("2024-01-15 14:30:00+00:00", 500.0)])
    assert session_vwap(df, date(2024, 1, 16)) is None


def test_session_vwap_returns_none_on_zero_volume() -> None:
    """Zero cumulative volume -> VWAP undefined -> None."""
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2024-01-15 14:30:00+00:00"], utc=True),
            "open": [500.0],
            "high": [500.0],
            "low": [500.0],
            "close": [500.0],
            "volume": [0.0],
            "vwap": [500.0],
        }
    )
    assert session_vwap(df, date(2024, 1, 15)) is None
