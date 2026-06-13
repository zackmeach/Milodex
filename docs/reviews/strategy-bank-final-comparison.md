# Research Screen — 2020-01-01 → 2024-12-31

> **Point-in-time research screen, 2026-05-05. NOT the current bank** — see docs/STRATEGY_BANK.md for canonical status. No promotion implied; all candidates at stage: backtest.

Generated: 2026-05-05T16:10:18.947964-04:00
Strategies: 12

| strategy_id | family | trades | oos_sharpe | oos_max_dd | fragile | gate |
| --- | --- | --- | --- | --- | --- | --- |
| `regime.daily.sma200_rotation.spy_shy.v1` | regime | 0 | n/a | 0.00% | no | pass (lifecycle_exempt) |
| `momentum.daily.dual_absolute.gem_weekly.v1` | momentum | 20 | 0.66 | 18.27% | no | block |
| `momentum.daily.xsec_rotation.sector_etfs.v1` | momentum | 366 | 0.34 | 10.61% | no | block |
| `meanrev.daily.ibs_lowclose.index_etfs.v1` | meanrev | 32 | 0.32 | 1.22% | no | block |
| `momentum.daily.tsmom.curated_largecap.v1` | momentum | 168 | 0.23 | 4.56% | yes | block |
| `breakout.daily.donchian_20_10.sector_etfs.v1` | breakout | 458 | 0.20 | 6.87% | yes | block |
| `breakout.daily.atr_channel.sector_etfs.v1` | breakout | 457 | 0.18 | 3.94% | yes | block |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | meanrev | 165 | -0.10 | 3.19% | no | block |
| `seasonality.daily.turn_of_month.spy.v1` | seasonality | 82 | -0.34 | 9.95% | no | block |
| `breakout.daily.nr7_inside.liquid_largecap.v1` | breakout | 551 | -0.58 | 9.36% | no | block |
| `momentum.daily.52w_high_proximity.largecap.v1` | momentum | 413 | -0.65 | 24.85% | no | block |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | meanrev | 257 | -1.41 | 7.86% | no | block |

## OOS Return Correlation Matrix

| strategy | regime.daily.sma200_rotation.spy_shy.v1 | momentum.daily.dual_absolute.gem_weekly.v1 | momentum.daily.xsec_rotation.sector_etfs.v1 | meanrev.daily.ibs_lowclose.index_etfs.v1 | momentum.daily.tsmom.curated_largecap.v1 | breakout.daily.donchian_20_10.sector_etfs.v1 | breakout.daily.atr_channel.sector_etfs.v1 | meanrev.daily.bbands_lowerband.curated_largecap.v1 | seasonality.daily.turn_of_month.spy.v1 | breakout.daily.nr7_inside.liquid_largecap.v1 | momentum.daily.52w_high_proximity.largecap.v1 | meanrev.daily.pullback_rsi2.curated_largecap.v1 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `regime.daily.sma200_rotation.spy_shy.v1` | 1.00 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |
| `momentum.daily.dual_absolute.gem_weekly.v1` | n/a | 1.00 | 0.40 | 0.14 | 0.17 | 0.37 | 0.34 | 0.25 | 0.33 | 0.22 | 0.14 | 0.27 |
| `momentum.daily.xsec_rotation.sector_etfs.v1` | n/a | 0.40 | 1.00 | 0.02 | 0.25 | 0.30 | 0.28 | 0.24 | 0.21 | 0.29 | 0.20 | 0.16 |
| `meanrev.daily.ibs_lowclose.index_etfs.v1` | n/a | 0.14 | 0.02 | 1.00 | 0.02 | 0.17 | 0.09 | 0.03 | 0.08 | 0.07 | 0.05 | 0.06 |
| `momentum.daily.tsmom.curated_largecap.v1` | n/a | 0.17 | 0.25 | 0.02 | 1.00 | 0.41 | 0.33 | 0.04 | 0.05 | 0.16 | 0.25 | 0.19 |
| `breakout.daily.donchian_20_10.sector_etfs.v1` | n/a | 0.37 | 0.30 | 0.17 | 0.41 | 1.00 | 0.62 | 0.07 | 0.18 | 0.21 | 0.23 | 0.15 |
| `breakout.daily.atr_channel.sector_etfs.v1` | n/a | 0.34 | 0.28 | 0.09 | 0.33 | 0.62 | 1.00 | 0.06 | 0.20 | 0.24 | 0.16 | 0.10 |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | n/a | 0.25 | 0.24 | 0.03 | 0.04 | 0.07 | 0.06 | 1.00 | 0.09 | 0.08 | 0.03 | 0.41 |
| `seasonality.daily.turn_of_month.spy.v1` | n/a | 0.33 | 0.21 | 0.08 | 0.05 | 0.18 | 0.20 | 0.09 | 1.00 | 0.05 | 0.05 | 0.07 |
| `breakout.daily.nr7_inside.liquid_largecap.v1` | n/a | 0.22 | 0.29 | 0.07 | 0.16 | 0.21 | 0.24 | 0.08 | 0.05 | 1.00 | 0.16 | 0.06 |
| `momentum.daily.52w_high_proximity.largecap.v1` | n/a | 0.14 | 0.20 | 0.05 | 0.25 | 0.23 | 0.16 | 0.03 | 0.05 | 0.16 | 1.00 | 0.03 |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | n/a | 0.27 | 0.16 | 0.06 | 0.19 | 0.15 | 0.10 | 0.41 | 0.07 | 0.06 | 0.03 | 1.00 |

