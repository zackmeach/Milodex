# ADR 0055 — Event-store per-strategy position ledger for concurrent runners

**Status:** Accepted
**Date:** 2026-06-05
**Related:** [ADR 0010](0010-hybrid-source-of-truth.md) (hybrid source of truth), [ADR 0011](0011-sqlite-event-store.md) (event store), [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) (account-scoped caps), [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) (per-process concurrency), [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) (per-strategy attribution at risk), [`src/milodex/risk/attribution.py`](../../src/milodex/risk/attribution.py), [`src/milodex/strategies/runner.py`](../../src/milodex/strategies/runner.py)

## Addendum — 2026-06-22 (same-symbol guardrail lifted)

> The interim guardrail stated throughout the body below ("do **not** co-run
> multiple strategies on the same symbol and same account," and line-23's claim
> that launch-time eval-symbol enforcement "is now code-enforced") is
> **historical**. Status now: **the guardrail is lifted and same-symbol,
> same-account co-run is allowed.**
>
> - The strategy-scoped position ledger this ADR specifies **shipped** and was
>   soak-verified; the runner now derives `context.positions` from the
>   per-strategy event-store ledger, so concurrent same-symbol runners no longer
>   corrupt each other's position view.
> - The **launch-time same-symbol refusal was removed on 2026-06-15** (`211d983`,
>   [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md)
>   2026-06-15 addendum). `evaluation_symbol_for_config` survives only as an
>   informational reconciliation map (`strategies/paper_runner_control.py` →
>   `live_runner_eval_symbols`); it no longer raises on collision.
> - The three invariants the guardrail proxied are enforced elsewhere:
>   per-account submit serialization
>   ([ADR 0056](0056-cross-process-submit-serialization-per-account-advisory-lock.md)),
>   the opposite-side resting-order veto (`_check_opposite_side_order`), and this
>   ADR's per-strategy ledger position cap.
> - One fail-safe residual remains (not unsafe): partial-fill ledger
>   reconciliation fails closed for the per-strategy cap and is nil in the paper
>   regime. Per-symbol advisory locks remain deferred.
>
> Read the guardrail language below as the 2026-06-05 decision record, superseded
> by this addendum.

## Context

On 2026-06-03, four SPY `5Min` intraday strategies (`breakout.orb`, `momentum.vwap_trend`, `meanrev.vwap_reversion`, `meanrev.rsi2`) ran concurrently in one Alpaca paper account under [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md)'s per-process supervisor model. They corrupted each other's position view.

**The failure mode.** Each strategy runner builds its evaluation `StrategyContext.positions` from `_current_positions()` ([`src/milodex/strategies/runner.py:519-524`](../../src/milodex/strategies/runner.py)), which maps `broker.get_positions()` into a `{symbol: quantity}` dict. That call site is invoked every evaluation cycle ([`runner.py:256`](../../src/milodex/strategies/runner.py)). `broker.get_positions()` returns the **account net** per symbol — Alpaca carries no strategy tag ([ADR 0029 Context](0029-per-strategy-position-attribution-at-risk-layer.md)). A runner therefore treats the entire account's net SPY balance as *its own* position when deciding entries, exits, and `max_positions` semantics at the strategy layer.

**Verified incident sequence (2026-06-03).** `meanrev.rsi2` bought 13 SPY @ 14:51:07 (+13 account net). `momentum.vwap_trend` sold 13 SPY @ 14:51:12 (account flat). On its next cycle, `meanrev.rsi2` read flat from the broker and **re-entered** (bought 13 @ 15:23). This looked like a `max_positions: 1` violation but was not: the account genuinely was flat because a sibling strategy's sell had offset `rsi2`'s lot. Net of all four strategies' fills that session, the account ended flat.

**Two consequences.**

1. **Spurious re-entries and corrupted strategy logic.** Any strategy whose signal logic is position-aware (hold, scale, exit, re-enter) will mis-fire when a sibling offsets its lot at the broker while the strategy's durable trade history still records its own fills.

2. **Broker wash-trade protection.** Opposite-side orders on the same symbol in one account trip Alpaca error `40310000` ("potential wash trade detected ... opposite side market/stop order exists"). This is a broker-side collision symptom of shared-account netting, not a Milodex risk-layer miss.

