# ADR 0022 — Strategy Rotation Scope Is the Declared Universe

**Status:** Accepted
**Date:** 2026-04-29
**Relates to:** ADR 0003 (config-driven strategies), ADR 0015 (strategy identifier and frozen manifest), [docs/strategy-families.md](../strategy-families.md), CLAUDE.md "Two strategies, two purposes"

## Context

Phase 1 runs two strategies in the same paper account: a lifecycle-proof regime rotation (`regime.daily.sma200_rotation.spy_shy.v1`) and a research-target mean-reversion (`meanrev.daily.pullback_rsi2.curated_largecap.v1`). Per CLAUDE.md and `docs/SRS.md` Key Terms, the two are configured and promoted separately and exist for distinct purposes.

The original implementation of `RegimeSpyShy200DmaStrategy.evaluate` ([src/milodex/strategies/regime_spy_shy_200dma.py](../../src/milodex/strategies/regime_spy_shy_200dma.py)) iterated **every** broker position with no universe filter and proposed a SELL for any symbol that wasn't the current target. Mathematically: "rotate to target" was implemented as "liquidate everything that isn't the target."

This was operationally surfaced on 2026-04-29 — see [docs/reviews/2026-04-29-runner-startup-investigation.md](../reviews/2026-04-29-runner-startup-investigation.md). When regime started a session against a paper account that still held meanrev's positions (AVGO, GLD, SLV from the previous evening), regime generated SELL intents for all three meanrev positions in addition to the BUY for SPY. Risk vetoed all four orders that morning for unrelated reasons (markets closed, stale data, position-count gate), but the architectural seam was clear: regime treated the entire account as its territory.

That contradicted three things at once:

1. The strategy's own YAML — `universe: [SPY, SHY]`.
2. The strategy family spec ([docs/strategy-families.md](../strategy-families.md) §family-regime).
3. The operator playbook in [ROADMAP_PHASE1.md §8 item 7](../ROADMAP_PHASE1.md), which already encodes the principle: "let meanrev manage [its positions] via its RSI exit rule."

## Decision

A strategy's rotation, rebalancing, and exposure-management rules operate **strictly within its declared universe**. Positions outside the universe are not the strategy's concern, even when held in the same broker account.

Specifically:

1. Every strategy evaluator that consumes `context.positions` must filter to symbols present in `context.universe` before generating intents.
2. The "already in target" / "already in universe" hold checks compare against the universe-filtered position set, not the full account.
3. Strategies must not infer ownership from broker presence. Foreign positions are invisible to the strategy.

The first applied instance of this rule is `RegimeSpyShy200DmaStrategy`. Future strategies are bound by the same rule.

## Rationale

- **The universe is the strategy's contract.** A strategy's YAML `universe` declares the set of symbols the strategy reasons about. Treating positions outside the universe as in-scope makes the universe key a lie. This ADR aligns implementation with the obvious reading of the config.
- **Phase 1 explicitly runs two strategies in one account.** That design fails the moment one strategy treats the other's positions as inventory to liquidate. Universe-scoping is the minimum requirement for strategies to coexist.
- **Cross-strategy contamination distorts research signal.** Meanrev exists to test whether mean-reversion has an edge. Letting regime exit meanrev's positions on regime's clock — instead of meanrev's RSI(2) exit rule at threshold 50 — replaces the meanrev exit rule's data with regime's regime-rotation timing. That contaminates the very dataset meanrev exists to produce.
- **The original behavior was an unstated assumption, not a stated design.** Nothing in the strategy YAML, family spec, or any prior ADR claimed regime owns the entire account. The "scorched-earth rotation" was a side effect of iterating `context.positions` directly, not an explicit design call. This ADR fills the gap with the correct call.
- **Operator-driven workarounds don't scale.** The pre-flight playbook for meanrev's first paper run already accepted "leave them and let meanrev manage them via its RSI exit rule" — the same logic should apply structurally, not as a per-session operator choice. Universe-scoping makes the structural fix.

## Consequences

- `regime_spy_shy_200dma.py` filters `normalized_positions` to its universe (`SPY`, `SHY`) before computing the hold check, the sell loop, and the buy guard. Foreign positions are invisible.
- Three new tests in [tests/milodex/strategies/test_regime_spy_shy_200dma.py](../../tests/milodex/strategies/test_regime_spy_shy_200dma.py) lock the behavior:
  - `test_regime_strategy_ignores_non_universe_positions_when_rotating` — foreign-only positions, no spurious sells.
  - `test_regime_strategy_sells_only_universe_positions_alongside_foreign_holdings` — mixed positions, only in-universe sells.
  - `test_regime_strategy_holds_target_alongside_foreign_position` — already-in-target with foreign present, hold not rebalance.
- The five existing regime tests continue to pass unchanged. Universe-scoping is a strict superset of the old behavior in single-strategy operation.
- Cleaning up positions outside a strategy's universe is now an **operator responsibility** (or another strategy's responsibility). A strategy cannot "sweep" foreign positions automatically.
- `meanrev_rsi2_pullback.py` already only proposes intents for symbols in its universe; no change required there. This ADR codifies the rule that was already implicit in the meanrev evaluator.

## Non-goals

- This ADR does **not** add a separate per-strategy positions table or strategy-attribution layer at the broker level. Strategy attribution stays at the audit/event-store level (per `R-XC-008`). Universe-scoping at the evaluator boundary is sufficient for cross-strategy independence in Phase 1.
- This ADR does **not** alter what risk checks the risk layer runs on a per-trade basis. Risk continues to evaluate every intent on its own merits regardless of which strategy proposed it.
- This ADR does **not** mandate that strategies produce *exit* intents for every in-universe position. A strategy can still legitimately hold positions outside its current target without selling them (e.g., the SHY half of regime's universe is held when risk-off).
- This ADR does **not** change reconcile or operator-facing position views. Those continue to show the entire broker account, not strategy-scoped slices.

## Update to CLAUDE.md (informational)

CLAUDE.md "Strategies are config-driven" already says the universe is part of config, not code. This ADR makes the dual point explicit: **the universe is also the boundary of the strategy's authority over the account.** Code reviewers should refuse a strategy evaluator that reads positions outside `context.universe`.
