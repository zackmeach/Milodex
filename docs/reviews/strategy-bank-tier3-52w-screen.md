# Research Screen — 2020-01-01 → 2024-12-31

Generated: 2026-05-05T16:10:37.086893-04:00
Strategies: 1

| strategy_id | family | trades | oos_sharpe | oos_max_dd | fragile | gate |
| --- | --- | --- | --- | --- | --- | --- |
| `momentum.daily.52w_high_proximity.largecap.v1` | momentum | 413 | -0.65 | 24.85% | no | block |

## OOS Return Correlation Matrix

| strategy | momentum.daily.52w_high_proximity.largecap.v1 |
| --- | --- |
| `momentum.daily.52w_high_proximity.largecap.v1` | 1.00 |

## Per-strategy detail

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
- Run ID: 64f1d72d-ecf1-466a-843e-e5252b6c91fc
