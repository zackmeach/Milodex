# Intraday Strategy Expansion After-Action Report

**Run:** overnight autonomous, 2026-05-28 → 2026-05-29
**Branch:** `overnight/intraday-strategy-expansion` (off `phase-state-cleanup-2026-05-28` HEAD)
**Scope:** backtest-only intraday/daytime strategy candidates. No paper/live changes, no promotions.

> **Status: COMPLETE.** All three candidates implemented + tested + walk-forward
> backtested (2022–2025) against a fresh same-window benchmark. The Backtest
> Results Summary Table is fully populated; bottom line in the Executive Summary
> and Final Recommendation. Committed (not pushed) — see Branch / Diff Summary.

---

## Executive Summary

**What was attempted.** Evaluate the 10 candidate intraday/daytime strategy
ideas against the existing Milodex architecture, select the best 3–5 that fit
cleanly, and implement them to backtest-evidence stage with tests — on a new
isolated branch, backtest-only, no promotions.

**What was implemented (3 clean candidates, all SPY-only, all long-only,
all `stage: backtest`):**

| # | Strategy ID | Signal family | One-line thesis |
|---|---|---|---|
| A | `meanrev.vwap_reversion.intraday.spy.v1` | mean-reversion | Buy when price stretches below session VWAP; exit on reversion to VWAP / stop / time-stop. |
| B | `momentum.vwap_trend.intraday.spy.v1` | trend-continuation | Buy when price holds above session VWAP with momentum + volume; exit on VWAP break / stop / time-stop. |
| C | `meanrev.rsi2.intraday.spy.v1` | oscillator mean-reversion | Buy when session RSI(2) is oversold; exit on RSI reversion / stop / time-stop. |

Each ships with: a strategy class, a YAML config (`stage: backtest`), registry
registration, a dedicated unit-test file, and a walk-forward backtest over the
canonical intraday window (2022-01-01 → 2025-12-31) measured head-to-head
against the unconditional-intraday-long benchmark on identical 5 bps friction.
One genuinely new, reused shared helper was added: cumulative **session VWAP**
(`session_vwap` / `session_vwap_series` / `regular_session_bars` in
`_session_intraday.py`), used by A and B.

**What was skipped / deferred.** Candidates 3, 4, 5, 6, 7, 8 were ranked and
deferred (clean future tasks; none needed tonight to hit 3 clean candidates).
Candidate 9 (ETF pair-spread) was **skipped — data + architecture blocked**: it
needs two-leg handling the single-primary long-round-trip engine does not model,
**and** 5-minute data for a second symbol that the cache does not hold.

**Outcome (walk-forward 2022–2025, 5 bps, 4 OOS windows).** All three are
**clean negative-Sharpe nulls** — none clears the paper Sharpe floor (> 0), none
beats the unconditional-intraday-long benchmark, **none should be promoted.**
Least-bad is A (`vwap_reversion`, Sharpe −1.89); worst is C (`rsi2`, Sharpe −7.96,
friction-dominated). This matches the sober prior (existing ORB −1.06, benchmark
−1.69). The durable value delivered is the **reusable session-VWAP helper + three
tested, benchmark-compared strategy scaffolds + honest null evidence**, not alpha.
See the Backtest Results table and Final Recommendation.

---

## Repo / Architecture Findings

### Existing intraday support found (sufficient for SPY-only candidates)

