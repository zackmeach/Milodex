# Milodex Design System

**Status:** Accepted &middot; 2026-05-07 &middot; v0.1
**Phase:** 5 (open per [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md))
**Architecture:** [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md) (PySide6 + Qt Quick), [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) (observability-first ordering), [ADR 0035](adr/0035-design-system-and-theme-architecture.md) (this design system + theme architecture)

This document is the canonical reference for Milodex's GUI design tokens, components, and themes. It is the spec the first Phase 5 GUI implementation PR builds against, and the artifact subsequent PRs cite when they extend it. When a GUI implementation choice is ambiguous, this document is the binding answer; if the document is wrong, the answer is to update the document, not to deviate silently.

The design system applies to every Qt Quick / QML surface in Milodex. CLI surfaces are unaffected.

## What this doc is

- The token set: every named design value (color, type, spacing, motion, elevation) the GUI may reference.
- The theme catalog: three themes (Editorial Dark, Editorial Light, Bronze), each defining concrete values for every token.
- The component principles: how components consume tokens; which components are foundational; what "good" looks like.
- The architecture: the Theme singleton pattern, hot-swap mechanism, persistence model, font-loading contract.
- The maintenance conventions: when to update, how to add a new theme, how to add a new component.

## What this doc is *not*

- A QML coding tutorial. Implementation patterns are sketched here; full QML conventions live alongside the source.
- A pixel-perfect spec for every screen. Surface-by-surface design lands as the surfaces ship; this doc gives those PRs the tokens to compose against.
- Permission to change the runtime ([ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md)) or scope ([ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md)). Those are upstream decisions.
- A brand identity exercise outside of Milodex. The system is scoped to Milodex's GUI surfaces.

---

## 1. Foundations

### 1.1 Direction: Editorial press

Milodex's GUI takes its aesthetic cue from financial-publishing tradition rather than from modern fintech defaults. The reference points are FT Weekend, the Wall Street Journal print edition, and the Economist &mdash; long-form, considered, evidence-led publications that have spent more than a century learning how to present complex financial information legibly. The platform's existing artifacts (walk-forward reports, gate-refusal narratives, strategy bank documentation, ADRs) already read like serious technical writing; the GUI surfaces this in visual form.

The direction is *not* a literal print-publication imitation. It borrows the legibility tradition (serif display + sans body + mono data, generous baseline rhythm, considered restraint) and applies it to a screen-native dark UI. The result reads as "considered, durable, instrument-grade" rather than "yet another modern dashboard."

### 1.2 Three themes

Same components, same tokens, three palettes. Hot-swappable at runtime via a property-binding pattern (see &sect;9.3).

| Theme | Role | Surface | Identity |
|---|---|---|---|
| **Editorial Dark** | Default | Warm-tinted near-black `#0a0907` | Parchment cream `#e6cf99` + oxblood `#722f37` |
| **Editorial Light** | Daytime variant | Cream-beige `#f5efe1` | Deep brown `#2a2218` + oxblood `#722f37` |
| **Bronze** | Alternate direction | Warm-tinted near-black `#0d0c0a` | Patinated bronze `#a68063` + verdigris `#5e8b7e` |

Editorial Dark and Editorial Light are the same aesthetic in inverted contrast &mdash; literally what publications look like on a screen vs. on paper. Bronze is a separate aesthetic story (workshop / craft-tool / patinated metal) that demonstrates the theme machinery and gives variety without diverging the structural design.

### 1.3 Type pairings

Three roles, two pairings:

| Role | Editorial (Dark + Light) | Bronze |
|---|---|---|
| **Display serif** &middot; surface titles, narrative emphasis | **Newsreader** | **Newsreader** (treated heavier) |
| **Body sans** &middot; UI chrome, buttons, navigation, body | **Public Sans** | **Public Sans** |
| **Data mono** &middot; numerics, identifiers, code-adjacent | **JetBrains Mono** | **JetBrains Mono** |

All three families are free, all three load via `QFontDatabase.addApplicationFont()` from `assets/fonts/` (see &sect;9.4).

