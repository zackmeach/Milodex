# ADR 0038 — Phase 5 is closed and Phase 6 may open

**Status:** Accepted · 2026-05-10
**Related:** [PHASE5_PLANNING.md](../PHASE5_PLANNING.md), [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md) (Phase 5 scope and ordering), [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md) (GUI runtime), [ADR 0035](0035-design-system-and-theme-architecture.md) (design system), [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) (Phase 6 Kanban visual spec), [ADR 0037](0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md) (distribution model), [DESIGN.md](../DESIGN.md), [DESIGN_SYSTEM.md](../DESIGN_SYSTEM.md), [PHASE6_BENCH_PREP.md](../PHASE6_BENCH_PREP.md)

## Context

[ADR 0031](0031-phase-4-is-closed-and-phase-5-may-open.md) authorized Phase 5 planning after Phase 4 closed the mechanics-before-UI foundation. [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md) then scoped Phase 5 as **(b) Desktop GUI + (c) distributable installer, observability-first**. The scope ordering was deliberately strict: render the existing strategy bank, risk, promotion, attribution, kill-switch, and runtime state before adding any new strategy operations.

Phase 5 executed that scope. The GUI runtime was fixed as PySide6 + Qt Quick by [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md). The design-system token set, themes, foundational QML components, application shell, and read-only observability surfaces landed across the Phase 5 GUI PR sequence. By the close-out slice, the production navigation had settled into the four-surface narrative recorded in [DESIGN.md](../DESIGN.md): `FRONT · BENCH · LEDGER · DESK`.

The four surfaces are intentionally read-only in Phase 5:

- **FRONT** gives the calm digest: the plain-language state of the system.
- **BENCH** renders strategy evidence and stage state, with modal detail, but no drag/write mechanics.
- **LEDGER** renders the paper-of-record/audit surface.
- **DESK** renders the dense cockpit view for an operator who wants the whole fold at once.

This matters because late Phase 5 produced a tempting Phase 6 doorway: the operator Kanban surface. [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) accepts the Kanban visual spec, but explicitly defers its mechanics. Phase 5 did not add drag-to-promote, bulk backtest/session dispatch, demotion gestures, live-boundary movement, or any write-capable promotion UI.

The installer scope also landed. [ADR 0037](0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md) chose PyInstaller `--onedir` + Inno Setup, unsigned with documented SmartScreen workaround. PR #73 added the installer directory, build script, PyInstaller spec, Inno Setup script, launcher, installed-build data-root resolution, and `docs/INSTALL.md`. The source-controlled evidence proves the installer path exists and is documented. Any external friend-test or GitHub Release publication evidence remains an operator/release artifact, not a source-controlled file.

This ADR is the Phase 5 close-out record.

## Decision

1. **Phase 5 is closed. Phase 6 planning may open.**

