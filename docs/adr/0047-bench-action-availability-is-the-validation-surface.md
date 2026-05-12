# ADR 0047 — Bench action availability is the validation surface

**Status:** Accepted - 2026-05-12
**Supersedes:** [ADR 0044](0044-kanban-uses-cached-hover-validation-and-authoritative-drop-validation.md) — drag-as-state-change is gone; validation is now computed pre-menu, not at drop time.
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md), [ADR 0039](0039-stage-session-and-bench-section-are-distinct.md), [ADR 0040](0040-bench-bulk-orchestration-uses-a-durable-job-ledger.md), [ADR 0004](0004-paper-only-phase-one.md), [ADR 0005](0005-kill-switch-manual-reset.md), [ADR 0008](0008-risk-layer-veto-architecture.md), [ADR 0009](0009-promotion-pipeline-stage-model.md), [ADR 0042](0042-live-and-micro-live-eligibility-is-locked-and-evidence-based.md)

## Context

ADR 0044 addressed validation timing for a drag-based interaction model: hover used cached verdicts; drop revalidated authoritatively. The Bench surface has moved away from drag-as-state-change. Cross-stage drag is gone. State transitions now flow through an `Action` menu on each row. That shift eliminates the hover-feedback problem entirely and changes where validation is expressed: it is now the *content* of the menu, not the *response* to a drop.

The operator still needs to know what actions are available at a glance, without committing to any action. The evidence behind a gate decision needs to be reachable without cluttering the row's default rendering.

## Decision

1. **Each row's `Action` menu is computed from current state before the operator opens it.** The menu is derived from promotion stage, runtime session state, gate evidence, kill-switch state, and the ADR 0004 paper-only lock. The computation runs as a precomputed read-model verdict on the same cadence used by the cached-verdict mechanism in ADR 0044.

2. **Unavailable actions are hidden, not disabled.** The operator never sees a forbidden path at the menu level. A hidden-action menu is shorter and unambiguous. Gate-failure context lives in the evidence modal, reachable from any row at any time, so the absence of an action is never opaque.

3. **Jobs that have been queued must revalidate at start time.** A snapshot decision at menu-open time is not authoritative for execution. Only start-time revalidation is. This carries forward ADR 0044 Decision 3 unchanged.

4. **Cached verdicts remain the data source for computing action availability.** The concept from ADR 0044 survives as the mechanism behind menu computation. It no longer surfaces as hover-on-drop feedback; it surfaces as the set of visible menu items.

## Rationale

Hiding unavailable actions rather than disabling them keeps the menu proportional to what the operator can actually do. Disabled items answer a question the operator did not ask ("why can't I do this?"); the evidence modal answers it directly and at a point when the operator is ready to engage with it.

Precomputed verdicts avoid coupling the menu-open event to live domain queries on the Qt main thread. The cadence of verdict refresh is an implementation concern; what matters here is that the menu reflects current state, not that it computes from scratch on each open.

Carrying forward the start-time revalidation rule from ADR 0044 is mandatory: state can change between menu-open and execution, especially for queued jobs where the gap may be minutes or longer.

## Consequences

- **ADR 0036 Q-E is now answered by this ADR.** ADR 0044 is superseded.
- The read-model layer must produce a per-row action set as part of its precomputed output, not just eligibility verdicts.
- The evidence modal is a required surface. The absence of visible actions is not self-explanatory without it.
- The write path continues to treat every job start as a fresh command validation, same as ADR 0044 Decision 3.
- Tests should cover menu content (which actions appear) as a function of strategy state, not just whether operations succeed or fail.

## Non-goals

- Does not implement the `Action` menu or evidence modal.
- Does not change promotion or risk rules.
- Does not authorize any live or micro-live action.
