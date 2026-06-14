"""Single-process advisory lock for serializing state-changing operations.

Phase 1 is single-operator, single-process per the concurrency model in
``docs/OPERATIONS.md``. This module provides a **lock file**-based
advisory mutex used by ``StrategyRunner`` and submit-capable CLI
commands to refuse to start while another Milodex process already holds
the lock. Attempts to acquire a held lock raise
``AdvisoryLockError`` with the current holder's PID and start time so
the operator sees which process to stop.

Stale locks (where the recorded PID no longer exists) are automatically
reclaimed on acquire, so a crashed process never leaves the system
permanently blocked.

This is an **advisory** lock — the OS does not enforce it. Every
state-changing code path must call ``acquire`` explicitly.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_logger = logging.getLogger(__name__)

_STALE_LOCK_MAX_AGE_SECONDS = 12 * 60 * 60
"""Age-since-last-heartbeat past which a lock is reclaimable even if its
PID looks live.

This is **not** a wall-clock timer on the holder's lifetime. The
strategy runner's ``run()`` loop is explicitly documented in
``docs/OPERATIONS.md`` as safe to leave running all day/overnight, so a
healthy holder routinely lives far longer than this. The signal that
makes reclaim safe is :meth:`AdvisoryLock.refresh` — a live holder
heartbeats once per work cycle, so the lock-file mtime means "a live
holder refreshed this recently", and "older than this threshold" means
"no live holder has touched it for that long" → almost certainly an
orphan whose recorded PID was *recycled* by the OS onto an unrelated
live process (false "still held" → permanent restart deadlock), or a
lock written by a build whose ``ctypes``/``OpenProcess`` path could not
prove liveness.

Sizing, relative to the poll interval: the heartbeat fires every poll
cycle, and the longest poll interval in the system is 60 s (the ``1D``
bar size in ``strategies/runner.StrategyRunner`` — see
``_POLL_INTERVAL_BY_BAR_SIZE``). 12 h is therefore ~720 consecutive
missed heartbeats: vastly more slack than any plausible transient stall,
GC pause, or slow broker call, so an actively-heartbeating runner is
*never* reclaimable; yet still bounded well below "days", so a genuinely
dead-but-PID-recycled lock self-heals within half a day instead of
never. Do not lower this toward a small multiple of the poll interval —
a brief stall on a live holder must not surface a steal window.
"""

_PID_REUSE_GRACE = timedelta(seconds=1)
"""Slack between a process's recorded start time and the moment its lock file
is written, used by :func:`holder_is_live`.

