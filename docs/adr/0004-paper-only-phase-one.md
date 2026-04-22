# ADR 0004 — Paper-Only for All of Phase One

**Status:** Accepted
**Date:** 2026-04-16

## Context

Phase one builds the research, execution, and risk infrastructure end-to-end. The system will have the *technical capability* to submit live orders long before any strategy has accumulated the evidence to deserve live capital. The risk layer's correctness is the system's highest-stakes assumption (see VISION.md). Until it has been exercised in anger, real capital at the mercy of an untested enforcement path is unacceptable.

## Decision

Throughout all of phase one, the `RiskEvaluator` rejects any trade whose submission would be routed to Alpaca's live endpoint. This is enforced in code (R-EXE-007), not just by convention or `.env` setting. Live-capital deployment is explicitly out of scope for phase one, even for strategies that pass all promotion thresholds.

## Rationale

- **Bugs in phase-one infrastructure are expected.** The entire research and execution stack is being built; it has not yet been stressed by real use. The window for risk-layer bugs is widest exactly when the cost of one is highest.
- **Promotion evidence has to be earned on paper first anyway.** The VISION pipeline requires backtest + paper trading with specific thresholds before live capital. Phase one cannot produce enough paper trades to clear that bar, so there is no strategy for which live capital is even eligible.
- **Hard stop, not a config toggle.** The alternative — "just don't set `TRADING_MODE=live`" — depends on operator discipline and is one typo from failure. A code-level refusal cannot be bypassed by a misread env var.
- **Reinforces R-EXE-001 and R-PRM-002.** Every phase-one trade passes through the risk layer; phase-one stages are `backtest` and `paper` only; the combination means a single additional stage-check closes the loop.
- **Reversible when the time is right.** Lifting the phase-one live-lock is a deliberate future ADR, not a silent config change.
