# ADR 0034 — Phase 5 scope orders observability before features

**Status:** Accepted · 2026-05-07
**Related:** [PHASE5_PLANNING.md](../PHASE5_PLANNING.md) §4.1, [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md) (GUI runtime), [ADR 0031](0031-phase-4-is-closed-and-phase-5-may-open.md) (authorizes Phase 5 planning), [ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md) (mechanics-before-UI; precedent for scope-decision-as-ADR), [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) (the attribution data the GUI renders), [VISION.md](../VISION.md), [FOUNDER_INTENT.md](../FOUNDER_INTENT.md)

## Context

[ADR 0031](0031-phase-4-is-closed-and-phase-5-may-open.md) closed Phase 4 with five §4.1 deferred candidates carrying forward as Phase 5 menu items: (a) micro_live, (b) GUI, (c) installer, (d) third research-target, (e) disciplined re-tune. On 2026-05-07 the operator scoped Phase 5 as **(b) + (c) — Desktop GUI + installer, observability-first**.

The "observability-first" qualifier is the load-bearing word. It binds the Phase 5 PR sequence to a specific ordering: render existing strategy data before adding any new strategy operations beyond what already exists at Phase 5 open. Without that binding, Phase 5 is structurally vulnerable to scope drift — the kind of "while we're here, let's also add X" pattern that has historically eroded phase boundaries.

This ADR records the scope decision and the ordering it implies. The departure from inline strikethrough — Phase 2 §4.1 and Phase 3 §4.1 used inline strikethrough — follows [ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md)'s precedent: scope decisions with architectural implications deserve their own record. "Observability before features" is architectural in the sense that it commits the platform's evolution path: Phase 5 renders the work that just shipped, it does not extend the strategy surface.

The operational pain that motivated Phase 5's scope is unchanged from the one [ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md) recorded: *"I can't quickly tell what strategies exist, where they are, how much they've made, what's been approved, how much money I have, how much I've gained or lost."* Phase 4's mechanics-firming ([ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md), [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md), the test audit, the doc drift cleanup) made the answer reachable from `data/milodex.db`. Phase 5 makes it reachable from a screen.

Six paper-stage strategies as of Phase 5 open form the rendering surface: `regime.daily.sma200_rotation.spy_shy.v1` (lifecycle-exempt), `breakout.daily.atr_channel.sector_etfs.v1`, `meanrev.daily.bbands_lowerband.curated_largecap.v1`, `meanrev.daily.pullback_rsi2.curated_largecap.v1`, `momentum.daily.tsmom.curated_largecap.v1`, `breakout.daily.donchian_20_10.sector_etfs.v1` (canonical reference: [STRATEGY_BANK.md](../STRATEGY_BANK.md)). The data backing each one's per-strategy P&L, attribution, paper-session state, walk-forward labeling, and kill-switch context already exists. Phase 5's job is to surface it.

## Decision

1. **Phase 5 scopes as (b) Desktop GUI + (c) distributable installer, observability-first.** This decision is the authoritative resolution of [PHASE5_PLANNING.md §4.1](../PHASE5_PLANNING.md).

2. **Live-trading boundary remains locked. [ADR 0004](0004-paper-only-phase-one.md) is unchanged.** PHASE5_PLANNING.md §4.2 inherits this resolution.

3. **PR ordering within Phase 5 puts observability surfaces before any feature surface that adds new strategy operations.** Specifically:
   - **Observability surfaces first.** Strategy-bank rendering, per-strategy attribution and P&L, paper-session status, kill-switch state, walk-forward labeling, market-clock and account-balance anchor surfaces, the [VISION.md](../VISION.md) "Daily Operator Workflow" eight-step loop. These render existing data; they do not add new operations that strategies can perform.
   - **Installer landing point.** The (c) installer ships *after* the observability surface ships, since there is nothing to install otherwise. The installer wraps a working GUI; the GUI does not wait on the installer.
   - **Feature surfaces are out of Phase 5 scope.** A re-tune workflow UI, new-family wizard, micro_live promotion UI, or any surface that gives a user new strategy operations beyond what exists at Phase 5 open is explicitly deferred to Phase 6+. This includes UI for any of (d) third research-target or (e) re-tune.

4. **Options (a), (d), (e) from the Phase 4 §4.1 deferred menu remain deferred to Phase 6+.** They are not pre-authorized by Phase 5's scope decision. Each requires its own scope decision when its phase opens.

## Rationale

**Mechanics-rendered-before-mechanics-extended.** [ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md) established the principle that a UI sitting on unclear mechanics roughly doubles debugging cost. The complement of that principle, applied to Phase 5: a UI that surfaces *existing* mechanics catches problems with those mechanics before the project commits to extending them. Rendering the bank reveals whether attribution data is reading correctly, whether the kill-switch state surfaces faithfully, whether walk-forward labels render against the actual evidence runs. Adding new strategy operations *and* the surface that renders them in the same phase is the worst-of-both posture: ambiguity grows in two dimensions at once.

