# ADR 0035 &mdash; Design system and theme architecture

**Status:** Accepted &middot; 2026-05-07
**Related:** [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md) (the canonical spec this ADR authorizes), [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md) (GUI runtime: PySide6 + Qt Quick), [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md) (Phase 5 scope: observability-first), [ADR 0018](0018-durable-state-directory-consolidation.md) (durable state under `data/`), [ADR 0005](0005-kill-switch-manual-reset.md) (kill-switch manual-reset semantic propagates to GUI), [PHASE5_PLANNING.md](../PHASE5_PLANNING.md) &sect;9 (Phase 5 PR sequence), [VISION.md](../VISION.md), [FOUNDER_INTENT.md](../FOUNDER_INTENT.md)

## Context

[ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md) locked the GUI runtime as PySide6 + Qt Quick (full QML) and called out a Phase 5 design system as in-scope, not optional: *"a token set covering color, typography, spacing, motion, elevation is defined and applied consistently across all QML surfaces. The token set lives alongside the QML source as a versioned artifact, not buried in component-level files."* [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md) ordered Phase 5 PRs as observability-first, with the design-system token set as the first GUI-bearing PR.

This ADR is the architectural artifact for that first PR. It records the foundational design decisions (direction, themes, typography, status-color policy) and the architectural decisions (theme singleton pattern, token-binding contract, hot-swap mechanism, persistence model, font loading). The detailed token catalog lives in [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md); this ADR records the decisions that shaped it.

The design direction was selected through a brainstorming session on 2026-05-07. Eight accent directions were considered against the Tier B polish target named in [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md); three type pairings were evaluated; the operator selected the Editorial Press direction with Newsreader + Public Sans + JetBrains Mono and a three-theme set (Editorial Dark default, Editorial Light daytime, Bronze alternate). The brainstorming artifacts are in `.superpowers/brainstorm/` (gitignored, working-tree-only).

## Decisions

### 1. Design direction: Editorial Press

The GUI takes its aesthetic cue from financial-publishing tradition (FT Weekend, WSJ print, the Economist) rather than from modern fintech defaults (Linear, Stripe, Vercel). Long-form serif display + workhorse sans body + tabular-mono data, generous baseline rhythm, considered restraint.

### 2. Three themes, one structural skeleton

The GUI supports three themes:
- **Editorial Dark** &mdash; default. Parchment cream + oxblood on warm-tinted near-black.
- **Editorial Light** &mdash; daytime variant. Same palette inverted to cream-paper surfaces.
- **Bronze** &mdash; alternate-direction. Patinated-bronze + verdigris workshop aesthetic.

Themes vary color values and (in Bronze) one type-role treatment. They do **not** vary structural design: layout, spacing scale, motion durations, type roles, component shapes, and elevation tiers are constant across themes. This is what "don't diverge the UI design" means architecturally &mdash; the structural skeleton is theme-blind, themes only redefine surface treatment.

### 3. Type pairing

| Role | Family | Loading |
|---|---|---|
| Display serif | Newsreader | Bundled in `assets/fonts/`, loaded via `QFontDatabase.addApplicationFont()` |
| Body sans | Public Sans | Same |
| Data mono | JetBrains Mono | Same |

All three are SIL Open Font License or similarly permissive. Bundled means font rendering is identical across machines &mdash; the GUI does not depend on operator-installed fonts.

### 4. Theme singleton pattern with property-binding hot-swap

A single `Theme.qml` QML singleton exposes every design token as a property. Components reference `Theme.color.brand.accent`, `Theme.type.body.md`, etc. The singleton's properties bind to the *active* theme; theme switching mutates which theme file populates the singleton; QML's property-binding system propagates the change to every component automatically.

This is the architectural mechanism that makes hot-swap free at runtime. It is also what enforces the token-binding contract: a component that hardcodes a hex value or pixel literal silently fails the contract because that value will not theme-swap.

### 5. Token-binding contract

**Every visual property of every component must be bound to a Theme token.** Hardcoded hex values, literal pixel values, and literal duration numbers in component QML are forbidden. A component that needs a value not in the token set requires a token-set extension PR before the component PR lands.

