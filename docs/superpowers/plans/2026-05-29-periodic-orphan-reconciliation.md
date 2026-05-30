# Periodic Orphan Reconciliation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the phantom `strategy_runs` gap (dead runners shown as "running") by triggering the existing liveness-gated reaper periodically (GUI timer + CLI), hardened with residual-1's re-check guard so periodic reaping is safe against the worker-thread async spawn.

**Architecture:** Reuse `reconcile_orphaned_runs_on_bootstrap`. Refactor its liveness check to surface the holder snapshot, extract a shared candidate-selection helper, and guard the close+unlink with a final holder re-check. Add a main-thread `QTimer` controller (one reused `EventStore`), a durable QSettings-backed interval, a drawer setting, and a `maintenance reap-orphans` CLI verb.

**Tech Stack:** Python 3.11, PySide6 (QObject/QTimer/QSettings), pytest, QML (Qt Quick), SQLite event store.

**Spec:** `docs/superpowers/specs/2026-05-29-periodic-orphan-reconciliation-design.md`

**Branch:** `feat/periodic-orphan-reaper` (already created; spec already committed).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/milodex/strategies/orphan_reconciliation.py` | Liveness check returns holder; `_orphan_candidates` helper; re-check guard | Modify |
| `src/milodex/gui/orphan_reaper_controller.py` | Main-thread QTimer driving the reaper; `reaped` signal; reused EventStore | Create |
| `src/milodex/gui/runner_health_settings.py` | Durable QSettings read/write of the reap interval (explicit org/app constructor) | Create |
| `src/milodex/gui/app.py` | Set org/app name; seed interval; construct/start/register controller | Modify |
| `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml` | RUNNER HEALTH preset buttons + `reapIntervalRequested` + mirror | Modify |
| `src/milodex/gui/qml/Milodex/Main.qml` | Handle `reapIntervalRequested` → QSettings write + controller push | Modify |
| `src/milodex/cli/commands/maintenance.py` | `reap-orphans` action + `--dry-run` | Modify |
| `docs/adr/0026-*.md` | Addendum: bookkeeping-recovery automation | Modify |
| `tests/milodex/strategies/test_orphan_reconciliation.py` | Guard + helper + tuple-return regression | Modify |
| `tests/milodex/gui/test_orphan_reaper_controller.py` | Controller behavior | Create |
| `tests/milodex/gui/test_runner_health_settings.py` | QSettings round-trip | Create |
| `tests/milodex/cli/test_maintenance.py` | `reap-orphans` CLI | Modify |
| `tests/milodex/gui/test_qml_load_smoke.py` | Drawer RUNNER HEALTH smoke | Modify |

---

## Task 1: Reaper core — holder snapshot, candidate helper, re-check guard

This is the delicate task (the safety fix). Do it first and fully TDD it. The existing
`reconcile_orphaned_runs_on_bootstrap` and `_has_live_runner` live in
`src/milodex/strategies/orphan_reconciliation.py` (read it first).

**Files:**
- Modify: `src/milodex/strategies/orphan_reconciliation.py`
- Test: `tests/milodex/strategies/test_orphan_reconciliation.py`

- [ ] **Step 1: Run the existing suite to confirm green baseline**

Run: `python -m pytest tests/milodex/strategies/test_orphan_reconciliation.py -q`
Expected: PASS (all existing tests green — this is the regression floor).

- [ ] **Step 2: Write the failing test for the residual-1 re-check guard**

Add to `tests/milodex/strategies/test_orphan_reconciliation.py`. This test plants an
open run with NO lock (so classify-time sees the strategy as dead), then makes
`current_holder()` return a *fresh* holder on the re-check call — simulating a runner
that wrote its lock in the window. Assert the run stays open and no lock is unlinked.

```python
def test_recheck_guard_skips_when_fresh_holder_appears(tmp_path, monkeypatch):
    """A runner that writes its lock between classify and unlink must be spared:
    the row stays open and the (new) lock is not unlinked."""
    store = EventStore(tmp_path / "milodex.db")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    _open_run(store, session_id="s-1", strategy_id="strat.x.v1")

    fresh = LockHolder(
        pid=os.getpid(),
        hostname="test-host",
        holder_name="fresh runner",
        started_at=datetime(2026, 5, 18, 12, 0, tzinfo=UTC),
        path=locks_dir / f"{runner_lock_name('strat.x.v1')}.lock",
    )
    # Sequence current_holder(): None at classify time, fresh holder at re-check.
    calls = iter([None, fresh])

    def fake_current_holder(self):
        try:
            return next(calls)
        except StopIteration:
            return fresh

    monkeypatch.setattr(AdvisoryLock, "current_holder", fake_current_holder)

    now = datetime(2026, 5, 18, 20, 0, tzinfo=UTC)
    closed = reconcile_orphaned_runs_on_bootstrap(store, locks_dir, now=now)

    assert closed == []  # skipped, not reaped
    assert [r for r in store.list_strategy_runs() if r.ended_at is None]  # still open
