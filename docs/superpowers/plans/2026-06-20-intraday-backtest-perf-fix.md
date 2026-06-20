# Intraday Backtest Perf Fix Implementation Plan

> **For agentic workers:** implemented by a fresh subagent under `superpowers:test-driven-development` +
> ponytail. Fix A touches the event-store audit seam → `risk-invariant-reviewer` mandatory. Steps use
> checkbox syntax.

**Goal:** Cut the intraday backtest from ~24 min to ~6–9 min (profiled, measured — not O(n²), but O(n) with a
huge per-bar constant). Two independent, additive fixes; no change to fill semantics, evidence, or the LIVE
audit trail.

**Profiled causes (cProfile, SPY 3-month candidate run, ~47ms/bar):**
- **Cause A (~50%):** the backtest writes a `record_no_action` explanation PER BAR; each
  `EventStore.append_explanation` opens a fresh connection + 3 PRAGMAs + INSERT + **`commit()`** (an fsync).
  ~6,800 fsyncs/backtest.
- **Cause B (~24%):** `session_bars_et` (`_session_intraday.py:158`) uses the pandas `.date` accessor, which
  materializes Python `date` objects element-by-element; called 3× per bar over the whole visible window.

**Architecture:** Fix B first (localized, lower-risk idiom swap in a hot-path session helper). Fix A second
(a backtest-scoped `EventStore.batched()` context manager — the LIVE/paper runner keeps per-decision commits).

**Tech Stack:** Python 3.11, pytest (`-n0`), ruff. Run with `PYTHONPATH=<worktree>/src`; backtest timing needs
`MILODEX_CACHE_DIR=C:\Users\zdm80\Milodex\market_cache` + the main `.env`. Branch baseline: full suite green
(`2937 passed`-ish after Task-4 — confirm at start).

**Hard constraints (from the grounding, verified):**
- **Fix A MUST be backtest-scoped.** `append_explanation` is SHARED by live (`runner.py:687`) and backtest
  (`simulation_kernel.py:223` → `execution/service.py:515`). A global change regresses live per-decision
  durability. Scope via an opt-in CM the BACKTEST enters and live never does.
- **Do NOT skip persistence.** Test pins require the rows present + parented: `test_simulation_kernel.py:345-350`
  (no_action count==1, direct-kernel path), `test_engine.py:310-316` + `test_walk_forward_runner.py:223-228`
  (backtest_engine rows parented after a full run), `test_engine_gate_bias.py:664-675` (symbol/close content).
  Batch = deferred flush, same rows/content/count — NOT skip.
- **Preserve the dual-ancestor check** (`_require_explanation_ancestor`) per row inside the buffered path.

---

## PR-PERF: intraday backtest perf (risk-invariant-reviewer on Fix A + thermonuclear-adjacent)

### Task 1 — Fix B: vectorized session-date mask (TDD)

**File:** `src/milodex/strategies/_session_intraday.py:155-159`. Test: `tests/milodex/strategies/test_session_intraday.py`.

Current:
```python
et = pd.DatetimeIndex(df["timestamp"]).tz_convert(ET_TZ)
mask = pd.Series(et.date == session_date, index=df.index)
```
Replace the `.date` accessor with an int-array component comparison (identical mask, no per-element Python
`date` allocation). RTH 9:30–16:00 ET → no midnight crossing, so this is bit-identical:
```python
et = pd.DatetimeIndex(df["timestamp"]).tz_convert(ET_TZ)
day_match = (et.year == session_date.year) & (et.month == session_date.month) & (et.day == session_date.day)
mask = pd.Series(day_match, index=df.index)
```

- [ ] **Step 1 — Add a test** asserting `session_bars_et` returns the IDENTICAL rows the old `.date`
  comparison did for a multi-day df spanning a DST boundary (e.g. bars on 2024-03-08 and 2024-03-11 around
  the spring-forward), proving the int-array form matches. (The existing `test_session_bars_et_filters_to_one_day:181`,
  `test_opening_range_bars*:200,216` already pin the core behavior — keep them green.)
- [ ] **Step 2 — Run those tests, expect PASS pre-change** (they pin behavior, not implementation).
- [ ] **Step 3 — Apply the swap.**
- [ ] **Step 4 — Run `tests/milodex/strategies/test_session_intraday.py` + the intraday strategy tests** (rsi2,
  vwap, the new random-matched) — all green (the mask is identical).
- [ ] **Step 5 — Commit:** `perf(strategies): vectorize session-date mask in session_bars_et (drop .date accessor)`

### Task 2 — Fix A: backtest-scoped explanation batching (TDD + risk-invariant-reviewer)

