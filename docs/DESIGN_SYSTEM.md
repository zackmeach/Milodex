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
| `typography.display.xl` | Newsreader | 64 / 1.05 | 500 | Hero / feature surfaces (rare) |
| `typography.display.lg` | Newsreader | 36 / 1.10 | 500 | Page titles |
| `typography.display.md` | Newsreader | 24 / 1.20 | 500 | Section titles ("Strategy Bank") |
| `typography.display.sm` | Newsreader | 18 / 1.30 | 500 | Subsection titles |
| `typography.display.sm.italic` | Newsreader | 18 / 1.30 | 400 italic | Editorial accents ("No. 6 in paper") |
| `typography.deck` | Newsreader | 14 / n/a (single-line role) | 400 italic | Editorial deck/marginalia — section kickers, inline editor's-note callouts. See &sect;6.5. |
| `typography.body.lg` | Public Sans | 15 / 1.50 | 400 | Primary body text |
| `typography.body.md` | Public Sans | 14 / 1.50 | 400 | Default UI text, dense forms |
| `typography.body.sm` | Public Sans | 13 / 1.45 | 400 | Captions, secondary metadata |
| `typography.label.xs` | Public Sans | 12 / 1.40 | 500 + `0.12em` letter-spacing + uppercase | Section labels ("STRATEGY BANK") |
| `typography.data.md` | JetBrains Mono | 14 / 1.60 | 400 + `tnum` | Default tabular data |
| `typography.data.sm` | JetBrains Mono | 13 / 1.60 | 400 + `tnum` | Compact tables |
| `typography.data.xs` | JetBrains Mono | 12 / 1.55 | 400 + `tnum` | Very dense tables, header rows |

**Tabular figures (`tnum`) are mandatory** for any data role. Strategy Bank rows, Sharpe ratios, trade counts, P&amp;L &mdash; all must align numerically. Set via `font.features: ["tnum"]` in QML. Without this, monospaced numbers still drift across rows in proportional-figure mode.

**Italic discipline.** Italic Newsreader is an editorial accent only (e.g., "No. 6 in paper", "Sharpe 0.327", inline citations). Buttons, navigation, dense labels never italicize. Status pills never italicize. Body text rarely italicizes.

---

## 3. Color Tokens

Color tokens are defined as *semantic roles*. Each theme provides concrete values for every role; component code references roles, never raw hex values. Status colors are a constrained exception &mdash; see &sect;6.

### 3.0 Operational text hierarchy

The text ladder is a readability contract, not a decoration scale:

| Token | Use |
|---|---|
| `color.text.primary` | Operational facts, selected rows, readable body, table values, and values the operator must scan quickly. |
| `color.text.secondary` | Row names, timestamps, section captions, table headers, evidence metadata, and important supporting context. |
| `color.text.muted` | Helper text, empty states, quiet commentary, and non-critical explanatory copy. |
| `color.text.disabled` | Disabled controls and unavailable UI only. Do not use for timestamps, evidence IDs, table headers, market values, event text, or permanent records. |

Serious does not mean faint. If a value is evidence, state, or an operator decision aid, it must not be rendered as disabled texture.

### 3.1 Editorial Dark (default)

#### Surface &amp; structure

| Token | Hex | Use |
|---|---|---|
| `color.surface.canvas` | `#0a0907` | Page background, root window |
| `color.surface.base` | `#100d09` | Cards, panels, content surfaces |
| `color.surface.raised` | `#14110d` | Elevated surfaces (dialogs, popovers, hovered rows) |
| `color.border.subtle` | `#1f1a12` | Hairline dividers, low-emphasis borders |
| `color.border.regular` | `#2a2218` | Default panel borders, button outlines |
| `color.border.emphasis` | `#4a3d28` | Hover/focus borders, emphasized cards |

#### Text &amp; brand

