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
from typing import TYPE_CHECKING

from milodex.strategies.paper_runner_control import runner_lock_name

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from milodex.core.advisory_lock import LockHolder
    from milodex.core.event_store import EventStore

_logger = logging.getLogger(__name__)

_ORPHAN_EXIT_REASON = "orphaned_no_live_runner"


def _has_live_runner(strategy_id: str, locks_dir: Path) -> tuple[bool, LockHolder | None]:
    """Return ``(is_live, holder_snapshot)`` for ``strategy_id``'s runner lock.

    Thin wrapper over the shared, identity-verified
    :func:`milodex.core.advisory_lock.holder_is_live` (PID-existence +
    process-start-time identity; loud degrade when start-time introspection is
    unavailable). ``holder_snapshot`` is the ``LockHolder`` this liveness
    decision was made against (or ``None`` if no lock was on disk), so a caller
    can re-check the lock against the *exact* snapshot before mutating — without
    a second, desynchronised ``current_holder()`` read.

    Reads ``current_holder()`` exactly once here, before delegating;
    ``holder_is_live`` never re-reads the lock. The reaper's recheck→unlink
    guard relies on that single-read contract: a ``None`` holder during
    classification consumes exactly one ``current_holder()`` call.
    """
    from milodex.core.advisory_lock import AdvisoryLock, holder_is_live

    lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
    holder = lock.current_holder()
    return holder_is_live(holder), holder


def _orphan_candidates(
    event_store: EventStore, locks_dir: Path
) -> list[tuple[str, LockHolder | None]]:
    """Open-run strategies with no live runner, each paired with the holder
    snapshot the liveness decision saw. Pure / read-only. Sorted by strategy id.

    Shared by the reaper (which re-checks the snapshot before mutating) and the
    CLI ``maintenance reap-orphans --dry-run`` preview, so both agree on exactly
    which strategies are orphan candidates.
    """
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

    Returns the sorted list of strategy ids that were reconciled. Safe to
    call repeatedly (idempotent: a closed run is no longer open).

    The close and the unlink are each guarded by a fresh holder re-check
    against the classification snapshot (residual-1 TOCTOU): a runner that
    wrote its lock in the window is left alone. This makes periodic reaping
    safe against the GUI's worker-thread async spawn, which is *not* serialized
    against the reaper.

    Two guards, not one:

    - **Guard 1 (before row-close).** Sound because the spawning subprocess
      acquires its lock *before* it appends its open ``strategy_runs`` row
      (``strategy.py`` enters ``with runner_lock:`` before
      ``StrategyRunner.__init__`` appends the row — do not reorder). So an
      unchanged-dead holder here means no fresh row exists yet, and the
      strategy-id-scoped close touches only the old orphan row.
    - **Guard 2 (immediately before unlink).** A runner can acquire the lock
      in the window *after* guard 1 but *before* the unlink; it writes its lock
      before appending its row, so guard 1's close did not touch it — but
      unlinking its freshly-written lock would orphan a live runner. Re-confirm
      the holder is still the same dead snapshot right before the unlink; if a
      fresh holder appeared, skip the unlink (the already-closed old-orphan row
      stays correct and the next tick re-checks).

    See docs/reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md.
    """
    from milodex.core.advisory_lock import AdvisoryLock

    def _holder_started(lock: AdvisoryLock) -> datetime | None:
        holder = lock.current_holder()
        return holder.started_at if holder else None

    closed: list[str] = []
    for strategy_id, snapshot in _orphan_candidates(event_store, locks_dir):
        lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
        snapshot_started = snapshot.started_at if snapshot else None

        # Guard 1 — re-confirm the holder before closing the row.
        if _holder_started(lock) != snapshot_started:
            _logger.info(
                "Orphan reconcile: skipping %r — lock holder changed before the "
                "row-close (snapshot=%s).",
                strategy_id,
                snapshot_started,
            )
            continue
        event_store.reconcile_orphan_strategy_runs(
            strategy_id=strategy_id,
            ended_at=now,
            exit_reason=_ORPHAN_EXIT_REASON,
        )
        closed.append(strategy_id)

        # Guard 2 — re-confirm IMMEDIATELY before the unlink. If a runner
        # acquired the lock in the window since guard 1, leave its fresh lock
        # intact. The check and the unlink are adjacent, so the residual window
        # is a single filesystem call.
        if _holder_started(lock) != snapshot_started:
            _logger.info(
                "Orphan reconcile: closed orphan row for %r but skipping unlink "
                "— a fresh lock holder appeared before the unlink (snapshot=%s).",
                strategy_id,
                snapshot_started,
            )
            continue
        # Unlink the stale lock so the strategy-runs row and the lock-file
        # surface stay in sync. Idempotent: missing_ok=True absorbs the
        # "no lock was on disk to begin with" case (e.g. a row left open
        # by a process that died before writing its lock).
        _stale_lock_path(strategy_id, locks_dir).unlink(missing_ok=True)
    return closed


def _stale_lock_path(strategy_id: str, locks_dir: Path) -> Path:
    """Path of the advisory-lock file for ``strategy_id``.

    Mirrors :attr:`AdvisoryLock.path`; kept local here to avoid
    constructing a full ``AdvisoryLock`` just to read its file path.
    """
    return locks_dir / f"{runner_lock_name(strategy_id)}.lock"
