# Phase 6 Operator Kanban Prep

**Status:** Planning artifact only. Phase 5 is closed; Phase 6 implementation
may begin only against the ADR decisions below. This document does not authorize
drag-to-promote, bulk backtest/session commands, demotion side effects, or
live-boundary movement by itself.

**Design context:** [ADR 0036](adr/0036-operator-kanban-surface-for-promotion-pipeline.md)
is the canonical visual spec. The Kanban surface implements the **BENCH** role in the
four-surface narrative ([DESIGN.md section 4](DESIGN.md)) and must respect the operative
principles in [DESIGN.md section 5](DESIGN.md): three voices never crossed, status colors
as nouns, no greetings/congratulations/recommendations, and column-reservation alignment.
Stage-hue tokens introduced by ADR 0036 Decision 3 are a separate ladder-location axis
from [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) status colors. [ADR 0046](adr/0046-kanban-stage-hues-extend-production-tokens.md)
settles the reconciliation: production tokens remain canonical, stage hues land as a
separate token namespace, and the existing parchment-dot texture may be reused through
QML-accessible tokens rather than raw prototype CSS.

## Decision Register

- **Display-name provenance:** decided by [ADR 0041](adr/0041-kanban-display-names-are-presentation-metadata.md). Add optional `strategy.display_name`; keep `strategy_id` as durable identity; expose display-name provenance in read models.
- **Stage versus session semantics:** decided by [ADR 0039](adr/0039-stage-session-and-kanban-lane-are-distinct.md). Promotion stage, runtime session state, and Kanban lane are distinct axes. `strategy_runs` remains the canonical foreground-runner liveness surface; `idle` is a lane label, not a promotion stage or session state.
- **Live/micro-live eligibility windows:** decided by [ADR 0042](adr/0042-live-and-micro-live-eligibility-is-locked-and-evidence-based.md). ADR 0004 remains authoritative; calendar countdowns are rejected; eligibility copy stays evidence-based and locked for capital-bearing stages.
- **Demotion security:** decided by [ADR 0043](adr/0043-kanban-demotion-gestures-open-a-governance-flow.md). A demotion drag opens a governance modal; drop alone never mutates durable state; stop, kill, disable, and demote remain separate verbs.
- **Drag validation timing:** decided by [ADR 0044](adr/0044-kanban-uses-cached-hover-validation-and-authoritative-drop-validation.md). Hover uses cached read-model verdicts; drop and job start revalidate authoritatively.
- **Responsive layout:** decided by [ADR 0045](adr/0045-kanban-responsive-layout-uses-horizontal-board-scroll.md). The board uses stable-width columns inside a horizontal scroll region; no wrap or mobile/tab mode in first implementation.
- **Bulk orchestration:** decided by [ADR 0040](adr/0040-kanban-bulk-orchestration-uses-a-durable-job-ledger.md). Bulk actions create durable parent/child orchestration jobs that link to `backtest_runs.run_id` or `strategy_runs.session_id`; cancellation is cooperative and no promotion/stage mutation happens as a side effect.
- **Token reconciliation:** decided by [ADR 0046](adr/0046-kanban-stage-hues-extend-production-tokens.md). Stage hues extend production tokens; status colors remain outcome nouns; prototype hex drift does not replace production palette.

## Implementation-ready Phase 6 Path

The first implementation PR should be a non-writing foundation PR:

1. Add the schema/read-model groundwork: optional `strategy.display_name`, Kanban card read-model fields for `strategy_id`, `display_name`, `display_name_source`, `promotion_stage`, `kanban_lane`, `session_state`, `eligibility_verdict`, and denial copy.
2. Add production stage tokens across all QML themes, expose them through `Theme.qml`, and update the design-system showcase. Do not inline prototype hex values.
3. Add the durable orchestration migrations and EventStore APIs for `orchestration_batches` and `orchestration_jobs`, including start-time revalidation hooks, but keep GUI buttons disabled or absent until the worker path exists.
4. Build a read-only Kanban board with stable-width columns inside horizontal scroll. It can show cached verdicts and locked-state copy, but drag/drop and bulk action submission remain disabled in this PR.

Explicitly out of scope for the first PR:

- drag-to-promote or drag-to-demote submission
- promotion, demotion, disablement, kill-switch reset, or runner stop side effects
- `micro_live` or `live` authorization, session start, or countdown copy
- daemon, auto-restart, or unattended service behavior
- mobile, wrapped-column, or tabbed Kanban layout
- automatic promotion after a completed backtest or session job

Until those implementation pieces land, the Phase 5 `BENCH` surface remains
view-only: it may show evidence and detail context, but it may not mutate
strategy state.
