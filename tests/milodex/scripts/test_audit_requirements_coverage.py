"""Tests for scripts/audit_requirements_coverage.py.

Covers the three public functions used by the traceability matrix:
- parse_srs       — extracts R-XX-NNN codes from docs/SRS.md
- scan_tests      — finds references in tests/
- generate_report — produces a Markdown file with sections A–E

Does NOT assert specific orphan counts; those change as the codebase evolves.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure the scripts/ directory is importable.
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from audit_requirements_coverage import generate_report, parse_srs, scan_tests  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
SRS_PATH = REPO_ROOT / "docs" / "SRS.md"
TESTS_DIR = REPO_ROOT / "tests"


class TestParseSrs:
    def test_extracts_at_least_one_code(self) -> None:
        """parse_srs returns a non-empty dict for the real SRS.md."""
        requirements = parse_srs(SRS_PATH)
        assert len(requirements) > 0, "Expected at least one R-XX-NNN code in SRS.md"

    def test_every_entry_has_section_and_text(self) -> None:
        """Each extracted code carries a section label and a text summary."""
        requirements = parse_srs(SRS_PATH)
        for code, meta in requirements.items():
            assert "section" in meta, f"{code} missing 'section'"
            assert isinstance(meta["section"], str) and meta["section"], f"{code} has blank section"
            assert "text" in meta, f"{code} missing 'text'"

    def test_known_code_present(self) -> None:
        """A well-known code (R-EXE-001) must be present and in Domain 3."""
        requirements = parse_srs(SRS_PATH)
        assert "R-EXE-001" in requirements
        assert "Domain 3" in requirements["R-EXE-001"]["section"]

    def test_code_format_valid(self) -> None:
        """All extracted codes match the R-XX-NNN / R-XXX-NNN pattern."""
        pattern = re.compile(r"^R-[A-Z]{2,3}-\d{3}$")
        requirements = parse_srs(SRS_PATH)
        for code in requirements:
            assert pattern.match(code), f"Unexpected code format: {code}"

    def test_back_reference_does_not_shadow_canonical_row(self) -> None:
        """R-BRK-001 is mentioned in the preamble but should resolve to Domain 1."""
        requirements = parse_srs(SRS_PATH)
        assert "R-BRK-001" in requirements
        assert "Domain 1" in requirements["R-BRK-001"]["section"]

    def test_synthetic_srs(self, tmp_path: Path) -> None:
        """parse_srs works correctly on a minimal synthetic SRS fragment."""
        fake_srs = tmp_path / "SRS.md"
        fake_srs.write_text(
            "## Domain 1 — Test Domain\n"
            "### System Requirements\n"
            "| R-TST-001 | The system shall do X. | Test: check X. |\n"
            "| R-TST-002 | The system shall do Y. | Test: check Y. |\n",
            encoding="utf-8",
        )
        requirements = parse_srs(fake_srs)
        assert set(requirements.keys()) == {"R-TST-001", "R-TST-002"}
        assert "Domain 1" in requirements["R-TST-001"]["section"]
        assert "do X" in requirements["R-TST-001"]["text"]


class TestScanTests:
    def test_finds_at_least_one_reference(self) -> None:
        """scan_tests returns at least one R-XX-NNN reference from the real tests/."""
        coverage = scan_tests(TESTS_DIR)
        assert len(coverage) > 0, "Expected at least one R-XX-NNN reference in tests/"

    def test_known_reference_found(self) -> None:
        """R-XC-016 is referenced in multiple test files; scan should find it."""
        coverage = scan_tests(TESTS_DIR)
        assert "R-XC-016" in coverage
        assert len(coverage["R-XC-016"]) >= 1

    def test_refs_have_expected_format(self) -> None:
        """All test references follow the path::function_name format."""
        coverage = scan_tests(TESTS_DIR)
        for code, refs in coverage.items():
            for ref in refs:
                assert "::" in ref, f"Reference for {code} missing '::': {ref!r}"

    def test_synthetic_tests_dir(self, tmp_path: Path) -> None:
        """scan_tests correctly extracts codes from docstrings and comments."""
        test_file = tmp_path / "test_sample.py"
        test_file.write_text(
            '"""Module covering R-TST-001."""\n'
            "\n"
            "def test_something():\n"
            '    """R-TST-002: verifies something."""\n'
            "    pass\n"
            "\n"
            "def test_other():\n"
            "    # R-TST-003 is handled here\n"
            "    pass\n",
            encoding="utf-8",
        )
        coverage = scan_tests(tmp_path)
        assert "R-TST-001" in coverage
        assert "R-TST-002" in coverage
        assert "R-TST-003" in coverage


class TestGenerateReport:
    @pytest.fixture()
    def minimal_inputs(
        self, tmp_path: Path
    ) -> tuple[dict[str, dict[str, str]], dict[str, list[str]], Path]:
        requirements = {
            "R-TST-001": {"section": "Domain 1 — Broker", "text": "Shall do X."},
            "R-TST-002": {"section": "Domain 1 — Broker", "text": "Shall do Y."},
            "R-TST-003": {"section": "Domain 2 — Data", "text": "Shall do Z."},
        }
        coverage: dict[str, list[str]] = {
            "R-TST-001": ["tests/test_foo.py::test_x"],
            # R-TST-002 has no coverage — orphan
            # R-TST-099 not in requirements — floater
            "R-TST-099": ["tests/test_foo.py::test_unknown"],
        }
        output = tmp_path / "COVERAGE.md"
        return requirements, coverage, output

    def test_produces_markdown_file(self, minimal_inputs: tuple) -> None:
        requirements, coverage, output = minimal_inputs
        generate_report(requirements, coverage, output)
        assert output.exists()

    def test_contains_all_five_sections(self, minimal_inputs: tuple) -> None:
        """The report must contain sections A through E."""
        requirements, coverage, output = minimal_inputs
        generate_report(requirements, coverage, output)
        text = output.read_text(encoding="utf-8")
        for section in ("## A —", "## B —", "## C —", "## D —", "## E —"):
            assert section in text, f"Missing section marker: {section!r}"

    def test_orphan_appears_in_section_c(self, minimal_inputs: tuple) -> None:
        requirements, coverage, output = minimal_inputs
        generate_report(requirements, coverage, output)
        text = output.read_text(encoding="utf-8")
        # R-TST-002 has no coverage entry.
        assert "R-TST-002" in text

    def test_floater_appears_in_section_d(self, minimal_inputs: tuple) -> None:
        requirements, coverage, output = minimal_inputs
        generate_report(requirements, coverage, output)
        text = output.read_text(encoding="utf-8")
        # R-TST-099 is cited by a test but not in requirements.
        assert "R-TST-099" in text

    def test_summary_counts_consistent(self, minimal_inputs: tuple) -> None:
        requirements, coverage, output = minimal_inputs
        stats = generate_report(requirements, coverage, output)
        assert stats["total_reqs"] == 3
        # R-TST-002 and R-TST-003 have no coverage entries → 2 orphans.
        assert stats["orphan_count"] == 2
        assert stats["floater_count"] == 1  # R-TST-099 ref

    def test_real_srs_and_tests_produces_valid_report(self, tmp_path: Path) -> None:
        """End-to-end: parse real SRS + scan real tests → valid Markdown output."""
        output = tmp_path / "COVERAGE.md"
        requirements = parse_srs(SRS_PATH)
        coverage = scan_tests(TESTS_DIR)
        stats = generate_report(requirements, coverage, output)

        text = output.read_text(encoding="utf-8")
        for section in ("## A —", "## B —", "## C —", "## D —", "## E —"):
            assert section in text

        assert stats["total_reqs"] > 0
        assert 0 <= stats["tested_reqs"] <= stats["total_reqs"]
        assert stats["orphan_count"] >= 0
