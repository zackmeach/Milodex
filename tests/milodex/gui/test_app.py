"""Smoke tests for the Milodex GUI application shell.

Tests:
- test_run_app_returns_int: run_app is callable; with Qt mocked it returns int.
- test_main_qml_exists: Main.qml exists on disk.
- test_qml_import_path_points_to_qml_dir: QML_IMPORT_PATH resolves correctly.
- test_run_app_returns_1_when_no_root_objects: coverage for the empty-rootObjects
  early-exit path.
- test_main_qml_loads_without_errors_via_subprocess: Main.qml loads in a fresh
  process (subprocess-isolated to avoid polluting the test-runner's Qt type cache).
- test_bench_surface_loads_without_errors_via_subprocess: BenchSurface loads in a
  fresh process (ADR 0035 integration smoke; retargeted from the retired showcase).

These tests avoid creating any QQmlApplicationEngine with the Milodex import path
because doing so would pre-compile the Milodex module into the process-global Qt
type cache. That compilation puts registered types (e.g. Button)
into a "module-cached" state that breaks the inline-QML composition technique used
by test_button_primary_instantiates_with_correct_variant in test_qml_components.py
(which depends on being the FIRST entity to compile the Milodex module into the
process cache).

QML loading integration coverage lives in test_qml_theme_loads.py and
test_qml_components.py (PR A-C tests).  Full QML integration (Main.qml and
BenchSurface.qml) is covered by the subprocess-isolated tests below.
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
        # run_app builds an ordered QmlSingleton registry and registers it via
        # register_qml_singletons (imported locally inside run_app), not the
        # back-compat register_qml_types wrapper. Patch it at its source module
        # so the mocked read-model instances are never handed to the real
        # qmlRegisterSingletonInstance.
        patch("milodex.gui.qml_setup.register_qml_singletons"),
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
        # See test_run_app_returns_int: run_app registers via
        # register_qml_singletons, so patch that at its source module.
        patch("milodex.gui.qml_setup.register_qml_singletons"),
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


class _StopSpy:
    """Minimal ``stop()``-bearing double recording invocation count."""

    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


def test_make_app_controller_accepts_extra_drainables_and_drains_them_on_quit():
    """``_make_app_controller`` accepts non-lifecycle drainables (the Bench
    command bridge) and ``quitRequested`` stops them after the read models.

    This is the wiring guarantee for the P2 fix: the bridge's private async
    pool, which the lifecycle filter never reaches, is drained on clean quit.
    """
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    from milodex.gui.app import _make_app_controller

    # AppController is a QObject; an application instance must exist to create
    # and exercise it. Reuse any existing one.
    _app = QCoreApplication.instance() or QGuiApplication([])

    rm = _StopSpy()
    bridge = _StopSpy()
    controller = _make_app_controller([rm], extra_drainables=[bridge])

    # quitRequested calls QGuiApplication.quit() last; with no running event
    # loop that is a harmless no-op.
    controller.quitRequested()

    assert rm.stop_calls == 1
    assert bridge.stop_calls == 1, (
        "AppController.quitRequested must drain the non-lifecycle bridge "
        "(P2: its private async pool is otherwise never drained on quit)."
    )


def test_make_app_controller_single_positional_arg_still_works():
    """Regression guard: callers passing only the positional read_models list
    (e.g. the test_app subprocess harness: ``_make_app_controller([])``) must
    keep working without supplying extra_drainables."""
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    from milodex.gui.app import _make_app_controller

    _app = QCoreApplication.instance() or QGuiApplication([])

    controller = _make_app_controller([])
    # No read models, no extra drainables — quitRequested must not raise.
    controller.quitRequested()


def test_main_qml_exists():
    """Main.qml exists at the expected path.

    File-system check only -- no Qt initialization.
    """
    from milodex.gui.app import QML_IMPORT_PATH

    main_qml = QML_IMPORT_PATH / "Milodex" / "Main.qml"

    assert main_qml.exists(), f"Main.qml missing at {main_qml}"


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

    # as_posix(): these strings are repr'd into generated source. A raw Windows
    # path inside a QML string literal parses `\<digit>` as a rejected legacy
    # octal escape (breaks under any worktree/tmp path like `...\1df...`).
    qml_path = (QML_IMPORT_PATH / "Milodex" / "Main.qml").as_posix()
    import_path = QML_IMPORT_PATH.as_posix()

    # Main.qml's default surface is FrontSurface; register a stub OperationalState
    # with a failing broker factory so the surface renders in its
    # broker-error branch (still a clean load — no QML warnings).
    script = f"""\
