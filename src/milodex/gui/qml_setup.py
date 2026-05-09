"""QML registration for the Milodex GUI.

Bridges Python-side ``QObject`` types into QML.  Registers
:class:`~milodex.gui.theme_manager.ThemeManager`,
:class:`~milodex.gui.operational_state.OperationalState`, and
:class:`~milodex.gui.strategy_bank_state.StrategyBankState` as QML
*singleton instances* under the ``Milodex`` import URI, which makes the
Python-owned objects visible to every QML file (including the ``Theme``
singleton).

QML singletons are the right shape for these objects because the
``Theme`` singleton itself needs to reach the ``ThemeManager`` during
its own property bindings — context properties on the root context are
not visible to QML singletons, so a registered singleton is the
load-bearing mechanism.  ``OperationalState`` and ``StrategyBankState``
follow the same pattern so multiple surfaces (Anchor, Strategy Bank,
future Attribution) can bind to the same live state without each
receiving its own copy.

Subsequent PRs (app shell, components) call :func:`register_qml_types`
once during application startup *before* loading any QML.
"""

from __future__ import annotations

from PySide6.QtQml import qmlRegisterSingletonInstance

from milodex.gui.operational_state import OperationalState
from milodex.gui.read_models import BenchState, DeskState, FrontPageState, LedgerState
from milodex.gui.strategy_bank_state import StrategyBankState
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
_strategy_bank_state_instance: StrategyBankState | None = None
_front_page_state_instance: FrontPageState | None = None
_bench_state_instance: BenchState | None = None
_ledger_state_instance: LedgerState | None = None
_desk_state_instance: DeskState | None = None


def register_qml_types(
    theme_manager: ThemeManager | None = None,
    operational_state: OperationalState | None = None,
    strategy_bank_state: StrategyBankState | None = None,
    front_page_state: FrontPageState | None = None,
    bench_state: BenchState | None = None,
    ledger_state: LedgerState | None = None,
    desk_state: DeskState | None = None,
) -> ThemeManager:
    """Register Python QML types and return the :class:`ThemeManager` singleton.

    Registers the *theme_manager* (or a freshly constructed instance
    when *None*) as the QML singleton ``Milodex.ThemeManager``.  When
    *operational_state* is provided, it is registered as
    ``Milodex.OperationalState``.  When *strategy_bank_state* is
    provided, it is registered as ``Milodex.StrategyBankState``.

    After this call any QML file can ``import Milodex 1.0`` and reference
    ``ThemeManager.theme``, ``OperationalState.*``,
    ``StrategyBankState.*`` etc.

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
    strategy_bank_state
        Optional pre-constructed :class:`StrategyBankState`.  When
        provided, it is registered as the ``Milodex.StrategyBankState``
        QML singleton.  When omitted, the Strategy Bank surface will not
        load.

    Returns
    -------
    ThemeManager
        The instance now registered as ``Milodex.ThemeManager``.  The
        caller should keep a Python reference; the module also holds
        one to defend against premature garbage collection.
    """
    global _singleton_instance, _operational_state_instance, _strategy_bank_state_instance
    global _front_page_state_instance, _bench_state_instance
    global _ledger_state_instance, _desk_state_instance

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

    if strategy_bank_state is not None:
        qmlRegisterSingletonInstance(
            StrategyBankState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "StrategyBankState",
            strategy_bank_state,
        )
        _strategy_bank_state_instance = strategy_bank_state

    if front_page_state is not None:
        qmlRegisterSingletonInstance(
            FrontPageState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "FrontPageState",
            front_page_state,
        )
        _front_page_state_instance = front_page_state

    if bench_state is not None:
        qmlRegisterSingletonInstance(
            BenchState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "BenchState",
            bench_state,
        )
        _bench_state_instance = bench_state

    if ledger_state is not None:
        qmlRegisterSingletonInstance(
            LedgerState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "LedgerState",
            ledger_state,
        )
        _ledger_state_instance = ledger_state

    if desk_state is not None:
        qmlRegisterSingletonInstance(
            DeskState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "DeskState",
            desk_state,
        )
        _desk_state_instance = desk_state

    return instance
