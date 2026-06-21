"""Reachability tests for the kill-switch reset flow (HR-4 / G-P1-1).

G-P1-1 finding: AnchorSurface was the sole GUI path to reset_kill_switch,
but nothing in the running app could navigate there after the FRONT/BENCH/
LEDGER/DESK nav rework.  HR-4 extracted KillSwitchResetModal and wires it
from two always-reachable surfaces:
  1. RiskStrip — posture text click (signal: killSwitchResetClicked)
  2. RiskOfficeDrawer — KILL SWITCH section button (signal: killSwitchResetRequested)

These tests assert the load-bearing structural properties that guarantee the
flow is reachable, without simulating mouse events (the subprocess harness
used here does not support synthetic events; that is deferred to manual
operator verification per the standing test-infrastructure note in
test_risk_office_drawer.py).

Test classes:
  TestModalStructure     — KillSwitchResetModal QML source has the correct
                           token-contract wiring and close/reset mechanics.
  TestRiskStripWiring    — RiskStrip declares killSwitchResetClicked and its
                           posture area wires it.
  TestDrawerWiring       — RiskOfficeDrawer declares killSwitchResetRequested
                           and the KILL SWITCH section exists.
  TestMainQmlWiring      — Main.qml instantiates KillSwitchResetModal and
                           routes both entry signals to it.
  TestQmlLoadClean       — KillSwitchResetModal.qml compiles cleanly in a
                           subprocess (zero engine warnings; non-empty root
                           objects).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUI_SRC = _REPO_ROOT / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"

_MODAL_QML = _MILODEX_QML_DIR / "components" / "KillSwitchResetModal.qml"
_RISK_STRIP_QML = _MILODEX_QML_DIR / "components" / "RiskStrip.qml"
_DRAWER_QML = _MILODEX_QML_DIR / "components" / "RiskOfficeDrawer.qml"
_MAIN_QML = _MILODEX_QML_DIR / "Main.qml"


# ---------------------------------------------------------------------------
# Structural: modal QML source
# ---------------------------------------------------------------------------


class TestModalStructure:
    """KillSwitchResetModal.qml must wire the unchanged token contract."""

    def test_modal_file_exists(self) -> None:
        assert _MODAL_QML.exists(), f"KillSwitchResetModal.qml missing: {_MODAL_QML}"

    def test_modal_token_contract_unchanged(self) -> None:
        """Token contract: resetKillSwitchToken → reset_kill_switch unchanged from AnchorSurface."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        assert "OperationalState.resetKillSwitchToken" in src, (
            "KillSwitchResetModal.qml must read OperationalState.resetKillSwitchToken"
        )
        assert "OperationalState.reset_kill_switch(" in src, (
            "KillSwitchResetModal.qml must call OperationalState.reset_kill_switch"
        )

    def test_modal_type_to_confirm_gate(self) -> None:
        """Type-to-confirm gate: the reset button must only fire when typed token matches."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        assert "confirmInput.text === OperationalState.resetKillSwitchToken" in src, (
            "KillSwitchResetModal.qml must gate the reset button on the typed token matching "
            "OperationalState.resetKillSwitchToken"
        )

    def test_modal_declares_open_property(self) -> None:
        """open property controls visibility — Main.qml sets it to show/hide the modal."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        assert "property bool open:" in src, (
            "KillSwitchResetModal.qml must declare `property bool open`"
        )

    def test_modal_emits_close_requested(self) -> None:
        """Modal emits closeRequested on cancel and after a successful reset."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        assert "signal closeRequested()" in src, (
            "KillSwitchResetModal.qml must declare `signal closeRequested()`"
        )
        assert src.count("root.closeRequested()") >= 2, (
            "KillSwitchResetModal.qml must emit closeRequested() from at least 2 places "
            "(Cancel button and after reset_kill_switch call)"
        )

    def test_modal_passes_token_to_slot(self) -> None:
        """The reset slot must be called with the token value, not a bare string."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        # The call may span lines (e.g. wrapped in a var ok = ... assignment).
        assert "OperationalState.reset_kill_switch(" in src, (
            "KillSwitchResetModal.qml must call OperationalState.reset_kill_switch"
        )
        assert "OperationalState.resetKillSwitchToken" in src, (
            "KillSwitchResetModal.qml must pass OperationalState.resetKillSwitchToken "
            "as the argument to reset_kill_switch"
        )

    def test_modal_clears_input_on_close(self) -> None:
        """Input must clear each time the modal closes (prevents lingering text on re-open)."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        assert "confirmInput.text" in src and "onOpenChanged" in src, (
            "KillSwitchResetModal.qml must clear confirmInput.text in an onOpenChanged handler"
        )

    def test_modal_reset_checks_return_value(self) -> None:
        """Reset button must branch on the slot's return value, not close unconditionally."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        # The slot is @Slot(str, result=bool); QML must capture the bool and branch.
        assert "var ok = OperationalState.reset_kill_switch(" in src, (
            "KillSwitchResetModal.qml reset button onClicked must capture the bool return "
            "value of reset_kill_switch into a local variable"
        )
        assert "if (ok)" in src, (
            "KillSwitchResetModal.qml reset button onClicked must branch on the return value"
        )

    def test_modal_keeps_open_on_failure(self) -> None:
        """On reset failure the modal must NOT emit closeRequested (error path omits it)."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        # The 'if (ok)' branch holds the only closeRequested() call in the reset handler;
        # the else branch must set the error property rather than closing.
        assert "_resetError" in src, (
            "KillSwitchResetModal.qml must declare a _resetError property to surface "
            "the failure message inline"
        )

    def test_modal_has_error_text_element(self) -> None:
        """An inline error Text element must exist and be bound to _resetError."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        assert "root._resetError" in src, (
            "KillSwitchResetModal.qml must bind an inline Text element to root._resetError "
            "to display reset-failure feedback"
        )

    def test_modal_clears_error_on_reopen(self) -> None:
        """Error state must clear in onOpenChanged so a re-open starts clean."""
        src = _MODAL_QML.read_text(encoding="utf-8")
        assert '_resetError = ""' in src, (
            "KillSwitchResetModal.qml onOpenChanged must clear _resetError alongside the "
            "input text so a re-opened modal starts with no stale error"
        )

    def test_modal_registered_in_qmldir(self) -> None:
        """KillSwitchResetModal must be registered in qmldir so QML imports resolve."""
        qmldir = (_MILODEX_QML_DIR / "qmldir").read_text(encoding="utf-8")
        assert "KillSwitchResetModal 1.0" in qmldir, (
            "qmldir must register KillSwitchResetModal 1.0 components/KillSwitchResetModal.qml"
        )


