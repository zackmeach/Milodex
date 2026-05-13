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
     (ThemeManager, OperationalState stub, StrategyBankState with
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
    "surfaces/AnchorSurface.qml",
    "surfaces/StrategyBankSurface.qml",
    "surfaces/DesignSystemShowcase.qml",
    "surfaces/FrontSurface.qml",
    "surfaces/KanbanSurface.qml",
    "surfaces/BenchSurface.qml",
    "surfaces/LedgerSurface.qml",
    "surfaces/DeskSurface.qml",
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
import os, sys
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
from milodex.gui.strategy_bank_state import StrategyBankState
from milodex.gui.read_models import FrontPageState, BenchState, KanbanState, LedgerState, DeskState

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

# StrategyBankState with nonexistent db_path: surface renders to "error"
# state but loads cleanly — exactly the no-data path we must validate.
sb = StrategyBankState(db_path=Path("/__nonexistent_smoke_test__"))
front = FrontPageState(db_path=Path("/__nonexistent_smoke_test__"), configs_dir=Path("configs"))
bench = BenchState(db_path=Path("/__nonexistent_smoke_test__"), configs_dir=Path("configs"))
kanban = KanbanState(db_path=Path("/__nonexistent_smoke_test__"), configs_dir=Path("configs"))
ledger = LedgerState(db_path=Path("/__nonexistent_smoke_test__"))
desk = DeskState(db_path=Path("/__nonexistent_smoke_test__"), configs_dir=Path("configs"))

register_qml_types(
    theme_manager=tm,
    operational_state=op,
    strategy_bank_state=sb,
    front_page_state=front,
    bench_state=bench,
    kanban_state=kanban,
    ledger_state=ledger,
    desk_state=desk,
)

_warnings: list[str] = []

engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: _warnings.extend(str(m) for m in msgs))
engine.addImportPath({import_root!r})
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
    assert "evidenceModalOpen" in surface_src, (
        "BenchSurface.qml must declare property evidenceModalOpen"
    )
    assert "evidenceModalRow" in surface_src, (
        "BenchSurface.qml must declare property evidenceModalRow"
    )
    assert "onEvidenceRequested:" in surface_src, (
        "BenchSurface.qml must handle onEvidenceRequested: on the BenchRow delegate"
    )
    assert "onCloseRequested: root.evidenceModalOpen = false" in surface_src, (
        "BenchSurface.qml must wire onCloseRequested to clear evidenceModalOpen"
    )


def test_bench_pr_j_modal_wording_contract() -> None:
    """PR J modal disclaimer and forbidden-phrase contract."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchEvidenceModal.qml").read_text(
        encoding="utf-8"
    )

    # Mandatory disclaimer must appear verbatim.
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

    # Close affordance must be emitted from at least 3 places:
    # Escape handler, outside-click MouseArea, close glyph MouseArea.
    assert modal_src.count("root.closeRequested()") >= 3, (
        "BenchEvidenceModal.qml must emit closeRequested() from at least 3 places "
        "(Escape, outside-click, close glyph)"
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
    assert "confirmationPreviewOpen" in surface_src, (
        "BenchSurface.qml must declare property confirmationPreviewOpen"
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
    assert "onCloseRequested: root.confirmationPreviewOpen = false" in surface_src, (
        "BenchSurface.qml must wire onCloseRequested to clear confirmationPreviewOpen"
    )
    # Mutual exclusion: each handler clears the other modal.
    # confirmationPreviewOpen = false appears in onEvidenceRequested AND onCloseRequested.
    assert surface_src.count("confirmationPreviewOpen = false") >= 2, (
        "BenchSurface.qml must clear confirmationPreviewOpen in at least 2 places "
        "(onEvidenceRequested mutual-exclusion + onCloseRequested)"
    )
    # evidenceModalOpen referenced in onActionPreviewRequested AND onCloseRequested.
    assert surface_src.count("evidenceModalOpen") >= 2, (
        "BenchSurface.qml must reference evidenceModalOpen in at least 2 clearing contexts "
        "(onActionPreviewRequested mutual-exclusion + onCloseRequested)"
    )


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
        "This preview shows the confirmation Milodex will require before starting, "
        "stopping, initiating, or refreshing an operational process. "
        "Command execution is not wired in Bench v1."
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

    guard = "root.evidenceModalOpen || root.confirmationPreviewOpen"
    assert surface_src.count(guard) >= 2, (
        f"BenchSurface.qml must contain the bleed-through guard "
        f'"{guard}" in at least 2 places (Keys.onPressed and WheelHandler.onWheel)'
    )


def test_bench_pr_l_intent_packet_sections() -> None:
    """PR L: all six ALL-CAPS section labels appear in BenchConfirmationModal.qml."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    for label in (
        '"ACTION"',
        '"INTENT PACKET"',
        '"CURRENT SNAPSHOT"',
        '"WOULD EVENTUALLY REQUIRE"',
        '"FUTURE RECORD"',
        '"SAFETY BOUNDARY"',
    ):
        assert label in modal_src, (
            f"BenchConfirmationModal.qml must contain section label {label} (PR L)"
        )


