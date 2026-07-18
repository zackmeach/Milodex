"""Tests for milodex.gui.theme_manager — Python-side ThemeManager.

Verifies the durable-state semantics of the theme persistence file
(``data/gui_settings.json``):

- default theme is ``editorial-dark``
- :meth:`ThemeManager.set_theme` mutates state, persists to disk, and
  emits ``themeChanged``
- corrupt or missing settings files fall back to the default with a
  ``WARNING`` log
- unknown theme names are rejected with a ``WARNING`` and no state
  change

All Qt-touching tests skip automatically when PySide6 is not
importable, mirroring ``test_fonts.py``.
"""

from __future__ import annotations

import json
import logging

import pytest

# ---------------------------------------------------------------------------
# PySide6 availability guard
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QCoreApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping Qt theme-manager tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QCoreApplication for QObject/Signal machinery."""
    if not _PYSIDE6_AVAILABLE:
        return None

    import sys

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv[:1])
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_default_theme_is_editorial_dark(qapp, tmp_path):
    """A fresh ThemeManager (no settings file) defaults to editorial-dark."""
    from milodex.gui.theme_manager import ThemeManager

    settings = tmp_path / "gui_settings.json"
    manager = ThemeManager(settings_path=settings)

    assert manager.theme == "editorial-dark"


@_skip_no_qt
def test_set_theme_changes_value(qapp, tmp_path):
    """set_theme('bronze') updates the active theme."""
    from milodex.gui.theme_manager import ThemeManager

    manager = ThemeManager(settings_path=tmp_path / "gui_settings.json")
    assert manager.set_theme("bronze") is True

    assert manager.theme == "bronze"


@_skip_no_qt
def test_set_theme_persists_to_disk(qapp, tmp_path):
    """After set_theme, the settings file holds the version-1 schema."""
    from milodex.gui.theme_manager import ThemeManager

    settings = tmp_path / "gui_settings.json"
    manager = ThemeManager(settings_path=settings)
    manager.set_theme("editorial-light")

    assert settings.exists()
    payload = json.loads(settings.read_text(encoding="utf-8"))
    assert payload == {"theme": "editorial-light", "version": 1}


@_skip_no_qt
def test_loads_persisted_theme(qapp, tmp_path):
    """A pre-existing settings file is honored at construction time."""
    from milodex.gui.theme_manager import ThemeManager

    settings = tmp_path / "gui_settings.json"
    settings.write_text(
        json.dumps({"theme": "bronze", "version": 1}),
        encoding="utf-8",
    )

    manager = ThemeManager(settings_path=settings)
    assert manager.theme == "bronze"


@_skip_no_qt
def test_set_theme_emits_signal(qapp, tmp_path):
    """themeChanged fires with the new theme name as its argument."""
    from milodex.gui.theme_manager import ThemeManager

    manager = ThemeManager(settings_path=tmp_path / "gui_settings.json")

    received: list[str] = []
    manager.themeChanged.connect(received.append)

    manager.set_theme("bronze")

    assert received == ["bronze"]


@_skip_no_qt
def test_set_theme_does_not_emit_when_value_unchanged(qapp, tmp_path):
    """Setting the current theme to itself does not emit a spurious signal."""
    from milodex.gui.theme_manager import ThemeManager

    manager = ThemeManager(settings_path=tmp_path / "gui_settings.json")

    received: list[str] = []
    manager.themeChanged.connect(received.append)

    # Default is editorial-dark — re-set it.
    manager.set_theme("editorial-dark")

    assert received == []


@_skip_no_qt
def test_unknown_theme_rejected_with_warning(qapp, tmp_path, caplog):
    """An unknown theme name logs a WARNING and leaves state unchanged."""
    from milodex.gui.theme_manager import ThemeManager

    manager = ThemeManager(settings_path=tmp_path / "gui_settings.json")
    before = manager.theme

    with caplog.at_level(logging.WARNING, logger="milodex.gui.theme_manager"):
        result = manager.set_theme("nonexistent-theme")

    assert result is False
    assert manager.theme == before
    assert any("unknown theme" in rec.message for rec in caplog.records)


@_skip_no_qt
def test_corrupt_settings_falls_back_to_default(qapp, tmp_path, caplog):
    """A garbage settings file logs WARNING and falls back to default."""
    from milodex.gui.theme_manager import ThemeManager

    settings = tmp_path / "gui_settings.json"
    settings.write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="milodex.gui.theme_manager"):
        manager = ThemeManager(settings_path=settings)

    assert manager.theme == "editorial-dark"
    assert any("failed to read settings" in rec.message for rec in caplog.records)


@_skip_no_qt
def test_unknown_persisted_theme_falls_back_to_default(qapp, tmp_path, caplog):
    """A settings file naming an unknown theme falls back with a WARNING."""
    from milodex.gui.theme_manager import ThemeManager

    settings = tmp_path / "gui_settings.json"
    settings.write_text(
        json.dumps({"theme": "obsolete", "version": 1}),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="milodex.gui.theme_manager"):
        manager = ThemeManager(settings_path=settings)

    assert manager.theme == "editorial-dark"
    assert any("unknown theme" in rec.message for rec in caplog.records)


@_skip_no_qt
def test_unknown_schema_version_falls_back(qapp, tmp_path, caplog):
    """A future schema version falls back to default and warns."""
    from milodex.gui.theme_manager import ThemeManager

    settings = tmp_path / "gui_settings.json"
    settings.write_text(
        json.dumps({"theme": "bronze", "version": 99}),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="milodex.gui.theme_manager"):
        manager = ThemeManager(settings_path=settings)

    assert manager.theme == "editorial-dark"
    assert any("unexpected version" in rec.message for rec in caplog.records)


@_skip_no_qt
def test_missing_settings_file_uses_default(qapp, tmp_path):
    """When the settings file does not exist, default is used silently."""
    from milodex.gui.theme_manager import ThemeManager

    settings = tmp_path / "definitely_missing.json"
    assert not settings.exists()

    manager = ThemeManager(settings_path=settings)
    assert manager.theme == "editorial-dark"


@_skip_no_qt
def test_set_theme_with_unchanged_value_does_not_rewrite_disk(qapp, tmp_path):
    """A no-op set_theme (same value) must not touch the settings file on disk.

    Captures the file's mtime before and after calling set_theme with the
    currently-active theme name, and asserts the mtime is unchanged.  This
    guards against the write-storm footgun where every hot-reload cycle
    triggers a disk write even when nothing changed.
    """
    from milodex.gui.theme_manager import ThemeManager

    settings = tmp_path / "gui_settings.json"
    manager = ThemeManager(settings_path=settings)

    # Perform an initial real change so the file exists on disk.
    manager.set_theme("bronze")
    assert settings.exists()

    mtime_before = settings.stat().st_mtime_ns

    # No-op: theme is already "bronze".
    result = manager.set_theme("bronze")

    mtime_after = settings.stat().st_mtime_ns

    assert result is True
    assert mtime_after == mtime_before, (
        "set_theme with an unchanged value must not rewrite the settings file"
    )


@_skip_no_qt
def test_known_themes_constant_is_canonical():
    """KNOWN_THEMES exposes the three canonical names in catalog order."""
    from milodex.gui.theme_manager import KNOWN_THEMES

    assert KNOWN_THEMES == ("editorial-dark", "editorial-light", "bronze")