- **Strategy contract** (`src/milodex/strategies/base.py`): `Strategy.evaluate(bars, context) -> StrategyDecision`. Intraday strategies read `context.bars_by_symbol[primary]`, inspect the latest bar, and emit a `TradeIntent` list + `DecisionReasoning`. `context.equity` (mark-to-market), `context.positions` (`{sym: qty}`), and `context.entry_state` (`{sym: {"entry_price", "held_days"}}`) are all populated by the engine each cycle.
- **Intraday engine** (`backtesting/engine.py::_simulate_intraday` ~line 1067, helpers in `backtesting/intraday_simulation.py`): per-day event loop with **advance → evaluate → drain** ordering and **T+1 next-bar-open fills guaranteed by construction** (engine.py:1167-1237). `_build_visible_bars` hands the strategy `df.iloc[:cursor]` — the full session-to-date history up to the current decision bar, so there is **no look-ahead** and session-scoped indicators are well-defined. Equity at evaluation = cash + mark-to-market of open positions (`_compute_equity`).
- **Existing intraday session helpers** (`strategies/_session_intraday.py`): `session_date_et`, `opening_range_bars`, `entry_window_bars`, `in_entry_window`, `is_entry_signal_bar`, `is_time_stop_bar` (half-day aware), `is_half_day`, `session_bars_et`, `to_eastern`. Half-day calendar is a hardcoded frozenset for 2022-2025.
- **Existing intraday strategies** to mirror: `breakout_orb_intraday.py` (ORB) and `bench_unconditional_intraday_long.py` (the benchmark). Both long-only single-name SPY round-trips with structural + time-stop exits — the exact pattern the new candidates follow.
- **Sizing**: `execution/sizing.shares_for_notional_pct(equity, notional_pct, unit_price)` (floors to 0 if equity too small).
- **Loader/registry** (`strategies/loader.py`): `build_default_registry()` imports + registers each class; config validated against a fixed schema (`strategy.{id,family,template,variant,version,description,enabled,parameters,tempo,risk,stage,backtest,disable_conditions_additional}`); `strategy.id` must equal `family.template.variant.vN`. Valid stages: `idle, backtest, paper, micro_live, live`. Valid bar sizes include `5Min`.
- **Backtest CLI**: `python -m milodex.cli.main backtest <strategy_id> --start YYYY-MM-DD --end YYYY-MM-DD [--walk-forward] [--slippage] [--initial-equity] [--risk-policy {bypass,enforce}] [--json]`. Resolves the strategy by id (scans `configs/`). Runs **fully offline** from the parquet cache (`risk_policy` defaults to `bypass`, `initial_equity` 100000, warmup auto-prepended ≈365 calendar days). Metrics land in the event store `data/milodex.db` (`backtest_runs.metadata_json`, `json_extract '$.oos_aggregate.{sharpe,max_drawdown_pct,trade_count}'`) and are echoed in the `--json` output; inspect with `analytics metrics <run_id>`.

### Gap added (small, well-contained, reused)

- No cumulative **session-VWAP** helper existed. Added `regular_session_bars`, `session_vwap_series`, `session_vwap`, and `session_close_offset_minutes` to `_session_intraday.py` (textbook VWAP: `typical = (H+L+C)/3`, volume-weighted, cumulative over regular-session bars; pre/post-market excluded; `None`/`NaN` on zero volume — deterministic and unit-tested). Reused by candidates A and B. No new framework, no new modules.

### Data constraints found (THE central constraint)

- **5-minute cache is SPY-only**: `market_cache/v3/5Min/` holds **`SPY.parquet` only** (118,256 bars, **2020-07-27 → 2026-05-28**, columns incl. a per-bar `vwap`). QQQ, IWM, and sector ETFs exist **only at 1Day** (`market_cache/1Day/`). 
- Fetching 5-minute data for a new symbol uses `milodex data fetch-universe` / `data bars`, which require **Alpaca credentials + network** — out of scope per the hard safety constraints and the operator's data-readiness directive. **Therefore every active config is SPY-only.** Multi-symbol / pair candidates are deferred with this documented blocker (lightweight data-readiness check performed: no QQQ/IWM 5Min parquet present).

### Governance / stage-source-of-truth observations (documented, not guessed)

- **Source of truth for *stage* is the event store `promotions` table, not the YAML `stage:` field** (`docs/STRATEGY_BANK.md`: "The promotion records are the binding source for stage"). The new candidates have **no promotion event**, so they are genuinely `backtest`-stage; their YAML `stage: backtest` is consistent with that. This satisfies the "must remain backtest/idle" constraint by construction — placing them at paper would require an explicit `milodex promotion promote` event, which was **not** performed.
- **Known governance footgun (observed, ADR 0032 precedent):** editing a YAML `stage:` field without a corresponding `promotion` event creates a stage divergence (the `pullback_rsi2` case). The candidates avoid this — their stage is `backtest` in YAML *and* in the (absent) event-store record.
- **Promotion thresholds** (`promotion/policy.py`, ADR 0052) — authoritative, restated here only for the morning checklist:
  - **Paper-readiness (permissive):** Sharpe > 0.0, max drawdown < 25%, trade count ≥ 30.
  - **Capital-readiness (strict, post-paper):** Sharpe > 0.5, max drawdown < 15%, trade count ≥ 30.
  - `--lifecycle-exempt` bypasses the statistical gate entirely (operator override).
