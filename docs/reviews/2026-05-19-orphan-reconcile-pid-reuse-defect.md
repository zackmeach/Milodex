# Investigation: orphan-reconcile PID-reuse defect, 2026-05-19

**Status:** read-only investigation. State was manually cleaned at end of session (1 stale `strategy_runs` row closed with `exit_reason='orphaned_host_reset_pid_reuse_manual'`, 6 stale lock files unlinked). No code changes applied.

**Severity:** real defect in the GUI-bootstrap orphan reconciler (`src/milodex/strategies/orphan_reconciliation.py`). After a host reset, any phantom whose old PID is reassigned by Windows to an unrelated live process **silently escapes reconcile and remains phantom indefinitely**. The active-ops read model then renders that strategy as "running" with full operator confidence — the worst failure mode for a system whose value proposition rests on operator trust in the autonomy boundary (`FOUNDER_INTENT.md`). Operationally, the rsi2 runner today never executed real work before crashing (market-open no-op gate held), so no broker damage was possible. But the same defect would silently mask a phantom-running production strategy whose risk-evaluated stance the operator believes is active.

**Operator question:** *"what happens when my computer resets while runners are spawned?"* — host hard-reset at ~15:09 ET on 2026-05-19 with 6 paper runners alive; GUI relaunched ~20:00 ET.

**One-paragraph answer:** Reconcile asks "is the PID recorded in the advisory lock currently alive?" via `_process_exists(holder.pid)`. After a reboot, Windows reassigns PIDs from the low pool, and one of the six stale PIDs (34476, owning `meanrev.daily.pullback_rsi2.curated_largecap.v1`) was handed to an unrelated process. Reconcile interpreted "PID exists" as "runner is still running" and skipped the row. The other five PIDs happened *not* to be reused and reconciled correctly. The lock-acquire path (`AdvisoryLock.acquire`) already implements a fallback that catches this kind of PID-reuse — a `_STALE_LOCK_MAX_AGE_SECONDS = 12h` age check on the lock file — but `_has_live_runner` does not consult it. A second gap: even for the five that reconciled successfully at the DB level, the orphan module never unlinks their stale lock files, so the locks were left on disk after the DB rows were closed.

---

## Timeline (UTC)

| Time | Event | Source |
|---|---|---|
| 14:58:22Z (10:58 ET) | 6 paper runners spawned from GUI. Locks written, `strategy_runs` rows opened. PIDs: 30436, 28856, 35924, 34476, 41968, 38224. | `data/locks/*.lock`, `strategy_runs` |
| 14:58:22Z – ~19:09Z | All 6 runners idle (market-open daily no-op gate in `strategies/runner.py`). Zero explanations recorded. | `explanations` (filtered) |
| ~19:09Z (~15:09 ET) | Host hard-reset (computer reboot). All 6 runner processes killed abruptly. GUI killed. Locks and DB rows not released — no orderly shutdown. | operator report |
| ~19:09Z – 00:00Z | System down / cold. Windows post-boot PID assignment. PID 34476 reassigned to unrelated process. PIDs 30436/28856/35924/41968/38224 happen not to be reused. | OS scheduler — non-deterministic |
| 00:00:23.080498Z (20:00 ET) | GUI relaunched. `reconcile_orphaned_runs_on_bootstrap` fires. Closes 5 of 6 open rows with `exit_reason='orphaned_no_live_runner'`. Skips `7217959b` (rsi2) — its lock-recorded PID 34476 resolves to a live (unrelated) process. | `strategy_runs.ended_at` batch, GUI startup |
| 00:00:23.080498Z+ | All 6 lock files remain on disk — reconcile module does not unlink them. | `data/locks/` |
| 00:00:23.080498Z+ | GUI renders rsi2 as "still running" (active-ops read model trusts `ended_at IS NULL`). Operator sees a confidently-wrong status. | operator observation |
| Investigation end | Operator manually cleaned: `update strategy_runs set ended_at=now, exit_reason='orphaned_host_reset_pid_reuse_manual' where session_id like '7217959b%'`; `rm data/locks/*.lock`. | manual SQL + filesystem |

---

## Root-Cause Analysis

### The reconcile path uses the unsafe primitive

`src/milodex/strategies/orphan_reconciliation.py:30-41`:

```python
def _has_live_runner(strategy_id: str, locks_dir: Path) -> bool:
    from milodex.core.advisory_lock import AdvisoryLock, _process_exists
    lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
    holder = lock.current_holder()
    return holder is not None and _process_exists(holder.pid)
```

`_process_exists(pid)` returns `True` if any process with that PID exists on the system — it has zero knowledge of process identity. After a reboot, Windows freely reassigns the recently-freed PID pool; an unrelated `notepad.exe` whose PID happens to match the lock's recorded value will be treated as the original runner.

### The lock-acquire path has the safeguard `_has_live_runner` is missing

