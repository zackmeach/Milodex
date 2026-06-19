"""Shared session-time utilities for intraday US equity strategies.

These helpers express "is this bar at 9:30 ET", "is this bar in the entry
window", "is this date a half-day session", etc. — concepts every intraday
strategy needs but the daily-strategy harness does not.

Half-day handling:
    The US equities half-day calendar is encoded as a hardcoded frozenset
    covering the 2022-2025 backtest window. This is intentionally a stopgap
    until a more dynamic calendar dep (e.g. ``pandas-market-calendars``) is
    justified by additional intraday strategies. Extending the window means
    adding entries here. See ``docs/STRATEGY_BANK.md`` for the maintenance
    note.

Time zone convention:
    All bar timestamps coming from Alpaca are tz-aware UTC. Conversion to
    ``America/New_York`` handles both EST (UTC-5) and EDT (UTC-4) — DST
    transitions are pandas' problem, not ours. Helpers accept UTC-aware
    ``pd.Timestamp`` or ``datetime`` and convert internally.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

ET_TZ = "America/New_York"

#: Regular session open in Eastern Time.
MARKET_OPEN_ET = time(9, 30)

#: Regular session close in Eastern Time.
MARKET_CLOSE_ET_FULL = time(16, 0)

#: Half-day session close in Eastern Time (early close at 1pm).
MARKET_CLOSE_ET_HALF = time(13, 0)

#: US equities early-close (13:00 ET) days for the 2022-2026 backtest window.
#: Sourced from NYSE's published holiday calendar. Half-days are: day after
#: Thanksgiving, day before July 4 when 7/4 is Wed/Thu/Fri, Christmas Eve when
#: it falls on a weekday.
US_MARKET_HALF_DAYS: frozenset[date] = frozenset(
    {
        date(2022, 11, 25),  # Day after Thanksgiving
        date(2023, 7, 3),  # Day before July 4
        date(2023, 11, 24),  # Day after Thanksgiving
        date(2024, 7, 3),  # Day before July 4
        date(2024, 11, 29),  # Day after Thanksgiving
        date(2024, 12, 24),  # Christmas Eve (Tue)
        date(2025, 7, 3),  # Day before July 4
        date(2025, 11, 28),  # Day after Thanksgiving
        date(2025, 12, 24),  # Christmas Eve (Wed)
        date(2026, 11, 27),  # Day after Thanksgiving
        date(2026, 12, 24),  # Christmas Eve (Thu)
    }
)


def to_eastern(ts: datetime | pd.Timestamp) -> pd.Timestamp:
    """Convert a UTC (or tz-naive, assumed UTC) timestamp to America/New_York.

    DST transitions are handled by pandas.
    """
    pts = pd.Timestamp(ts)
    if pts.tz is None:
        pts = pts.tz_localize("UTC")
    return pts.tz_convert(ET_TZ)


def is_half_day(d: date) -> bool:
    """Return True if ``d`` is a known US equities early-close day."""
    return d in US_MARKET_HALF_DAYS


def session_date_et(ts: datetime | pd.Timestamp) -> date:
    """Return the ET-local date of ``ts``."""
    return to_eastern(ts).date()


def is_session_start_bar(ts: datetime | pd.Timestamp) -> bool:
    """Return True if ``ts`` is exactly 9:30 ET (the first regular-session bar)."""
    return to_eastern(ts).time() == MARKET_OPEN_ET


def in_opening_range(ts: datetime | pd.Timestamp, opening_range_minutes: int) -> bool:
    """Return True if ``ts`` falls within ``[9:30, 9:30 + opening_range_minutes)`` ET.

    Uses start-of-bar timestamping (Alpaca convention) — a bar timestamped
    9:55 ET with ``opening_range_minutes=30`` falls *inside* the opening
    range (covers 9:55-10:00); a bar timestamped 10:00 ET falls *outside*
    (covers 10:00-10:05).
    """
    et = to_eastern(ts)
    return _et_time_offset_minutes(et) < opening_range_minutes


def in_entry_window(
    ts: datetime | pd.Timestamp,
    opening_range_minutes: int,
    entry_window_minutes: int,
) -> bool:
    """Return True if ``ts`` is in the entry window post-opening-range.

    The window is ``[9:30 + opening_range_minutes, 9:30 + opening_range_minutes +
    entry_window_minutes)`` ET. With defaults (30, 60), that's [10:00, 11:00) ET.
    """
    et = to_eastern(ts)
    offset = _et_time_offset_minutes(et)
    return opening_range_minutes <= offset < opening_range_minutes + entry_window_minutes


def is_entry_signal_bar(
    ts: datetime | pd.Timestamp,
    opening_range_minutes: int,
) -> bool:
    """Return True if ``ts`` is exactly the first bar after the opening range.

    With ``opening_range_minutes=30``, this is the bar timestamped 10:00 ET.
    Used by the unconditional-intraday-long benchmark.
    """
    et = to_eastern(ts)
    return _et_time_offset_minutes(et) == opening_range_minutes


def is_time_stop_bar(
    ts: datetime | pd.Timestamp,
    minutes_before_close: int,
) -> bool:
    """Return True if ``ts`` is the forced-exit bar (close - minutes_before_close).

    Adapts to half-days: on a half-day the close is 13:00 ET, so with
    ``minutes_before_close=5`` the time-stop bar is 12:55 ET, not 15:55 ET.
    """
    et = to_eastern(ts)
    d = et.date()
    close_t = MARKET_CLOSE_ET_HALF if is_half_day(d) else MARKET_CLOSE_ET_FULL
    close_dt = datetime.combine(d, close_t)
    target = (close_dt - timedelta(minutes=minutes_before_close)).time()
    return et.time() == target


def latest_session_date_et(df: pd.DataFrame) -> date | None:
    """Return the ET-local date of the most recent timestamp in ``df``, or None if empty."""
    if df.empty:
        return None
    return session_date_et(df["timestamp"].iloc[-1])


def session_bars_et(df: pd.DataFrame, session_date: date) -> pd.DataFrame:
    """Return the rows of ``df`` whose ET date matches ``session_date``.

    Sorted by ascending timestamp, with the index reset.
    """
    if df.empty:
        return df.iloc[:0]
    et = pd.DatetimeIndex(df["timestamp"]).tz_convert(ET_TZ)
    mask = pd.Series(et.date == session_date, index=df.index)
    return df.loc[mask].sort_values("timestamp").reset_index(drop=True)


def opening_range_bars(
    df: pd.DataFrame,
    session_date: date,
    opening_range_minutes: int,
) -> pd.DataFrame:
    """Return the bars of ``df`` that fall within the opening range on ``session_date``.

    With ``opening_range_minutes=30`` and 5min bars, returns up to 6 rows.
    Empty if the session hasn't reached the opening range yet.
    """
    session = session_bars_et(df, session_date)
    if session.empty:
        return session
    et = pd.DatetimeIndex(session["timestamp"]).tz_convert(ET_TZ)
    offsets = pd.Series(
        [_et_time_offset_minutes(t) for t in et],
        index=session.index,
    )
    return session.loc[(offsets >= 0) & (offsets < opening_range_minutes)].reset_index(drop=True)


def entry_window_bars(
    df: pd.DataFrame,
    session_date: date,
    opening_range_minutes: int,
    entry_window_minutes: int,
) -> pd.DataFrame:
    """Return the bars of ``df`` that fall within the entry window on ``session_date``.

    The window is ``[opening_range_minutes, opening_range_minutes +
    entry_window_minutes)`` minutes after 9:30 ET. With defaults (30, 60),
    that's [10:00, 11:00) ET — 12 5min bars or 4 15min bars.
    """
    session = session_bars_et(df, session_date)
    if session.empty:
        return session
    et = pd.DatetimeIndex(session["timestamp"]).tz_convert(ET_TZ)
    offsets = pd.Series(
        [_et_time_offset_minutes(t) for t in et],
        index=session.index,
    )
    upper = opening_range_minutes + entry_window_minutes
    return session.loc[(offsets >= opening_range_minutes) & (offsets < upper)].reset_index(
        drop=True
    )


def session_close_offset_minutes(session_date: date) -> int:
    """Return minutes from 9:30 ET to the session close (adapts to half-days).

    Full session: 390 (9:30 -> 16:00). Half-day: 210 (9:30 -> 13:00).
    """
    close_t = MARKET_CLOSE_ET_HALF if is_half_day(session_date) else MARKET_CLOSE_ET_FULL
    return (close_t.hour - MARKET_OPEN_ET.hour) * 60 + (close_t.minute - MARKET_OPEN_ET.minute)


def regular_session_bars(df: pd.DataFrame, session_date: date) -> pd.DataFrame:
    """Return ``session_date``'s regular cash-session bars, sorted ascending.

    Restricts to bars whose offset from 9:30 ET is in ``[0, session_close_offset)``
    — i.e. 9:30 ET through the last bar before the close (15:55 ET on a full
    session, 12:55 ET on a half-day) — excluding any pre-market / after-hours
    bars present in the cache. Index is reset; empty DataFrame when the session
    has no regular-session bars.
    """
    session = session_bars_et(df, session_date)
    if session.empty:
        return session.iloc[:0]
    et = pd.DatetimeIndex(session["timestamp"]).tz_convert(ET_TZ)
    offsets = pd.Series([_et_time_offset_minutes(t) for t in et], index=session.index)
    close_offset = session_close_offset_minutes(session_date)
    return (
        session.loc[(offsets >= 0) & (offsets < close_offset)]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def session_vwap_series(
    df: pd.DataFrame,
    session_date: date,
    *,
    regular_session_only: bool = True,
) -> pd.DataFrame:
    """Return ``session_date``'s bars with an added cumulative-session-VWAP column.

    The returned DataFrame is the session's bars sorted ascending by timestamp
    (index reset), with one extra column ``vwap_cum`` holding the cumulative
    session VWAP *through and including* each bar:

        vwap_cum[i] = sum_{k<=i}(typical_k * volume_k) / sum_{k<=i}(volume_k)

    where ``typical = (high + low + close) / 3`` (the textbook VWAP input —
    deliberately not the per-bar ``vwap`` column, so the value is well-defined
    even when that column is absent or unreliable).

    Because the input ``df`` is the cursor-truncated view the backtest engine
    passes to ``evaluate`` (bars only up to the current decision bar), the
    cumulative VWAP is computed over completed bars only — no look-ahead.

    ``regular_session_only=True`` (default) restricts accumulation to regular
    cash-session bars (via :func:`regular_session_bars`), excluding any
    pre-market / after-hours bars present in the cache. Bars with non-positive
    cumulative volume yield ``NaN`` in ``vwap_cum`` (VWAP undefined). Returns an
    empty DataFrame when the session has no qualifying bars.
    """
    if regular_session_only:
        session = regular_session_bars(df, session_date)
    else:
        session = session_bars_et(df, session_date).sort_values("timestamp").reset_index(drop=True)
    if session.empty:
        return session.iloc[:0]
    typical = (
        session["high"].astype(float)
        + session["low"].astype(float)
        + session["close"].astype(float)
    ) / 3.0
    volume = session["volume"].astype(float).clip(lower=0.0)
    cum_pv = (typical * volume).cumsum()
    cum_v = volume.cumsum()
    session = session.copy()
    session["vwap_cum"] = cum_pv.where(cum_v > 0) / cum_v.where(cum_v > 0)
    return session


def session_vwap(
    df: pd.DataFrame,
    session_date: date,
    *,
    regular_session_only: bool = True,
) -> float | None:
    """Return the cumulative session VWAP through the latest bar of ``session_date``.

    Thin wrapper over :func:`session_vwap_series` returning the final
    ``vwap_cum`` value. ``None`` when the session has no qualifying bars or
    cumulative volume is zero throughout (VWAP undefined).
    """
    series = session_vwap_series(df, session_date, regular_session_only=regular_session_only)
    if series.empty:
        return None
    last = series["vwap_cum"].iloc[-1]
    if pd.isna(last):
        return None
    return float(last)


def _et_time_offset_minutes(et_ts: pd.Timestamp | datetime) -> int:
    """Return minutes from 9:30 ET to ``et_ts`` (negative if pre-open)."""
    t = et_ts.time()
    return (t.hour - MARKET_OPEN_ET.hour) * 60 + (t.minute - MARKET_OPEN_ET.minute)
