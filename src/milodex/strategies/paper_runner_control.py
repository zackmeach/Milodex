"""Out-of-process control helpers for paper strategy runners."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def runner_lock_name(strategy_id: str) -> str:
    """Return the advisory-lock namespace for a paper runner."""
    return f"milodex.runtime.strategy.{strategy_id}"


def controlled_stop_request_path(locks_dir: Path, strategy_id: str) -> Path:
    """Return the file path used to request a controlled paper-runner stop."""
    return Path(locks_dir) / f"{runner_lock_name(strategy_id)}.controlled_stop.json"


def consume_controlled_stop_request(
    path: Path | None,
    *,
    strategy_id: str,
) -> dict[str, Any] | None:
    """Consume a pending controlled-stop request for ``strategy_id`` if present."""
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None

    if payload.get("strategy_id") != strategy_id or payload.get("mode") != "controlled":
        return None

    path.unlink(missing_ok=True)
    return payload


@dataclass(frozen=True)
class PaperRunnerStartResult:
    """Result returned when Bench launches a paper runner subprocess."""

    strategy_id: str
    pid: int
    command: tuple[str, ...]
    stop_request_path: Path
    launched_at: datetime


@dataclass(frozen=True)
class ControlledStopRequestResult:
    """Result returned when Bench requests a controlled paper-runner stop."""

    strategy_id: str
    request_path: Path
    requested_at: datetime
    holder: dict[str, Any]


class PaperRunnerControl:
    """Start and stop paper runners without blocking the GUI thread."""

    def __init__(
        self,
        *,
        locks_dir: Path,
        python_executable: str | None = None,
        cwd: Path | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self._locks_dir = Path(locks_dir)
        self._python_executable = python_executable or sys.executable
        self._cwd = Path(cwd) if cwd is not None else None
        self._log_dir = Path(log_dir) if log_dir is not None else None

    def _runner_log_path(self, strategy_id: str) -> Path | None:
        """Return the per-runner log file path, or ``None`` if unconfigured.

        When no ``log_dir`` was supplied the child's stdio is discarded
        (legacy ``DEVNULL`` behaviour). When configured, every launch gets
        its own timestamped file so an early crash (e.g. wrong-interpreter
        ``ImportError``) is recoverable instead of silently lost.
        """
        if self._log_dir is None:
            return None
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
        return self._log_dir / f"runner.{strategy_id}.{timestamp}.log"

    @staticmethod
    def _open_runner_stdio(
        log_path: Path | None,
    ) -> tuple[Any, Any, Any]:
        """Resolve the child's stdout/stderr targets.

        Returns ``(stdout, stderr, handle_to_close)``. When ``log_path`` is
        ``None`` the child's output is discarded (legacy behaviour). When a
        path is given, stdout and stderr are merged into that file so a
        detached runner's early failure is no longer invisible. The caller
        owns ``handle_to_close`` and must close it once the child has
        inherited its own descriptor.
        """
        if log_path is None:
            return subprocess.DEVNULL, subprocess.DEVNULL, None
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("a", encoding="utf-8")  # noqa: SIM115 - closed by caller
        return handle, subprocess.STDOUT, handle

    def start(self, strategy_id: str) -> PaperRunnerStartResult:
        """Launch ``milodex strategy run`` for ``strategy_id`` asynchronously."""
        self._locks_dir.mkdir(parents=True, exist_ok=True)
        stop_path = controlled_stop_request_path(self._locks_dir, strategy_id)
        stop_path.unlink(missing_ok=True)
        command = (
            self._python_executable,
            "-m",
            "milodex.cli.main",
            "strategy",
            "run",
            strategy_id,
        )
        log_path = self._runner_log_path(strategy_id)
        stdout, stderr, log_handle = self._open_runner_stdio(log_path)
        popen_kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": stdout,
            "stderr": stderr,
        }
        if self._cwd is not None:
            popen_kwargs["cwd"] = str(self._cwd)
        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(command, **popen_kwargs)  # noqa: S603
        finally:
            # The child has dup'd its own descriptor; release our handle so
            # the file is not held open by the (long-lived) GUI process.
            if log_handle is not None:
                log_handle.close()
        return PaperRunnerStartResult(
            strategy_id=strategy_id,
            pid=int(process.pid),
            command=command,
            stop_request_path=stop_path,
            launched_at=datetime.now(tz=UTC),
        )

    def request_controlled_stop(
        self,
        strategy_id: str,
        *,
        holder: dict[str, Any],
    ) -> ControlledStopRequestResult:
        """Write a controlled-stop request for the active runner to consume."""
        self._locks_dir.mkdir(parents=True, exist_ok=True)
        path = controlled_stop_request_path(self._locks_dir, strategy_id)
        requested_at = datetime.now(tz=UTC)
        payload = {
            "strategy_id": strategy_id,
            "mode": "controlled",
            "requested_at": requested_at.isoformat(),
            "holder": dict(holder),
        }
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp_path.replace(path)
        return ControlledStopRequestResult(
            strategy_id=strategy_id,
            request_path=path,
            requested_at=requested_at,
            holder=dict(holder),
        )
