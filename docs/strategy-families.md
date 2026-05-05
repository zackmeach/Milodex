# Strategy Families — Normative Specifications

**Status:** Living document (normative subsystem spec — authority rank 2 per `docs/adr/README.md`)
**Scope:** Phase 1 strategy families. New families are added by new sections here, not by burying meaning in YAML.

---

## How to Read This Document

A **strategy family** is a named archetype of trading logic: what market behavior it tries to exploit, what its semantic invariants are, what parameters can vary, and what conditions should disable it. Every **promotable strategy instance** (see SRS Key Terms) is an instance of exactly one family.

This document is **normative**. YAML config files under `configs/` carry only the *frozen values* for a specific instance — they do not redefine the family. If a proposed change to a strategy is not expressible within the family's declared parameter surface, the change is either a new variant, a new version (per ADR 0015), or a new family — not a silent override in YAML.

Each section below defines one family with a fixed structure:

1. **Family identifier** (used in the `strategy.id` prefix, per ADR 0015)
2. **Market behavior exploited** — the hypothesis
3. **Semantic invariants** — what is hardcoded in the strategy engine and cannot be overridden by config
4. **Parameter surface** — what YAML is allowed to vary
5. **Entry rule** — the normative condition
6. **Exit rule** — the normative condition
7. **Ranking rule** — how to pick among qualifying candidates when position limits bind
8. **Default disable conditions** — environments that invalidate the family's evidence

---

## Family: `meanrev` — Daily Mean-Reversion Swing

### Identifier prefix
`meanrev.*`

### Market behavior exploited

The `meanrev` family targets **short-term overshoot and snapback behavior in liquid markets**. The hypothesis is that some assets temporarily move too far, too fast relative to recent behavior, and then mean-revert over the next few trading sessions as panic, forced flows, or short-term imbalance fades. The edge is not that prices always revert — it is that under defined conditions the probability-adjusted bounce may be large enough to justify disciplined entry with tight risk controls.

### Semantic invariants (hardcoded in code, not overrideable by config)

- `long_only: true`
- `signal_evaluation: end_of_day` — signals are computed on completed daily bars; no intraday signal generation
- `execution_timing: next_market_open` — orders are submitted at the open following the signal
- `stop_semantics: close_based` — stops are evaluated once per day on close, executed at the next open
- `timeframe: 1D` (daily bars only)
- `promotion_requires_frozen_manifest: true` (per ADR 0015)

Changing any of the above produces a **new version** of the strategy, not a variant.

### Parameter surface (allowed to vary in YAML)

| Parameter | Meaning | Notes |
|---|---|---|
| `universe` | Curated list of approved symbols | Phase 1 uses a fixed curated universe; automatic discovery is Phase 2+ |
| `rsi_lookback` | RSI period | typical: 2 |
| `rsi_entry_threshold` | Enter when RSI < this | typical: 5–15 |
| `rsi_exit_threshold` | Exit when RSI > this | typical: 40–60 |
| `ma_filter_length` | Long-only regime filter | typical: 100–200 |
| `stop_loss_pct` | Close-based stop distance | typical: 0.03–0.07 |
| `max_hold_days` | Maximum trading days in position | typical: 3–7 |
| `max_concurrent_positions` | Per-strategy position cap | subject to global caps |
| `sizing_rule` | One of: `equal_notional`, `fixed_notional` | extension requires a new version |
| `per_position_notional_pct` | Used when sizing rule requires it | |
| `ranking_enabled` | Whether to rank candidates | |
| `ranking_metric` | One of: `rsi_ascending`, `drawdown_deepest` | extension requires a new version |

### Entry rule (normative)

> Enter long at the **next market open** if, at the prior close:
> 1. The symbol is in the approved universe; **and**
> 2. `close > SMA(ma_filter_length)`; **and**
> 3. `RSI(rsi_lookback) < rsi_entry_threshold`; **and**
> 4. The symbol is not already in an open position; **and**
> 5. The symbol is not blocked by any risk or execution constraint.

### Exit rule (normative)

> Exit at the **next market open** if, at the prior close, **any** of the following holds:
> - `RSI(rsi_lookback) > rsi_exit_threshold`; **or**
> - `max_hold_days` reached (counted in trading days since entry); **or**
> - Stop condition triggered: `close <= entry_price * (1 - stop_loss_pct)`.

### Ranking rule (normative)