## Per-strategy detail

### `regime.daily.sma200_rotation.spy_shy.v1`

- Family: regime
- Trades: 0
- OOS Sharpe: n/a
- OOS Max DD: 0.00%
- OOS Total Return: +0.00%
- Single-window dependency: False
- Gate: lifecycle_exempt — allowed=True
- Run ID: e4e8d29f-4fc2-4038-a7d3-413721de9fe3

### `momentum.daily.dual_absolute.gem_weekly.v1`

- Family: momentum
- Trades: 20
- OOS Sharpe: 0.6608
- OOS Max DD: 18.27%
- OOS Total Return: +23.45%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Max drawdown 18.268259315221627% must be < 15.0% (got 18.268259315221627)
  - Trade count must be >= 30 (got 20)
- Run ID: 8d8b6f47-1ba2-4096-82f1-a252cdddfb35

### `momentum.daily.xsec_rotation.sector_etfs.v1`

- Family: momentum
- Trades: 366
- OOS Sharpe: 0.3368
- OOS Max DD: 10.61%
- OOS Total Return: +10.76%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe 0.3367774453514211 must be > 0.5 (got 0.3367774453514211)
- Run ID: e0707311-b6d2-4edb-bcb3-bac1e98dbd92

### `meanrev.daily.ibs_lowclose.index_etfs.v1`

- Family: meanrev
- Trades: 32
- OOS Sharpe: 0.3167
- OOS Max DD: 1.22%
- OOS Total Return: +0.78%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe 0.31671333909266086 must be > 0.5 (got 0.31671333909266086)
- Run ID: 99f21971-88f2-4b78-bb73-77633aa30bee

### `momentum.daily.tsmom.curated_largecap.v1`

- Family: momentum
- Trades: 168
- OOS Sharpe: 0.2298
- OOS Max DD: 4.56%
- OOS Total Return: +2.03%
- Single-window dependency: True
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe 0.2297820483814155 must be > 0.5 (got 0.2297820483814155)
- Run ID: 13ca02d5-72e0-4e86-8a72-603b7d9867f7

### `breakout.daily.donchian_20_10.sector_etfs.v1`

- Family: breakout
- Trades: 458
- OOS Sharpe: 0.1955
- OOS Max DD: 6.87%
- OOS Total Return: +2.34%
- Single-window dependency: True
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe 0.1954989134655335 must be > 0.5 (got 0.1954989134655335)
- Run ID: aa128a61-e171-4509-b522-1d0e3628327c

### `breakout.daily.atr_channel.sector_etfs.v1`

- Family: breakout
- Trades: 457
- OOS Sharpe: 0.1802
- OOS Max DD: 3.94%
- OOS Total Return: +2.13%
- Single-window dependency: True
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe 0.18022442431937954 must be > 0.5 (got 0.18022442431937954)
- Run ID: 768665b9-17a9-45f3-a0fa-e0f582b9b447

### `meanrev.daily.bbands_lowerband.curated_largecap.v1`

- Family: meanrev
- Trades: 165
- OOS Sharpe: -0.1022
- OOS Max DD: 3.19%
- OOS Total Return: -0.63%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe -0.10215826114300981 must be > 0.5 (got -0.10215826114300981)
- Run ID: 4a73f052-6be3-475c-adc7-d4a0b94f6d23