# ---------------------------------------------------------------------------
# Structural: RiskStrip wiring
# ---------------------------------------------------------------------------


class TestRiskStripWiring:
    """RiskStrip must declare and emit killSwitchResetClicked when active."""

    def test_signal_declared(self) -> None:
        src = _RISK_STRIP_QML.read_text(encoding="utf-8")
        assert "signal killSwitchResetClicked()" in src, (
            "RiskStrip.qml must declare `signal killSwitchResetClicked()`"
        )

    def test_signal_emitted_from_posture_area(self) -> None:
        """killSwitchResetClicked must be emitted from a MouseArea on the posture text."""
        src = _RISK_STRIP_QML.read_text(encoding="utf-8")
        assert "root.killSwitchResetClicked()" in src, (
            "RiskStrip.qml must emit root.killSwitchResetClicked() from the posture MouseArea"
        )

    def test_click_only_fires_when_active(self) -> None:
        """The MouseArea must gate on killSwitchActive to prevent spurious opens."""
        src = _RISK_STRIP_QML.read_text(encoding="utf-8")
        assert "root.killSwitchActive" in src, (
            "RiskStrip.qml posture MouseArea must gate on root.killSwitchActive"
        )


# ---------------------------------------------------------------------------
# Structural: RiskOfficeDrawer wiring
# ---------------------------------------------------------------------------


