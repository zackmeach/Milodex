# Investigation: Runner Startup, 2026-04-29

**Status:** read-only investigation. No state mutations. No code changes applied. No broker actions.

**Operator question:** _"new day! 8:58 AM, do we run the runner?"_ — followed by `milodex strategy run regime.daily.sma200_rotation.spy_shy.v1` and a `controlled_stop` after one cycle.

**One-paragraph answer:** The runner did exactly what it should — connected, evaluated, and proposed a regime rotation. Risk caught the proposal on three independent grounds and zero orders reached Alpaca. Two underlying conditions made the cycle noisy rather than boring: (1) the local event store has accumulated phantom records from earlier dev/test sessions and the reconcile machinery surfaces them as an incident every time, and (2) the regime strategy's "rotate to target" sweep is unscoped — it proposes selling _every_ broker position that isn't the target symbol, including positions that belong to the meanrev strategy. Both have known scaffolded markers or are tracked in the §7 backlog; only the regime-scope question is a real open design call.

---

## Timeline (UTC / ET)

| Time | Event | Source |
|---|---|---|
| 13:02:19 / 9:02:19 | Operator ran `milodex reconcile` — DRIFT DETECTED, incident `explanations#14546` recorded (hash `1c81d3c799e9…`). 1 position mismatch, 3 order mismatches. | event store |
| 13:02:48 / 9:02:48 | Operator ran `milodex strategy run regime.daily.sma200_rotation.spy_shy.v1`. Session `89d82e2c-df00-435b-9744-3f7a8c47e7b7` opened. | `strategy_runs` row |
| 13:02:50 / 9:02:50 | Strategy fired. Rule `regime.ma_filter_cross` → "rotate to SPY" (latest close 711.24 above 200-DMA). Four `TradeIntent`s generated: SELL AVGO 24, SELL GLD 23, SELL SLV 152, BUY SPY 12. | runner cycle log |
| 13:02:50–51 | All four submits **blocked** by risk. Reason codes (consistent across all four): `market_closed`, `stale_market_data`, `max_concurrent_positions_exceeded`. Zero broker activity. | `explanations#14547–14550`, `trades#10663–10666` |
| 13:06:20 / 9:06:20 | Operator Ctrl+C → `c`. Session ended `controlled_stop`. | `strategy_runs` row |

Independent confirmation via fresh `milodex reconcile` at 13:10 UTC: same drift, deduplicated by content hash (R-OPS-010 idempotency working as designed).

---

## Root-Cause Taxonomy

### A. Reconcile incident — entirely from known scaffolded gaps

The 13:02:19 incident has two reason codes and is **not new** behavior:

#### A.1 `order_local_only_recent` (3 orders: SLV `928377c1…`, AVGO `a8027ea2…`, GLD `af288310…`)

