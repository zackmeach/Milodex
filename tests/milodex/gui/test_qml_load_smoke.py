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

register_qml_types(
    theme_manager=tm,
    operational_state=op,
    strategy_bank_state=sb,
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
