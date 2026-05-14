"""Ratchet test — `Theme.typography.data.*` consumers must apply tabular figures.

Per DESIGN_SYSTEM.md §2 non-negotiable callout:

> Any column of numbers — in any surface, in any theme — renders in
> `typography.data.*` (JetBrains Mono) with `tnum` on.

The `tnum` OpenType feature is declared on the `data.md`, `data.sm`, and
`data.xs` tokens in `Theme.qml` (the `features: ["tnum"]` property), but
QML consumers must explicitly bind `font.features` to that property —
setting only `font.family` and `font.pixelSize` consumes the token's
glyph metrics without enabling tabular-figure spacing, which silently
re-introduces proportional digit widths in tabular columns. The visual
regression looks like "numbers drift left-to-right across rows" and was
the largest density violation flagged by the 2026-05-13 UI critique.

This ratchet keeps every numeric-bearing QML file's `data.*.family`
occurrence count equal to its `data.*.features` occurrence count. If a
future PR adds a `font.family: Theme.typography.data.<X>.family`
declaration without the matching `font.features: ...` companion, this
test fails with a per-file gap report.

**Scope is intentionally narrow.** The allowlist below names the QML
files that today render numerics in tabular contexts. A global grep
across `src/` is rejected as too brittle — it would false-positive on
documentation, comments, or experimental files. The allowlist is what
the test protects; PRs that add new numeric surfaces add themselves to
this list as part of their landing checklist.

If a glyph (asterisk, bullet, drag handle) uses a `data.*` token, the
ratchet still requires it to set `font.features` — `tnum` is a no-op on
non-digit text, so the equality rule has no functional downside and the
rule stays simple. (Reclassifying glyph-style usage to a glyph-specific
token is a separate, doctrine-shaped follow-up.)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Allowlist — QML files that render numerics in tabular or column contexts.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_QML_ROOT = _REPO_ROOT / "src" / "milodex" / "gui" / "qml" / "Milodex"

# Files that consume `Theme.typography.data.*` for numeric or column-aligned
# content. New surfaces or components that render numerics MUST add themselves
# here and bind `font.features` at every `font.family: Theme.typography.data.*`
# declaration.
NUMERIC_QML_FILES: tuple[str, ...] = (
    # Components
    "components/BenchRow.qml",
    "components/BenchEvidenceModal.qml",
    "components/BenchConfirmationModal.qml",
    "components/StrategyRow.qml",
    "components/GateTable.qml",
    # Surfaces
    "surfaces/AnchorSurface.qml",
    "surfaces/BenchSurface.qml",
    "surfaces/DeskSurface.qml",
    "surfaces/DesignSystemShowcase.qml",
    "surfaces/FrontSurface.qml",
    "surfaces/KanbanSurface.qml",
    "surfaces/LedgerSurface.qml",
)


_FAMILY_RE = re.compile(r"Theme\.typography\.data\.(md|sm|xs)\.family")
_FEATURES_RE = re.compile(r"Theme\.typography\.data\.(md|sm|xs)\.features")


def _count(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text))


@pytest.mark.parametrize("relative_path", NUMERIC_QML_FILES)
def test_data_token_consumer_sets_tnum_features(relative_path: str) -> None:
    """Every `data.*.family` consumer in this file must also bind `data.*.features`.

    See module docstring for the rule, the failure mode, and how to add a
    new file to the allowlist.
    """
    qml_path = _QML_ROOT / relative_path
    assert qml_path.is_file(), (
        f"Allowlist references {relative_path} but the file does not exist. "
        f"If the file was renamed or removed, update NUMERIC_QML_FILES."
    )

    text = qml_path.read_text(encoding="utf-8")
    family_count = _count(_FAMILY_RE, text)
    features_count = _count(_FEATURES_RE, text)

    assert family_count == features_count, (
        f"{relative_path}: "
        f"{family_count} `Theme.typography.data.*.family` declarations vs "
        f"{features_count} `Theme.typography.data.*.features` declarations "
        f"(gap of {family_count - features_count}). "
        f"Every consumer of a `data.*` token must also bind `font.features` "
        f"so tabular figures are applied. See DESIGN_SYSTEM.md §2 "
        f"non-negotiable callout and the test module docstring."
    )


def test_allowlist_covers_every_numeric_consumer() -> None:
    """The allowlist must enumerate every QML file under `qml/Milodex` that
    references `Theme.typography.data.*`. If a new file slips in without
    being added to the allowlist, the per-file ratchet above can't protect
    it. This second test fails when that happens, pointing at the new file.
    """
    referenced: set[Path] = set()
    for path in _QML_ROOT.rglob("*.qml"):
        text = path.read_text(encoding="utf-8")
        if _FAMILY_RE.search(text):
            referenced.add(path.relative_to(_QML_ROOT))

    allowlisted = {Path(p) for p in NUMERIC_QML_FILES}
    missing = referenced - allowlisted

    assert not missing, (
        "QML files reference `Theme.typography.data.*` but are not in "
        "NUMERIC_QML_FILES. Add them to the allowlist in this test module "
        "(and confirm each `data.*.family` consumer also binds "
        "`data.*.features`):\n  "
        + "\n  ".join(sorted(str(p) for p in missing))
    )