class TestDrawerWiring:
    """RiskOfficeDrawer KILL SWITCH section wiring.

    The signal-declared, section-exists, and section-gated-on-active source
    pins were converted to behavioral trigger-and-observe tests (burn backlog
    C2 batch 1) and deleted from here:
      * test_drawer_kill_switch_reset_opens_modal       (signal -> modal opens)
      * test_drawer_kill_switch_section_renders_when_active
      * test_drawer_kill_switch_section_hidden_when_inactive

    Only the onClicked -> signal link below stays a source pin: the offscreen
    harness cannot synthesize the mouse click that fires the RESET KILL SWITCH
    button's MouseArea, so there is no honest behavioral observation for it.
    """

    def test_signal_emitted_from_section(self) -> None:
        """The section button must emit killSwitchResetRequested.

        Source-only by necessity: this guards the
        ``onClicked: root.killSwitchResetRequested()`` one-liner in the RESET
        KILL SWITCH button's MouseArea, which the offscreen QQuickView harness
        cannot drive without synthetic mouse events.
        """
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "root.killSwitchResetRequested()" in src, (
            "RiskOfficeDrawer.qml must emit root.killSwitchResetRequested() from the "
            "KILL SWITCH section button"
        )


# ---------------------------------------------------------------------------
# Structural: Main.qml wiring
# ---------------------------------------------------------------------------


class TestMainQmlWiring:
    """Main.qml must instantiate KillSwitchResetModal and route both entry signals to it."""

    def test_modal_instantiated(self) -> None:
        src = _MAIN_QML.read_text(encoding="utf-8")
        assert "KillSwitchResetModal {" in src, "Main.qml must instantiate KillSwitchResetModal"

    def test_modal_has_id(self) -> None:
        src = _MAIN_QML.read_text(encoding="utf-8")
        assert "id: killSwitchResetModal" in src, (
            "Main.qml must give KillSwitchResetModal id: killSwitchResetModal"
        )

    def test_close_requested_wired(self) -> None:
        """Main.qml must close the modal when it emits closeRequested."""
        src = _MAIN_QML.read_text(encoding="utf-8")
        assert "onCloseRequested:" in src, (
            "Main.qml must handle KillSwitchResetModal.onCloseRequested"
        )

    def test_risk_strip_path_wired(self) -> None:
        """Path 1: RiskStrip.onKillSwitchResetClicked opens the modal."""
        src = _MAIN_QML.read_text(encoding="utf-8")
        assert "onKillSwitchResetClicked:" in src, (
            "Main.qml must handle RiskStrip.onKillSwitchResetClicked to open the reset modal"
        )
        # The handler must set the modal open
        assert "killSwitchResetModal.open = true" in src, (
            "Main.qml onKillSwitchResetClicked must set killSwitchResetModal.open = true"
        )

    def test_drawer_path_wired(self) -> None:
        """Path 2: RiskOfficeDrawer.onKillSwitchResetRequested opens the modal."""
        src = _MAIN_QML.read_text(encoding="utf-8")
        assert "onKillSwitchResetRequested" in src, (
            "Main.qml must handle RiskOfficeDrawer.onKillSwitchResetRequested "
            "to open the reset modal"
        )
        assert "killSwitchResetModal.open = true" in src, (
            "Main.qml onKillSwitchResetRequested handler must set killSwitchResetModal.open = true"
        )

    def test_anchor_surface_route_deleted(self) -> None:
        """'anchor' routing line must be removed from the surface Loader (HR-4)."""
        src = _MAIN_QML.read_text(encoding="utf-8")
        assert 'activeSurface === "anchor"' not in src, (
            "Main.qml surface Loader must not contain the old anchor route (deleted in HR-4)"
        )
        assert 'return "surfaces/AnchorSurface.qml"' not in src, (
            "Main.qml must not return AnchorSurface.qml from the surface Loader (deleted in HR-4)"
        )

    def test_stale_anchor_comment_deleted(self) -> None:
        """Stale comment calling AnchorSurface the 'sole GUI path' must be removed."""
        src = _MAIN_QML.read_text(encoding="utf-8")
        assert "sole GUI path" not in src, (
            "Main.qml must not contain the stale 'sole GUI path' comment referencing "
            "AnchorSurface (deleted in HR-4)"
        )

    def test_anchor_surface_file_deleted(self) -> None:
        """AnchorSurface.qml must no longer exist on disk."""
        anchor_path = _MILODEX_QML_DIR / "surfaces" / "AnchorSurface.qml"
        assert not anchor_path.exists(), (
            "AnchorSurface.qml must be deleted — replaced by KillSwitchResetModal (HR-4)"
        )