### `seasonality.daily.turn_of_month.spy.v1`

- Family: seasonality
- Trades: 82
- OOS Sharpe: -0.3438
- OOS Max DD: 9.95%
- OOS Total Return: -5.97%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe -0.3438432813257689 must be > 0.5 (got -0.3438432813257689)
- Run ID: 84154b1d-24fb-47a4-8954-b9cc782ffb92

### `breakout.daily.nr7_inside.liquid_largecap.v1`

- Family: breakout
- Trades: 551
- OOS Sharpe: -0.5826
- OOS Max DD: 9.36%
- OOS Total Return: -8.66%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe -0.5826019354920068 must be > 0.5 (got -0.5826019354920068)
- Run ID: a75055bf-05de-4cc9-98bb-7e5f04934a9d

### `momentum.daily.52w_high_proximity.largecap.v1`

- Family: momentum
- Trades: 413
- OOS Sharpe: -0.6489
- OOS Max DD: 24.85%
- OOS Total Return: -16.89%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe -0.6488955382539584 must be > 0.5 (got -0.6488955382539584)
  - Max drawdown 24.85057164005036% must be < 15.0% (got 24.85057164005036)
- Run ID: b3cc9d8d-ac80-426b-980c-f758baaa5abd

### `meanrev.daily.pullback_rsi2.curated_largecap.v1`

- Family: meanrev
- Trades: 257
- OOS Sharpe: -1.4117
- OOS Max DD: 7.86%
- OOS Total Return: -7.58%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe -1.4116820785890587 must be > 0.5 (got -1.4116820785890587)
- Run ID: a636f908-d76f-4430-86b1-c140313fc538

## Tier 3 Dispositions

| Candidate | Outcome | Reason |
| --- | --- | --- |
| `meanrev.daily.gap_down_reversal.sp100.v1` | blocked | Requires earnings-calendar data and an ADR before implementation. |
| `momentum.daily.52w_high_proximity.largecap.v1` | implemented, blocked | 413 trades, Sharpe -0.65, max drawdown 24.85%; failed Sharpe and drawdown gates. |
| `seasonality.daily.pre_fomc_drift.spy.v1` | blocked | Requires a FOMC calendar data pattern and should be handled as a separate ADR/subproject. |
| `stat_arb.daily.zscore_residual.etf_pairs.v1` | gated, follow-up planned | Throwaway long-only smoke cleared the numeric gates, so a separate implementation plan was written instead of adding a permanent family in Tier 3. |

## Recommendations

No statistical strategy from Tier 1, Tier 2, or Tier 3 should promote to paper from this evidence set. The only passing row is `regime.daily.sma200_rotation.spy_shy.v1`, and that pass is the existing lifecycle exemption rather than a new statistical promotion candidate.

Recommended retirements or freezes:

- Retire or archive `momentum.daily.52w_high_proximity.largecap.v1` as a Tier 3 rejected daily-swing adaptation unless a new hypothesis changes the holding-period constraint.
- Keep `momentum.daily.xsec_rotation.sector_etfs.v1` and `meanrev.daily.ibs_lowclose.index_etfs.v1` as the best failed research references, not promotion candidates.
- Treat `breakout.daily.nr7_inside.liquid_largecap.v1` as rejected for this bank despite adequate trade count; its negative Sharpe is enough to stop.
- Do not add `gap_down_reversal` or `pre_fomc_drift` until their calendar-data prerequisites are designed.

Open follow-up:

- `stat_arb.daily.zscore_residual.etf_pairs.v1` deserves a separate implementation pass because the smoke gate cleared: 50 trades, Sharpe 0.56, max drawdown 3.25%, max absolute correlation 0.33. See `docs/superpowers/plans/2026-05-05-stat-arb-etf-pairs-v1.md`.

## Evidence Notes

The normal CLI `research screen` requires Alpaca credentials because it constructs `AlpacaDataProvider` before cache access. This run used a throwaway cache-only provider over `C:\Users\zdm80\Milodex\market_cache` to avoid live credential access. The Tier 1 and Tier 2 results match the existing review artifacts closely, but large-cap universe rows depend on currently cached symbols; rerun with Alpaca credentials before making any promotion decision.
