"""Tests for the queued-intents lifecycle store (queue-at-open, migration 016)."""

from __future__ import annotations

from milodex.core.event_store import EventStore, QueuedIntentEvent


def test_schema_version_is_16_after_construction(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.schema_version == 16


def test_queued_intents_table_exists(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert "queued_intents" in store.list_table_names()


def test_queued_intent_event_is_importable():
    assert QueuedIntentEvent.__name__ == "QueuedIntentEvent"
