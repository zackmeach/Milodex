# Phase 3 Planning

> **Phase 3 was formally closed on 2026-05-05 via [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md).** This document is now a historical record. C-1, C-2, C-6 all closed. New planning belongs in [PHASE4_PLANNING.md](PHASE4_PLANNING.md). Section §4.1 deferred candidates ((ii) micro_live, (iv) desktop GUI, (v) distributable installer, (vi) cleanup-only) carry forward as Phase 4 menu items, not commitments.

**Status:** Closed 2026-05-05. Originally opened 2026-05-04 as the prerequisite-mandated planning artifact per [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md). Filename `PHASE3_PLANNING.md` is chosen deliberately over `ROADMAP_PHASE3.md` because Phase 3 had no committed scope at opening — same convention Phase 2 used. **§4 was decided 2026-05-05 as (i)+(iii) engineering-led** with the live boundary remaining locked (§4.2 = (a)). The operative exit-criteria subset narrowed to **C-1 + C-2 + C-6**, all now closed.

**Predecessors:** [PHASE2_PLANNING.md](PHASE2_PLANNING.md) (closed historical record), [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md) (authorizes this doc), [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md) (Phase 2's named architectural artifact, foundation for any Phase 3 concurrency work), [VISION.md](VISION.md), [FOUNDER_INTENT.md](FOUNDER_INTENT.md), [SRS.md](SRS.md).

---

## 1. What Phase 2 Left Behind

Phase 2 closed against two exit criteria — C-1 (carry list closed) and C-2 (honest-signal property locked) — both evidenced in [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md). The full close-out narrative lives there; this section captures only what carries into Phase 3.

The single most load-bearing Phase 2 result was a **non-regression**: nothing Phase 2 added weakened Phase 1's honest-signal property. Phase 1's win was that the platform refused to lie about meanrev (walk-forward Sharpe 0.327 < 0.50 gate, refused promotion). Phase 2 turned that empirical fact into a machine-verifiable invariant via two regression tests keyed to those exact numbers. **Anything Phase 3 adds must not weaken this property** — same binding constraint Phase 2 inherited from Phase 1, now with stronger test-surface enforcement.

Phase 2 also left:

- An **empty §3 carry list.** All four Phase 1 §7 carry-forward items (CI-1 close-bar finalization, CI-2 strategy_runs row, CS-1 position-cap scope, P-1 walk-forward labeling) closed in Phase 2. Phase 3 starts from zero outstanding cleanup work.
- A **structural lock on live trading** per [ADR 0004](adr/0004-paper-only-phase-one.md). [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md) explicitly does not relax it. Phase 3 may revisit, but only via a new ADR superseding 0004 — relaxing it via configuration is not on the table.
- An **account-scoped position-cap discipline** codified in [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md). The risk evaluator's `concurrent_positions` check is account-authoritative; strategy YAML `risk.max_positions` is informational metadata only. Any Phase 3 multi-strategy work builds on this — sizing `max_concurrent_positions` to the sum of strategies' expected concurrent positions is now the documented contract, not implicit behavior.
- A **walk-forward labeling discipline** (P-1's resolution): per-metric `(OOS)` tags on every walk-forward run's trust-report surface, plus `sortino_ratio=None` for walk-forward (the equity curve is fragmented across OOS windows). Any new strategy types or new metrics added in Phase 3 must extend this discipline, not bypass it.
- A **Phase 2 §4 deferred menu** — (i) second research-target, (ii) micro_live promotion, (iii) concurrent multi-strategy, plus the §4.3 floor "Open question" items (GUI, installer). These become the Phase 3 §4.1 candidate set, none decided.
- Two **strategies in distinct lifecycle states**: regime SPY/SHY 200-DMA running paper-only as the lifecycle-proof (exempt from edge gates per `R-PRM-004`); meanrev RSI(2) pullback in `backtest` stage, refused promotion to paper because walk-forward Sharpe 0.327 < 0.50 gate. Whether either advances in Phase 3 is a §4 question.

---

## 2. Phase 3 Goals (Anchor: FOUNDER_INTENT priority order)

[FOUNDER_INTENT.md](FOUNDER_INTENT.md) fixes the priority order for tradeoffs (unchanged from Phase 1 and 2):

1. **Trustworthy.** Build something real, functional, and trustworthy.
2. **Engineering capability.** Demonstrate strong AI-assisted engineering.
3. **Accessibility.** Make the system accessible and easy to use.
4. **Shareability.** Make it portfolio-worthy.
5. **Profitability.** Pursue profit as validation of effectiveness.

What changes for Phase 3 vs Phase 2: Phase 2 was almost entirely priority #1 and #2 work (close trust gaps, rebuild surface to match spec). Phase 3 has priority #1 closed enough that the menu now spans the full priority stack. Each §4.1 alternative has a different priority-fit:

| §4.1 alternative | Primary priority | Secondary |
|---|---|---|
| (i) Second research-target | #2 engineering capability | #5 profitability if it earns the gate |
| (ii) Micro_live promotion | #5 profitability validation | #1 if operational evidence holds |
| (iii) Concurrent multi-strategy | #2 engineering capability | #1 if account-scope holds in practice |
| (iv) Desktop GUI | #3 accessibility | #4 shareability |
| (v) Distributable installer | #4 shareability | #3 accessibility |
| (vi) Cleanup-only | #1 trustworthy | — |

A feature that improves shareability at the cost of trustworthiness still loses. A feature that improves accessibility without weakening anything above it is a strong candidate. This is the same discipline Phase 2 used — Phase 3 just has more items where the discipline gets exercised.

Draft goal candidates the operator can shape from:

- **G1. Preserve C-2 honest-signal regression, ADR 0024 account-scope discipline, and P-1 walk-forward labeling.** Anything added in Phase 3 must not weaken these. Equivalent to Phase 2's G1 — the trustworthiness floor.
- **G2. Decide what Phase 3 actually scopes** (§4.1) — analog of Phase 2 §4.1, now with six alternatives instead of four.
- **G3. Decide the live-trading boundary** (§4.2). Phase 3 may keep ADR 0004 in force, supersede it for micro_live only, or supersede it for all stages. Each move requires its own ADR.
- **G4. Define exit criteria** (§5) analogous to Phase 1's six SCs and Phase 2's two-of-six narrowed. Phase 3's count depends on §4.1.
- **G5. Add per-feature ADRs for any architectural seam crossed.** Live boundary, GUI runtime model, installer distribution model, second research family, daemon/supervisor runtime if §4.1.iii implies one — each gets its own ADR.

These are draft goals. The operator may add, drop, reorder, or refine.

---

## 3. Carry List

**Empty per [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md).** Phase 2 closed CI-1, CI-2, CS-1, P-1, and C-2. Phase 3 starts from zero outstanding §3 items.

If anything surfaces during Phase 3 planning or execution, it gets added here with a stable identifier (e.g., `XX-1`, `XX-2`) following the same convention as Phase 2 §3 (`CI-` for runner internals, `CS-` for cross-strategy, `P-` for presentation, etc., or new prefixes for new categories).

---

## 4. Open Phase 3 Scope Questions

These are the questions the operator owns. Each is framed with alternatives; **none has a recommended answer here**.

### 4.1 ~~What does Phase 3 actually scope?~~ — **Decided 2026-05-05: (i) second research-target + (iii) concurrent multi-strategy (engineering-led bundle)**

Six candidates were considered: (i) second research-target strategy, (ii) micro_live promotion, (iii) concurrent multi-strategy execution, (iv) desktop GUI, (v) distributable installer, (vi) cleanup-only. The full menu with priority-fit and dependency analysis is preserved in this file's git history at commit `6dace51`.

**Decision:** Phase 3 takes up **(i) + (iii) — engineering-led bundle**. A second research-target strategy moves through the full lifecycle alongside meanrev; both run concurrently in paper. The live boundary stays locked through Phase 3 (see §4.2). Rationale: FOUNDER_INTENT priority #1 (trustworthy) is closed via Phase 2's invariants; priority #2 (engineering capability) is the natural next pull; live-boundary work is structurally premature without a strategy that has earned the gate; GUI/installer is larger than a single phase should swallow without first proving the harness scales to two research threads. The (i)+(iii) bundle is the smallest scope that simultaneously exercises *"harness handles a second research thread"* and *"[ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md) account-scope discipline holds in practice."* See §4.4 for the family choice for (i); §4.2 for the live-boundary decision; §4.5 / §4.6 / §4.7 are not in scope (their conditional triggers do not fire under (i)+(iii)).

Alternatives **(ii)** micro_live, **(iv)** desktop GUI, **(v)** distributable installer, **(vi)** cleanup-only were considered and deferred. They remain Phase 4 candidates.

### 4.2 ~~What's the live-trading boundary for Phase 3?~~ — **Decided 2026-05-05: (a) live remains locked**

[ADR 0004](adr/0004-paper-only-phase-one.md) stays in force through Phase 3. Implied by §4.1 = (i)+(iii): neither a second research-target's lifecycle trip nor concurrent paper execution requires the live boundary to move. The (i)+(iii) bundle's evidence is paper-only; live promotion belongs to Phase 4 once a research-target has earned the gate.

Alternatives **(b)** unlock micro_live only and **(c)** unlock all stages were considered and deferred.

### 4.3 What's the equivalent of Phase 2's §7 floor?

Same Phase 2 floor table, with each "Open question" now tracked through Phase 2's actual resolution. Phase 3's defaults reflect what's actually on the table now vs what stays out.

| Item | Phase 2 status | Default Phase 3 status |
|---|---|---|
| Concurrent multi-strategy execution | Open, deferred (CS-1 doc-only resolution) | **Open question** (§4.1.iii) |
| Daemon / supervisor runtime | Out by default | **Conditional** (depends on §4.1.iii / §4.1.iv implementation choice) |
| Crypto / alternative assets | Out by default | Out by default |
| ML-driven signals | Out by default | Out by default |
| Alternative / sentiment data | Out by default | Out by default |
| Desktop GUI | Open question, not opened | **Open question** (§4.1.iv) |
| Alternative brokers | Out by default | Out by default |
| Distributable installer | Open question, not opened | **Open question** (§4.1.v) |
| Live trading | Open question, resolved as locked | **Open question** (§4.2) |
| HFT / low-latency trading | Out | Out (matches Phase 1 rationale) |
| Multi-user collaboration | Out | Out (Milodex is a personal tool) |
| Walk-forward parameter search | Out per [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md) | Out (search procedure conflicts with OOS validation) |
| Options / derivatives | Out | Out by default |
| Cloud-native distributed architecture | Out | Out by default |
| Social / marketplace features | Out | Out by default |
| Auto-resume after kill switch | Out per [ADR 0005](adr/0005-kill-switch-manual-reset.md) | Out (manual reset is the contract) |
| Unattended overnight running | Implicit (foreground process per [ADR 0012](adr/0012-runtime-and-dual-stop.md)) | Out by default |
| Auto-discovered universe expansion | Out per VISION research discipline | Out by default |

Items marked **Open question** are the ones the operator should explicitly decide; **Conditional** items become open if a related §4.1 alternative is chosen; the rest stay floor unless a specific case is made.

### 4.4 ~~If §4.1.i, which research-target family is next?~~ — **Decided 2026-05-05: momentum (`momentum.daily.tsmom.curated_largecap.v1`)**

**Decision:** Time-series momentum (TSMOM) on the same Phase 1 curated large-cap universe meanrev uses. Family identifier `momentum`; full normative spec lives in [strategy-families.md §Family: momentum](strategy-families.md#family-momentum--daily-time-series-momentum-swing). Strategy ID `momentum.daily.tsmom.curated_largecap.v1`. Daily swing tempo, cross-sectional ranked entry, regime-filtered, equal-notional sized — mirrors meanrev's structural shape so the test is *"does the harness carry a second research thread?"* rather than *"does new architecture land?"*

Rationale: Momentum is the cleanest statistical contrast to meanrev's reversion (different market behavior exploited per VISION's "idea vs. tuning" rule). Daily swing tempo keeps it within the existing tempo lane. No new architectural seam — single-leg positions, daily timeframe, long-only, curated universe, same Strategy ABC contract. Pairs/cointegration would have introduced multi-leg position support (a new architectural decision per [strategy-families.md "Adding a New Family"](strategy-families.md#adding-a-new-family)'s ADR-required clause), exceeding Phase 3 scope discipline. Per [strategy-families.md](strategy-families.md)'s convention, a new family that does not introduce a new architectural decision does not require its own ADR — the family section is the canonical spec.

Alternatives **breakout** (close to momentum but adds a separate volatility-filter parameter surface), **pairs/cointegration** (multi-leg, new architectural seam — too big for Phase 3), and **a second mean-reversion variant** (would risk being tuning rather than a new idea per VISION's research discipline) were considered and deferred.

### 4.5 If §4.1.ii or §4.2.b, which strategy promotes to micro_live?

(New question — Phase 2 didn't cross the live boundary.)

- **(α) Regime SPY/SHY (lifecycle-proof).** Per VISION, regime is lifecycle-exempt because a 200-DMA regime strategy can't produce 30 trades or a Sharpe-meaningful sample (1–3 trades per year typical). Its micro_live evidence has to be operational: orders place at the right times, kill-switch fires correctly, explanations are reviewable, manifest drift detection holds. Strongest candidate by current evidence; weakest by financial-edge claim. Real risk: a regime strategy in micro_live may run for a year with zero trades, in which case the "evidence" is "nothing happened" — which is operationally informative but financially uninformative.

- **(β) Meanrev RSI(2) pullback.** Refused paper promotion at backtest → paper because walk-forward OOS-aggregate Sharpe 0.327 < 0.50. Cannot promote to micro_live without first earning paper. Three options: (a) re-tune with discipline (per VISION's research rules — declare the search space first, OOS-check every round), risking parameter overfitting if not careful; (b) replace the parameterization with a meaningfully-distinct mean-reversion variant per §4.4 last bullet and re-run the lifecycle; (c) accept that meanrev is a truthful failure and not promote it. Phase 3 may inherit (c) cleanly — the honest-signal property holds.

- **(γ) New research-target produced by §4.1.i.** Conditional on Phase 3 producing a new research-target that earns the gate. Phase 3 is structurally too early for this unless §4.1 includes (i) and the new target produces evidence quickly.

- **(δ) None.** §4.2 = (a). Live boundary stays locked through Phase 3. Most conservative; strongest constraint on §4.1 (rules out (ii)).

### 4.6 If §4.1.iv, which GUI runtime model?

(New question — Phase 2 didn't open GUI work.)

[VISION.md "Interface"](VISION.md) names two candidates:

- **PySide6 (Qt bindings, Python).** Single-language stack. Bundles natively (single .exe via PyInstaller or Briefcase). Ecosystem aligns with the existing Python codebase — direct in-process access to `EventStore`, `ExecutionService`, `StrategyRunner`. Compromise on look-and-feel: Qt-styled, not as polished as web-stack defaults. Smaller code-surface delta; faster path to a working GUI.
- **Tauri (JS/TS frontend, Rust shell).** Modern, polished look-and-feel out of the box. Web-stack defaults are the expected aesthetic for portfolio-worthy projects (FOUNDER_INTENT #4). Cross-platform. Two-language stack: TS/JS frontend, plus the existing Python core would need either an HTTP/JSON-RPC bridge or a Tauri sidecar. Adds operational complexity. Larger code-surface delta; slower path to a working GUI but higher polish ceiling.

Sub-question if Tauri: how does the Python core run? (a) sidecar process Tauri spawns, (b) separate operator-managed process the GUI talks to over localhost, (c) full rewrite of the strategy runtime in Rust (out of scope for Phase 3). New ADR for the chosen model.

### 4.7 If §4.1.v, what's the distribution model?

(New question — Phase 2 didn't open installer work.)

- **(α) GitHub clone + `pip install -e .`** — what currently works for the developer-operator. No installer. Friend has to be technical enough to clone, install Python, set up venv, write a `.env`, run commands. Smallest distribution code surface. Highest friction for non-technical friends. Defensible for "shareable with technical friends" but not for "polished portfolio piece."
- **(β) Single-file binary (PyInstaller / Briefcase / Nuitka).** Pre-bundled Python interpreter + dependencies. Friend runs an `.exe`. Larger artifact (50–100MB typical). Compatible with §4.6 PySide6 GUI naturally; works with CLI-only too. Mid-friction.
- **(γ) Native installer (MSI / NSIS / WiX).** Full Windows installer with start-menu entry, uninstall flow, possible auto-update. More polished, more code. Requires code signing (cost, certificate management) for Windows SmartScreen-trusted distribution. Highest polish; highest setup cost.
- **(δ) Containerized distribution (Docker).** For technical friends with Docker installed. Sidesteps Python-version-mismatch issues. Doesn't fit the "polished desktop product" intent in FOUNDER_INTENT — a Docker artifact is operationally a developer tool, not a desktop product. Fallback option if (α–γ) all stall.

New ADR for the chosen model.

---

## 5. Exit Criteria

Phase 1 had six SCs simultaneous-when-true. Phase 2 narrowed to two. With §4.1 = (i)+(iii) decided, the operative set is **three criteria**: **C-1, C-2, C-6**. C-3, C-4, C-5 are not in scope (their §4.1 trigger alternatives were not chosen) and remain Phase 4 candidates.

- **C-1.** *(operative — §4.1.i chosen)* A second research-target strategy moves through the full lifecycle — define → frozen manifest → backtest → walk-forward → trust report — with the same evidence shape as meanrev's Phase 1 trip. Walk-forward report uses the per-metric `(OOS)` labeling discipline P-1 added. The strategy either earns the gate (Sharpe > 0.5, max drawdown < 15%, ≥30 trades) or is refused honestly per the same C-2 mechanism Phase 2 locked. Family choice: momentum per §4.4; strategy ID `momentum.daily.tsmom.curated_largecap.v1`.

- **C-2.** *(operative — §4.1.iii chosen — architecturally satisfied 2026-05-05)* Concurrent multi-strategy paper execution. Three sub-criteria:
  - **C-2a (model documented).** [ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) lands the per-process supervisor model: each strategy runs in its own foreground process via `milodex strategy run`, ADR 0012's runtime model is preserved unchanged, ADR 0024's account-scoped enforcement is the safety mechanism. Single-process supervisor was rejected as superseding ADR 0012 for marginal benefit.
  - **C-2b (account-scoped enforcement exercised in practice).** Already evidenced in [Phase 2 CS-1](PHASE2_PLANNING.md)'s session `a140da6c-a50d-4bdb-98e9-fc2b20e2ed1f` — regime started against a paper account that still held meanrev's three leftover positions, every regime cycle was refused with `max_concurrent_positions_exceeded`, the risk evaluator's account-scoped count behaved correctly. ADR 0024 codified that behavior; ADR 0026 confirms it as the binding semantics for concurrent execution.
  - **C-2c (≥30 paper-trading-day runtime evidence).** Operator-side ongoing activity — cannot be manufactured in commit time. Accumulates naturally as the operator runs the per-process pattern in paper. Same shape as Phase 1 SC-3's "first fire" evidence: Phase 3 closes on architectural sufficiency, runtime evidence accumulates after.

- **C-3.** *(deferred — §4.1.ii not chosen)* Micro_live promotion. Phase 4 candidate.

- **C-4.** *(deferred — §4.1.iv not chosen)* Desktop GUI. Phase 4 candidate.

- **C-5.** *(deferred — §4.1.v not chosen)* Distributable installer. Phase 4 candidate.

- **C-6.** *(always — preserves Phase 2 invariants)* All Phase 2 invariants preserved end-to-end:
  - C-2 (honest-signal) regression tests still green: `test_gate_refuses_meanrev_shape_evidence_on_sharpe_alone` and `test_promotion_promote_refuses_meanrev_shape_evidence_through_cli` both pass on Phase 3's last commit.
  - ADR 0024 account-scoped position caps still enforced — no code path counts positions per-strategy.
  - P-1 walk-forward labeling still rendering on every walk-forward run — `(OOS)` per-metric tags present, `sortino_ratio=None` for walk-forward.
  - No silent removal or relaxation of any Phase 1 or Phase 2 ADR via configuration. Removals require a new ADR explicitly superseding the prior one.

Phase 3 ends when **C-1 + C-2 + C-6 are simultaneously true**, an ADR closes Phase 3 analogous to [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md) / [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md) (planned ADR 0027), and Phase 4 planning is authorized.

### Deferred candidates (stay deferred unless §4 opens them)

- **C-7.** Full live trading (any strategy from micro_live to live). Tied to §4.2.c — structurally premature.
- **C-8.** Auto-discovered universe expansion. Currently out per VISION research discipline; revisitable in a later phase.
- **C-9.** Daemon / supervisor runtime model (background-running, unattended). Conflicts with VISION's "manually-invoked, long-running foreground process" decision and ADR 0012's runtime model. Would require its own ADR if §4.1.iii / §4.1.iv push toward it.
- **C-10.** Alternative broker integration. VISION names this as Phase 2+ optionality; Phase 3 may inherit without resolving.
- **C-11.** Sentiment / alternative data, ML-driven signals, premium data sources. VISION names "scale if justified"; Phase 3 may inherit without resolving.

---

## 6. What This Document Is *Not*

- **Not a commitment.** Until the operator approves a §4 scope decision, this document is a working brief.
- **Not a substitute for ADRs.** Any decision in §4 that crosses an architectural seam (live trading, GUI runtime, installer model, daemon/supervisor, second broker, multi-leg positions) requires its own ADR.
- **Not the final shape of Phase 3.** Phase 1 and Phase 2 both evolved continuously through their work; Phase 3's planning should expect the same.
- **Not a reframe of profitability.** Per [FOUNDER_INTENT.md](FOUNDER_INTENT.md), profit is validation, not purpose. Phase 3's success is whether the platform stays trustworthy as it expands its surface — not whether any strategy makes money.
- **Not a sequencing plan.** §3 (empty) and §5 do not yet have an "ordered work breakdown" analogous to [ROADMAP_PHASE1.md §8](ROADMAP_PHASE1.md#8-ordered-work-breakdown-actionable-sequence). Sequencing follows scope decisions, not the other way around.
- **Not an authorization for live capital.** Phase 3 may unlock micro_live (§4.2.b); it does not pre-authorize a live capital transition. The autonomy-boundary actions in [VISION.md §Autonomy Boundary](VISION.md#autonomy-boundary) (capital allocation, kill-switch reset, live promotion, broker permission) remain human-gated regardless of phase.

---

## 7. What's Explicitly Still Out (Phase 3 Floor)

These remain out of scope for Phase 3 unless a separate ADR opens them, even if §4 resolves expansively:

- **High-frequency / low-latency trading** — incompatible with daily swing tempo; same Phase 1/2 rationale.
- **Multi-user collaboration as a first-class system requirement** — Milodex remains a personal tool. "Shareable with friends" stays at the packaging-and-defaults level (per §4.1.v), not multi-tenancy.
- **Fully autonomous live trading without human gating** — conflicts with [VISION.md §Autonomy Boundary](VISION.md#autonomy-boundary). Even if §4.2 opens micro_live, the autonomy-boundary actions stay human-gated.
- **Walk-forward parameter search** — conflicts with [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md)'s "evaluate fixed parameters, do not fit them" decision.
- **Cloud-native distributed architecture** — Milodex stays local-first.
- **Options / derivatives infrastructure** — [ADR 0016](adr/0016-phase1-instrument-whitelist.md) extends naturally; opening it is a separate ADR.
- **AI-generated strategy invention without strict human review** — research-discipline rules in [VISION.md §Research Discipline](VISION.md#research-discipline) remain binding.
- **Social / marketplace / subscription-platform features** — not on the table.
- **Auto-resume after kill switch** — manual reset per [ADR 0005](adr/0005-kill-switch-manual-reset.md); never relaxed.
- **Unattended overnight / multi-day continuous running** — out unless §4.1.iii / §4.1.iv push toward a daemon model and a new ADR supersedes [ADR 0012](adr/0012-runtime-and-dual-stop.md).
- **Premium data sources without justified edge improvement** — per VISION's "scale if justified" rule.
- **Auto-discovered universe** — curated universe per VISION's research discipline.
- **Re-tuning meanrev as if it were a new idea** — per VISION's "idea versus tuning" rule, RSI 8 vs. RSI 10 is tuning, not a new strategy. If §4.5(β-a) is chosen, the search space must be declared first and OOS-checked.

---

## 8. Tracking Conventions

(Same conventions as [PHASE2_PLANNING.md §8](PHASE2_PLANNING.md). Reproduced for self-containedness.)

- Each new carry item gets a stable identifier (e.g., `XC-1` for Phase 3 cleanup, `LV-1` for live-boundary work).
- §4 scope decisions are decisions, not work items. Each one's resolution becomes a §1-style anchor section once chosen, and any architectural seam it crosses gets its own ADR.
- This doc evolves until Phase 3 has stable scope. At that point it either becomes `ROADMAP_PHASE3.md` (mirroring Phase 1's pattern) with §4 / §5 frozen and §3 / §7 carried forward, or remains as planning context alongside an ordered roadmap. The operator decides which.
- Resolved §4 questions are struck through (as Phase 2's §4.1 / §4.2 were) with the decided option called out inline.
- When an item is resolved, the resolution lands as: a small ADR (if it crosses an architectural seam), a checked box here with a commit hash (if mechanical), or both.
- Per [ROADMAP_PHASE1.md §10](ROADMAP_PHASE1.md#10-tracking-this-roadmap)'s pattern: items are checked off as completed with linked merge commits. Reopening only happens if the definition of done regresses — never quietly un-checked.

---

## 9. Immediate Next Steps

§4 is decided. Phase 3 is now in execution mode. The operative set is C-1 + C-2 + C-6. Concrete sequence:

1. **Add momentum family spec** to [strategy-families.md](strategy-families.md). *(Done 2026-05-05 — see §Family: momentum.)*
2. **TDD the momentum strategy.** Golden-output test mirroring meanrev's pattern → `MomentumDailyTsmomStrategy` class implementing the family spec → strategy parameter validation tests → cross-sectional ranking tests → exit-rule tests (momentum_exit, max_hold, stop_loss).
3. **Add config + universe + freeze manifest.** New `configs/momentum_daily_tsmom_v1.yaml`; existing `universe.phase1.curated.v1` is the universe; `milodex promotion freeze momentum.daily.tsmom.curated_largecap.v1` lifecycle-tracks the new strategy at the `backtest` stage.
4. **Run walk-forward backtest.** `milodex backtest run momentum.daily.tsmom.curated_largecap.v1 --start 2015-01-01 --end 2024-12-31 --walk-forward 4`. Capture OOS-aggregate Sharpe / drawdown / trade count. Verify P-1 per-metric `(OOS)` labeling holds in the trust report. Whether the gate accepts or refuses is honest signal either way — C-2's regression tests apply equally.
5. **Land [ADR 0026]** for the concurrency model. Decision space: per-process supervisor (smaller change, preserves [ADR 0012](adr/0012-runtime-and-dual-stop.md)) vs single-process supervisor (bigger seam, would supersede ADR 0012). Per-process is the engineering-judgment default unless a specific case for single-process emerges from execution.
6. **Exercise concurrent multi-strategy paper execution.** Regime + meanrev (or regime + momentum, depending on what survives the walk-forward gate) running simultaneously for ≥30 paper trading days. Verify account-scoped `concurrent_positions` cap behaves correctly under concurrent demand.
7. **Close Phase 3.** [ADR 0027] analogous to ADR 0023 / 0025. Open `PHASE4_PLANNING.md`.

The C-6 always-on invariant gates every step: C-2 honest-signal regression tests, ADR 0024 account-scope, and P-1 walk-forward labeling must remain green at every commit. Any slip is a stop-and-fix, not a continue-with-known-broken.
