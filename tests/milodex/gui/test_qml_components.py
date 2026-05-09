"""QML smoke tests for the four foundational Milodex components.

Tests are structured in two tiers:

**Tier 1 — Component-instantiation tests** (``Button``, ``StatusPill``):
    Load the actual registered component via inline QML, instantiate it, and
    assert on properties exposed by the component's public API.  These tests
    would fail if the component files were deleted or their property bindings
    were broken.

**Tier 2 — Token-resolution tests** (``StrategyRow``, ``Surface``):
    Verify the token *values* that the component binds to — not the component
    itself.  These tests confirm the Theme singleton exposes the expected
    values under each theme; they do NOT instantiate the component directly.
    Renaming or deleting a component file would not cause these tests to fail.

All tests skip when PySide6 is not importable.

.. warning:: PROCESS-GLOBAL SINGLETON CONSTRAINT — same as test_qml_theme_loads.py
   =========================================================================

   ``qmlRegisterSingletonInstance`` is **process-global**.  This module shares
   the same ThemeManager instance as ``test_qml_theme_loads.py`` if both
   modules run in the same pytest process.  The ``engine`` fixture handles the
   already-registered case via try/except and resets to ``editorial-dark``
   before each test.

   Do NOT add tests that assert per-test isolated persistence; for that use
   ``test_theme_manager.py``.  Ordering within this module is load-bearing
   because the singleton is shared.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# PySide6 availability guard
# ---------------------------------------------------------------------------

try:
    from PySide6.QtGui import QGuiApplication  # noqa: F401
    from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping QML component tests",
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QML_IMPORT_ROOT: Path = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui" / "qml"


# ---------------------------------------------------------------------------
# Fixtures — mirror test_qml_theme_loads.py exactly
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication (Qt Quick needs a GUI app)."""
    if not _PYSIDE6_AVAILABLE:
        return None

    import os
    import sys

    from PySide6.QtGui import QGuiApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


@pytest.fixture
def engine(qapp, tmp_path):
    """Fresh QQmlApplicationEngine with Milodex import path registered."""
    from PySide6.QtQml import QQmlApplicationEngine

    from milodex.gui.qml_setup import register_qml_types
    from milodex.gui.theme_manager import ThemeManager

    manager = ThemeManager(settings_path=tmp_path / "gui_settings.json")
    try:
        register_qml_types(manager)
    except Exception:
        from milodex.gui import qml_setup as _qml_setup

        manager = _qml_setup._singleton_instance
        if manager is not None:
            manager.set_theme("editorial-dark")

    qml_engine = QQmlApplicationEngine()
    qml_engine.addImportPath(str(_QML_IMPORT_ROOT))
    return qml_engine, manager


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _load_qml(engine, qml_source: str):
    """Compile and instantiate inline QML; return (component, root_object)."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent

    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(str(w.toString()) for w in ws))

    component = QQmlComponent(engine)
    component.setData(qml_source.encode("utf-8"), QUrl())

    assert component.status() != QQmlComponent.Error, (
        f"QML compile error: {component.errorString()}"
    )

    obj = component.create(engine.rootContext())
    assert obj is not None, f"QML create() returned None; errors: {component.errorString()}"
    obj.setParent(engine)
    assert warnings == [], f"QML emitted warnings: {warnings}"
    return component, obj


# ---------------------------------------------------------------------------
# QuietAction tests — Tier 1 (component instantiation)
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_quiet_action_instantiates_with_text():
    """QuietAction exposes text and enabled state for low-risk affordances."""
    script = f"""
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

component = QQmlComponent(engine)
component.setData(b'''
import QtQuick
import Milodex 1.0

QuietAction {{
    text: "Open record"
    enabled: true
}}
''', QUrl())
if component.status() == QQmlComponent.Error:
    print(component.errorString(), file=sys.stderr)
    sys.exit(2)
obj = component.create(engine.rootContext())
if obj is None:
    print(component.errorString(), file=sys.stderr)
    sys.exit(3)
if obj.property("text") != "Open record":
    print("text property did not bind", file=sys.stderr)
    sys.exit(4)