```

Add `from milodex.core.advisory_lock import AdvisoryLock, LockHolder` to imports if
not already present (`AdvisoryLock` is imported; add `LockHolder`).

The `[None, fresh]` sequence maps to exactly two `current_holder()` calls — one in
`_has_live_runner` during classify (returns `None` → early return before any
`_process_exists` probe), one in the guard re-check. Add a comment in the test noting
this depends on the `holder is None` early-return short-circuit, so a future reorder of
`_has_live_runner` (probing the process before the None check) would desync the iterator.

- [ ] **Step 3: Run it — verify it fails**

Run: `python -m pytest tests/milodex/strategies/test_orphan_reconciliation.py::test_recheck_guard_skips_when_fresh_holder_appears -v`
Expected: FAIL — current code closes the run (returns `["strat.x.v1"]`) because there
is no re-check; `closed == []` assertion fails.

- [ ] **Step 4: Refactor `_has_live_runner` to return `(is_live, holder)`**

Change the signature and the two return paths. The holder is read at the top; return it
alongside the boolean so the caller gets the exact snapshot the decision was made on.

```python
def _has_live_runner(
    strategy_id: str, locks_dir: Path
) -> tuple[bool, LockHolder | None]:
    """Return (is_live, holder_snapshot).

    holder_snapshot is the LockHolder read for the decision (or None if absent),
    so a caller can re-check against the *same* snapshot without a second read.
    """
    from milodex.core.advisory_lock import (
        AdvisoryLock,
        _process_exists,
        _process_start_time,
    )

    lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
    holder = lock.current_holder()
    if holder is None or not _process_exists(holder.pid):
        return False, holder
    proc_start = _process_start_time(holder.pid)
    if proc_start is None:
        _logger.warning(  # unchanged warning text/body
            "Orphan reconcile: process-start-time introspection unavailable "
            "for pid %d (holder of strategy %r). Falling back to bare PID-"
            "existence check — a recycled PID in this regime would be mis-"
            "classified as a live runner. See docs/reviews/"
            "2026-05-19-orphan-reconcile-pid-reuse-defect.md.",
            holder.pid,
            strategy_id,
        )
        return True, holder
    return proc_start <= holder.started_at + _PID_REUSE_GRACE, holder
```

Add `LockHolder` to the `if TYPE_CHECKING:` import block (it's only a type here):
`from milodex.core.advisory_lock import LockHolder` alongside the existing
`EventStore` type import.

- [ ] **Step 5: Extract `_orphan_candidates` and add the re-check guard in the reaper**

Replace the body of `reconcile_orphaned_runs_on_bootstrap` so candidate selection is a
shared helper returning `(strategy_id, dead_snapshot)` pairs, and the mutation loop
re-reads the holder immediately before close+unlink, skipping if it changed.

```python
def _orphan_candidates(
    event_store: EventStore, locks_dir: Path
) -> list[tuple[str, LockHolder | None]]:
    """Open-run strategies with no live runner, each paired with the holder
    snapshot the liveness decision saw. Pure / read-only. Sorted by strategy id."""
    open_strategy_ids = sorted(
        {run.strategy_id for run in event_store.list_strategy_runs() if run.ended_at is None}
    )
    candidates: list[tuple[str, LockHolder | None]] = []
    for strategy_id in open_strategy_ids:
        is_live, snapshot = _has_live_runner(strategy_id, locks_dir)
        if not is_live:
            candidates.append((strategy_id, snapshot))
    return candidates


