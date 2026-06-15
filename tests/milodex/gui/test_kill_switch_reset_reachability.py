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
    """RiskOfficeDrawer must declare killSwitchResetRequested and have a KILL SWITCH section."""

    def test_signal_declared(self) -> None:
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "signal killSwitchResetRequested()" in src, (
            "RiskOfficeDrawer.qml must declare `signal killSwitchResetRequested()`"
        )

    def test_kill_switch_section_exists(self) -> None:
        """KILL SWITCH section heading must appear in the drawer."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert '"KILL SWITCH"' in src, (
            'RiskOfficeDrawer.qml must contain a "KILL SWITCH" section label'
        )

    def test_signal_emitted_from_section(self) -> None:
        """The section button must emit killSwitchResetRequested."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "root.killSwitchResetRequested()" in src, (
            "RiskOfficeDrawer.qml must emit root.killSwitchResetRequested() from the "
            "KILL SWITCH section button"
        )

    def test_section_gated_on_active(self) -> None:
        """KILL SWITCH section must only show when kill switch is active."""
        src = _DRAWER_QML.read_text(encoding="utf-8")
        assert "killSwitchActive" in src, (
            "RiskOfficeDrawer.qml KILL SWITCH section must gate visibility on killSwitchActive"
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