**What already exists.** [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) introduced per-strategy position **attribution** at the risk layer: `attribute_position()` and `count_positions_by_strategy()` in [`src/milodex/risk/attribution.py`](../../src/milodex/risk/attribution.py) reconstruct *who owns the current broker net* by walking submitted `trades` rows. [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) remains the account-wide floor; per-strategy caps are additive ([ADR 0029 Decision 5-7](0029-per-strategy-position-attribution-at-risk-layer.md)). The runner was never updated to consume the same model — it still feeds strategies raw broker net. That asymmetry is the bug.

**Guardrail (effective until soak-verified).** Do **not** co-run multiple strategies on the **same symbol and same account**. Launch-time enforcement on evaluation symbol (`context.universe[0]`) is now code-enforced in `milodex strategy run` (see ADR 0026 addendum); the live-soak guardrail otherwise stays until strategy-scoped positions are verified under fleet operation.

## Options evaluated

### Option A — Per-strategy account or sub-account isolation

Give each concurrent strategy its own Alpaca account (or broker partition) so `broker.get_positions()` net equals that strategy's lot. Symbol partitioning within one account is not a broker primitive — isolation is effectively "one strategy (or non-overlapping symbol set) per account."

| | |
| --- | --- |
| **Pros** | Broker truth and strategy truth align by construction; `_current_positions()` needs no semantic change; wash-trade collisions across strategies on the same symbol disappear because they are different accounts; no ledger drift between Milodex and Alpaca for strategy-scoped reads. |
| **Cons** | Contradicts the shared-account concurrency model [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) Decision 3 ("the account is shared"); fragments paper and live capital across N accounts; multiplies API keys, buying-power surfaces, and operator reconciliation; does not reuse [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) / [`attribution.py`](../../src/milodex/risk/attribution.py) investment; [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) account caps must be reasoned per-account or re-aggregated; Alpaca sub-account ergonomics for a sub-$1k live footprint are poor; per-strategy P&L and fleet dashboards become cross-account aggregation work. |
| **Relation to existing ADRs** | Preserves [ADR 0010](0010-hybrid-source-of-truth.md) broker authority per account but abandons single-account fleet operation. Extends [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) only by duplicating it per account. Bypasses [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) rather than completing it. |

### Option B — Event-store per-strategy position ledger (extend ADR 0029)

Stop feeding strategy evaluation raw broker net. Derive each runner's `context.positions` from the event store: submitted `trades` rows filtered to **that runner's `strategy_id`** (`strategy_name` column), summed per symbol (buys minus sells). Reconcile the per-strategy ledger against the account net on every cycle or via a dedicated reconcile surface; broker net remains authoritative for account-level facts per [ADR 0010](0010-hybrid-source-of-truth.md).

