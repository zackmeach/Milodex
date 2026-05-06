# Strategy Bank — Survivorship-Corrected Re-Baseline (curated_largecap v2)

**Generated:** 2026-05-06
**Window:** 2020-01-01 → 2024-12-31 (4 walk-forward folds)
**Universe under test:** `universe.curated_largecap.v2` — ex-ante selected at 2019-12-31
**Universe replaced:** `universe.phase1.curated.v1` — hindsight-selected, retained for historical reproducibility
**Engine:** post-PR-#27/#28/#29 (split + dividend adjusted bars, T+1 fills, ETF-tier slippage, universe coverage assertion, survivorship-disclosure column).

> **What this is.** Companion to [`strategy-bank-post-fixes.md`](strategy-bank-post-fixes.md). The earlier doc re-baselined the bank under engine fixes and disclosed the survivorship-bias hazard via PR #29. This doc *quantifies* that bias for the three strategies that lived on the hindsight-selected `curated_largecap` universe (tsmom, rsi2, bbands) by re-running them against the ex-ante-selected v2 manifest. The other ten strategies sit on universes that were already survivorship-immune (ETF-only) or are out of scope for this PR (sp100_liquid); their numbers from the post-dividend baseline hold unchanged.

---

## 1. Executive summary

Three strategies migrated from `universe.phase1.curated.v1` (hindsight) to `universe.curated_largecap.v2` (ex-ante S&P 100 at 2019-12-31, market cap ≥ $100B). **All three still pass the strict promotion gate** after the correction. None flipped block.

| Strategy | Pre (v1) Sharpe | Post (v2) Sharpe | Δ | Verdict |
|---|---:|---:|---:|---|
| `momentum.daily.tsmom.curated_largecap.v1` | 1.24 | **0.88** | **−0.36** | passes — strong edge survives |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | 1.04 | **0.73** | **−0.31** | passes — clear edge survives |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | 0.51 | **0.52** | +0.01 | passes — universe-composition-resilient |

The headline finding is **predicted in shape, surprising in distribution**. The two momentum-and-trend strategies (tsmom, rsi2) lost 0.31-0.36 Sharpe — squarely in the predicted 0.2-0.4 inflation band for a 5-year curated-large-cap window. **bbands barely moved at all (+0.01)**, which is the most informative single result in this report: mean-reversion on individual names triggered by lower-band penetration is largely indifferent to *which* 22 large-caps populate the universe, because the trigger fires on instrument-specific volatility rather than market-cap-cluster trends. That's a credibility positive for bbands.

The strict gate (Sharpe > 0.5, DD < 15%, ≥ 30 trades) catches none of the three. The earlier-than-feared rule of thumb "survivorship correction will likely flip bbands back to block" did not materialize.

---

## 2. Per-strategy detail

### `momentum.daily.tsmom.curated_largecap.v1`

| Metric | Pre (v1, hindsight) | Post (v2, ex-ante) | Δ |
|---|---:|---:|---:|
| OOS Sharpe | 1.24 | **0.88** | −0.36 |
| OOS Max DD | 5.49% | 6.25% | +0.76 pp |
| Trades | 464 | 458 | −6 |
| Fragility flag | no | no | unchanged |
| Gate | pass | pass | — |

The Sharpe drop is the largest of the three migrants, which is consistent with how time-series momentum harvests sustained trends. The 2020-2024 winners (NVDA, AMZN, AVGO, META) had unusually clean trend exposure in the v1 universe; the v2 universe replaces three of them (META=FB ticker change, TSLA not in S&P 100 in 2019, AMD not in S&P 100 in 2019) with five names that didn't have spectacular 2020-2024 trends (DIS, INTC, KO, VZ, V). The diluted trend exposure is exactly what the methodology was designed to surface.

A 0.88 Sharpe with a 6.25% max DD over 458 trades is still a clean, gate-clearing edge. Trade count is essentially unchanged because the trend-detection logic fires roughly the same number of times regardless of whose trends qualify; the *quality* of the trends moves, not the *count* of qualifying setups.

### `meanrev.daily.pullback_rsi2.curated_largecap.v1`

| Metric | Pre (v1, hindsight) | Post (v2, ex-ante) | Δ |
|---|---:|---:|---:|
| OOS Sharpe | 1.04 | **0.73** | −0.31 |
| OOS Max DD | 3.88% | 3.98% | +0.10 pp |
| Trades | 753 | 776 | +23 |
| Fragility flag | no | no | unchanged |
| Gate | pass | pass | — |

