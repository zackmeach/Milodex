# ADR 0052 — Promotion policy is a typed governance source of truth

**Status:** Accepted · 2026-05-15
**Related:** [ADR 0009](0009-promotion-pipeline-stage-model.md) (promotion stages), [ADR 0004](0004-paper-only-phase-one.md) (Phase-1 live-lock), [ADR 0005](0005-kill-switch-manual-reset.md) (kill switch, manual reset), [ADR 0008](0008-risk-layer-veto-architecture.md) (risk-layer veto), [ADR 0011](0011-sqlite-event-store.md) (event store), [ADR 0015](0015-strategy-identifier-and-frozen-manifest.md) (manifest freeze), [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) (backtest sandbox), [SRS R-PRM-004](../SRS.md), [Design spec](../superpowers/specs/2026-05-15-promotion-policy-source-of-truth-design.md).

## Context

Promotion and governance "truth" is duplicated across approximately eight surfaces: `FOUNDER_INTENT.md`, `CLAUDE.md`, `SRS.md`, `RISK_POLICY.md`, `STRATEGY_BANK.md`, `PROMOTION_GOVERNANCE.md`, `configs/risk_defaults.yaml`, and `promotion/state_machine.py`. There is no single source of truth for *promotion* policy the way `configs/risk_defaults.yaml` is the source of truth for *execution-risk* numerics.

This is **not** a code-vs-spec drift bug. `SRS.md` R-PRM-004 explicitly specifies the two-tier gate: a paper-entry tier (Sharpe > 0.0 / max DD < 25% / configured trade floor) and a capital gate for post-paper promotion (Sharpe > 0.5 / max DD < 15% / same floor), plus a lifecycle-proof regime-strategy exception that replaces the statistical gate with a defined operational check. `promotion/state_machine.py` implements exactly that split. SRS and code agree.

The wrong artifact is the doctrine layer, most acutely `CLAUDE.md`, which presents the capital-gate numbers (Sharpe > 0.5 / max DD < 15%) as if they were *the* promotion gate, conflating the two tiers. The fix is consolidation into one typed source of truth that the gate consumes and docs point at — not a behavior change.

Separately, the R-PRM-004 lifecycle-proof operational gate (deterministic backtest ran; explanation record per simulated signal; ≥1 fault-injection rejection) is specified but **not enforced**: `check_gate`'s `lifecycle_exempt` branch returns `allowed=True` unconditionally. By explicit decision, this work defines that gate as a typed concept but does not enforce it. The non-enforcement is recorded as a named, tracked gap — see Known gap below.

Additionally, there has been no clear boundary between which promotion attributes are fixed safety invariants, which are governance policy, and which are operator-tunable preferences. Conflation of these three tiers is what allows future changes to accidentally weaken one tier while intending to adjust another.

## Decision

### 1. Three-tier conceptual model

Promotion-related attributes are classified into exactly three tiers, and the tiers must never be reconflated:

| Tier | Where it lives | Configurable? |
|---|---|---|
| **System invariants** | Code (`state_machine.py`, risk layer) | Never |
| **Promotion governance policy** | `src/milodex/promotion/policy.py` (typed, versioned) | Only by ADR |
| **Operator risk preferences** | `configs/risk_defaults.yaml` | Operator, within bounds |

### 2. System invariants — non-negotiable, code-side

The following are **system invariants**. They are not configurable, not delegated to `policy.py`, and not overridable by any strategy, model, operator preference, or agent. They remain code-side in `state_machine.py` and the risk layer, and they are enumerated here by name so they can be referenced unambiguously:

- **Risk-layer veto.** Every trade intent passes through the risk layer before execution. No broker call reaches the broker without passing through `milodex.risk`.
- **No broker bypass.** No code path may submit to the broker without passing the risk layer. No strategy, GUI surface, agent, or config edit may introduce a bypass.
- **Stage order, no-skip.** Stages advance in the order `backtest → paper → micro_live → live`. Skipping a stage is not permitted. `validate_stage_transition` enforces this.
- **No-downgrade-via-promote.** Promotion is forward-only. Walking back a stage is a governed demotion (separate, explicit, audited), never an implicit consequence of a promote call.
- **Manual kill-switch reset.** When a kill switch is triggered, trading halts. Auto-resume is never acceptable. Reset requires an explicit manual operator action (ADR 0005).
- **Phase-1 live-lock.** `micro_live` and `live` stages are locked during Phase 1. `PHASE_ONE_BLOCKED_STAGES` enforces this in `state_machine.py`. No promotion path reaches live while Phase 1 is active (ADR 0004).

### 3. Promotion governance policy — typed, versioned in code, governance-owned

**`src/milodex/promotion/policy.py` is the single source of truth for promotion governance policy.**

The policy is expressed as `PromotionPolicy`, a frozen typed dataclass. Its current named instance is `PHASE1_GOVERNANCE_V1`. The policy carries:

- **Research-target statistical tiers** — the paper-entry gate (Sharpe > 0.0, max DD < 25.0%) and the capital gate (Sharpe > 0.5, max DD < 15.0%) with a default trade floor of 30, overridable per-strategy config.
- **Lifecycle-gate definition** — a `LifecycleGateDefinition` representing the R-PRM-004 operational criteria for lifecycle-proof regime strategies (deterministic backtest ran; explanation record per simulated signal; ≥1 fault-injection rejection). The field `enforced: bool = False` is explicit: the non-enforcement is a typed fact, not a buried comment.

`ACTIVE_PROMOTION_POLICY = PHASE1_GOVERNANCE_V1` is the single named current governance policy. It is **not runtime-selectable**. There is no profile selector, no config-file override, no agent knob. Profile selection is deferred future work and requires its own ADR before it may be added.