def test_bench_pr_l_safety_boundary_wording() -> None:
    """PR L: SAFETY BOUNDARY verbatim sentence and its three sub-clauses are present."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    verbatim = (
        "Bench v1 renders this intent packet for review only. "
        "No command is submitted, no event is written, and no state is changed."
    )
    assert verbatim in modal_src, (
        "BenchConfirmationModal.qml _COPY_SAFETY_BOUNDARY must match verbatim"
    )
    # Defense-in-depth: individual clauses checked separately.
    assert "No command is submitted" in modal_src
    assert "no event is written" in modal_src
    assert "no state is changed" in modal_src


def test_bench_pr_l_future_record_strings() -> None:
    """PR L: all seven non-executable record label strings appear in _futureRecord."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    for record_label in (
        "promotion_event",
        "demotion_event",
        "stage_return_event",
        "session_start_event",
        "session_stop_event",
        "backtest_request_event",
        "backtest_refresh_event",
    ):
        assert record_label in modal_src, (
            f"BenchConfirmationModal.qml _futureRecord must contain {record_label!r} (PR L)"
        )


def test_bench_pr_l_capital_live_precision() -> None:
    """PR L: capital-lock-short, paper-start copy, and paper-stage guard are all present."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    assert ("Capital-bearing transitions remain locked while ADR 0004 is in force.") in modal_src, (
        "BenchConfirmationModal.qml _COPY_CAPITAL_LOCK_SHORT must match verbatim"
    )
    assert ("Paper-stage sessions use live feed with no capital exposure.") in modal_src, (
        "BenchConfirmationModal.qml _COPY_PAPER_START must contain paper-start sentence"
    )
    # _isCapitalBoundary Start Trading guard: paper stage is excluded from capital-bearing.
    assert 'stage === "micro_live" || stage === "live"' in modal_src, (
        "BenchConfirmationModal.qml _isCapitalBoundary must exclude paper stage "
        "from Start Trading capital classification (PR L refinement)"
    )


def test_bench_pr_l_intent_copy_helpers() -> None:
    """PR L: all five Intent Packet helpers are declared in BenchConfirmationModal.qml."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )

    # Functions declared with `function` keyword.
    for fn_decl in (
        "function _actionKind(",
        "function _intentCopy(",
        "function _futureRecord(",
        "function _safetyCopy(",
    ):
        assert fn_decl in modal_src, f"BenchConfirmationModal.qml must declare {fn_decl!r} (PR L)"

    # _requirements is a readonly property var, not a function.
    assert "readonly property var _requirements" in modal_src, (
        "BenchConfirmationModal.qml must declare `readonly property var _requirements` (PR L)"
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
    """BenchConfirmationModal must read actionData.actionIntentPreview (PR N)."""
    modal_src = (_MILODEX_QML_DIR / "components" / "BenchConfirmationModal.qml").read_text(
        encoding="utf-8"
    )
    assert "actionIntentPreview" in modal_src, (
        "BenchConfirmationModal.qml must reference actionIntentPreview (PR N)"
    )
    for helper in ("_preview", "_previewAvailable"):
        assert helper in modal_src, (
            f"BenchConfirmationModal.qml must declare {helper!r} helper (PR N)"
        )

    # Each PR L helper must now consult the preview before falling back.
    for needle in (
        "_preview.capitalBearing",
        "action.actionIntentPreview.actionKind",
        "action.actionIntentPreview.intentCopy",
        "_preview.requirements",
        "action.actionIntentPreview.futureRecord",
        "action.actionIntentPreview.safetyCopy",
    ):
        assert needle in modal_src, f"BenchConfirmationModal.qml must read {needle!r} (PR N)"


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
