# Research Strategy Bank — Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bank of backtested-only strategy candidates so future promotion decisions have real options. Twelve candidates, prioritized across three tiers, spanning the momentum / mean-reversion / breakout / regime gaps in the current two-strategy lineup.

**Explicit stance:** Every strategy landed by this roadmap ships with `stage: backtest` in its YAML. None are frozen, none are promoted, none run under `milodex strategy run`. They exist to be backtested and compared. Promotion from this bank to `paper` is a separate, deliberate decision per strategy — not a roadmap deliverable.

**Tech stack:** Python 3.11+, pytest, ruff, existing `milodex.strategies` module, existing backtest engine.

---

## 1. Onboarding — Required Reading Before Implementing Anything

A fresh agent should read these before touching code. They encode the invariants this roadmap assumes.

### Authoritative specs (normative)
- `docs/FOUNDER_INTENT.md` — the deeper "why" that drives tone, scope, and tradeoff calls.
- `docs/VISION.md` — Phase 1 scope and autonomy boundary.
- `docs/SRS.md` — key terms, especially `promotable strategy instance`, `lifecycle-proof strategy`, `research-target strategy`.
- `docs/strategy-families.md` — **normative** family definitions. This is where family-level rules live. YAML does NOT restate family invariants.
- `docs/adr/0015-strategy-identifier-and-frozen-manifest.md` — identifier scheme (`<family>.<tempo>.<template>.<universe_variant>.v<N>`), version-vs-variant rule.
- `docs/adr/0021-strategies-read-own-trade-ledger.md` — position provenance contract. Relevant because new strategy classes read `context.positions` and `context.entry_state`; those are ledger-scoped now.
- `docs/ENGINEERING_STANDARDS.md` — section on authoritative vs orchestration modules, highest-risk paths, mandatory tests.

### Existing code to study (descriptive)
- `src/milodex/strategies/base.py` — `Strategy` ABC, `StrategyContext`, `StrategyDecision`, `DecisionReasoning`, `StrategyParameterSpec`.
- `src/milodex/strategies/meanrev_rsi2_pullback.py` — canonical cross-sectional family implementation. Reference for any new mean-reversion or momentum variant.
- `src/milodex/strategies/regime_spy_shy_200dma.py` — canonical single-asset family implementation. Reference for strategies with one position at a time.
- `src/milodex/strategies/loader.py` — how family classes are registered, how configs are hashed.
- `src/milodex/strategies/runner.py` — how bars, positions, and entry_state are assembled before `evaluate()`. You do not need to modify this to add a new strategy; you need to understand what it passes in.
- `src/milodex/backtesting/` — the backtest engine. Read `engine.py` (or equivalent) to understand how `Strategy.evaluate()` is called during a backtest and how trades get recorded.
- `src/milodex/data/models.py` — `BarSet` and `Bar` shapes. Bars carry OHLCV; nothing else.

### Existing configs to study
- `configs/meanrev_daily_rsi2pullback_v1.yaml` — cross-sectional instance config template.
- `configs/spy_shy_200dma_v1.yaml` — single-asset instance config template.
- `configs/universe_phase1_v1.yaml` — frozen universe manifest format (SRS R-DAT-016). New strategies that need a different universe either reference this or declare a new universe manifest following the same schema.
- `configs/risk_defaults.yaml` — global risk guardrails. Applies above every strategy config.
- `configs/sample_strategy.yaml` — template showing every field a strategy config may carry.

### Phase 1 hard constraints (non-negotiable)
- US equities/ETFs only, Alpaca broker, under $1k capital.
- Daily bars only. `signal_evaluation: end_of_day`, `execution_timing: next_market_open`.
- Market orders only (ADR 0013).
- Long-only.
- Hold period ≤ 5 trading days.
- No external data calls beyond daily bars from the existing `DataProvider`.
- PDT rule: under $25k means no same-day round trips. Daily swing naturally avoids this; confirm no strategy violates it.

### Commands you'll need
```bash
pip install -e ".[dev]"                        # install, if fresh
pytest tests/milodex/strategies/ -q            # strategy tests
pytest tests/milodex/backtesting/ -q           # backtest tests
ruff check src/ tests/                          # lint
ruff format src/ tests/                         # format
milodex config validate configs/<new>.yaml     # config schema check
milodex backtest <strategy_id>                  # run the backtest (after landing)
milodex analytics trades --strategy <strategy_id>  # inspect backtest trades
```

---

## 2. Per-Strategy Implementation Pattern