When the number of qualifying entry candidates on a given evaluation exceeds the capital or position budget:

> Rank qualifying candidates by `ranking_metric` (default: `rsi_ascending` — lowest RSI first). Take the top `(max_concurrent_positions - current_open_positions)` candidates. Reject the remainder silently; the rejection is recorded in the explanation record (R-XC-008) but generates no order.

### Default disable conditions

The strategy is halted when any of the following hold even if the code is functioning correctly:

1. Abnormal market regime or volatility far outside tested bounds
2. Significant spread expansion or liquidity deterioration
3. Major unresolved data-quality issues
4. Corporate-action handling uncertainty
5. Broker / execution instability
6. Repeated unexplained divergence between expected and actual fills
7. Breach of drawdown or risk-budget limits
8. Operator-declared pause after unusual market events

Instance YAML may add conditions but **shall not remove** any of the above.

### Template: `daily.ibs_lowclose` — Internal Bar Strength

A second template within the `meanrev` family. Inherits every family-level
semantic invariant above (long-only, end-of-day signal, next-open execution,
close-based stops, daily timeframe, frozen-manifest promotion). What differs
is the entry signal — IBS is a single-bar indicator about *where in today's
range the close sat*, not a multi-day oscillator over closes.

The two templates therefore exercise structurally different oversold
mechanics rather than two parameterizations of the same idea (per VISION's
"idea vs. tuning" rule). They are deliberately deployed on different
universe shapes — the RSI(2) template runs on curated single-name large-caps;
the IBS template runs on broad index ETFs where intraday bar location is
historically a more reliable oversold signal than on idiosyncratic names.

#### Parameter surface (allowed to vary in YAML)

| Parameter | Meaning | Notes |
|---|---|---|
| `universe` | Curated list of approved symbols | Phase 1 default: `universe.index_etfs.v1` (SPY, QQQ, IWM, DIA) |
| `ibs_entry_threshold` | Enter when `IBS < this` | typical: 0.15–0.25 |
| `ma_filter_length` | Long-only regime filter (close > SMA) | typical: 100–200 |
| `stop_loss_pct` | Close-based stop distance | typical: 0.02–0.05 (tighter than RSI(2) — IBS holds are shorter) |
| `max_hold_days` | Maximum trading days in position | typical: 2–4 |
| `max_concurrent_positions` | Per-strategy position cap | subject to global account-scoped caps per ADR 0024 |
| `sizing_rule` | One of: `equal_notional`, `fixed_notional` | extension requires a new version |
| `per_position_notional_pct` | Used when sizing rule requires it | |
| `ranking_enabled` | Whether to rank candidates | |
| `ranking_metric` | One of: `ibs_ascending` | extension requires a new version |

