# Crypto Archetype Proof Slice — Audit & Report

**Date:** 2026-05-30
**Scope:** CLI-only, backtest-only crypto-spot archetype proof using two BTC/USD canaries.
**Deliverable question:** *Can Milodex represent, load, test, and backtest a crypto spot archetype without corrupting the equity assumptions, promotion model, risk model, GUI, or paper runtime?*

This is a harness/architecture proof, **not** an alpha claim. No paper, no live, no GUI, no broker, no orchestration, no network fetch.

---

## Phase 0 — Crypto Archetype Audit

Every claim below is grounded in code read during the audit (file:line). Verified directly, not assumed from the brief.

### 1. Does Milodex already support non-equity asset classes anywhere?
**No.** There is no asset-class taxonomy. Strategies carry `family/template/variant`, a `universe` of symbol strings, and a `tempo.bar_size`; nothing branches on asset class. `AlpacaDataProvider` uses `StockHistoricalDataClient`/`StockBarsRequest` only (`data/alpaca_provider.py:17,60`) — the Alpaca SDK ships `CryptoHistoricalDataClient`/`CryptoBarsRequest` (0.43.4) but they are not integrated. Cache holds equities/ETFs daily + SPY 5Min only (`market_cache/v3/{1Day,5Min}`).

### 2. Do `/`-symbols (`BTC/USD`) survive config loading, registry, strategy IDs, event store, and backtest output?
**Yes, on the backtest/fixture path.** `_load_universe` uppercases and preserves the slash (`loader.py:387`). Event-store `symbol` columns are unconstrained `TEXT` written via parameterized queries (`core/event_store.py:10,36,431,489`); `migrations/001_initial.sql:10,36`. Symbol/strategy-id are never split, regexed, or turned into filesystem paths in runtime flows — lock names use `pathlib` which treats the whole string as one component (`advisory_lock.py:111`, `paper_runner_control.py:34-41`), and attribution only `.strip().upper()`s (`risk/attribution.py:78`). **One landmine, out of scope:** the *Parquet cache* path is `dir/{SYMBOL}.parquet` (`data/cache.py:68`), so `BTC/USD` would create a nested `BTC/` dir and the write would fail. The cache is only touched by the real (network) providers — the fixture/backtest path never hits it. See Q7/Q10.

### 3. Does the backtest engine assume equity market hours?
**No.** The intraday simulation builds its event timeline purely from actual bar timestamps and `bar_ts + bar_size` (`backtesting/intraday_simulation.py:39-87`) — zero references to 9:30/16:00/RTH/half-days. Those concepts live only in `strategies/_session_intraday.py`, which is imported by *equity strategies*, not by the engine. Cross-UTC-day pending-order carryover is handled by construction. A 24/7 strategy that does **not** import the session helper replays continuous bars correctly.

### 4. Does the strategy contract assume daily/equity bars?
**No.** `Strategy.evaluate(bars, context)` is bar-agnostic (`strategies/base.py:153`). The engine dispatches daily-vs-intraday on the `Timeframe` enum (`engine.py:842`: `DAY_1` → daily, else intraday) — an if-equality, not an exhaustive match, so any new intraday timeframe routes to the intraday path automatically.

### 5. Does sizing assume whole shares only?
**The shared helper does; the engine/kernel do not.** `shares_for_notional_pct` returns `int` via `math.floor` (`execution/sizing.py:22,47`) — for ~$50k BTC at 10% of $100k that floors to **0**, which would silently zero out every crypto order. But the simulation kernel stores and fills **float** quantity natively (`simulation_kernel.py:456,460-461`), `compute_equity` multiplies float qty × price (`:763-766`), and `TradeIntent.quantity` is already `float` (`execution/models.py:51`). **Fix is local to the strategy:** a fractional sizing helper, not an engine change.

### 6. Does risk assume equities, market hours, margin, or shortability?
**Irrelevant to this slice — the backtest bypasses the risk layer.** `BacktestEngine` injects `NullRiskEvaluator` under the default `BYPASS` policy (`engine.py:1305-1307`; CLAUDE.md "backtesting is intentionally below the risk layer"). Even if it didn't, the evaluator reasons in *notional* terms (`market_value`, `estimated_order_value`) and is fractional-friendly. The market-hours / paper-only / shortability checks fire only in paper/live, which this slice never enters.

### 7. Does the data cache support hourly or 30-minute crypto bars?
**Partially, and not for `/`-symbols.** `ParquetCache` is keyed by `{version}/{timeframe.value}/{SYMBOL}.parquet` (`cache.py:64-68`). `1Hour` works; **`30Min` does not exist** as a `Timeframe` (`data/models.py:30-41`) and the `/`-path bug (Q2) blocks crypto symbols. The backtest fixture path bypasses the cache entirely via `SimulatedDataProvider` (`data/simulated.py`), so neither limitation affects the proof.

