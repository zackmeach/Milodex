"""Tests for KillSwitchStateStore._migrate_legacy_state_if_needed.

The migration function (state.py ~line 70-90) reads a legacy JSON file and,
on the FIRST call only, writes an 'activated' event to the event store when
the JSON shows active=true.  Two invariants are locked here:

1. Idempotency guard — if the event store already has events, migration is a
   NO-OP.  A regression inverting this guard would re-migrate on every call,
   potentially appending duplicate activation events on a system that has been
   running correctly under the new event-store regime.

2. Inactive-file guard — a legacy JSON with ``"active": false`` must NOT
   migrate as an activation.  A stuck-off switch must stay off, and an
   inactive legacy file must not spuriously activate the kill switch.

Meaningfulness is verified for each test via the docstring's mutation
description (invert the respective guard, confirm the test fails, revert).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from milodex.core.event_store import EventStore, KillSwitchEvent
from milodex.execution.state import KillSwitchStateStore

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write_legacy_json(path: Path, *, active: bool, reason: str | None = None) -> None:
    """Write a legacy kill-switch JSON file in the format migration expects."""
    data: dict = {"active": active}
    if reason is not None:
        data["reason"] = reason
    if active:
        data["last_triggered_at"] = datetime.now(tz=UTC).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: NO-OP when event store already has kill-switch events
# ---------------------------------------------------------------------------


def test_migration_is_noop_when_event_store_has_events(tmp_path):
    """Migration must not re-migrate when the event store already has events.

    Scenario: system has been running under the new event-store regime — there
    is at least one 'activated' event in the store. A stale legacy JSON with
    active=true also exists (e.g., not yet cleaned up). Calling get_state()
    (which internally calls _migrate_legacy_state_if_needed) must not append a
    duplicate activation event.

    Meaningfulness check (mutate-then-revert):
        In state.py, inverting the guard at line ~73:
            ``if self._event_store.get_latest_kill_switch_event() is not None:``
        to
            ``if self._event_store.get_latest_kill_switch_event() is None:``
        causes migration to run even when events exist, which appends a second
        'activated' event. This test catches that because the assertion
        ``len(events) == 1`` would fail with ``len(events) == 2``.
    """
    legacy_path = tmp_path / "kill_switch.json"
    _write_legacy_json(legacy_path, active=True, reason="legacy reason")

    event_store = EventStore(tmp_path / "milodex.db")
    # Seed one 'activated' event directly — simulates the system having already
    # migrated or having been activated under the new regime.
    event_store.append_kill_switch_event(
        KillSwitchEvent(
            event_type="activated",
            recorded_at=datetime.now(tz=UTC),
            reason="already migrated",
        )
    )

    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=legacy_path,
    )

    # Trigger migration path via get_state (called N times to prove idempotency).
    state = store.get_state()
    state2 = store.get_state()

    assert state.active is True
    assert state2.active is True

    events = event_store.list_kill_switch_events()
    # Must still be exactly 1 event — migration must not have re-fired.
    assert len(events) == 1, (
        f"Expected 1 event (pre-seeded only); got {len(events)}. "
        "Migration ran despite the event store already having events."
    )
    assert events[0].reason == "already migrated"


# ---------------------------------------------------------------------------
# Test 2: Inactive legacy file must NOT activate the kill switch
# ---------------------------------------------------------------------------


def test_migration_inactive_legacy_file_does_not_activate(tmp_path):
    """A legacy JSON with active=false must not migrate as an activation.

    Scenario: the operator explicitly turned the kill switch off before the
    migration to the event-store regime. The legacy file records active=false.
    After migration, the kill switch must remain inactive.

    Meaningfulness check (mutate-then-revert):
        In state.py, removing or inverting the ``if not bool(data.get("active", False)): return``
        guard at line ~79 causes migration to append an 'activated' event even
        for inactive legacy files. This test catches that because:
            - ``state.active`` would be True instead of False
            - ``len(events)`` would be 1 instead of 0
    """
    legacy_path = tmp_path / "kill_switch.json"
    _write_legacy_json(legacy_path, active=False)

    event_store = EventStore(tmp_path / "milodex.db")
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=legacy_path,
    )

    # Calling get_state triggers _migrate_legacy_state_if_needed.
    state = store.get_state()

    assert state.active is False, (
        "Legacy inactive file should not activate the kill switch after migration."
    )

    events = event_store.list_kill_switch_events()
    assert len(events) == 0, (
        f"Expected 0 events; got {len(events)}. "
        "Migration wrote an activation event for an inactive legacy file."
    )
