# ADR 0033 — GUI runtime is PySide6 + Qt Quick (full QML)

**Status:** Accepted · 2026-05-07
**Related:** [PHASE5_PLANNING.md](../PHASE5_PLANNING.md) §4.6, [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md) (Phase 5 scope ordering), [ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md) (mechanics-before-UI principle), [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) (per-strategy attribution that the GUI renders), [ADR 0005](0005-kill-switch-manual-reset.md) (kill-switch manual-reset semantic the GUI must preserve), [ADR 0004](0004-paper-only-phase-one.md) (paper-only — unaffected), [VISION.md](../VISION.md) ("Daily Operator Workflow"), [FOUNDER_INTENT.md](../FOUNDER_INTENT.md)

## Context

[ADR 0031](0031-phase-4-is-closed-and-phase-5-may-open.md) closed Phase 4 with the §4.1 deferred menu — (a) micro_live, (b) GUI, (c) installer, (d) third research-target, (e) re-tune — carrying forward as Phase 5+ candidates. Phase 5 opens with §4.1 = (b) + (c) — Desktop GUI + installer, observability-first. [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md) records the scope and ordering decision; this ADR resolves the prerequisite **GUI runtime** question that gates all downstream Phase 5 work.

Five candidates were considered: PySide6 (Qt Widgets, Qt Quick, or hybrid), Tauri (Rust shell + JS/TS frontend), Electron, Flet, and a local-FastAPI + browser-frontend pattern. Within PySide6, three sub-strategies were considered: Widgets-only, Qt Widgets + QML islands hybrid, and full Qt Quick (QML).

The decision space is shaped by four binding constraints already in the codebase:

1. **FOUNDER_INTENT priorities #3 (accessibility) and #4 (shareability)** name the polish target. The product must look "polished, smooth, surprisingly easy to start, visually clear, impressive without feeling cluttered or confusing." A peer, employer, or curious friend exploring Milodex should come away thinking the founder can "build and ship substantial systems." Tier A polish — "operator dashboard that looks serious and considered" — is below the bar this priority pulls toward. The target is Tier B: "designed product."
2. **[ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md)'s mechanics-before-UI principle** holds: the UI must not introduce ambiguity between display logic and data layer. Phase 4 firmed the mechanics so that any UI that lands in Phase 5 sits on attribution data ([ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md)), event-store integrity ([ADR 0011](0011-sqlite-event-store.md)), and a sandboxed backtest path ([ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md)) that are individually testable. Whatever runtime ships must preserve that property — not import a second display-vs-data ambiguity surface in trade for polish.
3. **Solo developer, Python-strong, Windows-first.** The maintenance surface that gets added by the GUI runtime will be carried by one person across an indefinite tail. Every new language ecosystem in the toolchain (Rust, JS/TS, Node, Flutter) is a future point of friction when the operator returns to the codebase after a quiet period. Friction compounds where the dev hasn't touched a stack for months.
4. **Phase 5 surfaces are observability-led** per [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md). The work renders existing strategy bank data, attribution, and paper-session state — six paper-stage strategies' P&L, kill-switch state, the daily-operator workflow's eight steps. These surfaces are dashboards, status panels, and animated state transitions. They are not "1990s data-entry forms with 50 fields per screen."

## Decision

The Phase 5 GUI runtime is **PySide6 + Qt Quick (full QML)**. The UI tree is QML throughout. Python is the data and logic backend, exposed to QML via `QObject` subclasses with `@Property`, `@Slot`, and `Signal`, registered via `@QmlElement` (or `setContextProperty` where appropriate). **Qt Widgets is not used in the production UI tree.**

A narrow exception is permitted only if a third-party Qt Widgets-based dialog has no Qt Quick equivalent and rewriting it is materially more expensive than accepting a one-screen seam. Such an exception requires explicit ADR-level justification at the time it is taken; it is not pre-authorized here.

## Rationale

