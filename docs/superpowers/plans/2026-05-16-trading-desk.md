# The Trading Desk — Redesign & Live-Data Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mock `DeskSurface.qml` with a 7-section "Trading Desk" wired to live Milodex data via six new read-models plus one small `OperationalState` extension.

**Architecture:** Each section is fed by a self-contained `QObject` read-model that copies the established `StrategyBankState` pattern (module SQL constants → pure `_query_*` helper testable without Qt → `_XRefreshSignals`/`_XRefreshRunnable` carrier → `XState` with per-instance `QThreadPool`, in-flight guard, `start/stop`, `Q_PROPERTY`s). Read-models are built and tested headless first (PRs 0–6); the QML surface is rewritten last (PRs 7–8) against proven contracts. All read-models are strictly read-only.

**Tech Stack:** Python 3.11, PySide6 (QtCore `QObject`/`QTimer`/`QThreadPool`/`Property`/`Signal`), sqlite3, pandas (ParquetCache), Qt Quick/QML, pytest (offscreen Qt), ruff.

**Spec:** `docs/superpowers/specs/2026-05-16-trading-desk-redesign-design.md` — read it before starting. Decisions there are locked.

---

## Conventions (read once — every read-model PR references this)

### Canonical reference implementation

`src/milodex/gui/strategy_bank_state.py` + `tests/milodex/gui/test_strategy_bank_state.py` are the **exact template**. Every new read-model is structurally identical; only the SQL, the row-shaping, the `Q_PROPERTY` set, and the assertions differ.

### Shared model scaffold (the "STANDARD SCAFFOLD")

Each new model file `src/milodex/gui/<name>_state.py` has this skeleton — **copy from `strategy_bank_state.py` and rename**; do not re-derive:

```python
from __future__ import annotations
import logging, sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from PySide6.QtCore import Property, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot

logger = logging.getLogger(__name__)

# --- module SQL / pure helpers (PR-specific) ---
def _query_<name>(db_path: Path, ...) -> <payload>:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)   # READ-ONLY (see below)
    conn.row_factory = sqlite3.Row
    try:
        ...
    finally:
        conn.close()

class _<Name>RefreshSignals(QObject):
    completed = Signal(dict)
    failed = Signal(str)

class _<Name>RefreshRunnable(QRunnable):
    def __init__(self, db_path, signals, **params) -> None:
        super().__init__(); self._db_path = db_path; self._signals = signals
        self._params = params; self.setAutoDelete(True)
    def run(self) -> None:  # pragma: no cover
        try:
            self._signals.completed.emit({"data": _query_<name>(self._db_path, **self._params),
                                          "refreshed_at": datetime.now(tz=UTC).isoformat()})
        except Exception as exc:  # noqa: BLE001
            logger.warning("<Name>State: refresh failed: %s", exc)
            self._signals.failed.emit(str(exc))

class <Name>State(QObject):
    # Signals + __init__ (db_path/refresh_interval_ms/parent) + start()/stop()
    # + _kick_refresh()/_on_refresh_complete()/_on_refresh_failed()
    # + Q_PROPERTY accessors — ALL copied verbatim from StrategyBankState,
    #   substituting the backing fields and Property declarations below.
```

**Read-only enforcement (spec §2.10, locked):** every `_query_*` helper opens the DB with the read-only URI `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)`. A write attempted on this connection raises `sqlite3.OperationalError`. Each model PR includes a test asserting the connection is read-only. (`StrategyBankState` uses a plain connection; the new models deliberately tighten this — do **not** copy its connect line, use the `mode=ro` form.)

**`stop()` / lifecycle / in-flight guard / preserve-last-known on failure / `dataStatus` ∈ {loading, ready, error}:** copied verbatim from `StrategyBankState` (lines 380–456). Do not modify the threading contract.

### Shared test scaffold (the "STANDARD TESTS")

