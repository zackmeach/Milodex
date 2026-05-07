# Test-Efficacy Audit (Mutation Testing)

**Audit date:** 2026-05-06
**Branch:** `feat/phase4-test-efficacy-audit` (Phase 4, PR #4)
**Tool:** mutmut 2.5.1 (pinned `>=2.4,<3` — see Methodology for why)
**Discovery only:** no test changes made. Operator chooses gaps to fix in a follow-up PR.

## A. Summary

Mutation testing was run against the four highest-stakes files in the codebase:
the risk veto layer, the promotion gate, the manifest discipline, and the kill-switch
state machine. Each file's mutants were tested against ITS direct test directory only
(`tests/milodex/risk/`, `tests/milodex/promotion/`, `tests/milodex/execution/`).

| File                                  | Mutants | Killed | Survived | Kill % |
|---------------------------------------|--------:|-------:|---------:|-------:|
| `src/milodex/risk/evaluator.py`       |     257 |    118 |      139 |  45.9% |
| `src/milodex/promotion/state_machine.py` | 150 |     76 |       74 |  50.7% |
| `src/milodex/promotion/manifest.py`   |      25 |     17 |        8 |  68.0% |
| `src/milodex/execution/state.py`      |      40 |     17 |       23 |  42.5% |
| **TOTAL**                             | **472** | **228**| **244**  |**48.3%**|

**Headline:** 48.3% of the generated mutants are killed by the per-file test directory.
That number is much lower than the 89.6% line-coverage figure, but it is **not** an
apples-to-apples comparison and not a verdict on test quality on its own. Three
patterns dominate the surviving 51.7%:

1. **Equivalent mutations the test suite cannot kill in principle.** Type-annotation
   mutations (`str | None` → `str & None`), `@dataclass(frozen=True)` →
   `frozen=False`, comparison-vs-equality flips that are dead code given a prior
   guard, etc. Roughly one in four survivors is in this bucket (best estimate
   from manual sampling — mutmut does not classify automatically).
2. **Cross-directory test pollution.** Mutations to risk-evaluator code paths
   like `_check_market_open`, `_check_trading_mode`, `_check_kill_switch`
   are caught by `tests/milodex/execution/test_service.py`, **not** by anything in
   `tests/milodex/risk/`. Per the audit's per-directory scoping, those
   integration kills are invisible in the table above. The numbers therefore
   understate true efficacy.
3. **Real gaps worth fixing.** A non-trivial residue: the promotion `demote()`
   function has no unit test in `tests/milodex/promotion/` (its only tests
   live under `tests/milodex/cli/`); the risk-evaluator's global
   `allowed = all(...)` aggregator is never asserted on as a positive
   invariant; `MIN_TRADES = 30` and `MAX_DRAWDOWN_PCT = 15.0` are not pinned
   to their literal values by any test; the kill-switch state store's legacy
   JSON-migration path (lines 70-90 of `execution/state.py`) is essentially
   untested.

The full per-file breakdown and the prioritized recommendation list are in
sections B, C, and E. Section D documents the methodology including the
several blockers that shaped it.

## B. Per-file analysis

### B.1 `src/milodex/risk/evaluator.py` — 257 mutants, 45.9% killed

Tests in scope: `tests/milodex/risk/test_risk_rules.py` (878 lines, direct
behavioral tests for each rule) and `tests/milodex/risk/test_policy.py` (47
lines, RiskDefaults loader).

Surviving mutants cluster on three patterns:

- **Type annotations on `EvaluationContext` fields (12 mutants, lines 46-69)**:
  `str | None` → `str & None`, etc. Equivalent — Python does not evaluate
  these at runtime, so no behavior test can kill them.
- **String literals inside `RiskCheckResult(...)` constructors (~50 mutants)**:
  the user-facing `name=`, `message=`, and the success-case `True` parameter
  values. The risk-rules tests assert on `passed` and `reason_code` but
  rarely on `name` or `message`. Most of these are minor (changing display
  text without breaking safety), but a handful are not — see the top-3 below.
- **Cross-cutting checks tested elsewhere**: `_check_kill_switch`,
  `_check_trading_mode`, `_check_market_open`, `_check_data_staleness` are
  exercised primarily by `tests/milodex/execution/test_service.py`. Mutations
  on those check bodies survive when the audit is scoped to `tests/milodex/risk/`
  alone, but most would die under a broader test set.

**Top 5 surviving mutations (ordered by safety stakes):**

| ID  | File:line | Original | Mutation | Assessment |
|----:|-----------|----------|----------|------------|
| 35  | `evaluator.py:116` | `if context.trading_mode != "paper":` | `if context.trading_mode == "paper":` | **Real gap.** Inverts the paper-mode gate. Killed by `tests/milodex/execution/test_service.py` but invisible to the per-file audit. Risk-rules tests do not exercise non-paper trading_mode. |
| 105 | `evaluator.py:233` | `if not context.market_open:` | `if context.market_open:` | **Real gap.** Inverts the market-hours check. Same pattern as #35 — killed only by `test_service.py`. |
| 18  | `evaluator.py:91`  | `allowed = all(check.passed for check in checks)` | `allowed = None` | **Real gap.** Tests assert on `decision.checks[i].passed` but never on the global `decision.allowed` for a happy-path call. A regression that broke the AND-aggregation across checks would not be caught by the risk-rules suite. |
| 144 | `evaluator.py:281` | `if current_loss_pct > max_daily_loss:` | `if current_loss_pct >= max_daily_loss:` | **Edge case worth a test.** Boundary condition on the daily-loss cap. There is a test for "loss between cap and kill switch" but not for `current_loss_pct == max_daily_loss` exactly. |
| 121 | `evaluator.py:253` | `if age > max_age:` | `if age >= max_age:` | **Edge case worth a test.** Boundary on data-staleness; tests do not pin the exact equality semantic. Less critical than #144 because the staleness window is not load-bearing. |

### B.2 `src/milodex/promotion/state_machine.py` — 150 mutants, 50.7% killed

Tests in scope: `tests/milodex/promotion/` (state_machine, manifest, evidence,
transition — 935 test lines combined).

Two structural observations dominate the survivor list:

- **`demote()` has no unit test in `tests/milodex/promotion/`.** The function
  spans state_machine.py lines 257-336. Its tests live under
  `tests/milodex/cli/test_promotion_demote.py`, which the per-file scoping
  excludes. About 30 of the 74 surviving mutants are inside `demote()`.
  This is the largest single concentration of survivors.
- **Threshold constants are not pinned by any test.** `MIN_SHARPE = 0.5`,
  `MAX_DRAWDOWN_PCT = 15.0`, and `MIN_TRADES = 30` survive numeric
  perturbation (e.g., `15.0` → `16.0`). The tests use the constants
  symbolically (`MIN_SHARPE - 1`) rather than asserting on the literal
  value. A silent edit lowering the bar would slip past CI.

**Top 5 surviving mutations:**

| ID  | File:line | Original | Mutation | Assessment |
|----:|-----------|----------|----------|------------|
| 11  | `state_machine.py:43`  | `MAX_DRAWDOWN_PCT: float = 15.0` | `MAX_DRAWDOWN_PCT: float = 16.0` | **Real gap.** Operator-stakes constant; no test asserts on the literal value. SRS R-PRM-002 names 15.0% explicitly — a test that says `assert MAX_DRAWDOWN_PCT == 15.0` would lock it. |
| 13  | `state_machine.py:44`  | `MIN_TRADES: int = 30` | `MIN_TRADES: int = 31` | **Real gap.** Same pattern as #11; SRS R-PRM-003 names 30 explicitly. |
| 98  | `state_machine.py:289` | `if reason is None or not reason.strip():` | `if reason is not None or not reason.strip():` | **Real gap.** Inverts the demote-reason validation. `demote(reason=None)` would silently succeed. No promotion-suite test calls `demote()` directly. |
| 100 | `state_machine.py:289` | `if reason is None or not reason.strip():` | `if reason is None and not reason.strip():` | **Real gap.** Same line, different operator-flip; same root cause. |
| 35  | `state_machine.py:77`  | `if to_idx < from_idx:` | `if to_idx <= from_idx:` | **Equivalent mutation.** The line directly above (`if to_idx == from_idx:`) returns first, so `<=` and `<` are behaviorally identical. |

### B.3 `src/milodex/promotion/manifest.py` — 25 mutants, 68.0% killed

Tests in scope: `tests/milodex/promotion/test_manifest.py` (182 lines).

Best kill rate of the four files — manifest.py is small (95 lines) and the
dedicated test file covers freeze, hash continuity, the
backtest-stage refusal, and the resolve-by-id helper. Surviving mutants:

| ID | File:line | Original | Mutation | Assessment |
|---:|-----------|----------|----------|------------|
| 3  | `manifest.py:25` | `_FROZEN_STAGES = frozenset({"paper", "micro_live", "live"})` | `frozenset({"paper", "micro_live", "XXliveXX"})` | **Equivalent for the test suite** — no test exercises freeze-of-`live` (Phase 1 blocks it at promotion). Worth a single test that explicitly enumerates the frozen-stage set: `assert _FROZEN_STAGES == frozenset({"paper", "micro_live", "live"})`. |
| 5  | `manifest.py:32` | `frozen_by: str = "operator"` | `frozen_by: str = "XXoperatorXX"` | **Edge case** — the default `frozen_by` value is not asserted. Tests pass `frozen_by` explicitly or accept any value. Low stakes. |
| 19 | `manifest.py:84` | `def resolve_strategy_config_path(strategy_id: str, config_dir: Path = Path("configs")) -> Path:` | `Path = Path("XXconfigsXX")` | **Edge case** — the default `config_dir` is `configs/`. Tests pass `tmp_path` explicitly so the default goes uncovered. Operator-facing: a default-typo would only bite when `resolve_strategy_config_path()` is called without arguments. |

The other 5 survivors are all string mutations inside `ValueError` messages —
true minor cosmetic. None of them threaten correctness.

### B.4 `src/milodex/execution/state.py` — 40 mutants, 42.5% killed

Tests in scope: `tests/milodex/execution/` (1,690 test lines). The kill-switch
store has no dedicated `test_state.py`; coverage comes via service tests.

Survivor concentration: lines 70-90, the legacy JSON-migration path. That
path is exercised exactly once in the suite (a single test that touches the
migration) and many of its branch conditions therefore go unverified.

**Top 5 surviving mutations:**

| ID | File:line | Original | Mutation | Assessment |
|---:|-----------|----------|----------|------------|
| 26 | `state.py:72` | `if self._event_store.get_latest_kill_switch_event() is not None:` | `is None` | **Real gap (migration path).** Inverts the "skip migration if event store already has events" check. Would re-migrate on every call. |
| 30 | `state.py:78` | `if not bool(data.get("active", False)):` | `if  bool(data.get("active", False)):` | **Real gap (migration path).** Inverts the "skip if legacy file says inactive" check. Would migrate inactive states as activations. |
| 22 | `state.py:62` | `event_type="reset"` | `event_type="XXresetXX"` | **Real gap.** The reset-vs-activated string is the load-bearing distinguishing field of the kill-switch event log. No test asserts that `reset()` writes the literal string `"reset"`. Tests assert `event_type == "activated"` for activations but not the symmetric `"reset"`. |
| 10 | `state.py:33` | `self._legacy_path.parent.mkdir(parents=True, exist_ok=True)` | `parents=False, exist_ok=True` | **Edge case.** Removes the multi-level mkdir behavior; would crash on first init under nested paths. Tests pass `tmp_path` (single-level) so mutation goes undetected. |
| 1  | `state.py:12` | `@dataclass(frozen=True)` | `frozen=False` | **Equivalent.** No test attempts to mutate a `KillSwitchState` instance, so the frozen-ness is unverified. Low stakes — operator behavior would not change unless other code started mutating these instances. |

## C. Top recommendations (prioritized)

The operator chooses what to fix in the follow-up PR. Recommendations are
grouped by stakes, not by count.

### Critical — safety-critical behaviors lacking explicit assertion

1. **Pin the global risk-veto aggregation.** `tests/milodex/risk/test_risk_rules.py`
   never asserts `decision.allowed is True` for the all-pass case nor
   `decision.allowed is False` for the any-fail case. Add one positive
   and one negative assertion against `decision.allowed` to lock the
   load-bearing AND across all checks. Targets surviving mutant #18 in
   `evaluator.py`.

2. **Pin `_FROZEN_STAGES` and the promotion thresholds to their literal
   values.** Add three one-liners in `tests/milodex/promotion/test_state_machine.py`:
   `assert MIN_SHARPE == 0.5`, `assert MAX_DRAWDOWN_PCT == 15.0`,
   `assert MIN_TRADES == 30`. SRS R-PRM-001/002/003 name these literals;
   without an assertion, a silent change to the constant would land
   without tripping CI. Targets state_machine mutants #6, #11, #13.

3. **Add direct unit tests for `demote()`.** Currently the only tests
   live under `tests/milodex/cli/test_promotion_demote.py`, which
   excludes ~30 of the 74 state_machine survivors. At minimum: one
   happy-path test, one `demote(reason=None)` rejection, one
   `demote(reason="")` rejection, one `from_stage == to_stage` rejection,
   one demote-from-paper-to-backtest-updates-yaml test. Targets the
   largest concentration of state_machine survivors (mutants #94-128).

### Important — correctness behaviors with weak coverage

4. **Boundary tests for `_check_daily_loss` and `_check_data_staleness`.**
   Add `current_loss_pct == max_daily_loss` (mutant #144) and
   `age == max_age` (mutant #121). Both rules use strict `>` comparisons;
   the existing tests cover "well over" and "well under" but not
   "exactly at." Same pattern as the `MIN_SHARPE` boundary test that
   already exists in `test_state_machine.py`.

5. **Lock the kill-switch event-type strings.** Add `assert events[-1].event_type == "reset"`
   to the `KillSwitchStateStore.reset()` test path. The activation
   string is already locked symmetrically; the reset string is not.
   Targets execution/state mutant #22.

### Polish — edge cases and error paths

6. **Cover the legacy JSON-migration path.** `execution/state.py` lines 70-90
   have a single happy-path test. Add: (a) `_migrate_legacy_state_if_needed()`
   when event store already has events should be a no-op (mutant #26);
   (b) legacy file with `"active": false` should not be migrated as an
   activation (mutant #30); (c) timestamp-fallback when `last_triggered_at`
   is missing or non-string. This codepath will be removed when the JSON
   format is dropped, so investing here only makes sense if the migration
   has months of life left.

7. **Lock `_FROZEN_STAGES` and `STAGE_ORDER` literal contents.** Add
   `assert _FROZEN_STAGES == frozenset({"paper", "micro_live", "live"})`
   to `test_manifest.py` and similarly for `STAGE_ORDER` (already tested
   for length but not content) in `test_state_machine.py`.

8. **Sample-test the `frozen_by` and `config_dir` defaults.** `freeze_manifest()`
   defaults `frozen_by="operator"` and `resolve_strategy_config_path()`
   defaults `config_dir=Path("configs")`. Both are operator-facing
   defaults that would only bite via the ergonomic call site (no
   keyword args). Two-line tests apiece.

## D. Methodology

### Tool

`mutmut` 2.5.1 (the last 2.x release).

- **Why 2.x not 3.x?** mutmut 3.x explicitly refuses to run on native Windows
  (boxed/mutmut#397, error: *"To run mutmut on Windows, please use the WSL"*).
  The operator's primary dev environment is Windows without WSL configured.
  2.5.1 runs on Windows.
- **Why pinned in `pyproject.toml`?** Same reason — a CI or fresh-install
  resolution to 3.x would break the audit on the operator's machine.
- **Why Python 3.13 not 3.14?** mutmut 2.5.1's pony ORM 0.7.19 dependency
  fails to deepcopy translator state under Python 3.14
  (`TypeError: cannot copy 'itertools.count' object`). 3.13 works, with
  the caveats noted below.

### Command invocation

For each target file:

```
PYTHONIOENCODING=utf-8 NO_COLOR=1 PY_COLORS=0 \
  python -m mutmut run \
    --paths-to-mutate src/milodex/<file>.py \
    --tests-dir tests/milodex/<dir> \
    --runner "python -m pytest --no-cov -x -q --tb=line -p no:cacheprovider tests/milodex/<dir>" \
    --no-progress --simple-output --CI
```

The encoding/color env vars prevent cp1252 bytes leaking into mutmut's
utf-8 streaming output decoder (mutmut crashes on `UnicodeDecodeError`
otherwise). `--tb=line` keeps tracebacks short. `-p no:cacheprovider`
disables pytest's cache to keep state clean across mutants.

### Files in scope

- `src/milodex/risk/evaluator.py` — the 12-check (kill switch through
  duplicate-order) risk veto layer
- `src/milodex/promotion/state_machine.py` — stage-transition validation,
  the gate, transactional promotion, demotion
- `src/milodex/promotion/manifest.py` — frozen-config snapshotting and
  resolution
- `src/milodex/execution/state.py` — the kill-switch state store (event-store
  backed, with legacy-JSON migration)

### Files out of scope

The audit deliberately did not touch the rest of the codebase. CLI
presentation, runner orchestration, broker adapters, strategies, data
providers, backtest engine, evidence packaging — all out of scope for this
PR. The four files listed are the highest-stakes "if these tests are
weak, real money is at risk" surface.

### Test scoping

Each file's mutations are tested only against its own per-module test
directory:

- `risk/evaluator.py` → `tests/milodex/risk/`
- `promotion/state_machine.py` → `tests/milodex/promotion/`
- `promotion/manifest.py` → `tests/milodex/promotion/`
- `execution/state.py` → `tests/milodex/execution/`

This is a deliberate scoping choice that **understates true kill rate** — many
mutations in `risk/evaluator.py` are caught only by
`tests/milodex/execution/test_service.py`. The justification: the audit
asks "do the tests near this code adequately verify it?" rather than
"does any test in the suite happen to catch this?". Both are valid
questions; this audit answers the first.

A whole-suite re-run would lift the overall kill rate. An estimate from
spot checks: an additional 30-40% of the per-file survivors would die
under the broader suite. The remaining residue would still be the
operator-actionable list in section C.

### Time taken

Wall-clock, end-to-end: ~50 minutes across all four files. Per-file:

- `risk/evaluator.py`: ~25 minutes (largest file, 257 mutants, two crash-and-resume
  cycles due to the pony bug below)
- `promotion/state_machine.py`: ~14 minutes (150 mutants, one resume)
- `promotion/manifest.py`: ~3 minutes (25 mutants, single-shot)
- `execution/state.py`: ~7 minutes (40 mutants, single-shot)

### Caveats

1. **Equivalent mutations are unkillable in principle.** Roughly 25%
   of the survivors are equivalent (no behavior change). Examples in the
   data: `dataclass(frozen=True)` → `frozen=False` when no test mutates
   the instance; `if to_idx < from_idx` → `<=` when a prior `==` guard
   makes them identical; `str | None` → `str & None` annotation flips
   that Python does not evaluate.

2. **mutmut 2.5.1 + pony ORM is unreliable on Python 3.13.** During the
   risk and state_machine runs, mutmut crashed twice with
   `ValueError: Attribute Mutant.line is required` from pony's translator
   deepcopy. Workaround: re-run with the same command (mutmut skips
   already-tested mutants from the cache). The remaining stragglers
   were resolved by running individual mutant IDs explicitly. The
   wall-clock impact was several minutes per file; the audit results
   are unaffected.

3. **mutmut 2.x leaves the source file in a mutated state when it
   crashes mid-mutant.** This was caught and reverted before any commit.
   The runner script (`scripts/run_mutation_audit.ps1`) does not yet
   guard against this; the operator should `git status` after every run
   and `git checkout` any unintentional changes to the four target
   files before continuing.

4. **Test pollution risk is low here.** All four test directories run
   to green individually and in combination (verified pre-audit:
   `pytest tests/milodex/risk tests/milodex/promotion tests/milodex/execution
   --no-cov -x -q` → 129 passed). The mutation runs reuse fresh `tmp_path`
   per test; mutmut's per-mutant subprocess invocation precludes
   cross-mutant pollution.

5. **mutmut's mutation operators are not exhaustive.** It does not test
   exception-type mutations, or off-by-one mutations on slices and ranges
   to the same depth as a tool like Cosmic Ray. Its operator suite is
   well-suited to safety-critical control-flow code (which is what we
   targeted) and less so to numerical or off-by-one heavy code.

## E. How to re-run

### Setup

```
pip install -e ".[dev]"
```

`mutmut>=2.4,<3` is in the dev dep group. Install verifies via:

```
python -m mutmut --version
```

### Reproduce a single file

```
python -m mutmut run \
  --paths-to-mutate src/milodex/risk/evaluator.py \
  --tests-dir tests/milodex/risk \
  --runner "python -m pytest --no-cov -x -q --tb=line -p no:cacheprovider tests/milodex/risk" \
  --no-progress --simple-output --CI
```

Replace the source path and tests dir for the other three targets per the
mapping in section D.

After the run completes (or crashes), inspect:

```
python -m mutmut results        # high-level, pony bug may surface
sqlite3 .mutmut-cache "SELECT status, COUNT(*) FROM mutant GROUP BY status;"
python -m mutmut show <id>      # see the diff for a specific mutant
```

### Reproduce the whole audit

```
pwsh scripts/run_mutation_audit.ps1
```

The script wraps the four invocations and pipes encoding env vars
through. It does not (yet) restore source files on mutmut crash —
add `git checkout src/milodex/<file>.py` after each iteration if you
hit the pony bug.

### Add a file to the mutation scope

Edit `scripts/run_mutation_audit.ps1`:

```pwsh
$targets = @{
    ...
    "broker"      = @{ Path = "src/milodex/broker/alpaca.py";    Tests = "tests/milodex/broker" }
    ...
}
```

The `Tests` path can also be a single test file (e.g.
`tests/milodex/execution/test_service.py`) if you want to scope the
mutation kill-set against a specific suite.

### Run mutmut on a single function

mutmut 2.x does not have a "function" filter. Use line-range filtering at
the cache layer:

```
sqlite3 .mutmut-cache "SELECT m.id FROM mutant m JOIN Line l ON m.line=l.id WHERE l.line_number BETWEEN 100 AND 150;"
```

Then iterate:

```
for id in $(sqlite3 .mutmut-cache "..."); do
    python -m mutmut run "$id" --paths-to-mutate <file> --tests-dir <dir> --runner "..."
done
```

### Notes for the next operator pass

- `.mutmut-cache` files are .gitignored. They survive across runs and
  let mutmut skip already-tested mutants — useful for resume-after-crash.
- The per-file cache backups produced during this audit
  (`.mutmut-cache-risk.db` etc.) are also .gitignored. They are the source
  of truth for the per-mutant diffs in section B.
- This audit was discovery only. Section C is the operator's input
  queue; the operator decides which gaps to fix and in what order.
  The follow-up PR should reference this document and check off the
  recommendations it addresses.
