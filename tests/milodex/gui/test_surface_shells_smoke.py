"""Load-clean probe tests for the PR11 shared surface shells.

ScrollSurface / EditorialHeader / SurfaceBase are extracted from the per-
surface re-roll of Flickable scaffold + editorial masthead + contract root.
A QML rename that resolves to ``undefined`` still "loads clean" at the
surface level, so this file probes each shell directly: it instantiates the
component via QQmlComponent.setData, asserts zero engine warnings, and reads
back the contract property/text that proves the type resolved (not undefined).

Mirrors the subprocess-harness pattern in test_desk_components_smoke.py.
All tests skip when PySide6 is not importable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"

try:
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping surface-shell probe tests",
)

# ---------------------------------------------------------------------------
# Subprocess harness (mirrors test_desk_components_smoke.py)
# ---------------------------------------------------------------------------

_HARNESS_SETUP = f"""\
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent

from milodex.gui.qml_setup import register_qml_types
from milodex.gui.theme_manager import ThemeManager

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
tm = ThemeManager()
register_qml_types(tm)

engine = QQmlApplicationEngine()
warnings = []
engine.warnings.connect(lambda ws: warnings.extend(str(w.toString()) for w in ws))
engine.addImportPath({str(_QML_IMPORT_ROOT)!r})
"""


def _build_probe_script(inline_qml: str, checks: str) -> str:
    """Return a subprocess script that loads *inline_qml* and runs *checks*.

    *checks* is a Python snippet that may reference ``obj`` (the created root
    object) and must ``sys.exit(non-zero)`` on failure; the harness asserts
    clean load (no warnings, obj not None) before running it.
    """
    set_data = (
        "\ncomponent = QQmlComponent(engine)\n"
        f"component.setData({inline_qml!r}.encode('utf-8'), QUrl())\n"
        "if component.status() == QQmlComponent.Error:\n"
        "    print(component.errorString(), file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "obj = component.create(engine.rootContext())\n"
        "if obj is None:\n"
        "    print(component.errorString(), file=sys.stderr)\n"
        "    sys.exit(3)\n"
        "if warnings:\n"
        "    print('\\n'.join(warnings), file=sys.stderr)\n"
        "    sys.exit(4)\n"
    )
    return _HARNESS_SETUP + set_data + checks


def _run(script: str, label: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"shell probe failed for {label}\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# ScrollSurface
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_scroll_surface_loads_and_exposes_content_height() -> None:
    """ScrollSurface resolves, accepts maxContentWidth, and exposes a
    contentHeight a surface can bind captureContentHeight to."""
    qml = (
        "import QtQuick\n"
        "import Milodex 1.0\n"
        "ScrollSurface {\n"
        "    width: 800; height: 600\n"
        "    maxContentWidth: 720\n"
        "    Column { width: parent.width; Text { text: 'x'; height: 200 } }\n"
        "}\n"
    )
    checks = (
        "ch = obj.property('contentHeight')\n"
        "if ch is None:\n"
        "    print('ScrollSurface.contentHeight is None (undefined?)', file=sys.stderr)\n"
        "    sys.exit(5)\n"
        "if not (ch > 0):\n"
        "    print(f'ScrollSurface.contentHeight not positive: {ch}', file=sys.stderr)\n"
        "    sys.exit(6)\n"
        "if obj.property('maxContentWidth') != 720:\n"
        "    print(f'maxContentWidth mismatch: {obj.property(\"maxContentWidth\")}',"
        " file=sys.stderr)\n"
        "    sys.exit(7)\n"
        "print('SCROLL_SURFACE_OK')\n"
    )
    _run(_build_probe_script(qml, checks), "ScrollSurface")


# ---------------------------------------------------------------------------
# EditorialHeader
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_editorial_header_loads_and_renders_title() -> None:
    """EditorialHeader resolves and round-trips its title/eyebrow/standfirst."""
    qml = (
        "import QtQuick\n"
        "import Milodex 1.0\n"
        "EditorialHeader {\n"
        "    width: 800\n"
        "    eyebrow: 'PAPER OF RECORD'\n"
        "    title: 'The Ledger'\n"
        "    standfirst: 'A chronological record.'\n"
        "}\n"
    )
    checks = (
        "if obj.property('title') != 'The Ledger':\n"
        "    print(f'title mismatch: {obj.property(\"title\")!r}', file=sys.stderr)\n"
        "    sys.exit(5)\n"
        "if obj.property('eyebrow') != 'PAPER OF RECORD':\n"
        "    print(f'eyebrow mismatch: {obj.property(\"eyebrow\")!r}', file=sys.stderr)\n"
        "    sys.exit(6)\n"
        "print('EDITORIAL_HEADER_OK')\n"
    )
    _run(_build_probe_script(qml, checks), "EditorialHeader")


# ---------------------------------------------------------------------------
# SurfaceBase
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_surface_base_loads_and_exposes_contract_members() -> None:
    """SurfaceBase resolves and exposes captureContentHeight + sessionBag."""
    qml = (
        "import QtQuick\n"
        "import Milodex 1.0\n"
        "SurfaceBase {\n"
        "    width: 400; height: 300\n"
        "    captureContentHeight: 1234\n"
        "}\n"
    )
    checks = (
        "if obj.property('captureContentHeight') != 1234:\n"
        "    print(f'captureContentHeight mismatch: "
        "{obj.property(\"captureContentHeight\")}', file=sys.stderr)\n"
        "    sys.exit(5)\n"
        "# sessionBag default is null; property must exist (KeyError-free read).\n"
        "sb = obj.property('sessionBag')\n"
        "if sb is not None:\n"
        "    print(f'sessionBag default not null: {sb!r}', file=sys.stderr)\n"
        "    sys.exit(6)\n"
        "print('SURFACE_BASE_OK')\n"
    )
    _run(_build_probe_script(qml, checks), "SurfaceBase")


# ---------------------------------------------------------------------------
# Content-renders net — real surface in a sized QQuickView.
#
# The load-clean probes above prove the shells *resolve*. They do NOT prove a
# recomposed surface still renders content: a binding that silently resolves to
# ``undefined`` (e.g. a ScrollSurface whose slot derives no height, collapsing
# contentHeight to 0) keeps the surface "loading clean" while rendering nothing.
#
# This net loads each REAL surface (Front / Ledger / Desk) into a sized
# QQuickView with all read-model singletons registered (harness mirrors
# test_desk_layout_regression.py _HARNESS_B/_F), pumps the loop, then asserts:
#   1. captureContentHeight > 0  (the scroll extent did not collapse), and
#   2. the page renders >= _MIN_VISIBLE_TEXT visible Text nodes.
#
# Non-vacuity is proven by the threshold: each surface's masthead alone emits
# several Text nodes; an empty-collapsed page yields 0. The test prints the
# observed counts so a regression to 0/near-0 is visible in the failure output.
# ---------------------------------------------------------------------------

# Each surface's masthead + standfirst alone is well above this; an empty /
# collapsed page renders 0 visible Text nodes. Chosen low enough to be robust
# to data-driven content (the no-DB harness yields mostly chrome) yet far above
# the empty-page floor of 0.
_MIN_VISIBLE_TEXT = 3


@pytest.mark.parametrize(
    "qml_relative",
    ["surfaces/FrontSurface.qml", "surfaces/LedgerSurface.qml", "surfaces/DeskSurface.qml"],
)
@_skip_no_qt
def test_surface_renders_content(qml_relative: str) -> None:
    """Real surface renders: captureContentHeight > 0 AND visible Text present.

    Guards against a silent fail-to-undefined collapse that the load-clean
    probes miss. See module-level net comment for the non-vacuity argument.
    """
    surface_path = _MILODEX_QML_DIR / qml_relative
    assert surface_path.exists(), f"surface missing: {surface_path}"
    script = _CONTENT_RENDERS_HARNESS.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        surface=repr(str(surface_path)),
        min_text=_MIN_VISIBLE_TEXT,
    )
    _run(script, f"content-renders[{qml_relative}]")


_CONTENT_RENDERS_HARNESS = r"""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from unittest.mock import MagicMock
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from milodex.gui.fonts import load_fonts
from milodex.gui.qml_setup import register_qml_types
from milodex.gui.theme_manager import ThemeManager
from milodex.gui.operational_state import OperationalState
from milodex.gui.strategy_bank_state import StrategyBankState
from milodex.gui.read_models import FrontPageState, BenchState, KanbanState, LedgerState
from milodex.gui.performance_state import PerformanceState
from milodex.gui.risk_throughput_state import RiskThroughputState
from milodex.gui.active_ops_state import ActiveOpsState
from milodex.gui.attention_state import AttentionState
from milodex.gui.market_tape_state import MarketTapeState
from milodex.gui.activity_feed_state import ActivityFeedState
from milodex.commands.bench import BenchCommandFacade
from milodex.gui.bench_command_bridge import BenchCommandBridge

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

