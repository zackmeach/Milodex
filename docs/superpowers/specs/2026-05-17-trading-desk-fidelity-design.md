# The Trading Desk — Design-Fidelity Pass (Design Spec)

**Date:** 2026-05-17
**Branch:** `feat/trading-desk` (builds on HEAD `c709b3d`)
**Status:** Approved in brainstorming; pending spec-review.
**Companion plan:** to be written (`docs/superpowers/plans/2026-05-17-trading-desk-fidelity.md`).

---

## 0. Why this exists

The functional Trading Desk (7-section IA, live read-models, three-state
honesty, ~620 tests green) is correct but **visually diverges from the rest
of the app** in a way the old pre-PR-8 mock `DeskSurface.qml` did not. Root
cause: design fidelity was never an acceptance criterion, a dispatch input,
or a review gate — the visual reference never crossed the subagent dispatch
boundary. This pass closes that gap by **restyling the verified functional
layer in place** to the old surface's exact visual language.

**The reference is one file:** the old `DeskSurface.qml` as it exists on
`master` (commit `757afe7`), 1326 lines. Every token binding below is
transcribed from it with line numbers cited inline — save the two
primitives explicitly marked *invented to the app idiom* (the type-only
toggle, §2 P4; the sparkline stroke, §3 II), which obey the same design
law without a literal reference line to transcribe. The reference is *not*
the Claude Design mockup (explicitly retired by the operator).

This is a **restyle-only** pass. It moves tokens and structure. It does not
touch behaviour. See §5 for the hard preserve-untouched contract.

---

## 1. The divergence (what this pass fixes)

Concrete diff between the new surface and the master reference:

1. **Section headers.** New: flat tiny uppercase roman-numeral labels, no
   serif letter, no hairline, no meta slot. Reference: serif display letter
   + baseline-aligned spaced-uppercase name + right mono-tnum meta + hairline
   rule. This motif repeats 7× per screen — it is the app's visual signature.
2. **Italic serif standfirsts — absent.** Reference has one editorial italic
   line under every section header. New has none.
