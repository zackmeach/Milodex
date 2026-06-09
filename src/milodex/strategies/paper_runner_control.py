"""Out-of-process control helpers for paper strategy runners."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from milodex.core.advisory_lock import LockHolder

from milodex.core.advisory_lock import AdvisoryLock, live_lock_holder
from milodex.strategies.loader import StrategyConfig, load_strategy_config, resolve_universe_ref

logger = logging.getLogger(__name__)

_INTERPRETER_PROBE_TIMEOUT_SECONDS = 15

# The runner is launched as ``python -m milodex.cli.main strategy run``.
# Probe that exact entrypoint, not the shallow ``milodex`` package: a
# half-broken interpreter (e.g. one with a corrupt pandas) can import the
# package yet fail the real import chain — the actual first-GUI-run cause.
_INTERPRETER_PROBE_IMPORT = "milodex.cli.main"


class PaperRunnerLaunchError(RuntimeError):
    """Raised when a paper runner is refused *before* any process is spawned.

    Distinct from a spawn failure: the launch never happened, so there is
    no orphan process or held lock to clean up. The message is surfaced to
    the operator via the Bench command facade.
    """


def runner_lock_name(strategy_id: str) -> str:
    """Return the advisory-lock namespace for a paper runner."""
    return f"milodex.runtime.strategy.{strategy_id}"


def resolved_universe_for_config(config: StrategyConfig) -> tuple[str, ...]:
    """Return the runtime universe for ``config``, resolving ``universe_ref`` when set."""
    resolved = config.universe
    if config.universe_ref is not None and not resolved:
        resolved = resolve_universe_ref(config.universe_ref, config.path)
    return resolved


def evaluation_symbol_for_config(config: StrategyConfig) -> str:
    """Return the strategy evaluation symbol (first resolved universe symbol).

    Matches :meth:`milodex.strategies.runner.StrategyRunner._evaluation_symbol`.
    """
    universe = resolved_universe_for_config(config)
    if not universe:
        msg = f"Strategy '{config.strategy_id}' has no resolvable universe for runtime execution."
        raise ValueError(msg)
    return universe[0]


def live_runner_eval_symbols(
    config_dir: Path,
    locks_dir: Path,
    *,
    exclude_strategy_id: str | None = None,
) -> dict[str, str]:
    """Map evaluation symbols to strategy IDs for other live runner locks.

    Returns ``{eval_symbol: strategy_id}`` for strategies whose per-strategy
    runner lock is held by an identity-verified live process. Stale or
    recycled-PID locks are ignored.
    """
    live_by_symbol: dict[str, str] = {}
    for path in sorted(Path(config_dir).glob("*.yaml")):
        try:
            config = load_strategy_config(path)
        except ValueError:
            continue
        if exclude_strategy_id is not None and config.strategy_id == exclude_strategy_id:
            continue
        lock = AdvisoryLock(runner_lock_name(config.strategy_id), locks_dir=locks_dir)
        if live_lock_holder(lock) is None:
            continue
        try:
            eval_symbol = evaluation_symbol_for_config(config)
        except ValueError:
            continue
        live_by_symbol[eval_symbol] = config.strategy_id
    return live_by_symbol


def controlled_stop_request_path(locks_dir: Path, strategy_id: str) -> Path:
    """Return the file path used to request a controlled paper-runner stop."""
    return Path(locks_dir) / f"{runner_lock_name(strategy_id)}.controlled_stop.json"


def _preserve_invalid_stop_request(path: Path, reason: str) -> None:
    """Set aside an unparseable stop-request file as ``<name>.invalid``.

    A malformed request must neither be consumed (it expressed *something*,
    just not parseably) nor re-parsed every poll cycle, and silently deleting
    it makes a mis-encoded stop request undiagnosable — the 2026-06-08/09
    soak showed stop-request encoding matters in practice. ``Path.replace``
    overwrites any older preserved copy (only the most recent invalid request
    is kept). Falls back to unlink — loudly — if the rename itself fails.
    """
    invalid_path = path.with_name(path.name + ".invalid")
    try:
        path.replace(invalid_path)
    except OSError as exc:
        logger.warning(
            "Invalid controlled-stop request at %s (%s); preserve-as-%s failed (%s) — removing",
            path,
            reason,
            invalid_path.name,
            exc,
        )
        path.unlink(missing_ok=True)
        return
    logger.warning(
        "Invalid controlled-stop request at %s (%s); preserved as %s — "
        "the stop request was NOT consumed",
        path,
        reason,
        invalid_path,
    )


def consume_controlled_stop_request(
    path: Path | None,
    *,
    strategy_id: str,
) -> dict[str, Any] | None:
    """Consume a pending controlled-stop request for ``strategy_id`` if present.

    Encoding: UTF-8; a UTF-8 BOM is tolerated (``utf-8-sig``). A file that
    cannot be decoded or parsed (e.g. a PowerShell-written UTF-16 file, or
    truncated JSON) is preserved as ``<name>.invalid`` with a warning rather
    than silently deleted, so a mis-encoded stop request is diagnosable. A
    transient read error leaves the file in place for the next poll.
    """
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        logger.warning("Controlled-stop request at %s unreadable (%s); will retry", path, exc)
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _preserve_invalid_stop_request(path, repr(exc))
        return None

    if not isinstance(payload, dict):
        _preserve_invalid_stop_request(path, f"non-object JSON payload ({type(payload).__name__})")
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


def _compute_creation_flags() -> int:
    """Return the subprocess Popen creationflags for a detached paper runner.

    CREATE_NO_WINDOW (0x08000000) is the correct console-suppression flag on
    Windows. DETACHED_PROCESS is mutually exclusive with CREATE_NO_WINDOW per
    MSDN (ERROR_INVALID_PARAMETER on combine) and paradoxically creates a
    console for a console-subsystem child .exe. Use CREATE_NO_WINDOW for
    suppression + CREATE_NEW_PROCESS_GROUP to keep the child outside the
    parent's group (Ctrl-C isolation).
    """
    flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    return flags


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

    def _interpreter_probe_command(self) -> list[str]:
        """Return the argv that proves the interpreter can run a runner.

        Imports the real runner entrypoint (``milodex.cli.main``), not the
        shallow ``milodex`` package, so a half-broken interpreter that can
        import the package but not the full launch chain is still caught.
        """
        return [
            self._python_executable,
            "-c",
            f"import {_INTERPRETER_PROBE_IMPORT}",
        ]

    def _verify_interpreter(self) -> str | None:
        """Return a refusal reason if the interpreter can't run a runner.

        The first-GUI-run wedge came from a GUI process whose interpreter
        could ``import milodex`` but blew up importing the runner entrypoint
        (a corrupt pandas in a non-venv Python). Probe the real entrypoint
        and fail loudly rather than spawn a doomed detached child. Returns
        ``None`` when the interpreter is usable.
        """
        try:
            completed = subprocess.run(  # noqa: S603
                self._interpreter_probe_command(),
                capture_output=True,
                timeout=_INTERPRETER_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return (
                f"Interpreter {self._python_executable!r} is not runnable "
                f"({exc.__class__.__name__}: {exc}). The paper runner was "
                "not started."
            )
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = detail[-1] if detail else f"exit code {completed.returncode}"
            return (
                f"Interpreter {self._python_executable!r} cannot import the "
                f"runner entrypoint {_INTERPRETER_PROBE_IMPORT!r} ({tail}). "
                "The paper runner was not started — this usually means the "
                "GUI is running outside the project virtualenv."
            )
        return None

    def _existing_live_runner(self, strategy_id: str) -> LockHolder | None:
        """Return the live advisory-lock holder for ``strategy_id``, if any.

        Read-only: never acquires, refreshes, or releases the lock. Routes
        through the shared identity-verified liveness helper
        (:func:`milodex.core.advisory_lock.live_lock_holder`): a stale lock
        whose recorded PID is dead — or a recycled PID whose process started
        after the lock was written — is treated as absent, so a crashed runner
        does not block a legitimate relaunch. The child's ``O_EXCL`` acquire in
        :meth:`start` remains the final single-runner correctness backstop.
        """
        lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=self._locks_dir)
        return live_lock_holder(lock)

    def start(self, strategy_id: str) -> PaperRunnerStartResult:
        """Launch ``milodex strategy run`` for ``strategy_id`` asynchronously."""
        interpreter_problem = self._verify_interpreter()
        if interpreter_problem is not None:
            raise PaperRunnerLaunchError(interpreter_problem)
        existing = self._existing_live_runner(strategy_id)
        if existing is not None:
            raise PaperRunnerLaunchError(
                f"A paper runner for {strategy_id!r} is already running "
                f"(pid {existing.pid} on {existing.hostname}, started "
                f"{existing.started_at.isoformat()}). Refusing to launch a "
                "duplicate — stop the existing runner first."
            )
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
        creationflags = _compute_creation_flags()
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
