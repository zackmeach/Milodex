# Milodex Design Intent

**Status:** Accepted · 2026-05-13 · v0.2
**Companion docs:** [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) (tokens, components, theme architecture) · [FOUNDER_INTENT.md](FOUNDER_INTENT.md) (product north star) · [ADR 0035](adr/0035-design-system-and-theme-architecture.md) (design system + theme architecture)

This document is the **narrative half** of Milodex's design. DESIGN_SYSTEM.md tells you *what tokens exist and how to compose them*; this doc tells you *why the surface looks and reads the way it does*, and what claims that look is making. When a surface PR has to make a judgment call the token reference doesn't decide, this doc is the binding answer.

## What this doc is

- The vibe and voice of the GUI, named precisely enough that two people would agree on whether a new surface is "in voice."
- The narrative the visual surface is conveying — what the operator should *feel* the product is asserting about itself.
- The four-surface structure (FRONT / BENCH / LEDGER / DESK) and why each one exists.
- The principles that fall out of the vibe — operative rules a new surface PR must respect.
- The negative space — the kinds of design moves this product specifically rejects.

## What this doc is *not*

- A token reference. See [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md).
- Pixel-perfect surface specs. Surfaces are shipped iteratively; this doc gives those PRs the editorial brief, not the layout.
- A brand exercise outside the GUI. Scoped to Milodex's Qt Quick surfaces.

---

## 1. The aesthetic, in one phrase

> **An editorial broadsheet, kept by lamp-light, ledger-typeset, addressed to its only reader.**

That phrase has four moves, each load-bearing:

| Move | What it means | Where it shows up |
|---|---|---|
| **Editorial broadsheet** | The voice and typography of a serious financial publication — Newsreader display serif, Public Sans body, italic decks, lettered section labels (`A.` / `B.` / `C.`), em-dashes, hairline rules. | DESIGN_SYSTEM §1.1 ("Editorial press"); every surface file's section labels |
| **Lamp-light** | A warm-dark palette, never cold-blue dark mode. Canvas `#0a0907` is near-black with an amber bias; text is *cream* (`#e4d2a8`), not white. The screen reads as a back-room library after closing, not a Bloomberg terminal. | EditorialDark.qml comments ("editorial-print restraint with more snap") |
| **Ledger-typeset** | JetBrains Mono with `tnum` everywhere a number lives. Columns line up down the page like a bound ledger. Numbers feel *typeset*, not rendered. | Theme.qml `typography.data.*`; the column-reservation foundation pattern |
| **Addressed to its only reader** | Layout and copy presuppose a single, attentive reader. Italic standfirsts, kickers, decks — the conventions of a publication that assumes you'll read every word. There is no "users," only the operator. | FrontSurface.qml ("a newspaper does not greet you"); DeskSurface "the operator's working spread" |

The phrase is meant to be *cite-able*. A surface PR that drifts from it should be visibly drifting against a named target, not against vague taste.

---

## 2. The vibe, translated into non-visual senses

For when "editorial" isn't enough — translations into senses other than sight, useful for describing the product to someone who can't see the screen, or as a check on whether a new surface is in voice:

**Sound register.** A reading room after closing. The small sounds of paper and a fountain pen. Specifically *not* a trading pit, a casino floor, or a notifications app. When something demands attention, it does so the way a footnote demands attention: italic, in the margin, never flashing.

**Texture.** Cream laid paper, cured leather, oxidized metal. The warm-dim glow of a brass desk lamp on a walnut desk. Numbers feel *typeset* — locked-width digits aligning down the page like a ledger book. Italic serif marginalia reads like an editor's red pencil.

**Weight and stance.** Heavy. Confident. Slow. The opposite of an app trying to feel urgent and frictionless. The interface carries itself the way a serious almanac does: *I have been here a while, I will be here a while, you can read me cover to cover.*