3. **Two foreign idioms:** the boxy `SegmentedToggle` pill and the filled
   funnel progress bars. Nothing else in the app uses chrome controls or
   bars — the app idiom is type, hairline, colour ("*No iconography — color
   only*", master file ethos, lines 9–10).
4. **Rhythm/air.** New is cramped; reference uses `space[7]` page inset,
   `space[6]` block spacing, serif display numerals.

---

## 2. Ported primitives (P1–P4)

All token names below are valid `Theme.*` tokens used verbatim in the master
reference. **Zero hardcoded literals** — every visual property binds a token.

### P1 — `SectionHeader` adopts the master `SectionLabel` band

Transcribe the master `component SectionLabel: Column` structure
(**master:208–262**) into the new `SectionHeader` component. Keep the new
IA's roman numerals (`I`–`VII`) as the `letter` value — same display
treatment the master `A.`–`H.` used, new numbering.

| Element | Binding | Master line |
|---|---|---|
| Root | `Column { spacing: Theme.space[2]; width: parent.width }` | 208–214 |
| Letter | `color: Theme.color.brand.primary`; `font.family/pixelSize/weight: Theme.typography.display.sm.*`; `anchors.left/top: parent` | 220–229 |
| Name | `anchors.left: letterText.right`; `anchors.leftMargin: Theme.space[3]`; `anchors.baseline: letterText.baseline`; `color: Theme.color.text.secondary`; `font.*: Theme.typography.label.xs.*` incl. `letterSpacing`; `font.capitalization: Font.AllUppercase` | 231–243 |
| Meta (right) | `visible: meta !== ""`; `anchors.right: parent.right`; `anchors.baseline: letterText.baseline`; `color: Theme.color.text.muted`; `font.family/pixelSize: Theme.typography.data.sm.*`; `font.features: Theme.typography.data.sm.features` (tnum) | 245–254 |
| Hairline | trailing `Rectangle { width: parent.width; height: 1; color: Theme.color.border.subtle }` | 257–261 |

Public API of `SectionHeader` is extended only by additive properties
(`letter`, `name`, `meta`); no call-site signal/behaviour change.

### P2 — Italic serif standfirst slot

One italic line directly under every `SectionHeader`. Treatment
transcribes the master **section** standfirst (master:653–659 — *not* the
page-header standfirst at master:326–331, which is a different, larger
size; do not confuse them):

- `color: Theme.color.text.secondary`
- `font.family: Theme.typography.body.md.family`
- `font.pixelSize: Theme.typography.body.sm.size`
- `font.italic: true`
- `wrapMode: Text.WordWrap` (additive — master section standfirst does not
  set it; safe for multi-line, not a fidelity change)

Per-section copy (final, not placeholder):

| § | Standfirst |
|---|---|
| I Risk & Mode | *risk posture and operating mode, on live broker state* |
| II Performance & Trust | *realised P/L for the selected window, with snapshot freshness stated plainly* |
| III Active Operations | *what is running right now in this session* |
| IV Risk Layer Throughput | *how work moved through the risk gate, stage by stage* |
| V Strategy Attention | *strategies that need an operator's eye, by reason* |
| VI Market Tape | *instrument status for the market-data feed* |
| VII Order / Signal Tape | *the chronological record of orders, signals, and fills* |

Section II's standfirst is **replaced at runtime** by the three-state
freshness text (empty / stale / fresh) — see §3 II. The logic selecting
which text shows is preserved untouched (§5); only its typography changes
to this treatment.

### P3 — Page rhythm

Adopt the master scroll/page container verbatim (**master:268–337**):

- `Flickable { anchors.fill: parent; contentWidth: width; contentHeight: pageColumn.implicitHeight + Theme.space[7] * 2; clip: true; flickableDirection: Flickable.VerticalFlick }`
- `Column { id: pageColumn; x: Theme.space[7]; width: scroller.width - Theme.space[7] * 2; topPadding: Theme.space[7]; spacing: Theme.space[6] }`
- Header band preserved as-is (kicker `label.xs` `text.muted` AllUppercase;
  serif `display.lg` title `brand.primary` with `letterSpacing: -0.4`;
  accent period `brand.accent` same `display.lg`; italic `body.md.size + 1`
  standfirst `text.secondary`) — master:286–334.
- Full-width top hairline under header band:
  `Rectangle { width: parent.width; height: 1; color: Theme.color.border.regular }`
  (master:337).
- Inter-section dividers: `Theme.color.border.subtle` hairlines (the P1
  trailing rule already provides per-header separation).

### P4 — Type-only period/filter control (the one invented primitive)

The master reference had **no interactive control** — this is the only
primitive not transcribed but invented, to obey the same "type and colour
only, no chrome" law (master ethos 9–10). Operator decision (brainstorming):
**type-only, no chrome.**

`SegmentedToggle` body is rewritten; **public API frozen**
(`property var options`, `property string current`,
`signal activated(string value)`) — see §5.

Rendered as:

- A `Row` of `Text` items, `spacing: Theme.space[4]`.
- Each option: `text: option.label`; `font.*: Theme.typography.label.xs.*`
  incl. `letterSpacing`; `font.capitalization: Font.AllUppercase`.
- Selected: `color: Theme.color.brand.primary`.
  Unselected: `color: Theme.color.text.muted`.
- **No** track, border, fill, radius, or background `Rectangle`.
- One `MouseArea` per word; `onClicked` emits `activated(value)` only when
  `value !== current` (preserve existing guard).

**Defect-A supersession (read with §5).** `SegmentedToggle`'s current
one-way-sizing scaffold (`_labelMetrics`, `_widthProbe`, `_segmentsWidth`,
the positioned-`trackRow` top-down height model) *is* the Defect-A fix. P4
deletes that scaffold by design — the type-only `Row` of `Text` has no
positioner⇄`anchors.fill`⇄child-height cycle by construction, so the
Defect-A class is **structurally moot for this component**, not "preserved
intact." This is the explicit resolution of the apparent §5 conflict: §5's
"one-way sizing" preserve clause scopes to the **DeskSurface RowLayout
`Layout.preferredHeight` contract (Defect-B)**, which P4 does *not* touch.
The polish-loop / non-zero-height **regression tests stay** (they now guard
a structurally-immune component, which is fine — a passing guard is not a
contradiction).

---

## 3. Per-section composition

Recurring rule: **magnitude = right-aligned mono-tnum value + faint
hairline; never a filled bar or chrome.**

**I · Risk & Mode** — serif `display` state line ("Guard ready")
`brand.primary`; P2 italic sub; then a key/value grid — labels
`Theme.typography.label.xs.*` `text.muted` AllUppercase, values
`Theme.typography.data.sm.*` (tnum) `text.primary`. (Master "● RISK & MODE"
block idiom.)

**II · Performance & Trust** — `Theme.typography.display.lg.*` serif P/L
numeral `brand.primary` (negative → `brand.accent`); period switch = P4;
`DAY %` / `SNAPSHOT` mono pair beneath. Sparkline restyled to a single
`Theme.color.border.regular` 1px hairline stroke, no fill, no axis —
**invented to the app idiom, not transcribed** (the master `Sparkline`
usage sets only `showAxis/showGrid/areaAlpha`, never a stroke-colour
token; this is the same invented-not-transcribed status as P4, and ties to
the §3 sparkline soft-spot note). Three-state freshness renders via the P2
standfirst slot (text only, no badge); **selection logic untouched** (§5).

**III · Active Operations** — rows: `name`
(`Theme.typography.label.xs.*`) + right mono-tnum count;
`Theme.color.border.subtle` 1px hairline per row. Empty state: honest
italic "*No data yet.*" `text.muted` (preserved behaviour, restyled).

**IV · Risk Layer Throughput** — **replaces the progress bars.** Master
`A. STRATEGY LADDER` pattern: each stage = label + em-dash micro-gloss
(e.g. `Evaluations — gate inputs`) + right-aligned mono-tnum count
(`Theme.typography.data.*`, tnum), one `Theme.color.border.subtle` 1px
hairline per row. Drop-off reads typographically down the column. No fill.

**V · Strategy Attention** — five `Theme.typography.display.*` serif count
numerals `brand.primary` with `label.xs` `text.muted` AllUppercase captions
beneath; spacing aligned to `Theme.space[6]`.

**VI · Market Tape** — unavailable state: P2 italic standfirst only
(`text.muted`), **no raw path in UI** (the verified VI fix: path → log).

**VII · Order / Signal Tape** — kind filter = P4 type-only row
(`ALL ORDERS REJECTIONS SIGNALS FILLS`). Table: column heads
`Theme.typography.label.xs.*` `text.muted` AllUppercase
(`TIME` `KIND` `SUBJECT / DETAIL`); one `Theme.color.border.subtle`
header hairline; body rows mono-tnum (`Theme.typography.data.*`). Empty:
italic "*No activity*" centered `text.muted`.

**Known soft spot (deferred, not a blocker):** the master sparkline was
decorative; the new one is wired to real P/L history. A single hairline
stroke cannot express shape. Acceptable now (P/L flat at $0.00); flagged
as future work, out of scope here.

---

## 4. Anti-regression gate (G1–G4)

**G1 — The spec is the reference, transcribed.** §2–§3 encode every
primitive as concrete token bindings with master line numbers. Every
implementer brief embeds this spec **and** the master file path
(`git show 757afe7:src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml`).
A subagent that never sees the old surface still has it, in the brief.

**G2 — Token-literal + fidelity-diff review step.** Per PR, the reviewer
checks: (a) every visual property binds a `Theme.*` token, zero hardcoded
literals; (b) opens master `DeskSurface.qml` at the cited lines and confirms
structural match — serif letter present, hairline present, standfirst
present, no box/bar. Checklist, not judgement; a Sonnet reviewer suffices.

**G3 — Render-level structural guard (presence *and* absence).** Existing
polish-loop + non-zero-section-height tests stay. Add two assertion
classes in a `QQuickView`+`show()` test:

- **Presence:** each `SectionHeader` instance exposes an objectName-tagged
  serif-letter `Text` and a `border.subtle` hairline `Rectangle`, asserted
  present via `QQuickItem` lookup. Catches a silently *dropped* lettermark.
- **Absence:** no `SegmentedToggle` instance contains a background/track
  `Rectangle` (color binds `surface.*` or has non-zero `radius`/`border`),
  and no funnel (Section IV) row contains a magnitude-fill `Rectangle`.
  Catches a silently *retained* foreign idiom — the precise mechanism that
  produced the original regression (a passing presence-only guard while the
  boxy pill survived). Without this, G1–G2 + G4 are the only thing standing
  between a retained pill and ship; G3 must guard both directions.

(Asserts structure, not beauty.)

**G4 — Operator visual sign-off is a named blocking gate.** The plan lists,
on the integrating PR, an explicit acceptance step: operator runs
`milodex gui` from the worktree and visually confirms the surface against
the master reference side-by-side. The branch does not finish until this
passes. Headless green is necessary, not sufficient — this is the lesson of
the whole arc.

**Honest limit:** G1–G3 prevent *structural* regression only. Aesthetic
drift (wrong-feeling spacing, an off italic) is caught only by G4. The
gate's purpose is to make machine-checkable parts machine-checked so the
operator's review is spent on taste, not on hunting for a dropped hairline.

---

## 5. Scope boundary & preserve-untouched contract

**In scope:** restyle-in-place of the 7-section `DeskSurface.qml`
composition and the shared components it renders through
(`SectionHeader`, `SegmentedToggle`, `Sparkline`, `FunnelRow`,
`RollupCell`, `TapeRow`, `RunnerSelect`, `ActivityTable`) to P1–P4. New:
P2 standfirst strings, P4 toggle body, IV funnel-as-ladder.

**Preserve untouched (hard constraints — a diff violating any of these is
rejected in review):**

- All 6 read-model singletons + `OperationalState.dailyPnl`: no signature,
  query, or threading change.
- **Defect-B** (the new DeskSurface RowLayout `Layout.preferredHeight`
  height contract) is preserved untouched — P4 does not affect it.
  **Defect-A** (the `SegmentedToggle` one-way-sizing scaffold) is
  *superseded*, not preserved: P4's type-only body has no sizing cycle by
  construction (see §2 P4 "Defect-A supersession"). The polish-loop /
  non-zero-height regression tests remain green either way.
- The three-state empty/stale/fresh gating (Section II) and the Section VI
  path-to-log fix: restyle changes their *typography*, never their *logic*
  or the conditions selecting each state.
- `SegmentedToggle` public API frozen: `property var options`,
  `property string current`, `signal activated(string value)`. Restyle is
  body-only; every call site and every functional test driving
  period/filter switching must pass unchanged.
- ~620 tests + render-level guards + `docs/KNOWN_FLAKY_TESTS.md`
  quarantine: green before and after; no edits except the G3 additions.

**The contract:** if a diff touches a `.py` read-model, a query, the gating
*conditions*, or the toggle signal contract, it is out of scope and review
rejects it. This pass paints a black box; it does not reach inside.

**Explicitly out of scope (named to prevent scope creep):** the
sparkline-shape soft spot (§3, deferred); any new data wiring (VI stays
"awaits read model"); chrome / FRONT / BENCH / LEDGER surfaces (untouched,
per the original scope-A lock).