- **Intraday-specific promotion rubric** (`docs/STRATEGY_BANK.md`, operator-enforced — not yet wired into `policy.py`): an intraday signal is a paper-merit candidate only if **Sharpe(candidate) > Sharpe(benchmark) AND Sharpe ≥ 0.3**, on the same universe + friction. The two existing intraday strategies at paper (`breakout.orb.intraday.spy.v1` Sharpe −1.06; `benchmark.unconditional_intraday_long.spy.v1` Sharpe −1.69) are **deliberate harness-validation canaries**, not signal-merit promotions.

---

## Candidate Selection Matrix

Fit = architecture/data fit for an *overnight* clean implementation (1–10).
"Data" notes the 5Min-cache constraint. All implemented candidates are SPY-only,
long-only, `stage: backtest`.

| # | Candidate | Fit | Impl. complexity | Expected BT quality | Status | Reason |
|---|---|---|---|---|---|---|
| 1 | VWAP mean-reversion (SPY/QQQ/IWM) | 9 | Low | High | **Implemented (A)** | Clean fit; reuses session helpers + new VWAP helper; long-only fades below-VWAP. SPY-only (data). |
| 2 | VWAP trend-continuation | 9 | Low | High | **Implemented (B)** | Mirror of A; shares the VWAP helper; long-only rides above-VWAP. Direct benchmark question. |
| 10 | Intraday RSI/IBS mean-reversion | 9 | Low | High | **Implemented (C)** | Reuses session helpers + self-contained Wilder RSI (mirrors daily sibling). Distinct oscillator family. |
| 3 | Opening-range continuation v2 | 7 | Medium | Medium | Deferred | ORB v1 already exists (known-null paper canary). v2 = adding regime/vol/volume filters to a heavily-competed-away base; high parameter-fishing risk. Future task; reuses ORB + VWAP/vol helpers. |
| 4 | Failed ORB / opening-range fade | 7 | Medium | Medium | Deferred | Cleanest 4th candidate — reuses ORB range helpers + VWAP target. Long-only restricts to *failed-downside-breakout* fades. Skipped only to keep the night to 3 clean candidates. |
| 5 | Intraday vol-scaled momentum | 6 | Medium | Medium | Deferred | Overlaps candidate B (momentum + filter). Would justify a realized-volatility helper not otherwise needed tonight. |
| 6 | Prior-day high/low breakout/rejection | 6 | Medium | Medium | Deferred | Needs a prior-session-levels helper. Long-only = prior-high breakout or prior-low bounce. Clean future task. |
| 7 | Gap fade / gap continuation | 6 | Medium | Medium | Deferred | Long-only collapses to *gap-down fade only* (narrower). Needs prior-close + early-session confirmation logic. |
| 8 | Close momentum / late-day | 6 | Low–Med | Low–Med | Deferred | Thin signal, strict late-day window; forced-exit-before-close interacts awkwardly with a late entry. |
| 9 | ETF pair-spread mean-reversion | 2 | High | N/A | **Skipped (blocked)** | **Architecture:** engine models a single-primary long round-trip, not a two-leg market-neutral spread. **Data:** needs 5Min bars for a second symbol; only SPY is cached. Both blockers documented; would require real architecture work + a data fetch (credentials). |

**Chosen 3:** #1, #2, #10 — the cleanest SPY-only, long-only fits; maximal helper
reuse (1 & 2 share the new VWAP helper; all three reuse the session-time
helpers); and three *distinct* signal families (reversion / trend / oscillator)
so the evidence is not three views of one idea.

---

## Implemented Candidates

Shared properties (all three): single-name **SPY**, **5Min** bars, **long-only**,
**one round trip per session** (enforced by a prior-in-window-signal guard
mirroring ORB), **forced exit** at `exit_minutes_before_close` (half-day aware),
**half-day sessions skipped**, **per-position 10% notional**, **5 bps slippage /
$0 commission** (identical to ORB + benchmark for fair comparison), **`stage:
backtest`**. Data requirement: the cached SPY 5Min parquet only — **no
credentials, no network**. Architecture dependencies: `Strategy` base contract,
`_session_intraday` helpers, `shares_for_notional_pct`, the intraday engine's
T+1 fill + `entry_state` population.

### A — `meanrev.vwap_reversion.intraday.spy.v1`

