# Periodic Orphan Reaper — Tasks 2–6 Implementation Plan

> **For agentic workers:** Execute task-by-task under TDD (red → green → refactor → commit). Steps use checkbox (`- [ ]`) syntax.

**Status:** Task 1 (reaper re-check guard) is COMMITTED (`c439152`). This plan covers the remaining Tasks 2–6, re-grounded against the committed Task 1 API and the real CLI/GUI test harnesses.

**Branch:** `feat/periodic-orphan-reaper`

**Spec:** `docs/superpowers/specs/2026-05-29-periodic-orphan-reconciliation-design.md`
**Master plan:** `docs/superpowers/plans/2026-05-29-periodic-orphan-reconciliation.md`

## Committed Task 1 API (depend on these exactly)

- `reconcile_orphaned_runs_on_bootstrap(event_store, locks_dir, *, now) -> list[str]`
  — now has the residual-1 re-check guard.
- `_orphan_candidates(event_store, locks_dir) -> list[tuple[str, LockHolder | None]]`
  — pure/read-only; sorted by strategy id. Shared by reaper + CLI dry-run.
- `_has_live_runner(strategy_id, locks_dir) -> tuple[bool, LockHolder | None]`.
All in `src/milodex/strategies/orphan_reconciliation.py`.

## Verified harness facts (do not re-derive)

- **CLI tests** (`tests/milodex/cli/test_maintenance.py:26-37`): `_run(argv, tmp_path) ->
  (code, out, err)` where `out`/`err` are `StringIO`. It calls `cli_entrypoint(argv,
  event_store_factory=lambda: EventStore(tmp_path/"milodex.db"), broker_factory=_no_broker,
  data_provider_factory=_refuse_data_provider, locks_dir=tmp_path/"locks", stdout=out,
  stderr=err)`. JSON asserted via `json.loads(out.getvalue())`.
- **CommandContext** exposes `ctx.get_event_store()` and `ctx.locks_dir` (wired from the
  `event_store_factory`/`locks_dir` above). `maintenance.run()` today acquires the runtime
  AdvisoryLock **unconditionally** at `maintenance.py:61` — the reap-orphans branch MUST
  early-return before it.
- **GUI tests** (pattern from `tests/milodex/gui/test_active_ops_state.py:24-37,510-525`):
  module-level `try: import PySide6 ... _PYSIDE6_AVAILABLE = True/False`, a
  `_skip_no_qt = pytest.mark.skipif(not _PYSIDE6_AVAILABLE, reason=...)`, and a
  module-scoped `qapp` fixture that sets `QT_QPA_PLATFORM=offscreen` and returns
  `QGuiApplication.instance()` or constructs one. Reuse this pattern verbatim.
- **`StrategyRunEvent`** seeding (open orphan): `EventStore(...).append_strategy_run(
  StrategyRunEvent(session_id=..., strategy_id=..., started_at=<dt>, ended_at=None,
  exit_reason=None, metadata={"mode": "paper"}))`. With NO lock file on disk the strategy
  classifies dead (`_has_live_runner` → `(False, None)`).
- **qml_setup.py registration template** (`qml_setup.py:296-308` risk_profile_bridge):
  `qmlRegisterSingletonInstance(ClassType, QML_IMPORT_URI, QML_IMPORT_VERSION[0],
  QML_IMPORT_VERSION[1], "Name", instance)`, module GC anchor at `:70`, `global` block
  `:131-136`, kwarg after `:88`, register block before `return instance` (`:310`). NO
  `engine`/`setContextProperty` in this function.
- **app.py anchors:** `QGuiApplication` `:201-203`; bootstrap reconcile `:263-281`;
  `register_qml_types(...)` call `:339`; post-registration `.start()` block `~:360-372`;
  `_make_app_controller([...])` stop-list `:389-404`; load-failure cleanup `:421-432`;
  `aboutToQuit.connect(...)` block `:442-453`.
- **Drawer anchors** (`RiskOfficeDrawer.qml`): `signal timeFormatRequested` `:41`;
  `readonly property string timeFormat` mirror `:80`; TIME FORMAT section `:360-435`;
  trailing SYSTEM section divider `:437-442`.
