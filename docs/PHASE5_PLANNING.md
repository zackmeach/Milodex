# Phase 5 Planning

> **Phase 5 was formally closed on 2026-05-10 via [ADR 0038](adr/0038-phase-5-is-closed-and-phase-6-may-open.md).** This document is now a historical record. Active planning belongs in Phase 6 artifacts, starting with [PHASE6_BENCH_PREP.md](PHASE6_BENCH_PREP.md) and any follow-up ADRs required by [ADR 0036](adr/0036-operator-kanban-surface-for-promotion-pipeline.md).

**Status:** Closed 2026-05-10 via [ADR 0038](adr/0038-phase-5-is-closed-and-phase-6-may-open.md). Originally opened 2026-05-07 as the prerequisite-mandated planning artifact per [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md). Filename `PHASE5_PLANNING.md` is chosen deliberately over `ROADMAP_PHASE5.md` because Phase 5's structural scope was decided at open ((b)+(c) observability-first per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md)) but the surface-by-surface implementation ordering was not — same convention prior phases used. **§4 is closed.** §4.1 is closed by [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md), §4.2 is closed (live remains locked), §4.6 is closed by [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md), and §4.7 is closed by [ADR 0037](adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md).

**Predecessors:** [PHASE4_PLANNING.md](PHASE4_PLANNING.md) (closed historical record), [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md) (authorizes this doc), [ADR 0028](adr/0028-phase-4-scope-closes-as-cleanup-and-attribution.md) (the mechanics-before-UI principle that justifies the Phase 5 GUI work sitting where it does), [ADR 0029](adr/0029-per-strategy-position-attribution-at-risk-layer.md) (per-strategy attribution — the data the GUI renders), [ADR 0030](adr/0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) (backtest sandbox semantics), [VISION.md](VISION.md), [FOUNDER_INTENT.md](FOUNDER_INTENT.md), [SRS.md](SRS.md).

---

## 1. What Phase 4 Left Behind

Phase 4 closed against its (f)+(g) scope per [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md): per-strategy position attribution at the risk layer ([ADR 0029](adr/0029-per-strategy-position-attribution-at-risk-layer.md)) and a four-part cleanup bundle (doc drift, test audit + Critical/Important gap closure, concurrent backtest UX with `--parallel N`, backtest sandbox semantics via [ADR 0030](adr/0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md)). Net +215 tests across the phase (556 → 771), zero regressions. Live boundary stayed locked per §4.2 = (a). The full close-out narrative lives in [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md); this section captures only what carries into Phase 5.

The single most load-bearing Phase 4 result was the **mechanics-before-UI principle, structurally satisfied**. Per [ADR 0028](adr/0028-phase-4-scope-closes-as-cleanup-and-attribution.md), the operator declined option (b) GUI in Phase 4 on the grounds that a UI built on mechanics with known ambiguities roughly doubles debugging cost. Phase 4 closed those ambiguities: per-strategy attribution data is now reachable at the risk layer, the test suite was audited and gap-closed against mutation testing, the backtest path lost its five-command demotion ceremony, and the documents were brought back into alignment with reality. Phase 5 inherits that foundation: the data the GUI renders is testable, the surfaces the GUI sits on are individually exercisable, and the trust properties the GUI must not weaken are explicitly named and regression-tested. **Anything Phase 5 adds must not weaken these properties** — same binding constraint that has held since Phase 1, now sitting on a firmer base than at any prior phase.

Phase 4 also left:

