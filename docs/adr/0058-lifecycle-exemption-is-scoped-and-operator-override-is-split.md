# ADR 0058 — Lifecycle Exemption Is Scoped; Operator Override Is Split (D-4)

**Status:** Accepted
**Date:** 2026-07-02
**Related:** [ADR 0052](0052-promotion-policy-is-a-typed-governance-source-of-truth.md) (typed promotion policy; this ADR amends its §7 lifecycle-gate stance), [CURRENT_ROADMAP.md](../CURRENT_ROADMAP.md) §2 (D-4) / §8 (decision-ownership map) / M4 (enforcement destination), [SRS.md](../SRS.md) R-PRM-004 (the SHALL-criteria) and Key Terms ("Lifecycle-proof strategy"), [`src/milodex/promotion/policy.py`](../../src/milodex/promotion/policy.py) (`LifecycleGateDefinition.applies_to`), [`src/milodex/promotion/orchestrator.py`](../../src/milodex/promotion/orchestrator.py) (scoping + override enforcement), [`src/milodex/promotion/state_machine.py`](../../src/milodex/promotion/state_machine.py) (`check_gate` — untouched), [`src/milodex/promotion/evidence.py`](../../src/milodex/promotion/evidence.py) (durable evidence).

Decided 2026-07-02 via the CURRENT_ROADMAP §8 decision-pause protocol (framing + independent dissent review + reconciliation), in a founder-directed session.

## Context

Two distinct governance defects were conflated behind a single flag.

1. **The R-PRM-004 SHALL-criteria are unenforced.** SRS R-PRM-004 says the lifecycle-proof strategy's `paper` gate requires (a) a successful deterministic backtest run, (b) explanation records (R-XC-008) generated for every simulated signal, and (c) the risk layer having rejected at least one synthetic fault-injection trade. In code these criteria are **define-only**: `LifecycleGateDefinition.enforced` is `False` and `state_machine.check_gate` short-circuits every `lifecycle_exempt=True` request to `allowed=True`. ADR 0052 recorded this as a "Tracked gap."

2. **`--lifecycle-exempt` was unscoped.** The flag bypassed the statistical gate for **any** strategy id, not just the lifecycle-proof regime strategy. It was, in effect, a general operator override of the statistical gate wearing a lifecycle-proof label. The durable ledger reflects this: five non-regime intraday canaries hold `promotion_type='lifecycle_exempt'` (2026-05-28/29) — a **misdescription** in the promotion ledger, because those strategies are not lifecycle-proof.

The SRS reflects the conflation at [`docs/SRS.md`](../SRS.md) R-PRM-004 (line ~290, the SHALL-criteria) and the Key Terms definition (line ~28, "Lifecycle-proof strategy").

The lifecycle-proof strategy is a single id: `regime.daily.sma200_rotation.spy_shy.v1` (SRS Key Terms — the only lifecycle-proof strategy).

## Decision

**Split now, enforce at M4.** Four points:

1. **Scope the lifecycle exemption** to policy-listed lifecycle-proof strategy ids. `LifecycleGateDefinition` gains `applies_to: tuple[str, ...]`, the typed identity source-of-truth for "lifecycle-proof". `PHASE1_GOVERNANCE_V1` sets `applies_to=("regime.daily.sma200_rotation.spy_shy.v1",)`. The promotion orchestrator refuses (fail-closed) a `lifecycle_exempt=True` request whose `strategy_id` is not in `applies_to`, and names `--operator-override` in the refusal.

2. **Add an honest general operator override** as a separate, loudly-recorded mechanism. `PromoteRequest` gains `operator_override: bool`; the CLI gains `--operator-override`. It is **paper-stage only** (fail-closed for any capital stage — the autonomy boundary owns `micro_live`/`live`), and requires a **non-empty operator reason** (the mandatory `--recommendation`, reused as the recorded reason). It produces `promotion_type="operator_override"` and is constructed in the orchestrator **without** routing through `check_gate`'s lifecycle branch — the statistical gate is skipped by an explicit operator act, durably recorded. `lifecycle_exempt` and `operator_override` are mutually exclusive.

3. **Durably record the three unenforced R-PRM-004 criteria** on every lifecycle-exempt promotion. The evidence package's `gate_check_outcome` gains, for the lifecycle path, a `lifecycle_criteria` block listing the criteria with `enforced=False` and `deferred="M4"`. For the operator-override path it records the operator's reason. The statistical path's serialized shape is unchanged — legacy rows and tests do not shift shape.

4. **Defer ALL enforcement of criteria (a)(b)(c) together to roadmap M4.** No enforcement code lands in this ADR's change. `enforced` stays `False`.

`applies_to` is the identity source-of-truth. `check_gate`'s signature and behavior are unchanged (it has a third caller — the research-screen surface at [`backtesting/walk_forward_batch.py`](../../src/milodex/backtesting/walk_forward_batch.py) — that passes `lifecycle_exempt=(family=="regime")` with no strategy identity; that call site is a display heuristic, not durable governance, and carries an advisory comment pointing here).

