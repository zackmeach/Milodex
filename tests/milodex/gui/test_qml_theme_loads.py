"""QML smoke tests for the Theme singleton.

These tests instantiate a real :class:`QQmlApplicationEngine`, register
the Milodex import URI, and load a tiny QML document that exercises
the ``Theme`` singleton.  They verify the architectural correctness
of the singleton's structure:

- the singleton loads without QML errors / warnings
- color tokens resolve to the active theme's hex values
- switching themes via :meth:`ThemeManager.set_theme` re-binds tokens
- status colors theme-tint correctly
- theme-invariant tokens (space, motion, radius) are stable across
  themes

All tests skip when PySide6 is not importable.

.. warning:: PROCESS-GLOBAL SINGLETON CONSTRAINT — READ BEFORE ADDING TESTS
   =========================================================================

   ``qmlRegisterSingletonInstance`` registers a C++ object into Qt's
   **process-global** type registry, not into a per-engine registry.  Once
   the first test in this module calls ``register_qml_types(manager)``, that
   *specific* ``ThemeManager`` instance is bound as ``Milodex.ThemeManager``
   for the **entire process lifetime** — re-registering (e.g. in a second
   fixture call) raises an exception, which is why the ``engine`` fixture
   uses a ``try/except`` to detect the already-registered case.

   Consequences for test isolation:

   1. **All tests in this module share the same ``ThemeManager`` instance.**
      The fixture re-targets ``manager._settings_path`` to each test's
      ``tmp_path``, but only on the Python side.  Earlier tests' ``tmp_path``
      directories are torn down by pytest after each test; writes that happen
      after the first test go to whichever ``_settings_path`` is current, not
      necessarily the one that existed when the ``engine`` fixture set up.

   2. **This is a smoke test of QML binding propagation, not a full isolation
      suite.**  Tests must not assert on per-test isolated persistence
      behavior (e.g. "disk was written to *this* tmp_path").  For isolated
      persistence semantics, use the Python-side tests in
      ``test_theme_manager.py``.

   3. **Ordering matters.**  The process-global singleton means test ordering
      within this module is load-bearing.  Do not add tests that assume a
      freshly-registered singleton; assume the singleton was registered by
      the first test that ran.

   4. **Future multi-window work.**  If per-engine theme isolation becomes
      necessary (e.g. multi-window tests, each with its own theme), this
      fixture pattern must be replaced with ``qmlRegisterSingletonType``
      using a factory callback so each engine gets its own ``ThemeManager``
      instance.  That is a non-trivial refactor — scope it as a separate PR.
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
    reason="PySide6 not installed — skipping QML theme tests",
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Path to the QML import root — the directory containing the `Milodex`
# folder (which holds qmldir + Theme.qml + themes/).  Used as an extra
# import path on the engine so `import Milodex 1.0` resolves.
_QML_IMPORT_ROOT: Path = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui" / "qml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication (Qt Quick needs a GUI app)."""
    if not _PYSIDE6_AVAILABLE:
        return None

    import os
    import sys

    from PySide6.QtGui import QGuiApplication

    # Use offscreen platform so tests run headless.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


