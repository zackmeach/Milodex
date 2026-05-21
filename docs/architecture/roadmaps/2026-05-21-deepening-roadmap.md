# Deepening Roadmap - 2026-05-21

## Status

Trackable roadmap for the verified opportunities in
[`2026-05-21-deepening-audit.md`](../audits/2026-05-21-deepening-audit.md).

This file is the cross-session pickup surface. The audit remains the evidence
record; this roadmap owns execution order, status, dependencies, and validation.

## How Agents Should Use This

1. Pick exactly one roadmap item whose `Status` is `ready` and whose
   `Dependencies` are all `done`. Choose the highest-priority ready item unless
   the user explicitly names a different item.
2. Before editing, re-check the cited files and update `Last verified` if the
   claim still holds.
3. Keep the implementation inside the listed `Implementation scope`.
4. Update this file when work starts and when it finishes.
5. Do not broaden a slice because nearby code is tempting. Open a follow-up item
   instead.

Allowed status values:

- `proposed`: verified opportunity, not ready for implementation.
- `ready`: next action is concrete enough for an agent to pick up.
- `in_progress`: one active session owns the item.
- `blocked`: cannot proceed without a decision or prerequisite.
- `done`: merged or otherwise completed and verified.
- `rejected`: deliberately not pursuing.

## Coordination Model

This is a coordinated roadmap, not a single tightly coupled rewrite.

The work is mostly isolated by module, but the first three items all touch the
Bench command facade or launch safety. They need careful order so a cosmetic
interface refactor does not hide a correctness bug.

Parallelism guidance:

- Run `RM-001` first. It fixes proven policy drift and should not wait for
  broader interface design.
- After `RM-001`, `RM-002`, `RM-003a`, and `RM-003b` should not overlap edits
  to `src/milodex/commands/bench.py`.
- `RM-004` is independent and can run in parallel with Bench work.
- `RM-005`, `RM-006`, `RM-007`, and `RM-008` are refactor lanes. Start them only
  after the launch-safety lane has stable tests, unless explicitly reprioritized.
- `RM-009` is ready but low priority. Do not pick it ahead of open P0/P1 ready
  work unless the user asks for documentation cleanup.

## Roadmap Summary

| ID | Source | Title | Priority | Status | Dependencies |
|---|---|---|---|---|---|
| RM-001 | AUDIT-001 | Bench paper-promotion gate parity | P0 | done | none |
| RM-002 | AUDIT-001 | Promotion governance/evidence interface exploration | P1 | proposed | RM-001 |
| RM-003a | AUDIT-002 | Runner audit linkage | P0 | done | RM-001 |
| RM-003b | AUDIT-002 | Bench workflow-readiness seam | P0 | proposed | RM-003a |
| RM-004 | AUDIT-003 | Risk profile activation/audit module | P0 | done | none |
| RM-005 | AUDIT-005 | Backtest run lifecycle and simulation kernel | P2 | proposed | RM-001, RM-003a, RM-003b |
| RM-006 | AUDIT-004 | Daily cross-sectional strategy evaluation flow | P2 | proposed | RM-001, RM-003a, RM-003b, RM-004 |
| RM-007 | AUDIT-006 | GUI polling adapter and projection locality | P3 | proposed | RM-003a, RM-003b, RM-004 |
| RM-008 | AUDIT-007 | Bench Qt bridge internal repetition | P3 | proposed | RM-001, RM-003a, RM-003b |
| RM-009 | AUDIT-008 | Stale architecture prose cleanup | P3 | ready | none |

## RM-001 - Bench Paper-Promotion Gate Parity

Source: AUDIT-001
Status: done
Priority: P0
Last verified: 2026-05-21
Dependencies: none

Problem:

Bench `submit_promote_to_paper` resolves metrics and calls `check_gate`, but it
does not pass `to_stage="paper"` or the configured
`strategy.backtest.min_trades_required` value. `check_gate` defaults
`to_stage` to `"micro_live"`, so the Bench path uses capital-stage thresholds
for a paper promotion. The CLI path passes both values correctly.

Evidence:

- Bench call site:
  [`src/milodex/commands/bench.py`](../../../src/milodex/commands/bench.py)
  around `submit_promote_to_paper`.
