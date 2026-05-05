# ADR 0027 — Phase 3 is closed and Phase 4 may open

**Status:** Accepted · 2026-05-05
**Related:** [PHASE3_PLANNING.md](../PHASE3_PLANNING.md) §5, [ADR 0023](0023-phase-1-is-closed-and-phase-2-may-open.md) (Phase 1 close-out), [ADR 0025](0025-phase-2-is-closed-and-phase-3-may-open.md) (Phase 2 close-out, Phase 3 authorization), [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) (Phase 3 concurrency model), [VISION.md](../VISION.md), [FOUNDER_INTENT.md](../FOUNDER_INTENT.md), all Phase 1 + 2 ADRs (0001–0025) plus Phase 3 addition (0026)

## Context

Phase 3 was authorized to begin via [ADR 0025](0025-phase-2-is-closed-and-phase-3-may-open.md) on 2026-05-04. The operator opened [PHASE3_PLANNING.md](../PHASE3_PLANNING.md) the same day; on 2026-05-05 §4 was decided as **(i) + (iii) — engineering-led bundle** with the live boundary remaining locked (§4.2 = (a)). The operative exit-criteria subset narrowed to **C-1, C-2, C-6**.

Phase 3 was therefore a focused engineering-capability test: add a second research-target strategy alongside meanrev, exercise it through the full lifecycle, codify the concurrency model that ADR 0024 prepared the safety contract for, and stop. No live boundary movement. No GUI. No installer.

[PHASE3_PLANNING.md §8](../PHASE3_PLANNING.md) follows the same convention Phase 1 and Phase 2 used: a close-out ADR before Phase 4 planning may open. This ADR is that close-out.

## Decision

Phase 3 is closed. Phase 4 planning is authorized to begin.

Specifically:

1. **C-1, C-2, and C-6 are accepted as closed** against the evidence summarized below. Walk-forward run `ac3227ab-0397-43f8-a1d8-d88000916cc5` in `data/milodex.db`, the test files cited, the commits land that evidence durably; this ADR is not a substitute for any of it.
2. **Phase 3 closed without weakening anything Phase 1 or Phase 2 promised.** The honest-signal property was tested and held: momentum's walk-forward refused promotion on Sharpe (0.06 < 0.5) and drawdown (15.37% > 15.0%) — the same gate machinery that refused meanrev now refuses momentum. The walk-forward labeling discipline P-1 added in Phase 2 is rendered automatically by the analytics module without per-strategy special-casing. ADR 0024's account-scoped enforcement is unchanged; ADR 0026 confirms it as the binding semantics for concurrent execution.
3. **The §3 carry list is empty.** Phase 3 opened with an empty carry list (per ADR 0025) and closes with an empty carry list. No Phase-2-style cleanup items were surfaced during Phase 3 execution.
4. **Live trading remains structurally locked.** [ADR 0004](0004-paper-only-phase-one.md) was not relaxed. Phase 4 may revisit, but only via a new ADR superseding 0004.

## Rationale

**The honest-signal property generalized across two strategies.** [ADR 0023](0023-phase-1-is-closed-and-phase-2-may-open.md)'s thesis — *"the platform refused to lie about meanrev"* — was an empirical observation about one strategy. [ADR 0025](0025-phase-2-is-closed-and-phase-3-may-open.md)'s C-2 turned that observation into a regression test keyed to meanrev's exact numbers. Phase 3 ran a different strategy (momentum.daily.tsmom) through the same gate without any per-strategy adaptation of the gate machinery, and it was refused for an *additionally* honest reason (drawdown 15.37% > 15.0%, plus the same Sharpe failure pattern). The thesis now reads: *"the platform refused to lie about any research-target whose evidence does not earn the gate"* — and the gate machinery is the same C-2 unit + E2E test surface Phase 2 locked.

**The harness scaled to a second research thread without architectural movement.** Momentum was implemented as a structural mirror of meanrev (cross-sectional / ranked / regime-filtered / equal-notional sized). The Strategy ABC contract held. The frozen-manifest pipeline accepted the new family without modification. The walk-forward + analytics + report surfaces handled the new strategy without per-strategy code paths. The 18 momentum tests passed alongside the existing 538 tests with zero regressions (538 → 556). Per FOUNDER_INTENT priority #2, this is engineering capability rendered visibly: the harness is reusable, not bespoke per strategy.

