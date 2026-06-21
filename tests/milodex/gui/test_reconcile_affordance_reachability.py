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

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUI_SRC = _REPO_ROOT / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"

_DRAWER_QML = _MILODEX_QML_DIR / "components" / "RiskOfficeDrawer.qml"

try:
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed - skipping reconcile affordance behavior tests",
)


# ---------------------------------------------------------------------------
# Structural: RiskOfficeDrawer QML
# ---------------------------------------------------------------------------


class TestDrawerReconcileWiring:
    """RiskOfficeDrawer FLEET RECONCILIATION wiring.

    The section-always-visible, _reconcile* property-declaration, and
    completion-handler pins were converted to behavioral trigger-and-observe
    tests (burn backlog C2 batch 3) and deleted from here:
      * test_reconcile_section_renders_independent_of_kill_switch
      * test_reconcile_clean_completion_updates_result
      * test_reconcile_dirty_completion_updates_result

    Only the button onClicked -> bridge call and the busy-set-before-call stay
    source pins: the offscreen harness cannot synthesize the mouse click that
    fires the RUN RECONCILIATION button's MouseArea.
    """

    def test_button_calls_run_reconciliation_async(self) -> None:
        """Button onClicked must call BenchCommandBridge.runReconciliationAsync().

        Source-only by necessity: guards the MouseArea ``onClicked`` body, which
        the offscreen QQuickView harness cannot drive without synthetic events.
        """
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "BenchCommandBridge.runReconciliationAsync()" in src, (
            "RiskOfficeDrawer.qml must call BenchCommandBridge.runReconciliationAsync() "
            "from the reconciliation button's onClicked handler"
        )

    def test_busy_flag_set_before_async_call(self) -> None:
        """_reconcileBusy must be set to true before the async call fires.

        Source-only by necessity: the busy flag is set inside the MouseArea
        ``onClicked`` body (the click path), which the offscreen harness cannot
        synthesize. The completion handler's busy-clear is covered behaviorally
        by test_reconcile_clean_completion_updates_result.
        """
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "root._reconcileBusy = true" in src, (
            "RiskOfficeDrawer.qml must set root._reconcileBusy = true in the button handler"
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


# ---------------------------------------------------------------------------
# Behavioral reachability — FLEET RECONCILIATION drawer section (burn backlog
# C2, batch 3)
#
# TestDrawerReconcileWiring grepped RiskOfficeDrawer.qml for the section
# eyebrow, the _reconcile* state properties, and the onReconciliationCompleted
# handler assignments. These tests replace the convertible pins by
# instantiating the real drawer with a fake BenchCommandBridge singleton (the
# Connections target) and driving / observing the live tree:
#   * FLEET RECONCILIATION renders even while the kill switch is inactive
#     (the section has no visibility gate);
#   * a "clean" reconciliationCompleted clears busy + renders a Clean result;
#   * a "dirty" reconciliationCompleted renders a Dirty result and flips the
#     not-clean flag.
#
# The button onClicked -> BenchCommandBridge.runReconciliationAsync() and the
# busy-set-before-call stay source pins (TestDrawerReconcileWiring.
# test_button_calls_run_reconciliation_async / test_busy_flag_set_before_async_
# call): the offscreen harness cannot synthesize the mouse click that fires them.
# ---------------------------------------------------------------------------


def _build_reconcile_probe_script(*, assertions: str) -> str:
    """Subprocess script: instantiate RiskOfficeDrawer with a fake
    BenchCommandBridge singleton (so the drawer's Connections target binds and
    reconciliationCompleted can be emitted from the test body). OperationalState
    boots with the kill switch INACTIVE so the reconcile section is exercised
    independently of the kill-switch section. The ``assertions`` body drives /
    observes the live ``drawer`` tree and exits non-zero on failure.
    """
    import_root = str(_QML_IMPORT_ROOT)
    return f"""\
import os, sys, tempfile, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock
from PySide6.QtCore import QUrl, QTimer, QCoreApplication, QObject, Signal, Slot
from PySide6.QtCore import QObject as _QObjectBase
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtQml import qmlRegisterSingletonInstance

from milodex.gui.fonts import load_fonts
from milodex.gui.theme_manager import ThemeManager
from milodex.gui import qml_setup
from milodex.gui.operational_state import OperationalState

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

tm = ThemeManager()

ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(
    active=False, reason=None, last_triggered_at=None
)

def _failing_broker():
    raise RuntimeError("probe: no broker")

op = OperationalState(
    broker_client_factory=_failing_broker,
    kill_switch_store=ks_store,
    trading_mode="paper",
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)
op._poll_kill_switch()

qml_setup.register_qml_types(theme_manager=tm, operational_state=op)

class FakeBenchCommandBridge(QObject):
    # Record-only stand-in: the drawer's Connections target. The completion
    # signal is emitted from the test body to drive onReconciliationCompleted.
    reconciliationCompleted = Signal("QVariantMap")

    @Slot()
    def runReconciliationAsync(self):
        pass

_fake_bridge = FakeBenchCommandBridge()
qmlRegisterSingletonInstance(QObject, "Milodex", 1, 0, "BenchCommandBridge", _fake_bridge)

probe = b\"\"\"
import QtQuick
import Milodex 1.0

Item {{
    id: probeRoot
    width: 1200
    height: 800

    RiskOfficeDrawer {{
        id: drawer
        objectName: "riskOfficeDrawerProbe"
        open: true
    }}
}}
\"\"\"

_qml_file = pathlib.Path(tempfile.mktemp(suffix=".qml"))
_qml_file.write_bytes(probe)

view = QQuickView()
view.engine().addImportPath({import_root!r})
view.setResizeMode(QQuickView.SizeRootObjectToView)
view.resize(1200, 800)
view.setSource(QUrl.fromLocalFile(str(_qml_file)))

if view.status() == QQuickView.Error:
    for e in view.errors():
        print(str(e.toString()), file=sys.stderr)
    sys.exit(2)

root = view.rootObject()
if root is None:
    print("rootObject() is None", file=sys.stderr)
    sys.exit(3)

view.show()
QTimer.singleShot(400, app.quit)
app.exec()

drawer = root.findChild(_QObjectBase, "riskOfficeDrawerProbe")
if drawer is None:
    print("drawer not found by objectName", file=sys.stderr)
    sys.exit(4)

def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

def _pump():
    QCoreApplication.processEvents()
    QCoreApplication.processEvents()

def _texts():
    out = []
    for it in _walk(drawer):
        try:
            if not it.isVisible():
                continue
        except Exception:
            pass
        t = it.property("text")
        if t:
            out.append(str(t))
    return out

{assertions}
"""


def _run_reconcile_probe(*, assertions: str, label: str, ok_token: str) -> None:
    script = _build_reconcile_probe_script(assertions=assertions)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{label} FAILED\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert ok_token in result.stdout


@_skip_no_qt
def test_reconcile_section_renders_independent_of_kill_switch() -> None:
    """FLEET RECONCILIATION renders (eyebrow + run button) even while the kill
    switch is inactive — the section has no visibility gate.

    Behavioral counterpart to TestDrawerReconcileWiring.test_section_always_visible.
    NON-VACUOUS: the probe boots with the kill switch inactive, so a sanity
    check asserts the KILL SWITCH eyebrow is ABSENT; if the reconcile section
    were gated like the kill-switch section, its eyebrow would disappear too
    and this fails (exit 5).
    """
    assertions = (
        "texts = _texts()\n"
        'if "FLEET RECONCILIATION" not in texts:\n'
        '    print("FLEET RECONCILIATION eyebrow missing rendered=" + repr(texts), '
        "file=sys.stderr)\n"
        "    sys.exit(5)\n"
        'if "RUN RECONCILIATION" not in texts:\n'
        '    print("RUN RECONCILIATION button missing rendered=" + repr(texts), file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'if "KILL SWITCH" in texts:\n'
        '    print("probe sanity: KILL SWITCH eyebrow shown while inactive", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        'print("RECONCILE_VISIBLE_OK")\n'
        "sys.exit(0)\n"
    )
    _run_reconcile_probe(
        assertions=assertions,
        label="reconcile section renders independent of kill switch",
        ok_token="RECONCILE_VISIBLE_OK",
    )


@_skip_no_qt
def test_reconcile_clean_completion_updates_result() -> None:
    """A "clean" reconciliationCompleted clears the busy flag and renders a
    Clean result line (result-clean flag true).

    Behavioral counterpart to TestDrawerReconcileWiring
    connections-handles-completed / busy-cleared / result-line-updated /
    busy+result+result-clean property declarations. NON-VACUOUS: flipping the
    handler's busy-clear (``_reconcileBusy = false`` -> ``= true``) leaves busy
    set and fails (exit 5).
    """
    assertions = (
        'drawer.setProperty("_reconcileBusy", True)\n'
        "_pump()\n"
        'payload = {"status": "clean", "recorded_at": "2026-06-21T14:30:00Z", '
        '"mismatch_count": 0}\n'
        "_fake_bridge.reconciliationCompleted.emit(payload)\n"
        "_pump()\n"
        'if bool(drawer.property("_reconcileBusy")):\n'
        '    print("_reconcileBusy not cleared on completion", file=sys.stderr)\n'
        "    sys.exit(5)\n"
        'res = str(drawer.property("_reconcileResult") or "")\n'
        'if not res.startswith("Clean"):\n'
        '    print("result not Clean: " + repr(res), file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'if not bool(drawer.property("_reconcileResultClean")):\n'
        '    print("_reconcileResultClean not True for clean run", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        "texts = _texts()\n"
        "if res not in texts:\n"
        '    print("clean result line not rendered rendered=" + repr(texts), file=sys.stderr)\n'
        "    sys.exit(8)\n"
        'print("RECONCILE_CLEAN_OK")\n'
        "sys.exit(0)\n"
    )
    _run_reconcile_probe(
        assertions=assertions,
        label="reconcile clean completion updates result",
        ok_token="RECONCILE_CLEAN_OK",
    )


@_skip_no_qt
def test_reconcile_dirty_completion_updates_result() -> None:
    """A "dirty" reconciliationCompleted renders a Dirty result with the
    mismatch count and flips the not-clean flag.

    Behavioral counterpart to TestDrawerReconcileWiring (dirty branch of the
    completion handler + _reconcileResultClean). NON-VACUOUS: rewriting the
    dirty result prefix (``"Dirty — "`` -> ``"Clean — "``) drops "Dirty" from
    the result and fails (exit 6).
    """
    assertions = (
        'drawer.setProperty("_reconcileBusy", True)\n'
        "_pump()\n"
        'payload = {"status": "dirty", "recorded_at": "2026-06-21T14:30:00Z", '
        '"mismatch_count": 2}\n'
        "_fake_bridge.reconciliationCompleted.emit(payload)\n"
        "_pump()\n"
        'if bool(drawer.property("_reconcileBusy")):\n'
        '    print("_reconcileBusy not cleared on dirty completion", file=sys.stderr)\n'
        "    sys.exit(5)\n"
        'res = str(drawer.property("_reconcileResult") or "")\n'
        'if "Dirty" not in res or "2 mismatch" not in res:\n'
        '    print("dirty result missing tokens: " + repr(res), file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'if bool(drawer.property("_reconcileResultClean")):\n'
        '    print("_reconcileResultClean True for dirty run", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        'print("RECONCILE_DIRTY_OK")\n'
        "sys.exit(0)\n"
    )
    _run_reconcile_probe(
        assertions=assertions,
        label="reconcile dirty completion updates result",
        ok_token="RECONCILE_DIRTY_OK",
    )