On a host running a real Milodex runner the observed gap is well under a second
(the process writes its own lock inside :meth:`AdvisoryLock.acquire`), but
clocks, filesystem timestamps, and the time to construct the holder record can
introduce sub-second drift. One second is generous enough to absorb that
without being wide enough to let a recycled PID slip through — a host reboot
guarantees a multi-minute gap, so the recycled-PID signature stays detectable.
"""


@dataclass(frozen=True)
class LockHolder:
    """Metadata recorded in a live lockfile."""

    pid: int
    hostname: str
    holder_name: str
    started_at: datetime
    path: Path


class AdvisoryLockError(RuntimeError):
    """Raised when an advisory lock cannot be acquired."""

    def __init__(self, message: str, *, holder: LockHolder | None = None) -> None:
        super().__init__(message)
        self.holder = holder


class AdvisoryLock:
    """File-based advisory lock keyed by a logical lock name."""

    def __init__(
        self,
        name: str,
        *,
        locks_dir: Path,
        holder_name: str = "milodex",
    ) -> None:
        if not name or not name.strip():
            msg = "Advisory lock name must be non-empty."
            raise ValueError(msg)
        self._name = name.strip()
        self._locks_dir = locks_dir
        self._holder_name = holder_name
        self._lock_path = locks_dir / f"{self._name}.lock"
        self._held = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def path(self) -> Path:
        return self._lock_path

    def acquire(self) -> LockHolder:
        """Acquire the lock or raise ``AdvisoryLockError``."""
        self._locks_dir.mkdir(parents=True, exist_ok=True)

        existing_holder = self._read_holder()
        if existing_holder is not None and _process_exists(existing_holder.pid):
            if self._lock_is_past_max_age():
                # The recorded PID resolves to a live process, but the
                # lock file is older than any legitimate single-session
                # hold (see _STALE_LOCK_MAX_AGE_SECONDS). Treat the PID as
                # almost certainly recycled (the original holder is long
                # dead) and reclaim — loudly, because the alternative is a
                # permanently wedged system that can never restart.
                _logger.warning(
                    "Reclaiming stale advisory lock '%s': lock file age "
                    "exceeds %ds and its recorded pid %d is presumed "
                    "recycled (original holder %s on %s, started %s). "
                    "If a legitimate Milodex process is genuinely still "
                    "running this long, stop it and investigate.",
                    self._name,
                    _STALE_LOCK_MAX_AGE_SECONDS,
                    existing_holder.pid,
                    existing_holder.holder_name,
                    existing_holder.hostname,
                    existing_holder.started_at.isoformat(),
                )
            else:
                msg = (
                    f"Advisory lock '{self._name}' is held by {existing_holder.holder_name} "
                    f"(pid {existing_holder.pid} on {existing_holder.hostname}, "
                    f"started {existing_holder.started_at.isoformat()}). "
                    "Stop the other process or wait for it to exit, then retry."
                )
                raise AdvisoryLockError(msg, holder=existing_holder)

        if existing_holder is not None:
            self._lock_path.unlink(missing_ok=True)

        try:
            fd = os.open(
                str(self._lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            # Another process won the race between our stale-check and open.
            holder = self._read_holder()
            msg = (
                f"Advisory lock '{self._name}' was acquired by another process during acquisition."
            )
            raise AdvisoryLockError(msg, holder=holder) from exc

        holder = LockHolder(
            pid=os.getpid(),
            hostname=platform.node(),
            holder_name=self._holder_name,
            started_at=datetime.now(tz=UTC),
            path=self._lock_path,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "pid": holder.pid,
                        "hostname": holder.hostname,
                        "holder_name": holder.holder_name,
                        "started_at": holder.started_at.isoformat(),
                    },
                    handle,
                    sort_keys=True,
                )
        except Exception:
            self._lock_path.unlink(missing_ok=True)
            raise

        self._held = True
        return holder

    def acquire_blocking(
        self,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.05,
    ) -> LockHolder:
        """Acquire, waiting up to ``timeout_seconds`` for a live holder to release.

        Unlike :meth:`acquire` (which refuses immediately when another live
        process holds the lock), this polls until the lock is free or the
        timeout elapses, then **fails closed**: it raises ``AdvisoryLockError``
        rather than proceeding unserialized. Stale / recycled-PID holders are
        reclaimed by the underlying :meth:`acquire`, so a dead holder never
        blocks for the full timeout. Used to serialize the cross-process submit
        critical section (see
        ``docs/architecture/2026-06-13-cross-process-submit-serialization-design.md``).
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                return self.acquire()
            except AdvisoryLockError as exc:
                if time.monotonic() >= deadline:
                    msg = (
                        f"Advisory lock '{self._name}' not acquired within "
                        f"{timeout_seconds:.1f}s; another holder kept it for the full "
                        "wait. Declined (fail-closed)."
                    )
                    raise AdvisoryLockError(msg, holder=exc.holder) from exc
                time.sleep(poll_interval_seconds)

    def refresh(self) -> None:
        """Heartbeat: bump the held lock file's mtime to "now".

        **Liveness invariant.** The age fallback in :meth:`acquire`
        (:data:`_STALE_LOCK_MAX_AGE_SECONDS`) reclaims a lock whose mtime
        is older than the threshold even if its recorded PID looks live,
        to break a recycled-PID deadlock. That is only safe if a *live*
        holder keeps its mtime fresh — otherwise a long-running but
        perfectly healthy holder (e.g. the strategy runner, whose
        ``run()`` loop is documented as safe to leave running all
        day/overnight) would have an "old" lock and a second invocation
        would steal it mid-session, causing duplicate trade submission.
        Any holder that lives longer than the threshold **MUST** call
        this once per work cycle so the mtime means "a live holder
        refreshed this recently" rather than a wall-clock timer.

        Cheap (a single ``os.utime`` on the lock path). No-op-safe: if
        this instance does not hold the lock, or the file is gone, or the
        stat/utime fails transiently, it returns silently — a missed
        heartbeat must never crash the holder; the worst case degrades to
        the pre-heartbeat behaviour for that one cycle.
        """
        if not self._held:
            return
        try:
            os.utime(self._lock_path, None)
        except OSError:
            return

    def release(self) -> None:
        """Release the lock if currently held by this instance."""
        if not self._held:
            return
        current = self._read_holder()
        if current is not None and current.pid == os.getpid():
            self._lock_path.unlink(missing_ok=True)
        self._held = False

    def __enter__(self) -> LockHolder:
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()

    def current_holder(self) -> LockHolder | None:
        """Read the current lock holder without acquiring or mutating anything.

        Pure read-only inspection surface for observers (e.g. the dashboard).
        Returns the same value as the internal stale-detection read; never
        creates, refreshes, or releases the lock.
        """
        return self._read_holder()

    def _lock_is_past_max_age(self) -> bool:
        """Return ``True`` if the lock file is older than the stale threshold.

        Uses the file mtime (wall clock). A missing file or an
        ``OSError`` reading its stat is treated as "not past age" — the
        caller already established the holder exists, and we must never
        weaken correctness for a genuinely-fresh lock on a transient
        stat error; the conservative outcome is "still held".
        """
        try:
            mtime = self._lock_path.stat().st_mtime
        except OSError:
            return False
        return (time.time() - mtime) > _STALE_LOCK_MAX_AGE_SECONDS

    def _read_holder(self) -> LockHolder | None:
        if not self._lock_path.exists():
            return None
        try:
            raw = self._lock_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return None
        pid = data.get("pid")
        if not isinstance(pid, int):
            return None
        started_at_raw = data.get("started_at")
        try:
            started_at = (
                datetime.fromisoformat(started_at_raw)
                if isinstance(started_at_raw, str)
                else datetime.now(tz=UTC)
            )
        except ValueError:
            started_at = datetime.now(tz=UTC)
        return LockHolder(
            pid=pid,
            hostname=str(data.get("hostname", "")),
            holder_name=str(data.get("holder_name", "milodex")),
            started_at=started_at,
            path=self._lock_path,
        )


