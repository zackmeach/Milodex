# Research Screen — 2020-01-01 → 2024-12-31

Generated: 2026-05-05T15:34:21.874602-04:00
Strategies: 4

## Tier 1 Baseline Context

Tier 1 results are reproduced here from
`docs/reviews/strategy-bank-tier1-results.md` so Tier 2 can be read against
the existing bank. All candidates remain at `stage: backtest`; no promotion is
implied by this artifact.

| Strategy | Trades | OOS Sharpe | Max DD | Total Return | Gate |
| --- | ---: | ---: | ---: | ---: | --- |
| `meanrev.daily.ibs_lowclose.index_etfs.v1` | 32 | 0.32 | 1.22% | +0.78% | refused |
| `breakout.daily.donchian_20_10.sector_etfs.v1` | 458 | 0.20 | 6.87% | +2.34% | refused |
| `momentum.daily.xsec_rotation.sector_etfs.v1` | 366 | 0.34 | 10.61% | +10.76% | refused |
| `momentum.daily.dual_absolute.gem_weekly.v1` | 20 | 0.66 | 18.27% | +23.45% | refused |

## Tier 2 Screen

| strategy_id | family | trades | oos_sharpe | oos_max_dd | fragile | gate |
| --- | --- | --- | --- | --- | --- | --- |
| `breakout.daily.atr_channel.sector_etfs.v1` | breakout | 457 | 0.18 | 3.94% | yes | block |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | meanrev | 165 | -0.10 | 3.19% | no | block |
| `seasonality.daily.turn_of_month.spy.v1` | seasonality | 82 | -0.34 | 9.95% | no | block |
| `breakout.daily.nr7_inside.liquid_largecap.v1` | breakout | 1024 | -0.94 | 21.17% | no | block |

## OOS Return Correlation Matrix

| strategy | breakout.daily.atr_channel.sector_etfs.v1 | meanrev.daily.bbands_lowerband.curated_largecap.v1 | seasonality.daily.turn_of_month.spy.v1 | breakout.daily.nr7_inside.liquid_largecap.v1 |
| --- | --- | --- | --- | --- |
| `breakout.daily.atr_channel.sector_etfs.v1` | 1.00 | 0.06 | 0.20 | 0.31 |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | 0.06 | 1.00 | 0.09 | 0.09 |
| `seasonality.daily.turn_of_month.spy.v1` | 0.20 | 0.09 | 1.00 | 0.18 |
| `breakout.daily.nr7_inside.liquid_largecap.v1` | 0.31 | 0.09 | 0.18 | 1.00 |

## Per-strategy detail

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
- Run ID: 0cdce99a-9477-4e21-a901-6a0c249018d8

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
- Run ID: 158dabfc-8765-4665-963c-a0f234c40b1c

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
- Run ID: d91e2e3d-2552-471d-b700-b8b69ef01f10

### `breakout.daily.nr7_inside.liquid_largecap.v1`

- Family: breakout
- Trades: 1024
- OOS Sharpe: -0.9400
- OOS Max DD: 21.17%
- OOS Total Return: -20.11%
- Single-window dependency: False
- Gate: statistical — allowed=False
- Gate failures:
  - Sharpe -0.9399947962417228 must be > 0.5 (got -0.9399947962417228)
  - Max drawdown 21.169015253380184% must be < 15.0% (got 21.169015253380184)
- Run ID: bb5b0b56-ed91-4ec5-bc04-a098ebf89be4
