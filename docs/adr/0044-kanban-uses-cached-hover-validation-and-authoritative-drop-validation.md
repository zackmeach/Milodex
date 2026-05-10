# ADR 0044 - Kanban uses cached hover validation and authoritative drop validation

**Status:** Accepted - 2026-05-10
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-E (hover-validation timing), [ADR 0039](0039-stage-session-and-kanban-lane-are-distinct.md), [ADR 0040](0040-kanban-bulk-orchestration-uses-a-durable-job-ledger.md), [ADR 0008](0008-risk-layer-veto-architecture.md), [ADR 0009](0009-promotion-pipeline-stage-model.md)

## Context

The prototype implies immediate feedback when a card is dragged over a target column, but production validation is policy-heavy. Promotion gates depend on strategy stage, evidence, manifest drift, live locks, human-review requirements, active sessions, and future orchestration jobs. Running authoritative validation on every pointer move would couple UI animation to domain logic and risk stale or partial decisions.

## Decision

1. **Hover feedback uses cached read-model verdicts.** Kanban cards expose an `eligibility_verdict` and denial copy computed before the drag begins or refreshed on a coarse cadence. The hover state may tint a target lane and show concise refusal copy from that cache.

2. **Drop validation is authoritative.** On drop, the backend re-validates the requested transition or action using current config, promotion evidence, manifests, kill-switch state, locks, and job/session state. A stale hover-allowed state does not authorize the drop.

3. **Start-time validation is also required for jobs.** Durable jobs created by the Kanban must re-read current authority before starting execution. Queue-time `requested_stage` is audit context, not permission to run later.

4. **Denials are visible and specific.** A refused drop snaps the card back and records/shows the domain reason, such as locked by ADR 0004, insufficient trades, manifest drift, active non-terminal job, or requires governance confirmation.

5. **Hover validation must be side-effect free.** It never writes lanes, jobs, promotions, sessions, risk state, or audit rows.

## Rationale

This gives the operator the fast feedback that makes drag-and-drop usable while keeping the domain model in charge. Cached hover validation is a courtesy. Drop and start validation are authority.

The double validation also handles the normal race where a strategy's stage, evidence, or lock state changes while the operator is dragging.

## Consequences

- **ADR 0036 Q-E is answered.**
- Kanban read models need precomputed verdicts and denial copy.
- The write path must treat every drop or job start as a fresh command validation.
- Tests should cover stale hover verdicts being refused on drop/start.
- The UI may feel permissive for a fraction of a second, but durable state remains correct.

## Non-goals

- Does not implement the validator.
- Does not change promotion/risk rules.
- Does not authorize any live or micro-live action.
