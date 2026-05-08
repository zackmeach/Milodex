"""Tests for milodex.gui.fonts — font-loading bootstrap.

These tests verify:
- All three bundled families are registered with Qt after ``load_fonts()``.
- The function returns a nonzero count and an empty failure list.
- Calling twice is idempotent.
- The ``FONTS_DIR`` asset directory exists and contains ``.ttf`` files.

Qt-dependent tests (those using QApplication / QFontDatabase) skip
automatically when PySide6 is not importable (headless CI without Qt).
The directory-existence test does not require Qt and always runs.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# PySide6 availability guard
# ---------------------------------------------------------------------------

try:
    from PySide6.QtWidgets import QApplication

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping Qt font tests",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication.

    QApplication must exist before any QFontDatabase call.  We reuse a single
    instance across all tests in this module for speed.
    """
    import sys

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fonts_dir_exists():
    """The asset directory is real and contains .ttf files.

    Does not require Qt — only checks the filesystem.
    """
    from milodex.gui.fonts import FONTS_DIR

    assert FONTS_DIR.exists(), f"FONTS_DIR does not exist: {FONTS_DIR}"
    assert FONTS_DIR.is_dir(), f"FONTS_DIR is not a directory: {FONTS_DIR}"
    ttf_files = list(FONTS_DIR.rglob("*.ttf"))
    assert len(ttf_files) > 0, f"No .ttf files found under {FONTS_DIR}"


@_skip_no_qt
def test_load_fonts_returns_nonzero_count(qapp):
    """load_fonts() returns a positive loaded_count and empty failed list."""
    from milodex.gui.fonts import load_fonts

    loaded_count, failed = load_fonts()

    assert loaded_count > 0, "Expected at least one font file to load successfully"
    assert failed == [], f"Expected no failures, got: {failed}"


@_skip_no_qt
def test_load_fonts_finds_all_three_families(qapp):
    """After load_fonts(), Qt knows about Newsreader, Public Sans, and JetBrains Mono."""
    from PySide6.QtGui import QFontDatabase

    from milodex.gui.fonts import load_fonts

    load_fonts()

    registered = QFontDatabase.families()
    for expected_family in ("Newsreader", "Public Sans", "JetBrains Mono"):
        assert expected_family in registered, (
            f"Font family '{expected_family}' not found in QFontDatabase.families(). "
            f"Registered families containing a substring: "
            f"{[f for f in registered if any(w in f for w in expected_family.split())]}"
        )


@_skip_no_qt
def test_load_fonts_is_idempotent(qapp):
    """Calling load_fonts() twice does not duplicate fonts in Qt's database.

    The contract documented in load_fonts() is that repeated calls do not
    cause duplication.  Asserting only that the return value is stable would
    pass even if Qt accumulated duplicate registrations, so this test
    additionally snapshots ``QFontDatabase.families()`` count before the
    second call and asserts it is unchanged afterwards — the actual
    behavioural contract.
    """
    from PySide6.QtGui import QFontDatabase

    from milodex.gui.fonts import load_fonts

    # First call loads the bundled fonts (or no-ops if a prior test loaded
    # them already in this module-scoped QApplication).
    count_first, failed_first = load_fonts()
    assert failed_first == [], "First call had unexpected failures"

    families_before_second = set(QFontDatabase.families())

    # Second call must not increase the family registry.
    count_second, failed_second = load_fonts()

    families_after_second = set(QFontDatabase.families())

    assert failed_second == [], "Second call had unexpected failures"
    assert count_second == count_first, (
        f"Idempotency broken: first={count_first}, second={count_second}"
    )
    assert families_after_second == families_before_second, (
        "Idempotency broken: QFontDatabase.families() changed across "
        f"the second load_fonts() call. Added: "
        f"{families_after_second - families_before_second}"
    )