**Color, emotionally.** Warm darkness. Brown-amber undertone, not blue-black. The brand accent is **oxblood** (`#7d3540`) — the binding of a law book. The Bronze theme pushes further into workshop territory: bronze, copper-verdigris, text *pressed into the metal*. Status colors are muted herbarium tones — sage, rust, mustard — never traffic-light saturation.

**Pacing.** Editorial. Motion resolves in ~220ms with a curve that lingers on departure and lands cleanly (`ease.editorial = [0.32, 0.72, 0, 1]`). No spring, no playful overshoot. The motion language says: *I respect your attention; I will not perform for you.*

---

## 3. What the visual story is conveying

Beyond the mood, the surface is asserting a set of claims about itself and its reader. None of these are written down in copy; the typography, palette, and pacing carry them. A new surface should reinforce these claims, not contradict them.

**"This is a record, not a feed."**
Broadsheet typography reads as *permanent, considered, edited* — the opposite of a stream you scroll past. Whatever appears on a Milodex surface is the kind of thing you'd refer back to, audit, keep. Nothing is disposable. This is why the LEDGER surface footer says *"every refusal is a permanent test"* — it's the explicit version of an implicit visual claim.

**"Authority through restraint."**
No gloss, no marketing varnish, no persuasion layer. The unspoken claim is *I won't try to convince you — the rigor of the presentation is the credibility.* It's the posture of a central-bank report or a coroner's inquest: serious matter, sober presentation, the work speaks.

**"One literate reader, addressed carefully."**
Editorial conventions — italic decks, kickers, marginalia — presuppose someone who reads every word. The surface is not designed for a crowd of skim-readers; it is a private publication with a circulation of one, and it *flatters that one* by treating them as capable of close reading.

**"Whoever sits here is the editor."**
Editorial design implies an editor — someone with voice, judgment, standards. By adopting that visual language, the surface positions the operator as the principal of the publication. Not a customer being served. Not a player being engaged. The person whose taste shapes what appears in print.

**"Things on this surface have weight."**
Heavy serifs, slow motion, monospaced numerics. The design refuses to feel weightless. *Decisions made here carry consequence — the surface won't pretend otherwise by feeling frictionless.*

**"Everything here can be checked."**
Tabular alignment, named statuses, italic marginalia for "this is commentary not data," strict separation of editorial and quantitative voice. Nothing is hidden in flourish; every value is in a column you can verify against every other column. It's a transparency claim made by layout, not by copy.

**"This is a private practice, not a public service."**
Dark, warm, after-hours, lamp-lit. Not the lobby of an institution — the desk in the back where the institution's principal does the work.

**"Restraint as a moral position."**
The deepest claim, and the one most likely to be eroded under PR-by-PR pressure. The *absences* — no celebratory color, no win-flashes, no nudging copy, no engagement-bait motion — read as a refusal to manipulate. A surface this quiet is asserting that the operator deserves to be left alone with the facts, and that excitement would be a *tell* of weak craft. **When a feature feels like it would benefit from a flourish, the flourish is almost always wrong.**

---

## 4. The four-surface narrative

Milodex's primary nav is `FRONT · BENCH · LEDGER · DESK` (`Main.qml:166-169`). These are publication and workshop metaphors, not app-tab metaphors, and they form a deliberate arc:

| Surface | Role | Voice | Density |
|---|---|---|---|
| **FRONT** | Front page / front porch | Conversational, warm-but-factual prose. *"A newspaper does not greet you."* Reports state, never recommends. | One column, generous margins, prose-led |
| **BENCH** | The strategy bench (governed lifecycle) | Governed pipeline ledger, typeset. Vertical stage-stacks (idle → backtest → paper → micro-live → live). Action-menu transitions, evidence dossier rail, confirmation modal for gated actions, gate enforcement visible. | Multi-column, stage-stacked, rail- and modal-mediated |
| **LEDGER** | Paper of record | Mono everywhere, columns aligned, outcomes in bright color (sage / rust). Reads like a printout. | Chronological, monospaced, sparse |
| **DESK** | The trading desk (dense cockpit) | Newspaper front page: hero band + 3-column body, lettered sections (A through H), italic standfirsts. Maximum density that still reads as edited. | Dense, columnar, typeset-newspaper |