Every strategy in every tier follows the same workflow. Do not deviate without reason. Each candidate below will reference this section.

### 2.1 Decide: variant of existing family, new version, or new family?
Rule of thumb, per ADR 0015 + `docs/strategy-families.md`:

- **New variant of existing family** (e.g. "sector ETFs" instead of "curated large-cap"): new YAML, no new code, no new family section. Cheapest.
- **New version of existing family** (e.g. a mean-rev using Bollinger bands instead of RSI, when the ranking_metric set doesn't cover it): new YAML + new `sizing_rule`/`ranking_metric` enum value added to the family doc + new code for the new rule. Medium.
- **New family** (e.g. seasonality, stat_arb): new section in `docs/strategy-families.md` FIRST, then new family class file, then config. Most expensive; only when the market behavior exploited is genuinely different.

For each candidate below, the roadmap states which of these three it is.

### 2.2 Steps (in order)

1. **[ ] Read the candidate's research source.** Don't implement from memory or from this roadmap's summary. The summary is a pointer, not the spec.
2. **[ ] If new family: add section to `docs/strategy-families.md`.** Follow the eight-part structure documented in that file's "How to Read This Document." Get the family invariants right before writing code — they constrain the code.
3. **[ ] If new universe: add a `universe_*_v1.yaml` manifest following the Phase 1 universe schema.** Reference it via `universe_ref` in the strategy config.
4. **[ ] Write the strategy class** in `src/milodex/strategies/<family>_<template>.py`. Inherit from `Strategy`. Declare `family`, `template`, `parameter_specs`. Implement `evaluate()` returning `StrategyDecision(intents=..., reasoning=DecisionReasoning(...))`. A zero-intent cycle still returns a `DecisionReasoning` with `rule="no_signal"` — never return an empty decision without reasoning. See `meanrev_rsi2_pullback.py` for the canonical shape.
5. **[ ] Register the family class in the loader** if it's a new family (existing family classes are already wired; new ones need to be added to the loader's family registry).
6. **[ ] Write the config YAML** at `configs/<family>_<template>_<variant>_v1.yaml`. Use dotted ID per ADR 0015. `stage: backtest`. Set `min_trades_required: 30` unless the candidate has documented reason for exemption (e.g. turn-of-month at 12 trades/year). Reference the universe manifest via `universe_ref`, don't inline symbols.
7. **[ ] Write unit tests** at `tests/milodex/strategies/test_<family>_<template>.py`. Mirror the structure of `test_meanrev_rsi2_pullback.py`. Cover: entry rule happy path, entry rule blocked by filter, exit rule (each exit condition as a separate test: signal exit, time stop, loss stop), ranking when N > capacity, no-signal cycle still emits `DecisionReasoning`. Minimum ~8 tests per strategy.
8. **[ ] Write one end-to-end backtest test.** Seed daily bars for a known window, run the backtest via the engine, assert at least one expected trade appears in the result set. This catches wiring bugs that unit tests miss.
9. **[ ] Validate the config:** `milodex config validate configs/<new>.yaml`.
10. **[ ] Run lint + full test suite:** `ruff check src/ tests/ && pytest -q`.
11. **[ ] Run the backtest:** `milodex backtest <strategy_id>`. Capture: trade count, Sharpe, max drawdown, win rate, PF. Add them to the strategy's config in a `backtest_results_v1_<date>:` block (non-normative; purely for human reference during comparison).
12. **[ ] Commit** on a feature branch named `feat/strategy-<family>-<template>-<variant>`. One strategy per branch. Don't batch multiple strategies into one PR.

### 2.3 Done criteria per strategy
- [ ] Strategy class exists and inherits from `Strategy`.
- [ ] Unit tests pass, covering entry + each exit condition + ranking + no-signal.
- [ ] Backtest smoke test passes.
- [ ] Config validates.
- [ ] Full suite (`pytest -q`) green; `ruff check` clean.
- [ ] Backtest runs end-to-end and produces ≥ `min_trades_required` trades over the test window (unless the candidate is documented as low-cadence like turn-of-month).
- [ ] Backtest metrics recorded in the config.
- [ ] One PR open per strategy, not merged until human review.

---

## 3. Tier 1 — Build First

Highest-evidence candidates, biggest family gaps filled. Start here. Every tier-1 candidate fits an existing family and needs no new family section — the architectural cost is purely per-strategy.

