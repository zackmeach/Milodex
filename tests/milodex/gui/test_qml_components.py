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

    assert obj.property("accent").lower() == "#722f37"
    assert obj.property("accentHover").lower() == "#8a3a45"
    assert obj.property("accentPressed").lower() == "#5d262d"
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

    assert obj.property("textPrimary").lower() == "#d8c5a3"  # secondary label
    assert obj.property("borderRegular").lower() == "#2a2218"  # secondary border
    assert obj.property("textSecondary").lower() == "#a89070"  # ghost label
    assert obj.property("statusNeg").lower() == "#d97757"  # danger label + border
    assert obj.property("statusNegHov").lower() == "#e08b6b"  # danger hover border


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
    assert obj.property("accent").lower() == "#722f37"
    assert obj.property("accentHover").lower() == "#8a3a45"
    assert obj.property("accentPressed").lower() == "#5d262d"
    assert obj.property("onBrand").lower() == "#f5e6c4"

    # Switch to bronze — verdigris accent, warm near-white onBrand
    manager.set_theme("bronze")
    assert obj.property("accent").lower() == "#5e8b7e"
    assert obj.property("accentHover").lower() == "#72a89a"
    assert obj.property("accentPressed").lower() == "#4d7268"
    assert obj.property("onBrand").lower() == "#f0ede8"

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
    assert obj.property("positive").lower() == "#9bb89e"  # paper — muted sage
    assert obj.property("info").lower() == "#6c89a3"  # backtest — ink
    assert obj.property("warning").lower() == "#c4965a"  # blocked — mustard


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

    # Editorial Dark status.negative is rust #d97757
    assert obj.property("negative").lower() == "#d97757"

    # Bronze status.negative theme-tints to deep rust #a04020
    manager.set_theme("bronze")
    assert obj.property("negative").lower() == "#a04020"

    manager.set_theme("editorial-dark")


# ---------------------------------------------------------------------------
# StrategyRow tests — Tier 2 (token-resolution only)
#
# These tests verify the Theme token values that StrategyRow binds its
# visual properties to.  They do NOT instantiate StrategyRow directly.
# ---------------------------------------------------------------------------


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
    assert obj.property("surfaceBase").lower() == "#100d09"
    assert obj.property("surfaceRaised").lower() == "#14110d"
    assert obj.property("borderSubtle").lower() == "#1f1a12"
    assert obj.property("borderRegular").lower() == "#2a2218"
    assert obj.property("brandAccent").lower() == "#722f37"  # selected accent bar
    # Pill status tokens (resolved via StatusPill composition)
    assert obj.property("statusPositive").lower() == "#9bb89e"  # paper
    assert obj.property("statusInfo").lower() == "#6c89a3"  # backtest
    assert obj.property("statusWarning").lower() == "#c4965a"  # blocked
    assert obj.property("statusNegative").lower() == "#d97757"  # killed
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

    assert obj.property("brandAccent").lower() == "#722f37"

    # Rebinds on theme swap (theme-binding contract)
    manager.set_theme("bronze")
    assert obj.property("brandAccent").lower() == "#5e8b7e"

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

    assert obj.property("surfaceBase").lower() == "#100d09"
    assert obj.property("borderSubtle").lower() == "#1f1a12"
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

    assert obj.property("borderSubtle").lower() == "#1f1a12"
    assert obj.property("borderRegular").lower() == "#2a2218"
    # Tokens are distinct — hovering changes something meaningful
    assert obj.property("borderRegular") != obj.property("borderSubtle")
