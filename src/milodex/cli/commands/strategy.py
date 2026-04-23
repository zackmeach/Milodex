"""strategy run command."""

from __future__ import annotations

import argparse

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.config import get_locks_dir
from milodex.core.advisory_lock import AdvisoryLock
from milodex.execution.models import ExecutionResult, ExecutionStatus


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


def _format_decision_line(result: ExecutionResult) -> str:
    req = result.execution_request
    side = req.side.value.upper()
    if result.status is ExecutionStatus.SUBMITTED:
        return f"fired {side} {req.symbol} x{req.quantity:g} — allowed"
    reason = result.risk_decision.summary or (result.message or "rejected")
    return f"rejected {side} {req.symbol} x{req.quantity:g} — {reason}"


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
        as_json = bool(getattr(args, "json_output", False))

        if not as_json:

            def on_cycle(results: list[ExecutionResult]) -> None:
                if not results:
                    print(
                        "no_action — strategy did not fire this cycle",
                        file=ctx.stdout,
                        flush=True,
                    )
                    return
                for result in results:
                    print(_format_decision_line(result), file=ctx.stdout, flush=True)

            runner.set_on_cycle_result(on_cycle)
            print(
                f"session: {runner.session_id}  strategy: {args.strategy_id}  "
                f"mode: {ctx.get_trading_mode()}",
                file=ctx.stdout,
                flush=True,
            )
        runner.run()
        return CommandResult(
            command="strategy.run",
            data={
                "strategy_id": args.strategy_id,
                "trading_mode": ctx.get_trading_mode(),
                "session_id": runner.session_id,
            },
            human_lines=[f"Session {runner.session_id} ended."],
        )