- **Files:** `src/milodex/strategies/meanrev_vwap_reversion_intraday.py`, `configs/meanrev_vwap_reversion_intraday_spy_v1.yaml`, registry in `strategies/loader.py`, tests `tests/milodex/strategies/test_meanrev_vwap_reversion_intraday.py`.
- **Thesis:** intraday SPY reverts toward cumulative session VWAP after stretching below it.
- **Entry:** flat, in entry window `[10:00, 15:00) ET`, and `(VWAP − close)/VWAP ≥ entry_deviation_pct` (0.4%). One entry/session.
- **Exit (priority):** (1) close ≤ `entry_price × (1 − stop_loss_pct)` (0.5%); (2) close ≥ session VWAP (reversion target); (3) time-stop 5 min before close.
- **Risk controls:** max 1 position, 10% notional, structural stop + VWAP target + forced time-stop, daily_loss_cap 2%.
- **Config params:** `opening_range_minutes 30, entry_window_minutes 300, entry_deviation_pct 0.004, stop_loss_pct 0.005, exit_minutes_before_close 5, per_position_notional_pct 0.10`. Sane defaults, **not tuned to pass**.
- **Tests added:** 9 (entry on stretch, insufficient-deviation no-entry, outside-window, VWAP-target exit, stop-loss exit, time-stop exit, one-entry-per-session, half-day skip, zero-volume → no signal). All pass.
- **Backtest command:** `python -m milodex.cli.main backtest meanrev.vwap_reversion.intraday.spy.v1 --start 2022-01-01 --end 2025-12-31 --walk-forward`
- **Backtest results (run `f0eb3b25`):** OOS Sharpe **−1.89**, MaxDD **2.69%**, **484 trades**, total return **−2.31%**. Per-window Sharpe **[−3.53, −1.96, −1.76, −0.40]** — **0 / 4 positive windows**.
- **Benchmark comparison:** loses — Sharpe **−1.89 < benchmark −1.27** (same window/friction, run `a1e36196`). The filter subtracts risk-adjusted value vs unconditional intraday long.
- **Paper-readiness: ❌ FAILS.** Trade count clears (484 ≥ 30) and drawdown is low (2.69% < 25%), but Sharpe −1.89 is far below the > 0.0 paper floor and the ≥ 0.3 intraday rubric. **Reject / defer on signal merit** (clean null — the deviation-reversion signal is anti-edge intraday on SPY after 5 bps).
- **Risks/caveats:** 2-month smoke (2024-01..03) fired only **3 round trips** — a 0.4% below-VWAP stretch is demanding in calm tape; trade count over the full window must be checked against the 30-floor. Long-only by engine constraint; cannot fade above-VWAP. Stop fills at next-bar open (engine convention), slightly optimistic vs intrabar stop-through.

### B — `momentum.vwap_trend.intraday.spy.v1`

- **Files:** `src/milodex/strategies/momentum_vwap_trend_intraday.py`, `configs/momentum_vwap_trend_intraday_spy_v1.yaml`, registry, tests `tests/milodex/strategies/test_momentum_vwap_trend_intraday.py`.
- **Thesis:** intraday SPY uptrends (price holding above session VWAP with momentum + volume) tend to persist into the session.
- **Entry:** flat, in window `[10:00, 12:00) ET`, AND `(close − VWAP)/VWAP ≥ min_above_vwap_pct` (0.1%) AND positive `momentum_lookback_bars` (6-bar) momentum AND latest volume > `volume_factor` (1.2×) the prior 6-bar mean. One entry/session.
- **Exit (priority):** (1) stop_loss 0.5% from entry; (2) close < session VWAP (trend invalidation); (3) time-stop.
- **Config params:** `opening_range_minutes 30, entry_window_minutes 120, min_above_vwap_pct 0.001, momentum_lookback_bars 6, volume_factor 1.2, stop_loss_pct 0.005, exit_minutes_before_close 5, per_position_notional_pct 0.10`.
- **Tests added:** 10 (entry on above-VWAP+momentum+volume, three distinct no-entry reasons, stop-loss / invalidation / time-stop exits, one-entry-per-session, half-day skip, zero-volume → no signal). All pass.
- **Backtest command:** `python -m milodex.cli.main backtest momentum.vwap_trend.intraday.spy.v1 --start 2022-01-01 --end 2025-12-31 --walk-forward`
- **Backtest results (run `c34868c5`):** OOS Sharpe **−1.79**, MaxDD **1.49%**, **274 trades**, total return **−1.22%**. Per-window Sharpe **[−0.17, −2.32, −2.51, −3.30]** — **0 / 4 positive windows**.
- **Benchmark comparison / paper-readiness: ❌ FAILS.** 274 trades ≥ 30 and drawdown tiny (1.49%), but Sharpe −1.79 is far below the > 0.0 floor. Confirms the structural worry: a long-only above-VWAP filter does not beat — it underperforms — unconditional intraday long after 5 bps. **Reject / defer on signal merit.**
- **Risks/caveats:** A long-only above-VWAP trend filter is, structurally, *a filtered version of the unconditional-long benchmark* — its entire value is whether the filter beats unconditional long after slippage. 2-month smoke fired 8 round trips (ample frequency). The one-entry-per-session guard uses only the above-VWAP condition (momentum/volume are transient) — documented simplification.

