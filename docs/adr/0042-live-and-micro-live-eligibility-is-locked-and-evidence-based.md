# ADR 0042 - Live and micro-live eligibility is locked and evidence-based

**Status:** Accepted - 2026-05-10
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-C (live-eligibility window), [ADR 0004](0004-paper-only-phase-one.md) (paper-only lock), [ADR 0009](0009-promotion-pipeline-stage-model.md) (promotion stage model), [ADR 0020](0020-promotion-thresholds-are-code-invariants.md) (promotion thresholds), [PHASE6_OPERATOR_KANBAN_PREP.md](../PHASE6_OPERATOR_KANBAN_PREP.md)

## Context

The Kanban prototype includes copy such as "21 days to live-eligibility window" and "gates passing - micro-live ready." No production policy currently defines a calendar wait before live. The code still blocks `micro_live` and `live` promotion under ADR 0004, and the existing promotion posture is evidence-based: Sharpe, max drawdown, trade count, manifest discipline, and explicit operator approval.

Phase 6 needs the board to show eligibility honestly without accidentally authorizing real capital or implying that a calendar countdown can make a strategy live-ready.

## Decision

1. **ADR 0004 remains authoritative.** `micro_live` and `live` are locked until a future ADR explicitly supersedes ADR 0004. The Kanban may display those lanes as locked future stages, but it may not enable promotion or session-start actions into them.

2. **The prototype's calendar countdown is rejected as policy.** Phase 6 does not add a "21 days to live" or similar time-only eligibility window.

3. **Eligibility copy is evidence-based while the lock remains in force.** Cards may show missing evidence such as trade count, Sharpe, max drawdown, manifest drift, paper-session absence, or "locked by ADR 0004." They must not say "micro-live ready" or "live-ready" while the boundary is locked.

4. **A future live-opening ADR must define any additional observation window.** If Milodex later opens `micro_live` or `live`, that ADR must decide whether evidence must be measured by paper trades, live trades, minimum market sessions, rolling windows, or some combination. The Kanban cannot invent that policy in UI copy.

5. **The board distinguishes "gate passing" from "authorized."** A strategy may satisfy backtest or paper evidence thresholds and still be locked from capital-bearing stages by ADR 0004 or explicit human-review requirements.

## Rationale

Milodex's safety model is built around evidence and explicit authority, not elapsed time. A countdown is visually satisfying but it can imply that patience alone unlocks live capital. That is the wrong mental model for a trading system with a sacred risk layer and a mandatory promotion pipeline.

Keeping `micro_live` and `live` visible but locked is useful. The operator can see the ladder without the UI pretending those rungs are currently actionable.

## Consequences

- **ADR 0036 Q-C is answered.**
- Phase 6 Kanban read models need an `eligibility_verdict` that can express `locked`, `blocked`, `gate_passing`, and `unknown` without equating any of them to live authorization.
- Prototype copy mentioning live countdowns becomes visual voice only, not policy.
- Any future live-opening work must supersede ADR 0004 before UI controls become actionable.

## Non-goals

- Does not authorize `micro_live` or `live`.
- Does not change current promotion thresholds.
- Does not define future live-stage observation windows.
- Does not implement eligibility read models.
