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

The authoritative decision register for the v1 implementation is the ADR pack: [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) (v1 scope and forbidden mutations), [ADR 0050](../adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md) (read-model schema and menu computation rules), [ADR 0047](../adr/0047-bench-action-availability-is-the-validation-surface.md) (action availability and empty-menu floor). Where this brief and an ADR conflict, the ADR governs.

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
| 7 | **Action button** | Uniformly labeled `Action`; variant escalates by row state (see §6) | per `Button.qml` |

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

## 6. Action button

Every row has a single action button on the right edge, uniformly labeled **`Action`**. The label is always the literal word `Action` — it does not change based on stage, evidence state, or available operations. The button opens the Action menu for that row (§7); the menu's contents — not the button's label — communicate what's available.

The button's **visual variant** escalates by row state to communicate friction level, using the same `Button.qml` variants as before:

| Row state | Button variant |
|---|---|
| Rows with forward-eligible actions available | `primary` |
| Rows with evidence or informational actions only | `outlined` |
| Rows where all state-changing actions are hidden (evidence-only state) | `ghost` |

**Capital-stage promotion items are hidden from the Action menu while ADR 0004 remains in force.** The button is still present and opens the menu; the menu simply does not show `Promote to Micro Live`, `Promote to Live`, or any other capital-stage directional verb until a future ADR lifts the lock. The button does not show `locked` — it shows `Action` and opens a shorter menu.

---

## 7. Action menu mechanics

State changes on the Bench are driven by a per-row **Action menu** — a dropdown accessible from the `Action` button on each row. Cross-section drag is not a state-change mechanism.

### §7.1 — Action menu pattern

- Each row has a single `Action` button on the right edge. Clicking it opens the Action menu for that row.
- Menu items are computed from `(current_stage, evidence_by_stage, runs_in_flight)` in the Python read-model layer per [ADR 0047](../adr/0047-bench-action-availability-is-the-validation-surface.md). QML consumes the precomputed per-row action set; QML does not own the computation rules.
- Unavailable actions are **hidden, not disabled** per ADR 0047 Decision 2. The absence of an action is itself information; gate-failure context lives in the evidence modal.
- `Open Evidence` is always present — it is the menu's empty-menu floor per ADR 0047 Decision 5. The Action menu is never empty.
- Available items are computed at render time, not lazily on menu open.

### §7.2 — Verb grammar

The Action menu's verbs are grouped into three closed classes. New verbs require an ADR amendment to [ADR 0050](../adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md).

**Directional (operator-driven):**
`Promote to Paper`, `Promote to Micro Live`, `Promote to Live`, `Demote to Backtest`, `Return to Paper`, `Return to Micro Live`, `Return to Live`, `Return to Idle`

- `Return to <active stage>` verbs are the leave-IDLE affordance; they surface only when the target stage's evidence is `freshness ∈ {Fresh, Aging}` AND `gate_result == Pass` (or `gate_result == NotApplicable` for LIVE — the only stage where NotApplicable is a valid gate_result, since LIVE has no further promotion gate).
- `Return to Idle` is the to-shelf affordance; surfaces from any active stage.
- There is no `Promote to Backtest` verb. `IDLE → BACKTEST` is system-driven on backtest job acceptance.
- Use `Return to Idle`; do not use `Send to Idle`.

**Invocation (operator-driven):**
`Initiate Backtest`, `Refresh Backtest`, `Start Trading`, `Stop Trading`

- `Stop Trading` maps to controlled-stop semantics — finish current cycle, close the `strategy_runs` row cleanly, do **not** cancel open orders. See [ADR 0012](../adr/0012-runtime-and-dual-stop.md) for the dual-stop model and [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) Decision 4 for the Bench mapping. The kill switch is a separate global affordance on the Anchor view, not a Bench Action menu item.

**Informational (empty-menu floor):**
`Open Evidence` — always present regardless of any other menu computation input.

### §7.3 — Menu items by row state

Menu visibility is computed from `(current_stage, evidence_by_stage, runs_in_flight)`. The pseudocode lives in ADR 0050 Decision 5; the table below groups the canonical row states for implementation reference.

