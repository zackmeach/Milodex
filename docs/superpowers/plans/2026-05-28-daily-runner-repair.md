# Daily Runner Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a daily-bar paper runner evaluate the correct (today's) session close regardless of launch time, and ensure the market data it evaluates is current.

**Architecture:** Two independent subsystems, shippable separately.
- **Plan A (runner, critical path):** Gate the daily watermark lock-in on a stateless **bar-identity** check — only lock in when the latest fetched daily bar is for the current session (`latest_bar.timestamp.date() >= self._now().date()`, both UTC). A runner launched *pre-open* fetches the prior session's stale bar (date < today) and now declines to lock it in, so it cannot poison its watermark and suppress today's real post-close evaluation. No broker-interface change. Preserves post-close cold-launch recovery (today-dated bar locks in) and refuses to lock in stale data as a safety bonus.
- **Plan B (data freshness, investigation-first):** Diagnose why the curated_largecap (05-18) and spy_shy (05-22) bar data was stale at the same fetch moment sector ETFs were current (05-27), then fix the refresh path. Root cause unknown → B0 is an investigation task; the B-fix tasks are authored after B0.

**Tech Stack:** Python 3.11+, pytest, ruff. SimulatedBroker / fake brokers for runner tests (`tests/milodex/strategies/test_runner.py`).

**Design intent confirmed this session:** Runner lifecycle is **per-session relaunch** (one process per trading day) — ADR 0012, ADR 0026, PHASE2_PLANNING.md §3.1 CI-1. The watermark is intentionally never reset within a lifetime. The defect is solely that a pre-open launch locks in a stale prior-day bar.

---

## Background: the bug (grounded `src/milodex/strategies/runner.py`)

`run_cycle()` (lines 226–292) gates:
- L229 `if is_daily_bar and market_open: return []` — daily strategies idle *during* market hours.
- L231 `if not market_open and self._last_processed_bar_at is not None: return []` — once a close is locked in, idle until next session.
- L256–261 advance the watermark via `_maybe_advance_lockin_watermark` (sets `_last_processed_bar_at` at L381/L393).

`_last_processed_bar_at` is initialized at L97 and **never reset** (by design).

**Failure trace (2026-05-28):** runners launched 09:01 ET (pre-open). `is_market_open()` was False; watermark was None → L231 did not gate. The fetch returned the *prior* session's daily bar (e.g. 05-27, or staler). `_maybe_advance_lockin_watermark` locked it in. Market then opened (L229 idled the runner), and after the *real* 16:00 close, L231 short-circuited — today's close was never evaluated. Confirmed via `explanations.latest_bar_timestamp`: dailies recorded 05-18/05-22/05-27 bars at the 09:02 launch, then went silent for 11 hours.

**Fix (bar-identity discriminator):** Add a guard that only proceeds with daily close-processing when the latest fetched bar is for the current session. After the existing `already_seen` check (L239–244) and before the account fetch (L246):

```python
if (
    is_daily_bar
    and not market_open
    and not self._is_current_session_bar(latest_bar)
):
    # Pre-open / weekend launch: the latest available daily bar is a PRIOR
    # session's close (date < today). Locking it in would poison the
    # watermark and suppress today's real post-close evaluation. Decline.
    return []
```

with the helper (placed near `_now`, ~L355):

```python
def _is_current_session_bar(self, latest_bar) -> bool:
    """True iff the latest daily bar is for the current session (or newer),
    not a prior session's stale close. Both sides are UTC: ``_now()`` returns
    ``datetime.now(tz=UTC)`` (L353-354) and BarSet timestamps are
    ``datetime64[ns, UTC]`` (data/models.py:64). ``>=`` (not ``==``) is
    required: real bars are never future-dated in prod, but the test harness
    builds today-dated bars against a historical fake clock — ``==`` would
    turn the existing lockin suite red."""
    return latest_bar.timestamp.date() >= self._now().date()
```

**Why bar-identity over a "saw market open" flag:** the flag approach (initial draft) broke the existing post-close lockin tests (which run `market_open=False` throughout and never witness an open) and prevented post-close cold-launch recovery. Bar-identity is stateless, keeps the suite green (their bars are today-dated ≥ historical fake-now), preserves post-close recovery, and declines stale data.

