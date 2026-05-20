# Intraday Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an intraday simulation path to `BacktestEngine` that respects `tempo.bar_size`, iterates an event timeline of bar starts and bar completions, and fills pending orders at the next available bar's open — while preserving daily-strategy behavior materially identically.

**Architecture:** Single `BacktestEngine` class with internal dispatch on `tempo.bar_size`. The existing `_simulate` body becomes `_simulate_daily` (moved unchanged). A new `_simulate_intraday` implements the day → event-timestamp → market-slice loop. A neutral `timeframe_from_bar_size` helper relocates from `runner.py` to `data/timeframes.py` so the engine doesn't import strategy-runner code. The data-fetch helper `prefetch_bars` becomes timeframe-parameterized.

**Tech Stack:** Python 3.11, pandas, pytest. Modifies `src/milodex/backtesting/engine.py`; touches `src/milodex/strategies/runner.py` and creates `src/milodex/data/timeframes.py`. New tests in `tests/milodex/backtesting/` and `tests/milodex/data/`.

**Spec reference:** [docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md](../specs/2026-05-20-intraday-backtest-engine-design.md). Read it before starting — the conventions section (bar_timestamp / decision_time / next_fill_time) is load-bearing.

---

## File Structure

**Create:**
- `src/milodex/data/timeframes.py` — shared `timeframe_from_bar_size(bar_size: str) -> Timeframe` helper. Single responsibility: string-to-enum mapping.
- `tests/milodex/data/__init__.py` (if not present)
- `tests/milodex/data/test_timeframes.py` — tests for the helper.
- `tests/milodex/backtesting/__init__.py` (if not present)
- `tests/milodex/backtesting/test_engine_daily_regression.py` — 4 daily regression tests asserting material equivalence post-refactor.
- `tests/milodex/backtesting/test_engine_intraday.py` — 10 intraday correctness tests.

**Modify:**
- `src/milodex/backtesting/engine.py` — single file owns all engine logic. Adding ~300 lines for intraday helpers and `_simulate_intraday`; refactoring existing `_simulate` into `_simulate_daily` plus dispatch. Don't unilaterally split this file — the codebase pattern is one engine module.
- `src/milodex/strategies/runner.py:562-570` — remove local `_timeframe_from_bar_size`, import from `data/timeframes.py`.

---

## Conventions Reminder (read before coding)

