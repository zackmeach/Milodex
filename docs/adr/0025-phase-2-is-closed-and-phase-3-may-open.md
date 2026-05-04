# ADR 0025 — Phase 2 is closed and Phase 3 may open

**Status:** Accepted · 2026-05-04
**Related:** [PHASE2_PLANNING.md](../PHASE2_PLANNING.md) §5, [ADR 0023](0023-phase-1-is-closed-and-phase-2-may-open.md) (the structurally analogous Phase 1 close-out), [VISION.md](../VISION.md), [FOUNDER_INTENT.md](../FOUNDER_INTENT.md), all Phase 1 ADRs (0001–0023) plus Phase 2 additions (0024)

## Context

Phase 2 was authorized to begin via [ADR 0023](0023-phase-1-is-closed-and-phase-2-may-open.md) on 2026-05-04. The operator opened [PHASE2_PLANNING.md](../PHASE2_PLANNING.md) the same day and resolved §4.1 as **option (iv) — cleanup-first**, with §4.2 following automatically as **option (a) — live remains locked through Phase 2**. The exit-criteria subset narrowed to two: **C-1 (carry list closed)** and **C-2 (honest-signal property locked)**.

Phase 2 was therefore deliberately the smallest possible follow-on to Phase 1: close the four §3 carry items (CI-1, CI-2, CS-1, P-1), lock the honest-signal property as a regression test, and stop. No new strategies. No live boundary movement. No new system goals.

[PHASE2_PLANNING.md §8](../PHASE2_PLANNING.md) mandates a close-out ADR before Phase 3 planning may open: same pattern as Phase 1's §10 + ADR 0023 prerequisite. This ADR is that close-out.

## Decision

Phase 2 is closed. Phase 3 planning is authorized to begin.

Specifically:

1. **C-1 and C-2 are accepted as closed** against the evidence summarized below. The audit trail in `data/milodex.db`, the test files cited, and the commits land that evidence durably; this ADR is not a substitute for any of it.
2. **Phase 2 closed without weakening anything Phase 1 promised.** The risk layer's account-scoped enforcement is now codified as authoritative ([ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)); the runner's lifecycle invariants are tighter (CI-1 stability lockin, CI-2 startup row); the trust-report surface no longer mis-renders walk-forward metrics (P-1); and the honest-signal property meanrev exposed in Phase 1 is now machine-verifiable (C-2).
3. **The §3 carry list is empty.** Phase 3 starts from zero outstanding §3 items. Any new carry items belong to Phase 3's planning artifact, not this one.
4. **Live trading remains structurally locked.** [ADR 0004](0004-paper-only-phase-one.md) was not relaxed. Phase 3 may revisit, but only via a new ADR superseding 0004.

## Rationale

**Phase 2 was scope-disciplined by design.** Per [FOUNDER_INTENT.md](../FOUNDER_INTENT.md) priority order, the operator chose cleanup-first over expanding the platform's surface. The result: a phase whose entire job was to close gaps the platform's own honest-signal property had surfaced. Phase 2 added zero new ways the platform can be wrong — it removed several.

**The honest-signal property survived the test of being formalized.** When [ADR 0023](0023-phase-1-is-closed-and-phase-2-may-open.md) named "the platform refused to lie about meanrev" as Phase 1's load-bearing thesis, the property was an empirical fact about one session. C-2 turns it into a machine-verifiable invariant: a unit test on `check_gate` plus an end-to-end CLI test, both keyed to meanrev's actual Phase 1 numbers (Sharpe 0.327 / max DD 6.41% / 752 trades). A silent change to `MIN_SHARPE` or to how the gate combines failures cannot pass CI without tripping at least one of these tests. The thesis is now load-bearing in the test surface, not just the docs.

**Each carry item was resolved without compromise.** CI-1 and CI-2 closed by tightening runner internals via TDD (3 + 6 new tests). CS-1 closed by *naming* the architecture honestly via [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) — no code change because the risk evaluator was already correct, just under-documented. P-1 closed by extending the trust-report surface to label every OOS-aggregate metric (3 new tests + a `sortino_ratio` clearing in `metrics_for_run` for the equity-curve-only metric that walk-forward shatters).

**Test count moved 525 → 538 across the carry list and C-2.** None of these tests are trivial — each one locks a property that surfaced in a real session ([CI-1 from session `7e4b0315-...`](../strategies/runner.py), [CI-2 from session `f73a5eb6-...`](../strategies/runner.py), [CS-1 from session `a140da6c-...`](../adr/0024-account-scoped-position-caps-are-authoritative.md), [P-1 from session `54e71b30-...`](../analytics/metrics.py), C-2 from the same `54e71b30-...` evidence as ADR 0023). The high test-to-code-change ratio reflects the pattern's purpose: lock real operational signals, prevent regression to them.

**Phase 2 ends with the platform more trustworthy than it started.** That is the FOUNDER_INTENT priority #1 (trustworthy) outcome rendered as an actual delta — every change tightened the platform's willingness to surface inconvenient truths or honestly name its limitations. Nothing was added that could weaken those properties.

## Closed exit criteria — evidence summary