The Sharpe drop matches the predicted survivorship-haircut magnitude. Notably the *trade count went up* (753 → 776) — replacing high-trend names (NVDA, META, AMD) with stagnant or struggling names (INTC, T-not-included-but-similar) increases the count of RSI(2) ≤ 10 entry triggers, because stagnant names oscillate more around their MA than trending names do. The mean-reversion mechanic is doing the same job; the *pool of opportunities* widened slightly while the *quality* tightened — a fair-value trade.

The strategy is currently **demoted from paper to backtest** as part of this PR. Re-promotion to paper requires a fresh `milodex promotion promote` invocation citing this run as the new evidence base.

### `meanrev.daily.bbands_lowerband.curated_largecap.v1`

| Metric | Pre (v1, hindsight) | Post (v2, ex-ante) | Δ |
|---|---:|---:|---:|
| OOS Sharpe | 0.51 | **0.52** | +0.01 |
| OOS Max DD | 2.65% | 3.38% | +0.73 pp |
| Trades | 350 | 361 | +11 |
| Fragility flag | no | no | unchanged |
| Gate | pass (barely) | pass (barely) | — |

The most interesting result. The previous-night concern was that bbands' Sharpe-0.51 near-miss was likely survivorship-inflated and would flip back to block on correction. **It didn't.** The Sharpe stayed essentially flat (+0.01, well within noise), and bbands continues to clear the gate.

The mechanism: Bollinger-band lower-penetration is an *instrument-specific* mean-reversion trigger. A name's price oscillating below its 20-day MA by 2σ is a volatility-and-distribution event, not a market-cap-cluster trend event. Whether the universe contains AAPL or INTC or both, the lower-band trigger fires when individual name volatility produces an oversold reading. The aggregate Sharpe washes out across many names whose oversold dynamics are roughly comparable.

This is a credibility positive for bbands — it's the strategy in this PR whose backtest numbers are *least* dependent on universe selection bias, which means its post-correction Sharpe is the most-trustworthy in the bank.

---

## 3. What this changes about the bank's overall verdict