- An **empty §3 carry list** per [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md). Phase 5 starts from zero outstanding cleanup work.
- A **structural lock on live trading** per [ADR 0004](adr/0004-paper-only-phase-one.md). [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md) explicitly does not relax it. Phase 5 may revisit, but only via a new ADR superseding 0004 — and the §4.1 scope decision via [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) does not authorize that revisit.
- A **per-strategy attribution module** at `src/milodex/risk/attribution.py` with the dual-cap semantic ([ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md) account-scoped floor + [ADR 0029](adr/0029-per-strategy-position-attribution-at-risk-layer.md) per-strategy ceiling). The "operator" pseudo-strategy id is now reserved per ADR 0029 Decision 3.
- A **backtest sandbox semantic** ([ADR 0030](adr/0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md)): backtesting a paper-stage strategy requires no demotion ceremony. The `is_backtest` flag on `EvaluationContext` is the architectural hook.
- A **per-process supervisor concurrency model** ([ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md)) and a **walk-forward labeling discipline** (P-1) that renders automatically for new strategies.
- **Six paper-stage strategies** per [STRATEGY_BANK.md](STRATEGY_BANK.md): `regime.daily.sma200_rotation.spy_shy.v1` (lifecycle-exempt), plus five statistical promotions on 2026-05-07 — `breakout.daily.atr_channel.sector_etfs.v1`, `meanrev.daily.bbands_lowerband.curated_largecap.v1`, `meanrev.daily.pullback_rsi2.curated_largecap.v1`, `momentum.daily.tsmom.curated_largecap.v1`, `breakout.daily.donchian_20_10.sector_etfs.v1`. Six others remain at backtest stage (blocked for structural/signal-decay reasons, not parameter reasons).
- A **pre-Phase-5 runtime cleanup wave** (PRs #51–#60) that closed runtime defects surfaced by the first multi-strategy paper day: graceful shutdown, market-hours gate before fetch, adaptive poll interval tied to bar size, batched StockBars, broker 429 retry/backoff parity, stage-validation guard, log routing with rotation, dual-ancestor `explanations.backtest_run_id` to close the orphan-evaluations gap. Mechanics are firmer at Phase 5 open than at any prior phase boundary.
- A **§4.1 deferred menu** that becomes the Phase 5 candidate set: (a) micro_live, (b) GUI, (c) installer, (d) third research-target, (e) disciplined re-tune. Phase 5's scope decision per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) selects (b)+(c); options (a), (d), (e) carry forward to Phase 6+.

---

## 2. Phase 5 Goals (Anchor: FOUNDER_INTENT priority order)

[FOUNDER_INTENT.md](FOUNDER_INTENT.md) priority order is unchanged across phases:

1. **Trustworthy.** Build something real, functional, and trustworthy.
2. **Engineering capability.** Demonstrate strong AI-assisted engineering.
3. **Accessibility.** Make the system accessible and easy to use.
4. **Shareability.** Make it portfolio-worthy.
5. **Profitability.** Pursue profit as validation of effectiveness.

What changes for Phase 5 vs Phase 4: priorities #3 (accessibility) and #4 (shareability) become the active pulls. Phase 4 firmed the mechanics so a UI could sit on them without importing display-vs-data ambiguity. Phase 5 renders those mechanics — strategy bank state, per-strategy attribution and P&L, paper-session status, kill-switch state, walk-forward labeling — as the first non-CLI surface in Milodex's history. Priority #1 (trustworthy) remains the floor: the GUI is a render layer, not a new source of truth. Priority #2 (engineering capability) shows up as the depth the runtime choice demonstrates ([ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md): full QML, design-system in scope, no escape to default-OS-chrome).

Priority #5 (profitability) remains structurally premature: it is unlocked only by a strategy that has earned the gate at micro_live or live, and Phase 5 does not move the live boundary. Phase 5 may surface the gate's verdict more legibly (the daily-operator workflow becomes inspectable from a screen), but it does not adjudicate the verdict.

Phase 5 goals:

- **G1.** Preserve [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md)'s C-2 honest-signal regression, [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md) account-scope floor, [ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) per-process supervisor pattern, [ADR 0029](adr/0029-per-strategy-position-attribution-at-risk-layer.md) per-strategy attribution, [ADR 0030](adr/0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) backtest sandbox semantics, [ADR 0005](adr/0005-kill-switch-manual-reset.md) manual-reset semantic, and P-1 walk-forward labeling. Anything added in Phase 5 must not weaken these. The trustworthiness-and-engineering floor.

- **G2.** Ship the (b)+(c) bundle observability-first per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md). Render the existing strategy bank, attribution, and paper-session state on a Tier B desktop UI before any installer work; ship a friend-installable distribution after the GUI ships.

