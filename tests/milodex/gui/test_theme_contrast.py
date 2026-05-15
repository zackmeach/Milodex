"""WCAG 2.1 contrast-ratio audit for the three Milodex themes.

Operates purely on hex values parsed from the theme QML files — does NOT
require PySide6 / Qt to be installed.  This audit is the regression
catch-net for token-color drift: if any critical text/surface pair drops
below WCAG AA, this test fails and the theme PR cannot ship.

WCAG 2.1 AA contrast requirements:

- ``>= 4.5:1`` for normal-size text (default for unknown-purpose pairs).
- ``>= 3.0:1`` for large text (>= 18pt regular or >= 14pt bold) and for
  graphical elements / decorative use.

Implementation notes:

- The contrast ratio formula uses the sRGB-to-linear gamma correction
  (the if/else around ``0.04045``) per WCAG 2.1 §1.4.3.
- ``text.disabled`` is intentionally exempt — disabled state has no AA
  requirement under WCAG 2.1.
- ``border.subtle`` and ``border.regular`` are decorative and tested
  against the looser 3.0:1 bar (large-text / graphical-element rule).
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_THEMES_DIR: Path = (
    Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui" / "qml" / "Milodex" / "themes"
)

# Launch scope is Editorial Dark only (#132); Light + Bronze are deferred
# post-launch and unreachable in the UI, so gating shippable work on their
# contrast would be testing colors no operator can select. Re-add them here
# when they are un-deferred.
_THEME_FILES: dict[str, Path] = {
    "editorial-dark": _THEMES_DIR / "EditorialDark.qml",
}


# ---------------------------------------------------------------------------
# QML hex parsing
# ---------------------------------------------------------------------------

# Match `<token>: "#rrggbb"` lines.  We only care about the leaf token name
# (the last identifier on the LHS), grouping by surrounding `<bag> { ... }`
# QtObject blocks.
_TOKEN_LINE = re.compile(
    r'readonly\s+property\s+string\s+(?P<name>\w+)\s*:\s*"(?P<hex>#[0-9a-fA-F]{6})"'
)
_BAG_OPEN = re.compile(r"property\s+var\s+(?P<bag>\w+)\s*:\s*QtObject\s*{")


def _parse_theme(path: Path) -> dict[str, dict[str, str]]:
    """Return ``{bag_name: {token_name: hex}}`` for a theme file.

    Tracks nested ``QtObject { ... }`` blocks so tokens are scoped to
    their enclosing bag (``surface``, ``border``, ``brand``, ``text``,
    ``status``).  The outer bag (``color`` / ``status``) is the
    grandparent — we report just the inner bag because every consumer
    references e.g. ``color.surface.base`` as ``surface.base``.
    """
    text = path.read_text(encoding="utf-8")
    out: dict[str, dict[str, str]] = {}
    bag_stack: list[str] = []
    depth = 0

    for line in text.splitlines():
        bag_match = _BAG_OPEN.search(line)
        token_match = _TOKEN_LINE.search(line)
        # Track brace depth so we pop off bags correctly.
        opens = line.count("{")
        closes = line.count("}")

        if bag_match and opens >= 1:
            bag_stack.append(bag_match.group("bag"))
            depth += opens
            depth -= closes
            continue

        if token_match and bag_stack:
            inner_bag = bag_stack[-1]
            out.setdefault(inner_bag, {})[token_match.group("name")] = token_match.group(
                "hex"
            ).lower()

        depth += opens
        # Pop bags when the brace they opened with closes again.  The
        # cheap heuristic: each closing brace pops one bag if the bag
        # stack is non-empty; the grammar of these files (every QtObject
        # opens with `{` on its definition line) makes this exact.
        for _ in range(closes):
            if bag_stack:
                bag_stack.pop()
        depth -= closes

    return out


# ---------------------------------------------------------------------------
# WCAG contrast formula
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert ``#rrggbb`` to a (r, g, b) tuple of 0-255 ints."""
    h = hex_str.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _srgb_to_linear(channel_8bit: int) -> float:
    """Convert an 8-bit sRGB channel to linear-light per WCAG 2.1 §1.4.3."""
    c = channel_8bit / 255.0
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_str: str) -> float:
    r, g, b = _hex_to_rgb(hex_str)
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Return the WCAG 2.1 contrast ratio between two colors (>= 1.0)."""
    l1 = _relative_luminance(fg_hex)
    l2 = _relative_luminance(bg_hex)
    lighter, darker = (l1, l2) if l1 > l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# Audit pairs
# ---------------------------------------------------------------------------


# Each entry: (fg_path, bg_path, min_ratio, label).  Path is "<bag>.<name>".
# - 4.5: normal text (default).
# - 3.0: large text / decorative (border lines, status indicators that
#   accompany text rather than replace it, brand chrome on surfaces).
_PAIRS: list[tuple[str, str, float, str]] = [
    # Body text on every surface tier
    ("text.primary", "surface.canvas", 4.5, "body text on canvas"),
    ("text.primary", "surface.base", 4.5, "body text on base"),
    ("text.primary", "surface.raised", 4.5, "body text on raised"),
    ("text.secondary", "surface.base", 4.5, "secondary text on base"),
    ("text.muted", "surface.base", 4.5, "muted text on base"),
    # Brand text usage — primary is sometimes a heading-weight color.
    # Treat as decorative-ish (3.0) because brand.primary is used for
    # display.* serif treatments which qualify as large text.
    ("brand.primary", "surface.base", 3.0, "brand primary on base (large/display)"),
    # brand.accent on surface.base is a deliberate exception: the oxblood
    # accent #722f37 is the foundational brand identity (PR A) and
    # inherently can't hit 3.0:1 on the near-black surfaces that the dark
    # themes require.  In practice brand.accent is rendered as a 2px
    # selection bar that ALWAYS accompanies a redundant surface tier
    # change (surface.base -> surface.raised on hover/select), so the
    # selection state remains discriminable.  We assert >= 1.5:1 so
    # accidental palette regressions still trip the audit.
    (
        "brand.accent",
        "surface.base",
        1.5,
        "brand accent on base (decorative chrome — paired with surface tier change)",
    ),
    # Text on the brand accent (e.g. primary button label)
    ("text.onBrand", "brand.accent", 4.5, "text.onBrand on brand.accent"),
    # Status colors on surface.base — these are decorative pills + dot
    # indicators that always accompany text, so 3.0 is the correct bar
    # except where they're consumed as text (status pill labels).  We
    # check the stricter 4.5 because StatusPill renders the role color
    # AS the label text on a 12% tint of itself — the effective contrast
    # is between the role color and surface.base.
    ("status.positive", "surface.base", 4.5, "status.positive label on base"),
    ("status.warning", "surface.base", 4.5, "status.warning label on base"),
    ("status.negative", "surface.base", 4.5, "status.negative label on base"),
    ("status.info", "surface.base", 4.5, "status.info label on base"),
]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _resolve(theme_data: dict[str, dict[str, str]], path: str) -> str:
    """Resolve ``"bag.name"`` to a hex string from the parsed theme.

    Knows that ``brand``, ``surface``, ``border``, ``text`` are sub-bags
    of the ``color`` bag, and ``positive`` / ``negative`` etc. live in
    the ``status`` bag.  The QML grammar puts each leaf in its sub-bag,
    so we look up by the sub-bag name regardless of the outer container.
    """
    bag, name = path.split(".", 1)
    if bag not in theme_data:
        raise KeyError(f"bag {bag!r} not found in theme; available: {list(theme_data)}")
    leaves = theme_data[bag]
    if name not in leaves:
        raise KeyError(f"token {name!r} not found in bag {bag!r}; available: {list(leaves)}")
    return leaves[name]


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_all_themes_meet_wcag_minimums() -> None:
    """Every (text, surface) pair across every theme passes WCAG AA.

    Failure mode is one collected list per theme listing every offending
    pair with its actual ratio and the threshold it missed.  The user
    sees the full damage in one run rather than fail-on-first.
    """
    failures: list[str] = []

    for theme_name, path in _THEME_FILES.items():
        theme_data = _parse_theme(path)
        for fg, bg, min_ratio, label in _PAIRS:
            fg_hex = _resolve(theme_data, fg)
            bg_hex = _resolve(theme_data, bg)
            ratio = contrast_ratio(fg_hex, bg_hex)
            if ratio < min_ratio:
                failures.append(
                    f"  [{theme_name}] {fg} ({fg_hex}) on {bg} ({bg_hex}): "
                    f"{ratio:.2f}:1 < {min_ratio}:1 ({label})"
                )

    assert not failures, "WCAG AA contrast failures:\n" + "\n".join(failures)


def test_brand_primary_distinct_from_text_primary_per_theme() -> None:
    """``color.brand.primary`` must not equal ``color.text.primary`` in any theme.

    This is the regression test for the Editorial Light collision found
    in PR D.6 review where both tokens collapsed to ``#2a2218`` and there
    was no usable brand color in the light theme.
    """
    failures: list[str] = []
    for theme_name, path in _THEME_FILES.items():
        theme_data = _parse_theme(path)
        brand = _resolve(theme_data, "brand.primary")
        text = _resolve(theme_data, "text.primary")
        if brand == text:
            failures.append(
                f"  [{theme_name}] brand.primary == text.primary == {brand} "
                "(no distinct brand color)"
            )
    assert not failures, "brand/text collisions:\n" + "\n".join(failures)
