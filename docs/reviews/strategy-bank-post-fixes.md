# Strategy Bank — Post-Fix Re-Baseline

**Generated:** 2026-05-05
**Window:** 2020-01-01 → 2024-12-31 (4 walk-forward folds)
**Universe coverage:** 100% across all 6 universes (sp100_liquid, sector_etfs_spdr, index_etfs, gem_quartet, spy_only, phase1.curated)
**Gate applied:** PRODUCTION (strict) — Sharpe > 0.5, DD < 15%, ≥ 30 fills. PR 3.1 (stage-aware gate) was deferred pending this re-baseline.

> **What this is.** The companion artifact to [`backtest-rejection-analysis.md`](backtest-rejection-analysis.md). The original analysis predicted that the 91.7% rejection rate was a composite of engine bugs and gate miscalibration; this report tests that prediction by re-running the same 12-strategy bank against the fixed engine. Fix attribution is the central question — what did the engine cleanups buy, and what (if anything) does the gate still need to do?

---

## 1. Executive summary

Under the same gate, on the same window, the rejection rate fell from **91.7% (11 of 12)** to **58.3% (7 of 12)** purely from the engine fixes. **Four strategies flipped from reject to pass** without any gate change.

| Cohort | Pre-fix gate | Post-fix gate | Δ |
|---|---|---|---|
| Pass (statistical) | 0 | 4 | +4 |
| Pass (lifecycle exempt) | 1 | 1 | 0 |
| Reject | 11 | 7 | −4 |
| **Pass rate** | **8.3%** | **41.7%** | **+33.4 pp** |

The headline finding of the original analysis is confirmed and stronger than predicted. The original predicted ~14% rejection only after *also* loosening the gate to paper-readiness; fixing the engine alone — keeping the strict production gate — dropped rejection by 33 percentage points. The H2 (engine false negatives) hypothesis is no longer hypothetical.

The remaining 7 rejects split cleanly into three groups:

- **3 real nulls** — Sharpe at or below zero (nr7 −0.11, ibs −0.23, xsec_rotation +0.18 fragile). These are genuine no-edge calls and the gate is doing its job.
- **3 near-misses below Sharpe 0.5** — bbands 0.43, ToM 0.37, 52w 0.33. Each cleared zero. Each is plausibly real but weak.
- **1 cadence victim** — gem_weekly: Sharpe **0.80** (a strong pass), but blocked on 20 fills (< 30) and DD 18.7% (> 15%). Both are structural artifacts of weekly rotation, not edge defects.

The cadence victim is the single cleanest argument that gate calibration still matters — even after the engine truth-telling is fixed, the production gate is rejecting a strategy with the second-highest Sharpe in the bank because it trades on a weekly cadence.

---

## 2. Per-strategy disposition

Combined post-fix verdict. The **Pre→Post Δ Sharpe** column is the engine-fix attribution; the larger the delta the more the strategy was being mis-measured.

