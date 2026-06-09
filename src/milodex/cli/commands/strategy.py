"""strategy run command."""

from __future__ import annotations

import argparse
from pathlib import Path

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.config import get_locks_dir
from milodex.core.advisory_lock import AdvisoryLock
from milodex.execution.models import ExecutionResult, ExecutionStatus
from milodex.promotion.stage_compat import ALLOWED_STAGES_BY_MODE, RECOGNIZED_MODES
from milodex.strategies.loader import load_strategy_config
from milodex.strategies.paper_runner_control import (
    controlled_stop_request_path,
    evaluation_symbol_for_config,
    live_runner_eval_symbols,
    runner_lock_name,
)
from milodex.strategies.runner_status import collect_runner_statuses


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
    strategy_status_parser = strategy_subparsers.add_parser(
        "status",
        help="Show runner liveness, heartbeat, and last evaluation per strategy.",
    )
    add_global_flags(strategy_status_parser)
    strategy_status_parser.add_argument(
        "strategy_id",
        nargs="?",
        default=None,
        help="Optional strategy identifier; defaults to all strategies with recorded sessions.",
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

    The ``ALLOWED_STAGES_BY_MODE`` table lives in
    :mod:`milodex.promotion.stage_compat` so both this CLI command and the Bench
    facade read the same authoritative mapping.
    """
    if trading_mode not in RECOGNIZED_MODES:
        recognized = ", ".join(sorted(RECOGNIZED_MODES))
        msg = (
            f"Unrecognized trading mode '{trading_mode}'. "
            f"Recognized modes: {recognized}. "
            "Check your TRADING_MODE environment variable."
        )
        raise ValueError(msg)
    allowed = ALLOWED_STAGES_BY_MODE[trading_mode]
    if config_stage not in allowed:
        msg = (
            f"Strategy '{strategy_id}' has stage='{config_stage}' but the active "
            f"trading mode is '{trading_mode}'. "
            f"Expected stage(s): {', '.join(sorted(allowed))}. "
            "Promote the strategy to the correct stage before running it in this mode."
        )
        raise ValueError(msg)


def _format_status_block(entry: dict) -> list[str]:
    lines = [f"{entry['strategy_id']}  state: {entry['state']}"]
    if entry["session_id"] is not None:
        session_line = f"  session {entry['session_id']} started {entry['session_started_at']}"
        if entry["session_ended_at"] is not None:
            session_line += (
                f", ended {entry['session_ended_at']} (exit: {entry['exit_reason'] or 'unknown'})"
            )
        lines.append(session_line)
    if entry["holder_pid"] is not None:
        lines.append(
            f"  pid {entry['holder_pid']} on {entry['holder_hostname']}  "
            f"heartbeat: {entry['heartbeat']}"
        )
    lines.append(f"  last evaluation: {entry['last_eval_at'] or 'none this session'}")
    lines.append(f"  stop requested: {'yes' if entry['stop_requested'] else 'no'}")
    if entry["note"]:
        lines.append(f"  note: {entry['note']}")
    return lines


def _run_status(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    locks_dir = ctx.locks_dir or get_locks_dir()
    statuses = collect_runner_statuses(
        ctx.get_event_store(),
        config_dir=ctx.config_dir,
        locks_dir=locks_dir,
        strategy_id=args.strategy_id,
    )
    lines = ["Strategy Runner Status"]
    if not statuses:
        lines.append("No strategy sessions recorded.")
    for entry in statuses:
        lines.extend(_format_status_block(entry))
    return CommandResult(
        command="strategy.status",
        data={"statuses": statuses},
        human_lines=lines,
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.strategy_command == "status":
        return _run_status(args, ctx)
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

    locks_dir = ctx.locks_dir or get_locks_dir()
    eval_symbol = evaluation_symbol_for_config(strategy_config)
    live_by_symbol = live_runner_eval_symbols(
        ctx.config_dir,
        locks_dir,
        exclude_strategy_id=args.strategy_id,
    )
    if eval_symbol in live_by_symbol:
        colliding_strategy_id = live_by_symbol[eval_symbol]
        msg = (
            f"Refusing to start strategy '{args.strategy_id}': evaluation symbol "
            f"{eval_symbol!r} is already in use by runner "
            f"'{colliding_strategy_id}'. Stop the existing runner for "
            f"'{colliding_strategy_id}' before starting another strategy on the "
            "same symbol."
        )
        raise ValueError(msg)

    # Per ADR 0026 (concurrent multi-strategy uses per-process supervisor)
    # the runner lock is scoped per strategy_id, not global. Two strategies can
    # run concurrently in separate processes; the same strategy still refuses a
    # second invocation. Reconcile and trade submit retain the global
    # "milodex.runtime" lock (separate namespace), so they serialize against
    # each other but do not block runners. Account-state arbitration falls to
    # the broker via the risk evaluator's per-call position query (ADR 0024),
    # not to inter-process file locks.
    runner_lock = AdvisoryLock(
        runner_lock_name(args.strategy_id),
        locks_dir=locks_dir,
        holder_name=f"milodex strategy run {args.strategy_id}",
    )
    with runner_lock:
        runner = ctx.get_strategy_runner(args.strategy_id)
        # The runner's run() loop holds this lock for its whole (possibly
        # all-day) lifetime. Wire the lock's heartbeat so each poll cycle
        # refreshes the lock-file mtime; the advisory lock's recycled-PID
        # age fallback then cannot steal the lock from this live session.
        runner.set_lock_heartbeat(runner_lock.refresh)
        if getattr(runner, "_controlled_stop_request_path", None) is None:
            runner._controlled_stop_request_path = controlled_stop_request_path(  # noqa: SLF001
                locks_dir,
                args.strategy_id,
            )
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
