"""Reachability tests for the kill-switch reset flow (HR-4 / G-P1-1).

G-P1-1 finding: AnchorSurface was the sole GUI path to reset_kill_switch,
but nothing in the running app could navigate there after the FRONT/BENCH/
LEDGER/DESK nav rework.  HR-4 extracted KillSwitchResetModal and wires it
from two always-reachable surfaces:
  1. RiskStrip — posture text click (signal: killSwitchResetClicked)
  2. RiskOfficeDrawer — KILL SWITCH section button (signal: killSwitchResetRequested)

Most of the original source-substring pins have been converted to behavioral
trigger-and-observe tests (burn backlog C1/C2): they instantiate the real
component in an offscreen QQuickView, drive it (emit the entry signal, type the
token, click reset/cancel), and observe the live QQuickItem tree / properties.
The remaining source pins in the Test* classes below are the inherently
source-only residue — onClicked -> signal links (no synthetic mouse events),
qmldir registration, file existence, Main.qml wiring, and the anchor-surface
deletion guards.

Source-pin classes (residue only):
  TestModalStructure     — KillSwitchResetModal file-existence + qmldir.
  TestRiskStripWiring    — RiskStrip onClicked->signal + click-active gate.
  TestDrawerWiring       — RiskOfficeDrawer onClicked->signal.
  TestMainQmlWiring      — Main.qml instantiation/routing + anchor deletion.
  TestQmlLoadClean       — KillSwitchResetModal.qml compiles cleanly.

Behavioral sections (trigger-and-observe), below the source-pin classes:
  RiskStrip / RiskOfficeDrawer reachability — the entry signal opens the modal.
  RiskOfficeDrawer section gating          — KILL SWITCH section visibility.
  KillSwitchResetModal reset mechanics     — token gate, success/failure,
                                             clear-on-reopen, cancel.
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
    """KillSwitchResetModal.qml file-existence + qmldir registration.

    The token contract, type-to-confirm gate, open property, close-requested
    emission, reset return-value branch, inline error property/Text, and
    input/error clear-on-reopen pins were converted to behavioral
    trigger-and-observe tests (burn backlog C2 batch 2) and deleted from here:
      * test_modal_reset_button_enabled_only_on_token_match
      * test_modal_reset_success_calls_store_and_closes
      * test_modal_reset_failure_keeps_open_and_surfaces_error
      * test_modal_clears_input_and_error_on_reopen
      * test_modal_cancel_emits_close_requested

    Only file-existence and qmldir registration stay as source pins — neither
    is observable through the offscreen render.
    """

    def test_modal_file_exists(self) -> None:
        assert _MODAL_QML.exists(), f"KillSwitchResetModal.qml missing: {_MODAL_QML}"

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
    """RiskStrip kill-switch reset entry wiring.

    test_signal_declared (``signal killSwitchResetClicked()``) was converted to
    the behavioral pilot test_risk_strip_kill_switch_reset_opens_modal (it
    invokes that exact signal and observes the modal open) and deleted from
    here. The onClicked -> signal emission and the killSwitchActive click-gate
    below stay source pins: the offscreen harness cannot synthesize the posture
    MouseArea click that fires them.
    """

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


# ---------------------------------------------------------------------------
# Behavioral reset mechanics — KillSwitchResetModal (burn backlog C2, batch 2)
#
# TestModalStructure grepped KillSwitchResetModal.qml for the token contract,
# the type-to-confirm gate, the reset return-value branch, the inline error
# property/Text, and the input/error clear-on-reopen. These tests replace the
# convertible pins by INSTANTIATING the real modal over a real OperationalState
# (with a controllable kill-switch store) and driving the reset flow:
#   * the "Yes, reset" button is enabled ONLY when the typed token matches;
#   * a successful reset calls the store and closes the modal;
#   * a failed reset keeps the modal open and surfaces the inline error;
#   * closing+reopening clears the typed token and the error.
#
# qmldir registration (test_modal_registered_in_qmldir) and the file-exists
# pin stay source pins.
# ---------------------------------------------------------------------------


def _build_modal_probe_script(*, reset_raises: bool, assertions: str) -> str:
    """Subprocess script: instantiate the real KillSwitchResetModal (open) over
    a real OperationalState whose kill-switch store's ``reset()`` either
    succeeds or raises (``reset_raises``). The ``assertions`` body drives the
    live modal (type the token, click reset/cancel) and observes the result.
    """
    import_root = str(_QML_IMPORT_ROOT)
    raises_literal = "True" if reset_raises else "False"
    return f"""\
import os, sys, tempfile, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import MagicMock
from PySide6.QtCore import QUrl, QTimer, QMetaObject, QCoreApplication
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
    active=True, reason="test-trip", last_triggered_at=None
)
if {raises_literal}:
    ks_store.reset.side_effect = RuntimeError("probe: store.reset() failed")

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

