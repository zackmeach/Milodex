"""Behavioral pilot for source-pinned QML smoke assertions (burn backlog C1).

Background
----------
``test_qml_load_smoke.py`` proves QML compiles cleanly and that ~28 literal
tokens are PRESENT IN SOURCE (``read_text`` + substring greps). A source pin
proves a string exists; it does not prove the behavior works. A refactor that
renames a token breaks the pin without a real regression, and a clamp that is
rewritten to a no-op still passes as long as the magic literal survives.

This module is the PILOT for the trigger-and-observe replacement pattern, to
be copied across the remaining pins in a follow-up batch. It does NOT delete
the source pins it shadows -- those stay until this pattern is reviewed.

The pattern (mirrors test_bench_confirmation_modal_behavior.py)
--------------------------------------------------------------
1. INSTANTIATE the real QML component in a ``QQuickView`` (offscreen Qt), so
   Layouts resolve and the scene-graph polish/render pass actually runs and
   produces real geometry -- not just a compiled-but-unrendered tree.
2. TRIGGER / set the state that drives the affordance under test (here: give
   the modal an oversized parent and deliberately overflowing body content).
3. OBSERVE the rendered RESULT via live ``QQuickItem`` property reads (here:
   the rendered modal-box height is actually clamped to the viewport, not the
   intrinsic content height).

This is strictly stronger than the source pin: a rename of ``_modalMaxHeight``
that PRESERVES the clamp still passes (no false break), while a refactor that
breaks the clamp (box taller than the viewport) FAILS -- which is the real
1440p-clipping regression the pin was meant to guard.

The pilot converts the behavior guarded by
``test_qml_load_smoke.py::test_bench_pr_o_modal_is_viewport_bounded``. That
source pin is intentionally LEFT IN PLACE for the review; this is the
behavioral proof it can eventually be replaced by.

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
    reason="PySide6 not installed - skipping QML smoke behavior tests",
)

_GUI_SRC = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui"
_QML_IMPORT_ROOT = _GUI_SRC / "qml"


def _run(script: str, label: str) -> str:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"{label} FAILED\n"
        f"returncode: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return result.stdout


# ---------------------------------------------------------------------------
# Subprocess harness
#
# Loads a probe Item hosting one real BenchModal (Milodex 1.0 type) inside an
# intentionally OVERSIZED parent, with a body slot that is deliberately taller
# than the viewport. The QQuickView renders it (so box.height resolves from the
# Math.min clamp), then {assertions} reads the live tree and exits non-zero on
# failure.
# ---------------------------------------------------------------------------

_HARNESS = r'''
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl, QTimer
from PySide6.QtCore import QObject as _QObjectBase
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from milodex.gui.fonts import load_fonts
from milodex.gui.theme_manager import ThemeManager
from milodex.gui import qml_setup

app = QGuiApplication.instance() or QGuiApplication(sys.argv)
load_fonts()

tm = ThemeManager()
qml_setup.register_qml_types(theme_manager=tm)

# Probe: an oversized host (4000px tall) with a BenchModal whose body content
# is far taller than any realistic viewport. If the height clamp regresses,
# the box grows to the intrinsic ~6200px and overflows the viewport.
probe = b"""
import QtQuick
import Milodex 1.0