tm = ThemeManager()
ks_store = MagicMock()
ks_store.get_state.return_value = MagicMock(active=False, reason=None, last_triggered_at=None)

def _failing_broker():
    raise RuntimeError("content-renders-test: no broker")

op = OperationalState(
    broker_client_factory=_failing_broker,
    kill_switch_store=ks_store,
    trading_mode="paper",
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)

_ne = Path("/__nonexistent_surface_content_test__")
sb = StrategyBankState(db_path=_ne)
front = FrontPageState(db_path=_ne, configs_dir=Path("configs"))
bench = BenchState(db_path=_ne, configs_dir=Path("configs"))
kanban = KanbanState(db_path=_ne, configs_dir=Path("configs"))
ledger = LedgerState(db_path=_ne)
performance = PerformanceState(db_path=_ne, cache_dir=_ne)
risk_throughput = RiskThroughputState(db_path=_ne)
active_ops = ActiveOpsState(db_path=_ne, configs_dir=Path("configs"), locks_dir=_ne)
attention = AttentionState(db_path=_ne)
market_tape = MarketTapeState(cache_dir=_ne)
activity_feed = ActivityFeedState(db_path=_ne)

_root = Path(tempfile.mkdtemp(prefix="milodex_surface_content_"))
(_root / "configs").mkdir()
(_root / "locks").mkdir()
facade = BenchCommandFacade(
    config_dir=_root / "configs",
    locks_dir=_root / "locks",
    get_trading_mode=lambda: "paper",
)
bridge = BenchCommandBridge(facade, bench_state=bench)