- **Main.qml:** `timeFormatRequested` `Connections` handler `:167-172`.

---

## Task 2: CLI `maintenance reap-orphans` + `--dry-run`

**Files:** Modify `src/milodex/cli/commands/maintenance.py`; Test `tests/milodex/cli/test_maintenance.py`.

- [ ] **Step 1: Write the failing dry-run test**

Add a seeding helper for an open orphan run, then assert dry-run lists it as a candidate
without closing it.

```python
from milodex.core.event_store import EventStore, StrategyRunEvent

def _seed_open_orphan(tmp_path: Path, strategy_id: str = "strat.x.v1") -> EventStore:
    store = EventStore(tmp_path / "milodex.db")
    store.append_strategy_run(
        StrategyRunEvent(
            session_id="s-1",
            strategy_id=strategy_id,
            started_at=_TS,
            ended_at=None,
            exit_reason=None,
            metadata={"mode": "paper"},
        )
    )
    return store

def test_reap_orphans_dry_run_lists_without_closing(tmp_path):
    store = _seed_open_orphan(tmp_path)
    code, out, err = _run(["maintenance", "reap-orphans", "--dry-run", "--json"], tmp_path)
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["data"]["candidates"] == ["strat.x.v1"]
    assert payload["data"]["applied"] is False
    # still open
    assert [r for r in store.list_strategy_runs() if r.ended_at is None]
```

- [ ] **Step 2: Run — verify it fails**

Run: `python -m pytest tests/milodex/cli/test_maintenance.py::test_reap_orphans_dry_run_lists_without_closing -v`
Expected: FAIL — argparse rejects `reap-orphans` (not in `maintenance_action` choices).

- [ ] **Step 3: Implement**