Item {{
    id: probeRoot
    width: 1000
    height: 4000

    BenchModal {{
        id: benchModal
        objectName: "benchModalProbe"
        anchors.fill: parent
        titleText: "Overflowing modal"
        proseText: "Probe content that intentionally overflows the viewport."

        // Body slot far taller than the 4000px host viewport.
        Rectangle {{
            objectName: "tallBody"
            width: 400
            height: 6000
            color: "red"
        }}

        Item {{
            objectName: "actionSlotProbe"
            width: 120
            height: 32
        }}
    }}
}}
"""

import tempfile, pathlib
_qml_file = pathlib.Path(tempfile.mktemp(suffix=".qml"))
_qml_file.write_bytes(probe)

view = QQuickView()
view.engine().addImportPath({import_root})
view.setResizeMode(QQuickView.SizeRootObjectToView)
view.resize(1000, 4000)
view.setSource(QUrl.fromLocalFile(str(_qml_file)))

if view.status() == QQuickView.Error:
    for e in view.errors():
        print(str(e.toString()), file=sys.stderr)
    sys.exit(2)

root = view.rootObject()
if root is None:
    print("rootObject() is None", file=sys.stderr)
    sys.exit(3)

view.show()
QTimer.singleShot(700, app.quit)
app.exec()

modal = root.findChild(_QObjectBase, "benchModalProbe")
if modal is None:
    print("modal not found by objectName", file=sys.stderr)
    sys.exit(4)

def _walk(item):
    yield item
    for c in item.childItems():
        yield from _walk(c)

{assertions}
'''


def _build(assertions: str) -> str:
    return _HARNESS.format(
        import_root=repr(str(_QML_IMPORT_ROOT)),
        assertions=assertions,
    )


@_skip_no_qt
def test_bench_modal_box_height_is_clamped_to_viewport() -> None:
    """BenchModal renders its box clamped to the viewport, not the (huge)
    intrinsic content height.

    Trigger: a 4000px-tall host with a 6000px body slot -- the intrinsic
    height far exceeds the viewport.
    Observe: the live modal root reports ``_modalIntrinsicHeight`` >
    ``_modalMaxHeight`` (the clamp is actually engaged), and the RENDERED box
    Rectangle's height equals ``_modalMaxHeight`` and is <= the parent height.

    This is the behavioral home of
    test_qml_load_smoke.py::test_bench_pr_o_modal_is_viewport_bounded (the
    1440p off-screen-clipping guard). NON-VACUOUS: if the box height binding
    drops the ``Math.min(..., _modalMaxHeight)`` clamp and hugs content, the
    rendered box height jumps to ~6200px > 4000px viewport and this fails;
    verified by editing box.height to ``root._modalIntrinsicHeight`` in
    BenchModal.qml -> test failed ("BOX OVERFLOWS VIEWPORT box_h=6198.0
    parent_h=4000.0").
    """
    assertions = (
        "intrinsic = float(modal.property('_modalIntrinsicHeight'))\n"
        "maxh = float(modal.property('_modalMaxHeight'))\n"
        "parent_h = float(modal.property('height'))\n"
        "# The probe is constructed so the clamp is genuinely engaged.\n"
        "if not (intrinsic > maxh):\n"
        "    print('CLAMP NOT ENGAGED intrinsic=' + str(intrinsic) "
        "+ ' max=' + str(maxh), file=sys.stderr); sys.exit(5)\n"
        "# Find the rendered modal box: the centered card Rectangle. It is the\n"
        "# rounded card (radius > 0) with width capped at 620; the 2px accent\n"
        "# border (radius 0) and the injected probe body ('tallBody', not\n"
        "# rounded) are excluded. Pick the TALLEST such candidate so the box is\n"
        "# located even when a broken clamp lets it overflow -- that keeps the\n"
        "# overflow failure message accurate.\n"
        "_probe_names = {'tallBody', 'actionSlotProbe', 'benchModalProbe'}\n"
        "box = None\n"
        "box_h = -1.0\n"
        "for it in _walk(modal):\n"
        "    if it is modal or it.objectName() in _probe_names:\n"
        "        continue\n"
        "    h = float(it.property('height') or 0)\n"
        "    w = float(it.property('width') or 0)\n"
        "    radius = it.property('radius')\n"
        "    if radius is None:\n"
        "        continue\n"
        "    if float(radius) > 0 and h > 0 and 100 < w <= 620 and h > box_h:\n"
        "        box = it\n"
        "        box_h = h\n"
        "if box is None:\n"
        "    print('could not locate rendered modal box', file=sys.stderr); sys.exit(6)\n"
        "if box_h > parent_h:\n"
        "    print('BOX OVERFLOWS VIEWPORT box_h=' + str(box_h) "
        "+ ' parent_h=' + str(parent_h), file=sys.stderr); sys.exit(7)\n"
        "if abs(box_h - maxh) > 1.0:\n"
        "    print('BOX HEIGHT NOT CLAMPED TO MAX box_h=' + str(box_h) "
        "+ ' max=' + str(maxh), file=sys.stderr); sys.exit(8)\n"
        "print('VIEWPORT_CLAMP_OK')\n"
        "sys.exit(0)\n"
    )
    out = _run(_build(assertions), "bench modal viewport clamp")
    assert "VIEWPORT_CLAMP_OK" in out
