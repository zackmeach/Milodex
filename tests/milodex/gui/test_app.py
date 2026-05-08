"""Smoke tests for the Milodex GUI application shell.

Tests:
- test_run_app_returns_int: run_app is callable; with Qt mocked it returns int.
- test_main_qml_exists: Main.qml and DesignSystemShowcase.qml exist on disk.
- test_qml_import_path_points_to_qml_dir: QML_IMPORT_PATH resolves correctly.
- test_run_app_returns_1_when_no_root_objects: coverage for the empty-rootObjects
  early-exit path.
- test_main_qml_loads_without_errors_via_subprocess: Main.qml loads in a fresh
  process (subprocess-isolated to avoid polluting the test-runner's Qt type cache).
- test_design_system_showcase_loads_without_errors_via_subprocess: showcase loads
  cleanly, verifying all components compose correctly.

These tests avoid creating any QQmlApplicationEngine with the Milodex import path
because doing so would pre-compile the Milodex module into the process-global Qt
type cache. That compilation puts registered types (Button, StrategyRow, StatusPill)
into a "module-cached" state that breaks the inline-QML composition technique used
by test_button_primary_instantiates_with_correct_variant in test_qml_components.py
(which depends on being the FIRST entity to compile the Milodex module into the
process cache).

QML loading integration coverage lives in test_qml_theme_loads.py and
test_qml_components.py (PR A-C tests).  Full QML integration (Main.qml and
DesignSystemShowcase.qml) is covered by the subprocess-isolated tests below.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QML_IMPORT_ROOT: Path = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui" / "qml"

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_app_returns_int():
    """run_app is callable and returns int when Qt is mocked.

    Does not require PySide6 to be installed -- patches the import.
    """
    from milodex.gui import app as app_module

    mock_qapp = MagicMock()
    mock_qapp.instance.return_value = None
    mock_qapp.return_value = mock_qapp
    mock_qapp.exec.return_value = 0

    mock_engine = MagicMock()
    mock_engine.return_value = mock_engine
    mock_engine.rootObjects.return_value = [MagicMock()]

    with (
        patch("milodex.gui.app.QML_IMPORT_PATH", _QML_IMPORT_ROOT / "Milodex"),
        patch("milodex.gui.fonts.load_fonts", return_value=(3, [])),
        patch("milodex.gui.qml_setup.register_qml_types"),
        patch("milodex.gui.theme_manager.ThemeManager") as mock_tm_cls,
    ):
        mock_tm = MagicMock()
        mock_tm.theme = "editorial-dark"
        mock_tm_cls.return_value = mock_tm

        with (
            patch("PySide6.QtGui.QGuiApplication", mock_qapp),
            patch("PySide6.QtQml.QQmlApplicationEngine", mock_engine),
        ):
            result = app_module.run_app()

    assert isinstance(result, int)


def test_run_app_returns_1_when_no_root_objects():
    """run_app returns 1 when QML engine has no root objects (load failure)."""
    from milodex.gui import app as app_module

    mock_qapp = MagicMock()
    mock_qapp.instance.return_value = None
    mock_qapp.return_value = mock_qapp

    mock_engine = MagicMock()
    mock_engine.return_value = mock_engine
    # Empty root objects -- load failed
    mock_engine.rootObjects.return_value = []

    with (
        patch("milodex.gui.app.QML_IMPORT_PATH", _QML_IMPORT_ROOT / "Milodex"),
        patch("milodex.gui.fonts.load_fonts", return_value=(3, [])),
        patch("milodex.gui.qml_setup.register_qml_types"),
        patch("milodex.gui.theme_manager.ThemeManager") as mock_tm_cls,
    ):
        mock_tm = MagicMock()
        mock_tm.theme = "editorial-dark"
        mock_tm_cls.return_value = mock_tm

        with (
            patch("PySide6.QtGui.QGuiApplication", mock_qapp),
            patch("PySide6.QtQml.QQmlApplicationEngine", mock_engine),
        ):
            result = app_module.run_app()

    assert result == 1


def test_main_qml_exists():
    """Main.qml and DesignSystemShowcase.qml exist at the expected paths.

    File-system check only -- no Qt initialization.
    """
    from milodex.gui.app import QML_IMPORT_PATH

    main_qml = QML_IMPORT_PATH / "Milodex" / "Main.qml"
    showcase_qml = QML_IMPORT_PATH / "Milodex" / "surfaces" / "DesignSystemShowcase.qml"

    assert main_qml.exists(), f"Main.qml missing at {main_qml}"
    assert showcase_qml.exists(), f"DesignSystemShowcase.qml missing at {showcase_qml}"


def test_qml_import_path_points_to_qml_dir():
    """QML_IMPORT_PATH resolves to the qml/ directory containing the Milodex module."""
    from milodex.gui.app import QML_IMPORT_PATH

    assert QML_IMPORT_PATH.is_dir(), f"QML_IMPORT_PATH is not a directory: {QML_IMPORT_PATH}"
    qmldir = QML_IMPORT_PATH / "Milodex" / "qmldir"
    assert qmldir.exists(), f"qmldir not found at {qmldir}"


# ---------------------------------------------------------------------------
# Subprocess-isolated QML integration tests
# ---------------------------------------------------------------------------
# These tests load real QML files in a fresh Python process so that the
# process-global Qt type cache is not polluted in the test-runner process.
# Each subprocess script:
#   1. Sets QT_QPA_PLATFORM=offscreen (no display required).
#   2. Loads fonts and registers QML types.
#   3. Constructs QGuiApplication + QQmlApplicationEngine.
#   4. Connects a warnings collector.
#   5. Loads the target QML file.
#   6. Exits 0 on success, non-zero on load failure or any QML warnings.
# ---------------------------------------------------------------------------


def test_main_qml_loads_without_errors_via_subprocess():
    """Main.qml loads successfully in a fresh process (no warnings, no errors).

    Subprocess-isolated to avoid polluting the test-runner's Qt type cache.

    Success: returncode == 0 and stderr is empty -- the test is marked PASSED
    (or XPASSED if the xfail marker is still present).

    Xfail: any non-zero return or non-empty stderr from the subprocess means the
    Qt environment is not fully set up (e.g., missing platform plugin, no
    offscreen support). The test is marked XFAIL and the suite stays green.
    """
    from milodex.gui.app import QML_IMPORT_PATH

    qml_path = str(QML_IMPORT_PATH / "Milodex" / "Main.qml")
    import_path = str(QML_IMPORT_PATH)

    script = f"""\
