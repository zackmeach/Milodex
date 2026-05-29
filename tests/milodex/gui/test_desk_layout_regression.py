"""Regression tests for two visually-confirmed Trading Desk layout defects
that the headless load-smoke gate missed.

Defect A: SegmentedToggle parent<->child implicit-size cycle spamming
Qt "Row called polish() inside updatePolish()" thousands of times.
Defect B: DeskSurface Row 1 / Row 2 plain-Column sections had no Layout
height contract and collapsed to height 0.

Both defects were invisible to test_qml_load_smoke.py: the polish spam is
emitted on the Qt logging category (not QQmlEngine.warnings), and a
zero-height RowLayout still "loads clean". These tests close that hole.

All tests skip when PySide6 is not importable.
"""

from __future__ import annotations

import subprocess  # noqa: S404 - mirrors sibling GUI subprocess-harness tests
import sys
from pathlib import Path

import pytest

try:
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed - skipping QML desk-layout regression tests",
)

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_MILODEX_QML_DIR = _QML_IMPORT_ROOT / "Milodex"
_DESK_SURFACE = _MILODEX_QML_DIR / "surfaces" / "DeskSurface.qml"


def _run(script: str, label: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, (
        f"{label} FAILED\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@_skip_no_qt
def test_segmented_toggle_no_polish_loop() -> None:
    """SegmentedToggle must not spam polish() - isolated AND composed in a
    parent whose implicit size derives from it.

    A Qt message handler captures every qWarning/qDebug line (the polish
    spam is emitted there, not via QQmlEngine.warnings). After load + an
    event-loop pump, ANY captured line containing "polish()" or
    "updatePolish" fails (exit 5). Pre-fix: thousands of lines. Post-fix: 0.
    """
    script = _HARNESS_A.format(import_root=repr(str(_QML_IMPORT_ROOT)))
    _run(script, "SegmentedToggle polish-loop detector")


@_skip_no_qt
def test_desk_surface_rows_have_nonzero_height() -> None:
    """DeskSurface Row-1 / Row-2 sections must render with height > 0.

    Loads real DeskSurface.qml with all six read-model singletons (same
    harness shape as test_qml_load_smoke.py) into a sized QQuickView so
    Layouts resolve, pumps the loop, walks to the Section I and Section IV
    SectionHeaders by title, and asserts each header and its containing
    column has positive height. Pre-fix both RowLayouts collapse to 0
    (exit 5/6). Post-fix they size to the tallest section (exit 0).
    """
    script = _HARNESS_B.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        desk=repr(str(_DESK_SURFACE)),
    )
    _run(script, "DeskSurface Row-1/Row-2 non-zero height")


@_skip_no_qt
def test_section_headers_have_editorial_primitives() -> None:
    """Every DeskSurface SectionHeader must expose the editorial primitives:
    a serif-letter Text (objectName 'sectionHeaderLetter') and a
    border.subtle hairline Rectangle (objectName 'sectionHeaderRule').
    Catches a silently dropped lettermark (gate G3, presence half).
    """
    script = _HARNESS_C.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        desk=repr(str(_DESK_SURFACE)),
    )
    _run(script, "SectionHeader editorial-primitive presence")


@_skip_no_qt
def test_no_foreign_chrome_idioms() -> None:
    """No SegmentedToggle may contain ANY Rectangle descendant, and no
    FunnelRow may contain a magnitude-fill Rectangle (only its tagged
    hairline). Catches a silently RETAINED chrome idiom — the exact
    mechanism that produced the original regression (gate G3, absence half).
    """
    script = _HARNESS_D.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
    )
    _run(script, "no foreign chrome idioms (toggle/funnel)")


@_skip_no_qt
def test_desk_loads_clean_after_sparkline_change() -> None:
    """DeskSurface must still load with zero LOAD-TIME QML errors after the
    Sparkline hairline opt-in.

    Note: this does NOT catch Canvas onPaint runtime exceptions (Qt swallows
    those); FRONT-safety rests on the change being strictly additive + the
    full-suite run.
    """
    script = _HARNESS_E.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        desk=repr(str(_DESK_SURFACE)),
    )
    _run(script, "DeskSurface clean-load post-sparkline")


