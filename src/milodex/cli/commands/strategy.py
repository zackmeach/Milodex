"""strategy run command."""

from __future__ import annotations

import argparse

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.config import get_locks_dir
from milodex.core.advisory_lock import AdvisoryLock


def register(subparsers: argparse._SubParsersAction) -> None:
    strategy_parser = subparsers.add_parser("strategy", help="Run configured strategies.")
    add_global_flags(strategy_parser)
    strategy_subparsers = strategy_parser.add_subparsers(dest="strategy_command", required=True)
    strategy_run_parser = strategy_subparsers.add_parser(
        "run",
        help="Run one strategy as a foreground paper-trading session.",
    )
    add_global_flags(strategy_run_parser)
    strategy_run_parser.add_argument(
        "strategy_id", help="Strategy identifier from the YAML config."
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.strategy_command != "run":
        raise ValueError(f"Unsupported strategy command: {args.strategy_command}")
    if ctx.get_trading_mode() != "paper":
        raise ValueError("strategy run is paper-only in Phase 1.")
    with AdvisoryLock(
        "milodex.runtime",
        locks_dir=ctx.locks_dir or get_locks_dir(),
        holder_name=f"milodex strategy run {args.strategy_id}",
    ):
        runner = ctx.get_strategy_runner(args.strategy_id)
        runner.run()
        return CommandResult(
            command="strategy.run",
            data={"strategy_id": args.strategy_id, "trading_mode": ctx.get_trading_mode()},
            human_lines=[f"Running strategy: {args.strategy_id}"],
        )