**FOUNDER_INTENT priority order points here directly.** Priority #3 (accessibility) is satisfied by an observability surface that lets a user — including the founder — orient on the system's state without a CLI. Priority #4 (shareability) is satisfied by an installer that lets a peer or friend run that observability surface on their own machine. Adding new strategy operations in Phase 5 does not advance #3 or #4; it advances priority #5 (profitability), which is structurally premature without an edge that has earned the gate. None of the six paper-stage strategies has earned the gate; per-strategy attribution and gate-refusal evidence are precisely what observability-first surfaces, faithfully, for whatever decision comes next.

**The honest-signal property is preserved by what Phase 5 *doesn't* add.** [ADR 0023](0023-phase-1-is-closed-and-phase-2-may-open.md)'s C-2 honest-signal regression test locks the gate's behavior on meanrev. Phase 3 generalized that property to momentum. Phase 5 surfaces the honest-signal property to a screen — including walk-forward labels, gate verdicts, and refusal reasons — but does not change the gate, the threshold, or the way evidence accumulates. A scope decision that allowed new strategy operations in Phase 5 would risk pulling the honest-signal property into the GUI's own implementation surface (e.g., a re-tune UI that conflated tuning with idea generation, or a promotion UI that softened the gate's text). Observability-first keeps the property's source where it lives — in the risk layer, the promotion module, and the walk-forward report — and treats the GUI as a render-only consumer.

**Scope-creep prevention by design.** Without this ADR, the §4.1 strikethrough alone would carry the (b)+(c) decision but not the ordering. Mid-Phase-5, a "while we're here" PR (a 13th strategy, a re-tune wizard, a small live-trading toggle) could land on the grounds that the planning doc named (b)+(c) and didn't explicitly forbid the rest. This ADR is the explicit forbidding. Phase 4's [ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md) demonstrated that scope-decision-as-ADR is a useful instrument; this ADR is its Phase 5 application.

## Consequences

- **PHASE5_PLANNING.md §4.1 is struck through with the (b)+(c) observability-first decision and references this ADR.** §4.2 inherits live-locked.
- **Phase 5 PR sequence is bounded.** Observability surfaces first, installer follows, no feature surface that adds new strategy operations. The first PR is the design-system token set ([ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md)'s Phase 5 design-system requirement); subsequent PRs render observability data; the installer PR comes last.
- **The §4.1 deferred menu's (a), (d), (e) remain Phase 6+ candidates.** They are not pre-authorized; each requires its own phase scope decision.
- **A 13th strategy is out of Phase 5 scope.** Adding new strategies (whether new-family or re-tune of an existing one) is a strategy operation, not an observability surface. If a strategy needs to enter the bank during Phase 5, it does so via the existing CLI promotion path, not via Phase 5 GUI work.
- **Live-trading boundary remains locked.** [ADR 0004](0004-paper-only-phase-one.md) unchanged. Phase 5 does not unlock micro_live; the GUI surfaces kill-switch state and the manual-reset semantic ([ADR 0005](0005-kill-switch-manual-reset.md)) but does not move the live boundary.
- **Phase 5 exit criteria narrow accordingly.** PHASE5_PLANNING.md §5 lists exactly two scope-bearing criteria — GUI ships with the daily-operator workflow visible, installer produces a working install on a clean machine — plus the always-applicable "all prior-phase invariants preserved" criterion. No exit criterion authorizes new strategy operations.
- **Future ADRs (distribution model for §4.7, charting library if it crosses an architectural seam, any QML bridge convention that becomes load-bearing) cite this ADR for the scope ordering.**

## Non-goals

- Does not pre-decide the §4.7 distribution model. PyInstaller is the default candidate per [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md); a separate ADR resolves it.
- Does not commit to a specific PR count, sequencing within the observability-surfaces phase, or surface-by-surface design. Implementation ordering within "observability before installer" is operator-owned.
- Does not authorize Qt Widgets in the production UI tree (that is [ADR 0033](0033-gui-runtime-is-pyside6-qt-quick.md)'s domain).
- Does not authorize live trading, micro_live promotion, kill-switch auto-reset, or any [PHASE5_PLANNING.md](../PHASE5_PLANNING.md) §7 floor item.
- Does not relax [ADR 0023](0023-phase-1-is-closed-and-phase-2-may-open.md)'s C-2 honest-signal regression test, [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)'s account-scoped floor, [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md)'s per-strategy attribution, [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md)'s supervisor model, [ADR 0030](0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md)'s backtest sandbox semantics, or any prior trust property.
- Does not retire the §4.1 deferred menu candidates (a), (d), (e). They remain on the Phase 6+ menu for future scoping.
