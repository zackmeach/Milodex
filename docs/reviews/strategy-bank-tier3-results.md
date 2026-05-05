# Strategy Bank — Tier 3 Results

**Date:** 2026-05-05
**Status:** Tier 3 research closeout in progress. All candidates remain at `stage: backtest`; no promotion is implied.

---

## Candidate Disposition

Tier 3 is intentionally speculative. The purpose is to close each candidate honestly, not to force every idea into permanent strategy code.

| Candidate | Disposition | Reason |
|---|---|---|
| `meanrev.daily.gap_down_reversal.sp100.v1` | blocked | Requires earnings-calendar awareness to avoid conflating earnings gaps with ordinary overnight mean reversion. Do not implement until an earnings data source and ADR exist. |
| `momentum.daily.52w_high_proximity.largecap.v1` | build | Uses only daily OHLCV and `universe.sp100_liquid.v1`, so it can be tested with existing Phase 1 infrastructure. |
| `seasonality.daily.pre_fomc_drift.spy.v1` | blocked | Requires a static or sourced FOMC calendar pattern. Treat calendar data support as a separate ADR/subproject before implementation. |
| `stat_arb.daily.zscore_residual.etf_pairs.v1` | gated | Long-only stat arb is a compromised adaptation of a published long/short edge. Run a throwaway smoke gate before adding a permanent `stat_arb` family. |

## Stat-Arb Gate

The long-only pairs candidate must clear all of these before any permanent family work:

- Sharpe > 0.5
- Trade count > 30
- Max drawdown < 15%
- Low correlation to existing strategy-bank returns

Throwaway smoke result, run against cached daily bars for `SPY/QQQ`, `SPY/IWM`, `DIA/QQQ`, `XLE/XLK`, `GLD/SLV`, `TLT/SHY`, and `SMH/SOXX`:

| Metric | Result | Gate |
|---|---:|---|
| Trades | 50 | pass |
| Sharpe | 0.56 | pass |
| Max drawdown | 3.25% | pass |
| Max absolute correlation to Tier 2 OOS returns | 0.33 | pass |
| Total return | +3.60% | evidence only |

Smoke rules: rolling 120-day log-price residual z-score, long only the underperforming leg at `z <= -2`, exit at `z >= 0`, `held_days >= 5`, or a 5% close stop. This result clears the gate, so no permanent `stat_arb` family is added in Tier 3. A separate implementation plan was written at `docs/superpowers/plans/2026-05-05-stat-arb-etf-pairs-v1.md`.

## 52-Week High Proximity Caveat

The 52-week-high candidate adapts monthly holding literature into a five-day Phase 1 swing-trading hold cap. Any evidence must be read as a test of this daily-swing adaptation, not a replication of the source literature.

## 52-Week High Proximity Evidence

Walk-forward screen, 2020-01-01 to 2024-12-31, $1k initial equity, cache-only provider over available cached members of `universe.sp100_liquid.v1`:

| Metric | Result | Gate |
|---|---:|---|
| Trades | 413 | pass |
| Sharpe | -0.65 | fail |
| Max drawdown | 24.85% | fail |
| Total return | -16.89% | evidence only |
| Single-window dependency | false | pass |

Gate result: blocked. The daily-swing adaptation generated plenty of trades but failed on both Sharpe and drawdown. Correlation to Tier 2 OOS returns was low-to-moderate (`0.03` to `0.16`), so the issue is not duplication; it is poor standalone behavior under the five-day hold cap.