### 3.1 `meanrev.daily.ibs_lowclose.index_etfs.v1` — Internal Bar Strength
- **Family:** `meanrev` (existing). **New version, not variant** — requires adding `ibs_entry` / `ibs_exit` rule semantics. Update `docs/strategy-families.md` parameter surface for meanrev to include `ibs_entry_threshold`, `ibs_exit_rule`, or create a new `meanrev.daily.ibs_lowclose` template noted under the family.
- **Hypothesis:** Close near daily low on index ETFs signals short-term oversold; reverts within 1-3 days.
- **Entry:** `IBS = (Close - Low) / (High - Low) < 0.2` AND `Close > SMA(200)`. Enter at next open.
- **Exit:** `Close > prior day's High` → exit; 3-day max hold; 3% stop.
- **Universe:** New manifest `universe.index_etfs.v1` containing `SPY`, `QQQ`, `IWM`, `DIA`.
- **Evidence:** Larsson & Lindahl 2013 "Mining for Three Dollars a Day" (Quantpedia); Connors *Short Term Trading Strategies That Work* (2008). Native daily-swing.
- **Why now:** Structurally different from RSI(2) — uses intraday bar location, not a multi-day oscillator. Fills the "alternative mean-reversion" gap.
- **Implementation notes:** Small universe (4 symbols) keeps ranking trivial. IBS is a single-bar computation; no lookback accumulation beyond SMA(200) for trend filter.
- **Estimated effort:** 1-2 days.

### 3.2 `momentum.daily.xsec_rotation.sector_etfs.v1` — Cross-Sectional Sector Momentum
- **Family:** `momentum` (NEW). **Requires new family section in `docs/strategy-families.md`** — this is the first momentum family.
- **Hypothesis:** Jegadeesh-Titman cross-sectional momentum: top-ranked assets by trailing return outperform over the next few days-to-weeks.
- **Entry:** Rank 11 SPDR sector ETFs by trailing 63-day total return each Friday close. Hold top 2. Enter top-2 MOO next Monday. SPY > SMA(200) gate to avoid bear-market whipsaw.
- **Exit:** Any holding outside top-3 at weekly rank → exit at next open. Signal is weekly; the 5-day hold cap is effectively enforced by rebalance cadence.
- **Universe:** New manifest `universe.sector_etfs_spdr.v1` containing the 11 GICS SPDRs (XLF, XLK, XLE, XLI, XLV, XLY, XLP, XLU, XLB, XLRE, XLC).
- **Evidence:** Jegadeesh & Titman 1993 *JoF*; Asness/Moskowitz/Pedersen 2013 *JoF*; Faber 2010 SSRN "Relative Strength Strategies for Investing"; Antonacci 2014 *Dual Momentum Investing*.
- **Why now:** Milodex has zero momentum exposure today. This fills the biggest family gap. Momentum and mean-reversion are structurally opposite phases of the returns distribution — adding it decorrelates the bank.
- **Implementation notes:** Weekly rebalance cadence is new — existing strategies evaluate daily. Confirm the runner/backtest engine handles "skip evaluation except on Fridays" cleanly, or encode the weekly gate inside the strategy's `evaluate()` by returning no-signal on non-Friday closes. With $1k capital / 2 positions / ~$500 each, position sizing is fine.
- **Daily-swing fit caveat:** Original research is monthly rebalance with 1-12 month holds. Weekly rebalance is the tightest faithful daily-swing adaptation. Expect ~30-50% of the published edge to survive the hold compression.
- **Estimated effort:** 3-4 days (new family section + new evaluate cadence + new ranking logic).

### 3.3 `breakout.daily.donchian_20_55.sector_etfs.v1` — Donchian Channel Breakout
- **Family:** `breakout` (NEW). **Requires new family section in `docs/strategy-families.md`** — first breakout family.
- **Hypothesis:** Prices breaking to 20-day highs continue in trend direction (Turtle heritage).
- **Entry:** `Close > max(High, 20)` on prior bar AND `Close > SMA(100)`. Enter MOO next day.
- **Exit:** `Close < min(Low, 10)` → exit; 5-day hard time stop; `ATR(20) * 2` initial stop.
- **Universe:** Reuse `universe.sector_etfs_spdr.v1` from 3.2.
- **Evidence:** Faith 2007 *Way of the Turtle*; Clenow 2019 *Trading Evolved*; Szakmary et al. 2010 *Journal of Banking & Finance*.
- **Why now:** No trend-following exposure today. Breakout is the third edge family in Phase 1's scope.
- **Daily-swing fit caveat:** Classical breakout systems let winners run indefinitely. The 5-day hard stop truncates the fat right tail that makes the strategy profitable. Expect material PF degradation vs published results. This is a known tradeoff; measure it explicitly.
- **Estimated effort:** 3-4 days (new family section + ATR / channel computations + rolling-window state handling in evaluate).