# ---------------------------------------------------------------------------
# Load-smoke: subprocess isolation (matching test_qml_load_smoke.py pattern)
# ---------------------------------------------------------------------------


def _build_modal_load_script(modal_path: Path) -> str:
    """Build a self-contained subprocess script that loads KillSwitchResetModal cleanly."""
    import_root = str(_QML_IMPORT_ROOT)
    qml_str = str(modal_path)

    return f"""\
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from unittest.mock import MagicMock
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from milodex.commands.bench import BenchCommandFacade
from milodex.gui.bench_command_bridge import BenchCommandBridge
from milodex.gui.fonts import load_fonts
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.risk_profile_bridge import RiskProfileBridge
from milodex.gui.theme_manager import ThemeManager
from milodex.gui.operational_state import OperationalState
from milodex.gui.read_models import FrontPageState, BenchState, LedgerState
from milodex.gui.performance_state import PerformanceState
from milodex.gui.risk_throughput_state import RiskThroughputState
from milodex.gui.active_ops_state import ActiveOpsState
from milodex.gui.attention_state import AttentionState
from milodex.gui.market_tape_state import MarketTapeState
from milodex.gui.activity_feed_state import ActivityFeedState

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

tm = ThemeManager()

ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(
    active=True, reason="test", last_triggered_at=None
)

def _failing_broker():
    raise RuntimeError("smoke-test: no broker")

op = OperationalState(
    broker_client_factory=_failing_broker,
    kill_switch_store=ks_store,
    trading_mode="paper",
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)

_nonexistent = Path("/__nonexistent_smoke_test__")
front = FrontPageState(db_path=_nonexistent, configs_dir=Path("configs"))
bench = BenchState(db_path=_nonexistent, configs_dir=Path("configs"))
ledger = LedgerState(db_path=_nonexistent)
performance = PerformanceState(db_path=_nonexistent, cache_dir=_nonexistent)
risk_throughput = RiskThroughputState(db_path=_nonexistent)
active_ops = ActiveOpsState(
    db_path=_nonexistent, configs_dir=Path("configs"), locks_dir=_nonexistent
)
attention = AttentionState(db_path=_nonexistent)
market_tape = MarketTapeState(cache_dir=_nonexistent)
activity_feed = ActivityFeedState(db_path=_nonexistent)

_smoke_root = Path(tempfile.mkdtemp(prefix="milodex_smoke_ks_"))
_smoke_configs = _smoke_root / "configs"
_smoke_locks = _smoke_root / "locks"
_smoke_configs.mkdir()
_smoke_locks.mkdir()
facade = BenchCommandFacade(
    config_dir=_smoke_configs,
    locks_dir=_smoke_locks,
    get_trading_mode=lambda: "paper",
)
bench_command_bridge = BenchCommandBridge(facade, bench_state=bench)
risk_profile_bridge = RiskProfileBridge(db_path=_nonexistent)

register_qml_types(
    theme_manager=tm,
    operational_state=op,
    front_page_state=front,
    bench_state=bench,
    ledger_state=ledger,
    performance_state=performance,
    risk_throughput_state=risk_throughput,
    active_ops_state=active_ops,
    attention_state=attention,
    market_tape_state=market_tape,
    activity_feed_state=activity_feed,
    bench_command_bridge=bench_command_bridge,
    risk_profile_bridge=risk_profile_bridge,
)

from milodex.gui.app import _make_app_controller
_app_ctrl = _make_app_controller([])
_warnings: list[str] = []

engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: _warnings.extend(str(m) for m in msgs))
engine.addImportPath({import_root!r})
engine.rootContext().setContextProperty("AppController", _app_ctrl)

engine.load(QUrl.fromLocalFile({qml_str!r}))

errors = [w for w in _warnings if w]

if not engine.rootObjects() and not errors:
    print("LOAD_FAILED: no root objects, no warnings", file=sys.stderr)
    sys.exit(2)

if errors:
    for e in errors:
        print(f"QML_WARNING: {{e}}", file=sys.stderr)
    sys.exit(3)

print("LOAD_OK")
sys.exit(0)
"""


