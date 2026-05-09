# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec file for the Milodex desktop application.
#
# Build with:
#   pyinstaller installer/milodex.spec --clean --noconfirm
# from the repo root.  The output lands in dist/Milodex/.
#
# Design decisions recorded here match ADR 0037:
#   --onedir (not --onefile): startup performance and reduced AV false-positive
#   risk.  The Python runtime and Qt DLLs sit on disk as ordinary signed files;
#   only the thin Milodex.exe shim is "novel."
#
# Data inclusions:
#   - configs/: strategy YAML files read at runtime via get_bundled_resource_dir()
#   - QML tree: the Milodex/ QML module; must preserve the directory structure
#     so `import Milodex 1.0` resolves against QML_IMPORT_PATH.
#   - assets/fonts/: bundled TrueType families (Newsreader, Public Sans,
#     JetBrains Mono). importlib.resources resolves these when the package is
#     structured with __init__.py files AND the data is declared in package_data.
#     We include them explicitly here as a belt-and-suspenders guard so they are
#     present even if importlib.resources falls back to MEIPASS scanning.

import os
from pathlib import Path

block_cipher = None

# Resolve paths relative to the repo root (one level above installer/).
# `SPECPATH` is the directory containing this spec file — a global that
# PyInstaller injects into the spec's namespace when it evaluates the file.
# This is the documented and robust pattern; an earlier Path(__spec__.origin)
# pattern was fragile because PyInstaller's evaluation context does not set
# __spec__ consistently across versions.
_spec_dir = Path(SPECPATH)
_repo_root = _spec_dir.parent

# ---------------------------------------------------------------------------
# Data inclusions
# ---------------------------------------------------------------------------

_datas = [
    # Strategy configs and universe manifests.
    # Directory copy (not glob) — PyInstaller's glob handling in Analysis.datas
    # has been version-inconsistent; the directory form is the documented and
    # well-tested path. Destination matches `get_bundled_resource_dir() / "configs"`
    # which resolves to `<MEIPASS>/configs/` in a frozen bundle.
    (str(_repo_root / "configs"), "configs"),

    # QML module tree.  Destination MUST mirror the package layout so the
    # production resolution
    #   QML_IMPORT_PATH = importlib.resources.files("milodex.gui").joinpath("qml")
    # in src/milodex/gui/app.py resolves to <MEIPASS>/milodex/gui/qml/ at
    # runtime.  Copying the whole `qml/` directory (rather than just the
    # `Milodex/` submodule) future-proofs against a second QML module being
    # added under the same parent.
    (str(_repo_root / "src" / "milodex" / "gui" / "qml"), "milodex/gui/qml"),

    # Bundled font families.  Destination matches
    #   importlib.resources.files("milodex.gui").joinpath("assets/fonts")
    # in src/milodex/gui/fonts.py — the same package-relative pattern as QML.
    (str(_repo_root / "src" / "milodex" / "gui" / "assets" / "fonts"), "milodex/gui/assets/fonts"),
]

# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
# PyInstaller's static analysis misses Qt modules that are loaded indirectly
# (e.g. through PySide6's plugin system).  List them explicitly so the frozen
# bundle includes the required shared libraries.

_hidden_imports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
]

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

a = Analysis(
    [str(_spec_dir / "milodex_launcher.py")],
    pathex=[str(_repo_root / "src")],
    binaries=[],
    datas=_datas,
    hiddenimports=_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Development and test dependencies — not needed at runtime.
        "pytest",
        "mutmut",
        "ruff",
        # Heavy scientific stack pulled in transitively by some deps but
        # not required for the Milodex GUI.
        "IPython",
        "matplotlib",
        "scipy",
        "sklearn",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------------
# EXE (the thin launcher shim inside the dist/Milodex/ directory)
# ---------------------------------------------------------------------------

# TODO: add icon='installer/milodex.ico' once an icon is commissioned.
# The icon parameter is intentionally omitted rather than left as None to
# avoid PyInstaller warnings on builds where the .ico file is absent.

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # --onedir: binaries land in COLLECT, not the EXE
    name="Milodex",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # Windowed application; no console window on launch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------------------------------------------------------------------------
# COLLECT (assembles dist/Milodex/ — the --onedir output)
# ---------------------------------------------------------------------------

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Milodex",
)
