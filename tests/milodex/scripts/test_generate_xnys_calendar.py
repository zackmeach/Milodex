"""Tests for scripts/generate_xnys_calendar.py (#396).

Covers:
- compact() then the sessions-module expansion reproduces the sessions exactly.
- render() emits a valid module that round-trips, with the empty-collection branches too.
- The committed table is byte-for-byte what render() writes (no hand edits).
- Weekend sessions are refused; main() writes LF text from a stubbed Alpaca client.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from milodex.data import _xnys_calendar
from milodex.data.sessions import SessionCalendar, _expand_table

# Make the scripts/ directory importable regardless of CWD
# (mirrors test_counterfactual_gate_parity).
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import generate_xnys_calendar  # noqa: E402
from generate_xnys_calendar import compact, render  # noqa: E402

_REGULAR = ("09:30", "16:00")
_HALF = ("09:30", "13:00")


def _rows(start: date, end: date, closed=(), half=()):
    """Weekday sessions in [start, end], minus ``closed``, with ``half`` closing at 13:00."""
    rows = []
    for n in range((end - start).days + 1):
        day = start + timedelta(days=n)
        if day.weekday() < 5 and day not in closed:
            open_, close = _HALF if day in half else _REGULAR
            rows.append({"date": day.isoformat(), "open": open_, "close": close})
    return rows


def _payload(rows):
    return {
        "source": "alpaca_exchange_calendar",
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "timezone": "America/New_York",
        "window_start": rows[0]["date"],
        "window_end": rows[-1]["date"],
        "sessions": rows,
    }


_THANKSGIVING, _CHRISTMAS = date(2025, 11, 27), date(2025, 12, 25)
_HOLIDAY_ROWS = _rows(
    date(2025, 11, 20),  # a Thursday: the window starts mid-week
    date(2026, 1, 6),
    closed={_THANKSGIVING, _CHRISTMAS, date(2026, 1, 1)},
    half={date(2025, 11, 28), date(2025, 12, 24)},
)
_PLAIN_ROWS = _rows(date(2025, 3, 3), date(2025, 3, 7))  # one regular week: no irregular days


def test_compact_keeps_only_irregular_days():
    table = compact(_payload(_HOLIDAY_ROWS))
    assert (table.COVERAGE_START, table.COVERAGE_END) == ("2025-11-20", "2026-01-06")
    assert table.CLOSED_WEEKDAYS == ("2025-11-27", "2025-12-25", "2026-01-01")
    assert table.SPECIAL_SESSIONS == {
        "2025-11-28": _HALF,
        "2025-12-24": _HALF,
    }


@pytest.mark.parametrize("rows", [_HOLIDAY_ROWS, _PLAIN_ROWS], ids=["holidays", "plain-week"])
def test_compaction_round_trips_through_the_sessions_expansion(rows):
    payload = _payload(rows)
    expanded = _expand_table(compact(payload))
    assert expanded["sessions"] == rows
    assert SessionCalendar.from_rows(expanded).coverage == (
        date.fromisoformat(rows[0]["date"]),
        date.fromisoformat(rows[-1]["date"]),
    )


@pytest.mark.parametrize("rows", [_HOLIDAY_ROWS, _PLAIN_ROWS], ids=["holidays", "plain-week"])
def test_rendered_module_is_valid_python_and_lossless(rows):
    namespace: dict = {}
    exec(render(compact(_payload(rows))), namespace)
    assert "GENERATED" in namespace["__doc__"]
    assert _expand_table(SimpleNamespace(**namespace))["sessions"] == rows


def test_render_of_the_plain_week_uses_empty_literals():
    text = render(compact(_payload(_PLAIN_ROWS)))
    assert "CLOSED_WEEKDAYS: tuple[str, ...] = ()\n" in text
    assert "SPECIAL_SESSIONS: dict[str, tuple[str, str]] = {}\n" in text


def test_committed_table_is_exactly_what_the_generator_renders():
    names = (
        "SOURCE",
        "FETCHED_AT",
        "COVERAGE_START",
        "COVERAGE_END",
        "REGULAR_OPEN",
        "REGULAR_CLOSE",
        "CLOSED_WEEKDAYS",
        "SPECIAL_SESSIONS",
    )
    committed = SimpleNamespace(**{name: getattr(_xnys_calendar, name) for name in names})
    assert render(committed) == Path(_xnys_calendar.__file__).read_text(encoding="utf-8")


def test_compact_refuses_a_weekend_session():
    rows = _rows(date(2025, 3, 3), date(2025, 3, 7))
    rows.append({"date": "2025-03-08", "open": "09:30", "close": "16:00"})  # Saturday
    with pytest.raises(ValueError, match="weekend"):
        compact(_payload(rows))


def test_compact_refuses_unordered_or_empty_sessions():
    rows = _rows(date(2025, 3, 3), date(2025, 3, 7))
    with pytest.raises(ValueError, match="ordered"):
        compact(_payload([rows[1], rows[0], *rows[2:]]))
    with pytest.raises(ValueError, match="no sessions"):
        compact({**_payload(rows), "sessions": []})


def test_main_writes_lf_text_from_the_alpaca_payload(tmp_path, monkeypatch, capsys):
    calls = []

    class FakeBroker:
        def research_calendar(self, start, end, now):
            calls.append((start, end, now.tzinfo is not None))
            return _payload(_HOLIDAY_ROWS)

    monkeypatch.setattr("milodex.broker.alpaca_client.AlpacaBrokerClient", FakeBroker)
    out = tmp_path / "_xnys_calendar.py"
    assert (
        generate_xnys_calendar.main(
            ["--start", "2025-11-20", "--end", "2026-01-06", "--out", str(out)]
        )
        == 0
    )
    assert calls == [(date(2025, 11, 20), date(2026, 1, 6), True)]
    data = out.read_bytes()
    assert b"\r" not in data
    assert data.decode("utf-8") == render(compact(_payload(_HOLIDAY_ROWS)))
    assert "coverage 2025-11-20..2026-01-06" in capsys.readouterr().out
