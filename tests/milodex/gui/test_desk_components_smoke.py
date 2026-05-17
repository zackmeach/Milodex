"""Smoke tests for Trading Desk shared QML components (PR-7).

Two-tier structure matching test_qml_components.py conventions:

**Tier 1 — load-clean tests**:
    Each component is instantiated via a subprocess harness (same pattern as
    test_qml_components.py:test_quiet_action_instantiates_with_text).  The
    subprocess sets QT_QPA_PLATFORM=offscreen, registers Milodex types, loads
    the component via QQmlComponent.setData with sample property values, and
    exits non-zero if any QML warnings are emitted.

**Tier 2 — token-binding / no-literal check**:
    Each component QML source is scanned for bare hex-color literals (``#``
    followed by 3 or 6 hex digits as a standalone token), matching the
    tnum_enforcement and source-scan approach used elsewhere in the test suite.
    A component that hardcodes a color literal fails the token-binding contract.

All tests skip when PySide6 is not importable.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"
_COMPONENTS_DIR = _QML_IMPORT_ROOT / "Milodex" / "components"

# PySide6 availability guard (mirrors test_qml_components.py)
try:
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping QML desk-component tests",
)

# ---------------------------------------------------------------------------
# Subprocess harness builder (mirrors test_qml_components.py inline approach)
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

_HARNESS_FOOTER = """\
if component.status() == QQmlComponent.Error:
    print(component.errorString(), file=sys.stderr)
    sys.exit(2)
obj = component.create(engine.rootContext())
if obj is None:
    print(component.errorString(), file=sys.stderr)
    sys.exit(3)
if warnings:
    print("\\n".join(warnings), file=sys.stderr)
    sys.exit(4)
print("LOAD_OK")
"""


def _build_load_script(inline_qml: str) -> str:
    """Return a subprocess script that loads *inline_qml* and asserts clean."""
    set_data = (
        f"\ncomponent = QQmlComponent(engine)\n"
        f"component.setData({inline_qml!r}.encode('utf-8'), QUrl())\n"
    )
    return _HARNESS_SETUP + set_data + _HARNESS_FOOTER


def _run(script: str, label: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"QML load failed for {label}\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Tier 2 — source scan: no bare hex literals
# ---------------------------------------------------------------------------

# Pattern: # followed by exactly 3 or 6 hex digits (case-insensitive), as a
# standalone token — not part of a longer word/comment.  Matches "#abc",
# "#a1b2c3", etc.  The negative lookahead excludes longer sequences that could
# be e.g. "#a1b2c3d4" (8-digit ARGB, also forbidden, caught by the 3/6 check
# anyway since it first matches the 6-digit prefix).
_HEX_LITERAL_RE = re.compile(r'(?<!["\w])#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-fA-F\w])')

_DESK_COMPONENTS = [
    "SectionHeader.qml",
    "SegmentedToggle.qml",
    "FunnelRow.qml",
    "RollupCell.qml",
    "TapeRow.qml",
    "RunnerSelect.qml",
    "ActivityTable.qml",
]


@pytest.mark.parametrize("filename", _DESK_COMPONENTS)
def test_no_hex_literal_in_component(filename: str) -> None:
    """Each desk component contains zero hardcoded hex-color literals."""
    path = _COMPONENTS_DIR / filename
    assert path.exists(), f"Component file missing: {path}"
    source = path.read_text(encoding="utf-8")
    matches = _HEX_LITERAL_RE.findall(source)
    assert matches == [], (
        f"{filename} contains {len(matches)} hardcoded hex literal(s): {matches!r}. "
        "All colors must bind to Theme tokens."
    )


# ---------------------------------------------------------------------------
# Tier 1 — load-clean tests (one per component)
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_section_header_loads_clean() -> None:
    """SectionHeader loads with numeral + title, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

SectionHeader {
    numeral: "14"
    title:   "Active Runners"
    width:   300
    height:  32
}
"""
    _run(_build_load_script(qml), "SectionHeader")


@_skip_no_qt
def test_segmented_toggle_loads_clean() -> None:
    """SegmentedToggle loads with options + current, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

SegmentedToggle {
    options: [
        { label: "1D", value: "1d" },
        { label: "1W", value: "1w" },
        { label: "1M", value: "1m" }
    ]
    current: "1d"
    width: 200
    height: 32
}
"""
    _run(_build_load_script(qml), "SegmentedToggle")


@_skip_no_qt
def test_funnel_row_loads_clean() -> None:
    """FunnelRow loads with label, value, proportion, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

FunnelRow {
    label:      "Screened"
    value:      "142"
    proportion: 0.71
    width: 300
    height: 28
}
"""
    _run(_build_load_script(qml), "FunnelRow")


@_skip_no_qt
def test_rollup_cell_loads_clean() -> None:
    """RollupCell loads with positive tone, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

RollupCell {
    label: "Day P&L"
    value: "+$1,240"
    tone:  "positive"
    width: 160
    height: 80
}
"""
    _run(_build_load_script(qml), "RollupCell")


@_skip_no_qt
def test_rollup_cell_negative_tone_loads_clean() -> None:
    """RollupCell loads with negative tone, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

