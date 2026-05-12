# ADR 0046 — Bench stage hues extend production tokens

**Status:** Accepted · 2026-05-10
**Related:** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) Q-H (token reconciliation), [ADR 0035](0035-design-system-and-theme-architecture.md), [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md), [DESIGN.md](../DESIGN.md)

## Context

The Bench prototype introduced stage hues and slightly brighter color values than production Editorial Dark. Production already has a token system with three themes, status colors as nouns, and a rule that QML binds to `Theme` rather than raw hex. It also already defines a parchment dot texture in the design-system doc, though the stage hue namespace does not yet exist in QML.

Phase 6 needs the visual richness of stage identity without letting prototype hex values become a parallel design system.

## Decision

1. **Production tokens are canonical.** Prototype palette drift does not replace existing `color.brand.*`, `color.text.*`, or `status.*` values.

2. **Stage hues become a separate token namespace.** The design system gains `stage.idle`, `stage.backtest`, `stage.paper`, `stage.microLive`, and `stage.live` tokens for every theme. They are for promotion-ladder identity, not positive/negative outcome state.

3. **Status colors remain outcome nouns.** Gate pass/fail, blocked, warnings, kill switch, and P&L continue to use `status.*`. Stage hue must not be used to imply safety, profitability, or approval.

4. **Dot-grain is already accepted as a texture token.** Phase 6 may use the existing `texture.parchment.dot` pattern where appropriate, but implementation must add QML-accessible texture support rather than hardcoding CSS-like strings.

5. **The Bench QML implementation waits for token implementation.** No Bench QML file should inline prototype hex colors or bespoke grain values. Stage tokens land in `Theme.qml`, all theme files, the design-system doc, and the showcase before the board depends on them.

## Rationale

The prototype is a visual contract, not a token fork. A separate stage namespace preserves the board's editorial color language while protecting the existing status semantics that carry risk and evidence meaning.

Keeping stage color distinct from outcome color matters because a `live` hue is not a recommendation, a `paper` hue is not a successful result, and a `backtest` hue is not a warning. They are locations on the ladder.

## Consequences

- **ADR 0036 Q-H is answered.**
- The first implementation PR should add stage tokens before adding Phase 6 Bench stage colors that need them.
- The Bench surface must bind to `stage.*` tokens, not prototype hex values.
- Prototype hex values may inspire the theme-specific palette but are not copied blindly.
- Design-system docs should clarify stage-vs-status semantics.

## Non-goals

- Does not implement the token changes.
- Does not redesign existing status colors.
- Does not change the three-theme architecture.