def reconcile_orphaned_runs_on_bootstrap(
    event_store: EventStore,
    locks_dir: Path,
    *,
    now: datetime,
) -> list[str]:
    """Close open strategy runs whose strategy has no live runner.

    Liveness-gated and idempotent. Before the mutating close+unlink, re-reads the
    advisory-lock holder and skips the strategy if a holder appeared or its
    started_at changed since classification — a runner that wrote its lock in the
    window (the residual-1 TOCTOU). See the spec's Concurrency safety section.
    """
    from milodex.core.advisory_lock import AdvisoryLock

    closed: list[str] = []
    for strategy_id, snapshot in _orphan_candidates(event_store, locks_dir):
        lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
        current = lock.current_holder()
        snapshot_started = snapshot.started_at if snapshot else None
        current_started = current.started_at if current else None
        if current_started != snapshot_started:
            # A fresh lock appeared (or the holder changed) between classify and
            # mutate — leave both the row and the lock alone. Next tick re-evaluates.
            _logger.info(
                "Orphan reconcile: skipping %r — lock holder changed during the "
                "re-check window (snapshot=%s, current=%s).",
                strategy_id,
                snapshot_started,
                current_started,
            )
            continue
        event_store.reconcile_orphan_strategy_runs(
            strategy_id=strategy_id,
            ended_at=now,
            exit_reason=_ORPHAN_EXIT_REASON,
        )
        _stale_lock_path(strategy_id, locks_dir).unlink(missing_ok=True)
        closed.append(strategy_id)
    return closed
```

(The guard relies on lock-before-row ordering in `strategy.py` — documented in the
spec; do not reorder that sequence.)

- [ ] **Step 6: Run the new test + the full file**

Run: `python -m pytest tests/milodex/strategies/test_orphan_reconciliation.py -q`
Expected: PASS — the new guard test passes AND every pre-existing test still passes
(the tuple-return refactor and candidate extraction must not regress them).

- [ ] **Step 7: Add a `_orphan_candidates` direct test**

```python
def test_orphan_candidates_lists_dead_open_strategies(tmp_path):
    from milodex.strategies.orphan_reconciliation import _orphan_candidates
    store = EventStore(tmp_path / "milodex.db")
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    _open_run(store, session_id="s-1", strategy_id="strat.x.v1")  # dead (no lock)
    candidates = _orphan_candidates(store, locks_dir)
    assert [sid for sid, _ in candidates] == ["strat.x.v1"]
```

Run: `python -m pytest tests/milodex/strategies/test_orphan_reconciliation.py -q`
Expected: PASS.

- [ ] **Step 8: Lint + commit**

```bash
python -m ruff check src/milodex/strategies/orphan_reconciliation.py tests/milodex/strategies/test_orphan_reconciliation.py
python -m ruff format src/milodex/strategies/orphan_reconciliation.py tests/milodex/strategies/test_orphan_reconciliation.py
git add src/milodex/strategies/orphan_reconciliation.py tests/milodex/strategies/test_orphan_reconciliation.py
git commit -m "fix(reconcile): re-check lock holder before reap (residual-1 guard)

Refactor _has_live_runner -> (is_live, holder); extract _orphan_candidates;
guard close+unlink with a final holder re-check so a runner that wrote its
lock in the window is spared. Prereq for safe periodic reaping.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: CLI `maintenance reap-orphans` + `--dry-run`

`maintenance` is registered in both `_COMMAND_MODULES` and `_DISPATCH`
(`src/milodex/cli/main.py:70,89`) — no `main.py` change. `CommandContext` exposes
`get_event_store()` and `locks_dir`. The reaper is liveness-gated, so **no runtime
advisory lock** (unlike `compact`).

**Files:**
- Modify: `src/milodex/cli/commands/maintenance.py`
- Test: `tests/milodex/cli/test_maintenance.py`

- [ ] **Step 1: Write the failing CLI dry-run test**

The real harness in `tests/milodex/cli/test_maintenance.py` is a local `_run(argv,
tmp_path)` returning `(code, out, err)` where `out`/`err` are `StringIO`; JSON tests
assert via `json.loads(out.getvalue())["data"]`. Use it — do NOT invent `run_cli`/
`result.stdout`. The dry-run must list candidates and NOT close the row.

