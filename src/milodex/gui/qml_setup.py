"""QML registration for the Milodex GUI.

Bridges Python-side ``QObject`` types into QML.  Registers
:class:`~milodex.gui.theme_manager.ThemeManager` as a QML *singleton
instance* under the ``Milodex`` import URI, which makes the same
Python-owned object visible to every QML file (including the
``Theme`` singleton) as ``ThemeManager``.

A QML singleton is the right shape for the theme-manager because the
``Theme`` singleton itself needs to reach the manager during its own
property bindings — context properties on the root context are not
visible to QML singletons, so a registered singleton is the
load-bearing mechanism.

Subsequent PRs (app shell, components) call :func:`register_qml_types`
once during application startup *before* loading any QML.
"""

from __future__ import annotations

from PySide6.QtQml import qmlRegisterSingletonInstance

from milodex.gui.theme_manager import ThemeManager

#: QML import URI under which Milodex types and QML files live.  Matches
#: the ``module`` declaration in ``qml/Milodex/qmldir`` so QML files can
#: ``import Milodex 1.0`` and resolve both the Python-registered
#: singleton and the bundled QML singletons / components.
QML_IMPORT_URI: str = "Milodex"
QML_IMPORT_VERSION: tuple[int, int] = (1, 0)

# Module-level reference so the registered singleton is not garbage
# collected after :func:`register_qml_types` returns.  Qt does not
# take ownership of singleton instances passed to
# ``qmlRegisterSingletonInstance``.
_singleton_instance: ThemeManager | None = None


def register_qml_types(theme_manager: ThemeManager | None = None) -> ThemeManager:
    """Register Python QML types and return the :class:`ThemeManager` singleton.

    Registers the *theme_manager* (or a freshly constructed instance
    when *None*) as the QML singleton ``Milodex.ThemeManager``.  After
    this call any QML file can ``import Milodex 1.0`` and reference
    ``ThemeManager.theme`` etc.

    Calling this function more than once with different instances is a
    Qt-level error; the first registration wins.  Tests that need to
    swap the instance should construct a new ``QQmlEngine`` per test.

    Parameters
    ----------
    theme_manager
        Optional pre-constructed instance — primarily for tests that
        want to point the manager at a tmp-path settings file.  When
        omitted a default instance is created.

    Returns
    -------
    ThemeManager
        The instance now registered as ``Milodex.ThemeManager``.  The
        caller should keep a Python reference; the module also holds
        one to defend against premature garbage collection.
    """
    global _singleton_instance

    instance = theme_manager if theme_manager is not None else ThemeManager()
    qmlRegisterSingletonInstance(
        ThemeManager,
        QML_IMPORT_URI,
        QML_IMPORT_VERSION[0],
        QML_IMPORT_VERSION[1],
        "ThemeManager",
        instance,
    )
    _singleton_instance = instance
    return instance
