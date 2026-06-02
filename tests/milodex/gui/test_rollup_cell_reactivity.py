"""PR10 reactivity dispute — proves (or disproves) that RollupCell._valueColor
stays reactive when tone changes at runtime.

The binding form under test:
    readonly property color _valueColor: Formatters.toneColor(root.tone)

Reviewer A claims this is a one-time static assignment because toneColor is a
function on a foreign singleton — the binding engine won't track root.tone as
a dependency.

Reviewer B (our prior) says QML's binding engine captures dependencies
dynamically during evaluation: root.tone IS read in the expression, so the
binding IS reactive.

Step 1 — reactivity proof:
  Load an inline QML component with the exact same binding form, set tone at
  runtime, process events, assert the color changed.

Step 2 — exact token assertions for toneColor:
  Compare each tone's resolved color against the known Theme token values
  (EditorialDark, the harness default), so a silent wrong-token mapping fails.

Step 3 — shortTime 12h case:
  Assert formatted output structure for a concrete UTC input (AM/PM, HH:MM,
  24h ≠ 12h output for the same input).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Infrastructure — identical pattern to test_formatters_singleton.py
# ---------------------------------------------------------------------------

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"

try:
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping QML reactivity tests",
)

# The harness setup spawned in a subprocess: registers ThemeManager (→ editorial-dark)
# and Formatters via register_qml_types, then creates a QQmlApplicationEngine.
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


def _build_probe_script(qml_source: str, assertions: str) -> str:
    return (
        _HARNESS_SETUP
        + f"""
component = QQmlComponent(engine)
component.setData({qml_source!r}.encode('utf-8'), QUrl())
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
"""
        + assertions
    )


def _run(script: str, label: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"Probe FAILED for {label}\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Step 1 — Reactivity test: does _valueColor track tone changes at runtime?
#
# QML component uses the EXACT binding form from RollupCell line 36:
#   readonly property color _valueColor: Formatters.toneColor(root.tone)
#
# We set tone="muted", read _valueColor, then set tone="warning", process
# events, and assert the color changed.  If QML's binding engine did NOT
# capture root.tone as a dependency, the color would be stuck at muted.
# ---------------------------------------------------------------------------

_REACTIVITY_PROBE_QML = """\
import QtQuick
import Milodex 1.0

Item {
    id: root
    property string tone: "muted"

    // Exact binding form from RollupCell.qml line 36.
    readonly property color _valueColor: Formatters.toneColor(root.tone)
}
"""

_REACTIVITY_ASSERTIONS = """\
from PySide6.QtCore import QCoreApplication

# Read color at initial tone ("muted").
color_muted = obj.property("_valueColor")
assert color_muted is not None, "Initial _valueColor is None"

# Mutate tone to "warning" at runtime.
ok = obj.setProperty("tone", "warning")
assert ok, "setProperty('tone', 'warning') returned False — property not writable?"

# Process pending events so QML binding re-evaluates.
QCoreApplication.processEvents()

color_warning = obj.property("_valueColor")
assert color_warning is not None, "Post-mutation _valueColor is None"

# THE REACTIVITY ASSERTION: the color must have changed.
assert color_muted != color_warning, (
    f"REACTIVITY BROKEN: _valueColor did not change when tone changed "
    f"muted→warning.  muted={color_muted!r}  warning={color_warning!r}  "
    f"Binding Formatters.toneColor(root.tone) is NOT reactive — "
    f"root.tone is not tracked as a dependency."
)

# Sanity: flip back to "positive" and verify another distinct color.
obj.setProperty("tone", "positive")
QCoreApplication.processEvents()
color_positive = obj.property("_valueColor")
assert color_positive != color_muted, (
    f"positive color must differ from muted: {color_positive!r} == {color_muted!r}"
)
assert color_positive != color_warning, (
    f"positive color must differ from warning: {color_positive!r} == {color_warning!r}"
)