_HARNESS_A = r'''
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile, pathlib
from PySide6.QtCore import QUrl, QTimer, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from milodex.gui.qml_setup import register_qml_types
from milodex.gui.theme_manager import ThemeManager

_msgs = []
def _handler(mode, ctx, message):
    _msgs.append(str(message))
qInstallMessageHandler(_handler)

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
tm = ThemeManager()
register_qml_types(tm)

# A QQuickView with .show() is REQUIRED to reproduce the bug: the polish
# loop only fires during the scene-graph polish pass, which QQmlComponent
# alone never schedules. This is exactly why the headless load-smoke gate
# (QQmlApplicationEngine, no view) missed it.
qml = b"""
import QtQuick
import Milodex 1.0

Item {{
    id: probe
    width: 600
    height: 400

    // Case 1 - composed inside a parent whose implicit size derives FROM
    // the toggle. This is the exact feedback topology the bug needs: if
    // the toggle has an internal parent<->child implicit cycle, wrapping
    // it so the parent reads its implicit size wedges the polish pass.
    Item {{
        id: wrap
        implicitWidth:  intrinsic.implicitWidth
        implicitHeight: intrinsic.implicitHeight
        SegmentedToggle {{
            id: intrinsic
            options: [
                {{ label: "Today", value: "Today" }},
                {{ label: "Week",  value: "Week"  }},
                {{ label: "Month", value: "Month" }}
            ]
            current: "Week"
        }}
    }}

    // Case 2 - explicit size given (must fill it, still no loop).
    SegmentedToggle {{
        id: composed
        anchors.top: wrap.bottom
        width: 420
        height: 36
        options: [
            {{ label: "All",     value: "All" }},
            {{ label: "Orders",  value: "order" }},
            {{ label: "Signals", value: "signal" }}
        ]
        current: "All"
    }}
}}
"""

_qml_file = pathlib.Path(tempfile.mktemp(suffix=".qml"))
_qml_file.write_bytes(qml)

view = QQuickView()
view.engine().addImportPath({import_root})
view.setSource(QUrl.fromLocalFile(str(_qml_file)))
if view.status() == QQuickView.Error:
    for e in view.errors():
        print(str(e.toString()), file=sys.stderr)
    sys.exit(2)
if view.rootObject() is None:
    print("rootObject() is None", file=sys.stderr)
    sys.exit(3)
view.resize(600, 400)
view.show()

# Pump the loop so the polish pass runs (and, if the cycle is present,
# re-runs thousands of times).
QTimer.singleShot(1200, app.quit)
app.exec()

polish = [m for m in _msgs
          if ("polish()" in m or "updatePolish" in m or "called polish" in m)]
if polish:
    print("captured " + str(len(polish)) + " polish-loop messages; first 3:",
          file=sys.stderr)
    for m in polish[:3]:
        print(m, file=sys.stderr)
    sys.exit(5)

print("NO_POLISH_LOOP (" + str(len(_msgs)) + " benign msgs)")
sys.exit(0)
'''

_HARNESS_B = r"""
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
    raise RuntimeError("regression-test: no broker")

op = OperationalState(
    broker_client_factory=_failing_broker,
    kill_switch_store=ks_store,
    trading_mode="paper",
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)

_ne = Path("/__nonexistent_desk_layout_test__")
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

_root = Path(tempfile.mkdtemp(prefix="milodex_desklayout_"))
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
view.setSource(QUrl.fromLocalFile({desk}))

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

sec1 = None
sec4 = None
for it in _walk(root):
    t = it.property("title")
    if t == "Risk & Mode":
        sec1 = it
    elif t == "Risk Layer Throughput":
        sec4 = it

if sec1 is None:
    print("Section I header (Risk & Mode) not found", file=sys.stderr)
    sys.exit(4)
if sec4 is None:
    print("Section IV header (Risk Layer Throughput) not found", file=sys.stderr)
    sys.exit(4)

h1 = sec1.height()
p1 = sec1.parentItem()
ph1 = p1.height() if p1 is not None else 0.0
h4 = sec4.height()
p4 = sec4.parentItem()
ph4 = p4.height() if p4 is not None else 0.0

print("Section I  header.height=" + str(h1) + " column.height=" + str(ph1))
print("Section IV header.height=" + str(h4) + " column.height=" + str(ph4))

if not (h1 > 0 and ph1 > 0):
    print("ROW 1 COLLAPSED: header.height=" + str(h1) + " column.height=" + str(ph1),
          file=sys.stderr)
    sys.exit(5)
if not (h4 > 0 and ph4 > 0):
    print("ROW 2 COLLAPSED: header.height=" + str(h4) + " column.height=" + str(ph4),
          file=sys.stderr)
    sys.exit(6)

print("ROWS_NONZERO")
sys.exit(0)
"""