**1. Polish consistency at Tier B.** FOUNDER_INTENT priorities #3 and #4 demand a "designed product" feel — not a Python script with a tkinter shell on top, and not a Qt Widgets app whose default chrome reads as "competent desktop UI from 2014." Qt Widgets is CPU-painted via `QPainter` with QSS theming whose practical ceiling is gradients, padding, fonts, and subtle hover transitions. Qt Quick is GPU-composited via a scene graph (OpenGL/Vulkan/Metal/DirectX), with first-class animation, custom shapes, shaders, and motion. A hybrid Widgets + QML window reads at the lower tier across the whole window — a Tier B QML dashboard next to a Tier A Widget table pulls perceived polish to A. Picking one tier and applying it consistently is visually stronger than mixing.

**2. Single-language toolchain in the Python sense.** Milodex's existing codebase is Python end-to-end. PySide6 is the Python binding to Qt; QML is a declarative markup layer authored in `.qml` files, not a separate runtime or distinct language ecosystem. Adding Qt Quick adds one declarative-syntax layer to the project, accessed and driven from Python. By contrast: Tauri adds Rust + JS/TS + Node tooling — three new ecosystems where one suffices. Electron adds JS/TS + Node + a bundled Chromium runtime. Flet wraps Flutter and inherits Flutter's toolchain. local-FastAPI + browser splits the runtime across two processes the operator must start, with a localhost URL the user must type. The QML ramp is bounded; the Tauri/Electron/Flet ramps add ecosystems that compound future maintenance friction for a solo Python-strong developer.

**3. Sweet-spot match.** Phase 5's surfaces are observability dashboards: strategy bank rendering, per-strategy attribution and P&L, paper-session status, kill-switch state, walk-forward labeling. These are exactly what Qt Quick was designed for — animation-rich, data-bound, GPU-composited, custom-shape-friendly. The historical "Widgets is more battle-tested for high-volume tables" concern does not apply at Milodex's data shapes: 12 strategies in the bank, event-store records measured in thousands, no high-frequency tick rendering. There is no Phase 5 surface where Qt Widgets' specific maturity is load-bearing.

**4. Mechanics-before-UI consistency ([ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md)).** A hybrid Widgets + QML app imports two styling systems (QSS for Widgets, QML theming for Quick), two animation systems, and two ways to wire signals into the UI tree. That imports the exact display-vs-data ambiguity surface ADR 0028's principle was designed to keep out of Phase 5. A single-stack QML UI keeps that surface area small and uniform. When a future anomaly surfaces, the question "is this a display bug or a data bug" has one place to look.

**5. Distribution simplicity preserved.** PySide6 + Qt Quick bundles cleanly via PyInstaller (single-file or single-folder modes). The Qt 6 runtime ships as Qt's QML and Quick libraries, no separate WebView dependency. By contrast: Tauri on Windows requires the WebView2 runtime, which is either pre-installed (modern Windows) or must be downloaded on first run — a real friction tax on the friend-install story FOUNDER_INTENT's shareability priority calls for. Electron ships Chromium per app (~200MB). The §4.7 distribution model decision (still open, separate ADR) inherits PySide6 + PyInstaller as the default candidate.

**6. Future portability is non-zero.** Qt Quick runs on mobile (iOS/Android), embedded (automotive, medical, industrial), and desktop. Qt Widgets effectively does not — its model is desktop-only by design. Phase 5 does not scope mobile or embedded targets, but a Qt Quick UI tree is portable in a way a Widgets tree is not. This is preserved as a cheaper future option than hybrid would be.

## Costs accepted

The decision is honest about what it costs. The ADR records these so the decision can be reviewed against actual experience later.

