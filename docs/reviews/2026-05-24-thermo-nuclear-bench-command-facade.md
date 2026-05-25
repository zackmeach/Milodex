# Thermo-Nuclear Code Quality Review: Bench Command Facade

**Date:** 2026-05-24  
**Scope:** `src/milodex/commands/bench.py` — `BenchCommandFacade`, promotion/governance choreography, workflow readiness, runner audit lifecycle, and adjacent callers (`bench_command_bridge.py`, `cli/commands/promotion.py`, `promotion/orchestrator.py`).  
**Method:** Read-only audit per the thermo-nuclear review skill (structural regressions first, code-judo opportunities second).  
**Verdict:** **Do not treat this module as “done.”** Behavior is largely correct and several recent consolidations (RM-010 orchestrator, workflow-readiness seam, runner audit linkage) are real wins — but the facade has crossed a maintainability cliff. The next work should **delete repeated choreography**, not add another action family inline.

---

## Executive summary

| Metric | Value | Skill threshold |
|--------|------:|-----------------|
| `bench.py` total lines | 2,423 | — |
| `BenchCommandFacade` class lines | 1,996 (356–2351) | **>1,000 = presumptive blocker** |
| Public methods on facade | 29 | — |
| Approx. branch keywords in class | ~145 | User cited ~115 |
| Largest methods | `submit_backtest` 188, `submit_promote_to_paper` / `propose_promote_to_paper` 167 each | — |
| Companion test file | `test_bench_facade.py` 2,225 lines | Mirrors the sprawl |

The deepening audit ([`docs/architecture/audits/2026-05-21-deepening-audit.md`](../architecture/audits/2026-05-21-deepening-audit.md) AUDIT-001/002) correctly flagged this area. Partial remediation landed (`prepare_and_record_promotion`, workflow readiness, stricter runner `audit_event_id`). **The facade file itself did not shrink** — complexity was centralized in promotion for one path while the class kept growing for six action families × (propose + submit) × (revalidation + orchestration jobs + durable refs).

---

## Approval bar (thermo-nuclear)

| Criterion | Status |
|-----------|--------|
| No structural regression | **Fail** — 2k-line god class |
| No missed dramatic simplification | **Fail** — obvious generic submit/propose scaffolding |
| No unjustified file-size explosion | **Fail** — ~2× over 1k-line rule |
| No spaghetti special-case growth | **Warn** — GUI-only demote branch, idle→backtest in backtest submit |
| No hacky/magical abstraction | **Pass** — mostly explicit; orchestration job swallowing exceptions is a smell |
| Boundary / type cleanliness | **Warn** — `Any` on factories/results, private API peek |
| Canonical-layer reuse | **Warn** — duplicate config resolution vs `resolve_strategy_config_path` |
| Obvious decomposition opportunity | **Fail** — not decomposed |

**Recommendation:** Block further feature growth in `bench.py` until a decomposition PR lands. New action families should not be copy-pasted as the seventh `(propose_*, submit_*)` pair.

---

## 1. Structural regressions (presumptive blockers)

### 1.1 God class past the 1k-line boundary

`BenchCommandFacade` is **1,996 lines** in a single class. That is not “large but organized”; it is a **merge conflict magnet** and a cognitive-load ceiling. ADR 0051 intended one module, not one class containing every lifecycle command for the GUI.

**Required remedy:** Decompose before adding code. A pragmatic split that preserves ADR 0051’s single import surface:

| New module | Owns |
|------------|------|
| `commands/bench_types.py` | `Blocker`, `Precondition`, `CommandProposal`, `CommandResult`, `WorkflowReadiness*`, action-family constants |
| `commands/bench_readiness.py` | `_DefaultWorkflowReadiness` (today ~90 lines at top of file) |
| `commands/bench_propose.py` | Six `propose_*` builders (or one registry keyed by `action_family`) |
| `commands/bench_submit.py` | Six `submit_*` dispatchers + shared revalidation shell |
| `commands/bench.py` | Thin `BenchCommandFacade` delegating to the above; re-export types for bridge/tests |

Keep `from milodex.commands.bench import BenchCommandFacade` stable via `__init__.py` — no caller churn.

### 1.2 Test file mirrors the anti-pattern

`tests/milodex/commands/test_bench_facade.py` at **2,225 lines** will not stay green through refactors unless tests are grouped by action family or by layer (propose vs submit vs readiness). Decomposition PR should **move tests with modules**, not leave one mega-fixture file.

---

## 2. Missed code-judo moves (high conviction)

