"""Direct unit tests for the kill-switch state store.

The mutation audit (``docs/TEST_EFFICACY_AUDIT.md`` §B.4) flagged that
``execution/state.py`` has no dedicated test file — coverage comes only
through service-level tests in ``test_service.py``. The tests here pin
the load-bearing event-type strings that the rest of the system reads
from the durable log.

Important #5 from §C: the activation event-type string is asserted in
``test_service.py`` (``event_type == "activated"``), but the symmetric
``"reset"`` string is not. A mutation flipping the literal would silently
break the kill-switch event-log contract.
"""

from __future__ import annotations

from milodex.core.event_store import EventStore
from milodex.execution.state import KillSwitchStateStore


def test_reset_writes_event_type_literal_reset(tmp_path):
    """Kills mutation: state.py:62 ``event_type="reset"``
    -> any other literal (e.g. ``"XXresetXX"``).

    The kill-switch event log distinguishes activations from resets via
    the ``event_type`` column. Downstream readers
    (``KillSwitchStateStore.get_state``, the trade kill-switch CLI,
    governance reporting) all rely on the literal ``"reset"`` string.
    A silent rename would make every reset look like a non-reset and
    leave the kill switch effectively stuck.
    """
    event_store = EventStore(tmp_path / "milodex.db")
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )

    # Activate first, then reset — only resets after activations are
    # observable from the durable log standpoint.
    store.activate("operator triggered")
    store.reset()

    events = event_store.list_kill_switch_events()
    assert [event.event_type for event in events] == ["activated", "reset"]
    # Pin the literal reset string against any rename.
    assert events[-1].event_type == "reset"
    # Reset events carry no reason (per state.py:66).
    assert events[-1].reason is None


def test_get_state_treats_reset_event_as_inactive(tmp_path):
    """Pin the read-side contract that pairs with the write-side string.

    ``get_state()`` returns ``active=False`` only when the latest event
    is None or its ``event_type == "reset"``. A mutation flipping the
    string-equality check (state.py:40) would either trap the kill
    switch in 'active' after a reset or release it spuriously after an
    unrelated event.
    """
    event_store = EventStore(tmp_path / "milodex.db")
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )

    store.activate("operator triggered")
    assert store.get_state().active is True

    store.reset()
    state = store.get_state()
    assert state.active is False
    assert state.reason is None


def test_activate_writes_event_type_literal_activated(tmp_path):
    """Symmetric pin for the activation literal at state.py:52.

    The audit notes the activation string is already locked
    symmetrically via ``test_service.py``; this duplicates that pin
    in the dedicated state-store test so the kill rate of the
    state-store-only mutation suite reflects it.
    """
    event_store = EventStore(tmp_path / "milodex.db")
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )

    store.activate("daily loss threshold")

    events = event_store.list_kill_switch_events()
    assert len(events) == 1
    assert events[0].event_type == "activated"
    assert events[0].reason == "daily loss threshold"