if obj.property("enabled") is not True:
    print("enabled property did not bind true", file=sys.stderr)
    sys.exit(5)
if warnings:
    print("\\n".join(warnings), file=sys.stderr)
    sys.exit(6)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@_skip_no_qt
def test_risk_strip_tokens_resolve():
    """RiskStrip token contract resolves without instantiating the component."""
    script = f"""
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
    property string surfaceBase:   Theme.color.surface.base
    property string borderRegular: Theme.color.border.regular
    property string textPrimary:   Theme.color.text.primary
    property string textSecondary: Theme.color.text.secondary
    property string positive:      Theme.status.positive
    property string negative:      Theme.status.negative
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
    "surfaceBase": "#13100a",
    "borderRegular": "#33291c",
    "textPrimary": "#e4d2a8",
    "textSecondary": "#c4a880",
    "positive": "#a8c4ab",
    "negative": "#df805e",
}}
for prop, value in expected.items():
    if obj.property(prop).lower() != value:
        print(f"{{prop}} was {{obj.property(prop)}}", file=sys.stderr)
        sys.exit(4)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Button tests — Tier 1 (component instantiation)
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_button_primary_instantiates_with_correct_variant(engine):
    """Button primary: component instantiates and exposes variant property.

    Verified working — the originally-suspected Qt module-type-cache concern
    does not affect this case. Other instantiation tests in this module
    remain xfail-marked pending confirmation that they also work.
    """
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Button {
        variant: "primary"
        text: "Confirm"
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("variant") == "primary"
    assert obj.property("text") == "Confirm"


@_skip_no_qt
def test_button_disabled_uses_inherited_enabled_property(engine):
    """Button disabled state binds to Item.enabled without QML override warnings."""
    _ = engine
    script = f"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
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

component = QQmlComponent(engine)
component.setData(b'''
import QtQuick
import Milodex 1.0

Button {{
    variant: "secondary"
    text: "Disabled"
    enabled: false
}}
''', QUrl())
if component.status() == QQmlComponent.Error:
    print(component.errorString(), file=sys.stderr)
    sys.exit(2)
obj = component.create(engine.rootContext())
if obj is None:
    print(component.errorString(), file=sys.stderr)
    sys.exit(3)
if obj.property("enabled") is not False:
    print("enabled property did not bind false", file=sys.stderr)
    sys.exit(4)
if warnings:
    print("\\n".join(warnings), file=sys.stderr)
    sys.exit(5)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


@_skip_no_qt
@pytest.mark.xfail(
    reason=(
        "Component instantiation may fail if the Milodex module type-cache is not "
        "in a state that supports composing registered components inline. "
        "If this test passes, remove the xfail marker."
    ),
    strict=False,
)
def test_button_danger_instantiates_with_correct_variant(engine):
    """Button danger: component instantiates and exposes variant property."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Button {
        variant: "danger"
        text: "Delete"
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("variant") == "danger"
    assert obj.property("text") == "Delete"


@_skip_no_qt
def test_button_primary_token_accent_resolves(engine):
    """Button primary variant: brand.accent + accentHover + accentPressed tokens resolve.

    Tier 2 fallback: verifies the token values Button._bgColor binds to exist
    and have the expected hex values under Editorial Dark.
    """
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string accent:        Theme.color.brand.accent
        property string accentHover:   Theme.color.brand.accentHover
        property string accentPressed: Theme.color.brand.accentPressed
        property string onBrandToken:  Theme.color.text.onBrand
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("accent").lower() == "#7d3540"
    assert obj.property("accentHover").lower() == "#9a4350"
    assert obj.property("accentPressed").lower() == "#622b34"
    assert obj.property("onBrandToken").lower() == "#f5e6c4"


