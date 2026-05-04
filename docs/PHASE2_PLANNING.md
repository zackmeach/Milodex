# Phase 2 Planning

> **Phase 2 was formally closed on 2026-05-04 via [ADR 0025](adr/0025-phase-2-is-closed-and-phase-3-may-open.md).** This document is now a historical record. All four §3 carry items closed; both §5 exit criteria (C-1, C-2) closed. New planning belongs in the Phase 3 planning document. Section §4 deferred candidates ((i) second research-target, (ii) micro_live, (iii) concurrent multi-strategy, GUI, installer) carry forward as Phase 3 menu items, not commitments.

**Status:** Closed 2026-05-04. Originally opened 2026-05-04 as the prerequisite-mandated planning artifact per [ROADMAP_PHASE1.md §10](ROADMAP_PHASE1.md#10-tracking-this-roadmap). **§4.1 was decided as option (iv) — cleanup-first;** §4.2 followed automatically as (a) — live remained locked through Phase 2. Active exit-criteria subset narrowed to **C-1 + C-2** (see §5), both now closed. Filename `PHASE2_PLANNING.md` was chosen deliberately over `ROADMAP_PHASE2.md` because Phase 2 had no committed scope at opening; cleanup-first turned the file into a tight planning + tracking artifact rather than a roadmap, which matches the actual shape of the phase that ran.

**Predecessors:** [ROADMAP_PHASE1.md](ROADMAP_PHASE1.md) (now historical), [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md) (authorizes this doc), [VISION.md](VISION.md), [FOUNDER_INTENT.md](FOUNDER_INTENT.md), [SRS.md](SRS.md).

---

## 1. What Phase 1 Left Behind

Phase 1 closed against six explicit success criteria, all evidenced in the event store with audit-row references in [ROADMAP_PHASE1.md §2](ROADMAP_PHASE1.md#2-phase-1-success-criteria--gating-checklist). The full close-out narrative is in [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md).

The single most load-bearing Phase 1 result was a **truthful failure**: meanrev's walk-forward OOS-aggregate Sharpe of 0.33 against a 0.50 promotion gate, with the single-window dependency flagged honestly in the trust report. The platform was asked "does this strategy as configured pass the promotion gate?" and answered: no. That answer is what [FOUNDER_INTENT.md](FOUNDER_INTENT.md) priority #1 (trustworthy) and [VISION.md](VISION.md)'s central concern ("avoiding the trap of mistaking noise for signal") demand. **Anything Phase 2 adds must not weaken this property.** That is the binding constraint above every other Phase 2 goal.

Phase 1 also left:

- An explicit **carry list** of four open §7 items, classified by ADR 0023 as carry-forward (§3 below).
- A **structural lock** on live trading per [ADR 0004](adr/0004-paper-only-phase-one.md). Phase 2 may revisit the lock, but only via a new ADR that supersedes 0004 — relaxing it via configuration is not on the table.
- **Two strategies running paper-only**: regime (lifecycle-proof, exempt from edge gates per `R-PRM-004`) and meanrev (research-target, gates applied honestly and refused promotion). The shape of "two strategies, two purposes" carried Phase 1; Phase 2's analog is a §4 question.
- **Five Phase-1.5 cleanup items closed on 2026-05-04** (kill-switch meanrev exercise, scaffolded markers, no-live-mode-drift CI invariant, stage-source consistency, plus the doc-sweep that ADR 0023 itself was). The cleanup band is empty — the carry list below is what remains.

---

## 2. Phase 2 Goals (Anchor: FOUNDER_INTENT priority order)

[FOUNDER_INTENT.md](FOUNDER_INTENT.md) fixes the priority order for tradeoffs:

1. **Trustworthy.** Build something real, functional, and trustworthy.
2. **Engineering capability.** Demonstrate strong AI-assisted engineering.
3. **Accessibility.** Make the system accessible and easy to use.
4. **Shareability.** Make it portfolio-worthy.
5. **Profitability.** Pursue profit as validation of effectiveness.

Phase 2 work proposals are evaluated against this order. **A feature that improves shareability at the cost of trustworthiness loses.** A feature that improves accessibility without weakening anything above it is a strong candidate. A feature whose only argument is "this would make the system look cooler" gets refused under the same discipline that gated Phase 1 scope.

Draft goal candidates the operator can shape from:

- **G1. Preserve and tighten the honest-signal property.** Anything Phase 2 adds — more strategies, live capital, a GUI, a second research target — must not silently weaken Phase 1's willingness to refuse a strategy that hasn't earned promotion. A regression test that locks the property is a strong candidate (see §5 C-2).
- **G2. Resolve the carry list** (§3) so the platform's surface area matches its specified semantics. Each item is a place where Phase 1's structure works but its current implementation is at most "mostly aligned." Closing the gap is engineering capability (priority #2) rendered visibly.
- **G3. Decide what Phase 2 actually scopes** (§4) — the equivalent of Phase 1's "two strategies, two purposes" discipline.
- **G4. Define exit criteria** analogous to Phase 1's six SCs, so Phase 2 has a structurally bounded ending and not a "we'll know when we get there." Candidates in §5; the operative subset is a §4 decision.

These are draft goals. The operator may add, drop, reorder, or refine.

---

## 3. Carry List from Phase 1 §7

Each item is reproduced in compressed form. Full surfacing-context with audit-row references is in [ROADMAP_PHASE1.md §7](ROADMAP_PHASE1.md#7-cross-cutting-work-threads-throughout-all-sub-phases). Each item already had three resolution options framed during the original surfacing — **the operator's job is to pick one or define a fourth**, not to receive a pre-decided answer. New stable identifiers (`CI-`, `CS-`, `P-`) are introduced here so subsequent sessions can reference items by ID; the same convention as the SRS's `R-XX-NNN` requirement codes.

### 3.1 Runner internals

Two issues sit inside [`src/milodex/strategies/runner.py`](../src/milodex/strategies/runner.py) and reflect the same thread of "things the runner does in shutdown that should happen in startup, or doesn't do consistently across the lifecycle." Whether they are worked together or separately is itself a planning decision.

#### CI-1. Close-bar finalization race — **Closed 2026-05-04 via option (a)**
- **Surfaced:** 2026-04-27, session `7e4b0315-371d-41b6-8060-248964b8c356`. 380 explanation rows during market hours, lock-in at 16:00:23 ET with `latest_bar_close = 266.24`, then 70 minutes of `already_seen` short-circuit until controlled stop.
- **Symptom:** `is_market_open()` flips False at the closing bell, but Alpaca's daily bar takes seconds-to-minutes to finalize after the closing auction settles. The lock-in cycle captures the bar at the *moment* the broker reported the market closed — which may still be aggregating server-side — and advances the watermark, so subsequent cycles that would have observed the truly finalized bar are short-circuited.
- **Operational consequence:** Recorded close (`266.24`) is almost certainly not the official 4:00 PM closing print. Divergence per session is unbounded and non-deterministic.
- **Resolution applied:** Option (a) — defer lock-in until two consecutive identical OHLCV fetches confirm the bar has settled. `StrategyRunner._maybe_advance_lockin_watermark` gates the post-close `_last_processed_bar_at` advance on a stability check: the bar's OHLCV signature is observed, and only after a second fetch with the identical signature separated by at least `close_lockin_min_interval_seconds` (default 30s) does the watermark advance. A `close_lockin_max_wait_seconds` timeout (default 300s / 5 min) advances anyway if the bar never stabilizes — fail-mode (a) prevents a broken provider from looping the runner forever, while the per-cycle explanations preserve forensic visibility into the unstable window. Six new tests in [tests/milodex/strategies/test_runner.py](../tests/milodex/strategies/test_runner.py) lock the behavior; the pre-existing in-progress-bar regression test was updated to assert lockin semantics rather than immediate advance. Full test suite green at 533 passed.
- **Resolution alternatives considered:** (b) Alpaca's bar-finalized signal — rejected; provider-specific coupling risks Phase 3+ broker-swap optionality (per VISION's "swap brokers later if needed"); (c) document and accept — rejected; deferral rather than fix.
- **Why this matters:** Non-gating for SC-3 — a fire would still produce a real broker order — but the gap between *spec'd* and *actual* close-bar value was structurally present every session. A FOUNDER_INTENT priority #1 (trustworthiness) concern: the audit chain was intact, but the recorded number wasn't what the operator thought it was.

#### CI-2. `strategy_runs` row not written at runner startup — **Closed 2026-05-04 via option (a)**
- **Surfaced:** 2026-05-04, session `f73a5eb6-a72f-46a4-addc-90ca4404fdc6`. 28 cycle rows recorded under that `session_id` between 19:19:02 and 19:39:38 UTC while the latest `strategy_runs` row was still `id=16` from a kill-switch exercise four hours earlier.
- **Symptom:** Runner records `explanations` rows on every cycle but inserts the corresponding `strategy_runs` row only on shutdown.
- **Operational consequence:** The canonical "is a runner active?" query (`SELECT * FROM strategy_runs WHERE ended_at IS NULL`) returns zero rows even when a runner is actively recording cycles. Operators and audit tooling cannot enumerate active sessions from the event store directly. A reverse query against `explanations` grouped by `session_id` works as a proxy but is indirect.
- **Resolution applied:** Option (a) — insert at startup. `StrategyRunner.__init__` now appends a `strategy_runs` row with `ended_at=NULL` and `exit_reason=NULL`; `EventStore.update_strategy_run_end(session_id, ended_at, exit_reason)` was added for the shutdown path to UPDATE the open row in place rather than INSERT a duplicate. The audit shape stays "one row per session" (count invariant preserved by the existing test surface) while the row goes from absent to present at the right point in the lifecycle. Three new tests in [tests/milodex/strategies/test_runner.py](../tests/milodex/strategies/test_runner.py) lock the behavior: `test_runner_startup_creates_strategy_runs_row_with_null_ended_at`, `test_runner_shutdown_updates_open_strategy_runs_row_without_duplicating`, `test_runner_active_session_findable_via_ended_at_is_null_query`. Full test suite green at 528 passed.
- **Resolution alternatives considered:** (b) separate active-sessions tracker — rejected as plumbing for marginal benefit; (c) document the constraint — rejected as deferring rather than fixing.
- **Why this matters:** Distinct from CI-1 (different aspect of the same module). FOUNDER_INTENT priority #3 (accessibility) concern: the operator can now trust the obvious "is anything running?" surface.

### 3.2 Cross-strategy concerns

#### CS-1. Strategy-level position caps vs account-scoped risk enforcement — **Closed 2026-05-04 via option (c)**
- **Surfaced:** 2026-05-04, regime session `a140da6c-a50d-4bdb-98e9-fc2b20e2ed1f`.
- **Symptom:** Regime declares `max_positions: 1` in [configs/spy_shy_200dma_v1.yaml](../configs/spy_shy_200dma_v1.yaml) (correct in isolation: regime should hold *either* SPY or SHY at any time, never both). The risk layer's `concurrent_positions` check counts *all* broker positions regardless of strategy origin. With meanrev's three leftover positions present, projected open count is `1 + 3 = 4 > 1` and every regime entry was blocked with `max_concurrent_positions_exceeded`.
- **Why both behaviors are individually correct:** [ADR 0022](adr/0022-strategy-rotation-scope-is-the-declared-universe.md) keeps strategies in their lane on the *intent* side (regime did not propose any rogue SELLs). The "risk layer is sacred" principle in [CLAUDE.md](../CLAUDE.md) requires account-level enforcement on the *execution* side. The conflict is schema-level — `max_positions` is overloaded between "strategy-internal invariant" and "account-wide brake fed to the risk evaluator."
- **Resolution applied:** Option (c) — document and accept. [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md) codifies account-scoped enforcement as the authoritative semantics: the risk evaluator's `concurrent_positions` check counts every open broker position regardless of strategy origin; strategy YAML `risk.max_positions` is informational metadata only. Multi-strategy paper accounts must size `max_concurrent_positions` to the sum of strategies' expected concurrent positions. No code changes — the risk evaluator's existing behavior is now named, not modified. Documentation updates: [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md), [docs/RISK_POLICY.md](RISK_POLICY.md) §"Position Cap Scope: Account-Authoritative", [configs/risk_defaults.yaml](../configs/risk_defaults.yaml) `max_concurrent_positions` comment, [configs/sample_strategy.yaml](../configs/sample_strategy.yaml) `max_positions` comment. Full test suite green at 533 passed.
- **Resolution alternatives considered:** (a) per-strategy position accounting — rejected as pre-committing Phase 2 to concurrent multi-strategy work (a §4.1.iii decision the operator has not yet made), and as requiring position-attribution reconciliation against broker positions (which don't carry strategy attribution); (b) split the schema — rejected as awkward (permanently splits a schema concern across two surfaces) without solving anything that (c) doesn't already solve.
- **Why this is more than a runner-internal:** This was the schema-level question Phase 1 deferred when "concurrent multi-strategy execution" was put in the §9 floor. The (c) resolution preserves Phase 2 §4.1.iii optionality cleanly: if/when the operator opens that goal, option (a) becomes the right next move and ADR 0024's account-scoped semantics extend rather than reverse.

### 3.3 Presentation

#### P-1. Walk-forward report labeling — **Closed 2026-05-04 via option (a)**
- **Surfaced:** 2026-04-26.
- **Symptom:** `analytics metrics` against a walk-forward `run_id` reports `total_return_pct=0`, `sharpe=null`, `trading_days=0` because each OOS window resets equity (per [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md) — the OOS-aggregate metrics live in the run's metadata, not in the trade-ledger metrics view). Trade-ledger metrics (win rate, profit factor, trade count) from the same run are still meaningful.
- **Operational consequence:** An operator could read meanrev's walk-forward run as "0.0% return" when its OOS-aggregate is `+4.34%`. The trust-report surface that closes SC-6 needs to distinguish "walk-forward windowed" from "whole-period" so a misread is impossible.
- **Resolution applied:** Option (a) — distinguish at every metric. Two layers of fix: (1) the analytics module's `metrics_for_run` already pulls OOS-aggregate values for `total_return_pct` / `max_drawdown_pct` / `sharpe_ratio` / `trading_days` / `cagr_pct` per [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md), and now also clears `sortino_ratio` to `None` for walk-forward (the equity curve is fragmented across OOS windows, so the broken-curve value is meaningless). (2) Both surfaces that render the trust report — `analytics metrics` ([src/milodex/cli/commands/analytics.py](../src/milodex/cli/commands/analytics.py) `_build_metrics_lines`) and `report strategy` ([src/milodex/cli/commands/report.py](../src/milodex/cli/commands/report.py)) — now append a `(OOS)` per-metric tag on Trading days, Total return, CAGR, Max drawdown, and Sharpe when the run is walk-forward, alongside the existing `(OOS-aggregate, walk-forward)` period suffix. Three new tests in [tests/milodex/cli/test_analytics_command.py](../tests/milodex/cli/test_analytics_command.py) and [tests/milodex/cli/test_report.py](../tests/milodex/cli/test_report.py) lock the per-metric labels and the sortino-cleared invariant. Full test suite green at 536 passed.
- **Resolution alternatives considered:** (b) hide the misleading metrics — rejected; the OOS-aggregate values are meaningful, hiding them would lose information; (c) wrap with a scope header — rejected; a header cannot prevent a metric line from being read in isolation.
- **Why this matters:** Smallest item by code surface, largest by FOUNDER_INTENT priority #3 (accessibility) impact. A less financially literate operator reading "0.0%" against meanrev's run would conclude the strategy is a no-op when it actually has the platform's most extensive evidence trail. The honest-signal property of Phase 1 (truthful failure) is undermined if the surface that displays it is itself misleading.

---

## 4. Open Phase 2 Scope Questions

These are the questions the operator owns. Each is framed with alternatives; **none has a recommended answer here**.

### 4.1 ~~What does Phase 2's "two strategies, two purposes" look like?~~ — **Decided 2026-05-04: (iv) cleanup-first**

Phase 1's discipline was: *one lifecycle-proof* (regime SPY/SHY, exempt from edge gates per `R-PRM-004`) and *one research-target* (meanrev RSI(2) pullback, held to gates and refused promotion).

**Decision:** Phase 2 resolves all four §3 carry items before adding any new system-level goals. Exit criteria narrow to C-1 (carry list closed) + C-2 (honest-signal regression test) per §5. Rationale: the platform's surface area should catch up to its specified semantics before more weight is placed on it; this matches FOUNDER_INTENT priority #1 (trustworthy) by closing known gaps before adding new ones, and FOUNDER_INTENT priority #2 (engineering capability) by making the gap-closing visible as engineering work.

Alternatives **(i)** add a second research-target, **(ii)** promote one strategy to micro_live, and **(iii)** concurrent multi-strategy execution were considered and deferred. They remain candidates for whatever opens after Phase 2 closes; the carry list resolutions chosen below may inform their feasibility (in particular, CS-1's resolution shapes the road for any future (iii)-style concurrency goal).

### 4.2 ~~Does Phase 2 cross the live-trading boundary?~~ — **Decided 2026-05-04: (a) live remains locked**

[ADR 0004](adr/0004-paper-only-phase-one.md) is Phase 1's lock. ADR 0023 is explicit: "Phase 2 may revisit the live-trading lock, but only via a new ADR that supersedes 0004."

**Decision:** Live remains locked for all of Phase 2 (option (a)). Implied by §4.1 = (iv): none of the four carry items requires the live boundary to move. ADR 0004 stays in force. Live is a Phase 3 question.

Alternatives **(b)** unlock micro_live only and **(c)** unlock all stages were considered and deferred.

### 4.3 What's the equivalent of Phase 1's §9 floor?

[VISION.md §Out of Scope for Phase 1](VISION.md#out-of-scope-for-phase-1--no-matter-how-tempting) plus [ROADMAP_PHASE1.md §9](ROADMAP_PHASE1.md#9-what-is-explicitly-not-in-this-roadmap) form Phase 1's floor. Phase 2's floor is up for grabs but with a strong default: **everything still locked unless explicitly opened.**

| Item | Phase 1 status | Default Phase 2 status |
|---|---|---|
| Concurrent multi-strategy execution | Out | **Open question** (see 4.1.iii) |
| Daemon / supervisor runtime | Out | Out by default |
| Crypto / alternative assets | Out | Out by default |
| ML-driven signals | Out | Out by default |
| Alternative / sentiment data | Out | Out by default |
| Desktop GUI | Out | **Open question** (FOUNDER_INTENT priority #3) |
| Alternative brokers | Out | Out by default |
| Distributable installer | Out | **Open question** (FOUNDER_INTENT priority #4) |
| Live trading | Out (per ADR 0004) | **Open question** (see 4.2) |
| HFT / low-latency trading | Out | Out (matches Phase 1 rationale) |
| Multi-user collaboration | Out | Out (Milodex is a personal tool) |
| Walk-forward parameter search | Out (per ADR 0021) | Out (search procedure conflicts with OOS validation) |
| Options / derivatives | Out | Out by default |
| Cloud-native distributed architecture | Out | Out by default |
| Social / marketplace features | Out | Out by default |

Items marked **Open question** are the ones the operator should explicitly decide; the rest stay floor unless a specific case is made.

### 4.4 If 4.1.i, which research-target family is next?

Conditional on Phase 2 scoping a second research-target strategy, the practical question is which family:

- **Momentum.** Cleanest contrast to mean-reversion. Same daily swing tempo. Different statistical character (trending vs. reverting). The lifecycle-proof regime strategy is technically a trend-following family member, but its purpose is platform proof, not research — momentum-as-research-target is distinct.
- **Breakout.** Closer to momentum but with an explicit volatility-filter dependency. Adds a parameter surface (breakout window, volatility regime) without inventing a new family.
- **Pairs / cointegration.** A different family entirely (cross-asset). Larger code-surface for the strategy-engine (multi-leg positions, cointegration tests) but more research-distinct.
- **A second mean-reversion variant.** Different parameterization (different RSI period, different exit rule, different universe slice). Smallest research-distinctness; tests parameter-space sensitivity rather than family-space.

[strategy-families.md](strategy-families.md) is the canonical source for what each family's normative shape looks like.

---

## 5. Exit Criteria

Phase 1's six SCs were small, evidence-bound, falsifiable, and simultaneous-when-true. Phase 2 mirrors that shape. With §4.1 = (iv) decided, the operative set is **two criteria**:

- **C-1. Carry list closed.** All four §3 items (CI-1, CI-2, CS-1, P-1) have an applied resolution and the surface area matches the specified semantics. Each item is closed when: the resolution option is picked, the change lands with a linked merge commit, and any test-locking the new behavior is in place.
- **C-2. Honest-signal property locked. — Closed 2026-05-04.** Two regression tests guard Phase 1's truthful-failure behavior, both keyed off meanrev's actual Phase 1 numbers (Sharpe 0.327, max DD 6.41%, 752 trades from session `54e71b30…`): (1) a unit test on `check_gate` ([tests/milodex/promotion/test_state_machine.py](../tests/milodex/promotion/test_state_machine.py) `test_gate_refuses_meanrev_shape_evidence_on_sharpe_alone`) asserts the gate refuses on Sharpe specifically while the other two thresholds pass; (2) an end-to-end CLI test ([tests/milodex/cli/test_promotion_promote.py](../tests/milodex/cli/test_promotion_promote.py) `test_promotion_promote_refuses_meanrev_shape_evidence_through_cli`) seeds a walk-forward backtest run with meanrev's OOS-aggregate metadata, attempts `milodex promotion promote --to paper --run-id ...`, and asserts the CLI refuses, names Sharpe in the error, and writes neither a `PromotionEvent` nor a `StrategyManifestEvent`. A silent change to `MIN_SHARPE` or to how the gate combines failures cannot pass CI without tripping at least one of these. ADR 0023's "the platform refused to lie about meanrev" thesis now lives on the test surface, not just on the original session evidence.

Phase 2 ends when **C-1 and C-2 are simultaneously true**, an ADR closes Phase 2 analogous to [ADR 0023](adr/0023-phase-1-is-closed-and-phase-2-may-open.md), and Phase 3 planning is authorized.

### Deferred candidates

The following criteria were considered for Phase 2 and deferred. They remain candidates for Phase 3+:

- **C-3 (deferred).** Second research-target strategy through the full lifecycle. Tied to §4.1.i.
- **C-4 (deferred).** Micro_live promotion of one strategy. Tied to §4.1.ii / §4.2.b.
- **C-5 (deferred).** Concurrent multi-strategy execution with schema resolved. Tied to §4.1.iii — note CS-1's resolution in §3.2 will inform feasibility.
- **C-6 (deferred).** Desktop GUI for the daily-operator workflow.
- **C-7 (deferred).** Distributable installer / clone-and-run path.

---

## 6. What This Document Is *Not*

- **Not a commitment.** Until the operator approves a scope decision per §4, this document is a working brief.
- **Not a substitute for ADRs.** Any decision in §4 that crosses an architectural seam (live trading, multi-strategy concurrency, second broker, GUI runtime model) requires its own ADR.
- **Not the final shape of Phase 2.** Phase 1's roadmap evolved continuously through the work; Phase 2's planning should expect the same.
- **Not a reframe of profitability.** Per [FOUNDER_INTENT.md](FOUNDER_INTENT.md), profit is validation, not purpose. Phase 2's success is whether the platform stays trustworthy as it grows — not whether either strategy makes money.
- **Not a sequencing plan.** §3 and §5 do not yet have an "ordered work breakdown" analogous to [ROADMAP_PHASE1.md §8](ROADMAP_PHASE1.md#8-ordered-work-breakdown-actionable-sequence). Sequencing follows scope decisions, not the other way around.

---

## 7. What's Explicitly Still Out (Phase 2 Floor)

These remain out of scope for Phase 2 unless a separate ADR opens them, even if §4 resolves expansively:

- **High-frequency / low-latency trading** — incompatible with daily swing tempo; same Phase 1 rationale.
- **Multi-user collaboration as a first-class system requirement** — Milodex remains a personal tool; "shareable with friends" stays at the packaging-and-defaults level, not multi-tenancy.
- **Fully autonomous live trading without human gating** — conflicts with [VISION.md §Autonomy Boundary](VISION.md#autonomy-boundary). Even if 4.2 opens micro_live, the autonomy-boundary actions (capital allocation, kill-switch reset, live promotion, broker permission) remain human-gated.
- **Walk-forward parameter search** — conflicts with [ADR 0021](adr/0021-walk-forward-metrics-are-oos-aggregate.md)'s "evaluate fixed parameters, do not fit them" decision. Walk-forward is OOS validation, not a fitting procedure.
- **Live trading at any stage** — locked per [ADR 0004](adr/0004-paper-only-phase-one.md) until superseded by a new ADR. §4.2 is where that ADR's case would be made.
- **Cloud-native distributed architecture** — Milodex stays local-first.
- **Options / derivatives infrastructure** — Phase 1 instrument whitelist per [ADR 0016](adr/0016-phase1-instrument-whitelist.md) extends naturally; opening it is a separate ADR.
- **AI-generated strategy invention without strict human review** — the research-discipline rules in [VISION.md §Research Discipline](VISION.md#research-discipline) remain binding.
- **Social / marketplace / subscription-platform features** — not on the table.

---

## 8. Tracking Conventions

- Each carry item has a stable identifier (`CI-1`, `CI-2`, `CS-1`, `P-1`); this document and subsequent planning sessions reference items by ID.
- When an item is resolved, the resolution lands as: a small ADR (if it crosses an architectural seam), a checked box here with a commit hash (if mechanical), or both.
- The §4 scope questions are decisions, not work items. Each one's resolution becomes a §1-style anchor section once chosen, and any architectural seam it crosses gets its own ADR.
- This doc evolves until Phase 2 has stable scope. At that point it either becomes `ROADMAP_PHASE2.md` (mirroring Phase 1's pattern) with §4 / §5 frozen and §3 / §7 carried forward, or remains as planning context alongside an ordered roadmap. The operator decides which.
- Per [ROADMAP_PHASE1.md §10](ROADMAP_PHASE1.md#10-tracking-this-roadmap)'s pattern: when this document hardens to a roadmap, items are checked off as completed with linked merge commits. Reopening an item only happens if its definition of done regresses — never quietly un-checked.

---

## 9. Immediate Next Steps

§4 is decided. The remaining decisions are per-carry-item: each of the four §3 items needs a resolution option picked (or a fourth defined). Two open dimensions to choose:

**Order to work the items.** Two natural shapes:
- **By size / blast radius:** smallest first (CI-2 and P-1 are local; CI-1 is provider-touching; CS-1 is schema-level).
- **By topical grouping:** runner-internals together (CI-1 + CI-2), then cross-strategy (CS-1), then presentation (P-1).

**Per-item resolution.** For each of CI-1, CI-2, CS-1, P-1, pick from the three options listed in §3 — or define a fourth. Per-item resolutions are independent: picking option (a) for CI-1 does not constrain the option chosen for CI-2.

After each item closes, this doc gets a checkbox + linked commit hash per [ROADMAP_PHASE1.md §10](ROADMAP_PHASE1.md#10-tracking-this-roadmap)'s pattern. When all four are closed plus C-2's regression test, Phase 2 is done and an ADR closes it.