**QML is a real second-language layer.** It is JavaScript-flavored declarative syntax with concepts that are new to a Python-only stack: property bindings, signal handlers, scene-graph item lifecycle, `Component`/`Loader`/`Repeater`, attached properties, anchor-based layout. It is not as foreign as Rust or as broad as React's ecosystem, but it is real surface area to learn. Practical ramp for a strong Python developer: a weekend to be productive, multiple weeks to be fluent. Phase 5 implicitly authorizes this learning investment as part of the cost of a Tier B desktop UI.

**QML tooling maturity is below Python's.** A QML language server (`qmlls`) exists; Qt Creator provides QML debugging, profiling, and a property inspector; but errors at the Python ↔ QML bridge surface as opaque binding-loop messages or silent no-ops rather than clean Python tracebacks. A typo in a `Q_PROPERTY` name or a mismatched type at the bridge often produces "binding loop detected" or no visible effect at all. Diagnostic discipline at the bridge — defensive logging, runtime type checks where they pay for themselves, documentation of the bridge contract — is a Phase 5 implementation concern.

**Custom design work cannot be hidden.** Qt Widgets has a "default OS chrome looks fine" escape hatch — a rough screen reads as "standard desktop application." Qt Quick has no equivalent default; every pixel is a design decision. That is the *point* of choosing Qt Quick (the Tier B polish target requires this), but it means a design system is load-bearing Phase 5 scope. A token set covering color (palette, semantic roles), typography (font family, scale, weight, line height), spacing (scale and base unit), motion (duration tiers, easing curves), and elevation (shadow tiers, surface levels) is defined up front and applied consistently. The token set lives alongside the QML source as a versioned artifact, not buried in component-level files.

**Smaller community velocity than web frontends.** Stack Overflow surface and AI-tooling familiarity with QML are real but smaller than React + Tailwind + Next.js. When the operator searches for "how do I do X in QML," answers exist and are findable, but the community is closer in scale to the Python-desktop-GUI community than to the modern web-frontend community. This is acceptable given the toolchain consolidation gain, but it is a real cost.

## Considered and rejected

**Tauri (Rust shell + JS/TS frontend).** Higher polish ceiling per pixel of design effort once the toolchain is stood up. Rejected because:
- Adds Rust, JS/TS, and Node ecosystems to a Python-strong solo dev's maintenance surface — three new language ecosystems where one suffices.
- Distribution on Windows requires the WebView2 runtime dependency.
- Multi-process IPC for Python-backend integration adds complexity in a pattern (front-end-talks-to-Python-via-localhost or via a Rust shim) that is not load-bearing for Milodex's actual surfaces.
- FOUNDER_INTENT priority #1 (trustworthy) is best served by a stack the operator can confidently revisit after a quiet month. Three-language surface area cuts against that.
- FOUNDER_INTENT priority #2 (engineering capability) is demonstrated by depth in the chosen stack, not breadth across three.

**Electron.** Same JS/TS + Node maintenance surface as Tauri, plus a ~200MB Chromium runtime per install. Distribution footprint fails FOUNDER_INTENT's "polished, surprisingly easy to start" intent. Rejected.

**Flet (Flutter wrapper).** Polished defaults from Flutter's design system. Rejected because the toolchain investment (Flutter SDK, Dart compilation pipeline, Flutter's own platform plumbing) does not compound on Milodex's existing Python codebase the way QML does. Qt Quick's bridge layer is a thin PySide6 wrapper around existing Python logic; Flet's bridge is a wire-protocol RPC over a separate Flutter runtime.

**PyQt6 / PySide6 — Widgets-only.** Tier A+ polish ceiling. Rejected because the polish target the operator confirmed is Tier B; QSS-themed Widgets cannot reach the "designed product" feel without QML alongside.

**PySide6 — hybrid (Widgets + QML islands).** A reasonable-sounding compromise that reads worse in practice. Rejected because:
- The window's perceived polish reads at the lower tier across the whole frame; a Tier B QML dashboard next to a Tier A Widget panel does not look like "Tier B with Tier A accents," it looks like a Tier B intent compromised by a Tier A reality.
- Two styling systems (QSS for Widgets, QML theming for Quick), two animation systems, two signal-wiring patterns. Imports the display-vs-data ambiguity ADR 0028 sought to keep out.
- The structural-Widgets argument ("Widgets are battle-tested for big tables, complex dialogs") does not apply at Milodex's data shapes.