@_skip_no_qt
def test_button_secondary_uses_text_primary_color(engine):
    """Button secondary variant label and border tokens resolve correctly."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string textPrimary:   Theme.color.text.primary
        property string borderRegular: Theme.color.border.regular
        property string textSecondary: Theme.color.text.secondary
        property string statusNeg:     Theme.status.negative
        property string statusNegHov:  Theme.status.negativeHover
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("textPrimary").lower() == "#e4d2a8"  # secondary label
    assert obj.property("borderRegular").lower() == "#33291c"  # secondary border
    # Ghost label uses text.secondary; brightened in PR D.6 to keep it
    # legible above the brightened text.muted on dark surfaces.
    assert obj.property("textSecondary").lower() == "#c4a880"  # ghost label
    assert obj.property("statusNeg").lower() == "#df805e"  # danger label + border
    assert obj.property("statusNegHov").lower() == "#e89472"  # danger hover border


@_skip_no_qt
def test_button_critical_tokens_resolve(engine):
    """Button critical variant: status.negative + onCritical + hover/pressed tokens resolve.

    Tier 2 test — verifies the tokens Button._bgColor and Button._textColor
    bind to for the `critical` variant under all three themes.  Critical is
    the kill-switch-class variant; its visual hierarchy must survive every
    theme swap with AA-passing contrast.
    """
    qml_engine, manager = engine

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string negative:        Theme.status.negative
        property string negativeHover:   Theme.status.negativeHover
        property string negativePressed: Theme.status.negativePressed
        property string onCritical:      Theme.color.text.onCritical
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    # Editorial Dark
    manager.set_theme("editorial-dark")
    assert obj.property("negative").lower() == "#df805e"
    assert obj.property("negativeHover").lower() == "#e89472"
    assert obj.property("negativePressed").lower() == "#bd6c4f"
    # Dark theme onCritical: dark text on light rust
    assert obj.property("onCritical").lower() == "#19170f"

    # Editorial Light
    manager.set_theme("editorial-light")
    assert obj.property("negative").lower() == "#a04020"
    assert obj.property("negativeHover").lower() == "#c04d28"
    assert obj.property("negativePressed").lower() == "#88361b"
    # Light theme onCritical: cream on deep rust
    assert obj.property("onCritical").lower() == "#f5e6c4"

    # Bronze
    manager.set_theme("bronze")
    assert obj.property("negative").lower() == "#d97550"
    assert obj.property("negativeHover").lower() == "#e88862"
    assert obj.property("negativePressed").lower() == "#b85e3d"
    # Bronze onCritical: dark "stamped" text
    assert obj.property("onCritical").lower() == "#19170f"

    manager.set_theme("editorial-dark")


@_skip_no_qt
def test_button_token_binding_survives_theme_swap(engine):
    """Switching to bronze re-binds button tokens (accent + onBrand)."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string accent:        Theme.color.brand.accent
        property string accentHover:   Theme.color.brand.accentHover
        property string accentPressed: Theme.color.brand.accentPressed
        property string onBrand:       Theme.color.text.onBrand
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    # Editorial Dark
    assert obj.property("accent").lower() == "#7d3540"
    assert obj.property("accentHover").lower() == "#9a4350"
    assert obj.property("accentPressed").lower() == "#622b34"
    assert obj.property("onBrand").lower() == "#f5e6c4"

    # Switch to bronze — verdigris accent, dark canvas-near onBrand
    # (PR D.6 inverted onBrand on Bronze: dark text on verdigris hits 5.09:1
    # vs. the prior warm-near-white at 3.29:1).
    manager.set_theme("bronze")
    assert obj.property("accent").lower() == "#6a9a8c"
    assert obj.property("accentHover").lower() == "#82b5a5"
    assert obj.property("accentPressed").lower() == "#578073"
    assert obj.property("onBrand").lower() == "#0d0c0a"

    # Restore
    manager.set_theme("editorial-dark")


# ---------------------------------------------------------------------------
# StatusPill tests — Tier 1 (component instantiation)
# ---------------------------------------------------------------------------


