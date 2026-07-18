# Milodex Design System

**Status:** Accepted &middot; 2026-05-13 &middot; v0.2
**Phase:** 6 (open per [ADR 0038](adr/0038-phase-5-is-closed-and-phase-6-may-open.md); Phase 5 closed)
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
| **Editorial Dark** | Default — **the only theme shipped at initial launch** | Warm-tinted near-black `#0a0907` | Parchment cream `#ecd6a5` + oxblood `#7d3540` |
| **Editorial Light** | Daytime variant — **architectural; deferred for post-launch parity** | Cream-beige `#f5efe1` | Deep brown `#2a2218` + oxblood `#722f37` |
| **Bronze** | Alternate direction — **architectural; deferred for post-launch parity** | Warm-tinted near-black `#0d0c0a` | Patinated bronze `#b58c6e` + verdigris `#6a9a8c` |

Editorial Dark and Editorial Light are the same aesthetic in inverted contrast &mdash; literally what publications look like on a screen vs. on paper. Bronze is a separate aesthetic story (workshop / craft-tool / patinated metal) that demonstrates the theme machinery and gives variety without diverging the structural design.

**Launch scope (2026-05-14):** initial launch supports Editorial Dark only. Editorial Light and Bronze remain architectural themes — the full token sets in `Theme.qml`, the QML theme files (`themes/EditorialLight.qml`, `themes/Bronze.qml`), the `ThemeManager` hot-swap mechanism, and the contrast/parity tests are all preserved. The launch UI does not let the operator switch to Light or Bronze; the showcase theme-tabs render those two as disabled with a `(post-launch)` suffix. Post-launch parity work re-enables them in a dedicated sequence after Editorial Dark is verified in production.

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

> **Note — surface-specific roles beyond the foundational scale.** The table above is the foundational scale. `Theme.qml`'s `typography` object also carries surface-specific roles introduced for FRONT/DESK composition that are not foundational steps: `display.heroNum` / `display.deskNum` / `display.heroAccent` / `display.heroCents` / `display.tally` (hero P&amp;L numerics and stage tallies), `body.lgPlus` / `body.mdPlus` (large narrative prose), and `label.xs.criticalTrack` (wider letter-spacing for the destructive button label). They are documented at their point of use (&sect;7.9 hero band, &sect;7.1 buttons); the foundational scale is what general surfaces compose against.

**Tabular figures (`tnum`) are mandatory** for any data role. Strategy Bank rows, Sharpe ratios, trade counts, P&amp;L &mdash; all must align numerically. Set via `font.features: ["tnum"]` in QML. Without this, monospaced numbers still drift across rows in proportional-figure mode.

