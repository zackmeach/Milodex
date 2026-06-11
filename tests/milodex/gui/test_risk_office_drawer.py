"""Tests for RiskOfficeDrawer profile-switching wiring (Task 35 / PR-7c).

Interaction-level tests (simulated clicks, typed input) are deferred to
manual operator verification — the QML subprocess harness used here does not
support synthetic mouse/keyboard events.  These tests verify instead that:

  - The drawer QML structure contains the confirmation-gate copy expected for
    elevation (typed-confirmation text) and reduction (single Confirm).
  - The bridge correctly applies elevation and reduction switches when called
    with the right tokens (tested at the Python layer, not the QML layer).

Full end-to-end UI interaction: manual verification deferred to operator.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from milodex.core.event_store import EventStore
from milodex.gui.risk_profile_bridge import RiskProfileBridge

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"


# ---------------------------------------------------------------------------
# QML structure tests (subprocess smoke — no interaction simulation)
# ---------------------------------------------------------------------------


def _build_drawer_structure_script(check: str) -> str:
    """Return a script that loads RiskOfficeDrawer and checks QML source."""
    import_root = str(_QML_IMPORT_ROOT)
    drawer_qml = str(_MILODEX_QML_DIR / "components" / "RiskOfficeDrawer.qml")
    return f"""\
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from unittest.mock import MagicMock
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from milodex.gui.fonts import load_fonts
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.theme_manager import ThemeManager
from milodex.gui.operational_state import OperationalState
from milodex.gui.read_models import FrontPageState, BenchState, LedgerState
from milodex.gui.performance_state import PerformanceState
from milodex.gui.risk_throughput_state import RiskThroughputState
from milodex.gui.active_ops_state import ActiveOpsState
from milodex.gui.attention_state import AttentionState
from milodex.gui.market_tape_state import MarketTapeState
from milodex.gui.activity_feed_state import ActivityFeedState
from milodex.commands.bench import BenchCommandFacade
from milodex.gui.bench_command_bridge import BenchCommandBridge
from milodex.gui.risk_profile_bridge import RiskProfileBridge

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()
tm = ThemeManager()
ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(active=False, reason=None, last_triggered_at=None)
op = OperationalState(
    broker_client_factory=lambda: (_ for _ in ()).throw(RuntimeError("smoke")),
    kill_switch_store=ks_store, trading_mode="paper",
    kill_switch_poll_seconds=9999.0, broker_poll_seconds=9999.0,
)
_ne = Path("/__nonexistent_smoke_test__")
front = FrontPageState(db_path=_ne, configs_dir=Path("configs"))
bench = BenchState(db_path=_ne, configs_dir=Path("configs"))
ledger = LedgerState(db_path=_ne)
perf = PerformanceState(db_path=_ne, cache_dir=_ne)
rt = RiskThroughputState(db_path=_ne)
ao = ActiveOpsState(db_path=_ne, configs_dir=Path("configs"), locks_dir=_ne)
att = AttentionState(db_path=_ne)
mt = MarketTapeState(cache_dir=_ne)
af = ActivityFeedState(db_path=_ne)
_r = Path(tempfile.mkdtemp(prefix="milodex_drawer_test_"))
(_r / "configs").mkdir(); (_r / "locks").mkdir()
facade = BenchCommandFacade(
    config_dir=_r / "configs",
    locks_dir=_r / "locks",
    get_trading_mode=lambda: "paper",
)
bcb = BenchCommandBridge(facade, bench_state=bench)
rpb = RiskProfileBridge(db_path=_ne)
register_qml_types(
    theme_manager=tm, operational_state=op,
    front_page_state=front, bench_state=bench, ledger_state=ledger,
    performance_state=perf, risk_throughput_state=rt, active_ops_state=ao,
    attention_state=att, market_tape_state=mt, activity_feed_state=af,
    bench_command_bridge=bcb, risk_profile_bridge=rpb,
)
from milodex.gui.app import _make_app_controller
_app_ctrl = _make_app_controller([])

_warnings = []
engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: _warnings.extend(str(m) for m in msgs))
engine.addImportPath({import_root!r})
engine.rootContext().setContextProperty("AppController", _app_ctrl)
engine.load(QUrl.fromLocalFile({drawer_qml!r}))
if _warnings:
    print("WARNINGS:", _warnings, file=sys.stderr)
    sys.exit(3)
