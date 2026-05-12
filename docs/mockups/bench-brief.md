# Bench Surface — Implementation Brief

> Hand-off document for implementing the Bench surface. Companion visual reference: `docs/mockups/bench-surface.html` (open in any browser; renders with the actual Editorial Dark token values).

---

## 1. Intent

The Bench is the operator's primary workspace for managing the Strategy Bank — the collection of trading strategies at various stages of validation (`idle → backtest → paper → micro_live → live`).

It is a **synthesis** of two patterns:

- **Governed-pipeline ergonomics (action-menu-driven, gate-enforced)** — per-row Action menus for state changes, all stages visible top-to-bottom, unavailable actions hidden rather than disabled
- **Editorial-print aesthetics** — vertical funnel-shaped layout, Newsreader serif headlines, italic standfirsts, prose-with-inline-signals, hairline rules, no card chrome

The synthesis exists because a symmetric horizontal board misrepresents Milodex's actual data model: the data is a **funnel** (many strategies enter, few survive to live), and movement is **gate-driven** (the system decides when evidence passes; the operator approves at boundaries). A horizontal column layout treats all stages as equally-weighted real estate — wrong for this domain.

The Bench answers two operator questions simultaneously:

1. **What's on the bench right now?** — status at a glance, by stage
2. **What can I move, and why is the rest blocked?** — active management with gate enforcement made visible

---

## 2. Layout — vertical stage-stacks, not horizontal columns

The five promotion stages render as **vertical sections stacked top-to-bottom**, full-width of the content area. Each section's height is determined by the number of strategies in that stage. The asymmetric heights *are* information — the funnel shape becomes visible.