> **Non-negotiable: mono + tabular for all tabular numerics.** Any column of numbers &mdash; in any surface, in any theme &mdash; renders in `typography.data.*` (JetBrains Mono) with `tnum` on. Proportional sans is forbidden for tabular numerics. This is the single most common density violation and the most damaging to the ledger-typeset register; surface PRs that render numbers in `typography.body.*` for a column will not pass review. The carve-out is hero serif numerics on FRONT and DESK (one number on the page, set in `typography.display.lg`/`xl` Newsreader) &mdash; those are typographic, not tabular. Reinforces [DESIGN.md §5.3](DESIGN.md#53-three-voices-never-crossed) and [DESIGN.md §6](DESIGN.md#6-the-negative-space--what-this-design-rejects) negative-space entry on proportional numerics in tabular columns.

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
| `color.surface.base` | `#13100a` | Cards, panels, content surfaces |
| `color.surface.raised` | `#1a1611` | Elevated surfaces (dialogs, popovers, hovered rows) |
| `color.border.subtle` | `#241f15` | Hairline dividers, low-emphasis borders |
| `color.border.regular` | `#33291c` | Default panel borders, button outlines |
| `color.border.emphasis` | `#544532` | Hover/focus borders, emphasized cards |

#### Text &amp; brand

| Token | Hex | Use |
|---|---|---|
| `color.brand.primary` | `#ecd6a5` | Surface titles, primary brand accent (parchment) |
| `color.brand.accent` | `#7d3540` | Primary buttons, selection rings, key links (oxblood) |
| `color.brand.accentHover` | `#9a4350` | Primary button background on hover |
| `color.brand.accentPressed` | `#622b34` | Primary button background on pressed |
| `color.text.primary` | `#e4d2a8` | Default body text, table content |
| `color.text.secondary` | `#c4a880` | Secondary text, italics, metadata |
| `color.text.muted` | `#9c8c6c` | Captions, disabled context, placeholder |
| `color.text.disabled` | `#43392c` | Truly disabled UI |
| `color.text.onBrand` | `#f5e6c4` | Text rendered on top of `color.brand.accent` (primary button label) |

#### Texture (optional)

| Token | Value | Use |
|---|---|---|
| `texture.parchment.dot` (**proposed / not yet in the token set**) | `radial-gradient(rgba(230,207,153,0.04) 1px, transparent 1px); background-size: 3px 3px;` | Subtle parchment grain over surfaces. Used sparingly; default is off. Not present in any theme `.qml`; sketch only until a PR adds it. |

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
| `color.surface.base` | `#1c1a12` | |
| `color.surface.raised` | `#27241d` | |
| `color.border.subtle` | `#2e2920` | |
| `color.border.regular` | `#463e2b` | |
| `color.border.emphasis` | `#665a3e` | |
| `color.brand.primary` | `#b58c6e` | Bronze |
| `color.brand.accent` | `#6a9a8c` | Verdigris (oxidized-copper green) |
| `color.text.primary` | `#e8dcc5` | |
| `color.text.secondary` | `#b89e7c` | |
| `color.text.muted` | `#a59b86` | Lifted in the 2026-05-08 brightness pass so muted text on `surface.base` clears AA |
| `color.text.disabled` | `#433b2b` | |
| `color.text.onBrand` | `#0d0c0a` | Dark text on verdigris. Verdigris is light; warm-near-white was too low-contrast — dark "stamped" text reads as workshop nameplate. |

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
| `column.kanbanCard` | `288px` | Stable Phase 6 Bench/kanban card width inside a stage section. |
| `column.kanbanCardMinHeight` | `132px` | Minimum Bench/kanban card height; content may grow downward. |
| `column.kanbanMetric` | `68px` | Fixed metric slots inside a Bench/kanban card (`S`, `D`, `N`). |
| `column.benchMetric` | `64px` | Bench-row numeric metric slot. |
| `column.benchConfigKey` | `380px` | Bench-row config-key (strategy ID / config) column. |
| `column.benchStage` | `90px` | Bench-row stage column. |
| `column.benchEvidence` | `260px` | Bench-row evidence column. |
| `column.benchAction` | `152px` | Bench-row action column. |
| `column.benchStatus` | `280px` | Bench-row status prose + meta column (fixed width; longer strings elide). |

> **Retired 2026-07-17 (dead-code purge):** `column.pill`, `column.metric`, `column.chips`, `column.tradeCount`, `column.kanbanCard`, `column.kanbanCardMinHeight`, `column.kanbanMetric`, `column.benchConfigKey`, `column.benchStage`, and `column.benchEvidence` were removed with the dead `StrategyRow` / `DesignSystemShowcase` / kanban code. Only `benchMetric`, `benchAction`, and `benchStatus` remain live.

Accessed via `Theme.column.benchStatus` etc. (Surfaces once composed the now-retired
`StrategyRow` with their own column widths via property overrides; `StrategyRow` was
retired 2026-07-17 with the dead showcase code.)

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
- **Status indicators at idle.** Pulsing pips, breathing dots, slow opacity loops, "live" shimmer on a status rail &mdash; all forbidden. The presence of the indicator is the message; idle animation is theater. Status indicators may animate *only on state change* (a Risk Office stamp swap, a kill-switch fire, a posture transition), using `motion.standard` and `ease.editorial`. Reinforces [DESIGN.md §5.2](DESIGN.md#52-honest-signal-over-decorative-motion) and the no-idle-animation entry in [DESIGN.md §6](DESIGN.md#6-the-negative-space--what-this-design-rejects).
- **Skeleton shimmers and structural placeholders for unbuilt features.** Skeletons imply "data is loading" when in fact nothing is wired. Skeleton placeholders are correct *only* for genuine async loads of real data the surface knows will arrive; for unbuilt capability, italic muted text naming the gap (`Theme.color.text.muted` on `typography.body.md` italic) is the right pattern. Reinforces [DESIGN.md §5.8](DESIGN.md#58-empty-states-are-honest-not-coy).

---

## 6. Status Colors

Status colors are the load-bearing semantic dimension of a trading-platform GUI: positive vs. negative P&amp;L, gate-passing vs. gate-blocked, paper vs. live, kill-switch state. They are also where convention (red/green/amber) collides with editorial coherence.

**Decision:** Status colors are *theme-tinted* &mdash; each theme provides a hue that fits its palette &mdash; but the *role* (positive / warning / negative / info) is stable across themes. Components reference roles, never raw hex.

**Stage hues:** Phase 6 Bench stage colors are a separate ladder-location axis
per [ADR 0046](adr/0046-bench-stage-hues-extend-production-tokens.md). They
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

The Phase 6 Bench surface is read-only: stage section color uses `Theme.stage.*`, but
every row and section affordance must say read-only or locked. It exposes no
write-capable Action menu submissions, no bulk actions, and no runner controls.

### 6.1 Roles

| Role | Editorial Dark | Editorial Light | Bronze | Meaning |
|---|---|---|---|---|
| `status.positive` | `#a8c4ab` muted sage | `#3d6b40` deep moss | `#a8c4ab` muted sage | Positive P&amp;L, gate passing, paper-active |
| `status.warning` | `#d5a566` mustard | `#7a560d` deep mustard | `#d5a566` mustard | Marginal, gate-narrow, attention-required |
| `status.negative` | `#df805e` rust | `#a04020` deep rust | `#d97550` rust | Negative P&amp;L, gate failing, kill-switch fired |
| `status.negativeHover` | `#e89472` | `#c04d28` | `#e88862` | Danger button border on hover/pressed |
| `status.info` | `#7a98b2` ink | `#3a5474` deep ink | `#7a98b2` ink | Neutral information, backtest stage |

Bronze's `status.positive` deliberately matches Editorial Dark's sage rather than tinting toward verdigris: the brand accent (`#6a9a8c` verdigris) and a verdigris positive on the same row would collide visually. Sage stays inside the palette story while staying distinct from the accent.

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

**Modal scope.** The kill-switch reset dialog lives in `KillSwitchResetModal.qml` (extracted from the deleted `AnchorSurface.qml` in HR-4; reachable from the `RiskStrip` badge kill-switch indicator and the `RiskOfficeDrawer` KILL SWITCH section). It is an overlay that `anchors.fill: parent` and swallows click-through with a backing `MouseArea`, but it masks only within the surface that instantiates it — it is not a window-level overlay in `Main.qml`. Future dialogs that gate destructive actions should mask at the window level (overlay in `Main.qml`). Tracked for the next surface that adds a dialog.

### 6.5 Editorial marginalia

Inline commentary text &mdash; "lifecycle exempt", "flagged, not retired", or other editor's-note callouts on tabular rows &mdash; should render as italic Newsreader at body size (`Theme.typography.deck`), prefixed with an em-dash, color `text.muted`. Lower-case is intentional: the marginalia reads as commentary, not as a bureaucratic label. Compare to `label.xs` uppercase, which reads as column-header / data-tag.

Typical placement: inside the strategy-ID column on `StrategyRow`, anchored after the strategy ID (and the audit asterisk, if present), elides if the remaining column space is short. Long strategy IDs always get the most space; the note takes whatever is left and elides gracefully.

The em-dash prefix gives the marginalia a deliberate-aside feel and visually separates it from the strategyId. The pattern was introduced in the PR E polish pass on the since-removed `StrategyBankSurface.qml` and later lived in `StrategyRow.qml` (retired 2026-07-17 as dead code); the same token and em-dash convention should be used wherever similar commentary is needed on tabular rows in future surfaces.

---

## 7. Components (initial set)

These four foundational components shipped in the first design-system PR. Subsequent observability surfaces compose against them. **`StatusPill` (§7.2) and `StrategyRow` (§7.3) — together with the `GateTable` component and the `DesignSystemShowcase` surface — were retired 2026-07-17 as dead code (unreachable in the shipped app); the subsections below are retained as historical design record. `Button` and `Surface` remain live.**

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
parsed. Follows the same pattern as the kill-switch panel wash in
`KillSwitchResetModal.qml`:

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

First use (historical): the BLOCKED section of the since-removed
`StrategyBankSurface.qml` (`status.negative` @ 0.06). The kill-switch panel —
now in `KillSwitchResetModal.qml` — uses a higher alpha (0.10) as an
active-alarm indicator, not a section hint. The Bench/Ledger/Desk surfaces
are the live homes for any future section-wash.

### 7.6 Modals and dossier rails (component selection)

[DESIGN.md &sect;5.11](DESIGN.md#511-modals-for-safety-critical-confirmation-rails-for-reference) splits content surfaces into two component patterns. This subsection translates that doctrine into token/component contracts.

**Selection rule.** A surface-PR question of the form *"should this be a modal?"* is answered by the role of the content, not by the volume of content:

| Content role | Component | Reason |
|---|---|---|
| Safety-critical confirmation, operator-approval gate, kill-switch reset | **Confirmation modal** (full overlay) | Forced focus and trivialized cancellation are functional requirements per [PRODUCT.md &sect;5](PRODUCT.md#5-safety-posture) "Preview before action." |
| Evidence dossier, history detail, configuration view, any reference content | **Dossier rail** (right-side sheet) or **row drawer** (slide-down attached to the originating row) | Operator should read it *next to* the row it describes, with surrounding context still visible. A modal here collapses the editorial register into a SaaS dialog. |

**Confirmation modal &mdash; interior contract.**

- Container: full-window overlay with `color.surface.canvas` @ 0.72 scrim, modal panel set on `color.surface.raised` with `1px color.border.regular`, `radius.md` (4px &mdash; minimal; not the bento-paradigm `radius.xl`).
- Header: `typography.display.sm` Newsreader for the action title; `typography.label.xs` Public Sans tracked uppercase for the action kicker ("CONFIRMATION PREVIEW" / "KILL-SWITCH RESET").
- Body: composed as a [Definition block](#77-definition-block) (see &sect;7.7) &mdash; small-caps labels, `typography.data.md` mono values for IDs / numerics, `typography.body.md` serif italic values for prose. **No nested card frames inside the modal frame.** Hairline rules (`1px color.border.subtle`) separate sections.
- Safety banner (when applicable): small-caps `typography.label.xs` red set in a band bounded by hairline rules above and below, color `status.negative`. Never a yellow alert bar. Never a rotated rubber-stamp graphic.
- Footer: action buttons follow &sect;7.1's variant hierarchy; for safety-critical surfaces `critical` + `ghost` cancel is the correct pairing. No "Continue / Next" wizard chrome.
- Motion: open via `motion.standard` (220ms) `ease.editorial` &mdash; scrim fades independently of the panel's `translate-y[8px] -> 0`. Dismiss is instant on Escape / outside-click; no exit animation needed for safety surfaces.

**Dossier rail &mdash; interior contract.**

- Container: anchored to the right edge of the surface, `color.surface.base` panel, `1px color.border.subtle` on the leading edge only, no radius (sharp vertical edge). Width: 384&ndash;448px range; never full-window.
- The originating row remains visible and selected (`2px` left border `color.brand.accent` per &sect;7.3); the rail is a column of the page, not a floating box.
- Header: dossier title set in `typography.display.sm` Newsreader; close-affordance is a ghost button with `typography.label.xs` "CLOSE", not an X glyph.
- Body: definition-block layout (&sect;7.7) with hairline-ruled sections. Long-form prose (Description, Plot) uses `typography.body.md` serif; IDs and numerics use `typography.data.md` mono. Italic Newsreader for editorial commentary per &sect;6.5.
- Motion: enter via `motion.standard` slide-in from right; persist until explicit close. No backdrop scrim &mdash; the rail does not interrupt; it accompanies.

A **row drawer** is a horizontal alternative: an attached panel that slides down beneath the originating row, pushing the rows below it. Same typography and structure as the rail; use when the surface is narrow or when the relationship to the originating row should feel inline rather than columnar. Bench is the natural fit for row drawers; Ledger and Desk are natural fits for rails.

### 7.7 Definition block

A typographic content block that replaces SaaS-style bordered card frames for status, evidence, and configuration content. The "AT THE GATE" surface on FRONT, the body of dossier rails, and the body of confirmation modals are all definition blocks.

**Anatomy.**

```
SMALL-CAPS LABEL              value (mono or serif, per data type)
SMALL-CAPS LABEL              value
&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;  hairline rule between groups
SMALL-CAPS LABEL              value
```

- Labels: `typography.label.xs` Public Sans, `text.secondary`, tracked uppercase, left-aligned in a reserved gutter (~140&ndash;160px).
- Values:
  - Free-text prose / descriptions &mdash; `typography.body.md` Public Sans, `text.primary`.
  - IDs, hashes, timestamps, numerics &mdash; `typography.data.md` JetBrains Mono with `tnum`, `text.primary`.
  - Editorial commentary &mdash; `typography.deck` Newsreader italic, `text.muted`, em-dash prefix per &sect;6.5.
- Inter-row spacing: `space[3]` (12px) vertical; sections separated by hairline rules (`1px color.border.subtle`) with `space[5]` (24px) padding above and below.
- **No bordered card frame.** The definition block has no container chrome &mdash; it is bounded by the surrounding surface (modal frame, rail edge, or page column), not by its own rectangle.

**Forbidden alternatives.**

- Rounded rectangle with `1px color.border.regular` enclosing the same content (this is the SaaS dashboard tell).
- Filled-background "card" with `color.surface.raised`.
- Two-CTA footer pinned to the bottom of the block. Links inline in the prose are correct.

This component is the editorial alternative to the bordered-card pattern. When a surface PR is about to render status or evidence inside a rounded rectangle, the answer is this component.

### 7.8 Navigation (top-level tabs)

The primary nav (`FRONT / BENCH / LEDGER / DESK`) is the most visible recurring control in the application. Its visual contract:

| State | Color | Background | Affordance |
|---|---|---|---|
| Active | `color.text.primary` (full-emphasis cream) | transparent | **1px baseline rule in `color.brand.primary` (parchment)** under the label, full label width |
| Inactive | `color.text.muted` | transparent | none |
| Hover (inactive) | `color.text.secondary` | transparent | `motion.fast` color transition |

**Forbidden.** Active state must not be rendered as a filled brand-accent (oxblood) pill. Oxblood is reserved for primary buttons, selection rings on data rows, and the period-after-headline typographic accent per &sect;3.1 and &sect;7.1. Using `color.brand.accent` as a nav-active background reads as a loud SaaS tab and dominates the surface; the screenshots in the 2026-05-13 second-pass critique flagged this as the loudest element on the page.

**Why a baseline rule and not a pill shape.** The DESIGN.md &sect;5 meta-rule permits conventional control affordances when they are quieter and more usable than the editorial alternative. A 1px parchment baseline rule is *both* &mdash; it carries clear affordance (the eye reads "this one is selected") at the volume of a printed page's running-section indicator, without the SaaS-dashboard tell of a filled pill. This is the canonical worked example of the controls-stay-quiet rule.

**Typography.** `typography.label.xs` Public Sans tracked uppercase, `space[4]` horizontal padding, `space[3]` vertical padding. The active baseline rule sits `space[1]` (4px) below the label baseline.

### 7.9 Desk-only composition: hero band and lettered sections

[DESIGN.md &sect;4](DESIGN.md#4-the-four-surface-narrative) promotes DESK's "hero band, columnar body, lettered sections (A through H)" from voice to non-negotiable. This subsection defines the token contracts.

**Hero band** (the lead block of DESK).

- One dominant block, occupying 2&times; the column width of the secondary blocks beside it.
- Lead metric: hero serif numerics, one of the two allowed carve-outs from the mono-numerics rule (&sect;2). `Theme.qml` ships dedicated hero numeric tokens — `typography.display.heroNum` (96/1.05) for the FRONT hero P&amp;L and `typography.display.deskNum` (56/1.05) for the denser DESK cockpit hero — both above the foundational `display.xl` (64/1.05). Use the dedicated hero token for the lead metric; reserve `display.xl` for rare feature surfaces.
- Standfirst: `typography.deck` Newsreader italic 14, `text.secondary`, single line.
- Padding: `space[6]` (32px) all sides; bottom edge is a `1px color.border.subtle` hairline rule, not a card border.
- Surrounding secondary blocks (1&times; column width) use `typography.display.md` (24/1.20) for their lead values; tertiary sidebars use `typography.display.sm`. The hero/secondary/tertiary type ladder is what carries "front-page composition" instead of equal-weight columns.

**Lettered section header** (sections A through H on DESK).

```
A.   STRATEGY LEDGER                                 12 configs
&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;&mdash;
```

- Letter token: `typography.display.sm` (18/1.30) Newsreader, weight 500, `color.brand.primary` (parchment).
- Section name: `typography.label.xs` Public Sans tracked uppercase, `text.secondary`.
- Right-aligned meta (count, timestamp, "no feed"): `typography.data.sm` mono, `text.muted`.
- Spacing: letter and section-name share a baseline with `space[3]` (12px) between them; right-aligned meta is on the same baseline.
- Underline: full-width `1px color.border.subtle` hairline rule, `space[2]` (8px) below the header baseline.
- Section content begins `space[4]` (16px) below the rule.

**Lettered sections are non-negotiable for DESK.** A DESK surface PR that renders sections without the lettered header pattern is out of voice for that surface regardless of its interior quality.

---

## 8. Status colors in motion

**Color transitions on status changes are forbidden.** When a strategy moves from `paper` to `kill-switch-fired`, the pill changes instantly from `pill-paper` to `pill-killed`. No fade. The instantaneous change is honest signal &mdash; the operator needs to see the state flip the moment it happens, not interpret a color crossfade.

**The kill-switch banner does not pulse, glow, or breathe.** Pulsing kill-switch indicators are an antipattern; they trade urgency for novelty and lose impact within a single session. The banner uses a stable, high-contrast static treatment.

**This rule generalizes to every status indicator on every surface.** The Risk Office strip, posture pips, paper/live mode badges, broker-connection dots &mdash; all stay static at idle. Animation is reserved for state *change*, never for steady state. See &sect;5.3.

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

**6. Editorial register is the default for content; conventional affordances are allowed for controls when quieter and more usable.** This is the [DESIGN.md &sect;5 preamble](DESIGN.md#5-operative-principles) meta-rule applied at the component level. Content components (Strategy row, Definition block, Dossier rail, Hero band, Lettered section header) are editorial &mdash; hairline-ruled, typographically composed, no card chrome. Control components (Navigation, Buttons, Status pills, future filter chips, future action menus) may retain conventional affordances (pill shape on filters, fill on the primary button, dropdown menu shape on action menus) when an editorial alternative would degrade usability. The discipline is *"as quiet as possible while remaining affordant,"* not editorial purity over function. A red navigation pill is a problem because the color is loud; a navigation tab shape is fine when it carries quiet affordance. Compare &sect;7.8: the active baseline rule is the worked example of this principle.

**7. Don't render unbuilt features as skeleton placeholders.** Components rendering an empty state for a not-yet-wired capability use italic muted text (`typography.body.md` italic, `text.muted`) that names the gap &mdash; *"awaits a data-feed read model"*, *"not wired"*. Skeleton shimmers or structural placeholders are reserved for genuine async loads of real data the surface knows will arrive. Implying a load when none is in progress is the same trust violation as faking data &mdash; see &sect;5.3 and [DESIGN.md &sect;5.8](DESIGN.md#58-empty-states-are-honest-not-coy).

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

This document is versioned (currently v0.2). The version increments on:
- A status-color policy change.
- A theme being retired.
- A foundational component being changed in a way that breaks downstream PRs.
- Any &sect;9 architectural revision.
- A governance-level addition that constrains future component design (e.g., v0.2's editorial-default-for-content / conventional-for-controls principle and the modal/rail selection rule).

Token additions and component additions are non-breaking; they don't trigger a version bump on their own. Governance additions that change *which* component a future surface should reach for *do* trigger a version bump, since they alter the spec downstream PRs are built against.

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
- [DESIGN.md](DESIGN.md) &mdash; the narrative half of Milodex's design (vibe, voice, four-surface arc); this doc's companion. v0.2 of both docs land together.
- [PRODUCT.md](PRODUCT.md) &mdash; product compass; &sect;6 defines the four-surface roles this doc's components serve.

---

## Changelog

- **v0.2 &mdash; 2026-05-13** &mdash; companion update to [DESIGN.md v0.2](DESIGN.md). Translates the doctrine of editorial-as-default, modal/rail philosophy, no-idle-pulse, no-fake-skeletons, and Bench wording alignment into token and component rules. No token *value* changes; v0.1 token set is retained verbatim. No QML, no production code. Additions: &sect;2 non-negotiable callout for mono + tabular numerics in all tabular columns; &sect;5.3 two new "does not animate" entries (status indicators at idle, skeleton shimmers for unbuilt features); &sect;7.6 (new) modal/rail component selection rule plus interior contracts for confirmation modal and dossier rail; &sect;7.7 (new) Definition block component as the editorial alternative to bordered SaaS card frames (canonical use: FRONT's "AT THE GATE"); &sect;7.8 (new) Navigation tab visual contract &mdash; active state is a 1px parchment baseline rule, not a brand-accent pill; &sect;7.9 (new) DESK-only composition rules &mdash; hero band token sketch and lettered section header contract, both non-negotiable for DESK; &sect;8 generalization &mdash; no-idle-pulse rule extended from the kill-switch banner to every status indicator on every surface; &sect;10 two new component principles &mdash; editorial-default-for-content / conventional-affordances-for-controls (with &sect;7.8 active-baseline-rule as the worked example), and no-skeleton-placeholders-for-unbuilt-features; &sect;11 versioning notes updated to document that governance additions trigger a version bump even when they don't add tokens.
- **v0.1 &mdash; 2026-05-07** &mdash; initial document. Captures the editorial-press direction, three themes (Editorial Dark, Editorial Light, Bronze), the foundational token set (color, typography, spacing, motion, radius, columns), and the initial four-component set (Buttons, Status pills, Strategy rows, Surface containers) plus the section-wash flourish. Companion to DESIGN.md v0.1.
