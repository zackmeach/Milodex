"""experiment create/list/update/show subcommands — F-PR2.

Thin facade over EventStore experiment-registry methods (append_experiment,
list_experiments, update_experiment, get_experiment). No business rules here
(ADR 0051) — all logic lives in the store layer (F-PR1).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.core.event_store import ExperimentEvent

_TERMINAL_STATUSES = ("promoted", "rejected", "failed", "inconclusive", "abandoned", "active")
_STAGES = ("backtest", "paper", "micro_live", "live")


def register(subparsers: argparse._SubParsersAction) -> None:
    experiment_parser = subparsers.add_parser(
        "experiment",
        help="Experiment registry — record and inspect strategy ideas (R-PRM-011).",
    )
    add_global_flags(experiment_parser)
    exp_sub = experiment_parser.add_subparsers(dest="experiment_command", required=True)

    # ── create ────────────────────────────────────────────────────────────────
    create_parser = exp_sub.add_parser(
        "create",
        help="Record a new experiment in the registry.",
    )
    add_global_flags(create_parser)
    create_parser.add_argument("--experiment-id", required=True)
    create_parser.add_argument("--hypothesis", required=True)
    create_parser.add_argument(
        "--stage-reached",
        required=True,
        choices=_STAGES,
        dest="stage_reached",
    )
    create_parser.add_argument(
        "--terminal-status",
        required=True,
        choices=_TERMINAL_STATUSES,
        dest="terminal_status",
    )
    create_parser.add_argument("--rationale", required=True)
    create_parser.add_argument("--strategy-id", default=None, dest="strategy_id")
    create_parser.add_argument("--config-hash", default=None, dest="config_hash")
    create_parser.add_argument("--lessons", default=None)
    create_parser.add_argument("--revisitable", action="store_true", default=False)
    create_parser.add_argument(
        "--evidence-json",
        default=None,
        dest="evidence_json",
        help="Inline JSON string or path to a JSON file.",
    )

    # ── list ──────────────────────────────────────────────────────────────────
    list_parser = exp_sub.add_parser(
        "list",
        help="List latest-per-experiment entries (optional terminal-status filter).",
    )
    add_global_flags(list_parser)
    list_parser.add_argument(
        "--terminal-status",
        default=None,
        choices=_TERMINAL_STATUSES,
        dest="terminal_status",
    )

    # ── update ────────────────────────────────────────────────────────────────
    update_parser = exp_sub.add_parser(
        "update",
        help="Append an update to an existing experiment (append-only).",
    )
    add_global_flags(update_parser)
    update_parser.add_argument("--experiment-id", required=True, dest="experiment_id")
    update_parser.add_argument(
        "--terminal-status",
        default=None,
        choices=_TERMINAL_STATUSES,
        dest="terminal_status",
    )
    update_parser.add_argument(
        "--stage-reached",
        default=None,
        choices=_STAGES,
        dest="stage_reached",
    )
    update_parser.add_argument("--rationale", default=None)
    update_parser.add_argument("--hypothesis", default=None)
    update_parser.add_argument("--lessons", default=None)
    update_parser.add_argument("--strategy-id", default=None, dest="strategy_id")
    update_parser.add_argument("--config-hash", default=None, dest="config_hash")
    update_parser.add_argument("--revisitable", action="store_true", default=False)
    update_parser.add_argument(
        "--evidence-json",
        default=None,
        dest="evidence_json",
        help="Inline JSON string or path to a JSON file.",
    )

    # ── show ──────────────────────────────────────────────────────────────────
    show_parser = exp_sub.add_parser(
        "show",
        help="Show the latest entry for an experiment.",
    )
    add_global_flags(show_parser)
    show_parser.add_argument("--experiment-id", required=True, dest="experiment_id")


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.experiment_command == "create":
        return _create(args, ctx)
    if args.experiment_command == "list":
        return _list(args, ctx)
    if args.experiment_command == "update":
        return _update(args, ctx)
    if args.experiment_command == "show":
        return _show(args, ctx)
    raise ValueError(f"Unsupported experiment command: {args.experiment_command}")


# ── helpers ───────────────────────────────────────────────────────────────────


def _parse_evidence_json(raw: str | None) -> dict | None:
    """Parse --evidence-json: inline JSON string or path to a JSON file."""
    if raw is None:
        return None
    # # ponytail: try file path first; fall back to inline JSON string
    path = Path(raw)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(raw)


def _event_to_dict(e: ExperimentEvent) -> dict:
    return {
        "id": e.id,
        "experiment_id": e.experiment_id,
        "hypothesis": e.hypothesis,
        "stage_reached": e.stage_reached,
        "terminal_status": e.terminal_status,
        "rationale": e.rationale,
        "recorded_at": e.recorded_at.isoformat(),
        "strategy_id": e.strategy_id,
        "config_hash": e.config_hash,
        "evidence_json": e.evidence_json,
        "lessons": e.lessons,
        "revisitable": e.revisitable,
    }


# ── subcommand implementations ────────────────────────────────────────────────


def _create(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    evidence = _parse_evidence_json(args.evidence_json)
    event = ExperimentEvent(
        experiment_id=args.experiment_id,
        hypothesis=args.hypothesis,
        stage_reached=args.stage_reached,
        terminal_status=args.terminal_status,
        rationale=args.rationale,
        recorded_at=datetime.now(tz=UTC),
        strategy_id=args.strategy_id,
        config_hash=args.config_hash,
        evidence_json=evidence,
        lessons=args.lessons,
        revisitable=args.revisitable,
    )
    row_id = ctx.get_event_store().append_experiment(event)
    data = {"row_id": row_id, "experiment_id": args.experiment_id}
    lines = [
        f"Experiment recorded: {args.experiment_id}",
        f"  stage_reached:   {args.stage_reached}",
        f"  terminal_status: {args.terminal_status}",
        f"  row_id:          {row_id}",
    ]
    return CommandResult(command="experiment.create", data=data, human_lines=lines)


def _list(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    events = ctx.get_event_store().list_experiments(terminal_status=args.terminal_status)
    rows = [_event_to_dict(e) for e in events]
    # compact table header
    lines = [
        f"{'experiment_id':<40}{'stage_reached':<14}{'terminal_status':<16}"
        f"{'strategy_id':<32}revisitable"
    ]
    for e in events:
        sid = e.strategy_id or "-"
        lines.append(
            f"{e.experiment_id:<40}{e.stage_reached:<14}{e.terminal_status:<16}"
            f"{sid:<32}{e.revisitable}"
        )
    if not events:
        lines = ["No experiments found."]
    return CommandResult(
        command="experiment.list",
        data={"count": len(rows), "experiments": rows},
        human_lines=lines,
    )


def _update(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    changes: dict = {}
    # Only forward flags that were explicitly set by the user.
    if args.terminal_status is not None:
        changes["terminal_status"] = args.terminal_status
    if args.stage_reached is not None:
        changes["stage_reached"] = args.stage_reached
    if args.rationale is not None:
        changes["rationale"] = args.rationale
    if args.hypothesis is not None:
        changes["hypothesis"] = args.hypothesis
    if args.lessons is not None:
        changes["lessons"] = args.lessons
    if args.strategy_id is not None:
        changes["strategy_id"] = args.strategy_id
    if args.config_hash is not None:
        changes["config_hash"] = args.config_hash
    if args.evidence_json is not None:
        changes["evidence_json"] = _parse_evidence_json(args.evidence_json)
    # --revisitable is store_true so it's always False by default; only forward
    # if the flag was explicitly provided.
    # # ponytail: argparse doesn't distinguish "user passed --revisitable" from
    # "defaulted to False", so we only forward True (the non-default value).
    if args.revisitable:
        changes["revisitable"] = True

    try:
        row_id = ctx.get_event_store().update_experiment(args.experiment_id, **changes)
    except KeyError as exc:
        return CommandResult(
            command="experiment.update",
            status="error",
            data={"experiment_id": args.experiment_id},
            human_lines=[f"Error: {exc}"],
            errors=[{"code": "not_found", "message": str(exc)}],
        )
    data = {"row_id": row_id, "experiment_id": args.experiment_id, "changes": changes}
    lines = [
        f"Experiment updated: {args.experiment_id}",
        f"  new row_id: {row_id}",
        f"  fields set: {', '.join(changes) or '(none)'}",
    ]
    return CommandResult(command="experiment.update", data=data, human_lines=lines)


def _show(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    event = ctx.get_event_store().get_experiment(args.experiment_id)
    if event is None:
        return CommandResult(
            command="experiment.show",
            status="error",
            data={"experiment_id": args.experiment_id},
            human_lines=[f"Experiment not found: {args.experiment_id}"],
            errors=[{"code": "not_found", "message": f"No experiment: {args.experiment_id}"}],
        )
    d = _event_to_dict(event)
    lines = [f"Experiment: {event.experiment_id}"]
    for k, v in d.items():
        if k != "experiment_id":
            lines.append(f"  {k}: {v}")
    return CommandResult(command="experiment.show", data=d, human_lines=lines)
