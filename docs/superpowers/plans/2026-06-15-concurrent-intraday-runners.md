# Plan — Run all paper runners concurrently (remove one-runner-per-symbol)

**Date:** 2026-06-15
**Status:** Ready to execute (not started)
**Goal:** Let every deployable paper strategy run at the same time, including multiple strategies that share an evaluation symbol (e.g. the six SPY strategies), without corrupting positions, tripping broker wash-trade rejections, or overshooting account caps.

## Why this exists

Today the launch guard at `src/milodex/cli/commands/strategy.py:176` refuses to start a second runner whose `universe[0]` matches a live one. With 11 deployable paper strategies pointing at only **3 distinct symbols** (SPY×6, AAPL×3, XLB×2), the practical concurrency ceiling is **3** — verified empirically 2026-06-15 (`fleet deploy` of all 11 launched 3, refused 8 with `evaluation symbol … already used (ADR 0055)`; machine load was trivial — 31% CPU, 8 GB free, so the cap is a correctness lock, not a resource limit).

The guard is a **coarse proxy**. The real requirement is three separable invariants on a **single shared Alpaca account**. Close them individually and the guard becomes unnecessary.

## The real invariant (what the guard stands in for)

| # | Invariant | Status today | Failure if violated |
|---|-----------|--------------|---------------------|
| 1 | Each strategy evaluates against **its own** lot, not account net | **Held for the runner** (`runner.py:651` `_current_positions()` → `strategy_positions`); **NOT held in the per-strategy risk cap** (reads broker-net — see PR3) | Spurious re-entry / phantom flat (ADR 0055 rsi2 +13 / vwap_trend −13 case) |
| 2 | No opposite-side order on the same symbol while another rests | Not checked; broker rejects with `40310000` | Wash-trade reject. **Note:** already *survivable* — the reject is caught at `service.py:359` and recorded REJECTED, it does **not** crash the runner. So this is audit hygiene, not crash-prevention. |
| 3 | No account-cap overshoot from two stale-snapshot fires | **Paper is lock-free** (`service.py:208-210` serializes `micro_live`/`live` only) | Two concurrent submits both clear a cap on a stale snapshot |

## Grounding (verified 2026-06-15, file:line)

- Launch guard: `src/milodex/cli/commands/strategy.py:176` (`raise ValueError` on eval-symbol collision). Mirrors: `.claude/skills/fleet-ops/scripts/fleet.py:136-140` (batch pre-check), `src/milodex/commands/bench.py` `_peek_eval_symbol_collision`.
- Serialization toggle: `src/milodex/execution/service.py:201-210` `_should_serialize_submit` → `_effective_stage(intent) in {"micro_live","live"}`; `_effective_stage` (`service.py:212`) uses the runner-bound `expected_stage` (`runner.py:674`).
- Wash-trade reject handling: `src/milodex/broker/alpaca_client.py:202-208` maps the API error → `OrderRejectedError`; caught at `src/milodex/execution/service.py:359-385`, records REJECTED, returns (no raise).
- Per-strategy cap: `src/milodex/risk/evaluator.py:697-812` `_check_strategy_concurrent_positions` enumerates `context.positions` (the **risk** `EvaluationContext.positions`, filled from **broker-net** in the service), then re-attributes each via `attribute_position`. Under broker netting a flat net hides the strategy's own lot → undercounts (fails open).
- Resting-order data source for PR2: `EvaluationContext.recent_orders` exists (`src/milodex/risk/evaluator.py:43`), populated by `broker.get_orders(limit=100)` at `service.py:634`, already consumed by the duplicate-order veto (`evaluator.py:630`). Account-scoped, which is exactly what `40310000` keys on. No new broker call needed.
- Universe handling: runner **fetches** the whole universe (`runner.py:585-591`) but **evaluates only `universe[0]`** (`primary_bars`, `runner.py:294`; `_evaluation_symbol` = `universe[0]`, `runner.py:619-621`). The stale-bar watermark is keyed to that primary symbol — fine for 1D, a latent stall for multi-symbol **intraday**.

## PR sequence

Order matters: caps and collision must be correct **before** the guard is removed, because removing the guard is what first allows two writers on one symbol.

### PR1 — Serialize paper submits *(tiny)* — closes invariant 3
- Change `_should_serialize_submit` (`service.py:208-210`) to include `"paper"`: `return self._effective_stage(intent) in {"paper","micro_live","live"}`. Keep the `source == "backtest"` early-return.
- **Amends ADR 0056** ("paper stays lock-free by design") — update that ADR; the lock-free rationale no longer holds once paper runs concurrently on one account.
- **Honest scope:** the lock makes Milodex's own submit critical sections mutually exclusive per account. It does **not** make caps atomic against *asynchronous broker fills* (a just-submitted order may not be in the next snapshot). It reduces paper to the same residual the live path already carries — economically nil at 5Min × ~11 strategies, <$1k intents, $101k account.
- **Acceptance:** existing serialization tests extended to paper; a test asserting two concurrent paper submits don't both clear a cap on a shared stale snapshot.