@contextmanager
def advisory_lock(
    name: str,
    *,
    locks_dir: Path,
    holder_name: str = "milodex",
) -> Iterator[LockHolder]:
    """Context manager wrapper around :class:`AdvisoryLock`."""
    lock = AdvisoryLock(name, locks_dir=locks_dir, holder_name=holder_name)
    holder = lock.acquire()
    try:
        yield holder
    finally:
        lock.release()


def holder_is_live(holder: LockHolder | None) -> bool:
    """Return ``True`` iff ``holder`` resolves to a genuinely-live process.

    The single shared, identity-verified answer to "is this advisory-lock
    holder actually a live process?" — consolidated from three prior, weaker
    implementations (orphan reconcile, paper-runner control, bench peek) so the
    operator-trust surfaces ride the strongest check, not the weakest.

    Two-stage liveness:

    1. The recorded PID resolves to an existing process.
    2. That process's start time is not *later* than the lock's ``started_at``
       (plus :data:`_PID_REUSE_GRACE`). A later start time means the OS
       reassigned the PID after the original holder died, so the live process
       is unrelated to the lock — classify as dead (recycled PID).

    Stage 2 catches the post-reboot PID-reuse case that
    :meth:`AdvisoryLock.acquire`'s :data:`_STALE_LOCK_MAX_AGE_SECONDS`
    fallback cannot: when the lock is only hours old, age is uninformative;
    process-start-time is the cleaner discriminator. See
    docs/reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md.

    If the platform cannot report a process start time, falls back to stage 1
    (bare PID-existence) and logs a loud WARNING — in that regime a recycled
    PID *can* be mis-classified as live, and a silently-degraded safety net is
    worse than a noisy one. No regression versus the pre-consolidation
    behavior.
    """
    if holder is None or not _process_exists(holder.pid):
        return False
    proc_start = _process_start_time(holder.pid)
    if proc_start is None:
        _logger.warning(
            "Liveness check: process-start-time introspection unavailable for "
            "pid %d (holder %r of lock %s). Falling back to bare PID-existence "
            "— a recycled PID in this regime would be mis-classified as a live "
            "runner. See docs/reviews/"
            "2026-05-19-orphan-reconcile-pid-reuse-defect.md.",
            holder.pid,
            holder.holder_name,
            holder.path.name,
        )
        return True
    return proc_start <= holder.started_at + _PID_REUSE_GRACE


def live_lock_holder(lock: AdvisoryLock) -> LockHolder | None:
    """Return ``lock``'s current holder iff it is identity-verified live.

    Read-only: never acquires, refreshes, or releases. The single shared
    "is a live runner holding this lock?" surface for observers — the GUI
    active-ops badge, the bench stop/duplicate-start paths, and the orphan
    reaper. Returns ``None`` when the lock is free *or* held only by a stale /
    recycled-PID lock file (so a dead-but-lock-present runner is reported
    honestly as absent). Callers needing the exact snapshot the decision was
    made against (e.g. the reaper's recheck→unlink guard) should read
    :meth:`current_holder` themselves and pass it to :func:`holder_is_live`.
    """
    holder = lock.current_holder()
    return holder if holder_is_live(holder) else None