2. **Phase 5 closed against the observability-first interpretation of C-1.** The system now has a PySide6/QML desktop GUI with the `FRONT · BENCH · LEDGER · DESK` surface narrative wired end-to-end. The surfaces bind to read models and render real project state where the read models exist. Where a feed has not yet been modeled, the GUI uses honest empty states per [DESIGN.md §5.8](../DESIGN.md#58-empty-states-are-honest-not-coy), not placeholder market-looking numbers.

3. **Phase 5 closed against the source-controlled implementation portion of C-2.** The distribution model ADR landed as [ADR 0037](0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md), and the installer implementation landed in PR #73. Milodex now has a Windows-first manual build path for an unsigned Inno Setup installer. Friend-test/release-publication evidence is outside the repository and remains an operator release checklist item, not a reason to keep the development phase open.

4. **All prior trust boundaries remain in force.** This ADR does not relax [ADR 0004](0004-paper-only-phase-one.md), [ADR 0005](0005-kill-switch-manual-reset.md), [ADR 0008](0008-risk-layer-veto-architecture.md), [ADR 0009](0009-promotion-pipeline-stage-model.md), [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md), [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md), [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md), or [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md).

5. **Phase 6 inherits Kanban as the likely anchor program, but not as pre-authorized implementation.** [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md) locks the visual spec and names deferred mechanics questions Q-A through Q-H. Those mechanics are now answered by ADRs 0039-0048 and summarized in [PHASE6_BENCH_PREP.md](../PHASE6_BENCH_PREP.md). Phase 6 implementation work must follow those decisions before shipping write-capable behavior.

6. **The remaining "not wired" empty states are honest Phase 6+ data-feed opportunities, not Phase 5 defects.** Market tape, sector heat, calendar, feed latency, capital deployed, drawdown, and comparable placeholders may be wired as future read models. They do not justify reopening Phase 5 because Phase 5's rule was truthful observability, not simulated completeness.

## Rationale

**Phase 5 did the thing ADR 0034 said to do: render before extending.** The project now has a desktop surface that lets the operator see Milodex without falling back to the CLI for the first orientation pass. It did not smuggle in new strategy verbs while doing so. That distinction is the core success condition. A read-only GUI is less exciting than a drag-capable operations board, but it is the correct first surface for a trading system whose risk layer and promotion gate are meant to stay sacred.

**The four-surface narrative is a stronger close-out point than the early surface names.** Phase 5 began from Anchor and Strategy Bank language. It closes with `FRONT · BENCH · LEDGER · DESK`, a clearer division of labor: approachable digest, operational bench, audit record, and dense cockpit. That is not a marketing reshuffle; it is the way the GUI keeps contradictory needs from fighting on one screen. A non-expert can read FRONT. A skeptical operator can verify LEDGER. A power user can live in DESK. BENCH holds strategy state without pretending Phase 6 mechanics already exist.

**Honest empty states preserve trust.** The current GUI still has visible "not wired" surfaces. That is acceptable because [DESIGN.md §5.8](../DESIGN.md#58-empty-states-are-honest-not-coy) makes the rule explicit: missing feeds must say they are missing. A trading UI that fabricates tape, heat, latency, or drawdown data to feel finished would undermine the very trust Phase 5 is meant to increase. Truthful incompleteness is a better Phase 5 endpoint than decorative completeness.

**The installer path is real enough to close the development phase.** PR #73 implements the chosen distribution posture: PyInstaller `--onedir`, Inno Setup, per-user install location, installed-build data-root handling, hash-printing build script, and documented SmartScreen/hash-verification flow. The friend-test named in PHASE5_PLANNING.md remains valuable, but its durable evidence is naturally a release artifact or operator note rather than source code. Keeping Phase 5 open for non-source-controlled release ritual would blur the distinction between implementation phase and release operation.

**The Kanban surface is captured without becoming Phase 5 scope.** ADR 0036 is useful precisely because it freezes the visual contract and defers the mechanics. The operator knows what the next major surface should feel like, and the repo knows which decisions must land before it mutates strategy state. This is the cleanest possible Phase 5-to-Phase 6 handoff: the doorway is visible, but the latch is not quietly opened.

## Closed exit criteria — evidence summary

| Deliverable | Closed | Evidence |
|---|---|---|
| Phase 5 scope selected as GUI + installer, observability-first | 2026-05-07 | [ADR 0034](0034-phase-5-scope-orders-observability-before-features.md), `PHASE5_PLANNING.md` §4.1 / §4.2 |
| GUI runtime selected | 2026-05-07 | [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md) |
| Design-system foundation | 2026-05-07 onward | [ADR 0035](0035-design-system-and-theme-architecture.md), `docs/DESIGN_SYSTEM.md`, `src/milodex/gui/qml/Milodex/Theme.qml`, themes, foundational QML components |
| Application shell and read-only GUI surfaces | 2026-05-07 to 2026-05-09 | PRs #63-#84: GUI module, bundled fonts, ThemeManager, QML components, app shell, FRONT, BENCH, LEDGER, DESK, router, screenshot tooling, design mockups |
| Four-surface narrative documented | 2026-05-09 | [DESIGN.md](../DESIGN.md), especially §4 and §5.8 |
| Operator Kanban captured and deferred | 2026-05-08 | [ADR 0036](0036-operator-kanban-surface-for-promotion-pipeline.md), [PHASE6_BENCH_PREP.md](../PHASE6_BENCH_PREP.md) |
| Distribution model selected | 2026-05-08 | [ADR 0037](0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md) |
| Installer implementation landed | 2026-05-08 | PR #73 / commit `792fb19`: `installer/milodex.spec`, `installer/milodex.iss`, `installer/build_installer.ps1`, `installer/milodex_launcher.py`, `docs/INSTALL.md`, installed-build data-root handling |

## Phase 6 carry-forward

These are not Phase 5 defects. They are the first honest menu for Phase 6 planning.

1. **Kanban foundation implementation.** ADR 0036 Q-A through Q-H are resolved by ADRs 0039-0048: display-name provenance, stage/session decoupling, eligibility-window policy, demotion security, hover/drop validation timing, responsive layout, bulk orchestration, and stage-hue token reconciliation. The first Phase 6 Kanban PR should be read-only foundation work before write-capable drag or bulk actions.

2. **Read-model/data-feed wiring for honest empty states.** Candidate feeds: market tape, sector heat, calendar, feed latency, capital deployed, drawdown, and any related DESK/FRONT placeholders. Each should be wired as a read model before the UI treats it as real.

3. **Surface-touching cleanup.** Fold dormant cleanup into the next relevant PR rather than running a standalone cleanup phase: orphan mock data in `DeskSurface`, unreachable `BenchSurface` modal paths, token-arithmetic deltas, standfirst copy that implies drag capability before it exists, and the `LedgerSurface` empty-state when `entries=[]`.

4. **Release-operations checklist.** Preserve the ADR 0037 distribution posture through actual releases: build installer, publish SHA-256, submit false-positive reports where appropriate, verify installer behavior on a clean non-developer machine, and record the outcome in release notes or an operator note.

## Consequences

- **`PHASE5_PLANNING.md` becomes a historical record.** Future edits should correct historical accuracy only. Active planning moves to Phase 6 artifacts.
- **Phase 6 may open, but does not inherit permission to bypass risk or promotion.** Write-capable GUI behavior must remain a front end over the existing promotion/risk rules, not a parallel route around them.
- **Live and micro_live remain locked unless a future ADR explicitly opens them.** Phase 5 did not create live eligibility, live promotion UI, or any exception to [ADR 0004](0004-paper-only-phase-one.md).
- **The GUI's truthful-incomplete posture remains valid.** "Not wired" is acceptable until the corresponding read model exists. Fake data is not.
- **Installer work moves from development scope to release operations.** The source implementation exists. Future work may add CI builds, code signing, auto-update, or non-Windows distribution only through new Phase 6+ decisions.
- **Phase 6 starts with a clearer doorway than Phase 5 did.** The next phase can start with the implementation foundation described in PHASE6_BENCH_PREP.md or with read-model wiring for the empty states. Both paths are consistent with Phase 5's close.

## Non-goals

- Does not implement the Phase 6 Kanban.
- Does not itself answer ADR 0036 Q-A through Q-H; those are answered by ADRs 0039-0048.
- Does not authorize drag-to-promote, drag-to-demote, bulk backtest/session commands, or any write-capable promotion surface.
- Does not authorize micro_live or live trading.
- Does not supersede ADR 0037's unsigned installer posture.
- Does not require every Phase 5 tactical cleanup item to be fixed before Phase 6 opens.
- Does not permit placeholder market data, placeholder P&L, or placeholder drawdown to masquerade as real observability.
