"""Operator-run event-store compaction (safe tier).

Prunes cascade-safe backtest explanations — rows with a ``backtest_run_id`` and
no linked trade (the bulk of the per-cycle no_trade/no_signal evaluations) — and
VACUUMs to reclaim file space. Never touches live (NULL ``backtest_run_id``) rows,
never deletes a trade (the prune predicate guarantees no ``ON DELETE CASCADE``
fires), and never writes ``backtest_runs`` (metadata_json metrics + promotion
evidence are preserved). Deliberately a CLI maintenance action, not a migration:
VACUUM is heavy and must be a one-shot under the runtime lock, not run on every
``EventStore`` init. See docs/incidents/2026-05-29-runner-fleet-oom-freeze.md.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from milodex.core.event_store import EventStore


@dataclass(frozen=True)
class CompactionPlan:
    prunable_explanations: int
    db_size_bytes: int


@dataclass(frozen=True)
class CompactionResult:
    pruned_explanations: int
    backup_path: Path | None
    vacuumed: bool
    db_size_before_bytes: int
    db_size_after_bytes: int


def plan_compaction(event_store: EventStore) -> CompactionPlan:
    """Read-only: count what a compaction would prune + current DB size."""
    return CompactionPlan(
        prunable_explanations=event_store.count_prunable_backtest_explanations(),
        db_size_bytes=_db_size(event_store.path),
    )


def run_compaction(
    event_store: EventStore,
    *,
    make_backup: bool = True,
    vacuum: bool = True,
    now: datetime | None = None,
) -> CompactionResult:
    """Back up (optional) -> prune cascade-safe backtest explanations -> VACUUM."""
    size_before = _db_size(event_store.path)
    backup_path = _backup(event_store.path, now=now) if make_backup else None
    pruned = event_store.prune_backtest_explanations_without_trades()
    if vacuum:
        event_store.vacuum()
    return CompactionResult(
        pruned_explanations=pruned,
        backup_path=backup_path,
        vacuumed=vacuum,
        db_size_before_bytes=size_before,
        db_size_after_bytes=_db_size(event_store.path),
    )


def _db_size(path: Path) -> int:
    """Total event-store footprint: the main DB plus its WAL/SHM sidecars.

    Under WAL mode (the event store's default) committed rows live in ``-wal``
    until a checkpoint, so measuring the ``.db`` file alone understates the real
    footprint and hides what a VACUUM reclaims.
    """
    total = 0
    for candidate in (
        path,
        path.with_name(path.name + "-wal"),
        path.with_name(path.name + "-shm"),
    ):
        try:
            total += candidate.stat().st_size
        except OSError:
            pass
    return total


def _backup(path: Path, *, now: datetime | None = None) -> Path:
    ts = (now or datetime.now(tz=UTC)).strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.pre-compact-{ts}.bak")
    shutil.copy2(path, backup)
    return backup
