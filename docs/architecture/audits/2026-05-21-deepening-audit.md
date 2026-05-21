# Deepening Architecture Audit - 2026-05-21

## Status

First architecture audit / no code changes.

## Scope

This audit synthesizes the 2026-05-21 bounded parallel architecture audit. It is
based on six parallel area agents plus a local ADR/test reviewer pass.

Areas covered:

- Bench command lifecycle and command facade
- Promotion evidence, analytics metrics, and run evidence
- Risk profile activation, kill switch, and audit write path
- GUI read models, Qt adapters, and projection builders
- Strategy modules and shared daily cross-sectional evaluation flow
- Backtest engine and simulation kernel
- Local ADR/test reviewer pass across project intent, ADRs, and test strategy

No new architecture scan was performed while creating this document. This is a
durable record of the current audit synthesis only.

## Decision

Proceed next on **AUDIT-001: Promotion governance and evidence seam for CLI +
Bench**.

Before broader interface exploration, perform one narrow parity bug fix: make
Bench pass `to_stage="paper"` and the configured `min_trades_required` value to
`check_gate`, then add CLI/Bench parity coverage. After that safety correction,
continue to interface exploration for AUDIT-001.

Do not implement the broader refactor yet. Do not design final interfaces in
this audit record.

## Executive Summary

The strongest opportunities are not "split big files because they are big." They
are places where a valuable module already passes the deletion test, but its
current interface leaks too much choreography to callers.

Most decision-worthy findings are also correctness or auditability risks:

- Bench `promote_to_paper` is proven to have drifted from CLI/governance
  semantics: it calls `check_gate` without `to_stage="paper"` or the configured
  trade floor, so it falls back to capital-stage defaults.
- Bench runner submit can report success without a durable audit reference.
- ADR 0051 workflow-readiness blockers are not implemented in the Bench facade.
- Risk profile activation/audit lives too low in a Qt bridge and has audit
  correctness failures.
- Promotion evidence and analytics are close to the right shape, but
  `promotion.run_evidence.metrics_from_run()` still lazily imports CLI analytics.
- Strategy and backtest refactors are strong, mostly in-process candidates with
  good test leverage. AUDIT-004 is a refactor opportunity, not a bug claim.

## Ranked Candidate Table

| ID | Rank | Candidate | Bucket | Dependency class | Leverage / locality / risk / cost | Confidence |
|---|---:|---|---|---|---|---|
| AUDIT-001 | 1 | Promotion governance/evidence seam for CLI + Bench | Strong | in-process + local-substitutable | Very high / high / high / medium | High |
| AUDIT-002 | 2 | Bench workflow readiness + runner audit lifecycle | Strong | local-substitutable + true external adapters | High / high / high / medium-high | High |
| AUDIT-003 | 3 | Risk profile activation and audit module | Strong | local-substitutable | High / high / high / medium | High |
| AUDIT-004 | 4 | Daily cross-sectional strategy evaluation flow | Strong | mostly in-process | High / high / medium / medium | High |
| AUDIT-005 | 5 | Backtest run lifecycle and simulation kernel | Strong | in-process + local-substitutable | High / medium-high / medium / high | High |
| AUDIT-006 | 6 | GUI polling adapter + projection locality | Maybe later | local-substitutable + Qt adapter | Medium / medium / low-medium / medium | High |
| AUDIT-007 | 7 | Bench Qt bridge internal repetition | Maybe later | in-process + Qt adapter | Low-medium / medium / low / low | Medium |
| AUDIT-008 | 8 | Stale phase/doc prose cleanup | Reject as architecture candidate | n/a | Low / high / low / low | High |

## Detailed Candidate Writeups

### AUDIT-001 - Promotion Governance/Evidence Seam

Files:

- [`src/milodex/commands/bench.py`](../../../src/milodex/commands/bench.py)
- [`src/milodex/cli/commands/promotion.py`](../../../src/milodex/cli/commands/promotion.py)
- [`src/milodex/promotion/state_machine.py`](../../../src/milodex/promotion/state_machine.py)
- [`src/milodex/promotion/run_evidence.py`](../../../src/milodex/promotion/run_evidence.py)
- [`src/milodex/analytics/metrics.py`](../../../src/milodex/analytics/metrics.py)

Problem:

Bench repeats promotion choreography and omits `to_stage="paper"` plus the
configured trade floor when checking the gate. Since `check_gate` defaults to
capital-stage behavior, Bench applies capital-stage defaults to paper promotion
unless the metrics happen to satisfy the stricter path. The CLI correctly passes
both `to_stage` and the configured trade floor. Separately,
`promotion.run_evidence.metrics_from_run()` lazily imports CLI analytics. Bench
does not directly import CLI analytics; the inversion is inside the promotion
run-evidence module. The interface is shallow because callers must know the
sequence: metrics -> gate -> hash -> evidence -> transition.

Deletion test:

Deleting `promotion.run_evidence.metrics_from_run` would force OOS-vs-whole
period logic to reappear in CLI promotion and Bench. Deleting
`promotion.evidence` would spread required-field validation and event-store
derivations across callers. These modules are earning their keep; the friction
is that the seam does not yet hide enough promotion choreography.

Complexity to move behind the seam:

- Requested stage-transition evidence
- OOS metrics lookup
- Stage-specific gate evaluation
- Configured trade-floor handling
- Manifest hash derivation
- Evidence package assembly
- Governance transition dispatch

ADR conflicts:

High conflict with ADR 0051's same-governance-path requirement and ADR 0052's
two-tier paper/capital promotion policy.

Tests:

Survive:

- Promotion policy and state-machine invariant tests
- CLI promotion behavior tests
- Analytics OOS aggregate behavior tests
- Transition atomicity tests

Replace or strengthen:

- Tests that monkeypatch CLI analytics from `promotion.run_evidence`
- Bench promote tests that only use capital-passing metrics
- Add Bench/CLI parity tests for paper-tier behavior and configured
  `min_trades_required`
- Add a narrow parity regression where a positive Sharpe below the capital
  threshold passes for `backtest -> paper`, matching the CLI path

Confidence: High.

### AUDIT-002 - Bench Workflow Readiness + Runner Audit Lifecycle

Files:

- [`src/milodex/commands/bench.py`](../../../src/milodex/commands/bench.py)
- [`src/milodex/strategies/paper_runner_control.py`](../../../src/milodex/strategies/paper_runner_control.py)
- [`docs/OPERATIONS.md`](../../OPERATIONS.md)
- [`docs/adr/0051-bench-command-infrastructure-v1.md`](../../adr/0051-bench-command-infrastructure-v1.md)

Problem:

Bench start/stop proposals check stage and advisory lock, but not all ADR 0051
workflow-readiness conditions. Runner start can also produce a submitted result
with `audit_event_id=None` when no session correlation is found. Existing tests
can hide this by using a fake runner result shape that includes `session_id`,
while production `PaperRunnerStartResult` does not provide one directly.

Deletion test:

Deleting the duplicated runner-launch and audit-correlation logic from the
facade would not remove complexity; it would move behind a runner lifecycle seam
where launch request, orchestration job, session correlation, and controlled-stop
request can be verified together.

Complexity to move behind the seam:

- Workflow-readiness blockers
- Reconciliation cleanliness checks
- Kill-switch state checks
- Data freshness checks
- Broker reachability checks
- Orchestration job creation/finalization
- Session correlation
- Controlled-stop request/audit linkage

ADR conflicts:

High conflict with ADR 0051 and OPERATIONS audit/readiness requirements.

Tests:

Survive:

- Stage compatibility tests
- Advisory-lock tests
- Controlled-stop semantics tests

Replace or strengthen:

- Replace production-mismatched fakes that include `session_id`
- Add readiness blocker tests
- Assert blocked/error when durable audit linkage cannot be made

Confidence: High.

### AUDIT-003 - Risk Profile Activation and Audit Module

Files:

- [`src/milodex/gui/risk_profile_bridge.py`](../../../src/milodex/gui/risk_profile_bridge.py)
- [`src/milodex/risk/config.py`](../../../src/milodex/risk/config.py)
- [`src/milodex/gui/app.py`](../../../src/milodex/gui/app.py)
- [`src/milodex/execution/state.py`](../../../src/milodex/execution/state.py)

Problem:

`RiskProfileBridge` owns Qt signaling plus risk activation rules, runner checks,
kill-switch checks, file writes, and raw audit SQL. Unknown current profile
content can crash switch activation without an audit row. Startup audit can
falsely record Conservative while another profile is selected because the
startup audit helper does not first check whether `risk_profile.txt` is absent.
Malformed known overlays can fall back to base defaults instead of the safe
default intent because `_load_overlay()` returns an empty dict and the loader
then merges base defaults.

Deletion test:

`load_active_risk_profile()` and `KillSwitchStateStore` already earn depth.
Deleting `RiskProfileBridge` would push some complexity into QML/app code, so it
is useful, but it is shallow at the audit seam. Deleting `EventStore` from the
risk-profile audit path changes little because audit appends bypass it; that is
the locality loss.

Complexity to move behind the seam:

- Profile normalization
- Activation refusal
- Startup default audit
- Kill-switch interpretation
- Active-runner checks
- Risk-profile audit append
- Atomic selector-file write

ADR conflicts:

High conflict with ADR 0054 safe-default and audit requirements.

Tests:

Survive:

- Runtime profile routing tests
- Ceiling validation tests
- Bridge refusal-path tests
- Kill-switch migration tests
- Migration 011 schema tests

Replace or strengthen:

- Invalid-current selector must refuse or normalize and still audit
- Startup audit must reflect absence-only or actual active profile
- Malformed Conservative overlay must fail safe
- Bridge kill-switch interpretation should stay aligned with
  `KillSwitchStateStore`

Confidence: High.

### AUDIT-004 - Daily Cross-Sectional Strategy Evaluation Flow

Files:

- [`src/milodex/strategies/base.py`](../../../src/milodex/strategies/base.py)
- [`src/milodex/strategies/meanrev_rsi2_pullback.py`](../../../src/milodex/strategies/meanrev_rsi2_pullback.py)
- [`src/milodex/strategies/meanrev_ibs_lowclose.py`](../../../src/milodex/strategies/meanrev_ibs_lowclose.py)
- [`src/milodex/strategies/momentum_daily_tsmom.py`](../../../src/milodex/strategies/momentum_daily_tsmom.py)
- [`src/milodex/strategies/breakout_donchian.py`](../../../src/milodex/strategies/breakout_donchian.py)

Problem:

Each daily cross-sectional strategy must remember a hidden protocol:
universe-scoped positions, `bars_by_symbol` normalization, exit-first flow,
capacity calculation, market regime filtering, ranking, sizing, rejection
reasoning, and `DecisionReasoning` shape. This is a refactor opportunity, not a
bug claim; its value is locality and leverage after higher-priority safety/audit
work.

Deletion test:

If a shared daily cross-sectional evaluation module existed and were deleted,
the complexity would reappear across at least eight strategy implementations.
That means the module would have real depth and leverage. Deleting any one
current strategy removes signal logic, but the repeated flow survives in its
neighbors.

Complexity to move behind the seam:

- Universe locality
- Bar/position normalization
- Exit-first precedence
- Capacity handling
- Rejection recording
- Ranking overflow
- Sizing/affordability handling
- Common no-signal reasoning

ADR conflicts:

None found if ADR 0008 risk veto, ADR 0003 config-driven strategies, ADR 0022
universe scope, and ADR 0024/0029 risk-layer caps remain intact.

Tests:

Survive:

- Signal-specific behavior tests
- Runner tests proving all universe symbols are fetched
- Backtest tests proving `bars_by_symbol` strategies evaluate correctly
- Promotion/manifest/risk tests

Replace or delete later:

- Repeated per-strategy tests for common universe filtering
- Missing-bar plumbing tests duplicated across strategies
- Capacity no-signal tests duplicated across strategies
- Affordability rejection tests duplicated across strategies
- Ranking overflow and common stop/max-hold precedence tests duplicated across
  strategies

Confidence: High.

### AUDIT-005 - Backtest Run Lifecycle and Simulation Kernel

Files:

- [`src/milodex/backtesting/engine.py`](../../../src/milodex/backtesting/engine.py)
- [`src/milodex/backtesting/walk_forward_runner.py`](../../../src/milodex/backtesting/walk_forward_runner.py)
- [`src/milodex/backtesting/walk_forward_batch.py`](../../../src/milodex/backtesting/walk_forward_batch.py)
- [`src/milodex/core/event_store.py`](../../../src/milodex/core/event_store.py)
- [`src/milodex/analytics/snapshots.py`](../../../src/milodex/analytics/snapshots.py)

Problem:

Walk-forward has some public-ish support from the backtesting module, but
orchestration still reaches into private `BacktestEngine` state and helper
methods. Daily and intraday simulation duplicate timeline, broker sync,
pending-order, skipped audit, fill counting, entry-state, and snapshot behavior.

Deletion test:

Deleting `walk_forward_runner` would push OOS parent-run lifecycle, data-quality
handling, run-manifest metadata, window isolation, and aggregation into CLI,
Bench, and batch runners. Deleting shared simulation helpers would push
timeline, pending-order, skip-audit, broker-sync, and snapshot behavior back into
daily and intraday loops.