import os
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from milodex.gui.fonts import load_fonts
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.theme_manager import ThemeManager

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl

app = QGuiApplication(sys.argv)
load_fonts()
tm = ThemeManager()
register_qml_types(tm)

warnings_seen = []

engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: warnings_seen.extend(msgs))
engine.addImportPath({import_path!r})
engine.load(QUrl.fromLocalFile({qml_path!r}))

if not engine.rootObjects():
    print("ERROR: no root objects after load", file=sys.stderr)
    sys.exit(1)

if warnings_seen:
    for w in warnings_seen:
        print(str(w), file=sys.stderr)
    sys.exit(2)

sys.exit(0)
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"Main.qml subprocess exited {result.returncode}.\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert result.stderr == "", (
        f"Main.qml subprocess produced stderr output (QML warnings):\n{result.stderr}"
    )


def test_design_system_showcase_loads_without_errors_via_subprocess():
    """DesignSystemShowcase.qml loads successfully in a fresh process.

    Same pattern as test_main_qml_loads_without_errors_via_subprocess but
    loads the showcase directly to verify that ALL components (Button,
    StatusPill, StrategyRow, Surface) compose correctly when rendered
    together in the showcase surface.

    Success: returncode == 0 and stderr is empty.
    Xfail: non-zero return or non-empty stderr indicates environment-specific
    Qt setup issues rather than a code bug.
    """
    from milodex.gui.app import QML_IMPORT_PATH

    import_path = str(QML_IMPORT_PATH)
    showcase_path = str(QML_IMPORT_PATH / "Milodex" / "surfaces" / "DesignSystemShowcase.qml")

    # DesignSystemShowcase is not a registered qmldir type -- it is a plain QML
    # file.  We use a Loader with an explicit file URL so the engine loads it by
    # path, not by type name.  The outer Window gives the engine a root object.
    # The base URL for loadData is set to the qml/ root so that the Milodex
    # module import inside DesignSystemShowcase.qml resolves correctly.
    script = f"""\
import os
import sys

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from milodex.gui.fonts import load_fonts
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.theme_manager import ThemeManager

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl

app = QGuiApplication(sys.argv)
load_fonts()
tm = ThemeManager()
register_qml_types(tm)

warnings_seen = []
load_errors = []

engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: warnings_seen.extend(msgs))
engine.addImportPath({import_path!r})

showcase_url = QUrl.fromLocalFile({showcase_path!r})

# Wrap the showcase Item in a minimal Window so the engine records a root
# object.  Loader by source URL avoids the need for a qmldir registration.
wrapper = (
    "import QtQuick 2.15\\n"
    "import QtQuick.Window 2.15\\n"
    "Window {{\\n"
    "    width: 1280; height: 800; visible: false\\n"
    "    Loader {{ anchors.fill: parent; source: {showcase_path!r} }}\\n"
    "}}\\n"
).encode()

# Base URL points to qml/ root so relative imports inside the showcase resolve.
engine.loadData(wrapper, QUrl.fromLocalFile({import_path!r} + "/wrapper.qml"))

if not engine.rootObjects():
    print("ERROR: no root objects -- showcase wrapper failed to load", file=sys.stderr)
    sys.exit(1)

if warnings_seen:
    for w in warnings_seen:
        print(str(w), file=sys.stderr)
    sys.exit(2)

sys.exit(0)
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"DesignSystemShowcase subprocess exited {result.returncode}.\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert result.stderr == "", (
        f"DesignSystemShowcase subprocess produced stderr output (QML warnings):\n{result.stderr}"
    )
