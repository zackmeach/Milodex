# ADR 0043 — Bench demotion actions open a governance flow

**Status:** Accepted · 2026-05-10
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-D (demotion-gesture security), [ADR 0005](0005-kill-switch-manual-reset.md) (manual reset), [ADR 0009](0009-promotion-pipeline-stage-model.md) (promotion stage model), [ADR 0039](0039-stage-session-and-bench-section-are-distinct.md), [PROMOTION_GOVERNANCE.md](../PROMOTION_GOVERNANCE.md)

## Context

The prototype makes promotion and demotion feel like the same gesture. Operationally they are not equivalent. Demotion can stop work, revoke eligibility, or record a lifecycle reversal. Existing promotion demotion requires reason and approval metadata, and `to_stage='disabled'` is currently ledger-only: it does not by itself rewrite YAML or guarantee a running process has stopped.

Phase 6 needs an action policy that preserves the Bench's direct interaction model without turning an action-menu gesture into an unaudited lifecycle mutation.

## Decision

1. **A demotion action (via the Action menu) is a request, not a mutation.** No stage, config, job, runner, or ledger state changes merely because a demotion action was selected. A governance confirmation must collect explicit operator intent before anything changes.

2. **Governance confirmation must collect explicit operator intent.** At minimum it captures target stage, reason, approved-by identity, and links to supporting evidence or incident context when available.

3. **Capital-affecting or safety-affecting demotions require typed confirmation.** Any future demotion from `live` or `micro_live`, any disablement, and any action coupled to stopping a runner must require a typed confirmation phrase. While ADR 0004 remains in force, these paths remain locked rather than merely confirmed.

4. **Stop, kill, disable, and demote remain separate verbs.** Controlled stop asks a runner to end. Kill switch halts trading and requires manual reset. Disablement prevents future use only when implemented as an enforced config/runtime state. Demotion records lifecycle reversal. The Bench may compose workflows, but the UI must name which verbs will happen before the operator confirms.

5. **Existing demotion mechanics remain the backend path.** Confirmed demotions call the same promotion state-machine semantics as the CLI and produce the same governance artifact. The Bench must not create a parallel demotion write path.

## Rationale

Action-menu gestures are valuable because they reduce ceremony for planning. They are dangerous if the gesture itself becomes the authorization. A governance confirmation preserves the action-menu's ergonomic intent while keeping Milodex's audit and human-review rules intact.

The distinction between demotion and disablement is especially important today because ledger-only disablement is not a runtime halt. The UI must not claim a strategy has been made safe unless the backend has actually enforced that state.

## Consequences

- **ADR 0036 Q-D is answered.**
- The first write-capable Bench PR may stub the Action menu demotion path before wiring demotion submission.
- Demotion implementation needs tests proving an action-menu gesture alone does not mutate durable state.
- Any future stop-plus-demote or disable-plus-demote workflow must declare both side effects in the governance confirmation.
- Live and micro-live demotions stay locked while ADR 0004 locks those stages.

## Non-goals

- Does not implement demotion UI.
- Does not authorize live or micro-live operations.
- Does not implement runtime-enforced disablement.
- Does not change kill-switch reset semantics.