Complexity to move behind the seam:

- Durable backtest-run lifecycle
- Orphan reconciliation
- Parent run metadata
- Manifest-on-failure policy
- Pending-order lifecycle
- Fill/rejection accounting
- Skipped-order audit rows
- Backtest equity snapshot policy

ADR conflicts:

None if ADR 0021, ADR 0030, ADR 0053, ADR 0011, and ExecutionService routing
remain intact.

Tests:

Survive:

- OOS aggregate behavior
- Parent-run creation
- Explanation ancestry
- Failed-run metadata
- Daily golden regressions
- Intraday session-boundary fills
- Stranded pending orders
- Risk-policy behavior

Replace or delete later:

- Tests/stubs that fake private engine attributes
- Tests that assert private helper shape instead of behavior across the
  backtesting interface

Confidence: High.

## Dependency and Testing Implications

No remote-owned dependencies were found in these audit areas.

Most candidates are in-process or local-substitutable, so they are good
deepening targets. True external dependencies only matter for broker/data
readiness checks and should remain behind adapters.

Testing implications:

- Move tests toward module interface behavior and away from private helper/cache
  assertions.
- Preserve policy invariants, CLI/Bench parity outcomes, QML forbidden-token
  tests, strategy signal tests, and backtest golden behavior.
- Replace CLI-adapter monkeypatch tests in promotion evidence.
- Replace duplicated per-state Qt lifecycle tests after a shared polling module
  exists.
- Replace private backtest helper tests after behavior is covered through the
  backtesting interface.
- Replace repeated per-strategy common-flow tests after the daily
  cross-sectional flow has its own interface-level tests.

## Maybe Later Items

### AUDIT-006 - GUI Polling Adapter and Projection Locality

Files:

- [`src/milodex/gui/read_models.py`](../../../src/milodex/gui/read_models.py)
- adjacent `src/milodex/gui/*_state.py` modules

This is worthwhile but less urgent than correctness/audit candidates.
`read_models.py` has a reusable `_PollingReadModel`, while adjacent state modules
repeat Qt adapter lifecycle logic. Bench row projection, evidence packet
projection, action intent preview, and ledger construction also share one large
implementation surface.

The module passes the deletion test, but the immediate risk is lower than
AUDIT-001 through AUDIT-005. Treat it as cleanup, not a first-order launch safety
issue.

### AUDIT-007 - Bench Qt Bridge Internal Repetition

Files:

- [`src/milodex/gui/bench_command_bridge.py`](../../../src/milodex/gui/bench_command_bridge.py)

The bridge is a real adapter seam and should stay. Internal action-specific slot
handling and unknown-proposal behavior are repetitive, and tests sometimes inspect
private proposal cache state. This is a reasonable cleanup after higher-risk
auditability work, not a first-order launch safety issue.

## Rejected Options

### AUDIT-008 - Stale Phase/Doc Prose Cleanup as Architecture Candidate

Stale comments and docs should be fixed, especially where `bench.py` still
describes an older inert Bench phase while all six action families are wired.
But deleting stale prose removes complexity rather than moving it behind a seam.
It is not a deepening opportunity.

Additional rejected options:

- Do not replace `EventStore` or SQLite. ADR 0011 is aligned with the product.
- Do not remove the Bench facade external seam. ADR 0051 wants it.
- Do not introduce ports where there is only one real adapter.
- Do not bypass `ExecutionService`; it is protected depth.
- Do not broaden this into a GUI rewrite.

## Recommended Next Candidates

Execution order now lives in the roadmap, which records completed safety slices
and dependencies. At the time of this cleanup pass, the current ready item is
**RM-009 - Stale architecture prose cleanup**; higher-priority Bench workflow
readiness work remains queued behind its roadmap status.

Historical audit priorities remain useful context, but agents should not use
this section as the pickup surface. Use
`docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md` for the next item.

## Open Questions

- Is launch scope blocked by missing ADR 0051 workflow-readiness checks?
- Should `BENCH_BOUNDARY.md` be updated before or after code changes?
- Should the next pass explicitly decide whether AUDIT-001 and AUDIT-002 are one
  promotion/command-readiness program or two separate refactors?

## Suggested Next Prompt

```text
Read docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md, select the highest-priority item whose status is ready and whose dependencies are done, and implement exactly that roadmap slice. Preserve each item’s implementation scope and validation commands; do not infer current execution order from this historical audit summary.
```