| | |
| --- | --- |
| **Pros** | Completes the architecture [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) started — one attribution module, not a parallel position model; preserves [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) shared account and per-process isolation; keeps [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) account-scoped enforcement on live broker positions at the risk layer; `attribution.py` already owns the `trades`-walk rules (submitted-only filter, operator pseudo-strategy, opening-fill semantics) — quantity ledger is the natural extension; fixes the observed spurious re-entry (`rsi2` would still read +13 SPY from its own submitted fills after `vwap_trend`'s sell); no broker API or capital fragmentation changes. |
| **Cons** | Broker net can diverge from any single strategy's ledger when siblings offset (by design — reconciliation must surface this); ledger can drift if operator manual trades occur outside Milodex ([ADR 0010](0010-hybrid-source-of-truth.md) mismatch path); does not eliminate same-symbol opposite-order wash-trade risk at the broker when two strategies legitimately trade the same symbol concurrently — only fixes the **position view** bug; requires runner change at [`runner.py:256`](../../src/milodex/strategies/runner.py) and [`runner.py:519-524`](../../src/milodex/strategies/runner.py); cross-process cap race noted in [ADR 0026 addendum (2026-05-30)](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) remains a separate hard gate before live capital. |
| **Relation to existing ADRs** | Directly extends [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) Decision 2 (reconstruct from `trades`, no new table). Honors [ADR 0010](0010-hybrid-source-of-truth.md): Alpaca wins for account state; Milodex wins for per-strategy derived position. Risk layer continues to query broker positions for [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) caps and [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) attribution — runner catches up to the same source. |

## Decision

**Adopt Option B: event-store per-strategy position ledger, implemented as an extension of [`src/milodex/risk/attribution.py`](../../src/milodex/risk/attribution.py), not a parallel module.**

Specifically:

1. **Runner `context.positions` is strategy-scoped.** `_current_positions()` ([`runner.py:519-524`](../../src/milodex/strategies/runner.py)) MUST return quantities derived from submitted `trades` where `strategy_name` equals the running strategy's `strategy_id`, not from `broker.get_positions()` net. The evaluation call site at [`runner.py:256`](../../src/milodex/strategies/runner.py) consumes this dict unchanged in shape.

2. **One attribution module owns both concerns.** Per [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) Non-goals ("does not pre-commit specific implementation code" — now committed here): add a quantity helper alongside `attribute_position()` — e.g. `strategy_position_quantity(strategy_id, symbol, event_store) -> float` — using the same submitted-only filter and symbol normalization as existing helpers. `count_positions_by_strategy()` and the risk evaluator continue to use broker positions as the position **existence** set and attribution for **ownership of broker net**; the new helper answers **how many shares this strategy believes it holds** regardless of account net.

3. **Broker net stays authoritative for account-level checks.** [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) and [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) risk-layer semantics are unchanged. The risk evaluator still receives live broker positions in `EvaluationContext`; it does not switch to strategy-scoped quantities for account caps.

4. **Reconciliation is mandatory and visible.** When `sum(per-strategy quantity for symbol) != broker net quantity for symbol`, log a reconciliation WARN per [ADR 0010](0010-hybrid-source-of-truth.md) and surface it in operator diagnostics (`milodex status` / GUI). Do not silently overwrite either side. The 2026-06-03 incident is the canonical example of *expected* divergence during concurrent same-symbol trading; reconciliation makes that divergence inspectable instead of misinterpreted as "flat."

5. **Option A remains an operator escape hatch, not the platform default.** For a strategy pair that cannot tolerate shared-symbol broker netting even with a correct ledger (e.g. persistent wash-trade collisions), the operator may run them on separate accounts. Milodex does not automate multi-account provisioning in this ADR.

6. **Interim guardrail stays until the runner ships strategy-scoped positions.** Do not co-run multiple strategies on one symbol and one account until the implementation below lands and is verified. This ADR does not lift the guardrail on acceptance alone.

## Rationale

- **The bug is an implementation gap, not a missing broker feature.** [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) already decided that per-strategy ownership is reconstructed from `trades` on demand and that Alpaca positions carry no strategy tag. The risk layer was updated; the runner was not. Building a second isolation layer (Option A) before completing Option B duplicates capital surfaces and evades the durable-history model the project already chose.

- **Option B fixes the demonstrated failure.** In the 2026-06-03 trace, `meanrev.rsi2`'s submitted BUY (+13) minus its own submitted SELLs (none before re-entry) yields +13 regardless of `momentum.vwap_trend`'s offsetting sell. Strategy evaluation would not have emitted a spurious re-entry. That is the precise defect at [`runner.py:256`](../../src/milodex/strategies/runner.py).

- **Option A trades one class of bug for operational complexity.** Multiple paper accounts obscure fleet P&L, kill-switch scope ([ADR 0005](0005-kill-switch-manual-reset.md) is account-wide in the event store), and the sub-$1k live-capital constraint in phase one. It is defensible for a pairwise escape, not as the default multi-strategy architecture.

- **Consistency with [`attribution.py`](../../src/milodex/risk/attribution.py).** `attribute_position()` answers "who owns the broker's current non-zero lot?" via opening-fill walk. The quantity ledger answers "what does strategy X think it holds?" via `strategy_name`-filtered running balance. Both read the same `trades` table, the same `status="submitted"` filter, and the same `OPERATOR_ATTRIBUTION` pseudo-strategy. A parallel position table or runner-local cache would reintroduce the drift [ADR 0029 Rationale](0029-per-strategy-position-attribution-at-risk-layer.md) rejected.

- **Honest about residual broker risk.** Same-symbol concurrent intraday strategies on one account can still place opposite-side orders that Alpaca rejects (`40310000`). This ADR fixes position **interpretation**; wash-trade collisions may still require symbol exclusivity, staggered tempo, or the cross-process submit serialization deferred in [ADR 0026 addendum (2026-05-30)](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) before live capital. Naming that limit matches [FOUNDER_INTENT.md](../FOUNDER_INTENT.md) priority #1 (trustworthy).

## Implementation sketch

No code in this ADR — prose sequence only:

1. **`attribution.py`:** Add `strategy_position_quantity(strategy_id, symbol, event_store) -> float` (and optionally `strategy_positions(strategy_id, event_store) -> dict[str, float]`) walking submitted rows for that `strategy_name` only, oldest-first running balance, clamped at zero per existing sell semantics in `attribute_position()`.

2. **`runner.py`:** Replace `_current_positions()` body to call the new helper scoped to `self._loaded.context.strategy_id` (or equivalent runner-bound id). Keep the return type `dict[str, float]` so strategy templates need not change.

3. **Reconciliation:** On runner cycle (or batched in `milodex reconcile`), for each symbol in the union of broker positions and strategy ledger keys, compare broker net vs sum of per-strategy ledgers including `operator`. Emit WARN with symbol, broker qty, per-strategy breakdown when `abs(broker - sum) > epsilon`.

4. **Tests:** Pin the 2026-06-03 scenario — strategy A buys, strategy B sells same size, strategy A's `_current_positions()` still reports its lot; broker reports flat; reconciliation WARN fires. Regression for [`runner.py:256`](../../src/milodex/strategies/runner.py) context wiring.

5. **Docs / ops:** Lift the interim same-symbol+account co-run ban only after the above tests pass and one supervised soak confirms reconciliation behaves under the intraday fleet.

6. **Out of scope for this ADR's first PR:** Cross-process submit serialization ([ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) live-capital gate), runner crash-on-broker-rejection robustness (separate follow-up), automated enforcement of the interim guardrail in CLI/GUI.

## Consequences

- **`_current_positions()` semantics change** from account net to strategy ledger at [`runner.py:519-524`](../../src/milodex/strategies/runner.py). Any test or operator assumption that "runner positions == Alpaca positions" becomes false by design during concurrent same-symbol operation.

- **Per-strategy DB trade rows still do not equal live broker position for that strategy** when siblings offset — but they **do** equal the strategy-scoped ledger the runner should use. Operator diagnostics must distinguish "my ledger" vs "account net" (see 2026-06-03 incident in Context above).

- **Risk evaluator and [`attribution.py`](../../src/milodex/risk/attribution.py) public contracts remain stable**; new helpers extend the module. No schema migration ([ADR 0029 Decision 8](0029-per-strategy-position-attribution-at-risk-layer.md)).

- **[ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) and [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md) are extended, not superseded.** Account caps count broker positions; per-strategy caps count attributed broker positions; runner logic counts strategy-ledger positions. *(Amended 2026-06-15 — see [Amendment](#amendment-2026-06-15--per-strategy-cap-reads-the-strategy-ledger) below.)*

## Amendment (2026-06-15) — per-strategy cap reads the strategy ledger

This ADR left a gap it named but did not close: the per-strategy concurrent-positions cap (`_check_strategy_concurrent_positions`, [ADR 0029](0029-per-strategy-position-attribution-at-risk-layer.md)) still enumerated **broker-net** `context.positions` and re-attributed each symbol. When a sibling's offsetting position nets the broker flat for a symbol this strategy holds — the exact 2026-06-03 case in the Context above (rsi2 +13 / vwap_trend −13 → account flat) — the symbol never enters the broker-net enumeration, the strategy's own lot is invisible, and the cap **fails open** (undercounts). The runner's `_current_positions()` was already fixed to read the strategy ledger; the risk cap was not, so the two disagreed.

The concurrent-intraday-runners work (2026-06-15, PR3) closes it: on the **live/paper path** `_check_strategy_concurrent_positions` now derives the owned set from `strategy_positions(proposing_strategy, event_store)` — the same strategy-scoped ledger the runner trusts. The cap and the runner now agree on what a strategy holds, independent of broker netting. The **backtest path** (ADR 0030 `is_backtest`) is unchanged: a single-strategy replay has no sibling netting, and `strategy_positions` is paper-source-only, so it keeps the broker-net + `attribute_position(source="backtest")` reconstruction. So the consequence line above now reads: per-strategy caps count **strategy-ledger** positions on the live/paper path (attributed broker positions only in backtest).

- **Interim guardrail:** multiple strategies on the same symbol and same account remain forbidden until implementation + soak verification. Acceptance of this ADR alone does not authorize lifting the ban.

- **Wash-trade (`40310000`) risk may persist** under concurrent same-symbol trading even after ledger fix; track as input to the live-capital serialization gate in [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md).

## Non-goals

- This ADR does **not** mandate per-strategy Alpaca accounts (Option A as default).
- This ADR does **not** add a `position_attribution` table or runner-local position cache.
- This ADR does **not** change broker-side lot semantics — Alpaca continues to net per account per symbol.
- This ADR does **not** implement cross-process order serialization (deferred live-capital gate).
- This ADR does **not** alter [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) per-process supervisor model.
- This ADR does **not** fix runner fatal exit on routine broker rejections (separate robustness work).