| Token | Hex | Use |
|---|---|---|
| `color.brand.primary` | `#e6cf99` | Surface titles, primary brand accent (parchment) |
| `color.brand.accent` | `#722f37` | Primary buttons, selection rings, key links (oxblood) |
| `color.brand.accentHover` | `#8a3a45` | Primary button background on hover |
| `color.brand.accentPressed` | `#5d262d` | Primary button background on pressed |
| `color.text.primary` | `#d8c5a3` | Default body text, table content |
| `color.text.secondary` | `#b89e7a` | Secondary text, italics, metadata |
| `color.text.muted` | `#8a7c5e` | Captions, disabled context, placeholder |
| `color.text.disabled` | `#3d342a` | Truly disabled UI |
| `color.text.onBrand` | `#f5e6c4` | Text rendered on top of `color.brand.accent` (primary button label) |

#### Texture (optional)

| Token | Value | Use |
|---|---|---|
| `texture.parchment.dot` | `radial-gradient(rgba(230,207,153,0.04) 1px, transparent 1px); background-size: 3px 3px;` | Subtle parchment grain over surfaces. Used sparingly; default is off. |

### 3.2 Editorial Light

Same semantic roles, inverted contrast.

| Token | Hex | Notes |
|---|---|---|
| `color.surface.canvas` | `#f5efe1` | Cream-beige paper |
| `color.surface.base` | `#ebe2cc` | Tinted surface (deepened in v0.2 so cards lift visibly) |
| `color.surface.raised` | `#dccfb0` | Elevated (deepened so popovers/dialogs lift visibly off cards) |
| `color.border.subtle` | `#ddd2bc` | |
| `color.border.regular` | `#c2b596` | |
| `color.border.emphasis` | `#9a8967` | |
| `color.brand.primary` | `#5d2a30` | Deep oxblood-tobacco — distinct from `text.primary` and `brand.accent` |
| `color.brand.accent` | `#722f37` | Same oxblood &mdash; works on cream |
| `color.text.primary` | `#2a2218` | |
| `color.text.secondary` | `#5e5238` | More emphasis than muted; AA-passing on the deepened `surface.base` |
| `color.text.muted` | `#6e5f3f` | Lighter than secondary; minimum AA-passing on `surface.base` |
| `color.text.disabled` | `#bbae8c` | |
| `color.text.onBrand` | `#f5e6c4` | Same cream as Editorial Dark — oxblood accent is shared |

### 3.3 Bronze

Workshop / craft-tool / patinated-metal aesthetic.

| Token | Hex | Notes |
|---|---|---|
| `color.surface.canvas` | `#0d0c0a` | Warm dark with bronze tilt |
| `color.surface.base` | `#19170f` | |
| `color.surface.raised` | `#22201a` | |
| `color.border.subtle` | `#28241b` | |
| `color.border.regular` | `#3d3625` | |
| `color.border.emphasis` | `#5a5036` | |
| `color.brand.primary` | `#a68063` | Bronze |
| `color.brand.accent` | `#5e8b7e` | Verdigris (oxidized-copper green) |
| `color.text.primary` | `#e0d4bd` | |
| `color.text.secondary` | `#a89070` | |
| `color.text.muted` | `#948a76` | Bumped from `#7e7565` (3.95:1) so muted text on `surface.base` clears AA |
| `color.text.disabled` | `#3d3525` | |
| `color.text.onBrand` | `#0d0c0a` | Dark text on verdigris (5.09:1). Verdigris is light; warm-near-white only hit 3.29:1 — dark "stamped" text reads as workshop nameplate. |

---

## 4. Spacing Scale

Base unit 4px. Eight steps. Tight enough for dense data tables, generous enough for editorial breathing room.

| Token | Value | Typical use |
|---|---|---|
| `space[0]` | `0px` | Reset, no gap |
| `space[1]` | `4px` | Tight intra-component padding |
| `space[2]` | `8px` | Default inline gap, button padding |
| `space[3]` | `12px` | Component padding, list item rhythm |
| `space[4]` | `16px` | Default panel padding, section padding |
| `space[5]` | `24px` | Large panel padding, section separation |
| `space[6]` | `32px` | Major section breaks |
| `space[7]` | `48px` | Hero spacing, feature surface padding |
| `space[8]` | `64px` | Page-margin scale, atmospheric whitespace |