- **G3.** Lock the GUI runtime architecture choice via [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md) (PySide6 + Qt Quick, full QML). Ship a versioned design-system token set as the first PR in the GUI surface — color, typography, spacing, motion, elevation tokens applied consistently across every QML surface.

- **G4.** Resolve §4.7 (distribution model) when the installer PR opens, with a new ADR. PyInstaller is the default candidate per [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md); the §4.7 ADR confirms or supersedes.

- **G5.** Friend-test the installer at least once on a non-developer machine before release publication. [ADR 0038](adr/0038-phase-5-is-closed-and-phase-6-may-open.md) closes the development phase against source-controlled installer implementation and moves friend-test evidence to release operations.

- **G6.** Per-feature ADRs for any architectural seam crossed beyond the runtime decision. Distribution model (§4.7), Python ↔ QML bridge convention if it becomes load-bearing, charting library if its choice constrains future surfaces, kill-switch GUI affordance if it adds a new confirmation pattern.

---

## 3. Carry List

**Empty per [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md).** Phase 4 closed with zero carry items. Phase 5 starts from zero outstanding §3 items.

If anything surfaces during Phase 5 planning or execution, it gets added here with a stable identifier following the convention from prior phases.

---

## 4. Open Phase 5 Scope Questions

These are the questions the operator owns. Each is framed with alternatives. Most are decided at Phase 5 open via the linked ADRs; §4.7 remains the principal open question.

### 4.1 ~~What does Phase 5 actually scope?~~ — **Decided 2026-05-07: (b) + (c) observability-first**

