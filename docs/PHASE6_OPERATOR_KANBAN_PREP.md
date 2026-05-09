# Phase 6 Operator Kanban Prep

**Status:** Planning artifact only. Phase 5 must not implement drag-to-promote,
bulk backtest/session commands, demotion gestures, or live-boundary movement.

**Design context:** [ADR 0036](adr/0036-operator-kanban-surface-for-promotion-pipeline.md)
is the canonical visual spec. The Kanban surface implements the **BENCH** role in the
four-surface narrative ([DESIGN.md §4](DESIGN.md)) and must respect the operative
principles in [DESIGN.md §5](DESIGN.md) — three voices never crossed, status colors as
nouns, no greetings/congratulations/recommendations, column-reservation alignment.
Stage-hue tokens introduced by ADR 0036 §Decision 3 are an extension of
[DESIGN_SYSTEM.md §6](DESIGN_SYSTEM.md) status-color policy and must be reconciled
into the design system before Phase 6 implementation begins.

Before implementation, Phase 6 must decide:

- Display-name provenance: config field, derived name, or metadata table.
- Stage versus session semantics: promotion stage must stay separate from runner liveness.
- Eligibility windows: live/micro-live rules must be policy, not UI copy.
- Demotion security: any capital-affecting move needs explicit confirmation.
- Drag validation timing: hover feedback versus drop-only refusal.
- Responsive layout: five columns need a 1280px strategy before QML porting.
- Bulk orchestration: backtest/session actions need progress, cancellation, and error reporting.
- Token reconciliation: stage hues and dot-grain treatment must land in the design system.

Until those decisions land, the Phase 5 `BENCH` surface is view-only: it may
show evidence and detail context, but it may not mutate strategy state.