Spacing tokens are accessed via bracket syntax (`Theme.space[4]`) because numeric dot-access (`Theme.space.4`) is invalid JS syntax in QML expressions. Spacing applies to padding, margin, and gap. Never use literal pixel values in QML &mdash; always reference a token. If a value is needed that doesn't exist in the scale, the answer is to add a token (with PR-level discussion), not to drop in a one-off literal.

**Border radius** uses a separate, smaller scale:

| Token | Value | Use |
|---|---|---|
| `radius.sm` | `3px` | Pills, inline badges |
| `radius.md` | `4px` | Buttons, default surfaces |
| `radius.lg` | `6px` | Cards, dialogs |
| `radius.xl` | `8px` | Hero / feature surfaces |
| `radius.full` | `9999px` | Avatars, status dots |

### 4.1 Column widths

Domain-specific layout dimensions for tabular surfaces (Strategy Bank, attribution
tables, anywhere a multi-cell row appears). Theme-invariant: constant across all themes.

| Token | Value | Use |
|---|---|---|
| `column.pill` | `96px` | Stage / status pill column (accommodates "blocked") |
| `column.metric` | `64px` | Right-aligned numeric metric (e.g., "+1.19") |
| `column.chips` | `200px` | Gate-chip slot + optional "— flagged, not retired" marginalia. Max case is 2 chips (~52px) + 8px left-padding + the flagged marginalia (~125px italic Newsreader 14px) ≈ 185px; 200 leaves headroom. |
| `column.tradeCount` | `88px` | Right-aligned secondary metric (e.g., "433 trades") |
| `column.kanbanLane` | `320px` | Stable Phase 6 Kanban lane width. Five lanes sit in a horizontal Flickable. |
| `column.kanbanCard` | `288px` | Stable read-only Kanban card width inside a lane. |
| `column.kanbanCardMinHeight` | `132px` | Minimum read-only Kanban card height; content may grow downward. |
| `column.kanbanMetric` | `68px` | Fixed metric slots inside a Kanban card (`S`, `D`, `N`). |

Accessed via `Theme.column.pill` etc. Surfaces that need different proportions
may compose `StrategyRow` with their own column widths via property overrides
(future enhancement; not in Phase 5 scope).

#### Column-reservation contract

**Rule: content never determines column width &mdash; the column always determines it.**

Every variable-content row slot must reserve a stable `Theme.column.*` width
sized for the maximum-case content. Smaller cases fill less of the slot; the
slot itself does not shrink. Within a section, columns then align across all
rows regardless of per-row content variance.

The contract emerged from a visible regression in PR E: the gate-chips slot
used `Layout.preferredWidth: visible ? implicitWidth : 0`, which made the slot
content-driven. RowLayout shifted every column to the right of the fillWidth
column to absorb the chip-row's variance, producing visibly drifting pill /
metric / tradeCount column positions row-to-row in the BLOCKED section.
PR E.3 introduced `column.chips` and the contract.

When adding a new variable-content slot to a row component:

1. Identify the maximum-case content (longest possible label, most chips, etc.).
2. Add a `Theme.column.<name>` token sized to fit it with breathing room.
3. Bind `Layout.preferredWidth: visible ? Theme.column.<name> : 0`.
4. Add `clip: true` on the slot's container as a backstop &mdash; if a future
   change makes the content overflow the reserved width, the overflow stays
   inside the slot rather than silently bleeding into adjacent columns.

Cross-section alignment (paper section vs blocked section) is not enforced.
Sections may legitimately have different column structures; section-level
visual treatment (e.g., the BLOCKED rust-wash) signals the difference.

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

**Stage hues:** Phase 6 Kanban stage colors are a separate ladder-location axis
per [ADR 0046](adr/0046-kanban-stage-hues-extend-production-tokens.md). They
land as `stage.idle`, `stage.backtest`, `stage.paper`, `stage.microLive`, and
`stage.live` tokens across all themes. Stage hue must not replace `status.*`
for gate outcomes, refusals, P&amp;L, kill-switch state, or approvals.