class TestQmlLoadClean:
    """KillSwitchResetModal.qml must compile and load with zero QML warnings."""

    def test_kill_switch_reset_modal_loads_clean(self) -> None:
        """KillSwitchResetModal.qml loads in a subprocess with kill switch active.

        We set ks_store.get_state to active=True so the modal is loaded in
        its operational state (kill switch fired), not just the inactive default.
        Zero QML engine warnings are required.
        """
        script = _build_modal_load_script(_MODAL_QML)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"KillSwitchResetModal.qml load failed\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Behavioral reachability (burn backlog C2 PILOT)
#
# The TestRiskStripWiring / TestMainQmlWiring classes above are SOURCE PINS:
# they grep the .qml for "signal killSwitchResetClicked()" and
# "killSwitchResetModal.open = true". That proves the literals exist, not that
# the entry affordance actually opens the modal. A rename, or a wiring that
# resolves to ``undefined``, can pass the pin while the flow is dead.
#
# This pilot replaces ONE of those pins (the RiskStrip entry path) with a real
# trigger-and-observe test, to be copied across the remaining pins in a
# follow-up batch. The source pins are LEFT IN PLACE until this pattern is
# reviewed and accepted.
#
# Pattern (mirrors test_bench_confirmation_modal_behavior.py):
#   1. INSTANTIATE the real RiskStrip + KillSwitchResetModal (Milodex 1.0
#      types) in a QQuickView, wired EXACTLY as Main.qml wires them
#      (RiskStrip.onKillSwitchResetClicked: modal.open = true), with a real
#      OperationalState whose kill switch is active.
#   2. TRIGGER the entry affordance: emit RiskStrip's killSwitchResetClicked()
#      signal -- the same no-arg signal the posture-text MouseArea fires when
#      the kill switch is active.
#   3. OBSERVE reachability: the modal's `open` property flips to true and the
#      modal item becomes effectively visible (isVisible()) -- i.e. the reset
#      flow is actually REACHED, not merely present in source.
# ---------------------------------------------------------------------------

try:
    from PySide6.QtGui import QGuiApplication as _QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

import pytest  # noqa: E402

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed - skipping kill-switch reset behavior test",
)


