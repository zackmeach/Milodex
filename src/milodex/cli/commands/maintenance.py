"""``milodex maintenance`` — operator-run event-store maintenance.

``compact`` prunes cascade-safe backtest evaluation explanations (backtest rows
with no linked trade) and VACUUMs to reclaim space. Dry-run by default; ``--apply``
mutates after writing a backup. Acquires the runtime advisory lock so it refuses
to run while a runner/GUI is live. See operations/maintenance.py for safety.
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
        choices=("compact",),
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


def _mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"