### 2.1 Generic submit shell (delete ~300+ lines)

Every `submit_*` repeats the same spine:

1. `if proposal.action_family != EXPECTED` → error `CommandResult` (~25 lines × 6)
2. `_require_event_store` where needed
3. `revalidation = self.propose_*…` + `if not revalidation.admissible` → blocked
4. Optional second `_resolve_config`
5. Dispatch to governance callee / engine / runner control
6. Map success to `CommandResult` + durable refs

**Counts in class body today:** `if proposal.action_family !=` × **6**, `revalidation = self.propose_` × **6**, `if not revalidation.admissible` × **6**, `config, resolve_blocker = self._resolve_config` × **9**.

Introduce one internal helper, e.g.:

```python
def _submit(
    self,
    proposal: CommandProposal,
    *,
    expected: str,
    revalidate: Callable[[], CommandProposal],
    execute: Callable[[CommandProposal, StrategyConfig], CommandResult],
    needs_event_store: bool = True,
) -> CommandResult: ...
```

Freeze and demote become **thin `execute` lambdas** (~15 lines each). Backtest and runner submits keep custom bodies but shed the boilerplate.

This is the single highest-leverage judo move: **fewer concepts for readers**, not just shorter files.

### 2.2 Generic propose shell (delete ~200+ lines)

Each `propose_*` opens with the same block:

```python
config, resolve_blocker = self._resolve_config(strategy_id)
if resolve_blocker is not None:
    return self._blocked_proposal(...)
preconditions = [Precondition("strategy_exists", passed=True, ...)]
blockers = []
# ... action-specific validation ...
return CommandProposal(...)
```

A `_propose(action_family, strategy_id, inputs, validate)` that accepts a pure `validate(config) -> tuple[list[Precondition], list[Blocker], dict projected]` would collapse six copies of the resolve-or-block preamble.

### 2.3 Unify strategy config resolution

Bench uses `_resolve_config`: glob `config_dir/*.yaml`, skip parse errors, match `strategy_id`.

CLI promotion uses `promotion.manifest.resolve_strategy_config_path`.

Both answer “which YAML is this strategy?” **Two algorithms will drift** (ordering, error messages, multi-file edge cases).

**Remedy:** Facade should call the canonical `resolve_strategy_config_path` + `load_strategy_config`, translating `FileNotFoundError` / `ValueError` into `Blocker(reason_code="strategy_not_found", …)`. Delete `_resolve_config`’s glob loop unless there is a documented reason Bench must differ.

### 2.4 Promotion propose vs orchestrator — split “preview” from “legality”

`submit_promote_to_paper` correctly delegates choreography to `prepare_and_record_promotion` (RM-010). That was the right consolidation.

`propose_promote_to_paper` still re-implements a **parallel rule set**:

- Stage must be `backtest` (orchestrator also calls `validate_stage_transition`)
- Evidence text gates (orchestrator’s caller contract; CLI uses `_require_evidence_inputs`)
- `run_id` presence (orchestrator resolves metrics and gate at submit)

**Gap:** An admissible proposal can still fail at submit on **gate metrics** — the preview never calls `check_gate` or `metrics_from_run`. That may be intentional (needs live event store), but it means the facade owns **two promotion models**: preview heuristics + orchestrator truth.

**Judo options (pick one):**

1. **Thin preview:** Propose only checks operator-input completeness + workflow readiness; document that statistical gate is submit-only. Shrink `propose_promote_to_paper` materially.
2. **Shared preview service in `promotion/`:** e.g. `preview_paper_promotion(strategy_id, run_id, …) -> list[Blocker]` used by CLI dry-run and Bench propose — **one** place for stage + evidence + gate preview.

Option 2 aligns with AUDIT-001’s “deeper promotion seam” without putting gate logic back in the facade.

### 2.5 Extract backtest submit orchestration

`submit_backtest` (~188 lines) mixes:

- Input parsing and revalidation
- Orchestration batch/job journaling
- Walk-forward vs single engine dispatch
- **Side effect:** idle → backtest YAML update + `PromotionEvent` append
- Durable ref / payload shaping

The idle→backtest transition is **promotion choreography embedded in a backtest command**. That is exactly the “repeated choreography instead of a service boundary” smell the audit describes.

**Remedy:** `promotion` or `commands/bench_backtest.py` function `record_bench_backtest_completion(...)` owning stage transition policy. Facade calls it after a successful run when snapshot stage is `idle`.

### 2.6 Runner session correlation belongs on the runner seam