if not engine.rootObjects():
    print("NO_ROOT_OBJECTS", file=sys.stderr)
    sys.exit(2)
print("LOAD_OK")
sys.exit(0)
"""


def test_drawer_qml_loads_clean() -> None:
    """RiskOfficeDrawer.qml loads with zero engine warnings."""
    script = _build_drawer_structure_script("load")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(
            f"RiskOfficeDrawer load failed\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def test_drawer_qml_source_contains_typed_confirmation_text() -> None:
    """Drawer source contains the typed-confirmation placeholder copy."""
    drawer_src = (_MILODEX_QML_DIR / "components" / "RiskOfficeDrawer.qml").read_text()
    assert "Type '" in drawer_src, (
        "Drawer must contain typed-confirmation placeholder text for elevation gate"
    )
    assert "aggressive" in drawer_src.lower(), (
        "Drawer must reference 'aggressive' profile in its profile-card iteration"
    )


def test_drawer_qml_source_contains_confirm_button() -> None:
    """Drawer source contains a CONFIRM button for both elevation and reduction."""
    drawer_src = (_MILODEX_QML_DIR / "components" / "RiskOfficeDrawer.qml").read_text()
    assert "CONFIRM" in drawer_src, "Drawer must contain a CONFIRM button"
    assert "confirm_reduction" in drawer_src, (
        "Drawer must use 'confirm_reduction' token for single-click reduction flow"
    )


def test_drawer_qml_source_contains_switch_requested_signal() -> None:
    """Drawer declares switchRequested signal for relay to Main.qml."""
    drawer_src = (_MILODEX_QML_DIR / "components" / "RiskOfficeDrawer.qml").read_text()
    assert "switchRequested" in drawer_src, (
        "Drawer must declare/emit switchRequested(target, token) signal"
    )


# ---------------------------------------------------------------------------
# Python-layer bridge tests — elevation and reduction logic
# (These test the actual gate enforcement without QML interaction)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_with_migrations(tmp_path: Path) -> Path:
    path = tmp_path / "milodex.db"
    EventStore(path)
    return path


def test_drawer_elevation_requires_typed_confirmation(
    db_with_migrations: Path,
) -> None:
    """Elevation (conservative → aggressive) requires typed confirmation equal to target.

    This tests the bridge contract that underpins the QML typed-input gate.
    End-to-end QML interaction (simulated typing): manual verification deferred to operator.
    """
    bridge = RiskProfileBridge(db_path=db_with_migrations)
    applied: list[str] = []
    refused: list[tuple[str, str]] = []
    bridge.switchApplied.connect(lambda name: applied.append(name))
    bridge.switchRefused.connect(lambda r, m: refused.append((r, m)))

    # Wrong token → refused
    result = bridge.attemptSwitch("aggressive", "wrong_token")
    assert result is False
    assert len(refused) == 1
    assert refused[0][0] == "typed_confirmation_mismatch"

    # Correct token → applied
    result = bridge.attemptSwitch("aggressive", "aggressive")
    assert result is True
    assert applied == ["aggressive"]


def test_drawer_reduction_requires_single_click(
    db_with_migrations: Path,
) -> None:
    """Reduction (aggressive → conservative) requires 'confirm_reduction' token.

    This tests the bridge contract that underpins the QML single-Confirm-click gate.
    End-to-end QML interaction: manual verification deferred to operator.
    """
    bridge = RiskProfileBridge(db_path=db_with_migrations)
    # First elevate to aggressive
    bridge.attemptSwitch("aggressive", "aggressive")

    applied: list[str] = []
    refused: list[tuple[str, str]] = []
    bridge.switchApplied.connect(lambda name: applied.append(name))
    bridge.switchRefused.connect(lambda r, m: refused.append((r, m)))

    # Missing confirm_reduction token → refused
    result = bridge.attemptSwitch("conservative", "wrong_token")
    assert result is False
    assert len(refused) == 1
    assert refused[0][0] == "reduction_confirmation_missing"

    # Correct single-click token → applied
    result = bridge.attemptSwitch("conservative", "confirm_reduction")
    assert result is True
    assert applied == ["conservative"]