---

## Plan A — Runner pre-open poisoning fix (critical path)

**Files:**
- Modify: `src/milodex/strategies/runner.py` (`run_cycle` — insert guard after `already_seen` block L244, before account fetch L246; add `_is_current_session_bar` helper near `_now` ~L355)
- Test: `tests/milodex/strategies/test_runner.py`

**Real test harness API (grounded — do NOT invent):**
- `_build_lockin_runner(*, tmp_path, strategy_config_dir, risk_defaults_file, initial_bars=None, market_open=False, close_lockin_min_interval_seconds=30.0, close_lockin_max_wait_seconds=300.0)` at `test_runner.py:1066` → returns `(runner, broker, provider, event_store)`. Default `initial_bars` = `{"SPY": build_barset([10,10,10]), "SHY": build_barset([10,10,10])}`.
- `build_barset(closes)` at `test_runner.py:182` → builds daily bars ending at the **real** `now(UTC).replace(hour=21)`, `tz=UTC`. **No per-bar date control** — express "prior session" by moving the runner clock, not a date string.
- Clock control: `runner._now = lambda: fake_now[0]` (see L1153–1154). `_now()` is UTC-aware.
- Market state: `StubBroker(market_open=...)` ctor kwarg, or mutate `broker._market_open` directly (see L1330). There is NO `set_market_open` method.
- Bars: `provider._bars_by_symbol = {"SPY": build_barset([...]), ...}` (see L1207). `provider._bars_by_symbol["SPY"].latest().timestamp` gives the latest bar's UTC Timestamp.
- Fixtures `strategy_config_dir` and `risk_defaults_file` are module fixtures already used by the lockin tests.

### Task A1: Failing test — pre-open (stale prior-day bar) must NOT lock in

- [ ] **Step 1: Write the failing test**

```python
def test_daily_pre_open_stale_bar_does_not_lock_in(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Pre-open launch: the latest available daily bar is a PRIOR session's
    close (bar date < today). The runner must NOT lock it in — doing so would
    poison the watermark and suppress today's real post-close evaluation."""
    runner, _, provider, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    # Bars end at "real today" (build_barset). Make them STALE relative to now
    # by advancing the runner clock two days past the latest bar — i.e. the
    # latest available bar is from a prior session, exactly the pre-open case.
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime() + timedelta(days=2)]
    runner._now = lambda: fake_now[0]

    results = runner.run_cycle()
    # Even after the stability interval elapses, a stale bar never locks in.
    fake_now[0] = fake_now[0] + timedelta(seconds=60)
    runner.run_cycle()

    assert results == []
    assert runner._last_processed_bar_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/milodex/strategies/test_runner.py::test_daily_pre_open_stale_bar_does_not_lock_in -v`
Expected: FAIL — current code locks in the stale bar after the stability window (`_last_processed_bar_at` becomes non-None).

- [ ] **Step 3: Add the helper + guard (minimal)**

Add `_is_current_session_bar` near `_now` (~L355) and the guard after the `already_seen` block (after L244, before L246) — exact code in the "Fix (bar-identity discriminator)" section above. No `__init__` change needed (stateless).

- [ ] **Step 4: Run the new test — expect PASS**

Run: `python -m pytest tests/milodex/strategies/test_runner.py::test_daily_pre_open_stale_bar_does_not_lock_in -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/milodex/strategies/runner.py tests/milodex/strategies/test_runner.py
git commit -m "fix(runner): decline daily lock-in on stale prior-session bar (pre-open poisoning)"
```

### Task A2: Test — post-close current-session bar still locks in (recovery path)

- [ ] **Step 1: Write the test**

```python
def test_daily_post_close_current_bar_locks_in(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Post-close (incl. cold launch): latest bar is TODAY's close
    (bar date == now date). The current-session guard must allow lock-in."""
    runner, _, provider, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
        close_lockin_min_interval_seconds=30.0,
    )
    # Anchor the clock to the SAME UTC date as the latest bar -> "today's close".
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner._now = lambda: fake_now[0]

    runner.run_cycle()                         # first cycle: pending stability
    assert runner._last_processed_bar_at is None
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    runner.run_cycle()                         # second cycle: lock in

    assert runner._last_processed_bar_at is not None
```

