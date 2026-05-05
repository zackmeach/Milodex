# Strategy Bank — Tier 1 Comparison

**Date:** 2026-05-05
**Author:** Generated as Tier 1 wrap-up per [research-strategy-bank-roadmap §3 exit criteria](../superpowers/plans/2026-04-24-research-strategy-bank-roadmap.md).
**Status:** All four Tier 1 strategies landed at `stage: backtest`. None promoted to paper. This document is the side-by-side evidence record the bank was built to produce.

---

## 1. What Tier 1 Was Designed To Do

The strategy bank's [explicit stance](../superpowers/plans/2026-04-24-research-strategy-bank-roadmap.md#explicit-stance):

> Every strategy landed by this roadmap ships with `stage: backtest` in its YAML. None are frozen, none are promoted... They exist to be backtested and compared. Promotion from this bank to `paper` is a separate, deliberate decision per strategy — not a roadmap deliverable.

Tier 1 specifically targeted **the biggest family gaps**:

- **First mean-reversion alternative** to RSI(2) — the IBS template, structurally different oversold mechanic
- **First breakout exposure** — the Donchian template, third edge family in Phase 1's scope
- **First cross-sectional momentum exposure** — the xsec_rotation template, fills the "no relative momentum" gap
- **First dual-momentum benchmark** to the regime lifecycle-proof — the GEM template

Family count grew from 3 (`meanrev`, `regime`, `momentum.tsmom`) to 4 (`meanrev`, `regime`, `momentum`, `breakout`), and template count grew from 3 to 7.

---

## 2. Side-by-Side Backtest Comparison

All four backtests run on the same window (2020-01-01 → 2024-12-31), $1k initial equity, walk-forward with 4 OOS windows.

| Strategy | Trades | OOS Sharpe | Max DD | Total Return | Pos / Neg windows | Single-window dependency | Gate result |
|---|---:|---:|---:|---:|---|---|---|
| **A.** `meanrev.daily.ibs_lowclose.index_etfs.v1` | 32 | **0.32** | 1.22% | +0.78% | 2 / 1 (1 zero-trade) | no | refused (Sharpe<0.5) |
| **B.** `breakout.daily.donchian_20_10.sector_etfs.v1` | 458 | **0.20** | 6.87% | +2.34% | 2 / 2 | **YES** | refused (Sharpe<0.5 + dependency) |
| **C.** `momentum.daily.xsec_rotation.sector_etfs.v1` | 366 | **0.34** | 10.61% | +10.76% | **3 / 1** | no | refused (Sharpe<0.5) |
| **D.** `momentum.daily.dual_absolute.gem_weekly.v1` | 20 | **0.66** ✓ | 18.27% | **+23.45%** | 2 / 2 | no | refused (trades<30 + DD>15%) |

Promotion gate per [ADR 0020](../adr/0020-promotion-thresholds-are-code-invariants.md): `trades ≥ 30` AND `Sharpe > 0.5` AND `max_drawdown < 15%`.

### Read in one breath

**Every Tier 1 candidate refused the promotion gate.** Each refused for a *different* reason, and each refusal was honest — the harness is doing what Phase 1, 2, and 3 built it to do:

- **A** has a clean shape (low DD, no dependency) but Sharpe just under threshold.
- **B** has plenty of trades and the published right-tail truncation hit exactly as the family doc predicted; *aggregate return depends on a single window*.
- **C** is the best-shaped result by every measure other than Sharpe (3/4 positive, no dependency, +10.76% return), and the daily-swing edge degradation landed within the predicted ~30-50% range.
- **D** has the highest Sharpe and the highest absolute return — *and* the lowest trade count and highest drawdown. Refuses on two axes simultaneously.

---

## 3. What Each Refusal Means

### A. IBS — clean shape, sub-threshold edge

The strategy generates few signals (32 trades over 5 years on 4 ETFs) but those signals are well-behaved: max drawdown 1.22%, 2/4 windows positive, no single-window dependency. The thesis works directionally — close-near-low days on broad index ETFs do mean-revert — but not strongly enough on this universe and window to clear Sharpe 0.5.

**Honest follow-up question:** does IBS work better on a wider universe? The published research (Larsson/Lindahl, Connors) tests on broader baskets. Expanding the universe is a *new variant*, not tuning, per [ADR 0015](../adr/0015-strategy-identifier-and-frozen-manifest.md).