In `register()`: change choices to `("compact", "reap-orphans")`; add:
```python
parser.add_argument(
    "--dry-run",
    action="store_true",
    dest="maintenance_dry_run",
    help="(reap-orphans) List orphan candidates without closing any run.",
)
```
In `run()`, branch at the very top, BEFORE the `AdvisoryLock("milodex.runtime")` block:
```python
def run(args, ctx):
    if getattr(args, "maintenance_action", "compact") == "reap-orphans":
        return _run_reap_orphans(args, ctx)
    event_store = ctx.get_event_store()
    with AdvisoryLock("milodex.runtime", ...):   # existing compact body, unchanged
        ...
```
Add the handler:
```python
def _run_reap_orphans(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    from datetime import UTC, datetime

    from milodex.strategies.orphan_reconciliation import (
        _orphan_candidates,
        reconcile_orphaned_runs_on_bootstrap,
    )

    event_store = ctx.get_event_store()
    if getattr(args, "maintenance_dry_run", False):
        candidates = [sid for sid, _ in _orphan_candidates(event_store, ctx.locks_dir)]
        human = ["Reap-orphans dry-run (no changes):"]
        human += [f"  would close: {sid}" for sid in candidates] or ["  no orphan runs."]
        human.append(f"  {len(candidates)} orphan run(s) would be closed.")
        return CommandResult(
            command="maintenance.reap-orphans",
            data={"applied": False, "candidates": candidates},
            human_lines=human,
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
**No runtime advisory lock** — the reaper is liveness-gated and the Task 1 guard covers
the spawn race. Update the module docstring's one-line summary to mention `reap-orphans`.

- [ ] **Step 4: Green + add apply & json-contract tests**

Add:
- `test_reap_orphans_apply_closes_and_reports` — `_run(["maintenance","reap-orphans","--json"], tmp_path)`; assert `payload["data"]["applied"] is True`, `"strat.x.v1" in payload["data"]["reaped"]`, and the row is now closed (`ended_at is not None`).
- `test_reap_orphans_dry_run_empty` — no seed; `candidates == []`.
- `test_reap_orphans_skips_live_lock` — seed the open run AND hold a real lock for it:
  `lock = AdvisoryLock(runner_lock_name("strat.x.v1"), locks_dir=tmp_path/"locks"); lock.acquire()`
  (the current process is the live holder), then `try:` run `reap-orphans` (apply) and assert
  `payload["data"]["reaped"] == []` and the row stays open; `finally: lock.release()`. This
  re-asserts the liveness gate at the CLI seam — the feature's safety reason for existing
  (reviewer-recommended). Import `AdvisoryLock` and `runner_lock_name`.

Run: `python -m pytest tests/milodex/cli/test_maintenance.py -q`
Expected: PASS (new + existing compact tests).

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

**Files:** Create `src/milodex/gui/orphan_reaper_controller.py`; Test `tests/milodex/gui/test_orphan_reaper_controller.py`.

The test file MUST open with the `_PYSIDE6_AVAILABLE`/`_skip_no_qt`/`qapp` triad copied
from `test_active_ops_state.py:24-37,510-525`, and decorate Qt tests with `@_skip_no_qt`.
Drive behavior deterministically: call `_reap()` directly and set `intervalSeconds`
directly — never wait on a real timer.

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path
import pytest

# ... _PYSIDE6_AVAILABLE / _skip_no_qt / qapp triad copied here ...

from milodex.gui.orphan_reaper_controller import OrphanReaperController

@_skip_no_qt
def test_interval_clamps_and_restarts_timer(qapp):
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    c.intervalSeconds = 1
    assert c.intervalSeconds == 5            # floor
    c.intervalSeconds = 99999
    assert c.intervalSeconds == 3600         # ceiling
    assert c._timer.interval() == 3600 * 1000

@_skip_no_qt
def test_clamp_boundaries_inclusive(qapp):
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=5)
    assert c.intervalSeconds == 5
    c.intervalSeconds = 3600
    assert c.intervalSeconds == 3600

@_skip_no_qt
def test_tick_invokes_reaper_and_emits_reaped(qapp, monkeypatch):
    monkeypatch.setattr(
        "milodex.gui.orphan_reaper_controller.reconcile_orphaned_runs_on_bootstrap",
        lambda store, locks_dir, *, now: ["strat.x.v1"],
    )
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    received = []
    c.reaped.connect(lambda ids: received.append(ids))
    c._reap()
    assert received == [["strat.x.v1"]]

@_skip_no_qt
def test_reap_swallows_reaper_exception(qapp, monkeypatch):
    def boom(store, locks_dir, *, now):
        raise RuntimeError("db gone")
    monkeypatch.setattr(
        "milodex.gui.orphan_reaper_controller.reconcile_orphaned_runs_on_bootstrap", boom
    )
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    c._reap()  # must not raise
```

- [ ] **Step 2: Run — verify failure (import error).**

Run: `python -m pytest tests/milodex/gui/test_orphan_reaper_controller.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the controller**

```python
"""Main-thread periodic driver for the orphan-run reaper (GUI).

Runs reconcile_orphaned_runs_on_bootstrap on a QTimer. The reaper is liveness-gated
and re-checks the lock holder before mutating (residual-1 guard), so periodic firing
is safe against the worker-thread async spawn. Reuses one EventStore (open-op-close
connections; no per-tick migration scan). reaped(list) is informational (logged), not
wired to a read-model refresh — the active-ops 30s poll clears the badge.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

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

    @Slot(int)
    def persistInterval(self, seconds: int) -> None:
        from milodex.gui.runner_health_settings import write_reap_interval_seconds

        self._set_interval(seconds)
        write_reap_interval_seconds(self._interval)

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
            _logger.warning(
                "Periodic reaper closed %d orphan run(s): %s", len(reaped), ", ".join(reaped)
            )
        self.reaped.emit(reaped)
```

(`persistInterval` is included now — Task 5 calls it from QML — so Task 3's file is not
reopened later. Its QSettings dependency, `runner_health_settings`, lands in Task 4;
sequence Task 4 before any test that exercises `persistInterval`.)

- [ ] **Step 4: Green.** Run: `python -m pytest tests/milodex/gui/test_orphan_reaper_controller.py -q` → PASS.

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

**Files:** Create `src/milodex/gui/runner_health_settings.py`; Test `tests/milodex/gui/test_runner_health_settings.py`.

Explicit `QSettings(format, scope, org, app)` constructor — no dependency on
`setOrganizationName` ordering. Tests isolate storage to a temp INI file.

- [ ] **Step 1: Write failing round-trip + default tests**

```python
import pytest
from PySide6.QtCore import QSettings

@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    monkeypatch.setattr(
        "milodex.gui.runner_health_settings._FORMAT", QSettings.IniFormat
    )
    yield

def test_default_is_60_when_unset(tmp_settings):
    from milodex.gui.runner_health_settings import read_reap_interval_seconds
    assert read_reap_interval_seconds() == 60

def test_round_trips_value(tmp_settings):
    from milodex.gui.runner_health_settings import (
        read_reap_interval_seconds, write_reap_interval_seconds,
    )
    write_reap_interval_seconds(300)
    assert read_reap_interval_seconds() == 300
```

(Guard the whole module with the `_PYSIDE6_AVAILABLE`/`_skip_no_qt` triad too, since it
imports `PySide6.QtCore`.)

- [ ] **Step 2: Run — verify failure (module missing).**

- [ ] **Step 3: Implement**

```python
"""Durable persistence for the GUI reap-interval setting (QSettings).