| Stage token | Hex | Meaning |
|---|---|---|
| `stage.idle` | `#6f6a5c` | Configured but not yet producing evidence |
| `stage.backtest` | `#7a98b2` | Historical evidence in progress or under review |
| `stage.paper` | `#a8c4ab` | Live feed, no capital |
| `stage.microLive` | `#d5a566` | Capital stage, capped and locked |
| `stage.live` | `#7d3540` | Live-capital stage, locked behind human review |

The Phase 6 Kanban surface is read-only: lane color uses `Theme.stage.*`, but
every card and lane affordance must say read-only or locked. It exposes no
drag/drop, no bulk actions, and no runner controls.

### 6.1 Roles

| Role | Editorial Dark | Editorial Light | Bronze | Meaning |
|---|---|---|---|---|
| `status.positive` | `#9bb89e` muted sage | `#3d6b40` deep moss | `#9bb89e` muted sage | Positive P&amp;L, gate passing, paper-active |
| `status.warning` | `#c4965a` mustard | `#7a560d` deep mustard | `#c4965a` mustard | Marginal, gate-narrow, attention-required |
| `status.negative` | `#d97757` rust | `#a04020` deep rust | `#cd6038` rust | Negative P&amp;L, gate failing, kill-switch fired |
| `status.negativeHover` | `#e08b6b` | `#c04d28` | `#df7548` | Danger button border on hover/pressed |
| `status.info` | `#6c89a3` ink | `#3a5474` deep ink | `#6c89a3` ink | Neutral information, backtest stage |

Bronze's `status.positive` deliberately matches Editorial Dark's sage rather than tinting toward verdigris: the brand accent (`#5e8b7e` verdigris) and a verdigris positive on the same row would collide visually. Sage stays inside the palette story while staying distinct from the accent.

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

### 6.4 Modal scope

**Modal scope.** The kill-switch confirmation dialog in `AnchorSurface.qml` masks at the surface level only — clicking a tab in `Main.qml` while the dialog is open will dismiss it without confirmation. Future dialogs that gate destructive actions should mask at the window level (overlay in `Main.qml`). Tracked for the next surface that adds a dialog.

### 6.5 Editorial marginalia

Inline commentary text &mdash; "lifecycle exempt", "flagged, not retired", or other editor's-note callouts on tabular rows &mdash; should render as italic Newsreader at body size (`Theme.typography.deck`), prefixed with an em-dash, color `text.muted`. Lower-case is intentional: the marginalia reads as commentary, not as a bureaucratic label. Compare to `label.xs` uppercase, which reads as column-header / data-tag.

Typical placement: inside the strategy-ID column on `StrategyRow`, anchored after the strategy ID (and the audit asterisk, if present), elides if the remaining column space is short. Long strategy IDs always get the most space; the note takes whatever is left and elides gracefully.

The em-dash prefix gives the marginalia a deliberate-aside feel and visually separates it from the strategyId. The pattern was introduced in the PR E polish pass for `StrategyBankSurface.qml`; the same token and em-dash convention should be used wherever similar commentary is needed on tabular rows in future surfaces.

---

## 7. Components (initial set)

These four foundational components ship in the first design-system PR. Subsequent observability surfaces compose against them.

### 7.1 Buttons

Five variants. All use `typography.body.md` weight 500, `space.2` x `space.3` padding, `radius.md`, `motion.fast` hover transitions.

| Variant | Background | Color | Border |
|---|---|---|---|
| `button.critical` | `status.negative` | `color.text.onCritical` (theme-specific high-contrast) | none |
| `button.primary` | `color.brand.accent` | `color.text.onBrand` (theme-specific high-contrast) | none |
| `button.secondary` | transparent | `color.text.primary` | `1px color.border.regular` |
| `button.ghost` | transparent | `color.text.secondary` | none |
| `button.danger` | transparent | `status.negative` | `1px status.negative` |

Hover state: critical uses `status.negativeHover` background; primary uses `color.brand.accentHover`; secondary border uses `color.border.emphasis`; ghost text uses `color.text.primary`; danger border uses `status.negativeHover`. Pressed state: critical uses `status.negativePressed`; primary uses `color.brand.accentPressed`. All transitions use `motion.fast`.

**Variant hierarchy.** The five variants form an emphasis ladder rather than a flat set:

- `critical` — stop-the-world action requiring deliberate operator confirmation. Kill-switch arming, kill-switch reset, manual force-close of a position. Filled rust, the highest visual emphasis in the system. Use sparingly — anything more frequent than a kill-switch operation belongs in `primary` or `danger`. Per [ADR 0005](adr/0005-kill-switch-manual-reset.md), `critical` actions never auto-execute on click; they open a confirmation dialog.
- `primary` — main happy-path action on the current surface ("Run backtest", "Promote to paper"). Filled brand accent.
- `danger` — destructive but bounded action (delete a row, cancel an order). Outlined rust, no fill — communicates "destructive" without screaming "stop-the-world."
- `secondary` — alternative action ("View details"). Outlined neutral.
- `ghost` — dismissive action ("Cancel"). No chrome, text only.

The split between `danger` and `critical` is the key distinction: `danger` is for actions whose worst-case is "you have to recreate a row"; `critical` is for actions whose worst-case is "the platform was protecting you from a real-money loss and you just disabled the protection."

- **Theme variation is by design.** The critical button's rust color reads differently across themes (Dark: warm caution; Light: hard stop; Bronze: between). All three pass WCAG AA per the contrast audit. Forcing visual uniformity would mean overriding the active theme's `status.negative`, which fights the theme system. Acceptable; deliberate.

### 7.2 Status pills

See &sect;6.3. Compact, rounded, role-keyed.

### 7.3 Strategy rows

The load-bearing component for the Strategy Bank surface. Anatomy:

```
[strategy-id mono] [*] [— note italic]   [stage-pill]   [gate chips] [— flagged, not retired italic]   [primary-metric mono]   [trade-count mono muted]
```

Layout: `RowLayout` with fixed column widths enforces tabular alignment across all rows.
- Strategy ID: `Layout.fillWidth: true` + `elide: Text.ElideRight` — absorbs available space, truncates long identifiers cleanly.
  - `auditFlag: true` renders a `*` superscript immediately after the strategy ID (ADR 0032 audit trail).
  - `note: string` renders italic Newsreader marginalia (`typography.deck`) after the ID (and asterisk), prefixed with an em-dash. See &sect;6.5.
- Stage pill: `Layout.preferredWidth: Theme.column.pill` (96px — accommodates "blocked").
- Gate-failure chips (optional, `gateFailures: var`): a `Row` of small inline chips rendered between metric and tradeCount columns when non-empty. Each chip renders a single capital letter (S, D, or N) inside a framed pill per ADR 0009 — the chip frame is the bracket; no literal `[`/`]` characters. Color: `status.negative` @ 0.12 background, 0.30 border. Typography: `data.xs` mono. When `flagFailingNotRetired: true`, italic Newsreader marginalia "— flagged, not retired" (`typography.deck`, `text.muted`) renders inline in the same row (see &sect;6.5). The slot uses the column-reservation pattern: `Layout.preferredWidth: Theme.column.chips` (200px) so all BLOCKED-section rows reserve the same width regardless of chip count, keeping the pill/metric/tradeCount columns horizontally stable. Rows with fewer chips fill less of the slot; the slot itself does not shrink. Paper rows (no chips, `visible: false`) collapse the slot to 0 — paper section aligns internally because every paper row has 0 chips. `clip: true` backstops future overflow into the tradeCount column.
- Primary metric: `Layout.preferredWidth: Theme.column.metric` (64px), right-aligned.
- Trade count: `Layout.preferredWidth: Theme.column.tradeCount` (88px), right-aligned.

Column width tokens are defined in `Theme.column.*` (&sect;4.1).

Typography: `typography.data.md` for IDs and metrics, `typography.data.sm` muted for trade count, `typography.deck` for inline editorial marginalia. Spacing: `space[5]` (24px) inter-column gap, `space[3]` horizontal padding, `space[2]` vertical. Surface: `radius.md`, `color.surface.base` background, `color.border.subtle` border, hover -> `color.surface.raised` + `color.border.regular`.

Selected/active state: `2px` left border in `color.brand.accent`, otherwise unchanged.

**Optional properties** (default to "off" values; existing call sites that omit them are unaffected):

