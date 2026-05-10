# ADR 0043 - Kanban demotion gestures open a governance flow

**Status:** Accepted - 2026-05-10
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-D (demotion-gesture security), [ADR 0005](0005-kill-switch-manual-reset.md) (manual reset), [ADR 0009](0009-promotion-pipeline-stage-model.md) (promotion stage model), [ADR 0039](0039-stage-session-and-kanban-lane-are-distinct.md), [PROMOTION_GOVERNANCE.md](../PROMOTION_GOVERNANCE.md)

## Context

The prototype makes promotion and demotion feel like the same drag gesture. Operationally they are not equivalent. Demotion can stop work, revoke eligibility, or record a lifecycle reversal. Existing promotion demotion requires reason and approval metadata, and `to_stage='disabled'` is currently ledger-only: it does not by itself rewrite YAML or guarantee a running process has stopped.

Phase 6 needs a gesture policy that preserves the Kanban's direct manipulation without turning drag-and-drop into an unaudited lifecycle mutation.

## Decision

1. **A demotion drag is a request, not a mutation.** Dropping a card into a lower lane opens a governance modal. No stage, config, job, runner, or ledger state changes merely because the card was dropped.

2. **The modal must collect explicit operator intent.** At minimum it captures target stage, reason, approved-by identity, and links to supporting evidence or incident context when available.

3. **Capital-affecting or safety-affecting demotions require typed confirmation.** Any future demotion from `live` or `micro_live`, any disablement, and any action coupled to stopping a runner must require a typed confirmation phrase. While ADR 0004 remains in force, these paths remain locked rather than merely confirmed.

4. **Stop, kill, disable, and demote remain separate verbs.** Controlled stop asks a runner to end. Kill switch halts trading and requires manual reset. Disablement prevents future use only when implemented as an enforced config/runtime state. Demotion records lifecycle reversal. The Kanban may compose workflows, but the UI must name which verbs will happen before the operator confirms.

5. **Existing demotion mechanics remain the backend path.** Confirmed demotions call the same promotion state-machine semantics as the CLI and produce the same governance artifact. The Kanban must not create a parallel demotion write path.

## Rationale

Direct manipulation is valuable because it reduces ceremony for planning. It is dangerous if the gesture itself becomes the authorization. A modal preserves the drag's ergonomic intent while keeping Milodex's audit and human-review rules intact.

The distinction between demotion and disablement is especially important today because ledger-only disablement is not a runtime halt. The UI must not claim a strategy has been made safe unless the backend has actually enforced that state.

## Consequences

- **ADR 0036 Q-D is answered.**
- The first write-capable Kanban PR may support drag-to-open-modal before it supports final demotion submission.
- Demotion implementation needs tests proving drop alone does not mutate durable state.
- Any future stop-plus-demote or disable-plus-demote workflow must declare both side effects in the confirmation modal.
- Live and micro-live demotions stay locked while ADR 0004 locks those stages.

## Non-goals

- Does not implement demotion UI.
- Does not authorize live or micro-live operations.
- Does not implement runtime-enforced disablement.
- Does not change kill-switch reset semantics.
