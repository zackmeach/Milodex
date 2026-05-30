"""Tests for the GUI periodic orphan-reaper controller."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

try:
    from PySide6.QtCore import QSettings  # noqa: F401
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed -- skipping Qt-aware OrphanReaperController tests",
)


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication so QObject + QTimer work."""
    if not _PYSIDE6_AVAILABLE:
        return None
    from PySide6.QtGui import QGuiApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


@_skip_no_qt
def test_interval_clamps_and_restarts_timer(qapp):
    from milodex.gui.orphan_reaper_controller import OrphanReaperController

    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    c.intervalSeconds = 1
    assert c.intervalSeconds == 5  # floor
    c.intervalSeconds = 99999
    assert c.intervalSeconds == 3600  # ceiling
    assert c._timer.interval() == 3600 * 1000


@_skip_no_qt
def test_clamp_boundaries_inclusive(qapp):
    from milodex.gui.orphan_reaper_controller import OrphanReaperController

    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=5)
    assert c.intervalSeconds == 5
    c.intervalSeconds = 3600
    assert c.intervalSeconds == 3600


@_skip_no_qt
def test_tick_invokes_reaper_and_emits_reaped(qapp, monkeypatch):
    from milodex.gui.orphan_reaper_controller import OrphanReaperController

    monkeypatch.setattr(
        "milodex.gui.orphan_reaper_controller.reconcile_orphaned_runs_on_bootstrap",
        lambda store, locks_dir, *, now: ["strat.x.v1"],
    )
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    received = []
    c.reaped.connect(lambda ids: received.append(ids))
    c._reap()
    assert received == [["strat.x.v1"]]


@_skip_no_qt
def test_reap_swallows_reaper_exception(qapp, monkeypatch):
    from milodex.gui.orphan_reaper_controller import OrphanReaperController

    def boom(store, locks_dir, *, now):
        raise RuntimeError("db gone")

    monkeypatch.setattr(
        "milodex.gui.orphan_reaper_controller.reconcile_orphaned_runs_on_bootstrap", boom
    )
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    c._reap()  # must not raise
