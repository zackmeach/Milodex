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