Uses an explicit (format, scope, org, app) QSettings so it does not depend on
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

- [ ] **Step 4: Green.** Run the file → PASS.

- [ ] **Step 5: Add the `persistInterval` integration test (uses Task 3 + Task 4)**

In `tests/milodex/gui/test_orphan_reaper_controller.py`, add (with `tmp_settings`
fixture copied/imported):
```python
@_skip_no_qt
def test_persist_interval_writes_and_updates(qapp, tmp_settings):
    from milodex.gui.runner_health_settings import read_reap_interval_seconds
    c = OrphanReaperController(event_store=object(), locks_dir=Path("x"), interval_seconds=60)
    c.persistInterval(300)
    assert c.intervalSeconds == 300
    assert read_reap_interval_seconds() == 300
```
Run: `python -m pytest tests/milodex/gui/test_orphan_reaper_controller.py tests/milodex/gui/test_runner_health_settings.py -q` → PASS.

- [ ] **Step 6: Lint + commit**

```bash
python -m ruff check src/milodex/gui/runner_health_settings.py tests/milodex/gui/test_runner_health_settings.py tests/milodex/gui/test_orphan_reaper_controller.py
python -m ruff format src/milodex/gui/runner_health_settings.py tests/milodex/gui/test_runner_health_settings.py tests/milodex/gui/test_orphan_reaper_controller.py
git add src/milodex/gui/runner_health_settings.py tests/milodex/gui/test_runner_health_settings.py tests/milodex/gui/test_orphan_reaper_controller.py
git commit -m "feat(gui): durable QSettings reap-interval helper + persistInterval test

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Wire the setting end-to-end (qml_setup + app.py + drawer + Main.qml)

**Files:** Modify `qml_setup.py`, `app.py`, `RiskOfficeDrawer.qml`, `Main.qml`;
Test `tests/milodex/gui/test_qml_load_smoke.py`.

- [ ] **Step 1: Register the controller in `qml_setup.py` (house style — NOT setContextProperty)**

1. Import `OrphanReaperController` at the top.
2. GC anchor beside `:70`: `_orphan_reaper_controller_instance: OrphanReaperController | None = None`.
3. Kwarg after `:88`: `orphan_reaper_controller: OrphanReaperController | None = None,`.
4. Add to the `global` block (`:131-136`).
5. Before `return instance` (`:310`):
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

- [ ] **Step 2: Construct/start/teardown in `app.py`**

- After `app = QGuiApplication(...)` (`:201-203`): `app.setOrganizationName("Milodex")`
  and `app.setApplicationName("Milodex")`.
- After the bootstrap reconcile `try/except` ends (`:280`), before the read-model block
  at `:282` (`db_path` `:271` and `locks_dir` `:256` are in scope):
```python
    from milodex.gui.orphan_reaper_controller import OrphanReaperController
    from milodex.gui.runner_health_settings import read_reap_interval_seconds

    orphan_reaper_controller = OrphanReaperController(
        event_store=EventStore(db_path),
        locks_dir=locks_dir,
        interval_seconds=read_reap_interval_seconds(),
    )
