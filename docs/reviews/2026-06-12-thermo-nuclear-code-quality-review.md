# Thermo-Nuclear Code Quality Review Findings

Date: 2026-06-12
Scope: entire Milodex project, read-only review
Method: orchestrated review using the thermo-nuclear code quality review lens, with
parallel subsystem reviewers and local cross-checks for high-impact findings.

No files were changed during the review. No tests were run as part of the review.

## How To Use This Report

This report is structured for follow-on LLM reviewers. Work one finding at a
time. For each finding:

1. Read the claim, evidence, and suggested remedy.
2. Run the verification steps or inspect the referenced files.
3. Set `Reviewer status` to one of: `blank`, `in work`, `resolved`, `dismissed`.
4. Fill in reviewer, date, verification notes, and resolution reference.

Blank status is intentional: it means the finding has not yet been adjudicated.

## Status Index

Adjudicated 2026-06-12 (PM session, 9 parallel code-grounded verification agents; every
referenced file opened, claims checked against real line numbers, cheap verification
steps run). Status vocabulary extended: `verified` (claim accurate), `verified
(overstated)` (real issue, severity/framing inflated or already mitigated),
`dismissed` (claim wrong, non-issue, or documented deliberate design).

Resolution pass 2026-06-12 (same session): every verified finding with an actionable
recommendation was fixed and merged to master as PRs #231–#245 (15 PRs). See the
Resolution Log at the end of this document for the finding → PR map. The only
verified findings without a dedicated PR: P2-11 (deliberate opportunistic-decompose
deferral) and P2-17 (already tracked as a prerequisite of the deferred crypto
data-ingestion task).

