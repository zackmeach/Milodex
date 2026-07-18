"""Theme manager for the Milodex GUI.

Owns the active theme name and persists the operator's choice to
``data/gui_settings.json`` per ADR 0035 Decision 7 and ADR 0018's
durable-state convention.

The :class:`ThemeManager` is a ``QObject`` exposed to QML so that the
``Theme`` singleton (defined in QML) can read its current theme name
and react to changes via the ``themeChanged`` signal.  This is the
Python half of the hot-swap mechanism described in DESIGN_SYSTEM.md
section 9.3.

Settings file schema (``version: 1``)::

    {"theme": "editorial-dark", "version": 1}

The file is tolerant of corruption: a missing or unparseable file
falls back to the default theme (``"editorial-dark"``) and logs a
``WARNING``.  Unknown theme names passed to :meth:`set_theme` are
rejected without state change.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from milodex.config import get_data_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

#: Tuple of theme names known to the system.  Order matches the catalog in
#: DESIGN_SYSTEM.md section 1.2 (default first, then variants).
KNOWN_THEMES: tuple[str, ...] = ("editorial-dark", "editorial-light", "bronze")

#: Default theme — matches DESIGN_SYSTEM.md section 1.2 ("Editorial Dark").
DEFAULT_THEME: str = "editorial-dark"

#: Persistence-file schema version.  Bumped when an incompatible schema
#: change lands.  Forward-compatible by design: unknown future versions
#: trigger a WARNING and a fallback rather than a crash.
SETTINGS_SCHEMA_VERSION: int = 1

#: Settings filename relative to the data directory.
SETTINGS_FILENAME: str = "gui_settings.json"


# ---------------------------------------------------------------------------
# ThemeManager
# ---------------------------------------------------------------------------


class ThemeManager(QObject):
    """Owns the active theme name and persists it to disk.

    Exposed to QML as a context property.  QML reads :pyattr:`theme`
    (the property), reacts to :pyattr:`themeChanged`, and calls
    :meth:`set_theme` to switch themes.
    """

    #: Emitted when the active theme changes.  The argument is the new
    #: theme name.  Named in Qt's camelCase convention so QML can connect
    #: with ``onThemeChanged: ...`` per PySide6 signal-naming rules.
    themeChanged = Signal(str)  # noqa: N815  Qt signal naming convention

    def __init__(
        self,
        parent: QObject | None = None,
        settings_path: Path | None = None,
    ) -> None:
        """Initialize the theme manager.

        Parameters
        ----------
        parent
            Optional Qt parent.
        settings_path
            Override the settings-file location.  Primarily for tests —
            production code uses the default (``data/gui_settings.json``).
        """
        super().__init__(parent)
        self._settings_path: Path = (
            settings_path if settings_path is not None else get_data_dir() / SETTINGS_FILENAME
        )
        self._theme: str = self._load_persisted_theme()

    # ------------------------------------------------------------------
    # Q_PROPERTY: theme
    # ------------------------------------------------------------------

    def _get_theme(self) -> str:
        """Return the currently active theme name."""
        return self._theme

    #: ``Q_PROPERTY(str theme NOTIFY themeChanged)`` — readable from QML
    #: as ``themeManager.theme``.
    theme = Property(str, _get_theme, notify=themeChanged)

    # ------------------------------------------------------------------
    # Q_INVOKABLE methods
    # ------------------------------------------------------------------

    @Slot(str, result=bool)
    def set_theme(self, name: str) -> bool:
        """Change the active theme; persist; emit :pyattr:`themeChanged`.

        Validates *name* against :data:`KNOWN_THEMES`.  Unknown names are
        rejected with a ``WARNING`` and no state change.

        Parameters
        ----------
        name
            One of :data:`KNOWN_THEMES`.

        Returns
        -------
        bool
            ``True`` if the theme was changed or was already the requested
            value, ``False`` if *name* was rejected.
        """
        if name not in KNOWN_THEMES:
            logger.warning(
                "ThemeManager.set_theme: unknown theme %r; rejecting (known themes: %s)",
                name,
                ", ".join(KNOWN_THEMES),
            )
            return False

        if name == self._theme:
            # Idempotent no-op — no signal, no disk write.
            return True

        self._theme = name
        self._persist(name)
        self.themeChanged.emit(name)
        return True

    # ------------------------------------------------------------------
    # Persistence (private)
    # ------------------------------------------------------------------

    def _load_persisted_theme(self) -> str:
        """Read the persisted theme from disk; tolerate every failure mode.

        Returns :data:`DEFAULT_THEME` when the file is missing, unreadable,
        unparseable, schema-mismatched, or names an unknown theme.
        """
        path = self._settings_path
        if not path.exists():
            return DEFAULT_THEME

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "ThemeManager: failed to read settings file %s (%s); "
                "falling back to default theme %r",
                path,
                exc,
                DEFAULT_THEME,
            )
            return DEFAULT_THEME

        if not isinstance(data, dict):
            logger.warning(
                "ThemeManager: settings file %s is not a JSON object; "
                "falling back to default theme %r",
                path,
                DEFAULT_THEME,
            )
            return DEFAULT_THEME

        version = data.get("version")
        if version != SETTINGS_SCHEMA_VERSION:
            logger.warning(
                "ThemeManager: settings file %s has unexpected version %r "
                "(expected %d); falling back to default theme %r",
                path,
                version,
                SETTINGS_SCHEMA_VERSION,
                DEFAULT_THEME,
            )
            return DEFAULT_THEME

        theme_name = data.get("theme")
        if theme_name not in KNOWN_THEMES:
            logger.warning(
                "ThemeManager: settings file %s names unknown theme %r; "
                "falling back to default theme %r",
                path,
                theme_name,
                DEFAULT_THEME,
            )
            return DEFAULT_THEME

        return theme_name

    def _persist(self, name: str) -> None:
        """Write the theme name to disk atomically.  Logs (does not raise) on failure.

        Uses a write-then-replace pattern (``tmp`` → ``path``) so that a
        crash mid-write never leaves a partial settings file.  ``os.replace``
        is atomic on POSIX and near-atomic on Windows (same volume required,
        which is always true here since both paths share the same parent).
        """
        path = self._settings_path
        payload = {"theme": name, "version": SETTINGS_SCHEMA_VERSION}
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp_path, path)
        except OSError as exc:
            logger.warning(
                "ThemeManager: failed to persist theme %r to %s (%s); in-memory state unchanged",
                name,
                path,
                exc,
            )
