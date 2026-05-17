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

from milodex.gui.active_ops_state import ActiveOpsState
from milodex.gui.activity_feed_state import ActivityFeedState
from milodex.gui.attention_state import AttentionState
from milodex.gui.bench_command_bridge import BenchCommandBridge
from milodex.gui.market_tape_state import MarketTapeState
from milodex.gui.operational_state import OperationalState
from milodex.gui.performance_state import PerformanceState
from milodex.gui.read_models import (
    BenchState,
    FrontPageState,
    KanbanState,
    LedgerState,
)
from milodex.gui.risk_throughput_state import RiskThroughputState
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
_kanban_state_instance: KanbanState | None = None
_ledger_state_instance: LedgerState | None = None
_performance_state_instance: PerformanceState | None = None
_risk_throughput_state_instance: RiskThroughputState | None = None
_active_ops_state_instance: ActiveOpsState | None = None
_attention_state_instance: AttentionState | None = None
_market_tape_state_instance: MarketTapeState | None = None
_activity_feed_state_instance: ActivityFeedState | None = None
_bench_command_bridge_instance: BenchCommandBridge | None = None


def register_qml_types(
    theme_manager: ThemeManager | None = None,
    operational_state: OperationalState | None = None,
    strategy_bank_state: StrategyBankState | None = None,
    front_page_state: FrontPageState | None = None,
    bench_state: BenchState | None = None,
    kanban_state: KanbanState | None = None,
    ledger_state: LedgerState | None = None,
    performance_state: PerformanceState | None = None,
    risk_throughput_state: RiskThroughputState | None = None,
    active_ops_state: ActiveOpsState | None = None,
    attention_state: AttentionState | None = None,
    market_tape_state: MarketTapeState | None = None,
    activity_feed_state: ActivityFeedState | None = None,
    bench_command_bridge: BenchCommandBridge | None = None,
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
    global _front_page_state_instance, _bench_state_instance, _kanban_state_instance
    global _ledger_state_instance, _performance_state_instance, _risk_throughput_state_instance
    global _active_ops_state_instance, _attention_state_instance, _market_tape_state_instance
    global _activity_feed_state_instance, _bench_command_bridge_instance

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

    if kanban_state is not None:
        qmlRegisterSingletonInstance(
            KanbanState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "KanbanState",
            kanban_state,
        )
        _kanban_state_instance = kanban_state

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

    if performance_state is not None:
        qmlRegisterSingletonInstance(
            PerformanceState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "PerformanceState",
            performance_state,
        )
        _performance_state_instance = performance_state

    if risk_throughput_state is not None:
        qmlRegisterSingletonInstance(
            RiskThroughputState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "RiskThroughputState",
            risk_throughput_state,
        )
        _risk_throughput_state_instance = risk_throughput_state

    if active_ops_state is not None:
        qmlRegisterSingletonInstance(
            ActiveOpsState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "ActiveOpsState",
            active_ops_state,
        )
        _active_ops_state_instance = active_ops_state

    if attention_state is not None:
        qmlRegisterSingletonInstance(
            AttentionState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "AttentionState",
            attention_state,
        )
        _attention_state_instance = attention_state

    if market_tape_state is not None:
        qmlRegisterSingletonInstance(
            MarketTapeState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "MarketTapeState",
            market_tape_state,
        )
        _market_tape_state_instance = market_tape_state

    if activity_feed_state is not None:
        qmlRegisterSingletonInstance(
            ActivityFeedState,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "ActivityFeedState",
            activity_feed_state,
        )
        _activity_feed_state_instance = activity_feed_state

    if bench_command_bridge is not None:
        # ADR 0051 Phase C2: register the Bench command bridge as a QML
        # singleton instance. QML files reach the facade only through this
        # bridge — see src/milodex/gui/bench_command_bridge.py and the
        # forbidden-token tests in tests/milodex/gui/test_qml_load_smoke.py.
        qmlRegisterSingletonInstance(
            BenchCommandBridge,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "BenchCommandBridge",
            bench_command_bridge,
        )
        _bench_command_bridge_instance = bench_command_bridge

    return instance