@pytest.fixture
def engine(qapp, tmp_path):
    """Fresh QQmlApplicationEngine with the Milodex import path registered.

    A new ThemeManager is registered as the ``Milodex.ThemeManager``
    singleton, pointed at a tmp-path settings file so tests do not
    pollute the real ``data/`` directory.

    .. warning:: SINGLETON CONSTRAINT — see module docstring for full details.

       ``qmlRegisterSingletonInstance`` is **process-global**.  The first call
       to ``register_qml_types`` binds *one* ``ThemeManager`` instance for the
       lifetime of the process.  All subsequent fixture invocations catch the
       re-registration exception, fetch the already-bound instance, and reset
       its theme to ``"editorial-dark"`` so tests start from a known state.

       ``manager._settings_path`` is re-pointed to ``tmp_path`` each call, but
       this is purely advisory — do NOT write persistence-isolation assertions
       here.  Use ``test_theme_manager.py`` for that.
    """
    from PySide6.QtQml import QQmlApplicationEngine

    from milodex.gui.qml_setup import register_qml_types
    from milodex.gui.theme_manager import ThemeManager

    # Register once per process; subsequent tests reuse the same
    # singleton instance and just retarget its settings file.
    manager = ThemeManager(settings_path=tmp_path / "gui_settings.json")
    try:
        register_qml_types(manager)
    except Exception:
        # Already registered — fetch the existing instance.
        from milodex.gui import qml_setup as _qml_setup

        manager = _qml_setup._singleton_instance
        # Reset to a known-good default so tests start from
        # editorial-dark.
        if manager is not None:
            manager.set_theme("editorial-dark")

    qml_engine = QQmlApplicationEngine()
    qml_engine.addImportPath(str(_QML_IMPORT_ROOT))
    return qml_engine, manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_qml(engine, qml_source: str):
    """Load *qml_source* into *engine*; return ``(component, root_object)``.

    The component must remain alive for the root object to remain valid;
    callers should keep both references in scope.  Captures QML warnings
    via ``warnings.connect`` and asserts none were emitted.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent

    warnings: list[str] = []
    engine.warnings.connect(lambda ws: warnings.extend(str(w.toString()) for w in ws))

    component = QQmlComponent(engine)
    component.setData(qml_source.encode("utf-8"), QUrl())

    assert component.status() != QQmlComponent.Error, (
        f"QML compile error: {component.errorString()}"
    )

    # Parent the created object to the engine so its C++ side outlives
    # the local Python wrapper (otherwise libshiboken deletes it the
    # moment the wrapper goes out of scope).
    obj = component.create(engine.rootContext())
    assert obj is not None, f"QML create() returned None; errors: {component.errorString()}"
    obj.setParent(engine)
    assert warnings == [], f"QML emitted warnings: {warnings}"
    return component, obj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_theme_singleton_loads_without_errors(engine):
    """A QML doc importing Theme compiles and instantiates cleanly."""
    qml_engine, _manager = engine

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string activeName: Theme.themeName
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component  # keep alive for the test's lifetime
    # The default-or-restored theme name must be a known string.
    assert obj.property("activeName") in ("editorial-dark", "editorial-light", "bronze")


@_skip_no_qt
def test_theme_color_resolves_to_editorial_dark_default(engine):
    """Theme.color.brand.primary equals Editorial Dark's parchment value."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string brandPrimary: Theme.color.brand.primary
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component  # keep alive for the test's lifetime
    # Qt normalizes hex colors to #aarrggbb / #rrggbb form.  Compare
    # case-insensitively against the spec value.
    assert obj.property("brandPrimary").lower() == "#ecd6a5"


@_skip_no_qt
def test_theme_switches_when_thememanager_changes(engine):
    """Switching to bronze re-binds Theme.color.brand.primary."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string brandPrimary: Theme.color.brand.primary
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component  # keep alive for the test's lifetime
    assert obj.property("brandPrimary").lower() == "#ecd6a5"

    manager.set_theme("bronze")
    assert obj.property("brandPrimary").lower() == "#b58c6e"


@_skip_no_qt
def test_status_colors_theme_tint_correctly(engine):
    """status.positive shifts from sage (dark) to verdigris (bronze)."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string positive: Theme.status.positive
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component  # keep alive for the test's lifetime
    assert obj.property("positive").lower() == "#a8c4ab"

    # Bronze status.positive changed in PR D.6 from verdigris #6a9a8c (which
    # collided with brand.accent — same hue on selection bar AND status pill)
    # to sage #a8c4ab (Editorial Dark's positive value, distinct from
    # brand.accent and still inside the Bronze palette story).
    manager.set_theme("bronze")
    assert obj.property("positive").lower() == "#a8c4ab"


