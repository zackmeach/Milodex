"""CLI error-ladder coverage for an unreadable SQLite event store (F3 finding).

A corrupt or locked ``data/milodex.db`` used to surface as the generic
``Unexpected error (DatabaseError): ...`` / ``(OperationalError): database is
locked`` (code ``unexpected_error``). ``cli.main`` now catches
``sqlite3.DatabaseError`` before the catch-all and emits an actionable,
operator-facing message (mirrors the docs/TROUBLESHOOTING.md "SQLite event store
is corrupt or locked" entry). Both cases still fail closed (exit 1).
"""

from __future__ import annotations

import json
import sqlite3
from io import StringIO
from pathlib import Path

from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import EventStore


def _refuse():
    raise AssertionError("broker / data provider must not be needed for an event-store read")


def _error_payload(err: StringIO, out: StringIO) -> dict:
    """The --json error object is rendered to stderr (falls back to stdout)."""
    raw = (err.getvalue() or out.getvalue()).strip()
    return json.loads(raw)["errors"][0]


def test_corrupt_event_store_file_gives_actionable_message(tmp_path: Path):
    # A real garbage file: EventStore() genuinely raises sqlite3.DatabaseError
    # ("file is not a database") on open (PRAGMA journal_mode=WAL in __init__),
    # exercising the whole path file -> EventStore -> ladder, not a stub.
    (tmp_path / "milodex.db").write_bytes(b"this is not a sqlite database -- pure garbage\n")
    out, err = StringIO(), StringIO()

    code = cli_entrypoint(
        ["analytics", "list", "--json"],
        event_store_factory=lambda: EventStore(tmp_path / "milodex.db"),
        broker_factory=_refuse,
        data_provider_factory=_refuse,
        stdout=out,
        stderr=err,
    )

    assert code == 1
    error = _error_payload(err, out)
    assert error["code"] == "event_store_corrupt"
    message = error["message"].lower()
    assert "corrupt" in message or "unreadable" in message
    assert "restore" in message  # tells the operator the fix
    assert "troubleshooting.md" in message
    # the generic catch-all did NOT fire
    assert "unexpected_error" not in (err.getvalue() + out.getvalue())


def test_locked_event_store_gives_actionable_message(tmp_path: Path):
    # "database is locked" is sqlite3.OperationalError (a DatabaseError subclass).
    # Injected via the factory: a real lock needs a concurrent writer + the 30s
    # busy_timeout, which is impractical (and flaky) in a unit test.
    def _locked() -> EventStore:
        raise sqlite3.OperationalError("database is locked")

    out, err = StringIO(), StringIO()
    code = cli_entrypoint(
        ["analytics", "list", "--json"],
        event_store_factory=_locked,
        broker_factory=_refuse,
        data_provider_factory=_refuse,
        stdout=out,
        stderr=err,
    )

    assert code == 1
    error = _error_payload(err, out)
    assert error["code"] == "event_store_locked"
    message = error["message"].lower()
    assert "locked" in message
    assert "writing" in message
    assert "unexpected_error" not in (err.getvalue() + out.getvalue())
