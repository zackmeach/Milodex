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

### Market behavior exploited (family-level)

The `meanrev` family targets **short-term overshoot and snapback behavior in liquid markets**. The hypothesis is that some assets temporarily move too far, too fast relative to recent behavior, and then mean-revert over the next few trading sessions as panic, forced flows, or short-term imbalance fades. The edge is not that prices always revert — it is that under defined conditions the probability-adjusted bounce may be large enough to justify disciplined entry with tight risk controls.

Individual **templates** within the family differ in *how they detect the overshoot* (RSI oscillator, bar-location, band width, etc.) but share the family-level invariants below.

### Semantic invariants (hardcoded, shared by every template)

- `long_only: true`
- `signal_evaluation: end_of_day` — signals are computed on completed daily bars; no intraday signal generation
- `execution_timing: next_market_open` — orders are submitted at the open following the signal
- `stop_semantics: close_based` — stops are evaluated once per day on close, executed at the next open
- `timeframe: 1D` (daily bars only)
- `promotion_requires_frozen_manifest: true` (per ADR 0015)

Changing any of the above produces a **new version** of the template, not a variant.

---

### Template: `daily.pullback_rsi2`

#### Hypothesis (template-specific)
Short-lookback RSI on trending names identifies transient oversold conditions that revert within a few sessions. Reference: Connors & Alvarez 2008 *Short Term Trading Strategies That Work*.

#### Parameter surface (allowed to vary in YAML)

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

#### Entry rule (normative)

> Enter long at the **next market open** if, at the prior close:
> 1. The symbol is in the approved universe; **and**
> 2. `close > SMA(ma_filter_length)`; **and**
> 3. `RSI(rsi_lookback) < rsi_entry_threshold`; **and**
> 4. The symbol is not already in an open position; **and**
> 5. The symbol is not blocked by any risk or execution constraint.

#### Exit rule (normative)

> Exit at the **next market open** if, at the prior close, **any** of the following holds:
> - `RSI(rsi_lookback) > rsi_exit_threshold`; **or**
> - `max_hold_days` reached (counted in trading days since entry); **or**
> - Stop condition triggered: `close <= entry_price * (1 - stop_loss_pct)`.

#### Ranking rule (normative)

> Rank qualifying candidates by `ranking_metric` (default: `rsi_ascending` — lowest RSI first). Take the top `(max_concurrent_positions - current_open_positions)` candidates. Reject the remainder silently; the rejection is recorded in the explanation record (R-XC-008) but generates no order.

---

### Template: `daily.ibs_lowclose`

#### Hypothesis (template-specific)
Close near the daily low (low Internal Bar Strength) signals that selling pressure dominated the session. On broad index ETFs this is a reliable 1-3 day oversold marker because single-name idiosyncratic risk is diluted away. Reference: Larsson & Lindahl 2013 *"Mining for Three Dollars a Day"* (Quantpedia); Connors 2008 *Short Term Trading Strategies That Work*.

**Critical note:** IBS is *not* a reliable signal on single names, where a close-near-low often reflects genuine stock-specific bad news rather than transient imbalance. This template is intended for broad / sector ETFs only; instance YAML is expected to enforce this through universe choice.

#### Parameter surface

| Parameter | Meaning | Notes |
|---|---|---|
| `universe` | Approved symbols (ETFs only in practice) | Use `universe_ref` to reference a frozen manifest |
| `ibs_entry_threshold` | Enter when IBS < this | typical: 0.15–0.25 |
| `prior_high_exit_enabled` | If true, exit when close > prior day's high | typical: true |
| `ma_filter_length` | Long-only regime filter | typical: 100–200 |
| `stop_loss_pct` | Close-based stop distance | typical: 0.02–0.04 |
| `max_hold_days` | Maximum trading days in position | typical: 2–4 |
| `max_concurrent_positions` | Per-strategy position cap | subject to global caps |
| `sizing_rule` | One of: `equal_notional`, `fixed_notional` | extension requires a new version |
| `per_position_notional_pct` | Used when sizing rule requires it | |
| `ranking_enabled` | Whether to rank candidates | |
| `ranking_metric` | One of: `ibs_ascending` | extension requires a new version |

IBS is defined as `IBS = (close - low) / (high - low)`, clamped to `[0, 1]`. When `high == low` (zero-range bar, typically a halted or all-day-flat session) IBS is undefined and the symbol is rejected for that evaluation with reason `"zero-range bar"`.

#### Entry rule (normative)

> Enter long at the **next market open** if, at the prior close:
> 1. The symbol is in the approved universe; **and**
> 2. `close > SMA(ma_filter_length)`; **and**
> 3. `IBS < ibs_entry_threshold`; **and**
> 4. The bar has non-zero range (`high > low`); **and**
> 5. The symbol is not already in an open position; **and**
> 6. The symbol is not blocked by any risk or execution constraint.

#### Exit rule (normative)

> Exit at the **next market open** if, at the prior close, **any** of the following holds:
> - `prior_high_exit_enabled == true` AND `close > prior_day_high`; **or**
> - `max_hold_days` reached (counted in trading days since entry); **or**
> - Stop condition triggered: `close <= entry_price * (1 - stop_loss_pct)`.

Unlike the `daily.pullback_rsi2` template, there is no RSI-style numeric exit threshold — the exit is event-based (close broke prior day's high) or time-based (max hold / stop). This reflects the shorter natural hold period of IBS signals; trades are expected to resolve within 1-3 bars.

#### Ranking rule (normative)

> Rank qualifying candidates by `ranking_metric` (default: `ibs_ascending` — lowest IBS first, i.e. closest to the low). Take the top `(max_concurrent_positions - current_open_positions)` candidates. Reject the remainder silently; the rejection is recorded in the explanation record (R-XC-008) but generates no order.

---

### Default disable conditions (family-level, shared by every template)

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

## Adding a New Family

A new family warrants a new section in this document when at least one of the following holds (per the version-vs-new-idea rule in VISION's Research Loop):

- The market behavior exploited is different (e.g., breakout / momentum / carry / dispersion)
- The entry or exit **concept** is different (e.g., time-of-day, event-driven, cross-sectional rather than single-name)
- The universe-construction logic is categorically different (e.g., rules-based screener vs. curated list)
- The risk model is materially different (e.g., volatility-targeted sizing vs. equal-notional)
- The timing model is different (e.g., multi-timeframe, intraday)

A new family requires a new section above. It does **not** require a new ADR unless it introduces a new architectural decision (e.g., a first non-daily timeframe, a first short strategy, a first cross-asset strategy). In that case, write the ADR first, then add the family section.

Within an existing family, changes are governed by ADR 0015's version-vs-variant rule.
