"""promotion freeze / promotion manifest subcommands (Phase 1.4 slice 1)."""

from __future__ import annotations

import argparse

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.promotion import (
    freeze_manifest,
    resolve_strategy_config_path,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    promotion_parser = subparsers.add_parser(
        "promotion",
        help="Freeze / inspect strategy manifests (promotion governance).",
    )
    add_global_flags(promotion_parser)
    promotion_subparsers = promotion_parser.add_subparsers(dest="promotion_command", required=True)

    freeze_parser = promotion_subparsers.add_parser(
        "freeze",
        help="Freeze the strategy's current YAML at its current stage.",
    )
    add_global_flags(freeze_parser)
    freeze_parser.add_argument("strategy_id", help="Strategy identifier from YAML config.")
    freeze_parser.add_argument(
        "--frozen-by",
        default="operator",
        help="Name or identifier recorded as the freezer.",
    )

    manifest_parser = promotion_subparsers.add_parser(
        "manifest",
        help="Show the active frozen manifest for a strategy.",
    )
    add_global_flags(manifest_parser)
    manifest_parser.add_argument("strategy_id", help="Strategy identifier from YAML config.")


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.promotion_command == "freeze":
        return _freeze(args, ctx)
    if args.promotion_command == "manifest":
        return _manifest_show(args, ctx)
    raise ValueError(f"Unsupported promotion command: {args.promotion_command}")


def _freeze(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    config_path = resolve_strategy_config_path(args.strategy_id, ctx.config_dir)
    event_store = ctx.get_event_store()
    event = freeze_manifest(config_path, event_store=event_store, frozen_by=args.frozen_by)
    data = {
        "strategy_id": event.strategy_id,
        "stage": event.stage,
        "config_hash": event.config_hash,
        "config_path": event.config_path,
        "frozen_at": event.frozen_at.isoformat(),
        "frozen_by": event.frozen_by,
    }
    lines = [
        f"Frozen manifest {event.config_hash[:12]} at stage "
        f"'{event.stage}' for {event.strategy_id}",
        f"  frozen_at: {event.frozen_at.isoformat()}",
        f"  frozen_by: {event.frozen_by}",
        f"  source:    {event.config_path}",
    ]
    return CommandResult(command="promotion.freeze", data=data, human_lines=lines)


def _manifest_show(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    config_path = resolve_strategy_config_path(args.strategy_id, ctx.config_dir)
    from milodex.strategies.loader import load_strategy_config

    config = load_strategy_config(config_path)
    event_store = ctx.get_event_store()
    event = event_store.get_active_manifest_for_strategy(args.strategy_id, config.stage)
    if event is None:
        data = {
            "strategy_id": args.strategy_id,
            "stage": config.stage,
            "active_manifest": None,
        }
        lines = [
            f"No active manifest for {args.strategy_id} at stage '{config.stage}'.",
        ]
        return CommandResult(command="promotion.manifest", data=data, human_lines=lines)
    data = {
        "strategy_id": event.strategy_id,
        "stage": event.stage,
        "active_manifest": {
            "config_hash": event.config_hash,
            "config_path": event.config_path,
            "frozen_at": event.frozen_at.isoformat(),
            "frozen_by": event.frozen_by,
        },
    }
    lines = [
        f"Active manifest for {event.strategy_id} (stage '{event.stage}')",
        f"  config_hash: {event.config_hash}",
        f"  frozen_at:   {event.frozen_at.isoformat()}",
        f"  frozen_by:   {event.frozen_by}",
        f"  source:      {event.config_path}",
    ]
    return CommandResult(command="promotion.manifest", data=data, human_lines=lines)