@_skip_no_qt
def test_invariant_tokens_stable_across_themes(engine):
    """space, motion, and radius do not vary across themes."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property int spaceFour: Theme.space[4]
        property int motionStandard: Theme.motion.standard
        property int radiusMd: Theme.radius.md
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component  # keep alive for the test's lifetime
    space_dark = obj.property("spaceFour")
    motion_dark = obj.property("motionStandard")
    radius_dark = obj.property("radiusMd")

    assert space_dark == 16
    assert motion_dark == 220
    assert radius_dark == 4

    manager.set_theme("editorial-light")
    assert obj.property("spaceFour") == space_dark
    assert obj.property("motionStandard") == motion_dark
    assert obj.property("radiusMd") == radius_dark

    manager.set_theme("bronze")
    assert obj.property("spaceFour") == space_dark
    assert obj.property("motionStandard") == motion_dark
    assert obj.property("radiusMd") == radius_dark


@_skip_no_qt
def test_brand_primary_is_distinct_from_text_primary(engine):
    """``Theme.color.brand.primary`` must differ from ``Theme.color.text.primary``
    in every theme.

    Catches the Editorial Light collision (brand.primary == text.primary
    == ``#33291c``) caught in PR D.6 review.  When both tokens collapse
    to the same value, display-rank surface titles render in the same
    colour as body text — the brand identity disappears and there is no
    visual difference between a section heading and its caption.
    """
    qml_engine, manager = engine

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string brandPrimary: Theme.color.brand.primary
        property string textPrimary:  Theme.color.text.primary
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    failures: list[str] = []
    for name in ("editorial-dark", "editorial-light", "bronze"):
        manager.set_theme(name)
        brand = obj.property("brandPrimary").lower()
        text = obj.property("textPrimary").lower()
        if brand == text:
            failures.append(f"  {name}: brand.primary == text.primary == {brand}")
    manager.set_theme("editorial-dark")
    assert not failures, "brand/text token collisions:\n" + "\n".join(failures)


@_skip_no_qt
def test_theme_column_widths_exist_and_are_invariant(engine):
    """Theme.column.* tokens exist, have the correct values, and are theme-invariant."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property int pill:       Theme.column.pill
        property int metric:     Theme.column.metric
        property int tradeCount: Theme.column.tradeCount
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component  # keep alive for the test's lifetime

    assert obj.property("pill") == 96
    assert obj.property("metric") == 64
    assert obj.property("tradeCount") == 88

    # Invariant — values must not change across theme switches
    manager.set_theme("editorial-light")
    assert obj.property("pill") == 96
    assert obj.property("metric") == 64
    assert obj.property("tradeCount") == 88

    manager.set_theme("bronze")
    assert obj.property("pill") == 96
    assert obj.property("metric") == 64
    assert obj.property("tradeCount") == 88


@_skip_no_qt
def test_theme_stage_tokens_exist_and_are_invariant(engine):
    """Theme.stage.* tokens expose the five promotion-ladder hues across themes."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property string idle:      Theme.stage.idle
        property string backtest:  Theme.stage.backtest
        property string paper:     Theme.stage.paper
        property string microLive: Theme.stage.microLive
        property string live:      Theme.stage.live
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    expected = {
        "idle": "#6f6a5c",
        "backtest": "#7a98b2",
        "paper": "#a8c4ab",
        "microLive": "#d5a566",
        "live": "#7d3540",
    }
    for prop, value in expected.items():
        assert obj.property(prop).lower() == value

    manager.set_theme("editorial-light")
    for prop, value in expected.items():
        assert obj.property(prop).lower() == value

    manager.set_theme("bronze")
    for prop, value in expected.items():
        assert obj.property(prop).lower() == value


@_skip_no_qt
def test_theme_kanban_column_dimensions_exist_and_are_invariant(engine):
    """Kanban column dimensions are stable tokens, not content-driven widths."""
    qml_engine, manager = engine
    manager.set_theme("editorial-dark")

    qml = """
    import QtQuick
    import Milodex 1.0

    Item {
        property int laneWidth:  Theme.column.kanbanLane
        property int cardWidth:  Theme.column.kanbanCard
        property int cardMinH:   Theme.column.kanbanCardMinHeight
        property int metricSlot: Theme.column.kanbanMetric
    }
    """
    component, obj = _load_qml(qml_engine, qml)
    _ = component

    assert obj.property("laneWidth") == 320
    assert obj.property("cardWidth") == 288
    assert obj.property("cardMinH") == 132
    assert obj.property("metricSlot") == 68

    manager.set_theme("editorial-light")
    assert obj.property("laneWidth") == 320
    assert obj.property("cardWidth") == 288
    assert obj.property("cardMinH") == 132
    assert obj.property("metricSlot") == 68

    manager.set_theme("bronze")
    assert obj.property("laneWidth") == 320
    assert obj.property("cardWidth") == 288
    assert obj.property("cardMinH") == 132
    assert obj.property("metricSlot") == 68