**Concurrency was decided without superseding ADR 0012.** ADR 0026's per-process supervisor model preserves the runtime model and dual-stop dialog that Phase 1 carefully designed. Single-process supervisor was rejected as too much architectural movement for a goal as narrow as "exercise two strategies concurrently in paper." Each runner keeps its own session, its own log, its own Ctrl-C dialog; the broker account is shared via ADR 0024's account-scoped enforcement, which was already exercised under concurrent demand in Phase 2's CS-1 incident (`a140da6c-...`). Phase 3 added no code to the runner — the architecture supported concurrency before Phase 3, ADR 0026 names it as the binding semantics.

**Phase 3 was scope-disciplined by design.** Per FOUNDER_INTENT priority order, the operator chose engineering-led (i)+(iii) over the larger accessibility-led (iv)+(v) bundle and over the structurally-premature live-boundary test (ii). The result: a phase whose entire job was to demonstrate that the harness scales to a second research thread and a second concurrent strategy. Live boundary did not move. GUI/installer remain Phase 4 candidates. The phase delivered exactly its scoped value and stopped.

**Test count moved 538 → 556.** All 18 new tests are in `tests/milodex/strategies/test_momentum_daily_tsmom.py`. None are trivial — each one locks a behavior of the new strategy: parameter validation, entry/exit rules, ranking, regime filter, stop-loss / max-hold / momentum-exit precedence, reasoning capture, loader integration, and a 12-bar golden test with hand-computed signals. The test-to-code-change ratio reflects the same purpose the Phase 2 ratio did: lock the new strategy's behavior at landing time so future regressions trip CI.

**Phase 3 ends with the platform demonstrably more capable than it started without weakening any prior trust property.** That is the FOUNDER_INTENT priority #2 (engineering capability) outcome rendered as an actual delta — every change exercised the harness on a new shape, and nothing tightened required loosening.

## Closed exit criteria — evidence summary

| Criterion | Closed | Evidence (abbreviated) |
|---|---|---|
| C-1 | 2026-05-05 | `momentum.daily.tsmom.curated_largecap.v1` defined ([configs/momentum_daily_tsmom_v1.yaml](../../configs/momentum_daily_tsmom_v1.yaml)); strategy class implementing the [momentum family spec](../strategy-families.md#family-momentum--daily-time-series-momentum-swing) ([src/milodex/strategies/momentum_daily_tsmom.py](../../src/milodex/strategies/momentum_daily_tsmom.py)); 18 tests including golden-output test ([tests/milodex/strategies/test_momentum_daily_tsmom.py](../../tests/milodex/strategies/test_momentum_daily_tsmom.py)); walk-forward run `ac3227ab-0397-43f8-a1d8-d88000916cc5` over 2015-01-01→2024-12-31 (496 trades, 4 OOS windows, OOS-aggregate Sharpe 0.06, total return +0.43%, max drawdown 15.37%, single-window dependency YES); promotion attempt refused on TWO gate violations (Sharpe < 0.5, drawdown ≥ 15%) — honest-signal property held. P-1 walk-forward labeling rendered automatically: every metric in the trust report carries `(OOS)`; Sortino correctly cleared to `n/a`. |
| C-2 | 2026-05-05 | Three-part: **C-2a** per-process supervisor model documented in [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md). **C-2b** account-scoped enforcement exercised in practice via [Phase 2 CS-1](../PHASE2_PLANNING.md)'s session `a140da6c-a50d-4bdb-98e9-fc2b20e2ed1f` — regime against meanrev's leftover positions, every cycle correctly refused with `max_concurrent_positions_exceeded`. **C-2c** ≥30 paper-trading-day runtime evidence is operator-side ongoing; phase closes on architectural sufficiency, runtime evidence accumulates after (same shape as Phase 1 SC-3 closing on first-fire evidence). |
| C-6 | 2026-05-05 | Phase 2 invariants preserved end-to-end: 556/556 tests green at close (was 538 → +18 momentum tests, zero regressions). C-2 honest-signal regression tests (`test_gate_refuses_meanrev_shape_evidence_on_sharpe_alone`, `test_promotion_promote_refuses_meanrev_shape_evidence_through_cli`) still pass and were also exercised by momentum's walk-forward refusal at the live CLI surface. ADR 0024 account-scoped position caps unchanged. P-1 walk-forward labeling renders for the new strategy without per-strategy code (the analytics module's discipline applies uniformly). No silent removal or relaxation of any prior ADR. |

