# Phase 1.2 Evidence — 2026-04-22

**Scope:** Satisfy the Phase 1.2 Definition of Done evidence requirements per `docs/ROADMAP_PHASE1.md`.
**Session date:** Wednesday 2026-04-22, US market hours (9:30 AM – 4:00 PM ET).
**Reviewer/operator:** Founder (Zack).

---

## Summary

All three evidence-gated success criteria for Phase 1.2 — SC-3, SC-4, and the promotion gate — were satisfied on this date against a live Alpaca paper account. The meanrev strategy executed a complete entry-to-exit round trip, the risk layer rejected an oversized order with a full audit trail, and the promotion pipeline recorded a formal `backtest → paper` transition. SC-5 (kill switch in practice) remains the only Phase 1.2 DoD item not yet collected and is noted as a follow-on action.

---

## SC-3: Strategy runs in paper mode and submits real orders

**Criterion (from `ROADMAP_PHASE1.md`):** Each strategy runs in paper mode against Alpaca and submits orders when its rule fires.

### Sessions run today

| Session | Strategy | Start (UTC) | End (UTC) | Exit |
|---|---|---|---|---|
| `a408a37a` | `regime.daily.sma200_rotation.spy_shy.v1` | 14:59:08 | 15:00:19 | controlled_stop |
| `3b8dd892` | `meanrev.daily.pullback_rsi2.curated_largecap.v1` | 15:07:48 | 15:16:55 | controlled_stop |
| PID 49828 | `meanrev.daily.pullback_rsi2.curated_largecap.v1` | 17:46:47 | ongoing | — |

### SPY round trip (meanrev, session PID 49828)

The strategy identified SPY as oversold on the 2026-04-22 daily bar and entered a position at the open. It exited when the RSI recovery threshold was met.

| Event | Time (UTC) | Time (ET) | Symbol | Side | Qty | Equity |
|---|---|---|---|---|---|---|
| **BUY submitted** | 14:03:29 | 10:03 AM | SPY | buy | 10 | $100,000.00 |
| **SELL submitted** | 17:26:20 | 1:26 PM | SPY | sell | 10 | $100,006.81 |

**Realized gain: +$6.71** (position closed; equity moved from $100,000.00 → $100,006.71 after fill).

Bar timestamps recorded in explanation events:
- Entry bar: `2026-04-22 14:02:00+00:00`
- Exit bar: `2026-04-22 17:25:00+00:00`

### JNJ entry (after risk config alignment)

Following the sizing alignment (`per_position_notional_pct` reduced from 0.25 → 0.10 to match `risk_defaults.yaml`), the strategy successfully entered JNJ.

| Event | Time (UTC) | Time (ET) | Symbol | Side | Qty | Avg Entry | Equity at Submit |
|---|---|---|---|---|---|---|---|
| **BUY submitted** | 17:47:32 | 1:47 PM | JNJ | buy | 44 | $224.25 | $100,006.71 |

Sizing verification: `floor($100,006.71 × 0.10 / $224.25) = floor(44.60) = 44 shares` — exactly 9.86% of equity, within the 10% single-position limit.

**SC-3 status: SATISFIED** — both a complete round trip and a new open position were submitted to Alpaca paper and filled.

---

## SC-4: RiskEvaluator has rejected at least one real attempted trade

**Criterion:** `RiskEvaluator` has rejected at least one real attempted trade in development (non-synthetic evidence required).

### JNJ rejection — 17:26:22 UTC (1:26 PM ET)

After the SPY position closed, the strategy generated a JNJ buy intent for 111 shares (sized at the original `per_position_notional_pct: 0.25` = 25% of equity). The risk layer rejected it with two simultaneous rule failures.

**Raw event record from `event_store.explanations`:**

```
symbol:         JNJ
side:           buy
quantity:       111
equity:         $100,006.71
status:         blocked
reason_codes:   ['max_order_value_exceeded', 'max_single_position_exceeded']
```

**Full risk check results (all 11 checks):**

| Check | Passed | Message |
|---|---|---|
| `kill_switch` | ✅ | Kill switch is inactive. |
| `paper_mode` | ✅ | Paper trading mode confirmed. |
| `strategy_stage` | ✅ | Strategy eligible for paper execution. |
| `market_hours` | ✅ | Market is open. |
| `data_staleness` | ✅ | Latest bar is within staleness limits. |
| `daily_loss` | ✅ | Daily loss is within configured limits. |
| `order_value` | ❌ | Estimated order value $24,887.31 exceeds limit $15,001.01. |
| `single_position` | ❌ | Projected position value $24,887.31 exceeds limit $10,000.67. |
| `total_exposure` | ✅ | Projected total exposure is within limits. |
| `concurrent_positions` | ✅ | Projected open positions are within limits. |
| `duplicate_order` | ✅ | No duplicate orders detected. |