```python
def test_reap_orphans_dry_run_lists_without_mutating(tmp_path):
    # seed an open strategy_run with no live lock (mirror how the file seeds state;
    # likely a get_data_dir/locks_dir patch + EventStore.append_strategy_run)
    code, out, err = _run(["maintenance", "reap-orphans", "--dry-run", "--json"], tmp_path)
    assert code == 0
    payload = json.loads(out.getvalue())
    assert "strat.x.v1" in payload["data"]["candidates"]
    # row still open afterwards
    assert [r for r in store.list_strategy_runs() if r.ended_at is None]
```

Ground the seeding + `tmp_path`→`get_data_dir`/`get_locks_dir` wiring against the
existing compact tests in the same file before writing.

- [ ] **Step 2: Run it — verify it fails**

Run: `python -m pytest tests/milodex/cli/test_maintenance.py::test_reap_orphans_dry_run_lists_without_mutating -v`
Expected: FAIL — `reap-orphans` is not a valid `maintenance_action` choice (argparse error).

- [ ] **Step 3: Implement the action**

In `register()`: extend choices to `("compact", "reap-orphans")`, and add:
```python
parser.add_argument(
    "--dry-run",
    action="store_true",
    dest="maintenance_dry_run",
    help="(reap-orphans) List orphan candidates without closing any run.",
)
```

In `run()`: branch on the action at the top, BEFORE acquiring the compact runtime lock
(reap-orphans must not take it):
```python
def run(args, ctx):
    action = getattr(args, "maintenance_action", "compact")
    if action == "reap-orphans":
        return _run_reap_orphans(args, ctx)
    # ... existing compact body unchanged ...
```

Add `_run_reap_orphans`:
```python
def _run_reap_orphans(args, ctx):
    from datetime import UTC, datetime

    from milodex.strategies.orphan_reconciliation import (
        _orphan_candidates,
        reconcile_orphaned_runs_on_bootstrap,
    )

    event_store = ctx.get_event_store()
    if getattr(args, "maintenance_dry_run", False):
        candidates = [sid for sid, _ in _orphan_candidates(event_store, ctx.locks_dir)]
        return CommandResult(
            command="maintenance.reap-orphans",
            data={"applied": False, "candidates": candidates},
            human_lines=[
                "Reap-orphans dry-run (no changes):",
                *(f"  would close: {sid}" for sid in candidates),
                f"  {len(candidates)} orphan run(s) would be closed."
                if candidates else "  no orphan runs.",
            ],
        )
    reaped = reconcile_orphaned_runs_on_bootstrap(
        event_store, ctx.locks_dir, now=datetime.now(tz=UTC)
    )
    return CommandResult(
        command="maintenance.reap-orphans",
        data={"applied": True, "reaped": reaped},
        human_lines=[f"Reaped {len(reaped)} orphan run(s): {', '.join(reaped) or '(none)'}."],
    )
```

- [ ] **Step 4: Run the dry-run test + add apply + json tests**

Add `test_reap_orphans_apply_closes_and_reports` (asserts the row is closed and the id
printed) and `test_reap_orphans_json_contract` (assert `data` keys via `--json`).
Run: `python -m pytest tests/milodex/cli/test_maintenance.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check src/milodex/cli/commands/maintenance.py tests/milodex/cli/test_maintenance.py
python -m ruff format src/milodex/cli/commands/maintenance.py tests/milodex/cli/test_maintenance.py
git add src/milodex/cli/commands/maintenance.py tests/milodex/cli/test_maintenance.py
git commit -m "feat(cli): milodex maintenance reap-orphans [--dry-run]

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `OrphanReaperController` (main-thread QTimer)

A `QObject` driving the reaper on the Qt main thread. Reuses ONE `EventStore`.
`reaped(list)` is informational (logged), not wired to a read-model refresh.

**Files:**
- Create: `src/milodex/gui/orphan_reaper_controller.py`
- Test: `tests/milodex/gui/test_orphan_reaper_controller.py`

**Test note:** drive behavior deterministically — call the reap slot directly and set
`intervalSeconds` directly; do NOT wait on a real timer firing. Use a `QCoreApplication`
fixture if one is needed for `QObject`/`QTimer` construction (mirror existing GUI tests'
app fixture).

- [ ] **Step 1: Write failing tests**

```python
def test_interval_property_clamps_and_restarts_timer(qapp):
    c = OrphanReaperController(event_store=FakeStore(), locks_dir=Path("x"), interval_seconds=60)
    c.intervalSeconds = 1            # below floor
    assert c.intervalSeconds == 5    # clamped to [5, 3600]
    c.intervalSeconds = 99999
    assert c.intervalSeconds == 3600
    assert c._timer.interval() == 3600 * 1000

