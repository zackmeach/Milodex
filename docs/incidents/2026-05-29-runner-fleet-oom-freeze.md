# Incident: runner-fleet startup OOM froze the workstation (2026-05-29)

**Status:** Resolved — fleet torn down, root-cause fix landed (see Resolution).
**Severity:** High (full-desktop freeze; no capital impact — paper only).
**Author:** Claude (diagnostic session), 2026-05-29.

---

## Summary

An operator-requested fleet soak test promoted three intraday strategies from
`backtest` → `paper`, then launched **all 11 paper-stage strategies at once** as
detached OS processes. Every runner runs a startup reconciliation that calls
`incident_already_logged()`, which loaded the **entire** `explanations` table into
memory via `EventStore.list_explanations()`. The event store is **974 MB**. With 11
runners doing this simultaneously, memory demand spiked into the tens of GB: two
intraday runners died outright with `MemoryError`, and the aggregate paging storm
froze the whole workstation — Claude Desktop included.

This was **not** a Claude Desktop bug, and the soak-test goal was valid. It exposed a
latent unbounded-query bug in Milodex's startup path, detonated by launching the full
fleet concurrently.

## Impact

- Entire desktop frozen / unresponsive during the launch storm.
- 2 of 11 runners (`breakout.orb.intraday.spy.v1`, `meanrev.vwap_reversion.intraday.spy.v1`)
  crashed at startup with `MemoryError`.
- 9 runners survived but ran detached, outliving the session; 3 intraday survivors
  re-read the 118,291-row 5Min SPY parquet every ~10s, sustaining load.
- No capital effect: all strategies were paper-stage.

## Timeline (reconstructed from `logs/loadtest_2026-05-29_load/` + runner logs)

| Time (local) | Event |
|---|---|
| ~11:57–11:58 | Prior session promoted 3 intraday configs → `paper` for the requested soak test; launched 11 detached runners, staggered 1s (`launched_pids.csv`). |
| 11:58–11:59 | Each runner hits startup reconciliation → `list_explanations()` over the 974 MB table. Memory balloons. |
| 11:59 | `breakout.orb.intraday` and `meanrev.vwap_reversion.intraday` die with `MemoryError` (`*.err.log`). |
| 11:58–12:04 | Aggregate memory pressure → paging storm → **desktop freeze**. |
| 12:04 | `sampler.ps1` begins sampling; `machine_impact.csv` shows 18 runner procs / 730 MB fleet WS (post-storm steady state). |
| 12:05 | 3 intraday survivors looping, re-reading the full parquet every ~10s. |
| (this session) | Fleet torn down: 18 processes killed, 9 advisory locks cleared. |

## Root cause (grounded)

The exact line in both `MemoryError` tracebacks:

```
File ".../milodex/operations/reconciliation.py", line 591, in incident_already_logged
    explanations = event_store.list_explanations()
File ".../milodex/core/event_store.py", line 612, in list_explanations
    return [_explanation_from_row(row) for row in rows]
MemoryError
```

`incident_already_logged` (`operations/reconciliation.py:590`) only needs the *most
recent* `reconcile_incident` explanation's `config_hash` — but it loaded **every**
explanation row and built a Python object per row:

```python
def incident_already_logged(event_store: EventStore, content_hash: str) -> bool:
    explanations = event_store.list_explanations()          # SELECT * FROM explanations  (974 MB)
    for exp in reversed(explanations):
        if exp.decision_type == "reconcile_incident":
            return exp.config_hash == content_hash
    return False
```

`EventStore.list_explanations()` (`core/event_store.py:609`) is an unbounded
`SELECT * FROM explanations ORDER BY id ASC` with no `LIMIT`. Memory cost scales with
total history, so this was a time bomb: fine when the DB was small, guaranteed OOM at
974 MB — and catastrophic when 11 runners trip it at the same instant.

### Contributing factors

- **Concurrent fleet launch** turned a slow per-process query into a machine-wide
  memory DoS.
