"""QML registration for the Milodex GUI.

Bridges Python-side ``QObject`` types into QML.  Registers
:class:`~milodex.gui.theme_manager.ThemeManager`,
:class:`~milodex.gui.operational_state.OperationalState`, and the
remaining read models as QML *singleton instances* under the ``Milodex``
import URI, which makes the Python-owned objects visible to every QML
file (including the ``Theme`` singleton).

QML singletons are the right shape for these objects because the
``Theme`` singleton itself needs to reach the ``ThemeManager`` during
its own property bindings — context properties on the root context are
not visible to QML singletons, so a registered singleton is the
load-bearing mechanism.

Production startup (``milodex.gui.app.run_app``) builds an ordered
:class:`QmlSingleton` registry via ``_build_qml_registry`` and calls
:func:`register_qml_singletons` directly.  :func:`register_qml_types` is
retained as a back-compat wrapper so existing test/smoke harnesses that pass
a keyword-subset are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtQml import qmlRegisterSingletonInstance

from milodex.gui.active_ops_state import ActiveOpsState
from milodex.gui.activity_feed_state import ActivityFeedState
from milodex.gui.attention_state import AttentionState
from milodex.gui.bench_command_bridge import BenchCommandBridge
from milodex.gui.market_tape_state import MarketTapeState
from milodex.gui.operational_state import OperationalState
from milodex.gui.orphan_reaper_controller import OrphanReaperController
from milodex.gui.performance_state import PerformanceState
from milodex.gui.read_models import (
    BenchState,
    FrontPageState,
    LedgerState,
)
from milodex.gui.risk_profile_bridge import RiskProfileBridge
from milodex.gui.risk_throughput_state import RiskThroughputState
from milodex.gui.theme_manager import ThemeManager

#: QML import URI under which Milodex types and QML files live.  Matches
#: the ``module`` declaration in ``qml/Milodex/qmldir`` so QML files can
#: ``import Milodex 1.0`` and resolve both the Python-registered
#: singleton and the bundled QML singletons / components.
QML_IMPORT_URI: str = "Milodex"
QML_IMPORT_VERSION: tuple[int, int] = (1, 0)


@dataclass(frozen=True)
class QmlSingleton:
    """One QML singleton-instance registration descriptor.

    A registry of these — one ordered list — drives BOTH QML registration
    order and (filtered by :attr:`lifecycle`) the polling start/stop order.
    Keeping a single ordered source of truth is the whole point: the
    Windows-shutdown teardown contract (stop polling → drain QThreadPool →
    quit) depends on the lifecycle order being exactly the registration order
    filtered to the lifecycle-bearing entries.
    """

    qml_name: str
    """QML type name, e.g. ``"OperationalState"`` — the ``import Milodex 1.0``
    handle QML files reference."""

    qml_type: type
    """The ``QObject`` subclass registered as the singleton type."""

    instance: object
    """The live instance handed to ``qmlRegisterSingletonInstance``."""

    lifecycle: bool = False
    """``True`` when the instance has ``.start()`` / ``.stop()`` polling and
    participates in the start/stop teardown order."""


# Module-level pin list so registered singleton instances are not garbage
# collected after registration returns.  Qt does NOT take ownership of
# instances passed to ``qmlRegisterSingletonInstance`` — drop the Python
# reference and QML sees a dead singleton.  Every registered instance is
# appended here.
_PINNED: list[object] = []

#: Back-compat reference to the registered :class:`ThemeManager`.  Some tests
#: read ``qml_setup._singleton_instance`` directly to reset the active theme
#: between cases (see ``tests/milodex/gui/test_qml_components.py`` and
#: ``test_qml_theme_loads.py``).  Kept as a module global for that contract;
#: production code uses the value returned by :func:`register_qml_types`.
_singleton_instance: ThemeManager | None = None


def register_qml_singletons(registry: Sequence[QmlSingleton]) -> None:
    """Register each :class:`QmlSingleton` in order and pin its instance.

    Iterates *registry* in the given order, calling
    ``qmlRegisterSingletonInstance`` for each descriptor and appending the
    live instance to the module-level :data:`_PINNED` list so it survives
    garbage collection.

    Registration ORDER is observable to Qt and is the load-bearing contract
    (see :class:`QmlSingleton`).  Callers must pass the registry already in
    canonical order.

    Parameters
    ----------
    registry
        Ordered descriptors to register.  Calling this more than once with a
        ``qml_name`` already registered in the process is a Qt-level error;
        the first registration wins.
    """
    for descriptor in registry:
        qmlRegisterSingletonInstance(
            descriptor.qml_type,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            descriptor.qml_name,
            descriptor.instance,
        )
        _PINNED.append(descriptor.instance)


# Static spec mapping each :func:`register_qml_types` keyword argument to its
# (qml_name, qml_type, lifecycle) in the canonical registration order.  This
# drives the back-compat wrapper; production (``app.py``) builds its own
# ordered registry directly.  ``theme_manager`` is handled separately because
# it defaults to a fresh ``ThemeManager()`` and is the return value.
_REGISTRY_SPEC: tuple[tuple[str, str, type, bool], ...] = (
    ("operational_state", "OperationalState", OperationalState, True),
    ("front_page_state", "FrontPageState", FrontPageState, True),
    ("bench_state", "BenchState", BenchState, True),
    ("ledger_state", "LedgerState", LedgerState, True),
    ("performance_state", "PerformanceState", PerformanceState, True),
    ("risk_throughput_state", "RiskThroughputState", RiskThroughputState, True),
    ("active_ops_state", "ActiveOpsState", ActiveOpsState, True),
    ("attention_state", "AttentionState", AttentionState, True),
    ("market_tape_state", "MarketTapeState", MarketTapeState, True),
    ("activity_feed_state", "ActivityFeedState", ActivityFeedState, True),
    ("bench_command_bridge", "BenchCommandBridge", BenchCommandBridge, False),
    ("risk_profile_bridge", "RiskProfileBridge", RiskProfileBridge, False),
    ("orphan_reaper_controller", "OrphanReaperController", OrphanReaperController, True),
)


def register_qml_types(
    theme_manager: ThemeManager | None = None,
    operational_state: OperationalState | None = None,
    front_page_state: FrontPageState | None = None,
    bench_state: BenchState | None = None,
    ledger_state: LedgerState | None = None,
    performance_state: PerformanceState | None = None,
    risk_throughput_state: RiskThroughputState | None = None,
    active_ops_state: ActiveOpsState | None = None,
    attention_state: AttentionState | None = None,
    market_tape_state: MarketTapeState | None = None,
    activity_feed_state: ActivityFeedState | None = None,
    bench_command_bridge: BenchCommandBridge | None = None,
    risk_profile_bridge: RiskProfileBridge | None = None,
    orphan_reaper_controller: OrphanReaperController | None = None,
) -> ThemeManager:
    """Register Python QML types and return the :class:`ThemeManager` singleton.

    Registers the *theme_manager* (or a freshly constructed instance
    when *None*) as the QML singleton ``Milodex.ThemeManager``.  When
    *operational_state* is provided, it is registered as
    ``Milodex.OperationalState``.  Additional kwargs are registered in
    the canonical order defined by :data:`_REGISTRY_SPEC`.

    After this call any QML file can ``import Milodex 1.0`` and reference
    ``ThemeManager.theme``, ``OperationalState.*`` etc.

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

    Notes
    -----
    This is a thin back-compat wrapper around
    :func:`register_qml_singletons`, kept at its original signature so
    existing callers (and the subprocess smoke harnesses that pass a subset
    of kwargs) are untouched.  Production startup
    (``milodex.gui.app.run_app``) builds an ordered :class:`QmlSingleton`
    registry directly and calls :func:`register_qml_singletons` instead.

    The ``theme_manager`` is always registered first (defaulting to a fresh
    ``ThemeManager()``); the remaining non-``None`` kwargs are registered in
    the canonical order defined by :data:`_REGISTRY_SPEC`.  Omitting a kwarg
    simply leaves that singleton unregistered -- exactly the previous
    ``if x is not None`` behaviour.
    """
    global _singleton_instance

    theme = theme_manager if theme_manager is not None else ThemeManager()

    provided = {
        "operational_state": operational_state,
        "front_page_state": front_page_state,
        "bench_state": bench_state,
        "ledger_state": ledger_state,
        "performance_state": performance_state,
        "risk_throughput_state": risk_throughput_state,
        "active_ops_state": active_ops_state,
        "attention_state": attention_state,
        "market_tape_state": market_tape_state,
        "activity_feed_state": activity_feed_state,
        "bench_command_bridge": bench_command_bridge,
        "risk_profile_bridge": risk_profile_bridge,
        "orphan_reaper_controller": orphan_reaper_controller,
    }

    registry: list[QmlSingleton] = [
        QmlSingleton(qml_name="ThemeManager", qml_type=ThemeManager, instance=theme)
    ]
    registry.extend(
        QmlSingleton(
            qml_name=qml_name,
            qml_type=qml_type,
            instance=provided[kwarg],
            lifecycle=lifecycle,
        )
        for kwarg, qml_name, qml_type, lifecycle in _REGISTRY_SPEC
        if provided[kwarg] is not None
    )

    register_qml_singletons(registry)
    _singleton_instance = theme
    return theme