def test_tick_invokes_reaper_and_emits_reaped(qapp, monkeypatch):
    monkeypatch.setattr(
        "milodex.gui.orphan_reaper_controller.reconcile_orphaned_runs_on_bootstrap",
        lambda store, locks_dir, *, now: ["strat.x.v1"],
    )
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    received = []
    c.reaped.connect(lambda ids: received.append(ids))
    c._reap()  # invoke the slot directly
    assert received == [["strat.x.v1"]]

def test_clamp_boundaries_inclusive(qapp):
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=5)
    assert c.intervalSeconds == 5      # exact floor preserved
    c.intervalSeconds = 3600
    assert c.intervalSeconds == 3600   # exact ceiling preserved

def test_reap_swallows_reaper_exception(qapp, monkeypatch):
    def boom(store, locks_dir, *, now):
        raise RuntimeError("db gone")
    monkeypatch.setattr(
        "milodex.gui.orphan_reaper_controller.reconcile_orphaned_runs_on_bootstrap", boom
    )
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    c._reap()  # must not raise; logged + survives to next tick
```

- [ ] **Step 2: Run — verify failure (module missing)**

Run: `python -m pytest tests/milodex/gui/test_orphan_reaper_controller.py -q`
Expected: FAIL — `OrphanReaperController` import error.

- [ ] **Step 3: Implement the controller**

```python
"""Main-thread periodic driver for the orphan-run reaper (GUI).

Runs reconcile_orphaned_runs_on_bootstrap on a QTimer. The reaper is
liveness-gated and now re-checks the lock holder before mutating (residual-1
guard), so periodic firing is safe against the worker-thread async spawn.
Reuses one EventStore (open-op-close connections; no per-tick migration scan).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Property, Signal, Slot

from milodex.core.event_store import EventStore
from milodex.strategies.orphan_reconciliation import reconcile_orphaned_runs_on_bootstrap

_logger = logging.getLogger(__name__)

_MIN_INTERVAL_SECONDS = 5
_MAX_INTERVAL_SECONDS = 3600


class OrphanReaperController(QObject):
    reaped = Signal(list)

    def __init__(
        self,
        *,
        event_store: EventStore,
        locks_dir: Path,
        interval_seconds: int = 60,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = event_store
        self._locks_dir = locks_dir
        self._interval = self._clamp(interval_seconds)
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval * 1000)
        self._timer.timeout.connect(self._reap)

    @staticmethod
    def _clamp(seconds: int) -> int:
        return max(_MIN_INTERVAL_SECONDS, min(_MAX_INTERVAL_SECONDS, int(seconds)))

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _get_interval(self) -> int:
        return self._interval

    def _set_interval(self, seconds: int) -> None:
        self._interval = self._clamp(seconds)
        self._timer.setInterval(self._interval * 1000)

    intervalSeconds = Property(int, _get_interval, _set_interval)

    @Slot()
    def _reap(self) -> None:
        try:
            reaped = reconcile_orphaned_runs_on_bootstrap(
                self._store, self._locks_dir, now=datetime.now(tz=UTC)
            )
        except Exception:
            _logger.exception("Periodic orphan reaper failed; will retry next tick")
            return
        if reaped:
            _logger.warning("Periodic reaper closed %d orphan run(s): %s", len(reaped), ", ".join(reaped))
        self.reaped.emit(reaped)
```

- [ ] **Step 4: Run tests — verify pass**

Run: `python -m pytest tests/milodex/gui/test_orphan_reaper_controller.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check src/milodex/gui/orphan_reaper_controller.py tests/milodex/gui/test_orphan_reaper_controller.py
python -m ruff format src/milodex/gui/orphan_reaper_controller.py tests/milodex/gui/test_orphan_reaper_controller.py
git add src/milodex/gui/orphan_reaper_controller.py tests/milodex/gui/test_orphan_reaper_controller.py
git commit -m "feat(gui): OrphanReaperController main-thread QTimer reaper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Durable QSettings interval helper

Explicit `QSettings(_ORG, _APP)` constructor — this removes the global-name-ordering
risk the spec flagged (no dependency on `setOrganizationName` having run first). The
app-level name is still set in Task 5/app wiring for Qt hygiene, but the helper does
not depend on it. Tests must isolate storage to a temp INI file.

**Files:**
- Create: `src/milodex/gui/runner_health_settings.py`
- Test: `tests/milodex/gui/test_runner_health_settings.py`

- [ ] **Step 1: Write failing round-trip + default tests**

```python
def test_default_is_60_when_unset(tmp_settings):
    assert read_reap_interval_seconds() == 60

def test_round_trips_value(tmp_settings):
    write_reap_interval_seconds(300)
    assert read_reap_interval_seconds() == 300
```

`tmp_settings` fixture redirects QSettings INI storage to `tmp_path`:
```python
@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    # force the helper to use IniFormat (see _settings below)
    monkeypatch.setattr("milodex.gui.runner_health_settings._FORMAT", QSettings.IniFormat)
    yield
```

- [ ] **Step 2: Run — verify failure**

Run: `python -m pytest tests/milodex/gui/test_runner_health_settings.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the helper**

```python
"""Durable persistence for the GUI reap-interval setting (QSettings).