### 3.4 `momentum.daily.dual_absolute.gem_weekly.v1` — Dual Momentum (Weekly GEM)
- **Family:** `momentum` (depends on 3.2 landing first).
- **Hypothesis:** Combining cross-sectional rank with absolute-return trend filter avoids momentum crashes (Antonacci 2014).
- **Entry:** Each Friday close, compute trailing 126-day return for SPY, EFA, AGG. Hold single top-ranked IF its return > SHY's return; else hold SHY.
- **Exit:** Weekly re-evaluation; switch on rank change.
- **Universe:** New manifest `universe.gem_quartet.v1` containing SPY, EFA, AGG, SHY.
- **Evidence:** Antonacci 2014 *Dual Momentum Investing*.
- **Why now:** Natural benchmark for the existing `regime.daily.sma200_rotation.spy_shy.v1`. If the dual-momentum version materially outperforms, it becomes the candidate lifecycle-proof replacement. If correlated, drop.
- **Daily-swing fit caveat:** Original GEM is monthly rebalance. Weekly adaptation degrades the edge modestly (less than breakout truncation).
- **Estimated effort:** 2 days (infrastructure from 3.2 is reusable; this is mostly a config + variant class).

### Tier 1 exit criteria
- [ ] Four strategies landed, each on its own feature branch.
- [ ] `docs/strategy-families.md` has `momentum` and `breakout` family sections.
- [ ] Three new universe manifests (`universe.index_etfs.v1`, `universe.sector_etfs_spdr.v1`, `universe.gem_quartet.v1`).
- [ ] Backtest metrics recorded in each config; a side-by-side comparison table added to this roadmap or a new `docs/reviews/strategy-bank-tier1-results.md`.

---

## 4. Tier 2 — Build After Tier 1 Is Validated

Moderate evidence, filling remaining gaps or providing A/B benchmarks. Do not start tier 2 until tier 1 has landed AND its backtest results have been reviewed — if tier 1 reveals a problem in the backtest engine or family structure, fix it before paying the cost again on tier 2.