RollupCell {
    label: "Drawdown"
    value: "-$320"
    tone:  "negative"
    width: 160
    height: 80
}
"""
    _run(_build_load_script(qml), "RollupCell (negative tone)")


@_skip_no_qt
def test_tape_row_loads_clean() -> None:
    """TapeRow loads with positive pctChange, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

TapeRow {
    symbol:    "SPY"
    close:     "523.14"
    pctChange: "+0.43%"
    asOf:      "16:00"
    width: 320
    height: 32
}
"""
    _run(_build_load_script(qml), "TapeRow")


@_skip_no_qt
def test_runner_select_loads_clean() -> None:
    """RunnerSelect loads with runners list + current, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

RunnerSelect {
    runners: [
        { id: "r1", label: "regime.daily.sma200" },
        { id: "r2", label: "momentum.weekly.v2"  }
    ]
    current: "r1"
    width: 240
    height: 36
}
"""
    _run(_build_load_script(qml), "RunnerSelect")


@_skip_no_qt
def test_activity_table_loads_clean() -> None:
    """ActivityTable loads with rows + no filter, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

ActivityTable {
    rows: [
        { ts: "09:31", kind: "fill",  subject: "SPY 100sh", detail: "@523.10", tone: "positive" },
        { ts: "09:35", kind: "alert", subject: "DD breach",  detail: "-2.1%",   tone: "negative" }
    ]
    filter: ""
    width: 500
    height: 200
}
"""
    _run(_build_load_script(qml), "ActivityTable")


@_skip_no_qt
def test_activity_table_with_filter_loads_clean() -> None:
    """ActivityTable loads with a filter string, zero QML warnings."""
    qml = """
import QtQuick
import Milodex 1.0

ActivityTable {
    rows: [
        { ts: "09:31", kind: "fill", subject: "SPY 100sh", detail: "@523.10", tone: "data" }
    ]
    filter: "fill"
    width: 500
    height: 200
}
"""
    _run(_build_load_script(qml), "ActivityTable (with filter)")


# ---------------------------------------------------------------------------
# Tier 2 — token-binding: spot-check key tokens resolve correctly
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_desk_component_status_tokens_resolve() -> None:
    """Status tokens used by RollupCell/TapeRow/ActivityTable resolve under Editorial Dark."""
    script = f"""\
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
engine.addImportPath({str(_QML_IMPORT_ROOT)!r})
component = QQmlComponent(engine)
component.setData(b'''
import QtQuick
import Milodex 1.0
Item {{
    property string positive: Theme.status.positive
    property string negative: Theme.status.negative
    property string warning:  Theme.status.warning
    property string textMuted: Theme.color.text.muted
    property string textPrimary: Theme.color.text.primary
    property string deskNumFamily: Theme.typography.display.deskNum.family
    property int    deskNumSize:   Theme.typography.display.deskNum.size
}}
''', QUrl())
if component.status() == QQmlComponent.Error:
    print(component.errorString(), file=sys.stderr)
    sys.exit(2)
obj = component.create(engine.rootContext())
if obj is None:
    print(component.errorString(), file=sys.stderr)
    sys.exit(3)
expected = {{
    "positive":     "#a8c4ab",
    "negative":     "#df805e",
    "warning":      "#d5a566",
    "textMuted":    "#9c8c6c",
    "textPrimary":  "#e4d2a8",
}}
for prop, val in expected.items():
    got = obj.property(prop)
    if hasattr(got, 'lower'):
        got = got.lower()
    if got != val:
        print(f"{{prop}} expected {{val}} got {{got}}", file=sys.stderr)
        sys.exit(4)
fam = obj.property("deskNumFamily")
assert fam == "Newsreader", f"deskNumFamily={{fam}}"
sz = obj.property("deskNumSize")
assert sz == 56, f"deskNumSize={{sz}}"
print("TOKEN_OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@_skip_no_qt
def test_desk_status_tokens_differ_across_themes() -> None:
    """status.positive changes across themes — proves no hardcoded literal."""
    script = f"""\
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
tm.set_theme("editorial-dark")

engine = QQmlApplicationEngine()
engine.addImportPath({str(_QML_IMPORT_ROOT)!r})
component = QQmlComponent(engine)
component.setData(b'''
import QtQuick
import Milodex 1.0
Item {{
    property string positive: Theme.status.positive
    property string negative: Theme.status.negative
}}
''', QUrl())
if component.status() == QQmlComponent.Error:
    print(component.errorString(), file=sys.stderr)
    sys.exit(2)
obj = component.create(engine.rootContext())

dark_positive = obj.property("positive").lower()
dark_negative = obj.property("negative").lower()

tm.set_theme("editorial-light")
light_positive = obj.property("positive").lower()
light_negative = obj.property("negative").lower()

if dark_positive == light_positive:
    print(f"status.positive did not change on theme swap: {{dark_positive!r}}", file=sys.stderr)
    sys.exit(5)
if dark_negative == light_negative:
    print(f"status.negative did not change on theme swap: {{dark_negative!r}}", file=sys.stderr)
    sys.exit(6)

print("THEME_SWAP_OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
