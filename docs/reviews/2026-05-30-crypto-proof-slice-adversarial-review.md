# Crypto Proof Slice — Adversarial Review

_Date: 2026-05-30 · Evidence-bound adversarial pass · Target: crypto-archetype proof-slice working tree on `master`_

## Verdict

**APPROVE**

The branch truthfully and safely proves its claim: a crypto **spot** archetype can be represented (config + registered class), loaded by the real `StrategyLoader`, sized fractionally, replayed through the real `BacktestEngine` + simulation kernel, and persisted/read-back from the real event store with `BTC/USD` intact — all without touching paper/live/micro_live, the risk evaluator, promotion governance, the broker, the runner/orchestrator, or the GUI. The change set is small, additive, and surgical; the full suite is green (**2186 passed, 24 skipped, 4 xfailed, 0 failed** run serially); ruff + format are clean. The documentation (report, Strategy Bank note, config headers, strategy docstrings) is unusually honest about what is *not* proven — no alpha, no real historical data, no CLI historical backtest, no paper/live eligibility — and the one real landmine (the Parquet cache cannot key a `/`-symbol) is disclosed in plain sight rather than hidden. No blockers, no high/medium defects were found; the only follow-ups are explicitly-documented deferrals.

## Executive Summary

- **What the branch proves:** `30Min` is plumbed through every timeframe/validation/provider touchpoint; two BTC/USD canaries load through the real loader and backtest through the real engine; `fractional_units_for_notional_pct` yields sub-unit float quantities that the kernel carries and the event store persists; `BTC/USD` (with `/`) survives strategy → engine → event store → read-back; both canaries produce real buy+sell round trips under their documented rules with correct exit priority.
- **What it does not prove (and says so):** no alpha (fixture is a sinusoid), no real historical BTC performance, no CLI historical crypto backtest, no crypto data ingestion, no symbol-safe cache, no paper/live/promotion eligibility.
- **Highest-risk areas reviewed:** (1) scope creep into equity sizing / promotion / runtime, (2) `30Min` becoming an accepted-but-unsupported state, (3) `BTC/USD` mangling on the `/`. All three came back clean — sizing isolation is exact, `30Min` routes through the generic intraday path (only `DAY_1` is ever special-cased), and the symbol round-trips through the event store unmangled while the cache landmine is bypassed *and* disclosed.
- **Merge recommendation:** merge as-is.

## Findings

### Blockers

None found.

### High

None found.

### Medium

None found.

### Low

