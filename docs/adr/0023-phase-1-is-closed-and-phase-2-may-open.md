# ADR 0023 — Phase 1 is closed and Phase 2 may open

**Status:** Accepted · 2026-05-04
**Related:** [ROADMAP_PHASE1.md](../ROADMAP_PHASE1.md) §10, [VISION.md](../VISION.md), [FOUNDER_INTENT.md](../FOUNDER_INTENT.md), [SRS.md](../SRS.md) §"Phase 1 Success Criteria", all prior ADRs (0001..0022)

## Context

Phase 1 is a structurally bounded effort: build the platform end-to-end against two strategies — a lifecycle-proof regime SPY/SHY rotation and a research-target meanrev RSI(2) pullback — running paper-mode against Alpaca, with no live trading allowed. Completion is defined by six explicit success criteria (SC-1..SC-6) in [SRS.md](../SRS.md) and tracked authoritatively in [ROADMAP_PHASE1.md §2](../ROADMAP_PHASE1.md#2-phase-1-success-criteria--gating-checklist).

All six SCs have been closed against durable evidence in the event store and the CLI. The most recent close was SC-3's meanrev half on 2026-04-28 — session `2506708f-b7a3-49eb-827a-f6883cc0b4ff` fired simultaneous BUY GLD ×23 + BUY SLV ×152 on the strategy's first cycle. Phase 1 has been gating-complete since.

`ROADMAP_PHASE1.md` §10 mandates a close-out ADR before Phase 2 planning may open: *"When all §2 success criteria are checked, Phase 1 is over. File an ADR closing it out, then (and only then) open the Phase 2 planning doc."* This ADR is that close-out.

## Decision

Phase 1 is closed. Phase 2 planning is authorized to begin.

Specifically:

1. **All six success criteria are accepted as closed** against the evidence summarized below. The audit trail in `data/milodex.db` is the durable record; nothing in this ADR substitutes for it.
2. **The platform is the platform Phase 1 promised to build.** No SC was closed by relaxing it. The risk layer has veto power and has exercised it; both strategies run end-to-end without manual intervention; backtests are deterministic and walk-forward; analytics and reconcile surfaces work; promotion is governed by a frozen-manifest state machine; the kill switch halts and requires manual reset; live trading remains structurally locked per [ADR 0004](0004-paper-only-phase-one.md).
3. **The §7 cross-cutting items are classified per the table below.** Open items either carry into Phase 2 explicitly, are absorbed into a small "Phase 1.5 cleanup band" the operator may resolve before Phase 2 hardens, or are closed as part of this ADR's writing.
4. **Phase 2 planning may now open.** This ADR is its prerequisite per §10, not its substitute. A separate Phase 2 planning document is the next artifact.

## Rationale

**The platform refused to lie about meanrev, and that is the win.** SC-2's meanrev evidence (walk-forward run `54e71b30…`, OOS-aggregate Sharpe 0.33 against a 0.50 promotion threshold, single-window dependency flagged honestly in the trust report) is not a setback — it is the most credible signal Phase 1 produces. The platform was asked "does this strategy as configured pass the promotion gate?" and answered: no. That answer is what FOUNDER_INTENT priority #1 asks for ("trustworthy, well-structured") and what VISION's central concern names directly ("avoiding the trap of mistaking noise for signal"). A platform that quietly approves whatever it tests is worse than no platform. Phase 1 demonstrates that Milodex is the former.

**SC-3's evidence pattern is symmetric across both strategies.** Regime fired on its first cycle on 2026-04-23 (BUY SPY ×12, filled at $710.21); meanrev fired on its first cycle on 2026-04-28 (simultaneous GLD/SLV at the RSI(2) ≤ 10.0 entry threshold). Both fires went strategy → execution → broker → event-store with `submitted_by=strategy_runner` and no manual intervention. The lifecycle-proof and research-target rails are operationally identical in their integration with the rest of the platform. That symmetry is what "two strategies, two purposes" was supposed to validate.

**SC-4 and SC-5 prove the safety machinery operates against real conditions.** A real risk rejection (`BUY SPY ×141` for $100k notional, four simultaneous gate violations) proves the risk layer can refuse non-synthetically. A kill-switch dry-run with cancellation, refusal of subsequent submits, manual reset, and resumption proves the halt path operates end-to-end. Both are durable in the event store under `explanations#9319` and `kill_switch_events#3..#4`.

**SC-6 closes the trust dashboard.** The operator can answer the central operational question — "is this strategy making or losing money, and how does it compare to SPY?" — from a single CLI invocation with confidence labels on every metric. That is FOUNDER_INTENT priority #3 ("accessible and easy to use") rendered concretely: a less financially literate operator can read the output and orient themselves without expert intermediation.

**SC-1's frozen-manifest infrastructure makes drift detectable.** Both strategies are lifecycle-tracked at the `paper` stage; runtime drift checks would refuse execution against an edited config without a fresh freeze. That is the silent invariant Phase 1's strict promotion pipeline depends on, and it is now under test (see [ADR 0015](0015-strategy-identifier-and-frozen-manifest.md)).

**Phase 1 was scoped intentionally and finished without scope creep.** No live trading. No third strategy. No GUI. No multi-strategy concurrency in the formal sense. No alternative brokers. The §7 list is the explicit account of what was deferred — non-gating items kept honest in their own section rather than allowed to drift into "Phase 1 plus a few more things." That discipline is itself an artifact of FOUNDER_INTENT and is explicitly preserved in the §7 classification below.

## Closed success criteria — evidence summary

For full detail and audit-row references see [ROADMAP_PHASE1.md §2](../ROADMAP_PHASE1.md#2-phase-1-success-criteria--gating-checklist).

| SC | Closed | Evidence (abbreviated) |
|---|---|---|
| SC-1 | 2026-04-26 | Both strategies defined entirely in YAML; manifests frozen at `paper` stage (regime hash `e9798c61…`, meanrev hash `f531a076…`). Runtime drift check enforces hash parity at evaluation time. |
| SC-2 | 2026-04-26 | Walk-forward backtests over 2015-01-01 → 2024-12-31. Meanrev OOS-aggregate Sharpe 0.33 (below 0.50 gate, fragility flagged honestly); regime OOS-aggregate Sharpe 1.07 over 1 window (regime is exempt from statistical thresholds per `R-PRM-004`; determinism guarantee carried by golden-output test). |
| SC-3 | 2026-04-28 | Regime fired BUY SPY ×12 on 2026-04-23, filled at $710.21 (audit `explanations#9320`). Meanrev fired simultaneous BUY GLD ×23 + BUY SLV ×152 on 2026-04-28, both filled (audit `explanations#14135` / `#14136`). Both via `submit_paper` with `submitted_by=strategy_runner`. |
| SC-4 | 2026-04-23 | `BUY SPY ×141` ($100k notional) attempted; rejected by four simultaneous risk checks (`max_order_value_exceeded`, `max_single_position_exceeded`, `max_total_exposure_exceeded`, `max_concurrent_positions_exceeded`). Audit row `explanations#9319`. Driving config defect fixed in commit `ca76985`. |
| SC-5 | 2026-04-23 | Kill switch activated via runner `k`-shutdown (`kill_switch_events#3`); manual `trade submit SPY` refused with `kill_switch_active` (`explanations#9322`); reset via `trade kill-switch reset --confirm` (`kill_switch_events#4`); same trade then succeeded (`explanations#9347`). |
| SC-6 | 2026-04-26 | `analytics metrics --strategy <id> --compare-spy` returns trust-report metric set with confidence labels; `report strategy` assembles the strategy-vs-SPY view including a "paper vs backtest" line. |

## §7 item classification

[ROADMAP_PHASE1.md §7](../ROADMAP_PHASE1.md#7-cross-cutting-work-threads-throughout-all-sub-phases) tracks the cross-cutting work that was non-gating for Phase 1. Each item is classified below.

| Item | Classification | Rationale |
|---|---|---|
| Scaffolded-vs-implemented markers (`R-XC-016`) | **Phase 1.5** — verify-and-close | Operator verifies that no `# scaffolded:` markers remain in Phase-1 surfaces; close if none. If any remain, list them as Phase 2 carry items. The verification itself is short. |
| Documentation updates | **Closed by this ADR** | The doc sweep is the close-out. The roadmap pointer to this ADR (added concurrently) and the README index update (also concurrent) are the visible docs work. Future doc updates belong to their own work items, not a perpetual TODO. |
| No live-mode drift | **Phase 1.5** — convert to CI invariant | Add a CI test that fails the build if any code path can reach the broker outside the existing paper-only check. Then close. The principle is structural; the test enforces it without operator vigilance. |
| Kill-switch meanrev exercise | **Phase 1.5** | ~10 minutes of operator work. Symmetrizes SC-5 evidence across both strategies. No design call, no code change. |
| Walk-forward report labeling | **Carry to Phase 2** | Presentation-layer fix. The trust report needs an explicit "walk-forward windowed" / "whole-period" label so an operator does not misread OOS-aggregate metrics as zeros. Tracked, not gating. |
| Stage-source consistency between `report strategy` and `promotion manifest` | **Phase 1.5** | A real reporting bug an operator can stumble into. Three remediation options exist; pick one and close. The runtime drift check uses the manifest's stage so safety is intact, but the reporting inconsistency confuses operators — that is a FOUNDER_INTENT priority #3 (accessibility) concern worth resolving before Phase 2. |
| Runner close-bar finalization race | **Carry to Phase 2** | Multi-hour design call. Three fixes proposed in the §7 entry. Non-gating for SC-3 — the gap between *spec'd* and *actual* close-bar value is structural and present every session, but the audit chain remains intact. Resolve before any later-stage promotion. |
| Strategy-level position caps vs account-scoped risk enforcement | **Carry to Phase 2** | Surfaced 2026-05-04. The schema-level overload of `max_positions` between "strategy-internal invariant" and "account-wide brake" is precisely the multi-strategy concurrency design call that VISION already lists as out-of-scope for Phase 1. |

The "Phase 1.5 cleanup band" therefore contains four named items: scaffolded-marker verification, the live-mode CI invariant, the kill-switch meanrev exercise, and stage-source consistency. None gate Phase 2 planning from opening; resolving them tightens what carries forward.

## Consequences

- **Phase 2 planning is unblocked.** A separate planning artifact is the next document; this ADR is the prerequisite, not the planning doc itself.
- **`ROADMAP_PHASE1.md` becomes a historical record.** A pointer at the top references this ADR. Future updates to that file are limited to historical-accuracy corrections, not active planning.
- **Live trading remains structurally locked.** This ADR does not relax [ADR 0004](0004-paper-only-phase-one.md) in any form. Phase 2 may revisit the live-trading lock, but only via a new ADR that supersedes 0004.
- **The §7 classification is the carry list.** Phase 2 planning starts from the carry-to-Phase-2 entries plus whatever remains of the Phase 1.5 band the operator has not closed by the time Phase 2 hardens.
- **The audit trail is the durable proof.** Every claim in this ADR is backed by event-store rows or commit hashes. Future readers should not need to take this ADR's word for any of it.

## Non-goals

- **This ADR does not open Phase 2.** It authorizes Phase 2 planning to begin. The planning artifact is separate and not yet written.
- **This ADR does not commit to any specific resolution of carry-forward §7 items.** Their classification governs *when* they are worked, not *how*.
- **This ADR does not promote any strategy beyond `paper`.** Promotion remains a separate operator action governed by [ADR 0009](0009-promotion-pipeline-stage-model.md) and the `paper → micro_live → live` state machine. Phase 1's paper-only safeguard ([ADR 0004](0004-paper-only-phase-one.md)) continues to refuse `--to micro_live` and `--to live` at the state-machine level.
- **This ADR does not declare meanrev a successful edge.** Meanrev did not pass the promotion gate. Whether to continue tuning meanrev, retire it, or replace it with another research target is a Phase 2 question.
- **This ADR does not reframe profitability as the success metric.** Per FOUNDER_INTENT, profitability is validation, not purpose. Phase 1 validated trust, structure, and operability — not edge.