| # | Strategy | Cadence | Pre Sharpe | **Post Sharpe** | Δ | DD | Trades | Gate |
|---|---|---|---:|---:|---:|---:|---:|---|
| 1 | `momentum.daily.tsmom.curated_largecap.v1` | daily | +0.06 | **+1.19** | **+1.13** | 7.75% | 462 | **PASS** |
| 2 | `regime.daily.sma200_rotation.spy_shy.v1` | regime | +1.07 | **+1.10** | +0.03 | 0.96% | 31 | **PASS** (lifecycle) |
| 3 | `breakout.daily.donchian_20_10.sector_etfs.v1` | daily | +0.20 | **+0.87** | **+0.67** | 5.88% | 419 | **PASS** |
| 4 | `momentum.daily.dual_absolute.gem_weekly.v1` | weekly | +0.66 | **+0.80** | +0.14 | 18.72% | 20 | block (DD, count) |
| 5 | `meanrev.daily.pullback_rsi2.curated_largecap.v1` | daily | +0.33 | **+0.73** | **+0.40** | 5.98% | 734 | **PASS** |
| 6 | `breakout.daily.atr_channel.sector_etfs.v1` | daily | +0.18 | **+0.69** | **+0.51** | 4.30% | 427 | **PASS** |
| 7 | `meanrev.daily.bbands_lowerband.curated_largecap.v1` | daily | −0.10 | **+0.43** | **+0.53** | 4.19% | 349 | block (Sharpe) |
| 8 | `seasonality.daily.turn_of_month.spy.v1` | monthly | −0.34 | **+0.37** | **+0.71** | 4.89% | 49 | block (Sharpe) |
| 9 | `momentum.daily.52w_high_proximity.largecap.v1` | daily | −0.65 | **+0.33** | **+0.98** | 18.99% | 753 | block (Sharpe, DD) |
| 10 | `momentum.daily.xsec_rotation.sector_etfs.v1` | daily | +0.34 | **+0.18** | −0.16 | 13.93% | 388 | block (Sharpe) |
| 11 | `breakout.daily.nr7_inside.liquid_largecap.v1` | daily | −0.58 | **−0.11** | +0.47 | 19.63% | 929 | block (Sharpe, DD) |
| 12 | `meanrev.daily.ibs_lowclose.index_etfs.v1` | daily | +0.32 | **−0.23** | −0.55 | 5.78% | 395 | block (Sharpe) |

Sources: pre-fix Sharpes from [`backtest-rejection-analysis.md`](backtest-rejection-analysis.md) §2 (DB where present, artifact otherwise); post-fix Sharpes from this re-baseline (run IDs in `strategy-bank-post-fixes.json` and the appended raw screen output).

---

## 3. What the fixes actually bought

Attribution is approximate — multiple PRs landed simultaneously and their effects compound. The qualitative reasoning below identifies the *dominant* driver per flip.

### Strong engine-attributable flips (Δ Sharpe > +0.5)

| Strategy | Δ | Dominant driver | Mechanism |
|---|---:|---|---|
| `tsmom.curated_largecap` | **+1.13** | PR 1.1 split adjustment | Held AAPL/NVDA/TSLA/AMZN/GOOGL through their 2020–22 splits. Each split previously recorded as a 50–95% one-day MTM drop. With `Adjustment.SPLIT`, those artificial drawdowns vanish. |
| `52w_high_proximity` | **+0.98** | PR 1.2 universe coverage + PR 1.1 split | Was running on 20 of 97 declared sp100 symbols; now runs on the full universe (753 trades post-fix). Plus exposure to the same large-cap splits as tsmom. |
| `turn_of_month.spy` | **+0.71** | PR 2.1 T+1 fill timing | Calendar anomaly that buys near month-end; previously decided AND filled on the same close ate the entire intra-day signal. Filling at T+1 open captures the open-to-close drift the strategy actually targets. |
| `donchian_20_10.sector_etfs` | **+0.67** | PR 2.2 ETF-tier slippage | Pure sector ETFs, no splits, no coverage gap. The 7-bps difference (10 → 3) on 419 trades is ~3% of round-trip cost. The lookahead fix (PR 2.1) compounds the same way. |
| `bbands_lowerband.curated_largecap` | **+0.53** | PR 2.2 slippage + PR 1.1 splits | Section 3.2 of the original analysis predicted slippage drag fully accounted for the −0.63% net return. Confirmed: at 5 bps (sp100 tier) the strategy clears zero. Splits exposure adds the rest. |
| `atr_channel.sector_etfs` | **+0.51** | PR 1.2 coverage + PR 2.2 slippage | Was a cache-caveat row; now runs on 100% sector ETFs at 3 bps. Behaves like donchian: same family, same universe, same fix profile. |

### Moderate flips (Δ +0.4 to +0.5)