### B. Donchian — known truncation penalty bit hard

458 trades, 2.34% return, but **single-window dependency: dropping window #3 (+6.58%) flips the aggregate sign**. Stability is poor (Sharpe std 1.23, range -1.21..2.14). The 5-day max_hold cap truncates the right-tail trend continuation that classical breakout systems depend on. The family doc warned about this exact degradation; the backtest measured it.

**Honest follow-up question:** would a structurally-different breakout entry (NR7 contraction, ATR-Keltner) survive the truncation better? Both are Tier 2 candidates (§4.1, §4.4). Re-tuning Donchian's parameters (entry/exit channel lengths, ATR multiplier) is parameter search per VISION's "idea vs. tuning" rule, not a new idea — and would not address the structural cause.

### C. Cross-sectional sector momentum — best shape, predicted edge degradation

3/4 windows positive, no single-window dependency, +10.76% absolute return — the best-shaped Tier 1 result. The daily-swing fit caveat predicted ~30-50% edge survival from monthly→weekly; published xsec momentum Sharpes are typically 0.7-1.0; we measured 0.34, **squarely in the predicted range**.

**Honest follow-up question:** is the residual edge enough? Two paths:
- **Tier 1.D (this artifact):** dual-momentum stacks an absolute-return floor on top of the same xsec idea. If the floor materially helps, that's signal; backtest below.
- **A monthly-rebalance variant:** would violate Phase 1's 5-day max_hold cap. Out of scope.

### D. Dual-momentum GEM — clears Sharpe, fails on cadence and drawdown

The only Tier 1 strategy whose Sharpe (0.66) clears the 0.5 threshold. The +23.45% absolute return is the highest of the four. **But:** trade count 20 < 30 minimum (single-asset weekly rotation is naturally low-cadence) and max drawdown 18.27% > 15% threshold (the 2022 bond-equity dual-decline left both AGG and SPY drawing down with SHY as the only floor).

**Honest follow-up question:** is GEM a candidate lifecycle-proof replacement for `regime.daily.sma200_rotation.spy_shy.v1`? See §4 below.

---

## 4. Family-Gap Picture After Tier 1

Before Tier 1, the bank had three families covering three signal shapes: mean-reversion (RSI(2) on curated large-caps), single-asset trend regime (SMA200 SPY/SHY), time-series absolute momentum (TSMOM on curated large-caps).

After Tier 1:

| Signal shape | Coverage |
|---|---|
| Mean-reversion (multi-day oscillator) | `meanrev.daily.pullback_rsi2` (curated large-caps) — Phase 1 |
| Mean-reversion (single-bar location) | `meanrev.daily.ibs_lowclose` (broad index ETFs) — **Tier 1.A** |
| Trend-following channel breakout | `breakout.daily.donchian_20_10` (sector ETFs) — **Tier 1.B** |
| Time-series absolute momentum | `momentum.daily.tsmom` (curated large-caps) — Phase 3 |
| Cross-sectional rank momentum | `momentum.daily.xsec_rotation` (sector ETFs) — **Tier 1.C** |
| Single-asset SMA-trend rotation | `regime.daily.sma200_rotation` (SPY/SHY) — Phase 1 |
| Single-asset dual-momentum rotation | `momentum.daily.dual_absolute` (GEM quartet) — **Tier 1.D** |

The bank now spans the four edge families Phase 1 named (mean-reversion, momentum, breakout, regime) with a structural alternative inside three of them.

---

## 5. Open Questions for Promotion Decisions

Per the roadmap's [explicit stance](../superpowers/plans/2026-04-24-research-strategy-bank-roadmap.md), promotion is **per-strategy and out-of-scope for the roadmap itself**. The questions the operator owns:

### 5.1 Does anything from Tier 1 promote?

Strict reading of [ADR 0020](../adr/0020-promotion-thresholds-are-code-invariants.md) says no — every Tier 1 candidate refused the gate. The lifecycle-proof exemption ([SRS R-PRM-004](../SRS.md)) currently applies only to the `regime` family.

### 5.2 Should GEM get the lifecycle-proof exemption?

GEM (D) shares the structural shape that made `regime` exempt: low-cadence single-asset rotation where statistical promotion thresholds are categorically unreachable in any reasonable window. The roadmap explicitly raised this question (§3.4: "If the dual-momentum version materially outperforms, it becomes the candidate lifecycle-proof replacement"). The case for exempting GEM and treating it as a candidate replacement for the SMA200 regime template depends on:

- **Correlation of return streams** between GEM and the existing regime template. If they're highly correlated, GEM is just a more complex version of the same idea — drop one. If GEM systematically avoids drawdowns the SMA200 rule misses, GEM is a richer signal — promote one, retire the other.
- **2022 drawdown characterization.** GEM's 18.27% max DD comes from a specific historical regime (bond-equity dual-decline). Whether that's "fragile to a rare regime" or "structurally vulnerable" is the question.

Both are Phase 4 §4.1.a / §4.5 questions if the operator wants to open them — they cross the autonomy boundary (live promotion).

### 5.3 What does the next research wave look like?

Two natural directions, both Tier 2:

- **Push on breakout** — NR7 (§4.1) and ATR-Keltner (§4.4) are structurally-different entries that may survive the daily-swing truncation better than Donchian.
- **Push on cross-sectional momentum** — Bollinger (§4.2) provides A/B test on the meanrev side; the xsec idea is now well-evidenced and the question is whether fewer-positions (top-1 instead of top-2) or different lookbacks would clear the gate. *Per VISION's "idea vs. tuning" rule, lookback search is tuning, not idea generation* — only legitimate as Phase 4 §4.1.e disciplined re-tune with declared search space.

A third — turn-of-month seasonality (§4.3) — introduces the first **calendar-based** signal family, structurally distinct from every existing family.

---

## 6. What Tier 1 Did *Not* Do

- **No promotion.** All four ship at `stage: backtest`. Promotion to `paper` is a separate decision per strategy.
- **No re-tuning.** Each strategy uses the parameters from its research source. Parameter search would invalidate the "follows the published work" claim.
- **No correlation matrix yet.** The roadmap's Tier 1 exit criterion includes "side-by-side comparison table" (this artifact). The "correlation matrix between strategies' returns" is a Tier 2 exit criterion ([roadmap §4 exit criteria](../superpowers/plans/2026-04-24-research-strategy-bank-roadmap.md)) — it requires the analytics layer to read trade-level returns from each strategy's backtest run and compute pairwise correlations. Deferred to Tier 2 wrap-up.
- **No live promotion.** Every Phase 1+ guardrail still binds. Live trading remains structurally locked per [ADR 0004](../adr/0004-paper-only-phase-one.md), which Phase 4 may revisit per [ADR 0027](../adr/0027-phase-3-is-closed-and-phase-4-may-open.md) but not via this artifact.

---

## 7. Tier 1 Exit Criteria — Status

Per [roadmap §3 exit criteria](../superpowers/plans/2026-04-24-research-strategy-bank-roadmap.md):

- [x] Four strategies landed, each on its own feature branch (PRs #7, #8, #9, #10).
- [x] `docs/strategy-families.md` has `momentum` (Phase 3) and `breakout` (Tier 1.B) family sections, plus three new templates (`meanrev.daily.ibs_lowclose`, `momentum.daily.xsec_rotation`, `momentum.daily.dual_absolute`).
- [x] Three new universe manifests (`universe.index_etfs.v1`, `universe.sector_etfs_spdr.v1`, `universe.gem_quartet.v1`).
- [x] Backtest metrics recorded in each config; side-by-side comparison table here.

Tier 1 closed. Tier 2 is open at the operator's discretion.

---

## 8. Reproducing the Numbers

Each backtest result above can be reproduced from the strategy bank's recorded `backtest_results_v1_2026-05-05` block in the strategy config:

```bash
milodex backtest meanrev.daily.ibs_lowclose.index_etfs.v1 --start 2020-01-01 --end 2024-12-31 --initial-equity 1000 --walk-forward
milodex backtest breakout.daily.donchian_20_10.sector_etfs.v1 --start 2020-01-01 --end 2024-12-31 --initial-equity 1000 --walk-forward
milodex backtest momentum.daily.xsec_rotation.sector_etfs.v1 --start 2020-01-01 --end 2024-12-31 --initial-equity 1000 --walk-forward
milodex backtest momentum.daily.dual_absolute.gem_weekly.v1 --start 2020-01-01 --end 2024-12-31 --initial-equity 1000 --walk-forward
```

The numbers in §2 are deterministic on the same data window with the same slippage assumptions (per-strategy `backtest.slippage_pct`). Drift would indicate either a data-vendor change or a bug in the engine — both are signals worth investigating.