The contract is enforced socially (PR review checks token references) and structurally (a theme swap that doesn't fully cascade is a bug). The structural enforcement is the primary one; PR review is the early-warning system.

### 6. Status-color theming policy

Status colors (positive / warning / negative / info) are theme-tinted but role-stable. Each theme provides hues compatible with its palette; component code references roles, never raw hex. Editorial Dark uses muted sage / mustard / rust / ink instead of the high-saturation red/green/amber/blue typical of trading dashboards &mdash; the muted set carries the same semantic information at the same contrast levels but stays inside the editorial palette.

This is a deliberate departure from fintech convention. Recorded here because future contributors will likely encounter "why isn't gain green?" and need the load-bearing answer: because raw saturated greens clash with parchment-and-oxblood, and the muted-sage / mustard / rust set preserves semantic clarity without breaking the palette.

### 7. Persistence under `data/`

Active theme persists in `data/gui_settings.json` per [ADR 0018](0018-durable-state-directory-consolidation.md)'s durable-state convention. Operator-scoped (per machine), not strategy-scoped. Theme choice is presentation, not audit trail.

### 8. Motion discipline: state changes are honest

Status-color transitions on operationally significant state changes (paper -> kill-switch-fired, gate-passing -> gate-blocked) are *not* animated. The instantaneous flip is honest signal; a crossfade obscures it. The kill-switch banner does not pulse, glow, or breathe &mdash; static high-contrast treatment instead. The kill-switch manual-reset semantic from [ADR 0005](0005-kill-switch-manual-reset.md) propagates: the GUI exposes a reset affordance that requires explicit confirmation. No auto-reset, no "click to clear" without confirmation.

P&amp;L numbers do not animate from one value to another. Their *position* may animate (e.g., during a row reorder), but their *value* never crossfades &mdash; that risks misreading mid-transition.

## Rationale

**Editorial direction matches what Milodex actually is.** The platform's existing artifacts &mdash; walk-forward reports, gate-refusal narratives, strategy bank documentation, ADRs &mdash; already read like serious technical writing. An editorial GUI surfaces what the project *is* rather than what fintech expects. It also inherits hundreds of years of legibility tradition for presenting complex financial information &mdash; a stronger bet than inventing a fresh aesthetic. Per [FOUNDER_INTENT.md](../FOUNDER_INTENT.md) priorities #1 (trustworthy), #2 (engineering capability rendered as visual identity), #3 (accessibility &mdash; publishing-grade legibility), and #4 (shareability &mdash; memorable identity).

**Three themes is enough; more would bloat without payoff.** Editorial Dark + Editorial Light covers operator-time-of-day preference; Bronze demonstrates the theme machinery without being a structural divergence. Each additional theme is real maintenance surface (contrast checks, status-color clash checks, component verification across themes). Three is the upper bound that earns its place at Phase 5 open.

**Theme singleton pattern is the standard QML approach for this problem.** Property bindings in QML are how the language expresses reactive UI; a singleton-as-theme-source uses that language feature for what it was designed for. Alternatives considered:
- *Per-component theme prop drilling* &mdash; rejected as architectural noise; every component would carry the same theme reference.
- *CSS-style theme classes on a root element* &mdash; QML doesn't have CSS; emulating it would invent a parallel system.
- *Build-time theme generation (one binary per theme)* &mdash; rejected because it precludes runtime hot-swap, which is part of the spec.

**Bundled fonts, not system fonts.** Operator-installed fonts vary by machine; some Windows installs lack Newsreader entirely. Bundling guarantees identical rendering and removes a class of "looks broken on my machine" bug reports before they happen. License terms permit redistribution for all three families.

**Status-color theming is a real departure that needs ADR-level recording.** Future contributors (or the operator returning after months) will look at a sage-tinted positive P&amp;L and wonder if it's a bug. This ADR is the answer: it's a design decision, here's why, here's how it preserves semantic clarity without breaking the palette.

**State-change motion discipline preserves operational honesty.** Animation is normally a polish accelerant; in operational UI it can become a polish-vs-honesty trade. Kill-switch state changes happen because something operationally important changed; the GUI must surface that change instantly, not gracefully. This decision goes into the ADR (rather than just the spec) because it's the kind of thing later "let's make it smoother" PRs would erode without seeing the upstream principle.

## Consequences

- **The design-system PR is the first Phase 5 GUI-bearing PR per [PHASE5_PLANNING.md](../PHASE5_PLANNING.md) &sect;9.** It ships:
  - `docs/DESIGN_SYSTEM.md` (the canonical spec) &mdash; lands in this ADR's accompanying PR.
  - QML source for the Theme singleton, ThemeManager, the three theme files, and the four foundational components (buttons, status pills, strategy rows, surface containers) &mdash; lands in subsequent PRs.
  - Bundled fonts under `assets/fonts/` with licenses.
  - Font-loading bootstrap in `milodex/gui/fonts.py` (or equivalent module).
  - Initial application shell that renders the Theme tokens visibly (a "design system showcase" surface that lets the operator preview themes &mdash; doubles as the integration test).

- **Subsequent observability-surface PRs cite this ADR for token-binding discipline.** Strategy-bank rendering, per-strategy attribution, paper-session status, kill-switch state &mdash; each renders against tokens defined here.

- **A failure mode to watch for: drift between [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md) and the QML source.** The doc is the binding spec; the QML implements it. PR review explicitly checks both directions: code that uses an undefined token, doc that names a token the code doesn't have.

- **Future themes and components require corresponding doc updates.** Per [DESIGN_SYSTEM.md &sect;11](../DESIGN_SYSTEM.md). A theme PR that doesn't update the doc is incomplete.

- **The "operator" pseudo-strategy id from [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) renders in the GUI's strategy bank just like any other strategy.** No special UI treatment; the design-system components are sufficient. (Noted because a contributor might be tempted to "make the operator row stand out" &mdash; this ADR's component principles say no.)

