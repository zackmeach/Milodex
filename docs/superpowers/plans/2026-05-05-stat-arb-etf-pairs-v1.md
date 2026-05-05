# Stat-Arb ETF Pairs v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the Tier 3 long-only ETF-pairs smoke result into a real backtest-only `stat_arb.daily.zscore_residual.etf_pairs.v1` strategy with tests, docs, and walk-forward evidence.

**Architecture:** Add a new `stat_arb` strategy family only after documenting its Phase 1 long-only compromise. The strategy evaluates a fixed ETF-pair universe cross-sectionally, computes rolling log-price residual z-scores, buys the most underperforming leg when residual z-score is sufficiently negative, and exits on residual mean reversion, five-day max hold, or close-based stop. It must remain `stage: backtest`; promotion is explicitly out of scope.

**Tech Stack:** Python 3.11+, pandas, existing Milodex `Strategy`, `StrategyLoader`, YAML configs, pytest, ruff, and `milodex research screen`.

---

### Task 1: Document The New Family Boundary

**Files:**
- Modify: `docs/strategy-families.md`

- [ ] **Step 1: Add a `stat_arb` family section**

Add a section that states the family is Phase 1 long-only statistical arbitrage research, not broker-level shorting or market-neutral execution.

```markdown
## Family: stat_arb

Statistical-arbitrage candidates use relative-value signals derived from relationships between instruments. In Phase 1, this family is restricted to long-only U.S. ETFs/equities. That means published long/short pair-trading edges are not replicated directly; every strategy must disclose the long-only adaptation and treat evidence as a separate hypothesis.

Family invariants:

- Long-only orders only.
- Daily bars only.
- No leverage, no shorting, no synthetic short exposure.
- Every candidate starts at `stage: backtest`.
- Promotion requires the standard statistical gate unless a later ADR creates a specific exemption.
```

- [ ] **Step 2: Add the template section**

```markdown
### Template: stat_arb.daily.zscore_residual

Entry:

- Compute rolling log-price residuals for configured ETF pairs.
- Rank candidates by most-negative residual z-score.
- Buy the underperforming leg when `residual_z <= entry_z`.

Exit:

- Sell when `residual_z >= exit_z`.
- Sell when `held_days >= max_hold_days`.
- Sell when `close <= entry_price * (1 - stop_loss_pct)`.

Ranking:

- Sort by residual z-score ascending, then absolute z-score descending, then symbol.

Daily-swing fit caveat:

The source edge is normally long/short and may depend on hedged exposure. This template only tests whether the long underperforming leg has enough standalone mean-reversion behavior under Phase 1 constraints.
```

- [ ] **Step 3: Run docs sanity check**

Run: `python -m pytest tests/milodex/strategies/test_loader.py -q`

Expected: existing loader tests pass; docs are not programmatically checked.

### Task 2: Write Failing Strategy Tests

**Files:**
- Create: `tests/milodex/strategies/test_stat_arb_zscore_residual.py`

- [ ] **Step 1: Write the failing tests**

Include tests for:

- Entry when a pair residual z-score is below `entry_z`.
- Rejection when z-score is not negative enough.
- Exit on residual mean reversion.
- Exit on stop loss.
- Exit on max hold.
- Ranking chooses the most-negative z-score when multiple pairs qualify.
- Loader resolves `configs/stat_arb_daily_zscore_residual_etf_pairs_v1.yaml`.

- [ ] **Step 2: Run tests red**

Run: `python -m pytest tests/milodex/strategies/test_stat_arb_zscore_residual.py -q`

Expected: fail because `milodex.strategies.stat_arb_zscore_residual` does not exist.

### Task 3: Implement The Strategy

**Files:**
- Create: `src/milodex/strategies/stat_arb_zscore_residual.py`
- Modify: `src/milodex/strategies/loader.py`
- Modify: `src/milodex/strategies/__init__.py`

- [ ] **Step 1: Add `StatArbZscoreResidualStrategy`**

Implement `family = "stat_arb"` and `template = "daily.zscore_residual"`.

Required parameters:

- `pairs`
- `lookback_days`
- `zscore_window`
- `entry_z`
- `exit_z`
- `max_hold_days`
- `stop_loss_pct`
- `max_concurrent_positions`
- `sizing_rule`
- `per_position_notional_pct`
- `ranking_enabled`
- `ranking_metric`

- [ ] **Step 2: Implement residual calculation**

Use rolling log-price OLS over `lookback_days`:

```python
beta = rolling_cov(log_y, log_x) / rolling_var(log_x)
alpha = rolling_mean(log_y) - beta * rolling_mean(log_x)
residual = log_y - (alpha + beta * log_x)
residual_z = (residual - residual.rolling(zscore_window).mean()) / residual.rolling(zscore_window).std()
```