print(
    f"REACTIVITY PASS  muted={color_muted!r}  "
    f"warning={color_warning!r}  positive={color_positive!r}"
)
"""


@_skip_no_qt
def test_rollup_cell_value_color_is_reactive() -> None:
    """RollupCell._valueColor binding is reactive: tone change propagates at runtime.

    Proves or disproves the reviewer dispute. If QML dependency capture is dynamic
    (Reviewer B's claim), root.tone is read inside the Formatters.toneColor(root.tone)
    call during binding evaluation, so the engine records it as a dependency and
    re-evaluates the binding when tone changes. This test will PASS if the claim
    is correct.

    A FAIL here means the binding is genuinely static and the minimal fix
    (wrapping in a JS block to force explicit read) must be applied.
    """
    _run(
        _build_probe_script(_REACTIVITY_PROBE_QML, _REACTIVITY_ASSERTIONS),
        "RollupCell reactivity",
    )


# ---------------------------------------------------------------------------
# Step 2 — Exact toneColor token assertions.
#
# The harness uses editorial-dark (the ThemeManager default).  Known token
# values from EditorialDark.qml:
#   brand    → color.brand.primary   = "#ecd6a5"
#   positive → status.positive       = "#a8c4ab"
#   negative → status.negative       = "#df805e"
#   warning  → status.warning        = "#d5a566"
#   muted    → color.text.muted      = "#9c8c6c"
#   data     → color.text.primary    = "#e4d2a8"
#   default  → color.text.primary    = "#e4d2a8"  (same as "data")
#
# We load the Theme singleton in the same engine and read the tokens directly,
# then compare against what toneColor returns — so this test stays correct even
# if the theme values change (it compares token→toneColor, not hardcoded hex).
# ---------------------------------------------------------------------------

_EXACT_TOKEN_PROBE_QML = """\
import QtQuick
import Milodex 1.0

QtObject {
    // toneColor resolved colors
    property color tc_brand:    Formatters.toneColor("brand")
    property color tc_positive: Formatters.toneColor("positive")
    property color tc_negative: Formatters.toneColor("negative")
    property color tc_warning:  Formatters.toneColor("warning")
    property color tc_muted:    Formatters.toneColor("muted")
    property color tc_data:     Formatters.toneColor("data")
    property color tc_default:  Formatters.toneColor("unknown_xyz")

    // Theme token values read directly — ground truth.
    property color th_brand:    Theme.color.brand.primary
    property color th_positive: Theme.status.positive
    property color th_negative: Theme.status.negative
    property color th_warning:  Theme.status.warning
    property color th_muted:    Theme.color.text.muted
    property color th_primary:  Theme.color.text.primary
}
"""

_EXACT_TOKEN_ASSERTIONS = """\
# For each tone, toneColor() must return EXACTLY the expected Theme token value.
# We compare QColor objects returned by QML — if a wrong token is mapped
# (e.g. "brand" resolves to text.secondary instead of brand.primary), the
# comparison fails even if the resulting colour looks "close enough".

cases = [
    ("brand",    "tc_brand",    "th_brand",    "Theme.color.brand.primary"),
    ("positive", "tc_positive", "th_positive", "Theme.status.positive"),
    ("negative", "tc_negative", "th_negative", "Theme.status.negative"),
    ("warning",  "tc_warning",  "th_warning",  "Theme.status.warning"),
    ("muted",    "tc_muted",    "th_muted",    "Theme.color.text.muted"),
    ("data",     "tc_data",     "th_primary",  "Theme.color.text.primary"),
    ("default",  "tc_default",  "th_primary",  "Theme.color.text.primary"),
]
all_ok = True
for tone, tc_prop, th_prop, token_name in cases:
    tc = obj.property(tc_prop)
    th = obj.property(th_prop)
    if tc != th:
        print(
            f"FAIL toneColor({tone!r}) → {tc!r} but expected {token_name} = {th!r}",
            file=sys.stderr,
        )
        all_ok = False
    else:
        print(f"OK   toneColor({tone!r}) == {token_name} ({tc!r})")

# data and default must be identical (both → text.primary).
tc_data = obj.property("tc_data")
tc_default = obj.property("tc_default")
if tc_data != tc_default:
    print(
        f"FAIL toneColor('data') != toneColor('unknown') — both must map to text.primary: "
        f"{tc_data!r} != {tc_default!r}",
        file=sys.stderr,
    )
    all_ok = False

# All six editorial tones must be mutually distinct.
tone_colors = {
    "brand":    obj.property("tc_brand"),
    "positive": obj.property("tc_positive"),
    "negative": obj.property("tc_negative"),
    "warning":  obj.property("tc_warning"),
    "muted":    obj.property("tc_muted"),
    "data":     obj.property("tc_data"),
}
seen = {}
for tone_name, color in tone_colors.items():
    for prev_name, prev_color in seen.items():
        if color == prev_color:
            print(
                f"FAIL toneColor({tone_name!r}) == toneColor({prev_name!r}) — "
                f"all six editorial tones must be distinct: {color!r}",
                file=sys.stderr,
            )
            all_ok = False
    seen[tone_name] = color

if not all_ok:
    sys.exit(5)
print("EXACT TOKEN PASS")
"""


@_skip_no_qt
def test_tone_color_maps_exact_theme_tokens() -> None:
    """toneColor maps every tone to the exact Theme token, not just any non-null color.

    Strengthens test_formatters_tone_color (which only checks non-null and
    positive≠negative).  This test catches a silent wrong-token mapping, e.g.
    "brand" resolving to text.secondary instead of brand.primary.

    Tokens compared against live Theme singleton values (editorial-dark default)
    so this test stays self-consistent if theme values change.
    All six editorial tones (brand/positive/negative/warning/muted/data) must
    also be mutually distinct.
    """
    _run(
        _build_probe_script(_EXACT_TOKEN_PROBE_QML, _EXACT_TOKEN_ASSERTIONS),
        "toneColor exact token mapping",
    )


# ---------------------------------------------------------------------------
# Step 3 — shortTime 12h concrete case.
#
# 2026-06-01T20:30:00.000Z is 20:30 UTC.
#   24h output: "20:30"  (guaranteed regardless of local timezone — we assert
#                          it matches HH:MM format and is non-AM/PM)
#   12h output: local-timezone-dependent (JS Date uses local tz), so we cannot
#               assert the exact hour without knowing the test runner's tz.
#               We assert:
#                 - Contains ":" exactly once before AM/PM
#                 - Contains "AM" or "PM"
#                 - 12h output ≠ 24h output (same input, different fmt)
#
# We also pin that 12:00 UTC noon formatted 12h reads as "12:00 PM" in UTC,
# which is timezone-invariant: noon UTC = noon local iff tz=UTC, but the
# assertion we actually need is just about structure not absolute hour value.
# The safe structure assertions are:
#   1. 12h output contains "AM" or "PM"
#   2. 24h output contains neither "AM" nor "PM"
#   3. Both contain ":"
#   4. 12h ≠ 24h for the same ISO input
#   5. null/empty → "—" for both modes (already covered by existing sentinel test)
# ---------------------------------------------------------------------------

_SHORTTIME_EXTENDED_PROBE_QML = """\
import QtQuick
import Milodex 1.0

QtObject {
    // A time that is definitely PM in any timezone west of UTC+9 (20:30 UTC):
    // even UTC+9 gives 05:30 local which is AM not PM, but that doesn't matter —
    // we assert STRUCTURE not absolute AM/PM value.
    property string t24_afternoon: Formatters.shortTime("2026-06-01T20:30:00.000Z", "24h")
    property string t12_afternoon: Formatters.shortTime("2026-06-01T20:30:00.000Z", "12h")

    // A time that is definitely AM in UTC (09:05 UTC):
    property string t24_morning: Formatters.shortTime("2026-06-01T09:05:00.000Z", "24h")
    property string t12_morning: Formatters.shortTime("2026-06-01T09:05:00.000Z", "12h")

    // Sentinel edge cases
    property string null24: Formatters.shortTime(null, "24h")
    property string null12: Formatters.shortTime(null, "12h")
    property string empty24: Formatters.shortTime("", "24h")
    property string empty12: Formatters.shortTime("", "12h")
}
"""

_SHORTTIME_EXTENDED_ASSERTIONS = """\
import re

def assert_24h_structure(val, label):
    assert ":" in val, f"{label}: 24h must contain ':': {val!r}"
    assert "AM" not in val and "PM" not in val, (
        f"{label}: 24h must not contain AM/PM: {val!r}"
    )
    # Must match HH:MM (two digits colon two digits)
    assert re.fullmatch(r'\\d{2}:\\d{2}', val), (
        f"{label}: 24h must match HH:MM pattern: {val!r}"
    )

def assert_12h_structure(val, label):
    assert ":" in val, f"{label}: 12h must contain ':': {val!r}"
    assert "AM" in val or "PM" in val, (
        f"{label}: 12h must contain AM or PM: {val!r}"
    )
    # Must match H:MM AM or H:MM PM (1-2 digits, colon, 2 digits, space, AM/PM)
    assert re.fullmatch(r'\\d{1,2}:\\d{2} (AM|PM)', val), (
        f"{label}: 12h must match H:MM AM/PM pattern: {val!r}"
    )

# Sentinel checks
assert obj.property("null24") == "\\u2014", f"null 24h→ {obj.property('null24')!r}"
assert obj.property("null12") == "\\u2014", f"null 12h→ {obj.property('null12')!r}"
assert obj.property("empty24") == "\\u2014", f"'' 24h→ {obj.property('empty24')!r}"
assert obj.property("empty12") == "\\u2014", f"'' 12h→ {obj.property('empty12')!r}"

t24_afternoon = obj.property("t24_afternoon")
t12_afternoon = obj.property("t12_afternoon")
t24_morning   = obj.property("t24_morning")
t12_morning   = obj.property("t12_morning")

assert_24h_structure(t24_afternoon, "afternoon 24h")
assert_12h_structure(t12_afternoon, "afternoon 12h")
assert_24h_structure(t24_morning, "morning 24h")
assert_12h_structure(t12_morning, "morning 12h")

# 12h output must differ from 24h output for the same input.
assert t12_afternoon != t24_afternoon, (
    f"12h and 24h must differ for the same input: "
    f"t12={t12_afternoon!r}  t24={t24_afternoon!r}"
)
assert t12_morning != t24_morning, (
    f"12h and 24h must differ for the same input: "
    f"t12={t12_morning!r}  t24={t24_morning!r}"
)

print(
    f"SHORTTIME PASS  afternoon: 24h={t24_afternoon!r} 12h={t12_afternoon!r}  "
    f"morning: 24h={t24_morning!r} 12h={t12_morning!r}"
)
"""


@_skip_no_qt
def test_formatters_short_time_12h_structure() -> None:
    """Formatters.shortTime: 12h output has H:MM AM/PM structure and differs from 24h.

    Strengthens the existing sentinel-only test with structure assertions on
    non-null inputs.  Timezone-agnostic: asserts format pattern (H:MM AM/PM)
    and that 12h≠24h for the same UTC input, without pinning the absolute hour.
    """
    _run(
        _build_probe_script(_SHORTTIME_EXTENDED_PROBE_QML, _SHORTTIME_EXTENDED_ASSERTIONS),
        "shortTime 12h structure",
    )