Each `tests/milodex/gui/test_<name>_state.py` copies the harness from `test_strategy_bank_state.py`:
- `_PYSIDE6_AVAILABLE` / `_skip_no_qt` block (verbatim).
- module-scoped `qapp` fixture (verbatim, offscreen).
- `_make_state(db_path, refresh_interval_ms=99_999_999)` and `_wait_for_pool(state)` (verbatim, renamed).
- PR-specific `_create_fixture_db(path)` + `_seed_*` builders (minimal schema — only the tables that PR's SQL touches; column sets must match the real schema in `data/milodex.db`).
- **Mandatory test set per model** (adapt assertions):
  1. `test_initial_state_is_loading` — pre-refresh `dataStatus == "loading"`, payloads empty.
  2. `test_refresh_populates_*` — seed known rows, `_kick_refresh()`, `_wait_for_pool()`, assert exact aggregates.
  3. `test_db_unavailable_sets_error` — missing DB → `dataStatus == "error"`, message non-empty.
  4. `test_error_after_success_preserves_last_known`.
  5. `test_concurrent_kick_drops_when_in_flight`.
  6. `test_stop_drains_in_flight_worker`.
  7. `test_query_<name>_readonly_connection` — attempt an INSERT on `_query_*`'s connection path is impossible; assert opening `file:...?mode=ro` then `conn.execute("CREATE TABLE x(a)")` raises `sqlite3.OperationalError`.
  8. Pure-logic tests for every `_compute_*` helper, **no Qt**.

### Real schema (authoritative — fixture DBs must match these column names)

- `portfolio_snapshots(id, recorded_at, session_id, strategy_id, equity, cash, portfolio_value, daily_pnl, positions_json)`
- `explanations(id, recorded_at, decision_type, status, strategy_name, strategy_stage, symbol, side, quantity, risk_allowed, risk_summary, reason_codes_json, session_id, backtest_run_id, ...)`
- `trades(id, explanation_id, recorded_at, status, source, symbol, side, quantity, strategy_name, strategy_stage, broker_order_id, broker_status, estimated_order_value, session_id, backtest_run_id)` — FK `trades.explanation_id → explanations.id`
- `strategy_runs(id, session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)`
- `promotions(id, recorded_at, strategy_id, from_stage, to_stage, promotion_type, approved_by, backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count, notes, manifest_id, reverses_event_id, evidence_json)`
- `strategy_manifests(id, strategy_id, stage, config_hash, config_json, config_path, frozen_at, frozen_by)`
- `backtest_runs(id, run_id, strategy_id, config_path, config_hash, start_date, end_date, started_at, ended_at, status, slippage_pct, commission_per_trade, metadata_json)` — walk-forward metrics live in `json_extract(metadata_json, '$.oos_aggregate.{sharpe,max_drawdown_pct,trade_count}')` (see `strategy_bank_state._SQL_PAPER`)

**Canonical paper-scope predicate** (spec §8, used by `RiskThroughputState` and `ActivityFeedState` identically — define once as a module constant, import into both):

```python
# src/milodex/gui/_dashboard_scope.py
PAPER_STAGES = ("paper", "micro_live", "live")
# explanations: strategy_stage IN PAPER_STAGES AND decision_type != 'backtest_fill'
# trades:       strategy_stage IN PAPER_STAGES AND backtest_run_id IS NULL
```

### Test command

```
pytest tests/milodex/gui/test_<name>_state.py -v
```
Full gate before any commit: `ruff check src/milodex/gui tests/milodex/gui && pytest tests/milodex/gui -q`.

### Commit convention

Conventional Commits, trailer `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`. One commit per completed task unless a task says otherwise. Each PR = its own branch off `master`, merged when green (operator's standing cadence; checkpoints after PR 3 and before PR 8 per spec §7).

---

## File Structure

**Create:**
- `src/milodex/gui/_dashboard_scope.py` — shared paper-scope predicate constants (PR 2).
- `src/milodex/gui/performance_state.py` — `PerformanceState` (PR 1).
- `src/milodex/gui/risk_throughput_state.py` — `RiskThroughputState` (PR 2).
- `src/milodex/gui/active_ops_state.py` — `ActiveOpsState` (PR 3).
- `src/milodex/gui/attention_state.py` — `AttentionState` (PR 4).
- `src/milodex/gui/market_tape_state.py` — `MarketTapeState` (PR 5).
- `src/milodex/gui/activity_feed_state.py` — `ActivityFeedState` (PR 6).
- `tests/milodex/gui/test_*_state.py` — one per model.
- `src/milodex/gui/qml/Milodex/components/{SectionHeader,SegmentedToggle,Sparkline,FunnelRow,RollupCell,TapeRow,RunnerSelect,ActivityTable}.qml` (PR 7).
- `tests/milodex/gui/test_desk_components_smoke.py` (PR 7).

**Modify:**
- `src/milodex/gui/operational_state.py` — add `dailyPnl` (PR 0).
- `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` — full rewrite (PR 8).
- `src/milodex/gui/qml_setup.py` + `src/milodex/gui/app.py` — register/start/stop the 6 new singletons (PR 8).
- `src/milodex/gui/read_models.py` — remove the obsolete mock `DeskState` (PR 8).

---

## Task PR-0: Extend `OperationalState` with `dailyPnl`

**Files:**
- Modify: `src/milodex/gui/operational_state.py` (`_account_to_snapshot` ~line 145; add accessor + `Property` near line 454)
- Test: `tests/milodex/gui/test_operational_state.py` (extend existing)

- [ ] **Step 1: Failing test** — add to `test_operational_state.py`:

```python
def test_account_snapshot_includes_daily_pnl() -> None:
    from types import SimpleNamespace
    from milodex.gui.operational_state import _account_to_snapshot
    acct = SimpleNamespace(equity=1000.0, cash=500.0, buying_power=500.0, daily_pnl=12.34)
    snap = _account_to_snapshot(account=acct, market_open=True, positions=[])
    assert snap["daily_pnl"] == 12.34
```

- [ ] **Step 2: Run, verify it fails**

Run: `pytest tests/milodex/gui/test_operational_state.py::test_account_snapshot_includes_daily_pnl -v`
Expected: FAIL `KeyError: 'daily_pnl'`

- [ ] **Step 3: Implement** — in `_account_to_snapshot` add `"daily_pnl": float(account.daily_pnl),` to the returned dict. Add backing field `self._daily_pnl: float = 0.0` in `__init__` (near `self._equity`). In `_on_broker_complete` set `self._daily_pnl = snapshot["daily_pnl"]` alongside equity and include it in the change check that emits `accountChanged`. Add accessor + property:

```python
def _get_daily_pnl(self) -> float:
    return self._daily_pnl
dailyPnl = Property(float, _get_daily_pnl, notify=accountChanged)  # noqa: N815
```

- [ ] **Step 4: Qt-level test** — add a test that drives a broker poll with a stub account exposing `daily_pnl` and asserts `state.dailyPnl` updates after `_wait_for_pool` (mirror the existing equity test in the file).

- [ ] **Step 5: Run full gate**

Run: `ruff check src/milodex/gui && pytest tests/milodex/gui/test_operational_state.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/milodex/gui/operational_state.py tests/milodex/gui/test_operational_state.py
git commit -m "feat(gui): expose AccountInfo.daily_pnl as OperationalState.dailyPnl"
```

---

## Task PR-1: `PerformanceState` (Section II)

**Files:**
- Create: `src/milodex/gui/performance_state.py`
- Test: `tests/milodex/gui/test_performance_state.py`

**Contract:** owns Week/Month/YTD/All-Paper from `portfolio_snapshots`; SPY benchmark from ParquetCache; stale flag. Today is **not** owned here (QML binds `OperationalState.dailyPnl`).

**Slices** (`SLICES = ("Today","Week","Month","YTD","All-Paper")`): each slice's window is `[now - delta, now]` except `Today` (placeholder zeros — QML overlays `dailyPnl`) and `All-Paper` (earliest snapshot → now). Deltas: Week=7d, Month=30d, YTD=Jan-1-of-current-year.

**Pure helpers (complete code):**

```python
def _period_return(equity_series: list[float]) -> float | None:
    if len(equity_series) < 2 or equity_series[0] == 0:
        return None
    return (equity_series[-1] / equity_series[0]) - 1.0

def _max_drawdown(equity_series: list[float]) -> float | None:
    if not equity_series:
        return None
    peak = equity_series[0]; mdd = 0.0
    for v in equity_series:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v / peak) - 1.0)
    return mdd  # <= 0.0

def _is_stale(newest_iso: str | None, now: datetime, max_trading_days: int = 2) -> bool:
    # spec §8: threshold pinned here at 2 *calendar*-day proxy for trading days
    # (documented simplification; refine only if a trading-calendar util exists).
    if newest_iso is None:
        return True
    newest = datetime.fromisoformat(newest_iso)
    return (now - newest).days > max_trading_days
```

**SPY benchmark:** read the daily-bar parquet for `SPY` via `ParquetCache` and compute `_period_return` on `close` over the same window. Cache dir from `milodex.config.get_cache_dir()`; **version: read the highest `vN` dir present** (helper `_latest_cache_version(cache_dir)` — verification: confirm against `market_cache/` which currently holds `1Day, v2, v3`). `Timeframe.DAY_1`. Excess = strategy return − SPY return per slice.

**Q_PROPERTYs:** `bySlice` (`QVariantMap`: slice → `{return, drawdown}`), `benchmarkBySlice` (`QVariantMap`: slice → `{spyReturn, excess}`), `sparkline` (`QVariantList` of equity floats, All-Paper window), `isStale` (bool), `staleAsOf` (str), `lastRefreshedAt`, `dataStatus`, `dataErrorMessage`.

- [ ] **Step 1:** Write `_create_fixture_db` (just `portfolio_snapshots`) + `_seed_snapshot(db, recorded_at, equity, ...)` in the test file.
- [ ] **Step 2:** Failing pure-logic tests for `_period_return`, `_max_drawdown`, `_is_stale` (boundary: exactly 2 days = fresh, 3 = stale). Run → FAIL (module missing).
- [ ] **Step 3:** Create `performance_state.py` with the pure helpers only. Run pure tests → PASS.
- [ ] **Step 4:** Failing test `test_query_performance_slices` — seed a known equity series, assert Week return/drawdown to 1e-9. Run → FAIL.
- [ ] **Step 5:** Implement `_query_performance(db_path, now)` (read-only conn; group snapshots into slice windows; build `bySlice`). Run → PASS.
- [ ] **Step 6:** Failing test for SPY benchmark + excess using a seeded SPY parquet in `tmp_path` (write a tiny DataFrame via `ParquetCache.write`). Run → FAIL.
- [ ] **Step 7:** Implement `_latest_cache_version` + SPY read + `benchmarkBySlice`. Run → PASS.
- [ ] **Step 8:** Add STANDARD SCAFFOLD (`_PerfRefreshSignals/Runnable/PerformanceState`) by copying `StrategyBankState`, substituting payload + Property set. Add the STANDARD TESTS (1–7) + a stale-flag boundary test. Run `pytest tests/milodex/gui/test_performance_state.py -v` → PASS.
- [ ] **Step 9:** Full gate. Commit:

```bash
git add src/milodex/gui/performance_state.py tests/milodex/gui/test_performance_state.py
git commit -m "feat(gui): PerformanceState — portfolio_snapshots slices + SPY benchmark + stale flag"
```

---

## Task PR-2: `RiskThroughputState` (Section IV) + shared scope module

**Files:**
- Create: `src/milodex/gui/_dashboard_scope.py`, `src/milodex/gui/risk_throughput_state.py`
- Test: `tests/milodex/gui/test_risk_throughput_state.py`

- [ ] **Step 1:** Create `_dashboard_scope.py`:

```python
"""Canonical paper-scope predicate shared by dashboard read-models (spec §8)."""
PAPER_STAGES: tuple[str, ...] = ("paper", "micro_live", "live")
EXPLANATION_PAPER_SQL = (
    "strategy_stage IN ('paper','micro_live','live') "
    "AND decision_type != 'backtest_fill'"
)
TRADE_PAPER_SQL = (
    "strategy_stage IN ('paper','micro_live','live') AND backtest_run_id IS NULL"
)
```

- [ ] **Step 2:** Failing test `test_funnel_stage_counts` — fixture DB with `explanations` + `trades`; seed a known mix (paper + a backtest_fill that must be excluded; no_signal rows; blocked rows; submitted/filled trades). Assert each stage exact.

- [ ] **Step 3:** Implement `_query_throughput(db_path, now)`. Stage definitions (spec §4, complete SQL predicates over the paper-scoped population, per slice window on `recorded_at`):

| Stage | Predicate |
|---|---|
| Evaluations | `COUNT(*)` of `explanations` where `EXPLANATION_PAPER_SQL` |
| Signals | + `status != 'no_signal'` |
| Orders proposed | + `decision_type IN ('submit','preview')` |
| Risk-approved | + `risk_allowed = 1` |
| Rejected | `explanations` paper-scoped `risk_allowed = 0` (`status='blocked'`) |
| Submitted | `trades` where `TRADE_PAPER_SQL` and `status='submitted'` |
| Filled | `trades` where `TRADE_PAPER_SQL` and `broker_status='filled'` |

Submitted/Filled join back to evaluations via `trades.explanation_id` (spec §8) — count distinct `trades.id` whose `explanation_id` is in the paper-scoped explanation set for the slice. Same `SLICES` + windows as PR 1. Expose `bySlice` (`QVariantMap`: slice → ordered list `[{key,label,value}]`).

- [ ] **Step 4:** Run → PASS. Add a test asserting a `decision_type='backtest_fill'` row is **excluded** (paper-scoping regression guard).
- [ ] **Step 5:** STANDARD SCAFFOLD + STANDARD TESTS (1–7). Run suite → PASS.
- [ ] **Step 6:** Full gate. Commit:

```bash
git add src/milodex/gui/_dashboard_scope.py src/milodex/gui/risk_throughput_state.py tests/milodex/gui/test_risk_throughput_state.py
git commit -m "feat(gui): RiskThroughputState — paper-scoped Evaluations→Filled funnel"
```

---

## Task PR-3: `ActiveOpsState` (Section III) — CADENCE CHECKPOINT

**Files:**
- Create: `src/milodex/gui/active_ops_state.py`
- Test: `tests/milodex/gui/test_active_ops_state.py`

**⚠ Verification gate (spec §8):** before implementing cadence, inspect a real frozen `strategy_manifests.config_json` (and a `configs/*.yaml`) to determine whether tempo/open-close is expressible. Document the finding in the PR description. If a discrete open/close field exists → use it; else cadence degrades to `bar_size`+`poll_interval` strings and the limitation is noted in the module docstring. **This is the post-PR-3 operator checkpoint.**

**Per-runner contract** (runner = latest `strategy_runs` per `strategy_id`):

```python
def _session_state(ended_at, exit_reason) -> str:
    return "running" if not ended_at else f"stopped:{exit_reason or 'unknown'}"

def _heartbeat(last_eval_iso: str | None, now: datetime, cadence_seconds: int) -> str:
    if last_eval_iso is None:
        return "no activity"
    age = (now - datetime.fromisoformat(last_eval_iso)).total_seconds()
    return "on schedule" if age <= cadence_seconds * 1.5 else f"overdue by {int(age//60)}m"

def _session_age(started_at_iso: str, now: datetime) -> str:
    secs = int((now - datetime.fromisoformat(started_at_iso)).total_seconds())
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"
```

- last-eval = `MAX(explanations.recorded_at)` for the runner's `session_id`.
- runner lock: `milodex.core.advisory_lock` — held/released for the strategy (read-only check; reuse the existing lock-inspection accessor; do not acquire).
- stop-requested: presence of the stop-request sentinel (`PaperRunnerControl` / `request_controlled_stop` path under `get_locks_dir()`); check file existence only.

Steps follow the PR-1 shape: fixture builders for `strategy_runs`+`explanations` (+ a fake manifest config_json), failing pure-helper tests (`_session_state`, `_heartbeat` boundary at 1.5×, `_session_age`), implement `_query_active_ops`, STANDARD SCAFFOLD + STANDARD TESTS, full gate, commit:

```bash
git commit -m "feat(gui): ActiveOpsState — runner state, cadence, heartbeat, lock, stop-request"
```

**After this PR: stop and report the cadence finding to the operator before continuing.**

---

## Task PR-4: `AttentionState` (Section V)

**Files:**
- Create: `src/milodex/gui/attention_state.py`
- Test: `tests/milodex/gui/test_attention_state.py`

Reuse `StrategyBankState`'s gate constants: `from milodex.promotion.state_machine import MIN_SHARPE, MAX_DRAWDOWN_PCT, MIN_TRADES`.

> **Scope-drift note (spec §8):** spec §8 names the canonical paper-scope predicate as shared by `RiskThroughputState` *and* `AttentionState`. This plan satisfies that anti-drift intent differently for `AttentionState`: it does **not** re-implement paper/backtest scoping — it calls `strategy_bank_state._query_bank(db_path)` for the `paperTesting`/`backtestOnly` classification (single source of truth, already test-locked) and uses `_dashboard_scope` only where it touches `explanations`/`trades` directly (the drift list's recency check). It therefore cannot drift from `RiskThroughputState`'s scoping because the two never both hand-roll the predicate. State this rationale in the PR description.

**Rollups** (counts): `runningNow` = `strategy_runs` with `ended_at IS NULL`; `paperTesting` / `backtestOnly` = reuse `strategy_bank_state._query_bank` (call it; do not duplicate the SQL — DRY); `needsReview` and `underperforming` per spec §4/§5 locked defs:

- `_compute_underperforming(paper_metric, baseline_metric, evidence_n, min_evidence_n)` → bool. **Minimum-evidence floor** pinned here: `min_evidence_n` default = `MIN_TRADES` (30) realized paper trades; below floor → never flagged. Underperforming = `evidence_n >= min_evidence_n and paper_metric < baseline_metric` (baseline = the `promotions` row's stored `sharpe_ratio` for the current stage).
- `needsReview` = count of: (a) latest completed `backtest_runs` clears all three gates AND no `promotions` row `to_stage='paper'` for that strategy; (b) paper strategy with realized trades ≥ micro_live threshold AND no `promotions` row `to_stage='micro_live'`; (c) an underperformer with **no** `promotions` row `promotion_type='demotion'`, no frozen manifest, and no stop-request dated after the breach. (c) ⊂ underperforming — assert this relationship in a test.
- `driftList` = top-N `[{name, note, tone}]` (e.g. underperformers + "no fills in N days" from `explanations`/`trades` recency).

Steps: fixture builders (`promotions`, `backtest_runs`, `strategy_runs`, `portfolio_snapshots`); failing pure tests for `_compute_underperforming` (floor boundary: n=29 not flagged, n=30 flagged) and the needsReview (a)/(b)/(c) classifiers individually; a test asserting every needsReview(c) strategy is also in the underperforming set; implement `_query_attention`; STANDARD SCAFFOLD + STANDARD TESTS; full gate; commit:

```bash
git commit -m "feat(gui): AttentionState — rollups, needs-review a/b/c, underperforming + evidence floor"
```

---

## Task PR-5: `MarketTapeState` (Section VI)

**Files:**
- Create: `src/milodex/gui/market_tape_state.py`
- Test: `tests/milodex/gui/test_market_tape_state.py`

Reads ParquetCache only (no DB, no network). Symbols `("SPY","QQQ","IWM","TLT","VIX")`. Per symbol: latest `close`, prior `close`, `pctChange = latest/prior - 1`, `asOf` = latest bar `timestamp` date. Timestamp-only, **no stale flag** (locked). Constructor takes `cache_dir: Path | None` (default `get_cache_dir()`), `refresh_interval_ms=60_000`. Reuse `_latest_cache_version` from PR 1 (move it to `_dashboard_scope.py` or a shared `_market_cache.py` if PR 1 hasn't — DRY: factor on first reuse).

- [ ] Pure test `_pct_change(latest, prior)` incl. prior==0 → None.
- [ ] Fixture: write small DataFrames via `ParquetCache.write` for two symbols; assert `rows` payload exact + a missing-symbol → entry with `dataStatus` note, others still present.
- [ ] STANDARD SCAFFOLD (no DB → `_query_*` takes `cache_dir`) + STANDARD TESTS adapted (no DB-missing test; instead empty-cache test). Full gate. Commit:

```bash
git commit -m "feat(gui): MarketTapeState — cached daily quotes, timestamp-only"
```

---

## Task PR-6: `ActivityFeedState` (Section VII)

**Files:**
- Create: `src/milodex/gui/activity_feed_state.py`
- Test: `tests/milodex/gui/test_activity_feed_state.py`

Union (not join — spec §8) of paper-scoped `explanations`-derived rows and `trades`-derived rows, normalized to `{time, strategy, kind, detail, symbol, tone}`, `ORDER BY recorded_at DESC LIMIT 200`. `kind ∈ {signal, rejection, order, fill}`:
- explanations → `signal` (status not in blocked/submitted/filled), `rejection` (`risk_allowed=0`).
- trades → `order` (`status='submitted'`), `fill` (`broker_status='filled'`).

`_row_tone(kind, ...)` pure helper maps to `positive|negative|warning|muted|data` (re-expressed in QML to editorial tokens — PR 8). Use `_dashboard_scope` predicates.

Steps mirror PR 2: failing `test_feed_union_ordering_and_cap` (seed >200 mixed rows, assert desc order, cap 200, paper-scoped excludes backtest), `_row_tone` pure tests, implement `_query_feed`, STANDARD SCAFFOLD + STANDARD TESTS, full gate, commit:

```bash
git commit -m "feat(gui): ActivityFeedState — paper-scoped explanations∪trades feed"
```

---

## Task PR-7: Shared QML components

**Files:**
- Create: `src/milodex/gui/qml/Milodex/components/{SectionHeader,SegmentedToggle,Sparkline,FunnelRow,RollupCell,TapeRow,RunnerSelect,ActivityTable}.qml`
- Modify: `src/milodex/gui/qml/Milodex/components/qmldir` (register each new component) — confirm the components dir/qmldir convention by reading an existing component (e.g. `Button.qml`) first.
- Test: `tests/milodex/gui/test_desk_components_smoke.py`

Each component: composed against `Theme.*` tokens only (no literals — token-binding contract; a hardcoded hex fails theme-swap). Tone→token map (spec §5): `positive→Theme.color.statusPositive`, `negative→Theme.color.statusNegative`, `warning→Theme.color.statusWarning`, `muted→Theme.color.textMuted`, `data→Theme.color.textPrimary` — confirm exact token names by reading `Theme.qml` first; use the real names.

- [ ] **Step 1:** Read an existing component + `Theme.qml` + `qmldir` to copy conventions exactly.
- [ ] **Step 2:** Write each component (props in / signals out; no read-model coupling — pure presentational; data passed via properties). `SegmentedToggle` exposes `options`, `current`, `activated(string)`. `Sparkline` takes `points: var`, draws via `Shape`/`Canvas`, instant (no animation). `ActivityTable` takes `rows: var` + `filter: string`.
- [ ] **Step 3:** Extend `tests/milodex/gui/test_qml_load_smoke.py` pattern (read it first) — a smoke test that loads each component in a minimal QML harness and asserts no QML errors + (where feasible) token-binding (instantiate under two themes, assert a color property differs).
- [ ] **Step 4:** Full gate (`pytest tests/milodex/gui -q`). Commit:

```bash
git commit -m "feat(gui): shared Trading Desk QML components"
```

---

## Task PR-8: Atomic `DeskSurface.qml` rewrite + wiring (BEFORE-REWRITE CHECKPOINT)

**Operator checkpoint per spec §7: confirm before starting.**

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/surfaces/DeskSurface.qml` (full rewrite to the 7-section IA)
- Modify: `src/milodex/gui/qml_setup.py` (6 new singleton params + `qmlRegisterSingletonInstance` blocks, mirroring the `desk_state` block exactly)
- Modify: `src/milodex/gui/app.py` (instantiate the 6 models with `db_path`/`cache_dir`, pass to `register_qml_types`, `.start()` after registration, `app.aboutToQuit.connect(.stop)` — mirror the existing `desk_state` lines)
- Modify: `src/milodex/gui/read_models.py` (remove the obsolete mock `DeskState`) and `qml_setup.py`/`app.py` (drop its references)
- Test: `tests/milodex/gui/test_qml_load_smoke.py` (extend), `tests/milodex/gui/test_app_wiring.py` (extend if present)

- [ ] **Step 1:** Read current `DeskSurface.qml`, `Main.qml`, the surface-loader, and an existing surface (e.g. `LedgerSurface.qml`) to copy chrome/section conventions. Chrome (`Main.qml`, RiskStrip, kill banner) is **not** modified (scope A).
- [ ] **Step 2:** Rewrite `DeskSurface.qml`: header band; Row1 `I·II·III`; hairline; Row2 `IV·V·VI`; hairline; `VII` full-width. Section I binds `OperationalState` (+ `dailyPnl` in II's Today cell). Sections II–VII bind the new singletons. Slice toggles index the precomputed `bySlice` map client-side (no re-query). Section II shows the stale state when `PerformanceState.isStale`. Animation discipline: instant state changes; figures never crossfade; kill banner never pulses.
- [ ] **Step 3:** Wire `qml_setup.py` + `app.py` for the 6 models; remove mock `DeskState`. Each model constructed with `db_path=db_path` (PerformanceState also needs cache via `get_cache_dir()`; MarketTapeState takes `cache_dir`; ActiveOpsState/AttentionState take `configs_dir` only if cadence/eligibility needs it — match the constructor each model actually defines).
- [ ] **Step 4:** Run QML load smoke test → assert `DeskSurface.qml` loads with zero QML errors and all 6 singletons resolve.
- [ ] **Step 5:** Manual visual verification — run `milodex gui`, confirm all 7 sections render with live data from `data/milodex.db`, slice toggles are instant, no console QML errors. (UI correctness cannot be asserted by unit tests — verify in the running app and report what was seen.)
- [ ] **Step 6:** Full gate: `ruff check src/milodex/gui tests/milodex/gui && pytest tests/milodex/gui -q`. Commit:

```bash
git commit -m "feat(gui): rewrite DeskSurface to 7-section Trading Desk on live data"
```

---

## Done criteria

- All 9 PRs merged green; `pytest tests/milodex/gui -q` passes; ruff clean.
- `DeskSurface.qml` renders the 7-section IA from live `data/milodex.db` + market cache; no mock `DeskState` remains.
- Every read-model: read-only connection test passes; preserve-last-known-on-failure test passes; `stop()` drains in-flight worker.
- Spec §8 verification items resolved and documented (cadence-config finding in PR 3 description; cache-version helper confirmed against `market_cache/`).
- Prior-phase invariants intact (Phase 5 C-3): no risk/execution-path code touched; chrome + FRONT/BENCH/LEDGER unchanged.