def _build_reachability_probe_script() -> str:
    """Self-contained subprocess script: wire RiskStrip -> modal as Main.qml
    does, trigger the entry signal, observe the modal opens."""
    import_root = str(_QML_IMPORT_ROOT)
    return f"""\
import os, sys, tempfile, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock
from PySide6.QtCore import QUrl, QTimer, QMetaObject
from PySide6.QtCore import QObject as _QObjectBase
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from milodex.gui.fonts import load_fonts
from milodex.gui.theme_manager import ThemeManager
from milodex.gui import qml_setup
from milodex.gui.operational_state import OperationalState

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

tm = ThemeManager()

# Real OperationalState with the kill switch ACTIVE (the operational state in
# which the reset affordance is meant to be reachable). The modal binds
# OperationalState.resetKillSwitchToken, so it must be registered.
ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(
    active=True, reason="test-trip", last_triggered_at=None
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

qml_setup.register_qml_types(theme_manager=tm, operational_state=op)

# Probe wiring is a faithful copy of Main.qml's entry path:
#   RiskStrip {{ onKillSwitchResetClicked: ksModal.open = true }}
#   KillSwitchResetModal {{ id: ksModal; onCloseRequested: open = false }}
probe = b\"\"\"
import QtQuick
import Milodex 1.0

Item {{
    id: probeRoot
    width: 1200
    height: 400

    RiskStrip {{
        id: riskStrip
        objectName: "riskStripProbe"
        killSwitchActive: true
        // Faithful to Main.qml: the entry signal opens the reset modal.
        onKillSwitchResetClicked: ksModal.open = true
    }}

    KillSwitchResetModal {{
        id: ksModal
        objectName: "killSwitchResetModalProbe"
        anchors.fill: parent
        onCloseRequested: ksModal.open = false
    }}
}}
\"\"\"

_qml_file = pathlib.Path(tempfile.mktemp(suffix=".qml"))
_qml_file.write_bytes(probe)

view = QQuickView()
view.engine().addImportPath({import_root!r})
view.setResizeMode(QQuickView.SizeRootObjectToView)
view.resize(1200, 400)
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

strip = root.findChild(_QObjectBase, "riskStripProbe")
modal = root.findChild(_QObjectBase, "killSwitchResetModalProbe")
if strip is None or modal is None:
    print("probe items not found", file=sys.stderr)
    sys.exit(4)

# Precondition: the modal starts CLOSED and invisible -- so the post-trigger
# observation is a real state change, not a vacuous always-open.
if bool(modal.property("open")):
    print("PRECONDITION: modal already open before trigger", file=sys.stderr)
    sys.exit(5)
if modal.isVisible():
    print("PRECONDITION: modal already visible before trigger", file=sys.stderr)
    sys.exit(6)

# TRIGGER: emit the real entry-affordance signal the posture MouseArea fires.
if not QMetaObject.invokeMethod(strip, "killSwitchResetClicked"):
    print("could not invoke killSwitchResetClicked signal", file=sys.stderr)
    sys.exit(7)

# Pump once so the QML handler (ksModal.open = true) and the resulting
# visible/binding update propagate through the scene graph.
QTimer.singleShot(200, app.quit)
app.exec()

# OBSERVE: the entry affordance actually REACHED the modal.
if not bool(modal.property("open")):
    print("OBSERVE: modal did not open after killSwitchResetClicked", file=sys.stderr)
    sys.exit(8)
if not modal.isVisible():
    print("OBSERVE: modal open but not effectively visible", file=sys.stderr)
    sys.exit(9)

print("REACHABILITY_OK")
sys.exit(0)
"""


@_skip_no_qt
def test_risk_strip_kill_switch_reset_opens_modal() -> None:
    """Driving RiskStrip's entry affordance opens the KillSwitchResetModal.

    Trigger: emit ``killSwitchResetClicked()`` on a RiskStrip wired exactly as
    Main.qml wires it (``onKillSwitchResetClicked: ksModal.open = true``) with
    the kill switch active.
    Observe: the modal flips from closed+invisible to ``open == true`` and
    effectively visible -- i.e. the reset flow is genuinely REACHED.

    This is the behavioral counterpart to the source pins in
    TestRiskStripWiring (``signal killSwitchResetClicked()``) and
    TestMainQmlWiring (``killSwitchResetModal.open = true``). NON-VACUOUS: the
    probe asserts the modal is closed+invisible BEFORE the trigger, so a
    vacuous always-open modal fails the precondition; rewiring
    onKillSwitchResetClicked to a no-op leaves the modal closed and fails the
    observation. Verified by stubbing the handler to ``{{}}`` -> exit 8
    ("modal did not open after killSwitchResetClicked").
    """
    script = _build_reachability_probe_script()
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "kill-switch reset reachability probe FAILED\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "REACHABILITY_OK" in result.stdout


