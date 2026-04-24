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
import os
import platform
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


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
