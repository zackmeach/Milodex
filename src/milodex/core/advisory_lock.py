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
from datetime import UTC, datetime
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