`submit_start_paper_runner` / `submit_stop_paper_runner` use `_latest_open_session_id`, which scans **all** `strategy_runs` and takes the last open row. RM-003a fixed “submitted with null audit id” but the mechanism is still **best-effort and racy** under concurrent runs.

**Remedy (AUDIT-002):** `paper_runner_control.start()` should return `session_id` (or the control layer creates the row before returning). Facade should not scrape the event store to guess correlation. Deleting `_latest_open_session_id` from the facade is a judo win.

### 2.7 Table-driven workflow-readiness matrix

Readiness is already conceptually a matrix (documented in RM-003b). Code spreads **per-method** `frozenset({...})` calls across four propose methods.

Replace with one dict:

```python
_READINESS_MATRIX: dict[str, tuple[frozenset[str], frozenset[str]]]  # required, inspected
```

Propose methods call `_evaluate_workflow_readiness(action_family=ACTION_FAMILY_*)` without repeating set literals. Adding a seventh action family becomes one matrix row, not four copy-pasted blocks.

---

## 3. Spaghetti / special-case branching

### 3.1 `gui_submit` demote guard

`propose_demote` / `submit_demote(..., gui_submit=True)` embed product policy (“disabled target not GUI-ready”) in the facade. The comment references `promotion.state_machine` slice 3 — **temporary branching likely to ossify**.

**Prefer:** Gate in `bench_v1.compute_menu_items` (already hides actions) *and* a single governance refusal in `demote` when runtime policy lands — not a parallel `gui_submit` flag on the facade API. CLI should not need to know about GUI readiness flags.

### 3.2 Mirrored private constant `_FROZEN_STAGES`

```2354:2361:src/milodex/commands/bench.py
# Mirror milodex.promotion.manifest._FROZEN_STAGES locally so the facade has
# no dependency on a private name. Stages where freezing is meaningful.
_FROZEN_STAGES: frozenset[str] = frozenset({"paper", "micro_live", "live"})
```

Duplicating a private set is **drift bait**. Export a public `FROZEN_STAGES` from `promotion.manifest` (one line) or add `is_stage_freezable(stage) -> bool` on the governance module. The facade should ask a question, not mirror data.

### 3.3 Dead legacy: `_phase_b_blocked`

`_phase_b_blocked` is **defined but never called**. Module docstring and `_NOT_SUBMIT_CAPABLE_PHASE_B` still describe Phase B as if it were current. This is doc/code drift that confuses reviewers.

**Remedy:** Delete method + constants if truly unused, or wire explicitly only in tests documenting historical behavior.

### 3.4 Orchestration job failures are silent

`_create_orchestration_job` and `_finish_orchestration_job` catch broad `Exception`, log, and continue. Submit still succeeds while audit journaling may be missing.

That is a **magical partial-success** mode: operators see “submitted” without guaranteed orchestration linkage. Prefer: fail the submit with `orchestration_journal_failed` when journaling is required, or make journaling mandatory before dispatch (atomic intent).

---

## 4. Boundary / abstraction / type-contract issues

### 4.1 ADR 0051 “owns no business rules” vs reality

ADR 0051 §4: *“Owns no business rules of its own: every decision is delegated…”*

The facade **does** own Bench-specific rules:

- Paper-only runner enforcement
- GUI demote target policy
- Workflow-readiness interpretation (via injectable seam, but default impl lives in-file)
- Preview-stage validation duplicated from CLI/governance
- Backtest idle→backtest side effect

**Clarify the ADR** or **move rules** into `promotion`, `strategies`, or `commands/policies.py`. The facade should orchestrate and translate shapes (`PromoteBlocked` → `Blocker`), not accumulate domain policy.

### 4.2 Dual `CommandResult` types

`milodex.commands.bench.CommandResult` ≠ `milodex.cli.formatter.CommandResult`. Name collision is manageable but `_promote_blocked_result` in CLI vs `_blockers_from_promote_blocked` in bench are **parallel presentation adapters** for the same orchestrator outcomes.

Consider `promotion.present.blocked_for_cli(...)` / `blocked_for_bench(...)` or one mapper in `promotion.orchestrator` with `surface: Literal["cli","bench"]` if duplication grows.

### 4.3 `Any` and getattr-heavy result mapping

`_backtest_durable_refs`, `_backtest_result_data`, runner submits use `getattr(result, …)` on untyped results. Works with fakes; obscures the contract.

**Remedy:** Protocol or typed result objects from `backtesting` / `paper_runner_control` so mapping code is exhaustive and mypy-friendly.