### PR2 — Decline opposite-side order when one rests *(small)* — closes invariant 2
- New risk check in `risk/evaluator.py` (next to `_check_duplicate_order`): decline the intent if `context.recent_orders` contains an **open** order on `intent.symbol` with `side != intent.side`. New reason code, e.g. `opposite_side_order_open`.
- Lives in the **sacred risk layer** (risk disposes) — do not put it in the runner or execution submit path. Composes with PR1: holding the account lock makes the `recent_orders` snapshot current at submit time.
- Do **not** use `strategy_positions` for this — that ledger folds filled/submitted lots and structurally cannot see a *resting* order (`attribution.py:302-316`; no order-level attribution, `evaluator.py:625-627`). The order book is the right primitive.
- **Acceptance:** `test_evaluator.py` case — resting BUY on SPY + incoming SELL on SPY → declined with the new reason code; same-side passes; different-symbol passes.

### PR3 — Per-strategy cap counts the strategy's own lots *(small)* — closes invariant 1 (the half that's broken)
- In `_check_strategy_concurrent_positions` (`evaluator.py:760-795`), derive the owned set from the **strategy-scoped ledger** (`strategy_positions(proposing_strategy)` / `strategy_open_lots`) instead of filtering broker-net `context.positions`. The runner already trusts this source (`runner.py:651`); the risk cap must match it.
- **ADR 0029 / 0055** touch (per-strategy attribution is authoritative for the per-strategy cap).
- **Acceptance:** `test_evaluator.py` — strategy A long SPY + strategy B short SPY (broker net flat) → A's per-strategy count still reflects its lot; cap triggers correctly instead of failing open.

### PR4 — Remove the launch guard *(tiny)* — only after PR1–PR3 merge
- Delete the `raise` at `strategy.py:176` and the mirrors (`fleet.py:136-140`, `bench.py` `_peek_eval_symbol_collision`).
- **Keep** the per-strategy runner advisory lock (still prevents same-strategy double-launch).
- **Supersedes** the ADR 0026 addendum (2026-06-05 same-symbol co-run refusal) — write the superseding note.
- **Acceptance (the empirical test from 2026-06-15, re-run):** `fleet deploy` all 11 → **all 11 launch**; `sample_health.py --samples 6 --interval 300` clean; force a same-symbol opposite-side scenario and confirm PR2 declines it (logged, no crash); confirm caps hold.

### PR5 — Per-symbol watermark *(small, DEFERRED)* — only if running multi-symbol *intraday* in one runner
- Generalize the intraday stale-bar watermark (`runner.py:288-294`) off `universe[0]` to per-symbol, so a multi-symbol intraday runner doesn't stall on the primary symbol's bar timing. Not needed for the "many single-symbol runners" goal; needed before one-runner-many-intraday-symbols.

## Open items to verify before/while executing (lower confidence — do not treat as settled)

- **Partial-fill ledger reconciliation.** `_record_execution` appears to write the *requested* quantity at submit time; check whether it's ever reconciled to broker `filled_qty` (start at `service.py:856-860`). The current one-writer-per-symbol guard masks the blast radius; removing it (PR4) exposes it. Verify before PR4 — if real, it's a prerequisite, not optional.
- Confirm `EvaluationContext.positions` is in fact broker-net at the call site (trace how the service builds the risk context vs. the runner's strategy context) — PR3's premise. Strongly indicated but worth a 5-minute confirmation.

## Side question — can the universe expand well past SPY/AAPL/XLB?

**Yes, and it's the cheapest concurrency win — independent of this plan.** Under the current guard, *each new distinct symbol is another concurrent runner slot.* The guard only bites when strategies pile onto the **same** symbol.

- **Ceiling:** ADR 0016 — Phase 1 is **long-only U.S. common stocks + plain-vanilla ETFs**. Crypto, options, leveraged/inverse/volatility ETFs, and shorts are forbidden until a superseding (Phase 2+) ADR. Enforced at universe-manifest load. Within US long-only equities/ETFs, expand freely.
- **Daily: data-ready now.** `market_cache/1Day/` holds ~40 symbols (all sector SPDRs, SPY/SHY/QQQ/IWM/DIA, megacaps, bond/commodity ETFs) and `market_cache/v2/1Day/` ~100 more largecaps. New daily strategies on distinct symbols can stand up today.
- **Intraday: gated on data warmup, not the engine.** The 5Min cache is **SPY-only** (`market_cache/v3/5Min/SPY.parquet`). Any new intraday symbol needs `milodex data fetch-universe` warmup first (heal/backfill: `--force`). This is the real prerequisite for more intraday concurrency, separate from removing the guard.

## Constraints the executing session must respect (from CLAUDE.md)

- **Risk layer is sacred** — PR2 belongs in `risk/`, never the submit path or runner. Risk disposes; never weaken a cap for concurrency.
- **Kill switch is manual-reset** — never auto-resume or script around it.
- **Until PR4 merges, the co-run guard is live** — use `fleet-ops` (`deploy`/`stop`/`verify`) and the project venv interpreter; daily runners are no-ops while the market is open (by design — not a stall).
- Tests mirror `tests/milodex/`; run `python -m pytest`. Touching `risk/`, `execution/`, `promotion/`, `broker/`, or runner stop/kill semantics → dispatch the **risk-invariant-reviewer** agent before merge.

## Recommended skills for the executing session

- `superpowers:test-driven-development` (PR2/PR3 are pure logic in the risk layer — TDD them).
- `risk-invariant-reviewer` agent — mandatory pre-merge review for every PR here (all touch risk/execution).
- `fleet-ops` + `fleet-health` — for the PR4 empirical acceptance run.
- `superpowers:executing-plans` — to drive this plan with review checkpoints.
