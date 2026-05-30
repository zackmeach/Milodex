"""Tests for the durable reap-interval QSettings helper."""

from __future__ import annotations

import pytest

try:
    from PySide6.QtCore import QSettings  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed -- skipping QSettings tests",
)


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    monkeypatch.setattr("milodex.gui.runner_health_settings._FORMAT", QSettings.IniFormat)
    yield


@_skip_no_qt
def test_default_is_60_when_unset(tmp_settings):
    from milodex.gui.runner_health_settings import read_reap_interval_seconds

    assert read_reap_interval_seconds() == 60


@_skip_no_qt
def test_round_trips_value(tmp_settings):
    from milodex.gui.runner_health_settings import (
        read_reap_interval_seconds,
        write_reap_interval_seconds,
    )

    write_reap_interval_seconds(300)
    assert read_reap_interval_seconds() == 300


@_skip_no_qt
def test_non_int_stored_value_falls_back_to_default(tmp_settings):
    from PySide6.QtCore import QSettings

    from milodex.gui.runner_health_settings import (
        _APP,
        _KEY,
        _ORG,
        read_reap_interval_seconds,
    )

    QSettings(QSettings.IniFormat, QSettings.UserScope, _ORG, _APP).setValue(_KEY, "garbage")
    assert read_reap_interval_seconds() == 60
