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

2. **`_load_universe_bars(..., timeframe: Timeframe)`** — explicit timeframe argument. No config-reading inside the helper. Drops the hardcoded `Timeframe.DAY_1`. Pure function, easy to test.

3. **Per-day timestamp set / decision-time builder** — given a day and the loaded `all_bars`, returns the sorted chronological union of decision-times across symbols whose bars fall in the day. Normalizes provider timestamp convention to avoid lookahead.

4. **Per-symbol cursors** — `cursors: dict[str, int]`. Cursor invariant: `cursor[symbol]` is the **exclusive end index** of the symbol's bar history currently visible to the strategy. Visible history is always `bars_df.iloc[:cursor[symbol]]`. Advanced as the iteration walks timestamps in chronological order. Persists across timestamps within a day and across day boundaries.

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
      → all_bars = self._load_universe_bars(universe, timeframe, start, end)
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

The guarantee falls out of the implementation discipline: `_simulate_daily(...)` is the existing `_simulate` body, **moved without behavioral change**. The intraday code lives in `_simulate_intraday(...)`. Daily configs touch zero new code paths.

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

1. **Timeframe dispatch test**. Given a config with `tempo.bar_size = "5Min"`, verify `_load_universe_bars` is called with `Timeframe.MIN_5`, not `Timeframe.DAY_1`. Catches the root wiring failure directly.

2. **Intraday smoke benchmark — exact trade counts**. Run the `benchmark.unconditional_intraday_long.spy.v1` config against 3 days of synthetic 5min SPY bars. Assert:
   - `trade_count == 6`
   - `buy_count == 3`
   - `sell_count == 3`
   - `round_trip_count == 3`

3. **No same-bar fill**. A trivial test strategy emits BUY on bar timestamped 10:00 ET. Assert the resulting fill price equals the 10:05 bar's open with slippage applied — NOT the 10:00 bar's open.

4. **No-lookahead decision-time test**. Start-timestamped bars are not treated as completed at their start. A strategy attempting to "see" the 10:00 bar's close at decision-time 10:00 must NOT have that bar in its visible history; the visible history at decision-time 10:00 includes only bars whose decision-time is ≤ 10:00 — i.e., bars timestamped 9:55 and earlier (for 5min bars with start-timestamping).

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
