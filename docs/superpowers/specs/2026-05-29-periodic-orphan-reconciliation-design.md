# Periodic Orphan Reconciliation — Design

**Date:** 2026-05-29
**Status:** Approved (brainstorming) — pending spec review + implementation plan
**Scope:** PR1 of the post-soak-test robustness sequence (PR1 → PR2 soak-test net)

## Problem

A hard-killed runner whose strategy is never restarted leaves a `strategy_runs`
row with `ended_at IS NULL` forever. The active-ops read model trusts
`ended_at IS NULL` with no liveness check, so the GUI renders that dead process
as a live "phantom" runner — a confidently-wrong status.

A liveness-gated reaper already exists —
`reconcile_orphaned_runs_on_bootstrap` (`src/milodex/strategies/orphan_reconciliation.py:93`) —
but it fires only at **two event-triggered moments**: GUI bootstrap
(`src/milodex/gui/app.py:270`) and same-strategy runner start
(`src/milodex/strategies/runner.py:111`). A runner killed at 11:00 whose
strategy is never restarted and whose GUI is never relaunched stays phantom
until one of those triggers happens to fire. The gap is **"no periodic
trigger,"** not "no reaper."

This was surfaced by the 2026-05-29 concurrent-fleet soak test and confirmed by
an independent Opus 4.8 review, which corrected an earlier (richer) proposal —
a new `last_heartbeat` column + bespoke reaper — as partly redundant with
infrastructure that already exists (the advisory-lock file already carries a
PID + start-time + per-cycle mtime heartbeat). The right fix is **"run the
existing reaper on a timer,"** with no schema change.

## Non-goals

- **No schema change.** No `strategy_runs.last_heartbeat` column; liveness comes
  from the existing advisory-lock holder record (PID + start-time, see
  `orphan_reconciliation._has_live_runner`, lines 44–90).
- **No change to the reaper's core logic.** `reconcile_orphaned_runs_on_bootstrap`
  is reused verbatim.
- **No auto-restart of strategies.** This automates recovery of the *bookkeeping*
  (closing the stale row) only. ADR 0026's core non-goal — the system does not
  resurrect a crashed strategy — stands. See "ADR addendum" below.
- **No launch-managing control plane / supervisor process.** Deferred; revisit
  only if intraday concurrency becomes routine.

## Decisions (operator-confirmed during brainstorming)

| Decision | Choice |
|---|---|
| Trigger home | GUI timer (Qt main thread) **+** a `maintenance reap-orphans` CLI primitive |
| Interval | Configurable, default **60s** |
| Setting location | `RiskOfficeDrawer.qml`, new section below TIME FORMAT |
| Setting control | Preset buttons **30s / 60s / 5min** (matches the 24h/12h toggle idiom) |
| Persistence | **Durable via QSettings** (survives restart, unlike `sessionBag.timeFormat`) |
| ADR | Addendum to ADR 0026 recording the bookkeeping-recovery automation |

## Architecture

### Component 1 — `OrphanReaperController` (QObject, Python/PySide)

A small `QObject` registered to the QML engine, holding:

- `db_path` and `locks_dir` (same inputs `app.py:270` passes the bootstrap call).
- An `intervalSeconds` property (int), default 60.
- An internal `QTimer` running on the **Qt main thread**.

On each `QTimer` timeout it constructs `EventStore(db_path)` (matching the
bootstrap call's per-invocation construction — connections are open-op-close,
so this is cheap and holds nothing), calls
`reconcile_orphaned_runs_on_bootstrap(store, locks_dir, now=datetime.now(tz=UTC))`,
and emits `reaped(list[str])` with the reaped strategy ids. The active-ops read
model listens to `reaped` and refreshes so the phantom badge clears.

Setting `intervalSeconds` restarts the `QTimer` with the new interval. Bounds:
clamp to `[5, 3600]` seconds defensively, though the preset UI only offers
30/60/300.

**Main-thread execution is load-bearing.** `docs/reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md:156`
documents a residual TOCTOU: the reaper reads the lock holder non-locking,
decides "dead," then closes the row and unlinks the lock; a runner that writes
its lock in that window has it wiped. Today that race is bounded to
"microseconds, during an unusual manual race" *because both reap and spawn
route through the GUI surface*. Running the reaper in the controller's
main-thread `QTimer` slot keeps it serialized on the Qt event loop against
start-runner actions — preserving that bound. The controller MUST NOT run the
reaper on a worker thread.

### Component 2 — Setting UI (`RiskOfficeDrawer.qml`)

New section below the existing TIME FORMAT section (`RiskOfficeDrawer.qml:377`),
following the identical signal-up pattern:

- Three preset buttons (30s / 60s / 5min), radio-style, mirroring the
  24-HOUR/12-HOUR toggle at lines 387–428.
- Emits a new `reapIntervalRequested(int seconds)` signal (sibling to
  `timeFormatRequested`, line 41).
- A `readonly property int reapIntervalSeconds` mirror reflecting the current
  value (sibling to the `timeFormat` mirror at line 80), sourced from the
  persisted value so the active preset highlights correctly on open.

`Main.qml` handles `reapIntervalRequested` (sibling to the `timeFormatRequested`
handler at lines 166–170): writes the value through the QSettings-backed store
**and** pushes it into `OrphanReaperController.intervalSeconds`.

### Component 3 — QSettings persistence

The GUI has no durable settings store today (`sessionBag` is explicitly
non-persistent, `Main.qml:77`). Introduce a minimal `QSettings`-backed
read/write for the reap interval:

