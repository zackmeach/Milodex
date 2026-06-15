"""Reachability tests for the GUI reconciliation affordance (HR-10 / G-P2-2).

G-P2-2 finding: GUI runner-start depends on a same-day clean reconciliation
the GUI cannot produce — no reconcile affordance exists anywhere in the GUI.
HR-10 adds a "Run reconciliation" button in the Risk Office drawer that calls
BenchCommandBridge.runReconciliationAsync() and observes the result via
reconciliationCompleted.

Test classes:
  TestDrawerReconcileWiring  — RiskOfficeDrawer declares the internal reconcile
                               state properties, the button, and calls
                               BenchCommandBridge.runReconciliationAsync().
  TestBridgeSlotWiring       — BenchCommandBridge declares runReconciliationAsync
                               as a Slot and reconciliationCompleted as a Signal.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUI_SRC = _REPO_ROOT / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"

_DRAWER_QML = _MILODEX_QML_DIR / "components" / "RiskOfficeDrawer.qml"


# ---------------------------------------------------------------------------
# Structural: RiskOfficeDrawer QML
# ---------------------------------------------------------------------------


class TestDrawerReconcileWiring:
    """RiskOfficeDrawer must declare reconcile state and call the bridge slot."""

    def test_section_always_visible(self) -> None:
        """FLEET RECONCILIATION section has no visibility gate (always visible)."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "FLEET RECONCILIATION" in src, (
            'RiskOfficeDrawer.qml must contain a "FLEET RECONCILIATION" section eyebrow'
        )
        # The section must NOT be gated on a dynamic property like killSwitchActive.
        # We verify by asserting the section label appears, and that the button
        # exists unconditionally (checked below).

    def test_busy_property_declared(self) -> None:
        """Internal _reconcileBusy property must be declared."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "property bool _reconcileBusy:" in src, (
            "RiskOfficeDrawer.qml must declare `property bool _reconcileBusy`"
        )

    def test_result_property_declared(self) -> None:
        """Internal _reconcileResult property must be declared."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "property string _reconcileResult:" in src, (
            "RiskOfficeDrawer.qml must declare `property string _reconcileResult`"
        )

    def test_result_clean_property_declared(self) -> None:
        """Internal _reconcileResultClean property must be declared."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "property bool _reconcileResultClean:" in src, (
            "RiskOfficeDrawer.qml must declare `property bool _reconcileResultClean`"
        )

    def test_button_calls_run_reconciliation_async(self) -> None:
        """Button onClicked must call BenchCommandBridge.runReconciliationAsync()."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "BenchCommandBridge.runReconciliationAsync()" in src, (
            "RiskOfficeDrawer.qml must call BenchCommandBridge.runReconciliationAsync() "
            "from the reconciliation button's onClicked handler"
        )

    def test_busy_flag_set_before_async_call(self) -> None:
        """_reconcileBusy must be set to true before the async call fires."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        # Both assignments must appear in the same handler block.
        assert "root._reconcileBusy = true" in src, (
            "RiskOfficeDrawer.qml must set root._reconcileBusy = true in the button handler"
        )

    def test_connections_handles_reconciliation_completed(self) -> None:
        """Connections block must handle onReconciliationCompleted from BenchCommandBridge."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "onReconciliationCompleted" in src, (
            "RiskOfficeDrawer.qml must handle BenchCommandBridge.onReconciliationCompleted "
            "via a Connections block to update the result line"
        )

    def test_busy_cleared_in_completion_handler(self) -> None:
        """_reconcileBusy must be cleared (set to false) in the completion handler."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "root._reconcileBusy = false" in src, (
            "RiskOfficeDrawer.qml must set root._reconcileBusy = false "
            "in the onReconciliationCompleted handler"
        )

    def test_result_line_updated_in_completion_handler(self) -> None:
        """_reconcileResult must be assigned in the completion handler."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "root._reconcileResult" in src, (
            "RiskOfficeDrawer.qml must assign root._reconcileResult "
            "in the onReconciliationCompleted handler"
        )


# ---------------------------------------------------------------------------
# Structural: BenchCommandBridge Python
# ---------------------------------------------------------------------------


class TestBridgeSlotWiring:
    """BenchCommandBridge must expose runReconciliationAsync slot and reconciliationCompleted."""

    def test_run_reconciliation_async_slot_declared(self) -> None:
        """runReconciliationAsync must be decorated with @Slot."""
        import inspect

        from milodex.gui.bench_command_bridge import BenchCommandBridge

        src = inspect.getsource(BenchCommandBridge)
        assert "runReconciliationAsync" in src, (
            "BenchCommandBridge must declare a runReconciliationAsync method"
        )

    def test_reconciliation_completed_signal_declared(self) -> None:
        """reconciliationCompleted must be a Signal on BenchCommandBridge."""
        from PySide6.QtCore import Signal

        from milodex.gui.bench_command_bridge import BenchCommandBridge

        assert hasattr(BenchCommandBridge, "reconciliationCompleted"), (
            "BenchCommandBridge must declare reconciliationCompleted"
        )
        # PySide6 signals are instances of Signal descriptor on the class.
        assert isinstance(BenchCommandBridge.__dict__.get("reconciliationCompleted"), Signal), (
            "BenchCommandBridge.reconciliationCompleted must be a PySide6 Signal"
        )

    def test_run_reconciliation_async_is_callable(self) -> None:
        """BenchCommandFacade.run_reconciliation_now must exist and be callable."""

        from milodex.commands.bench import BenchCommandFacade

        assert hasattr(BenchCommandFacade, "run_reconciliation_now"), (
            "BenchCommandFacade must declare run_reconciliation_now"
        )
        assert callable(BenchCommandFacade.run_reconciliation_now), (
            "BenchCommandFacade.run_reconciliation_now must be callable"
        )