- [ ] **Step 2: Run — expect PASS** (current-session bar is not suppressed by the guard)

Run: `python -m pytest tests/milodex/strategies/test_runner.py::test_daily_post_close_current_bar_locks_in -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/milodex/strategies/test_runner.py
git commit -m "test(runner): post-close current-session bar still locks in"
```

### Task A3: Full suite + lint, confirm existing lockin tests stay green

- [ ] **Step 1: Run the runner test module**

Run: `python -m pytest tests/milodex/strategies/test_runner.py -v`
Expected: all PASS. The four existing post-close lockin tests at L1115–1254 stay green **because** their bars are built at the real today (`build_barset`) while `fake_now` is a fixed historical date (e.g. `2026-05-04`): the bar date is far ≥ now date, so the `>=` current-session guard never suppresses them. (This is why the guard uses `>=`, not `==`.)

- [ ] **Step 2: Run the full strategies test dir + a broad smoke**

Run: `python -m pytest tests/milodex/strategies/ -q`
Expected: all PASS.

- [ ] **Step 3: Lint**

Run: `python -m ruff check src/milodex/strategies/runner.py tests/milodex/strategies/test_runner.py`
Expected: clean.

- [ ] **Step 4: Commit (only if lint auto-fixed something)**

### Task A4: Operational guardrail — document launch guidance