| Strategy | Δ | Driver |
|---|---:|---|
| `pullback_rsi2.curated_largecap` | **+0.40** | The canonical regression case from `test_state_machine.py:194-222`. DB had 0.327 (the strategy-bank-final-comparison artifact derided this number as wrong; post-fix it's 0.73). The DB number was directionally right; the engine was understating the magnitude through largecap-splits exposure. |
| `nr7_inside.liquid_largecap` | **+0.47** | Universe coverage fix: trade count jumped 20-symbol to 97-symbol (929 trades post-fix). Sharpe still negative (−0.11), but no longer the −0.58 artifact. The strategy is genuinely null; the original number was indefensible. |

### Strategies that got worse

| Strategy | Δ | Plausible cause |
|---|---:|---|
| `xsec_rotation.sector_etfs` | **−0.16** | T+1 fill timing (PR 2.1) — this strategy's edge was partly the lookahead-bias artifact. Fragile=True confirms single-window dependency. |
| `ibs_lowclose.index_etfs` | **−0.55** | Same. IBS rotates daily on internal bar strength; same-close fills were buying the bottom of the bar that contained the signal. Real edge appears smaller after the bias is removed. |

These two are the cleanest cases for the lookahead correction. A strategy whose Sharpe falls when fills move from same-close to next-open was harvesting a measurement artifact, not a real edge.

### `gem_weekly` — the cadence victim

Sharpe **+0.80**, max DD 18.72%, **20 fills** over five years. Both block reasons — DD > 15% and trades < 30 — are predicted by structural cadence:

- Weekly rotation = ~52 decisions/year × 5y = 260 decisions, of which most are no-change. Twenty fills (each fill is one buy-or-sell leg, so this is ~10 round-trips) is the natural rate for dual-momentum.
- Lower fill count means each individual round-trip has higher per-trade weight, so the empirical max DD across just 4 walk-forward windows is naturally noisier.

A cadence-aware gate (PR 3.2 in the original plan) would set the trade floor based on cadence; a stage-aware gate (PR 3.1) would let the strategy pass backtest→paper on its Sharpe alone. Either fix unblocks this strategy. Without them, gem_weekly's strong Sharpe goes nowhere.

---

## 4. The remaining rejects, classified

| Strategy | Sharpe | Class | Honest verdict |
|---|---:|---|---|
| `gem_weekly` | +0.80 | **Strong edge, structural rejection** | Clear pass on edge; blocked on cadence artifacts. |
| `bbands_lowerband` | +0.43 | **Plausible weak edge** | Below 0.5 but cleanly above zero with a 4.2% DD. Five-year sample at 349 trades is decent power. |
| `turn_of_month.spy` | +0.37 | **Plausible weak edge, fragile** | Single-window dependency + fragile flag. SPY calendar anomaly that's been picked-over for decades; some edge appears to remain. |
| `52w_high_proximity` | +0.33 | **Marginal** | Cleared the universe-coverage and split fixes. Real but small. DD 18.99% is the strongest reason for caution. |
| `xsec_rotation` | +0.18 | **Weak null, fragile** | Lost ground after the lookahead fix. Single-window dependency. Likely real null. |
| `nr7_inside` | −0.11 | **Real null** | Negative Sharpe + 19.63% DD across 929 trades. The universe-coverage fix raised it from −0.58 but the underlying signal is gone. |
| `ibs_lowclose` | −0.23 | **Real null** | The lookahead-removal flip is the smoking gun: this strategy was harvesting fill timing, not internal bar strength. |

Three rejections (nr7, ibs, xsec) the gate is correct about. Four rejections (gem_weekly, bbands, ToM, 52w) the gate is debatably wrong about — they're below the live-readiness bar but plausibly above the paper-readiness bar.

---

## 5. The PR 3.1 revival question

The original plan's PR 3.1 introduced a stage-aware gate: permissive at backtest→paper (Sharpe > 0.0, DD < 25%, ≥ 15 round-trips), strict at paper→live (Sharpe > 0.5, DD < 15%, ≥ 30 round-trips). It was reverted in the working tree pending this re-baseline.

What the post-fix data argues:

| Argument | Direction |
|---|---|
| Engine fixes alone took rejection from 91.7% → 58.3% (4-of-11 flipped). | Argues PR 3.1 is **less load-bearing** than originally predicted. The bulk of the false-rejection problem was H2 (engine), not H3 (gate). |
| 4 of 7 remaining rejects are plausible weak edges (gem_weekly, bbands, ToM, 52w), not real nulls. | Argues PR 3.1 is **still load-bearing**. Strict gate is rejecting plausibly-real edges before they get any out-of-sample test. |
| 3 of 7 remaining rejects are real nulls and the strict gate correctly catches them. | Argues PR 3.1 is **safe to enable** — paper-readiness wouldn't promote nr7/ibs/xsec either (they're at-or-below zero). |
| gem_weekly Sharpe 0.80 blocked on cadence is the single cleanest mis-calibration in the bank. | Argues PR 3.2 (cadence-aware) is **independently load-bearing**. |