For start-timestamped 5min bars (Alpaca's convention):
- A bar timestamped `T` covers `[T, T + 5min)`.
- `decision_time = bar_timestamp + bar_size` (when the bar's OHLC becomes observable).
- `fill_event` at T = at least one symbol has `bar_timestamp == T`.
- `decision_event` at T = at least one symbol has `decision_time == T`.

Strict ordering at each event timestamp T:
1. **Drain** pending orders if T is a fill event for any symbol (fill at that bar's open).
2. **Advance cursors** if T is a decision event for any symbol (cursor invariant: `cursor[symbol]` is exclusive end index).
3. **Evaluate** strategy ONLY if cursors advanced (no eval on pure fill events).

**v1 timestamping scope:** assume Alpaca interval-start timestamps. Do NOT implement end-timestamped provider normalization.

---

## Phase A — Prep: Shared Timeframe Helper

### Task A1: Create the shared `timeframe_from_bar_size` helper

**Files:**
- Create: `src/milodex/data/timeframes.py`
- Test: `tests/milodex/data/test_timeframes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/milodex/data/test_timeframes.py
"""Tests for timeframe_from_bar_size shared helper."""

from __future__ import annotations

import pytest

from milodex.data.models import Timeframe
from milodex.data.timeframes import timeframe_from_bar_size


def test_maps_each_valid_bar_size() -> None:
    assert timeframe_from_bar_size("1D") == Timeframe.DAY_1
    assert timeframe_from_bar_size("1H") == Timeframe.HOUR_1
    assert timeframe_from_bar_size("15Min") == Timeframe.MINUTE_15
    assert timeframe_from_bar_size("5Min") == Timeframe.MINUTE_5
    assert timeframe_from_bar_size("1Min") == Timeframe.MINUTE_1


def test_raises_keyerror_on_unknown() -> None:
    with pytest.raises(KeyError):
        timeframe_from_bar_size("30Min")
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/milodex/data/test_timeframes.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'milodex.data.timeframes'`.

- [ ] **Step 3: Create the helper**

```python
# src/milodex/data/timeframes.py
"""Shared bar-size → Timeframe mapping.

Used by both the live runner ([runner.py]) and the backtest engine.
Lives here so neither imports the other.
"""

from __future__ import annotations

from milodex.data.models import Timeframe

_BAR_SIZE_TO_TIMEFRAME: dict[str, Timeframe] = {
    "1D": Timeframe.DAY_1,
    "1H": Timeframe.HOUR_1,
    "15Min": Timeframe.MINUTE_15,
    "5Min": Timeframe.MINUTE_5,
    "1Min": Timeframe.MINUTE_1,
}


def timeframe_from_bar_size(value: str) -> Timeframe:
    """Return the Timeframe enum matching ``value``.

    Raises:
        KeyError: when ``value`` is not a recognized bar-size string.
    """
    return _BAR_SIZE_TO_TIMEFRAME[value]
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m pytest tests/milodex/data/test_timeframes.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/milodex/data/timeframes.py tests/milodex/data/test_timeframes.py
git commit -m "feat(data): timeframe_from_bar_size shared helper

Single source of truth for bar-size string → Timeframe enum.
Replaces strategies/runner.py's _timeframe_from_bar_size in the
next task, and used by backtesting/engine.py for intraday dispatch."
```

### Task A2: Migrate runner.py to use the shared helper

**Files:**
- Modify: `src/milodex/strategies/runner.py:562-570` (remove local helper, replace usage with import)

- [ ] **Step 1: Find all call sites of `_timeframe_from_bar_size` in runner.py**

```
python -c "import re; print(re.findall(r'_timeframe_from_bar_size\([^)]*\)', open('src/milodex/strategies/runner.py').read()))"
```

Expected output: a list of call sites (likely just at line 426).

- [ ] **Step 2: Update the import block at the top of `runner.py`**

Find the existing import of `Timeframe` from `milodex.data.models` and add the new helper import. The new line:

```python
from milodex.data.timeframes import timeframe_from_bar_size
```

- [ ] **Step 3: Replace all `_timeframe_from_bar_size(...)` calls with `timeframe_from_bar_size(...)` (drop the leading underscore)**

Use Edit to rename in-place.

- [ ] **Step 4: Delete the local helper at runner.py:562-570**

The function definition `def _timeframe_from_bar_size(value: str) -> Timeframe: ...` is removed entirely.

- [ ] **Step 5: Run the full strategy test suite to confirm runner still works**

```
python -m pytest tests/milodex/strategies/ -q
```

Expected: all tests pass (current count is 233; should still be 233 ± the 2 new timeframe tests if pytest collected them).

- [ ] **Step 6: Commit**

```bash
git add src/milodex/strategies/runner.py
git commit -m "refactor(runner): use shared timeframe_from_bar_size helper

Removes the local duplicate; the engine will import from the same
neutral location."
```

---

## Phase B — Daily Regression Baseline (BEFORE any engine modification)

**Critical ordering note:** the four daily regression tests below MUST be written and their expected values captured BEFORE any engine modifications (Phases C, D, E). Their purpose is to detect drift introduced by the refactor, so the baseline values must come from the pre-modification engine. If you have already made engine changes when starting this phase, `git stash` them, complete Phase B, then `git stash pop`.

### Task B1: Build daily regression test fixtures + capture baselines

**Files:**
- Create: `tests/milodex/backtesting/__init__.py` (if not present)
- Create: `tests/milodex/backtesting/test_engine_daily_regression.py`

**Goal:** Capture expected `_SimulationOutput` values from the CURRENT (pre-engine-refactor) engine on 4 synthetic daily inputs. These tests must pass against the pre-modification engine AND against the post-PR engine.

- [ ] **Step 1: Write four test cases with synthetic inputs and placeholder asserts**

```python
# tests/milodex/backtesting/test_engine_daily_regression.py
"""Daily regression suite for the backtest engine refactor.

Each test constructs a synthetic strategy + universe + bars, runs the
engine, and asserts material equivalence (NOT byte/snapshot identity)
against expected values captured from the pre-refactor engine.

If a test fails after the refactor lands, the daily code path has
regressed. Per spec §Daily-Preservation Guarantee.
"""

from __future__ import annotations


def test_daily_regression_simple_long_only() -> None:
    """Simple long-only daily strategy on a single ETF — basic daily path."""
    # Setup: minimal IBS-style strategy, single SPY, ~6 months of synthetic daily bars
    # Run engine
    # Assert: trade_count, buy_count, sell_count exact int match
    # Assert: equity_curve dates and values match within $0.01
    # Assert: final_equity within $0.01
    pass  # Implementer fills in based on Step 2 capture


def test_daily_regression_multi_symbol_cross_sectional() -> None:
    """Multi-symbol cross-sectional strategy — catches dict-order regressions."""
    pass


def test_daily_regression_stranded_pending_orders() -> None:
    """Strategy with orders that never fill — catches skipped_count regression."""
    pass


def test_daily_regression_max_hold_exits() -> None:
    """Strategy exercising the held_days / max_hold_days day-loop accounting."""
    pass
```

- [ ] **Step 2: For each test, run the pre-modification engine and capture expected values**

For each test:
1. Pick or build a synthetic strategy fixture with deterministic behavior. For test #1, a stripped IBS-style fixture is fine.
2. Build synthetic daily bars (~125 trading days = 6 months) with deterministic OHLC.
3. Run `BacktestEngine(...).run(...)` against the CURRENT engine (no Phase C modifications yet).
4. Observe `BacktestResult` and hardcode the values as test assertions.

Pattern per test:
```python
assert result.trade_count == 42        # exact int
assert result.buy_count == 21
assert result.sell_count == 21
assert result.skipped_count == 0
assert result.round_trip_count == 21
assert abs(result.final_equity - 100423.12) < 0.01
assert len(result.equity_curve) == 125
assert result.equity_curve[0] == (date(2024, 1, 2), 100000.00)
assert abs(result.equity_curve[-1][1] - 100423.12) < 0.01
```

For tests #2-4: similar approach with different fixtures designed to exercise the targeted regression risk.

- [ ] **Step 3: Run the regression suite — must PASS against the pre-modification engine**

```
python -m pytest tests/milodex/backtesting/test_engine_daily_regression.py -v
```

Expected: 4 passed. If anything fails here, the test or fixture is wrong — fix before moving on.

- [ ] **Step 4: Commit**

```bash
git add tests/milodex/backtesting/test_engine_daily_regression.py tests/milodex/backtesting/__init__.py
git commit -m "test(backtesting): daily regression suite — 4 cases (pre-refactor baseline)

Expected values captured from the pre-modification engine. These tests
are the safety net for the intraday PR — every subsequent engine
modification must keep them green."
```

---

## Phase C — Dispatch Skeleton (No Behavior Change Yet)

### Task C1: Add `timeframe` parameter to `prefetch_bars`

**Files:**
- Modify: `src/milodex/backtesting/engine.py:316-344` (`prefetch_bars` signature and body)

**Why this task first:** the change is structurally local and reversible. After this task, daily configs still pass `Timeframe.DAY_1`; behavior is unchanged. This is the foundation for the dispatch.

- [ ] **Step 1: Update `prefetch_bars` signature**

```python
# engine.py around line 316
def prefetch_bars(
    self,
    start_date: date,
    end_date: date,
    *,
    timeframe: Timeframe = Timeframe.DAY_1,
) -> dict[str, BarSet]:
    """Fetch bars for the universe over ``[start_date - warmup, end_date]``.

    Args:
        timeframe: bar granularity to fetch. Defaults to DAY_1 for
            backwards compatibility with daily-strategy callers. Intraday
            backtests pass MINUTE_5 / MINUTE_15 / HOUR_1 etc.
    """
    # ... existing docstring continues with the threshold resolution order ...
```

- [ ] **Step 2: Replace the hardcoded `Timeframe.DAY_1` at line 341 with the parameter**

```python
bars = self._data_provider.get_bars(
    symbols=universe,
    timeframe=timeframe,   # was: timeframe=Timeframe.DAY_1
    start=warmup_start,
    end=end_date,
)
```

- [ ] **Step 3: Update the internal caller at engine.py:499** (the walk-forward runner's prefetch call)

If it currently calls `prefetch_bars(start, end)` without a timeframe argument, leave it alone — the default DAY_1 covers the daily case. Verify by using the Grep tool on `src/milodex/backtesting/engine.py` for `prefetch_bars(` to enumerate call sites. If any internal caller passes positional args, fix it to pass keyword.

- [ ] **Step 4: Run the full backtest test suite**

```
python -m pytest tests/milodex/backtesting/ -q
```

Expected: all existing tests pass. Behavior is unchanged because the default is `Timeframe.DAY_1`.

- [ ] **Step 5: Commit**

```bash
git add src/milodex/backtesting/engine.py
git commit -m "refactor(backtesting): parameterize prefetch_bars by timeframe

Default Timeframe.DAY_1 preserves all existing daily behavior. Intraday
configs will pass MINUTE_5 etc once the dispatch is wired in Task C2."
```

### Task C2: Split `_simulate` into dispatch + `_simulate_daily`

**Files:**
- Modify: `src/milodex/backtesting/engine.py:619-870` (the `_simulate` method body)

- [ ] **Step 1: Locate `_simulate(...)` method definition**

It starts at engine.py:619. The full body extends to approximately line 870 (where the method ends).

- [ ] **Step 2: Rename the existing `_simulate(...)` method to `_simulate_daily(...)`**

Use Edit. Change the `def _simulate(` to `def _simulate_daily(`. Keep all parameters, body, and return type identical. No other changes inside the body.

- [ ] **Step 3: Add a new top-level `_simulate(...)` dispatcher above `_simulate_daily`**

```python
def _simulate(
    self,
    *,
    all_bars: dict[str, BarSet],
    trading_days: list[date],
    db_run_id: int,
    session_id: str,
    initial_equity: float,
) -> _SimulationOutput:
    """Dispatch to the daily or intraday simulation path based on tempo.bar_size."""
    bar_size = self._loaded.config.tempo["bar_size"]
    if bar_size == "1D":
        return self._simulate_daily(
            all_bars=all_bars,
            trading_days=trading_days,
            db_run_id=db_run_id,
            session_id=session_id,
            initial_equity=initial_equity,
        )
    return self._simulate_intraday(
        all_bars=all_bars,
        trading_days=trading_days,
        db_run_id=db_run_id,
        session_id=session_id,
        initial_equity=initial_equity,
    )
```

- [ ] **Step 4: Add a placeholder `_simulate_intraday` that raises**

For now, the dispatch routes non-daily configs to an unimplemented method. We'll fill it in Phase E. Placeholder:

```python
def _simulate_intraday(
    self,
    *,
    all_bars: dict[str, BarSet],
    trading_days: list[date],
    db_run_id: int,
    session_id: str,
    initial_equity: float,
) -> _SimulationOutput:
    """Intraday simulation path. Implemented in Phase E.

    See docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md.
    """
    raise NotImplementedError(
        "Intraday backtest engine is not yet implemented for this branch. "
        "See docs/superpowers/plans/2026-05-20-intraday-backtest-engine.md."
    )
```

- [ ] **Step 5: Also pass `timeframe` to `prefetch_bars` from the public entry point**

Find the entry point that calls `prefetch_bars` (likely in `run(...)` or `walk_forward(...)`). Add the timeframe derivation:

```python
from milodex.data.timeframes import timeframe_from_bar_size

# ... in the entry method ...
bar_size = self._loaded.config.tempo["bar_size"]
timeframe = timeframe_from_bar_size(bar_size)
all_bars = self.prefetch_bars(start_date, end_date, timeframe=timeframe)
```

(If the existing call sites pass no timeframe, they'll use `DAY_1` default — but daily configs should now go through the same code path that derives the timeframe from config, for consistency.)

- [ ] **Step 6: Run the full backtest test suite**

```
python -m pytest tests/milodex/backtesting/ -q
```

Expected: all tests pass. Daily configs go through `_simulate` → `_simulate_daily` (identical body to before). No intraday config exists yet that would hit `_simulate_intraday`.

- [ ] **Step 7: Commit**

```bash
git add src/milodex/backtesting/engine.py
git commit -m "refactor(backtesting): split _simulate into dispatcher + _simulate_daily

The existing _simulate body is now _simulate_daily, called via a thin
dispatch on tempo.bar_size. Intraday configs (placeholder method) raise
NotImplementedError until Phase E. Daily configs traverse identical
code, materially unchanged."
```

### Task C2-Verify: Re-run daily regression suite

After Task C2's commit, re-run the daily regression suite (captured in Phase B) to verify the dispatch refactor preserved daily behavior.

- [ ] **Run:**

```
python -m pytest tests/milodex/backtesting/test_engine_daily_regression.py -v
```

Expected: 4 passed. The dispatch refactor must not have changed any daily output value.

If anything fails: revert C1/C2, debug, retry. The regression suite IS the contract.

### Task C3: Extract `_evaluate_strategy` private method (load-bearing refactor)

**Files:**
- Modify: `src/milodex/backtesting/engine.py:_simulate_daily` (extract strategy-call logic into a new private method `_evaluate_strategy`)

**Why this is a standalone task:** the spec calls out that the `_evaluate_strategy` extraction is the highest daily-preservation risk in the PR. It needs its own commit, its own re-run of the regression suite, and to be reverted cleanly if it introduces drift.

- [ ] **Step 1: Locate the strategy.evaluate() call inside `_simulate_daily`**

It's somewhere around engine.py:720-780 (within the day loop, after the bars-by-symbol slice and before pending-order processing). Use Grep to find `strategy.evaluate(`.

- [ ] **Step 2: Identify the inputs and outputs of the call**

The call site reads from `self._loaded.strategy`, `bars_by_symbol`, a `StrategyContext` constructed from `positions`/`entry_state`/`cash`. It returns a `StrategyDecision` whose `intents` get appended to `pending`.

- [ ] **Step 3: Extract into a new private method**

```python
def _evaluate_strategy(
    self,
    *,
    bars_by_symbol_visible: dict[str, BarSet],
    cash: float,
    positions: dict[str, tuple[float, float]],
    entry_state: dict[str, dict],
) -> list[TradeIntent]:
    """Call the loaded strategy's evaluate() and return its TradeIntents.

    Shared between _simulate_daily and _simulate_intraday. Pure
    extraction — no behavior change.
    """
    # ... build StrategyContext from inputs ...
    # ... call self._loaded.strategy.evaluate(...) ...
    # ... return decision.intents ...
```

The method body is whatever the daily loop currently does inline. Move the code; do NOT change semantics.

- [ ] **Step 4: Replace the inline call in `_simulate_daily` with `self._evaluate_strategy(...)`**

- [ ] **Step 5: Run the daily regression suite — must still pass**

```
python -m pytest tests/milodex/backtesting/test_engine_daily_regression.py -v
```

Expected: 4 passed. If any test fails, the extraction was non-trivial — revert and try again with smaller scope.

- [ ] **Step 6: Run the broader backtest test suite for safety**

```
python -m pytest tests/milodex/backtesting/ -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/milodex/backtesting/engine.py
git commit -m "refactor(backtesting): extract _evaluate_strategy private method

Pure extraction of the strategy.evaluate() call site from
_simulate_daily into a shared private method, ready for _simulate_intraday
to reuse. Daily regression suite passes — no behavior change."
```

---

## Phase D — Intraday Primitives (TDD, each small)

Each task in this phase: write failing test → minimal implementation → verify pass → commit. Add code as private helpers inside `engine.py` (don't split files yet).

### Task D1: `_build_intraday_event_timeline` helper

**Files:**
- Modify: `src/milodex/backtesting/engine.py` (add helper function)
- Test: `tests/milodex/backtesting/test_engine_intraday.py` (new file — add the test for this helper as the first test)

- [ ] **Step 1: Create the test file with the first helper test**

```python
# tests/milodex/backtesting/test_engine_intraday.py
"""Intraday backtest engine correctness tests.

See docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md §5.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from milodex.backtesting.engine import _build_intraday_event_timeline
from milodex.data.models import BarSet


def test_event_timeline_for_single_symbol_5min_session() -> None:
    """For a full 9:30-16:00 ET session of 5min SPY bars, the event timeline
    is the chronological union of fill events (every bar's start) and
    decision events (every bar's completion).
    """
    # Build 78 bars: 9:30, 9:35, ..., 15:55 ET
    bars_df = _build_synthetic_5min_session("2024-01-15", ["SPY"])["SPY"].to_dataframe()

    timeline = _build_intraday_event_timeline(
        all_bars={"SPY": BarSet(bars_df)},
        day=date(2024, 1, 15),
        bar_size_minutes=5,
    )

    # Expected: 79 unique events — first event is 9:30 (pure fill event for
    # the 9:30 bar; no decision event because that's the previous session's
    # close); subsequent events are unions; last event is 16:00 (pure decision
    # event for the 15:55 bar; no fill event because there's no 16:00 bar).
    timestamps = [t for t, _meta in timeline]
    assert len(timestamps) == 79
    # First event = 9:30 ET = 14:30 UTC
    assert timestamps[0] == pd.Timestamp("2024-01-15 14:30:00+00:00")
    # Last event = 16:00 ET = 21:00 UTC
    assert timestamps[-1] == pd.Timestamp("2024-01-15 21:00:00+00:00")


def _build_synthetic_5min_session(date_str: str, symbols: list[str]) -> dict[str, BarSet]:
    """Build a full 9:30-16:00 ET session of 5min bars for each symbol.

    78 bars per symbol, deterministic OHLC.
    """
    bars: dict[str, BarSet] = {}
    open_et = pd.Timestamp(f"{date_str} 09:30:00").tz_localize("America/New_York")
    open_utc = open_et.tz_convert("UTC")
    rows: list[dict[str, Any]] = []
    for i in range(78):
        ts = open_utc + pd.Timedelta(minutes=5 * i)
        rows.append({
            "timestamp": ts,
            "open": 500.0 + i * 0.01,
            "high": 500.5 + i * 0.01,
            "low": 499.5 + i * 0.01,
            "close": 500.0 + i * 0.01,
            "volume": 1_000_000,
            "vwap": 500.0 + i * 0.01,
        })
    df = pd.DataFrame(rows)
    for symbol in symbols:
        bars[symbol] = BarSet(df.copy())
    return bars
```

- [ ] **Step 2: Run test to verify it fails**

```
python -m pytest tests/milodex/backtesting/test_engine_intraday.py::test_event_timeline_for_single_symbol_5min_session -v
```

Expected: FAIL with `ImportError: cannot import name '_build_intraday_event_timeline'`.

- [ ] **Step 3: Implement `_build_intraday_event_timeline` in engine.py**

Add as a private module-level helper (near the existing `_build_ts_date_index` at line 1328):

```python
def _build_intraday_event_timeline(
    all_bars: dict[str, BarSet],
    day: date,
    bar_size_minutes: int,
) -> list[tuple[pd.Timestamp, dict[str, Any]]]:
    """Return the chronological event timeline for one trading day.

    Each entry is ``(timestamp, metadata)`` where ``metadata`` carries:
    - ``fill_symbols``: list of symbols with a bar at ``bar_timestamp == timestamp``
    - ``decision_symbols``: list of symbols with a bar at ``decision_time == timestamp``

    The timeline is the chronological union of fill events (bar starts)
    and decision events (bar completions) for all universe symbols whose
    bars fall in ``day``. See spec §3 component #3.
    """
    bar_size = pd.Timedelta(minutes=bar_size_minutes)
    fill_map: dict[pd.Timestamp, list[str]] = {}
    decision_map: dict[pd.Timestamp, list[str]] = {}

    for symbol, barset in all_bars.items():
        df = barset.to_dataframe()
        if df.empty:
            continue
        ts_series = pd.to_datetime(df["timestamp"], utc=True)
        for bar_ts in ts_series:
            if bar_ts.date() != day:
                continue
            fill_map.setdefault(bar_ts, []).append(symbol)
            decision_ts = bar_ts + bar_size
            decision_map.setdefault(decision_ts, []).append(symbol)

    all_event_times = sorted(set(fill_map.keys()) | set(decision_map.keys()))
    return [
        (
            t,
            {
                "fill_symbols": fill_map.get(t, []),
                "decision_symbols": decision_map.get(t, []),
            },
        )
        for t in all_event_times
    ]
```

- [ ] **Step 4: Run test to verify it passes**

```
python -m pytest tests/milodex/backtesting/test_engine_intraday.py::test_event_timeline_for_single_symbol_5min_session -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/milodex/backtesting/engine.py tests/milodex/backtesting/test_engine_intraday.py
git commit -m "feat(backtesting): _build_intraday_event_timeline helper

Chronological union of fill events and decision events per day. First
piece of the intraday simulation primitives."
```

### Task D2: `_opens_at_timestamp` helper

**Files:**
- Modify: `src/milodex/backtesting/engine.py`
- Test: `tests/milodex/backtesting/test_engine_intraday.py`

- [ ] **Step 1: Add test**

```python
def test_opens_at_timestamp_returns_only_symbols_with_bar_at_t() -> None:
    """At event timestamp T, _opens_at_timestamp returns the symbol → open-price
    map ONLY for symbols whose bar_timestamp == T. Symbols absent at T are
    not in the result.
    """
    bars = _build_synthetic_5min_session("2024-01-15", ["SPY", "QQQ"])

    # Drop the 10:05 bar from QQQ to simulate a missing bar
    qqq_df = bars["QQQ"].to_dataframe()
    target_ts = pd.Timestamp("2024-01-15 15:05:00+00:00")  # 10:05 ET = 15:05 UTC
    qqq_df_drop = qqq_df[qqq_df["timestamp"] != target_ts].reset_index(drop=True)
    bars["QQQ"] = BarSet(qqq_df_drop)

    from milodex.backtesting.engine import _opens_at_timestamp

    opens = _opens_at_timestamp(bars, target_ts)

    # SPY has the 10:05 bar; QQQ doesn't
    assert "SPY" in opens
    assert "QQQ" not in opens
    # SPY's open at 10:05 = base + 7 increments (bar 7 in the 78-bar series)
    expected_spy_open = 500.0 + 7 * 0.01
    assert abs(opens["SPY"] - expected_spy_open) < 1e-9
```

- [ ] **Step 2: Run test, expect failure**

```
python -m pytest tests/milodex/backtesting/test_engine_intraday.py::test_opens_at_timestamp_returns_only_symbols_with_bar_at_t -v
```

Expected: FAIL on import.

- [ ] **Step 3: Implement `_opens_at_timestamp`**

```python
def _opens_at_timestamp(
    all_bars: dict[str, BarSet],
    timestamp: pd.Timestamp,
) -> dict[str, float]:
    """Return ``{symbol: open_price}`` for symbols with a bar starting at ``timestamp``.

    Symbols without a bar at ``timestamp`` are not in the result.
    """
    opens: dict[str, float] = {}
    for symbol, barset in all_bars.items():
        df = barset.to_dataframe()
        if df.empty:
            continue
        ts_series = pd.to_datetime(df["timestamp"], utc=True)
        match = df.loc[ts_series == timestamp]
        if not match.empty:
            opens[symbol] = float(match["open"].iloc[0])
    return opens
```

- [ ] **Step 4: Run test, expect pass**

```
python -m pytest tests/milodex/backtesting/test_engine_intraday.py::test_opens_at_timestamp_returns_only_symbols_with_bar_at_t -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/milodex/backtesting/engine.py tests/milodex/backtesting/test_engine_intraday.py
git commit -m "feat(backtesting): _opens_at_timestamp helper"
```

### Task D3: `_drain_pending_at_timestamp` helper

**Files:**
- Modify: `src/milodex/backtesting/engine.py`
- Test: `tests/milodex/backtesting/test_engine_intraday.py`

**Key semantic difference from `_drain_pending`:** an order for a symbol with no open at this timestamp REMAINS in `pending`. It is NOT skipped. Skipping happens only at backtest end if no future bar exists for the symbol.

- [ ] **Step 1: Add test**

The test verifies the semantic divergence from `_drain_pending`: missing-open symbols stay in pending instead of being counted toward `skipped_count`. Test setup uses a minimal fixture — the implementer reads engine.py's existing `_PendingOrder` dataclass (search for `_PendingOrder` in engine.py to confirm exact field names) and constructs two mock pending orders.

```python
def test_drain_pending_at_timestamp_fills_matched_keeps_unmatched() -> None:
    """Two pending orders: SPY (has open at T) and QQQ (no open at T).
    SPY fills; QQQ stays in pending. skipped_count untouched (it's a
    backtest-end concern, not a per-timestamp concern).
    """
    from milodex.broker.models import OrderSide, OrderType
    from milodex.execution.models import TradeIntent
    from milodex.backtesting.engine import (
        _drain_pending_at_timestamp,
        _PendingOrder,
    )

    # Mock pending orders: BUY 10 SPY (will fill), BUY 5 QQQ (will NOT fill)
    intent_spy = TradeIntent(
        symbol="SPY", side=OrderSide.BUY, quantity=10.0, order_type=OrderType.MARKET
    )
    intent_qqq = TradeIntent(
        symbol="QQQ", side=OrderSide.BUY, quantity=5.0, order_type=OrderType.MARKET
    )
    pending = [
        _PendingOrder(intent=intent_spy, decision_timestamp=pd.Timestamp("2024-01-15 14:55:00+00:00"), decision_session_id="sess-1"),
        _PendingOrder(intent=intent_qqq, decision_timestamp=pd.Timestamp("2024-01-15 14:55:00+00:00"), decision_session_id="sess-1"),
    ]

    # Opens at T=15:00 UTC: only SPY has an open
    opens_at_t = {"SPY": 500.50}

    # Mock the broker/exec-service infrastructure with stubs that record calls
    # and let the helper apply slippage/commission cleanly. Implementer uses
    # the same stubbing pattern that test_engine_daily_regression.py uses.
    sim_broker, sim_data_provider, execution_service, event_store = _make_test_engine_stubs()
    cash = 100_000.0
    positions: dict[str, tuple[float, float]] = {}
    entry_state: dict[str, dict] = {}
    sym_fills: dict[str, dict[str, int]] = {}

    new_cash, buys, sells, remaining = _drain_pending_at_timestamp(
        pending=pending,
        opens=opens_at_t,
        cash=cash,
        positions=positions,
        entry_state=entry_state,
        sim_broker=sim_broker,
        sim_data_provider=sim_data_provider,
        execution_service=execution_service,
        timestamp=pd.Timestamp("2024-01-15 15:00:00+00:00"),
        session_id="sess-1",
        db_run_id=1,
        sym_fills=sym_fills,
    )

    # SPY filled: cash decreased by ~500.50 * 10 + slippage + commission
    assert buys == 1
    assert sells == 0
    assert new_cash < cash
    assert "SPY" in positions
    # QQQ still pending: no fill, intent stays queued
    assert len(remaining) == 1
    assert remaining[0].intent.symbol == "QQQ"
    # CRITICAL: no skipped counter incremented — that's a backtest-end concern
    # (the helper signature has no skipped_count return value at all; this
    # invariant is enforced by the API shape itself)
```

(The implementer adds a `_make_test_engine_stubs()` test helper in the same file that returns the minimal mocked dependencies. Pattern: reuse the same stub builders already used in the daily regression suite from Task B1.)

- [ ] **Step 2-5: TDD cycle as above. Implement `_drain_pending_at_timestamp` as a generalization of `_drain_pending`.**

Key difference: when looking up a symbol's open price, if the symbol is absent from the `opens` dict, do NOT increment `skipped_count` and do NOT record a `backtest_missing_next_open` event. Instead, return the order back into the new pending list.

```python
def _drain_pending_at_timestamp(
    pending: list[_PendingOrder],
    opens: dict[str, float],
    cash: float,
    positions: dict[str, tuple[float, float]],
    entry_state: dict[str, dict],
    sim_broker: SimulatedBroker,
    sim_data_provider: SimulatedDataProvider,
    execution_service: ExecutionService,
    timestamp: pd.Timestamp,
    session_id: str,
    db_run_id: int,
    sym_fills: dict[str, dict[str, int]],
) -> tuple[float, int, int, list[_PendingOrder]]:
    """Drain pending orders that have an open price available at this timestamp.

    Returns:
        (cash, buy_count, sell_count, remaining_pending)

    Orders whose symbol is in ``opens`` fill at that open price. Orders
    whose symbol is absent from ``opens`` remain in ``remaining_pending``
    for processing at a future fill event.

    Unlike :func:`_drain_pending`, this function does NOT count missing
    opens as skipped. Skipping is a backtest-end concern, not a
    per-timestamp concern.
    """
    # Implementation follows _drain_pending's broker/execution-service wiring
    # but routes unmatched symbols back to pending instead of incrementing
    # skipped_count.
    ...
```

- [ ] **Commit:**

```bash
git commit -m "feat(backtesting): _drain_pending_at_timestamp — keeps unmatched pending"
```

### Task D4: `_mark_to_market_at_day_end` helper

**Files:**
- Modify: `src/milodex/backtesting/engine.py`
- Test: `tests/milodex/backtesting/test_engine_intraday.py`

- [ ] **TDD cycle. Helper signature:**

```python
def _mark_to_market_at_day_end(
    positions: dict[str, tuple[float, float]],
    all_bars: dict[str, BarSet],
    day: date,
    cash: float,
) -> float:
    """Return end-of-day equity = cash + sum(qty * latest_close_for_symbol_on_day).

    Uses each symbol's latest available close at or before the day's
    final timestamp. Critical for multi-symbol universes where one
    symbol may be missing the final bar of the day.
    """
    equity = cash
    for symbol, (qty, _avg_cost) in positions.items():
        df = all_bars[symbol].to_dataframe()
        ts_series = pd.to_datetime(df["timestamp"], utc=True)
        day_mask = ts_series.dt.date == day
        day_df = df.loc[day_mask]
        if day_df.empty:
            # No bars for this symbol on this day; fall back to last close before day
            prior_mask = ts_series.dt.date < day
            prior_df = df.loc[prior_mask]
            if prior_df.empty:
                continue  # no price data — equity unchanged
            latest_close = float(prior_df["close"].iloc[-1])
        else:
            latest_close = float(day_df["close"].iloc[-1])
        equity += qty * latest_close
    return equity
```

- [ ] Test that multi-symbol with one missing final bar still computes correct equity.

- [ ] **Commit:**

```bash
git commit -m "feat(backtesting): _mark_to_market_at_day_end — multi-symbol-safe"
```

### Task D5: Per-symbol cursor advancement helper

**Files:**
- Modify: `src/milodex/backtesting/engine.py`
- Test: `tests/milodex/backtesting/test_engine_intraday.py`

- [ ] **TDD cycle. Helper signature:**

```python
def _advance_cursors(
    cursors: dict[str, int],
    all_bars: dict[str, BarSet],
    timestamp: pd.Timestamp,
    bar_size_minutes: int,
) -> bool:
    """Advance ``cursors[symbol]`` for each symbol whose next-unconsumed bar
    has ``decision_time <= timestamp``. Return True if any cursor advanced.

    Cursor invariant: ``cursor[symbol]`` is the EXCLUSIVE end index of the
    symbol's visible bar history. Visible = ``df.iloc[:cursor[symbol]]``.
    """
    bar_size = pd.Timedelta(minutes=bar_size_minutes)
    advanced = False
    for symbol, barset in all_bars.items():
        df = barset.to_dataframe()
        if df.empty:
            continue
        ts_series = pd.to_datetime(df["timestamp"], utc=True)
        idx = cursors.get(symbol, 0)
        while idx < len(df):
            bar_ts = ts_series.iloc[idx]
            decision_time = bar_ts + bar_size
            if decision_time <= timestamp:
                idx += 1
                advanced = True
            else:
                break
        cursors[symbol] = idx
    return advanced
```

- [ ] **Tests:**

```python
def test_advance_cursors_includes_bar_with_decision_time_eq_T() -> None:
    """At T = 10:00 ET = 15:00 UTC for 5min bars, the 9:55 bar (decision_time
    14:55 + 5min = 15:00 UTC) IS visible after advancement. Cursor advances
    from 0 to 6 (six bars: 9:30, 9:35, 9:40, 9:45, 9:50, 9:55).
    """
    bars = _build_synthetic_5min_session("2024-01-15", ["SPY"])
    cursors = {"SPY": 0}
    target = pd.Timestamp("2024-01-15 15:00:00+00:00")  # 10:00 ET = decision_time of 9:55 bar

    advanced = _advance_cursors(cursors, bars, target, bar_size_minutes=5)

    assert advanced is True
    assert cursors["SPY"] == 6  # 6 bars whose decision_time <= 15:00 UTC


def test_advance_cursors_excludes_bar_with_decision_time_gt_T() -> None:
    """At T = 15:00 UTC, the 10:00 bar (decision_time 15:05 UTC) MUST NOT
    be included. Cursor advances to exactly 6, not 7.
    """
    bars = _build_synthetic_5min_session("2024-01-15", ["SPY"])
    cursors = {"SPY": 0}
    advanced = _advance_cursors(
        cursors, bars, pd.Timestamp("2024-01-15 15:00:00+00:00"), bar_size_minutes=5
    )
    assert cursors["SPY"] == 6  # exclusive-end: bars 0..5 visible (i.e. 9:30..9:55)
    # The 10:00 bar (index 6) has decision_time 15:05 UTC > 15:00 UTC; NOT visible
    visible = bars["SPY"].to_dataframe().iloc[: cursors["SPY"]]
    assert len(visible) == 6
    assert pd.to_datetime(visible["timestamp"].iloc[-1], utc=True) == pd.Timestamp("2024-01-15 14:55:00+00:00")


def test_advance_cursors_multiple_symbols_independent() -> None:
    """Symbols advance independently. Drop the 9:30 bar from QQQ; at T=15:00
    UTC, SPY advances to 6 (full opening 30 min), QQQ advances to 5.
    """
    bars = _build_synthetic_5min_session("2024-01-15", ["SPY", "QQQ"])
    qqq_df = bars["QQQ"].to_dataframe().iloc[1:].reset_index(drop=True)
    bars["QQQ"] = BarSet(qqq_df)
    cursors = {"SPY": 0, "QQQ": 0}

    _advance_cursors(cursors, bars, pd.Timestamp("2024-01-15 15:00:00+00:00"), bar_size_minutes=5)

    assert cursors["SPY"] == 6
    assert cursors["QQQ"] == 5  # missing 9:30 bar


def test_advance_cursors_initial_zero_means_no_history() -> None:
    """At simulation start, cursors[symbol] = 0 means iloc[:0] is empty —
    no bars visible to the strategy.
    """
    bars = _build_synthetic_5min_session("2024-01-15", ["SPY"])
    cursors = {"SPY": 0}
    visible_before = bars["SPY"].to_dataframe().iloc[: cursors["SPY"]]
    assert visible_before.empty
```

- [ ] **Commit:**

```bash
git commit -m "feat(backtesting): _advance_cursors — exclusive-end-index invariant"
```

---

## Phase E — `_simulate_intraday` Implementation

### Task E1: Wire up `_simulate_intraday` with the helpers

**Files:**
- Modify: `src/milodex/backtesting/engine.py:_simulate_intraday` (replace `raise NotImplementedError`)

- [ ] **Step 1: Replace the placeholder body**

```python
def _simulate_intraday(
    self,
    *,
    all_bars: dict[str, BarSet],
    trading_days: list[date],
    db_run_id: int,
    session_id: str,
    initial_equity: float,
) -> _SimulationOutput:
    """Intraday simulation: day → event timestamp → market-slice.

    See spec §2 for the strict ordering at each event timestamp:
        1. Drain pending at T if T is a fill event
        2. Advance cursors at T if T is a decision event
        3. Evaluate strategy ONLY if cursors advanced

    Cursors persist across days. Pending orders persist across days.
    Daily equity samples at day end via _mark_to_market_at_day_end.
    """
    universe = list(self._loaded.context.universe)
    if not universe:
        msg = "Strategy must resolve a non-empty universe before backtesting."
        raise ValueError(msg)
    if not trading_days:
        return _SimulationOutput(
            equity_curve=[],
            trade_count=0,
            buy_count=0,
            sell_count=0,
            final_equity=initial_equity,
            round_trip_count=0,
            skipped_count=0,
        )

    bar_size_str = self._loaded.config.tempo["bar_size"]
    bar_size_minutes = _bar_size_to_minutes(bar_size_str)  # NEW HELPER — add inline

    sim_broker = SimulatedBroker(
        slippage_pct=self._slippage_pct,
        commission_per_trade=self._commission,
    )
    sim_data_provider = SimulatedDataProvider(all_bars)
    execution_service = ExecutionService(
        broker_client=sim_broker,
        data_provider=sim_data_provider,
        kill_switch_store=KillSwitchStateStore(event_store=self._event_store),
        risk_defaults_path=self._risk_defaults_path,
        risk_evaluator=self._build_risk_evaluator(),
        event_store=self._event_store,
        is_backtest=True,
    )

    cash = initial_equity
    positions: dict[str, tuple[float, float]] = {}
    entry_state: dict[str, dict] = {}
    cursors: dict[str, int] = {symbol: 0 for symbol in universe}

    equity_curve: list[tuple[date, float]] = []
    buy_count = 0
    sell_count = 0
    trade_count = 0
    sym_fills: dict[str, dict[str, int]] = {}
    pending: list[_PendingOrder] = []

    for day in trading_days:
        # Held-days accounting (same as daily path)
        for sym in entry_state:
            entry_state[sym]["held_days"] = int(entry_state[sym]["held_days"]) + 1

        timeline = _build_intraday_event_timeline(
            all_bars=all_bars,
            day=day,
            bar_size_minutes=bar_size_minutes,
        )

        for ts, meta in timeline:
            # 1. Drain pending at T if T is a fill event for any symbol
            if meta["fill_symbols"]:
                opens = _opens_at_timestamp(all_bars, ts)
                cash, drained_buys, drained_sells, pending = _drain_pending_at_timestamp(
                    pending=pending,
                    opens=opens,
                    cash=cash,
                    positions=positions,
                    entry_state=entry_state,
                    sim_broker=sim_broker,
                    sim_data_provider=sim_data_provider,
                    execution_service=execution_service,
                    timestamp=ts,
                    session_id=session_id,
                    db_run_id=db_run_id,
                    sym_fills=sym_fills,
                )
                buy_count += drained_buys
                sell_count += drained_sells
                trade_count += drained_buys + drained_sells

            # 2. Advance cursors if T is a decision event for any symbol
            advanced = _advance_cursors(
                cursors=cursors,
                all_bars=all_bars,
                timestamp=ts,
                bar_size_minutes=bar_size_minutes,
            )

            # 3. Evaluate strategy ONLY if cursors advanced
            if not advanced:
                continue

            bars_by_symbol_visible = {
                symbol: BarSet(
                    all_bars[symbol].to_dataframe().iloc[: cursors[symbol]]
                )
                for symbol in universe
                if cursors.get(symbol, 0) > 0
            }
            new_intents = self._evaluate_strategy(
                bars_by_symbol_visible=bars_by_symbol_visible,
                cash=cash,
                positions=positions,
                entry_state=entry_state,
            )  # NEW PRIVATE METHOD — extracts strategy-call logic
            for intent in new_intents:
                pending.append(_PendingOrder(
                    intent=intent,
                    decision_timestamp=ts,
                    decision_session_id=session_id,
                ))

        # Day end: mark-to-market and append equity sample
        eod_equity = _mark_to_market_at_day_end(
            positions=positions,
            all_bars=all_bars,
            day=day,
            cash=cash,
        )
        equity_curve.append((day, eod_equity))

    # End of all days: stranded pending orders → skipped_count
    skipped_count = len(pending)

    round_trip_count = sum(
        min(s["buys"], s["sells"]) for s in sym_fills.values()
    )

    return _SimulationOutput(
        equity_curve=equity_curve,
        trade_count=trade_count,
        buy_count=buy_count,
        sell_count=sell_count,
        final_equity=eod_equity if equity_curve else initial_equity,
        round_trip_count=round_trip_count,
        skipped_count=skipped_count,
    )
```

- [ ] **Step 2: Add the `_bar_size_to_minutes` helper if not present**

```python
def _bar_size_to_minutes(bar_size: str) -> int:
    mapping = {"1Min": 1, "5Min": 5, "15Min": 15, "1H": 60, "1D": 1440}
    return mapping[bar_size]
```

- [ ] **Step 3: Confirm `_evaluate_strategy` is already extracted**

This extraction already happened in Task C3. `_simulate_intraday` calls `self._evaluate_strategy(...)` — same method `_simulate_daily` now uses. No new refactor here.

- [ ] **Step 4: Run the daily regression suite — MUST still pass**

```
python -m pytest tests/milodex/backtesting/test_engine_daily_regression.py -v
```

Expected: 4 passed. Adding `_simulate_intraday` must not affect daily behavior because the dispatch routes `bar_size == "1D"` configs to `_simulate_daily` unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/milodex/backtesting/engine.py
git commit -m "feat(backtesting): _simulate_intraday implementation

Day → event-timestamp → strict-ordering loop. Reuses _evaluate_strategy
extracted in Task C3. Daily regression suite still passes. Intraday
correctness tests follow in Phase F."
```

---

## Phase F — Intraday Correctness Tests (10 tests from spec §5)

### Task F1: Add the 10 intraday correctness tests

**Files:**
- Modify: `tests/milodex/backtesting/test_engine_intraday.py`

Add the 10 tests from spec §5 in order. Each follows the TDD pattern: write test, expect pass (the helpers and `_simulate_intraday` already exist), commit individually.

**Tests to add (numbered to match the spec):**

- [ ] **Test 1: Timeframe dispatch test.** Verify a config with `tempo.bar_size = "5Min"` causes `prefetch_bars` to receive `Timeframe.MIN_5`, not `DAY_1`.

- [ ] **Test 2: Intraday smoke benchmark — exact counts.** Run `benchmark.unconditional_intraday_long.spy.v1` against 4 days of synthetic 5min SPY bars. Assert: `buy_count == 4, sell_count == 3, trade_count == 7, round_trip_count == 3, skipped_count == 1`.

- [ ] **Test 3: No same-bar fill.** Strategy fires BUY based on the 10:00 bar; fill is at the 10:10 bar's open, NOT 10:00 or 10:05.

- [ ] **Test 4: No-lookahead, both directions.** Match the spec §5 #4 example exactly: at iteration step T = 10:00 ET (= 15:00 UTC), the bar with `decision_time == T` (bar_timestamp 9:55, whose decision_time = 9:55 + 5min = 10:00) IS visible in the strategy's history. The bar with `decision_time > T` (bar_timestamp 10:00, whose decision_time = 10:05) is NOT visible. Both directions asserted — protects against an implementer "fixing" lookahead by under-shooting.

- [ ] **Test 5: Multi-symbol visible history with missing current bar.** Symbol A has 10:05 bar, symbol B doesn't. At T=10:05: A in `opens_at_timestamp`, B not in opens, B's history through prior bar IS visible.

- [ ] **Test 6: Pending order survives missing current bar.** Pending BUY for symbol B from 10:00 decision; B missing at 10:05; B present at 10:10. At T=10:05: order remains pending, `skipped_count` does NOT increment. At T=10:10: fills.

- [ ] **Test 7: Independent cursor advancement.** Cursors exclusive-end; symbols advance independently; cursors persist across timestamps and across day boundaries.

- [ ] **Test 8: Session-boundary pending fill.** SELL at decision event T=16:00 fills at next-session 9:30 open with slippage.

- [ ] **Test 9: Stranded pending at backtest end.** Order with no future bar is counted in `skipped_count` only after the final timestamp.

- [ ] **Test 10: Walk-forward isolation.** Window 2's state derived only from window 2's own fetched/warmup bars.

- [ ] **Per test, after writing it, run:**

```
python -m pytest tests/milodex/backtesting/test_engine_intraday.py::test_N -v
```

Expect pass. Commit each test individually with a focused message.

### Task F2: Re-run daily regression after intraday lands

**Why a standalone checkpoint:** the daily regression suite was last validated in Task C3 (after `_evaluate_strategy` extraction). Phase D added intraday primitives and Phase E wired `_simulate_intraday`. None of those should affect daily — but verifying explicitly is the contract.

- [ ] **Run:**

```
python -m pytest tests/milodex/backtesting/test_engine_daily_regression.py -v
```

Expected: 4 passed.

If any test fails here, an intraday-side change leaked into the daily path. Bisect to find the offending commit and fix or revert.

No commit needed — this is a verification checkpoint only. If green, proceed to Phase G.

---

## Phase G — Smoke Validation

### Task G1: Run ORB + benchmark configs against the new engine

**Files:** No code changes. Operator validation.

- [ ] **Step 1: Run the ORB backtest over a short window**

```
python -m milodex.cli.main backtest breakout.orb.intraday.spy.v1 --start 2024-01-02 --end 2024-01-08
```

Expected: non-zero `trade_count`. Specifically, ~4-6 trades over 4 sessions if breakout-rate is ~50%. NO MORE silent zero.

- [ ] **Step 2: Run the benchmark backtest over the same window**

```
python -m milodex.cli.main backtest benchmark.unconditional_intraday_long.spy.v1 --start 2024-01-02 --end 2024-01-08
```

Expected: ~3-4 round trips (depending on exact session count + the documented stranded final exit).

- [ ] **Step 3: Sanity-check Sharpe and drawdown make geometric sense**

For a short 4-session window, Sharpe is statistically noisy — don't over-interpret. The validation here is "does the engine produce sensible numbers" not "is the strategy profitable."

- [ ] **Step 4: No commit needed** — operator-level validation.

---

## Phase H — Final Validation

### Task H1: Full test suite + lint + diff review

- [ ] **Step 1: Run the full test suite**

```
python -m pytest -q -m "not flaky_qt_pollution"
```

Expected: previous count + new tests, all green. Pre-PR count was 1873; this PR adds ~14 new tests (4 daily regression, 10 intraday), so expected new count ~1887.

- [ ] **Step 2: Lint**

```
python -m ruff check src/milodex/backtesting/ src/milodex/data/timeframes.py tests/milodex/backtesting/ tests/milodex/data/
python -m ruff format src/milodex/backtesting/ src/milodex/data/timeframes.py tests/milodex/backtesting/ tests/milodex/data/
```

Expected: clean (no issues, or auto-format applied).

- [ ] **Step 3: Diff review — make sure no unintended changes**

```
git diff master..HEAD --stat
git diff master..HEAD src/milodex/backtesting/engine.py
```

Confirm:
- `_simulate_daily` body is byte-identical to the pre-PR `_simulate` body (renamed only)
- No conditionals inside `_simulate_daily` reading `bar_size` (intraday concerns should not leak)
- `prefetch_bars` signature change is additive (timeframe defaults to DAY_1)

- [ ] **Step 4: Manual operator step — run the actual ORB and benchmark backtests over the 2022-2025 window**

```
python -m milodex.cli.main backtest breakout.orb.intraday.spy.v1 --start 2022-01-01 --end 2025-12-31 --walk-forward
python -m milodex.cli.main backtest benchmark.unconditional_intraday_long.spy.v1 --start 2022-01-01 --end 2025-12-31 --walk-forward
```

Expected: ORB produces 150-250 trades; benchmark produces ~750-1000 round trips (one per session, with the final-session strand and one strand per walk-forward window boundary). Sharpe comparison against benchmark is the operator-level promotion check per spec.

This step is NOT a CI test. It's a manual operator-driven validation requiring real Alpaca data and 5-15 minutes of fetching + simulation.

- [ ] **Step 5: Final commit if any post-validation tweaks**

If H1 reveals issues, fix them and commit. If clean, no commit needed.

---

## Out of Scope (Confirmed)

Per spec — DO NOT include in this PR:
- Per-strategy promotion gate override in `policy.py`
- Regime-stratified Sharpe reporting
- Engine-native benchmark comparison (sibling-config-as-benchmark stays)
- Schema validator for `commission_per_trade: 0.0` on non-daily bars
- Per-bar (vs daily) equity sampling
- Intraday data-completeness validation
- New time-in-force order types
- Live-runner intraday changes
- End-timestamped provider normalization

If any of these creep in during implementation, push back.

---

## DRY / YAGNI Reminders

- Don't refactor `_simulate_daily` beyond extracting `_evaluate_strategy`. Daily-preservation guarantee depends on minimal touch.
- Don't add new public surface — all new helpers are private (`_build_intraday_event_timeline`, `_opens_at_timestamp`, `_drain_pending_at_timestamp`, `_advance_cursors`, `_mark_to_market_at_day_end`).
- Don't introduce abstract base classes or strategy patterns. Single class, two paths, internal dispatch — exactly what the spec says.
- Don't add tracing, logging, or instrumentation beyond what the existing engine has.

## Size estimate

Small-medium PR. ~400 lines of new code in engine.py (helpers + `_simulate_intraday`), ~50 lines for `data/timeframes.py`, ~400 lines of new tests across two test files, ~5 lines of runner.py modification. Plus the existing strategy code on the branch is unchanged.

Two focused sittings or one long sitting.
