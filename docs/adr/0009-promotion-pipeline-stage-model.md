# ADR 0009 — Promotion Pipeline as Enforced Stage Model

**Status:** Accepted
**Date:** 2026-04-16

## Context

The VISION document defines a four-stage lifecycle: `backtest → paper → micro_live → live`. Promotion between stages is gated by evidence (Sharpe > 0.5, max drawdown < 15%, min 30 trades) and, for later stages, by explicit operator approval. This lifecycle could be implemented as a convention (the operator watches metrics and moves strategies by hand), as advisory metadata (the config has a `stage` field that's ignored at runtime), or as enforced state that the risk layer honors.

## Decision

Stage is a required field on every strategy config, valued in `{backtest, paper, micro_live, live}`. The `RiskEvaluator` reads the originating strategy's stage on every trade intent and rejects any intent whose routing would exceed what the stage permits:

- `backtest` → may only generate intents inside the backtester; real submission is refused.
- `paper` → may submit paper orders only.
- `micro_live` → (phase two+) may submit live orders capped at micro-capital sizing.
- `live` → (phase two+) may submit live orders without the micro cap.

Advancing from `paper` to `micro_live` or from `micro_live` to `live` requires an explicit CLI promotion command; editing the YAML alone does not enable live trading (R-PRM-005). The promotion command records a log entry with the evidence snapshot at time of transition.

## Rationale

- **Convention alone is not enough.** The whole point of the promotion pipeline is to prevent the operator from fooling themselves. A `stage` field that only humans check is the same as no field at all when discipline slips.
- **Enforcement at the risk layer is the cheapest correct place.** The evaluator already sees every intent and already reads config; adding one check closes the stage loop without a new subsystem.
- **Evidence is captured at transition time, not inferred later.** Logging the metrics snapshot when stage advances gives a clean audit trail: what did the strategy look like when we trusted it more? This is essential for diagnosing paper/live performance divergence down the line.
- **Dual control for live capital.** Requiring both a config edit *and* a distinct CLI command to reach `live` means no single mistake — a typo, a fat-fingered paste, a wrong git branch — can expose real money.
- **Supports the VISION "Autonomy Boundary" principle.** Deploying a strategy to live capital and raising position-size limits both flow through the promotion command, making them auditable discrete events rather than diffuse config drift.
- **Phase-one compatibility.** During phase one, `micro_live` and `live` are physically unreachable (see ADR 0004). The stage model is built now so that enforcement is exercised under `backtest`/`paper` usage long before live capital is on the table.