The IBS template intentionally has no separate exit threshold — exit is
signal-driven (close above prior day's high), not threshold-driven.

#### Entry rule (normative)

> Enter long at the **next market open** if, at the prior close:
> 1. The symbol is in the approved universe; **and**
> 2. `close > SMA(ma_filter_length)`; **and**
> 3. `IBS = (Close - Low) / (High - Low) < ibs_entry_threshold`; **and**
> 4. The bar's range is non-zero (`High > Low`); a degenerate bar is
>    rejected as undefined rather than treated as oversold; **and**
> 5. The symbol is not already in an open position; **and**
> 6. The symbol is not blocked by any risk or execution constraint.

#### Exit rule (normative)

> Exit at the **next market open** if, at the prior close, **any** of the
> following holds (most-specific rule wins when several fire):
> - Stop condition: `close <= entry_price * (1 - stop_loss_pct)`; **or**
> - `max_hold_days` reached (counted in trading days since entry); **or**
> - Signal exit: `close > prior_day_high`.

The signal exit captures the canonical IBS thesis — a snapback close that
clears yesterday's high signals the oversold pressure has unwound.

#### Ranking rule (normative)

When the number of qualifying entry candidates exceeds capacity:

> Rank qualifying candidates by `ranking_metric` (default: `ibs_ascending`
> — lowest IBS first, i.e. the most decisively close-near-low). Take the
> top `(max_concurrent_positions - current_open_positions)` candidates.
> Reject the remainder silently; the rejection is recorded in the
> explanation record (R-XC-008) but generates no order.

#### Default disable conditions

Same eight conditions as the `daily.pullback_rsi2` template — both
templates operate in the same liquid-market context and depend on the same
data-quality and broker-stability assumptions. Instance YAML may add
conditions but **shall not remove** any of the family-level disable
conditions defined above.

#### Decision rule identifiers

The IBS template emits the following `DecisionReasoning.rule` identifiers:
`meanrev.ibs_entry`, `meanrev.ibs_exit`, `meanrev.stop_loss`,
`meanrev.max_hold`, `no_signal`. The stop-loss and max-hold identifiers are
shared with `daily.pullback_rsi2` because their semantics (close-based stop
from entry price; held-days time stop) are identical at the family level.

---

## Family: `regime` — Daily Regime Rotation

### Identifier prefix
`regime.*`

### Market behavior exploited

The `regime` family does not attempt to discover edge. It encodes a **defensive rotation rule** between a risk-on and a risk-off asset based on a single, widely-understood trend filter. The hypothesis is conventional and well-documented: sustained drawdowns in broad equity indices tend to occur below the long-term moving average, and sitting in short-duration Treasuries during those periods historically reduces drawdown without claiming predictive skill.

The family exists in Phase 1 primarily as a **lifecycle-proof vehicle** (see SRS Key Terms). Its purpose is to exercise the full Milodex platform end-to-end with a strategy simple enough that any operational bug becomes the obvious explanation of any surprise.

### Semantic invariants (hardcoded, not overrideable)

- `long_only: true`
- `signal_evaluation: end_of_day`
- `execution_timing: next_market_open`
- `single_asset_allocation: true` — at most one position at a time
- `timeframe: 1D`
- `promotion_requires_frozen_manifest: true` (ADR 0015)
- **Exempt from statistical promotion thresholds** (SRS R-PRM-004): trade count, Sharpe, and drawdown metrics are collected but do not gate promotion. Operational gates apply instead.

### Parameter surface

| Parameter | Meaning | Notes |
|---|---|---|
| `ma_filter_length` | Trend-filter MA period | typical: 200 |
| `risk_on_symbol` | Symbol held above the filter | typical: `SPY` |
| `risk_off_symbol` | Symbol held below the filter | typical: `SHY` |
| `allocation_pct` | Fraction of capital deployed (the rest stays in cash) | typical: 1.00 |

Changing the asset class (e.g., risk-off moving from Treasuries to gold), the number of allocation buckets, or the trend-filter type (SMA → EMA → breakout) produces a **new version**, not a variant.

### Rule (normative)

> Daily, after the close: if `close(risk_on_symbol) > SMA(risk_on_symbol, ma_filter_length)`, the target position is `risk_on_symbol`; otherwise the target position is `risk_off_symbol`. If the current position differs from the target, exit the current position and enter the target at the **next market open**. Allocation is 100% of `allocation_pct` into the single target symbol.

### Ranking rule

N/A — the family is single-asset by invariant.

### Default disable conditions

1. Major unresolved data-quality issues
2. Broker / execution instability
3. Operator-declared pause

The regime family's disable-condition catalog is intentionally short because the family takes no view on volatility regime, liquidity, or corporate actions in its target symbols (large broad-market ETFs).

---

## Family: `momentum` — Daily Time-Series Momentum Swing

### Identifier prefix
`momentum.*`

### Market behavior exploited

The `momentum` family targets **trend continuation in liquid markets at daily swing tempo**. The hypothesis is that some assets exhibit short-term momentum — recent strength persists into the next handful of trading sessions before mean-reversion or new information dominates. The edge is not that prices always trend — it is that under defined conditions, the probability-adjusted continuation may be large enough to justify disciplined entry on confirmed momentum with tight risk controls.

This family is the structural counterpart to `meanrev`: where meanrev buys oversold pullbacks, `momentum` buys confirmed strength. The two are deliberately statistically opposite (reversion vs. continuation) so they exercise the harness on materially different signal shapes rather than two parameterizations of the same idea (per VISION's "idea vs. tuning" rule).

### Semantic invariants (hardcoded in code, not overrideable by config)

- `long_only: true`
- `signal_evaluation: end_of_day` — signals are computed on completed daily bars; no intraday signal generation
- `execution_timing: next_market_open` — orders are submitted at the open following the signal
- `stop_semantics: close_based` — stops are evaluated once per day on close, executed at the next open
- `timeframe: 1D` (daily bars only)
- `promotion_requires_frozen_manifest: true` (per ADR 0015)

Changing any of the above produces a **new version** of the strategy, not a variant.

### Parameter surface (allowed to vary in YAML)

| Parameter | Meaning | Notes |
|---|---|---|
| `universe` | Curated list of approved symbols | Phase 1 uses a fixed curated universe; automatic discovery is Phase 2+ |
| `momentum_lookback` | Trading days over which to compute the return signal | typical: 20–60 |
| `momentum_entry_threshold` | Minimum return over `momentum_lookback` to qualify (decimal; `0.05` = 5%) | typical: 0.03–0.10 |
| `momentum_exit_threshold` | Exit when return over `momentum_lookback` < this value | typical: 0.0 (exit when momentum turns negative) |
| `ma_filter_length` | Long-only regime filter (price > SMA) | typical: 100–200 |
| `stop_loss_pct` | Close-based stop distance | typical: 0.05–0.10 |
| `max_hold_days` | Maximum trading days in position | typical: 5–20 (longer than meanrev — trends need room) |
| `max_concurrent_positions` | Per-strategy position cap | subject to global account-scoped caps per ADR 0024 |
| `sizing_rule` | One of: `equal_notional`, `fixed_notional` | extension requires a new version |
| `per_position_notional_pct` | Used when sizing rule requires it | |
| `ranking_enabled` | Whether to rank candidates | |
| `ranking_metric` | One of: `momentum_descending` | extension requires a new version |
| `market_regime_symbol` | Optional broad-market regime filter symbol (e.g. `SPY`) | empty string disables |
| `market_regime_ma_length` | MA length for regime filter | typical: 200 |

### Entry rule (normative)

> Enter long at the **next market open** if, at the prior close:
> 1. The symbol is in the approved universe; **and**
> 2. `close > SMA(ma_filter_length)`; **and**
> 3. `(close / close[-momentum_lookback]) - 1 >= momentum_entry_threshold`; **and**
> 4. The symbol is not already in an open position; **and**
> 5. The symbol is not blocked by any risk or execution constraint.

### Exit rule (normative)

> Exit at the **next market open** if, at the prior close, **any** of the following holds:
> - `(close / close[-momentum_lookback]) - 1 < momentum_exit_threshold`; **or**
> - `max_hold_days` reached (counted in trading days since entry); **or**
> - Stop condition triggered: `close <= entry_price * (1 - stop_loss_pct)`.

The momentum and stop-loss conditions are independently sufficient; either alone closes the position. Their evaluation order in code is: stop_loss > max_hold > momentum_exit (most specific first), so when multiple conditions fire on the same bar, the more specific rule names the explanation.

### Ranking rule (normative)

When the number of qualifying entry candidates on a given evaluation exceeds the capital or position budget:

> Rank qualifying candidates by `ranking_metric` (default: `momentum_descending` — highest `momentum_lookback` return first). Take the top `(max_concurrent_positions - current_open_positions)` candidates. Reject the remainder silently; the rejection is recorded in the explanation record (R-XC-008) but generates no order.

### Default disable conditions

The strategy is halted when any of the following hold even if the code is functioning correctly:

1. Abnormal market regime or volatility far outside tested bounds
2. Significant spread expansion or liquidity deterioration
3. Major unresolved data-quality issues
4. Corporate-action handling uncertainty
5. Broker / execution instability
6. Repeated unexplained divergence between expected and actual fills
7. Breach of drawdown or risk-budget limits
8. Operator-declared pause after unusual market events

Same eight conditions as `meanrev` — both families operate in the same liquid-market context and depend on the same data-quality and broker-stability assumptions. Instance YAML may add conditions but **shall not remove** any of the above.

### Template: `daily.xsec_rotation` — Cross-Sectional Rank Rotation

A second template within the `momentum` family. Inherits every family-level
semantic invariant above (long-only, end-of-day signal, next-open execution,
close-based stops, daily timeframe, frozen-manifest promotion). What differs
is the entry concept — `daily.tsmom` evaluates each symbol on its own
absolute return over a fixed lookback; `daily.xsec_rotation` ranks symbols
**against each other** each rebalance and holds the top-N. The two templates
exercise structurally different momentum mechanics — time-series vs.
cross-sectional — on different universe shapes (curated large-caps vs.
sector-ETF basket where cross-sectional dispersion is the unit of analysis).

This template introduces a **weekly rebalance cadence**: ranking and
turnover decisions only happen on Fridays at the close. On non-Friday
evaluations the strategy returns `no_signal`. The runner is unchanged —
the cadence is enforced inside `evaluate()` based on the latest bar's
timestamp, not by external scheduling.

#### Parameter surface (allowed to vary in YAML)

| Parameter | Meaning | Notes |
|---|---|---|
| `universe` | Curated list of approved symbols | Phase 1 default: `universe.sector_etfs_spdr.v1` (11 SPDR sector ETFs) |
| `ranking_lookback` | Trading days over which trailing total return is computed for ranking | typical: 60–63 (≈ 3 months) |
| `target_positions` | How many top-ranked symbols to hold | typical: 2–3 |
| `exit_outside_top_n` | Exit a held symbol when it falls outside this rank (hysteresis) | typical: `target_positions + 1` |
| `rebalance_weekday` | Trading-week day on which ranking + turnover fires (0=Monday, 4=Friday) | typical: 4 (Friday) — close of week → next Monday open |
| `ma_filter_length` | Per-symbol trend filter (close > SMA) — gates whether a top-ranked symbol is enterable | typical: 100–200; set to 0 to disable |
| `stop_loss_pct` | Close-based stop distance (mid-week protection) | typical: 0.05–0.10 |
| `max_hold_days` | Hard time stop in trading days since entry | typical: 5 — effectively enforced by weekly rebalance, but kept as a belt-and-braces guard |
| `max_concurrent_positions` | Per-strategy position cap (must be ≥ `target_positions`) | subject to global account-scoped caps per ADR 0024 |
| `sizing_rule` | One of: `equal_notional`, `fixed_notional` | extension requires a new version |
| `per_position_notional_pct` | Used when sizing rule requires it | |
| `ranking_enabled` | Always `true` for this template — kept for parameter-shape parity with other templates | |
| `ranking_metric` | One of: `xsec_return_descending` | extension requires a new version |
| `market_regime_symbol` | Optional broad-market regime filter symbol (e.g. `SPY`) | empty string disables |
| `market_regime_ma_length` | MA length for regime filter | typical: 200 |

#### Entry rule (normative)

> On the **rebalance bar** (latest bar's weekday equals `rebalance_weekday`), at the prior close:
> 1. Compute trailing return over `ranking_lookback` bars for every universe member with sufficient history;
> 2. Rank descending; **and**
> 3. If a market-regime filter is configured and the regime symbol is below its MA, suppress entries (still allow exits — see below);
> 4. For each top-`target_positions` symbol not currently held: if `close > SMA(ma_filter_length)` (when `ma_filter_length > 0`), enter at the next market open; otherwise reject with the reason recorded in the explanation.
>
> On any **non-rebalance bar**, the strategy emits `no_signal` and no entries are evaluated. Stops, however, are checked daily — see exit rule.

#### Exit rule (normative)

> On the **rebalance bar**, at the prior close, any held symbol whose rank is greater than `exit_outside_top_n` exits at the next market open.
>
> On **every bar (including non-rebalance)**, the following exit rules are evaluated for held positions (most-specific first):
> - `close <= entry_price * (1 - stop_loss_pct)` — close-based stop_loss; **or**
> - `held_days >= max_hold_days` — time stop.
>
> Stops fire daily; rank-based exits fire weekly. This is the published behavior — the strategy commits to a one-week hold but escapes early on a hard loss.

#### Ranking rule (normative)

When the number of qualifying entry candidates exceeds capacity:

> Rank qualifying candidates by `ranking_metric` (default: `xsec_return_descending` — highest trailing return over `ranking_lookback` bars first). Take the top `target_positions` candidates that are not already held; reject the remainder silently. Rejected candidates are recorded in the explanation record (R-XC-008).

#### Daily-swing fit caveat

The published cross-sectional momentum literature (Jegadeesh & Titman 1993; Asness/Moskowitz/Pedersen 2013; Antonacci 2014) operates on **monthly** rebalances with 1–12 month holds. A weekly rebalance is the tightest faithful daily-swing adaptation. Expect ~30–50% of the published edge to survive the hold compression. This is a known tradeoff; measure it explicitly.

#### Default disable conditions

Same eight conditions as the `daily.tsmom` template. Instance YAML may add conditions but **shall not remove** any of the family-level disable conditions defined above.

#### Decision rule identifiers

The xsec_rotation template emits the following `DecisionReasoning.rule` identifiers: `momentum.xsec_entry`, `momentum.xsec_exit` (rank-based weekly exit), `momentum.stop_loss`, `momentum.max_hold`, `no_signal`. The percent-stop and max-hold identifiers are shared with `daily.tsmom` because their semantics are identical at the family level.

### Template: `daily.dual_absolute` — Dual Momentum (GEM)

A third template within the `momentum` family. Inherits every family-level
semantic invariant above but **operates as a single-asset rotation** (at
most one position at a time, like the `regime` family). What differs is
the entry concept — combining **relative momentum** (rank a small risk-on
basket by trailing return) with **absolute momentum** (only hold the
relative winner if its return also beats a risk-off floor; otherwise hold
the risk-off asset).

This is Antonacci's Global Equities Momentum (GEM) algorithm, structurally
adjacent to the `regime.daily.sma200_rotation` template but with a
materially different signal — a multi-asset cross-sectional rank with an
absolute floor, rather than a single-asset trend-filter. The natural use
case in the bank is as a **benchmark for the existing regime template**:
if dual-momentum materially outperforms the SMA200 rotation, it becomes
the candidate lifecycle-proof replacement; if correlated, drop one.

#### Single-asset semantic invariant

In addition to the `momentum` family-level invariants, the
`daily.dual_absolute` template declares **`single_asset_allocation: true`**
— at most one position at a time, exactly like the `regime` family. This
is hardcoded in the strategy, not a configurable knob.

#### Parameter surface (allowed to vary in YAML)

| Parameter | Meaning | Notes |
|---|---|---|
| `universe` | Curated list of approved symbols (must contain `risk_off_symbol`) | Phase 1 default: `universe.gem_quartet.v1` (SPY, EFA, AGG, SHY) |
| `risk_off_symbol` | The absolute-momentum floor; held when no risk-on candidate beats it | typical: `SHY` (short Treasuries) |
| `momentum_lookback` | Trading days over which trailing return is computed for both relative + absolute checks | typical: 126 (≈ 6 calendar months) |
| `rebalance_weekday` | Trading-week day on which ranking + turnover fires (0=Monday, 4=Friday) | typical: 4 |
| `allocation_pct` | Fraction of capital deployed (the rest stays in cash) | typical: 1.00 |
| `sizing_rule` | One of: `single_asset_full_allocation` | extension requires a new version |

The template intentionally omits per-symbol MA filters, stop_loss, and
max_hold_days — Antonacci's published behavior commits to the rebalance
cadence and uses no intra-period stops. Phase 1 daily-swing fit naturally
caps holds at one week (Monday-to-Friday), well under the 5-day Phase 1
ceiling.

#### Entry/Exit rule (normative)

> On the **rebalance bar** (latest bar's weekday equals `rebalance_weekday`), at the prior close:
> 1. Compute trailing return over `momentum_lookback` bars for every universe member with sufficient history;
> 2. Identify `top_risk_on` = the universe member with the highest trailing return *among symbols other than `risk_off_symbol`*;
> 3. If `return(top_risk_on) > return(risk_off_symbol)`, the target position is `top_risk_on`; otherwise the target position is `risk_off_symbol`;
> 4. If the current position differs from the target: exit the current position and enter the target at the **next market open**. Allocation is `allocation_pct` of equity into the single target symbol.
>
> On any **non-rebalance bar**, the strategy emits `no_signal` and no turnover happens. There are no intra-period stops in this template.

#### Ranking rule

N/A in the cross-sectional sense — the template is single-asset by invariant. The "ranking" is the simple max over `momentum_lookback`-day returns.

#### Daily-swing fit caveat

The published GEM uses **monthly** rebalances. Weekly is the tightest faithful daily-swing adaptation. The edge degradation is expected to be **modest** — less than the breakout truncation, more than the ts-momentum cross-sectional adaptation, since GEM's edge is largely about not being in equities during sustained drawdowns, and a monthly-vs-weekly rebalance changes that signal less than it changes a momentum-continuation signal.

#### Default disable conditions

Inherits the eight-condition list from the family-level `momentum` block. Instance YAML may add conditions but **shall not remove** any of the family-level disable conditions defined above.

#### Decision rule identifiers

The `daily.dual_absolute` template emits the following `DecisionReasoning.rule` identifiers: `momentum.dual_absolute_rotation` (target switched on this rebalance), `momentum.dual_absolute_hold` (target unchanged on this rebalance, no order emitted), `no_signal` (non-rebalance bar). The template emits no stop-related identifiers because it has no intra-period stops by design.

---

## Family: `breakout` — Daily Channel Breakout

### Identifier prefix
`breakout.*`

### Market behavior exploited

The `breakout` family targets **trend continuation in liquid markets at daily swing tempo**. The hypothesis is that a price clearing a recent high (or, by extension, a recent volatility-channel boundary) signals participants have shifted from accumulation to expansion, and the immediate follow-through is large enough often enough to justify disciplined entry on confirmed strength. The edge is not that breakouts always continue — it is that under defined conditions, the right-tail of the next-few-days return distribution is heavy enough to absorb the loss-tail of failed breakouts.

This family is structurally adjacent to `momentum`: both are continuation-side strategies. The difference is the entry trigger — momentum compares prices over fixed lookbacks; breakout compares the latest close against a rolling extremum. Both run on the same liquid universes and depend on the same data-quality assumptions, but they fire on materially different bar shapes (a slow grinding uptrend versus a sharp boundary cross).

### Daily-swing fit caveat (family-level)

Classical breakout systems (Turtle, Faith 2007) let winners run indefinitely — the strategy is profitable because the right-tail of trend continuation is fat. Phase 1's daily-swing tempo caps holds at ≤ 5 trading days, which truncates that fat right tail. **Expect material PF (profit-factor) degradation on every breakout instance vs. published results.** This is a known tradeoff. Each instance must measure it explicitly in its backtest evidence; there is no per-strategy code workaround.

### Semantic invariants (hardcoded in code, not overrideable by config)

- `long_only: true`
- `signal_evaluation: end_of_day` — signals are computed on completed daily bars; no intraday signal generation
- `execution_timing: next_market_open` — orders are submitted at the open following the signal
- `stop_semantics: close_based` — stops are evaluated once per day on close, executed at the next open
- `timeframe: 1D` (daily bars only)
- `promotion_requires_frozen_manifest: true` (per ADR 0015)
- `look_ahead_safe: true` — the entry-channel max is always computed over bars **strictly prior to** the latest bar (the breakout reference is `max(high[-N-1:-1])`, not `max(high[-N:])`). Including the latest bar's high in the channel reference would make the trigger trivially true on every breakout day.

Changing any of the above produces a **new version** of the strategy, not a variant.

### Parameter surface (allowed to vary in YAML)

| Parameter | Meaning | Notes |
|---|---|---|
| `universe` | Curated list of approved symbols | Phase 1 default: `universe.sector_etfs_spdr.v1` |
| `entry_channel_length` | Lookback over which the entry-side high is computed | typical: 20 (Turtle System 1) |
| `exit_channel_length` | Lookback over which the exit-side low is computed | typical: 10 (Turtle System 1) — typically shorter than entry |
| `ma_filter_length` | Long-only regime filter (close > SMA) | typical: 100–200 |
| `atr_lookback` | ATR window for the volatility stop | typical: 20 |
| `atr_stop_multiplier` | Stop distance as `atr_stop_multiplier × ATR(atr_lookback)` from entry price | typical: 1.5–2.5 |
| `stop_loss_pct` | Pure-percent close-based stop (in addition to the ATR stop — whichever is breached first wins) | typical: 0.05–0.10 |
| `max_hold_days` | Hard time stop in trading days since entry | typical: 5 (Phase 1 cap) |
| `max_concurrent_positions` | Per-strategy position cap | subject to global account-scoped caps per ADR 0024 |
| `sizing_rule` | One of: `equal_notional`, `fixed_notional` | extension requires a new version |
| `per_position_notional_pct` | Used when sizing rule requires it | |
| `ranking_enabled` | Whether to rank candidates | |
| `ranking_metric` | One of: `breakout_strength_descending` (closest to zero = bare breakout, larger = stronger) | extension requires a new version |
| `market_regime_symbol` | Optional broad-market regime filter symbol (e.g. `SPY`) | empty string disables |
| `market_regime_ma_length` | MA length for regime filter | typical: 100–200 |

### Entry rule (normative)

> Enter long at the **next market open** if, at the prior close:
> 1. The symbol is in the approved universe; **and**
> 2. `close > SMA(ma_filter_length)`; **and**
> 3. `close > max(high[-entry_channel_length-1:-1])` — i.e. today's close is above the highest high of the prior `entry_channel_length` bars, **excluding today's bar**; **and**
> 4. The symbol is not already in an open position; **and**
> 5. The symbol is not blocked by any risk or execution constraint.

### Exit rule (normative)

> Exit at the **next market open** if, at the prior close, **any** of the following holds (most-specific rule wins when several fire):
> - ATR stop: `close <= entry_price - (atr_stop_multiplier × ATR(atr_lookback))` measured against the entry-side ATR snapshot recorded at entry; **or**
> - Percent stop: `close <= entry_price * (1 - stop_loss_pct)`; **or**
> - `max_hold_days` reached (counted in trading days since entry); **or**
> - Channel-low exit: `close < min(low[-exit_channel_length-1:-1])`.

ATR is True Range averaged over `atr_lookback`, where `TR = max(high - low, |high - prior_close|, |low - prior_close|)` (Wilder, simple moving average over the lookback for Phase 1; Wilder smoothing is a separate version per ADR 0015 if needed later).

**Phase 1 ATR compromise (deviation from published family behavior):** the classical Donchian/Turtle ATR stop **freezes ATR at entry** and references that snapshot for the life of the trade. Milodex's runner and backtest engine currently stamp `entry_price` and `held_days` into `entry_state` but not `entry_atr`. To avoid a multi-module plumbing change while the family is at `stage: backtest`, breakout instances **compute ATR live on each evaluation** (over the latest `atr_lookback` bars) and use that for the stop reference. The cost is a self-adjusting stop that chases volatility — tighter when vol drops after entry, looser when vol rises. Promotion of any breakout instance to `paper` requires either (a) freezing `entry_atr` via runner/engine plumbing, or (b) explicit ADR acceptance of the live-ATR semantics as the family contract. This compromise is recorded here so the deviation from published behavior is visible.

### Ranking rule (normative)

When the number of qualifying entry candidates exceeds capacity:

> Rank qualifying candidates by `ranking_metric` (default: `breakout_strength_descending` — strongest breakout first, where strength is `(close - prior_channel_high) / prior_channel_high`). Take the top `(max_concurrent_positions - current_open_positions)` candidates. Reject the remainder silently; the rejection is recorded in the explanation record (R-XC-008) but generates no order.

### Default disable conditions

The strategy is halted when any of the following hold even if the code is functioning correctly:

1. Abnormal market regime or volatility far outside tested bounds
2. Significant spread expansion or liquidity deterioration
3. Major unresolved data-quality issues
4. Corporate-action handling uncertainty
5. Broker / execution instability
6. Repeated unexplained divergence between expected and actual fills
7. Breach of drawdown or risk-budget limits
8. Operator-declared pause after unusual market events

Same eight conditions as `meanrev` and `momentum` — all three families operate in the same liquid-market context and depend on the same data-quality and broker-stability assumptions. Instance YAML may add conditions but **shall not remove** any of the above.

### Decision rule identifiers

The breakout family emits the following `DecisionReasoning.rule` identifiers: `breakout.channel_entry`, `breakout.channel_exit`, `breakout.atr_stop`, `breakout.stop_loss`, `breakout.max_hold`, `no_signal`. The percent-stop and max-hold identifiers are deliberately family-prefixed (not shared with `meanrev`/`momentum`) so explanations remain unambiguous when multiple families run concurrently.

---

## Adding a New Family

A new family warrants a new section in this document when at least one of the following holds (per the version-vs-new-idea rule in VISION's Research Loop):

- The market behavior exploited is different (e.g., breakout / momentum / carry / dispersion)
- The entry or exit **concept** is different (e.g., time-of-day, event-driven, cross-sectional rather than single-name)
- The universe-construction logic is categorically different (e.g., rules-based screener vs. curated list)
- The risk model is materially different (e.g., volatility-targeted sizing vs. equal-notional)
- The timing model is different (e.g., multi-timeframe, intraday)

A new family requires a new section above. It does **not** require a new ADR unless it introduces a new architectural decision (e.g., a first non-daily timeframe, a first short strategy, a first cross-asset strategy). In that case, write the ADR first, then add the family section.

Within an existing family, changes are governed by ADR 0015's version-vs-variant rule.
