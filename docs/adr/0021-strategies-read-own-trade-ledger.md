# ADR 0021 — Strategies Read Their Own Trade Ledger, Not the Broker Account

**Status:** Accepted
**Date:** 2026-04-24
**Relates to:** ADR 0008 (risk-layer veto architecture), ADR 0011 (SQLite event store), ADR 0013 (market-orders-only in Phase 1), ADR 0015 (strategy identifier and frozen manifest)

## Context

Paper-mode trading at Alpaca uses a single account shared across every Milodex strategy. `BrokerClient.get_positions()` reports that account-wide view — it does not distinguish which strategy opened which position.

Until 2026-04-24, `StrategyRunner._current_positions()` fed that account-wide list directly into `StrategyContext.positions`. A strategy's `evaluate()` method then iterated those positions as though they were its own holdings. This is wrong: a position another strategy (or the operator) opened is not one this strategy may touch. The contract at the strategy boundary implicitly promised "these are your open positions," and the runtime was delivering "these are every position on the account."

The incident that forced the decision (see commit `27cfcce`, 2026-04-24):

- 2026-04-23: The regime strategy (`regime.daily.sma200_rotation.spy_shy.v1`) opened a 13-share SPY paper position.
- 2026-04-24 morning: The meanrev strategy (`meanrev.daily.pullback_rsi2.curated_largecap.v1`) was started for its own paper-session shakeout. Its `evaluate()` read `context.positions`, saw SPY at 13 shares, computed RSI(2) > 50 against SPY's bars, and emitted `SELL SPY x13` intents — proposing to unwind the regime strategy's position from inside the meanrev session.
- The intents were only blocked because meanrev had no frozen manifest yet, so the risk layer's `manifest_drift` check refused them. The manifest check was not written to catch this class of bug — it caught it by accident. Once the manifest was frozen, the intent would have passed every risk check and gone to the broker.

Commit `27cfcce` added a narrow fix at the meanrev layer: scope `open_positions` to the strategy's declared universe. That is correct and stays correct — a strategy should never touch a symbol outside its own universe — but it does not close the full class of bugs. Two strategies that legitimately share a symbol in their universes (e.g. both trading SPY under different regimes) would still collide. The architecturally correct answer has to live one layer lower, at the runtime contract that decides what `context.positions` means.

## Decision

Strategies read their own open positions from the trade ledger, filtered by originating `strategy_name`, not from `BrokerClient.get_positions()`.

Specifically:

1. **`StrategyContext.positions`** is populated by `compute_ledger_positions(event_store, strategy_id)`, which sums signed quantities (BUY positive, SELL negative) over `trades` rows where `strategy_name == strategy_id`, `source == 'paper'`, and `status == 'submitted'`. Only symbols with strictly positive net quantity remain.
2. **The broker's position list is consulted only for `avg_entry_price` lookup**, keyed on symbols that already appear in the ledger-derived result. The broker remains authoritative for actual fill price; it is not authoritative for "is this mine."
3. **The universe-scope filter in meanrev stays**, belt-and-suspenders. A strategy that declares a universe is asserting "these are the only symbols I touch," and enforcing that locally costs nothing and clarifies intent.
4. **The `trades` schema already carries `strategy_name`** (migration `001_initial.sql`, column 12). No migration is required — the gap was purely downstream.
5. **Reconciliation gaps** between the ledger (`submitted`) and actual broker fills (fill status, partial fills, post-submit rejections) remain a `milodex reconcile` concern (`R-OPS-004`). For Phase 1 this is acceptable because ADR 0013 limits us to market orders, where fills are near-instant and near-complete.

## Rationale

- **Provenance is the invariant that prevents the whole bug class.** Scoping-by-universe fixes one flavor; scoping-by-originating-strategy fixes all flavors, including the shared-universe case that Phase 2+ will produce.
- **The contract at the strategy boundary is now honest.** "`context.positions` is what you own" was always the implicit promise. This ADR makes the runtime keep it.
- **Defense in depth, intentional this time.** The risk layer's manifest-drift check accidentally blocked this bug on 2026-04-24. With provenance at the runtime and universe-scoping at the strategy, the manifest check returns to its real job (rejecting drift between promoted config and runtime config) and is no longer relied on for an unrelated contamination concern.
- **Schema was already ready.** `trades.strategy_name` has existed since the initial migration. The cost of this change is a new helper and a runner swap, not a schema rollout.
- **Reads correctly to a new contributor.** A future reader of `strategies/runner.py` sees `compute_ledger_positions(self._event_store, self._strategy_id)` and understands: a strategy's world is what its own ledger says it is. That matches the mental model of an append-only event store as the source of truth.

## Consequences

- `StrategyRunner._current_positions()` no longer calls `broker.get_positions()`. The broker is consulted only for `avg_entry_price` in `_build_entry_state()`.
- A new module `src/milodex/strategies/positions.py` holds `compute_ledger_positions`. A new `EventStore.list_trades_for_strategy(strategy_name, source, statuses)` method performs the filtered query.
- Existing tests that seeded broker positions without corresponding ledger trades had to seed both. This surfaces the intent more clearly — "this strategy owns this position" — rather than relying on broker-side state that the runtime no longer trusts.
- `context.entry_state` is now keyed on ledger-owned symbols only. Entry price prefers the broker's `avg_entry_price`; falls back to ledger VWAP when the broker has no record for the symbol (rare: operator manually closed broker position without updating the ledger).

## Non-goals

- **This ADR does not change order submission.** `ExecutionService.submit_paper()` and the risk layer are unchanged. The change is purely in how `context.positions` is computed.
- **This ADR does not introduce fill reconciliation.** Submitted-but-rejected-by-broker trades and partial fills are out of scope here. `milodex reconcile` remains the correct surface for that (`R-OPS-004`, `OPERATIONS.md`).
- **This ADR does not enable multi-strategy concurrent execution.** Phase 1 remains one-strategy-at-a-time per `VISION.md`. Provenance makes future concurrency safer but does not unlock it.
- **This ADR does not eliminate the meanrev universe filter.** That filter encodes a separate, semantically-useful invariant (a strategy only touches symbols in its own universe) and is kept as defense in depth.