Each surface answers a different question:

- **FRONT** answers *"how is the system, in plain language?"* — the answer a non-expert can read. The single-column composition is intentional: the empty space left and right of the reading column is editorial negative space, not under-design. Anchor the canvas with a thin running head, a build/timestamp colophon set in mono, or a hairline rule defining the reading column. Do not restructure FRONT into a multi-column dashboard or a stuffed broadsheet — that breaks its role as the prose-led approachable surface and steals the metaphor DESK is meant to inhabit.
- **BENCH** answers *"what stage is each technique in, what evidence is visible, and what gated actions are available?"* — governed lifecycle management. Phrasing aligned to [PRODUCT.md §6](PRODUCT.md#6-current-product-surfaces): the harness reports state and surfaces gated actions; the operator decides whether to take any of them. Earlier framings that suggested the strategy "needs" something next inverted the harness relationship and should not return.
- **LEDGER** answers *"what has the system actually done, and why?"* — auditability.
- **DESK** answers *"what's everything I might need on one fold?"* — power-user cockpit. The newspaper-front-page register (hero band, columnar body, lettered sections A through H) is non-negotiable for this surface; equal-weight column smears or sparse landing-page compositions are out of voice for DESK regardless of how well-typeset their interiors are.

The arc is **approachable → operational → auditable → dense**. A new operator lands on FRONT and is oriented in seconds; a sophisticated operator can drive from DESK; a skeptical operator can verify everything in LEDGER. This is the design's structural answer to the founder-intent tension between *approachable for the financially non-literate* and *credible to a serious reader* — the surfaces split that load instead of compromising it on one screen.

A new surface idea should fit one of these four roles, or it should be a strong argument for a fifth metaphor in the same publication-and-workshop family. Don't add a tab named `Settings` or `Analytics` — those names break the metaphor.

---

## 5. Operative principles

These are the rules a surface PR has to respect even when this doc isn't open. They are derived from §3 and are the most common places drift happens.

**Meta-rule governing this section.** The editorial register is the *default* for content surfaces — prose, data, status, evidence, history. For functional controls — navigation, filters, action menus, dialogs, confirmations — conventional UI affordances may be retained when an editorial alternative would degrade usability. The discipline is "as quiet as possible while remaining affordant," not "editorial purity over function." A red navigation pill is a problem because the *color* is loud; a navigation pill *shape* may be acceptable if it is the most legible affordance. A filter chip with a pill shape is acceptable; a pulsing brand-color filter chip is not. The product is a real instrument, not an editorial art project — these principles must protect against both failure modes.

### 5.1 Tokens are the contract; raw values are a smell

Components bind to `Theme.<token>` and never to raw colors or sizes (per ADR 0035 Decision 4 and DESIGN_SYSTEM §9.1). If a surface needs a value that isn't in the token set, the answer is to add the token to the token set, not to inline a hex string.

### 5.2 Honest signal over decorative motion

State changes are honest signal, not entertainment. Don't animate the act of switching surfaces. Do animate hover/active color transitions at `motion.fast` (120ms). The `editorial` easing curve is for one-shot transitions that should feel *deliberate*, not bouncy. **No springs, no overshoot.**

**Status indicators do not pulse, breathe, or animate at idle.** A live-status pip that pulses every two seconds is performing aliveness, not reporting it — the consumer-fintech "the system is alive!" tell. Animate a status indicator *only on state change*: a Risk Office stamp swap, a kill-switch fire, a posture transition. Idle status is still. The presence of the indicator is the message; the animation is theater.

### 5.3 Three voices, never crossed

| Voice | Family | Role | Never used for |
|---|---|---|---|
| **Display serif** (Newsreader) | Newsreader | Surface titles, hero numbers, section letters | Body prose, data cells |
| **Body sans** (Public Sans) | Public Sans | UI chrome, prose, captions, italic standfirsts | Numbers in a table |
| **Data mono** (JetBrains Mono + `tnum`) | JetBrains Mono | Every numeric cell, every identifier, every code-adjacent token | Prose, headlines |

A number in body sans is a bug. Italic prose in mono is a bug. This boundary is what makes data feel *typeset* and prose feel *edited*; smudging it collapses the whole register.

### 5.4 Italic is for editorial commentary, never emphasis

Italic (Newsreader italic, or Public Sans italic for body prose) signals *commentary on the data* — standfirsts, decks, marginalia, "flagged, not retired" notes, captions. **Never use italic for "this is important."** Importance is carried by weight, color, and position, not by slant.

### 5.5 Numbers right-align; prose left-aligns; never the reverse

Tabular data is right-aligned to its column gutter so digits stack. Prose is left-aligned. Centered text is reserved for status pills and a small set of explicit roles. This is what makes the surface feel *ledger-typeset* rather than slide-deck-arranged.

### 5.6 Status colors are nouns, not adjectives

`status.positive` (sage), `status.negative` (rust/oxblood-distinct), `status.warning` (mustard), `status.info` (slate). These are *named conditions*, not "good vibes" / "bad vibes." A green checkmark to celebrate a successful save is wrong; sage on a "GATES PASS" outcome in the ledger is right. The distinction is whether the color is reporting state or applauding it.

### 5.7 Rust ≠ oxblood — keep them separate

`status.negative` (rust, `#df805e` in Editorial Dark) is for *system-declined* events — refusals, kill-switch fires, failures. `color.brand.accent` (oxblood, `#7d3540`) is for *brand commitment* — the period after a headline, the active-tab accent bar, the "PROMOTE" affirming highlight. They are not interchangeable, even though both are reds. Mixing them collapses two different signals.

### 5.8 Empty states are honest, not coy

A missing data feed says *"not wired"* or *"awaits a data-feed read model"*, not *"No data yet — check back soon!"* The surface refuses to fill placeholder space with market-looking numbers. (See `FrontSurface.qml` `marketSummaryText` and `DeskSurface` Today's Tape empty-state copy.) This is a credibility move: a surface that fakes data once is never trusted again.

**This rule also forbids skeleton shimmers and structural placeholders for features that are not yet built.** A skeleton row implies "data is loading" when in fact nothing is wired — the same trust violation as faking numbers, in a different costume. Skeleton placeholders are correct *only* for genuine async loads of real data the surface knows will arrive. For not-yet-implemented capability, italic muted text that names the gap is the right pattern.

### 5.9 Don't greet, don't congratulate, don't recommend

- **No greetings.** A newspaper does not say "Good morning, Zack." It prints today's date.
- **No congratulations.** A win is reported in sage; it is not *celebrated*. No confetti, no "🎉", no "Nice work!"
- **No recommendations.** The surface reports facts; the operator decides. *"Strategy X is ready for promotion review"* is acceptable; *"We suggest you promote X"* is not.

These three are explicit in `FrontSurface.qml` ("Tone: warm but factual. Reports state in plain language. Does NOT recommend") and should be enforced everywhere.

### 5.10 Column reservation is a foundation contract

Tabular rows reserve column width even when a cell is empty, so BLOCKED rows (or any short row) align with the rows above and below them. This is the column-reservation foundation pattern (PR D.5 / ADR-equivalent). It is what makes the bench and ledger feel *bound*, not raggedly assembled.

### 5.11 Modals for safety-critical confirmation; rails for reference

Modals interrupt. They force focus and trivialize cancellation (Escape, click-outside, explicit X). That makes them *correct* for safety-critical confirmation surfaces — the Bench Action-Menu confirmation, kill-switch reset, any operator-approval gate where **forced focus is the feature, not the bug.** PRODUCT.md §5's "preview before action" promise depends on this interruption being unmissable.

Modals are *wrong* for reference and browse content — evidence dossiers, history detail, configuration views. Reference content belongs in **right-rail dossiers** or row-attached drawers so the operator can read it next to the row it describes, with the surrounding context still visible. A modal over reference content collapses the editorial register into a SaaS dialog the moment it opens.

Modal interiors follow the editorial register: small-caps labels with mono or serif values per §5.3, hairline rules between sections, no nested card frames inside the modal frame, and sober typographic treatment for safety banners — small-caps red set in a hairline-ruled band, never a yellow alert bar and never a decorative rubber-stamp graphic. The modal frame is the only enclosure; everything inside is composition.

---

## 6. The negative space — what this design rejects

Naming what's *out of voice* is as load-bearing as naming what's in voice. Each of these is a default-modern-fintech move that Milodex specifically rejects:

| Rejected move | Why it's wrong here |
|---|---|
| Gradient backgrounds, glassmorphism, frosted blur | Reads as "consumer app trying to seem premium." The aesthetic is print, not glass. |
| Gain-green / loss-red traffic-light saturation | Casino register. Sage and rust carry the same signal at half the volume. |
| Animated counters (numbers ticking up) | Performs the win. The number being there is the message. |
| Toast notifications, snackbars, "Saved! ✓" | Greets the operator after every action. The action's effect is the confirmation. |
| Emoji in UI copy or surface labels | Wrong register. The closest analogue is the oxblood period after a headline — typographic ornament, not pictographic. |
| Onboarding tours, coach marks, tooltips that teach | Treats the operator as a student. The surface should be legible without instruction. Inline italic decks carry context. |
| Progress sparkles, success animations, loading shimmer | Performance. A loading state is *italic muted text*: "loading." or "pending." |
| "Get started" / "Continue" / "Next" CTA chrome | Wrong vocabulary. The surface uses verbs: "Open in bench →", "Strategy detail →". |
| Rounded-pill primary buttons with shadow | The button language is `radius.md` (4px) outlined or filled, no shadow. (See `Button.qml`.) |
| Iconography as primary signal | Color and language carry the signal. Icons are sparingly used and never load-bearing. (DeskSurface header: "No iconography — color + language carry the signal.") |
| Light-on-color callout banners ("ℹ Tip:") | Marginalia is *italic in the column*, not in a colored box. |
| Third-party charting libraries with default styling | A chart that doesn't look hand-set is wrong. The Sparkline component is intentionally minimal — line + optional fill, no axes by default, no tooltip. |
| Bordered card frames for content blocks (rounded rectangles enclosing data, status, or evidence) | The frame is doing SaaS-dashboard work. Editorial sections are bounded by hairline rules and small-caps section heads, not by container chrome. A surface composed of stacked cards is a dashboard wearing a serif font, not an editorial broadsheet. (Common failure: FRONT's "AT THE GATE" rendered as a card with two CTAs at the bottom.) |
| Proportional numerics in any tabular column | A column of numbers in proportional sans cannot be scanned vertically — digit widths shift line to line. Mono with `tnum` is non-negotiable for any column of numbers (see §5.3). This is the single most common density violation. |
| Skeleton shimmers or structural placeholders for features that are not built | Implies an async load is in progress when in fact nothing is wired. Same trust violation as faking data. Italic muted text naming the gap is the right pattern (see §5.8). |
| Animated status indicators at idle (breathing dots, pulsing pips, slow opacity loops) | Performs aliveness. The indicator's presence is the message; the animation is theater. Animate only on state change (see §5.2). |

The unifying principle: **anything whose purpose is to make the operator feel something about an outcome — rather than to inform them of it — is out of voice.**

---

## 7. Themes as voices, not skins

The three themes (Editorial Dark, Editorial Light, Bronze) are three *voices of the same publication*, not three skins of the same app:

- **Editorial Dark** — the default, the night edition. Cream on warm-near-black. The most common reading surface.
- **Editorial Light** — the day edition. Same publication, paper-side-up. Inverted contrast, identical structure.
- **Bronze** — a separate aesthetic story (workshop / craft-tool / patinated metal). Not a re-skin; a parallel voice that demonstrates the theme machinery and gives the operator a second register to read in.

Same components, same tokens, different palettes. A surface designed in Editorial Dark must work in all three without per-theme code paths. (DESIGN_SYSTEM §1.2.) If a surface looks correct in only one theme, the surface — not the other themes — is wrong.

---

## 8. Connecting design intent to founder intent

[FOUNDER_INTENT.md](FOUNDER_INTENT.md) names a tension: the product must be **approachable to the financially non-literate** *and* **credible to a serious reader**. The visual story answers that tension this way:

- **Approachable** is carried by FRONT — single column, prose, plain language, large primary numbers, hairline rules. No jargon-without-context. The first 30 seconds on FRONT should leave a non-expert oriented.
- **Credible** is carried by LEDGER and DESK — ledger-typeset record-keeping, lettered sections, mono numerics, named refusals. The first 30 seconds on LEDGER should leave a skeptical reader convinced that the system tells the truth about itself.
- **Polished without intimidating** is carried by the editorial restraint — the absences listed in §6. The product looks serious because it *is* serious, not because it's been visually loaded with seriousness signals.

The reaction the founder wants — *"this almost seems too easy for what we're doing"* — is a function of: (1) FRONT being legible without instruction, (2) the surface refusing to feel busy, and (3) editorial typography quietly signaling craft so the operator trusts the simplicity is *earned*, not hidden.

---

## 9. How to use this doc

- **Designing a new surface.** Read §1 and §4 to place it in the publication metaphor. Read §5 for the operative rules. Read §6 to know what you'll be tempted to do that's wrong.
- **Reviewing a surface PR.** Spot-check §5.3 (three voices), §5.6–5.7 (status colors), §5.9 (no greetings/congrats/recs), and §6 (negative space). Most drift happens here.
- **Deciding a token addition.** Read §1's four moves. If the token reinforces one, it's likely valid. If it would only make sense in a "modern fintech" register, it's likely §6.
- **Disagreeing with this doc.** The doc is wrong sometimes. The path is to update DESIGN.md and DESIGN_SYSTEM.md together, not to deviate silently in a surface PR.

---

## Changelog

- **v0.2 — 2026-05-13** — second-pass audit. No structural rewrite; v0.1 doctrine that survived the second pass is retained verbatim. Folds in: editorial-register-as-default meta-rule (§5 preamble) protecting against both the SaaS-dashboard and the editorial-art-project failure modes; modal/rail philosophy (§5.11) — modals for safety-critical confirmation only, rails for reference content; explicit no-idle-pulse rule for status indicators (§5.2 extension); explicit no-skeleton-shimmer rule for unbuilt features (§5.8 extension); Bench question rewording aligned to PRODUCT.md §6 — "what stage, what evidence, what gated actions," replacing the operator-paternal "what does it need next" (§4); FRONT canvas-anchoring guidance — anchor the reading column with marginalia rather than restructuring to multi-column (§4); DESK lettered-section requirement promoted from "voice" to non-negotiable (§4); four new entries in negative space (§6): bordered card frames for content, proportional numerics in tabular columns, skeleton placeholders for unbuilt features, idle status animations. Token additions surfaced by the second-pass critique are deferred to a companion DESIGN_SYSTEM.md v0.2 update.
- **v0.1 — 2026-05-09** — initial document. Captures the editorial-broadsheet vibe, the four-surface narrative (FRONT/BENCH/LEDGER/DESK), the operative principles, and the negative space. Companion to DESIGN_SYSTEM.md v0.1.
