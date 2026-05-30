# Periodic Orphan Reconciliation — Design

**Date:** 2026-05-29
**Status:** Spec-review converged (iter 2 conditional-approve; iter-3 fixes applied) — pending operator review
**Scope:** PR1 of the post-soak-test robustness sequence (PR1 → PR2 soak-test net)

## Problem

A hard-killed runner whose strategy is never restarted leaves a `strategy_runs`
row with `ended_at IS NULL` forever. The active-ops read model trusts
`ended_at IS NULL` with no liveness check, so the GUI renders that dead process
as a live "phantom" runner — a confidently-wrong status.

A liveness-gated reaper already exists —
`reconcile_orphaned_runs_on_bootstrap` (`src/milodex/strategies/orphan_reconciliation.py:93`) —
but it fires only at **two event-triggered moments**: GUI bootstrap
(`src/milodex/gui/app.py:270`, the liveness-gated module reaper) and
same-strategy runner start (`src/milodex/strategies/runner.py:111`, which calls
the *lower-level* `event_store.reconcile_orphan_strategy_runs` directly, not the
liveness-gated bootstrap function). A runner killed at 11:00 whose strategy is
never restarted and whose GUI is never relaunched stays phantom until one of
those triggers happens to fire. The gap is **"no periodic trigger,"** not
"no reaper."

Surfaced by the 2026-05-29 concurrent-fleet soak test; scope corrected across
two independent Opus 4.8 reviews. The first review rejected a richer proposal (a
new `last_heartbeat` column + bespoke reaper) as redundant with existing
advisory-lock liveness. The second (spec) review caught a **false safety claim**
in the first draft of this spec — see "Concurrency safety" below — which forces a
small, justified expansion of scope into the reaper's core.

## Non-goals

- **No schema change.** No `strategy_runs.last_heartbeat` column; liveness comes
  from the existing advisory-lock holder record (PID + start-time, see
  `orphan_reconciliation._has_live_runner`, lines 44–90).
- **No auto-restart of strategies.** This automates recovery of the *bookkeeping*
  (closing the stale row) only. ADR 0026's core posture — the system does not
  resurrect a crashed strategy; the operator is the supervisor — stands. See
  "ADR addendum."
- **No launch-managing control plane / supervisor process.** Deferred; revisit
  only if intraday concurrency becomes routine.
- **No event-driven badge refresh.** The phantom badge clears on the active-ops
  read model's existing 30s poll, not via a push from the reaper (see
  "Concurrency safety" and "Read-model staleness").

## In scope, and why it grew

The first draft claimed "no change to the reaper's core logic." That is no longer
true: this PR **lands residual-1's deferred TOCTOU fix** inside the reaper. This
is not optional polish — it is the prerequisite that makes periodic reaping safe
(see "Concurrency safety"). The reaper's *liveness classification* is unchanged;
what's added is a re-check guard immediately before the mutating close+unlink.

## Decisions (operator-confirmed during brainstorming)

| Decision | Choice |
|---|---|
| Trigger home | GUI timer (Qt main thread) **+** a `maintenance reap-orphans` CLI primitive |
| Interval | Configurable, default **60s** |
| Setting location | `RiskOfficeDrawer.qml`, new section below TIME FORMAT |
| Setting control | Preset buttons **30s / 60s / 5min** (matches the 24h/12h toggle idiom) |
| Persistence | **Durable via QSettings** (survives restart, unlike `sessionBag.timeFormat`) |
| ADR | Addendum to ADR 0026 recording the bookkeeping-recovery automation |

## Concurrency safety (the load-bearing section)

**The first draft's central claim was false and is removed.** It asserted that
running the reaper in a main-thread `QTimer` slot serializes it against
start-runner actions on the Qt event loop, preserving residual-1's bound. It does
not. The GUI's primary start path runs on a **worker thread**, not the main
thread: `BenchCommandBridge` dispatches `_SubmitRunnable(QRunnable)`
(`src/milodex/gui/bench_command_bridge.py:58`) onto a `QThreadPool`
(`bench_command_bridge.py:141`, started at `:244`), and the submit calls
`subprocess.Popen` (`src/milodex/strategies/paper_runner_control.py:254`) on that
worker thread. The spawned subprocess writes its advisory lock. A main-thread
reaper tick therefore runs **concurrently** with a spawn — it does not serialize
against it.