**Files:** `src/milodex/core/event_store.py` (add `batched()` CM near `append_explanation:408`, reuse
`_insert_explanation:448`), `src/milodex/backtesting/engine.py` (wrap the `_simulate_*` dispatch in `run()`).
Test: `tests/milodex/core/test_event_store.py` + the intraday engine/walk-forward tests.

**Design:**
- `EventStore.batched()` — a `@contextmanager` that opens ONE connection (with the same
  `busy_timeout`/WAL/`foreign_keys` PRAGMAs as `_connect`), sets an instance flag/holds the connection so that
  `append_explanation` (and `append_explanation_and_trade` if it's on the hot path) route their INSERT through
  `_insert_explanation(held_conn, event)` **without a per-row commit**, and **commits once in `finally`** on
  exit. Commit-in-`finally` means a clean run flushes all rows AND a mid-sim exception flushes the rows written
  so far — replicating today's per-bar-commit durability (today, rows up to a failure already persist). Keep
  `_require_explanation_ancestor(event)` per row inside the buffered path (same validation).
  - Re-entrancy: nested `batched()` is a no-op inner (the outermost owns the connection + commit). Guard it.
  - When NOT in `batched()` mode, `append_explanation` behaves exactly as today (fresh connect + commit) — so
    the LIVE runner and the direct-kernel tests (`test_simulation_kernel.py`, which call
    `simulate_decision_step` outside `engine.run()`) keep per-call commits and their `count==1` pin stays
    immediately visible.
- **Hook:** in `engine.run()`, wrap the `_simulate_daily`/`_simulate_intraday` dispatch (after
  `append_backtest_run`, ~`engine.py:375` and the parallel path ~`:533`) with `with self._event_store.batched():`.
  The backtest run row itself + trades flush within (or alongside) the same batch; ensure the final result read
  (`list_trades_for_backtest_run`, `list_explanations`) happens AFTER the CM exits (so the commit is visible).

- [ ] **Step 1 — Failing tests** (`test_event_store.py`):
  ```python
  def test_batched_defers_commit_then_flushes_on_exit():
      # inside `with store.batched():` append N explanations; assert a SECOND connection sees 0 rows
      # mid-context (deferred), then all N after the context exits (flushed once).
  def test_batched_flushes_partial_on_exception():
      # append 2, then raise inside the context; assert the 2 are persisted (commit-in-finally), exception propagates.
  def test_outside_batched_commits_per_call_unchanged():
      # append outside batched() -> immediately visible to a second connection (live path unchanged).
  def test_nested_batched_is_noop_inner():
      # nested with-blocks: inner exit does NOT commit/close; outer exit does.
  ```
- [ ] **Step 2 — Run, expect FAIL** (`batched` missing).
- [ ] **Step 3 — Implement `batched()`** per the design; route `append_explanation` through it when active.
- [ ] **Step 4 — Run the new tests + the SHARED-PATH pins:** `test_simulation_kernel.py` (no_action count==1
  via the unbuffered direct path), `test_engine.py` (backtest_engine rows parented after run; the
  early-failure `== []` cases at :354,:420), `test_walk_forward_runner.py:223-228`, `test_engine_gate_bias.py:664`,
  `test_engine_risk_policy.py`. ALL green — same rows, same content, same counts.
- [ ] **Step 5 — Hook into `engine.run()`** + run the intraday engine + walk-forward suites green.
- [ ] **Step 6 — Commit:** `perf(core): backtest-scoped EventStore.batched() to defer per-bar explanation commits`

### Task 3 — Timing verification (the proof)

- [ ] Time a single candidate intraday backtest (`meanrev.rsi2.intraday.spy.v1`, full
  `[2023-01-01, 2026-06-18]`) BEFORE (on master/pre-fix tip) and AFTER both fixes, with the warm cache.
  Report wall-clock both ways. Target: ~24 min → ~6–9 min. Capture the number (operator rule #6 — verified).
- [ ] Full suite green (`python -m pytest -q`): same pass count + the new perf tests, 1 skipped, 4 xfailed.

**Review:** TDD (above) + `/ponytail-review` + **`risk-invariant-reviewer`** (Fix A is the event-store/audit
seam — confirm: live path untouched, backtest-scoped, dual-ancestor check preserved, rows/content/count
identical, exception flushes partial like today) + the timing verification. Full suite green.

## Self-review (orchestrator)

- Both profiled causes addressed; Fix A scoped backtest-only (live `append_explanation` path unchanged);
  skip-persistence ruled out (test pins require rows); commit-in-`finally` replicates today's partial-failure
  durability; Fix B is a bit-identical mask swap. ✅
- Reuses existing primitives (`_insert_explanation`, the `_connect` PRAGMAs) — no new INSERT path invented. ✅
- Risk: a mid-context exception must still propagate (don't swallow) — `finally` commits, `raise` re-raises. ✅