import os
import sys
from unittest.mock import MagicMock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

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
from milodex.gui.risk_profile_bridge import RiskProfileBridge
from milodex.gui.app import _make_app_controller

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl
from pathlib import Path

app = QGuiApplication(sys.argv)
load_fonts()
tm = ThemeManager()

ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(
    active=False, reason=None, last_triggered_at=None
)
def failing_factory():
    raise RuntimeError('test: no broker available')

op_state = OperationalState(
    broker_client_factory=failing_factory,
    kill_switch_store=ks_store,
    trading_mode='paper',
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)
db_path = Path('/__nonexistent_app_test__')
configs_dir = Path('configs')
front_page_state = FrontPageState(db_path=db_path, configs_dir=configs_dir)
bench_state = BenchState(db_path=db_path, configs_dir=configs_dir)
ledger_state = LedgerState(db_path=db_path)
performance_state = PerformanceState(db_path=db_path, cache_dir=db_path)
risk_throughput_state = RiskThroughputState(db_path=db_path)
active_ops_state = ActiveOpsState(db_path=db_path, configs_dir=configs_dir, locks_dir=db_path)
attention_state = AttentionState(db_path=db_path)
market_tape_state = MarketTapeState(cache_dir=db_path)
activity_feed_state = ActivityFeedState(db_path=db_path)
risk_profile_bridge = RiskProfileBridge(db_path=db_path)
register_qml_types(
    theme_manager=tm,
    operational_state=op_state,
    front_page_state=front_page_state,
    bench_state=bench_state,
    ledger_state=ledger_state,
    performance_state=performance_state,
    risk_throughput_state=risk_throughput_state,
    active_ops_state=active_ops_state,
    attention_state=attention_state,
    market_tape_state=market_tape_state,
    activity_feed_state=activity_feed_state,
    risk_profile_bridge=risk_profile_bridge,
)

_app_ctrl = _make_app_controller([])

warnings_seen = []

engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: warnings_seen.extend(msgs))
engine.addImportPath({import_path!r})
engine.rootContext().setContextProperty("AppController", _app_ctrl)
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


# ---------------------------------------------------------------------------
# HISTORICAL FLAKE SENTINEL (2026-05-17)
#
# This subprocess test (test_bench_surface_loads_without_errors_via_subprocess,
# retargeted 2026-07-17 from the retired DesignSystemShowcase smoke) guards the
# missing-fonts flake fixed by bundled fonts.  It was historically FLAKY in
# full-suite runs due to pre-existing process-global Qt/QML state pollution in
# the test-runner process, and passes reliably in isolation.
# (test_anchor_surface_loads_without_errors_via_subprocess was removed with
# AnchorSurface in HR-4.)
#
# Root cause: process-global Qt/QML type-cache pollution from other tests in
# the gui suite contaminates the subprocess-launch environment in a
# nondeterministic way, producing intermittent failures and occasional Win32
# access violations in the Qt thread-pool.
#
# Reproduces at pre-feature commits (e.g. d762ecd) — NOT caused by the
# Trading Desk feature.  Root-cause remediation is deferred to a separate
# tracked task.  See docs/KNOWN_FLAKY_TESTS.md.
# ---------------------------------------------------------------------------