Uses an explicit (org, app, format) QSettings so it does not depend on
QCoreApplication.setOrganizationName having run first.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

_ORG = "Milodex"
_APP = "Milodex"
_KEY = "runner_health/reap_interval_seconds"
_DEFAULT = 60
_FORMAT = QSettings.NativeFormat  # overridden to IniFormat in tests


def _settings() -> QSettings:
    return QSettings(_FORMAT, QSettings.UserScope, _ORG, _APP)


def read_reap_interval_seconds() -> int:
    raw = _settings().value(_KEY, _DEFAULT)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT


def write_reap_interval_seconds(seconds: int) -> None:
    _settings().setValue(_KEY, int(seconds))
```

- [ ] **Step 4: Run tests — verify pass**

Run: `python -m pytest tests/milodex/gui/test_runner_health_settings.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
python -m ruff check src/milodex/gui/runner_health_settings.py tests/milodex/gui/test_runner_health_settings.py
python -m ruff format src/milodex/gui/runner_health_settings.py tests/milodex/gui/test_runner_health_settings.py
git add src/milodex/gui/runner_health_settings.py tests/milodex/gui/test_runner_health_settings.py
git commit -m "feat(gui): durable QSettings reap-interval helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Wire the drawer setting end-to-end

QML: RUNNER HEALTH section in `RiskOfficeDrawer.qml` (mirror the TIME FORMAT block at
lines 360-435), a `reapIntervalRequested(int seconds)` signal (sibling to
`timeFormatRequested` at line 41), and a `readonly property int reapIntervalSeconds`
mirror (sibling to `timeFormat` at line 80). `Main.qml`: a `Connections` handler
(sibling to lines 167-172) that writes QSettings and pushes to the controller.

**Files:**
- Modify: `src/milodex/gui/qml/Milodex/components/RiskOfficeDrawer.qml`
- Modify: `src/milodex/gui/qml/Milodex/Main.qml`
- Modify: `src/milodex/gui/app.py`
- Test: `tests/milodex/gui/test_qml_load_smoke.py`

- [ ] **Step 1a: Register the controller in `qml_setup.py` (house style — NOT setContextProperty)**

`register_qml_types` lives in `src/milodex/gui/qml_setup.py` (NOT app.py) and registers
every bridge via `qmlRegisterSingletonInstance` with a module-level GC anchor. It has
**no `engine` parameter** — do not use `setContextProperty` here. Mirror the
`risk_profile_bridge` block exactly (`qml_setup.py:296-308`):

1. Import `OrphanReaperController` at the top of `qml_setup.py`.
2. Add a GC anchor beside the others (`qml_setup.py:70`):
   `_orphan_reaper_controller_instance: OrphanReaperController | None = None`
