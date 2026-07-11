"""M4 fault-injection drill harness — runner + evidence-report writer.

Usage
-----
    python scripts/drills/run_drills.py                 # run every cell
    python scripts/drills/run_drills.py --cell locked_db
    python scripts/drills/run_drills.py --report docs/drills/<date>-m4-drill-matrix.md
    python scripts/drills/run_drills.py --list

Each cell injects a REAL fault into a throwaway scratch environment and asserts
against the REAL operator surface (actual CLI output and/or actual event-store
rows). Exit code is nonzero if any cell FAILs or ERRORs.

Slow / network cells
--------------------
``locked_db`` holds the 30s SQLite ``busy_timeout`` and is standalone-only (the
pytest CI wrapper skips it). ``broker_outage`` and ``kill_switch_trip_reset``
make one outbound *unauthenticated, bogus-credential* Alpaca request and are
excluded from the offline CI wrapper. The harness itself always runs them.
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.drills.cells import CELL_REGISTRY, NETWORK_CELLS, SLOW_CELLS  # noqa: E402
from scripts.drills.harness import DrillResult  # noqa: E402


def run_cells(names: list[str]) -> list[DrillResult]:
    results: list[DrillResult] = []
    for name in names:
        fn = CELL_REGISTRY[name]
        print(f"[drill] running {name} ...", flush=True)
        try:
            result = fn()  # type: ignore[operator]
        except Exception:  # noqa: BLE001 — a cell crash is an ERROR verdict, not a harness crash
            result = DrillResult(
                name=name,
                status="ERROR",
                fault="(cell raised before producing a verdict)",
                detail=traceback.format_exc(),
            )
        results.append(result)
        print(f"[drill] {name}: {result.status}", flush=True)
    return results


def _render_report(results: list[DrillResult]) -> str:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    passed = sum(1 for r in results if r.status == "PASS")
    total = len(results)
    lines: list[str] = [
        "# M4 Fault-Injection Drill Matrix",
        "",
        f"Generated: {now}  ",
        "Harness: `scripts/drills/run_drills.py` (standalone, subprocess-driven).  ",
        f"Result: **{passed}/{total} cells PASS**.",
        "",
        "Each cell injects a real fault into a throwaway scratch environment "
        "(tempfile-backed data / logs / locks / cache; never the real state) and "
        "asserts against the real operator surface — actual "
        "`python -m milodex.cli.main` output and/or actual event-store rows. "
        "Unit-test coverage is deliberately not the pass criterion.",
        "",
        "## Verdicts",
        "",
        "| Cell | Verdict | Notes |",
        "| --- | --- | --- |",
    ]
    for r in results:
        tags = []
        if r.name in SLOW_CELLS:
            tags.append("slow ~30s")
        if r.name in NETWORK_CELLS:
            tags.append("outbound bogus-cred call")
        note = ", ".join(tags) or "—"
        lines.append(f"| `{r.name}` | **{r.status}** | {note} |")
    lines.append("")

    for r in results:
        lines.append(f"## `{r.name}` — {r.status}")
        lines.append("")
        lines.append(f"**Fault injected:** {r.fault}")
        lines.append("")
        lines.append("**Assertions:**")
        lines.append("")
        lines.append("```")
        lines.append(r.detail.rstrip() if r.detail else "(none)")
        lines.append("```")
        lines.append("")
        lines.append("**Operator-facing output (verbatim, trimmed):**")
        lines.append("")
        lines.append("```")
        lines.append(r.operator_output.rstrip() if r.operator_output else "(none captured)")
        lines.append("```")
        lines.append("")
        lines.append("**Durable record queried:**")
        lines.append("")
        lines.append("```")
        lines.append(r.durable_record.rstrip() if r.durable_record else "(none)")
        lines.append("```")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M4 fault-injection drill harness.")
    parser.add_argument(
        "--cell",
        action="append",
        choices=sorted(CELL_REGISTRY),
        help="Run only this cell (repeatable). Default: run all.",
    )
    parser.add_argument(
        "--report", type=Path, help="Write a markdown evidence report to this path."
    )
    parser.add_argument("--list", action="store_true", help="List cell names and exit.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list:
        for name in sorted(CELL_REGISTRY):
            tags = []
            if name in SLOW_CELLS:
                tags.append("slow")
            if name in NETWORK_CELLS:
                tags.append("network")
            suffix = f"  ({', '.join(tags)})" if tags else ""
            print(f"{name}{suffix}")
        return 0

    names = args.cell or list(CELL_REGISTRY)
    results = run_cells(names)

    print("\n=== Drill matrix results ===")
    for r in results:
        print(f"  {r.status:5}  {r.name}")
    passed = sum(1 for r in results if r.status == "PASS")
    total = len(results)
    print(f"=== {passed}/{total} cells PASS ===")

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(_render_report(results), encoding="utf-8")
        print(f"[drill] report written to {args.report}")

    return 0 if all(r.status == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