```
┌─ THE BENCH ──────────────────────────────────────────────────────┐
│                                                                  │
│  i. IDLE · 02              configured, not yet run               │
│  ────────────                                                    │
│  [row]                                                           │
│  [row]                                                           │
│                                                                  │
│  ii. BACKTEST · 03         historical evidence in flight         │
│  ────────────                                                    │
│  [row]                                                           │
│  [row]                                                           │
│  [row]                                                           │
│                                                                  │
│  iii. PAPER · 04           live feed, no capital                 │
│  ────────────                                                    │
│  [row]                                                           │
│  [row]                                                           │
│  [row]                                                           │
│  [row]                                                           │
│                                                                  │
│  iv. MICRO LIVE · 02       live capital, capped sizing           │
│  ────────────                                                    │
│  [row]                                                           │
│  [row]                                                           │
│                                                                  │
│  v. LIVE · 01              full attribution, real capital        │
│  ════════════               ← thicker rule for live section      │
│  [row, oxblood-tinted, 2px brand-accent left border]             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

The layout is the funnel. Do not equalize section heights; the asymmetry is the design.

---

## 3. Section header anatomy

Each section header is a single horizontal row containing:

- **Roman numeral** (`i. ii. iii. iv. v.`) — Newsreader italic, `text.muted`, font size around 19px
- **Stage name** (`IDLE`, `BACKTEST`, `PAPER`, `MICRO LIVE`, `LIVE`) — Public Sans, letter-spaced uppercase, weight 600, `text.primary`
- **Mid-dot separator** (`·`) — `text.muted`
- **Stage count** (`02`, `03`, etc., 2-digit zero-padded) — JetBrains Mono with `tnum`, `text.muted`
- **Section caption** (right-aligned) — Newsreader italic, `text.secondary`, e.g. *"historical evidence in flight"*, *"live capital, capped sizing"*
- **Hairline rule** below the header — 1px `border` color (slightly heavier than `border.subtle`)

The LIVE section's hairline rule is **2px and `brand.accent`** to distinguish it as the lead-story section.

### Empty-state treatment

When a section has 0 strategies, render the body as a single italic Newsreader caption *"no strategies in this stage"* in `text.muted`, centered, with normal section vertical spacing. Do not render a blank region or a placeholder card.

---

## 4. Row anatomy

Each strategy is a **row**, not a card. Rows have hairline separators (`border.subtle`) only — no rounded backgrounds, no card chrome, no drop shadows.

Grid columns (left to right):

| # | Column | Content | Typography |
|---|---|---|---|
| 1 | **Drag handle** | 6-dot grip glyph, positioned in the gutter to the left of the row | Mono, `text.muted`, opacity 0.30 default → 0.65 on row hover. Cursor: `grab`. |
| 2 | **Strategy block** | Strategy display name on top, dotted strategy ID below | Name: Newsreader 20px medium, `text.primary`. ID: JetBrains Mono 11px, `text.muted`. |
| 3 | **Sharpe** | Right-aligned numeric (e.g. `+0.87`) | data.md mono with `tnum` |
| 4 | **Max drawdown** | Right-aligned percentage (e.g. `9.2%`) | data.md mono with `tnum` |
| 5 | **Trade count** | Right-aligned integer (e.g. `435`) | data.md mono with `tnum` |
| 6 | **Status prose** | Italic Newsreader sentence with one inline colored signal word + a smaller mono `meta` line below | Newsreader italic 14.5px, `text.secondary` for prose; the signal word in body weight + status color; meta line in mono 11px, `text.muted` |
| 7 | **Action button** | Stage-appropriate button variant | per `Button.qml` (see §6) |

A subtle column-header row (Sharpe / max-dd / trades) appears once per section, between the section header and the first row, in the `num-label` style (10px Public Sans uppercase letter-spaced, `text.muted`).

### Status prose — the load-bearing element

The status column carries the most explanatory weight on the surface. Each row's status is a single italic Newsreader sentence with **one inline colored signal word**, plus a smaller mono `meta` line below.

Examples:

> *"**Walk-forward complete** — gates pass on every window."* (with "Walk-forward complete" in `status.positive`)
>
> *"**Max-dd 17.1% over 15% gate** — needs reparam."* (with the gate-failure phrase in `status.warning`)
>
> *"**Sharpe holds** — capital stages remain locked by ADR 0004."* (with "Sharpe holds" in `status.info`)
>
> *"**Cooking** — more paper evidence required; capital stages locked."* (with "Cooking" in `status.info`)
>
> *"**Lifecycle exempt.** Currently holding SPY."* (with "Lifecycle exempt" in `status.positive`)

The colored word inside the italic sentence is a Wall Street Journal convention: emphasis word becomes the eye-catcher, prose around it carries context. The `meta` line below holds operational detail (day count, open positions, capital deployed) in mono.

**Do not use the StatusPill component for this.** StatusPill is for column-positioned status in tables; the inline-signal-within-prose is a different pattern that requires direct text-color binding.

---

## 5. Visual escalation by stage

Rows in different stages get visually escalating treatments. The escalation is **subtle** — typography weight stays the same; only color, opacity, and background-tint change.

| Stage | Row treatment |
|---|---|
| **IDLE** | Strategy name in `text.secondary` (not primary). Numerics show em-dashes (`—`) in `text.disabled`. Visually quietest — nothing to evaluate yet. |
| **BACKTEST** | Full-strength typography. Numerics rendered. |
| **PAPER** | Full-strength typography. Numerics rendered. |
| **MICRO LIVE** | Full-strength typography. Numerics rendered. `meta` line gains capital-deployed detail (`$4.2k deployed`). |
| **LIVE** | Full-strength typography + 5%-alpha oxblood (`brand.accent`) wash on row background + 2px solid `brand.accent` left border. Editorial "lead story" treatment. |

The LIVE row is the only row on the surface with a background color other than transparent. Don't overdo it — 5% alpha is intentional.

---

## 6. Action button — escalating by stage

Each row has a single action button on the right edge. The button's variant communicates the friction level of the next forward action. The Button component (`Button.qml`) already supports the variants; use it directly.

| Stage | When forward-eligible | When blocked / in-progress |
|---|---|---|
| IDLE | `outlined` "Run backtest →" | n/a |
| BACKTEST | `primary` "→ Promote to paper" | `outlined` "View evidence →" or `ghost` "in progress…" |
| PAPER | `ghost` "locked" while ADR 0004 remains in force; future ADR may reopen | `outlined` "View evidence →" |
| MICRO LIVE | `ghost` "locked" while ADR 0004 remains in force; future ADR may reopen | `ghost` "no action" |
| LIVE | `outlined` "Open detail →" | n/a |

**Capital-bearing promotion controls stay locked while ADR 0004 remains in force.** A future ADR that opens micro-live or live may reintroduce typed-confirm critical controls, but this mockup is not authority to enable them.

The per-row action button opens the Action menu for that row. Available actions are computed from the row's current read-model state; actions that are not valid for the current stage or evidence state are hidden entirely.

---

## 7. Action menu mechanics

State changes on the Bench are driven by a per-row **Action menu** — a dropdown accessible from the action button on each row. Cross-section drag is not a state-change mechanism.

### Action menu pattern

- Each row has a single action button on the right edge. Clicking it opens the Action menu for that row.
- The menu shows only the actions that are valid for the current row state. Actions that are structurally blocked (gate fails, ADR 0004 lock, etc.) are **hidden entirely** — not disabled. The absence of an action is itself information.
- Available actions are computed from the read-model state at render time, not lazily at hover.
- Evidence modals (gate table, strategy detail) are accessible from any row regardless of action availability.

### Action menu items by stage

| Stage | Available actions |
|---|---|
| IDLE | "Run backtest →" |
| BACKTEST (gate-pass) | "→ Promote to paper", "View evidence →" |
| BACKTEST (in-progress or gate-fail) | "View evidence →" |
| PAPER | "View evidence →" (capital-stage promotions hidden while ADR 0004 in force) |
| MICRO LIVE | "Open detail →" (capital-stage promotions hidden while ADR 0004 in force) |
| LIVE | "Open detail →" |

### Friction escalation per ADR 0005 / CLAUDE.md "Autonomy Boundary"

These transitions remain locked while ADR 0004 is in force. If a future ADR opens them, they **always require typed confirmation** before any state mutation:

- Promotion to LIVE
- Demotion from LIVE
- Re-enabling any strategy after a kill-switch event
- Increasing capital allocation on a live strategy

The friction is enforced by the modal the Action menu triggers. The autonomy boundary is preserved at the modal level.

### Within-section drag for priority reorder

Within a single stage section, rows may be reordered by drag to set visual priority. This drag is **purely cosmetic** — it does not mutate `promotion_stage`, `session_state`, or any durable field. The reorder is reflected in the `stage_section` display order only.

- Drag handle: the 6-dot grip glyph in the row gutter (see §4)
- Displacement threshold: ~6px to prevent accidental drags from clicks
- During drag, the row renders as a floating ghost; the origin slot remains as a placeholder
- Dropping within the same section reorders; dropping outside the section is a no-op (not a promotion/demotion)

---

## 8. Modals — the three patterns

There are three modal flows used by the Bench. **All three share the same surface treatment**; only content varies.

### Common surface treatment

- Full-page overlay: `rgba(10, 9, 7, 0.82)` + 2px backdrop-filter blur
- Modal centered, max-width ~620px
- Modal background: `Theme.color.surface.base`
- Modal border: 1px `Theme.color.border` on left/right/bottom; **2px solid top border** in a stage-appropriate color:
  - **`status.negative` (rust)** for blocked-promotion and consequence-confirm modals
  - **`brand.accent` (oxblood)** for typed-confirm-promotion-to-live modals (this is the only place oxblood appears on a modal — the brand color marks "deliberate brand-level commitment")
- Padding: 36px top, 44px sides, 32px bottom

### Pattern A — Blocked-promotion modal (gate failure)

Shown when a forward-promotion drop is blocked because one or more gates fail.

Content structure:

1. **Eyebrow** — "Cannot promote yet" — `label.xs`, `status.negative`
2. **Title** — Newsreader display, e.g. *"NR7 Inside-Day Breakout fails one gate."*
3. **Prose paragraph** — italic Newsreader, plain-language explanation referencing the gate count
4. **Gate table** — 4-column (Gate / Current / Required / Result), one row per gate, pass/fail styled in sage/rust
5. **"What unblocks this" section** — italic Newsreader bullet list of remediation options. Each option is concrete and actionable.
6. **Action footer** — `outlined` "Open evidence →" + `ghost` "Cancel"

The gate table is a **reusable component** — it appears here, in the Strategy Detail drill-in, and (where applicable) in the consequence-confirm modal. Build it as a reusable element.

### Pattern B — Typed-confirm modal (future live boundary)

Reserved for a future ADR that opens the live boundary. All gates may have passed; the typed phrase is the friction.

Content structure:

1. **Eyebrow** — "Confirm promotion to live" or "Confirm retirement from live" — `label.xs`, `text.muted`
2. **Title** — Newsreader display, e.g. *"Promote RSI-2 Pullback to live trading."*
3. **Prose paragraph** — italic Newsreader, names the consequences. For promotion: *"This will allocate live capital and begin live trading. The decision is recorded permanently per ADR 0005."* For demotion: *"This will halt live trading on regime.daily.* — close 1 open position at the next session boundary, drain over 5 sessions, and archive attribution."*
4. **Typed-confirm field** — `TextField` requiring the operator to type the literal phrase `PROMOTE` (or `RETIRE`). The action button is `disabled` until the field's value matches exactly.
5. **Action footer** — `critical` "PROMOTE →" (or `critical` "RETIRE →") + `ghost` "Cancel". The action button is uppercase letter-spaced even disabled.

This modal's top border is `brand.accent` (oxblood), not rust — the live boundary is a brand-level commitment, not a system-decline event.

### Pattern C — Consequence-confirm modal (demotion / re-test)

Shown for backward demotion that doesn't cross the live boundary, or for re-test from any stage.

Content structure:

1. **Eyebrow** — "Confirm move" — `label.xs`, `text.muted`
2. **Title** — Newsreader display, e.g. *"Move ATR Channel Breakout back to backtest."*
3. **Prose paragraph** — italic Newsreader, names the consequences (positions to close, evidence to re-evaluate, etc.)
4. **Action footer** — `primary` "Move →" + `ghost` "Cancel"

This modal's top border is `status.negative` (rust) like Pattern A — both surfaces communicate "the system is asking you to slow down and read."

---

## 9. Things to NOT do

These are the moves an implementer's instinctive UX habits will pull toward. Resist them.

- **No symmetric horizontal columns.** This is the kanban shape we explicitly leave behind. Do not render the five stages side-by-side.
- **No card chrome on rows.** Rows are bound by hairlines only. No rounded backgrounds, no drop shadows, no per-row containers.
- **No drag-without-confirmation at the live boundary.** Drag mechanics never bypass typed-confirm for promotion-to-live or demotion-from-live.
- **No 4-pip / progress-bar indicators per row.** Stage is communicated by section membership; per-row stage indicators are redundant.
- **No `✕` / `✓` icons.** Use color (sage / rust) and language ("passes" / "fails") to communicate gate evaluation. Iconography clashes with the editorial-print aesthetic.
- **No greeting copy.** The page header is `THE BENCH` (Newsreader display) plus an italic standfirst — not "Good morning, here's your bench."
- **No batch-action toolbars across the top.** Single-row operations only in v1. Multi-select with contextual actions may come later but is out of scope.
- **No animated value transitions.** Per existing motion-discipline (DESIGN_SYSTEM.md §5.3 / 8): metric values, trade counts, and status changes update **instantly**, not by tweening. Animation is reserved for hover state, drag mechanics, and modal enter/exit.
- **No StatusPill on rows.** The inline-signal-within-prose is a different pattern. StatusPill is for column-based status in tabular surfaces, not for the Bench.
- **No "AI suggests promoting X" copy.** The system reports; it does not advise. Surface the facts (gates pass, evidence locked, days at stage). The operator decides.

---

## 10. Reuse from the existing design system

These already exist in the codebase. **Do not rebuild them.**

| Asset | Path | What it provides |
|---|---|---|
| Theme tokens | `src/milodex/gui/qml/Milodex/Theme.qml` | All colors, typography roles, spacing scale, motion durations, easing curves |
| Editorial Dark theme | `src/milodex/gui/qml/Milodex/themes/EditorialDark.qml` | Concrete hex values for the default theme |
| Editorial Light theme | `src/milodex/gui/qml/Milodex/themes/EditorialLight.qml` | Concrete hex values for the light theme |
| Bronze theme | `src/milodex/gui/qml/Milodex/themes/Bronze.qml` | Concrete hex values for the alternate theme |
| Button | `components/Button.qml` | All five variants (`primary` / `secondary` / `ghost` / `danger` / `critical`) |
| Surface | `components/Surface.qml` | The padded container used for modal bodies and any panel surface |
| Theme manager | `theme_manager.py` + `Theme.qml` activeTheme dispatch | Live theme switching |

Bind every visual property through `Theme.<token>`. Never hardcode hex. The contrast audit (`tests/milodex/gui/test_theme_contrast.py`) will fail CI on any unbound color drift.

The Bench surface should be added at:

- `src/milodex/gui/qml/Milodex/surfaces/BenchSurface.qml` — the QML file
- Wire into `Main.qml` as a new tab/surface alongside `AnchorSurface` and the `DesignSystemShowcase`

Live data wiring (when the surface composes against real strategy state) should follow the **OperationalState pattern** documented in `src/milodex/gui/operational_state.py`:

- A new `StrategyBankState(QObject)` exposing Q_PROPERTYs for the strategy list per stage
- Two-cadence polling (fast for kill-switch / state changes, slow for evidence refresh)
- Worker-thread bridge via `Qt.QueuedConnection`
- Idempotent `start()` / `stop()` with worker drain

---

## 11. Implementation sequencing — recommended

Two PRs, decent each. PR 1 ships a fully usable Bench; PR 2 adds the drag ergonomics on top.

### PR 1 — Bench-static

- Vertical stage-stacked layout
- All section headers, row anatomy, status prose, action buttons
- Inline-signal coloring
- LIVE-row distinguished treatment (oxblood wash + 2px accent border)
- All three modal patterns (blocked-promotion, typed-confirm, consequence-confirm) wired to per-row buttons
- Reusable gate table component
- Empty-state caption per section
- Section header drop-target *visual states* defined in CSS / QML, but not yet wired to drag events

This PR ships a fully functional Bench. Every action is reachable via per-row click. No drag yet.

### PR 2 — Action menu write mechanics

- Action menu wiring: per-row dropdown surfacing available actions computed from read-model state
- Unavailable actions hidden (not disabled) based on current stage, gate verdicts, and ADR 0004 lock state
- Write-capable modal flows triggered from the Action menu (same modal components from PR 1, now wired to submit mutations)
- Within-section drag for priority reorder (`DragHandler` or equivalent), scoped to cosmetic `stage_section` ordering only
- Touch / trackpad-drag testing on Windows for Qt 6.11+

If PR 2 hits unexpected scope, PR 1 is already shipped and fully usable. The read-only Bench is a complete product; write mechanics are an operational upgrade, not a prerequisite.

---

## 12. Visual reference

Open `docs/mockups/bench-surface.html` in a browser. The mockup uses the actual Editorial Dark hex values and live Google Fonts for Newsreader, Public Sans, and JetBrains Mono. It shows:

- The complete vertical-stage layout with realistic strategy data
- All five action button variants in their natural row contexts
- The LIVE row's lead-story treatment
- The drag-handle affordance on every row
- The blocked-promotion modal in its open state, anchored over the page

The mockup is the source of truth for visual decisions. If the brief and the mockup disagree, **the mockup wins** — update the brief.