### C — `meanrev.rsi2.intraday.spy.v1`

- **Files:** `src/milodex/strategies/meanrev_rsi2_intraday.py`, `configs/meanrev_rsi2_intraday_spy_v1.yaml`, registry, tests `tests/milodex/strategies/test_meanrev_rsi2_intraday.py`.
- **Thesis:** intraday SPY snaps back after a short-period RSI prints oversold (intraday sibling of `meanrev.daily.pullback_rsi2`).
- **Entry:** flat, in window `[10:00, 15:00) ET`, session Wilder RSI(2) ≤ `rsi_entry_threshold` (10). RSI computed over the *current session's* regular-session closes (resets each session). One entry/session.
- **Exit (priority):** (1) stop_loss 0.5% from entry; (2) session RSI ≥ `rsi_exit_threshold` (60); (3) time-stop.
- **Config params:** `opening_range_minutes 30, entry_window_minutes 300, rsi_lookback 2, rsi_entry_threshold 10, rsi_exit_threshold 60, stop_loss_pct 0.005, exit_minutes_before_close 5, per_position_notional_pct 0.10`.
- **Tests added:** 10 (RSI hand-computation verification, oversold entry, not-oversold no-entry, outside-window, stop-loss / RSI-reversion / time-stop exits, one-entry-per-session, half-day skip, RSI-undefined → no signal). All pass.
- **Backtest command:** `python -m milodex.cli.main backtest meanrev.rsi2.intraday.spy.v1 --start 2022-01-01 --end 2025-12-31 --walk-forward`
- **Backtest results (run `0312e734`):** OOS Sharpe **−7.96**, MaxDD **6.54%**, **1542 trades**, total return **−6.52%**. Per-window Sharpe **[−8.21, −10.17, −7.44, −7.42]** — **0 / 4 positive windows**.
- **Benchmark comparison / paper-readiness: ❌ FAILS — worst of the three.** The signal fires almost every session (~771 round trips); at 5 bps × 2 legs the cumulative friction plus buying intraday dips with no regime filter produces a catastrophic −7.96 Sharpe. This is the textbook over-trading-into-friction null. **Reject on signal merit.** (A multi-day trend filter and/or far-lower trade frequency would be the only paths worth re-testing — a fresh design, not a tweak.)
- **Risks/caveats:** No multi-day trend filter (the daily sibling has a 200-SMA filter) — intraday RSI(2) buys dips without regime context, so 2022-bear sessions can stop out repeatedly; downside capped by stop + time-stop. RSI(2) is jumpy; the `_wilder_rsi_series` matches the daily strategy's recursive-Wilder algorithm exactly (verified by unit test).

---

## Skipped / Deferred Candidates

| # | Candidate | Why skipped now | Architecture/work needed | Worth a future task? |
|---|---|---|---|---|
| 9 | ETF pair-spread | **Blocked.** | Two-leg market-neutral position handling (engine is single-primary long round-trip); plus 5Min data for a 2nd symbol (only SPY cached → Alpaca fetch w/ credentials). | Only if a market-neutral execution model + multi-symbol intraday data are deliberately funded. Significant. |
| 3 | ORB continuation v2 | Base signal is a known null at paper; v2 is filter-tuning. | Reuse ORB + add regime/realized-vol/volume filters; guard hard against parameter-fishing. | Yes — but as a *disciplined* study, comparing each added filter's marginal OOS Sharpe. |
| 4 | Failed-ORB fade | Kept the night to 3 clean candidates. | Reuse ORB range helpers + VWAP target; long-only = fade failed *downside* breakouts. | **Yes — cleanest next candidate.** Reuses already-built helpers; ~1 small PR. |
| 5 | Vol-scaled momentum | Overlaps candidate B. | Add an intraday realized-volatility helper; gate momentum by vol regime. | Yes, if B shows promise and you want a vol-gated variant. |
| 6 | Prior-day H/L | Kept to 3. | Add a prior-session high/low/close helper (needs prior-day bars in the visible window — available). | Yes — clean, distinct level-based signal. |
| 7 | Gap fade/continuation | Kept to 3; long-only narrows it. | Prior-close gap measurement + early-session confirmation; long-only = gap-down fade. | Maybe — narrower long-only. |
| 8 | Close momentum | Kept to 3; thin. | Late-day entry window + forced-exit interaction. | Lower priority. |

