# ADR 0048 — Bench uses vertical stage sections with natural scroll

**Status:** Accepted - 2026-05-12
**Supersedes:** [ADR 0045](0045-kanban-responsive-layout-uses-horizontal-board-scroll.md) — the surface is no longer a horizontal board, so horizontal scroll is structurally moot.
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md), [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md) (Qt Quick), [ADR 0035](0035-design-system-and-theme-architecture.md), [ADR 0046](0046-bench-stage-hues-extend-production-tokens.md), [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md)

## Context

ADR 0045 addressed responsive layout for a five-column horizontal Kanban board, choosing stable column widths inside a horizontal `Flickable`. The Bench surface has moved away from that board model. Cross-stage drag is gone; state transitions flow through an `Action` menu. Without drag-to-promote, the horizontal multi-column layout no longer maps to any interaction. The left-to-right pipeline metaphor is not load-bearing when the operator's primary action surface is a per-row menu.

A vertical layout resolves the column-width stability problem by eliminating it: each stage occupies a full-content-area-width section, and rows within a section align to a stable token grid.

## Decision

1. **The Bench surface is a vertical stack of five stage sections.** Sections follow the promotion pipeline order top-to-bottom: `IDLE → BACKTEST → PAPER → MICRO-LIVE → LIVE`. Each section spans the full content area width.

2. **Section heights are asymmetric by design.** Strategy counts vary by stage. A full funnel has more strategies at IDLE than at LIVE, and that shape is information the operator should see. It is not a layout problem to compensate for.

3. **Scrolling is vertical and native.** The surface uses a standard vertical `ScrollView`. There is no horizontal `Flickable` and no edge-scroll complexity.

4. **Row-column tokens remain the alignment contract within each row.** Design-system tokens such as `column.pill` and `column.metric` govern stable slot widths inside each strategy row. The absence of fixed-width stage columns at the surface level does not remove alignment discipline at the row level.

## Rationale

Vertical sections match how the operator reads a funnel: top-to-bottom by pipeline stage, dense where there is more work, sparse where the pipeline narrows. A horizontal board served the drag-to-promote interaction; without that interaction it adds scrolling complexity without contributing to comprehension.

Native vertical scroll is the expected behavior for a page-height list in any desktop operating environment. It requires no bespoke implementation and carries no edge-scroll or touch-event concern.

Asymmetric section heights are honest. A padded-to-equal-height layout would suggest the operator has the same number of strategies at every stage, which is false for any real funnel state.

## Consequences

- **ADR 0036 Q-F is now answered by this ADR.** ADR 0045 is superseded.
- The horizontal `Flickable` / `ScrollView` board is not implemented.
- Stage-section headers replace column headers. Roman-numeral ticks and per-stage hue identity from the v0.3 prototype visual spec carry forward to section headers.
- Per-row alignment tokens (`column.pill`, `column.metric`, etc.) from the design system are the stability contract; they apply within rows, not across a surface-level column grid.
- Future layout experiments (overview panel, filtered single-section view) are not blocked by this decision.

## Non-goals

- Does not specify exact section header markup or row layout tokens.
- Does not change stage hue token decisions (see [ADR 0046](0046-bench-stage-hues-extend-production-tokens.md)).
- Does not implement within-section priority reorder drag (that mechanic survives from ADR 0036 Decision 2 and is implementation work).