- CLI parity target:
  [`src/milodex/cli/commands/promotion.py`](../../../src/milodex/cli/commands/promotion.py)
  passes `to_stage=to_stage` and `min_trade_count=int(config.backtest.get("min_trades_required", 30))`.
- Gate default:
  [`src/milodex/promotion/state_machine.py`](../../../src/milodex/promotion/state_machine.py)
  defaults `check_gate(..., to_stage="micro_live")`.
- Policy source:
  ADR 0052 says paper and capital gates are distinct.

Next action:

Make the Bench path pass `to_stage="paper"` and the configured
`min_trades_required` value to `check_gate`, then add parity tests proving the
Bench and CLI gate behavior match for paper promotion. Also fix operator-facing
Bench proposal/blocker text so paper promotion does not display capital-tier
thresholds.

Implementation scope:

- Only touch Bench promotion submit behavior, paper-promotion proposal/blocker
  copy, and directly relevant tests.
- Do not introduce a new promotion interface yet.
- Do not move metrics code.
- Do not redesign evidence assembly.

Suggested implementation slices:

1. Add a regression test where Sharpe is positive but below the capital
   threshold and drawdown passes the paper threshold. Bench should allow
   `backtest -> paper`.
2. Add a regression test where a strategy-specific `min_trades_required` value
   is honored by Bench.
3. Patch the Bench call to `check_gate`.
4. Patch paper-promotion proposal/blocker text to derive paper thresholds from
   promotion policy, or avoid numeric threshold text.
5. Run focused tests.

Validation:

- `python -m pytest tests/milodex/commands/test_bench_facade.py`
- `python -m pytest tests/milodex/cli/test_promotion.py tests/milodex/promotion/test_policy.py`
- `python -m ruff check src/ tests/`

Done criteria:

- Bench `promote_to_paper` uses paper-tier thresholds.
- Bench honors configured trade floor.
- A paper-tier pass below capital-tier Sharpe is covered by tests.
- Paper-promotion preview/blocker text no longer restates capital-tier
  thresholds.
- No broader promotion refactor lands in this item.

## RM-002 - Promotion Governance/Evidence Interface Exploration

Source: AUDIT-001
Status: proposed
Priority: P1
Last verified: 2026-05-21
Dependencies: RM-001

Problem:

CLI and Bench still know the promotion choreography: metrics lookup, gate
evaluation, manifest hash derivation, evidence assembly, and governance
transition dispatch. `promotion.run_evidence.metrics_from_run()` also lazily
imports CLI analytics, so the promotion layer is not self-contained.

Evidence:

- `promotion.run_evidence.metrics_from_run()` documents and performs the lazy
  CLI analytics import.
- `cli/commands/promotion.py` and `commands/bench.py` both orchestrate the same
  promotion sequence.
- The audit's deletion test says the existing evidence modules have depth, but
  their current interface still leaks too much sequence knowledge.

Next action:

Explore 2-3 interface shapes for a deeper promotion governance/evidence module.
Do not implement the refactor in the exploration pass.

Implementation scope:

- This item is design-only unless a later session explicitly promotes one slice
  to implementation.
- Preserve ADR 0051, ADR 0052, ADR 0015, and ADR 0021.
- Keep `ExecutionService` and the risk layer untouched.

Validation:

- Produce
  `docs/architecture/interface-explorations/2026-05-21-promotion-governance-evidence.md`,
  comparing alternatives by depth, locality, caller burden, and test migration.
- Identify the smallest safe first implementation slice.
- Confirm `RM-001` tests remain the guardrail before any refactor.

Done criteria:

- A written interface decision exists.
- The decision names what module owns paper-promotion gate/evidence
  choreography.
- Test migration is explicit.
- Follow-up roadmap IDs exist for implementation slices that come out of the
  exploration.

## RM-003a - Runner Audit Linkage

Source: AUDIT-002
Status: done
Priority: P0
Last verified: 2026-05-21
Dependencies: RM-001

Problem:

Runner start can report `status="submitted"` with `audit_event_id=None` when no
durable session correlation is found. ADR 0051 requires a submitted command
result to write or link durable audit evidence.

Evidence:

- ADR 0051 requires every submitted result to carry durable audit evidence.
- Bench `submit_start_paper_runner` currently enriches a `session_id` from the
  runner result or the latest open strategy run, and then returns submitted even
  if neither exists.
- `PaperRunnerStartResult` has no `session_id`, while current tests use a fake
  result that injects one.

Next action:

Make submitted runner-control results refuse or error when durable audit linkage
cannot be established. Replace production-mismatched runner fakes with fakes
that match `PaperRunnerStartResult`, then explicitly seed event-store session
correlation where success is expected.

Implementation scope:

- Do not add broker calls directly to `src/milodex/gui/`.
- Do not conflate controlled stop with the kill switch.
- Do not start live or micro-live execution.
- Do not implement workflow-readiness checks in this slice.

Validation:

- `python -m pytest tests/milodex/commands/test_bench_facade.py`
- `python -m pytest tests/milodex/gui/test_bench_command_bridge.py`
- Add a no-session-correlation test that expects `blocked` or `error`, not
  `submitted`.
- Add a successful runner-start test that uses the real production result shape
  and a seeded open `strategy_runs` row.

Done criteria:

- No runner-control submit returns `status="submitted"` with null
  `audit_event_id`.
- Tests no longer rely on a fake production runner result shape that includes
  `session_id`.

## RM-003b - Bench Workflow-Readiness Seam

Source: AUDIT-002
Status: proposed
Priority: P0
Last verified: 2026-05-21
Dependencies: RM-003a

Problem:

Bench paper-runner proposals check strategy existence, trading mode, stage, and
advisory lock, but ADR 0051 requires workflow-readiness checks for submit-capable
actions: reconciliation cleanliness, kill-switch state, data freshness, and
broker reachability. `OPERATIONS.md` makes these submit-gate conditions for
workflow-relevant commands.

Evidence:

- Bench start proposal checks only local stage/mode/lock conditions in
  [`src/milodex/commands/bench.py`](../../../src/milodex/commands/bench.py).
- ADR 0051 requires workflow readiness for promotion, runner start, runner stop,
  and active-runner demotion.
- The reconciliation command surface currently has scaffolded pieces, so the
  roadmap must not imply a complete reconciliation gate already exists.

Next action:

Design and implement a workflow-readiness seam for Bench submit-capable actions.
The first implementation pass must define the matrix below before code changes:

| Action family | reconciliation clean | kill switch inactive | data fresh | broker reachable |
|---|---|---|---|---|
| promote_to_paper | required | required | required | required |
| start_paper_runner | required | required | required | required |
| stop_paper_runner | inspect/report | required | inspect/report | inspect/report |
| demote with active runner | required | required | inspect/report | inspect/report |

The exact blocker codes should be stable and named before implementation:
`reconciliation_drift`, `kill_switch_open`, `data_stale`,
`broker_unreachable`, plus a specific code for any scaffolded reconciliation
dependency that cannot yet produce a final answer.

Implementation scope:

- Keep readiness checks behind a small interface with local adapters and test
  fakes.
- Do not add broker calls directly to `src/milodex/gui/`.
- Do not block safe-anytime backtests on broker reachability.
- Do not treat controlled stop as a kill-switch action.
- If reconciliation cannot yet provide a real clean/dirty verdict, land an
  explicit blocker or decision note rather than a silent pass.

Validation:

- `python -m pytest tests/milodex/commands/test_bench_facade.py`
- `python -m pytest tests/milodex/gui/test_bench_command_bridge.py`
- Tests cover each blocker code in the matrix.
- Tests cover late-submit revalidation after a previously admissible proposal.

Done criteria:

- Workflow-readiness blockers are structured and stable.
- The readiness matrix is implemented or explicitly deferred by named blocker.
- Submit-time revalidation checks readiness, not only propose-time validation.

## RM-004 - Risk Profile Activation/Audit Module

Source: AUDIT-003
Status: done
Priority: P0
Last verified: 2026-05-21
Dependencies: none

Problem:

`RiskProfileBridge` owns Qt signaling plus activation rules, runner checks,
kill-switch checks, selector-file writes, and raw audit SQL. Unknown current
selector content can crash profile activation before an audit row is written.
Startup audit can record Conservative even when a different selector exists.
Malformed known overlays fall back to base defaults rather than the safe
Conservative overlay.