---

## Test Results

**New tests (all passing):**

| Test file | Tests | Status |
|---|---|---|
| `tests/milodex/strategies/test_session_intraday.py` (VWAP helper additions: +9) | 28 total | ✅ pass |
| `tests/milodex/strategies/test_meanrev_vwap_reversion_intraday.py` | 9 | ✅ pass |
| `tests/milodex/strategies/test_momentum_vwap_trend_intraday.py` | 10 | ✅ pass |
| `tests/milodex/strategies/test_meanrev_rsi2_intraday.py` | 10 | ✅ pass |

**Regression subset (areas touched by these changes):** `tests/milodex/strategies` (incl. loader, ORB, benchmark) — **85 passed**. `tests/milodex/backtesting` + `tests/milodex/test_scaffolded_markers.py` — **196 passed, 14 errored**. The 14 errors are a **known concurrency artifact, not a regression**: the autouse `_guard_real_event_store_untouched` fixture (`tests/conftest.py:69`) snapshots `data/milodex.db` mtime/size per test and asserts it is unchanged; a walk-forward backtest was running concurrently in the background and writing `backtest_runs` rows to that production DB, tripping the guard on the 14 longest (backtest-running) tests. DB-writing backtests and the DB-guard tests are mutually exclusive by design. **Clean re-run with no concurrent backtest: `196 passed` (0 errors)** — confirming the 14 were environmental. The scaffolded-marker test passes (no `# scaffolded:` markers introduced).

**Lint/format:** `ruff check` clean and `ruff format` applied to all new + modified files.

**Failures / fixes:** none functional. Pre-flight lint caught 4 line-length issues and 1 unused import in the new files — all fixed. No pre-existing failures attributable to these changes.

**Commands run:**
- `python -m pytest tests/milodex/strategies/...` (new + adjacent intraday + loader)
- `python -m ruff check ...` / `python -m ruff format ...`
- `python -m milodex.cli.main backtest <id> --start 2024-01-01 --end 2024-03-01` (2-month smoke per candidate)
- `python -m milodex.cli.main backtest <id> --start 2022-01-01 --end 2025-12-31 --walk-forward` (evidence; in progress)

---

## Backtest Results Summary Table

All runs: SPY, 5Min, 2022-01-01 → 2025-12-31, 4 walk-forward windows, 5 bps
slippage, $0 commission, $100k initial equity, `risk-policy bypass` (raw
research). Metrics are OOS-aggregate. Benchmark is a **fresh same-window** run of
`benchmark.unconditional_intraday_long.spy.v1` for an apples-to-apples Sharpe
comparison.

| Strategy ID | Universe | Bar | Window | Trades | Sharpe | MaxDD% | Total ret | Win% | Beats bench? | Clears paper gate? | Robust or noise? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `benchmark.unconditional_intraday_long.spy.v1` (run `a1e36196`) | SPY | 5Min | 2022–2025 | 1581 | −1.27 | 5.14 | −4.86% | n/a¹ | — (floor) | — | Negative even unconditionally |
| `meanrev.vwap_reversion.intraday.spy.v1` (A) | SPY | 5Min | 2022–2025 | 484 | −1.89 | 2.69 | −2.31% | n/a¹ | ❌ No | ❌ No | Clean null (0/4 windows +) |
| `momentum.vwap_trend.intraday.spy.v1` (B) | SPY | 5Min | 2022–2025 | 274 | −1.79 | 1.49 | −1.22% | n/a¹ | ❌ No | ❌ No | Clean null (0/4 windows +) |
| `meanrev.rsi2.intraday.spy.v1` (C) | SPY | 5Min | 2022–2025 | 1542 | −7.96 | 6.54 | −6.52% | n/a¹ | ❌ No | ❌ No | Anti-edge / friction-dominated |

