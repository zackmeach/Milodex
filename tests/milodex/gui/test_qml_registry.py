"""Tests for the single-source-of-truth QML singleton registry (PR3).

Two contracts are pinned here:

1. **Registration / teardown ORDER** — ``app._build_qml_registry`` produces ONE
   ordered list that drives QML registration order *and* (filtered by
   ``lifecycle``) the polling start/stop order.  That start/stop order is the
   Windows clean-shutdown contract (stop every polling read model →
   drain QThreadPool → quit); reordering it is a real regression.  The
   order-snapshot test below freezes both the full registration order and the
   lifecycle slice so an accidental reorder fails loudly.

2. **GC pin** — Qt does NOT take ownership of instances handed to
   ``qmlRegisterSingletonInstance``; ``register_qml_singletons`` must keep a
   Python reference to each so the singleton is not garbage-collected after
   registration returns.  The pin test asserts the module-level ``_PINNED``
   list actually holds the registered instances by identity.

These are pure / lightweight tests: the order-snapshot test never touches Qt
(``_build_qml_registry`` only constructs descriptors), and the pin tests
register under throwaway names so they do not collide with the production
singleton names other GUI tests register process-wide.
"""

from __future__ import annotations

import sys

import pytest

try:
    import PySide6  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(not _PYSIDE6_AVAILABLE, reason="PySide6 not installed")


# ---------------------------------------------------------------------------
# Expected canonical orders (the contract being frozen)
# ---------------------------------------------------------------------------

# Full QML registration order: every singleton, ThemeManager first.
_EXPECTED_REGISTRATION_ORDER: tuple[str, ...] = (
    "ThemeManager",
    "OperationalState",
    "StrategyBankState",
    "FrontPageState",
    "BenchState",
    "KanbanState",
    "LedgerState",
    "PerformanceState",
    "RiskThroughputState",
    "ActiveOpsState",
    "AttentionState",
    "MarketTapeState",
    "ActivityFeedState",
    "BenchCommandBridge",
    "RiskProfileBridge",
    "OrphanReaperController",
)

# The lifecycle slice (registration order filtered to .lifecycle entries) IS the
# polling start/stop teardown order. ThemeManager, BenchCommandBridge and
# RiskProfileBridge are register-only (no start/stop) and must be absent here;
# orphan_reaper_controller must be LAST.
_EXPECTED_LIFECYCLE_ORDER: tuple[str, ...] = (
    "OperationalState",
    "StrategyBankState",
    "FrontPageState",
    "BenchState",
    "KanbanState",
    "LedgerState",
    "PerformanceState",
    "RiskThroughputState",
    "ActiveOpsState",
    "AttentionState",
    "MarketTapeState",
    "ActivityFeedState",
    "OrphanReaperController",
)


def _sentinel_kwargs() -> dict[str, object]:
    """One distinct sentinel object per ``_build_qml_registry`` keyword.

    ``_build_qml_registry`` stores each value verbatim in
    ``QmlSingleton.instance`` and never touches it, so plain objects suffice and
    no Qt app is needed.  Distinct identities let us assert instance plumbing.
    """
    keys = (
        "theme_manager",
        "operational_state",
        "strategy_bank_state",
        "front_page_state",
        "bench_state",
        "kanban_state",
        "ledger_state",
        "performance_state",
        "risk_throughput_state",
        "active_ops_state",
        "attention_state",
        "market_tape_state",
        "activity_feed_state",
        "bench_command_bridge",
        "risk_profile_bridge",
        "orphan_reaper_controller",
    )
    return {k: object() for k in keys}


# ---------------------------------------------------------------------------
# Order-snapshot tests (no Qt required)
# ---------------------------------------------------------------------------


def test_registry_registration_order_is_canonical() -> None:
    """_build_qml_registry yields the exact canonical registration order."""
    from milodex.gui.app import _build_qml_registry

    registry = _build_qml_registry(**_sentinel_kwargs())

    assert [d.qml_name for d in registry] == list(_EXPECTED_REGISTRATION_ORDER)


def test_registry_lifecycle_slice_is_the_teardown_contract() -> None:
    """The lifecycle slice equals the Windows start/stop teardown order.

    operational_state first ... orphan_reaper_controller LAST. The three
    register-only entries (ThemeManager, BenchCommandBridge, RiskProfileBridge)
    must NOT appear. A reorder here is a real shutdown regression, so this
    assertion is intentionally exact.
    """
    from milodex.gui.app import _build_qml_registry

    registry = _build_qml_registry(**_sentinel_kwargs())

    lifecycle_names = [d.qml_name for d in registry if d.lifecycle]
    assert lifecycle_names == list(_EXPECTED_LIFECYCLE_ORDER)
    assert lifecycle_names[-1] == "OrphanReaperController"

    # Register-only entries carry lifecycle=False and are excluded.
    register_only = {d.qml_name for d in registry if not d.lifecycle}
    assert register_only == {"ThemeManager", "BenchCommandBridge", "RiskProfileBridge"}


def test_registry_carries_each_instance_through_unchanged() -> None:
    """Each sentinel instance lands on the matching descriptor, in order."""
    from milodex.gui.app import _build_qml_registry

    kwargs = _sentinel_kwargs()
    registry = _build_qml_registry(**kwargs)

    # The non-theme kwargs map name->instance in canonical order; theme_manager
    # is the first descriptor.
    assert registry[0].instance is kwargs["theme_manager"]
    by_name = {d.qml_name: d.instance for d in registry}
    assert by_name["OperationalState"] is kwargs["operational_state"]
    assert by_name["OrphanReaperController"] is kwargs["orphan_reaper_controller"]
    assert by_name["BenchCommandBridge"] is kwargs["bench_command_bridge"]