Residual-1 (`docs/reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md:156`)
is precisely this race: the reaper reads `current_holder()` non-locking, decides
"dead," closes the row, then unlinks the lock; a runner that writes its lock in
that window has it wiped → two runners on one logical lock. Today it is bounded to
"microseconds, unusual CLI-during-bootstrap workflow" *only because* reap and
spawn route through the same surface serially (review line 160). **A 60s periodic
reaper running concurrently with the worker-thread spawn destroys that bound** —
every operator "Start paper runner" click now overlaps possible reaper ticks.

**Fix (residual-1's deferred remedy, review line 162):** make the close+unlink
conditional on a final re-check.

First, **refactor `_has_live_runner` to return `(is_live: bool, holder:
LockHolder | None)`** — today it returns a bare `bool` (`orphan_reconciliation.py:44,90`)
and discards the holder it read at `:69`. The reaper needs the *exact* holder
snapshot the liveness decision was made against; it must come from that single
read, **not** a second independent `current_holder()` call (a second read would
reintroduce a TOCTOU inside the classify step). `_has_live_runner` is local to
this module — the only call site is the reaper loop.

Then, in the reaper loop, after the `(False, snapshot)` classification, and
immediately before mutating, re-read `current_holder()`:

- If a holder now exists that was **not** there before (snapshot was `None`), or
  whose `started_at` differs from the snapshot (a fresh runner wrote its lock in
  the window), **skip the strategy entirely** — do not close the row, do not
  unlink. The next tick re-evaluates.
- Otherwise proceed with close + unlink as today.

The residual window is negligible because it is **a few instructions wide** —
the gap between the re-check read and the `unlink` (`orphan_reconciliation.py:120`)
— *not* because of any `started_at` collision (a fresh runner stamps
`started_at = now()` at its own `acquire()`, `advisory_lock.py:166`, so it will
never match the dead holder's value; the comparison is what detects the fresh
lock). This is the bound the review already specified; this PR pays the cost now
that periodic + worker-thread spawn makes the workflow first-class.

**Load-bearing ordering invariant.** The single skip guards *both* the row-close
and the unlink, and that is sound **only because the spawning subprocess acquires
its lock before it appends its open row**: `strategy.py` enters `with
runner_lock:` (`:126`) before constructing `StrategyRunner`, whose `__init__`
appends the open `strategy_runs` row (`runner.py:116`). Lock-precedes-row means
any new open row the reaper could see implies its lock is already on disk, so the
`current_holder()` re-check sees it and skips. If that sequence were ever
reversed, the guard would silently let a live runner's row be closed. Any future
reorder of lock-vs-row in `strategy.py` breaks this guard.

## Architecture

### Component 1 — `OrphanReaperController` (QObject, Python/PySide)

A small `QObject` registered to the QML engine (following the existing
bridge-registration house style used for `RiskProfileBridge` / `BenchCommandBridge`
in `app.py`), holding:

- A single long-lived `EventStore` instance (constructed once in `__init__`) and
  `locks_dir`. `EventStore` holds no persistent connection between calls
  (open-op-close, `event_store.py:_connect`), so one instance is reused across
  ticks. This deliberately avoids re-running `EventStore.__init__`'s idempotent
  migration fast-path (`event_store.py:328-338`) every tick, and makes the
  controller trivially injectable in tests.
- An `intervalSeconds` property (int), default 60, clamped to `[5, 3600]`.
- An internal `QTimer` on the Qt main thread.

On each timeout it calls
`reconcile_orphaned_runs_on_bootstrap(self._store, self._locks_dir, now=datetime.now(tz=UTC))`
and emits `reaped(list[str])` with the reaped ids **for logging/telemetry only**
(not wired to a read-model refresh — see below). Setting `intervalSeconds`
restarts the `QTimer`.

The controller runs the reaper on the main thread because that is simplest and
the reaper is cheap — **not** because it provides a serialization guarantee (it
does not; see Concurrency safety). The TOCTOU is handled inside the reaper, not
by thread placement.

### Component 2 — Setting UI (`RiskOfficeDrawer.qml`)

New section below the existing TIME FORMAT section (`RiskOfficeDrawer.qml:377`),
following the identical signal-up pattern:

- Three preset buttons (30s / 60s / 5min), radio-style, mirroring the
  24-HOUR/12-HOUR toggle at lines 387–428.
- Emits a new `reapIntervalRequested(int seconds)` signal (sibling to
  `timeFormatRequested`, line 41).
- A `readonly property int reapIntervalSeconds` mirror (sibling to the
  `timeFormat` mirror at line 80), sourced from the persisted value so the active
  preset highlights correctly on open.

`Main.qml` handles `reapIntervalRequested` (sibling to the `timeFormatRequested`
handler at lines 166–172): writes through the QSettings-backed store **and**
pushes the value into `OrphanReaperController.intervalSeconds`.

### Component 3 — QSettings persistence

The GUI has no durable settings store today (`sessionBag` is explicitly
non-persistent, `Main.qml:77-85`; grep confirms no `QSettings` /
`setOrganizationName` anywhere under `src/milodex`). Introduce a minimal
`QSettings`-backed read/write for the one key:

- **Prerequisite:** set `QCoreApplication.setOrganizationName("Milodex")` and
  `setApplicationName("Milodex")` in `app.py` immediately after the
  `QGuiApplication` is constructed (`app.py:201-203`) and **before** any
  `QSettings` read. There is no earlier `QSettings` read today, so ordering risk
  is low — verify none is introduced ahead of it.
- Key: `runner_health/reap_interval_seconds`, default 60.
- Read at bootstrap to seed both `OrphanReaperController.intervalSeconds` and the
  drawer's active-preset highlight; written on `reapIntervalRequested`.

Scoped to the one key for v1 — the seam a future durable GUI preference extends,
not a general settings framework.

### Component 4 — CLI primitive `milodex maintenance reap-orphans`

Mirrors `maintenance compact` (`src/milodex/cli/commands/maintenance.py:20-104`).
`maintenance` is already registered in both `_COMMAND_MODULES` and `_DISPATCH`
(`src/milodex/cli/main.py:70,89`), so extending the existing module needs no
`main.py` change. Behavior:

- Extend `maintenance_action` choices to include `"reap-orphans"`.
- Resolve `EventStore(get_data_dir()/"milodex.db")` and `get_locks_dir()`, call
  the reaper, report reaped strategy ids.
- `--dry-run`: print the candidates without mutating.
- **No runtime advisory lock** (unlike `compact`, `maintenance.py:61`). The reaper
  is liveness-gated and *intended* to run alongside live runners (it skips them).
  The residual-1 re-check guard (above) now covers the CLI-vs-spawn race that this
  lock-free design would otherwise expose.

### Shared candidate helper (eliminates dry-run drift)

`reconcile_orphaned_runs_on_bootstrap` already owns the authoritative
candidate logic (`orphan_reconciliation.py:104-122`) and returns the reaped ids.
To give `--dry-run` the *same* selection without a parallel implementation,
extract one helper:

```
_orphan_candidates(event_store, locks_dir) -> list[str]
    # open runs whose strategy has no live runner; sorted; pure/read-only
```

The reaper loops `_orphan_candidates(...)`, applies the re-check guard, then
mutates. `--dry-run` calls `_orphan_candidates(...)` and prints. One source of
truth, zero drift.

## Read-model staleness (replaces the dropped event-refresh)

`ActiveOpsState` is a `PollingReadModel` (`src/milodex/gui/.../active_ops_state.py`)
with an internal 30s `QTimer` and no public force-refresh slot. The first draft's
"read model listens to `reaped` and refreshes" had no attachment point and is
**dropped**. Instead the two timers compose: the reaper closes the orphaned row on
its interval; the active-ops poll reflects the closed row on its next cycle.

Worst-case badge staleness = reap_interval + poll_interval (≈ 60s + 30s = ~90s
with defaults) — acceptable for a display-correctness badge, and documented. If a
snappier clear is ever wanted, the clean follow-up is a `@Slot() forceRefresh()`
on `ActiveOpsState` wired to `controller.reaped`; explicitly out of scope for v1.

## Data flow

```
GUI bootstrap (app.py)
  ├─ setOrganizationName/setApplicationName  (enables QSettings)
  ├─ read QSettings runner_health/reap_interval_seconds (default 60)
  ├─ existing one-shot reconcile_orphaned_runs_on_bootstrap (now with re-check guard)
  └─ construct OrphanReaperController(store, locks_dir, interval) → start QTimer

QTimer.timeout (main thread, every interval)
  └─ reconcile_orphaned_runs_on_bootstrap(...) → close+unlink guarded by re-check
       └─ emit reaped(ids)  [log/telemetry only]
   (active-ops 30s poll independently reflects the closed row → badge clears)

RiskOfficeDrawer preset click
  └─ reapIntervalRequested(seconds) → Main.qml
       ├─ QSettings write
       └─ OrphanReaperController.intervalSeconds = seconds → QTimer restart

CLI: milodex maintenance reap-orphans [--dry-run]
  └─ --dry-run: _orphan_candidates(...) → print
     apply:     reconcile_orphaned_runs_on_bootstrap(...) → print reaped ids
```

## ADR addendum (ADR 0026)

ADR 0026 ("Concurrent Multi-Strategy Uses Per-Process Supervisor") keeps the
runner unchanged and makes the operator the supervisor; crash recovery is
operator-driven (it does not auto-restart a crashed strategy). This PR automates
a narrow slice — **closing the orphaned `strategy_runs` bookkeeping row on a
timer and on demand** — without restarting the strategy. The addendum records
(paraphrasing 0026, not quoting verbatim — the implementer must confirm any
quoted sentence exists before citing it):

- The read-model bookkeeping (`ended_at IS NULL` → live) is now auto-reconciled on
  a timer and via `maintenance reap-orphans`, in addition to the existing event
  triggers.
- The strategy process is still never auto-restarted — 0026's per-process,
  operator-driven model is unchanged.
- The reaper remains liveness-gated and now re-checks the lock holder before the
  close+unlink, closing residual-1 (which 0026-era code deferred) because periodic
  reaping + the worker-thread async spawn makes that race first-class.

## Testing

Core reaper liveness logic is covered by
`tests/milodex/strategies/test_orphan_reconciliation.py`. New / changed tests:

- **Residual-1 re-check guard** (in `test_orphan_reconciliation.py`): simulate a
  fresh lock appearing between the liveness read and the unlink (holder absent at
  classify time, then a holder with a *newer* `started_at` present at re-check) →
  assert the strategy is **skipped**: row stays open, lock not unlinked. This is
  the regression guard for the whole safety argument. **Mechanism:** this requires
  a *sequenced* mock on the holder read (returns `None`/dead-holder on the classify
  call, then a fresh holder on the re-check call) — a single statically-planted
  lock can't exercise the gap (that steady state is already covered by
  `test_leaves_open_run_with_live_lock_holder`). Drive it via a side-effecting
  stub on `current_holder()` (e.g. a list of return values consumed per call).