#### LOW-1 — Shared indicators are duplicated from the equity RSI rather than shared (documented deferral)
- **Evidence:** `src/milodex/strategies/_indicators.py` docstring: _"It is duplicated here rather than imported from a strategy module so the new crypto canaries do not depend on a sibling strategy's private function; adopting this shared helper in the existing equity strategies is a deliberate, out-of-scope future cleanup."_ The report ("What was built") repeats this.
- **Why it's only LOW / not a finding to act on now:** the duplication is intentional and explicitly scoped out; the `wilder_rsi_series` implementation is classic SMA-seeded Wilder (seed = simple mean of the first `lookback` gains/losses, then the Wilder recursion) and is unit-tested against a reference. The only durable risk is drift: if the equity RSI-2 strategies ever change their RSI seeding, this helper and the "mirrors the proven equity RSI" claim must change with them. Smallest correction (future): converge both on this module.
- **False-positive check:** documented as deferred ✔; in-scope to *note*, out-of-scope to *fix* ✔; not theoretical (it's a real maintenance seam) ✔; fixing now would expand the branch into equity strategies it deliberately leaves untouched ✔.

#### LOW-2 — Template name `ema_cross` describes state-based, not event-based, entry
- **Evidence:** `src/milodex/strategies/momentum_crypto_ema_cross.py` enters on the **state** `fast > slow` while flat (`if fast <= slow: return _no_signal(...)`), not on a detected crossover *event* (no `prev_fast <= prev_slow and curr_fast > curr_slow` check). Exit is the state `fast < slow`.
- **Why it's only LOW / non-actionable:** the docstring and the config description say exactly this ("fast EMA above slow EMA after warmup → BUY"), and the unit test is honestly named `test_buy_when_fast_above_slow_and_flat` (not `..._on_cross`). Because entry is flat-only and exit is `fast < slow`, the realized behavior for a single long-only position is functionally identical to crossover trading (hold while `fast > slow`, exit on cross-down, re-enter on the next cross-up). No doc/config/test claims event-detection semantics, so nothing is misleading. No change needed.
- **False-positive check:** not deferred (it's current behavior) ✔; docs are accurate, so not a truthfulness defect ✔; behavior ≡ crossover for the single-position long-only case, so not a correctness defect ✔; "fixing" (adding explicit cross detection) would change behavior with no benefit ✔.

## Non-issues investigated

1. **Equity whole-share sizing is byte-for-byte unchanged.** The `sizing.py` diff shows `shares_for_notional_pct` untouched; the new `fractional_units_for_notional_pct` is appended below it. Behavior re-confirmed by execution: whole-share path returns `int` and floors (`shares_for_notional_pct(1000, 0.5, 50000) == 0`); fractional path returns `float` and does **not** floor.
2. **The fractional helper is isolated to crypto.** `fractional_units_for_notional_pct` is imported only by `momentum_crypto_ema_cross.py` and `meanrev_crypto_rsi2.py` (scan of all `src/milodex`); no equity strategy imports it and no equity strategy file was modified. It is keyword-only and validates `equity>0`, `0<notional_pct<=1`, `unit_price>0` (7 tests in `tests/milodex/execution/test_sizing.py`, incl. the three rejection cases and the no-floor / 8-dp-rounding cases).
3. **`30Min` is contained, not a hidden unsupported state.** Added to `data/models.py` enum, `data/timeframes._BAR_SIZE_TO_TIMEFRAME` + `_TIMEFRAME_TO_MINUTES` (→30), `data/alpaca_provider._TIMEFRAME_MAP`, and both `_VALID_BAR_SIZES` sets (loader + config_validation), with timeframe + Alpaca-exhaustiveness tests. Across `src/`, the only `Timeframe` member ever branched on is `DAY_1` (engine daily/intraday dispatch at `engine.py:842`; runner market-hours gate) — `30Min` falls into the same generic intraday bucket as the already-supported `5Min`/`15Min`, and the real-engine smoke test exercises the 30Min canary end to end. `to_minutes()` returns 30; existing sizes still map.
4. **The backtest "round trip" is a genuine event-store round trip.** `tests/milodex/backtesting/test_crypto_archetype_backtest.py` runs each canary through a real `BacktestEngine` + `EventStore(tmp_path/...)`, then **re-reads** `store.list_trades()` and asserts `all(t.symbol == "BTC/USD")` and `all(0 < t.quantity < 1)` on the persisted rows — i.e. it verifies symbol-safety and fractional sizing against what came *back out of* the store, not just the in-memory result. No mocks (`Mock`/`monkeypatch`/`patch` = 0), no network (`requests`/`alpaca` = 0); only `SimulatedDataProvider` for deterministic bars.
5. **No crypto durable footprint.** Live `data/milodex.db` query: `strategy_runs` (by `strategy_id LIKE '%crypto%'`), `trades` (by symbol `LIKE '%/%'`), and `explanations` all return **0** crypto rows. No promotion rows were created. Both configs are `stage: backtest`. The smoke tests write to throwaway `tmp_path` DBs, never the durable store.
6. **Risk layer untouched and correctly bypassed.** No `risk/` file in the change set; the report grounds (Q6) that `BacktestEngine` injects `NullRiskEvaluator` under the default `BYPASS` policy — risk is enforced at promotion, not simulation (per CLAUDE.md). The canaries never reach paper/live, so market-hours / shortability / margin checks are never invoked.
7. **GUI is passive and safe.** No `gui/` or Bench-control files touched. The report grounds (Q9) that `gui/read_models._load_strategy_configs` catches a bad config's `ValueError` and skips with a warning, and that crypto renders as an inert `stage: backtest` row exposing neither `bar_size` nor `symbol`. The full suite (incl. GUI smoke/read-model tests) is green with the two new strategies registered.
8. **Symbol handling is honestly represented.** Strategies emit `TradeIntent(symbol="BTC/USD")`; the loader uppercases and preserves the slash; the event store stores it as unconstrained `TEXT`. The one place the `/` *would* break — the Parquet cache key `dir/{SYMBOL}.parquet` — is explicitly called out in the report (Q2/Q7) as a landmine the fixture/backtest path bypasses and the data-ingestion task must fix. Nothing implies cache-backed BTC/USD works.
9. **Indicator edge cases are safe.** `wilder_rsi_series` returns `NaN` during warmup (consumers guard with `pd.isna`), `100.0` on all-gains (`avg_loss == 0, avg_gain > 0`), and `50.0` on a perfectly flat series (`avg_gain == avg_loss == 0`) — no division crash. `ema_series` uses `ewm(adjust=False)`.
10. **Tests are deterministic and portable.** Fixtures are fixed-timestamp sinusoids / explicit close lists; no wall-clock, no `Date.now`-style nondeterminism, no absolute/user-machine paths (`tmp_path` + `REPO`-relative config paths). The end-to-end smoke test runs a 6-day window with a 7-day warmup lead-in — seconds, not minutes — acceptable for routine CI.

## Deferred work that is correctly out of scope

All accurately documented in the report, Strategy Bank note, and/or config headers — none is falsely claimed done, so none is a finding:

- Crypto data ingestion (real OHLCV fetch via `CryptoHistoricalDataClient`/`CryptoBarsRequest`).
- Symbol-safe cache keying for the `/` in `BTC/USD` (`data/cache.py:68`).
- A local/fixture `--data-source` seam on the `backtest` CLI (CLI hardcodes the network `AlpacaDataProvider`); hence no CLI historical crypto backtest.
- Crypto fee model (slippage is a disclosed conservative 10-bps placeholder).
- Crypto broker execution and paper/live/micro_live eligibility.
- GUI/Bench crypto controls.
- **Bar-count max-hold.** The implementation uses **calendar-day-granular** `max_hold_days`, and every surface says so — the strategy docstring, the config header, the test name `test_sell_when_max_hold_expires`, and the report's design table all state that `held_days` ticks per outer day by design and a sub-day bar-count max-hold would require kernel plumbing. Day-granular is not penalized here because nothing claims bar-count behavior.
- 5-minute stress canary.
- Adopting the shared `_indicators` module in the existing equity RSI strategies (LOW-1).

## Test / command verification

| Command | Result |
| --- | --- |
| `python -m pytest` (full suite) | **2186 passed, 24 skipped, 4 xfailed**, 0 failed |
| `python -m pytest --co -q tests/` (collection) | 2192 collected |
| 8 crypto-related test files | 39 passed, 0 failed |
| `python -m ruff check src/ tests/` | `All checks passed!` |
| `python -m ruff format --check` (4 new src files) | `4 files already formatted` |
| Live DB: crypto rows in `strategy_runs` / `trades` / `explanations` | 0 / 0 / 0 |
| Behavioral probe: whole-share vs fractional sizing | whole-share floors to `int`; fractional returns un-floored `float` |

The **full suite was run** and is green; the report's headline "2186 passed … 4 xfailed" reproduces exactly (`addopts` is `None` — no marker filtering or xdist sharding in play). The report says "2 skipped" vs 24 here — a trivial environment difference in conditionally-skipped tests; the pass/xfail counts match and there are zero failures.

## Review coverage

Read in full and analyzed (via `git diff` / `git diff --no-index`, which rendered reliably):

- **Strategy classes:** `src/milodex/strategies/momentum_crypto_ema_cross.py` (241 ln), `src/milodex/strategies/meanrev_crypto_rsi2.py` (284 ln) — entry/exit rules, exit priority, no-pyramid, no-short, warmup, NaN handling, fractional sizing, parameter validation.
- **Indicators:** `src/milodex/strategies/_indicators.py` (62 ln) — `ema_series`, `wilder_rsi_series`, `_rsi_from` edge cases.
- **Sizing:** `src/milodex/execution/sizing.py` diff — additive-only; new helper signature/validation/rounding/return type.
- **Timeframe plumbing:** `data/models.py`, `data/timeframes.py`, `data/alpaca_provider.py` diffs.
- **Loader / validation:** `strategies/loader.py` (valid sizes + registry registration), `cli/config_validation.py` (valid sizes).
- **Configs:** both BTC/USD YAMLs in full — params, `stage: backtest`, `universe: ["BTC/USD"]`, honesty headers.
- **Tests:** `test_crypto_archetype_backtest.py`, `test_sizing.py`, `test_momentum_crypto_ema_cross.py`, `test_meanrev_crypto_rsi2.py` in full; `test_indicators.py`, `test_crypto_configs.py`, `test_timeframes.py`, `test_alpaca_provider.py` (names + mocking/import audit).
- **Docs:** `docs/reviews/2026-05-30-crypto-archetype-proof-slice.md` (full), Strategy Bank note (`docs/STRATEGY_BANK.md` diff).
- **Scope sweep:** `Timeframe` member usage across `src/`, fractional/indicator import graph, live event-store row inventory, ruff/format/full-suite verification.

Whole change set (modified): `cli/config_validation.py`, `data/alpaca_provider.py`, `data/models.py`, `data/timeframes.py`, `execution/sizing.py`, `strategies/loader.py`, `tests/.../test_alpaca_provider.py`, `tests/.../test_timeframes.py`, `docs/STRATEGY_BANK.md`. (Added): two configs, three strategy/indicator modules, four test modules, two docs. **No** file under `risk/`, `promotion/`, `gui/`, `broker/`, `backtesting/engine*`, or any runner/orchestrator path was touched.

## Final recommendation

**Merge as-is.** The slice is coherent, in-scope, additive, fully green, and documented with rare honesty (including the cache `/`-landmine and the day-granular max-hold caveat). The two LOW items are documented deferrals, not merge gates. No split, no held changes, no additional tests required for merge. The only thing worth carrying forward as a tracked follow-up is the crypto data-ingestion task the branch already names as the prerequisite for real historical backtests.

---

### Reviewer's note on method (transparency)

Per the project's "report only verified results" rule, two corrections were made mid-review before finalizing: (1) an early draft flagged the event-store "round trip" test as not actually re-reading the store — that was based on a corrupted/garbled tool read; the real test *does* call `store.list_trades()` and assert symbol-safety + fractional sizing on the persisted rows, so the finding was withdrawn. (2) An early garbled read of `python -m pytest` output showed "1245 passed"; a clean re-run gave the authoritative 2186. Both corrections trace to the same cause — intermittent truncation/garbling of large tool outputs in this session — so all source claims here were re-grounded through `git diff` (which rendered reliably) and behavior was re-verified by execution. Nothing is asserted from an un-grounded read.