**IDLE — no prior history (Missing+Pending evidence at all stages, no run in flight)**

| Visible items |
|---|
| `Initiate Backtest` |
| `Open Evidence` |

**IDLE — no prior history, backtest run in flight (`runs_in_flight[BACKTEST] == True`)**

| Visible items |
|---|
| `Open Evidence` (monitoring affordance while run is in flight; no second re-run verb) |

**IDLE — with prior PAPER evidence Fresh+Pass (returning shelf candidate)**

| Visible items |
|---|
| `Return to Paper` |
| `Initiate Backtest` |
| `Open Evidence` |

**IDLE — with prior MICRO LIVE evidence Fresh+Pass (deeper shelf candidate)**

| Visible items |
|---|
| `Return to Micro Live` |
| `Return to Paper` *(if PAPER evidence is also Fresh/Aging+Pass)* |
| Re-run verb depends on BACKTEST evidence: `Initiate Backtest` if BACKTEST is Missing, Invalidated, or `(Aging|Stale)+Fail`; `Refresh Backtest` if BACKTEST is `(Aging|Stale)+Pass`; hidden if BACKTEST is Fresh+anything. |
| `Open Evidence` |

**IDLE — with prior LIVE evidence Fresh+NotApplicable (deepest shelf candidate)**

| Visible items |
|---|
| `Return to Live` *(only stage where `gate_result == NotApplicable` satisfies `can_return_to`; LIVE has no further promotion gate)* |
| `Return to Micro Live` *(if MICRO LIVE evidence is also Fresh/Aging+Pass)* |
| `Return to Paper` *(if PAPER evidence is also Fresh/Aging+Pass)* |
| Re-run verb: hidden if BACKTEST evidence is Fresh+anything (workflow discipline); `Refresh Backtest` if BACKTEST is `(Aging|Stale)+Pass`; `Initiate Backtest` if BACKTEST is Missing, Invalidated, or `(Aging|Stale)+Fail`. *(LIVE evidence's `NotApplicable` does not trigger any re-run verb directly per `re_run_verb`.)* |
| `Open Evidence` |

**IDLE — with prior MICRO LIVE evidence Stale+Pass; BACKTEST evidence also Stale+Pass (aged-out shelved candidate)**

| Visible items |
|---|
| `Refresh Backtest` *(driven by BACKTEST evidence Stale+Pass per `re_run_verb`; MICRO LIVE Stale+Pass cannot support a Return verb but signals the strategy has prior usable history. Note: at IDLE, `re_run_verb` operates on the BACKTEST evidence record — the verb name reflects the stage being re-run.)* |
| `Open Evidence` |

**IDLE — with prior MICRO LIVE evidence Stale+Fail; BACKTEST evidence Stale+Fail or Invalidated (aged out, no usable baseline)**

| Visible items |
|---|
| `Initiate Backtest` *(driven by BACKTEST evidence; Stale+Fail and Invalidated both produce `Initiate Backtest` per `re_run_verb`. Stale failing evidence has no usable baseline; start fresh.)* |
| `Open Evidence` |

**BACKTEST — gate pass, Fresh+Pass evidence, no run in flight**

| Visible items |
|---|
| `Promote to Paper` |
| `Return to Idle` |
| `Open Evidence` |

**BACKTEST — gate fail, Fresh+Fail evidence (just-completed failing run)**

| Visible items |
|---|
| `Return to Idle` |
| `Open Evidence` (no re-run verb — evidence is Fresh; an invalidating change must come first) |

**BACKTEST — Aging+Pass evidence (passing but approaching stale)**

| Visible items |
|---|
| `Promote to Paper` |
| `Refresh Backtest` |
| `Return to Idle` |
| `Open Evidence` |

**BACKTEST — run in flight (`runs_in_flight[BACKTEST] == True`; evidence may be Missing+Pending for an initial run, or any prior state for a refresh-in-flight)**

| Visible items |
|---|
| `Return to Idle` |
| `Open Evidence` (monitoring affordance; no re-run verb while run is in flight) |

**PAPER — current Fresh+Pass evidence, session idle**

| Visible items |
|---|
| `Start Trading` |
| `Demote to Backtest` |
| `Return to Idle` |
| `Open Evidence` |
| *(capital-stage promotions hidden while ADR 0004 in force)* |

**PAPER — session running (`Stop Trading` surfaces, `Start Trading` hidden)**

| Visible items |
|---|
| `Stop Trading` |
| `Demote to Backtest` |
| `Return to Idle` |
| `Open Evidence` |

**PAPER — Aging+Pass evidence, session idle**

| Visible items |
|---|
| `Start Trading` |
| `Refresh Backtest` *(aging evidence triggers refresh; Promote to Micro Live also gated-out by ADR 0004 forward lock)* |
| `Demote to Backtest` |
| `Return to Idle` |
| `Open Evidence` |

> **Coverage note:** the rows above are canonical, not exhaustive. Fixture data per ADR 0049 Decision 5 must additionally cover Stale and Invalidated evidence at PAPER, MICRO LIVE, and (where applicable) other states the menu rules can produce. Each fixture row exercises a distinct branch of the menu computation; the table here demonstrates the rule patterns.

**MICRO LIVE — all states (capital-stage forward path locked under ADR 0004)**

| Visible items |
|---|
| `Stop Trading` (if session running) or `Start Trading` (if session idle) |
| `Return to Idle` |
| `Open Evidence` |
| *(Promote to Live is hidden by ADR 0004's forward lock. Demote to Backtest and Return to Paper are capital-affecting demotions; per [ADR 0043](../adr/0043-bench-demotion-actions-open-a-governance-flow.md) Decision 3 they remain locked rather than merely confirmed while ADR 0004 is in force.)* |

**LIVE — all states (capital-stage paths locked while ADR 0004 in force)**

| Visible items |
|---|
| `Stop Trading` (if session running) or `Start Trading` (if session idle) |
| `Return to Idle` |
| `Open Evidence` |
| *(Demote to Backtest and Return to Micro Live are capital-affecting demotions; per [ADR 0043](../adr/0043-bench-demotion-actions-open-a-governance-flow.md) Decision 3 they remain locked rather than merely confirmed while ADR 0004 is in force. The hide is governance-gated, not forward-lock-gated. LIVE evidence carries `gate_result == NotApplicable` since LIVE has no further promotion gate.)* |

### §7.4 — Refresh Backtest vs. Initiate Backtest boundary

These two verbs have a precise, non-overlapping boundary:

- **`Refresh Backtest`** — only for `(Aging|Stale)+Pass` evidence. Connotation: "this passed, but it's been a while; renew usable evidence." Never surfaces for any Fail variant.
- **`Initiate Backtest`** — for `Invalidated` evidence (at any age), `(Aging|Stale)+Fail` evidence, and `Missing+Pending` with no run in flight (no completed evidence and nothing started).
- **No re-run verb** for `Fresh+Pass`, `Fresh+Fail`, or any evidence state with a run in flight. For the Fresh cases: an invalidating change (config edit, parameter change, risk-policy update, methodology change) must transition the evidence to `Invalidated` first; only then does `Initiate Backtest` appear. This enforces "do something different before retrying" rather than allowing blind re-runs. `Open Evidence` carries the monitoring affordance while a run is in flight.

The semantic boundary keeps the audit trail honest: a `Refresh` event in the audit trail implies prior usable evidence existed; an `Initiate` event does not.

### §7.5 — Within-section drag for priority reorder

Within a single stage section, rows may be reordered by drag to set visual priority. This drag is **purely cosmetic** — it does not mutate `promotion_stage`, `session_state`, or any durable field. Per [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md), the v1 reorder is session-only and not persisted: a page navigation, view switch, or app restart loses the reorder.

- Drag handle: the 6-dot grip glyph in the row gutter (see §4)
- Displacement threshold: ~6px to prevent accidental drags from clicks
- During drag, the row renders as a floating ghost; the origin slot remains as a placeholder
- Dropping within the same section reorders; dropping outside the section is a no-op (not a promotion/demotion)
- Cross-section drag does not exist

### §7.6 — v1 visual-prototype scope

Every menu item, every modal, every drag action, and every confirmation in v1 is rendered for feel-validation only. **No backend state is mutated.** Specifically, per [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) Decision 2, all eight forbidden mutation paths apply:

- No promotion writes
- No demotion writes
- No broker calls (Alpaca client not invoked from any Bench path)
- No backtest execution calls (no actual backtest jobs are created or queued)
- No trading-session start/stop writes (`strategy_runs` rows not opened or closed by Bench paths)
- No persisted priority reorder (within-section drag is session-only)
- No event-store writes from Bench code paths (no operator-action ledger records)
- No kill-switch triggers from Bench paths

Any contributor who finds a Bench path requiring one of the above must escalate before proceeding. This constraint is binary — "limited writes" is not an acceptable interpretation.

### Friction escalation per ADR 0005 / CLAUDE.md "Autonomy Boundary"

These transitions remain locked while ADR 0004 is in force. If a future ADR opens them, they **always require typed confirmation** before any state mutation:

- Promotion to LIVE
- Demotion from LIVE
- Re-enabling any strategy after a kill-switch event
- Increasing capital allocation on a live strategy

The friction is enforced by the modal the Action menu triggers. The autonomy boundary is preserved at the modal level.

---

## 8. Modals — the three patterns

> **v1 note:** All three modal patterns are visual stubs per [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md). Every modal renders and dismisses cleanly without mutating backend state. Modal copy may use placeholder phrasing such as "this would have done X" where helpful.

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

Shown when a forward promotion is blocked because one or more gates fail.

Content structure:

1. **Eyebrow** — "Cannot promote yet" — `label.xs`, `status.negative`
2. **Title** — Newsreader display, e.g. *"NR7 Inside-Day Breakout fails one gate."*
3. **Prose paragraph** — italic Newsreader, plain-language explanation referencing the gate count
4. **Gate table** — 4-column (Gate / Current / Required / Result), one row per gate, pass/fail styled in sage/rust
5. **"What unblocks this" section** — italic Newsreader bullet list of remediation options. Each option is concrete and actionable.
6. **Action footer** — `outlined` "Open Evidence" + `ghost` "Cancel"

The gate table is a **reusable component** — it appears here, in the Strategy Detail drill-in, and (where applicable) in the consequence-confirm modal. Build it as a reusable element.

### Pattern B — Typed-confirm modal (future live boundary)

Reserved for a future ADR that opens the live boundary. All gates may have passed; the typed phrase is the friction.

Content structure:

1. **Eyebrow** — "Confirm promotion to live" or "Confirm retirement from live" — `label.xs`, `text.muted`
2. **Title** — Newsreader display, e.g. *"Promote RSI-2 Pullback to live trading."*
3. **Prose paragraph** — italic Newsreader, names the consequences. For promotion: *"This will allocate live capital and begin live trading. The decision is recorded permanently per ADR 0005."* For demotion / shelving: *"This will halt live trading on regime.daily — close 1 open position at the next session boundary, drain over 5 sessions, and archive attribution."*
4. **Typed-confirm field** — `TextField` requiring the operator to type the literal phrase `PROMOTE` (or `RETIRE`). The action button is `disabled` until the field's value matches exactly.
5. **Action footer** — `critical` "PROMOTE →" (or `critical` "RETIRE →") + `ghost` "Cancel". The action button is uppercase letter-spaced even disabled.

This modal's top border is `brand.accent` (oxblood), not rust — the live boundary is a brand-level commitment, not a system-decline event. The verbs in the eyebrow and title use the locked grammar: `Promote to Live`, `Return to Idle`, etc.

### Pattern C — Consequence-confirm modal (demotion)

Shown for backward demotion that doesn't cross the live boundary. Per the verb grammar (ADR 0050 Decision 7), demotion and re-test are separate verbs: a re-test is a `Demote to Backtest` (Pattern C) followed by a separate `Initiate Backtest` invocation on the resulting BACKTEST row. Pattern C governs only the demotion step.

**v1 scope:** in v1, Pattern C is reachable only from PAPER via `Demote to Backtest`. Capital-affecting demotions (`Return to Paper` from MICRO LIVE; `Demote to Backtest` and `Return to Micro Live` from LIVE) are locked while ADR 0004 is in force per ADR 0043 Decision 3 and do not surface in any menu. When the live boundary opens, those paths reach Pattern C as well.

Content structure:

1. **Eyebrow** — "Confirm demotion" — `label.xs`, `text.muted`
2. **Title** — Newsreader display, e.g. *"Demote to Backtest — ATR Channel Breakout."*
3. **Prose paragraph** — italic Newsreader, names the consequences (positions to close, evidence to re-evaluate, etc.)
4. **Action footer** — `primary` "Confirm →" + `ghost` "Cancel"

This modal's top border is `status.negative` (rust) like Pattern A — both surfaces communicate "the system is asking you to slow down and read." Titles use the locked verb names: `Demote to Backtest`, `Return to Idle`, etc.

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
- **No backend mutations from Bench paths in v1.** Per [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) Decision 2, all eight mutation classes are forbidden: no promotion writes, no demotion writes, no broker calls, no backtest execution, no trading-session start/stop, no persisted priority reorder, no event-store writes, no kill-switch triggers. Any Bench code path that reaches a write surface in v1 is a bug.

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

## 11. Implementation sequencing — v1 PR plan

The existing implementation (`BenchSurface`, `BenchRow`, prototype Action menu, prototype modals, in-section reorder) is **reconciled** to the ADR pack rather than rebuilt. Each PR's scope is "bring the existing code into compliance with the ADRs and this brief," not "build from scratch."

### PR D — Read-model schema extension

Decent. Adds `Freshness` and `GateResult` enums; `EvidenceRecord` dataclass; `evidence_by_stage: dict[Stage, EvidenceRecord]` and `runs_in_flight: dict[Stage, bool]` fields on the strategy read model. Schema only — no fixture data, no freshness computation. Freshness computation is v2 work deferred to a future ADR.

### PR E — Fixture data set

Small. Populates fixture strategies spanning the menu state space per [ADR 0049](../adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) Decision 5: every promotion stage, every `Freshness` value at relevant stages, every `GateResult` value, at least one fixture exercising every menu rule in ADR 0047 and ADR 0050, and `Open Evidence` verifiable as the empty-menu floor on every fixture row.

### PR F — BenchSurface + BenchRow QML reconciliation

Decent. Reconciles the existing vertical `BenchSurface` and `BenchRow` QML to the ADR pack: vertical sections, full row anatomy per §4, visual escalation per §5, section header anatomy per §3, empty-state treatment. Existing code refined, not rebuilt.

### PR G — Action menu wiring

Decent. **Major checkpoint.** Reconciles the existing prototype Action menu to the ADR pack: uniform `Action` button label per §6, per-row menu items computed from `(current_stage, evidence_by_stage, runs_in_flight)` in Python per §7.1, hide-don't-disable per ADR 0047 Decision 2, `Open Evidence` floor always present per ADR 0047 Decision 5. Existing prototype refined.

### PR H — Within-section drag

Small. Reconciles existing in-section reorder to the ADR pack: cosmetic only, non-persisting, session-lifetime only per §7.5 and ADR 0049. Cross-section drag does not exist.

### PR I — Evidence modal + per-action confirmation modals

Decent. Reconciles existing prototype modals to the ADR pack: all three patterns per §8, locked verb names in eyebrows and titles, visual stubs with no backend mutation per ADR 0049. `Open Evidence` modal is a required surface per ADR 0047 — absence of visible actions is not self-explanatory without it.

---

## 12. Visual reference

Open `docs/mockups/bench-surface.html` in a browser. The mockup uses the actual Editorial Dark hex values and live Google Fonts for Newsreader, Public Sans, and JetBrains Mono. It shows:

- The complete vertical-stage layout with realistic strategy data
- All five action button variants in their natural row contexts
- The LIVE row's lead-story treatment
- The drag-handle affordance on every row
- The blocked-promotion modal in its open state, anchored over the page

The authority order is: **ADRs > this brief > mockup**. If the brief and the mockup disagree, update the brief against the ADRs, then update the mockup against the brief. The mockup is the source of truth for visual decisions not governed by an ADR; ADRs govern interaction model, verb grammar, schema, and scope.