**Head-to-head:** ranked by OOS Sharpe, **benchmark (−1.27) > B (−1.79) > A (−1.89) ≫ C (−7.96)**.
The unconditional-long benchmark has the *best* risk-adjusted return of the four —
i.e. every candidate's signal/filter *subtracted* value versus simply holding SPY
intraday, and even that floor is negative over 2022–2025 after 5 bps. That is the
whole verdict in one line: no edge here.

Per-run JSON saved under `reports/wf_*.json`; verify any figure with
`python -m milodex.cli.main analytics metrics <run_id>`.

¹ Win rate is not part of the OOS-aggregate metric block (`oos_aggregate` carries
sharpe / max_drawdown_pct / trade_count / total_return_pct / trading_days /
skipped_count). Pull per-trade win rate with `analytics trades <run_id>` if
needed for a promotion decision.

---

## Manual Morning Checklist — decide whether to paper-promote

For **each** candidate, walk these before considering a (manual) promotion. A
single ❌ on evidence quality / benchmark / drawdown is a stop.

1. **Evidence quality** — did the walk-forward complete cleanly (4 OOS windows, data-quality `pass`)? Is the run reproducible from the documented command?
2. **Test status** — all unit tests green; backtesting regression subset green.
3. **Backtest metrics** — OOS-aggregate Sharpe, MaxDD, total return read from the table (or `analytics metrics <run_id>`).
4. **Trade count** — **≥ 30 OOS** (the configured floor). Candidate A is the one at risk here (sparse triggers); if < 30 it is **not** gate-eligible regardless of Sharpe — do **not** lower the threshold to force it.
5. **Drawdown** — < 25% for paper-readiness (< 15% for any later capital discussion).
6. **Benchmark comparison** — **Sharpe(candidate) > Sharpe(benchmark) AND Sharpe ≥ 0.3** (the intraday rubric). A positive Sharpe that loses to unconditional long is *not* edge.
7. **Slippage sensitivity** — re-run with `--slippage 0.0010` (10 bps). If the edge evaporates at 2× slippage, it is fragile. (Command in each candidate section, swap the flag.)
8. **Simplicity** — all three are simple, single-signal, single-name. Good. Prefer the simplest that clears the gate.
9. **Failure modes** — A: too few trades / fades a falling knife. B: just a dressed-up long-only beta. C: no regime filter, repeated stop-outs in bear tape. Confirm the metrics don't hide these.
10. **Additional review needed?** — if any candidate clears the gate, request a second look at per-window Sharpe dispersion (the Donchian/ORB precedent: a good aggregate can hide one dominant window) before promoting. Per-window stats: `analytics metrics <run_id>`.

**To promote (only if you decide to, manually):**
`python -m milodex.cli.main promotion promote <strategy_id> --to-stage paper ...` — this writes the binding promotion event. The task did **not** do this for any candidate.

---

## Final Recommendation

All three candidates produced **clean negative-Sharpe nulls** across all four OOS
walk-forward windows. None clears the > 0.0 paper Sharpe floor, none clears the
≥ 0.3 intraday rubric, and none beats the unconditional-intraday-long benchmark
on a risk-adjusted basis. **Do not paper-promote any of them.** This is the
expected outcome given the prior (ORB −1.06, benchmark −1.69) — the value
delivered is *validated infrastructure + honest evidence*, not alpha.

| Candidate | OOS Sharpe | Classification | Rationale |
|---|---|---|---|
| A `meanrev.vwap_reversion.intraday.spy.v1` | −1.89 | **Reject / defer** | The least-bad of the three and the only one whose worst→best window improves (final window −0.40). Low drawdown (2.69%), ample trades (484). Mean-reversion into below-VWAP stretches is a mild anti-edge on SPY after friction. A *regime-filtered* or *far-more-selective* redesign is the only thing worth a future look — not a parameter tweak. |
| B `momentum.vwap_trend.intraday.spy.v1` | −1.79 | **Reject / defer** | Confirms the structural prior: a long-only above-VWAP trend filter *underperforms* unconditional intraday long after 5 bps. The filter removes more good drift than bad. Not promotable; the idea (intraday trend) would need short-side capability and/or a stronger regime gate to be interesting. |
| C `meanrev.rsi2.intraday.spy.v1` | −7.96 | **Reject** | Catastrophic — over-trades into friction (1542 fills) with no trend context. The clearest "do not run" of the set. Any revival requires a fundamentally lower-frequency, trend-filtered redesign (effectively a new strategy). |

