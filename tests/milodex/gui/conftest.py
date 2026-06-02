"""Shared pytest fixtures for tests/milodex/gui.

The primary fixture is ``event_store_db``: a fully-migrated milodex.db backed
by the real EventStore migration chain.  Tests that previously hand-rolled
``CREATE TABLE`` statements inside each test file now use this fixture instead,
so the test schema always equals the production schema (all migrations applied).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def event_store_db(tmp_path: Path) -> Path:
    """Return a Path to a milodex.db with the REAL (fully-migrated) schema.

    Constructs an EventStore against ``tmp_path / "milodex.db"``, which applies
    all migrations (001..N) via ``EventStore._apply_migrations``, then returns
    the db Path.  Tests seed rows via raw ``INSERT`` statements or the
    EventStore API; the schema is guaranteed to match production.

    Scope: function (each test gets a fresh, empty db).
    """
    from milodex.core.event_store import EventStore

    db = tmp_path / "milodex.db"
    EventStore(db)  # applies all migrations
    return db