- **Live trading remains structurally locked.** This ADR is GUI-presentation-only; [ADR 0004](0004-paper-only-phase-one.md) is unaffected. The kill-switch manual-reset semantic from [ADR 0005](0005-kill-switch-manual-reset.md) propagates to the GUI per &sect;8 above.

- **A `Theme` singleton implies a single global concept of "active theme."** Multi-window scenarios (if Phase 6+ adds them) inherit the same active theme; per-window themes are not supported and would require a new ADR.

## Considered and rejected

**Per-component hardcoded values + a "theme override" injection at render.** Rejected: defeats the point of property bindings, and the override mechanism would itself become a sub-system to maintain.

**A more elaborate theme-builder that lets operators define their own themes at runtime.** Rejected as Phase 5 scope creep. Three curated themes deliver the FOUNDER_INTENT #4 (shareability) value; runtime theme authoring is a Phase 6+ candidate at most.

**Web-style CSS-token files (`tokens.css`, `theme.scss`) processed by a build step.** Rejected: QML's property-binding model is the better tool here. Adding a CSS-style preprocessor pulls in build complexity for no architectural payoff.

**Skipping Editorial Light entirely and shipping only two themes (Editorial Dark + Bronze).** Considered. Rejected because daytime light mode is the most-asked-for theme variant in any desktop app, has clear FOUNDER_INTENT priority #3 (accessibility) value, and is essentially free given the editorial direction (publications work natively in light). Two themes feels under-sized for a system designed to demonstrate hot-swap.

**Using saturated red / green / amber for status colors and accepting the palette clash.** Considered. Rejected because the editorial coherence is the load-bearing aesthetic claim, and high-saturation status colors against parchment-and-oxblood read as "operational dashboard bolted onto a print magazine." The muted-sage / mustard / rust set preserves semantic clarity within the palette. This decision is recorded explicitly because it's the most likely future contention point.

**Animating P&amp;L number transitions for "polish."** Considered. Rejected per &sect;8: number values must never crossfade; a number visibly mid-interpolation is a number the operator can misread.

**Pulsing kill-switch banner.** Rejected as antipattern. A pulse trades urgency for novelty and loses impact within a single session of exposure.

## Non-goals

- Does not specify pixel-perfect designs for individual surfaces (Strategy Bank dashboard, Per-strategy attribution panel, etc.). Surface design lands as the surfaces ship; this ADR + [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md) provide the tokens to compose against.
- Does not authorize Qt Widgets in the production UI tree (that is [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md)'s domain).
- Does not pre-decide the &sect;4.7 distribution model. The design-system PRs ship before the installer; PyInstaller is the default candidate per [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md).
- Does not authorize live trading, micro_live promotion, kill-switch auto-reset, or any [PHASE5_PLANNING.md](../PHASE5_PLANNING.md) &sect;7 floor item.
- Does not commit to specific PR boundaries within the design-system implementation. Within the "design system + foundational components" scope, the operator may sequence PRs as serves the work.
- Does not retire or weaken any prior trust property: [ADR 0023](0023-phase-1-is-closed-and-phase-2-may-open.md)'s C-2 honest-signal regression, [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)'s account-scoped floor, [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md)'s per-strategy attribution, [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md)'s supervisor model, [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md)'s backtest sandbox semantics. Per [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md), Phase 5 is observability-first; this ADR concerns presentation, not behavior.
- Does not authorize per-window themes or runtime user-authored themes. Three curated themes are the spec; deviations require their own ADR.
- Does not commit to specific charting library, dialog patterns, or input-control library beyond the foundational component set. Those land as specific surfaces need them.