- [ ] **Step 3: Implement entries and exits**

Entry emits only `OrderSide.BUY` for the underperforming leg. Exit precedence is `stop_loss`, then `max_hold`, then `residual_reversion`.

- [ ] **Step 4: Register and export the strategy**

Add imports and registry registration in `loader.py`; add public export in `__init__.py`.

- [ ] **Step 5: Run tests green**

Run: `python -m pytest tests/milodex/strategies/test_stat_arb_zscore_residual.py -q`

Expected: all new strategy tests pass.

### Task 4: Add Config

**Files:**
- Create: `configs/stat_arb_daily_zscore_residual_etf_pairs_v1.yaml`

- [ ] **Step 1: Add the frozen config**

Use:

```yaml
strategy:
  id: "stat_arb.daily.zscore_residual.etf_pairs.v1"
  family: "stat_arb"
  template: "daily.zscore_residual"
  variant: "etf_pairs"
  version: 1
  enabled: true
  universe:
    - "SPY"
    - "QQQ"
    - "IWM"
    - "DIA"
    - "XLE"
    - "XLK"
    - "GLD"
    - "SLV"
    - "TLT"
    - "SHY"
    - "SMH"
    - "SOXX"
  parameters:
    pairs:
      - ["SPY", "QQQ"]
      - ["SPY", "IWM"]
      - ["DIA", "QQQ"]
      - ["XLE", "XLK"]
      - ["GLD", "SLV"]
      - ["TLT", "SHY"]
      - ["SMH", "SOXX"]
    lookback_days: 120
    zscore_window: 60
    entry_z: -2.0
    exit_z: 0.0
    max_hold_days: 5
    stop_loss_pct: 0.05
    max_concurrent_positions: 1
    sizing_rule: "equal_notional"
    per_position_notional_pct: 0.20
    ranking_enabled: true
    ranking_metric: "residual_z_ascending"
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.20
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.00
    min_trades_required: 30
    walk_forward_windows: 4
  disable_conditions_additional:
    - "Do not deploy as market-neutral or long/short; Phase 1 implementation is long-only."
```

- [ ] **Step 2: Validate config**

Run: `python -m milodex.cli.main config validate configs/stat_arb_daily_zscore_residual_etf_pairs_v1.yaml`

Expected: config validation passes.

### Task 5: Run Evidence And Update Reviews

**Files:**
- Modify: `docs/reviews/strategy-bank-tier3-results.md`
- Modify: `docs/reviews/strategy-bank-final-comparison.md`

- [ ] **Step 1: Run focused evidence**

Run:

```bash
python -m milodex.cli.main research screen --strategy-id stat_arb.daily.zscore_residual.etf_pairs.v1 --start 2020-01-01 --end 2024-12-31 --initial-equity 1000 --report-out docs/reviews/stat-arb-etf-pairs-v1-results.md
```

Expected: report is written and includes trade count, Sharpe, max drawdown, gate result, and OOS equity curve.

- [ ] **Step 2: Run complete strategy-bank comparison**

Run the final screen with explicit `--strategy-id` entries for all bank strategies, including the new stat-arb candidate. Avoid `--configs "*_v1.yaml"` because universe manifests are also YAML files.

- [ ] **Step 3: Update review artifacts**

Record the actual walk-forward result. If the real backtest fails the gate despite the smoke result, document the smoke-to-engine gap plainly and keep the strategy at `stage: backtest`.

### Task 6: Final Verification

**Files:**
- Test: `tests/milodex/strategies/test_stat_arb_zscore_residual.py`
- Test: `tests/milodex/backtesting/`

- [ ] **Step 1: Run focused tests**

Run: `python -m pytest tests/milodex/strategies/test_stat_arb_zscore_residual.py -q`

Expected: all focused tests pass.

- [ ] **Step 2: Run backtesting tests**

Run: `python -m pytest tests/milodex/backtesting/ -q`

Expected: all backtesting tests pass.

- [ ] **Step 3: Run lint**

Run: `python -m ruff check src/ tests/`

Expected: no lint errors.

- [ ] **Step 4: Run full tests**

Run: `python -m pytest -q`

Expected: full suite passes.

## Self-Review

Spec coverage: this plan covers family docs, strategy tests, implementation, config validation, walk-forward evidence, review updates, and final verification.

Placeholder scan: no `TBD`, `TODO`, or "similar to" placeholders remain.

Type consistency: the strategy id, family/template/variant, config path, class name, and ranking metric are consistent across tasks.