`src/milodex/core/advisory_lock.py:114-141` (excerpt):

```python
if existing_holder is not None and _process_exists(existing_holder.pid):
    if self._lock_is_past_max_age():
        # The recorded PID resolves to a live process, but the
        # lock file is older than any legitimate single-session
        # hold (see _STALE_LOCK_MAX_AGE_SECONDS). Treat the PID as
        # almost certainly recycled (the original holder is long
        # dead) and reclaim — loudly, because the alternative is a
        # permanently wedged system that can never restart.
        _logger.warning("Reclaiming stale advisory lock '%s': ...", ...)
    else:
        msg = f"Advisory lock '{self._name}' is held by ..."
        raise AdvisoryLockError(msg)
```

`AdvisoryLock.acquire` knows that "PID exists" is not the same as "the original holder is alive" and adds a lock-file-age fallback (`_STALE_LOCK_MAX_AGE_SECONDS = 12 * 60 * 60`). `_has_live_runner` does not consult this fallback. The reconcile module reuses the bare `_process_exists` primitive that `acquire` was specifically designed to backstop.

### Why the 12h fallback alone would not have saved us

Our lock was ~9 hours old when reconcile ran (14:58Z → 00:00Z next day). Even if `_has_live_runner` mirrored `acquire`'s logic exactly, it would still have classified PID 34476 as a live runner — 9h < 12h threshold. The 12h fallback is calibrated for "definitely-stale-from-long-idle" scenarios, not for "host-reset-then-quick-restart." The two failure modes both produce a recycled-PID phantom but they need different discriminators:

- **Long-idle PID-reuse** (lock weeks old, system never rebooted, PID slowly recycled by long-lived processes): lock-file age is the right signal.
- **Host-reset PID-reuse** (lock hours old, system rebooted, PID reassigned from low pool): lock age is uninformative; **process start time vs lock `started_at` is the correct discriminator**. A real surviving process must have started *before* the lock was written; a reused PID's process necessarily started *after* the host came back up, which is by definition after the lock was written.

### A second gap: locks are not unlinked after reconciliation

`reconcile_orphan_strategy_runs` in `event_store.py` (called from `orphan_reconciliation.py:62`) closes the DB row only. The lock file is left on disk. This means:

1. After reconcile, the `data/locks/` directory contains stale locks for strategies whose run rows have been cleanly closed — divergent state across the two surfaces.
2. The next launch attempt for those strategies relies on `AdvisoryLock.acquire`'s own staleness handling to clear the lock. That path works when `_process_exists(holder.pid)` returns `False` (acquire will reclaim cleanly). It does NOT work when the lock's PID has been reused — `acquire` will refuse with `AdvisoryLockError` until the 12h threshold passes. So a recycled-PID lock blocks new runners for up to 12h post-reboot even after reconcile has done its part on the DB side.

---

## Operator-visibility behavior (mixed)

- **Visible (correct):** GUI surfaced the 5 successfully-reconciled runs as `orphaned` at launch. Operator could see that bootstrap reconcile fired and took action.
- **Invisible (wrong):** GUI rendered rsi2 as "still running" because its `strategy_runs.ended_at IS NULL`. No banner, no warning, no indication that reconcile considered the row and chose to skip it. The active-ops read model lacks any "I evaluated this and trust the PID" provenance, so an operator-visible "phantom suspected" channel does not exist.

Per `FOUNDER_INTENT.md`, the autonomy boundary is enforceable only if the operator can trust the surface. A confidently-rendered "running" status for a runner that isn't running is the operator-trust failure mode most worth preventing.

---

## Recommended Fix

### Bug 1 — `_has_live_runner` must verify process identity, not just liveness

Replace the bare `_process_exists` check with a start-time comparison. Pseudocode:

```python
def _has_live_runner(strategy_id, locks_dir) -> bool:
    holder = AdvisoryLock(...).current_holder()
    if holder is None:
        return False
    proc_start = _process_start_time(holder.pid)
    if proc_start is None:
        return False  # process doesn't exist
    # If the live process started AFTER the lock was written, the lock's
    # original holder is dead and the PID has been reassigned.
    # Grace: small skew (e.g. 1s) to absorb file-write / clock ordering.
    return proc_start <= holder.started_at + timedelta(seconds=1)
```

Implementation note: `psutil.Process(pid).create_time()` is the standard cross-platform answer. Project doesn't yet have psutil — alternatives:
- Windows: `wmic process where ProcessId=N get CreationDate` or `Get-CimInstance Win32_Process` (CIM via ctypes is doable but heavier).
- POSIX: `/proc/<pid>/stat` field 22 (start-time in jiffies since boot).
- Pragmatic: add `psutil` as a runtime dep — it's the right tool for exactly this and a few other places the codebase wants process introspection.

