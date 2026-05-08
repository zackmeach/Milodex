"""QML registration for the Milodex GUI.

Bridges Python-side ``QObject`` types into QML.  Registers
:class:`~milodex.gui.theme_manager.ThemeManager` and (optionally)
:class:`~milodex.gui.operational_state.OperationalState` as QML *singleton
instances* under the ``Milodex`` import URI, which makes the Python-owned
objects visible to every QML file (including the ``Theme`` singleton).

QML singletons are the right shape for these objects because the
``Theme`` singleton itself needs to reach the ``ThemeManager`` during
its own property bindings — context properties on the root context are
not visible to QML singletons, so a registered singleton is the
load-bearing mechanism.  ``OperationalState`` follows the same pattern
so multiple surfaces (Anchor, future Strategy Bank, future Attribution)
can bind to the same live state without each receiving its own copy.

Subsequent PRs (app shell, components) call :func:`register_qml_types`
once during application startup *before* loading any QML.
"""

from __future__ import annotations

from PySide6.QtQml import qmlRegisterSingletonInstance

from milodex.gui.operational_state import OperationalState
from milodex.gui.theme_manager import ThemeManager

#: QML import URI under which Milodex types and QML files live.  Matches
#: the ``module`` declaration in ``qml/Milodex/qmldir`` so QML files can
#: ``import Milodex 1.0`` and resolve both the Python-registered
#: singleton and the bundled QML singletons / components.
QML_IMPORT_URI: str = "Milodex"
QML_IMPORT_VERSION: tuple[int, int] = (1, 0)

# Module-level references so registered singletons are not garbage
# collected after :func:`register_qml_types` returns.  Qt does not take
# ownership of singleton instances passed to ``qmlRegisterSingletonInstance``.
_singleton_instance: ThemeManager | None = None
_operational_state_instance: OperationalState | None = None


def register_qml_types(
    theme_manager: ThemeManager | None = None,
    operational_state: OperationalState | None = None,
) -> ThemeManager:
    """Register Python QML types and return the :class:`ThemeManager` singleton.

    Registers the *theme_manager* (or a freshly constructed instance
    when *None*) as the QML singleton ``Milodex.ThemeManager``.  When
    *operational_state* is provided, it is registered as
    ``Milodex.OperationalState``.  After this call any QML file can
    ``import Milodex 1.0`` and reference ``ThemeManager.theme`` etc.

    Calling this function more than once with different instances is a
    Qt-level error; the first registration wins.  Tests that need to
    swap the instance should construct a new ``QQmlEngine`` per test.

    Parameters
    ----------
    theme_manager
        Optional pre-constructed instance — primarily for tests that
        want to point the manager at a tmp-path settings file.  When
        omitted a default instance is created.
    operational_state
        Optional pre-constructed :class:`OperationalState`.  When
        provided, it is registered as the ``Milodex.OperationalState``
        QML singleton.  When omitted (e.g. for tests that only exercise
        Theme), the singleton is not registered and QML surfaces that
        depend on it will not load.

    Returns
    -------
    ThemeManager
        The instance now registered as ``Milodex.ThemeManager``.  The
        caller should keep a Python reference; the module also holds
        one to defend against premature garbage collection.
    """
    global _singleton_instance, _operational_state_instance

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

    if operational_state is not None:
        qmlRegisterSingletonInstance(
            OperationalState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "OperationalState",
            operational_state,
        )
        _operational_state_instance = operational_state

    return instance