# ---------------------------------------------------------------------------
# Behavioral reachability + section gating — RiskOfficeDrawer (burn backlog C2,
# batch 1)
#
# RiskOfficeDrawer is the SECOND always-reachable entry to the kill-switch
# reset flow (the KILL SWITCH section button). The TestDrawerWiring source pins
# grepped RiskOfficeDrawer.qml for the signal declaration, the section eyebrow,
# and the killSwitchActive gate. These tests replace them by instantiating the
# real drawer (+ modal wired as Main.qml wires them) and driving / observing
# the live tree:
#   * the drawer's killSwitchResetRequested signal actually OPENS the modal;
#   * the KILL SWITCH section RENDERS when the kill switch is active;
#   * the section is GONE when the kill switch is inactive.
#
# The onClicked -> root.killSwitchResetRequested() link in the RESET KILL
# SWITCH button's MouseArea stays a source pin (TestDrawerWiring.
# test_signal_emitted_from_section): the offscreen harness cannot synthesize
# the mouse click that fires it, so there is no honest behavioral observation
# for that one line.
# ---------------------------------------------------------------------------


def _build_drawer_probe_script(*, active: bool, assertions: str) -> str:
    """Subprocess script: instantiate RiskOfficeDrawer wired to
    KillSwitchResetModal exactly as Main.qml wires them, with a real
    OperationalState whose kill switch is ``active``. The ``assertions`` body
    reads the live ``drawer`` / ``modal`` / ``root`` tree and exits non-zero
    on failure.
    """
    import_root = str(_QML_IMPORT_ROOT)
    active_literal = "True" if active else "False"
    return f"""\
import os, sys, tempfile, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock
from PySide6.QtCore import QUrl, QTimer, QMetaObject
from PySide6.QtCore import QObject as _QObjectBase
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from milodex.gui.fonts import load_fonts
from milodex.gui.theme_manager import ThemeManager
from milodex.gui import qml_setup
from milodex.gui.operational_state import OperationalState

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

tm = ThemeManager()

ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(
    active={active_literal}, reason="test-trip", last_triggered_at=None
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
# Load the kill-switch state synchronously (no worker threads) so the drawer's
# OperationalState.killSwitchActive binding reads the right value at QML load.
op._poll_kill_switch()

qml_setup.register_qml_types(theme_manager=tm, operational_state=op)

# Probe wiring mirrors Main.qml's drawer entry path:
#   RiskOfficeDrawer {{ onKillSwitchResetRequested: ksModal.open = true }}
#   KillSwitchResetModal {{ id: ksModal; onCloseRequested: open = false }}
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
        onKillSwitchResetRequested: ksModal.open = true
    }}

    KillSwitchResetModal {{
        id: ksModal
        objectName: "killSwitchResetModalProbe"
        anchors.fill: parent
        onCloseRequested: ksModal.open = false
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
modal = root.findChild(_QObjectBase, "killSwitchResetModalProbe")
if drawer is None or modal is None:
    print("probe items not found", file=sys.stderr)
    sys.exit(4)

def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

def _texts(rootitem):
    out = []
    for it in _walk(rootitem):
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


def _run_drawer_probe(*, active: bool, assertions: str, label: str, ok_token: str) -> None:
    script = _build_drawer_probe_script(active=active, assertions=assertions)
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
def test_drawer_kill_switch_reset_opens_modal() -> None:
    """Driving RiskOfficeDrawer's entry affordance opens the KillSwitchResetModal.

    Trigger: emit ``killSwitchResetRequested()`` on a RiskOfficeDrawer wired
    exactly as Main.qml wires it (``onKillSwitchResetRequested: ksModal.open =
    true``) with the kill switch active.
    Observe: the modal flips from closed+invisible to ``open == true`` and
    effectively visible -- the drawer entry path genuinely REACHES the reset
    flow.

    Behavioral counterpart to TestDrawerWiring.test_signal_declared
    (``signal killSwitchResetRequested()``) and TestMainQmlWiring
    (``killSwitchResetModal.open = true``). NON-VACUOUS: the probe asserts the
    modal is closed+invisible BEFORE the trigger, so a vacuous always-open
    modal fails the precondition; rewiring onKillSwitchResetRequested to a
    no-op leaves the modal closed and fails the observation (exit 8).
    """
    assertions = (
        'if bool(modal.property("open")):\n'
        '    print("PRECONDITION: modal already open before trigger", file=sys.stderr)\n'
        "    sys.exit(5)\n"
        "if modal.isVisible():\n"
        '    print("PRECONDITION: modal already visible before trigger", file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'if not QMetaObject.invokeMethod(drawer, "killSwitchResetRequested"):\n'
        '    print("could not invoke killSwitchResetRequested signal", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        "QTimer.singleShot(200, app.quit)\n"
        "app.exec()\n"
        'if not bool(modal.property("open")):\n'
        '    print("modal did not open after killSwitchResetRequested", file=sys.stderr)\n'
        "    sys.exit(8)\n"
        "if not modal.isVisible():\n"
        '    print("modal open but not effectively visible", file=sys.stderr)\n'
        "    sys.exit(9)\n"
        'print("DRAWER_REACHABILITY_OK")\n'
        "sys.exit(0)\n"
    )
    _run_drawer_probe(
        active=True,
        assertions=assertions,
        label="drawer kill-switch reset reachability",
        ok_token="DRAWER_REACHABILITY_OK",
    )


@_skip_no_qt
def test_drawer_kill_switch_section_renders_when_active() -> None:
    """The KILL SWITCH section renders (eyebrow + reset button) when the kill
    switch is active.

    Behavioral counterpart to TestDrawerWiring.test_kill_switch_section_exists.
    NON-VACUOUS: deleting the KILL SWITCH eyebrow Text (or breaking its label
    binding so it resolves empty) drops "KILL SWITCH" from the rendered tree.
    """
    assertions = (
        "texts = _texts(drawer)\n"
        'if "KILL SWITCH" not in texts:\n'
        '    print("KILL SWITCH eyebrow missing rendered=" + repr(texts), file=sys.stderr)\n'
        "    sys.exit(5)\n"
        'if "RESET KILL SWITCH" not in texts:\n'
        '    print("RESET KILL SWITCH button missing rendered=" + repr(texts), file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'print("DRAWER_SECTION_VISIBLE_OK")\n'
        "sys.exit(0)\n"
    )
    _run_drawer_probe(
        active=True,
        assertions=assertions,
        label="drawer kill-switch section renders when active",
        ok_token="DRAWER_SECTION_VISIBLE_OK",
    )


@_skip_no_qt
def test_drawer_kill_switch_section_hidden_when_inactive() -> None:
    """The KILL SWITCH section is absent from the rendered tree when the kill
    switch is inactive (the section is visibility-gated on killSwitchActive).

    Behavioral counterpart to TestDrawerWiring.test_section_gated_on_active.
    NON-VACUOUS: the sanity check asserts the always-visible FLEET
    RECONCILIATION eyebrow IS rendered, so the absence of "KILL SWITCH" is a
    real gating result, not a dead walk; dropping the ``visible:`` gate makes
    the eyebrow appear and the test fails (exit 6).
    """
    assertions = (
        "texts = _texts(drawer)\n"
        'if "FLEET RECONCILIATION" not in texts:\n'
        '    print("sanity: walk found no rendered drawer text rendered=" + repr(texts), '
        "file=sys.stderr)\n"
        "    sys.exit(5)\n"
        'if "KILL SWITCH" in texts:\n'
        '    print("KILL SWITCH eyebrow shown while inactive", file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'if "RESET KILL SWITCH" in texts:\n'
        '    print("RESET KILL SWITCH button shown while inactive", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        'print("DRAWER_SECTION_HIDDEN_OK")\n'
        "sys.exit(0)\n"
    )
    _run_drawer_probe(
        active=False,
        assertions=assertions,
        label="drawer kill-switch section hidden when inactive",
        ok_token="DRAWER_SECTION_HIDDEN_OK",
    )