## §3 carry-list classification

**Empty at open, empty at close.** Phase 3 opened with an empty §3 carry list per [ADR 0025](0025-phase-2-is-closed-and-phase-3-may-open.md). Phase 3 execution surfaced no new cleanup items. Phase 4 starts from an empty §3 list.

## Consequences

- **Phase 4 planning is unblocked.** A separate planning artifact is the next document; this ADR is the prerequisite, not the planning doc itself.
- **`PHASE3_PLANNING.md` becomes a historical record.** A pointer at the top references this ADR. Future updates to that file are limited to historical-accuracy corrections, not active planning.
- **Live trading remains structurally locked.** This ADR does not relax [ADR 0004](0004-paper-only-phase-one.md) in any form. Phase 4 may revisit the live-trading lock, but only via a new ADR that supersedes 0004.
- **Phase 4 carry list is empty.** Phase 4 starts from zero outstanding §3 items. Any new carry items belong to Phase 4's planning artifact.
- **The honest-signal property is now a load-bearing test demonstrated across two strategies.** If C-2 ever fails on the gate machinery, that is the platform telling its operator the gate has been weakened — and the right response is to re-establish the property, not to relax the test.
- **The §4 deferred candidates remain deferred.** Phase 3 §4.1 alternatives (ii) micro_live, (iv) GUI, (v) installer, plus the not-applicable (vi) cleanup-only — all remain candidates for Phase 4 planning. Their conditional sub-questions (§4.5 / §4.6 / §4.7) become live again if Phase 4 takes them up.
- **Per-strategy position attribution remains a Phase 4+ optionality.** [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)'s "option (a) becomes the right next move if §4.1.iii is taken up" was not exercised in Phase 3 — ADR 0026's per-process supervisor with account-scoped enforcement satisfied the engineering-capability test without needing per-strategy attribution. The optionality stands clean for Phase 4+.
- **The audit trail is the durable proof.** Every claim in this ADR is backed by event-store rows, commit hashes, or test files. Future readers should not need to take this ADR's word for any of it.

## Non-goals

- **This ADR does not open Phase 4.** It authorizes Phase 4 planning to begin. The planning artifact (`PHASE4_PLANNING.md`) is separate and lands alongside this ADR.
- **This ADR does not commit to any specific Phase 4 scope.** [Phase 3 §4.1's deferred alternatives](../PHASE3_PLANNING.md) ((ii) micro_live, (iv) GUI, (v) installer) form the Phase 4 menu; the operator has not chosen.
- **This ADR does not promote any strategy beyond `paper`.** Promotion remains a separate operator action governed by [ADR 0009](0009-promotion-pipeline-stage-model.md) and the paper-only safeguard from [ADR 0004](0004-paper-only-phase-one.md). Momentum is currently at `backtest` stage with an honest gate refusal on its walk-forward run.
- **This ADR does not declare momentum a successful edge.** Momentum's walk-forward Sharpe of 0.06 and drawdown of 15.37% are below the gate. Whether to retire momentum, re-tune within VISION's research-discipline rules, or replace it with a different research-target is a Phase 4 question.
- **This ADR does not declare meanrev a successful edge.** Meanrev's Phase 1 walk-forward Sharpe of 0.327 was already below the gate; that did not change in Phase 3. Both research-target strategies have been honestly refused.
- **This ADR does not reframe profitability as the success metric.** Per [FOUNDER_INTENT.md](../FOUNDER_INTENT.md), profitability is validation, not purpose. Phase 3 validated that the platform's harness scales to a second research thread without weakening its trust properties — that is the success.
