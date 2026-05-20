# Intraday Backtest Engine — Design Spec

**Date:** 2026-05-20
**Status:** Brainstormed; pending writing-plans transition
**Branch:** to be created from `feat/intraday-orb-spy-v1`

## Context

Milodex's `BacktestEngine` is daily-only by construction. It hardcodes `timeframe=Timeframe.DAY_1` at [engine.py:341](../../src/milodex/backtesting/engine.py) when fetching universe bars, and iterates `for day in trading_days` in `_simulate(...)` at [engine.py:682](../../src/milodex/backtesting/engine.py), calling `strategy.evaluate()` once per trading day with a daily bar slice.

The first intraday strategies — `breakout.orb.intraday.spy.v1` and `benchmark.unconditional_intraday_long.spy.v1` (commit `f587d65` on `feat/intraday-orb-spy-v1`) — produce zero trades silently when backtested. The benchmark, which trades unconditionally every full session and should produce ~3 round trips on a 3-day window, also produces zero trades. The diagnostic signature is conclusive: the engine is on the daily path regardless of the strategy's `tempo.bar_size = "5Min"`.

This PR adds a second simulation path inside `BacktestEngine` to support non-daily bar sizes. The daily path is preserved unchanged. The intraday path iterates bars chronologically by decision-time, evaluates strategies once per timestamp using the full multi-symbol market slice available, and fills pending orders at the next available bar's open — never the same bar (no lookahead).

## Goals & Non-goals

**Goals (in scope):**

- `BacktestEngine` respects `tempo.bar_size` for the entire simulation pipeline: data fetch, simulation loop, evaluation cadence, fill semantics.
- New intraday simulation path iterates bars in timestamp order, evaluates once per timestamp, fills pending orders at the next available bar's open for each symbol (including next-session open if no later same-day bar exists).
- Daily strategies continue to produce materially identical backtest output. Asserted by regression tests.
- Single `BacktestEngine` class with an internal dispatch based on `tempo.bar_size`. No separate engine class. No broad abstract-base refactor.
- Equity snapshots remain daily-keyed at day end, preserving the `backtest_equity_snapshots` contract (ADR 0053) and downstream consumers (GUI bench, analytics, reports).

**Non-goals (explicitly out of scope for this PR):**

- Per-strategy promotion gate overrides in `policy.py` (deferred — manual at promote-time per `breakout.orb.intraday.spy.v1` plan).
- Regime-stratified Sharpe reporting in backtest output.
- Engine-native benchmark comparison (sibling-config-as-benchmark is the current mechanism).
- Schema validator for `commission_per_trade` on non-daily bars.
- Per-bar (vs daily) equity sampling — explicitly preserved daily.
- Intraday data-completeness validation (per-session/per-timestamp completeness checks) — coarse universe coverage stays as-is, documented as a known limitation.
- New time-in-force order types (`DAY`, `GTC`, etc.) — pending orders are simulated next-bar orders, documented as such.
- Live-runner intraday changes — the runner already handles intraday polling cadence ([runner.py:34](../../src/milodex/strategies/runner.py)). This PR is engine-only.

## Architecture

`BacktestEngine` remains a single public engine with an internal dispatch based on `tempo.bar_size`. The existing daily simulation body is moved into `_simulate_daily(...)` with behavior preserved as closely as possible. A new `_simulate_intraday(...)` implements the day → timestamp → market-slice loop for non-`1D` configs. Both paths return the same `_SimulationOutput` shape so downstream persistence, GUI reporting, and analytics contracts do not change.

Shared setup remains centralized: strategy loading, risk evaluator construction, warmup calculation, broker/data/execution service construction, result shaping, and persistence. The data-loading helper becomes timeframe-aware by accepting an explicit `Timeframe` argument derived from `tempo.bar_size`; daily configs continue to pass `Timeframe.DAY_1`, while intraday configs pass `MIN_1`, `MIN_5`, `MIN_15`, etc.

The daily path stays as close to the current implementation as possible. Intraday behavior belongs in the new private path, not as scattered conditionals throughout the daily loop.

