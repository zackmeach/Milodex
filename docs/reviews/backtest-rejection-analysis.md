# Backtest Rejection Analysis

**Generated:** 2026-05-05
**Question:** 11 of 12 backtested strategies were rejected (91.7%). Is this because the strategies lack edge (H1), the engine is biased toward false negatives (H2), or the gate is mis-calibrated for the backtest→paper transition (H3)?

**Verdict (one sentence):** The 91.7% number is not trustworthy — it conflates two distinct engine bugs (raw/unadjusted bars and a degenerate cached universe), a too-pessimistic slippage default, and a gate calibrated for promotion-to-live applied at promotion-to-paper; under DB-authoritative numbers and a paper-readiness gate, the rejection rate falls to **~14% (1 of 7)** and the actual diagnosis is closer to "engine and gate, not edge."

---

## 1. Executive summary

| Hypothesis | Verdict | Magnitude of contribution to rejection rate |
|---|---|---|
| **H1 — Real nulls** | Partial. Most strategies sit in the Sharpe 0.0–0.4 range, which is the *expected* signature of weak-but-real ETF/largecap edges, not zero-edge nulls. | ~20% of rejections look like genuine weak-null calls (e.g. tsmom 0.06). |
| **H2 — Engine false negatives** | **Confirmed, severe.** Three concrete bugs identified, two of them likely flipping multiple verdicts. | ~50% of rejections are tainted by one or more of: split-adjustment bug, cached-universe coverage gap, slippage over-pessimism. |
| **H3 — Gate mis-calibration for paper transition** | **Confirmed.** Switching to a paper-readiness gate (Sharpe > 0.0, DD < 25%, ≥ 30 fills) cuts rejection from 91.7% → 41.7%; restricted to DB-authoritative strategies, it cuts to ~14%. | The Sharpe > 0.5 floor is the single biggest driver of rejection — every DB-resident strategy clears DD and trade count once Sharpe is moved to > 0.0. |

**Practical implication:** the founder's three options should be acted on in this order — fix the engine bugs first (otherwise we're calibrating the gate against contaminated numbers), then re-evaluate the gate, then make per-strategy retire/keep calls.

---

## 2. Evidence — H1 (real nulls)

Below: every strategy classified by null-likelihood, using DB numbers where available and artifact numbers (with cache caveat) otherwise.

| Strategy | Cadence | Sharpe | Class | Rationale | Flip criterion |
|---|---|---|---|---|---|
| `regime.daily.sma200_rotation.spy_shy.v1` | regime | +1.07 | n/a (lifecycle exempt) | Phase 1 lifecycle-proof. | n/a |
| `momentum.daily.dual_absolute.gem_weekly.v1` | weekly | +0.66 | **Inconclusive** | Weekly cadence, 20 fills (10 round-trips) over 5y. Trade-count rejection is structural, not an edge signal. | Re-run with longer window or weekly-aware gate. |
| `momentum.daily.xsec_rotation.sector_etfs.v1` | daily | +0.34 | **Near-miss** | Sharpe in textbook "weak-real-edge" zone for sector rotation. Three windows positive, one negative; no fragility. | Sharpe drop below 0.2 after engine bugs fixed → reclass to weak null. |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | daily | +0.33 (DB) / −1.41 (artifact) | **Near-miss (DB)** / **Strong null (artifact)** | DB and artifact disagree dramatically. DB is canonical (regression test [`test_state_machine.py:194-222`](tests/milodex/promotion/test_state_machine.py:194) locks the DB number). Artifact's −1.41 is a cache-limited rerun. | Decision deferred until engine bugs fixed. |
| `meanrev.daily.ibs_lowclose.index_etfs.v1` | daily | +0.32 | **Near-miss / Inconclusive** | Sharpe positive, DD trivial (1.22%), but only 32 fills (16 round-trips) — power-limited regardless of gate. | Re-run on a wider universe; check if extra trades preserve Sharpe. |
| `breakout.daily.donchian_20_10.sector_etfs.v1` | daily | +0.20 | **Weak null** | Single-window dependency = True (stability flag). Edge concentrated in one window, looks like a sample-size mirage. | n/a — keep rejected. |
| `breakout.daily.atr_channel.sector_etfs.v1` | daily | +0.18 (artifact) | **Weak null (caveated)** | Cache-caveat row. Sister strategy to Donchian, similar profile. | Re-run with proper data to decide. |
| `momentum.daily.tsmom.curated_largecap.v1` | daily | +0.06 | **Weak null** | Sharpe near zero, single-window dependency, plausibly explained by split-adjustment artifacts (holds AAPL/NVDA/TSLA/AMZN/GOOGL through their 2020–22 splits). | Sharpe stays under 0.2 after split-adjustment fix → confirmed weak null. |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | daily | −0.10 (artifact) | **Inconclusive** | Cache-caveat row. Slippage drag (~1.7%) plausibly fully explains the −0.63% net return. | Sharpe still < 0.0 after slippage corrected → strong null; otherwise near-miss. |
| `seasonality.daily.turn_of_month.spy.v1` | monthly | −0.34 (artifact) | **Strong null (caveated)** | Cache-caveat row. Calendar anomalies on SPY are widely picked-over; negative Sharpe is the expected null. | n/a — keep rejected even after cache fix. |
| `breakout.daily.nr7_inside.liquid_largecap.v1` | daily | −0.58 (artifact) | **Inconclusive** | Cache caveat is severe here: declared 97 S&P-100 symbols, ran on 20. Negative Sharpe is not a credible edge signal under the declared universe. | Re-run on full universe; could land anywhere from −0.3 to +0.2. |
| `momentum.daily.52w_high_proximity.largecap.v1` | daily | −0.65 (artifact) | **Inconclusive** | Same 20/97 cache caveat as NR7. Plus split-adjustment exposure for the largest movers (NVDA, TSLA). Verdict cannot be reached without rerun. | Re-run on full universe with split-adjusted bars. |