| ID | Severity | Area | Title | Reviewer status |
| --- | --- | --- | --- | --- |
| P1-01 | P1→P2 | Risk / Promotion | Risk allows backtest strategies to submit paper orders | resolved (#231) |
| P1-02 | P1 | Execution / Audit | Broker submission happens before durable audit/outbox state | resolved (#236) |
| P1-03 | P1 | Backtesting / Evidence | Backtests can be marked completed before final metadata persists | resolved (#233) |
| P1-04 | P1→P2 | Promotion / Config | Promotion and demotion can durably write DB state, then fail YAML rewrite | resolved (#239) |
| P2-01 | P2→P3 | Bench / Promotion | Bench transitions idle to backtest after completion, not acceptance | resolved (#240) |
| P2-02 | P2→P3 | Backtesting / Analytics | Final backtest equity snapshots are best-effort silent | resolved (#233) |
| P2-03 | P2 | Risk / Strategy Config | risk.stop_loss_pct ownership is split and partly dead | resolved (#232) |
| P2-04 | P2→P3 | Strategies / Config | Strategy parameter contracts are too thin and validated late | resolved (#245) |
| P2-05 | — | Promotion Policy | lifecycle_exempt is caller-controlled instead of policy-resolved | dismissed |
| P2-06 | P2→P3 | Risk Profile | Active risk profile is split between audited service and unaudited file runtime | resolved (#238) |
| P2-07 | P2 | Strategy Safety | Disable-condition halts are specified but not implemented | resolved (#242, implemented) |
| P2-08 | P2 | Data / Reproducibility | Universe-manifest policy contradicts inline universes | resolved (#232) |
| P2-09 | — | Commands / Architecture | BenchCommandFacade has become a policy surface | dismissed |
| P2-10 | P2 | Event Store / Risk | Reconciliation and risk attribution leak raw event-store SQL | resolved (#237) |
| P2-11 | P2→P3 | Operations | Reconciliation mixes folding, mutation, readiness, incidents, and rendering | verified (overstated) — decompose when next touched |
| P2-12 | P2 | GUI / Bench | QML modal owns command semantics that Python already owns | resolved (#243) |
| P2-13 | P2→P3 | GUI Tests | GUI source-string tests lock implementation instead of behavior | resolved (#243) |
| P2-14 | P2→P3 | GUI Registry | QML registry has two canonical encodings | resolved (#244) |
| P2-15 | P2→P3 | GUI Lifecycle | BenchCommandBridge calls private read-model refresh methods | resolved (#244) |
| P2-16 | — | GUI Read Models | Dashboard read ownership is fragmented across many independent pollers | dismissed |
| P2-17 | P2→tracked | Data Cache | ParquetCache cannot safely key slash symbols like BTC/USD | verified (overstated) — tracked on crypto ingestion task |
| P2-18 | P2→P3 | Scripts / Governance | One-off scripts can mutate governance state outside governed commands | resolved (#235) |
| P2-19 | P2→P3 | Scripts / Promotion | Counterfactual gate duplicates promotion policy | resolved (#235) |
| P2-20 | — | CLI Rendering | Plain/rich/report formatting can drift | dismissed |
| P2-21 | P2 | CLI Evidence | Missing data-quality evidence is reported as pass | resolved (#234) |
| P2-22 | P2→P3 | CLI Config | CLI config validation keeps stale dead schema constants | resolved (#234) |
| P3-01 | — | Core / Persistence | EventStore is a cross-domain god object | dismissed |
| P3-02 | P3 | Operations Policy | Data-freshness threshold is duplicated | resolved (#234) |
| P3-03 | P3 | Backtesting | Daily and intraday backtest loops duplicate orchestration choreography | resolved (#241) |
| P3-04 | P3 | GUI Compatibility | gui/read_models.py compatibility shim still exports private helpers | resolved (#244) |

## Findings

### P1-01: Risk allows `backtest` strategies to submit paper orders

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend fix-now (tiny PR)

Claim: The risk layer allows a strategy at `stage: backtest` to submit a paper
order, even though the promotion pipeline says paper-mode execution requires
`stage: paper`.

Evidence:

- `RiskEvaluator._check_strategy_stage` accepts `{"backtest", "paper"}`:
  [src/milodex/risk/evaluator.py:230](../../src/milodex/risk/evaluator.py#L230)
- The canonical stage compatibility table allows only `paper` for paper mode:
  [src/milodex/promotion/stage_compat.py:22](../../src/milodex/promotion/stage_compat.py#L22)
- SRS says `stage: backtest` cannot submit paper orders:
  [docs/SRS.md:288](../SRS.md#L288)

Why it matters: The risk layer is the sacred boundary. If direct execution
service calls can bypass the promotion evidence boundary, then the CLI guard is
doing policy enforcement that belongs in risk.

Suggested remedy: Replace the hardcoded set in `_check_strategy_stage` with the
canonical stage/mode policy, or move that policy into risk and make every caller
consume it. Paper submit should require effective stage `paper`.

Verification steps:

- Add or run a risk-layer test with `stage="backtest"`, `trading_mode="paper"`,
  and `preview_only=False`.
- Expected fixed behavior: risk refuses with `strategy_stage_ineligible`.
- Review `tests/milodex/execution/test_service.py` around the existing stage
  eligibility tests and ensure the backtest-stage paper path is covered.

Reviewer notes:

```text
Core claim VERIFIED: evaluator.py:230 accepts {"backtest", "paper"} while the
canonical table (stage_compat.py:22) allows only "paper" — the risk layer is
looser than promotion policy. OVERSTATED on exposure: the CLI launch path
enforces ALLOWED_STAGES_BY_MODE (cli/commands/strategy.py:102) and bench runner
preflight does too (commands/bench.py:1101), so no production path reaches
submit_paper with a backtest-stage strategy today. Real gaps: the
stage-ineligibility test covers only stage="live" (test_service.py:570-601) —
no test for backtest-stage paper rejection — and project doctrine says the
risk layer, not the CLI, must be the final arbiter. Effective severity P2, but
fix anyway because the fix is one line + one test on the sacred layer:
tighten _check_strategy_stage to consume stage_compat and add the test.
```

### P1-02: Broker submission happens before durable audit/outbox state

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend fix-now (small PR)

Claim: `ExecutionService.submit_paper` submits to the broker before it writes a
durable attempt/audit row. The audit record itself is split across two separate
event-store commits.

Evidence:

- Broker submission happens before audit recording:
  [src/milodex/execution/service.py:146](../../src/milodex/execution/service.py#L146)
- Successful result is recorded after broker return:
  [src/milodex/execution/service.py:187](../../src/milodex/execution/service.py#L187)
- `_record_execution` appends explanation, then trade:
  [src/milodex/execution/service.py:571](../../src/milodex/execution/service.py#L571),
  [src/milodex/execution/service.py:626](../../src/milodex/execution/service.py#L626)
- `EventStore.append_explanation` and `EventStore.append_trade` are separate
  methods/transactions:
  [src/milodex/core/event_store.py:358](../../src/milodex/core/event_store.py#L358),
  [src/milodex/core/event_store.py:456](../../src/milodex/core/event_store.py#L456)

Why it matters: A database failure after broker success can create an invisible
broker order. A failure between explanation and trade can create orphan audit
state. Both damage duplicate-order checks, reconciliation, and trust
reconstruction.

Suggested remedy: Introduce a durable execution attempt/outbox row before broker
submission, with an idempotency key or client order ID. Update that row with the
final broker outcome. Add an event-store method that writes explanation plus
trade atomically.

Verification steps:

- Force broker success followed by `append_trade` failure.
- Expected fixed behavior: there is still a durable pending/submitted attempt
  with enough data to reconcile and dedupe.
- Verify explanation and trade cannot be partially written for a final submit
  outcome.

Reviewer notes:

```text
VERIFIED in full. Broker submit (service.py:146) precedes any durable row;
explanation and trade are separate commits (event_store.py append_explanation
/ append_trade each open and commit their own connection); the schema has no
client_order_id / idempotency key / attempt table (checked migrations). The
sharpest consequence the report under-sold: the duplicate-order risk check
(risk/evaluator.py count_recent_submitted_orders) queries only `trades`, so a
DB failure after broker success defeats dedup exactly when it is needed.
Mitigation: reconciliation sync_local_only_orders (reconciliation.py:382)
recovers, but manually. Paper-only operation softens blast radius today; this
must be closed before any live capital. Fix: durable attempt row (with client
order id) before submit, updated with broker outcome, plus an atomic
explanation+trade write. P1 stands.
```

### P1-03: Backtests can be marked completed before final evidence metadata persists

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend fix-now (small PR)

Claim: Backtest and walk-forward runs can be durably marked `completed` before
the final metadata blob is persisted.

Evidence:

- Walk-forward closeout updates status, then metadata:
  [src/milodex/backtesting/engine.py:395](../../src/milodex/backtesting/engine.py#L395),
  [src/milodex/backtesting/engine.py:400](../../src/milodex/backtesting/engine.py#L400)
- Single backtest closeout updates status, then metadata:
  [src/milodex/backtesting/engine.py:482](../../src/milodex/backtesting/engine.py#L482),
  [src/milodex/backtesting/engine.py:487](../../src/milodex/backtesting/engine.py#L487)
- Event-store status and metadata updates are separate commits:
  [src/milodex/core/event_store.py:1348](../../src/milodex/core/event_store.py#L1348),
  [src/milodex/core/event_store.py:1409](../../src/milodex/core/event_store.py#L1409)

Why it matters: `backtest_runs.metadata_json` is part of the evidence surface.
A terminal `completed` row without full metadata can bypass orphan recovery
because the row is no longer `running`.

Suggested remedy: Add one atomic event-store method that writes terminal status,
`ended_at`, and final metadata in a single transaction. Prefer fail-closed
behavior over completed-but-partial evidence.

Verification steps:

- Monkeypatch `update_backtest_run_metadata()` to raise after simulation
  succeeds.
- Run `BacktestEngine.run()` and `run_walk_forward()`.
- Expected fixed behavior: no terminal completed row exists without final
  metadata, or the row records an explicit failure/partial state.

Reviewer notes:

```text
VERIFIED in full. Both closeout sites (engine.py walk-forward ~395-400 and
single-run ~482-501) call update_backtest_run_status then
update_backtest_run_metadata as two separate commits. The orphan sweep filters
status='running' ("Terminal-status rows are never swept" per event_store.py
comment), so a completed-without-metadata row is both possible and invisible
to recovery. metadata_json is the sole home of the evidence metrics (no
redundancy). Fix: one atomic event-store method writing status + ended_at +
metadata in a single transaction, used at both closeout sites, with a
fail-closed test (metadata write raises → row stays 'running'). P1 stands;
pairs naturally with P1-02 in an evidence-atomicity PR.
```

### P1-04: Promotion and demotion can durably write DB state, then fail YAML rewrite

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (small PR)

Claim: Promotion and demotion are documented as atomic but can commit durable DB
state before the YAML stage update fails.

Evidence:

- `transition()` documents "Atomic promotion":
  [src/milodex/promotion/state_machine.py:149](../../src/milodex/promotion/state_machine.py#L149)
- Manifest plus promotion are committed before YAML stage rewrite:
  [src/milodex/promotion/state_machine.py:215](../../src/milodex/promotion/state_machine.py#L215)
- YAML stage matcher rejects some valid human-edited forms, such as trailing
  inline comments:
  [src/milodex/promotion/state_machine.py:354](../../src/milodex/promotion/state_machine.py#L354)

Why it matters: The operator can receive an error while a promotion row and
active manifest already exist. Retry can duplicate governance events or leave
YAML/runtime drift.

Suggested remedy: Precompute and validate the YAML rewrite before any DB write.
Longer term, make event-store stage state authoritative and treat YAML stage as
an input or generated cache instead of a second mutable source of truth.

Verification steps:

- Add a test for a stage line like `stage: "backtest" # comment`.
- Add a simulated YAML write failure after promotion DB commit.
- Expected fixed behavior: either no durable rows are written, or the result is
  an explicit partial-success object with repair references.

Reviewer notes:

```text
Structurally VERIFIED, severity OVERSTATED. DB-first ordering is a deliberate
outbox pattern, and the failure mode is loud, not silent: the YAML matcher
raises ValueError whose message explicitly states "Durable state is written...
the next cycle's drift check will flag this discrepancy"
(state_machine.py:360-365), and the risk layer's manifest drift check refuses
execution on YAML/manifest divergence — so a desync cannot silently trade.
The inline-comment rejection is real (the regex `$` anchor rejects trailing
comments) but no shipped config has one and the result is the loud error, not
corruption. Real gaps: docstring says "Atomic" (it isn't), no YAML-failure
test, no structured partial-success result. Effective severity P2. Fix:
precompute/validate the YAML rewrite before any DB write, correct the
docstring, add the failure-injection test.
```

### P2-01: Bench transitions `idle` to `backtest` after completion, not acceptance

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (small PR)

Claim: Bench performs the system-driven `idle -> backtest` transition after the
backtest result is available, even though ADR 0050 says the transition occurs
when the backtest job is accepted/created.

Evidence:

- ADR 0050 locks acceptance-time semantics:
  [docs/adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md:107](../adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md#L107)
- Bench creates the orchestration job before the run:
  [src/milodex/commands/bench.py:1386](../../src/milodex/commands/bench.py#L1386)
- Bench updates YAML and appends `stage_return` only after the run result:
  [src/milodex/commands/bench.py:1433](../../src/milodex/commands/bench.py#L1433),
  [src/milodex/commands/bench.py:1450](../../src/milodex/commands/bench.py#L1450)

Why it matters: The read model can show an active accepted backtest while the
strategy remains at `idle`, which is exactly the stale state ADR 0050 tries to
avoid.

Suggested remedy: Create a canonical system-driven stage-return helper and call
it when the job is accepted/created. Keep backtest evidence completion separate.

Verification steps:

- Trigger `Initiate Backtest` on an idle strategy.
- Expected fixed behavior: stage-return governance state is written as part of
  job acceptance, before simulation completion.
- Confirm backtest failure does not roll back the acceptance-time stage return
  unless there is an explicit compensating governance event.

Reviewer notes:

```text
VERIFIED as an ADR deviation, OVERSTATED on impact. ADR 0050 Decision 6 does
lock acceptance-time semantics, and bench writes the stage_return only after
the run result (bench.py ~1450-1468), violating the letter of the ADR. But
submit_backtest is synchronous — the read-model refresh happens after both the
run and the event write — so the "stale read model shows accepted backtest
while strategy stays idle" hazard the ADR targets is not operator-observable;
the window exists only for a concurrent reader mid-call. Fix is pure code
motion (write the governance event at job creation). Backlog it; do it before
or with the ADR 0050 v2 freshness-computation work, which will need correct
acceptance timing.
```

### P2-02: Final backtest equity snapshots are best-effort silent

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (tiny PR)

Claim: The engine presents final `backtest_equity_snapshots` as a standard
simulation artifact, but snapshot write failures are swallowed silently.

Evidence:

- Engine comments describe recording one snapshot per simulation:
  [src/milodex/backtesting/engine.py:1036](../../src/milodex/backtesting/engine.py#L1036)
- Snapshot writer is labeled best-effort:
  [src/milodex/backtesting/simulation_kernel.py:518](../../src/milodex/backtesting/simulation_kernel.py#L518)
- Catch-all exception handler silently passes:
  [src/milodex/backtesting/simulation_kernel.py:528](../../src/milodex/backtesting/simulation_kernel.py#L528)

Why it matters: Completed runs can lack an analytics artifact without any
visible degraded status or metadata warning.

Suggested remedy: Either make final snapshot persistence required for successful
completion, or persist an explicit warning/error in run metadata when snapshot
writing fails.

Verification steps:

- Monkeypatch `record_backtest_equity_snapshot` to raise.
- Run a minimal backtest.
- Expected fixed behavior: run fails, or completed run metadata contains a clear
  snapshot persistence warning.

Reviewer notes:

```text
VERIFIED that the except swallows silently (simulation_kernel.py ~519-529, no
logging), OVERSTATED on "without any visible degraded status": analytics
already surfaces zero-snapshot strategies as an explicit open question in
trust reports ("No portfolio snapshots recorded... reconstructed from the
trade ledger only", analytics/reports.py ~202), and equity is reconstructable
from the trade ledger — snapshots are an optimization, not the sole evidence.
Remaining real gap: a write failure is indistinguishable from a legitimately
empty simulation, and there is no forensic trail. Fix (tiny): log the
exception and record a snapshot_write_error flag in run metadata. Do not make
snapshot persistence fail the run.
```

### P2-03: `risk.stop_loss_pct` ownership is split and partly dead

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (small PR)

Claim: Stop-loss ownership is split between strategy parameters and `risk`,
while the risk-side field is loaded but not enforced as a risk rule.

Evidence:

- Risk config loads `stop_loss_pct` into execution config:
  [src/milodex/execution/config.py:27](../../src/milodex/execution/config.py#L27)
- Strategy configs commonly mirror `parameters.stop_loss_pct` and
  `risk.stop_loss_pct`.
- The repository guide already calls the related validation/config trap out:
  [CLAUDE.md:88](../../CLAUDE.md#L88)
- Prior review notes also identify `risk.stop_loss_pct` as dead plumbing:
  [docs/reviews/2026-06-10-runner-process-audit.md:143](2026-06-10-runner-process-audit.md#L143)

Why it matters: Operators and contributors can reasonably believe the risk
layer enforces the stop, while the effective stop is strategy self-discipline at
bar cadence.

Suggested remedy: Pick one canonical owner. If stop loss is risk policy,
enforce it in risk as an envelope and cross-check strategy parameters. If it is
strategy signal logic, remove it from required risk schema/configs and docs.

Verification steps:

- Run `rg "stop_loss_pct" src/milodex/risk`.
- Add a test proving either risk-layer enforcement exists, or configs cannot
  declare misleading `risk.stop_loss_pct`.

Reviewer notes:

```text
VERIFIED. rg "stop_loss_pct" src/milodex/risk → zero hits: the risk layer
enforces nothing; real stops are strategy bar-cadence self-discipline via
parameters.stop_loss_pct. execution/config.py loads risk.stop_loss_pct into a
dataclass field nothing reads. Mitigation since the 2026-06-10 runner audit
(which flagged the same thing): loader cross-check (loader.py ~236-257) fails
the load when parameters. and risk. values diverge, so silent disagreement is
impossible. Still a contributor/operator trap — the config implies risk-layer
enforcement that does not exist. Fix: drop risk.stop_loss_pct from required
keys + dead field, document bar-cadence stop semantics in RISK_POLICY.md.
```

### P2-04: Strategy parameter contracts are too thin and validated late

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (decent PR)

Claim: Strategy parameter schemas only validate presence/null/type at load time;
ranges, enums, and cross-field invariants are reimplemented in each strategy and
fail during evaluation.

Evidence:

- `StrategyParameterSpec` only captures name/type/required/nullability:
  [src/milodex/strategies/base.py:16](../../src/milodex/strategies/base.py#L16)
- Loader validation checks only basic shape:
  [src/milodex/strategies/loader.py:307](../../src/milodex/strategies/loader.py#L307)
- Repeated per-strategy `_validated_parameters` logic exists, for example:
  [src/milodex/strategies/meanrev_rsi2_pullback.py:123](../../src/milodex/strategies/meanrev_rsi2_pullback.py#L123),
  [src/milodex/strategies/momentum_daily_tsmom.py:125](../../src/milodex/strategies/momentum_daily_tsmom.py#L125)

Why it matters: Milodex is config-driven. Invalid configs should fail at the
loading or promotion boundary, not during a runner cycle or backtest simulation.

Suggested remedy: Extend the parameter spec with reusable constraints, enums,
min/max, default/coercion rules, and relation validators; or introduce
per-strategy typed parameter objects produced by the loader.

Verification steps:

- Create a YAML with valid types but invalid cross-field values, such as
  `rsi_entry_threshold >= rsi_exit_threshold`.
- Expected fixed behavior: loader rejects it before market data or evaluation.

Reviewer notes:

```text
VERIFIED that StrategyParameterSpec stops at name/type/required/null and that
strategies reimplement range/enum/cross-field checks per-class. OVERSTATED on
"validated late": type and presence violations DO fail at load time via the
loader; only range/enum/relation violations surface at first evaluation —
which for a config change means the first backtest bar, not a live runner
cycle with capital at stake. Quality-of-life gap, not a safety gap (the
promotion gate, not config load, is the capital boundary). Fix when touched
next: extend the spec with min/max/enum/relation validators applied in
validate_strategy_parameters. Backlog.
```

### P2-05: `lifecycle_exempt` is caller-controlled instead of policy-resolved

Reviewer status: dismissed
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: documented deliberate deferral (ADR 0052 §7) — no action

Claim: Lifecycle-exempt promotion is passed as a raw boolean from callers,
rather than being resolved by a policy model that knows which strategies are
eligible.

Evidence:

- `check_gate()` bypasses thresholds when `lifecycle_exempt=True`:
  [src/milodex/promotion/state_machine.py:119](../../src/milodex/promotion/state_machine.py#L119)
- CLI exposes a lifecycle-exempt flag through promotion flow:
  [src/milodex/cli/commands/promotion.py:208](../../src/milodex/cli/commands/promotion.py#L208)
- Bench proposal/submit code also passes the boolean:
  [src/milodex/commands/bench.py:730](../../src/milodex/commands/bench.py#L730),
  [src/milodex/commands/bench.py:1598](../../src/milodex/commands/bench.py#L1598)

Why it matters: The caller owns the exemption decision. That weakens the policy
owner boundary and can allow non-lifecycle strategies to skip statistical
evidence gates.

Suggested remedy: Replace the boolean with a typed promotion class resolved from
strategy metadata/registry and operational evidence. Reject lifecycle exemption
unless the strategy is explicitly lifecycle-proof.

Verification steps:

- Add tests that a non-regime strategy with `lifecycle_exempt=True` is blocked.
- Add tests that lifecycle exemption without required operational evidence is
  blocked.

Reviewer notes:

```text
DISMISSED — documented deliberate design, not a defect. ADR 0052 §7 ("Known
gap") explicitly states the check_gate lifecycle-exempt branch returning
allowed=True unconditionally is "a deliberate scope decision, not an
oversight"; the lifecycle gate exists as a typed concept
(LifecycleGateDefinition, enforced=False) in policy.py as a contract for the
deferral. CLAUDE.md documents --lifecycle-exempt as an operator-override
mechanism. Gate thresholds themselves are NOT caller-controlled. The review's
remedy (policy-resolved exemption from strategy metadata) is the already-
planned future work, not a fix. Optional nicety: a comment in check_gate
pointing at ADR 0052 §7.
```

### P2-06: Active risk profile is split between audited service and unaudited file runtime

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (small PR)

Claim: Runtime enforcement reads `data/risk_profile.txt` directly, while a
separate activation service writes audit rows. Direct file edits can change
runtime behavior without an audit event.

Evidence:

- Runtime reads the active profile file:
  [src/milodex/risk/config.py:165](../../src/milodex/risk/config.py#L165)
- Unknown profiles fall back with a warning only:
  [src/milodex/risk/config.py:298](../../src/milodex/risk/config.py#L298)
- Activation service writes the file then audit row:
  [src/milodex/risk/profile_activation.py:113](../../src/milodex/risk/profile_activation.py#L113)
- Audit schema allows context mode:
  [src/milodex/core/migrations/011_risk_profile_changes.sql:10](../../src/milodex/core/migrations/011_risk_profile_changes.sql#L10)
- Profile activation currently hardcodes paper context:
  [src/milodex/risk/profile_activation.py:255](../../src/milodex/risk/profile_activation.py#L255)

Why it matters: Risk-policy changes must be explicit, human-approved, logged,
and reviewable. A file edit path bypasses that model.

Suggested remedy: Make event-store risk-profile audit state authoritative.
Treat `risk_profile.txt` as a cache or remove it. Centralize profile names and
thread actual trading mode into activation/audit.

Verification steps:

- Write `risk_profile.txt` directly to `aggressive` without a matching audit row.
- Expected fixed behavior: runtime refuses, records an incident, or falls back
  without changing enforcement until audited.

Reviewer notes:

```text
Split VERIFIED, threat OVERSTATED. The decisive fact the report omitted:
unknown/invalid profile names fall back to CONSERVATIVE — the safest profile —
with a warning (risk/config.py ~300-308); the failure direction is toward
tighter limits, never looser. The file-as-activation-mechanism with audit
alongside is ADR 0054's documented design, and the threat actor here is the
solo operator hand-editing his own file to bypass his own audit row — not a
meaningful boundary for a single-operator local system. The real residual
risk is accidental drift between file and audit trail, which today yields only
a log line. Fix (small): startup/periodic reconcile of risk_profile.txt vs
the latest risk_profile_changes audit row, surfacing divergence as a WARN or
soft incident. Do not make the file authoritative-or-refused; the
conservative fallback is the right default.
```

### P2-07: Disable-condition halts are specified but not implemented

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — decision needed: implement (decent PR) or mark deferred in docs (tiny PR)

Claim: Requirements promise strategy-family default `disable_conditions` and
risk-layer halt behavior, but implementation only validates additional strings
and carries them in strategy context.

Evidence:

- SRS requirement:
  [docs/SRS.md:170](../SRS.md#L170)
- Strategy family docs mention disable conditions:
  [docs/strategy-families.md:86](../strategy-families.md#L86)
- Loader handles additional disable-condition strings:
  [src/milodex/strategies/loader.py:265](../../src/milodex/strategies/loader.py#L265)
- Strategy context carries disable conditions:
  [src/milodex/strategies/base.py:45](../../src/milodex/strategies/base.py#L45)
- Requirements coverage marks the item incomplete:
  [docs/REQUIREMENTS_COVERAGE.md:81](../REQUIREMENTS_COVERAGE.md#L81)

Why it matters: Docs overstate a safety boundary. Operators may assume strategy
disable conditions halt intent generation when they do not.

Suggested remedy: Either mark this requirement deferred everywhere, or implement
canonical family defaults, merge config additions, evaluate active conditions in
risk/runner, and write explanation records.

Verification steps:

- Run `rg -n "disable_conditions" src/milodex/risk src/milodex/execution src/milodex/backtesting src/milodex/strategies`.
- Add tests for default-removal refusal and triggered halt behavior.

Reviewer notes:

```text
VERIFIED. SRS R-STR-014 says the risk layer "shall halt" on active disable
conditions; rg confirms zero disable_conditions hits in risk/, execution/, or
backtesting/ — the plumbing (parse, validate, carry in StrategyContext) exists
but the halt does not. REQUIREMENTS_COVERAGE.md already marks R-STR-014 at
zero tests, so the gap is tracked, but SRS and strategy-families.md read as
shipped behavior with no "deferred" marker — that is the genuine defect: docs
overstate a safety boundary. Half-built plumbing is itself a trap ("the
wiring is there, it must work"). Decision for the operator: implement the
halt (family-default catalog + risk-layer evaluation + explanation records;
decent PR) or mark R-STR-014 deferred in SRS + strategy-families now (tiny).
The doc fix should not wait on the decision.
```

### P2-08: Universe-manifest policy contradicts inline universes

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend fix-now (tiny doc PR: amend SRS)

Claim: SRS says strategies shall not inline symbol lists, but the loader accepts
inline `universe` and docs describe inline behavior.

Evidence:

- SRS requirement:
  [docs/SRS.md:103](../SRS.md#L103)
- Loader accepts exactly one of inline `universe` or `universe_ref`:
  [src/milodex/strategies/loader.py:418](../../src/milodex/strategies/loader.py#L418),
  [src/milodex/strategies/loader.py:427](../../src/milodex/strategies/loader.py#L427)
- Run manifest has no universe manifest hash when `universe_ref` is absent:
  [src/milodex/backtesting/run_manifest.py:81](../../src/milodex/backtesting/run_manifest.py#L81)
- Risk docs describe inline behavior:
  [docs/RISK_POLICY.md:209](../RISK_POLICY.md#L209)

Why it matters: Reproducibility and audit guarantees are ambiguous. A reviewer
cannot tell whether inline universes are legacy, test-only, or supported.

Suggested remedy: Choose one policy. If inline is supported, amend SRS with
named exceptions and manifest semantics. If forbidden, migrate configs and add a
validation failure test.

Verification steps:

- Run config validation against an inline-universe strategy.
- Compare result against the policy chosen in SRS and risk docs.

Reviewer notes:

```text
VERIFIED. SRS R-DAT-016 says "shall not inline a symbol list", yet three
shipped configs inline universes (spy_shy_200dma_v1.yaml:24 and both crypto
configs) and RISK_POLICY.md:209-211 documents inline-universe semantics as
supported (inline ⇒ surv_corr=no). So inline is supported-by-design for
regime/single-asset strategies and the SRS is simply wrong about its own
system. Policy contradiction, not a code bug. Fix: amend R-DAT-016 with the
qualified exception (single-asset/regime strategies may inline; manifest
required for survivorship-corrected multi-symbol universes) — do not delete
the feature. Tiny.
```

### P2-09: `BenchCommandFacade` has become a policy surface

Reviewer status: dismissed
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: no action — track file size as a separate scalability concern

Claim: `commands/bench.py` is no longer a thin orchestrator. It owns readiness,
proposal policy, runner preconditions, reconciliation actions, job journaling,
and stage mutation details.

Evidence:

- Project guide says orchestration modules should be thin:
  [CLAUDE.md:29](../../CLAUDE.md#L29)
- Bench readiness/policy starts in the facade area:
  [src/milodex/commands/bench.py:283](../../src/milodex/commands/bench.py#L283)
- Promotion proposal logic:
  [src/milodex/commands/bench.py:722](../../src/milodex/commands/bench.py#L722)
- Backtest submission:
  [src/milodex/commands/bench.py:1293](../../src/milodex/commands/bench.py#L1293)
- Orchestration job creation:
  [src/milodex/commands/bench.py:2241](../../src/milodex/commands/bench.py#L2241)
- Shared submit shell:
  [src/milodex/commands/bench.py:2501](../../src/milodex/commands/bench.py#L2501)

Why it matters: GUI command behavior can drift from CLI, governance, and runtime
layers. The file is high blast radius for future Bench actions.

Suggested remedy: Split action handlers and canonical domain services: workflow
readiness in operations, promotion proposal policy in promotion, runner preflight
in runner control, reconciliation in operations, and a small Bench adapter.

Verification steps:

- After refactor, public Bench propose/submit tests should pass without testing
  `_submit_with_config` directly.
- New action addition should not require editing a giant central branch table.

Reviewer notes:

```text
DISMISSED. Read against ADR 0051 Decision 4 ("owns no business rules of its
own; every decision is delegated"), each cited site delegates: readiness
checks call operations/risk helpers; promotion proposals route through
validate_stage_transition / check_gate / assemble_evidence_package; backtest
submission dispatches to BacktestEngine and existing promotion machinery; the
reconciliation action calls operations.reconciliation directly. No threshold
or policy decision originates in bench.py except the data-freshness constant,
which is its own finding (P3-02, verified). The mischaracterization is
orchestration-breadth read as policy-ownership. The legitimate residue: 2798
lines / 53 methods is high blast radius — a future split by action family is
worth considering as scalability work, not as an ADR violation.
```

### P2-10: Reconciliation and risk attribution leak raw event-store SQL

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (small PR: shared constants + parity test first)

Claim: Risk attribution and reconciliation reach into private event-store
connections and duplicate trade-ledger semantics.

Evidence:

- Risk attribution reaches into `_connect()`:
  [src/milodex/risk/attribution.py:299](../../src/milodex/risk/attribution.py#L299),
  [src/milodex/risk/attribution.py:376](../../src/milodex/risk/attribution.py#L376)
- Risk mirrors reconciliation constants because of dependency tangles:
  [src/milodex/risk/attribution.py:49](../../src/milodex/risk/attribution.py#L49)
- Reconciliation owns similar constants:
  [src/milodex/operations/reconciliation.py:46](../../src/milodex/operations/reconciliation.py#L46)
- Reconciliation also reaches private SQL:
  [src/milodex/operations/reconciliation.py:948](../../src/milodex/operations/reconciliation.py#L948)

Why it matters: Schema semantics leak into risk and operations. Trade status or
source changes must be patched in multiple places.

Suggested remedy: Extract a typed `TradeLedger` or read-model module in `core`
that owns submitted-fill queries, status constants, order-id folding, and
per-strategy balances. Risk and reconciliation should depend on that interface.

Verification steps:

- Run `rg "_connect\\(" src/milodex/risk src/milodex/operations`.
- Expected fixed behavior: no raw private connection use outside repository
  layers.
- Add parity tests proving attribution and reconciliation fold the same fixture
  identically.

Reviewer notes:

```text
VERIFIED. _connect() reach-ins confirmed (attribution.py:299,376;
reconciliation.py:948, each tagged noqa SLF001) and
POSITION_AFFECTING_STATUSES is duplicated between the two modules — the
attribution copy carries an explicit comment that the duplication breaks a
circular dependency, and ADR 0029 left this as an open implementation
question, so it is conscious debt rather than carelessness. Values match
today; NO parity test exists to catch drift (grep of tests: zero hits).
Cheapest risk reduction first: shared status-constants module + a parity test,
small PR. The fuller TradeLedger read-model extraction is real but should
follow the P1-02 execution-atomicity work, which will reshape the same
tables.
```

### P2-11: Reconciliation mixes folding, mutation, readiness, incidents, and rendering

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (decompose when next touched)

Claim: `operations/reconciliation.py` owns too many operational concepts in one
module.

Evidence:

- Main reconciliation flow:
  [src/milodex/operations/reconciliation.py:182](../../src/milodex/operations/reconciliation.py#L182)
- Position correction:
  [src/milodex/operations/reconciliation.py:270](../../src/milodex/operations/reconciliation.py#L270)
- Local-only order sync:
  [src/milodex/operations/reconciliation.py:382](../../src/milodex/operations/reconciliation.py#L382)
- Readiness:
  [src/milodex/operations/reconciliation.py:534](../../src/milodex/operations/reconciliation.py#L534)
- Incident helpers:
  [src/milodex/operations/reconciliation.py:828](../../src/milodex/operations/reconciliation.py#L828)
- Human text rendering:
  [src/milodex/operations/reconciliation.py:961](../../src/milodex/operations/reconciliation.py#L961)

Why it matters: Presentation changes risk touching correction logic. R-OPS
behavior is harder to audit atomically.

Suggested remedy: Decompose into models, folds, compare, incidents, corrections,
readiness, and CLI/rich rendering modules.

Verification steps:

- Existing reconciliation tests should map to the new modules without behavior
  changes.
- CLI command modules should own human/rich output.

Reviewer notes:

```text
VERIFIED that the module is broad (1114 lines, ~37 functions: fold, compare,
corrections, local-only sync, readiness, incidents). OVERSTATED on
"rendering": human_lines() at the cited line returns a list of strings — data
transformation; TTY/rich formatting lives in the CLI command module as it
should. The concerns it does own are tightly related (you cannot apply
corrections without owning the fold/compare semantics) and readiness is a
read-only query. Maintainability debt, not a correctness risk. Decompose
(models / folds / corrections / readiness / incidents) opportunistically the
next time R-OPS behavior changes; no standalone refactor PR warranted now.
```

### P2-12: QML modal owns command semantics that Python already owns

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (small PR)

Claim: `BenchConfirmationModal.qml` owns command-family semantics, routing,
preview fallbacks, and canonical defaults that already have Python owners.

Evidence:

- QML hardcodes submit-capable kinds:
  [src/milodex/gui/qml/Milodex/components/BenchConfirmationModal.qml:80](../../src/milodex/gui/qml/Milodex/components/BenchConfirmationModal.qml#L80)
- QML top-level submit dispatcher:
  [src/milodex/gui/qml/Milodex/components/BenchConfirmationModal.qml:565](../../src/milodex/gui/qml/Milodex/components/BenchConfirmationModal.qml#L565)
- QML action-kind fallback:
  [src/milodex/gui/qml/Milodex/components/BenchConfirmationModal.qml:682](../../src/milodex/gui/qml/Milodex/components/BenchConfirmationModal.qml#L682)
- Python already produces normalized action intent previews:
  [src/milodex/gui/bench_actions.py:290](../../src/milodex/gui/bench_actions.py#L290)
- Python exposes submit-capable families/backtest defaults through bridge:
  [src/milodex/gui/bench_command_bridge.py:561](../../src/milodex/gui/bench_command_bridge.py#L561)

Why it matters: Adding or changing one Bench action requires synchronized edits
across Python menu/read-model code, bridge slots, QML booleans, QML dispatch,
fallback copy, and tests.

Suggested remedy: Move action descriptors and proposal defaults into Python as
the single owner. QML should render action descriptors and call a small generic
bridge surface.

Verification steps:

- Run `rg "_submitCapableKinds|_canonicalBacktestParams|function _dispatch|function _actionKind" src/milodex/gui/qml/Milodex/components/BenchConfirmationModal.qml`.
- Expected fixed behavior: command semantics are absent or reduced to purely
  presentational bindings.

Reviewer notes:

```text
VERIFIED. Five duplicated semantic surfaces counted in the modal: the
_submitCapableKinds set, _canonicalBacktestParams defaults, the
_dispatchSubmit branch table (6 per-family functions), the _actionKind
fallback classifier (mirrors bench_actions._action_kind), and submit
eligibility. Sharpest evidence: the bridge ALREADY exposes
submitCapableActionFamilies() (bench_command_bridge.py ~758) and the QML
ignores it, keeping a static copy. Adding a 7th submit-capable family is a
5-place synchronized edit. Mitigating context: PR 13 deliberately kept bridge
socket names inline in QML for greppability — that tradeoff survives the fix.
Fix: one canonical action-family spec on the Python side consumed by bridge
and QML; descriptors render, QML stops owning the set. Small.
```

### P2-13: GUI source-string tests lock implementation instead of behavior

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (tiny PR)

Claim: Static QML source-string tests have become a structural lock on the
current implementation rather than a behavioral safety net.

Evidence:

- Tests assert future-record strings:
  [tests/milodex/gui/test_qml_load_smoke.py:933](../../tests/milodex/gui/test_qml_load_smoke.py#L933)
- Tests assert private helper declarations:
  [tests/milodex/gui/test_qml_load_smoke.py:978](../../tests/milodex/gui/test_qml_load_smoke.py#L978)
- Tests assert submit-capable map details:
  [tests/milodex/gui/test_qml_load_smoke.py:1500](../../tests/milodex/gui/test_qml_load_smoke.py#L1500)

Why it matters: Behavior-preserving UI refactors fail because strings moved, not
because the product regressed. This blocks the QML/Python boundary cleanup.

Suggested remedy: Keep source scans only for negative architectural guardrails
such as forbidden imports or unsafe QML tokens. Convert positive route/copy
contract checks to runtime QML behavior tests with fake bridge objects.

Verification steps:

- Rename an internal helper without changing behavior.
- Expected fixed behavior: source-smoke tests do not fail, while behavioral
  submit-route safety tests still catch regressions.

Reviewer notes:

```text
VERIFIED in part, OVERSTATED as a blanket claim. The suite is already split
three ways: (a) negative architectural guards (forbidden imports/tokens) —
keep, exactly what the report itself recommends; (b) doctrine/operator-copy
assertions (safety text, future-record labels) — these are behavior, the
strings ARE the product; (c) ~4-5 genuine implementation pins ("function
_actionKind(" etc., test_qml_load_smoke.py ~976-988) — the real lock.
Critically, PR 13 (commit f066282) already converted the worst cosmetic
substring loop to a behavioral test driving an instantiated modal
(test_bench_confirmation_modal_behavior.py), so runtime QML behavior testing
is demonstrably feasible in this venv. Fix is narrow: convert/remove category
(c) only. Tiny.
```

### P2-14: QML registry has two canonical encodings

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (tiny PR)

Claim: Production startup and compatibility/test registration each encode the
QML singleton registry, while both claim or depend on canonical ordering.

Evidence:

- Production registry says it is the single ordered source of truth:
  [src/milodex/gui/app.py:121](../../src/milodex/gui/app.py#L121)
- Compatibility wrapper has its own `_REGISTRY_SPEC`:
  [src/milodex/gui/qml_setup.py:133](../../src/milodex/gui/qml_setup.py#L133)
- Test exists to cross-check the duplicate sources:
  [tests/milodex/gui/test_qml_registry.py:270](../../tests/milodex/gui/test_qml_registry.py#L270)

Why it matters: Adding a read model requires editing multiple registry surfaces.
Lifecycle flags can diverge between production and helper/test registration.

Suggested remedy: Create one registry descriptor module with singleton name, QML
type, kwarg/source name, and lifecycle flag. Production startup and
`register_qml_types` should both consume it.

Verification steps:

- Run `rg "_REGISTRY_SPEC|_build_qml_registry" src/milodex/gui tests/milodex/gui/test_qml_registry.py`.
- Expected fixed behavior: there is one descriptor owner and no duplicate-source
  sync test.

Reviewer notes:

```text
VERIFIED, with the mitigation noted: both encodings exist
(app.py _build_qml_registry and qml_setup._REGISTRY_SPEC, both claiming
canonicality in their docstrings), but
test_registry_spec_and_build_qml_registry_are_in_sync compares the full
ordered (name, lifecycle) sequences and fails loudly on divergence — so
silent drift is effectively impossible; the cost is dual maintenance (two
edits per new read model) plus the contradictory "single source of truth"
claims. Low-stakes, clean fix: one descriptor module consumed by both, delete
the sync test. Tiny; bundle with the next read-model addition rather than as
a standalone PR.
```

### P2-15: `BenchCommandBridge` calls private read-model refresh methods

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (tiny PR)

Claim: `BenchCommandBridge` reaches into private polling read-model refresh
mechanics after submit.

Evidence:

- `_refresh_after_submit` documents the shutdown hazard:
  [src/milodex/gui/bench_command_bridge.py:369](../../src/milodex/gui/bench_command_bridge.py#L369)
- Bridge calls `_kick_refresh()` on read models:
  [src/milodex/gui/bench_command_bridge.py:381](../../src/milodex/gui/bench_command_bridge.py#L381),
  [src/milodex/gui/bench_command_bridge.py:386](../../src/milodex/gui/bench_command_bridge.py#L386)
- The private method is in polling lifecycle:
  [src/milodex/gui/polling_lifecycle.py:147](../../src/milodex/gui/polling_lifecycle.py#L147)

Why it matters: The command bridge is now a read-model lifecycle coordinator and
must duplicate stopped/shutdown protection.

Suggested remedy: Add a public stopped-safe `request_refresh(reason)` API to
`PollingReadModel`, or introduce a small GUI refresh coordinator/event bus.

Verification steps:

- Run `rg "_kick_refresh\\(" src/milodex/gui`.
- Expected fixed behavior: bridge does not call private read-model methods.
- Add a test where async submit completion arrives after stop and no worker
  starts.

Reviewer notes:

```text
VERIFIED mechanics, OVERSTATED hazard. _kick_refresh() itself has no
stopped-guard (polling_lifecycle.py ~147-151), but the bridge guards every
call behind its own _stopped flag and documents exactly why
(bench_command_bridge.py ~369-378); the bridge is also the documented sole
external caller. So the shutdown hazard is handled — the defect is that the
contract lives in the caller instead of the read model, which every future
caller must rediscover. Fix: public request_refresh(reason) on
PollingReadModel with the stopped-guard inside; bridge drops its noqa SLF001
calls. Tiny polish, not a bug fix.
```

### P2-16: Dashboard read ownership is fragmented across many independent pollers

Reviewer status: dismissed
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: no action — revisit only inside a deliberate read-model consolidation

Claim: Dashboard/front/bench/ledger surfaces are built by many independent
pollers and DB readers, so they can render from different database instants.

Evidence:

- Polling read models each own timers/thread pools, for example:
  [src/milodex/gui/performance_state.py:29](../../src/milodex/gui/performance_state.py#L29),
  [src/milodex/gui/risk_throughput_state.py:11](../../src/milodex/gui/risk_throughput_state.py#L11),
  [src/milodex/gui/strategy_bank_state.py:12](../../src/milodex/gui/strategy_bank_state.py#L12)
- App constructs many read models independently:
  [src/milodex/gui/app.py:473](../../src/milodex/gui/app.py#L473)

Why it matters: Cross-surface UI state is eventual and coincidental rather than
a cohesive read snapshot. Query ownership and error handling are repeated.

Suggested remedy: Introduce a dashboard snapshot/read-store owner that polls
once per tick, opens DB/cache once, builds shared projections, and fans out thin
QObjects/properties. If too large, start with central DB snapshot context and
refresh orchestration.

Verification steps:

- Instrument SQLite connection creation during GUI startup and one refresh
  cycle.
- Expected fixed behavior: one snapshot owner drives related dashboard
  projections.

Reviewer notes:

```text
DISMISSED. The facts are correct (five independent pollers, each with timer +
single-thread pool) but the consequence is theoretical: all read read-only
local SQLite (mode=ro URIs), so cross-surface skew is bounded by milliseconds
of sequential reads against a 30-second poll cadence — not perceptible, and
the surfaces show trend/funnel summaries, not tick-consistent state. The
proposed snapshot-owner refactor is a multi-day rework of five modules for no
observable benefit at this scale. Architectural tidiness, not a defect. If a
future consolidation of GUI read models happens for other reasons, fold this
idea in then.
```

### P2-17: `ParquetCache` cannot safely key slash symbols like `BTC/USD`

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: already tracked — gate of the deferred crypto data-ingestion task

Claim: The data layer accepts symbols like `BTC/USD`, but `ParquetCache` uses
the raw uppercase symbol as a filename.

Evidence:

- Cache path is `{version}/{timeframe}/{symbol.upper()}.parquet`:
  [src/milodex/data/cache.py:64](../../src/milodex/data/cache.py#L64),
  [src/milodex/data/cache.py:68](../../src/milodex/data/cache.py#L68)
- Crypto config accepts `BTC/USD`:
  [configs/momentum_crypto_ema_cross_btc_usd_1h_v1.yaml:46](../../configs/momentum_crypto_ema_cross_btc_usd_1h_v1.yaml#L46)
- Tests assert crypto configs load with `BTC/USD`:
  [tests/milodex/strategies/test_crypto_configs.py:37](../../tests/milodex/strategies/test_crypto_configs.py#L37)
- Repository guide calls this gotcha out:
  [CLAUDE.md:86](../../CLAUDE.md#L86)

Why it matters: First real cache-backed crypto ingestion will write nested paths
or fail instead of treating symbols as opaque provider keys.

Suggested remedy: Add a canonical filesystem-safe cache key function with
reverse metadata, migrate or bump cache version, and add slash-symbol tests.

Verification steps:

- Add `ParquetCache.write/read("BTC/USD", ...)` tests.
- Expected fixed behavior: no nested `BTC/` directory is created, and read/write
  round-trip works.

Reviewer notes:

```text
VERIFIED mechanics, but this is a restatement of an already-documented,
already-tracked constraint, not a new defect. CLAUDE.md carries this exact
gotcha verbatim, both crypto configs are stage: backtest only, and the
backtest path uses SimulatedDataProvider which bypasses the cache entirely —
no production path can reach the unsafe write today. The fix (filesystem-safe
cache key + cache-version bump + slash-symbol round-trip tests) is the
documented prerequisite of the deferred crypto data-ingestion task and should
land there, not as a standalone PR now. No new action.
```

### P2-18: One-off scripts can mutate governance state outside governed commands

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (tiny: archive script, write the rule down)

Claim: A one-off backfill script mutates authoritative promotion history
directly outside promotion governance flows.

Evidence:

- Script purpose and hardcoded scope:
  [scripts/backfill_pullback_rsi2_audit_gap.py:1](../../scripts/backfill_pullback_rsi2_audit_gap.py#L1)
- Script constructs promotion event/write path:
  [scripts/backfill_pullback_rsi2_audit_gap.py:91](../../scripts/backfill_pullback_rsi2_audit_gap.py#L91),
  [scripts/backfill_pullback_rsi2_audit_gap.py:107](../../scripts/backfill_pullback_rsi2_audit_gap.py#L107),
  [scripts/backfill_pullback_rsi2_audit_gap.py:145](../../scripts/backfill_pullback_rsi2_audit_gap.py#L145)

Why it matters: Scripts normalize bypassing the lifecycle/audit boundary
Milodex is designed to protect.

Suggested remedy: Move backfills behind named maintenance or migration commands
with explicit dry-run/apply modes, runtime locks, canonical backfill APIs, and
clear audit semantics. Archive or remove one-off mutation scripts after use.

Verification steps:

- Search `scripts/` for `EventStore`, `append_*`, and direct DB writes.
- Expected fixed behavior: each state-changing script is a maintenance command
  or migration wrapper with dry-run/apply and locking.

Reviewer notes:

```text
VERIFIED that the script writes promotion history directly; OVERSTATED as a
live hazard. The single offender is a forensic one-time repair with real
guardrails: idempotent (no-ops if the row exists), hardcoded strategy +
timestamp scope, --verify-only mode, and a notes field documenting the exact
audit gap it repaired. The legitimate concern is normalization — the next
backfill copying the pattern without the guardrails. Action: archive the
executed script (or move under a `milodex maintenance` namespace) and record
the rule that governance backfills require dry-run/apply, idempotency, and
hardcoded scope, or get refused. Tiny.
```

### P2-19: Counterfactual gate duplicates promotion policy

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (tiny PR: parity test)

Claim: `scripts/counterfactual_gate.py` says it imports production gate logic,
but mirrors constants and cadence policy inline.

Evidence:

- Script-local production gate constants:
  [scripts/counterfactual_gate.py:32](../../scripts/counterfactual_gate.py#L32),
  [scripts/counterfactual_gate.py:36](../../scripts/counterfactual_gate.py#L36),
  [scripts/counterfactual_gate.py:48](../../scripts/counterfactual_gate.py#L48)
- Script-local gate logic:
  [scripts/counterfactual_gate.py:204](../../scripts/counterfactual_gate.py#L204)
- Production policy lives separately:
  [src/milodex/promotion/policy.py:138](../../src/milodex/promotion/policy.py#L138)

Why it matters: Research reports can claim production parity while silently
drifting from `ACTIVE_PROMOTION_POLICY`.

Suggested remedy: Factor a pure, lightweight promotion policy evaluator and
import it from both production and scripts.

Verification steps:

- Add a parity test comparing script outcomes to production `check_gate` or the
  extracted policy evaluator.
- Expected fixed behavior: changing promotion thresholds in one place updates
  both production and counterfactual reports.

Reviewer notes:

```text
VERIFIED. Script-local constants (counterfactual_gate.py:36-38: min_sharpe
0.5, max_dd 15.0, min_trades 30) mirror PHASE1_GOVERNANCE_V1 in
promotion/policy.py; values match TODAY, nothing enforces that tomorrow. The
script's own comments admit it avoids importing the full stack because of
circular imports — research-only, so urgency is low, but a report claiming
"production parity" that can silently drift is exactly the failure mode the
finding names. Cheapest permanent fix: a parity test running the script gate
vs ACTIVE_PROMOTION_POLICY over fixture evidence; the fuller shared-evaluator
extraction can wait. Tiny.
```

### P2-20: Plain/rich/report formatting can drift

Reviewer status: dismissed
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: no action — reopen only with a concrete plain-vs-rich contradiction

Claim: Backtest/report formatting paths do not use one canonical view model, and
there is already evidence of rich/plain drift.

Evidence:

- Backtest CLI appends walk-forward confidence/warnings around:
  [src/milodex/cli/commands/backtest.py:274](../../src/milodex/cli/commands/backtest.py#L274),
  [src/milodex/cli/commands/backtest.py:309](../../src/milodex/cli/commands/backtest.py#L309)
- Rich walk-forward rendering consumes a separate view:
  [src/milodex/cli/rich_views.py:899](../../src/milodex/cli/rich_views.py#L899)
- Other output/report surfaces render similar evidence independently:
  [src/milodex/cli/commands/analytics.py:272](../../src/milodex/cli/commands/analytics.py#L272),
  [src/milodex/cli/commands/report.py:469](../../src/milodex/cli/commands/report.py#L469)

Why it matters: Operator-facing trust/evidence labels can vary by output mode.

Suggested remedy: Build one canonical backtest/report view model first, then
render JSON, plain text, and rich output from it.

Verification steps:

- Add TTY/rich snapshot tests asserting insufficient-evidence confidence appears
  in walk-forward rich output.
- Expected fixed behavior: JSON/plain/rich surfaces agree on warning and
  confidence labels.

Reviewer notes:

```text
DISMISSED. The load-bearing claim — "there is already evidence of rich/plain
drift" — did not substantiate under inspection. The one divergence found is a
deliberate, commented layout choice (walk-forward confidence rendered as an
extra-warnings panel in rich vs inline in plain; backtest.py ~309-312 says so
explicitly), and report/analytics derive confidence from a shared helper.
Same verdicts, different placement, is not drift. The structural observation
(multiple render paths, no single view model) is true of most CLIs and does
not justify a view-model refactor without a real misreport. Reopen only with
a concrete run where plain and rich state different conclusions.
```

### P2-21: Missing data-quality evidence is reported as `pass`

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend fix-now (tiny PR)

Claim: CLI backtest output silently upgrades absent data-quality evidence to
`pass`.

Evidence:

- `_data_quality_payload(None)` returns status `pass`:
  [src/milodex/cli/commands/backtest.py:334](../../src/milodex/cli/commands/backtest.py#L334),
  [src/milodex/cli/commands/backtest.py:338](../../src/milodex/cli/commands/backtest.py#L338)
- Run manifest preserves missing quality as `None`:
  [src/milodex/backtesting/run_manifest.py:118](../../src/milodex/backtesting/run_manifest.py#L118),
  [src/milodex/backtesting/run_manifest.py:121](../../src/milodex/backtesting/run_manifest.py#L121)
- Bench preserves missing status:
  [src/milodex/commands/bench.py:2439](../../src/milodex/commands/bench.py#L2439)

Why it matters: Audit-heavy output can overstate evidence quality for legacy or
partial results.

Suggested remedy: Use `unknown` or `not_recorded` for missing quality, and only
print `pass` when a scanner produced that verdict.

Verification steps:

- Add or update tests for legacy `BacktestResult(data_quality={})`.
- Expected fixed behavior: output says unknown/not recorded, not pass.

Reviewer notes:

```text
VERIFIED. _data_quality_payload(None) returns {"status": "pass",
blocker_count: 0, ...} (backtest.py ~334-343) and _data_quality_label
defaults a missing status to "pass" — so a legacy run with no scanner output
renders as if a scanner ran clean. The manifest does this correctly
(status: None preserved), making the CLI the odd one out. Scope-limiting
fact: the promotion gate does not consume this field, so no governance
decision is corrupted — it is an operator-facing audit-label defect, which is
still exactly the kind of evidence-overstatement this system is built to
avoid. Fix: "unknown"/"not recorded" for absent evidence + a legacy-result
test. Tiny.
```

### P2-22: CLI config validation keeps stale dead schema constants

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend fix-now (tiny PR: delete)

Claim: `cli/config_validation.py` contains stale strategy schema constants that
do not match real configs, even though strategy validation delegates to the real
loader.

Evidence:

- Stale `_STRATEGY_REQUIRED_KEYS` says `name` and `universe`:
  [src/milodex/cli/config_validation.py:12](../../src/milodex/cli/config_validation.py#L12)
- `_VALID_STAGES` and `_VALID_BAR_SIZES` are local duplicates:
  [src/milodex/cli/config_validation.py:50](../../src/milodex/cli/config_validation.py#L50)
- Actual strategy validation delegates to loader:
  [src/milodex/cli/config_validation.py:89](../../src/milodex/cli/config_validation.py#L89)
- Project guide explicitly warns about this:
  [CLAUDE.md:88](../../CLAUDE.md#L88)

Why it matters: Dead constants in executable source are a trap for future
contributors and LLM agents.

Suggested remedy: Delete stale constants, or re-export canonical loader/schema
constants only. Keep CLI validation as a thin wrapper around the loader.

Verification steps:

- Remove or centralize the constants.
- Expected fixed behavior: no source path advertises `name` as a required
  strategy key unless the real loader requires it.

Reviewer notes:

```text
VERIFIED — and already a documented CLAUDE.md gotcha. _STRATEGY_REQUIRED_KEYS
(name/universe) contradicts real configs (id/universe_ref); _VALID_STAGES and
_VALID_BAR_SIZES are orphaned local duplicates; actual validation delegates
to load_strategy_config, so the constants are pure dead-code trap with zero
functional impact. Effective severity P3. Fix: delete the constants (or
re-export loader canon), and drop the corresponding CLAUDE.md gotcha line
once gone. Tiny, zero risk.
```

### P3-01: EventStore is a cross-domain god object

Reviewer status: dismissed
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: no action — big but cohesive by ADR 0011 design

Claim: `EventStore` contains persistence APIs, dataclasses, SQL, and row mappers
for many bounded contexts in one large file.

Evidence:

- Explanation API starts near:
  [src/milodex/core/event_store.py:358](../../src/milodex/core/event_store.py#L358)
- Backtest run APIs:
  [src/milodex/core/event_store.py:1318](../../src/milodex/core/event_store.py#L1318)
- Manifest/promotion atomic append:
  [src/milodex/core/event_store.py:1596](../../src/milodex/core/event_store.py#L1596)
- Backtest equity snapshot APIs:
  [src/milodex/core/event_store.py:1714](../../src/milodex/core/event_store.py#L1714)
- Row mappers occupy the bottom of the file:
  [src/milodex/core/event_store.py:1857](../../src/milodex/core/event_store.py#L1857)

Why it matters: Every new domain adds more dataclasses, SQL, methods, and row
mappers to one file and one giant test surface.

Suggested remedy: Split by repository/read-model modules sharing the same SQLite
connection and migration layer. Preserve a compatibility facade if needed.

Verification steps:

- Extract one low-risk domain repository first, such as snapshots or
  orchestration.
- Expected fixed behavior: callers use domain-specific repository interfaces,
  and `EventStore` no longer grows for every bounded context.

Reviewer notes:

```text
DISMISSED. The file is large (≈2183 lines, ~63 methods) but "god object"
implies coupling that is not present: each domain appends to its own table
with no cross-domain schema references; the single connection + migration
layer is ADR 0011's deliberate choice; callers import event types, not
internals; and no other finding in this report — including the P1s — roots in
EventStore's structure. No observed merge friction or test-surface pain.
Repository-per-domain extraction is a taste refactor available later if the
file becomes a real bottleneck; nothing today justifies it. Note: the
P1-02/P1-03 fixes will ADD atomic methods here — fine; that is the file
doing its job.
```

### P3-02: Data-freshness threshold is duplicated

Reviewer status: verified
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend fix-now (tiny PR)

Claim: Bench says its stale-data threshold is shared with the CLI trust report,
but both modules hardcode the threshold independently.

Evidence:

- Bench constant:
  [src/milodex/commands/bench.py:280](../../src/milodex/commands/bench.py#L280)
- Bench comment says it matches CLI report:
  [src/milodex/commands/bench.py:455](../../src/milodex/commands/bench.py#L455)
- CLI report has a separate threshold comparison:
  [src/milodex/cli/commands/report.py:259](../../src/milodex/cli/commands/report.py#L259),
  [src/milodex/cli/commands/report.py:267](../../src/milodex/cli/commands/report.py#L267)

Why it matters: One surface can later block as stale while another reports
fresh, undermining readiness trust.

Suggested remedy: Move freshness threshold/classification into a shared policy
helper consumed by Bench and report.

Verification steps:

- Change the threshold in one place.
- Expected fixed behavior: Bench/readiness and report tests move together.

Reviewer notes:

```text
VERIFIED. bench.py:280 defines _DATA_FRESHNESS_STALE_HOURS = 24.0; report.py
compares age_hours > 24.0 independently; the bench comment explicitly CLAIMS
the threshold is "shared with the CLI trust report" while nothing enforces
it, and no parity test exists. Values match today by coincidence of history.
Worst kind of duplication: one that advertises itself as shared. Fix: one
constant in a shared policy module, two imports, one parity test. Tiny —
good candidate to bundle with whichever PR next touches readiness.
```

### P3-03: Daily and intraday backtest loops duplicate orchestration choreography

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (small PR, after P1-03 fix)

Claim: The daily and intraday backtest loops share substantial orchestration
shape even after the simulation kernel extraction.

Evidence:

- Daily simulation loop starts at:
  [src/milodex/backtesting/engine.py:883](../../src/milodex/backtesting/engine.py#L883)
- Intraday simulation loop starts at:
  [src/milodex/backtesting/engine.py:1067](../../src/milodex/backtesting/engine.py#L1067)

Why it matters: Fixes to counters, pending orders, finalization, or evidence
recording can drift between daily and intraday paths.

Suggested remedy: After evidence atomicity fixes, extract shared run-state and
finalization helpers, such as `SimulationRunState` or `SimulationLoopResult`.

Verification steps:

- Compare daily/intraday paths before and after extraction.
- Expected fixed behavior: common finalization and counter logic live in one
  helper or kernel-facing contract, with path-specific bar semantics isolated.

Reviewer notes:

```text
VERIFIED but narrower than framed. Post-kernel-extraction, the genuinely
shared choreography is ~30-40 lines of skeleton: tick_held_days at day start,
drain ordering, stranded-order recording, final broker sync + snapshot, and
_SimulationOutput construction. The loop cores (calendar-day iteration vs
event-timeline advance→evaluate→drain) are deliberately different trading
semantics and must NOT be unified. Right-sized fix: extract a finalization
helper both paths call. Sequence it after the P1-03 atomic-closeout change,
which rewrites the same finalization code — doing them in that order avoids
touching it twice.
```

### P3-04: `gui/read_models.py` compatibility shim still exports private helpers

Reviewer status: verified (overstated)
Reviewer: Claude (PM adjudication)
Reviewed on: 2026-06-12
Resolution reference: none yet — recommend backlog (tiny PR)

Claim: The old GUI read-model god module was physically split, but a
compatibility shim still preserves a broad API including private helpers.

Evidence:

- Compatibility `__all__` surface:
  [src/milodex/gui/read_models.py:52](../../src/milodex/gui/read_models.py#L52)
- Re-export tests pin the contract:
  [tests/milodex/gui/test_read_models_reexports.py:59](../../tests/milodex/gui/test_read_models_reexports.py#L59)

Why it matters: Consumers can keep coupling to the old aggregate module and
underscore helper names, making future cleanup look like a breaking API change
inside the GUI package.

Suggested remedy: Phase down the shim. Production and tests should import
focused modules directly. Keep only intentional public compatibility symbols,
and stop re-exporting underscore helpers.

Verification steps:

- Run `rg "from milodex\\.gui\\.read_models import _|read_models\\._" tests src`.
- Expected fixed behavior: no private helper imports through the shim.

Reviewer notes:

```text
VERIFIED that the shim's __all__ exports four underscore helpers, OVERSTATED
on coupling: the verification grep shows production code imports NOTHING
private through the shim — the only hits are test files. The shim and its
re-export pinning test are deliberate artifacts of the 2026-06-02 GUI
hardening split, and the State-class identity contract (`is`, not `==`) it
protects is load-bearing for the QML registry. Remaining cleanup: drop the
underscore names from __all__, repoint the two-three test imports at the
helpers' home modules, keep the State-class identity test. Tiny.
```

## Resolution Log (2026-06-12, PRs #231–#245)

All merged to master the same day as the adjudication. Every PR body carries the
finding id(s), design notes, and verbatim test counts.

| PR | Findings | Summary |
| --- | --- | --- |
| #231 | P1-01 | Risk layer consumes canonical ALLOWED_STAGES_BY_MODE; backtest-stage paper submit refused; unknown modes fail closed |
| #232 | P2-08, P2-03 | SRS R-DAT-016 inline-universe exception; risk.stop_loss_pct optional/inert, dead execution-config field removed, RISK_POLICY ownership doc |
| #233 | P1-03, P2-02 | Atomic finalize_backtest_run (status+ended_at+metadata, one transaction, all closeout sites); snapshot write failures logged + surfaced in run metadata |
| #234 | P2-21, P2-22, P3-02 | Absent data-quality evidence renders not_recorded (never pass); stale config_validation constants deleted; shared DATA_FRESHNESS_STALE_HOURS |
| #235 | P2-18, P2-19 | Executed governance backfill archived + backfill rules in scripts/README.md; counterfactual gate constants+outcomes parity tests |
| #236 | P1-02 | execution_attempts outbox (migration 014) before broker submit with client_order_id; atomic append_explanation_and_trade; dedup veto counts pending/submitted/error attempts (PM override: error counts — unknown delivery is fail-closed); stale-pending reconciliation WARN |
| #237 | P2-10 | POSITION_AFFECTING_STATUSES single home in core/trade_status.py; identity + fold-parity tests |
| #238 | P2-06 | reconcile_profile_against_audit: file vs latest successful audit row; informational WARN via reconciliation + GUI startup |
| #239 | P1-04 | YAML stage rewrite precomputed/validated before durable writes (transition AND demote); honest non-atomic docstring; failure-injection tests |
| #240 | P2-01 | idle→backtest stage_return written at job acceptance per ADR 0050 D6; failure does not roll back |
| #241 | P3-03 | Shared _finalize_simulation helper for daily+intraday tails; behavior-preserving, zero test edits |
| #242 | P2-07 | SRS R-STR-014 IMPLEMENTED: risk/disable_conditions.py catalog (9 conditions, honest evaluability triage: 3 auto-evaluated co-firing with existing checks, 6 declared-only), _check_disable_conditions veto with disable_condition_active, fail-closed evaluators, docs updated |
| #243 | P2-12, P2-13 | Python-owned ActionKindSpec table feeds preview/bridge/QML; QML kind tables and fallback classifiers deleted; function-name test pins removed/converted to behavioral |
| #244 | P2-14, P2-15, P3-04 | Single REGISTRY_SPEC descriptor (sync test deleted); public PollingReadModel.request_refresh with stopped-guard; shim underscore exports trimmed |
| #245 | P2-04 | Declarative spec constraints (bounds/choices/relations) enforced at config load across all 23 strategy classes; all 21 shipped configs load unchanged |

Not fixed by design: P2-05, P2-09, P2-16, P2-20, P3-01 (dismissed — see notes);
P2-11 (decompose reconciliation opportunistically when next touched); P2-17
(cache key fix belongs to the deferred crypto data-ingestion task).