- Requires `QCoreApplication` org/app name to be set (e.g. `"Milodex"`), or an
  explicit `QSettings` file path. Implementation must verify/establish this in
  the GUI bootstrap (`app.py`) before any `QSettings` read.
- Key: `runner_health/reap_interval_seconds`, default 60.
- Read at bootstrap to seed `OrphanReaperController.intervalSeconds` and the
  drawer's active-preset highlight; written on `reapIntervalRequested`.

This store is intentionally scoped to the one key for v1. It is the seam a
future durable GUI preference would extend, not a general settings framework.

### Component 4 — CLI primitive `milodex maintenance reap-orphans`

Mirrors the shape of `maintenance compact`
(`src/milodex/cli/commands/maintenance.py`). Behavior:

- Extend `maintenance_action` choices to include `"reap-orphans"`.
- Resolve `EventStore(get_data_dir()/"milodex.db")` and `locks_dir`
  (`get_locks_dir()`), call the reaper, report reaped strategy ids.
- `--dry-run`: report what *would* be reaped without mutating. Implemented by a
  read-only pass that lists open runs whose strategy has no live runner (reuse
  `_has_live_runner`), since the reaper itself mutates. (A read-only counterpart
  helper may be extracted alongside, kept in `orphan_reconciliation.py`.)
- **No runtime advisory lock.** Unlike `compact`, the reaper is liveness-gated
  and *intended* to run while other runners are alive (it skips live ones). The
  CLI-vs-concurrent-spawn race is the same narrow bound today's manual
  `milodex strategy run` carries during a bootstrap reconcile; documented,
  accepted for v1.
- Register in BOTH `_COMMAND_MODULES` and `_DISPATCH` if a new module — but
  `maintenance` is already registered in both (`src/milodex/cli/main.py:70,89`),
  so extending the existing module needs no `main.py` change.

## Data flow

```
GUI bootstrap (app.py)
  ├─ set QCoreApplication org/app name (enables QSettings)
  ├─ read QSettings runner_health/reap_interval_seconds (default 60)
  ├─ existing one-shot reconcile_orphaned_runs_on_bootstrap (unchanged)
  └─ construct OrphanReaperController(db_path, locks_dir, interval) → start QTimer

QTimer.timeout (main thread, every interval)
  └─ reconcile_orphaned_runs_on_bootstrap(...) → emit reaped(ids)
       └─ active-ops read model refreshes → phantom badge clears

RiskOfficeDrawer preset click
  └─ reapIntervalRequested(seconds) → Main.qml
       ├─ QSettings write
       └─ OrphanReaperController.intervalSeconds = seconds → QTimer restart

CLI: milodex maintenance reap-orphans [--dry-run]
  └─ reconcile_orphaned_runs_on_bootstrap(...) (or read-only preview) → print ids
```

## ADR addendum (ADR 0026)

ADR 0026 ("Concurrent Multi-Strategy Uses Per-Process Supervisor") states the
operator is the supervisor and explicitly makes crash recovery a non-goal:
*"This ADR does not address what happens when one process crashes mid-cycle —
recovery is operator-driven."* This PR automates a narrow slice of that:
**closing the orphaned `strategy_runs` bookkeeping row on a timer.** It does NOT
restart the strategy. The addendum records:

- The read-model bookkeeping (`ended_at IS NULL` → live) is now auto-reconciled
  on a timer and on demand via CLI, in addition to the existing event triggers.
- The strategy process itself is still never auto-restarted — 0026's core
  per-process, operator-driven model is unchanged.
- The reaper remains liveness-gated; a genuinely-running runner is never reaped.

## Testing

Core reaper logic is already covered by
`tests/milodex/strategies/test_orphan_reconciliation.py`. New tests:

- **`OrphanReaperController`** (`tests/milodex/gui/...`): setting `intervalSeconds`
  restarts the timer with the new interval; a timer tick invokes the reaper and
  emits `reaped` with the reaped ids (inject a fake/seeded store + locks_dir);
  interval clamps to `[5, 3600]`.
- **QSettings persistence**: write → read round-trips the interval; default is 60
  when unset.
- **CLI `reap-orphans`**: dry-run lists candidates without mutating; apply closes
  orphans and prints ids; JSON contract; dry-run + apply on a seeded store.
- **Drawer QML smoke** (`tests/milodex/gui/test_qml_load_smoke.py`): the new
  RUNNER HEALTH section loads; `reapIntervalRequested` is emitted on preset
  click; the active preset reflects the seeded value. (Note: this smoke test
  substring-asserts QML source — any literal added must match the asserted
  strings.)

The main-thread serialization constraint is enforced by construction (reaper
runs only in the controller's main-thread `QTimer` slot) and documented in the
controller; it is not separately unit-tested (a true concurrency assertion
belongs to PR2's soak test).

## Files touched

- `src/milodex/strategies/orphan_reconciliation.py` — (maybe) extract a read-only
  candidate-listing helper for `--dry-run`. Core reaper unchanged.
- `src/milodex/gui/orphan_reaper_controller.py` — NEW `OrphanReaperController`.
- `src/milodex/gui/app.py` — set org/app name; seed QSettings; construct +
  start controller; wire `reaped` to the read-model refresh.
- `src/milodex/gui/qml/Milodex/Main.qml` — `reapIntervalRequested` handler;
  QSettings write; push to controller.
- `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml` — RUNNER HEALTH
  section + signal + mirror property.
- `src/milodex/cli/commands/maintenance.py` — `reap-orphans` action + `--dry-run`.
- `docs/adr/0026-*.md` — addendum.
- Tests as above.

## Sizing

Tiny-to-small. The reaper exists and is tested; this is trigger plumbing + a
setting + a CLI verb + an ADR addendum.
