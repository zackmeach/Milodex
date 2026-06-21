"""QML load smoke test — catches the bug class where invalid QML errors at
load time but pytest never sees it because pytest doesn't render QML.

Surfaced bugs this catches:
  - Row.verticalAlignment (PR E): assigning to a non-existent property on a
    RowLayout child — the chipsRow became "Type X unavailable" and silently
    cascaded up through every importer.
  - QtQuick.Controls native-style cache (PR D.6 era): module import errors.
  - Component-instantiation failures: a child fails to load and cascades
    "Type X unavailable" upward through every importer.

Approach: each parametrized case spawns a fresh subprocess that:
  1. Sets QT_QPA_PLATFORM=offscreen so no display server is needed.
  2. Registers all Milodex QML types via the production qml_setup path
     (ThemeManager, OperationalState stub, and the State models with
     nonexistent db_path — the surface renders to "error" state but loads
     cleanly, which is the path we need to validate).
  3. Loads the target .qml file via QQmlApplicationEngine.
  4. Asserts zero engine warnings and non-empty rootObjects().

Subprocess isolation: each test gets a fresh Python process so Qt's
process-global module cache cannot carry over a previous test's
compiled-type state. Mirrors the pattern in tests/milodex/gui/test_app.py.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# QML targets
# ---------------------------------------------------------------------------

# Top-level surfaces and main window. Components are exercised transitively
# when a surface that imports them loads successfully.
_QML_TARGETS = [
    "surfaces/DesignSystemShowcase.qml",
    "surfaces/FrontSurface.qml",
    "surfaces/BenchSurface.qml",
    "surfaces/LedgerSurface.qml",
    "surfaces/DeskSurface.qml",
    # HR-4: AnchorSurface deleted; KillSwitchResetModal is a component, exercised
    # transitively when Main.qml or any surface that imports it loads.
    "components/KillSwitchResetModal.qml",
]

# Main.qml is the application root; it is tested separately because it wraps
# all surfaces and exercises the full import graph.
_MAIN_QML = "Main.qml"

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"


# ---------------------------------------------------------------------------
# Subprocess script builder
# ---------------------------------------------------------------------------


def _build_script(qml_path: Path) -> str:
    """Return a self-contained Python script that loads *qml_path* in a fresh Qt env.

    The script exits:
      0 — load clean (rootObjects non-empty, zero engine warnings)
      2 — engine bootstrap failed (no root objects, no warnings — rare)
      3 — one or more QML warnings / load errors
    """
    import_root = str(_QML_IMPORT_ROOT)
    qml_str = str(qml_path)

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

# Stub kill-switch store — returns a safe "inactive" state.
ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(
    active=False, reason=None, last_triggered_at=None
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

front = FrontPageState(db_path=Path("/__nonexistent_smoke_test__"), configs_dir=Path("configs"))
bench = BenchState(db_path=Path("/__nonexistent_smoke_test__"), configs_dir=Path("configs"))
ledger = LedgerState(db_path=Path("/__nonexistent_smoke_test__"))

# Trading Desk read-models. Nonexistent db/cache paths → each section
# renders its quiet inline error/empty state; the surface still loads
# cleanly (per-section error isolation — spec §5). This is exactly the
# no-data path PR 8 must validate.
_nonexistent = Path("/__nonexistent_smoke_test__")
performance = PerformanceState(db_path=_nonexistent, cache_dir=_nonexistent)
risk_throughput = RiskThroughputState(db_path=_nonexistent)
active_ops = ActiveOpsState(
    db_path=_nonexistent, configs_dir=Path("configs"), locks_dir=_nonexistent
)
attention = AttentionState(db_path=_nonexistent)
market_tape = MarketTapeState(cache_dir=_nonexistent)
activity_feed = ActivityFeedState(db_path=_nonexistent)

# Real BenchCommandBridge backed by a real facade over a throwaway tmpdir.
# The bridge must be registered as a QML singleton instance so QML references
# to ``Milodex.BenchCommandBridge`` resolve at load time (Phase C2 review F1).
_smoke_root = Path(tempfile.mkdtemp(prefix="milodex_smoke_"))
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

# PR-7c: RiskProfileBridge registered as QML singleton so Main.qml and
# RiskOfficeDrawer can call RiskProfileBridge.activeProfileName() etc.
# Use a nonexistent db_path — activeProfileName() reads risk_profile.txt
# (defaults to "conservative" if absent), not the DB, so it loads cleanly.
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

_warnings: list[str] = []

engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: _warnings.extend(str(m) for m in msgs))
engine.addImportPath({import_root!r})

# PR-7c Task 37: register AppController as context property so Main.qml's
# onQuitRequested handler can resolve AppController.quitRequested().
from milodex.gui.app import _make_app_controller
_app_ctrl = _make_app_controller([])  # smoke: no real read models to stop
engine.rootContext().setContextProperty("AppController", _app_ctrl)

engine.load(QUrl.fromLocalFile({qml_str!r}))

errors = [w for w in _warnings if w]

if not engine.rootObjects() and not errors:
    print("LOAD_FAILED: no root objects, no warnings — engine bootstrap failed",
          file=sys.stderr)
    sys.exit(2)

if errors:
    for e in errors:
        print(f"QML_WARNING: {{e}}", file=sys.stderr)
    sys.exit(3)

print("LOAD_OK")
sys.exit(0)
"""


# ---------------------------------------------------------------------------
# Helper: run a script and assert clean exit
# ---------------------------------------------------------------------------