Evidence:

- `RiskProfileBridge.attemptSwitch()` indexes `_RISK_ORDER[current]` without
  normalizing or refusing an unknown current selector.
- `record_startup_default()` writes Conservative-to-Conservative without first
  checking whether `risk_profile.txt` is absent.
- `_load_overlay()` returns `{}` for malformed known overlays, which merges base
  defaults.
- ADR 0054 requires bounded, auditable operator preference activation.

Next action:

Introduce a risk-owned activation/audit module behind the Qt bridge. The bridge
should translate Qt calls and signals; the module should own normalization,
refusal, kill-switch interpretation, active-runner checks, audit append, and
atomic selector write.

Implementation scope:

- Do not move risk preferences into strategy config.
- Do not add a strategy/model/agent path for switching profiles.
- Do not weaken absolute ceilings.
- The bridge may remain the Qt adapter, but policy decisions leave the bridge.

Suggested implementation slices:

1. Add failing tests for unknown-current selector, startup audit when a selector
   already exists, and malformed Conservative overlay behavior.
2. Add a non-Qt risk activation module.
3. Route `RiskProfileBridge` through that module.
4. Align kill-switch interpretation with `KillSwitchStateStore`.

Validation:

- `python -m pytest tests/milodex/gui/test_risk_profile_bridge.py`
- `python -m pytest tests/milodex/gui/test_risk_office_drawer.py`
- `python -m pytest tests/milodex/gui/test_app.py`
- `python -m pytest tests/milodex/gui/test_qml_load_smoke.py`
- `python -m pytest tests/milodex/risk/test_config.py`
- `python -m pytest tests/milodex/execution/test_kill_switch_migration.py`
- `python -m pytest tests/milodex/core/test_migrations.py`
- If audit append moves behind `EventStore`, also run
  `python -m pytest tests/milodex/core/test_event_store.py`.
- `python -m ruff check src/ tests/`

Done criteria:

- Refusals always audit, including invalid current selector state.
- Startup default audit only records absence, or records the actual active
  profile if that becomes the chosen rule.
- Malformed known overlays fail safe according to ADR 0054.
- Qt bridge is an adapter, not the owner of activation policy.

## RM-005 - Backtest Run Lifecycle and Simulation Kernel

Source: AUDIT-005
Status: proposed
Priority: P2
Last verified: 2026-05-21
Dependencies: RM-001, RM-003a, RM-003b

Problem:

Walk-forward orchestration still reaches into private `BacktestEngine` state and
helper methods for run lifecycle, data quality, manifests, and event-store
updates. Daily and intraday simulation paths duplicate pending-order lifecycle,
skipped-order audit, broker sync, fill accounting, entry-state, and snapshot
policy.

Next action:

Explore a backtest-run lifecycle module and a simulation-kernel helper module.
Prefer a staged refactor that first removes private engine reach-through from
walk-forward before changing daily/intraday simulation internals.

Implementation scope:

- Preserve ADR 0021 OOS aggregate behavior.
- Preserve ADR 0030 backtest sandbox semantics.
- Preserve ADR 0053 backtest equity snapshots in their distinct table.
- Do not bypass `ExecutionService` inside simulation.

Validation:

- `python -m pytest tests/milodex/backtesting`
- Golden tests for daily and intraday behavior remain green.
- Walk-forward parent-run, explanation ancestry, failed-run metadata, and
  stranded-pending-order tests stay behavior-level rather than private-helper
  tests.

Done criteria:

- Walk-forward uses a public backtesting interface for parent-run lifecycle.
- Private engine attribute access is reduced or eliminated from
  `walk_forward_runner.py` and `walk_forward_batch.py`.
- Daily/intraday shared behavior has its own testable interface.

## RM-006 - Daily Cross-Sectional Strategy Evaluation Flow

Source: AUDIT-004
Status: proposed
Priority: P2
Last verified: 2026-05-21
Dependencies: RM-001, RM-003a, RM-003b, RM-004

Problem:

Daily cross-sectional strategies repeat the same evaluation protocol: universe
locality, `bars_by_symbol` normalization, exit-first precedence, capacity,
regime filtering, ranking overflow, sizing/affordability, rejected alternatives,
and `DecisionReasoning` shape.

Next action:

