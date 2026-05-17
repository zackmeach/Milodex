"""Requirement-to-test traceability matrix for Milodex.

Parses R-XX-NNN codes from docs/SRS.md, scans tests/ for references, and
generates docs/REQUIREMENTS_COVERAGE.md with orphan + floater flagging.

Usage:
    python scripts/audit_requirements_coverage.py
    python scripts/audit_requirements_coverage.py --check   # exit 1 if orphans exist

Discovery-only: does not modify SRS.md, does not add tests.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRS_PATH = REPO_ROOT / "docs" / "SRS.md"
TESTS_DIR = REPO_ROOT / "tests"
OUTPUT_PATH = REPO_ROOT / "docs" / "REQUIREMENTS_COVERAGE.md"

# Pattern that matches R-XX-NNN or R-XXX-NNN (two or three uppercase letters).
REQ_CODE_RE = re.compile(r"R-[A-Z]{2,3}-\d{3}")

# Pattern that identifies a markdown section heading (## or ###).
HEADING_RE = re.compile(r"^#{1,6}\s+(.+)")


# ---------------------------------------------------------------------------
# SRS parsing
# ---------------------------------------------------------------------------


def _req_text_from_table_row(line: str) -> str:
    """Extract a one-line summary from a markdown table row containing a req code."""
    # Table rows look like: | R-BRK-001 | The system **shall** ...  | Test: ... |
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    # First cell is the code; second cell is the requirement text if present.
    if len(cells) >= 2:
        text = cells[1]
        # Strip markdown bold markers for readability.
        text = re.sub(r"\*{1,2}(.*?)\*{1,2}", r"\1", text)
        return text[:120].strip()
    return ""


def parse_srs(srs_path: Path) -> dict[str, dict[str, str]]:
    """Return {code: {"section": ..., "text": ...}} for every R-XX-NNN in SRS.md."""
    if not srs_path.exists():
        raise FileNotFoundError(f"SRS file not found: {srs_path}")

    requirements: dict[str, dict[str, str]] = {}
    # Internal scratch state: tracks whether the canonical entry came from a table row.
    _is_table_row: dict[str, bool] = {}

    # Track the ## (top-level domain) heading separately from the ### sub-heading
    # so that "### System Requirements" doesn't obscure "## Domain 1 — Broker …".
    current_domain = "Preamble"
    current_section = "Preamble"

    with srs_path.open(encoding="utf-8") as fh:
        for line in fh:
            heading_match = HEADING_RE.match(line)
            if heading_match:
                level = len(line) - len(line.lstrip("#"))
                title = heading_match.group(1).strip()
                if level <= 2:
                    # Top-level or second-level heading sets the domain context.
                    current_domain = title
                    current_section = title
                else:
                    # Sub-headings (###, ####, …) are qualified by the domain.
                    current_section = f"{current_domain} / {title}"
                continue

            codes = REQ_CODE_RE.findall(line)
            if not codes:
                continue

            # Determine requirement text: prefer table-row format.
            req_text = ""
            if "|" in line:
                req_text = _req_text_from_table_row(line)
            else:
                # Inline mention — capture rest of the sentence (up to 120 chars).
                first_code = codes[0]
                idx = line.find(first_code)
                snippet = line[idx:].strip()
                snippet = re.sub(r"\*{1,2}(.*?)\*{1,2}", r"\1", snippet)
                req_text = snippet[:120].strip()

            row = "|" in line
            for code in codes:
                if code not in requirements:
                    requirements[code] = {"section": current_section, "text": req_text}
                    _is_table_row[code] = row
                elif row and not _is_table_row.get(code):
                    # Upgrade from an inline/back-reference to the canonical table row.
                    requirements[code] = {"section": current_section, "text": req_text}
                    _is_table_row[code] = True

    return requirements


# ---------------------------------------------------------------------------
# Test scanning
# ---------------------------------------------------------------------------


def _extract_function_name(line: str) -> str | None:
    """Return the function name if the line is a `def test_...` declaration."""
    m = re.match(r"^\s*(?:async\s+)?def\s+(test_\w+)", line)
    return m.group(1) if m else None


def _extract_codes_from_string(text: str) -> list[str]:
    return REQ_CODE_RE.findall(text)


def _scan_file_ast(path: Path, base_dir: Path | None = None) -> list[tuple[str, list[str]]]:
    """Return [(test_ref, [codes])] from docstrings and comments via AST + line scan.

    test_ref format: relative/path/to/file.py::function_name
                  or relative/path/to/file.py::module
    """
    base = base_dir if base_dir is not None else REPO_ROOT
    relative = path.relative_to(base)
    results: list[tuple[str, list[str]]] = []

    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return results

    lines = source.splitlines()

    # ---- Pass 1: AST — extract docstrings from test functions and the module. ----
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        tree = None

    docstring_codes: dict[str, list[str]] = {}

    if tree is not None:
        # Module-level docstring.
        module_doc = ast.get_docstring(tree)
        if module_doc:
            codes = _extract_codes_from_string(module_doc)
            if codes:
                docstring_codes["module"] = codes

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    doc = ast.get_docstring(node)
                    if doc:
                        codes = _extract_codes_from_string(doc)
                        if codes:
                            docstring_codes[node.name] = codes

    # ---- Pass 2: line scan — function-name encoding + tokenize for true comments. ----
    current_func: str | None = None
    comment_codes: dict[str, list[str]] = defaultdict(list)

    # Sub-pass A: function-name encoding (line scan only, not comment detection).
    for line in lines:
        func_name = _extract_function_name(line)
        if func_name:
            current_func = func_name
            # Function-name encoding: test_R_EXE_001_... → R-EXE-001
            name_codes = _extract_codes_from_string(func_name.replace("_", "-"))
            if name_codes:
                comment_codes[current_func].extend(name_codes)

    # Sub-pass B: tokenize to identify true comment tokens (avoids string-literal FPs).
    current_func = None
    try:
        token_gen = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok_type, tok_string, tok_start, _tok_end, _line_text in token_gen:
            # Track which function we're inside by re-scanning function defs.
            line_no = tok_start[0]
            raw_line = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
            fn = _extract_function_name(raw_line)
            if fn:
                current_func = fn
            if tok_type == tokenize.COMMENT:
                codes = _extract_codes_from_string(tok_string)
                if codes:
                    context = current_func if current_func else "module"
                    comment_codes[context].extend(codes)
    except tokenize.TokenError:
        pass  # Incomplete source — best effort.

    # ---- Merge: docstrings take priority; comments supplement. ----
    # Use dict.fromkeys to preserve insertion order while deduplicating contexts.
    all_contexts = dict.fromkeys(list(docstring_codes) + list(comment_codes))
    for context in all_contexts:
        merged = list(
            dict.fromkeys(docstring_codes.get(context, []) + comment_codes.get(context, []))
        )
        if merged:
            ref = f"{relative!s}::{context}"
            results.append((ref, merged))

    return results


def scan_tests(tests_dir: Path) -> dict[str, list[str]]:
    """Return {req_code: [test_refs]} from all .py files under tests_dir."""
    coverage: dict[str, list[str]] = defaultdict(list)

    for py_file in sorted(tests_dir.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        for test_ref, codes in _scan_file_ast(py_file, base_dir=tests_dir):
            for code in codes:
                if test_ref not in coverage[code]:
                    coverage[code].append(test_ref)

    return {code: sorted(refs) for code, refs in coverage.items()}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

_SECTION_ORDER = [
    "Domain 1",
    "Domain 2",
    "Domain 3",
    "Domain 4",
    "Domain 5",
    "Domain 6",
    "Domain 7",
    "Domain 8",
    "Domain 9",
    "Cross-Cutting",
    "Preamble",
    "Key Terms",
]


def _section_sort_key(section: str) -> tuple[int, str]:
    for i, prefix in enumerate(_SECTION_ORDER):
        if section.startswith(prefix):
            return (i, section)
    return (len(_SECTION_ORDER), section)


def _req_sort_key(item: tuple[str, dict[str, str]]) -> tuple[tuple[int, str], str]:
    code, meta = item
    return (_section_sort_key(meta["section"]), code)


def _git_short_sha() -> str:
    """Return the current HEAD short SHA, or 'unknown' if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def generate_report(
    requirements: dict[str, dict[str, str]],
    coverage: dict[str, list[str]],
    output_path: Path,
) -> dict[str, object]:
    """Write the Markdown report and return summary stats."""
    total_reqs = len(requirements)
    tested_reqs = sum(1 for code in requirements if coverage.get(code))
    orphan_codes = sorted(
        [code for code in requirements if not coverage.get(code)],
        key=lambda c: (_section_sort_key(requirements[c]["section"]), c),
    )
    floater_refs: list[str] = []
    for code, refs in sorted(coverage.items()):
        if code not in requirements:
            floater_refs.extend(refs)
    # Deduplicate floater refs while preserving order.
    seen: set[str] = set()
    unique_floaters: list[str] = []
    for ref in floater_refs:
        if ref not in seen:
            seen.add(ref)
            unique_floaters.append(ref)

    pct = (tested_reqs / total_reqs * 100) if total_reqs else 0.0

    # Collect all test refs that mention *any* code (for total-test count).
    all_test_refs: set[str] = set()
    for refs in coverage.values():
        all_test_refs.update(refs)
    total_tests_with_refs = len(all_test_refs)

    sha = _git_short_sha()
    as_of_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    lines: list[str] = []

    # ---- Header ----
    lines += [
        "# Requirements Coverage Matrix",
        "",
        f"> As of: commit `{sha}` — {as_of_date}",
        "> Generated by `scripts/audit_requirements_coverage.py`. Do not edit by hand.",
        "> Re-run the script to refresh.",
        "",
    ]

    # ---- Section A: Summary ----
    lines += [
        "## A — Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total requirements (SRS.md) | {total_reqs} |",
        f"| Test references found | {total_tests_with_refs} |",
        f"| Requirements with ≥1 test | {tested_reqs} |",
        f"| **Orphans** (no tests) | **{len(orphan_codes)}** |",
        f"| Floater test refs | {len(unique_floaters)} |",
        f"| Coverage | {pct:.1f}% |",
        "",
    ]

    # ---- Section B: Coverage matrix ----
    lines += [
        "## B — Coverage Matrix",
        "",
        "| Requirement | Section | Tests | Test references |",
        "|-------------|---------|-------|-----------------|",
    ]
    for code, meta in sorted(requirements.items(), key=_req_sort_key):
        refs = coverage.get(code, [])
        count = len(refs)
        refs_cell = "<br>".join(f"`{r}`" for r in refs) if refs else "—"
        sec = meta["section"]
        short_section = sec.split(" — ")[0] if " — " in sec else sec
        lines.append(f"| `{code}` | {short_section} | {count} | {refs_cell} |")
    lines.append("")

    # ---- Section C: Orphans ----
    lines += [
        "## C — Orphans (requirements with no tests)",
        "",
    ]
    if orphan_codes:
        lines += [
            "| Requirement | Section | Summary |",
            "|-------------|---------|---------|",
        ]
        for code in orphan_codes:
            meta = requirements[code]
            sec = meta["section"]
            short_section = sec.split(" — ")[0] if " — " in sec else sec
            text = meta["text"].replace("|", "\\|")
            lines.append(f"| `{code}` | {short_section} | {text} |")
    else:
        lines.append("_No orphans — every requirement has at least one test reference._")
    lines.append("")

    # ---- Section D: Floaters ----
    lines += [
        "## D — Floaters (test refs citing unknown requirement codes)",
        "",
    ]
    if unique_floaters:
        lines += [
            "| Test reference | Unknown code(s) cited |",
            "|---------------|-----------------------|",
        ]
        # Build reverse map: ref → unknown codes it mentions.
        ref_to_unknowns: dict[str, list[str]] = defaultdict(list)
        for code, refs in coverage.items():
            if code not in requirements:
                for ref in refs:
                    ref_to_unknowns[ref].append(code)
        for ref in unique_floaters:
            codes_str = ", ".join(f"`{c}`" for c in sorted(set(ref_to_unknowns[ref])))
            lines.append(f"| `{ref}` | {codes_str} |")
    else:
        lines.append("_No floaters — all test-cited codes exist in SRS.md._")
    lines.append("")

    # ---- Section E: Methodology ----
    lines += [
        "## E — Methodology",
        "",
        "### Requirement extraction",
        "",
        "The script reads `docs/SRS.md` line by line, tracking the current `##`/`###` heading",
        "as the section label. Any line matching `R-[A-Z]{2,3}-\\d{3}` yields one or more",
        "requirement codes. For table rows (`|`-separated), the second cell is taken as the",
        "one-line summary; for inline mentions the text following the first code (up to 120",
        "characters) is used. First occurrence wins — later back-references to a code do not",
        "overwrite its canonical section or summary.",
        "",
        "### Test reference extraction",
        "",
        "Each `.py` file under `tests/` is processed in two passes:",
        "",
        "1. **AST pass** — module-level docstrings and test-function docstrings are parsed",
        "   via `ast.get_docstring()`. Any `R-XX-NNN` pattern found there is attributed to",
        "   that test function (or `module` for module-level docstrings).",
        "2. **Line-scan pass** — every inline comment (`#`…) is scanned for `R-XX-NNN`",
        "   patterns and attributed to the enclosing `def test_*` function (or `module` if",
        "   no function is active yet). Function names are also checked after converting",
        "   underscores to hyphens (e.g. `test_R_EXE_001_foo` → `R-EXE-001`).",
        "",
        "Results are de-duplicated per (code, test_ref) pair. Test references are reported",
        "as `relative/path/to/file.py::function_name`.",
        "",
        "### Orphan and floater definitions",
        "",
        "- **Orphan:** a requirement code present in SRS.md with zero test references.",
        "- **Floater:** a test reference that cites a code not present in SRS.md (potential",
        "  typo, renamed requirement, or stale comment).",
        "",
        "### Limitations",
        "",
        "- References inside multi-line strings that are not function docstrings (e.g.",
        "  `pytest.mark.parametrize` arguments, assertion messages) may be missed by the",
        "  AST pass but captured by the line-scan pass if the pattern appears in a comment.",
        "- The script counts *references*, not *meaningful test coverage*. A passing mention",
        "  of a code in a comment does not prove the test validates that requirement.",
        "- Run the script after any SRS.md edit or new test file to keep the report fresh.",
        "",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "total_reqs": total_reqs,
        "tested_reqs": tested_reqs,
        "orphan_count": len(orphan_codes),
        "floater_count": len(unique_floaters),
        "pct": pct,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate requirements-to-test traceability matrix for Milodex."
    )
    p.add_argument(
        "--check",
        action="store_true",
        default=False,
        help="Exit non-zero if any orphan requirements are found (for CI integration).",
    )
    p.add_argument(
        "--srs",
        type=Path,
        default=SRS_PATH,
        help=f"Path to SRS.md (default: {SRS_PATH}).",
    )
    p.add_argument(
        "--tests-dir",
        type=Path,
        default=TESTS_DIR,
        help=f"Root of the tests directory to scan (default: {TESTS_DIR}).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_PATH,
        help=f"Output path for the Markdown report (default: {OUTPUT_PATH}).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    requirements = parse_srs(args.srs)
    coverage = scan_tests(args.tests_dir)
    stats = generate_report(requirements, coverage, args.output)

    print(
        f"Requirements: {stats['total_reqs']}  "
        f"Tested: {stats['tested_reqs']}  "
        f"Orphans: {stats['orphan_count']}  "
        f"Floaters: {stats['floater_count']}  "
        f"Coverage: {stats['pct']:.1f}%"
    )
    print(f"Report written to {args.output}")

    if args.check and stats["orphan_count"] > 0:
        print(f"FAIL: {stats['orphan_count']} orphan(s) found.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
