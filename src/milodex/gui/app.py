"""Application shell for the Milodex GUI.

Bootstrap the Qt Quick application, load bundled fonts, register QML types,
and hand control to the Qt event loop.  Import-time side effects are minimal:
the PySide6 import happens inside :func:`run_app` so that CLI paths that do
not invoke the GUI do not pay the import cost.

Usage::

    from milodex.gui.app import run_app
    raise SystemExit(run_app())
"""

from __future__ import annotations

import importlib.resources
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constant
# ---------------------------------------------------------------------------

#: Absolute path to the QML import root — the directory that contains the
#: ``Milodex/`` folder (which holds ``qmldir``, ``Theme.qml``, etc.).
#: Resolved via ``importlib.resources`` for correctness across editable
#: installs, unpacked wheels, and PyInstaller bundles.
QML_IMPORT_PATH: Path = Path(str(importlib.resources.files("milodex.gui").joinpath("qml")))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_app() -> int:
    """Bootstrap and run the Milodex Qt Quick application.

    Steps:

    1. Construct :class:`QGuiApplication` (not ``QApplication`` -- the UI is
       Qt Quick only; no Widgets are used per ADR 0033).
    2. Call :func:`~milodex.gui.fonts.load_fonts` to register bundled font
       families (Newsreader, Public Sans, JetBrains Mono) with Qt.
    3. Construct :class:`~milodex.gui.theme_manager.ThemeManager` and call
       :func:`~milodex.gui.qml_setup.register_qml_types` to bind it as the
       ``Milodex.ThemeManager`` QML singleton.
    4. Construct :class:`QQmlApplicationEngine`.
    5. Add :data:`QML_IMPORT_PATH` as a QML import search path so
       ``import Milodex 1.0`` resolves.
    6. Load ``Main.qml`` (the top-level ApplicationWindow).
    7. If no root objects were created (load failure), log an error and
       return exit code 1.
    8. Wire the QML ``quit`` signal to ``app.quit``.
    9. Return ``app.exec()`` (the Qt event-loop exit code).

    Returns
    -------
    int
        Process exit code: 0 for clean exit, non-zero for error.
    """
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
    except ImportError:
        logger.error(
            "run_app: PySide6 is not installed -- cannot start the GUI. "
            "Install it with: pip install PySide6"
        )
        return 1

    from milodex.gui.fonts import load_fonts
    from milodex.gui.qml_setup import register_qml_types
    from milodex.gui.theme_manager import ThemeManager

    # --- 1. QGuiApplication ---------------------------------------------------
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv)

    logger.info("run_app: Milodex GUI starting")

    # --- 2. Fonts -------------------------------------------------------------
    loaded_count, failed = load_fonts()
    if failed:
        logger.warning(
            "run_app: %d font file(s) failed to load -- display may degrade",
            len(failed),
        )

    # --- 3. ThemeManager + QML type registration ------------------------------
    theme_manager = ThemeManager()
    register_qml_types(theme_manager)
    logger.info("run_app: active theme = %r", theme_manager.theme)

    # --- 4. Engine ------------------------------------------------------------
    engine = QQmlApplicationEngine()

    # --- 5. QML import path ---------------------------------------------------
    engine.addImportPath(str(QML_IMPORT_PATH))

    # --- 6. Load Main.qml -----------------------------------------------------
    main_qml_path = QML_IMPORT_PATH / "Milodex" / "Main.qml"
    logger.info("run_app: loading %s", main_qml_path)
    engine.load(str(main_qml_path))

    # --- 7. Check for load failure --------------------------------------------
    if not engine.rootObjects():
        logger.error(
            "run_app: QQmlApplicationEngine has no root objects after load -- "
            "Main.qml failed to initialize. Check QML errors above."
        )
        return 1

    logger.info(
        "run_app: engine loaded successfully (%d root object(s))",
        len(engine.rootObjects()),
    )

    # --- 8. Wire quit signal --------------------------------------------------
    engine.quit.connect(app.quit)

    # --- 9. Event loop --------------------------------------------------------
    return app.exec()


# ---------------------------------------------------------------------------
# Module main guard (for `python -m milodex.gui.app`)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(run_app())
