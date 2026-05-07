# Phase 4 Planning

> **Phase 4 was formally closed on 2026-05-06 via [ADR 0031](adr/0031-phase-4-is-closed-and-phase-5-may-open.md).** This document is now a historical record. C-6 (per-strategy attribution) and C-7 (prior-phase invariants preserved) were satisfied; the §4.1 (g) cleanup bundle (doc drift, test audit, test gap closure, backtest sandbox semantics + `--parallel` UX) was fully delivered. The §4.1 deferred candidates — (a) micro_live, (b) GUI, (c) installer, (d) third research-target, (e) re-tune — carry forward as Phase 5 menu items, not commitments. New planning belongs in `PHASE5_PLANNING.md`.

**Status:** Opened 2026-05-05 as the prerequisite-mandated planning artifact per [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md). Filename `PHASE4_PLANNING.md` is chosen deliberately over `ROADMAP_PHASE4.md` because Phase 4 has no committed scope at opening — same convention Phase 2 and Phase 3 used. **§4 is open.** No scope decided. The §4.1 menu carries forward Phase 3 §4.1's deferred alternatives ((ii) micro_live, (iv) GUI, (v) installer, (vi) cleanup-only) plus three new candidates that surfaced during Phase 3.

**Predecessors:** [PHASE3_PLANNING.md](PHASE3_PLANNING.md) (closed historical record), [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md) (authorizes this doc), [ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) (Phase 3's named architectural artifact, foundation for any further concurrency work), [VISION.md](VISION.md), [FOUNDER_INTENT.md](FOUNDER_INTENT.md), [SRS.md](SRS.md).

---

## 1. What Phase 3 Left Behind

Phase 3 closed against three exit criteria — C-1 (second research-target through full lifecycle), C-2 (concurrent multi-strategy architecturally satisfied), and C-6 (Phase 2 invariants preserved) — all evidenced in [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md). The full close-out narrative lives there; this section captures only what carries into Phase 4.

**Phase 4 scope is now locked.** On 2026-05-06, the operator resolved §4.1 as **(f) + (g) — per-strategy attribution + cleanup-only** and §4.2 as **(a) — live remains locked**. [ADR 0028](adr/0028-phase-4-scope-closes-as-cleanup-and-attribution.md) is the authoritative record of that decision. All further Phase 4 work is bounded by this scope; options (a) through (e) are deferred to the Phase 5+ menu.

The single most load-bearing Phase 3 result was **the honest-signal property generalizing across two strategies**. Phase 1 produced a truthful failure on meanrev (Sharpe 0.327 < 0.5); Phase 2 turned that into a regression test; Phase 3 ran a different strategy (momentum.daily.tsmom, Sharpe 0.06, max drawdown 15.37%) through the same gate without per-strategy adaptation, and the gate refused for an *additionally* honest reason. The thesis now reads: *the platform refuses to lie about any research-target whose evidence does not earn the gate*. **Anything Phase 4 adds must not weaken this property** — same binding constraint Phase 2 and Phase 3 inherited from Phase 1, now demonstrated across two strategies, not one.

Phase 3 also left:

- An **empty §3 carry list** per [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md). Phase 4 starts from zero outstanding cleanup work.
- A **structural lock on live trading** per [ADR 0004](adr/0004-paper-only-phase-one.md). [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md) explicitly does not relax it. Phase 4 may revisit, but only via a new ADR superseding 0004.
- An **account-scoped position-cap discipline** ([ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md)) and a **per-process supervisor concurrency model** ([ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md)). Concurrent multi-strategy paper execution is now an exercised pattern with a documented supervisor model.
- A **walk-forward labeling discipline** (P-1) that renders automatically for new strategies. Demonstrated in Phase 3 — momentum's walk-forward report rendered with `(OOS)` per-metric labels and `Sortino: n/a` without per-strategy code.
- **Two strategies with truthful gate refusals**: meanrev (Sharpe 0.327, refused 2026-04-26) and momentum.daily.tsmom (Sharpe 0.06 + max drawdown 15.37%, refused 2026-05-05). Both at `backtest` stage. Whether either advances in Phase 4 is a §4 question (and the answer cannot be "promote them as-is" — neither has earned the gate).
- A **§4 deferred menu** that becomes the Phase 4 candidate set. Phase 3 §4.1's deferred (ii)/(iv)/(v)/(vi) carry forward; Phase 4 also adds three new candidates that surfaced during Phase 3 execution (third research-target, re-tune within research discipline, per-strategy position attribution).

---

## 2. Phase 4 Goals (Anchor: FOUNDER_INTENT priority order)

[FOUNDER_INTENT.md](FOUNDER_INTENT.md) priority order is unchanged across phases:

1. **Trustworthy.** Build something real, functional, and trustworthy.
2. **Engineering capability.** Demonstrate strong AI-assisted engineering.
3. **Accessibility.** Make the system accessible and easy to use.
4. **Shareability.** Make it portfolio-worthy.
5. **Profitability.** Pursue profit as validation of effectiveness.

What changes for Phase 4 vs Phase 3: the platform now has *two* strategies that have honestly failed the gate. The harness is demonstrably reusable for new research-targets; concurrent execution is documented. Priority #1 (trustworthy) is firmly closed. Priority #2 (engineering capability) is firmly demonstrated. The natural next pulls are priority #3 (accessibility — first non-developer-facing surface) and priority #4 (shareability — friend-installable distribution).

Priority #5 (profitability) remains structurally premature: it is unlocked only by a strategy that has earned the gate, and Phase 4 starts with zero such strategies. Phase 4 may try to produce one (via §4.1.d third research-target or §4.1.e disciplined re-tuning), but a "profitability phase" without first solving for "an edge that exists" would be the kind of premature optimization VISION explicitly warns against.

Draft goal candidates the operator can shape from:

- **G1.** Preserve C-2 honest-signal regression, ADR 0024 account-scope discipline, ADR 0026 per-process supervisor pattern, and P-1 walk-forward labeling. Anything added in Phase 4 must not weaken these. Equivalent to Phase 3's G1 — the trustworthiness-and-engineering floor.
- **G2.** Decide what Phase 4 actually scopes (§4.1) — six candidates this phase, vs Phase 3's six and Phase 2's four.
- **G3.** Decide the live-trading boundary (§4.2). Phase 4 may keep it locked, open micro_live only, or unlock all stages. Each move requires its own ADR.
- **G4.** Define exit criteria. Phase 1 had six SCs, Phase 2 narrowed to two, Phase 3 narrowed to three. Phase 4's count depends on §4.1.
- **G5.** Add per-feature ADRs for any architectural seam crossed. GUI runtime model, installer distribution model, third research family (only if it requires new architecture per [strategy-families.md "Adding a New Family"](strategy-families.md#adding-a-new-family)), per-strategy position attribution, daemon/supervisor — each gets its own ADR.

These are draft goals. The operator may add, drop, reorder, or refine.

---

## 3. Carry List

**Empty per [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md).** Phase 3 closed C-1, C-2, C-6 with zero carry items. Phase 4 starts from zero outstanding §3 items.

If anything surfaces during Phase 4 planning or execution, it gets added here with a stable identifier following the convention from Phase 2 §3.

---

## 4. Open Phase 4 Scope Questions

These are the questions the operator owns. Each is framed with alternatives; **none has a recommended answer here**.

### 4.1 ~~What does Phase 4 actually scope?~~ — **Decided 2026-05-06: (f) + (g) — per-strategy attribution + cleanup-only**

> **Decision ([ADR 0028](adr/0028-phase-4-scope-closes-as-cleanup-and-attribution.md)):** Phase 4 scopes as **(f) per-strategy position attribution at the risk layer** plus **(g) cleanup-only**. This is mechanics-firming, not feature-adding. The operator named a concrete operational pain — inability to quickly tell what strategies exist, where they stand, what they've made or lost — and concluded that the right fix is to firm the underlying mechanics before layering any UI on top. Per FOUNDER_INTENT priority #1 (trustworthy), a UI built on unclear mechanics roughly doubles debugging cost by making anomalies ambiguous between display logic and data layer. Options (a) through (e) are deferred to the Phase 5+ menu. The (f) work extends [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md); ADR 0029 will articulate its semantics. The (g) cleanup bundle's backtest-sandbox item is articulated in ADR 0030.

Seven candidates. Phase 3 §4.1's deferred items carry forward; three new candidates surfaced during Phase 3 execution. *(Historical record — scope decided above.)*

**(a) Micro_live promotion of one strategy** *(Phase 3 §4.1.ii deferred → here)*. Open the live boundary at micro_live only. Currently structurally constrained: regime is the only strategy that *can* promote (lifecycle-exempt — operational evidence, not statistical), since both meanrev and momentum failed the gate. Tied to §4.2 and §4.5.

**(b) Desktop GUI** *(Phase 3 §4.1.iv deferred → here)*. First non-CLI interface. FOUNDER_INTENT #3 (accessibility) + VISION's "polished, smooth, surprisingly easy to start." Largest code-surface candidate. Tied to §4.6 (PySide6 vs Tauri runtime model).

**(c) Distributable installer / clone-and-run path** *(Phase 3 §4.1.v deferred → here)*. The friend-installable distribution per VISION. FOUNDER_INTENT #4 (shareability). Tied to §4.7 (distribution model).

**(d) Third research-target strategy** *(new — Phase 3 surfaced)*. After meanrev and momentum both failed the gate, what's the next family to try? Candidates: breakout, pairs/cointegration, cross-sectional ranking with a different metric, calendar-based effects. The "harness scales" claim is well-evidenced after Phase 3; Phase 4's question would be whether *any* family on this curated universe earns the gate, not whether the harness can carry it. Tied to §4.4.

**(e) Disciplined re-tuning of meanrev or momentum** *(new — Phase 3 surfaced)*. Per VISION's "idea vs. tuning" rule, a re-tune is *only* legitimate if (i) the search space is declared in advance, (ii) every tuning round is OOS-checked, (iii) "magic parameter islands" are treated as fragility, not edge. Re-tuning is research, not engineering — the gate refuses if the OOS evidence doesn't pass, regardless of how clean the in-sample looks. Risk: it's easy to mistake tuning for idea generation. Tied to §4.4.

**(f) Per-strategy position attribution at the risk layer** *(new — surfaced via ADR 0026)*. [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md)'s deferred option (a). Build a strategy-attributed `concurrent_positions` check so each strategy's cap binds independently. Requires position-to-strategy reconciliation against broker positions (which carry no strategy tag). FOUNDER_INTENT #2 (engineering capability) — significant code surface. Compatible with (a)/(b)/(c)/(d)/(e); not required by any.

**(g) Cleanup-only** *(Phase 3 §4.1.vi → here)*. Like Phase 2's resolved (iv) and Phase 3's not-chosen (vi): no new system goals, just whatever surfaces during Phase 4 running. Phase 3 surfaced no carry items, so this option is again nearly-empty unless extended runtime exposes new gaps.

**Bundle shapes worth naming explicitly:**

- **(b)+(c) bundle.** Ship a GUI inside an installer. Highest combined FOUNDER_INTENT alignment (#3 + #4). No new strategies, no live-boundary movement. Largest code-surface delta. Good fit if the priority signal is "make Milodex usable by someone who isn't the developer."
- **(a) alone.** Most conservative live-boundary movement. Requires §4.5 to resolve which strategy crosses; per Phase 3's evidence, only regime (lifecycle-exempt) is currently eligible. Smallest scope, biggest stakes.
- **(d) alone, or (d)+(e).** Continued research focus. Risk: a third strategy may *also* fail the gate (which is honest signal but doesn't move the platform forward in a different way than Phase 3 already did). Engineering question becomes "is the universe wrong?" or "is daily-swing the wrong tempo for these families?" — both are research questions, not engineering ones.
- **(f) alone or paired with (a).** Per-strategy attribution as the runway for live trading. Only valuable if Phase 4 (or 5+) opens micro_live and operates more than one strategy concurrently with real capital — otherwise account-scoped enforcement is sufficient.

Each option (or bundle) gets a Phase 4 ADR if it crosses an architectural seam. None is pre-recommended here.

### 4.2 ~~What's the live-trading boundary for Phase 4?~~ — **Decided 2026-05-06: (a) live remains locked**

> **Decision ([ADR 0028](adr/0028-phase-4-scope-closes-as-cleanup-and-attribution.md)):** Phase 4 §4.2 = **(a) — live remains locked**. [ADR 0004](adr/0004-paper-only-phase-one.md) is unchanged and stays in force through Phase 4. Neither (f) nor (g) requires the live boundary to move. Live unlocking is a Phase 5+ question and requires a new ADR superseding ADR 0004.

[ADR 0004](adr/0004-paper-only-phase-one.md) is still in force per [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md). Phase 4 may revisit, but only via a new ADR that supersedes 0004. *(Historical record — boundary decided above.)*

**(a) Live remains locked.** Phase 4 stays paper-only. Mirrors Phase 2 and Phase 3's resolution. Compatible with §4.1 (b), (c), (d), (e), (f), (g). Most conservative.

**(b) Unlock micro_live only.** Open the first live stage but not full-scale live. Compatible with §4.1.a. Requires the new ADR plus §4.5 (which strategy promotes — currently only regime is eligible). Auto-restart and unattended live trading remain out — kill-switch reset stays manual per [ADR 0005](adr/0005-kill-switch-manual-reset.md).

**(c) Unlock all stages.** Full live trading authorized. **Structurally premature** — no strategy has produced micro_live evidence. Reserved for a later phase after micro_live evidence accumulates.

### 4.3 What's the equivalent of Phase 3's §7 floor?

Same Phase 3 floor table, with each "Open question" updated for Phase 4. The default remains: **everything still locked unless explicitly opened.**

| Item | Phase 3 status | Default Phase 4 status |
|---|---|---|
| Concurrent multi-strategy execution | Open, decided as (i)+(iii) per-process | **In force** (per [ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md)) |
| Daemon / supervisor runtime | Conditional, not opened | Out by default (Phase 4 not opening it unless §4.1 implies) |
| Crypto / alternative assets | Out by default | Out by default |
| ML-driven signals | Out by default | Out by default |
| Alternative / sentiment data | Out by default | Out by default |
| Desktop GUI | Open, deferred | **Open question** (§4.1.b) |
| Alternative brokers | Out by default | Out by default |
| Distributable installer | Open, deferred | **Open question** (§4.1.c) |
| Live trading | Open, decided as locked | **Open question** (§4.2) |
| HFT / low-latency trading | Out | Out |
| Multi-user collaboration | Out | Out |
| Walk-forward parameter search | Out per [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md) | Out |
| Options / derivatives | Out by default | Out by default |
| Cloud-native distributed architecture | Out | Out by default |
| Social / marketplace features | Out | Out by default |
| Auto-resume after kill switch | Out per [ADR 0005](adr/0005-kill-switch-manual-reset.md) | Out (manual reset is the contract) |
| Unattended overnight running | Out by default | Out by default |
| Auto-discovered universe expansion | Out per VISION research discipline | Out by default |
| Per-strategy position attribution at risk layer | Deferred per ADR 0026 | **Open question** (§4.1.f) |

### 4.4 If §4.1.d, which research-target family is next? If §4.1.e, which strategy is re-tuned?

(New conditional question — splits Phase 3 §4.4 into two paths since Phase 4 also has the re-tune option.)

**For (d) third research-target family:**
- **Breakout.** Volatility-filter dependency; close to momentum but adds a parameter surface. No new architectural seam (single-leg, daily, long-only).
- **Pairs / cointegration.** Different family entirely (cross-asset). Multi-leg positions = new architectural seam = new ADR required.
- **Cross-sectional ranking with different metric.** E.g., volatility-adjusted momentum, dispersion-based, value-tilted. Within-family-shape (cross-sectional rank) but different ranking concept. Could be a momentum variant or a new family depending on the entry concept.
- **Calendar-based effect.** E.g., turn-of-month, Federal Reserve announcement window. Different timing model = potentially new family per [strategy-families.md](strategy-families.md).

**For (e) disciplined re-tune:**
- **Meanrev.** Phase 1's evidence: Sharpe 0.327 over 4 OOS windows, fragile (single-window dependency). Tuning the parameter surface (RSI lookback, entry/exit thresholds, MA filter length) is *idea-space tuning*, not idea generation per VISION. Re-tune is legitimate only with declared search space + per-round OOS check.
- **Momentum.** Phase 3's evidence: Sharpe 0.06 over 4 OOS windows, also fragile (2/4 positive). Same discipline applies.
- **Both, separately.** Run disciplined re-tunes on both, accept whichever (if either) earns the gate.

### 4.5 If §4.1.a or §4.2.b, which strategy promotes to micro_live?

(Carries forward from Phase 3 §4.5 with one structural update: momentum has now also been evaluated and refused.)

- **(α) Regime SPY/SHY.** Lifecycle-exempt (operational evidence only). Strongest candidate by current evidence; weakest by financial-edge claim. Same caveats as Phase 3 §4.5.α.
- **(β) Meanrev RSI(2) pullback.** Refused at backtest → paper. Cannot promote to micro_live without first earning paper. Three sub-options: (a) disciplined re-tune (§4.1.e); (b) replace with a meaningfully-distinct meanrev variant; (c) accept as truthful failure.
- **(γ) Momentum.daily.tsmom.** Refused at backtest → paper. Same three sub-options as meanrev.
- **(δ) New research-target produced by §4.1.d.** Conditional on Phase 4 producing a winner.
- **(ε) None.** §4.2 = (a). Live boundary stays locked through Phase 4.

### 4.6 If §4.1.b, which GUI runtime model?

(Carries forward from Phase 3 §4.6.)

- **PySide6** — single-language Python stack, native bundling, faster path to working GUI, lower polish ceiling.
- **Tauri (JS/TS frontend, Rust shell)** — higher polish ceiling, two-language stack with bridge, slower path to working GUI.

New ADR for the chosen runtime is required.

### 4.7 If §4.1.c, what's the distribution model?

(Carries forward from Phase 3 §4.7.)

- **(α) GitHub clone + `pip install`** — developer-only, smallest distribution code.
- **(β) Single-file binary** (PyInstaller / Briefcase / Nuitka) — pre-bundled, mid-friction.
- **(γ) Native installer** (MSI / NSIS / WiX) — most polished, highest setup cost (signing).
- **(δ) Containerized distribution** (Docker) — for technical friends, doesn't fit "polished desktop product" intent.

New ADR for the chosen model is required.

---

## 5. Exit Criteria

Phase 4's count depends on §4.1. Each candidate criterion below is keyed to a §4.1 option; the operative subset narrows once §4.1 resolves.

- **C-1.** *(if §4.1.a / §4.2.b)* One strategy promoted to micro_live with a new ADR superseding [ADR 0004](adr/0004-paper-only-phase-one.md) in scope (micro_live only, not all stages). Operational evidence trail per Phase 3 §5 C-3 wording.

- **C-2.** *(if §4.1.b)* Desktop GUI ships with the daily-operator workflow ([VISION.md "Daily Operator Workflow"](VISION.md#daily-operator-workflow) eight steps). All eight steps work from the GUI without falling back to the CLI. GUI runtime ADR landed (§4.6 codified).

- **C-3.** *(if §4.1.c)* Distributable installer produces a working Milodex install on a clean machine. Friend-tested at least once on a non-developer machine. Distribution-model ADR landed (§4.7 codified).

- **C-4.** *(if §4.1.d)* A third research-target strategy moves through the full lifecycle with the same evidence shape as meanrev and momentum's prior trips. Either earns the gate or is refused honestly per the same C-2 mechanism Phase 2 locked. If refused: that's still C-4 satisfied (honest-signal property held across three strategies).

- **C-5.** *(if §4.1.e)* A disciplined re-tune of meanrev and/or momentum lands evidence — either gate-earning or gate-refused. The discipline (declared search space, per-round OOS check, fragility-as-fragility) is documented in a small ADR or planning artifact so the boundary between tuning and idea is auditable.

- **C-6.** *(if §4.1.f)* Per-strategy `concurrent_positions` attribution at the risk layer. Position-to-strategy reconciliation against broker positions. New ADR superseding [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md)'s "account-scoped is binding" semantics or extending them. Tests cover the new behavior; existing account-scoped enforcement remains as the floor.

- **C-7.** *(always — preserves Phase 1+2+3 invariants)* All prior-phase invariants preserved end-to-end:
  - C-2 (Phase 2 honest-signal) regression tests still green.
  - ADR 0024 account-scoped position caps still enforced.
  - ADR 0026 per-process supervisor still authoritative.
  - P-1 walk-forward labeling still rendering on every walk-forward run.
  - No silent removal or relaxation of any prior ADR via configuration. Removals require a new ADR explicitly superseding the prior one.

Phase 4 ends when the chosen subset is simultaneously true, an ADR closes Phase 4 analogous to [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md) / [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md) / [ADR 0027](adr/0027-phase-3-is-closed-and-phase-4-may-open.md), and Phase 5 planning is authorized.

### Deferred candidates (stay deferred unless §4 opens them)

- Full live trading promotion (any strategy from micro_live to live). Tied to §4.2.c — structurally premature.
- Auto-discovered universe expansion. Currently out per VISION research discipline.
- Daemon / supervisor runtime model. Conflicts with [ADR 0012](adr/0012-runtime-and-dual-stop.md) and [ADR 0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md).
- Alternative broker integration. VISION names this as Phase 2+ optionality.
- Sentiment / alternative data, ML-driven signals, premium data sources.

---

## 6. What This Document Is *Not*

- **Not a commitment.** Until §4 is resolved, this is a working brief.
- **Not a substitute for ADRs.** Any decision in §4 that crosses an architectural seam (live trading, GUI runtime, installer model, per-strategy attribution, daemon/supervisor, second broker, multi-leg positions) requires its own ADR.
- **Not the final shape of Phase 4.** Prior phases evolved continuously through their work; Phase 4's planning should expect the same.
- **Not a reframe of profitability.** Per [FOUNDER_INTENT.md](FOUNDER_INTENT.md), profit is validation, not purpose. Phase 4's success is whether the platform stays trustworthy and engineering-capable as it expands its surface — not whether any strategy makes money.
- **Not a sequencing plan.** Sequencing follows §4 decisions; this document opens those decisions, not their order.
- **Not an authorization for live capital.** Phase 4 may unlock micro_live (§4.2.b); it does not pre-authorize a live capital transition. The autonomy-boundary actions in [VISION.md §Autonomy Boundary](VISION.md#autonomy-boundary) (capital allocation, kill-switch reset, live promotion, broker permission) remain human-gated regardless of phase.

---

## 7. What's Explicitly Still Out (Phase 4 Floor)

These remain out of scope for Phase 4 unless a separate ADR opens them, even if §4 resolves expansively:

- **High-frequency / low-latency trading**
- **Multi-user collaboration as a first-class system requirement**
- **Fully autonomous live trading without human gating**
- **Walk-forward parameter search** (conflicts with [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md))
- **Cloud-native distributed architecture**
- **Options / derivatives**
- **AI-generated strategy invention without strict human review**
- **Social / marketplace features**
- **Auto-resume after kill switch** (manual reset per [ADR 0005](adr/0005-kill-switch-manual-reset.md))
- **Unattended overnight / multi-day continuous running** (out unless §4.1 implies)
- **Premium data sources without justified edge improvement**
- **Auto-discovered universe**
- **Re-tuning as if it were a new idea** (per VISION's "idea vs. tuning" rule)

---

## 8. Tracking Conventions

Same conventions as [PHASE2_PLANNING.md §8](PHASE2_PLANNING.md) and [PHASE3_PLANNING.md §8](PHASE3_PLANNING.md). Reproduced for self-containedness.

- Each new carry item gets a stable identifier.
- §4 scope decisions are decisions, not work items. Each one's resolution becomes a §1-style anchor section once chosen, and any architectural seam it crosses gets its own ADR.
- This doc evolves until Phase 4 has stable scope. At that point it either becomes `ROADMAP_PHASE4.md` (mirroring Phase 1's pattern) with §4 / §5 frozen and §3 / §7 carried forward, or remains as planning context alongside an ordered roadmap.
- Resolved §4 questions are struck through with the decided option called out inline.
- Items checked off as completed with linked merge commits. Reopening only happens if the definition of done regresses.

---

## 9. Immediate Next Steps

§4 is open. The first decision to close is **§4.1 (what Phase 4 actually scopes)** because it shapes every other §4 question:
- §4.1 = (a) shapes §4.2 (live boundary moves) and §4.5 (which strategy).
- §4.1 = (b) shapes §4.6 (GUI runtime).
- §4.1 = (c) shapes §4.7 (distribution model).
- §4.1 = (d) shapes §4.4 (which research family).
- §4.1 = (e) shapes §4.4 (which strategy re-tunes).
- §4.1 = (f) requires a new ADR superseding or extending [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md).

Three natural first-pass shapes for Phase 4 (presented as candidates only — operator picks):

- **Accessibility-led.** §4.1 = (b) + (c). Ship a GUI inside an installer. Live boundary stays locked. FOUNDER_INTENT #3 + #4 heavy. Largest code-surface delta. Best fit if the priority signal is "the platform is now demonstrably trustworthy and engineering-capable; make it usable by someone who isn't the developer."
- **Live-boundary test.** §4.1 = (a), §4.2 = (b), §4.5 = (α). Promote regime to micro_live as the lifecycle-proof's first real-capital trip. FOUNDER_INTENT #5 (validation). Smallest scope, biggest stakes. Practical risk: regime may run for months in micro_live without firing, in which case the evidence is "nothing happened correctly" — operationally true, but a thin Phase 4 outcome unless paired with another goal.
- **Research continuation.** §4.1 = (d) and/or (e). Try a third research-target or disciplined re-tunes. Risk: another truthful failure, which honestly evidences the platform but is structurally similar to Phase 3's outcome. Engineering question becomes "is daily swing the wrong tempo for these strategies on this universe?" or "is the universe wrong?" — both are research questions.

The Phase 1 → 2 → 3 progression has been: trust property surfaced (Phase 1) → trust property locked as test (Phase 2) → trust property generalized across two strategies (Phase 3). The natural Phase 4 pull, by FOUNDER_INTENT priority order, is **accessibility** — taking the trustworthy and engineering-capable platform out of the developer-CLI into something a non-developer can use. But this is the operator's call, not a pre-decision in this document.

A combined-everything shape (a)+(b)+(c)+(d)+(e)+(f) is structurally too large for a single phase — same scope-discipline argument that has held since Phase 2. Phase 4 should pick one shape (or a defensibly-small bundle), close it, then close out and let Phase 5 planning open.

After §4.1 is decided, the rest of §4 narrows to whichever conditional questions apply.
