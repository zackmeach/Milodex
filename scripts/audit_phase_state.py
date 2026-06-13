"""Phase-state drift audit for Milodex.

Guards against documentation drift around the project's phase model. Two
independent checks:

1. **Currency check** — a *living* doc must not claim that a phase other than
   the current one (``CURRENT_PHASE``) is "in planning" / "underway" /
   "current". This catches stale status lines like "Phase 4 in planning" after
   the project has moved on. Closed-history docs and ADRs are exempt: they
   legitimately preserve the language of the era they record.

2. **Coverage check** — every Phase-1 reference in the repo must land in a
   recognized lifecycle bucket:
     - ``adr``         — immutable decision records (docs/adr/)
     - ``evidence``    — point-in-time forensic write-ups (docs/reviews/)
     - ``scratch``     — plans/specs working space (docs/superpowers/)
     - ``code``        — runtime identifiers / tests / config (src, tests, configs, scripts)
     - ``historical``  — closed-history or frozen-snapshot living docs
     - ``allowlisted`` — living docs whose Phase-1 mentions are reviewed scope qualifiers
   Anything else is ``unclassified`` — a Phase-1 mention in an unrecognized
   active doc that a human must triage (fix it or add it to the allowlist).

Usage::

    python scripts/audit_phase_state.py            # report, always exit 0
    python scripts/audit_phase_state.py --check     # exit 1 if any drift exists
    python scripts/audit_phase_state.py --root DIR  # audit a different tree

Discovery-only: this script never modifies any file.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The phase the project is actually in. When Phase 6 closes and Phase 7 opens,
# bump this (see docs/README.md "Active planning" and the phase-closure ADR
# chain 0023 → 0025 → 0027 → 0031 → 0038). The currency check re-arms against
# the new value automatically.
CURRENT_PHASE = 6

# --- lifecycle path map ----------------------------------------------------

# Path prefixes that are inherently exempt from drift (repo-relative, "/"-sep).
_ADR_PREFIXES = ("docs/adr/",)
_EVIDENCE_PREFIXES = ("docs/reviews/",)
_SCRATCH_PREFIXES = ("docs/superpowers/",)
_CODE_PREFIXES = ("src/", "tests/", "configs/", "scripts/")

# Closed-history and frozen-snapshot living docs. Phase-1 references here are
# historical record; currency language here is the language of the era.
_HISTORICAL_DOCS = frozenset(
    {
        "docs/ROADMAP_PHASE1.md",
        "docs/PHASE2_PLANNING.md",
        "docs/PHASE3_PLANNING.md",
        "docs/PHASE4_PLANNING.md",
        "docs/PHASE5_PLANNING.md",
        "docs/TEST_EFFICACY_AUDIT.md",
        "docs/LAUNCH_READINESS.md",
    }
)

# Active living docs whose Phase-1 references have been reviewed and judged to
# be scope/constraint qualifiers (e.g. "Phase 1 default", "out of scope for
# Phase 1", the Phase-1 instrument whitelist) rather than claims that Phase 1
# is the current phase. New living docs are NOT auto-trusted: a Phase-1 mention
# in a doc not listed here is reported as unclassified until a human triages it.
_ALLOWLISTED_DOCS = frozenset(
    {
        "README.md",
        "CLAUDE.md",
        "AGENTS.md",
        "docs/README.md",
        "docs/VISION.md",
        "docs/SRS.md",
        "docs/PRODUCT.md",
        "docs/FOUNDER_INTENT.md",
        "docs/RISK_POLICY.md",
        "docs/REPORTING.md",
        "docs/OPERATIONS.md",
        "docs/PAPER_WORKFLOW.md",
        "docs/DISTRIBUTION.md",
        "docs/strategy-families.md",
        "docs/ENGINEERING_STANDARDS.md",
        "docs/PROMOTION_GOVERNANCE.md",
        "docs/INSTALL.md",
        "docs/STRATEGY_BANK.md",
        "docs/CLI_UX.md",
        "docs/REQUIREMENTS_COVERAGE.md",
        "docs/PHASE6_BENCH_PREP.md",
        "docs/bench/README.md",
        "docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md",
        "docs/architecture/roadmaps/2026-06-10-hardening-roadmap.md",
        "docs/architecture/2026-06-13-cross-process-submit-serialization-design.md",
    }
)

# Categories that never count as drift.
_OK_CATEGORIES = frozenset({"adr", "evidence", "scratch", "code", "historical", "allowlisted"})

# Files scanned for references / currency claims.
_TEXT_SUFFIXES = frozenset({".md", ".py", ".yaml", ".yml"})
_SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "data",
        "market_cache",
        "logs",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        "htmlcov",
        "reports",
        "tmp",
    }
)

# --- matchers --------------------------------------------------------------

# A Phase-1-family reference: "Phase 1", "Phase-1", "Phase One", "phase1",
# "Phase 1.5". The negative lookahead stops "Phase 12"/"Phase 10" matching as
# Phase 1.
_PHASE1_RE = re.compile(r"phase[\s-]?(?:one\b|1(?!\d))", re.IGNORECASE)

# Living-doc currency claims that name an active/planning phase. Each pattern
# captures the claimed phase number so it can be compared to CURRENT_PHASE.
_CURRENCY_RES = (
    re.compile(r"phase\s*(\d+)\s+in planning", re.IGNORECASE),
    re.compile(r"phase\s*(\d+)\s+planning is underway", re.IGNORECASE),
    re.compile(r"currently in phase\s*(\d+)", re.IGNORECASE),
    re.compile(r"we are (?:now )?in phase\s*(\d+)", re.IGNORECASE),
)


def classify_path(rel_path: str) -> str:
    """Map a repo-relative path to its lifecycle category."""
    p = rel_path.replace("\\", "/")
    if p in _HISTORICAL_DOCS:
        return "historical"
    if any(p.startswith(prefix) for prefix in _ADR_PREFIXES):
        return "adr"
    if any(p.startswith(prefix) for prefix in _EVIDENCE_PREFIXES):
        return "evidence"
    if any(p.startswith(prefix) for prefix in _SCRATCH_PREFIXES):
        return "scratch"
    if any(p.startswith(prefix) for prefix in _CODE_PREFIXES):
        return "code"
    if p in _ALLOWLISTED_DOCS:
        return "allowlisted"
    return "unclassified"


def iter_phase_references(text: str) -> list[tuple[int, str]]:
    """Return ``(lineno, line)`` for each line containing a Phase-1 reference."""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _PHASE1_RE.search(line):
            out.append((lineno, line))
    return out


def detect_currency_claims(text: str, current_phase: int) -> list[tuple[int, str, int]]:
    """Return ``(lineno, line, claimed_phase)`` for stale currency claims.

    A claim is stale (drift) when it asserts that a phase *other than*
    ``current_phase`` is the active/planning phase.
    """
    out: list[tuple[int, str, int]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in _CURRENCY_RES:
            for match in pattern.finditer(line):
                claimed = int(match.group(1))
                if claimed != current_phase:
                    out.append((lineno, line, claimed))
    return out


@dataclass
class PhaseReference:
    path: str
    lineno: int
    line: str
    category: str
    note: str = ""


@dataclass
class AuditResult:
    references: list[PhaseReference] = field(default_factory=list)
    currency_drift: list[PhaseReference] = field(default_factory=list)

    @property
    def unclassified(self) -> list[PhaseReference]:
        return [r for r in self.references if r.category == "unclassified"]

    @property
    def ok(self) -> bool:
        return not self.currency_drift and not self.unclassified

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for ref in self.references:
            tally[ref.category] = tally.get(ref.category, 0) + 1
        return tally


def _git_tracked_files(root: Path) -> list[Path] | None:
    """Return git-tracked files under ``root``, or None if ``root`` is not a repo.

    Using the tracked set means gitignored build output (dist/, build/, the
    PyInstaller onedir, coverage HTML, etc.) is excluded for free, so the audit
    sees only source-of-truth content.
    """
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    return sorted(root / rel for rel in completed.stdout.split("\0") if rel)


def _iter_text_files(root: Path):
    """Yield text files under ``root``, skipping vendored / generated trees.

    Prefers the git-tracked file set; falls back to a filtered directory walk
    for non-repo trees (e.g. synthetic test fixtures).
    """
    candidates = _git_tracked_files(root)
    if candidates is None:
        candidates = sorted(root.rglob("*"))
    for path in candidates:
        if not path.is_file() or path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        yield path


def audit_repo(root: Path = REPO_ROOT, current_phase: int = CURRENT_PHASE) -> AuditResult:
    """Scan ``root`` for Phase-1 references and stale currency claims."""
    result = AuditResult()
    for path in _iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        category = classify_path(rel)
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for lineno, line in iter_phase_references(text):
            result.references.append(
                PhaseReference(path=rel, lineno=lineno, line=line.strip(), category=category)
            )

        # Currency check applies only to active living docs (allowlisted or
        # not-yet-classified). Historical docs, ADRs, evidence, and code are
        # exempt — old "in planning" language there is a faithful record.
        if category in ("allowlisted", "unclassified"):
            for lineno, line, claimed in detect_currency_claims(text, current_phase):
                result.currency_drift.append(
                    PhaseReference(
                        path=rel,
                        lineno=lineno,
                        line=line.strip(),
                        category="currency-drift",
                        note=f"claims Phase {claimed} is active; current is Phase {current_phase}",
                    )
                )
    return result


def _print_report(result: AuditResult, current_phase: int) -> None:
    print(f"Phase-state audit - current phase: {current_phase}")
    print(f"Total Phase-1 references: {len(result.references)}")
    print("By category:")
    for category in sorted(result.counts()):
        print(f"  {category:<13} {result.counts()[category]}")

    print()
    if result.currency_drift:
        print(f"CURRENCY DRIFT ({len(result.currency_drift)}) - living docs claim a closed phase:")
        for ref in result.currency_drift:
            print(f"  {ref.path}:{ref.lineno}  - {ref.note}")
            print(f"      {ref.line}")
    else:
        print("CURRENCY DRIFT: none")

    print()
    if result.unclassified:
        print(f"UNCLASSIFIED ({len(result.unclassified)}) - Phase-1 mention in unrecognized doc:")
        for ref in result.unclassified:
            print(f"  {ref.path}:{ref.lineno}")
            print(f"      {ref.line}")
        print("  -> fix the doc or add it to _ALLOWLISTED_DOCS / _HISTORICAL_DOCS.")
    else:
        print("UNCLASSIFIED: none")

    print()
    print("RESULT:", "clean" if result.ok else "DRIFT DETECTED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit Milodex phase-state documentation drift.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any currency drift or unclassified reference exists.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to audit (default: the Milodex repo).",
    )
    args = parser.parse_args(argv)

    result = audit_repo(args.root, CURRENT_PHASE)
    _print_report(result, CURRENT_PHASE)

    if args.check and not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
