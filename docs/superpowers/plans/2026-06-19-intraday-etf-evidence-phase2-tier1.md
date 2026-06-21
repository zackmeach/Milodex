# Intraday ETF Evidence — Phase 2 Tier 1 Implementation Plan

> **For agentic workers:** each PR is implemented by a fresh subagent under
> `superpowers:test-driven-development` + ponytail. Steps use checkbox (`- [ ]`) syntax. Orchestrator
> dispatches per-PR, reviews between PRs, runs `risk-invariant-reviewer` on E-PR2, and thermonuclear at the
> Tier-0+1 gate. **This spec was adversarially reviewed (2-lens Opus panel); all findings are folded in.**

**Goal:** Stand up the working-lane milestone — a real candidate evaluated per-symbol across 17 ETFs against
its baselines, including a **random matched-exposure** null — so the operator can review per-symbol
candidate-vs-baseline evidence (coverage attached). Needs neither F nor G.

**Architecture:** Two PRs.
(E-PR2) **Random matched-exposure baseline** — a long-only intraday baseline that, per session, makes a
deterministic per-(symbol,session-date) random decision to enter (gated by a per-symbol `session_entry_rate`
matching the candidate's measured round-trip count) at a random time in the entry window, **held to the
session time-stop** (no stop, no signal exit). Plus a rate-measurement helper and per-symbol config
generation that injects the measured rate. Seed is a config parameter; determinism is per-(symbol,session-date).
(E-PR3) **`baseline_ref` config field** — optional loader/config field linking a baseline to its candidate;
metadata until G consumes it.

**Tech Stack:** Python 3.11, pytest (`-n0` tight loops), ruff, numpy (`np.random.default_rng`; transitive via
pandas). Run tests with `PYTHONPATH=<worktree>/src`. Branch baseline at Tier-1 start: `2911 passed, 1 skipped,
4 xfailed`.

**Locked decisions (operator, this session):**
- Faithful match IN THE STRATEGY: per-symbol measured rate; **exit = time-stop only, NO stop_loss, no signal
  exit** ("random entry, held to close"); matched on trade-COUNT + entry-WINDOW, NOT hold-duration (documented).
- Seed = a config `parameter` hashed per-(symbol,session-date) — **no `StrategyContext` change**.
- **Single fixed seed at Tier 1**, the baseline row labeled "single random draw (seed=X), not a
  distribution." Multi-seed mean + CI band is a **G (Tier 3)** feature — out of scope here.
- Long-only. **Never merge to master.**

---

## Design reference (READ BEFORE ANY TASK — the adversarial review rewrote the entry mechanic)

**The candidate** (`meanrev_rsi2_intraday_spy_v1.yaml`): entry window `[opening_range_minutes,
opening_range_minutes + entry_window_minutes)` = `[30, 330)` min → `[10:00, 15:00)` ET; one entry/session on
RSI(2)≤10; exits stop/RSI-revert/time-stop; sizing `per_position_notional_pct=0.10`; skips half-days.

**The random matched-exposure baseline** replaces *signal* with *chance*, holds to close:
- Same entry window + same `per_position_notional_pct` + skips half-days. **No `stop_loss`. No signal exit.**
- Per session, a deterministic RNG seeded from `(symbol, session_date, seed)` decides:
  1. `enter_this_session = rng.random() < session_entry_rate` (matches round-trip COUNT in expectation),
  2. if entering, `target_offset_min` = a uniformly random integer minute in
     `[opening_range_minutes, opening_range_minutes + entry_window_minutes)`.
- **Entry firing (streaming-safe — the corrected mechanic):** emit BUY at the **first PRESENT in-window bar
  whose offset-from-open ≥ `target_offset_min`, while flat AND not already-entered-this-session.** Do NOT use
  exact-offset equality and do NOT enumerate `entry_window_bars` for the choice — the engine's visible barset
  is **cursor-truncated** (only bars up to "now"; `intraday_simulation.py:17-18`,
  `engine.py:1246-1250`), so the full window isn't visible at decision time, and an exact `==` match silently
  never fires on a missing bar (under-trading thin symbols — review BLOCKER B1/B2).
- **One-round-trip-per-session guard (review BLOCKER B3):** mirror RSI2's `_already_entered_this_session`
  (`meanrev_rsi2_intraday.py:193-204, 351-370`): re-derive the session RNG + `target_offset_min`, and refuse a
  new BUY if any PRIOR visible in-window bar this session already had offset ≥ target. A `context.positions`
  flat-check alone is INSUFFICIENT — the T+1 fill gap leaves `positions` empty across bars
  (`engine.py:1240-1305`), so the prior-bar re-scan is what actually prevents double entry. (No `entry_state`
  read is needed at all now that there's no stop.)
- Exit: emit SELL when holding AND `is_time_stop_bar(ts, exit_minutes_before_close)` (`_session_intraday.py:124`).
- Edge (documented, acceptable): if `enter_this_session` but `target_offset_min` lands past the last present
  in-window bar (thin/short session), no BUY fires — a minor, coverage-correlated under-fire inherent to
  streaming random entry. Far better than the exact-`==` blocker; G attaches coverage so it's visible.

**Determinism (the global-seed pitfall — review confirmed sound):** derive the RNG INSIDE `evaluate()` from
`(symbol, session_date, seed)` — NEVER a process-global `np.random.seed` (windows run sequentially with no
reseed, `walk_forward_runner.py:250-260`; a global seed correlates every window). Draw `enter` FIRST then
`target` in fixed order every call so the choice is stable across a session's bars:
```python
import hashlib
import numpy as np

def _session_rng(symbol: str, session_date: date, seed: int) -> np.random.Generator:
    basis = f"{symbol}:{session_date.isoformat()}:{seed}"
    return np.random.default_rng(int.from_bytes(hashlib.sha256(basis.encode()).digest()[:8], "big"))
```

**Seed channel:** `seed` is read via `context.parameters["seed"]` (the `_validated_parameters` pattern,
`bench_time_of_day_null.py:119`). The only production `StrategyContext(` site is `loader.py:102`; all others
are tests/fixtures — **no contract change** (review NIT confirmed).

**Recording:** put `{seed_basis, session_entry_rate, entered_session, target_offset_min, entry_offset_min}`
in `DecisionReasoning.extras` (a LEGACY always-serialized field, `base.py:196` — do NOT add a
`DecisionReasoning` field; that needs `omit_if_default`, golden test `test_base_reasoning.py`).

**Naming:** match the sibling baselines' convention — they use `family="benchmark"`
(`bench_unconditional_intraday_long.py:48`), NOT `"bench"`. Read a sibling config for the exact
family/template/id shape; the new id must satisfy `id == {family}.{template}.{variant}.v{version}`. Proposed:
`family="benchmark"`, `template="random_matched_exposure.intraday"`, variant=symbol →
`benchmark.random_matched_exposure.intraday.spy.v1`. **Registration is AUTOMATIC** — `build_default_registry`
package-scans `milodex.strategies` (`loader.py:444-449`) and registers every concrete `Strategy`; there is NO
manual list to edit. Just ensure `(family, template)` is unique (`loader.py:479-486` raises on collision).

---

## E-PR2: random matched-exposure baseline (decent — highest-risk; risk-invariant-reviewer + thermonuclear)

**Files:**
- Create: `src/milodex/strategies/bench_random_matched_exposure_long.py` (auto-registers)
- Create: `src/milodex/research/candidate_rates.py` (rate-measurement helper)
- Modify: `src/milodex/research/fanout.py` (add optional `param_overrides` hook)
- Test: `tests/milodex/strategies/test_bench_random_matched_exposure_long.py`,
  `tests/milodex/research/test_candidate_rates.py`, `tests/milodex/research/test_fanout.py` (extend)
- Config: `configs/bench_random_matched_exposure_long_spy_v1.yaml` (base) + 16 generated

### Task 1: the strategy class (TDD — the risk core)

Read `bench_unconditional_intraday_long.py` AND `meanrev_rsi2_intraday.py` IN FULL first (the entry-window,
half-day-skip, `positions` flat-check, and `_already_entered_this_session` patterns). Reuse Tier-0
`single_symbol` + the `_session_intraday` helpers (`session_date_et:76`, `is_time_stop_bar:124`,
`is_entry_signal_bar`/in-window helpers, `session_close_offset_minutes:209`). **No stop, no entry_state.**

- [ ] **Step 1 — Failing tests** (inline `StrategyContext` per `test_bench_unconditional_intraday_long.py:164-185`):
  ```python
  def test_determinism_stable_offset_across_growing_barsets():
      # call evaluate() across a SEQUENCE of growing (cursor-advancing) barsets for one session;
      # assert the chosen target/entry is identical each call and exactly one BUY is emitted (review M3+B3)
  def test_different_session_dates_decorrelate():        # same seed, two dates -> independent draws
  def test_entry_rate_zero_never_enters():               # rate=0.0 -> no BUY ever
  def test_entry_rate_one_enters_at_real_in_window_bar():# rate=1.0 -> BUY at a PRESENT in-window bar
  def test_entry_fires_first_bar_at_or_after_target():   # target between bars -> fires at first bar >= target
  def test_missing_target_bar_still_fires_next_present_bar():  # target offset has no bar -> next present in-window bar fires (thin-coverage robustness, B2)
  def test_target_past_last_bar_no_entry():              # target beyond last in-window bar -> no BUY (documented edge)
  def test_exit_on_time_stop_only():                     # holding + is_time_stop_bar -> SELL; NO stop path exists
  def test_one_round_trip_per_session_across_fill_gap(): # flat positions across post-target bars -> still ONE BUY (B3 re-scan guard)
  def test_records_extras():                             # extras has seed_basis, session_entry_rate, entered_session, target_offset_min, entry_offset_min
  def test_skips_half_day():                             # a half-day session -> no entry (matches siblings)
  def test_empty_universe_no_signal()                    # single_symbol(None) path
  def test_multi_symbol_universe_raises()                # Tier-0 cardinality guard fires
  ```
- [ ] **Step 2 — Run, expect FAIL.**
- [ ] **Step 3 — Implement** per the Design reference. `_validated_parameters`: `opening_range_minutes`,
  `entry_window_minutes`, `exit_minutes_before_close`, `per_position_notional_pct`, `session_entry_rate`
  (0.0–1.0), `seed` (int). **No `stop_loss_pct`.** evaluate(): single_symbol→barset→session bars→
  `session_date`; skip half-days; `_session_rng`; `enter = rng.random() < rate`; if enter,
  `target = int(rng.integers(opening_range_minutes, opening_range_minutes + entry_window_minutes))`; emit BUY
  at first present in-window bar (offset ≥ target) while flat AND no prior in-window bar this session reached
  ≥ target (re-scan guard); exit at time-stop. Record `extras`. Long-only.
- [ ] **Step 4 — Run, expect PASS** (all Task-1 tests). ruff.
- [ ] **Step 5 — Commit:** `feat(strategies): random matched-exposure intraday baseline, time-stop exit (E-PR2)`

  NOTE: registration is automatic (package scan). After creating the module, run the existing registry/loader
  tests — the new class appears via discovery; verify no `(family, template)` collision. No separate
  registration task/commit.

### Task 2: candidate rate-measurement helper (TDD)

`src/milodex/research/candidate_rates.py`:
`measure_candidate_rates(*, candidate_config_glob | candidate_strategy_ids, start, end, ctx) -> dict[str, float]`.

- [ ] **Design:** run the candidate's 17 per-symbol walk-forwards via `run_batch` (the Tier-0 candidate
  fan-out configs already exist; the 5Min cache is warmed — Tier-0 0C). For each per-symbol row:
  **`rate[symbol] = oos_round_trip_count / oos_trading_days`**, both read from the SAME walk-forward result
  object (`walk_forward_runner.py` — `round_trip_count` ~:104 is round-trips; `oos_trading_days` ~:97 is the
  OOS trading-day count). **Use round_trip_count, NOT `trade_count`** (`trade_count` = buy+sell fills ≈ 2×
  round-trips — review MAJOR-3 would set the rate 2× too high). Map strategy_id→symbol via the config's
  resolved single-symbol universe. Clamp to [0,1]. Document in the docstring: denominator includes half-days
  (baseline skips them), a known ~1%/yr under-count — acceptable for a null. **A measured rate of exactly 0.0
  is a degenerate (always-flat) baseline — emit a WARNING, do not silently accept (review M2).** Precondition
  (assert/document): the candidate's 17 configs exist + their 5Min caches are warmed.
- [ ] **Step 1 — Failing test** (`test_candidate_rates.py`): with a small SimulatedDataProvider run or a
  stubbed `run_batch` returning a known per-symbol `round_trip_count`/`oos_trading_days`, assert
  `measure_candidate_rates` returns one rate per symbol = round_trips/oos_trading_days, clamped [0,1], and
  WARNs on a 0-trade symbol. Confirm the real field names against `walk_forward_runner`/`walk_forward_batch`
  before asserting.
- [ ] **Step 2-4 — Red → implement → green.**
- [ ] **Step 5 — Commit:** `feat(research): measure per-symbol candidate round-trip rate for matched baseline`

### Task 3: fanout `param_overrides` hook (TDD)

- [ ] **Design:** add optional `param_overrides: dict[str, dict[str, Any]] | None = None` to
  `generate_per_symbol_configs` (`fanout.py:23`). After the existing id/universe/description rewrites in the
  per-symbol loop (after `fanout.py:~109`), inject `for k, v in (param_overrides or {}).get(sym, {}).items():
  strategy_section["parameters"][k] = v`. Default `None` → no-op, so the Tier-0 `test_fanout.py` tests stay
  green. NOTE the base-variant symbol (spy) is SKIPPED in the loop (`fanout.py:79`), so its override is applied
  to the base config separately in Task 4. `ponytail:` comment the hook.
- [ ] **Step 1 — Failing test** (`test_fanout.py`, extend): generate with `param_overrides={SYM:
  {"session_entry_rate": 0.37}}`; assert that symbol's generated config has
  `parameters["session_entry_rate"] == 0.37` and still loads + resolves to one eligible symbol; assert a
  symbol with no override is unchanged. Run the FULL existing `test_fanout.py` (no regression).
- [ ] **Step 2-4 — Red → implement → green.**
- [ ] **Step 5 — Commit:** `feat(research): optional per-symbol param overrides in fan-out generator`

### Task 4: base config + generate the 16 (matched rates injected)

- [ ] Author `configs/bench_random_matched_exposure_long_spy_v1.yaml`: id
  `benchmark.random_matched_exposure.intraday.spy.v1` (CONFIRM against a sibling's family/template shape),
  `universe_ref: universe.spy_only.v1`, parameters = candidate's `opening_range_minutes`/`entry_window_minutes`/
  `exit_minutes_before_close`/`per_position_notional_pct` + `seed: <fixed int>` + placeholder
  `session_entry_rate` (NO `stop_loss_pct`), tempo `5Min`, stage `backtest`, `backtest.slippage_pct: 0.0005`.
- [ ] Measure rates (Task 2) over `[2023-01-01, 2026-06-18]`; generate the 16 (Task 3 `param_overrides` with
  each symbol's rate); set the SPY base's `session_entry_rate` to SPY's measured rate. Verify all 17 load +
  resolve to one symbol + `research screen` resolves 17 unique ids.
- [ ] **Commit:** `chore(configs): random matched-exposure baseline configs (17 ETFs, matched rates, single seed)`

**E-PR2 review:** TDD + `/ponytail-review` + **`risk-invariant-reviewer`** (new strategy on the
trade-proposal path; confirm NO `StrategyContext`/risk/determinism violation, time-stop-only exit, the
re-scan one-entry guard) + full suite green. Held for the **thermonuclear** at the Tier-0+1 gate.

---

## E-PR3: `baseline_ref` config field (small)

An optional `baseline_ref` string on a strategy config, naming the candidate a baseline nulls. Metadata only —
G (Tier 3) consumes it. Nothing reads it at runtime.

**Files:** `src/milodex/strategies/loader.py` (parse + carry, default `None`) + `StrategyConfig` (add field,
default None). Test: where config-field tests live.

- [ ] **Step 1 — Failing test:** a config with `strategy.baseline_ref: "meanrev.rsi2.intraday.spy.v1"` loads
  with `load_strategy_config(...).baseline_ref == "..."`; a config WITHOUT it loads with `baseline_ref is None`.
- [ ] **Step 2 — FAIL → Step 3 — implement** (optional field, default None; do NOT require it; do NOT validate
  the referent exists — G's job; surgical) **→ Step 4 — PASS** + full config-validation tripwires green.
- [ ] **Step 5 — Commit:** `feat(strategies): optional baseline_ref config field (E-PR3, metadata for G)`
- [ ] **Optional:** set `baseline_ref` on the 4 baseline BASE configs (not per-symbol — defer the regen churn).

**E-PR3 review:** TDD + `/ponytail-review` + a light `risk-invariant-reviewer` (touches the loader seam) +
full suite green. Independent of E-PR2.

---

## Working-lane run (after E-PR2 + E-PR3, before the Tier-0+1 gate)

Not a code PR — the milestone demonstration:
- [ ] Measure candidate rates → generate matched-baseline configs (Tasks 2-4).
- [ ] Run candidate + all 4 baselines (no-trade, time-of-day-null, unconditional-long, random-matched-exposure)
  × 17 via `research screen` over `[2023-01-01, 2026-06-18]`.
- [ ] Produce the per-symbol candidate-vs-baseline table, **coverage attached per symbol** (Tier-0 decision),
  and the random-matched row **labeled "single random draw (seed=X), not a distribution"** (the multi-seed CI
  is a G feature). This is the artifact the operator reviews at the Tier-1 gate (the §6.1 F/G/H-timing
  decision is taken here, with the working lane in front of him).

---

## Cross-PR notes

- **Order:** E-PR3 first (small, independent, de-risks the loader). E-PR2 Task 1 (strategy) → Task 2 (rates,
  needs the candidate runnable + cache warm) → Task 3 (fanout hook) → Task 4 (configs, needs rates + hook).
- **risk-invariant-reviewer on E-PR2** mandatory. **Thermonuclear** at the Tier-0+1 gate covers Tier 0 + Tier 1.
- **Top risks (from the adversarial review):** (1) the streaming entry mechanic — first-present-bar-≥-target,
  NOT exact `==`; (2) the one-entry re-scan guard surviving the T+1 fill gap; (3) the rate numerator =
  round_trips not fills; (4) determinism proven across GROWING barsets. Every one has a named test above.

## Self-review (orchestrator, pre-dispatch)

- All 2-lens review findings folded: B1/B2 (grid target + first-present-bar firing), B3 (re-scan guard, not
  positions-only; no entry_state since no stop), B4 (auto-registration, family=benchmark), M1 (rate =
  round_trips/oos_trading_days from exposed fields), M2 (rate-0 warn + cache precondition), M3 (growing-barset
  determinism test); methodology: single-seed-labeled (CI→G), time-stop-only exit (no stop, resolves the
  stop-rigging + hold-mismatch). ✅
- Operator sign-offs honored: config-param seed (no StrategyContext change), faithful per-symbol rate,
  long-only, time-stop-only exit, single-seed-labeled-now. ✅
- Reuses Tier-0 surfaces (single_symbol, fanout, _session_intraday); no risk/execution/promotion touch. ✅
