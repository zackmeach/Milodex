"""Import-smoke coverage for the two GUI screenshot-capture scripts.

``scripts/`` is not linted or otherwise exercised by the test suite (SWP-
20260708-08), so a signature break in an imported symbol — e.g.
``register_qml_types`` — can silently recur without any test failing until
someone runs the script by hand. This happened once already (repaired in
1f62183).

Both scripts are safe to import: module-level code only sets
``QT_QPA_PLATFORM=offscreen`` (a no-op env default) and performs imports; all
Qt object construction, engine loading, and file I/O happens inside
``capture()``/``main()``, which this test never calls. Loading each script as
a module via ``importlib`` therefore exercises every top-level import
(including ``from milodex.gui.qml_setup import register_qml_types``) without
spawning a QApplication or touching the filesystem.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"

SCRIPT_NAMES = [
    "capture_gui_screenshots.py",
    "capture_bench_interactive.py",
]


def _import_script(name: str):
    path = SCRIPTS_DIR / name
    module_name = f"_capture_script_smoke_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


@pytest.mark.parametrize("script_name", SCRIPT_NAMES)
def test_capture_script_imports_cleanly(script_name: str) -> None:
    """The script module (and every symbol it imports) still resolves.

    A rename or signature drift in an imported symbol — most notably
    ``register_qml_types`` — raises ImportError/AttributeError here, well
    before someone discovers it by running the script manually.
    """
    module = _import_script(script_name)
    assert callable(module.capture)
    assert callable(module.main)