# Modal wired as Main.qml wires it: open, closeRequested -> open = false.
probe = b\"\"\"
import QtQuick
import Milodex 1.0

Item {{
    id: probeRoot
    width: 1200
    height: 800

    KillSwitchResetModal {{
        id: ksModal
        objectName: "killSwitchResetModalProbe"
        anchors.fill: parent
        open: true
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

modal = root.findChild(_QObjectBase, "killSwitchResetModalProbe")
if modal is None:
    print("modal not found by objectName", file=sys.stderr)
    sys.exit(4)

def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

def _pump():
    QCoreApplication.processEvents()
    QCoreApplication.processEvents()

def _confirm_input():
    for it in _walk(modal):
        if it.metaObject().className() == "QQuickTextInput":
            return it
    return None

def _button(label):
    # Button.qml's root carries a `variant` property; its inner Text mirrors
    # the same `text` but has no `variant`, so filtering on variant lands on
    # the clickable Button (owner of the clicked() signal and enabled state).
    for it in _walk(modal):
        if str(it.property("text") or "") == label and it.property("variant") is not None:
            return it
    return None

def _texts():
    out = []
    for it in _walk(modal):
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


def _run_modal_probe(*, reset_raises: bool, assertions: str, label: str, ok_token: str) -> None:
    script = _build_modal_probe_script(reset_raises=reset_raises, assertions=assertions)
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
def test_modal_reset_button_enabled_only_on_token_match() -> None:
    """The "Yes, reset" button is enabled ONLY when the typed text matches the
    reset token (type-to-confirm gate).

    Behavioral counterpart to TestModalStructure.test_modal_type_to_confirm_gate.
    NON-VACUOUS: dropping the ``enabled: confirmInput.text === ...`` gate (e.g.
    ``enabled: true``) makes the empty-input case enabled and fails (exit 7).
    """
    assertions = (
        "ci = _confirm_input()\n"
        'btn = _button("Yes, reset")\n'
        "if ci is None:\n"
        '    print("confirmInput not found", file=sys.stderr)\n'
        "    sys.exit(5)\n"
        "if btn is None:\n"
        '    print("reset button not found", file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'if bool(btn.property("enabled")):\n'
        '    print("reset enabled with empty input", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        'ci.setProperty("text", "WRONG")\n'
        "_pump()\n"
        'if bool(btn.property("enabled")):\n'
        '    print("reset enabled with wrong token", file=sys.stderr)\n'
        "    sys.exit(8)\n"
        'ci.setProperty("text", "CONFIRM")\n'
        "_pump()\n"
        'if not bool(btn.property("enabled")):\n'
        '    print("reset NOT enabled with correct token", file=sys.stderr)\n'
        "    sys.exit(9)\n"
        'print("RESET_GATE_OK")\n'
        "sys.exit(0)\n"
    )
    _run_modal_probe(
        reset_raises=False,
        assertions=assertions,
        label="reset button type-to-confirm gate",
        ok_token="RESET_GATE_OK",
    )


@_skip_no_qt
def test_modal_reset_success_calls_store_and_closes() -> None:
    """A reset with the correct token calls the kill-switch store's reset() and
    closes the modal with no inline error.

    Behavioral counterpart to TestModalStructure token-contract / passes-token /
    emits-close-requested (success) / reset-checks-return-value (ok branch).
    NON-VACUOUS: short-circuiting the reset call (``var ok = true || ...``)
    leaves store.reset() uncalled and fails (exit 6).
    """
    assertions = (
        "ci = _confirm_input()\n"
        'btn = _button("Yes, reset")\n'
        "if ci is None or btn is None:\n"
        '    print("probe items missing", file=sys.stderr)\n'
        "    sys.exit(5)\n"
        'ci.setProperty("text", "CONFIRM")\n'
        "_pump()\n"
        'QMetaObject.invokeMethod(btn, "clicked")\n'
        "_pump()\n"
        "if not ks_store.reset.called:\n"
        '    print("store.reset() not called on successful reset", file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'if bool(modal.property("open")):\n'
        '    print("modal still open after successful reset", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        'if str(modal.property("_resetError") or ""):\n'
        '    print("unexpected _resetError after success: " + str(modal.property("_resetError")), '
        "file=sys.stderr)\n"
        "    sys.exit(8)\n"
        'print("RESET_SUCCESS_OK")\n'
        "sys.exit(0)\n"
    )
    _run_modal_probe(
        reset_raises=False,
        assertions=assertions,
        label="reset success calls store and closes",
        ok_token="RESET_SUCCESS_OK",
    )


@_skip_no_qt
def test_modal_reset_failure_keeps_open_and_surfaces_error() -> None:
    """When the store's reset() fails (reset_kill_switch returns False), the
    modal STAYS open and renders the inline error text.

    Behavioral counterpart to TestModalStructure keeps-open-on-failure /
    has-error-text-element / reset-checks-return-value (else branch).
    NON-VACUOUS: blanking the else-branch error string makes _resetError empty
    and fails (exit 7); closing unconditionally fails the still-open check.
    """
    assertions = (
        "ci = _confirm_input()\n"
        'btn = _button("Yes, reset")\n'
        "if ci is None or btn is None:\n"
        '    print("probe items missing", file=sys.stderr)\n'
        "    sys.exit(5)\n"
        'ci.setProperty("text", "CONFIRM")\n'
        "_pump()\n"
        'QMetaObject.invokeMethod(btn, "clicked")\n'
        "_pump()\n"
        'if not bool(modal.property("open")):\n'
        '    print("modal closed after FAILED reset (should stay open)", file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'err = str(modal.property("_resetError") or "")\n'
        "if not err:\n"
        '    print("no _resetError after failed reset", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        "texts = _texts()\n"
        "if err not in texts:\n"
        '    print("error text not rendered in live tree rendered=" + repr(texts), '
        "file=sys.stderr)\n"
        "    sys.exit(8)\n"
        'print("RESET_FAILURE_OK")\n'
        "sys.exit(0)\n"
    )
    _run_modal_probe(
        reset_raises=True,
        assertions=assertions,
        label="reset failure keeps modal open with error",
        ok_token="RESET_FAILURE_OK",
    )


@_skip_no_qt
def test_modal_clears_input_and_error_on_reopen() -> None:
    """Closing then reopening the modal clears both the typed token and any
    prior inline error (onOpenChanged reset).

    Behavioral counterpart to TestModalStructure clears-input-on-close /
    clears-error-on-reopen. NON-VACUOUS: dropping the ``root._resetError = ""``
    line from onOpenChanged leaves the stale error after reopen (exit 9).
    """
    assertions = (
        "ci = _confirm_input()\n"
        'btn = _button("Yes, reset")\n'
        "if ci is None or btn is None:\n"
        '    print("probe items missing", file=sys.stderr)\n'
        "    sys.exit(5)\n"
        'ci.setProperty("text", "CONFIRM")\n'
        "_pump()\n"
        'QMetaObject.invokeMethod(btn, "clicked")\n'
        "_pump()\n"
        'if not str(modal.property("_resetError") or ""):\n'
        '    print("precondition: expected _resetError after failed reset", file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'modal.setProperty("open", False)\n'
        "_pump()\n"
        'modal.setProperty("open", True)\n'
        "_pump()\n"
        "ci2 = _confirm_input()\n"
        "if ci2 is None:\n"
        '    print("confirmInput missing after reopen", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        'if str(ci2.property("text") or ""):\n'
        '    print("confirmInput not cleared on reopen: " + str(ci2.property("text")), '
        "file=sys.stderr)\n"
        "    sys.exit(8)\n"
        'if str(modal.property("_resetError") or ""):\n'
        '    print("_resetError not cleared on reopen: " + str(modal.property("_resetError")), '
        "file=sys.stderr)\n"
        "    sys.exit(9)\n"
        'print("CLEAR_ON_REOPEN_OK")\n'
        "sys.exit(0)\n"
    )
    _run_modal_probe(
        reset_raises=True,
        assertions=assertions,
        label="modal clears input and error on reopen",
        ok_token="CLEAR_ON_REOPEN_OK",
    )


@_skip_no_qt
def test_modal_cancel_emits_close_requested() -> None:
    """The Cancel button closes the modal (emits closeRequested).

    Behavioral counterpart to TestModalStructure.test_modal_emits_close_requested
    (cancel path). NON-VACUOUS: stubbing Cancel's ``onClicked`` to ``{}`` leaves
    the modal open and fails (exit 7).
    """
    assertions = (
        'btn = _button("Cancel")\n'
        "if btn is None:\n"
        '    print("cancel button not found", file=sys.stderr)\n'
        "    sys.exit(5)\n"
        'if not bool(modal.property("open")):\n'
        '    print("precondition: modal not open before Cancel", file=sys.stderr)\n'
        "    sys.exit(6)\n"
        'QMetaObject.invokeMethod(btn, "clicked")\n'
        "_pump()\n"
        'if bool(modal.property("open")):\n'
        '    print("modal still open after Cancel", file=sys.stderr)\n'
        "    sys.exit(7)\n"
        'print("CANCEL_OK")\n'
        "sys.exit(0)\n"
    )
    _run_modal_probe(
        reset_raises=False,
        assertions=assertions,
        label="modal cancel closes",
        ok_token="CANCEL_OK",
    )
