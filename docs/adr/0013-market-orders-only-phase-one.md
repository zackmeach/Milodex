# ADR 0013 — Market Orders Only for Phase 1

**Status:** Accepted
**Date:** 2026-04-16

## Context

The broker interface could expose a full order-type menu from day one — market, limit, stop, stop-limit, trailing-stop — or start with a minimum subset and grow. Each additional order type adds surface to `ExecutionService`, risk checks that need to understand partial-fill edge cases, backtest slippage models that must handle unfilled limits, and strategy interfaces that must express which type they want.

Mean reversion on daily swing with sub-$1k capital on liquid US equities/ETFs does not require limit precision to work. A market order at the bar close gets filled at approximately the closing price; slippage is bounded by the conservative 0.1–0.2% assumption baked into backtests.

## Decision

Phase 1 supports **market orders only**. `ExecutionService.submit()` accepts only market-order intents; any other order type is rejected at the evaluator boundary with a structured `UnsupportedOrderType` error. Time-in-force defaults to DAY (R-BRK-010). Limit, stop, stop-limit, and trailing-stop are explicitly Phase 2+.

Stop-loss behavior is handled by the risk layer (not by stop orders at the broker): the `RiskEvaluator` places an exit-on-threshold check as part of its ongoing evaluation. If the exit check triggers, the risk layer emits a market sell intent, which flows through the same `ExecutionService` path as any other order.

## Rationale

- **Market-only matches the phase-one strategy scope.** Daily-swing mean reversion on liquid instruments does not need sub-cent entry precision. The slippage is small and well-modeled by the backtest assumption.
- **Smaller surface for the highest-stakes layer.** The risk evaluator has fewer order-type edge cases to understand (partial fills on limits, expired stops, order modification races). Every additional order type is another class of "did this fail safely?" question for the risk layer.
- **Backtest/live parity is trivial.** A market order at bar close is the same in both backtest and live within the slippage model. Limit orders introduce fill-or-not ambiguity that needs an explicit fill model, another place for backtest-vs-live divergence to hide.
- **Stop-loss placement lives in the risk layer, not at the broker.** This is deliberately the authority boundary: risk controls are exercised in code Milodex controls, not delegated to broker-side orders that might be forgotten, modified, or outlive a strategy configuration change. It also means the stop-loss honors any other risk check (kill switch, stage) automatically, because it flows through the same gate.
- **No irreversible investment in order-type machinery.** The `ExecutionService` contract can be widened later without changing callers; strategies currently emit typed intents with `order_type='market'`, and adding other types is additive. Deferring the work costs nothing.
- **YAGNI discipline on the broker surface.** Supporting limit orders "because you might need them someday" adds code that needs testing, maintenance, and documentation for a capability no Phase 1 strategy actually uses. When the first strategy that genuinely needs limits appears, that is the right time to add them.