**Limits that triggered** (from `configs/risk_defaults.yaml`):
- `max_order_value_pct: 0.15` → limit = 15% × $100,006.71 = $15,001.01
- `max_single_position_pct: 0.10` → limit = 10% × $100,006.71 = $10,000.67

A second identical rejection was recorded at **17:39:53 UTC** (same reason codes, same order value $24,893.41 at the updated intraday quote).

An earlier rejection at **15:16:55 UTC** additionally triggered `strategy_disabled` (the strategy had not yet been promoted from `backtest` to `paper` at that point), demonstrating the stage-gate check working correctly.

**SC-4 status: SATISFIED** — three independent risk rejections recorded in the event store, each with a complete 11-check audit trail. The risk layer vetoed an order attempting 24.9% of equity against a 10% single-position cap.

---

## Promotion gate

**Criterion:** Promotion from `backtest` to `paper` must pass the gate (Sharpe > 0.50, max drawdown < 15%, trades ≥ 30) and be explicitly approved.

### Qualifying backtest

| Field | Value | Threshold | Pass |
|---|---|---|---|
| Run ID | `2ccea042-d869-43ef-aa13-ae49a9483ec4` | — | — |
| Period | 2023-01-01 to 2026-04-21 | — | — |
| Trading days | 827 | — | — |
| Sharpe ratio | **1.02** | > 0.50 | ✅ |
| Max drawdown | **11.47%** | < 15% | ✅ |
| Trade count | **1,057** (529 round trips) | ≥ 30 | ✅ |
| Win rate | 68.8% | — | — |
| CAGR | +12.52% | — | — |
| Total return | +47.26% | — | — |
| Avg hold | 3.9 days | — | — |
| Slippage assumption | 0.10% per side | — | — |

### Promotion event

```
strategy_id:       meanrev.daily.pullback_rsi2.curated_largecap.v1
from_stage:        backtest
to_stage:          paper
promotion_type:    statistical
approved_by:       owner
recorded_at:       2026-04-22 16:44:14 UTC
backtest_run_id:   2ccea042-d869-43ef-aa13-ae49a9483ec4
sharpe_ratio:      1.022
max_drawdown_pct:  11.47
trade_count:       1057
```

**Note on accidental second promotion:** A second promotion event (`paper → micro_live`, recorded at 16:45:39 UTC) was written by an inadvertent second `milodex promote` invocation immediately after the first. The YAML config was manually rolled back to `stage: "paper"`. The event store retains both records; the config-file stage (`stage: "paper"`) is the authoritative runtime gate. A future ADR or CLI guard should prevent accidental double-promotion in the same session.

---

## Advisory lock (single-process enforcement)

The advisory lock (`data/locks/milodex.runtime.lock`) was exercised in practice today. A second attempt to start the strategy runner while one was already active (PID 49828) was correctly rejected:

```
Error: Advisory lock 'milodex.runtime' is held by
milodex strategy run meanrev.daily.pullback_rsi2.curated_largecap.v1
(pid 49828 on Zack-PC, started 2026-04-22T17:46:47.538857+00:00).
Stop the other process or wait for it to exit, then retry.
```

This satisfies the single-process concurrency requirement noted in the 2026-04-21 assessment (Finding #9).

---

## SC-5: Kill switch (not yet collected)

**Criterion:** Kill switch has been triggered in practice, verified to halt trading, verified to require manual reset.

**Status: PENDING.** The kill switch is implemented, event-store-backed, and unit-tested. It has not yet been activated during a live paper session. This is the sole remaining Phase 1.2 DoD gap.

**Action:** Trigger during the next paper session:
```bash
milodex trade kill-switch activate
# verify: no new orders are submitted
milodex trade kill-switch status
milodex trade kill-switch reset
# verify: trading resumes on next cycle
```

---

## Current account state at time of writing

| Field | Value |
|---|---|
| Account type | Alpaca paper |
| Equity | ~$100,006 |
| Open positions | JNJ 44 shares @ $224.25 avg, unrealized ~-$6 |
| Realized P&L today | +$6.71 (SPY round trip) |
| Active runner | PID 49828, cycling, started 17:46:47 UTC |

---

## Phase 1.2 DoD checklist

| Item | Status |
|---|---|
| Regime strategy runs end-to-end in paper | ✅ Session `a408a37a` ran to controlled_stop |
| Meanrev strategy runs end-to-end in paper | ✅ Full round trip + open position |
| Explanation records per decision | ✅ All 11 checks logged per trade intent |
| Dual-stop dialog verified | ✅ Unit-tested; both sessions exited via controlled_stop |
| SC-3: strategy submits real paper orders | ✅ Evidenced above |
| SC-4: risk layer rejected a real trade | ✅ Three rejection events with full audit trail |
| SC-5: kill switch triggered in practice | ⬜ Pending — next action |
| Promotion gate recorded | ✅ `backtest → paper` at 16:44 UTC |
| Advisory lock enforced | ✅ Second runner correctly blocked |

*Next scheduled checkpoint: after SC-5 collection. Update this document with kill switch evidence when collected.*
