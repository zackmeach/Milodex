"""Tests for milodex.data.sessions: calendar, held-days, visibility, flatten deadline (#396).

Pure: literal calendars and a fixed ``now`` -- the only wall-clock read is the coverage
tripwire. Nothing consumes the module yet; the equivalence pins at the bottom compare the
shared helpers to the engine's current copies and go once the engine adopts them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.backtesting.intraday_simulation import _regular_session_mask
from milodex.data.sessions import (
    CalendarCoverageError,
    SessionCalendar,
    SessionPolicy,
    _expand_table,
    held_days,
    regular_hours_mask,
    warmup_calendar_days,
)

ET = ZoneInfo("America/New_York")

_PAYLOAD = {
    "source": "alpaca_exchange_calendar",
    "fetched_at": "2025-12-01T00:00:00+00:00",
    "timezone": "America/New_York",
    "window_start": "2025-01-01",
    "window_end": "2025-12-31",
    "sessions": [
        {"date": "2025-01-14", "open": "09:30", "close": "16:00"},  # Tue, EST
        {"date": "2025-07-03", "open": "09:30", "close": "13:00"},  # Thu, early close
        {"date": "2025-07-14", "open": "09:30", "close": "16:00"},  # Mon, EDT
        {"date": "2025-07-15", "open": "09:30", "close": "16:00"},  # Tue, EDT
        {"date": "2025-11-26", "open": "09:30", "close": "16:00"},  # Wed
        {"date": "2025-11-28", "open": "09:30", "close": "13:00"},  # Fri, Thu 11-27 closed
    ],
}
_CAL = SessionCalendar.from_rows(_PAYLOAD)
_SAME = SessionPolicy.for_tempo({"bar_size": "5Min", "position_lifecycle": "same_session"}, _CAL)
_MULTI = SessionPolicy.for_tempo({"bar_size": "5Min", "position_lifecycle": "multi_session"})
_DAILY = SessionPolicy.for_tempo({"bar_size": "1D"})


def _et(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=ET)


def _starts(*et_wall_clock: str) -> pd.DatetimeIndex:
    """Bar-start stamps given as ET wall clock, returned as a UTC DatetimeIndex."""
    return pd.DatetimeIndex(et_wall_clock).tz_localize("America/New_York").tz_convert("UTC")


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def _weekdays(start: date, end: date) -> list[date]:
    return [day for day in _days(start, end) if day.weekday() < 5]


# ---------------------------------------------------------------------------
# held_days
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fill_index", range(5), ids=["mon", "tue", "wed", "thu", "fri"])
def test_held_days_hits_max_hold_five_exactly_five_sessions_after_the_fill(fill_index):
    sessions = _weekdays(date(2025, 3, 3), date(2025, 3, 21))  # no holidays
    fill = sessions[fill_index]
    due = [d for d in sessions if held_days(sessions, fill, d) >= 5]
    assert due[0] == sessions[fill_index + 5]
    assert held_days(sessions, fill, sessions[fill_index + 4]) == 4


def test_held_days_is_not_the_calendar_day_difference():
    sessions = _weekdays(date(2025, 3, 3), date(2025, 3, 14))
    tue_fill, next_mon = date(2025, 3, 4), date(2025, 3, 10)
    assert (next_mon - tue_fill).days == 6  # the runner's old rule would call this due
    assert held_days(sessions, tue_fill, next_mon) == 4


def test_held_days_does_not_count_a_holiday_monday():
    sessions = [date(2025, 8, 28), date(2025, 8, 29), date(2025, 9, 2), date(2025, 9, 3)]
    assert held_days(sessions, date(2025, 8, 29), date(2025, 9, 2)) == 1  # Labor Day absent
    assert held_days(sessions, date(2025, 8, 29), date(2025, 9, 3)) == 2


def test_held_days_thanksgiving_week():
    sessions = [
        date(2025, 11, 24),
        date(2025, 11, 25),
        date(2025, 11, 26),
        date(2025, 11, 28),  # Thu 11-27 closed
        date(2025, 12, 1),
    ]
    wed_fill = date(2025, 11, 26)
    assert held_days(sessions, wed_fill, date(2025, 11, 27)) == 0  # closed day, nothing yet
    assert held_days(sessions, wed_fill, date(2025, 11, 28)) == 1
    assert held_days(sessions, wed_fill, date(2025, 12, 1)) == 2


def test_held_days_counts_crypto_weekends():
    every_day = [date(2025, 7, 14) + timedelta(days=n) for n in range(14)]
    fri_fill = date(2025, 7, 18)
    assert held_days(every_day, fri_fill, date(2025, 7, 19)) == 1  # Sat
    assert held_days(every_day, fri_fill, date(2025, 7, 20)) == 2  # Sun
    assert held_days(every_day, fri_fill, date(2025, 7, 21)) == 3  # Mon
    assert held_days(every_day, fri_fill, date(2025, 7, 25)) == 7


def test_held_days_counts_a_day_once_however_many_bars_it_has():
    sessions = _weekdays(date(2025, 3, 3), date(2025, 3, 14))
    bars = [d for d in sessions for _ in range(78)]  # 5Min bars: many per day
    assert held_days(bars, date(2025, 3, 4), date(2025, 3, 10)) == 4
    assert held_days(iter(bars), date(2025, 3, 4), date(2025, 3, 10)) == 4


def test_held_days_is_zero_when_as_of_does_not_follow_the_fill():
    sessions = _weekdays(date(2025, 3, 3), date(2025, 3, 14))
    fill = date(2025, 3, 5)
    assert held_days(sessions, fill, fill) == 0
    assert held_days(sessions, fill, date(2025, 3, 4)) == 0
    assert held_days([], fill, date(2025, 3, 10)) == 0


# ---------------------------------------------------------------------------
# SessionPolicy.for_tempo
# ---------------------------------------------------------------------------


def test_for_tempo_same_session_uses_given_calendar_else_xnys():
    assert _SAME.same_session
    assert _SAME.bar == timedelta(minutes=5)
    assert _SAME.calendar is _CAL
    tempo = {"bar_size": "15Min", "position_lifecycle": "same_session"}
    default = SessionPolicy.for_tempo(tempo)
    assert default.bar == timedelta(minutes=15)
    assert default.calendar is SessionCalendar.xnys()


def test_for_tempo_multi_session_and_daily_carry_no_calendar():
    assert not _MULTI.same_session
    assert _MULTI.calendar is None
    hourly = SessionPolicy.for_tempo({"bar_size": "1H"}, _CAL)  # lifecycle defaults to multi
    assert not hourly.same_session
    assert hourly.bar == timedelta(hours=1)
    assert hourly.calendar is None
    assert not _DAILY.same_session
    assert _DAILY.bar == timedelta(days=1)
    assert _DAILY.calendar is None


def test_for_tempo_daily_ignores_same_session_lifecycle():
    policy = SessionPolicy.for_tempo({"bar_size": "1D", "position_lifecycle": "same_session"}, _CAL)
    assert not policy.same_session
    assert policy.calendar is None


def test_for_tempo_rejects_unknown_bar_size():
    with pytest.raises(KeyError):
        SessionPolicy.for_tempo({"bar_size": "2H"})


def test_same_session_policy_requires_a_calendar():
    with pytest.raises(ValueError, match="calendar"):
        SessionPolicy(same_session=True, bar=timedelta(minutes=5))


# ---------------------------------------------------------------------------
# SessionPolicy.visible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("day", ["2025-07-15", "2025-01-14"], ids=["edt", "est"])
def test_visible_keeps_only_regular_hours_for_same_session(day):
    starts = _starts(f"{day} 08:20", f"{day} 09:25", f"{day} 09:30", f"{day} 15:55", f"{day} 16:00")
    assert _SAME.visible(starts).tolist() == [False, False, True, True, False]


def test_visible_multi_session_keeps_extended_hours():
    starts = _starts("2025-07-15 03:00", "2025-07-15 08:20", "2025-07-15 16:00", "2025-07-15 20:00")
    assert _MULTI.visible(starts).tolist() == [True] * 4


def test_visible_completion_cutoff_is_exactly_start_plus_bar():
    starts = _starts("2025-07-15 15:50")  # the 15:50 bar completes at 15:55:00
    assert _SAME.visible(starts, _et(2025, 7, 15, 15, 55, 0)).tolist() == [True]
    assert _SAME.visible(starts, _et(2025, 7, 15, 15, 54, 59)).tolist() == [False]


def test_visible_combines_completion_and_regular_hours():
    starts = _starts(
        "2025-07-15 08:20",
        "2025-07-15 09:30",
        "2025-07-15 15:50",
        "2025-07-15 15:55",
        "2025-07-15 16:00",
    )
    # At the bell: 15:55 has just completed; 16:00 has not; 08:20 is complete but pre-market.
    assert _SAME.visible(starts, _et(2025, 7, 15, 16, 0)).tolist() == [
        False,
        True,
        True,
        True,
        False,
    ]
    assert _MULTI.visible(starts, _et(2025, 7, 15, 16, 0)).tolist() == [
        True,
        True,
        True,
        True,
        False,
    ]


def test_visible_daily_bar_completes_one_day_after_its_stamp():
    starts = pd.DatetimeIndex(["2025-07-15T04:00:00Z"])  # 00:00 ET daily stamp
    assert _DAILY.visible(starts, datetime(2025, 7, 16, 4, 0, tzinfo=UTC)).tolist() == [True]
    assert _DAILY.visible(starts, datetime(2025, 7, 16, 3, 59, 59, tzinfo=UTC)).tolist() == [False]
    assert _DAILY.visible(starts).tolist() == [True]


# ---------------------------------------------------------------------------
# SessionPolicy.flatten_deadline / evaluation_open / overdue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "last_open", "first_closed"),
    [
        (  # EDT: 15:55 ET == 19:55Z
            date(2025, 7, 15),
            datetime(2025, 7, 15, 19, 54, 59, tzinfo=UTC),
            datetime(2025, 7, 15, 19, 55, 0, tzinfo=UTC),
        ),
        (  # EST: 15:55 ET == 20:55Z
            date(2025, 1, 14),
            datetime(2025, 1, 14, 20, 54, 59, tzinfo=UTC),
            datetime(2025, 1, 14, 20, 55, 0, tzinfo=UTC),
        ),
    ],
    ids=["edt", "est"],
)
def test_evaluation_closes_at_five_minutes_before_the_bell(day, last_open, first_closed):
    assert _SAME.flatten_deadline(day, None) == first_closed
    assert _SAME.evaluation_open(last_open, None)
    assert not _SAME.evaluation_open(first_closed, None)


def test_half_day_deadline_is_12_55_et():
    day = date(2025, 11, 28)
    assert _SAME.flatten_deadline(day, None) == _et(2025, 11, 28, 12, 55)
    assert _SAME.evaluation_open(_et(2025, 11, 28, 12, 54, 59), None)
    assert not _SAME.evaluation_open(_et(2025, 11, 28, 12, 55, 0), None)


def test_broker_next_close_earlier_than_the_table_wins():
    day = date(2025, 7, 15)
    unscheduled_early_close = _et(2025, 7, 15, 13, 0)
    assert _SAME.flatten_deadline(day, unscheduled_early_close) == _et(2025, 7, 15, 12, 55)
    assert _SAME.evaluation_open(_et(2025, 7, 15, 12, 54, 59), unscheduled_early_close)
    assert not _SAME.evaluation_open(_et(2025, 7, 15, 12, 55, 0), unscheduled_early_close)


def test_deadline_is_returned_in_utc():
    deadline = _SAME.flatten_deadline(date(2025, 7, 15), _et(2025, 7, 15, 13, 0))
    assert deadline.utcoffset() == timedelta(0)


def test_broker_next_close_on_a_later_day_does_not_move_todays_deadline():
    day = date(2025, 7, 15)
    tomorrows_close = _et(2025, 7, 16, 16, 0)
    assert _SAME.flatten_deadline(day, tomorrows_close) == _et(2025, 7, 15, 15, 55)
    assert _SAME.evaluation_open(_et(2025, 7, 15, 15, 54, 59), tomorrows_close)
    assert not _SAME.evaluation_open(_et(2025, 7, 15, 17, 0), tomorrows_close)


def test_flatten_lead_is_configurable():
    policy = SessionPolicy(
        same_session=True,
        bar=timedelta(minutes=5),
        calendar=_CAL,
        flatten_lead=timedelta(minutes=10),
    )
    assert policy.flatten_deadline(date(2025, 7, 15), None) == _et(2025, 7, 15, 15, 50)


@pytest.mark.parametrize(
    "day", [date(2025, 7, 4), date(2025, 7, 5), date(2025, 11, 27)], ids=["holiday", "sat", "thu"]
)
def test_closed_day_has_no_deadline_and_never_evaluates(day):
    assert _SAME.flatten_deadline(day, None) is None
    assert not _SAME.evaluation_open(datetime(day.year, day.month, day.day, 11, 0, tzinfo=ET), None)


def test_evaluation_waits_for_the_open():
    assert not _SAME.evaluation_open(_et(2025, 7, 15, 9, 29, 59), None)
    assert _SAME.evaluation_open(_et(2025, 7, 15, 9, 30, 0), None)


def test_evaluation_uses_the_et_date_of_now():
    # 00:30Z on 07-16 is 20:30 ET on 07-15: past the deadline, but no coverage error either.
    assert not _SAME.evaluation_open(datetime(2025, 7, 16, 0, 30, tzinfo=UTC), None)


@pytest.mark.parametrize("policy", [_MULTI, _DAILY], ids=["multi_session", "daily"])
def test_non_same_session_has_no_deadline_and_always_evaluates(policy):
    saturday_night = datetime(2025, 7, 19, 3, 0, tzinfo=UTC)
    assert policy.flatten_deadline(date(2025, 7, 15), _et(2025, 7, 15, 13, 0)) is None
    assert policy.evaluation_open(saturday_night, None)
    assert policy.evaluation_open(_et(2025, 7, 15, 16, 30), None)


def test_overdue_flags_a_lot_from_an_earlier_session():
    assert _SAME.overdue(date(2025, 7, 14), _et(2025, 7, 15, 9, 0))


def test_overdue_is_false_for_a_same_day_lot():
    assert not _SAME.overdue(date(2025, 7, 15), _et(2025, 7, 15, 15, 0))
    # 00:30Z on 07-16 is still 07-15 in New York.
    assert not _SAME.overdue(date(2025, 7, 15), datetime(2025, 7, 16, 0, 30, tzinfo=UTC))
    assert _SAME.overdue(date(2025, 7, 15), datetime(2025, 7, 16, 4, 30, tzinfo=UTC))


@pytest.mark.parametrize("policy", [_MULTI, _DAILY], ids=["multi_session", "daily"])
def test_overdue_is_never_true_unless_same_session(policy):
    assert not policy.overdue(date(2025, 7, 1), _et(2025, 7, 15, 9, 0))


def test_naive_now_is_rejected():
    naive = datetime(2025, 7, 15, 10, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        _SAME.evaluation_open(naive, None)
    with pytest.raises(ValueError, match="timezone-aware"):
        _SAME.overdue(date(2025, 7, 14), naive)


def test_evaluation_outside_calendar_coverage_raises():
    with pytest.raises(CalendarCoverageError):
        _SAME.evaluation_open(_et(2031, 1, 2, 10, 0), None)


# ---------------------------------------------------------------------------
# Crypto isolation
# ---------------------------------------------------------------------------


def test_non_same_session_policies_never_touch_xnys(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("SessionCalendar.xnys() touched")

    monkeypatch.setattr(SessionCalendar, "xnys", boom)
    with pytest.raises(AssertionError, match="xnys"):  # the tripwire is armed
        SessionPolicy.for_tempo({"bar_size": "5Min", "position_lifecycle": "same_session"})

    now = datetime(2025, 7, 19, 3, 0, tzinfo=UTC)
    starts = _starts("2025-07-19 02:00", "2025-07-19 03:00")
    for tempo in (
        {"bar_size": "5Min", "position_lifecycle": "multi_session"},
        {"bar_size": "15Min"},
        {"bar_size": "1D"},
        {"bar_size": "1D", "position_lifecycle": "same_session"},
    ):
        policy = SessionPolicy.for_tempo(tempo)
        assert policy.calendar is None
        assert policy.visible(starts, now).shape == (2,)
        assert policy.visible(starts).all()
        assert policy.flatten_deadline(date(2025, 7, 19), None) is None
        assert policy.evaluation_open(now, None)
        assert not policy.overdue(date(2025, 7, 1), now)


# ---------------------------------------------------------------------------
# SessionCalendar
# ---------------------------------------------------------------------------


def test_from_rows_builds_utc_sessions_and_closed_days():
    session = _CAL.session(date(2025, 7, 15))
    assert session.day == date(2025, 7, 15)
    assert session.open == datetime(2025, 7, 15, 13, 30, tzinfo=UTC)  # 09:30 EDT
    assert session.close == datetime(2025, 7, 15, 20, 0, tzinfo=UTC)
    assert _CAL.session(date(2025, 1, 14)).open == datetime(2025, 1, 14, 14, 30, tzinfo=UTC)  # EST
    assert _CAL.session(date(2025, 7, 4)) is None  # omitted weekday: closed
    assert _CAL.session(date(2025, 7, 5)) is None  # weekend
    assert _CAL.coverage == (date(2025, 1, 1), date(2025, 12, 31))
    assert _CAL.is_early_close(date(2025, 7, 3))
    assert not _CAL.is_early_close(date(2025, 7, 15))
    assert not _CAL.is_early_close(date(2025, 7, 4))  # closed day


def test_session_outside_coverage_raises():
    for day in (date(2024, 12, 31), date(2026, 1, 1)):
        with pytest.raises(CalendarCoverageError):
            _CAL.session(day)
        with pytest.raises(CalendarCoverageError):
            _CAL.is_early_close(day)
    assert _CAL.session(date(2025, 1, 1)) is None  # window edges are inside coverage
    assert _CAL.session(date(2025, 12, 31)) is None
    assert issubclass(CalendarCoverageError, LookupError)


def _payload(**changes):
    return {**_PAYLOAD, **changes}


def _session_rows(*rows):
    return [{"date": d, "open": o, "close": c} for d, o, c in rows]


@pytest.mark.parametrize(
    "payload",
    [
        _payload(timezone="UTC"),
        {k: v for k, v in _PAYLOAD.items() if k != "timezone"},
        {k: v for k, v in _PAYLOAD.items() if k != "sessions"},
        _payload(window_start="2025-13-01"),
        _payload(window_start="2026-01-01", window_end="2025-01-01"),
        _payload(window_start="2025-02-01"),  # first session precedes the window
        _payload(window_end="2025-11-27"),  # last session follows the window
        _payload(sessions=None),
        _payload(sessions=[{"date": "2025-07-15", "open": "09:30"}]),
        _payload(sessions=["2025-07-15"]),
        _payload(
            sessions=_session_rows(
                ("2025-07-15", "09:30", "16:00"), ("2025-07-14", "09:30", "16:00")
            )
        ),
        _payload(
            sessions=_session_rows(
                ("2025-07-15", "09:30", "16:00"), ("2025-07-15", "09:30", "16:00")
            )
        ),
        _payload(sessions=_session_rows(("2025-07-15", "9:30pm", "16:00"))),
        _payload(sessions=_session_rows(("2025-07-15", "09:30", "25:00"))),
        _payload(sessions=_session_rows(("2025-07-15", "", "16:00"))),
        _payload(sessions=[{"date": "2025-07-15", "open": 930, "close": "16:00"}]),
        _payload(sessions=_session_rows(("2025-07-15", "16:00", "09:30"))),
        None,
        [],
    ],
    ids=[
        "timezone",
        "no-timezone",
        "no-sessions",
        "bad-window-date",
        "reversed-window",
        "session-before-window",
        "session-after-window",
        "sessions-none",
        "row-missing-close",
        "row-not-a-mapping",
        "unordered",
        "duplicate",
        "bad-open",
        "bad-close",
        "empty-open",
        "int-open",
        "open-after-close",
        "payload-none",
        "payload-list",
    ],
)
def test_from_rows_rejects_malformed_payloads(payload):
    with pytest.raises(ValueError):
        SessionCalendar.from_rows(payload)


def test_expand_table_rejects_special_session_on_a_non_session_day():
    table = SimpleNamespace(
        SOURCE="s",
        FETCHED_AT="2025-01-01T00:00:00+00:00",
        COVERAGE_START="2025-01-06",
        COVERAGE_END="2025-01-10",
        REGULAR_OPEN="09:30",
        REGULAR_CLOSE="16:00",
        CLOSED_WEEKDAYS=("2025-01-08",),
        SPECIAL_SESSIONS={"2025-01-08": ("09:30", "13:00")},
    )
    with pytest.raises(ValueError, match="2025-01-08"):
        _expand_table(table)


# ---------------------------------------------------------------------------
# The committed XNYS table
# ---------------------------------------------------------------------------

# The hardcoded set in strategies/_session_intraday.py (to be deleted) ...
_LEGACY_HALF_DAYS = {
    date(2022, 11, 25),
    date(2023, 7, 3),
    date(2023, 11, 24),
    date(2024, 7, 3),
    date(2024, 11, 29),
    date(2024, 12, 24),
    date(2025, 7, 3),
    date(2025, 11, 28),
    date(2025, 12, 24),
    date(2026, 11, 27),
    date(2026, 12, 24),
}
# ... which misses these three early closes (#396, V6).
_MISSED_HALF_DAYS = {date(2020, 11, 27), date(2020, 12, 24), date(2021, 11, 26)}


def test_xnys_early_closes_2020_to_2026_are_the_legacy_set_plus_the_missed_three():
    assert len(_LEGACY_HALF_DAYS) == 11
    xnys = SessionCalendar.xnys()
    early = {d for d in _days(date(2020, 1, 1), date(2026, 12, 31)) if xnys.is_early_close(d)}
    assert early == _LEGACY_HALF_DAYS | _MISSED_HALF_DAYS


def test_xnys_spot_checks():
    xnys = SessionCalendar.xnys()
    assert xnys.session(date(2025, 12, 25)) is None
    assert xnys.is_early_close(date(2025, 12, 24))
    assert xnys.session(date(2026, 7, 3)) is None  # July 4th observed
    assert xnys.session(date(2025, 7, 5)) is None  # Saturday
    assert xnys.session(date(2025, 1, 9)) is None  # national day of mourning
    regular = xnys.session(date(2025, 7, 15))
    assert (regular.open, regular.close) == (
        datetime(2025, 7, 15, 13, 30, tzinfo=UTC),
        datetime(2025, 7, 15, 20, 0, tzinfo=UTC),
    )
    assert xnys.session(date(2025, 3, 7)).open == datetime(2025, 3, 7, 14, 30, tzinfo=UTC)  # EST
    assert xnys.session(date(2025, 3, 10)).open == datetime(2025, 3, 10, 13, 30, tzinfo=UTC)  # EDT
    assert xnys.session(date(2025, 11, 28)).close == datetime(2025, 11, 28, 18, 0, tzinfo=UTC)
    assert not xnys.is_early_close(date(2025, 7, 15))
    assert not xnys.is_early_close(date(2025, 12, 25))  # closed day


def test_xnys_raises_outside_its_coverage():
    xnys = SessionCalendar.xnys()
    start, end = xnys.coverage
    with pytest.raises(CalendarCoverageError):
        xnys.session(start - timedelta(days=1))
    with pytest.raises(CalendarCoverageError):
        xnys.session(end + timedelta(days=1))
    assert xnys.session(end) is not None  # coverage is first..last returned session


def test_xnys_is_cached():
    assert SessionCalendar.xnys() is SessionCalendar.xnys()


def test_xnys_coverage_reaches_a_year_ahead():
    end = SessionCalendar.xnys().coverage[1]
    assert end >= date.today() + timedelta(days=365), (
        f"XNYS table coverage ends {end}, under a year from today. Re-run "
        "scripts/generate_xnys_calendar.py and commit the regenerated "
        "src/milodex/data/_xnys_calendar.py."
    )


# ---------------------------------------------------------------------------
# Equivalence pins against the engine's current copies (delete when the engine adopts these)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [("2025-03-07", "2025-03-12"), ("2025-10-30", "2025-11-05")],
    ids=["spring-forward", "fall-back"],
)
def test_regular_hours_mask_matches_the_engines_mask_across_dst(start, end):
    index = pd.date_range(start, end, freq="5min", tz="UTC")  # includes extended hours
    mask = regular_hours_mask(index)
    assert mask.any()
    assert not mask.all()
    assert np.array_equal(mask, _regular_session_mask(index))


@pytest.mark.parametrize(
    ("declared", "parameters", "expected"),
    [
        (200, {"lookback": 5}, 310),  # declared wins: ceil(200 * 1.4) + 30
        (3, {}, 35),
        (0, {"flag": True, "n": 10}, 365),  # bools are not lookbacks
        (0, {"lookback": 200.0}, 600),  # whole floats count
        (0, {"multiplier": 2.5, "k": 7}, 365),  # fractional floats do not
        (0, {"seed": 20260619}, 3650),  # capped at ten years
        (0, {"name": "x", "n": None}, 365),  # no numeric params
        (0, {}, 365),
        (0, {"n": 500}, 1500),
        (0, {"n": 0, "m": -5}, 365),  # non-positive ignored
    ],
)
def test_warmup_calendar_days_matches_the_engine_resolver(declared, parameters, expected):
    engine_like = SimpleNamespace(
        _loaded=SimpleNamespace(
            strategy=SimpleNamespace(max_lookback_periods=lambda: declared),
            config=SimpleNamespace(parameters=parameters),
        )
    )
    assert BacktestEngine._warmup_calendar_days(engine_like) == expected
    assert warmup_calendar_days(declared, parameters) == expected
