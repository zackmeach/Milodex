# Thermo-Nuclear Code Quality Review: GUI Polling / Read-Model Lifecycle

**Date:** 2026-05-24  
**Scope:** GUI read-model polling lifecycle — `src/milodex/gui/read_models.py`, the seven adjacent `*_state.py` modules that duplicate QTimer/QThreadPool worker orchestration, `operational_state.py` (structural outlier), `app.py` shutdown wiring, and related tests under `tests/milodex/gui/`.  
**Method:** Read-only audit per the thermo-nuclear review skill (structural regressions first, code-judo opportunities second).  
**Roadmap anchor:** [RM-007 — GUI Polling Adapter and Projection Locality](../architecture/roadmaps/2026-05-21-deepening-roadmap.md#rm-007---gui-polling-adapter-and-projection-locality) (P3, blocked).  
**Verdict:** **Do not approve the current split architecture as “finished.”** Trading-governance risk is low (maintainability-only), but the codebase already contains the correct abstraction and then re-implemented it seven times. That is a clear structural smell and a high-payback deletion target when RM-007 unblocks.

---

## Executive summary

| Metric | Value | Skill threshold |
|--------|------:|-----------------|
| `read_models.py` total lines | 1,552 | **>1,000 = presumptive blocker** |
| `_PollingReadModel` + worker scaffold | ~88 lines (148–236) | Canonical lifecycle (private) |
| Read models using `_PollingReadModel` | 4 (`FrontPage`, `Bench`, `Kanban`, `Ledger`) | — |
| Adjacent state modules duplicating lifecycle | 7 | Roadmap: “real in 7 of 8” |
| Structural outlier | `OperationalState` (dual cadence: 1 s kill-switch + 15 s broker) | Documented exception |
| Approx. duplicated lifecycle LOC (7 modules) | **~490–560** (timer, pool, signals, start/stop, in-flight guard, complete/fail handlers) | — |
| `*_state.py` modules (excl. operational) | ~2,900 lines total | Mostly domain SQL + duplicated scaffold |
| Tests pinning `_kick_refresh` / `_thread_pool` | 7 files, ~90+ `noqa: SLF001` touchpoints | Blocks cheap migration |

The 2026-05-23 roadmap investigation is **correct on both halves**:

1. **Projection duplication is a false alarm** — Bench evidence packets, action intent previews, kanban lanes, and ledger construction live in `read_models.py` only. Co-location ≠ duplication.
2. **QThread lifecycle duplication is real** — seven modules copy the same pattern that `_PollingReadModel` already encodes for four siblings in the same package.

The missed opportunity is not “invent a polling adapter.” It is **finish the migration the codebase already started** and **stop growing `read_models.py` as a god file** while doing so.

---

## Approval bar (thermo-nuclear)

| Criterion | Status |
|-----------|--------|
| No structural regression | **Warn** — dual architectures in one package |
| No missed dramatic simplification | **Fail** — inherit/extract existing `_PollingReadModel` |
| No unjustified file-size explosion | **Fail** — `read_models.py` already >1k lines |
| No spaghetti special-case growth | **Pass** — duplication is copy-paste, not scattered `if` |
| No hacky/magical abstraction | **Pass** — pattern is explicit; `_PollingReadModel` is the right shape |
| Boundary / type cleanliness | **Pass** — builders are callables; workers stay off GUI thread |
| Canonical-layer reuse | **Fail** — seven modules ignore the canonical base |
| Obvious decomposition opportunity | **Fail** — extract polling module + split projections |

**Recommendation:** Treat RM-007 as **approved direction, blocked only by sequencing and test rewrites** — not as optional polish. Do not add new `*_state.py` files without inheriting the shared lifecycle. Do not add substantial projection logic to `read_models.py` without splitting projections out first.

---

## 1. Structural regressions

### 1.1 Two polling architectures in one package

`read_models.py` defines a complete, working lifecycle:

```148:200:src/milodex/gui/read_models.py
class _PollingReadModel(QObject):
    """Shared Q_PROPERTY lifecycle for read-only GUI models."""
    # ... timer, per-instance QThreadPool(max=1), in-flight guard,
    # QueuedConnection signals, start/stop with waitForDone(2000) ...
    def _kick_refresh(self) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        self._thread_pool.start(_RefreshRunnable(self._builder, self._signals))
```

`FrontPageState`, `BenchState`, `KanbanState`, and `LedgerState` are thin subclasses: they pass a `builder` callable and implement `_apply_result` only. That is the **inevitable** shape.

`StrategyBankState`, `PerformanceState`, `ActivityFeedState`, `RiskThroughputState`, `ActiveOpsState`, `AttentionState`, and `MarketTapeState` each re-implement the same spine (~70–90 lines per file): `_XRefreshSignals`, `_XRefreshRunnable`, `QTimer` → `_kick_refresh`, `_refresh_in_flight`, `start`/`stop`, `_on_refresh_complete` / `_on_refresh_failed`, `waitForDone(2000)`, defensive signal disconnect.

Module docstrings explicitly cross-reference each other (“Identical to `performance_state`”, “Identical to `strategy_bank_state`”) — the authors knew these were clones. The clones were never collapsed.

**Impact:** Any lifecycle fix (shutdown race, in-flight semantics, error-state contract) must be applied in **eight places** (seven modules + base), or behavior diverges silently.

### 1.2 `read_models.py` is past the 1k-line boundary without a compelling monolith reason

| Region | Approx. lines | Role |
|--------|---------------|------|
| Polling base + 4 QObjects | ~300 | Lifecycle + thin QML bindings |
| `build_*_snapshot` + `_strategy_rows` | ~100 | Orchestration entrypoints |
| Bench menu / evidence / action preview | ~350 | ADR 0050 projection |
| Ledger / session / promotion SQL helpers | ~450 | Event-store projections |
| Kanban / display / misc helpers | ~350 | Shared row derivation |

The file is not 1,552 lines because polling is inherently huge; it is huge because **projections were never split**. `_PollingReadModel` is ~5% of the file. The thermo-nuclear 1k rule applies here: further Bench/Kanban/Ledger features must not land inline without decomposition.

**Required remedy (orthogonal to RM-007 but should be sequenced together):**

| New module | Owns |
|------------|------|
| `gui/polling_lifecycle.py` | `PollingReadModel`, `_RefreshRunnable`, `_RefreshSignals` (public names) |
| `gui/projections/strategy_rows.py` | `_StrategyRow`, `_strategy_rows`, config loading |
| `gui/projections/bench.py` | `_evidence_packet`, `_action_intent_preview`, `_compute_bench_action_menu`, `build_bench_snapshot` |
| `gui/projections/ledger.py` | `_ledger_entries`, `build_ledger_snapshot` |
| `gui/read_models.py` | Thin QObject subclasses + re-exports for stable imports |

Keep `from milodex.gui.read_models import BenchState, build_bench_snapshot` stable during migration.

### 1.3 Tests encode the wrong abstraction

Every duplicated state module has a parallel test file that drives **`_kick_refresh()` directly** and asserts on **`_thread_pool`**, **`_refresh_in_flight`**, and bespoke signal carriers — often with copy-pasted lifecycle tests (“second kick while in flight is a no-op”).

That pattern appears **once per module** (~8–15 call sites each). It made sense when each module owned its lifecycle; it becomes **active friction** against RM-007.

Roadmap done criteria already state the fix: *tests assert behavior through the read-model interface, not private timer fields.*

**Presumptive blocker for migration PRs:** lifecycle tests should move to `tests/milodex/gui/test_polling_lifecycle.py` (one module) + per-state tests that only call `start()`/`stop()` and assert Q_PROPERTY / signal outcomes via public surface.

---

## 2. Missed code-judo moves (high conviction)

### 2.1 Extract and reuse `PollingReadModel` — delete ~500 lines

**The move:** Promote `_PollingReadModel` to `gui/polling_lifecycle.py` as `PollingReadModel`. Migrate each `*State` module to:

```python
class StrategyBankState(PollingReadModel):
    def __init__(self, db_path: Path, refresh_interval_ms: int = 30_000, parent=None):
        self._paper: list = []
        self._blocked: list = []
        super().__init__(
            builder=lambda: _build_bank_snapshot(db_path),
            refresh_interval_ms=refresh_interval_ms,
            parent=parent,
        )

    def _apply_result(self, result: dict) -> None:
        # diff-guarded emits only — same as today
```

Delete per-module: `_BankRefreshSignals`, `_BankRefreshRunnable`, timer/pool wiring, duplicated `start`/`stop`, duplicated `_on_refresh_failed` (base already preserves last-known data on error for `dataStatus`).

**Builder contract:** Normalize on one timestamp key. Today:

- `_PollingReadModel._on_refresh_complete` reads `result.get("lastRefreshedAt")` with `_now_iso()` fallback.
- Adjacent modules emit `refreshed_at` from workers.

Pick **`lastRefreshedAt` in the builder dict** everywhere (or teach the base to accept both once). Do not carry dual keys through migration.

**Proof slice (roadmap-aligned):** `StrategyBankState` — purest match, no broker factory, deepest tests. One behavioral rewrite PR proves the pattern; then batch the other six.

**Estimated deletion:** ~70 lines × 7 modules ≈ **490 lines** of pure scaffolding, plus ~7 × ~25 lines of signal/runnable classes ≈ **175 lines** → **~650 lines** removed before counting test deduplication.

### 2.2 Collapse bespoke runnables into `_RefreshRunnable`

Adjacent modules wrap the same try/emit/fail pattern:

```python
def run(self):
    try:
        ... = _query_*()
        self._signals.completed.emit({...})
    except Exception as exc:
        logger.warning(...)
        self._signals.failed.emit(str(exc))
```

`_RefreshRunnable` already takes `builder: Callable[[], dict]`. Every `_query_*` should become a module-level pure function (many already are, e.g. `_query_bank`) passed as `builder=lambda: {...}`. **Delete seven `_*RefreshRunnable` classes.**

`PerformanceState` and `MarketTapeState` need extra closed-over args (`cache_dir`) — trivial via `lambda` or `functools.partial`; not a reason for a separate runnable type.

### 2.3 `OperationalState` — compose, do not force-fit

`OperationalState` is correctly called out as an outlier:

- Kill-switch: 1 s, **main thread** (local SQLite).
- Broker snapshot: 15 s, **worker pool**, in-flight drop.

Do **not** subclass `PollingReadModel` wholesale. **Code-judo alternative:**

- Extract **`BrokerPollLifecycle`** (or reuse `PollingReadModel` with a broker-only builder) for the broker half only.
- Keep kill-switch timer + `_poll_kill_switch` on `OperationalState` itself.

That removes duplicated broker runnable/signal/stop-drain code **without** lying about a single cadence model.

### 2.4 App shutdown registry (minor)

`app.py` lists twelve `start()` / `stop()` calls by hand. After migration, consider a single `POLLING_MODELS: list` built once in `run_app` and passed to `AppController` — same behavior, one place to register new surfaces. Low priority; only worth doing when touching `run_app` for RM-007 anyway.

---

## 3. What is *not* a problem (close the false positives)

| Claim | Finding |
|-------|---------|
| Bench evidence / action preview duplicated across GUI | **Not duplicated** — only in `read_models.py` |
| Ledger construction duplicated | **Not duplicated** — `build_ledger_snapshot` + `_ledger_entries` are local |
| Kanban lane logic scattered | **Co-located** in `read_models.py` |
| `_kick_refresh` on Bench after commands | **Correct** — `bench_command_bridge.py` calls `_kick_refresh` on `BenchState` / `LedgerState`; stays valid if method remains on `PollingReadModel` base |

Do not spend RM-007 budget “deduplicating” projections. Spend it on **lifecycle** and **file decomposition**.

---

## 4. Spaghetti / branching complexity

**Pass with nuance.** The duplication is **parallel spaghetti** (seven copies of the same path), not tangled special cases inside one path. That is easier to fix than ad-hoc `if strategy_id == ...` in shared modules — but it is still spaghetti at the package level.

`LedgerState._refilter` and Bench action-menu logic are legitimately domain-heavy; they belong in projection modules, not in lifecycle modules.

---

## 5. Boundary and contract notes

| Topic | Assessment |
|-------|------------|
| Per-instance `QThreadPool` | **Correct** — comments in `strategy_bank_state` and `operational_state` explain why not `globalInstance()` |
| `QueuedConnection` on worker signals | **Correct** — consistent everywhere |
| Read-only SQLite (`mode=ro`) in workers | **Correct** — activity feed / bank modules |
| Private `_PollingReadModel` | **Wrong layer** — forces import-from-`read_models` or duplication; extract to shared module |
| `bench_command_bridge` reaching `_kick_refresh` | Acceptable if `_kick_refresh` remains on base class; document as post-command refresh hook |

---

## 6. Suggested implementation sequence (when RM-007 unblocks)

Per roadmap: after RM-006. Order matters for reviewability:

1. **Extract** `gui/polling_lifecycle.py`; re-export from `read_models` for one release if needed.
2. **Add** `test_polling_lifecycle.py` — public `start`/`stop`, in-flight drop, error preserves `dataStatus`, shutdown disconnect (move duplicated tests here).
3. **Migrate** `StrategyBankState` only; rewrite `test_strategy_bank_state.py` to stop pinning `_thread_pool`.
4. **Batch migrate** remaining six DB/cache states (one PR or two).
5. **Refactor** `OperationalState` broker half to shared lifecycle.
6. **Split** `read_models.py` projections (can be parallel if touch conflict risk is high — prefer after step 4 so lifecycle churn is settled).

**Do not:** redesign QML, change read-model schemas, or combine with Bench command bridge cleanup (roadmap scope guard).

---

## 7. Risk ranking (founder priority context)

| Area | Governance / trading risk | Maintainability risk |
|------|---------------------------|---------------------|
| Duplicated polling lifecycle | Low | **High** — drift, shutdown bugs, review fatigue |
| `read_models.py` size | Low | **High** — merge conflicts, hard navigation |
| Test coupling to privates | Low | **Medium** — migration tax |
| OperationalState dual cadence | Low | **Low** — documented, intentional |

Aligns with your ranking: **below strategy/bench/backtest for correctness**, but **worth a focused RM-007 slice** before the GUI surface count grows further.

---

## 8. Findings checklist (priority order)

1. **[Blocker]** Seven modules duplicate `_PollingReadModel` instead of inheriting it — delete ~500–650 LOC via migration.
2. **[Blocker]** `read_models.py` at 1,552 lines — split polling base from projections before new features.
3. **[High]** Tests pin private lifecycle — rewrite as part of migration, not after.
4. **[High]** Normalize `lastRefreshedAt` vs `refreshed_at` in builder results during migration.
5. **[Medium]** Extract `OperationalState` broker poll onto shared lifecycle; keep kill-switch separate.
6. **[Medium]** Optional `app.py` polling registry when touching startup/shutdown.
7. **[Closed]** Projection duplication across modules — not substantiated.

---

## References

- Roadmap: `docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md` — RM-007
- Lifecycle contract doc: `read_models.py` module docstring (lines 1–11)
- Shutdown: `app.py` `AppController.quitRequested` + `run_app` start/stop lists
- Command refresh hook: `bench_command_bridge.py` (`_kick_refresh` on bench/ledger)
- Prior thermo-nuclear report format: `docs/reviews/2026-05-24-thermo-nuclear-bench-command-facade.md`
