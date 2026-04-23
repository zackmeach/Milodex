"""Shared test helpers for promotion/frozen-manifest plumbing.

See Phase E of the Phase 1.4 slice-1 plan. Used by any test that constructs
an :class:`ExecutionService` around a paper+ strategy — the drift check now
requires a matching frozen manifest in the event store.
"""

from __future__ import annotations

from pathlib import Path

from milodex.core.event_store import EventStore
from milodex.promotion import freeze_manifest


def seed_frozen_manifest(
    event_store: EventStore,
    config_path: Path,
) -> str:
    """Freeze ``config_path`` into ``event_store`` at its current stage.

    Returns the canonical config hash for the frozen row so tests can assert
    on it. The stage is read from the YAML (AD-8); there is no ``stage`` kwarg.
    """
    event = freeze_manifest(config_path, event_store=event_store)
    return event.config_hash
