"""``milodex maintenance`` — operator-run event-store maintenance.

``compact`` prunes cascade-safe backtest evaluation explanations (backtest rows
with no linked trade) and VACUUMs to reclaim space. Dry-run by default; ``--apply``
mutates after writing a backup. Acquires the runtime advisory lock so it refuses
to run while a runner/GUI is live. See operations/maintenance.py for safety.

``reap-orphans`` closes phantom ``strategy_runs`` rows left by hard-killed runners
(the on-demand twin of the GUI's periodic reaper). Liveness-gated and lock-free —
it skips strategies with a live runner — so it does NOT take the runtime lock.
``--dry-run`` lists candidates without closing anything.
"""

from __future__ import annotations

import argparse
from typing import Any

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.core.advisory_lock import AdvisoryLock
from milodex.operations.maintenance import plan_compaction, run_compaction


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "maintenance",
        help="Operator-run event-store maintenance (compaction).",
        description=(
            "Deliberate, operator-run maintenance for the SQLite event store. "
            "`compact` prunes cascade-safe backtest evaluation explanations "
            "(backtest rows with no linked trade) and VACUUMs to reclaim space. "
            "Live rows, trades, backtest metrics, and promotion evidence are preserved."
        ),
    )
    add_global_flags(parser)
    parser.add_argument(
        "maintenance_action",
        nargs="?",
        choices=("compact", "reap-orphans"),
        default="compact",
        help="Maintenance action (default: compact).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        dest="maintenance_apply",
        help="Apply the prune + VACUUM. Without it, runs a read-only dry-run (counts only).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="maintenance_dry_run",
        help="(reap-orphans) List orphan candidates without closing any run.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        dest="maintenance_no_backup",
        help="Skip the pre-compaction DB backup (not recommended).",
    )
    parser.add_argument(
        "--no-vacuum",
        action="store_true",
        dest="maintenance_no_vacuum",
        help="Prune rows but skip VACUUM (file size won't shrink until a later VACUUM).",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if getattr(args, "maintenance_action", "compact") == "reap-orphans":
        return _run_reap_orphans(args, ctx)

    event_store = ctx.get_event_store()
    with AdvisoryLock(
        "milodex.runtime",
        locks_dir=ctx.locks_dir,
        holder_name="milodex maintenance",
    ):
        plan = plan_compaction(event_store)
        if not getattr(args, "maintenance_apply", False):
            data: dict[str, Any] = {
                "applied": False,
                "prunable_explanations": plan.prunable_explanations,
                "db_size_bytes": plan.db_size_bytes,
            }
            human = [
                "Compaction dry-run (no changes):",
                f"  prunable backtest explanations: {plan.prunable_explanations:,}",
                f"  current DB size: {_mb(plan.db_size_bytes)}",
                "Re-run with --apply to prune + VACUUM (a backup is written first).",
            ]
            return CommandResult(command="maintenance.compact", data=data, human_lines=human)

        result = run_compaction(
            event_store,
            make_backup=not getattr(args, "maintenance_no_backup", False),
            vacuum=not getattr(args, "maintenance_no_vacuum", False),
        )
        data = {
            "applied": True,
            "pruned_explanations": result.pruned_explanations,
            "backup_path": str(result.backup_path) if result.backup_path else None,
            "vacuumed": result.vacuumed,
            "db_size_before_bytes": result.db_size_before_bytes,
            "db_size_after_bytes": result.db_size_after_bytes,
        }
        human = [
            f"Compaction applied: pruned {result.pruned_explanations:,} backtest explanation(s).",
            f"  DB size: {_mb(result.db_size_before_bytes)} -> {_mb(result.db_size_after_bytes)}",
        ]
        if result.backup_path:
            human.append(f"  backup: {result.backup_path}")
        if not result.vacuumed:
            human.append(
                "  VACUUM skipped (--no-vacuum); file size unchanged until a later VACUUM."
            )
        return CommandResult(command="maintenance.compact", data=data, human_lines=human)


def _run_reap_orphans(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    """Close (or, with --dry-run, list) orphaned strategy_runs rows.

    No runtime advisory lock: the reaper is liveness-gated and re-checks the lock
    holder before mutating, so it is safe to run alongside live runners (it skips
    them). Mirrors the GUI's periodic reaper as an on-demand operator command.
    """
    from datetime import UTC, datetime

    from milodex.strategies.orphan_reconciliation import (
        _orphan_candidates,
        reconcile_orphaned_runs_on_bootstrap,
    )

    event_store = ctx.get_event_store()
    if getattr(args, "maintenance_dry_run", False):
        candidates = [sid for sid, _ in _orphan_candidates(event_store, ctx.locks_dir)]
        human = ["Reap-orphans dry-run (no changes):"]
        human += [f"  would close: {sid}" for sid in candidates] or ["  no orphan runs."]
        human.append(f"  {len(candidates)} orphan run(s) would be closed.")
        return CommandResult(
            command="maintenance.reap-orphans",
            data={"applied": False, "candidates": candidates},
            human_lines=human,
        )

    reaped = reconcile_orphaned_runs_on_bootstrap(
        event_store, ctx.locks_dir, now=datetime.now(tz=UTC)
    )
    return CommandResult(
        command="maintenance.reap-orphans",
        data={"applied": True, "reaped": reaped},
        human_lines=[f"Reaped {len(reaped)} orphan run(s): {', '.join(reaped) or '(none)'}."],
    )


def _mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"