register_qml_types(
    theme_manager=tm,
    operational_state=op,
    strategy_bank_state=sb,
    front_page_state=front,
    bench_state=bench,
    kanban_state=kanban,
    ledger_state=ledger,
    performance_state=performance,
    risk_throughput_state=risk_throughput,
    active_ops_state=active_ops,
    attention_state=attention,
    market_tape_state=market_tape,
    activity_feed_state=activity_feed,
    bench_command_bridge=bridge,
)

view = QQuickView()
view.engine().addImportPath({import_root})
view.setResizeMode(QQuickView.SizeRootObjectToView)
view.resize(1600, 1100)
view.setSource(QUrl.fromLocalFile({surface}))

if view.status() == QQuickView.Error:
    for e in view.errors():
        print(str(e.toString()), file=sys.stderr)
    sys.exit(2)

root = view.rootObject()
if root is None:
    print("rootObject() is None", file=sys.stderr)
    sys.exit(3)

view.show()
QTimer.singleShot(900, app.quit)
app.exec()

def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

cch = root.property("captureContentHeight")
if cch is None:
    print("captureContentHeight is None (undefined?)", file=sys.stderr)
    sys.exit(4)
if not (cch > 0):
    print("captureContentHeight collapsed: " + str(cch), file=sys.stderr)
    sys.exit(5)

visible_text = [
    it for it in _walk(root)
    if it.metaObject().className().startswith("QQuickText")
    and it.property("visible")
    and (it.property("text") or "") != ""
    and it.width() > 0
    and it.height() > 0
]
n = len(visible_text)
print("captureContentHeight=" + str(cch) + " visibleText=" + str(n))
if n < {min_text}:
    print("TOO FEW VISIBLE TEXT NODES: " + str(n) + " (page rendered empty?)",
          file=sys.stderr)
    sys.exit(6)

print("SURFACE_RENDERS_CONTENT")
sys.exit(0)
"""
