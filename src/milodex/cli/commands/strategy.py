"""strategy run command."""

from __future__ import annotations

import argparse
from pathlib import Path

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.config import get_locks_dir
from milodex.core.advisory_lock import AdvisoryLock
from milodex.execution.models import ExecutionResult, ExecutionStatus
from milodex.strategies.loader import load_strategy_config


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


def _resolve_config_path(strategy_id: str, config_dir: Path) -> Path:
    """Locate the YAML file whose ``strategy.id`` matches *strategy_id*."""
    for path in sorted(config_dir.glob("*.yaml")):
        try:
            cfg = load_strategy_config(path)
        except ValueError:
            continue
        if cfg.strategy_id == strategy_id:
            return path
    msg = f"Strategy config not found for strategy_id: {strategy_id}"
    raise ValueError(msg)


def _format_decision_line(result: ExecutionResult) -> str:
    req = result.execution_request
    side = req.side.value.upper()
    if result.status is ExecutionStatus.SUBMITTED:
        return f"fired {side} {req.symbol} x{req.quantity:g} — allowed"
    reason = result.risk_decision.summary or (result.message or "rejected")
    if result.risk_decision.reason_codes:
        reason = f"{reason} ({', '.join(result.risk_decision.reason_codes)})"
    return f"rejected {side} {req.symbol} x{req.quantity:g} — {reason}"


# TODO(phase-2): when live mode opens, extend this table AND relax the
# `trading_mode != "paper"` guard in run() below.
_ALLOWED_STAGES_BY_MODE: dict[str, frozenset[str]] = {
    "paper": frozenset({"paper"}),
}

_RECOGNIZED_MODES: frozenset[str] = frozenset(_ALLOWED_STAGES_BY_MODE)


def _check_stage_compatibility(strategy_id: str, config_stage: str, trading_mode: str) -> None:
    """Raise ValueError if config_stage is not eligible to run under trading_mode.

    Enforces the promotion-pipeline boundary: a strategy must have been promoted
    to at least the stage that corresponds to the active trading mode before it
    can be run.  Running a backtest-stage strategy in paper context (or a
    paper-stage strategy in live context) is a data-integrity violation — the
    strategy hasn't passed the promotion gates required for that environment.

    Strict exact-match policy: paper mode only accepts paper-stage strategies.
    If no convention existed, the strictest interpretation was chosen so that
    a reviewer can relax it rather than having to tighten a bug.
    """
    if trading_mode not in _RECOGNIZED_MODES:
        recognized = ", ".join(sorted(_RECOGNIZED_MODES))
        msg = (
            f"Unrecognized trading mode '{trading_mode}'. "
            f"Recognized modes: {recognized}. "
            "Check your TRADING_MODE environment variable."
        )
        raise ValueError(msg)
    allowed = _ALLOWED_STAGES_BY_MODE[trading_mode]
    if config_stage not in allowed:
        msg = (
            f"Strategy '{strategy_id}' has stage='{config_stage}' but the active "
            f"trading mode is '{trading_mode}'. "
            f"Expected stage(s): {', '.join(sorted(allowed))}. "
            "Promote the strategy to the correct stage before running it in this mode."
        )
        raise ValueError(msg)


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.strategy_command != "run":
        raise ValueError(f"Unsupported strategy command: {args.strategy_command}")
    if ctx.get_trading_mode() != "paper":
        raise ValueError("strategy run is paper-only in Phase 1.")

    # Stage-compatibility guard: refuse to run a strategy whose promotion stage
    # does not match the active trading mode.  This prevents backtest-stage
    # strategies from executing in paper context — a data-integrity defect
    # caught in the 2026-05-07 audit where three backtest-stage strategies ran
    # inside the paper runner.  Load the config lightweight (no registry
    # instantiation) before acquiring the advisory lock so the error is cheap
    # and the lock is never held for a strategy that would be refused anyway.
    config_path = _resolve_config_path(args.strategy_id, ctx.config_dir)
    strategy_config = load_strategy_config(config_path)
    _check_stage_compatibility(args.strategy_id, strategy_config.stage, ctx.get_trading_mode())

    # Per ADR 0026 (concurrent multi-strategy uses per-process supervisor)
    # the runner lock is scoped per strategy_id, not global. Two strategies can
    # run concurrently in separate processes; the same strategy still refuses a
    # second invocation. Reconcile and trade submit retain the global
    # "milodex.runtime" lock (separate namespace), so they serialize against
    # each other but do not block runners. Account-state arbitration falls to
    # the broker via the risk evaluator's per-call position query (ADR 0024),
    # not to inter-process file locks.
    with AdvisoryLock(
        f"milodex.runtime.strategy.{args.strategy_id}",
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