> **Decision ([ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md)):** Phase 5 scopes as **(b) Desktop GUI + (c) distributable installer, observability-first**. PR sequence puts observability surfaces (strategy bank rendering, per-strategy attribution and P&L, paper-session status, kill-switch state, walk-forward labeling, the [VISION.md "Daily Operator Workflow"](VISION.md#daily-operator-workflow) eight-step loop) before any feature surface that adds new strategy operations. Installer ships after the GUI surfaces ship. Options (a) micro_live, (d) third research-target, (e) re-tune from the Phase 4 §4.1 deferred menu remain Phase 6+ candidates and are not pre-authorized.

Five candidates carried forward from Phase 4's §4.1 deferred menu. *(Historical record — scope decided above.)*

**(a) Micro_live promotion** *(Phase 4 §4.1.a deferred → here)*. Open the live boundary at micro_live. Structurally constrained: regime is the only strategy that could promote (lifecycle-exempt — operational evidence, not statistical), since the five statistical promotions on 2026-05-07 are paper-stage with no paper evidence yet. Tied to §4.2.

**(b) Desktop GUI** *(Phase 4 §4.1.b deferred → here)*. First non-CLI interface. FOUNDER_INTENT #3 (accessibility). Tied to §4.6 (runtime). **Selected.**

**(c) Distributable installer / clone-and-run path** *(Phase 4 §4.1.c deferred → here)*. The friend-installable distribution per VISION. FOUNDER_INTENT #4 (shareability). Tied to §4.7 (distribution model). **Selected.**

**(d) Third research-target strategy** *(Phase 4 §4.1.d deferred → here)*. After meanrev (Phase 1) and momentum (Phase 3) failed the gate, what's the next family to try? Phase 4 surfaced six additional candidates at backtest stage; some are blocked for structural reasons, some for signal-decay reasons. A *new* family entry would be additive, not curative. Carries to Phase 6+.

**(e) Disciplined re-tuning** *(Phase 4 §4.1.e deferred → here)*. Per VISION's "idea vs. tuning" rule, a re-tune is legitimate only with declared search space + per-round OOS check + fragility-as-fragility. Carries to Phase 6+.

**Bundle shape selected: (b)+(c) observability-first.** Highest combined FOUNDER_INTENT alignment (#3 + #4) and the mechanics-before-UI complement: render the work that just shipped before extending the strategy surface.

### 4.2 ~~What's the live-trading boundary for Phase 5?~~ — **Decided 2026-05-07: (a) live remains locked**

> **Decision ([ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md)):** Phase 5 §4.2 = **(a) — live remains locked**. [ADR 0004](adr/0004-paper-only-phase-one.md) is unchanged and stays in force through Phase 5. Neither (b) nor (c) requires the live boundary to move. The GUI surfaces kill-switch state and reset affordance per [ADR 0005](adr/0005-kill-switch-manual-reset.md) (manual reset, explicit confirmation) but does not move the live boundary. Live unlocking is a Phase 6+ question and requires a new ADR superseding ADR 0004.

[ADR 0004](adr/0004-paper-only-phase-one.md) is still in force. Phase 5 may revisit, but only via a new ADR that supersedes 0004 — and the (b)+(c) scope does not require any such revisit. *(Historical record — boundary decided above.)*

**(a) Live remains locked.** Phase 5 stays paper-only. Mirrors Phases 2, 3, 4. **Selected.**

**(b) Unlock micro_live only.** Open the first live stage but not full-scale live. Not selected. Would require §4.5 resolution and the new ADR superseding 0004.

**(c) Unlock all stages.** Full live trading authorized. **Structurally premature** — no strategy has produced micro_live evidence. Not selected.

### 4.3 What's the Phase 5 floor table?

Same Phase 4 floor table, with each item updated for Phase 5. The default remains: **everything still locked unless explicitly opened.**

| Item | Phase 4 status | Default Phase 5 status |
|---|---|---|
| Concurrent multi-strategy execution | In force per [ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) | **In force** (unchanged) |
| Per-strategy position attribution at risk layer | In force per [ADR 0029](adr/0029-per-strategy-position-attribution-at-risk-layer.md) | **In force** (unchanged) |
| Backtest sandbox semantics | In force per [ADR 0030](adr/0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) | **In force** (unchanged) |
| Daemon / supervisor runtime model | Out by default | Out by default |
| Crypto / alternative assets | Out by default | Out by default |
| ML-driven signals | Out by default | Out by default |
| Alternative / sentiment data | Out by default | Out by default |
| Desktop GUI | Open question (§4.1.b) | **In scope** per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md); runtime locked per [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md) |
| Alternative brokers | Out by default | Out by default |
| Distributable installer | Open question (§4.1.c) | **In scope** per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md); model is §4.7 |
| Live trading | Open question (§4.2) → locked | **Locked** per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) (default) |
| HFT / low-latency trading | Out | Out |
| Multi-user collaboration | Out | Out |
| Walk-forward parameter search | Out per [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md) | Out |
| Options / derivatives | Out by default | Out by default |
| Cloud-native distributed architecture | Out | Out by default |
| Social / marketplace features | Out | Out by default |
| Auto-resume after kill switch | Out per [ADR 0005](adr/0005-kill-switch-manual-reset.md) | Out (manual reset is the contract; GUI preserves it) |
| Unattended overnight running | Out by default | Out by default |
| Auto-discovered universe expansion | Out per VISION research discipline | Out by default |
| Third research-target | Open question (§4.1.d) → not chosen | Out (Phase 6+) per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) |
| Disciplined re-tune | Open question (§4.1.e) → not chosen | Out (Phase 6+) per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) |
| New strategy operations beyond Phase 5 open | N/A | **Out** per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) |

### 4.4 N/A — no third research-target or re-tune in Phase 5 scope

(Phase 4's §4.4 split conditional questions for (d) third research-target and (e) re-tune. Phase 5 does not select either; this section is preserved as a placeholder so future planning can reference Phase 4's framing if Phase 6+ opens these.)

### 4.5 N/A — no live-trading boundary movement in Phase 5

(Phase 4's §4.5 framed which strategy promotes to micro_live if the boundary moves. Phase 5's §4.2 keeps the boundary locked, so this section has no resolution to record.)

