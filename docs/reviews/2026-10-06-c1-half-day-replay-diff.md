# C1 PR 6: replay diff for the 2020–21 early-close correction

**Date:** 2026-10-06
**Issue:** [#396](https://github.com/zackmeach/Milodex/issues/396) (C1). The correction itself landed in C1 PR 5 (#399).

**The defect.** The hardcoded `US_MARKET_HALF_DAYS` set missed three NYSE early closes: 2020-11-27, 2020-12-24 and 2021-11-26 (audit V6). #399 replaced it with the committed XNYS table. On a half-day, the 10 session strategies skip the session (no entry; a defensive exit if a position is open).

This note measures what that changes in the only recorded intraday evidence that starts before 2022:
- `breakout.orb.intraday.spy.v1` (backtest run 109);
- `benchmark.unconditional_intraday_long.spy.v1` (backtest run 110).

## Method

- **Code.** An archive sandbox of master `5d50ee7` (before #399), with a scratch event store and a scratch cache copy. No live store, cache or process was touched.
- **Variants.**
  - **legacy** runs the code as-is.
  - **corrected** rebinds `US_MARKET_HALF_DAYS` to the legacy set plus the three dates before the CLI runs.
- **Run mode.** Mode, window geometry and parameters were reproduced from the recorded runs' `metadata_json`: walk-forward, 4 windows (train 224d / test 223d / step 223d), 2020-01-01..2024-12-31, $100,000, 5 bp slippage, `risk_policy=bypass`.
- **Equivalence check.** The corrected set equals the merged table in this window. `SessionCalendar.xnys()` at `6df703e` lists exactly the nine early closes in 2020-07-27..2024-12-31: the six legacy dates plus the three added ones. Single-day `simulate_window` probes on the merged tree, with no patch, match the corrected variant on every probe date.

## Result: no gate verdict moves

### ORB

Corrected is bit-identical to legacy in every trade and metric (858 ledger rows, row for row). The only affected session inside an out-of-sample (OOS) window is 2021-11-26, and ORB has no signal that day under either variant.

### Benchmark

One round trip is removed, which causes two sizing knock-ons:

| date (window) | legacy | corrected | why |
|---|---|---|---|
| 2021-11-26 (w0) | BUY 22 @ 432.9464 → SELL @ 430.0649 (engine session-end flatten on the 13:50 post-close print), −$63.39 | no trade | half-day skipped |
| 2022-02-22 (w0) | 23 sh, −$107.35 | 24 sh, −$112.02 | sizing: equity +$63.39 moves `floor(0.10 × equity / price)` from 23.99 to 24 |
| 2022-04-01 (w0) | 22 sh, +$11.04 | 23 sh, +$11.54 | same mechanism |

### OOS aggregate

| metric | ORB legacy = corrected | benchmark legacy | benchmark corrected |
|---|---|---|---|
| trade rows | 858 | 1772 | 1770 |
| total return % | −2.4142 | −6.8565 | −6.8000 |
| Sharpe | −1.0638 | −1.6017 | −1.5885 |
| max drawdown % | 2.5938 | 6.9787 | 6.9222 |

### Against the gate

The thresholds come from `promotion/policy.py`:
- paper: Sharpe > 0.0, drawdown < 25%;
- capital: Sharpe > 0.5, drawdown < 15%;
- trade floor: 30.

Both strategies fail only the Sharpe leg, under every variant. The largest move is +0.013 Sharpe. A single early-close session is worth tens of dollars per $100k, so it could flip a verdict only for a candidate already within about 0.02 Sharpe of a threshold.

### The two 2020 dates

They fall in window 0's train segment, which `run_walk_forward` does not simulate. In single-day probes they are worth −$15.00 and +$6.87 to the benchmark, and −$27.63 to ORB (a 2020-12-24 stop-loss). They would matter only for a whole-period run.

## Recorded runs 109/110 do not reproduce on current code

The legacy replay differs from the recorded runs. For example, the benchmark's Sharpe is −1.6017 replayed against −1.6926 recorded, and its trade count is 1772 against 1769. Three causes were identified:

1. **Engine semantics changed after the runs were recorded.** The recorded runs contain overnight round trips: ORB 16, benchmark 76. The current engine's same-session flatten realizes those at the session's last regular-hours close.
2. **Cached bars drifted from 2023 on.** 308 of ORB's and 662 of the benchmark's recorded fills no longer match a cached bar. The replay's own fills match the cache 100%. The ratios are not one constant scale, so the mechanism is not a single re-adjustment and is unidentified.
3. **The config hashes differ.** The cause is not isolated.

The corrected-vs-legacy diff above is clean regardless, because both variants ran on identical code and data.

**Implication:** the stored evidence for these two paper-stage strategies predates current engine semantics. Regenerating it means two walk-forward backtests written into the live event store, about 35 and 100 minutes each. That is an operational step for the owner and was not run here. No verdict depends on it.

## Notes

- **Effects outside this replay.** `session_close_offset_minutes` now ends the regular session at 12:55 on early-close days. That also changes `regular_session_bars` and `session_vwap*`, and therefore `gap_continuation`'s prior-session close for the session after an early close. Neither strategy here uses those helpers, and no other intraday strategy has completed evidence before 2022.
- **Engine RTH mask on early-close days.** The engine's same-session mask is a fixed 09:30–16:00, so on an early-close day it still admits post-13:00 prints, up to 13:50 on 2021-11-26. It is moot for the session strategies, which now skip the session. ADR 0059 records it as a residual.
- **A truncated data day.** On 2024-12-23 the cache holds only 11 regular-hours bars, ending 10:20. It is not an early close and is not part of this diff.