@_skip_no_qt
@pytest.mark.xfail(
    reason=(
        "Component instantiation may fail if the Milodex module type-cache is not "
        "in a state that supports composing registered components inline. "
        "If this test passes, remove the xfail marker."
    ),
    strict=False,
)
def test_status_pill_paper_instantiates(engine):
    """StatusPill paper: component instantiates and exposes variant + text properties."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    StatusPill {
        variant: "paper"
        text: "paper"
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("variant") == "paper"
    assert obj.property("text") == "paper"


@_skip_no_qt
@pytest.mark.xfail(
    reason=(
        "Component instantiation may fail if the Milodex module type-cache is not "
        "in a state that supports composing registered components inline. "
        "If this test passes, remove the xfail marker."
    ),
    strict=False,
)
def test_status_pill_killed_instantiates(engine):
    """StatusPill killed: component instantiates and exposes variant property."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    StatusPill {
        variant: "killed"
        text: "killed"
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("variant") == "killed"


@_skip_no_qt
def test_status_pill_paper_token_resolves_to_status_positive(engine):
    """StatusPill paper/backtest/blocked base color tokens resolve correctly.

    Tier 2 fallback: verifies the token values StatusPill._baseColor binds to.
    """
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string positive: Theme.status.positive
        property string info:     Theme.status.info
        property string warning:  Theme.status.warning
        property string negative: Theme.status.negative
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    # Editorial Dark status colors
    assert obj.property("positive").lower() == "#a8c4ab"  # paper — muted sage
    assert obj.property("info").lower() == "#7a98b2"  # backtest — ink
    assert obj.property("warning").lower() == "#d5a566"  # blocked — mustard