### 4.6 ~~Which GUI runtime model?~~ — **Decided 2026-05-07: PySide6 + Qt Quick (full QML)**

> **Decision ([ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md)):** GUI runtime is **PySide6 + Qt Quick** with the UI tree authored in QML throughout. Python is the data and logic backend exposed via `QObject` subclasses with `@Property`, `@Slot`, and `Signal`, registered as `@QmlElement`. Qt Widgets is not used in the production UI tree (narrow exception requires its own ADR-level justification). A versioned design-system token set is in scope as the first GUI PR.

Five candidates considered; Tauri, Electron, Flet, Widgets-only PySide6, and Widgets+QML-hybrid PySide6 were each rejected with explicit reasons recorded in [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md). The decision matrix is in that ADR; this section preserves only the resolution.

### 4.7 ~~What's the distribution model?~~ — **Decided 2026-05-08: (γ) PyInstaller `--onedir` + Inno Setup, unsigned with documented SmartScreen workaround**

> **Decision ([ADR 0037](adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md)):** Bundle is PyInstaller `--onedir` (not `--onefile`). Installer wrapper is Inno Setup producing a per-user `%LOCALAPPDATA%\Programs\Milodex\` install (no admin elevation). Code-signing posture is **unsigned** for Phase 5; SmartScreen "More info → Run anyway" workaround documented in `docs/INSTALL.md` with SHA-256 verification instructions. Auto-update deferred to Phase 6+. Platform scope is Windows only for Phase 5; macOS / Linux deferred. The unsigned posture is reversible without architectural rework when an audience-driven case for a code-signing certificate emerges.

Four candidates considered; (α) `pip install` was ruled out for failing FOUNDER_INTENT priorities #3/#4 for a non-developer audience, (β) PyInstaller alone was passed-over in favor of the more polished installer-wrapper UX, and (δ) Docker was ruled out for not fitting the "polished desktop product" intent. The decision matrix and rationale are in [ADR 0037](adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md); this section preserves only the resolution.

---

## 5. Exit Criteria

Phase 5 has three exit criteria:

- **C-1.** *(scope: (b) GUI)* Desktop GUI ships with the [VISION.md "Daily Operator Workflow"](VISION.md#daily-operator-workflow) eight steps surfaced. The operator can answer all of "what strategy or system is active / what Milodex is doing on their behalf / what data it is using / what the current state is / what actions it may take next / what safeguards or limits are in place" from the GUI without falling back to the CLI. Per-strategy attribution and P&L render correctly against `data/milodex.db` evidence. Kill-switch state and reset affordance preserve the [ADR 0005](adr/0005-kill-switch-manual-reset.md) manual-reset semantic with explicit confirmation. The Phase 5 design-system token set ([ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md)) is applied consistently across every GUI surface.

- **C-2.** *(scope: (c) installer)* Distributable installer produces a working Milodex install path. The §4.7 distribution-model ADR is landed and references this exit. [ADR 0038](adr/0038-phase-5-is-closed-and-phase-6-may-open.md) treats the source-controlled installer implementation and documentation as sufficient to close the development phase; friend-test and release-publication evidence are release-operations artifacts. Code-signing posture is explicit and recorded (signed, or unsigned with documented SmartScreen workaround).

- **C-3.** *(always — preserves Phase 1+2+3+4 invariants)* All prior-phase invariants preserved end-to-end:
  - C-2 honest-signal regression tests still green ([ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md)).
  - [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md) account-scoped position caps still enforced as the floor.
  - [ADR 0029](adr/0029-per-strategy-position-attribution-at-risk-layer.md) per-strategy attribution still operational; the regime+meanrev integration scenario still passes.
  - [ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) per-process supervisor still authoritative.
  - [ADR 0030](adr/0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md) backtest sandbox semantics still in force.
  - [ADR 0005](adr/0005-kill-switch-manual-reset.md) kill-switch manual-reset semantic preserved through every surface, GUI included.
  - [ADR 0004](adr/0004-paper-only-phase-one.md) paper-only lock still in force.
  - P-1 walk-forward labeling still rendering on every walk-forward run.
  - No silent removal or relaxation of any prior ADR via configuration. Removals require a new ADR explicitly superseding the prior one.

Phase 5 ends when C-1, C-2, and C-3 are simultaneously true, an ADR closes Phase 5 analogous to [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md) / [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md) / [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md) / [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md), and Phase 6 planning is authorized.

### Deferred candidates (stay deferred unless §4 reopens them — Phase 6+)

- (a) micro_live promotion of any strategy. Tied to §4.2 — boundary remains locked.
- (d) third research-target strategy.
- (e) disciplined re-tune of meanrev or momentum.
- Full live trading promotion (any strategy from micro_live to live). Structurally premature.
- Auto-discovered universe expansion. Out per VISION research discipline.
- Daemon / supervisor runtime model. Conflicts with [ADR 0012](adr/0012-runtime-and-dual-stop.md) and [ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md).
- Alternative broker integration. VISION names this as Phase 2+ optionality.
- Sentiment / alternative data, ML-driven signals, premium data sources.

---

## 6. What This Document Is *Not*

- **Not a commitment beyond the (b)+(c) observability-first scope.** §4.7 is now closed by [ADR 0037](adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md).
- **Not a substitute for ADRs.** Any decision in Phase 5 that crosses an architectural seam (distribution model, Python↔QML bridge convention if it becomes load-bearing, charting library if its choice constrains future surfaces, kill-switch GUI affordance if it adds a new confirmation pattern) requires its own ADR.
- **Not the final shape of Phase 5.** Prior phases evolved through their work; Phase 5's planning expects the same. The PR sequence within "observability before installer" is operator-owned.
- **Not a reframe of profitability.** Per [FOUNDER_INTENT.md](FOUNDER_INTENT.md), profit is validation, not purpose. Phase 5's success is rendering the platform's existing trustworthy, engineering-capable mechanics on a Tier B desktop UI that a non-developer can install and use — not whether any strategy makes money during paper validation.
- **Not an authorization for live capital.** Phase 5 does not unlock micro_live or any live boundary. The autonomy-boundary actions in [VISION.md §Autonomy Boundary](VISION.md#autonomy-boundary) remain human-gated regardless of phase.
- **Not a sequencing plan beyond observability-before-installer.** Surface-by-surface ordering inside the observability phase is operator-owned.
- **Not authorization for new strategy operations.** Per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md), Phase 5 does not add new strategy operations beyond what exists at Phase 5 open. New strategies, re-tunes, or any UI for new strategy operations are explicitly out of scope.

---

## 7. What's Explicitly Still Out (Phase 5 Floor)

These remain out of scope for Phase 5 unless a separate ADR opens them:

- **High-frequency / low-latency trading**
- **Multi-user collaboration as a first-class system requirement**
- **Fully autonomous live trading without human gating**
- **Walk-forward parameter search** (conflicts with [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md))
- **Cloud-native distributed architecture**
- **Options / derivatives**
- **AI-generated strategy invention without strict human review**
- **Social / marketplace features**
- **Auto-resume after kill switch** (manual reset per [ADR 0005](adr/0005-kill-switch-manual-reset.md))
- **Unattended overnight / multi-day continuous running** (out by default)
- **Premium data sources without justified edge improvement**
- **Auto-discovered universe**
- **Re-tuning as if it were a new idea** (per VISION's "idea vs. tuning" rule)
- **Live-trading boundary movement** (paper-only lock per [ADR 0004](adr/0004-paper-only-phase-one.md))
- **New strategy operations beyond Phase 5 open** per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) — no new-family wizard, re-tune UI, micro_live promotion UI, or any GUI surface that adds operations beyond rendering existing data
- **Qt Widgets in the production UI tree** per [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md) (narrow exception requires its own ADR)
- **Mobile or embedded targets** (Qt Quick supports them; Phase 5 does not scope them)

---

## 8. Tracking Conventions

Same conventions as [PHASE2_PLANNING.md §8](PHASE2_PLANNING.md), [PHASE3_PLANNING.md §8](PHASE3_PLANNING.md), and [PHASE4_PLANNING.md §8](PHASE4_PLANNING.md). Reproduced for self-containedness.

- Each new carry item gets a stable identifier.
- §4 scope decisions are decisions, not work items. Each one's resolution becomes a §1-style anchor section once chosen, and any architectural seam it crosses gets its own ADR.
- This doc evolves until Phase 5 has stable scope on all questions. At that point §4 / §5 freeze; §3 / §7 carry forward.
- Resolved §4 questions are struck through with the decided option called out inline.
- Items checked off as completed with linked merge commits. Reopening only happens if the definition of done regresses.

---

## 9. Historical Implementation Sequence

§4.1, §4.2, §4.6 were decided at Phase 5 open via [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md) and [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md). §4.7 was later closed by [ADR 0037](adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md). This section is preserved as historical sequencing, not active next steps.

PR sequence within Phase 5, per [ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md)'s observability-first ordering and [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md)'s design-system requirement:

1. **Design-system token set.** First Phase 5 GUI PR. Specified by [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) and authorized by [ADR 0035](adr/0035-design-system-and-theme-architecture.md). Editorial direction (Newsreader + Public Sans + JetBrains Mono), three themes (Editorial Dark default, Editorial Light, Bronze), Theme singleton + property-binding hot-swap, status-color theming policy. The first PR ships the documented spec, the QML Theme infrastructure, the four foundational components, and the bundled fonts.
2. **Application shell + main window.** PySide6 application bootstrap, top-level window, navigation skeleton. QML-only UI tree per [ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md). Python ↔ QML bridge pattern documented as it lands.
3. **Anchor surface — `milodex status` equivalent.** Trading mode, kill-switch state with manual-reset affordance per [ADR 0005](adr/0005-kill-switch-manual-reset.md), market-clock, account balance, open positions count. Mirrors VISION's "Daily Operator Workflow" step 1.
4. **Strategy bank surface.** Six paper-stage strategies rendered with their evidence run IDs, walk-forward Sharpe / MaxDD / trade count, promotion type, watch-during-paper notes. Mirrors [STRATEGY_BANK.md](STRATEGY_BANK.md) on a screen.
5. **Per-strategy attribution and P&L surface.** [ADR 0029](adr/0029-per-strategy-position-attribution-at-risk-layer.md)'s attribution data rendered per strategy: open positions, attributed P&L, dual-cap state. The "what have my strategies made or lost" answer the operator named in [ADR 0028](adr/0028-phase-4-scope-closes-as-cleanup-and-attribution.md).
6. **Paper-session status + walk-forward labeling.** Active paper-runner state, last-tick time, strategy-runs and evaluations counts. Walk-forward report rendering with `(OOS)` per-metric labels per P-1. Backtest sandbox state ([ADR 0030](adr/0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md)) surfaced where relevant.
7. **Distribution-model ADR (§4.7) + installer PR.** Closed by [ADR 0037](adr/0037-distribution-model-pyinstaller-onedir-plus-inno-setup-unsigned.md) and the installer implementation referenced by [ADR 0038](adr/0038-phase-5-is-closed-and-phase-6-may-open.md). Friend-test evidence remains a release-operations artifact.
8. **Phase 5 close-out ADR.** Closed by [ADR 0038](adr/0038-phase-5-is-closed-and-phase-6-may-open.md).

The PR count above is indicative, not committed. Surface ordering within "observability before installer" is operator-owned. The binding constraint is the ordering ([ADR 0034](adr/0034-phase-5-scope-orders-observability-before-features.md)) and the runtime ([ADR 0033](adr/0033-gui-runtime-is-pyside6-qt-quick.md)), not the exact PR-by-PR breakdown.

A combined-everything shape ((a)+(b)+(c)+(d)+(e)) is structurally too large for a single phase — same scope-discipline argument that has held since Phase 2. Phase 5 picks (b)+(c) observability-first, closes it, and lets Phase 6 planning open against the remaining deferred menu.