def _process_exists(pid: int) -> bool:
    """Return ``True`` if a process with the given PID is currently running."""
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        return _windows_process_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't own it.
        return True
    except OSError:
        return False
    return True


def _process_start_time(pid: int) -> datetime | None:
    """Return the wall-clock time the process owning ``pid`` was started.

    Returns ``None`` if the process does not exist, if introspection failed,
    or if the platform's start-time surface is unavailable. Callers should
    fall back to a less strict liveness check in that case.

    Used to detect PID reuse: a live process whose start time is *later*
    than an advisory lock's recorded ``started_at`` cannot be the lock's
    original holder — the OS reassigned the PID between the holder's death
    and the probe. The lock-acquire path's ``_STALE_LOCK_MAX_AGE_SECONDS``
    fallback handles the long-idle PID-reuse case (where lock-file age is
    the cleaner signal); start-time identity handles the host-reset case
    (where lock age is uninformative because everything is fresh).
    """
    if pid <= 0:
        return None
    if platform.system() == "Windows":
        return _windows_process_start_time(pid)
    return _posix_process_start_time(pid)


def _windows_process_exists(pid: int) -> bool:
    # Note: the unknowable cases below deliberately return True ("assume
    # held") so a genuinely-fresh lock is never stolen on a probe failure.
    # The permanent-deadlock that this used to cause in a stripped env
    # (no ctypes / OpenProcess blocked) is now bounded by the lock-file
    # age fallback in AdvisoryLock.acquire: a lock this code cannot prove
    # dead still self-heals once it is older than _STALE_LOCK_MAX_AGE
    # _SECONDS, instead of blocking restarts forever.
    try:
        import ctypes
        import ctypes.wintypes
    except Exception:
        return True

    process_query_limited_information = 0x1000
    still_active = 259

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        success = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not success:
            # If we can't tell, err on the side of "still held" to avoid
            # racing in on a sibling process.
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


# FILETIME counts 100-nanosecond intervals since 1601-01-01 UTC.
# Unix epoch (1970-01-01) is 11644473600 seconds after that, or 1.16e17 ticks.
_FILETIME_EPOCH_OFFSET_MICROSECONDS = 11_644_473_600 * 1_000_000


def _windows_process_start_time(pid: int) -> datetime | None:
    # Mirrors _windows_process_exists's defensive shape: on any probe
    # failure return None so callers fall back to the bare PID-existence
    # check rather than mis-classifying a live runner as recycled.
    try:
        import ctypes
        import ctypes.wintypes
    except Exception:
        return None

    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        creation = ctypes.wintypes.FILETIME()
        exit_t = ctypes.wintypes.FILETIME()
        kernel_t = ctypes.wintypes.FILETIME()
        user_t = ctypes.wintypes.FILETIME()
        success = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_t),
            ctypes.byref(kernel_t),
            ctypes.byref(user_t),
        )
        if not success:
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        microseconds_since_1970 = ticks // 10 - _FILETIME_EPOCH_OFFSET_MICROSECONDS
        if microseconds_since_1970 <= 0:
            return None
        return datetime.fromtimestamp(microseconds_since_1970 / 1_000_000, tz=UTC)
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_start_time(pid: int) -> datetime | None:
    # /proc/<pid>/stat field 22 = start time in clock ticks since boot.
    # /proc/stat 'btime' = boot time in seconds since the Unix epoch.
    # Together they give the process start in wall-clock time.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            content = f.read()
        # Field 2 (comm) is parenthesized and may contain spaces; the
        # rightmost ')' is the safe delimiter before fields 3+.
        rparen = content.rfind(")")
        if rparen < 0:
            return None
        fields = content[rparen + 1 :].split()
        # After ')' we are at field 3 (state). Field 22 (start time) is
        # index 19 in this remaining slice.
        start_ticks = int(fields[19])
        with open("/proc/stat", encoding="utf-8") as f:
            btime = next(int(line.split()[1]) for line in f if line.startswith("btime "))
        clk_tck = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        start_epoch = btime + start_ticks / clk_tck
        return datetime.fromtimestamp(start_epoch, tz=UTC)
    except (OSError, ValueError, StopIteration, IndexError, KeyError):
        return None
