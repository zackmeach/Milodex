# ADR 0028 — Promotion Gate is Stage-Aware

**Status:** Accepted
**Date:** 2026-05-05
**Relates to:** ADR 0009 (promotion pipeline stage model), ADR 0020 (promotion thresholds are code invariants — superseded), ADR 0023 (Phase 1 closed thesis)

## Context

ADR 0020 established `MIN_SHARPE = 0.5`, `MAX_DRAWDOWN_PCT = 15.0`, `MIN_TRADES = 30` as a single set of thresholds that gated *every* promotion. The same bar applied to `backtest → paper`, `paper → micro_live`, and `micro_live → live`. The 2026-05-05 backtest-rejection audit (see [`docs/reviews/backtest-rejection-analysis.md`](../reviews/backtest-rejection-analysis.md)) showed this single-threshold formulation was driving a 91.7% backtest rejection rate, where roughly half the rejections were strategies that had legitimate edge potential but couldn't satisfy live-trading criteria from a backtest run alone.

The framing problem: the 0.5/15/30 numbers were calibrated against the cost of taking a strategy *to live*. Applying that bar to the *first* gate (backtest→paper) treats both transitions as if they had the same cost. They don't.

## Decision

Promotion thresholds are now **stage-aware**. The gate selects a threshold dict based on `to_stage`:

```python
PAPER_READINESS_THRESHOLDS = {
    "min_sharpe": 0.0,
    "max_drawdown_pct": 25.0,
    "min_round_trips": 15,
}
LIVE_READINESS_THRESHOLDS = {
    "min_sharpe": 0.5,
    "max_drawdown_pct": 15.0,
    "min_round_trips": 30,
}
THRESHOLDS_BY_TARGET_STAGE = {
    "paper": PAPER_READINESS_THRESHOLDS,
    "micro_live": LIVE_READINESS_THRESHOLDS,
    "live": LIVE_READINESS_THRESHOLDS,
}
```

`check_gate(to_stage=..., ...)` is the new signature. The previous module-level constants `MIN_SHARPE`, `MAX_DRAWDOWN_PCT`, `MIN_TRADES` remain as backward-compat aliases pointing at the live values.

The gate also reads `round_trip_count` (closed positions) when provided, falling back to `trade_count` (raw fills) for pre-PR-2.3 evidence. The shift from fills to round-trips matches what the threshold is actually trying to measure: statistical-power evidence, which scales with round-trips not fills.

## Rationale

**Asymmetric cost.** A false negative at backtest→paper scraps a real edge before any data is collected. A false positive at backtest→paper occupies one paper slot at $0 risk. A false positive at paper→live risks real capital. The thresholds should reflect this cost asymmetry, not encode a single bar that's tuned for the highest-stakes transition and accidentally applied to the lowest.

**Paper trading exists for a reason.** If the only promotion that mattered were live, paper would be redundant. Paper trading collects forward-walk evidence under realistic execution against fresh data the strategy hasn't seen. That evidence is worth more than another 100 backtest trades. A strategy with a marginal backtest Sharpe deserves the chance to produce paper data — which is exactly the job paper-readiness is designed to do.

**The 0.5 / 15 / 30 numbers retain their authority where they belong.** They still gate the transitions to micro_live and live. ADR 0023's "the platform refused to lie about meanrev" property is preserved at paper→live; meanrev's Sharpe-0.327 evidence still cannot promote to live. What changed is that meanrev (and any similar strategy) can now reach paper to collect the data live promotion *would* require.

**Round-trips, not fills.** `MIN_TRADES = 30` was never about counting orders — it was a proxy for statistical power. Two strategies with 30 fills can have wildly different round-trip counts (and therefore wildly different sample sizes) depending on whether they're making round trips or holding overnight. Round-trip count is what the threshold has always been trying to measure.

## Consequences

- Existing gate-passing evidence still passes the LIVE-stage gate. Existing gate-failing evidence may now pass at the PAPER stage — that's the intent.
- `walk_forward_batch.run_batch` now passes `to_stage="paper"` to `check_gate` because the screen's job is "would this clear backtest→paper?"
- The `milodex promotion promote` CLI now passes `to_stage` from the user's `--to` flag to `check_gate`. The thresholds align with the target stage automatically.
- The honest-signal regression test (`test_meanrev_shape_evidence_*`) is split into two: paper-readiness PASS, live-readiness FAIL. Both behaviors are locked.
- The deferred conceptual question — confidence-interval-based gating instead of threshold-based — stays deferred. See §8 of `docs/reviews/backtest-rejection-analysis.md` and project memory `project_open_question_gate_concept.md`. Revisit only if the stage-aware fix is still misclassifying strategies on power grounds after the cadence-aware refinement (PR 3.2) lands and the strategy bank is re-baselined.

## Supersedes

ADR 0020's single-threshold formulation. The "thresholds are code-level invariants" portion of ADR 0020 is preserved — they remain Python constants, governed by code review and git commits, not config edits. What changes is the *count* of thresholds (one set → three) and the basis (fills → round-trips).
