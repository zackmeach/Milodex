"""Structural firewall tests: promotion/ must never read the experiment_registry.

Decision record: docs/reviews/2026-07-09-D5-evidence-durability-brief.md
("Sharpen amendments", item 1). The experiment_registry (research/evidence
ledger) holds permanently-exploratory, non-durable IEX verdicts (ADR 0017);
no promotion codepath may read it — that firewall exists today only by
accident of wiring (promotion/ never had a reason to touch it), so these
tests enforce it structurally rather than trusting the accident to persist.

A plain import-forbid alone would be decorative: promotion/ legitimately
imports and holds a live `milodex.core.event_store.EventStore`
(`promotion/orchestrator.py`), and the registry reader/writer methods
(`list_experiments` / `get_experiment` / `append_experiment` /
`update_experiment`) ride on that same, unforbiddable object — a breach adds
no new top-level import. So this file combines a source-token scan (catches
a breach that calls those methods on the existing EventStore) with an
import-forbid on the CLI/research modules that have no legitimate reason to
be imported from promotion/.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# tests/milodex/promotion/ -> repo root -> src/milodex/promotion.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMOTION_SRC = _REPO_ROOT / "src" / "milodex" / "promotion"
_GUI_SRC = _REPO_ROOT / "src" / "milodex" / "gui"
_COMMANDS_SRC = _REPO_ROOT / "src" / "milodex" / "commands"

# The four experiment_registry read/write methods (core/event_store.py) plus the
# registry name itself, scanned as plain substrings.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "list_experiments",
    "get_experiment",
    "append_experiment",
    "update_experiment",
    "experiment_registry",
)

# Bare "experiment" as a table/identifier token, word-boundary matched so
# "experiments_x"-style unrelated identifiers don't false-positive.
_BARE_EXPERIMENT_RE = re.compile(r"\bexperiment\b")

_FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "from milodex.cli.commands.experiment",
    "import milodex.cli.commands.experiment",
    "from milodex.cli.commands.research",
    "import milodex.cli.commands.research",
    "from milodex.research.evidence_assembler",
    "import milodex.research.evidence_assembler",
)


def _promotion_source_files() -> list[Path]:
    return sorted(_PROMOTION_SRC.rglob("*.py"))


def _gui_and_commands_source_files() -> list[Path]:
    return sorted(_GUI_SRC.rglob("*.py")) + sorted(_COMMANDS_SRC.rglob("*.py"))


@pytest.mark.parametrize("src_file", _promotion_source_files(), ids=lambda p: p.name)
def test_promotion_file_contains_no_experiment_registry_tokens(src_file: Path) -> None:
    """D-5 C+: promotion/ source must never name an experiment_registry read/write.

    Fails if any promotion/**.py file contains list_experiments, get_experiment,
    append_experiment, update_experiment, experiment_registry, or the bare word
    "experiment" as a table/identifier token — the exploratory IEX ledger must
    never be readable from a promotion codepath (docs/reviews/2026-07-09-D5-
    evidence-durability-brief.md).
    """
    text = src_file.read_text(encoding="utf-8")
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in text, (
            f"{src_file.name} must not reference {forbidden!r} — the experiment_registry "
            "firewall (D-5 C+) forbids promotion/ from touching the research-evidence "
            "ledger. See docs/reviews/2026-07-09-D5-evidence-durability-brief.md."
        )
    # Bare "experiment" is scanned line-by-line, skipping comment-only lines, so an
    # innocuous prose comment (e.g. "# a quick experiment with retries") can't
    # false-positive the way a code identifier or table-name reference would.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        bare_match = _BARE_EXPERIMENT_RE.search(line)
        assert bare_match is None, (
            f"{src_file.name} must not reference the bare token 'experiment' — the "
            "experiment_registry firewall (D-5 C+) forbids promotion/ from touching the "
            f"research-evidence ledger. Line: {line!r}. See "
            "docs/reviews/2026-07-09-D5-evidence-durability-brief.md."
        )


@pytest.mark.parametrize("src_file", _promotion_source_files(), ids=lambda p: p.name)
def test_promotion_file_contains_no_experiment_cli_or_assembler_imports(src_file: Path) -> None:
    """D-5 C+: promotion/ source must never import the experiment/research CLI or assembler.

    Fails if any promotion/**.py file imports milodex.cli.commands.experiment,
    milodex.cli.commands.research, or milodex.research.evidence_assembler —
    none of these are legitimate promotion-codepath dependencies (docs/reviews/
    2026-07-09-D5-evidence-durability-brief.md, "Sharpen amendments" item 1).
    """
    for line in src_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
            assert not stripped.startswith(forbidden), (
                f"{src_file.name} must not import {forbidden!r} (D-5 C+ firewall). Line: {line!r}"
            )


@pytest.mark.parametrize("src_file", _gui_and_commands_source_files(), ids=lambda p: str(p))
def test_gui_and_commands_contain_no_experiment_registry_references(src_file: Path) -> None:
    """D-5: gui/ and commands/ preserve the zero-experiment_registry-reads invariant.

    Fails if any gui/**.py or commands/**.py file references experiment_registry —
    the decision record notes this operator-facing surface currently has zero
    reads and records that as an invariant to preserve until a durability column
    (Option B, D-8-gated) exists (docs/reviews/2026-07-09-D5-evidence-durability-
    brief.md, "Sharpen amendments" item 2).
    """
    text = src_file.read_text(encoding="utf-8")
    assert "experiment_registry" not in text, (
        f"{src_file} must not reference 'experiment_registry' — gui/ and commands/ "
        "preserve the zero-registry-reads invariant recorded in docs/reviews/"
        "2026-07-09-D5-evidence-durability-brief.md."
    )
