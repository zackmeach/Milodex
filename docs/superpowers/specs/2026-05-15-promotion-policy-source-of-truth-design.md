# Promotion Policy Source-of-Truth — Design Spec

- **Date:** 2026-05-15
- **Status:** Approved (brainstorming) — pending spec review
- **Companion ADR:** ADR 0052 (to be authored as part of this work)
- **Scope class:** Behavior-preserving consolidation. One decent PR.

## Problem

Promotion / governance "truth" is duplicated across `FOUNDER_INTENT.md`, `CLAUDE.md`,
`SRS.md`, `RISK_POLICY.md`, `STRATEGY_BANK.md`, `PROMOTION_GOVERNANCE.md`,
`configs/risk_defaults.yaml`, and `promotion/state_machine.py`. There is no single
source of truth for *promotion* policy the way `configs/risk_defaults.yaml` is the
SoT for *execution-risk* numerics.

This is **not** a code-vs-spec drift bug. Verified against the repo:

- `SRS.md` R-PRM-004 (line 290) explicitly specifies the two-tier gate: research-target
  paper = Sharpe > 0.0 / max DD < 25% / configured trade floor; research-target
  post-paper (capital) = Sharpe > 0.5 / max DD < 15% / same floor; lifecycle-proof
  strategy exempt from statistical thresholds with an operational gate instead.
- `promotion/state_machine.py` (constants lines 49–53, `check_gate` 105–157,
  `_thresholds_for_stage` 160–166) implements **exactly** that split.

**SRS and code agree.** The wrong artifact is the doctrine/summary layer — most
acutely `CLAUDE.md`, which presents the capital-gate numbers (Sharpe > 0.5 /
DD < 15%) as if they were *the* promotion gate, conflating the two tiers. The
fix is consolidation into one typed source of truth that the gate consumes and
the docs point at — not a behavior change.

Separately, the SRS R-PRM-004 lifecycle-proof **operational gate** (deterministic
backtest ran; explanation record per simulated signal; ≥1 fault-injection
rejection) is specified but **not enforced** — `check_gate`'s `lifecycle_exempt`
branch (lines 119–127) returns `allowed=True` unconditionally. By explicit
decision, this work *defines* that gate as a typed concept but does **not**
enforce it; the non-enforcement is recorded as a named, tracked gap.

## Principle ratified by ADR 0052

> Risk posture is configurable; safety invariants are not; promotion policy is
> governance-owned, versioned in typed code, and changes only by deliberate ADR —
> never by config edit, strategy, model, or agent.

## Three-tier conceptual model

| Tier | Where it lives | Configurable? | This work |
|---|---|---|---|
| **System invariants** — stage-order, no-skip, no-downgrade, Phase-1 live-lock, risk-layer veto | Code (`validate_stage_transition`, risk layer) | Never | Unchanged; **enumerated and named** in ADR 0052 |
| **Promotion governance policy** — research-target statistical tiers; lifecycle-proof operational-gate definition | `promotion/policy.py` (typed) | Only by ADR | **Created here** |
| **Operator risk preferences** — sizing, caps, kill-switch, staleness | `configs/risk_defaults.yaml` | Operator, within bounds | Untouched; boundary drawn so the two are never reconflated |

## Components

### `src/milodex/promotion/policy.py` (new — the SoT)

- **`PromotionCheckResult`** — the frozen verdict dataclass, **moved here from
  `state_machine.py` with its public shape preserved exactly**: fields
  `allowed: bool`, `promotion_type: str`, `failures: list[str]`,
  `sharpe_ratio: float | None`, `max_drawdown_pct: float | None`,
  `trade_count: int | None`. `state_machine.py` **re-exports** it
  (`from milodex.promotion.policy import PromotionCheckResult`) so existing
  imports `from milodex.promotion.state_machine import PromotionCheckResult`
  keep working unchanged. This is a consolidation PR, not an API-shape PR — the
  public type name and shape do not change.
- **`LifecycleGateDefinition`** — frozen, **definition-only**. Structured
  representation of the three R-PRM-004 operational criteria plus a human
  description, and an explicit field `enforced: bool = False` so the
  non-enforcement is a typed fact, not a buried comment.
- **`PromotionPolicy`** — frozen dataclass. Holds the tier thresholds (paper:
  min Sharpe 0.0, max DD 25.0; capital: min Sharpe 0.5, max DD 15.0; default
  trade floor 30, overridable per-strategy) **and** the decision behavior:
  - `evaluate_research_target(*, sharpe_ratio, max_drawdown_pct, trade_count, target_stage, min_trade_count) -> PromotionCheckResult`
    — produces the statistical verdict with the **same failure-message strings
    and `promotion_type="statistical"`** as today.
  - `lifecycle_gate -> LifecycleGateDefinition` — the definition-only operational gate.
- **`PHASE1_GOVERNANCE_V1: PromotionPolicy`** — the single named instance, values
  identical to today's `state_machine.py` constants.
- **`ACTIVE_PROMOTION_POLICY = PHASE1_GOVERNANCE_V1`** — the one export the rest
  of the system references. **Not runtime-selectable.** ADR 0052 states
  explicitly that this is the single named current governance policy, not a
  selector; profile selection is deferred future work.

### `src/milodex/promotion/state_machine.py` (seam — behavior-preserving)

- Remove constants `MIN_SHARPE`, `MAX_DRAWDOWN_PCT`, `MIN_TRADES`,
  `PAPER_MIN_SHARPE`, `PAPER_MAX_DRAWDOWN_PCT` and `_thresholds_for_stage()`.
