"""Tests for AppController.quitRequested() clean-quit slot (Task 37 / PR-7c).

Verifies that quitRequested() calls stop() on every polling read model
held by the app.
"""

from __future__ import annotations

from unittest.mock import MagicMock


def test_quit_handler_stops_all_polling_read_models() -> None:
    """AppController.quitRequested() calls stop() on every read model passed in.

    Constructs AppController via _make_app_controller with a list of mock read
    models, then invokes quitRequested().  QGuiApplication.quit() is patched
    to a no-op (no Qt app running in unit test context).
    """
    from milodex.gui.app import _make_app_controller

    read_models = [MagicMock() for _ in range(12)]

    controller = _make_app_controller(read_models)

    # Patch QGuiApplication.quit and QThreadPool.globalInstance to avoid
    # needing a running Qt app.
    from PySide6.QtCore import QThreadPool
    from PySide6.QtGui import QGuiApplication

    import milodex.gui.app as _app_module  # noqa: F401 — not used directly

    # QGuiApplication.quit is a static method; patch at class level.
    original_quit = QGuiApplication.quit
    original_wait = QThreadPool.globalInstance().waitForDone
    quit_called = []

    try:
        QGuiApplication.quit = staticmethod(lambda: quit_called.append(True))  # type: ignore[assignment]
        # waitForDone is an instance method; replace on the global instance
        pool = QThreadPool.globalInstance()
        pool.waitForDone = lambda timeout=-1: None  # type: ignore[method-assign]

        controller.quitRequested()

    finally:
        QGuiApplication.quit = original_quit  # type: ignore[assignment]
        pool.waitForDone = original_wait  # type: ignore[method-assign]

    # Every read model must have stop() called
    for rm in read_models:
        rm.stop.assert_called_once()


def test_quit_handler_skips_none_entries() -> None:
    """AppController.quitRequested() skips None entries in the read model list."""
    from milodex.gui.app import _make_app_controller

    real_rm = MagicMock()
    read_models = [None, real_rm, None]

    controller = _make_app_controller(read_models)

    from PySide6.QtCore import QThreadPool
    from PySide6.QtGui import QGuiApplication

    original_quit = QGuiApplication.quit
    try:
        QGuiApplication.quit = staticmethod(lambda: None)  # type: ignore[assignment]
        pool = QThreadPool.globalInstance()
        original_wait = pool.waitForDone
        pool.waitForDone = lambda timeout=-1: None  # type: ignore[method-assign]

        controller.quitRequested()  # must not raise on None entries

    finally:
        QGuiApplication.quit = original_quit  # type: ignore[assignment]
        pool.waitForDone = original_wait  # type: ignore[method-assign]

    real_rm.stop.assert_called_once()


def test_quit_handler_stop_exception_does_not_prevent_quit() -> None:
    """If a read model's stop() raises, quitRequested() continues and still quits."""
    from milodex.gui.app import _make_app_controller

    bad_rm = MagicMock()
    bad_rm.stop.side_effect = RuntimeError("stop failed")
    good_rm = MagicMock()

    controller = _make_app_controller([bad_rm, good_rm])

    from PySide6.QtCore import QThreadPool
    from PySide6.QtGui import QGuiApplication

    quit_called = []
    original_quit = QGuiApplication.quit
    try:
        QGuiApplication.quit = staticmethod(lambda: quit_called.append(True))  # type: ignore[assignment]
        pool = QThreadPool.globalInstance()
        original_wait = pool.waitForDone
        pool.waitForDone = lambda timeout=-1: None  # type: ignore[method-assign]

        controller.quitRequested()  # must not raise

    finally:
        QGuiApplication.quit = original_quit  # type: ignore[assignment]
        pool.waitForDone = original_wait  # type: ignore[method-assign]

    good_rm.stop.assert_called_once()
