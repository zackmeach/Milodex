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

from typing import TYPE_CHECKING

from milodex.strategies.paper_runner_control import runner_lock_name

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from milodex.core.event_store import EventStore

_ORPHAN_EXIT_REASON = "orphaned_no_live_runner"


def _has_live_runner(strategy_id: str, locks_dir: Path) -> bool:
    """Return ``True`` if a live process holds the runner lock for ``strategy_id``.

    Mirrors the liveness semantics ``AdvisoryLock.acquire`` uses, so a
    crashed runner's stale lock counts as *not* live and its run is
    eligible for reconciliation.
    """
    from milodex.core.advisory_lock import AdvisoryLock, _process_exists

    lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
    holder = lock.current_holder()
    return holder is not None and _process_exists(holder.pid)


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
        closed.append(strategy_id)
    return closed