```python
class BacktestEngine:
    def _simulate(self, ...):
        bar_size = self._loaded.config.tempo["bar_size"]
        if bar_size == "1D":
            return self._simulate_daily(...)
        return self._simulate_intraday(...)

    def _simulate_daily(self, ...):
        # existing simulation body, moved without behavioral change

    def _simulate_intraday(self, ...):
        # new day → timestamp → market-slice loop
```

The data-fetch fix is small, but it unlocks a separate intraday simulation path that owns the non-daily semantics.

## The Intraday Simulation Path

`_simulate_intraday()` keeps the **outer day loop** identical in shape to the daily path. Per day:

1. **Held-days accounting** at day start (existing daily logic, preserved).
2. **Build the day's timestamp set**: sorted union of bar decision-times across all universe symbols whose bars fall in this day. For a SPY-only intraday universe on a full session, this is 78 timestamps. For a multi-symbol intraday universe with mismatched calendars, this is the union — at some timestamps only a subset of symbols will have bars. Pre-computed once per day.
3. **For each timestamp T in the day, in chronological order:**
   - Drain pending orders that have a fillable bar at T: for each pending order, if its symbol appears in `opens_at_timestamp(T)`, fill at that open with slippage and commission applied. **Orders whose symbol has no bar at T remain pending** (they are not skipped — they wait for the next bar where the symbol appears).
   - Build `bars_by_symbol_visible`: per-symbol, the visible-history slice through the cursor's current position. Visible history is `df.iloc[:cursor[symbol]]`. Symbols absent at T retain their prior cursor position; they remain in the strategy's context with whatever completed-bar history they have.
   - Evaluate the strategy once: `strategy.evaluate(primary_barset_visible, context_with_bars_by_symbol_visible)`. The strategy sees one consistent multi-symbol market slice. New intents append to `pending`.
4. **At day end**: mark-to-market each held position using its latest available close at or before the day's final timestamp; sum to compute end-of-day equity; append to equity curve.
5. **Cross-day**: pending orders persist across day boundaries. Cursors persist across day boundaries (they are advanced in chronological iteration, not reset).

**At backtest end**: any pending orders that never find a future bar for their symbol are counted toward `skipped_count`. Skipping happens only after the final timestamp is processed and no future bar exists for the symbol — not at any earlier "missing current bar" timestamp.

## Conventions