Changes to `PHASE1_GOVERNANCE_V1` or any named policy instance require a new ADR. The policy is governance-owned; it is not tunable by operator preference.

### 4. Operator risk preferences — unchanged, boundary drawn

`configs/risk_defaults.yaml` remains the source of truth for operator risk preferences: position sizing, account-level caps, daily loss limits, kill-switch thresholds, staleness bounds. These are not promotion policy and must not be conflated with it. The boundary is:

- Preferences configure *how much risk the operator accepts within each stage's trading* — they constrain the risk layer.
- Promotion policy governs *whether a strategy is ready to advance to the next stage* — it gates stage transitions.

Nothing in this ADR changes `configs/risk_defaults.yaml` or the runtime behavior of the risk layer.

### 5. Ratified principle

The ratified principle for this system is:

> Risk posture is configurable; safety invariants are not; promotion policy is governance-owned, versioned in typed code, and changes only by deliberate ADR — never by config edit, strategy, model, or agent.

### 6. Behavior preservation

This consolidation is behavior-preserving. The public constants (`MIN_SHARPE`, `MAX_DRAWDOWN_PCT`, `MIN_TRADES`, `PAPER_MIN_SHARPE`, `PAPER_MAX_DRAWDOWN_PCT`) are preserved as policy-derived public aliases in `state_machine.py` so all existing imports and callers continue unmodified. `check_gate`'s signature, keyword args, and return type are unchanged. `PromotionCheckResult` is re-exported from `state_machine.py` for import-path stability. `test_state_machine.py` and `test_transition.py` pass unedited.

### 7. Known gap — lifecycle operational gate is defined but not enforced

The R-PRM-004 lifecycle-proof operational gate (criteria: a — deterministic backtest ran; b — explanation record per simulated signal; c — ≥1 fault-injection rejection) is now a typed concept (`LifecycleGateDefinition`, `enforced=False`) in `policy.py`. The `check_gate` lifecycle-exempt branch still returns `allowed=True` unconditionally. This is a **deliberate scope decision**, not an oversight. The gap is named here explicitly so it is a conscious deferral, not silent technical debt. Closing it requires its own spec and test surface; that work is tracked but not scheduled.

## Rationale

**Typed code as the source of truth is more durable than duplicated prose.** When the threshold lives in five docs and one module, a change to the module silently contradicts the docs, and a doc reader cannot verify currency. When `policy.py` is the source of truth and docs point at it, there is one place to change and one place to verify.

**The three-tier model prevents accidental tier leakage.** Without explicit classification, "promotion config" blurs into "risk config" blurs into "safety invariant." That blurring is the failure mode — a well-intentioned config edit that accidentally weakens a safety invariant. Naming the tiers and stating where each lives closes that path.

**System invariants belong in code, not in policy objects.** Invariants must be enforceable without consulting any runtime policy state. Moving them into `policy.py` would mean an accidental or malicious policy replacement could bypass them. They stay code-side by design.

**Not runtime-selectable is the right posture now.** Adding a profile selector before there is a legitimate use case for it introduces a surface that could accidentally weaken governance without a clear benefit. The deferral is appropriate; when a legitimate use case exists, a new ADR opens the selector path deliberately.

**Naming the lifecycle gate gap is better than silently leaving it.** A `LifecycleGateDefinition` with `enforced=False` is a contract with the codebase: "this criteria set exists, we have decided not to enforce it yet, and that decision is visible." A buried `# TODO` or an absent concept would mean a future developer does not know whether the non-enforcement was intentional.

## Consequences

- **`src/milodex/promotion/policy.py` is the promotion-policy source of truth** from this ADR forward. No other file owns promotion gate thresholds.
- **`state_machine.py` becomes a thin adapter.** Its structural invariants (`validate_stage_transition`, `STAGE_ORDER`, `PHASE_ONE_BLOCKED_STAGES`, `transition`, `demote`) are unchanged. Its statistical gate logic delegates to `ACTIVE_PROMOTION_POLICY.evaluate_research_target`. Its `check_gate` signature is unchanged; callers are unaffected.
- **`CLAUDE.md` "Promotion pipeline" rule is updated** to state the two-tier reality and point at `policy.py` and this ADR rather than duplicating threshold numbers.
- **`SRS.md` R-PRM-004** receives a one-line pointer to `policy.py` and this ADR as the implementation source of truth; the spec text is not rewritten.
- **`STRATEGY_BANK.md`** receives a one-line note that paper-stage entry uses the paper tier (Sharpe > 0.0 / DD < 25%), not the capital tier, pointing at this ADR.
- **`PROMOTION_GOVERNANCE.md`** receives the source-of-truth pointer; any duplicated threshold numbers present are removed.
- **The lifecycle operational gate remains unenforced.** The `check_gate` lifecycle-exempt path is behavior-identical to its pre-ADR state. This ADR documents the gap; a future ADR closes it.
- **No YAML promotion config is introduced.** Promotion policy is code, not configuration.
- **No CLI or Bench surfacing of the active policy.** Surfacing `ACTIVE_PROMOTION_POLICY` in the UI is a clean later extension.

## Non-goals

- Does **not** add a YAML promotion policy or make promotion thresholds operator-configurable.
- Does **not** add profile selection or a policy loader.
- Does **not** add CLI or Bench "active policy" display.
- Does **not** enforce the lifecycle-proof operational gate.
- Does **not** change any promotion thresholds, stage rules, or risk-layer behavior.
- Does **not** supersede any existing ADR. This is additive — it ratifies a consolidation and draws boundaries that were previously implicit.