def _run_and_assert(script: str, label: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(
            f"QML load failed for {label}\n"
            f"returncode: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("qml_relative", _QML_TARGETS)
def test_surface_qml_loads_clean(qml_relative: str) -> None:
    """Each surface QML compiles and loads with zero engine warnings."""
    qml_path = _MILODEX_QML_DIR / qml_relative
    assert qml_path.exists(), f"QML target missing: {qml_path}"
    _run_and_assert(_build_script(qml_path), qml_relative)


def test_main_qml_loads_clean() -> None:
    """Main.qml (application root) loads with zero engine warnings."""
    qml_path = _MILODEX_QML_DIR / _MAIN_QML
    assert qml_path.exists(), f"Main.qml missing: {qml_path}"
    _run_and_assert(_build_script(qml_path), _MAIN_QML)


def test_trading_desk_singletons_resolve_in_qml() -> None:
    """PR 8: all six Trading Desk read-models resolve as QML singletons.

    A probe QML component imports ``Milodex 1.0`` and binds one
    Q_PROPERTY from each of the six new singletons. If any singleton is
    not registered, QML emits a warning at component creation and the
    subprocess exits non-zero. This is the explicit "6 singletons
    resolve in the QML context" assertion for the DeskSurface rewrite,
    complementary to the full-surface load in
    ``test_surface_qml_loads_clean[surfaces/DeskSurface.qml]``.
    """
    probe_qml = (
        "import QtQuick\n"
        "import Milodex 1.0\n"
        "QtObject {\n"
        "    property var a: PerformanceState.bySlice\n"
        "    property var b: RiskThroughputState.bySlice\n"
        "    property var c: ActiveOpsState.runners\n"
        "    property var d: AttentionState.rollups\n"
        "    property var e: MarketTapeState.rows\n"
        "    property var f: ActivityFeedState.rows\n"
        "    property string g: PerformanceState.dataStatus\n"
        "}\n"
    )
    script = _build_script(_MILODEX_QML_DIR / "surfaces" / "DeskSurface.qml")
    probe_block = (
        "from PySide6.QtCore import QUrl\n"
        "from PySide6.QtQml import QQmlComponent\n"
        f"probe_src = {probe_qml!r}\n"
        "_warnings = []\n"
        "engine = QQmlApplicationEngine()\n"
        "engine.warnings.connect(lambda msgs: _warnings.extend(str(m) for m in msgs))\n"
        f"engine.addImportPath({str(_QML_IMPORT_ROOT)!r})\n"
        "component = QQmlComponent(engine)\n"
        "component.setData(probe_src.encode('utf-8'), QUrl())\n"
        "obj = component.create()\n"
        "errors = [w for w in _warnings if w]\n"
        "if errors:\n"
        "    for e in errors:\n"
        "        print(f'QML_WARNING: {e}', file=sys.stderr)\n"
        "    sys.exit(3)\n"
        "if obj is None:\n"
        "    print('PROBE_CREATE_FAILED: ' + component.errorString(), file=sys.stderr)\n"
        "    sys.exit(4)\n"
        "if obj.property('g') != 'loading':\n"
        "    print(f'UNEXPECTED_DATA_STATUS: {obj.property(\"g\")!r}', file=sys.stderr)\n"
        "    sys.exit(5)\n"
        "print('PROBE_OK')\n"
        "sys.exit(0)\n"
    )
    marker = "_warnings: list[str] = []"
    setup, _sep, _rest = script.partition(marker)
    composed_script = setup + probe_block
    _run_and_assert(composed_script, "Trading Desk 6-singleton probe")


def test_bench_command_bridge_resolves_in_qml() -> None:
    """ADR 0051 Phase C2 review F1 (updated for Bench backtest submit): the QML engine
    must resolve ``Milodex.BenchCommandBridge`` and its slot return must
    round-trip.

    A probe QML component imports ``Milodex 1.0`` and binds
    ``submitCapableActionFamilies()`` to a property. If the singleton is not
    registered, QML emits a warning at component creation; if registration
    succeeded, the property reads the currently submit-capable action
    families. Either failure exits the subprocess non-zero.
    """
    probe_qml = (
        "import QtQuick\n"
        "import Milodex 1.0\n"
        "QtObject {\n"
        "    property var families: BenchCommandBridge.submitCapableActionFamilies()\n"
        "}\n"
    )
    # Reuse the existing _build_script harness to set up the engine, then
    # swap out the load step for a QQmlComponent.setData probe. The simplest
    # way is to write the probe to a tempfile and load it as a normal target.
    script = _build_script(_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml")
    # Replace the engine.load(...) block and trailing exit logic with an
    # inline probe that compiles the QML above and verifies the result.
    probe_block = (
        "from PySide6.QtCore import QUrl\n"
        "from PySide6.QtQml import QQmlComponent\n"
        f"probe_src = {probe_qml!r}\n"
        "_warnings = []\n"
        "engine = QQmlApplicationEngine()\n"
        "engine.warnings.connect(lambda msgs: _warnings.extend(str(m) for m in msgs))\n"
        f"engine.addImportPath({str(_QML_IMPORT_ROOT)!r})\n"
        "component = QQmlComponent(engine)\n"
        "component.setData(probe_src.encode('utf-8'), QUrl())\n"
        "obj = component.create()\n"
        "errors = [w for w in _warnings if w]\n"
        "if errors:\n"
        "    for e in errors:\n"
        "        print(f'QML_WARNING: {e}', file=sys.stderr)\n"
        "    sys.exit(3)\n"
        "if obj is None:\n"
        "    print('PROBE_CREATE_FAILED: ' + component.errorString(), file=sys.stderr)\n"
        "    sys.exit(4)\n"
        "families = obj.property('families')\n"
        "if list(families) != ['demote', 'freeze_manifest', 'backtest', "
        "'promote_to_paper', 'start_paper_runner', 'stop_paper_runner']:\n"
        "    print(f'UNEXPECTED_FAMILIES: {families!r}', file=sys.stderr)\n"
        "    sys.exit(5)\n"
        "print('PROBE_OK')\n"
        "sys.exit(0)\n"
    )
    # Strip everything from the first occurrence of "_warnings: list[str]"
    # in the generated script, then append the probe block. This keeps the
    # full setup (facade, bridge, register_qml_types) intact.
    marker = "_warnings: list[str] = []"
    setup, _sep, _rest = script.partition(marker)
    composed_script = setup + probe_block
    _run_and_assert(composed_script, "BenchCommandBridge singleton probe")


def test_bench_ledger_copy_and_drag_safety_contract() -> None:
    """Bench remains a ledger-table prototype, not a cross-stage board."""
    qml_path = _MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml"
    source = qml_path.read_text(encoding="utf-8")
    main_source = (_MILODEX_QML_DIR / _MAIN_QML).read_text(encoding="utf-8")

    assert "Operator Kanban" not in source
    assert "Milodex · Strategy Bench" in source
    assert (
        'root.activeSurface === "bench")          return "surfaces/BenchSurface.qml"' in main_source
    )
    assert "DropArea" not in source
    assert "Drag." not in source
    assert "targetStage" not in source
    assert re.search(r"\bstage\s=(?!=)", source) is None
    # PR I: Action button replaced with folio mark affordance.
    row_src = (_MILODEX_QML_DIR / "components" / "BenchRow.qml").read_text(encoding="utf-8")
    assert 'text: "Action"' not in row_src
    assert "id: folioMark" in row_src
    # PR I: Flickable click-drag page scroll is disabled — deterministic desktop
    # scrolling means mouse-wheel works, click-drag does not. Guards against a
    # regression that restores interactive scrolling.
    assert "interactive: false" in source
    # PR I: drag-handle exclusion from the row-body click area MUST be geometric
    # (anchors), not z-order. This is the load-bearing safety property: a refactor
    # that collapses rowClickArea back to anchors.fill: parent would silently let
    # handle clicks open the menu. The named id "handleSlot" is stable.
    assert "anchors.left: handleSlot.right" in row_src


def test_bench_menu_engine_contract() -> None:
    """Folio mark affordance still uses the compute_menu_items engine pipeline.

    PR J narrowed the contract: the informational floor item ("Open Evidence",
    verbClass "informational") now emits evidenceRequested via a guarded branch
    in onTriggered. All state-changing verbs (directional / invocation) remain
    no-op per ADR 0049 Decision 2. This test asserts the exact shape of that
    guard so any future addition of a mutation pathway forces a test edit.
    """
    row_src = (_MILODEX_QML_DIR / "components" / "BenchRow.qml").read_text(encoding="utf-8")
    assert "actionItems" in row_src
    assert "QQC2.Menu" in row_src
    assert "Instantiator" in row_src
    assert "modelData.label" in row_src
    # onTriggered must exist and contain the informational-floor guard.
    assert "onTriggered" in row_src
    # The guard must check BOTH verbClass and label before dispatching.
    assert 'modelData.verbClass === "informational"' in row_src, (
        'BenchRow.qml onTriggered must gate on verbClass === "informational"'
    )
    assert 'modelData.label === "Open Evidence"' in row_src, (
        'BenchRow.qml onTriggered must gate on label === "Open Evidence"'
    )
    # PR K: the else branch must emit actionPreviewRequested.
    assert "signal actionPreviewRequested" in row_src, (
        "BenchRow.qml must declare signal actionPreviewRequested (PR K)"
    )
    assert "root.actionPreviewRequested(root.rowData, modelData)" in row_src, (
        "BenchRow.qml onTriggered else branch must emit "
        "actionPreviewRequested(root.rowData, modelData)"
    )
    # State-changing dispatch tokens must not appear anywhere in the file.
    for forbidden in (
        "BenchState.promote",
        "BenchState.demote",
        "BenchState.start",
        "BenchState.stop",
        "BenchState.refresh",
        "BenchState.backtest",
        "BenchState.return",
        "broker.",
        "eventStore.",
        "eventstore.",
        "executeOrder",
        "config.write",
        "submitCommand",
        "dispatchCommand",
    ):
        assert forbidden not in row_src, (
            f"BenchRow.qml must not contain mutation token {forbidden!r} (ADR 0049)"
        )


def test_bench_pr_j_evidence_modal_wiring() -> None:
    """PR J modal wiring contract: one surface-owned BenchEvidenceModal, zero per-row."""
    modal_path = _MILODEX_QML_DIR / "components" / "BenchEvidenceModal.qml"
    assert modal_path.exists(), f"BenchEvidenceModal.qml missing: {modal_path}"

    qmldir_src = (_MILODEX_QML_DIR / "qmldir").read_text(encoding="utf-8")
    assert "BenchEvidenceModal 1.0" in qmldir_src, "qmldir must register BenchEvidenceModal 1.0"

    row_src = (_MILODEX_QML_DIR / "components" / "BenchRow.qml").read_text(encoding="utf-8")
    assert "property var rowData" in row_src, "BenchRow.qml must declare `property var rowData`"
    assert "signal evidenceRequested" in row_src, (
        "BenchRow.qml must declare `signal evidenceRequested`"
    )
    assert "BenchEvidenceModal {" not in row_src, (
        "BenchRow.qml must NOT instantiate BenchEvidenceModal — zero per-row instances"
    )

    surface_src = (_MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml").read_text(encoding="utf-8")
    assert "rowData: modelData" in surface_src, (
        "BenchSurface.qml must pass `rowData: modelData` to the BenchRow delegate"
    )
    assert surface_src.count("BenchEvidenceModal {") == 1, (
        "BenchSurface.qml must contain exactly one BenchEvidenceModal instantiation"
    )
    assert "activeModal" in surface_src, (
        "BenchSurface.qml must declare property activeModal (the modal state enum)"
    )
    assert "evidenceModalRow" in surface_src, (
        "BenchSurface.qml must declare property evidenceModalRow"
    )
    assert "onEvidenceRequested:" in surface_src, (
        "BenchSurface.qml must handle onEvidenceRequested: on the BenchRow delegate"
    )
    # Both modals' onCloseRequested handlers route through closeAllModals().
    assert surface_src.count("onCloseRequested: root.closeAllModals()") >= 2, (
        "BenchSurface.qml must wire onCloseRequested through root.closeAllModals() "
        "in at least 2 places (BenchEvidenceModal and BenchConfirmationModal)"
    )


def test_bench_pr_j_modal_wording_contract() -> None:
    """PR J disclaimer and forbidden-phrase contract.

    Updated in PR 4 (post-DESIGN.md v0.2) to reflect the architecture
    change from centered modal to right-rail dossier. Close-affordance
    paths are split: BenchEvidenceModal forwards a single
    `closeRequested()` signal from the rail; the rail itself
    (RightRailDossier) emits from Escape and the CLOSE button.
    Outside-click dismissal is intentionally absent per the PR 4 brief
    — ordinary Bench interactions must not accidentally close the
    dossier.
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchEvidenceModal.qml").read_text(
        encoding="utf-8"
    )
    rail_src = (_MILODEX_QML_DIR / "components" / "RightRailDossier.qml").read_text(
        encoding="utf-8"
    )

    # Mandatory disclaimer must appear verbatim (now passed via
    # RightRailDossier.footerNote — still must be present in the file).
    disclaimer = (
        "Bench v1 evidence is read-only and sourced from the current GUI "
        "read-model snapshot. Real event-derived freshness and gate "
        "reconstruction are deferred."
    )
    assert disclaimer in modal_src, (
        "BenchEvidenceModal.qml footer disclaimer must match the mandatory verbatim text"
    )

    # Forbidden phrases — would imply authoritative freshness or
    # event-store reconstruction.
    for forbidden in ("is fresh", "currently passing", "current gate result", "reconstructed"):
        assert forbidden not in modal_src, (
            f"BenchEvidenceModal.qml must not contain forbidden phrase {forbidden!r}"
        )

    # BenchEvidenceModal forwards close from the rail (at least 1 path).
    assert modal_src.count("root.closeRequested()") >= 1, (
        "BenchEvidenceModal.qml must forward closeRequested() from the RightRailDossier"
    )

    # RightRailDossier owns the two close paths: Escape + CLOSE button.
    # Outside-click is intentionally absent in PR 4 — verify the rail
    # emits closeRequested from at least 2 places.
    assert rail_src.count("root.closeRequested()") >= 2, (
        "RightRailDossier.qml must emit closeRequested() from at least 2 places "
        "(Escape handler + CLOSE button MouseArea). Outside-click dismissal is "
        "intentionally absent per the PR 4 dossier brief."
    )


def test_bench_pr_j_no_mutation_guarantee() -> None:
    """PR J introduces no backend mutation in BenchRow, BenchSurface, or BenchEvidenceModal."""
    mutation_tokens = (
        "BenchState.promote",
        "BenchState.demote",
        "BenchState.start",
        "BenchState.stop",
        "BenchState.refresh",
        "BenchState.backtest",
        "BenchState.return",
        "broker.",
        "eventStore.",
        "eventstore.",
        "executeOrder",
        "config.write",
        "submitCommand",
        "dispatchCommand",
        "CommandProposal",
    )
    files = {
        "BenchRow.qml": _MILODEX_QML_DIR / "components" / "BenchRow.qml",
        "BenchSurface.qml": _MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml",
        "BenchEvidenceModal.qml": _MILODEX_QML_DIR / "components" / "BenchEvidenceModal.qml",
        "BenchConfirmationModal.qml": (
            _MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml"
        ),
    }
    for filename, path in files.items():
        src = path.read_text(encoding="utf-8")
        for token in mutation_tokens:
            assert token not in src, (
                f"{filename} must not contain mutation token {token!r} (ADR 0049)"
            )


def test_bench_drag_uses_stable_coordinate_mapping() -> None:
    """Drag delta must be computed against a stable parent frame, not row-local mouseY.

    The dragged row's own position changes during drag. If the delta is computed
    from this MouseArea's local `mouseY`, the row's motion feeds back into the
    coordinate frame and the delta oscillates — visible as row jitter and
    delegates overlapping each other on commit. The fix is to map the pointer
    position into a stable parent frame (rowsContainer) via dragHandle.mapToItem,
    which cancels the row's own motion.

    These assertions are deliberately tight:
    - BenchRow.qml must declare `property Item dragCoordinateItem` so the
      stable target is injectable by BenchSurface.
    - BenchRow.qml must contain at least one `dragHandle.mapToItem(` call so
      a refactor cannot silently revert to row-local mouseY.
    - BenchSurface.qml must wire `dragCoordinateItem: rowsContainer` on the
      BenchRow delegate. Pointing this at `root` or the delegate itself would
      reintroduce the feedback loop.
    - The previous bug pattern `_pressMouseY = mouseY` must not return.
    """
    row_src = (_MILODEX_QML_DIR / "components" / "BenchRow.qml").read_text(encoding="utf-8")
    surface_src = (_MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml").read_text(encoding="utf-8")

    assert "property Item dragCoordinateItem" in row_src, (
        "BenchRow.qml must declare property Item dragCoordinateItem"
    )
    assert "dragHandle.mapToItem(" in row_src, (
        "BenchRow.qml drag delta must be computed via dragHandle.mapToItem"
    )
    assert "dragCoordinateItem: rowsContainer" in surface_src, (
        "BenchSurface.qml must wire dragCoordinateItem: rowsContainer on the BenchRow delegate"
    )
    assert "_pressMouseY = mouseY" not in row_src, (
        "BenchRow.qml must not compute press position from row-local mouseY"
    )


def test_bench_stable_column_geometry_contract() -> None:
    """Bench rows and section header MUST share a stable column geometry contract.

    The previous implementation used a per-row `RowLayout` with two
    `Layout.fillWidth: true` participants (strategy + status). After a
    `rowOrder` splice, delegates get rebound to different modelData → different
    text implicitWidths → the layout solver picks different widths → columns
    visibly shift after reorder. The header had its own independent RowLayout,
    so header and rows could diverge from each other too.

    The fix is explicit anchor-based geometry with fixed Theme.column.* widths
    for every column except the strategy block (which fills the residual). The
    same anchor chain runs in both BenchRow.qml row content and BenchSurface.qml
    section header — that is the contract this test enforces.
    """
    row_src = (_MILODEX_QML_DIR / "components" / "BenchRow.qml").read_text(encoding="utf-8")
    surface_src = (_MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml").read_text(encoding="utf-8")

    # Stable column ids must exist in BenchRow — these are the load-bearing
    # anchor targets that future refactors might be tempted to remove.
    for column_id in (
        "id: actionSlot",
        "id: statusCol",
        "id: tradesText",
        "id: maxDDText",
        "id: sharpeText",
        "id: strategyCol",
    ):
        assert column_id in row_src, (
            f"BenchRow.qml must declare {column_id!r} for the stable column chain"
        )

    # BenchRow row content must not reintroduce a top-level RowLayout. The
    # status-prose Column still contains a small RowLayout for the inline
    # signal-word + tail rendering — that's contained and not the column grid.
    # Forbid the specific token used by the old top-level layout instead.
    assert "id: rowLayout" not in row_src, (
        "BenchRow.qml must not contain the old top-level `id: rowLayout` RowLayout"
    )

    # Right-anchored chain must be visibly present (rightmost-first).
    # If a refactor breaks the chain, one of these substrings will be missing.
    assert "anchors.right: actionSlot.left" in row_src
    assert "anchors.right: statusCol.left" in row_src
    assert "anchors.right: tradesText.left" in row_src
    assert "anchors.right: maxDDText.left" in row_src
    assert "anchors.right: sharpeText.left" in row_src

    # Shared geometry contract: every fixed-width column reads from
    # Theme.column.* in both files. The header must reference the same tokens.
    assert "Theme.column.benchStatus" in row_src, (
        "BenchRow.qml status column must use Theme.column.benchStatus"
    )
    assert "Theme.column.benchStatus" in surface_src, (
        "BenchSurface.qml header status column must use Theme.column.benchStatus"
    )
    for token in (
        "Theme.column.benchMetric",
        "Theme.column.benchAction",
    ):
        assert token in row_src, f"BenchRow.qml must reference {token}"
        assert token in surface_src, f"BenchSurface.qml header must reference {token}"

    # Header must use the same right-anchored chain pattern. The header
    # ColHeader ids must form a chain anchored to the right; if either the
    # ids or the chain disappear, the header could diverge from rows again.
    for header_id in (
        "id: headerAction",
        "id: headerStatus",
        "id: headerTrades",
        "id: headerMaxDD",
        "id: headerSharpe",
    ):
        assert header_id in surface_src, f"BenchSurface.qml header must declare {header_id!r}"
    assert "anchors.right: headerAction.left" in surface_src
    assert "anchors.right: headerStatus.left" in surface_src
    assert "anchors.right: headerTrades.left" in surface_src
    assert "anchors.right: headerMaxDD.left" in surface_src
    assert "anchors.right: headerSharpe.left" in surface_src


def test_bench_dragging_branch_precedes_live_branch() -> None:
    """Dragged rows — LIVE included — must paint opaque, not as a 5% oxblood wash.

    The row background color block must check `root.dragging` before `_isLive`
    so the opaque surface.raised branch wins for dragged LIVE rows. If `_isLive`
    appears first, dragged LIVE rows render at 5% alpha and neighbors ghost
    through the paper strip — violating the PR I drag-visual contract.

    This is a static ordering guard, not a runtime check. The two distinctive
    tokens we look for are the literal `if (root.dragging)` open and the
    `if (_isLive)` open in BenchRow.qml. Both appear only inside this color
    block, so simple index comparison is sufficient.
    """
    row_src = (_MILODEX_QML_DIR / "components" / "BenchRow.qml").read_text(encoding="utf-8")
    dragging_idx = row_src.find("if (root.dragging)")
    is_live_idx = row_src.find("if (_isLive)")
    assert dragging_idx != -1, "BenchRow.qml must contain `if (root.dragging)` branch"
    assert is_live_idx != -1, "BenchRow.qml must contain `if (_isLive)` branch"
    assert dragging_idx < is_live_idx, (
        "BenchRow.qml row-background color block must check root.dragging "
        "before _isLive — otherwise dragged LIVE rows render at 5% alpha and "
        "neighbors ghost through the paper strip."
    )


def test_bench_pr_h_drag_safety_contract() -> None:
    """PR H static guards: Y-only drag, no cross-stage drop surface, no backend mutation.

    These assertions enforce the hard constraints from the PR H brief:
    - No DropArea in either BenchRow or BenchSurface (within-section drag uses
      explicit Y positioning, not a DropArea-based drop target).
    - Drag is Y-axis only: Drag.XAxis and Drag.XAndYAxis are forbidden.
    - No backend mutation tokens: BenchState.promote / BenchState.demote must
      not appear in either QML file.
    - BenchSurface does not reference Drag. at all (drag wiring lives in BenchRow).
    - BenchRow uses cursor-delta tracking (no drag.target, no Drag.YAxis) so the
      dragged row's y remains fully declarative via BenchSurface's binding.
    """
    bench_surface = _MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml"
    bench_row = _MILODEX_QML_DIR / "components" / "BenchRow.qml"

    surface_src = bench_surface.read_text(encoding="utf-8")
    row_src = bench_row.read_text(encoding="utf-8")

    # No DropArea in either file — within-section drag uses explicit Y positioning.
    assert "DropArea" not in surface_src, "BenchSurface.qml must not contain DropArea"
    assert "DropArea" not in row_src, "BenchRow.qml must not contain DropArea"

    # Drag axis — X or XAndY axes are forbidden; only Y is permitted.
    assert "Drag.XAxis" not in surface_src, "BenchSurface.qml must not use Drag.XAxis"
    assert "Drag.XAxis" not in row_src, "BenchRow.qml must not use Drag.XAxis"
    assert "Drag.XAndYAxis" not in surface_src, "BenchSurface.qml must not use Drag.XAndYAxis"
    assert "Drag.XAndYAxis" not in row_src, "BenchRow.qml must not use Drag.XAndYAxis"

    # BenchSurface must not reference Drag. at all (drag wiring is in BenchRow only).
    assert "Drag." not in surface_src, "BenchSurface.qml must not reference Drag."

    # BenchRow uses cursor-delta tracking, not Qt's drag.target mechanism.
    # This keeps the dragged row's position fully declarative via the y binding
    # in BenchSurface, avoiding the binding-break that drag.target would cause.
    # Check for the QML property assignment form (drag.target:) not bare mentions
    # in comments, which legitimately reference drag.target to explain why it's absent.
    assert "drag.target:" not in row_src, (
        "BenchRow.qml must not use drag.target — cursor-delta tracking only"
    )
    assert "Drag.YAxis" not in row_src, (
        "BenchRow.qml must not use Drag.YAxis — Qt's built-in drag mechanism is "
        "incompatible with declarative y binding"
    )

    # No backend mutation calls in either file.
    assert "BenchState.promote" not in surface_src, (
        "BenchSurface.qml must not call BenchState.promote"
    )
    assert "BenchState.demote" not in surface_src, (
        "BenchSurface.qml must not call BenchState.demote"
    )
    assert "BenchState.promote" not in row_src, "BenchRow.qml must not call BenchState.promote"
    assert "BenchState.demote" not in row_src, "BenchRow.qml must not call BenchState.demote"


def test_bench_pr_k_confirmation_modal_wiring() -> None:
    """PR K modal wiring contract: one surface-owned BenchConfirmationModal, zero per-row."""
    modal_path = _MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml"
    assert modal_path.exists(), f"BenchConfirmationModal.qml missing: {modal_path}"

    qmldir_src = (_MILODEX_QML_DIR / "qmldir").read_text(encoding="utf-8")
    assert "BenchConfirmationModal 1.0" in qmldir_src, (
        "qmldir must register BenchConfirmationModal 1.0"
    )

    row_src = (_MILODEX_QML_DIR / "components" / "BenchRow.qml").read_text(encoding="utf-8")
    assert "signal actionPreviewRequested" in row_src, (
        "BenchRow.qml must declare signal actionPreviewRequested"
    )
    assert "BenchConfirmationModal {" not in row_src, (
        "BenchRow.qml must NOT instantiate BenchConfirmationModal — zero per-row instances"
    )

    surface_src = (_MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml").read_text(encoding="utf-8")
    assert surface_src.count("BenchConfirmationModal {") == 1, (
        "BenchSurface.qml must contain exactly one BenchConfirmationModal instantiation"
    )
    assert 'activeModal === "confirmation"' in surface_src, (
        'BenchSurface.qml must gate BenchConfirmationModal.open on activeModal === "confirmation"'
    )
    assert "confirmationPreviewRow" in surface_src, (
        "BenchSurface.qml must declare property confirmationPreviewRow"
    )
    assert "confirmationPreviewAction" in surface_src, (
        "BenchSurface.qml must declare property confirmationPreviewAction"
    )
    assert "onActionPreviewRequested:" in surface_src, (
        "BenchSurface.qml must handle onActionPreviewRequested: on the BenchRow delegate"
    )
    # Mutual exclusion is now structural: activeModal is single-valued, so the
    # legacy count-based guards (`confirmationPreviewOpen = false` count >= 2,
    # `evidenceModalOpen` count >= 2) are redundant and have been removed.


def test_bench_pr_k_modal_wording_contract() -> None:
    """PR K confirmation modal disclaimer and forbidden-phrase contract."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    # Mandatory verbatim copy strings — single-line literals in the source.
    assert (
        "This preview shows the confirmation Milodex will require before changing a "
        "strategy's Bench stage. Command execution is not wired in Bench v1."
    ) in modal_src, "BenchConfirmationModal.qml _COPY_DIRECTIONAL must match verbatim"

    assert (
        "This confirmation sends operational process requests through the Bench "
        "command bridge. Paper runner start and controlled stop are validated "
        "again before submit."
    ) in modal_src, "BenchConfirmationModal.qml _COPY_INVOCATION must match verbatim"

    assert (
        "Capital-bearing transitions remain locked while ADR 0004 is in force. "
        "This modal is a visual shell only."
    ) in modal_src, "BenchConfirmationModal.qml _COPY_CAPITAL_LOCK must match verbatim"

    assert "Not wired in v1" in modal_src, (
        "BenchConfirmationModal.qml disabled primary action must be labelled 'Not wired in v1'"
    )
    assert "visual shell only" in modal_src, (
        "BenchConfirmationModal.qml must contain 'visual shell only' scope statement"
    )


def test_bench_modal_surfaces_all_blockers() -> None:
    """A blocked proposal must surface EVERY blocker, not just the first.

    Regression for the 2026-05-29 "nothing happened" report: start_paper_runner
    was refused with two blockers (broker_unreachable + reconciliation_drift) but
    the modal showed only blockers[0].message. The fix routes all blocker text
    through _blockerSummary(); the single-blocker surfacing path must be gone.
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    assert "_blockerSummary" in modal_src, (
        "BenchConfirmationModal.qml must format blocked proposals via _blockerSummary "
        "(surfaces all blockers, not just the first)"
    )
    assert "blockers[0].message" not in modal_src, (
        "BenchConfirmationModal.qml must not surface only blockers[0]; route blocker "
        "text through _blockerSummary so every blocker reason is shown"
    )
    # The refusal must read as a hard refusal, not a soft hint.
    assert "Blocked — not submitted:" in modal_src, (
        "BenchConfirmationModal.qml blocker summary must frame the state as a refusal"
    )

    # Forbidden phrases — would imply live command dispatch.
    for forbidden in (
        "will promote",
        "will demote",
        "will start",
        "will stop",
        "will initiate",
        "command sent",
        "executing",
        "in progress",
    ):
        assert forbidden not in modal_src, (
            f"BenchConfirmationModal.qml must not contain forbidden phrase {forbidden!r} "
            "(ADR 0049 Decision 2)"
        )


def test_bench_pr_k_bleed_through_guards() -> None:
    """PR K bleed-through guards: KeyHandler and WheelHandler both gate on both modals.

    When either modal is open, keyboard scroll and mouse-wheel scroll must be
    suppressed in the Bench Flickable. The exact OR expression in both handlers
    is asserted so a refactor that drops one modal from the guard fails fast.
    """
    surface_src = (_MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml").read_text(encoding="utf-8")

    guard = 'root.activeModal !== "none"'
    assert surface_src.count(guard) >= 2, (
        f"BenchSurface.qml must contain the bleed-through guard "
        f'"{guard}" in at least 2 places (Keys.onPressed and WheelHandler.onWheel)'
    )


# PR 13 re-aim: the former test_bench_pr_l_intent_packet_sections (a cosmetic
# source-substring loop over the six ALL-CAPS section labels) was CONVERTED to
# a behavioral check. test_modal_renders_labelled_sections in
# tests/milodex/gui/test_bench_confirmation_modal_behavior.py drives an
# instantiated modal and asserts the EXACT label strings render in the live
# item tree (all seven, incl. COMMAND DRAFT PREVIEW) — a mislabel now fails
# behaviorally, so the brittle raw-source pin is removed.


def test_bench_safety_and_capital_copy_is_python_owned() -> None:
    """P2-12: the safety-boundary / capital-lock / paper-start operator copy
    has ONE owner — bench_actions.py. The modal renders the preview's
    pre-rendered ``safetyCopy`` verbatim and carries no fallback copy of its
    own.

    Supersedes the former PR L pins (test_bench_pr_l_safety_boundary_wording,
    test_bench_pr_l_capital_live_precision, test_bench_pr_l_future_record_
    strings): the QML fallback tables those tests grepped were removed; the
    copy and the kind→record / capital-bearing classifications are pinned at
    the Python owner here and in tests/milodex/gui/test_read_models.py
    (PR N block: kind classification, futureRecord strings, capital-bearing
    paper-start refinement, safetyCopy content).
    """
    from milodex.gui.bench_actions import (
        _COPY_CAPITAL_LOCK_SHORT,
        _COPY_PAPER_START,
        _COPY_SAFETY_BOUNDARY,
    )

    # The operator-facing strings are the product — pinned verbatim at the owner.
    assert _COPY_SAFETY_BOUNDARY == (
        "Bench renders this intent packet for review before any submit-capable action "
        "is validated through the command bridge."
    )
    assert _COPY_CAPITAL_LOCK_SHORT == (
        "Capital-bearing transitions remain locked while ADR 0004 is in force."
    )
    assert _COPY_PAPER_START == (
        "Paper-stage sessions use live feed with no capital exposure. "
        "Capital-bearing stages remain locked while ADR 0004 is in force."
    )

    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )
    # The modal renders the Python-owned copy via the preview...
    assert "_preview.safetyCopy" in modal_src, (
        "BenchConfirmationModal.qml must render the preview's safetyCopy (P2-12)"
    )
    # ...and must not regrow a local fallback copy of the boundary sentence.
    assert "Capital-bearing stages remain locked" not in modal_src, (
        "BenchConfirmationModal.qml must not duplicate the Python-owned safety copy (P2-12)"
    )


# ---------------------------------------------------------------------------
# PR M (ADR 0049): Evidence Packet read-model contract — QML consumption
# ---------------------------------------------------------------------------


def test_bench_pr_m_evidence_modal_reads_packet() -> None:
    """BenchEvidenceModal must prefer rowData.evidencePacket fields (PR M)."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchEvidenceModal.qml").read_text(
        encoding="utf-8"
    )

    # _packet helper is declared and reads from rowData.evidencePacket.
    assert "rowData.evidencePacket" in modal_src, (
        "BenchEvidenceModal.qml must read rowData.evidencePacket (PR M)"
    )
    for helper in (
        "_packet",
        "_pktMetrics",
        "_pktEvidence",
        "_pktGate",
        "_pktStatus",
        "_pktSession",
        "_pktJob",
        "_pktSource",
    ):
        assert helper in modal_src, (
            f"BenchEvidenceModal.qml must declare {helper!r} packet helper (PR M)"
        )


def test_bench_pr_m_confirmation_modal_reads_packet() -> None:
    """BenchConfirmationModal Current Snapshot must read evidencePacket (PR M)."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    assert "rowData.evidencePacket" in modal_src, (
        "BenchConfirmationModal.qml must read rowData.evidencePacket (PR M)"
    )
    for helper in ("_packet", "_pktMetrics", "_pktEvidence", "_pktStatus"):
        assert helper in modal_src, (
            f"BenchConfirmationModal.qml must declare {helper!r} packet helper (PR M)"
        )


def test_bench_pr_m_no_authoritative_freshness_claims() -> None:
    """PR M must NOT claim authoritative freshness/gate reconstruction in QML."""
    forbidden_phrases = (
        # The packet exposes a non-authoritative sentinel; the QML must surface
        # the sentinel verbatim and never invent reconstructed verdicts.
        # Phrases here are claim-shaped — existing negative framings like
        # "never mistakes this snapshot for an authoritative gate result"
        # are deliberately not matched.
        "freshness reconstructed",
        "gate reconstructed",
        "freshness: pass",
        "gate: pass",
        "gate verdict pass",
    )
    for filename in ("BenchEvidenceModal.qml", "BenchConfirmationModal.qml"):
        src = (_MILODEX_QML_DIR / "components" / filename).read_text(encoding="utf-8")
        for phrase in forbidden_phrases:
            assert phrase.lower() not in src.lower(), (
                f"{filename} must not contain authoritative-claim phrase {phrase!r}"
            )
    # The non-reconstructed sentinel must appear somewhere on the Evidence
    # modal so operators see the explicit deferral.
    evidence_src = (_MILODEX_QML_DIR / "components" / "BenchEvidenceModal.qml").read_text(
        encoding="utf-8"
    )
    assert "_pktGate.freshness" in evidence_src and "_pktGate.gateResult" in evidence_src, (
        "BenchEvidenceModal.qml must render the packet's freshness/gateResult sentinels"
    )


# ---------------------------------------------------------------------------
# PR N (ADR 0049): Action Intent Preview read-model contract — QML consumption
# ---------------------------------------------------------------------------


def test_bench_pr_n_confirmation_modal_prefers_action_preview() -> None:
    """BenchConfirmationModal must read actionData.actionIntentPreview (PR N).

    P2-12 tightened the contract: the preview is the ONLY source — Python
    guarantees it on every menu item, the QML fallback classifiers were
    removed, and every per-kind field binds straight off ``_preview``.
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )
    assert "actionIntentPreview" in modal_src, (
        "BenchConfirmationModal.qml must reference actionIntentPreview (PR N)"
    )

    # Every per-kind field must bind off the normalized preview.
    for needle in (
        "_preview.actionKind",
        "_preview.intentCopy",
        "_preview.requirements",
        "_preview.futureRecord",
        "_preview.safetyCopy",
        "_preview.capitalBearing",
        "_preview.executable",
    ):
        assert needle in modal_src, f"BenchConfirmationModal.qml must read {needle!r} (P2-12)"

    # The removed fallback classifiers must not regrow (P2-12).
    for forbidden in (
        "function _actionKind(",
        "function _intentCopy(",
        "function _futureRecord(",
        "function _safetyCopy(",
        "_submitCapableKinds",
    ):
        assert forbidden not in modal_src, (
            f"BenchConfirmationModal.qml must not redeclare {forbidden!r} — the "
            "action-kind spec is Python-owned (bench_actions.ACTION_KIND_SPECS, P2-12)"
        )


def test_bench_pr_n_no_executable_or_wired_truth_in_qml() -> None:
    """PR N preview MUST stay non-executable; QML must not invent executable=true."""
    forbidden = (
        "executable: true",
        "wired: true",
        "preview.executable === true",
        "preview.wired === true",
    )
    for filename in ("BenchRow.qml", "BenchConfirmationModal.qml", "BenchEvidenceModal.qml"):
        src = (_MILODEX_QML_DIR / "components" / filename).read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in src, (
                f"{filename} must not contain {phrase!r} (PR N: previews stay non-executable)"
            )


def test_bench_pr_o_command_draft_preview_section_present() -> None:
    """PR O: confirmation modal renders a COMMAND DRAFT PREVIEW section."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )
    assert "COMMAND DRAFT PREVIEW" in modal_src, (
        "BenchConfirmationModal.qml must render the COMMAND DRAFT PREVIEW section (PR O)"
    )
    assert "commandDraftPreview" in modal_src, (
        "BenchConfirmationModal.qml must declare commandDraftPreview (PR O)"
    )


def test_bench_pr_o_command_draft_preview_shape() -> None:
    """commandDraftPreview must carry proposal-state fields."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )
    for needle in (
        '"schemaVersion": 1',
        '"source":',
        '"submissionState": root._isSubmitCapable ? "submit_capable" : "not_submittable_v1"',
        '"validationState": root._isSubmitCapable ? "validated_on_submit" : "not_validated_v1"',
        '"blockedBy":',
        '"executable": root._isSubmitCapable',
        '"wired": root._isSubmitCapable',
    ):
        assert needle in modal_src, f"commandDraftPreview must declare {needle!r} (PR O)"


def test_bench_pr_o_command_draft_preview_source_copy() -> None:
    """PR O: source.note must include the read-only boundary phrases."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )
    # Single-line literals so grep matches substring-exactly.
    assert '"kind": "local_ui_draft_preview"' in modal_src, (
        "commandDraftPreview.source.kind must be 'local_ui_draft_preview' (PR O)"
    )
    for phrase in (
        "No command is submitted",
        "no event is written",
        "no state is changed",
    ):
        assert phrase in modal_src, (
            f"commandDraftPreview source note must contain {phrase!r} (PR O)"
        )


def test_bench_pr_o_command_draft_preview_ui_copy() -> None:
    """PR O: visible UI copy must include literal state strings and banner."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )
    assert "not_submittable_v1" in modal_src, (
        "commandDraftPreview must surface the not_submittable_v1 sentinel (PR O)"
    )
    assert "not_validated_v1" in modal_src, (
        "commandDraftPreview must surface the not_validated_v1 sentinel (PR O)"
    )
    assert (
        "Milodex can render this draft for review, but Bench v1 cannot submit it." in modal_src
    ), "commandDraftPreview banner copy must be verbatim (PR O)"
    assert (
        "Milodex will validate this proposal through the command bridge before submitting it."
        in modal_src
    ), "submit-capable command preview banner copy must be present (PR 13)"


def test_bench_pr_p_boundary_doc_exists_and_anchors_to_code() -> None:
    """PR P: docs/BENCH_BOUNDARY.md must exist and cite the load-bearing artifacts.

    The doc is a human-facing layer over ADR 0049 Decision 2 + the data
    shapes added in PRs M / N / O. It explains the three-layer read-only
    chain (Evidence Packet → Action Intent Preview → Command Draft Preview)
    and the invariants that keep each layer non-executable.

    This test does NOT police prose. It only verifies the doc references
    the binding artifacts so a future contributor cannot silently delete
    the doc and quietly weaken the boundary. If you rename a referenced
    artifact, update the doc — both must move together.
    """
    repo_root = Path(__file__).resolve().parents[3]
    doc_path = repo_root / "docs" / "BENCH_BOUNDARY.md"
    assert doc_path.exists(), (
        "docs/BENCH_BOUNDARY.md must exist — it is the human-facing layer "
        "over ADR 0049 Decision 2 + the PR M/N/O data shapes"
    )

    src = doc_path.read_text(encoding="utf-8")

    # The doc must name the three layers by their property names so a
    # rename refactor cannot leave the doc pointing at the wrong object.
    required_anchors = (
        "evidencePacket",
        "actionIntentPreview",
        "commandDraftPreview",
        "ADR 0049",
        "read_models.py",
        "BenchConfirmationModal.qml",
        "test_qml_load_smoke.py",
        "test_read_models.py",
    )
    for needle in required_anchors:
        assert needle in src, (
            f"docs/BENCH_BOUNDARY.md must reference {needle!r} — "
            "the doc and the code must stay in lockstep"
        )

    # The doc must reproduce the load-bearing invariant strings verbatim
    # so a silent weakening of the contract trips a test, not just a
    # surprised reader.
    invariants = (
        "not_reconstructed_v1",
        "not_submittable_v1",
        "not_validated_v1",
        "local_ui_draft_preview",
        "gui_read_model_snapshot",
        "gui_read_model_preview",
        "Milodex can render this draft for review, but Bench v1 cannot submit it.",
    )
    for needle in invariants:
        assert needle in src, f"docs/BENCH_BOUNDARY.md must quote the invariant {needle!r} verbatim"

    # The doc must explicitly state that wiring real commands requires a
    # new ADR — this is the escalation rail the boundary depends on.
    assert "new ADR" in src or "separate ADR" in src, (
        "docs/BENCH_BOUNDARY.md must require a new/separate ADR for any "
        "move from preview to submit (escalation rail per ADR 0049)"
    )


# ADR 0051 §9 — narrow allowlist of files permitted to declare command
# infrastructure. Every entry must correspond to a file ADR 0051 explicitly
# names. Widening this list silently is exactly the failure mode the
# perimeter exists to prevent; a new entry must arrive with the action-family
# wiring PR that needs it.
_ADR_0051_COMMAND_INFRA_ALLOWLIST: frozenset[str] = frozenset(
    {
        # The Python facade itself.
        "src/milodex/commands/bench.py",
        # The package __init__ re-exports the facade dataclasses.
        "src/milodex/commands/__init__.py",
        # The Qt bridge over the facade. This is the single GUI adapter for
        # all submit-capable Bench action families; widening the perimeter
        # beyond this file remains the failure mode the tests prevent.
        "src/milodex/gui/bench_command_bridge.py",
    }
)


def test_bench_pr_p_python_has_no_command_infrastructure() -> None:
    """PR P: no Python file may define a CommandProposal class or submit/dispatch entry point.

    A grep-level guard: if any future PR adds `class CommandProposal`, a
    `submit_command` function, or a `dispatch_command` function anywhere in
    the Python codebase outside the ADR 0051 allowlist, this test fails and
    forces the contributor to open a new ADR per the BENCH_BOUNDARY.md
    escalation rail.

    ADR 0051 amends this perimeter narrowly: ``CommandProposal``,
    ``CommandResult``, ``Blocker``, ``BenchCommandFacade`` are permitted in
    the files named in ``_ADR_0051_COMMAND_INFRA_ALLOWLIST``. Submit-shaped
    dispatch functions (``submit_command``, ``dispatch_command``,
    ``execute_command``) remain forbidden everywhere — the facade names its
    submit methods per action family (``submit_backtest``, …) which is the
    contract wired action families use.
    """
    repo_root = Path(__file__).resolve().parents[3]
    src_root = repo_root / "src" / "milodex"

    forbidden_python_patterns = (
        re.compile(r"\bclass\s+CommandProposal\b"),
        re.compile(r"\bdef\s+submit_command\b"),
        re.compile(r"\bdef\s+dispatch_command\b"),
        re.compile(r"\bdef\s+execute_command\b"),
    )

    for py_path in src_root.rglob("*.py"):
        rel_posix = py_path.relative_to(repo_root).as_posix()
        if rel_posix in _ADR_0051_COMMAND_INFRA_ALLOWLIST:
            continue
        text = py_path.read_text(encoding="utf-8")
        for pattern in forbidden_python_patterns:
            match = pattern.search(text)
            assert match is None, (
                f"{py_path.relative_to(repo_root)} declares {match.group(0)!r} — "
                "ADR 0049 Decision 2 / ADR 0051 §9 forbid Python command "
                "infrastructure outside the allowlist. Either open a new ADR "
                "or add the file to _ADR_0051_COMMAND_INFRA_ALLOWLIST in the "
                "same PR that wires the corresponding action family."
            )


def test_bench_pr_c2_modal_demote_submit_affordance() -> None:
    """ADR 0051 Phase C2: BenchConfirmationModal carries an action-aware
    submit affordance for the demote action family only.

    Pins:

    * the verbatim "Not wired in v1" inert primary remains in source
      (it renders for every non-demote action family);
    * a "Confirm demotion" submit affordance is wired alongside;
    * the submit MouseArea calls into the BenchCommandBridge, not directly
      into the facade, broker, runner, or event store;
    * the reason input is present and required;
    * the modal still rejects the literal mutation tokens forbidden by
      ADR 0049 (covered by test_bench_pr_n_no_mutation_token_drift; this
      test only adds the C2-specific positive assertions).
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    # Inert "Not wired in v1" still present — non-demote actions still see it.
    assert "Not wired in v1" in modal_src

    # Submit affordance — demote-only. The "Confirm demotion" submit-button
    # label was CONVERTED to a behavioral check (burn backlog C2 batch 4):
    # tests/milodex/gui/test_bench_confirmation_modal_behavior.py::
    # test_submit_capable_family_renders_its_label drives a demote action and
    # asserts the label renders. The socket-method + capability pins stay here.
    assert "_isSubmitCapable" in modal_src
    assert "BenchCommandBridge.proposeDemote(" in modal_src
    assert "BenchCommandBridge.submitDemote(" in modal_src

    # Reason input is wired and gates the submit button. The placeholder
    # copy ("Reason required for the audit record") was CONVERTED to a
    # behavioral check (PR 13 re-aim): behavioral coverage in
    # tests/milodex/gui/test_bench_confirmation_modal_behavior.py::
    # test_stage_walkback_blank_reason_refuses_without_proposing asserts the
    # placeholder renders in the live tree AND that a blank reason refuses
    # without proposing. The structural property pin stays here.
    assert "_reasonText" in modal_src

    # The submit MouseArea only routes through the bridge — no facade,
    # event-store, broker, runner, or config references in the modal.
    forbidden_direct = (
        "from milodex.commands",
        "BenchCommandFacade",
        "EventStore.",
        "AlpacaBrokerClient",
        "StrategyRunner",
        "promotion.state_machine",
    )
    for token in forbidden_direct:
        assert token not in modal_src, (
            f"BenchConfirmationModal.qml must not contain {token!r} — the "
            "bridge is the only command boundary (ADR 0051 §5)."
        )


def test_bench_pr_d1_modal_freeze_manifest_submit_affordance() -> None:
    """ADR 0051 Phase D1: BenchConfirmationModal carries an action-aware
    submit affordance for the freeze_manifest action family alongside the
    Phase C2 demote affordance.

    Pins:

    * the verbatim "Not wired in v1" inert primary remains in source (it
      still renders for every non-demote / non-freeze action family);
    * a "Confirm freeze" submit affordance label is present;
    * the submit dispatch routes through BenchCommandBridge slots only;
    * the modal still rejects the literal mutation tokens forbidden by
      ADR 0049 (covered by test_bench_pr_n_no_mutation_token_drift).
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    assert "Not wired in v1" in modal_src
    # "Confirm freeze" submit-button label CONVERTED to behavioral (C2 batch 4):
    # test_bench_confirmation_modal_behavior.py::
    # test_submit_capable_family_renders_its_label. Socket-method pins stay.
    assert "_isFreezeManifestSubmit" in modal_src
    assert "BenchCommandBridge.proposeFreezeManifest(" in modal_src
    assert "BenchCommandBridge.submitFreezeManifest(" in modal_src

    # Forbidden direct paths: same set as the C2 affordance pin.
    forbidden_direct = (
        "from milodex.commands",
        "BenchCommandFacade",
        "EventStore.",
        "AlpacaBrokerClient",
        "StrategyRunner",
        "promotion.state_machine",
        "promotion.manifest",
    )
    for token in forbidden_direct:
        assert token not in modal_src, (
            f"BenchConfirmationModal.qml must not contain {token!r} — the "
            "bridge is the only command boundary (ADR 0051 §5)."
        )


def test_bench_pr13_modal_backtest_submit_affordance() -> None:
    """PR 13: backtest evidence actions route through the command bridge.

    DOCTRINE / SOCKET CONTRACT (kept as verbatim text): the bridge method
    names proposeBacktest / submitBacktestAsync and the submitBacktest-
    without-Async negation are the socket contract and stay pinned here.

    P2-12: the canonical walk-forward params are Python-owned
    (CANONICAL_BACKTEST_PARAMS in bench_command_bridge.py — pinned in
    tests/milodex/gui/test_bench_command_bridge.py). QML submits only the
    strategy id, so the param literals must NOT reappear in the modal.
    Behavioral coverage in
    tests/milodex/gui/test_bench_confirmation_modal_behavior.py:
    test_backtest_submit_proposes_strategy_id_only drives a backtest submit
    and asserts the proposed payload is exactly {"strategy_id": ...}. The
    intent copy renders via the Python-owned preview
    (test_modal_renders_labelled_sections).
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    # "Run backtest" submit-button label is covered behaviorally by
    # test_bench_confirmation_modal_behavior.py::
    # test_submit_capable_action_shows_submit_affordance (+ the family-label
    # parametrization). The socket-method + P2-12 forbid pins stay here.
    assert "_isBacktestSubmit" in modal_src
    assert "BenchCommandBridge.proposeBacktest(" in modal_src
    assert "BenchCommandBridge.submitBacktestAsync(" in modal_src
    assert "BenchCommandBridge.submitBacktest(" not in modal_src
    # P2-12: no local canonical-param table in QML.
    for forbidden in ('"initial_equity"', "_canonicalBacktestParams", "2020-01-01"):
        assert forbidden not in modal_src, (
            f"BenchConfirmationModal.qml must not carry {forbidden!r} — canonical "
            "backtest params are Python-owned (CANONICAL_BACKTEST_PARAMS, P2-12)"
        )


def test_bench_modal_long_running_submits_use_async_bridge_slots() -> None:
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    assert "BenchCommandBridge.submitBacktestAsync(" in modal_src
    assert "BenchCommandBridge.submitStartPaperRunnerAsync(" in modal_src
    assert "BenchCommandBridge.submitStopPaperRunnerAsync(" in modal_src
    assert "BenchCommandBridge.submitBacktest(" not in modal_src
    assert "BenchCommandBridge.submitStartPaperRunner(" not in modal_src
    assert "BenchCommandBridge.submitStopPaperRunner(" not in modal_src
    assert "_handleAsyncSubmitCompleted" in modal_src
    assert "onSubmitCompleted" in modal_src  # declarative Connections{} handler
    assert "_pendingProposalId" in modal_src


def test_bench_pr14_modal_promote_to_paper_submit_affordance() -> None:
    """PR 14: Promote to Paper routes through the command bridge."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    # "Confirm promotion" submit-button label CONVERTED to behavioral (C2 batch
    # 4): test_bench_confirmation_modal_behavior.py::
    # test_submit_capable_family_renders_its_label. Socket + payload pins stay.
    assert "_isPromoteToPaperSubmit" in modal_src
    assert "BenchCommandBridge.proposePromoteToPaper(" in modal_src
    assert "BenchCommandBridge.submitPromoteToPaper(" in modal_src
    assert '"recommendation": recommendation' in modal_src
    assert '"known_risk": knownRisk' in modal_src
    assert '"run_id": runId' in modal_src
    assert '"lifecycle_exempt": false' in modal_src
    start = modal_src.find("function _dispatchPromoteToPaperSubmit")
    assert start != -1
    end = modal_src.find("function _dispatchSubmit", start)
    assert end != -1
    assert '"approved_by"' not in modal_src[start:end]
    # The two operator-evidence placeholders ("Recommendation required",
    # "Known risk required") were CONVERTED to behavioral checks (PR 13
    # re-aim): behavioral coverage in
    # tests/milodex/gui/test_bench_confirmation_modal_behavior.py::
    # test_promote_to_paper_blank_evidence_refuses asserts both placeholders
    # render in the live tree AND that blank evidence refuses without
    # proposing. The socket-method + payload-shape pins stay here.


def test_bench_modal_promote_to_paper_prefills_operator_evidence() -> None:
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    assert "function _defaultRecommendationText()" in modal_src
    assert "function _defaultKnownRiskText()" in modal_src
    assert "root._recommendationText = root._defaultRecommendationText()" in modal_src
    assert "root._knownRiskText = root._defaultKnownRiskText()" in modal_src
    assert "Promote to paper from passing Bench backtest evidence." in modal_src


def test_bench_pr_d1_other_action_families_remain_not_wired() -> None:
    """ADR 0051: demote, freeze_manifest, backtest, and Promote to Paper are
    submit-capable. The modal must still render the inert "Not wired in v1"
    primary for every other action family.

    P2-12: the submit-capable predicate is no longer a QML kind table — it
    binds off the Python-owned preview flag (``_preview.executable``, derived
    from bench_actions.ACTION_KIND_SPECS). The behavioral proof that
    submit-capable actions show the submit affordance and non-capable ones
    show the inert placeholder lives in
    tests/milodex/gui/test_bench_confirmation_modal_behavior.py.
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    # Submit-capability must come from the Python spec via the preview, not
    # from a local QML kind table.
    assert "readonly property bool _isSubmitCapable: !!_preview.executable" in modal_src, (
        "The submit-capable predicate must bind off the Python-owned preview "
        "flag (P2-12). Other action families remain preview-only."
    )
    assert "_isReturnToIdleSubmit" in modal_src
    assert "_isPromoteToPaperSubmit" in modal_src

    # The inert primary block must be visible-gated to the !_isSubmitCapable
    # branch so promote / return / start / stop / initiate / refresh actions
    # continue to see "Not wired in v1".
    assert "visible: !root._isSubmitCapable" in modal_src


def test_bench_modal_return_to_idle_routes_through_demote_submit() -> None:
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    assert "readonly property bool _isReturnToIdleSubmit" in modal_src
    assert "root._isDemoteSubmit || root._isReturnToIdleSubmit" in modal_src
    # The "Return to idle" submit-button label CONVERTED to behavioral (C2 batch
    # 4): test_bench_confirmation_modal_behavior.py::
    # test_submit_capable_family_renders_its_label drives a return-to-idle action
    # and asserts the label renders. The dispatch-routing pins above stay.


def test_bench_pr_c2_surface_listens_for_submitted() -> None:
    """ADR 0051 Phase C2: BenchSurface listens for the modal's ``submitted``
    signal so the preview state clears after a successful demotion."""
    surface_src = (_MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml").read_text(encoding="utf-8")
    assert "onSubmitted" in surface_src, (
        "BenchSurface.qml must handle BenchConfirmationModal.submitted "
        "so the preview closes after a successful demotion."
    )


def test_adr_0051_command_infra_allowlist_only_lists_existing_facade_paths() -> None:
    """Pin the allowlist: every entry must (a) exist on disk and (b) be one
    of the exact paths ADR 0051 names. Silently widening the allowlist by
    adding stub files is exactly what the perimeter exists to prevent."""
    repo_root = Path(__file__).resolve().parents[3]
    permitted = {
        "src/milodex/commands/bench.py",
        "src/milodex/commands/__init__.py",
        # Phase C2 wiring: the Qt bridge over the facade. ADR 0051 §10 (Phase
        # C2 status) names this exact path. Subsequent action-family wiring
        # PRs must NOT widen this set without their own ADR amendment.
        "src/milodex/gui/bench_command_bridge.py",
    }
    assert _ADR_0051_COMMAND_INFRA_ALLOWLIST == frozenset(permitted), (
        "Changing _ADR_0051_COMMAND_INFRA_ALLOWLIST must land in the same PR "
        "as the action-family wiring that justifies the new entry, and must "
        "be cited by the ADR amendment. See ADR 0051 §9."
    )
    for rel in _ADR_0051_COMMAND_INFRA_ALLOWLIST:
        assert (repo_root / rel).exists(), (
            f"Allowlist entry {rel!r} does not exist on disk. Remove the entry or land the file."
        )


def test_bench_pr_o_modal_is_viewport_bounded() -> None:
    """PR O follow-up: BenchModal must cap its height against parent.height.

    Manual smoke discovered the modal clipped at 1440p because the box height
    was content-driven. The fix caps box.height at parent.height minus a safe
    margin and routes overflow through a Flickable. This test guards both
    halves of that fix so a future refactor can't quietly revert to a
    content-only height.
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchModal.qml").read_text(encoding="utf-8")

    # The box height must reference _modalMaxHeight (the parent-bounded cap).
    assert "_modalMaxHeight" in modal_src, (
        "BenchModal.qml must declare a parent-bounded _modalMaxHeight cap"
    )
    assert "parent.height" in modal_src, (
        "BenchModal.qml _modalMaxHeight must derive from parent.height"
    )
    assert "Math.min(root._modalIntrinsicHeight, root._modalMaxHeight)" in modal_src, (
        "BenchModal.qml box.height must clamp at _modalMaxHeight"
    )


def test_bench_pr_o_modal_body_is_scrollable() -> None:
    """PR O follow-up: the modal body must scroll when content overflows."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchModal.qml").read_text(encoding="utf-8")

    # A Flickable named bodyScroll holds the scrollable body region.
    assert "Flickable {" in modal_src, (
        "BenchModal.qml must wrap body content in a Flickable (PR O follow-up)"
    )
    assert "id: bodyScroll" in modal_src, "BenchModal.qml must name its body Flickable bodyScroll"
    # The Flickable must declare contentWidth/contentHeight + clip true.
    assert "contentHeight: contentBlock.implicitHeight" in modal_src, (
        "bodyScroll.contentHeight must track the inner content column"
    )
    assert "clip: true" in modal_src, "bodyScroll must clip overflow"
    # WheelHandler must absorb the event so it does not leak to the Bench.
    assert "WheelHandler" in modal_src and "event.accepted = true" in modal_src, (
        "bodyScroll must install a WheelHandler that accepts the wheel event"
    )


def test_bench_pr_o_modal_footer_is_pinned_outside_scroll() -> None:
    """PR O follow-up: footer/action row must remain visible regardless of scroll.

    Asserts the footerBlock is anchored to the box (parent.bottom) and is
    NOT nested inside the Flickable. Encoding both halves keeps a future
    refactor from quietly moving the action row into the scroll container.
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchModal.qml").read_text(encoding="utf-8")

    # The footer is anchored to the box bottom (not Flickable bottom).
    assert "id: footerBlock" in modal_src
    footer_idx = modal_src.index("id: footerBlock")
    flickable_open_idx = modal_src.index("Flickable {")
    # Find Flickable's matching closing brace by counting depth.
    depth = 0
    flickable_close_idx = -1
    i = flickable_open_idx + len("Flickable ")
    while i < len(modal_src):
        ch = modal_src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                flickable_close_idx = i
                break
        i += 1
    assert flickable_close_idx > flickable_open_idx, (
        "could not locate Flickable closing brace — refactor must keep block well-formed"
    )
    assert not (flickable_open_idx < footer_idx < flickable_close_idx), (
        "footerBlock must be a sibling of the Flickable, not a child — "
        "the action row must stay pinned and always visible"
    )


def test_bench_pr_o_command_draft_preview_no_submit_handler() -> None:
    """PR O: the draft preview must never wire an onClicked submit handler.

    Defense-in-depth: the existing 'Not wired in v1' primary stays inert,
    and the new section must not introduce a MouseArea+onClicked that
    would silently make the draft submittable.
    """
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )
    forbidden_handlers = (
        "onSubmit(",  # narrowed: onSubmitCompleted is a bridge listener, not a draft dispatch
        "submitDraft",
        "submit(",
        "dispatch(",
        "executeDraft",
    )
    for token in forbidden_handlers:
        assert token not in modal_src, (
            f"BenchConfirmationModal.qml must not declare {token!r} (PR O: draft is display-only)"
        )


def test_bench_pr_n_no_mutation_token_drift() -> None:
    """PR N must not regress the PR J/K/L/M mutation-token forbid list."""
    mutation_tokens = (
        "BenchState.promote",
        "BenchState.demote",
        "BenchState.start",
        "BenchState.stop",
        "BenchState.refresh",
        "BenchState.backtest",
        "BenchState.return",
        "broker.",
        "eventStore.",
        "eventstore.",
        "executeOrder",
        "config.write",
        "submitCommand",
        "dispatchCommand",
        "CommandProposal",
    )
    files = (
        _MILODEX_QML_DIR / "components" / "BenchRow.qml",
        _MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml",
        _MILODEX_QML_DIR / "components" / "BenchEvidenceModal.qml",
        _MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml",
    )
    for path in files:
        src = path.read_text(encoding="utf-8")
        for token in mutation_tokens:
            assert token not in src, (
                f"{path.name} must not contain mutation token {token!r} (ADR 0049)"
            )


def test_session_bag_exists_with_correct_defaults() -> None:
    """sessionBag QtObject must exist at Main.qml root with expected defaults.

    Wiring smoke check for issue 12 — operator's perfSlice/throughputSlice
    selections persist across page switches via this shared QtObject.
    Defaults match the prior DeskSurface-local initialization (Week/Week).
    """
    # Load Main.qml via the full engine, then find the sessionBag QtObject by
    # objectName and inspect its string properties via QObject.property().
    probe_block = (
        "from PySide6.QtCore import QUrl, QObject\n"
        f"main_qml = {str(_MILODEX_QML_DIR / _MAIN_QML)!r}\n"
        "_warnings = []\n"
        "engine = QQmlApplicationEngine()\n"
        "engine.warnings.connect(lambda msgs: _warnings.extend(str(m) for m in msgs))\n"
        f"engine.addImportPath({str(_QML_IMPORT_ROOT)!r})\n"
        "engine.load(QUrl.fromLocalFile(main_qml))\n"
        "errors = [w for w in _warnings if w]\n"
        "if errors:\n"
        "    for e in errors:\n"
        "        print(f'QML_WARNING: {e}', file=sys.stderr)\n"
        "    sys.exit(3)\n"
        "if not engine.rootObjects():\n"
        "    print('LOAD_FAILED: no root objects', file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "root_win = engine.rootObjects()[0]\n"
        "# sessionBag is a QtObject — search by QObject base type with objectName.\n"
        "bag = root_win.findChild(QObject, 'sessionBag')\n"
        "if bag is None:\n"
        "    print('SESSION_BAG_MISSING: findChild returned None', file=sys.stderr)\n"
        "    sys.exit(6)\n"
        "perf = bag.property('perfSlice')\n"
        "throughput = bag.property('throughputSlice')\n"
        "if perf != 'Week':\n"
        "    print(f'BAD_PERF_SLICE: expected Week, got {perf!r}', file=sys.stderr)\n"
        "    sys.exit(7)\n"
        "if throughput != 'Week':\n"
        "    print(f'BAD_THROUGHPUT_SLICE: expected Week, got {throughput!r}', file=sys.stderr)\n"
        "    sys.exit(8)\n"
        "print('SESSION_BAG_OK')\n"
        "sys.exit(0)\n"
    )
    # Reuse the setup scaffolding from _build_script (QGuiApplication, QML
    # type registration) but discard the actual load block. The probe does
    # its own engine.load() of Main.qml.
    script = _build_script(_MILODEX_QML_DIR / _MAIN_QML)
    marker = "_warnings: list[str] = []"
    setup, _sep, _rest = script.partition(marker)
    composed_script = setup + probe_block
    _run_and_assert(composed_script, "sessionBag defaults probe")