- **`_orphan_candidates` helper**: returns the same set the reaper acts on; pure
  (no mutation).
- **`OrphanReaperController`** (`tests/milodex/gui/...`): setting `intervalSeconds`
  restarts the timer and clamps to `[5, 3600]`; a tick invokes the reaper and
  emits `reaped` (inject a seeded store + locks_dir); reuses one `EventStore`.
- **QSettings persistence**: write → read round-trips; default 60 when unset.
- **CLI `reap-orphans`**: dry-run lists candidates without mutating; apply closes
  orphans + prints ids; JSON contract.
- **Drawer QML smoke** (`tests/milodex/gui/test_qml_load_smoke.py`): the new
  RUNNER HEALTH section loads; `reapIntervalRequested` emitted on preset click;
  active preset reflects the seeded value. **Trap:** this smoke test
  substring-asserts QML source (`test_qml_load_smoke.py:378-394` etc.) — any new
  literal must match the asserted strings, and new asserted strings must be added
  in lockstep.

The concurrency *interleaving* itself (worker-thread spawn vs main-thread reaper)
is not unit-tested here — a true concurrency assertion belongs to PR2's soak test.
What PR1 tests is the **guard's logic** (skip-on-holder-change), which is the
deterministic core of the safety fix.

## Files touched

- `src/milodex/strategies/orphan_reconciliation.py` — change `_has_live_runner`
  to return `(is_live, holder)`; extract `_orphan_candidates`; add the residual-1
  re-check guard (using that holder snapshot) before close+unlink in
  `reconcile_orphaned_runs_on_bootstrap`. **(core logic change, justified above)**
- `src/milodex/gui/orphan_reaper_controller.py` — NEW `OrphanReaperController`.
- `src/milodex/gui/app.py` — set org/app name; seed QSettings; construct + start
  controller. (No `reaped`→read-model wiring in v1.)
- `src/milodex/gui/qml/Milodex/Main.qml` — `reapIntervalRequested` handler;
  QSettings write; push to controller.
- `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml` — RUNNER HEALTH
  section + signal + mirror property.
- `src/milodex/cli/commands/maintenance.py` — `reap-orphans` action + `--dry-run`.
- `docs/adr/0026-*.md` — addendum.
- Tests as above.

## Sizing

Small. The reaper exists; this is the residual-1 guard + trigger plumbing + a
setting + a CLI verb + an ADR addendum. The residual-1 guard is the only part
touching genuinely delicate logic and gets the most adversarial test.