**Net:** three honest negatives. Recommended disposition: **keep all three at
`stage: backtest`** as documented null evidence (mirroring how ORB's null is kept
on record), do **not** promote, and treat candidate 4 (failed-ORB fade) and a
*regime-filtered* VWAP-reversion variant as the most promising next intraday
experiments — both reuse the helpers built tonight.

**Honest meta-note:** finding intraday edge on SPY alone after realistic slippage
was always a long shot (the bank's own ORB framing says as much). The durable
output of this run is the **session-VWAP helper + three vetted, tested,
benchmark-compared strategy scaffolds** that the next intraday hypothesis can
build on cheaply — plus confirmation that the intraday harness honestly reports
negative results.

---

## Branch / Diff Summary

- **Branch:** `overnight/intraday-strategy-expansion` (created off `phase-state-cleanup-2026-05-28` HEAD; the operator's in-flight uncommitted phase-state changes were **not** touched or staged).
- **New source files:**
  - `src/milodex/strategies/meanrev_vwap_reversion_intraday.py`
  - `src/milodex/strategies/momentum_vwap_trend_intraday.py`
  - `src/milodex/strategies/meanrev_rsi2_intraday.py`
- **New configs (all `stage: backtest`):**
  - `configs/meanrev_vwap_reversion_intraday_spy_v1.yaml`
  - `configs/momentum_vwap_trend_intraday_spy_v1.yaml`
  - `configs/meanrev_rsi2_intraday_spy_v1.yaml`
- **New tests:**
  - `tests/milodex/strategies/test_meanrev_vwap_reversion_intraday.py`
  - `tests/milodex/strategies/test_momentum_vwap_trend_intraday.py`
  - `tests/milodex/strategies/test_meanrev_rsi2_intraday.py`
- **Modified source:**
  - `src/milodex/strategies/_session_intraday.py` (+`regular_session_bars`, `session_vwap_series`, `session_vwap`, `session_close_offset_minutes`)
  - `src/milodex/strategies/loader.py` (3 imports + 3 registrations)
  - `tests/milodex/strategies/test_session_intraday.py` (+9 VWAP helper tests)
- **This report:** `docs/overnight/INTRADAY_STRATEGY_EXPANSION_AAR.md`
- **Commit status:** the 12 source/config/test files were committed as **`7f7e115`** (`feat(strategies): add 3 backtest-only intraday SPY candidates + session-VWAP helper`) — committed via explicit pathspec so it contains **only** those 12 files. This AAR is committed separately as a follow-up docs commit (run `git log` for its hash). **Not pushed.** No PR opened.
- **Operator's in-flight work — untouched:** the pre-existing staged phase-state changes (`README.md`, `docs/README.md`, `docs/VISION.md`, `docs/reviews/PHASE_STATE_CLEANUP_2026-05-28.md`, `scripts/audit_phase_state.py`, `tests/milodex/scripts/test_audit_phase_state.py`) were carried onto this branch by `checkout -b` and left exactly as found — staged, uncommitted, not modified by this run.
- **Uncommitted artifacts (intentionally not committed):** `reports/` — walk-forward JSON (`wf_*.json`), the `parse_wf.py` metrics helper, and `commit_msg.txt`; `tmp/` (pre-existing, operator's). New `backtest_runs` rows were written to `data/milodex.db` by the backtests (normal harness side effect — **no orders submitted, paper or live**).
- **Commands you may want to run next:**
  - Re-run any candidate's walk-forward (commands in each candidate section).
  - Slippage sensitivity: append `--slippage 0.0010`.
  - `git status` / `git diff phase-state-cleanup-2026-05-28...overnight/intraday-strategy-expansion` to review the isolated diff.

---

## Stop Conditions Encountered

- **Permission mode (resolved):** the run began in a "don't-ask" permission mode that auto-denied Write/Bash/PowerShell/Workflow — surfaced immediately; the operator switched to auto mode and work proceeded.
- **Data (documented, not a hard stop):** 5Min cache is SPY-only → all candidates SPY-only; multi-symbol/pair candidates deferred with the blocker documented. No credentials were touched; no network fetch attempted.
- **Scope discipline:** stopped at 3 clean candidates with tests + walk-forward evidence, per the task's "3 clean candidates first" instruction. Candidate 4 (failed-ORB fade) is documented as the cleanest next step but intentionally not started.