**local-FastAPI + browser-frontend.** A localhost FastAPI backend with a React or Svelte frontend served from the same process, opened in the user's default browser. Rejected because the distribution UX is "run this, then open localhost:8765 in your browser" — a real friction tax that fails FOUNDER_INTENT's "surprisingly easy to start" intent. Friend-install demos hit this every time. Bundling a browser to make the experience seamless converges back to Tauri or Electron with their own costs.

## Consequences

- **Phase 5 implementation introduces QML as a first-class skill.** The first GUI-bearing PR establishes the QML directory layout, the theme/token module location, the Python ↔ QML bridge pattern (`@QmlElement`-registered classes vs. `setContextProperty` for singletons), and the import-path discipline. These conventions are documented as the implementation lands, not pre-specified here.
- **A Phase 5 design system is in scope, not optional.** The token set (color, typography, spacing, motion, elevation) is defined and applied consistently across all QML surfaces. The token set is a versioned artifact in the repo. "We'll style it later" is not an acceptable Phase 5 posture.
- **PyInstaller is the default distribution candidate** for §4.7's distribution-model ADR. PySide6 + Qt Quick bundles cleanly via PyInstaller; the §4.7 ADR validates this in practice and either confirms or supersedes.
- **Project dependency surface grows by PySide6 + the Qt 6 runtime libraries.** Multi-tens-of-MB but reasonable for a desktop app. No Rust toolchain, no Node, no separate WebView runtime, no Chromium bundle.
- **No GUI code is written in this ADR.** This ADR resolves the runtime question only. The first GUI-bearing PR follows after [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md) and PHASE5_PLANNING.md merge.
- **The kill-switch manual-reset semantic ([ADR 0005](0005-kill-switch-manual-reset.md)) propagates to the GUI.** The GUI surface displays kill-switch state and exposes the reset affordance, but the reset action requires an explicit confirmation step. No auto-reset UI, no "click to clear" without confirmation. The contract is unchanged from the CLI.
- **Subsequent Phase 5 PRs cite this ADR for the runtime choice.** Future doc references to the GUI runtime (PHASE5_PLANNING.md §4.6, distribution-model ADR, any GUI implementation ADR) link here.
- **Live trading remains structurally locked.** This ADR concerns UI runtime only; [ADR 0004](0004-paper-only-phase-one.md) is unaffected. The GUI does not unlock or alter the live-trading boundary.

## Non-goals

- Does not commit to a specific QML directory structure, theme token names, component hierarchy, or naming conventions. Those are implementation decisions for Phase 5 GUI PRs.
- Does not commit to a specific charting library. Qt Quick has built-in `Shape` and `Canvas` primitives; pyqtgraph and Qt Charts are options. Charting library is a separate implementation choice deferred to its first need.
- Does not pre-decide §4.7 distribution model. PyInstaller is the default candidate per the bundling note above; a separate ADR resolves the model when that PR opens.
- Does not authorize Qt Widgets in the production UI tree by default. The narrow exception (third-party Widgets-based dialog with no Qt Quick alternative) requires its own ADR-level justification when taken.
- Does not authorize live trading, micro_live promotion, or any §4.2 boundary movement. Live remains locked per [ADR 0004](0004-paper-only-phase-one.md) and [ADR 0031](0031-phase-4-is-closed-and-phase-5-may-open.md).
- Does not authorize auto-resume after kill switch, unattended overnight running, or any [PHASE5_PLANNING.md](../PHASE5_PLANNING.md) §7 floor item.
- Does not pre-commit specific PR sequencing within Phase 5; ordering follows [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md).