```
- Pass `orphan_reaper_controller=orphan_reaper_controller` to `register_qml_types(...)` (`:339`).
- After registration (`~:360-372`): `orphan_reaper_controller.start()`.
- Add to the `_make_app_controller([...])` list (`:389-404`).
- Add to the load-failure cleanup block (`:421-432`): `orphan_reaper_controller.stop()`.
- Add to the `aboutToQuit` block (`:442-453`): `app.aboutToQuit.connect(orphan_reaper_controller.stop)`.

- [ ] **Step 3: Drawer RUNNER HEALTH section (`RiskOfficeDrawer.qml`)**

- Signal near `:41`: `signal reapIntervalRequested(int seconds)`.
- Mirror near `:80` (guarded — smoke harness does not register the singleton):
```qml
readonly property int reapIntervalSeconds:
    (typeof OrphanReaperController !== "undefined") ? OrphanReaperController.intervalSeconds : 60
```
- New section: clone the TIME FORMAT `ColumnLayout` (`:360-435`) and its preceding/following
  divider; header `"RUNNER HEALTH"`; `Repeater` model
  `[{label:"30s",value:30},{label:"60s",value:60},{label:"5 MIN",value:300}]`; delegate
  `isSelected: root.reapIntervalSeconds === modelData.value`; `onClicked:
  root.reapIntervalRequested(modelData.value)`. **The delegate must reference only
  `root.reapIntervalSeconds`/`root.reapIntervalRequested` — never `OrphanReaperController.*`
  directly — so the unregistered-singleton smoke path stays warning-free.**

- [ ] **Step 4: Main.qml handler (`:167-172` sibling)**

```qml
Connections {
    target: riskDrawer
    function onReapIntervalRequested(seconds) {
        if (typeof OrphanReaperController !== "undefined")
            OrphanReaperController.persistInterval(seconds)
    }
}
```

- [ ] **Step 5: Update the QML smoke test**

In `tests/milodex/gui/test_qml_load_smoke.py`, find how it asserts the drawer/TIME FORMAT
strings and add the new asserted substrings (`"RUNNER HEALTH"`, `"5 MIN"`,
`reapIntervalRequested`) in lockstep with the QML literals. Confirm Main.qml + the drawer
still load in the smoke engine WITHOUT the controller registered (the `typeof` guards make
this safe).

Run: `python -m pytest tests/milodex/gui/test_qml_load_smoke.py -q` → PASS.

- [ ] **Step 6: Full GUI sweep + lint + commit**

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

**Files:** Modify `docs/adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md`.

- [ ] **Step 1: Append a dated addendum** (after the existing 2026-05-05 addendum), in prose
  (paraphrase 0026 — no fabricated verbatim quotes):
  - Orphaned `strategy_runs` bookkeeping rows are now auto-reconciled on a GUI timer
    (default 60s, configurable via RUNNER HEALTH, durable via QSettings) and on demand via
    `milodex maintenance reap-orphans`, in addition to the existing bootstrap/runner-start
    triggers.
  - The strategy process is still never auto-restarted — 0026's per-process, operator-driven
    model is unchanged.
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
- [ ] Manual smoke (optional): launch GUI → Risk Office drawer → RUNNER HEALTH presets
      render; click 30s, relaunch, confirm 30s persists; `milodex maintenance reap-orphans
      --dry-run` against `data/milodex.db`.
- [ ] Merge `feat/periodic-orphan-reaper` → `master` with `--no-ff`; push origin.

## Sequencing & risks

- **Order:** 2 → 3 → 4 → 5 → 6. Task 3 defines `persistInterval` (calls Task 4's helper),
  so Task 4 must land before any test that runs `persistInterval` — handled by putting that
  integration test in Task 4 Step 5.
- **Task 5 is the riskiest** (multi-file QML/Python wiring). The `typeof` guards are
  load-bearing for the smoke test; the `qmlRegisterSingletonInstance` block must match the
  house style exactly (no `engine`).
- **Read-model staleness:** badge clears on the active-ops 30s poll, not via `reaped`
  (no force-refresh seam). Worst case ≈ interval + 30s. Documented; deferred follow-up.