Design a shared daily cross-sectional evaluation module. Keep signal-specific
logic inside each strategy and move only the repeated flow behind a deeper
interface.

Implementation scope:

- Do not change strategy parameters or YAML schema.
- Do not move risk-layer caps into strategies.
- Do not alter signal-specific thresholds.
- Start with two representative strategies before migrating all daily
  cross-sectional modules.

Validation:

- `python -m pytest tests/milodex/strategies`
- `python -m pytest tests/milodex/backtesting/test_engine.py`
- Add interface-level tests for common capacity, missing-bar, ranking overflow,
  affordability, and exit-first behavior.

Done criteria:

- At least two strategies share the module without behavioral drift.
- Common-flow tests move to the shared interface.
- Signal-specific tests remain in strategy-specific files.

## RM-007 - GUI Polling Adapter and Projection Locality

Source: AUDIT-006
Status: proposed
Priority: P3
Last verified: 2026-05-21
Dependencies: RM-003a, RM-003b, RM-004

Problem:

`read_models.py` has `_PollingReadModel`, but adjacent state modules duplicate
QTimer, QThreadPool, worker-signal, start/stop, and failure-handling lifecycle
logic. Bench row projection, evidence packet projection, action intent preview,
and ledger construction also share a large implementation surface.

Next action:

After launch-safety work settles, identify one adjacent state module to migrate
to the shared polling interface as a proof slice.

Implementation scope:

- Do not redesign QML surfaces.
- Do not change read-model schemas unless a specific projection bug requires it.
- Do not combine with Bench command bridge cleanup.

Validation:

- `python -m pytest tests/milodex/gui`
- Existing QML forbidden-token tests stay green.

Done criteria:

- One duplicated polling lifecycle is removed.
- Tests assert behavior through the read-model interface, not private timer
  fields.

## RM-008 - Bench Qt Bridge Internal Repetition

Source: AUDIT-007
Status: proposed
Priority: P3
Last verified: 2026-05-21
Dependencies: RM-001, RM-003a, RM-003b

Problem:

`BenchCommandBridge` is the right Qt adapter seam, but its internal
action-specific propose/submit/cache/unknown-proposal handling is repetitive.
Tests also sometimes pin private proposal-cache behavior.

Next action:

Extract a small internal helper for proposal caching, unknown-proposal payloads,
and submit dispatch after the Bench command facade behavior is stable.

Implementation scope:

- Keep the bridge as the only GUI adapter to `BenchCommandFacade`.
- Do not move business rules into QML.
- Do not alter action-family availability.

Validation:

- `python -m pytest tests/milodex/gui/test_bench_command_bridge.py`
- `python -m pytest tests/milodex/commands/test_bench_facade.py`

Done criteria:

- Repetition is reduced without changing QML-visible payloads.
- Unknown-proposal behavior remains structured and tested.

## RM-009 - Stale Architecture Prose Cleanup

Source: AUDIT-008
Status: ready
Priority: P3
Last verified: 2026-05-21
Dependencies: none

Problem:

Some comments and docs still describe older Bench phases, such as `bench.py`
describing a Phase B skeleton while submit-capable action families are now
wired. This is not a deepening opportunity because deleting stale prose removes
complexity rather than moving it behind a deeper interface.

Next action:

Update stale comments and prompts so they match current implementation status,
especially the audit's suggested prompt and the top-level `bench.py` module
docstring.

Implementation scope:

- Documentation and comments only.
- Do not change code behavior.
- Do not use this item to alter ADR decisions.

Validation:

- `python -m pytest tests/milodex/docs tests/milodex/commands/test_bench_facade.py`
- `python -m ruff check src/ tests/`

Done criteria:

- No comment claims Bench is propose-only.
- The audit's suggested next prompt points at RM-001 first.

## Roadmap-Level Acceptance

This roadmap is ready to execute when:

- Every P0 item has a concrete next action and validation command.
- No P0 item marked `ready` contains multiple independently shippable safety
  slices.
- Dependencies prevent concurrent edits to the same fragile seam.
- Each item names an implementation scope.
- Each item can be resumed by a future agent without re-reading the entire
  architecture audit.
- The reviewer pass has either accepted the roadmap or its blocking findings
  have been incorporated.
