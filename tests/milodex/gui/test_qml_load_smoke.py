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
        'root.activeSurface === "bench")          return "surfaces/BenchSurface.qml"'
        in main_source
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
    """Folio mark affordance still uses the compute_menu_items engine pipeline."""
    row_src = (_MILODEX_QML_DIR / "components" / "BenchRow.qml").read_text(encoding="utf-8")
    assert "actionItems" in row_src
    assert "QQC2.Menu" in row_src
    assert "Instantiator" in row_src
    assert "modelData.label" in row_src
    # v1 visual-prototype contract: menu items remain no-op. Wiring real dispatch
    # through onTriggered would break ADR 0049; this assertion forces any future
    # implementer adding a mutation pathway to also touch this test.
    assert "onTriggered" in row_src


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
    surface_src = (_MILODEX_QML_DIR / "surfaces" / "BenchSurface.qml").read_text(
        encoding="utf-8"
    )

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
    assert "BenchState.promote" not in row_src, (
        "BenchRow.qml must not call BenchState.promote"
    )
    assert "BenchState.demote" not in row_src, (
        "BenchRow.qml must not call BenchState.demote"
    )