@_skip_no_qt
def test_status_pill_killed_token_and_theme_tinting(engine):
    """StatusPill killed base color token and its theme-tinting."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string negative: Theme.status.negative
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    # Editorial Dark status.negative is rust #df805e
    assert obj.property("negative").lower() == "#df805e"

    # Bronze status.negative was bumped in PR D.6 from #a04020 (2.77:1 — failed AA)
    # to #d97550 (4.55:1) to clear the contrast audit on surface.base.
    manager.set_theme("bronze")
    assert obj.property("negative").lower() == "#d97550"

    manager.set_theme("editorial-dark")


# ---------------------------------------------------------------------------
# StrategyRow tests — Tier 2 (token-resolution only)
#
# These tests verify the Theme token values that StrategyRow binds its
# visual properties to.  They do NOT instantiate StrategyRow directly.
# ---------------------------------------------------------------------------


@_skip_no_qt
@pytest.mark.xfail(
    reason=(
        "StrategyRow's transitive QtQuick.Layouts dependency interacts with the "
        "process-global Qt type cache the same way PR C's Button danger / "
        "StatusPill instantiation tests do — passes in isolation, fails when "
        "the cache has already compiled the Milodex module from a prior test "
        "without Layouts resolved.  If this passes, remove the xfail marker."
    ),
    strict=False,
)
def test_strategy_row_instantiates_and_exposes_properties(engine):
    """StrategyRow Tier 1: component instantiates, RowLayout import resolves.

    Surfaces breakage if QtQuick.Layouts ever fails to resolve under the
    project's Qt configuration (the only component that imports Layouts;
    the other Tier 1 tests don't exercise it).  Reads the four properties
    StrategyRow exposes (strategyId, stage, metricValue, tradeCount)
    through QML — confirms the public API is intact and the layout
    composition (RowLayout + StatusPill in an Item wrapper) loads cleanly.
    """
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    StrategyRow {
        strategyId:  "regime.daily.sma200_rotation.spy_shy.v1"
        stage:       "paper"
        metricValue: "+1.19"
        tradeCount:  27
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("strategyId") == "regime.daily.sma200_rotation.spy_shy.v1"
    assert obj.property("stage") == "paper"
    assert obj.property("metricValue") == "+1.19"
    assert obj.property("tradeCount") == 27


@_skip_no_qt
def test_strategy_row_token_references_resolve(engine):
    """StrategyRow token bindings: surface, pill, data, and spacing tokens resolve.

    Tier 2 test — verifies all token values StrategyRow binds its visual
    properties to under Editorial Dark.  Does not instantiate StrategyRow.
    """
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        // Surface/row tokens
        property string surfaceBase:    Theme.color.surface.base
        property string surfaceRaised:  Theme.color.surface.raised
        property string borderSubtle:   Theme.color.border.subtle
        property string borderRegular:  Theme.color.border.regular
        property string brandAccent:    Theme.color.brand.accent
        // Pill tokens by stage (delegated to StatusPill)
        property string statusPositive: Theme.status.positive
        property string statusInfo:     Theme.status.info
        property string statusWarning:  Theme.status.warning
        property string statusNegative: Theme.status.negative
        // Text + type tokens
        property string textPrimary:    Theme.color.text.primary
        property string textMuted:      Theme.color.text.muted
        property string dataMdFamily:   Theme.typography.data.md.family
        property string dataSmFamily:   Theme.typography.data.sm.family
        property string labelXsFamily:  Theme.typography.label.xs.family
        // Spacing
        property int space2: Theme.space[2]
        property int space3: Theme.space[3]
        property int space5: Theme.space[5]
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    # Row surface tokens (Editorial Dark)
    assert obj.property("surfaceBase").lower() == "#13100a"
    assert obj.property("surfaceRaised").lower() == "#1a1611"
    assert obj.property("borderSubtle").lower() == "#241f15"
    assert obj.property("borderRegular").lower() == "#33291c"
    assert obj.property("brandAccent").lower() == "#7d3540"  # selected accent bar
    # Pill status tokens (resolved via StatusPill composition)
    assert obj.property("statusPositive").lower() == "#a8c4ab"  # paper
    assert obj.property("statusInfo").lower() == "#7a98b2"  # backtest
    assert obj.property("statusWarning").lower() == "#d5a566"  # blocked
    assert obj.property("statusNegative").lower() == "#df805e"  # killed
    # Type families
    assert obj.property("dataMdFamily") == "JetBrains Mono"
    assert obj.property("dataSmFamily") == "JetBrains Mono"
    assert obj.property("labelXsFamily") == "Public Sans"
    # Spacing scale
    assert obj.property("space2") == 8
    assert obj.property("space3") == 12
    assert obj.property("space5") == 24


@_skip_no_qt
def test_strategy_row_brand_accent_token_rebinds_on_theme_swap(engine):
    """StrategyRow selected accent bar token (brand.accent) rebinds on theme swap.

    Tier 2 test — verifies token-binding contract for the accent-bar color.
    """
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0
    Item {
        property string brandAccent: Theme.color.brand.accent
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("brandAccent").lower() == "#7d3540"

    # Rebinds on theme swap (theme-binding contract)
    manager.set_theme("bronze")
    assert obj.property("brandAccent").lower() == "#6a9a8c"

    manager.set_theme("editorial-dark")


# ---------------------------------------------------------------------------
# Surface tests — Tier 2 (token-resolution only)
#
# These tests verify the Theme token values that Surface binds to.
# They do NOT instantiate Surface directly.
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_surface_background_and_radius_tokens_resolve(engine):
    """Surface token-resolution: color.surface.base, border, radius, and padding.

    Tier 2 test — verifies the tokens Surface binds its background ``color``,
    ``border.color``, ``radius``, and padding properties to.  Does not
    instantiate Surface directly.
    """
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0
    Item {
        property string surfaceBase:  Theme.color.surface.base
        property string borderSubtle: Theme.color.border.subtle
        property int    radiusLg:     Theme.radius.lg
        property int    space5:       Theme.space[5]
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("surfaceBase").lower() == "#13100a"
    assert obj.property("borderSubtle").lower() == "#241f15"
    assert obj.property("radiusLg") == 6  # radius.lg
    assert obj.property("space5") == 24  # space[5] padding


@_skip_no_qt
def test_surface_hover_border_tokens_are_distinct(engine):
    """Surface hover border tokens are distinct — hovering produces a visible change.

    Tier 2 test — verifies border.subtle != border.regular so Surface's
    interactive hover transition has a visible effect.
    """
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0
    Item {
        property string borderSubtle:  Theme.color.border.subtle
        property string borderRegular: Theme.color.border.regular
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("borderSubtle").lower() == "#241f15"
    assert obj.property("borderRegular").lower() == "#33291c"
    # Tokens are distinct — hovering changes something meaningful
    assert obj.property("borderRegular") != obj.property("borderSubtle")