### Options rejected

- **A — enforce the criteria now (full).** Enforcing (a)(b)(c) today forces one of two bad outcomes: fabricating fake fault-injection evidence to satisfy (c), or freezing the intraday lane while the tooling is built. Criterion (c) needs a fault-injection harness and a reason-code convention that do not exist yet; that tooling belongs to M4. Rejected — it trades an honest, labeled gap for either dishonest evidence or a stalled lane.

- **B — document loudly, change nothing.** Leaves `--lifecycle-exempt` unscoped and the five canary rows misdescribed. This ratifies a permanently dishonest ledger — the ledger would keep asserting "lifecycle_exempt" for strategies that are not lifecycle-proof. Rejected.

- **C — phased enforcement (weak-form criteria now, strong later).** The proposed weak form of criterion (b) — "explanation records exist for the run" — is a **false-negative generator**: the regime strategy's latest completed walk-forward run `0733d4d1` has **0 linked explanations**, because a regime strategy legitimately yields zero signals in a short OOS window, so "explanation records for every simulated signal" is **vacuously satisfied** yet a naive count reads it as a failure. Two further traps compound this: `explanations.backtest_run_id` is an **integer FK** to `backtest_runs.id`, **not** the UUID `run_id` (a join on the wrong column silently returns nothing), and criterion (a) alone has **unbounded staleness** (a years-old "successful backtest run" would satisfy it). Rejected — a weak form that misfires on the one strategy it governs is worse than an honest deferral.

## Consequences

- **M4 owns (a)(b)(c) enforcement design.** The deferred work: fault-injection tooling + a reason-code convention for criterion (c); signal-count metadata so criterion (b) can distinguish "zero signals" from "missing explanations"; the correct integer-FK join (`explanations.backtest_run_id` → `backtest_runs.id`) plus a freshness bound for criterion (a). Tracked at CURRENT_ROADMAP M4.

- **SRS amendment.** R-PRM-004 gains minimal sentences: the exemption is scoped to policy-listed lifecycle-proof ids; a distinct, durably-recorded operator override (`promotion_type='operator_override'`, mandatory reason, paper-stage only) may bypass the statistical gate; criteria enforcement is deferred. All cite ADR 0058 only (citing another `R-XXX-NNN` inside R-PRM-004 prose corrupts the generated coverage matrix).

- **Historical rows retain old semantics.** The five pre-ADR `lifecycle_exempt` canary rows are never rewritten (the promotion ledger is append-only). Read them via the **date boundary**: rows recorded before this ADR's adoption carry the old, unscoped semantics; rows after carry the scoped meaning. New non-regime bypasses are recorded honestly as `operator_override`.

- **No new schema.** `promotions.promotion_type` has no CHECK constraint; the new `'operator_override'` value is additive. Existing consumers branch only on `('demotion','stage_return')` and tolerate a new string.

- **`check_gate` and the risk layer are untouched.** The scoping and override enforcement live entirely in the orchestrator seam. The sacred risk-layer path is not modified.

- **CLAUDE.md** Gotchas entry for `--lifecycle-exempt` is updated to the scoped reality and the split override.

## Addendum 2026-07-11 — M4 enforcement design

The deferral above is closed. The three R-PRM-004 criteria (a)(b)(c) are now **enforced** for every lifecycle-exempt promotion, evaluated against the event store, fail-closed. `LifecycleGateDefinition.enforced` flips to `True`; `check_gate` and the risk layer are still **untouched** (the `lifecycle_exempt` short-circuit in `state_machine.check_gate` still returns `allowed=True`). Enforcement lives entirely in the orchestrator seam — the same seam that already owns scoping and the operator-override split — so the sacred paths are unchanged.

### Enforcement seam

`prepare_and_record_promotion` (`promotion/orchestrator.py`), after stage validation, bypass-admissibility (scoping / mutual-exclusion), metrics resolution, and the unchanged `check_gate`. When `request.lifecycle_exempt` **and** `ACTIVE_PROMOTION_POLICY.lifecycle_gate.enforced`, it calls `evaluate_lifecycle_criteria(strategy_id, event_store)` (`promotion/lifecycle_criteria.py`). If any criterion is unsatisfied the promotion is refused with `reason_code="lifecycle_criteria_unmet"` and per-criterion actionable messages — no durable writes. When satisfied, the per-criterion result is recorded durably in `gate_check_outcome.lifecycle_criteria` (`enforced=True`, `satisfied=True`, `evidence_max_age_days`, and one entry per criterion with its evidence refs — run id, ages, counts, synthetic-explanation id). The pre-M4 `deferred="M4"` shape is retired. Enforcement runs **only** for the scoped regime id, because a non-scoped lifecycle-exempt request is already refused by `_check_bypass_admissibility` before this point — enforcement is *in addition to* scoping, not a replacement.

### Criteria semantics as built