The three themes share the same family set; they vary only in *treatment*. Bronze uses the same Newsreader but at heavier optical weights and with letter-spaced uppercase for surface titles &mdash; an "industrial nameplate" reading of the same font &mdash; so structural type roles never diverge.

---

## 2. Type Scale

Six display steps, four body steps, three data steps. All sizes in pixels (Qt Quick's natural unit).

| Token | Family | Size / line-height | Weight | Use |
|---|---|---|---|---|
| `type.display.xl` | Newsreader | 64 / 1.05 | 500 | Hero / feature surfaces (rare) |
| `type.display.lg` | Newsreader | 36 / 1.10 | 500 | Page titles |
| `type.display.md` | Newsreader | 24 / 1.20 | 500 | Section titles ("Strategy Bank") |
| `type.display.sm` | Newsreader | 18 / 1.30 | 500 | Subsection titles |
| `type.display.sm.italic` | Newsreader | 18 / 1.30 | 400 italic | Editorial accents ("No. 6 in paper") |
| `type.body.lg` | Public Sans | 14 / 1.50 | 400 | Primary body text |
| `type.body.md` | Public Sans | 13 / 1.50 | 400 | Default UI text, dense forms |
| `type.body.sm` | Public Sans | 12 / 1.45 | 400 | Captions, secondary metadata |
| `type.label.xs` | Public Sans | 10 / 1.40 | 500 + `0.12em` letter-spacing + uppercase | Section labels ("STRATEGY BANK") |
| `type.data.md` | JetBrains Mono | 13 / 1.60 | 400 + `tnum` | Default tabular data |
| `type.data.sm` | JetBrains Mono | 11 / 1.60 | 400 + `tnum` | Compact tables |
| `type.data.xs` | JetBrains Mono | 10 / 1.55 | 400 + `tnum` | Very dense tables, header rows |

**Tabular figures (`tnum`) are mandatory** for any data role. Strategy Bank rows, Sharpe ratios, trade counts, P&amp;L &mdash; all must align numerically. Set via `font.features: ["tnum"]` in QML. Without this, monospaced numbers still drift across rows in proportional-figure mode.

**Italic discipline.** Italic Newsreader is an editorial accent only (e.g., "No. 6 in paper", "Sharpe 0.327", inline citations). Buttons, navigation, dense labels never italicize. Status pills never italicize. Body text rarely italicizes.

---

## 3. Color Tokens

Color tokens are defined as *semantic roles*. Each theme provides concrete values for every role; component code references roles, never raw hex values. Status colors are a constrained exception &mdash; see &sect;6.

### 3.1 Editorial Dark (default)

#### Surface &amp; structure

| Token | Hex | Use |
|---|---|---|
| `color.surface.canvas` | `#0a0907` | Page background, root window |
| `color.surface.base` | `#100d09` | Cards, panels, content surfaces |
| `color.surface.raised` | `#14110d` | Elevated surfaces (dialogs, popovers, hovered rows) |
| `color.border.subtle` | `#1f1a12` | Hairline dividers, low-emphasis borders |
| `color.border.default` | `#2a2218` | Default panel borders, button outlines |
| `color.border.emphasis` | `#4a3d28` | Hover/focus borders, emphasized cards |

#### Text &amp; brand

| Token | Hex | Use |
|---|---|---|
| `color.brand.primary` | `#e6cf99` | Surface titles, primary brand accent (parchment) |
| `color.brand.accent` | `#722f37` | Primary buttons, selection rings, key links (oxblood) |
| `color.text.primary` | `#d8c5a3` | Default body text, table content |
| `color.text.secondary` | `#a89070` | Secondary text, italics, metadata |
| `color.text.muted` | `#6b5d44` | Captions, disabled context, placeholder |
| `color.text.disabled` | `#3d342a` | Truly disabled UI |

#### Texture (optional)

| Token | Value | Use |
|---|---|---|
| `texture.parchment.dot` | `radial-gradient(rgba(230,207,153,0.04) 1px, transparent 1px); background-size: 3px 3px;` | Subtle parchment grain over surfaces. Used sparingly; default is off. |

### 3.2 Editorial Light

Same semantic roles, inverted contrast.

| Token | Hex | Notes |
|---|---|---|
| `color.surface.canvas` | `#f5efe1` | Cream-beige paper |
| `color.surface.base` | `#ede5d4` | Tinted surface |
| `color.surface.raised` | `#e2d8c2` | Elevated |
| `color.border.subtle` | `#ddd2bc` | |
| `color.border.default` | `#c2b596` | |
| `color.border.emphasis` | `#9a8967` | |
| `color.brand.primary` | `#2a2218` | Deep brown (text-on-paper) |
| `color.brand.accent` | `#722f37` | Same oxblood &mdash; works on cream |
| `color.text.primary` | `#2a2218` | |
| `color.text.secondary` | `#6b5d44` | |
| `color.text.muted` | `#8a7c5e` | |
| `color.text.disabled` | `#bbae8c` | |

### 3.3 Bronze

Workshop / craft-tool / patinated-metal aesthetic.

| Token | Hex | Notes |
|---|---|---|
| `color.surface.canvas` | `#0d0c0a` | Warm dark with bronze tilt |
| `color.surface.base` | `#19170f` | |
| `color.surface.raised` | `#22201a` | |
| `color.border.subtle` | `#28241b` | |
| `color.border.default` | `#3d3625` | |
| `color.border.emphasis` | `#5a5036` | |
| `color.brand.primary` | `#a68063` | Bronze |
| `color.brand.accent` | `#5e8b7e` | Verdigris (oxidized-copper green) |
| `color.text.primary` | `#e0d4bd` | |
| `color.text.secondary` | `#a89070` | |
| `color.text.muted` | `#7e7565` | |
| `color.text.disabled` | `#3d3525` | |

---

## 4. Spacing Scale

Base unit 4px. Eight steps. Tight enough for dense data tables, generous enough for editorial breathing room.

| Token | Value | Typical use |
|---|---|---|
| `space.0` | `0px` | Reset, no gap |
| `space.1` | `4px` | Tight intra-component padding |
| `space.2` | `8px` | Default inline gap, button padding |
| `space.3` | `12px` | Component padding, list item rhythm |
| `space.4` | `16px` | Default panel padding, section padding |
| `space.5` | `24px` | Large panel padding, section separation |
| `space.6` | `32px` | Major section breaks |
| `space.7` | `48px` | Hero spacing, feature surface padding |
| `space.8` | `64px` | Page-margin scale, atmospheric whitespace |

Spacing tokens apply to padding, margin, and gap. Never use literal pixel values in QML &mdash; always reference a token. If a value is needed that doesn't exist in the scale, the answer is to add a token (with PR-level discussion), not to drop in a one-off literal.

**Border radius** uses a separate, smaller scale:

| Token | Value | Use |
|---|---|---|
| `radius.sm` | `3px` | Pills, inline badges |
| `radius.md` | `4px` | Buttons, default surfaces |
| `radius.lg` | `6px` | Cards, dialogs |
| `radius.xl` | `8px` | Hero / feature surfaces |
| `radius.full` | `9999px` | Avatars, status dots |

---

## 5. Motion

Three duration tiers, two easing curves. Editorial direction reads "considered, not flashy" &mdash; restrained motion is part of the discipline.

### 5.1 Durations

| Token | Value | Use |
|---|---|---|
| `motion.fast` | `120ms` | Hover states, micro-feedback, pressed states. Should feel instant but not snap. |
| `motion.standard` | `220ms` | Surface transitions, panel reveals, dialog open/close. The default. |
| `motion.deliberate` | `400ms` | Theme swap, route changes, large-surface transitions. Should feel like a page turn, not a snap. |

### 5.2 Easings

| Token | Cubic-bezier | Use |
|---|---|---|
| `ease.standard` | `cubic-bezier(0.4, 0, 0.2, 1)` | Default. Material-like, slight asymmetry &mdash; quick-out, gentle-in. |
| `ease.editorial` | `cubic-bezier(0.32, 0.72, 0, 1)` | Considered, page-turn-feeling. Surface transitions, large reveals. |

QML implements via `Behavior on <property> { NumberAnimation { duration: Theme.motion.standard; easing.bezierCurve: Theme.ease.editorial } }`. See &sect;9.3 for binding pattern.

### 5.3 What does *not* animate

- The kill-switch state. State changes are instant. Animation here would obscure operational urgency.
- Critical-error dialogs. Open instantly.
- Strategy-row data updates within a paper session. Numbers update without transition; flicker is honest, smooth interpolation hides what's happening.
- Theme swap of any P&amp;L number. The number's *position* may animate; the *value* never animates from one number to another &mdash; that risks misreading mid-transition.

---

## 6. Status Colors

Status colors are the load-bearing semantic dimension of a trading-platform GUI: positive vs. negative P&amp;L, gate-passing vs. gate-blocked, paper vs. live, kill-switch state. They are also where convention (red/green/amber) collides with editorial coherence.

**Decision:** Status colors are *theme-tinted* &mdash; each theme provides a hue that fits its palette &mdash; but the *role* (positive / warning / negative / info) is stable across themes. Components reference roles, never raw hex.

### 6.1 Roles

| Role | Editorial Dark | Editorial Light | Bronze | Meaning |
|---|---|---|---|---|
| `status.positive` | `#9bb89e` muted sage | `#4a7a4d` deep moss | `#5e8b7e` verdigris | Positive P&amp;L, gate passing, paper-active |
| `status.warning` | `#c4965a` mustard | `#8b6510` deep mustard | `#c4965a` mustard | Marginal, gate-narrow, attention-required |
| `status.negative` | `#d97757` rust | `#a04020` deep rust | `#a04020` deep rust | Negative P&amp;L, gate failing, kill-switch fired |
| `status.info` | `#6c89a3` ink | `#3a5474` deep ink | `#6c89a3` ink | Neutral information, backtest stage |

### 6.2 Why not raw red / green / amber

A pure RGB-saturation green and red work for a dashboard whose palette is also high-saturation (Bloomberg, Trading View). They clash with the muted parchment-and-oxblood palette of Editorial Dark; the eye reads them as bolt-on signal lights rather than part of the considered design. The muted-sage / mustard / rust set carries the same semantic information at the same contrast levels but stays inside the palette.

This is a deliberate departure from fintech convention. The semantic clarity (positive vs. negative still reads instantly at a glance) is preserved; the visual coherence is improved.

### 6.3 Status pills

Token references for the four built-in pill variants:

```
.pill-paper      { background: status.positive @ 0.12; color: status.positive; border: status.positive @ 0.30 }
.pill-backtest   { background: status.info     @ 0.12; color: status.info;     border: status.info     @ 0.30 }
.pill-blocked    { background: status.warning  @ 0.12; color: status.warning;  border: status.warning  @ 0.30 }
.pill-killed     { background: status.negative @ 0.12; color: status.negative; border: status.negative @ 0.30 }
```

`@ 0.12` and `@ 0.30` denote alpha blends of the role color against the surface. QML implements via `Qt.alpha()` or pre-computed values bound to the Theme singleton.

---

## 7. Components (initial set)

These four foundational components ship in the first design-system PR. Subsequent observability surfaces compose against them.

### 7.1 Buttons

Four variants. All use `type.body.md` weight 500, `space.2` x `space.3` padding, `radius.md`, `motion.fast` hover transitions.

| Variant | Background | Color | Border |
|---|---|---|---|
| `button.primary` | `color.brand.accent` | `#f5e6c4` (high-contrast cream) | none |
| `button.secondary` | transparent | `color.text.primary` | `1px color.border.default` |
| `button.ghost` | transparent | `color.text.secondary` | none |
| `button.danger` | transparent | `color.status.negative` | `1px` (deep rust) |

Hover state lightens primary background, brightens secondary border, intensifies ghost color, intensifies danger border. Pressed state darkens by `motion.fast`.

### 7.2 Status pills

See &sect;6.3. Compact, rounded, role-keyed.

### 7.3 Strategy rows

The load-bearing component for the Strategy Bank surface. Anatomy:

```
[strategy-id mono]    [stage-pill]   [primary-metric mono]   [trade-count mono muted]
```

Tokens: `type.data.md` for IDs and metrics, `type.data.sm` muted for trade count, `space.5` (24px) inter-column gap, `space.3` horizontal padding, `space.2` vertical, `radius.md`, `color.surface.base` background, `color.border.subtle` border, hover -> `color.surface.raised` + `color.border.default`.

Selected/active state: `2px` left border in `color.brand.accent`, otherwise unchanged.

### 7.4 Surface containers

The default panel/card. Background `color.surface.base`, border `1px color.border.subtle`, `radius.lg`, `space.5` padding. Hover (when interactive) -> `color.border.default`.

---

## 8. Status colors in motion

**Color transitions on status changes are forbidden.** When a strategy moves from `paper` to `kill-switch-fired`, the pill changes instantly from `pill-paper` to `pill-killed`. No fade. The instantaneous change is honest signal &mdash; the operator needs to see the state flip the moment it happens, not interpret a color crossfade.

**The kill-switch banner does not pulse, glow, or breathe.** Pulsing kill-switch indicators are an antipattern; they trade urgency for novelty and lose impact within a single session. The banner uses a stable, high-contrast static treatment.

---

## 9. Architecture

### 9.1 Theme singleton pattern

A single `Theme` QML singleton exposes every token as a property. Components reference tokens via `Theme.color.brand.accent`, `Theme.type.body.md`, etc. The singleton's properties are themselves bindings that read from the *active* theme file.

```qml
// QML/Theme.qml (singleton, registered via qmldir)
pragma Singleton
import QtQuick

QtObject {
    property QtObject color: activeTheme.color
    property QtObject type: typeRoles  // shared across themes
    property QtObject space: spaceScale  // shared
    property QtObject motion: motionTokens  // shared

    property QtObject activeTheme: ThemeManager.theme
    // ... theme switching logic in ThemeManager.qml
}
```

### 9.2 Token binding contract

**Every visual property of every component must be bound to a Theme token.** Hardcoded hex values, literal pixel values, literal duration numbers in component QML are forbidden. The contract is enforced socially (PR review) and structurally (a theme swap that doesn't fully cascade is a bug, not a feature).

A component that needs a value not in the token set requires a token-set extension PR before the component PR lands. This forces the design system to grow coherently rather than accumulate one-off literals.

### 9.3 Hot-swap mechanism

Theme switching is a single property change on the `ThemeManager` singleton:

```qml
// triggered by a UI affordance or settings change
ThemeManager.setTheme("editorial-light")
```

QML's property-binding system propagates the change. Components don't need explicit reload logic; their `Theme.color.x` references re-bind automatically. The animation on theme swap uses `motion.deliberate` + `ease.editorial` &mdash; the page-turn feel.

### 9.4 Persistence

The active theme is persisted in the durable state directory under `data/` per [ADR 0018](adr/0018-durable-state-directory-consolidation.md). A small JSON file (`data/gui_settings.json`) holds:

```json
{
  "theme": "editorial-dark",
  "version": 1
}
```

Persistence is operator-scoped (per-machine), not strategy-scoped. The theme is a presentation choice, not part of any audit trail.

### 9.5 Font loading

All three font families (Newsreader, Public Sans, JetBrains Mono) ship bundled with the application under `assets/fonts/`. The application loads them at QML init via `QFontDatabase.addApplicationFont()` before any QML surface renders.

```python
# milodex/gui/fonts.py (sketch)
from PySide6.QtGui import QFontDatabase
from pathlib import Path

FONTS_DIR = Path(__file__).parent.parent / "assets" / "fonts"

def load_fonts() -> None:
    for ttf in FONTS_DIR.rglob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))
    # Subsequent QML "font.family: 'Newsreader'" resolves correctly.
```

Fonts must be present in `assets/fonts/` before the design-system PR lands. Licenses (SIL Open Font License or similar permissive licenses for all three families) ship alongside as `LICENSES.md`.

### 9.6 Status-color theme-tinting

Status colors are populated by the active theme like any other token. Components that render a status pill or P&amp;L coloring read `Theme.status.positive`, etc. This is the architectural enforcement of &sect;6's policy &mdash; component code does not branch on theme name to choose status hues.

---

## 10. Component principles

**1. Components consume tokens, never raw values.** See &sect;9.2.

**2. Components are theme-blind.** A component does not know which theme is active; it asks the singleton for tokens and renders. This is what makes theme hot-swap free.

**3. Components compose; they don't inherit deeply.** A `StrategyRow` is built from `Surface`, `StatusPill`, `Text`, etc. &mdash; not from a hand-drawn QML hierarchy that duplicates token references. Composition keeps token-binding discipline scalable.

**4. Components surface state, never fabricate it.** A `StrategyRow` displays the strategy's current `data/milodex.db` state. It does not cache, does not interpolate, does not "smooth" transitions in data. Per &sect;5.3 and &sect;8.

**5. Components have a single visible source of truth for their visual.** Every component file's top stanza is a comment listing the tokens it consumes and the data it expects. PR review uses this to spot drift.

---

## 11. Maintenance conventions

Update this document when any of the following events occur:

- A token is added, renamed, or removed.
- A theme is added, modified, or retired.
- A component's token consumption changes (added/removed token references).
- A new component is added to the foundational set (&sect;7).
- A status-color policy changes (e.g., a fifth role added).
- An architectural decision in &sect;9 is revised (which requires a new ADR superseding [ADR 0035](adr/0035-design-system-and-theme-architecture.md)).

When updating: do not silently change token values. Either the change is significant enough to merit a PR with rationale, or it's a typo fix that deserves a one-line note in the PR description. Token drift between this doc and the code is a bug.

### Adding a new theme

1. Define the new theme's concrete values for every token in &sect;3 and &sect;6 across roles.
2. Confirm WCAG AA contrast for `text.primary` on `surface.base`, `text.secondary` on `surface.base`, and every `status.*` role on `surface.base`.
3. Confirm the new theme does not require new component or token-set additions; if it does, those land first.
4. Add a row to &sect;1.2 and a section under &sect;3.
5. Add the theme file (`themes/<NewTheme>.qml`) and register it with `ThemeManager`.
6. Test theme hot-swap to and from the new theme; confirm every component renders correctly.

### Adding a new component

1. Sketch the component's token consumption *first*. If a token is missing, add it to the token set in a separate PR.
2. Implement the component as a single QML file under `components/`. Top-of-file comment lists tokens consumed and data expected.
3. Confirm the component renders correctly under all three themes via theme hot-swap.
4. Add a section under &sect;7 (or a deeper "Components catalog" section if &sect;7 grows beyond the foundational four).
5. PR review checks token-binding discipline (&sect;9.2).

### Versioning

This document is versioned (currently v0.1). The version increments on:
- A status-color policy change.
- A theme being retired.
- A foundational component being changed in a way that breaks downstream PRs.
- Any &sect;9 architectural revision.

Token additions and component additions are non-breaking; they don't trigger a version bump.

---

## 12. References

- [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md) &mdash; GUI runtime is PySide6 + Qt Quick (full QML)
- [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) &mdash; Phase 5 scope orders observability before features
- [ADR 0035](adr/0035-design-system-and-theme-architecture.md) &mdash; Design system and theme architecture (this doc's authorizing ADR)
- [ADR 0005](adr/0005-kill-switch-manual-reset.md) &mdash; Kill switch manual reset (propagates to GUI per &sect;5.3 and &sect;8)
- [ADR 0018](adr/0018-durable-state-directory-consolidation.md) &mdash; Durable state under `data/` (theme persistence per &sect;9.4)
- [PHASE5_PLANNING.md](PHASE5_PLANNING.md) &mdash; Phase 5 scope and PR sequence
- [VISION.md](VISION.md) &mdash; "Daily Operator Workflow" (the eight-step loop the GUI surfaces render)
- [FOUNDER_INTENT.md](FOUNDER_INTENT.md) &mdash; Priority order driving the polish target