3. Add the kwarg to `register_qml_types(...)` (after `risk_profile_bridge`, line 88):
   `orphan_reaper_controller: OrphanReaperController | None = None,`
4. Add `_orphan_reaper_controller_instance` to the `global` block (lines 131-136).
5. Before `return instance` (line 310), add:
```python
    if orphan_reaper_controller is not None:
        qmlRegisterSingletonInstance(
            OrphanReaperController,
            QML_IMPORT_URI,
            QML_IMPORT_VERSION[0],
            QML_IMPORT_VERSION[1],
            "OrphanReaperController",
            orphan_reaper_controller,
        )
        _orphan_reaper_controller_instance = orphan_reaper_controller
```
After `import Milodex 1.0`, QML references it as the singleton `OrphanReaperController`
(same access pattern as `RiskProfileBridge`).

- [ ] **Step 1b: Set org/app name + construct/start the controller in `app.py`**

After `app = QGuiApplication(...)` (around line 203), add:
```python
app.setOrganizationName("Milodex")
app.setApplicationName("Milodex")
```
After the bootstrap reconcile block (after line 281), construct the controller seeded
from QSettings:
```python
from milodex.gui.orphan_reaper_controller import OrphanReaperController
from milodex.gui.runner_health_settings import read_reap_interval_seconds

orphan_reaper_controller = OrphanReaperController(
    event_store=EventStore(db_path),
    locks_dir=locks_dir,
    interval_seconds=read_reap_interval_seconds(),
)
```
Pass `orphan_reaper_controller=orphan_reaper_controller` to the `register_qml_types(...)`
call (app.py:339). Start it after registration (near line 360):
`orphan_reaper_controller.start()`. Wire teardown in **all three** places the other
read models are wired:
- the `_make_app_controller([...])` stop-list (app.py:389-404),
- the load-failure cleanup block (app.py:421-432),
- **and** `app.aboutToQuit.connect(orphan_reaper_controller.stop)` (app.py:441-453) —
  the normal-window-close path. Omitting this leaves the QTimer firing during teardown.

- [ ] **Step 2: Add the QML RUNNER HEALTH section + signal + mirror in `RiskOfficeDrawer.qml`**

Add the signal near line 41:
```qml
signal reapIntervalRequested(int seconds)
```
Add the mirror near line 80 (read from the controller context property):
```qml
readonly property int reapIntervalSeconds:
    typeof OrphanReaperController !== "undefined" ? OrphanReaperController.intervalSeconds : 60
```
Add a new section (mirror the TIME FORMAT ColumnLayout at 363-435) below TIME FORMAT
with a divider, header `"RUNNER HEALTH"`, and a `Repeater` over
`[{label:"30s",value:30},{label:"60s",value:60},{label:"5 MIN",value:300}]`, each
delegate's `isSelected: root.reapIntervalSeconds === modelData.value`, `onClicked:
root.reapIntervalRequested(modelData.value)`.

**CRITICAL — guard every `OrphanReaperController` reference with `typeof`.** The smoke
harness (`test_qml_load_smoke.py`) calls `register_qml_types(...)` WITHOUT the controller,
so in that subprocess `OrphanReaperController` is an *unregistered* name; any *unguarded*
reference throws a QML warning and fails the smoke test. The mirror property
(`typeof OrphanReaperController !== "undefined" ? ... : 60`) is the only place that
touches the singleton — the delegate binds the local `root.reapIntervalSeconds`, never
the singleton directly. Keep it that way: the delegate's `isSelected` and `onClicked`
must reference `root.reapIntervalSeconds` / `root.reapIntervalRequested`, NOT
`OrphanReaperController.*`. **Smoke-test trap:** the QML smoke test substring-asserts
source — see Step 4.

- [ ] **Step 3: Handle the signal in `Main.qml`**

Add a `Connections` block (sibling to lines 167-172):
```qml
Connections {
    target: riskDrawer
    function onReapIntervalRequested(seconds) {
        if (typeof OrphanReaperController !== "undefined")
            OrphanReaperController.intervalSeconds = seconds
        RunnerHealthSettings.write(seconds)   // see note
    }
}
```
**Persistence note:** QML cannot call the Python `runner_health_settings` module
directly. Two clean options — pick one and implement:
(a) expose a `@Slot(int)` `persistInterval` on `OrphanReaperController` that calls
`write_reap_interval_seconds`, and call `OrphanReaperController.persistInterval(seconds)`
(simplest — drop the `RunnerHealthSettings.write` line); or
(b) register a tiny settings bridge as a context property.
**Recommended: (a)** — one slot on the controller, no new bridge. Update the controller
(Task 3 file) to add:
```python
@Slot(int)
def persistInterval(self, seconds: int) -> None:
    from milodex.gui.runner_health_settings import write_reap_interval_seconds
    write_reap_interval_seconds(int(seconds))
    self._set_interval(seconds)