**Previous bank state (post-PR-#28, pre-survivorship-correction):**
- 6 passes: tsmom, regime, rsi2, donchian, atr_channel, bbands

**New bank state (post-survivorship-correction):**
- 6 passes: tsmom, regime, rsi2, donchian, atr_channel, bbands — **all the same names, with corrected numbers for three of them.**

| Strategy | Pre-correction Sharpe | Post-correction Sharpe | Trustworthiness |
|---|---:|---:|---|
| `regime` | 1.19 | 1.19 | unchanged — already on survivorship-immune universe |
| `donchian` | 0.87 | 0.87 | unchanged — sector_etfs immune |
| `atr_channel` | 0.64 | 0.64 | unchanged — sector_etfs immune |
| `tsmom` | 1.24 | **0.88** | corrected; still strong |
| `rsi2` | 1.04 | **0.73** | corrected; still passes |
| `bbands` | 0.51 | **0.52** | universe-resilient; corrected number is the same |

The bank still has 6 passes. The composition is unchanged. The *numbers* for tsmom and rsi2 are now honest. bbands' number was already honest. donchian, atr_channel, and regime were already honest because they don't use a stock-cluster universe.

---

## 4. What about the other strategies?

The seven remaining blocked strategies are unchanged by this PR:

- `gem_weekly` (gem_quartet ETFs, immune) — still 0.83 Sharpe, blocked on cadence (20 trades, DD 17.88%)
- `xsec_rotation` (sector_etfs, immune) — still 0.22 Sharpe, fragile, blocked
- `nr7` (sp100_liquid, **not corrected** — needs separate PR for point-in-time membership)
- `52w_high_proximity` (sp100_liquid, **not corrected** — same)
- `ibs` (index_etfs, immune) — still −0.12, real null
- `ToM` (spy_only, immune) — still −0.27, real null
- (sample.daily.example.curated.v1) — still errors, registry mismatch

The two `sp100_liquid`-based strategies (nr7, 52w) are the remaining survivorship-bias-affected research-target strategies. Both are currently blocked, so the bias matters less in the immediate term — fixing `sp100_liquid` won't unblock new candidates, only refine the credibility of already-blocked ones. Tracked as future work.

---

## 5. Recommendations

1. **Re-promote rsi2 to paper** when ready. The post-correction Sharpe of 0.73 cleanly clears the gate; the strategy's edge is real-shaped under ex-ante selection. The promotion-evidence package now has a defensible universe basis.

2. **Trust bbands more than before.** The universe-resilience finding is a positive credibility signal. If you're considering adding bbands to a paper-trading rotation, it's the strategy whose backtest number you should weight most heavily relative to the gate threshold.

3. **Lower expectations for tsmom marginally.** A real 0.88 Sharpe is still worth running, but the 1.24 number that was on display before is fictitious for forward-looking purposes. If forward Sharpe drops further in paper trading, that's not a regression — it's a continuation of the survivorship haircut.

4. **Defer `sp100_liquid` correction.** Both strategies that use it are already blocked at the gate; correcting the universe would refine their credibility but not change their disposition. Address it when there's a research candidate that *would* unblock with point-in-time `sp100_liquid` membership.

5. **Keep the methodology auditable.** The new manifest (`configs/universe_curated_largecap_v2.yaml`) documents its selection rule inline. Future curators can verify the membership logic without recomputing it from scratch — that's the durable value-add of ex-ante selection over post-hoc curation.

---

## 6. Methodology notes (audit trail)

**Selection rule:** Membership in S&P 100 ("OEX") at 2019-12-31, market cap ≥ $100B. The 2019-12-31 cutoff pre-dates every Phase 1 evaluation window, eliminating any 2020-2024 information from the selection.

**Drops from v1:**
- META — was "FB" until 2022-06-09 ticker change. Including it requires ticker-aliasing infrastructure (not built in this PR). Re-addable when point-in-time `sp100_liquid` migration introduces aliasing.
- TSLA — ~$76B market cap at 2019-12-31; not in S&P 100. Entered S&P 500 only in Dec 2020.
- AMD — ~$56B market cap at 2019-12-31; not in S&P 100.

**Adds vs v1:**
- DIS, INTC, KO, V, VZ — all in S&P 100 at 2019-12-31 with market cap > $200B. INTC and VZ struggled 2020-2024, DIS COVID-shocked, KO and V steady — together they represent a defensibly-2019 "what would I research today?" cohort that doesn't require 2020-2024 hindsight.

**Final v2 stock list (22 names):** AAPL, AVGO, GOOGL, INTC, MSFT, NVDA, DIS, NFLX, VZ, AMZN, COST, HD, KO, PG, WMT, BAC, JPM, V, CVX, XOM, JNJ, UNH.

**Sector spread:** Technology 6 / Communication Services 3 / Consumer Discretionary 3 / Consumer Staples 3 / Financials 3 / Energy 2 / Healthcare 2.

**ETFs unchanged from v1:** 20 ETFs (SPY, QQQ, IWM, DIA, sector SPDRs, GLD, SLV, TLT, SHY, etc.) — all survivorship-immune.

**Cache:** v3 (split + dividend adjusted, post-PR-#28). All 42 v2 symbols pre-fetched via `milodex data fetch-universe --universe-ref universe.curated_largecap.v2`.

**Gate applied:** PRODUCTION (strict) — Sharpe > 0.5, DD < 15%, ≥ 30 fills. Same as PR #27's re-baseline.

**Walk-forward:** 4 OOS folds, OOS-aggregate metrics per ADR 0021.

---

## 7. Appendix — operator follow-up steps

After this PR merges, the operator runs:

1. `milodex promotion demote meanrev.daily.pullback_rsi2.curated_largecap.v1 --to backtest --reason "Universe migration: phase1.curated.v1 → curated_largecap.v2 (ex-ante survivorship correction)" --approved-by operator` — captures the demotion in the event-store ledger. The YAML already says `stage: backtest` (committed in this PR), so this is ledger-only.

2. `milodex promotion promote meanrev.daily.pullback_rsi2.curated_largecap.v1 --to paper --run-id <run-id-from-this-screen> --recommendation "Same RSI(2) edge; universe now ex-ante selected (survivorship-corrected). Sharpe drops 1.04 → 0.73 — squarely in predicted band; remaining edge is real-shaped at the gate threshold." --risk "Universe composition change is a real config break — paper-trading evidence on phase1.curated.v1 does not transfer 1:1 to curated_largecap.v2. The strategy is starting from backtest credibility under the new universe." --approved-by operator` — re-freezes the strategy at paper stage with the new evidence package.
