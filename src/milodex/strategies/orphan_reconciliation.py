"""GUI-bootstrap reconciliation of orphaned strategy runs.

``event_store.reconcile_orphan_strategy_runs`` already exists, but it is
only invoked by ``StrategyRunner`` when *that same strategy* is started
again — lazy and per-strategy. A runner that is hard-killed and whose
strategy is never restarted leaves an ``ended_at IS NULL`` row forever,
and the active-ops read model (which trusts ``ended_at IS NULL`` with no
liveness check) renders it as a live "phantom" runner.

This module closes that gap: a global, liveness-gated sweep run once at
GUI bootstrap. An open run is only closed when its strategy holds **no
live advisory lock** — so a genuinely-running runner is never reaped.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from milodex.strategies.paper_runner_control import runner_lock_name

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from milodex.core.event_store import EventStore

_logger = logging.getLogger(__name__)

_ORPHAN_EXIT_REASON = "orphaned_no_live_runner"

# Small slack between a process's recorded start time and the moment the
# lock file is written. On a host running a real Milodex runner the
# observed gap is well under a second (the process writes its own lock
# inside `AdvisoryLock.acquire`), but clocks, filesystem timestamps, and
# the time it takes to construct the holder record can introduce sub-
# second drift. One second is generous enough to absorb that without
# being wide enough to let a recycled PID slip through — a host reboot
# guarantees a multi-minute gap.
_PID_REUSE_GRACE = timedelta(seconds=1)


def _has_live_runner(strategy_id: str, locks_dir: Path) -> bool:
    """Return ``True`` if a live process holds the runner lock for ``strategy_id``.

    Two-stage liveness check:
    1. The lock's recorded PID resolves to an existing process.
    2. That process's start time is not *later* than the lock's
       ``started_at`` (plus a small grace). If it is, the OS has
       reassigned the PID since the original holder died and the
       "live" process is unrelated to the lock — classify as dead.

    Stage 2 catches the post-reboot PID-reuse case that `AdvisoryLock.acquire`'s
    `_STALE_LOCK_MAX_AGE_SECONDS` fallback cannot: when the lock is only
    hours old, age is uninformative; process-start-time is the cleaner
    discriminator. See docs/reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md.

    If the platform cannot report a process start time, falls back to
    stage 1 only — no regression versus the pre-fix behavior.
    """
    from milodex.core.advisory_lock import (
        AdvisoryLock,
        _process_exists,
        _process_start_time,
    )

    lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
    holder = lock.current_holder()
    if holder is None or not _process_exists(holder.pid):
        return False
    proc_start = _process_start_time(holder.pid)
    if proc_start is None:
        # Introspection unavailable — trust bare PID-existence (legacy).
        # Surface this loudly: in this regime the recycled-PID safeguard
        # is degraded, so a post-reboot phantom *can* slip through. The
        # only thing worse than a silent recovery is a silently-degraded
        # safety net. Operator sees the warning; reconcile still proceeds
        # so a system without ctypes / /proc isn't permanently wedged.
        _logger.warning(
            "Orphan reconcile: process-start-time introspection unavailable "
            "for pid %d (holder of strategy %r). Falling back to bare PID-"
            "existence check — a recycled PID in this regime would be mis-"
            "classified as a live runner. See docs/reviews/"
            "2026-05-19-orphan-reconcile-pid-reuse-defect.md.",
            holder.pid,
            strategy_id,
        )
        return True
    return proc_start <= holder.started_at + _PID_REUSE_GRACE


def reconcile_orphaned_runs_on_bootstrap(
    event_store: EventStore,
    locks_dir: Path,
    *,
    now: datetime,
) -> list[str]:
    """Close open strategy runs whose strategy has no live runner.

    Returns the sorted list of strategy ids that were reconciled. Safe to
    call repeatedly (idempotent: a closed run is no longer open).
    """
    open_strategy_ids = sorted(
        {run.strategy_id for run in event_store.list_strategy_runs() if run.ended_at is None}
    )
    closed: list[str] = []
    for strategy_id in open_strategy_ids:
        if _has_live_runner(strategy_id, locks_dir):
            continue
        event_store.reconcile_orphan_strategy_runs(
            strategy_id=strategy_id,
            ended_at=now,
            exit_reason=_ORPHAN_EXIT_REASON,
        )
        # Unlink the stale lock so the strategy-runs row and the lock-file
        # surface stay in sync. Idempotent: missing_ok=True absorbs the
        # "no lock was on disk to begin with" case (e.g. a row left open
        # by a process that died before writing its lock).
        _stale_lock_path(strategy_id, locks_dir).unlink(missing_ok=True)
        closed.append(strategy_id)
    return closed


def _stale_lock_path(strategy_id: str, locks_dir: Path) -> Path:
    """Path of the advisory-lock file for ``strategy_id``.

    Mirrors :attr:`AdvisoryLock.path`; kept local here to avoid
    constructing a full ``AdvisoryLock`` just to read its file path.
    """
    return locks_dir / f"{runner_lock_name(strategy_id)}.lock"
