# Runner In-Progress Bar Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent `StrategyRunner` from marking today's in-progress 1D bar as "processed," which currently causes it to skip the real post-close evaluation forever when the runner is started during market hours.

**Architecture:** Guard `run_cycle` with `broker.is_market_open()`. When the market is open, skip the cycle entirely without advancing `_last_processed_bar_at`. When the market is closed, behave exactly as today — fetch bars, evaluate, record the bar timestamp. This lets the runner wait for a finalized bar rather than locking onto an in-progress one.

**Tech Stack:** Python 3.11, pytest, existing `BrokerClient.is_market_open()` interface (already implemented on SimulatedBroker, AlpacaBrokerClient, and the StubBroker in tests).

---

## Background

### The bug (discovered 2026-04-23 ~17:14 ET)

`StrategyRunner.run_cycle` at `src/milodex/strategies/runner.py:86-116` uses this guard:

```python
already_seen = (
    self._last_processed_bar_at is not None
    and latest_bar.timestamp <= self._last_processed_bar_at
)
```

For 1D bars, Alpaca returns today's *in-progress* bar during market hours with the same `timestamp` (start-of-day UTC) that the bar will carry once finalized post-close. If the runner runs a cycle while the market is still open, it:

1. Fetches today's in-progress bar, timestamp = `2026-04-23T00:00:00Z`.
2. Evaluates the strategy (often returns `no_action` because the data isn't what the strategy expects at mid-day).
3. Records `_last_processed_bar_at = 2026-04-23T00:00:00Z`.
4. After 4pm ET, Alpaca finalizes the bar with the *same timestamp*.
5. `latest_bar.timestamp <= self._last_processed_bar_at` → `True` → skipped forever.

The runner sits idle until a new-day bar appears (next trading day). No real post-close evaluation ever happens.

### Why the `broker.is_market_open()` guard works

- All three `BrokerClient` implementations expose `is_market_open()` (SimulatedBroker, AlpacaBrokerClient, the StubBroker in tests).
- If the market is open, any 1D bar returned covers a period that is not yet complete — processing it is always wrong for a daily-close strategy.
- If the market is closed (pre-open, post-close, weekend, holiday), the latest 1D bar is either fully final (post-close of a previous trading day) or not yet published (pre-open today). The existing `_last_processed_bar_at` guard handles both.
- Skipping the cycle *without* recording `_last_processed_bar_at` means the next cycle after the close will re-fetch, see the finalized bar, and process it normally.

### Why not Option 1 (track `is_final` on the Bar model)

Would require threading a finality flag through `Bar`, `BarSet`, the cache layer, the Alpaca provider, and the simulated provider. Alpaca's API does not clearly expose a "this daily bar is final" flag; we'd synthesize it from market-hours anyway. Net: more surface for no additional correctness.

### Why not Option 2 (use close-time instead of start-of-day)

Doesn't solve the problem. Today's in-progress bar and today's finalized bar still share the same close-time (e.g., `2026-04-23T21:00:00Z` for US equities). The `<=` check still collapses them.

### Scope

- Minimal, in-place fix inside `run_cycle`. No API changes.
- One new regression test. One small operations docs note.
- Does not address streaming / intraday bar handling — out of scope until intraday work begins (see `docs/ROADMAP_PHASE1.md`).

---

## File Inventory

- **Modify:** `src/milodex/strategies/runner.py` — add market-open guard at the top of `run_cycle`.
- **Modify:** `tests/milodex/strategies/test_runner.py` — add regression test + update `StubBroker` to allow overriding `is_market_open` return value.
- **Modify:** `docs/OPERATIONS.md` — one-line note under the runtime ops section explaining the "runner skips cycles during market hours" behavior.

No new files. No ADR (the event-store semantics are unchanged; this is a runtime-behavior tweak).

---

## Task 1: Make `StubBroker.is_market_open()` configurable

The current stub hardcodes `return True`. The regression test needs to toggle it. We change the stub (not production code) to accept a flag, defaulting to `True` to preserve every existing test's behavior.

**Files:**
- Modify: `tests/milodex/strategies/test_runner.py:27-91` (StubBroker class)

- [ ] **Step 1: Add `market_open` field to StubBroker**

Replace the `__init__` signature and body (lines ~30-41) with:

```python
def __init__(
    self,
    *,
    account: AccountInfo,
    positions: list[Position] | None = None,
    orders: list[Order] | None = None,
    market_open: bool = True,
) -> None:
    self.account = account
    self.positions = positions or []
    self.orders = orders or []
    self.submit_calls: list[dict[str, object]] = []
    self.cancel_all_orders_calls = 0
    self._market_open = market_open
```

Replace the `is_market_open` method (lines ~58-59) with:

```python
def is_market_open(self) -> bool:
    return self._market_open
```

- [ ] **Step 2: Run the full runner test suite to confirm nothing regressed**

Run: `pytest tests/milodex/strategies/test_runner.py -v`
Expected: all existing tests still pass (default `market_open=True` preserves old behavior).

- [ ] **Step 3: Commit**

```bash
git add tests/milodex/strategies/test_runner.py
git commit -m "test(runner): make StubBroker.is_market_open configurable

Prep for the in-progress-bar regression test in the next commit. Existing
tests default to market_open=True which matches the prior hardcoded value."
```

---

## Task 2: Write the failing regression test

This is the test that proves the bug. It runs two cycles with the same 1D-bar timestamp: once with `market_open=True` (in-progress), once with `market_open=False` (finalized). After the fix, the second cycle must evaluate the strategy.

**Files:**
- Modify: `tests/milodex/strategies/test_runner.py` (append new test at end of file)

- [ ] **Step 1: Write the failing test**

Append to `tests/milodex/strategies/test_runner.py`:

```python
# ---------------------------------------------------------------------------
# Regression: in-progress bar must not poison _last_processed_bar_at
# ---------------------------------------------------------------------------


def test_runner_skips_in_progress_bar_and_evaluates_finalized_bar(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A cycle run during market hours must not record the in-progress bar.

    Regression for the 2026-04-23 bug: previously, a mid-day cycle marked
    today's in-progress bar as seen, which caused every later cycle to skip
    the same-timestamp finalized bar forever. After the fix the runner
    should:
      1. Skip the cycle while the market is open (no broker submit, no
         explanation event, no `_last_processed_bar_at` update).
      2. Evaluate the strategy once the market closes on the very same
         bar timestamp.
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        market_open=True,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )

    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, strategy_config_dir / "regime_runner.yaml")

    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    # --- Cycle 1: market is open, bar is in-progress -------------------------
    results_during_market = runner.run_cycle()

    assert results_during_market == []
    assert runner._last_processed_bar_at is None, (
        "In-progress bar must not advance _last_processed_bar_at; "
        "doing so poisons later cycles against the same-timestamp "
        "finalized bar."
    )
    assert broker.submit_calls == []
    assert event_store.list_explanations() == []

    # --- Cycle 2: market closed, same bar is now final ------------------------
    broker._market_open = False
    results_post_close = runner.run_cycle()

    assert len(results_post_close) == 1, (
        "Once the market closes on the same-day bar, the runner must "
        "evaluate it instead of skipping forever."
    )
    assert broker.submit_calls[0]["symbol"] == "SHY"
    assert runner._last_processed_bar_at is not None
```

- [ ] **Step 2: Run the test to verify it FAILS**

Run: `pytest tests/milodex/strategies/test_runner.py::test_runner_skips_in_progress_bar_and_evaluates_finalized_bar -v`

Expected: FAIL. The first-cycle assertions will fail because the current runner processes the in-progress bar and records `_last_processed_bar_at`. Specifically, either `results_during_market == []` fails (runner fires SHY during market hours) or `runner._last_processed_bar_at is None` fails.

- [ ] **Step 3: Do NOT commit a failing test** — proceed directly to Task 3.

---

## Task 3: Implement the fix

**Files:**
- Modify: `src/milodex/strategies/runner.py:86-116` (the `run_cycle` method)

- [ ] **Step 1: Add the market-open guard at the top of `run_cycle`**

In `src/milodex/strategies/runner.py`, update the `run_cycle` method. Insert the guard as the first operation — before any data fetch, broker call, or strategy evaluation — so the skip is cheap.

Replace the existing `run_cycle` method:

```python
    def run_cycle(self) -> list[ExecutionResult]:
        """Process one new daily close when available."""
        bars_by_symbol = self._fetch_bars_by_symbol()
        primary_bars = bars_by_symbol[self._evaluation_symbol()]
        latest_bar = primary_bars.latest()
        already_seen = (
            self._last_processed_bar_at is not None
            and latest_bar.timestamp <= self._last_processed_bar_at
        )
        if already_seen:
            return []
```

with:

```python
    def run_cycle(self) -> list[ExecutionResult]:
        """Process one new daily close when available.

        Skips the cycle entirely while the market is open: a 1D bar returned
        during market hours is still in-progress and its timestamp is identical
        to the post-close finalized bar, so processing it here would poison
        ``_last_processed_bar_at`` against the real close evaluation.
        """
        if self._broker.is_market_open():
            return []

        bars_by_symbol = self._fetch_bars_by_symbol()
        primary_bars = bars_by_symbol[self._evaluation_symbol()]
        latest_bar = primary_bars.latest()
        already_seen = (
            self._last_processed_bar_at is not None
            and latest_bar.timestamp <= self._last_processed_bar_at
        )
        if already_seen:
            return []
```

Note the docstring update is the *only* comment added — it documents *why* (non-obvious invariant), not *what*, per CLAUDE.md comment guidance.

- [ ] **Step 2: Run the regression test to verify it PASSES**

Run: `pytest tests/milodex/strategies/test_runner.py::test_runner_skips_in_progress_bar_and_evaluates_finalized_bar -v`
Expected: PASS.

- [ ] **Step 3: Run the full runner test suite**

Run: `pytest tests/milodex/strategies/test_runner.py -v`
Expected: all tests pass. In particular, the three SIGINT dialog tests still pass — they use the default `market_open=True` stub, but their fake `time.sleep` fires SIGINT *before* the first cycle completes. If any of those tests start failing, it means the market-open guard is now skipping their cycle too. Inspect carefully — most likely remedy is to pass `market_open=False` to `_build_sigint_runner`'s StubBroker.

- [ ] **Step 4: Run the full project test suite**

Run: `pytest`
Expected: all tests pass. If anything in `tests/milodex/cli/` or `tests/milodex/execution/` fails, the failure is almost certainly a test that wires up a StubBroker-like double elsewhere. Grep for other stub implementations: `grep -rn "def is_market_open" tests/` and confirm they all default to `True`.

- [ ] **Step 5: Run ruff**

Run: `ruff check src/ tests/ && ruff format --check src/ tests/`
Expected: clean. Fix any formatting issues with `ruff format src/ tests/` if needed.

- [ ] **Step 6: Commit**

```bash
git add src/milodex/strategies/runner.py tests/milodex/strategies/test_runner.py
git commit -m "fix(runner): skip in-progress bars while market is open

StrategyRunner.run_cycle previously fetched and evaluated today's 1D bar
even when the market was still open, which recorded _last_processed_bar_at
with a timestamp that later collided with the finalized post-close bar
(same timestamp, since Alpaca uses start-of-day UTC for 1D bars). That
collision caused the runner to skip the real close evaluation forever.

Guard run_cycle with broker.is_market_open() and return early without
advancing _last_processed_bar_at when the market is open. Post-close
cycles then re-fetch the (now-final) bar normally.

Discovered by a paper-runner session started at 15:56 ET on 2026-04-23
that never evaluated the 16:00 ET close over 80+ minutes of polling.

Regression test added."
```

---

## Task 4: Document the behavior

**Files:**
- Modify: `docs/OPERATIONS.md` — append one bullet to the strategy-runner section.

- [ ] **Step 1: Locate the runtime-ops / strategy-run section**

Run: `grep -n "strategy run\|StrategyRunner\|foreground" docs/OPERATIONS.md`

Pick the most-relevant section. If OPERATIONS.md has a "Running strategies" or similar section, append there. If there is no such section, append a `## Strategy runtime notes` section at the end with a single bullet.

- [ ] **Step 2: Add the note**

Append (adjust wording if a section heading already exists):

```markdown
- **Starting the runner during market hours is safe but idle.** `milodex strategy run <id>` skips cycles entirely while `broker.is_market_open()` returns True. The runner will print its session banner and then stay silent until the market closes, at which point it picks up the finalized daily bar and evaluates normally. This avoids a subtle same-timestamp collision where an in-progress 1D bar shares its timestamp with the post-close finalized bar.
```

- [ ] **Step 3: Commit**

```bash
git add docs/OPERATIONS.md
git commit -m "docs(ops): note runner behavior during market hours

Document that milodex strategy run is intentionally idle while the
market is open; processing resumes automatically post-close. References
the in-progress-bar fix committed in the prior change."
```

---

## Verification (after all tasks complete)

- [ ] `pytest` — all tests pass.
- [ ] `ruff check src/ tests/ && ruff format --check src/ tests/` — clean.
- [ ] `git log --oneline -n 3` — three commits: the StubBroker prep, the fix + regression test, the docs note.
- [ ] Smoke-test manually (optional, takes 30 seconds): start a fresh `milodex strategy run regime.daily.sma200_rotation.spy_shy.v1` during market hours, observe the session banner, confirm no further output until market close (or stop with Ctrl-C). Outside market hours, confirm it evaluates immediately as before.

---

## Notes for future intraday work

This fix is correct for daily-close strategies but is intentionally blunt for intraday tempos — an intraday strategy *must* process bars while the market is open. Before adding an intraday-capable runner variant:

- Replace this guard with a tempo-aware one (e.g., "skip only if the bar's timeframe period has not yet elapsed").
- OR thread an `is_final` flag from the data provider to the runner, so intraday bars that have closed within the session are distinguished from the current live bar.
- OR build a streaming (websocket) data path that only emits bars once they are confirmed closed.

The current fix should not be extended to cover intraday tempos — it would effectively disable intraday trading. It's a Phase 1 daily-swing expedient and should be replaced when intraday capabilities are added.