**(a) "a successful deterministic backtest run" — with a freshness bound.** `event_store.get_latest_successful_backtest_run(strategy_id)` returns the most recent `status='completed'` run (running / failed / cancelled / orphan_recovered excluded). Its age is measured from `started_at`. `LifecycleGateDefinition.evidence_max_age_days` (see `policy.py` `LIFECYCLE_EVIDENCE_MAX_AGE_DAYS = 90`) bounds staleness — the "unbounded staleness" trap this ADR named for criterion (a). No successful run, or one older than the bound → **UNMET** with the age and the bound in the message. *90 days is a first-pass value for a low-cadence regime strategy and is flagged for founder review.*

**(b) "explanation records (R-XC-008) generated for every simulated signal."** The backtest engine now writes an additive `signal_count` field into the run's `metadata_json` (`engine.run()` and `walk_forward_runner`) — the count of order intents that reached the execution/drain phase (`trade_count + skipped_count`), each of which produces exactly one execution-or-skip explanation row. Criterion (b) reads `signal_count` from the run found in (a) and compares it against `event_store.count_explanations_for_backtest_run(run.id)` — the **integer** FK join (`explanations.backtest_run_id = backtest_runs.id`), never the UUID `run_id` (the wrong-column join silently returns zero, which this ADR named). Verdict: `signal_count is None` (a pre-enforcement run) → **UNMET**, "re-run the backtest to generate signal-count metadata" (fail-closed; never assume); `signal_count == 0` → **SATISFIED** (the regime strategy legitimately yields zero signals — the option-C false-negative this ADR rejected); `explanation_count >= signal_count` → **SATISFIED**; otherwise → **UNMET** ("N signals but only M explanation rows — missing explanations").

**(c) "the risk layer having rejected at least one synthetic fault-injection trade."** New tooling: `milodex promotion fault-check <strategy-id>` → `promotion/fault_injection.py`. It builds a deliberately guardrail-violating synthetic intent (a BUY of ~$10M notional against a ~$1k synthetic account, far above the fat-finger cap), runs it through the **real** `RiskEvaluator.evaluate` (evaluation only — no broker client is ever constructed; manual-trade mode isolates the account-level fat-finger cap), and records the veto as an explanation row carrying the reason-code convention below. The evaluation is run in manual-trade mode but the row's `strategy_name` is the strategy being proved, so criterion (c) is per-strategy. Criterion (c) queries `event_store.get_latest_synthetic_fault_injection_veto(strategy_id)` and requires a veto within the same 90-day freshness bound. None on record → **UNMET**, "run `milodex promotion fault-check <id>`"; stale → **UNMET** with the age. If the risk layer ever **approves** the synthetic intent (`SyntheticFaultApprovedError`) or vetoes without the targeted guardrail reason (`SyntheticFaultGuardrailError`), the tool **screams** — no satisfying row is recorded and the command exits nonzero — because that is a risk-layer regression.

### The (c) reason-code convention

- **Marker shape:** the explanation row is written with `decision_type='synthetic_fault_injection'`, `submitted_by='promotion_fault_check'`, `risk_allowed=False`, the real veto's `reason_codes` (which must include `max_order_value_exceeded`), and `context_json` `{"synthetic_fault_injection": true, "expected_reason_code": "max_order_value_exceeded", "note": "SYNTHETIC self-test … never reached a broker", …}`.
- **Tool:** `milodex promotion fault-check <strategy-id>`.
- **Query:** `WHERE strategy_name = ? AND decision_type = 'synthetic_fault_injection' AND risk_allowed = 0`, newest first, then the freshness bound.
- **Operator-surface labeling (no pollution of operator truth):** synthetic rows are excluded from `count_paper_rejections`, from the CLI trust dashboard's per-strategy "last action" (`report.py`), and from the paper-scoped dashboard predicate `EXPLANATION_PAPER_SQL` (activity feed + rejected-throughput bucket). They normally carry the strategy's `backtest` stage (already excluded from paper-scoped surfaces); the `decision_type` exclusions are belt-and-suspenders against a fault-check run while a strategy sits at a paper stage.

### What a refusal looks like, per criterion

- (a): `Criterion (a) a successful deterministic backtest run: No successful (status='completed') backtest run found for '<id>'. Run a deterministic backtest … before promotion.` — or, when stale, `… is N days old, exceeding the 90-day freshness bound. Re-run the backtest …`
- (b): `Criterion (b) explanation records generated for every simulated signal: Backtest run <id> predates signal-count metadata and cannot be evaluated … Re-run the backtest to generate signal-count metadata.` — or `N simulated signal(s) but only M explanation row(s) linked to the run — explanation records are missing.`
- (c): `Criterion (c) the risk layer having rejected at least one synthetic fault-injection trade: No synthetic fault-injection veto on record for '<id>'. Run \`milodex promotion fault-check <id>\` …`

### Scope guards (unchanged)

`--operator-override` path untouched; the statistical gate untouched; historical pre-ADR-0058 `lifecycle_exempt` rows keep their old semantics (append-only ledger; date-boundary reads). The lifecycle exemption stays scoped to the policy-listed regime id — enforcement is in addition to scoping. No numeric threshold is restated outside `policy.py`.