def test_bench_surface_loads_without_errors_via_subprocess():
    """BenchSurface.qml loads successfully in a fresh process.

    ADR 0035 integration smoke and the historical missing-fonts flake sentinel
    (see docs/KNOWN_FLAKY_TESTS.md).  Retargeted 2026-07-17 from the retired
    DesignSystemShowcase smoke: the showcase and its showcase-only components
    (StrategyRow/StatusPill/GateTable) were deleted as dead code, so the smoke
    now pins a live surface.  BenchSurface is the richest live surface not
    otherwise subprocess-covered (Main.qml defaults to FrontSurface), so it
    exercises a distinct load path with bundled fonts and registered singletons.

    Success: returncode == 0 and stderr is empty.
    """
    from milodex.gui.app import QML_IMPORT_PATH

    # as_posix(): surface_path is repr'd INTO the QML wrapper string below —
    # a raw Windows path there parses `\<digit>` as a rejected legacy octal
    # escape (breaks under any worktree/tmp path like `...\1df...`).
    import_path = QML_IMPORT_PATH.as_posix()
    surface_path = (QML_IMPORT_PATH / "Milodex" / "surfaces" / "BenchSurface.qml").as_posix()

    # BenchSurface reads BenchState / BenchCommandBridge / Theme / Formatters.
    # Register the singletons it needs (BenchCommandBridge over a mock facade —
    # the surface only reads its Qt properties at load, never invokes a command).
    # A Loader with an explicit file URL loads the surface by path; the outer
    # Window gives the engine a root object and the base URL at the qml/ root
    # resolves the `import Milodex 1.0` inside the surface.
    script = f"""\
import os
import sys
from unittest.mock import MagicMock
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from milodex.gui.fonts import load_fonts
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.theme_manager import ThemeManager
from milodex.gui.read_models import BenchState, LedgerState
from milodex.gui.bench_command_bridge import BenchCommandBridge

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtCore import QUrl

app = QGuiApplication(sys.argv)
load_fonts()
tm = ThemeManager()

db_path = Path('/__nonexistent_app_test__')
configs_dir = Path('configs')
bench_state = BenchState(db_path=db_path, configs_dir=configs_dir)
ledger_state = LedgerState(db_path=db_path)
bench_command_bridge = BenchCommandBridge(
    MagicMock(), bench_state=bench_state, ledger_state=ledger_state
)
register_qml_types(
    theme_manager=tm,
    bench_state=bench_state,
    ledger_state=ledger_state,
    bench_command_bridge=bench_command_bridge,
)

warnings_seen = []

engine = QQmlApplicationEngine()
engine.warnings.connect(lambda msgs: warnings_seen.extend(msgs))
engine.addImportPath({import_path!r})

# Wrap the surface in a minimal Window so the engine records a root object.
# Loader by source URL avoids depending on qmldir registration ordering.
wrapper = (
    "import QtQuick 2.15\\n"
    "import QtQuick.Window 2.15\\n"
    "Window {{\\n"
    "    width: 1280; height: 800; visible: false\\n"
    "    Loader {{ anchors.fill: parent; source: {surface_path!r} }}\\n"
    "}}\\n"
).encode()

# Base URL points to qml/ root so relative imports inside the surface resolve.
engine.loadData(wrapper, QUrl.fromLocalFile({import_path!r} + "/wrapper.qml"))

if not engine.rootObjects():
    print("ERROR: no root objects -- BenchSurface wrapper failed to load", file=sys.stderr)
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
        f"BenchSurface subprocess exited {result.returncode}.\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    assert result.stderr == "", (
        f"BenchSurface subprocess produced stderr output (QML warnings):\n{result.stderr}"
    )


def test_kill_switch_reset_modal_file_deleted():
    """HR-4: AnchorSurface.qml must be deleted; KillSwitchResetModal.qml must exist.

    AnchorSurface was the sole GUI path to reset_kill_switch but was stranded
    when the nav rework removed its tab (G-P1-1). The reset flow now lives in
    KillSwitchResetModal, which is opened from RiskStrip and the Risk Office drawer.
    """
    from milodex.gui.app import QML_IMPORT_PATH

    anchor_path = QML_IMPORT_PATH / "Milodex" / "surfaces" / "AnchorSurface.qml"
    modal_path = QML_IMPORT_PATH / "Milodex" / "components" / "KillSwitchResetModal.qml"

    assert not anchor_path.exists(), (
        "AnchorSurface.qml must be deleted — reset flow now lives in KillSwitchResetModal (HR-4)"
    )
    assert modal_path.exists(), (
        "KillSwitchResetModal.qml must exist — extracted from AnchorSurface by HR-4"
    )