- [ ] **Step 1:** Add a one-line gotcha to `CLAUDE.md` Gotchas: daily runners launched *pre-open* used to lock onto the prior session's stale bar and silently skip today's close; this is now defended in code (the runner declines a bar whose date < today). Launching after the open or post-close both work correctly. (Do NOT claim post-close launch is unsupported — bar-identity preserves it.)
- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): daily runner pre-open stale-bar guard + launch guidance"
```

---

## Plan B — Per-universe bar-data staleness (investigation-first)

### Task B0: Diagnose the per-universe staleness (NO code change yet)

**Exit criteria:** a written root-cause statement explaining why, at the same 09:01 ET fetch on 2026-05-28, `_fetch_bars_by_symbol` returned 05-27 bars for sector ETFs but 05-18 for curated_largecap and 05-22 for spy_shy.

- [ ] **Step 1:** Identify the data provider the runner uses. `runner._fetch_bars_by_symbol` (L436–442) calls `self._data_provider.get_bars(universe, timeframe, start, end=date.today())`. Find which concrete provider is wired for paper runs and whether it reads a cache (`market_cache/v3/`, `v2/`, or legacy `1Day/`) or hits Alpaca live.
- [ ] **Step 2:** Inspect the v3/v2 cache contents for one largecap symbol vs one sector ETF — last bar date in each parquet. Determine whether the cache is the source and whether it's refreshed on read or only by an explicit warmup.
- [ ] **Step 3:** Determine whether staleness is (a) a stale cache never refreshed, (b) a provider that silently serves cached bars when a live fetch returns empty, or (c) an Alpaca data-entitlement/feed gap for largecap symbols. Cite file:line.
- [ ] **Step 4:** Write the root cause + recommended fix into this plan as Tasks B1+ (TDD steps), then proceed.

**Suggested approach:** dispatch a focused investigation (Explore or general-purpose agent) scoped to the data provider + cache; do NOT modify files in B0.

### B0 result (2026-05-28 investigation)

**Root cause:** `AlpacaDataProvider.get_bars` (`src/milodex/data/alpaca_provider.py:135-138`) — when `end = date.today()` and the cache is behind today, the `if end >= today:` branch requests only `(today, today)`. The `elif end > cache_end:` gap-fill (L139-142) is unreachable because the `if` always wins when `end >= today`. A today-only daily request returns EMPTY from the live IEX feed (probed), so nothing is written and the cache stays pinned at its last-warmed bar. Per-universe staleness = universes warmed on different dates (largecap→05-18, spy_shy→05-22). Caches read are **v3** (`CACHE_VERSION="v3"`, alpaca_provider.py:39); legacy `1Day/` and `v2/` are not read by the runner.

**Probe:** `(today, today)` → 0 bars for AAPL/SPY/XLE/SHY; `(today-12, today)` → 8 fresh bars each (last 2026-05-28). The data exists; the provider never asks for the gap.

**Fix is one line** (preserves the today-refetch intent while filling the stale tail):
`fetch_from = max(start, today)` → `fetch_from = max(start, min(cache_end + timedelta(days=1), today))`

This composes with Plan A: A *declines* stale data (safe), B1 *heals* the cache (correct). After B1 a runner launched on a stale cache self-heals (fetches `cache_end+1 .. today`) — no manual re-warm needed.

### Task B1: Fix the unreachable gap-fill

**Files:**
- Modify: `src/milodex/data/alpaca_provider.py:136`
- Test: `tests/milodex/data/test_alpaca_provider.py` (class `TestGetBarsCaching`)

- [ ] **Step 1:** Write failing test `test_stale_cache_behind_today_fetches_full_tail` — seed cache ending `today-10`, request `end=today`, assert `request.start.date() == cache_end + 1 day` (discriminator) and result latest == today. Harness: `_make_bar(ts, close)`, `provider` fixture (tmp cache), inspect `provider._client.get_stock_bars.call_args.args[0]`.
- [ ] **Step 2:** Run — expect FAIL (buggy code requests `start == today`).
- [ ] **Step 3:** Apply the one-line fix at L136.
- [ ] **Step 4:** Run — expect PASS. Then run the full `test_alpaca_provider.py` (the two existing today-refetch tests at L347/L370 must stay green: when cache already reaches today, `min(cache_end+1, today) == today`, so behavior is unchanged).
- [ ] **Step 5:** Lint + commit.

### Task B2 (optional follow-up, NOT in this PR): fail-loud staleness

The investigation recommends the runner log loudly when declining stale daily data. Plan A's `_is_current_session_bar` already *declines* it (silently returns `[]`); a `logger.warning` at that decline point would aid diagnosis. Deferred — A already makes it safe; track as a small enhancement.

### Task B3 (ops verification, after B1): confirm caches heal

- [ ] Run a real `get_bars` (or `data fetch-universe --end <today>`) for a stale largecap symbol through the fixed provider; confirm the v3 cache advances to the current session. Do this before any runner relaunch.

---

## Out-of-scope backlog (sequenced, NOT part of this plan's execution)

These are tracked for sequencing after A+B land. Each becomes its own plan/PR.

| Pri | ID | Issue | First step |
|-----|----|-------|-----------|
| 1 | #4 | broker_status reconcile — fills never detected; Desk shows "0 fills" despite 2 real fills 2026-05-28 | Read `src/milodex/cli/commands/reconcile.py` — confirm what already exists before building |
| 2 | #3 | `MAX(id)` vs `recorded_at` promotion-read ordering (`event_store.py:1225`) | Written plan (risk layer reads this path); honor `recorded_at`/`reverses_event_id` chain |
| 3 | #6 | Cross-strategy `duplicate_order_window` attribution (ORB opened, bench closed same position) | Decision/ADR: per-strategy P&L from intent log, not broker position |
| 4 | #5 | Section VII Order/Signal Tape sparse rendering | Re-verify after #4 lands (suspected dependent) |
| 5 | DOC | `STRATEGY_BANK.md` stale (ORB/bench listed backtest, now paper) | Doc refresh — now unblocked (market closed, state stable) |

---

## Notes for the executor

- **Branch first:** repo convention is no direct commits to `master`. Create `fix/daily-runner-pre-open-poisoning` before Task A1.
- **System state:** all 8 paper runners were cleanly stopped (controlled_stop) 2026-05-29 ~00:12 UTC; locks released; account flat. No live runner will be disturbed by source edits.
- **Do not relaunch runners** until A lands AND B0/B-fix resolves largecap staleness — otherwise the dailies evaluate stale data again.
- **CLI entry:** `python -m milodex.cli.main ...` (not `python -m milodex`).
- **Tests:** `python -m pytest` (bare `pytest` may not be on PATH).
