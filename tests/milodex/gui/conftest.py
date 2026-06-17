"""Shared pytest fixtures for tests/milodex/gui.

The primary fixture is ``event_store_db``: a fully-migrated milodex.db backed
by the real EventStore migration chain.  Tests that previously hand-rolled
``CREATE TABLE`` statements inside each test file now use this fixture instead,
so the test schema always equals the production schema (all migrations applied).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Process-wide Qt application singleton (xdist isolation)
# ---------------------------------------------------------------------------
# Qt allows exactly one QCoreApplication-family object per process and does not
# tolerate mixing kinds. Some GUI test modules create a *bare* QCoreApplication
# (e.g. test_bench_command_bridge._process_qt_until) while the font/QML tests
# need a GUI-capable application with a font subsystem. Under pytest-xdist the
# tests scatter across worker processes, so a worker can run a
# QCoreApplication-creating test before a font/QML test — leaving
# QFontDatabase.addApplicationFont with no font subsystem (returns -1; or a
# native access-violation crash when a QApplication is then constructed over the
# bare QCoreApplication). Serial (-n0) already passes because an early GUI test
# establishes a QGuiApplication that everyone reuses; this flake is purely
# xdist worker-grouping.
#
# conftest.py is imported during collection, before any test module in this
# directory, so constructing the most-derived QApplication here — once per
# worker — makes it THE singleton that every `*.instance() or *Application(...)`
# call in the package reuses: GUI-capable for the font/QML tests, and reused
# (never a new bare core app) by the QCoreApplication call sites. Held in a
# module global so it is not garbage-collected mid-session.
try:
    from PySide6.QtWidgets import QApplication

    _GUI_TEST_APP = QApplication.instance() or QApplication([])
except ImportError:
    # PySide6 absent (headless CI without Qt): the Qt-dependent tests skip
    # themselves via their own importorskip / skipif guards.
    _GUI_TEST_APP = None


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