Keep the 12h `_STALE_LOCK_MAX_AGE_SECONDS` fallback as a belt-and-suspenders for the "lock from before clock-skew / start-time can't be read" case — but the start-time check must be the primary discriminator.

### Bug 2 — Reconcile must unlink the lock file when closing the run row

In `reconcile_orphaned_runs_on_bootstrap`, after `event_store.reconcile_orphan_strategy_runs(...)` succeeds for a strategy, unlink `runner_lock_name(strategy_id).lock`. Idempotent (file-not-found is a no-op). Same transaction shape as the existing reclaim path in `AdvisoryLock.acquire` when it determines the holder is dead.

### Operator-visibility hardening (separate concern, lower priority)

Consider emitting an explicit GUI banner / log entry when bootstrap reconcile fires:
- "Reconciled N orphan strategy runs at startup: <list>"
- "Skipped M strategies whose locks reported live PIDs: <list>" — this would have made the rsi2 escape visible today.

The "skipped" channel is the trust-building one: it tells the operator *what reconcile chose not to touch* and why, so a silently-phantom row isn't just rendered as confidently-running.

---

## Test Gaps

`tests/milodex/strategies/test_orphan_reconciliation.py` (current):
- `test_reconciles_open_run_with_no_live_runner` — passes today (PID in lock genuinely dead).
- `test_leaves_open_run_with_live_lock_holder` — acquires the lock *inside the test process*, so the lock's PID IS a real live runner. **Does not exercise PID-reuse.**
- `test_no_open_runs_returns_empty` — degenerate case.

Missing — and required before the fix can be considered shipped:

1. **Recycled-PID classification test.** Write a lock whose `pid` is `os.getpid()` (live) but whose `started_at` is `datetime.now() + timedelta(seconds=10)` — the lock was written *after* the live process started, which is the recycled-PID signature. Reconcile should classify as dead and close the row.
2. **Lock-cleanup-on-reconcile test.** Open a run row, write a lock for a clearly-dead PID, run reconcile. Assert: the run row is closed AND the lock file no longer exists.
3. **Mixed-cohort test.** Six open run rows, three with truly-dead PIDs, two with recycled-PID locks, one with a genuinely-live runner. Assert reconcile closes the five non-live rows + unlinks their five locks, and leaves the one live row + lock untouched.

---

## Tracking

Not filed in any external tracker (project does not use Linear). This document is the canonical defect record. Suggested follow-up: a TDD-driven fix PR that lands the three tests above first, then the start-time-identity check + lock unlink. Risk-layer adjacency (operator-visible runner state directly informs trust in the autonomy boundary) argues for the PM-mode two-stage review pattern from CLAUDE.md — Opus reviewer warranted on the implementation PR.

---

## Follow-up: known residual limitations (post-fix)

The fix PR lands the start-time identity check + lock unlink as described above, plus a `_logger.warning` when start-time introspection is unavailable (Opus-review finding). Two narrower issues remain explicitly **deferred**, not silently accepted:

### Residual 1 — bootstrap-reconcile vs. concurrent runner-spawn race (narrow TOCTOU)

`reconcile_orphaned_runs_on_bootstrap` reads `current_holder()` non-locking, decides "dead," then closes the DB row and unlinks the lock. If a brand-new runner for the same strategy writes its lock between the read and the unlink, the unlink wipes the new runner's lock. The runner's in-memory `self._held = True` keeps it operating, but a subsequent acquire attempt would now succeed cleanly → two runners holding the same logical lock.

**Bound:** the window is narrow (microseconds) and the workflow that triggers it is unusual — a CLI `milodex strategy run` invoked *during* a GUI bootstrap. Standard operator workflow does not produce this race because both spawn paths route through the same GUI surface that calls reconcile before spawning anything new.

**Defer reason:** the fix (re-read holder before unlink, skip if `started_at` differs from what reconcile saw) adds non-trivial complexity to a path the standard workflow does not exercise. Worth revisiting if multi-runner deployment or CLI-during-GUI workflows become first-class.

### Residual 2 — no-ctypes / no-/proc fallback regime

On a Windows env where `ctypes` is unavailable (very stripped install, sandbox without `ctypes.windll`), `_process_start_time` returns `None`, the identity check is skipped, and the code falls back to bare PID-existence. In that regime the original 2026-05-19 defect reproduces — a recycled PID is classified as a live runner.

**Mitigation in this PR:** `_logger.warning` fires every time the fallback is taken, naming the pid and strategy_id and pointing at this document. Operators see a noisy reconcile log instead of a silently-degraded safeguard.

**Defer reason:** the project's primary dev/run environment ships ctypes (standard CPython on Windows + standard CPython on macOS/Linux with `/proc` on Linux). Hardening against stripped-environment regression is genuinely out of scope for the phase. Worth revisiting if PyInstaller bundling or a sandboxed-runtime deployment surface materializes — both could plausibly produce the no-ctypes regime.