- `check_gate()` becomes a thin adapter:
  - `lifecycle_exempt=True` → returns `allowed=True`,
    `promotion_type="lifecycle_exempt"` **exactly as today** (define-only; no new
    enforcement of the operational gate).
  - statistical → delegates to
    `ACTIVE_PROMOTION_POLICY.evaluate_research_target(...)`, returning the
    `PromotionCheckResult` it produces.
  - `check_gate`'s signature, keyword args, and return type are unchanged.
- Re-export `PromotionCheckResult` from `policy.py` for import-path stability.
- `validate_stage_transition()`, `transition()`, `demote()`, `STAGE_ORDER`,
  `PHASE_ONE_BLOCKED_STAGES` — **unchanged**. Structural invariants stay code-side.
- Fix the incorrect SRS citations in the module docstring (currently cites
  R-PRM-001 / R-PRM-002 for thresholds; correct reference is R-PRM-004).

## Documentation reconciliation — point, do not duplicate

- **ADR 0052** (next free number; ADR index currently ends at 0051): ratifies
  the three-tier model, **enumerates and names** the system invariants as
  non-negotiable, declares `promotion/policy.py` the promotion-policy SoT,
  states `ACTIVE_PROMOTION_POLICY` is the single current policy and **not**
  runtime-selectable, and records the lifecycle operational-gate
  non-enforcement as a **named known gap with a tracked follow-up**. Additive —
  supersedes nothing.
- **`SRS.md` R-PRM-004:** already correct — **not rewritten**. Add a one-line
  pointer to `promotion/policy.py` + ADR 0052 as the implementation SoT.
- **`CLAUDE.md`:** the actually-wrong doc. Rewrite the "Promotion pipeline"
  rule to state the two-tier reality in one sentence and **point at ADR 0052 /
  `policy.py` carrying no threshold numbers** (duplicated numbers are the root
  cause).
- **`STRATEGY_BANK.md`:** add a one-line note that paper-stage entry is the
  paper tier (Sharpe > 0.0 / DD < 25%), not the capital tier, pointing at
  ADR 0052. No metric or stage changes.
- **`PROMOTION_GOVERNANCE.md`:** add the SoT pointer; remove any duplicated
  threshold numbers if present (verify during implementation).

## Testing

- **Characterization test (keystone).** Parametrized over a matrix — paper vs
  capital tier; pass/fail of each of Sharpe / DD / trade-count; `None` metric
  values; `lifecycle_exempt=True`; custom per-strategy trade floors (e.g. 20,
  30) — asserting the post-refactor `check_gate()` returns **identical
  `allowed`, `promotion_type`, `failures` (string-for-string), the echoed
  metric fields, and identical boundary behavior** (Sharpe exactly 0.0 and
  exactly 0.5; DD exactly 25.0 and exactly 15.0). The goal is field-and-message
  equivalence and boundary equivalence, **not** object identity.
- **`policy.py` unit tests:** tier selection, the boundary conditions above,
  `LifecycleGateDefinition.enforced is False`, `PHASE1_GOVERNANCE_V1` values
  equal the previous constants.
- **CLAUDE.md regression lock (targeted).** Assert that `CLAUDE.md` does **not**
  state promotion threshold values as authoritative policy and **does** point
  to ADR 0052 / `policy.py`. Deliberately narrow — it must not block legitimate
  metrics or examples elsewhere in the file. Proposed; operator confirms inclusion.
- Known-gap record: the lifecycle operational-gate non-enforcement is recorded
  in ADR 0052 and in the "Known gaps" section below. Not fixed here by decision.

## Known gaps (deliberate deferrals)

1. **Lifecycle-proof operational gate is defined but not enforced.** `check_gate`
   lifecycle-exempt path returns `allowed=True` unconditionally; the three
   R-PRM-004 operational criteria are modeled (`LifecycleGateDefinition`,
   `enforced=False`) but not verified. Tracked for a future deliberate decision
   with its own spec/test surface.

## Out of scope (YAGNI)

- YAML promotion policy / any operator-tunable promotion config.
- Profile selection or loader machinery.
- CLI / Bench "active policy" surfacing.
- Enforcing the lifecycle operational gate.
- Sweeping unrelated `commands/bench.py` "Phase B / not submit-capable"
  docstrings (separate doc-hygiene task — flagged, not bundled).

## Final scope instruction (operator-authored)

```
Implement ADR 0052 + promotion/policy.py as a behavior-preserving
source-of-truth consolidation.

Do:
- Add typed PromotionPolicy.
- Add PHASE1_GOVERNANCE_V1.
- Move statistical gate decision logic out of state_machine.py.
- Keep structural invariants in state_machine.py.
- Define lifecycle-proof operational gate but do not enforce it.
- Add tests proving no behavior change.
- Reconcile CLAUDE.md / STRATEGY_BANK.md / PROMOTION_GOVERNANCE.md
  to point at the new source.

Do not:
- Add YAML promotion policy.
- Add profile selection.
- Change promotion behavior.
- Enforce lifecycle-proof operational gate yet.
- Sweep unrelated Bench docstrings.
```

## Deliverable

One cohesive, reviewable PR: `promotion/policy.py` + `state_machine.py` seam +
characterization/unit tests + ADR 0052 + the four documentation edits. No
production behavior change to verify; correctness is proven by the
characterization test.