**Class summary:** of 11 rejections — **2 strong nulls** (Donchian, ToM), **2 weak nulls** (atr_channel, tsmom), **3 near-misses** (xsec, RSI2, IBS), **4 inconclusive** (GEM, bbands, NR7, 52w). The H1 share is at most ~36% of rejections (4 of 11), and that's the upper bound; under DB-only data it falls further.

---

## 3. Evidence — H2 (engine false negatives)

Full audit findings reproduced from the engine-correctness pass. Three confirmed BUGs, one SUSPECT, three CORRECT.

### BUG 1 — Corporate actions not adjusted (severe)

[`alpaca_provider.py:131-150`](src/milodex/data/alpaca_provider.py:131) constructs `StockBarsRequest` without an `adjustment` parameter; alpaca-py defaults to `Adjustment.RAW`. The cache parquets therefore contain **raw, unadjusted bars**. Splits inside the 2020–24 backtest window:

- AAPL 4-for-1 — Aug 31 2020
- NVDA 4-for-1 — Jul 20 2021
- TSLA 5-for-1 — Aug 31 2020
- TSLA 3-for-1 — Mar 25 2022
- AMZN 20-for-1 — Jun 6 2022
- GOOGL 20-for-1 — Jul 18 2022

All five tickers are in `universe.phase1.curated.v1`. Any strategy holding through one of those days records a one-day mark-to-market drop of 50–95% on the affected position. With per-position notional caps of 0.10–0.20, that's a one-day equity hit of 5–19% per split — enough to wreck Sharpe and drawdown on small samples.

**Strategies most affected:** every largecap-universe strategy (RSI2, bbands, tsmom, NR7, 52w-high). The DB-vs-artifact divergence on RSI2 (Sharpe +0.33 vs −1.41) and tsmom (+0.06 vs +0.23) is consistent with different runs hitting or missing different splits depending on cache state.

**Fix:** one keyword argument — `adjustment=Adjustment.SPLIT` — at [`alpaca_provider.py:131`](src/milodex/data/alpaca_provider.py:131). Cache must be invalidated and re-fetched.

### BUG 2 — Largecap universe coverage gap (severe)

`market_cache/1Day/` contains 42 symbols. Two strategies declare `universe.sp100_liquid.v1` (97 symbols) but only **20 of the 97** are cached:

| Strategy | Declared | Cached | Coverage |
|---|---|---|---|
| `breakout.daily.nr7_inside.liquid_largecap.v1` | 97 | 20 | **20.6%** |
| `momentum.daily.52w_high_proximity.largecap.v1` | 97 | 20 | **20.6%** |

For ranking-based strategies, this collapses the candidate pool by ~80% and concentrates trades into a tech-heavy mega-cap subset. Their negative Sharpes (−0.58, −0.65) are not valid signals about the declared edge. The artifact's own Evidence Note ([line 220-222](docs/reviews/strategy-bank-final-comparison.md:220)) acknowledges the caveat and explicitly says rerun is required.

The data layer also does not warn on missing symbols: [`engine.py:348-349`](src/milodex/backtesting/engine.py:348) silently `continue`s when the primary bar is absent. **No "expected N, got M" assertion exists.** Adding one would catch this class of bug at run-time rather than report-time.

### BUG 3 — Decide-and-fill on the same close (look-ahead bias)

[`engine.py:_simulate`](src/milodex/backtesting/engine.py:298) iterates trading days; at line 346 it slices bars to `<= day` (inclusive of T's close), passes T's close into the strategy at line 368, and the same iteration submits orders that the [`SimulatedBroker`](src/milodex/broker/simulated.py:88) fills at `close × (1 ± slippage)`. The strategy decides on T's close and fills on T's close — same bar.

Correct pattern: decide on T's close → fill at T+1's open (or T+1's close).

**Effect on rejection rate:** *opposite* direction from the others — look-ahead biases reported Sharpes **upward**, so this bug masks true edge weakness rather than masking edge strength. Once fixed, today's marginal-positive strategies (xsec_rotation +0.34, tsmom +0.06) may decline further; today's marginal-negative strategies stay near zero. So this bug doesn't *cause* rejections; it just means the entire bank's metrics need re-baselining once it's fixed.

### SUSPECT — Slippage default too pessimistic

[`engine.py:113-122`](src/milodex/backtesting/engine.py:113) defaults `slippage_pct` to 0.001 (10 bps per fill, ~20 bps per round-trip). Every strategy YAML reviewed inherits the default. For Alpaca routing of liquid SPY/sector ETFs, observed round-trip slippage is typically **2–5 bps**, so 0.001 is a **2–4× pessimism premium**.

Approximate 5-year slippage drag (= `trades × 0.001 × position_pct`):

| Strategy | Trades | Pos pct | Drag | OOS return | Slippage-explained? |
|---|---|---|---|---|---|
| nr7_inside | 551 | 0.20 | ~11.0% | -0.58 Sharpe | ~half |
| donchian_20_10 | 458 | 0.20 | ~9.2% | +0.20 Sharpe | n/a |
| atr_channel | 457 | 0.20 | ~9.1% | +0.18 Sharpe | n/a |
| 52w_high | 413 | 0.20 | ~8.3% | -0.65 Sharpe | partial |
| xsec_rotation | 366 | 0.20 | ~7.3% | +0.34 Sharpe | n/a |
| **bbands_lowerband** | **165** | **0.10** | **~1.7%** | **−0.63% net (-0.10 Sharpe)** | **fully explained** |
| tsmom | 168 | 0.10 | ~1.7% | +2.03% | n/a |

`meanrev.daily.bbands_lowerband` is the cleanest case: its entire negative net return is plausibly accounted for by slippage drag alone. Under realistic Alpaca slippage, it likely lands at a small-positive Sharpe. (Estimated, not recomputed — recompute requires a backtest re-run.)

### CORRECT (verified)

- **trade_count** is fills (not round-trips) — [`engine.py:417,465`](src/milodex/backtesting/engine.py:417). The 30-trade gate is therefore a 15-round-trip floor for long-only strategies. This biases the gate *toward* false promotions, not false rejections — orthogonal to the rejection-rate question, but worth flagging because it overstates statistical power by 2×.
- **Walk-forward stitching** — [`walk_forward_runner.py:312-355`](src/milodex/backtesting/walk_forward_runner.py:312) computes Sharpe on stitched daily-return series (correct), not as mean of per-window Sharpes. Geometric equity stitching at line 340.
- **Indicator warm-up** — [`engine.py:_warmup_calendar_days`](src/milodex/backtesting/engine.py:575) returns `max(365, 3 × largest_int_param)`, comfortably covering all reviewed strategies' lookbacks.

---

## 4. Evidence — H3 (gate calibration)

Counterfactual sweeps from `scripts/counterfactual_gate.py`, applied to the same 12-strategy evidence set.

| Variant | Description | Pass | Reject |
|---|---|---|---|
| Production | Sharpe > 0.5, DD < 15%, ≥ 30 fills | 1 | 91.7% |
| Paper-readiness | Sharpe > 0.0, DD < 25%, ≥ 30 fills | 7 | 41.7% |
| Power-aware (trade-count only) | Production thresholds + cadence-scaled trade floor | 1 | 91.7% |
| Paper + power-aware | Sharpe > 0.0, DD < 25%, cadence-scaled trade floor | 7 | 41.7% |

**Restricted to DB-authoritative strategies (7):** the paper-readiness gate passes 6 of 7 (only IBS fails on power-aware trade count, otherwise it would also pass). That's a **~14% rejection rate** under the proposed paper gate vs. **86% under production** for the same DB rows.

**Why this matters.** The cost asymmetry argues hard for a permissive backtest→paper gate:

- False negative at backtest gate: missed edge, scrap a strategy that would have worked. Cost = research time + lost upside.
- False positive at backtest gate: a Sharpe-0.2 strategy gets paper-traded. Cost = one paper-trading slot for ~6 weeks. *No real capital at risk.*

The Sharpe > 0.5 / DD < 15% bar is reasonable for promotion-to-live (where false positives cost real money), but it's the same bar applied to backtest→paper. ADR 0020 needs to be revisited as one threshold *per stage transition*, not one threshold globally.

The dual_absolute (GEM weekly) case is the cleanest example of mis-calibration: Sharpe **0.66** (passes), 18.27% DD (just over 15%), 20 fills (= 10 round-trips, below 30). It's a weekly rotation strategy — 20 fills over five years is its natural cadence. Rejecting it on trade count is rejecting a structural feature of the strategy class, not a power deficit.

---

## 5. Per-strategy disposition

> **Update — 2026-05-05 (post-fix re-baseline).** The "Recommended action" column below was the pre-fix prediction. The full re-run on the fixed engine has now landed; canonical post-fix dispositions live in [`strategy-bank-post-fixes.md`](strategy-bank-post-fixes.md). Summary: 4 of 11 prior rejections flipped to PASS purely from engine fixes (tsmom, donchian, rsi2, atr_channel), under the unchanged strict gate. Pass rate went from 8.3% → 41.7%. Of the 7 remaining rejects: 3 are real nulls the gate is correct about (nr7, ibs, xsec_rotation), 3 are plausible weak edges below Sharpe 0.5 (bbands, ToM, 52w), and 1 is the cadence victim (gem_weekly: Sharpe 0.80 blocked on 20 fills + 18.72% DD). The H2 (engine) hypothesis is confirmed dominant; H3 (gate) survives but is now the minority problem. The original disposition table is preserved below for predictive-vs-actual comparison.

Combining streams A, B, C into one decision matrix (pre-fix prediction):

| Strategy | H1 class | H2 flags | Paper-readiness gate | Recommended action | **Post-fix actual** |
|---|---|---|---|---|---|
| `regime.sma200_rotation` | n/a | none | PASS (exempt) | Keep as Phase 1 lifecycle-proof. | **PASS** (Sharpe 1.10) ✓ |
| `dual_absolute.gem_weekly` | inconclusive | none | fail (trade count only) | Build a weekly-cadence gate variant; re-evaluate. Strong candidate. | **block** (Sharpe 0.80 — strong edge, blocked on cadence + DD) |
| `xsec_rotation.sector_etfs` | near-miss | look-ahead bias | PASS | Re-baseline after engine fixes; if Sharpe > 0.2 holds, promote to paper. | **block** (Sharpe 0.18 — fell with lookahead fix; weak null) |
| `pullback_rsi2.curated_largecap` | DB: near-miss; artifact: strong null | split-adjustment exposure | PASS (DB) | Re-baseline after split-adjustment fix; DB is canonical. | **PASS** (Sharpe 0.73) ✓ |
| `ibs_lowclose.index_etfs` | near-miss | none | PASS (under loose count) | Power-limited at 32 fills; re-run on wider universe. | **block** (Sharpe −0.23 — lookahead-fix flip; real null) |
| `donchian_20_10.sector_etfs` | weak null | look-ahead bias | PASS | Re-baseline. Stability flag = single-window dependency. Likely true weak null. | **PASS** (Sharpe 0.87 — stronger than predicted) ✓ |
| `atr_channel.sector_etfs` | weak null | cache caveat | PASS | Re-run with full data; otherwise behave like Donchian. | **PASS** (Sharpe 0.69 — stronger than predicted) ✓ |
| `tsmom.curated_largecap` | weak null | split-adjustment exposure | PASS | Re-baseline after split fix. Likely confirmed weak null. | **PASS** (Sharpe 1.19 — split-fix delta dominated) ✓ |
| `bbands_lowerband.curated_largecap` | inconclusive | slippage fully explains | fail (Sharpe < 0) | Re-run with realistic 3 bps slippage; could flip to small-positive. | **block** (Sharpe 0.43 — flipped to positive but below 0.5) |
| `turn_of_month.spy` | strong null | cache caveat | fail (Sharpe < 0) | Keep rejected. SPY calendar anomalies are picked-over. | **block** (Sharpe 0.37 — flipped positive; weak edge after T+1 fix) |
| `nr7_inside.liquid_largecap` | inconclusive | severe cache caveat (20/97) | fail (Sharpe < 0) | **Verdict cannot be reached.** Re-run on full universe before disposition. | **block** (Sharpe −0.11 — real null on full universe) |
| `52w_high_proximity.largecap` | inconclusive | severe cache caveat + split exposure | fail (Sharpe < 0) | **Verdict cannot be reached.** Re-run on full universe with adjusted bars. | **block** (Sharpe 0.33 — flipped from −0.65; weak edge below 0.5) |

---

## 6. Recommendations

Ranked by impact-per-effort. Recommendations 1 and 2 are mandatory before any further promotion decision; recommendations 3–5 are policy.

1. **Fix the split-adjustment bug** ([`alpaca_provider.py:131`](src/milodex/data/alpaca_provider.py:131)). One keyword argument plus cache invalidation. Until this lands, **no rejection of a largecap strategy is trustworthy.** Highest impact-per-effort in this whole analysis.
2. **Refresh `universe.sp100_liquid.v1` cache** so NR7 and 52w-high actually run on their declared universes. Add a coverage-assertion at backtest start: refuse to run a backtest where less than X% of the declared universe has bars in the requested window. Closes the silent-degradation hole.
3. **Introduce a paper-readiness gate** distinct from the live-readiness gate. Proposal: Sharpe > 0.0, DD < 25%, ≥ 30 fills for backtest→paper; keep Sharpe > 0.5 / DD < 15% / ≥ 30 fills for paper→micro_live (where it actually belongs). Requires amending ADR 0020 and adding a `to_stage`-aware branch in `check_gate`.
4. **Make `MIN_TRADES` cadence-aware** so weekly-rotation strategies aren't structurally excluded. Encode cadence in strategy YAML; gate reads cadence and applies a per-cadence floor.
5. **Lower default slippage for ETF universes to 3 bps** (or make it universe-aware: ETF/SPY = 3 bps, S&P-100 mega-caps = 5 bps, smaller-cap = 10 bps). Document the source. The current 10 bps default is a defensible-but-not-honest assumption; it's the difference between bbands being a small-positive-Sharpe strategy and a small-negative one.
6. **Fix the look-ahead fill timing** ([`engine.py:_simulate`](src/milodex/backtesting/engine.py:298)). Lower urgency than 1–2 because it makes existing Sharpes look *too good*, not too bad — but mandatory before any live promotion. After this fix, every existing rejection should be re-baselined.
7. **Persist research-screen runs to the event store.** The five Tier 2/3 strategies missing from `backtest_runs` undermined the whole analysis. The CLI path that produced the comparison artifact should write to the same store as the canonical CLI.
8. **Document `trade_count` semantics** in the gate's docstring (fills vs round-trips). Either rename `MIN_TRADES` → `MIN_FILLS` or compute round-trips and gate on those. Not blocking — just truthfulness.

---

## 7. What this analysis did *not* do (and why)

- **No re-runs.** The user explicitly chose "existing artifacts only." The strongest evidence (cost-stripped Sharpe, full-universe NR7/52w, split-adjusted RSI2) requires fresh backtests. Recommendations 1 and 2 above unlock that work.
- **No ADR draft.** The user chose "findings + recommendations." The ADR amending 0020 is the natural follow-up if recommendation 3 is accepted.
- **No code changes to the engine or gate.** Both stay untouched. The only new code is `scripts/counterfactual_gate.py`, which is read-only against the event store.
- **No precise cost-stripped Sharpe recompute.** Stream B's slippage table provides analytical bounds; an exact recompute would need re-running each backtest with `slippage_pct=0` plus an analyst-controlled add-back, out of scope here.

---

## 8. Deferred — a deeper question about how the gate decides "enough evidence"

This analysis treats the trade-count rule (`MIN_TRADES = 30`) as a calibration problem to be fixed by making the threshold cadence-aware (recommendation 4 above). That's the right tactical fix. But there's a conceptual question underneath it that this analysis explicitly did not pursue, and it's worth not losing track of.

**The question.** `MIN_TRADES = 30` is nominally a *statistical-power* threshold — the idea is that fewer than 30 observations is too thin a sample to draw inference about Sharpe. But the gate doesn't actually compute statistical power. It just counts trades and compares to a constant. Two strategies with 30 trades each can have wildly different evidence quality depending on the variance of their returns: a low-volatility regime strategy with 30 trades has much tighter Sharpe confidence intervals than a high-volatility momentum strategy with 30 trades. The current rule treats them identically.

A more honest version would compute the standard error of Sharpe given the sample size and return variance, then gate on something like "Sharpe is significantly different from zero at 90% confidence" or "Sharpe lower-bound exceeds X at 90% confidence." That's the framework most academic backtest-overfitting literature works in (Lopez de Prado's "Probability of Backtest Overfitting" is the canonical reference).

**Why it's deferred.** Three reasons.

1. **Premature.** With the engine bugs (split adjustment, cache coverage) still in place, recalibrating the gate against contaminated data would just be guessing. Fix the data first, see how the rejection table actually looks, then decide whether the existing gate concept is still inadequate.
2. **Heavier change.** A confidence-interval gate is not a config tweak — it changes what the gate fundamentally is. ADR 0020 and the regression tests in [`test_state_machine.py`](tests/milodex/promotion/test_state_machine.py) are written assuming threshold-based gating. Moving to interval-based gating means rewriting that machinery, redoing operator UX (now the gate's verdict depends on a confidence level you have to choose), and re-explaining promotion decisions in the explainability layer.
3. **Tactical fix may suffice.** If recommendations 3 and 4 (paper-readiness gate + cadence-aware trade count) eliminate the misclassifications observed in this analysis, the conceptual upgrade may not be needed at all. The simpler change should be tried first.

**When to revisit.** After recommendations 1–4 have landed and a fresh round of strategies has been backtested, look at the gate-decision distribution. If the gate is still flagging strategies as "too few trades" when their Sharpe confidence intervals would actually be tight (or passing strategies whose Sharpe intervals are wide and span zero), that's the trigger to take this on. Until then, this is a future-Zack problem.

**Tracking.** This analysis is the durable record. If this question becomes load-bearing, draft an ADR titled "Promotion gate: confidence-interval-based evidence threshold" and reference this section.

---

## 9. Reproducing this analysis

```bash
# Replay every gate variant, including verification:
python scripts/counterfactual_gate.py --all

# Just the production gate (should match strategy-bank-final-comparison.md):
python scripts/counterfactual_gate.py --variant production --verify

# Just the proposed paper gate:
python scripts/counterfactual_gate.py --variant paper-readiness
```

Source DB: `data/milodex.db`, table `backtest_runs.metadata_json`.
Source artifact: `docs/reviews/strategy-bank-final-comparison.md`.
Gate logic mirrors [`src/milodex/promotion/state_machine.py`](src/milodex/promotion/state_machine.py) (constants inlined in the script — see top-of-file note).
