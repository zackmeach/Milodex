"""R-XC-016 enforcement: ``# scaffolded:`` marker tally.

Per ``docs/ENGINEERING_STANDARDS.md`` §"Scaffolded vs Implemented" and
SRS R-XC-016, anything not yet fully implemented carries a structured
``# scaffolded:`` comment in code, mirrored in CLI help and the relevant
doc. This test pins the canonical inventory so:

- A new marker without a registry entry fails the test (no silent debt).
- A removed marker without a registry update fails the test (forces real
  closure, including doc update).
- A marker landing on a Phase-1 success-criteria critical path fails the
  test (the SC walkthrough refuses scaffolded code on critical paths).

The verification clause of R-XC-016 calls out exactly this kind of grep
test as the CI tally mechanism.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "milodex"
_MARKER_RE = re.compile(r"#\s*scaffolded:", re.IGNORECASE)

# Canonical registry: rel-path under ``src/milodex/`` → list of substrings
# that must appear on a ``# scaffolded:`` comment line in that file. Update
# this dict in the same commit that adds or removes a marker.
CANONICAL_SCAFFOLDED_MARKERS: dict[str, list[str]] = {
    "analytics/snapshots.py": [
        "runner/engine wiring deferred",
    ],
    "cli/commands/reconcile.py": [
        "deferred reconciliation checks",
        "submit-gate wiring",
    ],
}

# Phase-1 success-criteria critical paths. These drive the SC walkthrough
# (regime + meanrev evaluation, risk vetting, paper submit, kill switch,
# event-store explanation records, backtesting, walk-forward). R-XC-016
# refuses ``# scaffolded:`` markers on these.
SC_CRITICAL_PATHS: frozenset[str] = frozenset(
    {
        "strategies/base.py",
        "strategies/loader.py",
        "strategies/regime_spy_shy_200dma.py",
        "strategies/meanrev_rsi2_pullback.py",
        "strategies/runner.py",
        "risk/evaluator.py",
        "risk/policy.py",
        "execution/service.py",
        "execution/state.py",
        "core/event_store.py",
        "backtesting/engine.py",
        "backtesting/walk_forward.py",
    }
)


def _discover_markers() -> dict[str, list[str]]:
    """Walk ``src/milodex/`` and return rel-path → list of marker comment lines."""
    found: dict[str, list[str]] = {}
    for path in _SRC_ROOT.rglob("*.py"):
        rel = path.relative_to(_SRC_ROOT).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            if _MARKER_RE.search(line):
                found.setdefault(rel, []).append(line.strip())
    return found


class TestScaffoldedMarkers:
    def test_discovered_markers_match_canonical_registry(self):
        discovered = _discover_markers()
        discovered_paths = set(discovered.keys())
        canonical_paths = set(CANONICAL_SCAFFOLDED_MARKERS.keys())

        untracked = sorted(discovered_paths - canonical_paths)
        assert not untracked, (
            f"Untracked `# scaffolded:` markers in: {untracked}. "
            "Register them in CANONICAL_SCAFFOLDED_MARKERS or remove the marker."
        )

        missing = sorted(canonical_paths - discovered_paths)
        assert not missing, (
            f"Canonical files lost their `# scaffolded:` marker: {missing}. "
            "Either restore the marker, or remove the entry from "
            "CANONICAL_SCAFFOLDED_MARKERS (if the feature is now implemented)."
        )

        for rel, expected_substrings in CANONICAL_SCAFFOLDED_MARKERS.items():
            lines = discovered.get(rel, [])
            for substring in expected_substrings:
                assert any(substring.lower() in line.lower() for line in lines), (
                    f"{rel}: expected a `# scaffolded:` line containing "
                    f"'{substring}'. Found lines: {lines}"
                )

    def test_no_scaffolded_marker_on_phase1_critical_paths(self):
        discovered = _discover_markers()
        on_critical = sorted(p for p in discovered if p in SC_CRITICAL_PATHS)
        assert not on_critical, (
            f"`# scaffolded:` markers found on Phase-1-critical paths: {on_critical}. "
            "R-XC-016: the SC walkthrough refuses scaffolded code on critical paths. "
            "Either finish the implementation or move the work off the critical path."
        )

    def test_canonical_registry_paths_resolve_to_real_files(self):
        for rel in CANONICAL_SCAFFOLDED_MARKERS:
            assert (_SRC_ROOT / rel).is_file(), (
                f"CANONICAL_SCAFFOLDED_MARKERS references missing file: {rel}"
            )

    def test_sc_critical_paths_resolve_to_real_files(self):
        for rel in SC_CRITICAL_PATHS:
            assert (_SRC_ROOT / rel).is_file(), f"SC_CRITICAL_PATHS references missing file: {rel}"
