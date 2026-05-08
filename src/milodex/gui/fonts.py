"""Font-loading bootstrap for the Milodex GUI.

Loads all bundled TrueType font families into Qt's font database so that
QML surfaces can reference them by family name.  Call :func:`load_fonts`
once before any QML surface renders.  Subsequent calls are harmless —
Qt deduplicates font registrations internally.

Bundled families (see ``assets/fonts/``):
- Newsreader (variable, SIL OFL 1.1)
- Public Sans (variable, SIL OFL 1.1)
- JetBrains Mono (variable, SIL OFL 1.1)
"""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constant
# ---------------------------------------------------------------------------

#: Absolute path to the bundled fonts directory.  Resolved via
#: ``importlib.resources`` so it works correctly whether the package is
#: installed from a wheel, installed in editable mode, or run from source.
#:
#: Caveat: the ``Path(str(...))`` coercion assumes the package resides on a
#: real filesystem path (editable install, unpacked wheel, PyInstaller
#: bundle).  Zipimport contexts (zipapp, packed-zip wheels) would require
#: ``importlib.resources.as_file()`` instead — Milodex's distribution
#: targets (PyInstaller, editable install) do not exercise that path.
FONTS_DIR: Path = Path(str(importlib.resources.files("milodex.gui").joinpath("assets/fonts")))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_fonts() -> tuple[int, list[Path]]:
    """Load all bundled ``.ttf`` fonts into Qt's application font database.

    Discovers every ``.ttf`` file under :data:`FONTS_DIR` and registers each
    with :func:`PySide6.QtGui.QFontDatabase.addApplicationFont`.  After this
    call the font families are resolvable by name from any QML surface via
    ``font.family: "Newsreader"`` (etc.).

    Safe to call multiple times: Qt returns the existing font-id for already-
    registered fonts, so repeated calls do not cause duplication.

    Returns
    -------
    tuple[int, list[Path]]
        A 2-tuple of ``(loaded_count, failed)``, where *loaded_count* is the
        number of font files successfully loaded and *failed* is a list of
        :class:`~pathlib.Path` objects for any files that
        ``addApplicationFont`` rejected (returned ``-1``).

    Raises
    ------
    ImportError
        If PySide6 is not installed.  This module is intentionally not
        imported at package-init time so that headless / CLI entry points
        do not require a Qt installation.
    """
    from PySide6.QtGui import QFontDatabase  # noqa: PLC0415  # pragma: no cover

    loaded_count = 0
    failed: list[Path] = []

    ttf_files = sorted(FONTS_DIR.rglob("*.ttf"))
    if not ttf_files:
        logger.warning("load_fonts: no .ttf files found under %s", FONTS_DIR)
        return 0, []

    for ttf_path in ttf_files:
        font_id = QFontDatabase.addApplicationFont(str(ttf_path))
        if font_id == -1:
            logger.warning("load_fonts: failed to load font file %s", ttf_path)
            failed.append(ttf_path)
        else:
            families = QFontDatabase.applicationFontFamilies(font_id)
            if not families:
                # Qt accepted the font (font_id != -1) but extracted no family
                # names. Possible for damaged or format-edge-case files. Log
                # rather than silently increment so the issue surfaces.
                logger.warning(
                    "load_fonts: font_id %d from %s yielded no family names",
                    font_id,
                    ttf_path.name,
                )
            for family in families:
                logger.info("load_fonts: loaded family '%s' from %s", family, ttf_path.name)
            loaded_count += 1

    if failed:
        logger.warning(
            "load_fonts: %d file(s) failed to load: %s",
            len(failed),
            [str(p) for p in failed],
        )
    else:
        logger.info("load_fonts: loaded %d font file(s) with no failures", loaded_count)

    return loaded_count, failed