### 4.4 Private API: `AdvisoryLock._read_holder`

```2285:2290:src/milodex/commands/bench.py
        lock = AdvisoryLock(
            runner_lock_name(strategy_id),
            locks_dir=self._locks_dir,
            holder_name="bench.facade.peek",
        )
        holder = lock._read_holder()  # noqa: SLF001 — single permitted peek
```

A `# noqa: SLF001` “single permitted peek” in a 2k-line class is a **boundary leak**. Public `peek_holder()` on `AdvisoryLock` (or reuse an existing peek helper from runner control) keeps the facade honest.

---

## 5. What is already working (do not regress)

These choices are **worth preserving** through refactors:

| Pattern | Why it’s good |
|---------|----------------|
| `prepare_and_record_promotion` for submit promote | CLI/Bench cannot drift on gate/evidence (RM-010) |
| Thin `submit_freeze_manifest` / `submit_demote` around governance callees | Correct seam — generalize, don’t inline more |
| Stale-proposal revalidation at submit | OPERATIONS audit trail discipline |
| Injectable `workflow_readiness` + fail-closed default | Testable; bridge can swap real adapter later |
| `BenchCommandBridge` stays thin (~536 lines) | ADR-compliant; repetition is mostly mechanical slots |
| Structured `Blocker` / `CommandProposal` contract | GUI and tests pin stable `reason_code`s |

---

## 6. Suggested refactor sequence (measure twice)

Ordered to minimize risk and maximize deleted lines:

1. **Extract types + readiness** to sibling modules (no behavior change).
2. **Introduce `_submit` / `_propose` shells**; migrate freeze, demote, promote submit first (lowest risk).
3. **Switch config resolution** to `resolve_strategy_config_path`.
4. **Extract `submit_backtest` body** + idle→backtest policy function.
5. **Runner control returns `session_id`**; delete `_latest_open_session_id`.
6. **Promotion preview service** (optional but aligns AUDIT-001).
7. **Split tests** by module; add parity tests CLI vs Bench for paper gate if not already present.

Each step should **reduce** `bench.py` line count; if a PR adds features without shrinking or splitting, treat it as a quality regression.

---

## 7. Findings checklist (for PR authors)

| ID | Severity | Finding | Action |
|----|----------|---------|--------|
| F-01 | **Blocker** | `BenchCommandFacade` ~2k lines | Decompose per §1.1 before new features |
| F-02 | **Blocker** | Sixfold submit/propose boilerplate | Generic shells §2.1–2.2 |
| F-03 | High | Duplicate config resolution | Use `resolve_strategy_config_path` §2.3 |
| F-04 | High | Backtest embeds promotion transition | Extract policy §2.5 |
| F-05 | High | Session correlation by store scan | Runner seam returns `session_id` §2.6 |
| F-06 | Medium | `propose_promote` parallel rule set | Shared preview or thin preview §2.4 |
| F-07 | Medium | `gui_submit` demote special case | Move to menu/governance §3.1 |
| F-08 | Medium | Mirrored `_FROZEN_STAGES` | Public API on manifest §3.2 |
| F-09 | Low | Dead `_phase_b_blocked` + stale Phase B docs | Delete/clarify §3.3 |
| F-10 | Low | Silent orchestration journal failures | Fail closed or document §3.4 |
| F-11 | Low | `AdvisoryLock._read_holder` SLF001 | Public peek API §4.4 |

---

## 8. References

- ADR 0051 — Bench command infrastructure: [`docs/adr/0051-bench-command-infrastructure-v1.md`](../adr/0051-bench-command-infrastructure-v1.md)
- Bench boundary: [`docs/BENCH_BOUNDARY.md`](../BENCH_BOUNDARY.md)
- Deepening audit AUDIT-001/002: [`docs/architecture/audits/2026-05-21-deepening-audit.md`](../architecture/audits/2026-05-21-deepening-audit.md)
- Roadmap RM-002/003: [`docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md`](../architecture/roadmaps/2026-05-21-deepening-roadmap.md)
- Implementation: [`src/milodex/commands/bench.py`](../../src/milodex/commands/bench.py)
- Promotion orchestrator: [`src/milodex/promotion/orchestrator.py`](../../src/milodex/promotion/orchestrator.py)
- Qt bridge (healthy thin layer): [`src/milodex/gui/bench_command_bridge.py`](../../src/milodex/gui/bench_command_bridge.py)

---

*Review performed read-only; no production code was modified except this report.*