### 4.1 `breakout.daily.nr7_inside.liquid_largecap.v1` — NR7 Volatility Contraction
- **Family:** `breakout` (uses the section written in 3.3).
- **Hypothesis:** 7-day narrowest range precedes directional breakouts (volatility contraction → expansion).
- **Entry:** Today's range = `min(last 7 days' ranges)` AND `Close > Open` AND `Close > SMA(50)`. Enter MOO next day; stop at today's Low.
- **Exit:** 3-day time stop; trail at prior day's Low once in profit.
- **Universe:** S&P 100 top-100 by dollar volume. New manifest `universe.sp100_liquid.v1` required.
- **Evidence:** Crabel 1990 *Day Trading with Short Term Price Patterns*; Connors & Raschke 1995 *Street Smarts*. **Practitioner-tier evidence, not peer-reviewed. Watch for data mining.**
- **Daily-swing fit:** Native.

### 4.2 `meanrev.daily.bbands_lowerband.curated_largecap.v1` — Lower Bollinger Touch
- **Family:** `meanrev` (existing).
- **Hypothesis:** In uptrends, lower BB touches mark oversold dips reverting to the middle band.
- **Entry:** `Close < LowerBB(20, 2)` AND `Close > SMA(200)`.
- **Exit:** `Close > MiddleBB(20)` → exit; 5-day time stop; 6% stop.
- **Universe:** Reuse `universe.phase1.curated.v1` (same as existing RSI(2) strategy — this is the A/B test).
- **Evidence:** Bollinger 2001 *Bollinger on Bollinger Bands*; Lento et al. 2007. Mixed academic results.
- **Why now:** Likely highly correlated with RSI(2). Backtest is a direct A/B: if Bollinger beats RSI(2) on the same universe, upgrade. If correlation > 0.9, drop — no point carrying two strategies that trade the same signal.

### 4.3 `seasonality.daily.turn_of_month.spy.v1` — Turn-of-Month
- **Family:** `seasonality` (NEW). **Requires new family section.** Calendar-based entry timing is categorically different from the price-driven families; it warrants its own section per the "Adding a New Family" rule in `docs/strategy-families.md`.
- **Hypothesis:** Equity returns cluster around month-end (Ariel 1987; Lakonishok & Smidt 1988; McConnell & Xu 2008).
- **Entry:** Buy SPY at MOO on the last trading day of each month (T-1 vs month end).
- **Exit:** Sell at MOC on the 3rd trading day of the new month (T+3). Fixed 4-5 day hold, no other exits.
- **Universe:** SPY only. `universe.spy_only.v1`.
- **Evidence:** Strong academic pedigree, persistent out-of-sample through 2005+.
- **Phase 1 fit:** ~12 trades/year. Will never reach 30 trades in a single year. Exemption pattern: same as `regime` family — operational gates apply instead of statistical. Document this exemption in the new family section AND in the config. **Low cadence is a feature here, not a bug** — it means the strategy can be promoted with a `lifecycle-proof-style` exemption if backtest evidence holds.

### 4.4 `breakout.daily.atr_channel.sector_etfs.v1` — ATR Keltner Channel Breakout
- **Family:** `breakout` (existing after 3.3).
- **Hypothesis:** ATR-width channel breakouts filter noise in choppy regimes better than fixed N-day highs.
- **Entry:** `Close > EMA(20) + 2 * ATR(20)` AND SPY > SMA(100).
- **Exit:** `Close < EMA(20)` → exit; 5-day time stop; `ATR * 1.5` stop.
- **Universe:** Reuse `universe.sector_etfs_spdr.v1`.
- **Evidence:** Keltner 1960; Linda Raschke variants; Clenow 2013 backtests on futures.
- **Daily-swing fit caveat:** Same as Donchian — truncation penalty on the right tail.

### Tier 2 exit criteria
- [ ] Four strategies landed, each on its own feature branch.
- [ ] `seasonality` family section added to `docs/strategy-families.md`, including the documented low-cadence exemption.
- [ ] Two new universe manifests (`universe.sp100_liquid.v1`, `universe.spy_only.v1`).
- [ ] Backtest comparison table extended: tier 1 + tier 2 side-by-side. Correlation matrix between strategies' returns included.

---

## 5. Tier 3 — Build Only If Tier 1/2 Results Justify

Speculative candidates with either evidence weakness, data-dependency blockers, or fit strain. Do not build these by default. Build them if tier 1/2 results show empty spots in the return-stream diversification matrix that these candidates would fill.

### 5.1 `meanrev.daily.gap_down_reversal.sp100.v1` — **BLOCKED**
- **Blocker:** Requires earnings calendar data to filter out news-driven gaps. Phase 1 data layer does not provide earnings data. Adding an earnings data source is out of scope without a dedicated sub-project.
- **Path to unblock:** Add earnings calendar integration to `src/milodex/data/`, with its own ADR. Only then revisit this candidate.

### 5.2 `momentum.daily.52w_high_proximity.largecap.v1` — 52-Week High Proximity
- **Family:** `momentum` (existing after 3.2).
- **Hypothesis:** Stocks near 52-week highs continue up; anchoring-bias underreaction (George & Hwang 2004).
- **Entry:** `Close / max(High, 252) > 0.97` AND `Close > prior Close`.
- **Exit:** 5-day time stop; `Close < SMA(20)` → exit; 5% stop.
- **Universe:** Top 100 by dollar volume. Reuse `universe.sp100_liquid.v1` from 4.1.
- **Evidence:** George & Hwang 2004 *JoF*; Liu et al. 2011.
- **Daily-swing fit caveat:** Aggressively adapted from monthly holds. Many names may fire simultaneously → ranking critical.

### 5.3 `seasonality.daily.pre_fomc_drift.spy.v1` — Pre-FOMC Drift — **DATA-BLOCKED**
- **Family:** `seasonality` (existing after 4.3).
- **Blocker:** Requires FOMC calendar (Fed announcement schedule). Not in Phase 1 data layer.
- **Path to unblock:** Small static data file could suffice — FOMC schedule is published 1-2 years ahead and changes rarely. A hand-maintained `data/calendars/fomc_2024_2027.yaml` is a plausible minimum. Still requires an ADR for the calendar-data pattern.
- **Evidence:** Lucca & Moench 2015 *JoF*. Strong, with post-publication decay.
- **Trade cadence:** 8 trades/year — same lifecycle-proof exemption pattern as turn-of-month.

### 5.4 `stat_arb.daily.zscore_residual.etf_pairs.v1` — Long-Only Pairs — **ARCHITECTURAL STRAIN**
- **Family:** `stat_arb` (NEW). Would require new family section.
- **Strain:** Classical pairs trading is long/short. Phase 1 is long-only. Long-only pairs is a crippled subset of the strategy — the published edge may not survive the constraint.
- **Path:** Backtest the long-only variant first before committing to the family section. If the backtest doesn't show a clear edge (Sharpe > 0.5, trade count > 30, low correlation to existing strategies), abandon — the architectural cost isn't justified.
- **Evidence:** Gatev/Goetzmann/Rouwenhorst 2006 *RFS*; Chan 2013. Pedigreed but alpha has decayed.

### Tier 3 exit criteria
- [ ] Each candidate either landed OR has a documented reason for skipping (blocker ADR, failed smoke backtest, superseded).
- [ ] Any new family section (`stat_arb`) only added if the corresponding strategy actually lands.
- [ ] Final bank comparison: correlation matrix across all landed strategies, ranked by Sharpe.

---

## 6. Architectural Prerequisites — Before Each Tier

Some candidates require architectural work before implementation. Front-load these — doing them mid-strategy-build is disruptive.

### Before Tier 1
- `momentum` family section in `docs/strategy-families.md` (for 3.2, 3.4).
- `breakout` family section in `docs/strategy-families.md` (for 3.3).
- Confirm backtest engine handles weekly-rebalance cadence (needed for 3.2, 3.4). If not, the strategy handles the gate internally by returning `no_signal` on non-Friday bars — fine but document the pattern.
- Confirm `DecisionReasoning.rule` identifiers for the new families. Suggested: `momentum.xsec_entry`, `momentum.xsec_exit`, `breakout.channel_entry`, `breakout.channel_exit`. Add them to the `base.py` docstring's enumerated rule list.

### Before Tier 2
- `seasonality` family section in `docs/strategy-families.md` (for 4.3) including the low-cadence statistical exemption pattern.

### Before Tier 3
- Only if pursuing 5.1 or 5.3: ADR + implementation for the corresponding calendar / earnings data source.
- Only if pursuing 5.4: `stat_arb` family section — but **gate this on the backtest showing an edge first**, don't commit the architecture before the evidence.

---

## 7. Commit + PR Strategy

- One strategy per branch, one PR per branch. Do not batch.
- Branch naming: `feat/strategy-<family>-<template>-<variant>`.
- PR title: `feat(strategies): <family> <template> <variant> v1 — <one-sentence hypothesis>`.
- PR body must include:
  - Candidate's research source (book / paper / URL).
  - Backtest metrics: trade count, Sharpe, max drawdown, win rate, PF.
  - Correlation to each already-landed strategy (if any).
  - Explicit "adapted vs native daily-swing" note.
  - `stage: backtest` explicitly confirmed — no promotion work in this PR.
- Do not merge without human review. Every strategy changes what Milodex believes about market behavior; the founder reviews each.

---

## 8. Done Criteria for the Whole Roadmap

- [ ] Tier 1: 4 strategies landed, 2 new family sections (`momentum`, `breakout`), 3 universe manifests, comparison table.
- [ ] Tier 2: 4 strategies landed (or explicitly skipped with documented reason), 1 new family section (`seasonality`), correlation matrix extended.
- [ ] Tier 3: each candidate either landed or explicitly skipped with blocker documented.
- [ ] `docs/strategy-families.md` is up to date with every family added.
- [ ] A final review artifact at `docs/reviews/strategy-bank-final-comparison.md` with: full strategy list, metrics, correlation matrix, recommended promotion candidates (ranked), recommended retirements (if any landed strategy is dominated by another and adds no diversification).
- [ ] Nothing on this roadmap is promoted past `stage: backtest`. Promotion is a separate decision the founder makes per strategy, using this bank as the menu.

---

## 9. What This Roadmap Does NOT Cover

- Promotion of any strategy to `paper`. That's a per-strategy manifest-freeze decision, made deliberately, after review of the backtest evidence here.
- Integration testing of multiple strategies running concurrently — Phase 1 is one-strategy-at-a-time (per VISION.md). The bank exists for selection, not parallel execution.
- Walk-forward parameter optimization. Configs here use the published parameters from each research source. Parameter search is a separate research project and would invalidate the "follows the published work" claim for each candidate.
- Live or micro_live stage. Phase 1 stops at `paper` for any strategy. No candidate in this bank goes further.
