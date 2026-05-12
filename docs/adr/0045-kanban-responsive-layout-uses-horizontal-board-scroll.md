# ADR 0045 - Kanban responsive layout uses horizontal board scroll

**Status:** Superseded - 2026-05-10
**Superseded by:** [ADR 0048](0048-bench-uses-vertical-stage-sections-with-natural-scroll.md) — the surface is no longer a horizontal board, so horizontal scroll is structurally moot.
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-F (responsive layout), [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md) (Qt Quick), [ADR 0035](0035-design-system-and-theme-architecture.md), [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md), [DESIGN.md](../DESIGN.md)

## Context

The Kanban prototype is a five-column board. At comfortable desktop widths it reads as one editorial surface. At 1280px laptop widths, five full columns cannot fit without either shrinking cards below useful scan density, wrapping columns, or introducing a navigation model.

Qt Quick can support horizontal board scrolling with fixed column widths, and Milodex's design-system already favors reserved columns and stable dimensions over content-driven resizing.

## Decision

1. **The Phase 6 Kanban uses a horizontally scrollable board.** The masthead and global controls remain fixed in the vertical page flow; the five stage columns live inside a horizontal `Flickable`/`ScrollView` region.

2. **Columns keep stable widths.** The implementation defines Kanban-specific width tokens for columns, cards, gutters, and metric rows rather than shrinking text or changing typography based on viewport width.

3. **No column wrapping for Phase 6.** Wrapping breaks the left-to-right promotion ladder and makes drag targets jump between rows. It is out of scope for the first implementation.

4. **No tabbed or single-column mobile mode for Phase 6.** Milodex remains a desktop operator surface. Narrow windows may scroll; they do not need a separate mobile interaction model.

5. **Drag behavior must account for scroll.** Auto-scroll near board edges and stable drop zones are part of the implementation contract before cross-column drag ships.

## Rationale

Horizontal scroll is the least surprising compromise for a board whose primary meaning is left-to-right progression. It preserves the visual spec and the operator's spatial memory without inventing a second layout.

Shrinking the board to fit would damage the dense evidence cards. Wrapping would make the stage ladder harder to reason about exactly when the operator is making consequential decisions.

## Consequences

- **ADR 0036 Q-F is answered.**
- The first Kanban PR should establish board layout and scroll mechanics before write-capable drag.
- Accessibility and keyboard navigation need horizontal-scroll affordances.
- Future responsive experiments can add overview/minimap or column focus, but not before the base board is stable.

## Non-goals

- Does not implement a mobile UI.
- Does not choose exact pixel widths.
- Does not implement drag auto-scroll.