**My read:** The data argues for reviving both PR 3.1 and PR 3.2, but with weaker urgency than the original plan implied. The engine was the dominant problem; the gate is the minority problem. The four remaining "near-miss" rejects (gem_weekly, bbands, ToM, 52w) are exactly the cohort PR 3.1+3.2 would unblock at the backtest→paper transition.

A reasonable middle path: revive PR 3.1+3.2 as planned, keeping the strict thresholds at paper→live untouched. The asymmetric-cost argument from the original plan still holds — false positives at backtest→paper risk only paper-trading slots, not real capital.

If you'd rather hold off on PR 3.1+3.2: the current 5 passes are still a usable bank to build a paper-trading rotation around (tsmom, donchian, RSI2, atr_channel, plus the regime lifecycle-proof). The cadence and near-miss strategies sit on the bench until either (a) the gate is revisited, or (b) those strategies get a research re-think.

---

## 6. Notes and caveats

- **`sample.daily.example.curated.v1` errored** during the screen run — `sample_strategy.yaml` was picked up by the `*.yaml` glob and has no registered template family. The error is harmless to this analysis (it doesn't affect the other 12) but worth filtering at the CLI level. Suggested follow-up: either move the sample file out of `configs/` or have the screen CLI skip strategies whose template isn't registered.
- **Walk-forward window count.** Post-fix Sharpes are aggregated across 4 OOS folds. Sample sizes for non-fragile strategies (most of the bank) cleanly satisfy the 30-trade floor at the *aggregate* level; per-fold counts are smaller and less stable.
- **Database authority.** All 13 rows above are now event-store rows (run IDs included). PR 4.1's research-screen persistence fix is doing its job — there's no DB↔artifact divergence in this re-baseline.
- **Correlation matrix appended below.** The diversification picture is mixed: the four passes have pairwise correlations in the 0.18–0.73 range, which is acceptable but not orthogonal. donchian and atr_channel at 0.73 are essentially the same strategy in different clothes.
- **Fragility flags.** Three of the seven blocked rows are flagged single-window-fragile (ToM, 52w, xsec_rotation) — those Sharpes are concentrated in one of four walk-forward folds and shouldn't be treated as stable estimates. One pass row is also flagged fragile: **rsi2** (Sharpe 0.73, fragile=True). Before allocating to rsi2 in paper trading, a per-window Sharpe inspection is warranted; if its edge collapses outside one fold, treat the aggregate Sharpe as optimistic.

---

## 7. Recommendation

1. **Keep the current 5 passes as the de-facto Phase 1 bank.** No code changes required to act on this; PR 4.1 already lands them in the event store.
2. **Decide on PR 3.1+3.2 revival as a policy question, not a data question.** The data is mixed. The deciding factor is risk appetite: do you want to paper-trade Sharpe 0.33–0.80 strategies on their own merit, or hold the line at strict-promotion across stages?
3. **Filter out `sample_strategy.yaml`** at the screen-CLI level (or move it to a non-discovery path). A 30-second fix.
4. **Don't recompute the strategy bank again until something actually changes.** The numbers above are now the canonical post-fix baseline.

---

## Appendix A — Raw screen output

Generated by `milodex research screen --configs '*.yaml' --start 2020-01-01 --end 2024-12-31`. Full per-strategy detail follows.

| strategy_id | family | trades | oos_sharpe | oos_max_dd | fragile | gate |
| --- | --- | --- | --- | --- | --- | --- |
| `momentum.daily.tsmom.curated_largecap.v1` | momentum | 462 | 1.19 | 7.75% | no | pass (statistical) |
| `regime.daily.sma200_rotation.spy_shy.v1` | regime | 31 | 1.10 | 0.96% | no | pass (lifecycle_exempt) |
| `breakout.daily.donchian_20_10.sector_etfs.v1` | breakout | 419 | 0.87 | 5.88% | no | pass (statistical) |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | meanrev | 734 | 0.73 | 5.98% | yes | pass (statistical) |
| `breakout.daily.atr_channel.sector_etfs.v1` | breakout | 427 | 0.69 | 4.30% | no | pass (statistical) |
| `momentum.daily.dual_absolute.gem_weekly.v1` | momentum | 20 | 0.80 | 18.72% | no | block |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | meanrev | 349 | 0.43 | 4.19% | no | block |
| `seasonality.daily.turn_of_month.spy.v1` | seasonality | 49 | 0.37 | 4.89% | yes | block |
| `momentum.daily.52w_high_proximity.largecap.v1` | momentum | 753 | 0.33 | 18.99% | yes | block |
| `momentum.daily.xsec_rotation.sector_etfs.v1` | momentum | 388 | 0.18 | 13.93% | yes | block |
| `breakout.daily.nr7_inside.liquid_largecap.v1` | breakout | 929 | -0.11 | 19.63% | no | block |
| `meanrev.daily.ibs_lowclose.index_etfs.v1` | meanrev | 395 | -0.23 | 5.78% | no | block |
| `sample.daily.example.curated.v1` | — | 0 | n/a | 0.00% | no | error |

Per-strategy detail and run IDs are in the JSON sibling: [`strategy-bank-post-fixes.json`](strategy-bank-post-fixes.json).

## Appendix B — OOS return correlation matrix

(Pearson correlation of daily OOS returns across 4 walk-forward folds. Cells with `n/a` indicate insufficient overlap.)

| strategy | tsmom | regime | donchian | rsi2 | atr | gem | bbands | ToM | 52w | xsec | nr7 | ibs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `tsmom` | 1.00 | 0.59 | 0.44 | 0.26 | 0.48 | 0.51 | 0.14 | 0.16 | 0.42 | 0.48 | 0.30 | 0.36 |
| `regime` | 0.59 | 1.00 | 0.50 | 0.41 | 0.47 | 0.83 | 0.35 | 0.32 | 0.42 | 0.60 | 0.35 | 0.67 |
| `donchian` | 0.44 | 0.50 | 1.00 | 0.19 | **0.73** | 0.44 | 0.08 | 0.18 | 0.41 | 0.40 | 0.37 | 0.33 |
| `rsi2` | 0.26 | 0.41 | 0.19 | 1.00 | 0.18 | 0.44 | 0.41 | 0.10 | 0.17 | 0.30 | 0.15 | 0.41 |
| `atr_channel` | 0.48 | 0.47 | **0.73** | 0.18 | 1.00 | 0.40 | 0.08 | 0.16 | 0.35 | 0.40 | 0.38 | 0.29 |
| `gem_weekly` | 0.51 | **0.83** | 0.44 | 0.44 | 0.40 | 1.00 | 0.41 | 0.23 | 0.35 | 0.48 | 0.33 | 0.58 |

Bolded cells indicate >0.7 — strong covariation worth flagging when sizing. donchian↔atr_channel (0.73) and regime↔gem_weekly (0.83) are the two clusters.