| Property | Type | Default | Effect |
|---|---|---|---|
| `note` | `string` | `""` | Renders italic Newsreader marginalia after strategyId (and audit asterisk). Lower-case. Typical use: `"lifecycle exempt"`. See &sect;6.5. |
| `gateFailures` | `var` (list of strings) | `[]` | Renders gate-code chips between metric and tradeCount. |
| `auditFlag` | `bool` | `false` | Renders `*` superscript after strategyId. |
| `flagFailingNotRetired` | `bool` | `false` | Renders italic serif marginalia "flagged, not retired" alongside gate chips. See &sect;6.5. |

### 7.4 Surface containers

The default panel/card. Background `color.surface.base`, border `1px color.border.subtle`, `radius.lg`, `space.5` padding. Hover (when interactive) -> `color.border.regular`.

### 7.5 Section-wash editorial flourish

Surfaces with categorically-distinct sections (e.g. "blocked" vs "running")
may use a low-alpha section-wash to signal section semantics before text is
parsed. Follows the same pattern as the kill-switch panel wash on AnchorSurface:

```qml
// Wash rectangle, z-ordered behind section content.
// 0.06 is the recommended alpha — registers on Editorial Dark's near-black
// canvas without becoming alarming on Editorial Light or Bronze.
color: Qt.rgba(Theme.status.<role>.r, .g, .b, 0.06)
```

Implemented as a Rectangle behind the section content, optionally with
negative margins to bleed slightly past the content edge ("blood under
the headline" editorial convention):

```qml
anchors {
    fill:         sectionContent
    topMargin:    -Theme.space[3]
    bottomMargin: -Theme.space[3]
    leftMargin:   -Theme.space[3]
    rightMargin:  -Theme.space[3]
}
```

Use sparingly — one section-wash per surface maximum, and only when the
section's status is itself the structural information. Do not use for
visual variety alone.

First use: `StrategyBankSurface.qml` BLOCKED section (`status.negative` @
0.06); the kill-switch panel on `AnchorSurface.qml` uses a higher alpha
(0.10) as an active-alarm indicator, not a section hint.

---

## 8. Status colors in motion

**Color transitions on status changes are forbidden.** When a strategy moves from `paper` to `kill-switch-fired`, the pill changes instantly from `pill-paper` to `pill-killed`. No fade. The instantaneous change is honest signal &mdash; the operator needs to see the state flip the moment it happens, not interpret a color crossfade.

**The kill-switch banner does not pulse, glow, or breathe.** Pulsing kill-switch indicators are an antipattern; they trade urgency for novelty and lose impact within a single session. The banner uses a stable, high-contrast static treatment.

---

## 9. Architecture

### 9.1 Theme singleton pattern

A single `Theme` QML singleton exposes every token as a property. Components reference tokens via `Theme.color.brand.accent`, `Theme.typography.body.md`, etc. The singleton's properties are themselves bindings that read from the *active* theme file.

```qml
// QML/Milodex/Theme.qml (singleton, registered via qmldir)
pragma Singleton
import QtQuick

QtObject {
    // Theme-variable: delegate to active theme.
    readonly property var color: activeTheme.color
    readonly property var status: activeTheme.status

    // Theme-invariant: declared inline; constant across themes.
    readonly property var typography: typographyRoles
    readonly property var space: spaceScale
    readonly property var motion: motionTokens
    readonly property var radius: radiusScale

    readonly property var activeTheme: {
        switch (Milodex.ThemeManager.theme) {
            case "editorial-light": return editorialLight
            case "bronze": return bronze
            default: return editorialDark
        }
    }
    // ... theme files imported as nested objects
}
```

**Naming notes.** Three tokens are named differently in code than the conceptual labels you might expect from prose:
- The display + body + data type-role bag is named `typography` (not `type`) because `type` is reserved in QML/JS expression contexts.
- `color.border.regular` (not `color.border.default`) because `default` is a JS reserved word.
- Spacing tokens `space.0` through `space.8` are accessed via bracket syntax: `Theme.space[4]` (numeric dot-access like `Theme.space.4` is invalid JS syntax).

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
