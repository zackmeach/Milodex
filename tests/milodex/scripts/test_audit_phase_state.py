"""Tests for scripts/audit_phase_state.py.

The phase-state audit guards against documentation drift: active ("living")
docs must not imply a closed phase is the current one, and every Phase-1
reference in the repo must land in a recognized lifecycle bucket
(ADR / evidence / scratch / code-identifier / closed-history / reviewed
allowlist) rather than in an unclassified active doc.

These tests cover the pure helpers (classify_path, iter_phase_references,
detect_currency_claims) with synthetic inputs, the audit_repo orchestrator on
a synthetic repo tree, and a real-repo smoke test that encodes the completion
condition (the live repo must audit clean).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the scripts/ directory is importable (mirrors test_audit_requirements_coverage).
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from audit_phase_state import (  # noqa: E402
    CURRENT_PHASE,
    audit_repo,
    classify_path,
    detect_currency_claims,
    iter_phase_references,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class TestClassifyPath:
    def test_adr_is_adr(self) -> None:
        assert classify_path("docs/adr/0023-phase-1-is-closed.md") == "adr"

    def test_reviews_is_evidence(self) -> None:
        assert classify_path("docs/reviews/2026-04-22-phase1-health.md") == "evidence"

    def test_superpowers_is_scratch(self) -> None:
        assert classify_path("docs/superpowers/plans/x.md") == "scratch"

    def test_src_is_code(self) -> None:
        assert classify_path("src/milodex/strategies/runner.py") == "code"

    def test_configs_is_code(self) -> None:
        assert classify_path("configs/universe_phase1_v1.yaml") == "code"

    def test_tests_is_code(self) -> None:
        assert classify_path("tests/milodex/test_no_live_mode_drift.py") == "code"

    def test_closed_history_doc_is_historical(self) -> None:
        assert classify_path("docs/ROADMAP_PHASE1.md") == "historical"
        assert classify_path("docs/PHASE2_PLANNING.md") == "historical"

    def test_frozen_snapshot_is_historical(self) -> None:
        assert classify_path("docs/LAUNCH_READINESS.md") == "historical"

    def test_reviewed_living_doc_is_allowlisted(self) -> None:
        assert classify_path("README.md") == "allowlisted"
        assert classify_path("docs/VISION.md") == "allowlisted"

    def test_unknown_active_doc_is_unclassified(self) -> None:
        assert classify_path("docs/SOME_NEW_DOC.md") == "unclassified"

    def test_windows_separators_normalized(self) -> None:
        assert classify_path("docs\\adr\\0023.md") == "adr"


class TestIterPhaseReferences:
    def test_matches_plain_phase_one(self) -> None:
        assert iter_phase_references("We are in Phase 1 now.") == [(1, "We are in Phase 1 now.")]

    def test_matches_phase_word(self) -> None:
        assert len(iter_phase_references("Phase One scope is fixed.")) == 1

    def test_matches_hyphenated(self) -> None:
        assert len(iter_phase_references("the Phase-1 whitelist")) == 1

    def test_matches_code_identifier(self) -> None:
        assert len(iter_phase_references("universe_phase1_v1")) == 1

    def test_matches_sub_phase(self) -> None:
        # "Phase 1.5 hardening" is still a Phase-1-family reference.
        assert len(iter_phase_references("future Phase 1.5 hardening")) == 1

    def test_does_not_match_phase_two(self) -> None:
        assert iter_phase_references("Phase 2 planning is underway.") == []

    def test_does_not_match_phase_twelve(self) -> None:
        # The "1" in "Phase 12" must not be mistaken for Phase 1.
        assert iter_phase_references("Phase 12 roadmap") == []

    def test_reports_correct_line_numbers(self) -> None:
        text = "line one\nPhase 1 here\nline three\nand Phase One again\n"
        refs = iter_phase_references(text)
        assert [lineno for lineno, _ in refs] == [2, 4]


class TestDetectCurrencyClaims:
    def test_flags_stale_in_planning_claim(self) -> None:
        claims = detect_currency_claims("Status: Phase 4 in planning.", current_phase=6)
        assert len(claims) == 1
        lineno, _line, claimed = claims[0]
        assert lineno == 1
        assert claimed == 4

    def test_flags_planning_underway(self) -> None:
        claims = detect_currency_claims("Phase 4 planning is underway.", current_phase=6)
        assert [c[2] for c in claims] == [4]

    def test_flags_currently_in_phase(self) -> None:
        claims = detect_currency_claims("We are currently in Phase 3.", current_phase=6)
        assert [c[2] for c in claims] == [3]

    def test_does_not_flag_current_phase(self) -> None:
        # A claim that the *actual* current phase is active is not drift.
        assert detect_currency_claims("Phase 6 in planning.", current_phase=6) == []

    def test_no_claim_no_flag(self) -> None:
        assert detect_currency_claims("Phase 1 caps capital at $1,000.", current_phase=6) == []

    def test_correct_line_number(self) -> None:
        text = "intro\nmore\nPhase 4 in planning\n"
        claims = detect_currency_claims(text, current_phase=6)
        assert claims[0][0] == 3


class TestAuditRepoSynthetic:
    def _build_repo(self, root: Path) -> None:
        (root / "docs" / "adr").mkdir(parents=True)
        (root / "docs" / "adr" / "0023-x.md").write_text(
            "Phase 1 is closed by this ADR.", encoding="utf-8"
        )
        (root / "docs" / "ROADMAP_PHASE1.md").write_text(
            "Phase 1 roadmap. Phase 1 in planning historically.", encoding="utf-8"
        )
        (root / "src").mkdir()
        (root / "src" / "thing.py").write_text("PHASE1 = 'universe_phase1_v1'\n", encoding="utf-8")

    def test_clean_repo_audits_ok(self, tmp_path: Path) -> None:
        self._build_repo(tmp_path)
        result = audit_repo(tmp_path)
        assert result.ok is True
        assert result.unclassified == []
        assert result.currency_drift == []

    def test_unclassified_active_doc_is_reported(self, tmp_path: Path) -> None:
        self._build_repo(tmp_path)
        (tmp_path / "docs" / "STRAY.md").write_text("Phase 1 details here.", encoding="utf-8")
        result = audit_repo(tmp_path)
        assert result.ok is False
        assert any(r.path == "docs/STRAY.md" for r in result.unclassified)

    def test_currency_drift_in_active_doc_is_reported(self, tmp_path: Path) -> None:
        self._build_repo(tmp_path)
        # README.md is an allowlisted living doc; a stale currency claim there is drift.
        (tmp_path / "README.md").write_text("Status: Phase 3 in planning.\n", encoding="utf-8")
        result = audit_repo(tmp_path)
        assert result.ok is False
        assert any(r.path == "README.md" for r in result.currency_drift)

    def test_currency_claim_in_historical_doc_is_ignored(self, tmp_path: Path) -> None:
        self._build_repo(tmp_path)
        # A closed-history doc may legitimately contain old "in planning" language.
        (tmp_path / "docs" / "ROADMAP_PHASE1.md").write_text(
            "Back then, Phase 1 in planning.\n", encoding="utf-8"
        )
        result = audit_repo(tmp_path)
        assert result.currency_drift == []

    def test_counts_cover_every_reference(self, tmp_path: Path) -> None:
        self._build_repo(tmp_path)
        result = audit_repo(tmp_path)
        # Every reference is assigned exactly one category; counts sum to total.
        assert sum(result.counts().values()) == len(result.references)


class TestMain:
    def _clean(self, root: Path) -> None:
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "ROADMAP_PHASE1.md").write_text("Phase 1 record.", encoding="utf-8")

    def test_check_returns_zero_on_clean_repo(self, tmp_path: Path) -> None:
        self._clean(tmp_path)
        assert main(["--check", "--root", str(tmp_path)]) == 0

    def test_check_returns_one_on_drift(self, tmp_path: Path) -> None:
        self._clean(tmp_path)
        (tmp_path / "README.md").write_text("Phase 2 in planning.\n", encoding="utf-8")
        assert main(["--check", "--root", str(tmp_path)]) == 1

    def test_default_mode_returns_zero_even_with_drift(self, tmp_path: Path) -> None:
        # Discovery mode reports but does not fail.
        self._clean(tmp_path)
        (tmp_path / "README.md").write_text("Phase 2 in planning.\n", encoding="utf-8")
        assert main(["--root", str(tmp_path)]) == 0


class TestRealRepo:
    """Completion-condition smoke test: the live repo must audit clean."""

    def test_current_phase_is_six(self) -> None:
        assert CURRENT_PHASE == 6

    def test_repo_has_phase_references(self) -> None:
        result = audit_repo(REPO_ROOT)
        assert len(result.references) > 0

    def test_repo_audits_clean(self) -> None:
        result = audit_repo(REPO_ROOT)
        assert result.currency_drift == [], (
            "Living docs claim a non-current phase is active: "
            + ", ".join(f"{r.path}:{r.lineno}" for r in result.currency_drift)
        )
        assert result.unclassified == [], (
            "Phase-1 references in unrecognized active docs (classify or allowlist them): "
            + ", ".join(f"{r.path}:{r.lineno}" for r in result.unclassified)
        )