These are yesterday's meanrev fills that the local trade rows still mark `submitted` because we never sync the `submitted` → `filled` transition back from the broker. The reconcile loop only checks the broker's **open** orders ([reconcile.py:237-238](../../src/milodex/cli/commands/reconcile.py#L237) — `status="open"`), so a filled order looks "missing at broker" from our side. A `_LOCAL_ONLY_INCIDENT_WINDOW = timedelta(hours=24)` ([reconcile.py:51](../../src/milodex/cli/commands/reconcile.py#L51)) gates them as incidents while fresh; tomorrow they'll degrade to warnings.

This is the deferred `filled_since_last_sync` reconciliation dimension — already a `# scaffolded:` marker in code and listed in the [ENGINEERING_STANDARDS Scaffolded Inventory](../ENGINEERING_STANDARDS.md#scaffolded-inventory). Closes when "R-OPS-004 v1.2 follow-up implements all eight `OPERATIONS.md` dimensions."

**Cross-check confirms this interpretation:** all three symbols (AVGO 24, GLD 23, SLV 152) appear as `kind: ok` matched positions in today's reconcile output — broker and local agree they are positions. The orders that produced them filled. Only the local order rows are stale.

#### A.2 `position_local_only` — SPY 210 phantom

Our local fold ([reconcile.py:_fold_positions](../../src/milodex/cli/commands/reconcile.py#L292)) sums signed quantity over every paper trade in `{submitted, accepted, filled}` status. Walking SPY history (669 rows total — most are dev artifacts):

- Trades with `broker_order_id = order-paper-1` from 2026-04-21 through 2026-04-23 are **simulated-broker** records that never went to Alpaca but still count toward the local fold.
- Real Alpaca submissions during regime sessions `ac0a6620` and `09877e1e` (2026-04-23) added another +13 SPY (12 + 1).
- Meanrev session `7f576851` sold 13 SPY on 2026-04-24 (the only real-Alpaca sell that landed in `submitted` status).
- Net local fold: **+210**. Net broker reality: **0**.

The local audit trail is intentionally append-only (R-XC-008), so we don't rewrite history when the simulated broker was swapped for the real one. The phantom will persist until either:
- (i) `filled_since_last_sync` lands and we reconcile fills back to a stored positions table that supersedes the fold, or
- (ii) we add a one-time "trim simulated-broker leftovers" migration that records compensating audit entries (not deletes), or
- (iii) we stop including `order-paper-1`-keyed trades in the position fold.

None of these is urgent — the phantom doesn't affect broker behavior, only reconcile output.

#### A.3 The other 12 `~`-flagged stale orders (BAC, JPM, JNJ, more SPY)

All >24h old → warnings, not incidents. Same root cause as A.1.

### B. Submit-gate doesn't refuse on drift — also known scaffolded gap

The runner started successfully despite a fresh DRIFT DETECTED incident from 30 seconds prior because submit-gate refusal on detected drift is itself scaffolded ([reconcile.py:716-721](../../src/milodex/cli/commands/reconcile.py#L716), [ENGINEERING_STANDARDS Scaffolded Inventory](../ENGINEERING_STANDARDS.md#scaffolded-inventory) row 2). Operator self-enforcement is the documented current contract. R-OPS-004 follow-up wires this into `ExecutionService.submit_paper`.

### C. Regime strategy proposes liquidating non-universe positions — newly surfaced

This is the only finding that **isn't already tracked**.

[`regime_spy_shy_200dma.py:93-103`](../../src/milodex/strategies/regime_spy_shy_200dma.py#L93):

```python
intents: list[TradeIntent] = []
for symbol, quantity in normalized_positions.items():
    if symbol != target_symbol:
        intents.append(
            TradeIntent(
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                order_type=OrderType.MARKET,
            )
        )
```

`normalized_positions` originates from `runner._current_positions()` ([runner.py:245-250](../../src/milodex/strategies/runner.py#L245)), which returns _all_ broker positions with no universe filter. The rotation rule then iterates that full set and proposes a SELL for any symbol that isn't the target. There is no check that the symbol belongs to the strategy's universe (`SPY`, `SHY`).

**This morning's manifestation:** Yesterday's meanrev session (`2506708f-…`) bought AVGO 24, GLD 23, SLV 152 and the broker still holds them. When regime started today and saw "rotate to SPY," the loop generated SELL intents for all three meanrev positions in addition to the BUY SPY leg. Risk vetoed all four (markets weren't open yet, data was stale, position-count gate blew), so nothing materially happened.

If risk had been silent — for example, if the runner had been started 35 minutes later when markets were open and data fresh — regime would have liquidated the meanrev positions to fund a SPY rotation. That's not what an operator running both strategies in the same paper account expects. It contradicts the [ROADMAP_PHASE1.md §8 item 7 pre-flight](../ROADMAP_PHASE1.md) precedent for meanrev (which sensibly says "leave them and let meanrev manage them via its RSI exit rule" — i.e., strategy-scoped position handling) and contradicts the obvious reading of the strategy YAML: `universe: [SPY, SHY]`.

**This is the only finding requiring a real decision.**

---

## Open Decisions for the Operator

### Decision 1 — Today's runner: what to do about leftover meanrev positions

| Option | Effect | Cost |
|---|---|---|
| (a) Liquidate AVGO/GLD/SLV manually before next regime run | Regime starts clean; rotation behaves as documented. | 3 manual `trade submit SELL` invocations. Realizes whatever P&L meanrev has on the positions. Forfeits the meanrev RSI-exit logic that would otherwise dispose of them. |
| (b) Accept the regime sweep | Whenever risk allows, regime liquidates meanrev's positions to rotate. | Cross-strategy interference. Defeats the purpose of running both strategies in one paper account. Realizes meanrev P&L on regime's clock, not meanrev's exit rule. |
| (c) Don't run regime today; let meanrev close out the positions via its exit rule | Meanrev RSI exit handles AVGO/GLD/SLV per its own logic. Regime sits idle. | One day of delay on regime's session. No structural fix. |
| (d) Patch regime to be universe-scoped, then run | Architectural fix. Both strategies coexist cleanly in one paper account. | Code change + tests + ADR-level documentation of the design call. ~30–60 min of focused work. |

I have not chosen on your behalf. (d) looks like the correct long-term direction; (a) or (c) are reasonable for today depending on whether you want forward motion on regime or on meanrev.

### Decision 2 — Phantom records cleanup (low urgency)

The 210 SPY phantom and the 12 stale `~` orders make every reconcile output noisy. Three paths:

| Option | Effect | Cost |
|---|---|---|
| (i) Wait for R-OPS-004 v1.2 (`filled_since_last_sync`) | Closes the loop properly; the phantom resolves itself once we sync fills and switch to a stored-positions source-of-truth. | Already on the roadmap. No new work. Reconcile stays noisy until then. |
| (ii) One-shot "trim" migration | Compensating audit entries that bring the fold to broker reality. Audit trail intact. | A few hours of careful work. Adds complexity to the migration story. Should only be done if the noise is actively harmful. |
| (iii) Exclude `order-paper-1` keyed rows from the position fold | Surgical, removes ~all of the simulated-broker contribution. | Small code change in `_fold_positions`. Doesn't help with R-OPS-004 v1.2 — at best a stopgap. |

My read: stick with (i). The noise is annoying but not load-bearing.

---

## Proposed Diff for Decision 1 (d) — Universe-Scoped Regime Rotation

**Not applied.** Provided here so the operator can review before approving.

```python
# src/milodex/strategies/regime_spy_shy_200dma.py
# In .evaluate(), replacing lines 93-103:

universe = {symbol.upper() for symbol in context.universe}
intents: list[TradeIntent] = []
for symbol, quantity in normalized_positions.items():
    if symbol == target_symbol:
        continue
    if symbol not in universe:
        # Position belongs to another strategy in the same account.
        # Regime owns rotation within its universe only.
        continue
    intents.append(
        TradeIntent(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=quantity,
            order_type=OrderType.MARKET,
        )
    )
```

**Test addition** (`tests/milodex/strategies/test_regime_spy_shy_200dma.py`):

```python
def test_regime_does_not_liquidate_non_universe_positions():
    """Regime rotation is scoped to its universe; foreign positions are ignored."""
    bars = _bars_with_close_above_200dma()
    context = StrategyContext(
        universe=("SPY", "SHY"),
        parameters={
            "ma_filter_length": 200,
            "risk_on_symbol": "SPY",
            "risk_off_symbol": "SHY",
            "allocation_pct": 0.1,
        },
        positions={"SHY": 50.0, "AVGO": 24.0, "GLD": 23.0},  # meanrev leftovers present
        equity=100_000.0,
        # ... existing fixture fields
    )
    decision = RegimeSpyShy200DmaStrategy().evaluate(bars, context)
    sells = [i for i in decision.intents if i.side is OrderSide.SELL]
    sell_symbols = {i.symbol for i in sells}
    assert sell_symbols == {"SHY"}, (
        f"Regime should only rotate within its universe; got SELL for {sell_symbols}"
    )
```

**Companion ADR** (`docs/adr/0022-strategy-rotation-scope.md`, new):

> Decision: Strategy rotation rules operate strictly within the strategy's declared universe. Positions outside the universe are not the strategy's concern, even when held in the same broker account.
>
> Rationale: Phase 1 design runs two strategies (regime + meanrev) in one paper account. The original "rotate to target" implementation treated the entire account as the strategy's territory, which would cause regime to liquidate meanrev's positions on every rotation cycle. That contradicts both the strategy YAML (universe is explicitly `[SPY, SHY]`) and the operator playbook (ROADMAP §8 item 7 pre-flight, which already documents "let meanrev manage them via its RSI exit rule"). Universe-scoping makes the strategies actually independent.
>
> Trade-off: A strategy can no longer "clean up" non-universe positions automatically. Operator (or another strategy) must manage those. This is the correct boundary.

The diff is small, the test is straightforward, and the ADR documents the design call. Estimated 30–60 min of focused work plus a code review pass.

---

## What I Did Not Do

- **No broker actions.** No orders placed, cancelled, or modified.
- **No event-store mutations.** Audit trail untouched.
- **No code changes applied.** The diff above is a proposal; `regime_spy_shy_200dma.py` is unchanged on disk. No new branch.
- **No risk-layer adjustments.** Three independent vetoes all worked as designed.

All findings are derived from read-only DB queries, fresh `milodex reconcile` invocations (which write idempotent incidents — already deduplicated by content hash), `pytest tests/milodex/strategies/` (47 passed, baseline clean), and code reading.

## What I Recommend Doing Next

1. **Decide Decision 1.** If (d), I can implement the diff + test + ADR on a feature branch and bring it back for review.
2. **Defer Decision 2.** Track within R-OPS-004 v1.2; today's noise is tolerable.
3. **Resume regime paper run** after the choice from #1 is in. Pre-flight (per [ROADMAP §8 item 7 pattern](../ROADMAP_PHASE1.md)): `status` → `reconcile` → `promotion manifest regime.daily.sma200_rotation.spy_shy.v1` → `strategy run`.
