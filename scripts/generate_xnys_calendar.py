"""Regenerate src/milodex/data/_xnys_calendar.py from Alpaca's exchange calendar (#396).

Read-only: one GET of the calendar (credentials from .env). Coverage is the first..last
session Alpaca actually returned -- days it did not return are never claimed. Review the
diff, then commit; a CI test fails once coverage reaches less than a year ahead.

    python scripts/generate_xnys_calendar.py [--start 2000-01-01] [--end 2029-12-31] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from milodex.data.sessions import SessionCalendar, _expand_table

REGULAR_OPEN = "09:30"
REGULAR_CLOSE = "16:00"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = _REPO_ROOT / "src" / "milodex" / "data" / "_xnys_calendar.py"

_HEADER = '''"""NYSE regular-session calendar table. GENERATED - DO NOT EDIT.

Written by scripts/generate_xnys_calendar.py from Alpaca's exchange calendar; regenerate
with that script and review the diff. Read only by milodex.data.sessions, which expands it:
every weekday in [COVERAGE_START, COVERAGE_END] is a regular session unless listed in
CLOSED_WEEKDAYS, and SPECIAL_SESSIONS overrides the regular open/close. Weekends are always
closed. Times are America/New_York wall clock.
"""
'''


def compact(payload: Mapping[str, Any]) -> SimpleNamespace:
    """Reduce a ``research_calendar()`` payload to its irregular days.

    The result carries the ``_xnys_calendar`` constants as attributes, so
    ``milodex.data.sessions._expand_table`` can expand it back losslessly.
    """
    sessions = payload["sessions"]
    days = [date.fromisoformat(item["date"]) for item in sessions]
    if not days:
        raise ValueError("calendar has no sessions")
    if days != sorted(set(days)):
        raise ValueError("calendar sessions must be ordered and unique")
    weekend = [day.isoformat() for day in days if day.weekday() >= 5]
    if weekend:
        raise ValueError(f"calendar has sessions on a weekend: {weekend}")
    first, last = days[0], days[-1]
    open_days = set(days)
    every_day = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return SimpleNamespace(
        SOURCE=payload["source"],
        FETCHED_AT=payload["fetched_at"],
        COVERAGE_START=first.isoformat(),
        COVERAGE_END=last.isoformat(),
        REGULAR_OPEN=REGULAR_OPEN,
        REGULAR_CLOSE=REGULAR_CLOSE,
        CLOSED_WEEKDAYS=tuple(
            day.isoformat() for day in every_day if day.weekday() < 5 and day not in open_days
        ),
        SPECIAL_SESSIONS={
            item["date"]: (item["open"], item["close"])
            for item in sessions
            if (item["open"], item["close"]) != (REGULAR_OPEN, REGULAR_CLOSE)
        },
    )


def render(table: SimpleNamespace) -> str:
    """Module text for ``table``; one entry per line so ``ruff format`` leaves it alone."""
    q = json.dumps
    closed = "".join(f"    {q(day)},\n" for day in table.CLOSED_WEEKDAYS)
    special = "".join(
        f"    {q(day)}: ({q(open_)}, {q(close)}),\n"
        for day, (open_, close) in table.SPECIAL_SESSIONS.items()
    )
    closed_literal = "(\n" + closed + ")" if closed else "()"
    special_literal = "{\n" + special + "}" if special else "{}"
    return (
        f"{_HEADER}\n"
        f"SOURCE = {q(table.SOURCE)}\n"
        f"FETCHED_AT = {q(table.FETCHED_AT)}\n"
        f"COVERAGE_START = {q(table.COVERAGE_START)}\n"
        f"COVERAGE_END = {q(table.COVERAGE_END)}\n"
        f"REGULAR_OPEN = {q(table.REGULAR_OPEN)}\n"
        f"REGULAR_CLOSE = {q(table.REGULAR_CLOSE)}\n"
        "\n"
        f"CLOSED_WEEKDAYS: tuple[str, ...] = {closed_literal}\n"
        "\n"
        f"SPECIAL_SESSIONS: dict[str, tuple[str, str]] = {special_literal}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", type=date.fromisoformat, default=date(2000, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2029, 12, 31))
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args(argv)

    # Imported here so compact/render stay importable (and testable) without alpaca-py.
    from milodex.broker.alpaca_client import AlpacaBrokerClient

    payload = AlpacaBrokerClient().research_calendar(args.start, args.end, now=datetime.now(UTC))
    SessionCalendar.from_rows(payload)  # validates timezone / ordering / window
    table = compact(payload)
    if _expand_table(table)["sessions"] != payload["sessions"]:
        raise SystemExit("compaction did not round-trip Alpaca's sessions; nothing written")
    args.out.write_text(render(table), encoding="utf-8", newline="\n")
    print(
        f"wrote {args.out}: coverage {table.COVERAGE_START}..{table.COVERAGE_END}, "
        f"{len(payload['sessions'])} sessions, {len(table.CLOSED_WEEKDAYS)} closed weekdays, "
        f"{len(table.SPECIAL_SESSIONS)} special sessions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