**Bar completion / decision time / fill time.** A provider bar timestamped `T` covers the interval `[T, T + bar_size)` if the provider uses interval-start timestamping (Alpaca's convention). The bar's OHLC is only fully observable at `T + bar_size`. The engine treats each provider bar as **complete only at its decision time**:

- If the provider timestamps bars by interval start: `decision_time = timestamp + bar_size`
- If the provider timestamps bars by interval end: `decision_time = timestamp`

Strategy evaluation at decision-time `T_d` may use bars completed at or before `T_d`. Resulting orders may fill no earlier than the next bar's open *after* `T_d` for the order's symbol. This is the no-lookahead invariant.

Even if the implementation stores only one normalized timestamp internally, the design distinguishes:

- `bar_timestamp` — the provider's label
- `bar_start_time`, `bar_end_time` — the interval bounds
- `decision_time` — when the strategy is allowed to know the bar's OHLC
- `next_fill_time` — the earliest possible fill timestamp for orders created at `decision_time`

**Pending order semantics.** Backtest pending orders are simulated next-bar orders that persist until the next available bar for their symbol appears, or until the backtest ends with no future bar (skipped). They do not expire at session close. This is intentional simplification for v1 — the PR does not model real broker time-in-force (DAY/GTC/IOC/etc.).

## Components & Helpers

1. **`timeframe_from_bar_size(bar_size: str) -> Timeframe`** — shared helper near the `Timeframe` enum. Location: `src/milodex/data/timeframes.py` (new file) or in `src/milodex/data/models.py` if it stays clean. Used by both the runner and the backtest engine. The engine MUST NOT import strategy-runner code for this; both should import from the neutral data module. Existing helper at [runner.py:562](../../src/milodex/strategies/runner.py) gets relocated.

2. **`prefetch_bars(..., timeframe: Timeframe)`** — explicit timeframe argument. No config-reading inside the helper. Drops the hardcoded `Timeframe.DAY_1` at [engine.py:341](../../src/milodex/backtesting/engine.py). `prefetch_bars` is the SINGLE data-fetch entry point used by both single-pass and walk-forward backtests ([engine.py:499](../../src/milodex/backtesting/engine.py)). Fixing it here covers both call sites; there is no second hardcoded `DAY_1` to chase elsewhere — but the test plan must explicitly cover walk-forward + intraday tempo to prevent regression.

3. **Per-day timestamp set / decision-time builder** — given a day and the loaded `all_bars`, returns the sorted chronological union of decision-times across symbols whose bars fall in the day. Normalizes provider timestamp convention to avoid lookahead. **Day-bucketing rule:** bars are assigned to a "day" by their `bar_timestamp.date()` (the provider's label, not their `decision_time`). This keeps each session's last bar in its own session's day-bucket even when the bar's `decision_time` rolls into the next clock day. The existing `_build_ts_date_index` ([engine.py:1328](../../src/milodex/backtesting/engine.py)) computes `pd.to_datetime(df["timestamp"], utc=True).dt.date` — UTC date. For US RTH intraday bars this matches the ET trading-session date because US RTH bars (9:30 ET / 14:30 UTC through 16:00 ET / 21:00 UTC) never cross UTC midnight. **Assumption documented:** intraday bars are RTH-only. Pre-market or after-hours intraday would require a tz-aware date conversion; out of scope for v1.

4. **Per-symbol cursors** — `cursors: dict[str, int]`. Cursor invariant: `cursor[symbol]` is the **exclusive end index** of the symbol's bar history currently visible to the strategy. Visible history is always `bars_df.iloc[:cursor[symbol]]`. **Initialization:** at simulation start, `cursors[symbol] = 0` for every universe symbol (no history visible yet). **Advancement timing — load-bearing:** at each timestamp T, the order of operations is strictly:
   1. **Drain pending orders at T** using opens from bars whose `bar_timestamp == T` (they were observable at T's open price, which is fixed at bar-start).
   2. **Advance cursors:** for each symbol, advance `cursors[symbol]` by 1 for every bar at the cursor's current position whose `decision_time <= T`. After this step, `bars_df.iloc[:cursor[symbol]]` includes every bar whose `decision_time` is ≤ T for that symbol.
   3. **Evaluate the strategy** with the post-advancement visible history.

   This ordering guarantees no lookahead (bars whose `decision_time > T` are excluded from the strategy's view) AND no underdraw (bars whose `decision_time == T` ARE visible, because they're complete by definition at T). Cursors persist across timestamps within a day and across day boundaries — they advance monotonically through the chronological iteration.

5. **`_opens_at_timestamp(bars_by_symbol, timestamp, ...) -> dict[str, float]`** — builds the symbol → open-price map for symbols that have a fillable bar at the given timestamp. Keeps data lookup separate from order execution. Symbols absent here are not fillable now, but may be fillable at a later timestamp.

6. **`_drain_pending_at_timestamp(pending, opens_at_t, cash, positions, ...)`** — generalization of the existing `_drain_pending`. Reuses broker/order accounting semantics: slippage applied multiplicatively, commission as flat USD per fill. Fills only orders whose symbol appears in `opens_at_t`. Orders for symbols absent from `opens_at_t` stay in `pending` (they are not skipped — they wait for the next available bar for their symbol).

7. **`_mark_to_market_at_day_end(positions, bars_by_symbol, ...)`** — at day end, marks each held position using that symbol's latest available close at or before the day's final timestamp. Important for multi-symbol universes where one symbol may be missing the final bar of the day. Produces the end-of-day equity sample fed into the equity curve.

8. **Intraday convention docstring** — module-level docstring on `_simulate_intraday()` explicitly documents `bar_timestamp` vs `bar_end_time` vs `decision_time` vs `next_fill_time`. This is reference documentation, not just a comment — future readers (and future agents) should be able to derive the no-lookahead invariant from it.

## Data Flow

```
CLI invocation
  → BacktestEngine.__init__(loaded_strategy)
  → BacktestEngine._simulate(...)
      → bar_size = loaded.config.tempo["bar_size"]
      → timeframe = timeframe_from_bar_size(bar_size)
      → all_bars = self.prefetch_bars(universe, timeframe, start, end)
      → coverage check (existing `_barset_has_bar_in_range` + threshold resolution)
      → if bar_size == "1D":
            return self._simulate_daily(all_bars, trading_days, ...)
        else:
            return self._simulate_intraday(all_bars, trading_days, ...)
      → both return _SimulationOutput → identical downstream persistence
```

No change to the public surface, no change to consumers (`backtest_runs`, `backtest_equity_snapshots`, GUI bench, report). The dispatch is the only top-level structural change.

## Edge Cases

- **Missing current bar for one symbol at timestamp T**. The symbol is absent from `opens_at_timestamp(T)`, so pending orders for that symbol cannot fill at T. However, if the symbol has prior completed bars, its visible history remains available in `context.bars_by_symbol` through the symbol's cursor. **Missing current bar means "not fillable now," not "erase this symbol from history."** Three concepts must stay distinct:
  - `opens_at_timestamp` — symbols with a current bar at T; eligible for fills
  - `bars_by_symbol_visible` — symbols with any completed history up to T; available for strategy context
  - `active_symbols_at_t` — optional metadata: symbols that actually printed a bar at T

- **Multi-symbol calendar mismatch** (one symbol trades on a day another doesn't). The per-day timestamp set excludes symbols with no bars that day. Per-symbol cursors stay put for absent symbols. Generalizes the daily path's behavior naturally.

- **Pending order for a symbol with no future bars at all** (delisting, end-of-backtest stranding). Skipped only after the final timestamp is processed with no future bar appearing. Counted toward `skipped_count`. Identical to the daily path's stranded-pending behavior.

- **Last-bar-of-session intent**. A signal emitted at the bar with `decision_time == session_close` produces a pending order. The next available bar for that symbol is the next session's first bar. The order fills overnight at next-session open. This is correct, documented, and the strategy's responsibility to design around. The engine does not special-case "same-session-only" fills.

- **Warmup window**. Stays calendar-day-based via the existing `_warmup_calendar_days()`. For intraday strategies with small lookbacks, this is generously over-sized — fine for correctness, free for performance with cached bars.

- **DST transitions and half-day sessions**. The engine never assumes a fixed session close time. Session boundaries are derived from the bars present in each day-bucket: the "last bar of session" is whichever bar has the latest `bar_timestamp` in the day-bucket, regardless of whether the session is full (close 16:00 ET → last 5min bar timestamped 15:55) or half (close 13:00 ET → last 5min bar timestamped 12:55). DST transitions are handled implicitly via the UTC-date bucketing of `_build_ts_date_index` plus the RTH-only assumption (US RTH bars never cross UTC midnight in any season). Strategy-layer half-day skipping (as the ORB and benchmark strategies do via `is_half_day(session_date)`) is independent of engine behavior — the engine simply iterates whatever bars are present and lets the strategy decide whether to act.

- **Coverage check applicability**. The existing `_barset_has_bar_in_range(symbol, start, end)` and the threshold resolution chain at [engine.py:366](../../src/milodex/backtesting/engine.py) work for intraday because they check bar presence in a date window regardless of granularity. **This is a coarse symbol-level presence check, not real intraday completeness validation.** A symbol with one 5min bar in the entire requested window passes as "covered." This is acceptable for v1 and documented as a known limitation. A future intraday data-quality layer can add expected-bars-per-session checks.

- **Walk-forward windows**. Each window has its own `_simulate_intraday` call. Pending orders, positions, and cursors are derived only from the window's own fetched/warmup bars — they do not leak between windows.

## Daily-Preservation Guarantee

For any strategy with `tempo.bar_size == "1D"`, `_simulate(...)` must produce a `_SimulationOutput` **materially identical** to what the pre-PR engine produces, given the same input bars, slippage, and commission.

"Materially identical" is defined by these assertions (NOT byte/JSON-snapshot identity, which is brittle against incidental serialization differences):

- `equity_curve`: same dates AND same equity values within `abs(diff) < 0.01`
- `trade_count`: exact int equality
- `buy_count`: exact int equality
- `sell_count`: exact int equality
- `round_trip_count`: exact int equality
- `skipped_count`: exact int equality
- `final_equity`: within `abs(diff) < 0.01`

The guarantee falls out of the implementation discipline: `_simulate_daily(...)` is the existing `_simulate` body, **moved without behavioral change**. The intraday code lives in `_simulate_intraday(...)`. Daily configs traverse the same logical simulation path as before.

**Caveat — shared helper signature change.** `prefetch_bars` changes signature to accept an explicit `timeframe` parameter (vs the hardcoded `Timeframe.DAY_1`). Daily call sites pass `Timeframe.DAY_1` and the helper body is otherwise unchanged. To rule out subtle regression through the signature change, the daily regression suite (§5) runs *after* the signature refactor is complete — not just after the dispatch is added — and asserts the four daily fixtures produce identical output post-refactor. If a daily test fails after the signature change but passes after dispatch addition alone, the helper refactor introduced a regression.

## Testing Strategy

### Half 1 — Daily Regression Suite

New module: `tests/milodex/backtesting/test_engine_daily_regression.py`.

Four test cases, each asserting material-equivalence against a pre-PR baseline `_SimulationOutput` captured before any engine changes:

1. **Simple long-only daily strategy** (IBS-style stripped fixture) — basic daily path coverage.
2. **Multi-symbol cross-sectional case** — catches dict-ordering regressions in pending-order handling.
3. **Stranded pending orders at backtest end** — catches `skipped_count` regression.
4. **`held_days` / `max_hold` exit behavior** — catches the day-loop's held-days accounting regression.

Tests use synthetic small datasets (no real Alpaca data) for speed and determinism.

### Half 2 — Intraday Correctness Tests

New module: `tests/milodex/backtesting/test_engine_intraday.py`.

Ten test cases:

1. **Timeframe dispatch test**. Given a config with `tempo.bar_size = "5Min"`, verify `prefetch_bars` is called with `Timeframe.MIN_5`, not `Timeframe.DAY_1`. Catches the root wiring failure directly.

2. **Intraday smoke benchmark — exact trade counts**. Run the `benchmark.unconditional_intraday_long.spy.v1` config against **4 days** of synthetic 5min SPY bars. The benchmark emits BUY at 10:00 ET decision-time and SELL at 15:55 ET decision-time each session. With "fills at next available bar's open" semantics, each session's BUY fills at that day's 10:05; each session's SELL fills at the NEXT session's 9:30. The final session's SELL has no future bar and strands. Assert:
   - `buy_count == 4` (one fill per day at 10:05)
   - `sell_count == 3` (days 1-3 sells fill on days 2-4 at 9:30; day 4 sell strands)
   - `trade_count == 7` (4 buys + 3 sells filled)
   - `round_trip_count == 3` (`min(buys, sells) per symbol`)
   - `skipped_count == 1` (day 4's exit, stranded)

   This exact-count contract validates BOTH the round-trip mechanism AND the documented session-boundary stranding behavior in one test.

3. **No same-bar fill**. A trivial test strategy emits BUY on bar timestamped 10:00 ET. Assert the resulting fill price equals the 10:05 bar's open with slippage applied — NOT the 10:00 bar's open.

4. **No-lookahead decision-time test — both directions of off-by-one**. At iteration step T, visible history must contain exactly those bars whose `decision_time ≤ T`. Test asserts BOTH directions:
   - **Inclusion (no under-shoot):** the bar with `decision_time == T` (i.e., `bar_timestamp == T - bar_size` for start-timestamped 5min bars; specifically the bar timestamped 9:55 at iteration step T = 10:00) MUST be in the strategy's visible history.
   - **Exclusion (no over-shoot / lookahead):** the bar with `decision_time > T` (the bar timestamped 10:00 at iteration step T = 10:00, whose `decision_time = 10:05`) MUST NOT be in the strategy's visible history.

   This protects against the implementer "fixing" a lookahead bug by under-shooting cursor advancement.

5. **Multi-symbol visible history with missing current bar**. Build a 2-symbol universe where symbol B has a missing 10:05 bar. At decision-time 10:05, assert symbol A's 10:05 bar IS in `opens_at_timestamp`, symbol B is NOT in `opens_at_timestamp`, AND symbol B's prior history through 10:00 IS visible in `context.bars_by_symbol`.

6. **Pending order survives missing current bar**. Setup: pending BUY for symbol B from a 10:00 decision; symbol B has no 10:05 bar; symbol B has a 10:10 bar. Assert:
   - At 10:05: order remains in `pending`; `skipped_count` does NOT increment.
   - At 10:10: order fills at B's 10:10 open with slippage.

7. **Independent cursor advancement**. Cursors are exclusive-end indices; symbols advance independently; cursors persist across timestamps and across day boundaries within a single simulation. Walk three days of bars and verify each symbol's cursor advances correctly without resetting at day boundaries.

8. **Session-boundary pending fill**. Strategy emits BUY on the bar whose decision-time equals session close. Assert the order fills at next-session 9:30 open with slippage. Documented behavior, not a bug.

9. **Stranded pending at final backtest end**. A pending order whose symbol has no future bar is counted toward `skipped_count` — only after the final timestamp is processed with no future bar appearing.

10. **Walk-forward isolation**. Two-window backtest. Assert window 2's state is derived only from window 2's own fetched/warmup bars — pending orders, positions, and cursors do not leak from window 1.

### Test Data Construction

Synthetic bars built inline in tests (extends the pattern from `test_breakout_orb_intraday.py`'s `_orb_session_bars` helper). 3 days × 78 bars per session = 234 bars per symbol max — well within unit-test budgets. **No real Alpaca data in unit tests.**

### Smoke Validation Outside CI

After implementation lands, a manual operator step (NOT a CI test): run the actual `breakout.orb.intraday.spy.v1` backtest over 2022-01-01 to 2025-12-31 against cached Alpaca data. Verify trade count is in the expected range (~150-250 trades), generate the Sharpe-vs-benchmark comparison, and apply the manual promotion-gate rubric (Sharpe ≥ 0.3 AND ORB Sharpe > benchmark Sharpe → eligible for paper). This is the real validation but it's manual, not a unit test, because the data fetch is expensive and external-dependency-heavy.

## Known Limitations / Future Work

- **Coarse coverage check.** Universe coverage validates symbol-level bar presence in the requested window; it does not validate per-session/per-timestamp intraday completeness. A symbol with one bar in the whole window passes. Future PR can add an intraday data-quality layer.
- **No time-in-force order modeling.** Pending orders persist indefinitely until they find a future bar (or the backtest ends). Real broker `DAY` orders expire at session close. v1 deliberately doesn't model this.
- **Daily-only equity sampling.** Equity is sampled once per trading day at day-end mark-to-market. Sub-daily equity curves would help intraday performance analysis but are out of scope.
- **No promotion-gate awareness of intraday tempo.** The `paper_gate.min_sharpe = 0.0` threshold is too loose for intraday's signal-to-cost ratio. Per-strategy gate overrides in `policy.py` are deferred; manual operator enforcement at promote-time fills the gap.

## References

- Branch: `feat/intraday-orb-spy-v1` (commit `f587d65`)
- Strategy plan: `~/.claude/plans/fancy-drifting-crystal.md`
- Discovery transcript: brainstorm session 2026-05-20
- Engine code: `src/milodex/backtesting/engine.py`
- Runner timeframe map: `src/milodex/strategies/runner.py:34-36`, `runner.py:562-570`
- Intraday strategies awaiting engine support:
  - `configs/breakout_orb_intraday_spy_v1.yaml`
  - `configs/bench_unconditional_intraday_long_spy_v1.yaml`
- ADR 0053: `backtest_equity_snapshots` table contract (must not change)