# ---------------------------------------------------------------------------
# GC-pin tests (require a Qt app for qmlRegisterSingletonInstance)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication so qmlRegisterSingletonInstance works."""
    if not _PYSIDE6_AVAILABLE:
        return None

    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv)
    return app


@_skip_no_qt
def test_register_qml_singletons_pins_every_instance(qapp) -> None:
    """register_qml_singletons appends each registered instance to _PINNED.

    Uses throwaway QML names so it never collides with the production singleton
    names other GUI tests register in this same process. Asserts the pin holds
    each instance by identity — that reference is what keeps Qt's
    non-owned singleton alive for the process lifetime.
    """
    from PySide6.QtCore import QObject

    from milodex.gui import qml_setup
    from milodex.gui.qml_setup import QmlSingleton, register_qml_singletons

    class _PinProbeA(QObject):
        pass

    class _PinProbeB(QObject):
        pass

    inst_a = _PinProbeA()
    inst_b = _PinProbeB()
    registry = [
        QmlSingleton("PinProbeA", _PinProbeA, inst_a, lifecycle=False),
        QmlSingleton("PinProbeB", _PinProbeB, inst_b, lifecycle=True),
    ]

    register_qml_singletons(registry)

    # Identity membership — the live objects themselves are retained.
    assert any(p is inst_a for p in qml_setup._PINNED)
    assert any(p is inst_b for p in qml_setup._PINNED)


@_skip_no_qt
def test_register_qml_types_back_compat_pins_and_returns_theme(qapp) -> None:
    """The back-compat wrapper still pins instances and returns the ThemeManager.

    Single-arg form: register_qml_types(tm) returns tm, sets the
    _singleton_instance accessor (read by test_qml_components/test_qml_theme_loads),
    and pins tm. Multi-kwarg form additionally pins the extra read model.
    """
    from milodex.gui import qml_setup
    from milodex.gui.qml_setup import register_qml_types
    from milodex.gui.theme_manager import ThemeManager

    tm = ThemeManager()
    returned = register_qml_types(tm)

    assert returned is tm
    assert qml_setup._singleton_instance is tm
    assert any(p is tm for p in qml_setup._PINNED)


@_skip_no_qt
def test_register_qml_types_multi_kwarg_registers_and_pins_extra(qapp) -> None:
    """Multi-kwarg back-compat form pins the extra non-None read model too."""
    from milodex.gui import qml_setup
    from milodex.gui.operational_state import OperationalState
    from milodex.gui.qml_setup import register_qml_types
    from milodex.gui.theme_manager import ThemeManager

    tm = ThemeManager()
    op_state = OperationalState(
        broker_client_factory=lambda: (_ for _ in ()).throw(RuntimeError("no broker")),
        kill_switch_store=_StubKillSwitchStore(),
        trading_mode="paper",
    )

    register_qml_types(tm, op_state)

    assert any(p is op_state for p in qml_setup._PINNED)


class _StubKillSwitchStore:
    """Minimal kill-switch store stub for constructing an OperationalState."""

    def get_state(self):  # noqa: D401 - trivial stub
        from types import SimpleNamespace

        return SimpleNamespace(active=False, reason=None, last_triggered_at=None)


# ---------------------------------------------------------------------------
# Cross-check: _REGISTRY_SPEC (back-compat wrapper) vs _build_qml_registry
# (prod path) must not silently diverge.
# ---------------------------------------------------------------------------


def test_registry_spec_and_build_qml_registry_are_in_sync() -> None:
    """_REGISTRY_SPEC and _build_qml_registry encode the same ordered sequence.

    Both sources encode the canonical QML singleton order and lifecycle flags.
    This test asserts they are identical so a change in one without the other
    fails loudly.  No Qt is needed — _build_qml_registry only constructs
    QmlSingleton descriptors (no Qt calls); _REGISTRY_SPEC is a plain tuple.

    If this test FAILS on a clean run the two sources have already diverged —
    the discrepancy must be resolved rather than the assertion updated.
    """
    from milodex.gui.app import _build_qml_registry
    from milodex.gui.qml_setup import _REGISTRY_SPEC

    # Build the expected sequence from _REGISTRY_SPEC: ThemeManager first
    # (lifecycle=False), then each spec entry (kwarg, qml_name, qml_type, lifecycle).
    spec_sequence = [("ThemeManager", False)] + [
        (qml_name, lifecycle) for _kwarg, qml_name, _qml_type, lifecycle in _REGISTRY_SPEC
    ]

    # Build the actual sequence from the prod path using one sentinel per param.
    sentinel_keys = (
        "theme_manager",
        "operational_state",
        "strategy_bank_state",
        "front_page_state",
        "bench_state",
        "kanban_state",
        "ledger_state",
        "performance_state",
        "risk_throughput_state",
        "active_ops_state",
        "attention_state",
        "market_tape_state",
        "activity_feed_state",
        "bench_command_bridge",
        "risk_profile_bridge",
        "orphan_reaper_controller",
    )
    sentinels = {k: object() for k in sentinel_keys}
    registry = _build_qml_registry(**sentinels)
    prod_sequence = [(d.qml_name, d.lifecycle) for d in registry]

    assert prod_sequence == spec_sequence, (
        "The two registry encodings have diverged.\n"
        f"  _build_qml_registry (prod): {prod_sequence}\n"
        f"  _REGISTRY_SPEC (back-compat): {spec_sequence}\n"
        "Update whichever source is stale so they agree."
    )