```
and have the QML handler call only `OrphanReaperController.persistInterval(seconds)`
(guarded by `typeof OrphanReaperController !== "undefined"`).
If you take (a), add a controller unit test: `persistInterval` writes QSettings and
updates `intervalSeconds` (extend Task 3's test file; use Task 4's `tmp_settings`
fixture). **Note:** option (a) reopens Task 3's controller file — after this edit,
re-run Task 3's full test module (`tests/milodex/gui/test_orphan_reaper_controller.py`)
to confirm no regression, then re-commit the controller.

- [ ] **Step 4: Update the QML smoke test**

In `tests/milodex/gui/test_qml_load_smoke.py`, add the new asserted substrings so the
drawer still loads and the section is present (e.g. `"RUNNER HEALTH"`, `"5 MIN"`,
`reapIntervalRequested`). Ground against how the file currently asserts the TIME FORMAT
section and copy that pattern.

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -q`
Expected: PASS (drawer loads with the new section).

- [ ] **Step 5: Full GUI test sweep + lint + commit**

```bash
python -m pytest tests/milodex/gui -q
python -m ruff check src/milodex/gui tests/milodex/gui
python -m ruff format src/milodex/gui tests/milodex/gui
git add src/milodex/gui tests/milodex/gui
git commit -m "feat(gui): RUNNER HEALTH reap-interval setting (drawer + wiring)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: ADR 0026 addendum

**Files:**
- Modify: `docs/adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md`

- [ ] **Step 1: Append an addendum**

Add a dated addendum section (after the existing 2026-05-05 addendum) recording, in
prose (no fabricated verbatim quotes — paraphrase 0026's operator-driven-recovery
posture):
- The orphaned `strategy_runs` bookkeeping row is now auto-reconciled on a GUI timer
  (default 60s, configurable, durable via QSettings) and on demand via
  `milodex maintenance reap-orphans`, in addition to the existing bootstrap/runner-start
  triggers.
- The strategy process itself is still never auto-restarted — 0026's per-process,
  operator-driven model is unchanged.
- The reaper is liveness-gated and now re-checks the lock holder before close+unlink
  (closing residual-1, which 0026-era code deferred), because periodic reaping plus the
  worker-thread async spawn makes that race first-class. Reference the spec and
  `docs/reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md`.

- [ ] **Step 2: Commit**

```bash
git add docs/adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md
git commit -m "docs(adr): 0026 addendum — automated orphan bookkeeping recovery

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (before merge)

- [ ] `python -m pytest -q` — full suite green.
- [ ] `python -m ruff check src/ tests/` — clean.
- [ ] Manual smoke (optional): launch GUI, open Risk Office drawer, confirm RUNNER
      HEALTH presets render and clicking one persists across a relaunch; run
      `milodex maintenance reap-orphans --dry-run` against `data/milodex.db`.
- [ ] Merge `feat/periodic-orphan-reaper` to `master` with `--no-ff`, push origin.

## Risks / notes

- **The residual-1 guard is the delicate part.** Its correctness depends on the
  lock-before-row ordering in `strategy.py` (lock acquired before the open row is
  appended). Task 1's guard test is the regression floor; do not weaken it.
- **EventStore reuse:** the controller holds one `EventStore`; safe because connections
  are open-op-close (`event_store.py:_connect`). The bootstrap call still constructs its
  own throwaway instance — both coexist.
- **Read-model staleness:** the badge clears on the active-ops 30s poll, not via the
  `reaped` signal (no force-refresh seam). Worst case ≈ interval + 30s. Documented; a
  `forceRefresh` slot is a deferred follow-up.