_HARNESS_C = r"""
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
    raise RuntimeError("regression-test: no broker")

op = OperationalState(
    broker_client_factory=_failing_broker,
    kill_switch_store=ks_store,
    trading_mode="paper",
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)

_ne = Path("/__nonexistent_desk_layout_test__")
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

_root = Path(tempfile.mkdtemp(prefix="milodex_desklayout_"))
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
view.setSource(QUrl.fromLocalFile({desk}))

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

headers = [it for it in _walk(root)
           if it.property("title") not in (None, "")
           and it.metaObject().className().startswith("SectionHeader")]
if len(headers) < 7:
    print("expected >=7 SectionHeaders, found " + str(len(headers)), file=sys.stderr)
    sys.exit(4)

bad = []
for h in headers:
    kids = list(_walk(h))
    has_letter = any(k.property("objectName") == "sectionHeaderLetter" for k in kids)
    has_rule   = any(k.property("objectName") == "sectionHeaderRule"   for k in kids)
    if not (has_letter and has_rule):
        bad.append((h.property("title"), has_letter, has_rule))

if bad:
    for t, l, r in bad:
        print("MISSING PRIMITIVE title=" + str(t)
              + " letter=" + str(l) + " rule=" + str(r), file=sys.stderr)
    sys.exit(5)

print("SECTION_HEADER_PRIMITIVES_OK (" + str(len(headers)) + " headers)")
sys.exit(0)
"""

_HARNESS_D = r'''
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile, pathlib
from PySide6.QtCore import QUrl, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from milodex.gui.qml_setup import register_qml_types
from milodex.gui.theme_manager import ThemeManager

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
tm = ThemeManager()
register_qml_types(tm)

# Probe both component types with minimal sample data — no read-models needed.
# A QML component definition either contains a Rectangle or it does not,
# independent of whether any data-bound DeskSurface instantiated it.
qml = b"""
import QtQuick
import Milodex 1.0

Item {{
    id: probe
    width: 600
    height: 200
    SegmentedToggle {{
        id: tg
        options: [
            {{ label: "Today", value: "Today" }},
            {{ label: "Week",  value: "Week"  }},
            {{ label: "Month", value: "Month" }}
        ]
        current: "Week"
    }}
    FunnelRow {{
        id: fn
        anchors.top: tg.bottom
        width: 400
        label: "Evaluations"
        gloss: "gate inputs"
        value: "142"
    }}
}}
"""

_qml_file = pathlib.Path(tempfile.mktemp(suffix=".qml"))
_qml_file.write_bytes(qml)

view = QQuickView()
view.engine().addImportPath({import_root})
view.setSource(QUrl.fromLocalFile(str(_qml_file)))
if view.status() == QQuickView.Error:
    for e in view.errors():
        print(str(e.toString()), file=sys.stderr)
    sys.exit(2)
if view.rootObject() is None:
    print("rootObject() is None", file=sys.stderr)
    sys.exit(3)
view.resize(600, 200)
view.show()

QTimer.singleShot(1200, app.quit)
app.exec()

root = view.rootObject()

def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

def _cls(it): return it.metaObject().className()

toggles = [it for it in _walk(root) if _cls(it).startswith("SegmentedToggle")]
funnels  = [it for it in _walk(root) if _cls(it).startswith("FunnelRow")]

missing = []
if not toggles:
    missing.append("SegmentedToggle")
if not funnels:
    missing.append("FunnelRow")
if missing:
    print("PROBE MISSING: " + ", ".join(missing) + " — assertion would be vacuous",
          file=sys.stderr)
    sys.exit(4)

violations = []
for tg in toggles:
    rects = [k for k in _walk(tg) if k is not tg
             and _cls(k).startswith("QQuickRectangle")]
    if rects:
        violations.append("SegmentedToggle has " + str(len(rects))
                          + " Rectangle(s) — must be type-only")

for fn in funnels:
    rects = [k for k in _walk(fn) if k is not fn
             and _cls(k).startswith("QQuickRectangle")]
    bad = [r for r in rects if r.property("objectName") != "funnelRule"]
    if bad:
        violations.append("FunnelRow has " + str(len(bad))
                          + " non-hairline Rectangle(s) — bar not removed")

if violations:
    for v in violations: print("CHROME RETAINED: " + v, file=sys.stderr)
    sys.exit(5)

print("NO_FOREIGN_CHROME (" + str(len(toggles)) + " toggles, "
      + str(len(funnels)) + " funnels)")
sys.exit(0)
'''

_HARNESS_E = r"""
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
    raise RuntimeError("regression-test: no broker")

op = OperationalState(
    broker_client_factory=_failing_broker,
    kill_switch_store=ks_store,
    trading_mode="paper",
    kill_switch_poll_seconds=9999.0,
    broker_poll_seconds=9999.0,
)

_ne = Path("/__nonexistent_desk_layout_test__")
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

_root = Path(tempfile.mkdtemp(prefix="milodex_desklayout_"))
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
view.setSource(QUrl.fromLocalFile({desk}))

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

errs = view.errors() if hasattr(view, "errors") else []
if view.status() == QQuickView.Error or errs:
    for e in errs: print(str(e.toString()), file=sys.stderr)
    sys.exit(5)
print("DESK_CLEAN_LOAD")
sys.exit(0)
"""
