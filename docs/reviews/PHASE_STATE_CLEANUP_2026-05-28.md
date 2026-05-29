# Phase-State Cleanup — 2026-05-28

> As of: commit `da546da` — 2026-05-28. Point-in-time review record (frozen). Branch: `phase-state-cleanup-2026-05-28`.

## Purpose

Reduce documentation drift around the project's **phase model**: make sure no
living doc implies a closed phase is the current one, route readers to the
active status surface, and put a repeatable guard in place so the drift can't
silently return. This was a documentation/state audit — **no runtime behavior
was in scope or changed.**

## Ground truth at audit time

The phase-closure ADR spine is the authority:

| Phase | State | Closed by |
|---|---|---|
| 1 | closed (2026-05-04) | [ADR 0023](../adr/0023-phase-1-is-closed-and-phase-2-may-open.md) |
| 2 | closed | [ADR 0025](../adr/0025-phase-2-is-closed-and-phase-3-may-open.md) |
| 3 | closed | [ADR 0027](../adr/0027-phase-3-is-closed-and-phase-4-may-open.md) |
| 4 | closed | [ADR 0031](../adr/0031-phase-4-is-closed-and-phase-5-may-open.md) |
| 5 | closed | [ADR 0038](../adr/0038-phase-5-is-closed-and-phase-6-may-open.md) |
| 6 | **current / in progress** | (active — [PHASE6_BENCH_PREP.md](../PHASE6_BENCH_PREP.md), [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md)) |

`docs/README.md` already classified the docs into living / frozen-snapshot /
closed-history sets correctly, and the `ROADMAP_PHASE1.md`, `PHASE2_PLANNING.md`,
and `PHASE3_PLANNING.md` bodies already carry strong closed-historical banners.
**The active-status entrypoint already exists** (`docs/README.md` as the map +
`PHASE6_BENCH_PREP.md` as active planning), so no `CURRENT_PROJECT_STATE.md` was
added — the goal's conditional for that file was not met.

## What the audit found

`scripts/audit_phase_state.py` scans every git-tracked text file for Phase-1
references and classifies each into a lifecycle bucket. At audit time: **714
Phase-1 references**, distributed as:

| Category | Count | Treatment |
|---|---|---|
| adr | 135 | immutable decision records — exempt |
| historical | 119 | closed-history / frozen-snapshot docs — exempt |
| scratch | 124 | `docs/superpowers/` plans + specs — exempt |
| evidence | 104 | `docs/reviews/` forensic write-ups — exempt |
| code | 101 | runtime identifiers / tests / config — out of scope, allowlisted by path |
| allowlisted | 131 | living docs whose Phase-1 mentions are reviewed scope qualifiers |
| unclassified | 0 | (none — every reference lands in a recognized bucket) |

The coverage check was clean on the first run — every reference already mapped
to a recognized bucket. The **currency check** flagged exactly **3 lines** in
living docs claiming a closed phase was active:

| File:line | Stale claim | Reality |
|---|---|---|
| `README.md:9` | "Phases 1–3 closed; Phase 4 in planning" | Phases 1–5 closed; Phase 6 in progress |
| `docs/VISION.md:330` | "Phases 2 and 3 closed; Phase 4 in planning" | Phases 2–5 closed; Phase 6 in progress |
| `docs/VISION.md:331` | "Phase 4 planning is underway" | Phase 4 closed; Phase 6 active |

`docs/PHASE6_BENCH_PREP.md:3` ("Phase 5 is closed; Phase 6 implementation …")
was checked and is **correct** — it claims the actual current phase, so it is
not drift.

## Fixes applied (5 files)

- **`README.md`** — rewrote the status line to the current phase and pointed it
  at `docs/README.md` (the doc map) + `PHASE6_BENCH_PREP.md` (active planning);
  added a `docs/README.md` "start here" row to the Documentation table; marked
  the `ROADMAP_PHASE1.md` table row as closed/historical; de-capped the ADR
  table row (was "(0001–0028)") to point at the ADR index; dropped two stale
  "Phase 1"-currency phrasings ("hard-blocked **in Phase 1**", CLI "**Phase 1**
  primary surface"). Also corrected the stale ADR count (28 → 54, "0001–0054")
  and **de-numberized** the volatile module/test counts ("77 source modules /
  70 test modules", "701 tests, ~70s") so they can't re-drift on every commit.
- **`docs/VISION.md`** — rewrote the "Phase 2+" roadmap entry to reflect
  Phases 2–5 closed / Phase 6 current, with accurate ADR references, and routed
  to the active planning doc + doc map.
- **`docs/README.md`** — corrected the stale ADR count (52 → 54).
- **`scripts/audit_phase_state.py`** *(new)* — the drift guard (below).
- **`tests/milodex/scripts/test_audit_phase_state.py`** *(new)* — tests for it.

## The ongoing guard

`scripts/audit_phase_state.py` is discovery-only (never writes). Two checks:

1. **Currency check** — flags any living doc claiming a phase ≠ `CURRENT_PHASE`
   is "in planning" / "underway" / "current". Phase-agnostic: bump
   `CURRENT_PHASE` when Phase 6 closes and it re-arms automatically. Exempts
   closed-history docs and ADRs (their era-language is a faithful record).
2. **Coverage check** — every Phase-1 reference must land in a known bucket; a
   mention in an unrecognized active doc is reported as `unclassified` for a
   human to triage (fix it, or add the doc to `_ALLOWLISTED_DOCS` /
   `_HISTORICAL_DOCS`).

Run: `python scripts/audit_phase_state.py` (report) or `--check` (exit 1 on
drift). New living docs are **not** auto-trusted — adding one with a Phase-1
mention fails the audit until classified, which is the point.

## Out-of-scope observations

- **Stale doc counts (now fixed in this PR):** the ADR counts (`README.md`
  28 → 54, `docs/README.md` 52 → 54) and the volatile module/test counts were
  found during the audit and folded in — ADR counts corrected, module/test
  counts de-numberized to stop them re-drifting. See *Fixes applied*.
- **Pre-existing ruff debt (spawned as a separate task):**
  `scripts/counterfactual_gate.py` (4× E501) and ~37 `tests/` files with
  `ruff format` drift. Unrelated files / different concern (code quality, not
  phase-state docs), so kept out of this surgical PR and queued on its own. Not
  introduced by this change; the documented gate `ruff check src tests` passes
  clean.

## Verification

| Check | Command | Result |
|---|---|---|
| Audit passes | `python scripts/audit_phase_state.py --check` | clean, exit 0 |
| Audit tests | `python -m pytest tests/milodex/scripts/test_audit_phase_state.py` | 36 passed |
| Lint (documented gate) | `python -m ruff check src tests` | All checks passed |
| Lint (new files) | `python -m ruff check scripts/audit_phase_state.py tests/.../test_audit_phase_state.py` | All checks passed |
| Full suite | `python -m pytest` | 2029 passed, 2 skipped, 4 xfailed; one live-Alpaca integration smoke test (`test_get_latest_bar`) hit a network `ReadTimeout`, passes on retry — environmental, unrelated |

**No runtime trading behavior changed.** Changes are confined to two Markdown
docs and a new discovery-only audit script + its tests. No `src/`, broker,
execution, risk, promotion, runner, database, or YAML-stage file was touched.