- **Sustained load:** intraday survivors re-read the full 118k-row parquet every cycle.
- **Windows Store `python.exe` shim** (the documented "phantom runner" trap in
  `CLAUDE.md`) was the launch interpreter, which is why the prior session burned effort
  second-guessing whether processes were real.

## Plan

1. **Recover the machine (operational).** Hard-kill the orphaned fleet and clear the
   stale advisory locks (controlled-stop won't work on detached/wedged runners). — *Done before this report.*
2. **Fix the root cause (code), TDD:**
   - Add a targeted `EventStore` method that returns the most recent
     `reconcile_incident` `config_hash` via a single `ORDER BY id DESC LIMIT 1` query —
     flat memory regardless of table size.
   - Rewrite `incident_already_logged` to use it. No behaviour change; the full-table
     load is eliminated from the hot startup path every runner hits.
   - Tests: new `EventStore` method (empty + most-recent-wins), and a regression test
     asserting `incident_already_logged` does **not** call `list_explanations`.
3. **Verify:** run the new tests plus the existing event-store and reconcile suites.
4. **Note adjacent risk (no change this pass):** `run_reconciliation` also calls
   `list_trades()` twice (`reconciliation.py:193,197`) — same unbounded pattern, a
   latent OOM vector that did not fire here. Tracked as follow-up, not fixed in this
   surgical pass.

## Resolution

### 1. Machine recovered

- Killed the 18-process fleet (9 redirectors + 9 worker children) via `taskkill /F /T`
  on the redirector PIDs — the tree-kill also caught a second worker child per runner
  (e.g. `42000 → 51744 + 48248`) that a worker-PID list would have orphaned.
- Cleared the 9 stale advisory locks in `data/locks/`.
- Verified: only the 2 unrelated (non-Milodex) python processes remained alive.
- The 2 OOM-crashed runners never acquired locks (they died before registering),
  confirming the failure hit *before* runner registration.

### 2. Root-cause fix (landed)

- **New:** `EventStore.latest_reconcile_incident_hash()` (`core/event_store.py`) —
  `SELECT config_hash ... WHERE decision_type='reconcile_incident' ORDER BY id DESC
  LIMIT 1`. The result is bounded to one row and memory stays flat regardless of table
  size. Without a `decision_type` index this can still scan the table, so add an index
  later if this query becomes latency-sensitive.
- **Changed:** `incident_already_logged()` (`operations/reconciliation.py`) now calls
  the targeted method instead of `list_explanations()`. Behaviour preserved (still
  compares against the most recent incident only); the full-table load is gone from the
  per-runner startup path.

### 3. Verification

- New tests, TDD: 2 for the `EventStore` method (empty → `None`; most-recent-wins,
  ignoring other decision types), 4 for `incident_already_logged` including a
  **regression guard** that monkeypatches `list_explanations` to raise — failing red
  before the fix, green after.
- Suites: `tests/milodex/core/test_event_store.py` + `tests/milodex/operations/test_reconciliation.py`
  → **37 passed**; `tests/milodex/cli/test_reconcile.py` → **17 passed**.
- `ruff check` + `ruff format --check` clean on all touched files.

### 4. Open follow-ups (not addressed this pass)

- **`list_trades()` ×2 in `run_reconciliation`** (`reconciliation.py:193,197`) — same
  unbounded full-table pattern; latent OOM vector that did not fire here. Larger change
  (touches open-order derivation); deferred deliberately to keep this fix surgical.
- **Concurrent fleet launch** has no guard — nothing throttles or staggers-with-backoff
  the launch of N runners. Consider a launch concurrency cap / readiness gate.
- **974 MB event store** — the `explanations` table is dominated by per-cycle
  `no_action` rows. Consider retention/compaction for non-decision explanations.
- **Phantom-runner interpreter trap** — the launch used the Windows Store `python.exe`
  shim rather than the project `.venv` (per `CLAUDE.md`).
- **Stale `strategy_runs` rows** — the hard-killed runners still show `ended_at IS NULL`
  ("phantom") until the bootstrap reconcile (#161) marks them ended.
- **Config promotions are intentional** — the three `backtest → paper` flips are part
  of the requested intraday soak-test setup.