| Criterion | Closed | Evidence (abbreviated) |
|---|---|---|
| C-1 | 2026-05-04 | All four §3 carry items resolved per [PHASE2_PLANNING.md §3](../PHASE2_PLANNING.md). CI-2 via runner startup row + UPDATE-on-shutdown semantics (3 tests). CI-1 via two-consecutive-identical-fetch lockin with 5-min timeout (5 + 1 modified tests). CS-1 via [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) + RISK_POLICY.md "Position Cap Scope" section + config comments (no code changes). P-1 via per-metric `(OOS)` labels in `analytics metrics` + `report strategy` + `sortino_ratio=None` for walk-forward (3 tests). |
| C-2 | 2026-05-04 | Two regression tests keyed to meanrev's Phase 1 numbers: unit test `test_gate_refuses_meanrev_shape_evidence_on_sharpe_alone` ([tests/milodex/promotion/test_state_machine.py](../../tests/milodex/promotion/test_state_machine.py)) on `check_gate(sharpe_ratio=0.327, max_drawdown_pct=6.41, trade_count=752, lifecycle_exempt=False)`; end-to-end test `test_promotion_promote_refuses_meanrev_shape_evidence_through_cli` ([tests/milodex/cli/test_promotion_promote.py](../../tests/milodex/cli/test_promotion_promote.py)) seeding a walk-forward run with the same OOS-aggregate metadata and asserting the CLI refuses with a Sharpe-specific reason while writing neither a promotion nor a manifest. ADR 0023's thesis now lives on the test surface. |

## §3 carry-list classification

All four §3 items are closed. None carry to Phase 3. The full per-item resolution narrative is in [PHASE2_PLANNING.md §3](../PHASE2_PLANNING.md).

| Item | Resolution | Closing artifact |
|---|---|---|
| CI-1 close-bar finalization race | Option (a) — defer lock-in until two consecutive identical OHLCV fetches separated by ≥30s (default), with 5-min max-wait timeout fallback | Code: [src/milodex/strategies/runner.py](../../src/milodex/strategies/runner.py) `_maybe_advance_lockin_watermark`; tests: [tests/milodex/strategies/test_runner.py](../../tests/milodex/strategies/test_runner.py) (5 new + 1 modified) |
| CI-2 strategy_runs row not written at startup | Option (a) — insert at `__init__`, UPDATE on shutdown via new `EventStore.update_strategy_run_end` | Code: [src/milodex/strategies/runner.py](../../src/milodex/strategies/runner.py), [src/milodex/core/event_store.py](../../src/milodex/core/event_store.py); tests: [tests/milodex/strategies/test_runner.py](../../tests/milodex/strategies/test_runner.py) (3 new) |
| CS-1 strategy-level position caps vs account-scoped enforcement | Option (c) — document and accept; account-scoped is authoritative, strategy YAML `risk.max_positions` is informational | [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md), [docs/RISK_POLICY.md](../RISK_POLICY.md) "Position Cap Scope", config comment in [configs/risk_defaults.yaml](../../configs/risk_defaults.yaml), template fix in [configs/sample_strategy.yaml](../../configs/sample_strategy.yaml) (no code changes) |
| P-1 walk-forward report labeling | Option (a) — distinguish at every metric; `(OOS)` per-line tag for OOS-aggregate metrics, `sortino_ratio=None` for walk-forward | Code: [src/milodex/cli/commands/analytics.py](../../src/milodex/cli/commands/analytics.py), [src/milodex/cli/commands/report.py](../../src/milodex/cli/commands/report.py), [src/milodex/analytics/metrics.py](../../src/milodex/analytics/metrics.py); tests: [tests/milodex/cli/test_analytics_command.py](../../tests/milodex/cli/test_analytics_command.py), [tests/milodex/cli/test_report.py](../../tests/milodex/cli/test_report.py) (3 new) |

## Consequences

- **Phase 3 planning is unblocked.** A separate planning artifact is the next document; this ADR is the prerequisite, not the planning doc itself.
- **`PHASE2_PLANNING.md` becomes a historical record.** A pointer at the top references this ADR. Future updates to that file are limited to historical-accuracy corrections, not active planning.
- **Live trading remains structurally locked.** This ADR does not relax [ADR 0004](0004-paper-only-phase-one.md) in any form. Phase 3 may revisit the live-trading lock, but only via a new ADR that supersedes 0004.
- **Phase 3 carry list is empty.** Phase 3 starts from zero outstanding §3 items. Any new carry items belong to Phase 3's planning artifact.
- **The honest-signal property is now a load-bearing test.** If C-2 ever fails, that is the platform telling its operator that something has changed the gate's willingness to refuse insufficient evidence — and the right response is to re-establish the property, not to relax the test.
- **The §4 deferred questions remain deferred.** [PHASE2_PLANNING.md §4](../PHASE2_PLANNING.md) options (i) second research-target, (ii) micro_live promotion, (iii) concurrent multi-strategy, (iv-extras) GUI / installer all remain candidates for Phase 3 — none decided here.
- **The audit trail is the durable proof.** Every claim in this ADR is backed by event-store rows, commit hashes, or test files. Future readers should not need to take this ADR's word for any of it.

## Non-goals

- **This ADR does not open Phase 3.** It authorizes Phase 3 planning to begin. The planning artifact is separate and not yet written.
- **This ADR does not commit to any specific Phase 3 scope.** §4 of [PHASE2_PLANNING.md](../PHASE2_PLANNING.md) remains the menu of deferred options; the operator has not chosen yet.
- **This ADR does not promote any strategy beyond `paper`.** Promotion remains a separate operator action governed by [ADR 0009](0009-promotion-pipeline-stage-model.md) and the paper-only safeguard from [ADR 0004](0004-paper-only-phase-one.md).
- **This ADR does not declare meanrev a successful edge.** Meanrev still has not passed the promotion gate. Whether to continue tuning meanrev, retire it, or replace it with another research target is a Phase 3 question.
- **This ADR does not reframe profitability as the success metric.** Per [FOUNDER_INTENT.md](../FOUNDER_INTENT.md), profitability is validation, not purpose. Phase 2 validated that the platform can close its own gaps without weakening its honest-signal property — that is the success.