### 8. Is there any local BTC/USD data already available?
**No.** Repo-wide search found zero crypto bars (only the Alpaca SDK's `crypto.py` and unrelated `site-packages`/`dist` hits). Cache = equities/ETF daily + SPY 5Min.

### 9. Do GUI / Strategy-Bank surfaces safely ignore or display backtest-only crypto configs?
**Yes.** `_load_strategy_configs` catches `ValueError` from a bad config and *skips with a warning* (`gui/read_models.py:873-888`); `_StrategyRow.as_qml()` never exposes `bar_size`/`symbol` (`:87-125`); `family` is checked only for the `'regime'` exemption — `'crypto'` hits no `KeyError` (`:1448-1465`). Bench facade returns a structured `Blocker`, never raises (`commands/bench.py:2182-2191`). Once `30Min` is a valid bar size, the crypto config *loads* and renders as an inert backtest-stage row.

### 10. What is the smallest safe implementation that proves the archetype without broad refactoring?
1. **`30Min` timeframe** — add one enum member + two map entries + one validation-set entry (+ the small enumerated siblings). General capability, contained.
2. **Fractional sizing helper** — new `fractional_units_for_notional_pct`, leave `shares_for_notional_pct` untouched.
3. **Two strategy classes** that compute *continuous* (non-session-reset) indicators and do **not** import `_session_intraday`.
4. **Two `stage: backtest` configs** (inert by Q9/promotion audit).
5. **Unit tests per rule + one real-engine backtest smoke test** that injects `SimulatedDataProvider` with deterministic fixture bars (proves loader → registry → engine → kernel → strategy → event store end to end).

No engine/kernel/risk/promotion/GUI changes. The cache `/`-bug and the missing crypto data provider are **deliberately left untouched** — they are the *data-ingestion* task (Q2/Q7), and the brief explicitly scopes full historical crypto backtesting as blocked by missing local data + the no-network policy.

---

## Design decisions (and deviations from the brief)

| Concern | Decision | Rationale |
|---|---|---|
| Symbol | `BTC/USD` (raw Alpaca form) | Event-store/runtime safe (Q2). Honest to the archetype. Cache `/`-normalization is the ingestion task's job. |
| Strategy IDs | `momentum.crypto.ema_cross.btc_usd_1h.v1`, `meanrev.crypto.rsi2.btc_usd_30m.v1` | Validator requires `id == family.template.variant.vN` (`loader.py:396-414`). Mapped to `template=crypto.ema_cross`/`crypto.rsi2`, `variant=btc_usd_1h`/`btc_usd_30m`. **Deviation:** brief's `...btc_usd.1h.v1` used a dotted variant; underscored to keep variant dot-free and self-documenting. |
| 24/7 | Strategies do **not** import `_session_intraday` | Engine timeline is already 24/7-native (Q3). |
| Fractional sizing | New `fractional_units_for_notional_pct` (no floor, 8-dp round) | Q5. |
| 30Min | New `Timeframe.MINUTE_30="30Min"` + enumerated siblings | Q7; minor, contained. |
| Asset-class metadata | **None added** | Nothing branches on it in backtest (Q1/Q4). A first-class taxonomy is deferred framework work. |
| max-hold (30m) | **Day-granular** via `held_days`/`max_hold_days`, not "12 bars" | `held_days` ticks per outer day *by design* (`simulation_kernel.py:41-47`). A true bar-count max-hold needs entry-timestamp plumbing through the shared kernel — out of scope per the "small change → broad arch → stop & document" rule. In practice RSI(2) normalization exits dominate; max-hold is a rare backstop. |
| Fees | Conservative slippage placeholder; **no fee model** | The 30m canary takes ~2× the round-trips of the 1h canary, so identical per-trade slippage compounds into materially more drag at 30m — documented, not modeled. No parameter tuning. |
| CLI offline run | **Not built** | CLI hardcodes `AlpacaDataProvider` with mandatory creds + network prefetch. Building a fixture provider + `--data-source` seam is the ingestion task. Proof runs through the engine directly (smoke test). |

---

## What was built

All changes are surgical and additive — no engine, kernel, risk, promotion, or GUI code was modified.

**New capability (30-minute bars — general, motivated by the 30m canary):**
- `Timeframe.MINUTE_30 = "30Min"` (`data/models.py`) + both helper maps (`data/timeframes.py`) + the Alpaca `_TIMEFRAME_MAP` entry (`data/alpaca_provider.py`, pinned by a new exhaustiveness test) + the loader's `_VALID_BAR_SIZES` and the duplicate `cli/config_validation.py` set.
- Deliberately **not** wired into the runner poll table (`.get` fallback, off the backtest path) or the `data` CLI `TIMEFRAME_CHOICES` (that belongs to the deferred ingestion task).

**New fractional sizing helper:**
- `fractional_units_for_notional_pct` (`execution/sizing.py`) — no whole-unit floor, 8-dp rounding, same validation as the equity helper. `shares_for_notional_pct` untouched.

**New shared indicators:**
- `_indicators.py` — `ema_series`, `wilder_rsi_series` (the latter mirrors the proven equity RSI). Used only by the new crypto strategies; adopting it in the existing `meanrev_rsi2_*` strategies is a noted future cleanup.

**Two strategy classes** (24/7, continuous indicators, no session helper, long-only, one position, fractional):
- `momentum_crypto_ema_cross.py` → `MomentumCryptoEmaCrossStrategy` (`momentum`/`crypto.ema_cross`).
- `meanrev_crypto_rsi2.py` → `MeanrevCryptoRsi2Strategy` (`meanrev`/`crypto.rsi2`).
- Both registered in `loader.build_default_registry`.

**Two `stage: backtest` configs:**
- `configs/momentum_crypto_ema_cross_btc_usd_1h_v1.yaml`, `configs/meanrev_crypto_rsi2_btc_usd_30m_v1.yaml` (universe `["BTC/USD"]`, conservative placeholder slippage 10 bps, boring untuned defaults).

**Docs:** this report; a backtest-only crypto section in `docs/STRATEGY_BANK.md`.

## Results

Built test-first (red → green per component). **Full suite: 2186 passed, 2 skipped, 4 xfailed** (the skips/xfails are pre-existing, untouched). ~38 new test cases:

- `test_sizing.py` (7) — fractional sizing returns sub-unit quantities, never floors to 0, validates inputs.
- `test_indicators.py` (6) — EMA/Wilder-RSI correctness incl. warmup NaN and oversold/recovery.
- `test_momentum_crypto_ema_cross.py` (7), `test_meanrev_crypto_rsi2.py` (8) — per-rule entry/exit/priority, no-pyramid, never-short-when-flat, fractional qty.
- `test_crypto_configs.py` (5) — both configs load through the real loader, IDs satisfy the strict validator, registry resolves both, `30Min` accepted, stage is `backtest`.
- `test_crypto_archetype_backtest.py` (3) — **the harness proof.** Each canary runs through the real `BacktestEngine` + simulation kernel + event store on a deterministic sinusoid fixture (injected via `SimulatedDataProvider`, no network/cache/strategy mocks). Verified: a real round trip executes (`buy_count > 0` and `sell_count > 0`), equity moves, the run persists `status='completed'`, trade rows are written, **the position quantity is fractional** (`0 < qty < 1`), and the **`BTC/USD` symbol round-trips through the event store unmangled**.
- `test_timeframes.py`/`test_alpaca_provider.py` — `30Min` maps both ways; `_TIMEFRAME_MAP` covers every `Timeframe` member.

**No alpha is claimed.** The fixture is a sinusoid chosen to exercise the rules, not real BTC data — it proves the *path*, not a *signal*. Fee/slippage is a conservative placeholder (10 bps), identical on both canaries so the 30m canary's heavier round-trip count is what drives its larger drag (the cadence point), not a tuned number.

**Answer to the deliverable question:** Yes. Milodex represents, loads, validates, and backtests a crypto-spot archetype through the existing CLI/research path with fractional sizing and a `/`-symbol, without touching the equity assumptions, promotion model, risk model, GUI, or paper runtime. The only blocked piece — full *historical* crypto backtesting — is blocked by missing local data + the no-network policy, exactly as scoped, and is the deferred ingestion task below.

## CLI commands (runnable once crypto data ingestion lands)

```bash
python -m milodex.cli.main backtest momentum.crypto.ema_cross.btc_usd_1h.v1 --start YYYY-MM-DD --end YYYY-MM-DD --walk-forward --json
python -m milodex.cli.main backtest meanrev.crypto.rsi2.btc_usd_30m.v1 --start YYYY-MM-DD --end YYYY-MM-DD --walk-forward --json
```

**These are blocked today** by: (a) no local BTC/USD bars, (b) the no-network policy, (c) `AlpacaDataProvider` is stock-only + the cache `/`-path bug. No network fetch was performed. The strategy/harness path is proven instead by the deterministic-fixture backtest smoke test.

## Explicit next step — crypto data ingestion (out of scope here)
A future task must: (1) normalize `/`-symbols to a filesystem-safe cache key (`cache.py:68`), (2) add a `CryptoDataProvider` using `CryptoHistoricalDataClient`/`CryptoBarsRequest`, (3) route crypto symbols to it, (4) expose a local-only / fixture data-source seam on the `backtest` CLI command. Only then are the CLI commands above runnable with real data.
