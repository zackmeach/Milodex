"""promotion freeze / manifest / promote subcommands (Phase 1.4 slices 1 + 2)."""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.promotion import (
    assemble_evidence_package,
    check_gate,
    freeze_manifest,
    resolve_strategy_config_path,
    validate_stage_transition,
)
from milodex.promotion.state_machine import transition
from milodex.strategies.loader import canonicalize_config_data, load_strategy_config


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

    promote_parser = promotion_subparsers.add_parser(
        "promote",
        help="Advance a strategy to the next stage with an evidence package.",
    )
    add_global_flags(promote_parser)
    promote_parser.add_argument("strategy_id", help="Strategy identifier from YAML config.")
    promote_parser.add_argument(
        "--to",
        required=True,
        dest="to_stage",
        choices=("paper", "micro_live", "live"),
        help="Target stage.",
    )
    promote_parser.add_argument(
        "--recommendation",
        default=None,
        help="Required: operator's written recommendation (non-blank).",
    )
    promote_parser.add_argument(
        "--risk",
        action="append",
        dest="known_risks",
        default=[],
        help="Known risk (repeatable). At least one required.",
    )
    promote_parser.add_argument(
        "--run-id",
        default=None,
        help="Backtest run ID used as statistical evidence.",
    )
    promote_parser.add_argument(
        "--approved-by",
        default="operator",
        help="Operator approving the promotion.",
    )
    promote_parser.add_argument(
        "--lifecycle-exempt",
        action="store_true",
        help="Bypass statistical thresholds (regime / lifecycle-proof strategies).",
    )
    promote_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required safety flag when --to live.",
    )
    promote_parser.add_argument(
        "--notes",
        default=None,
        help="Optional free-form notes recorded with the promotion.",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.promotion_command == "freeze":
        return _freeze(args, ctx)
    if args.promotion_command == "manifest":
        return _manifest_show(args, ctx)
    if args.promotion_command == "promote":
        return _promote(args, ctx)
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


def _promote(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    _require_evidence_inputs(args)

    config_path = resolve_strategy_config_path(args.strategy_id, ctx.config_dir)
    config = load_strategy_config(config_path)
    from_stage = config.stage
    to_stage = args.to_stage

    validate_stage_transition(from_stage, to_stage)

    if to_stage == "live" and not args.confirm:
        raise ValueError(
            "Promoting to 'live' requires --confirm. "
            "This action will be recorded and is irreversible."
        )

    event_store = ctx.get_event_store()

    sharpe_ratio, max_drawdown_pct, trade_count = _metrics_from_run(args.run_id, event_store)

    gate_result = check_gate(
        lifecycle_exempt=args.lifecycle_exempt,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
    )
    if not gate_result.allowed:
        return _promote_blocked_result(args.strategy_id, from_stage, to_stage, gate_result)

    manifest_hash = _compute_post_update_hash(config.raw_data, to_stage)
    evidence = assemble_evidence_package(
        strategy_id=args.strategy_id,
        from_stage=from_stage,
        to_stage=to_stage,
        manifest_hash=manifest_hash,
        backtest_run_id=args.run_id,
        recommendation=args.recommendation,
        known_risks=args.known_risks,
        promotion_type=gate_result.promotion_type,
        gate_check_outcome={
            "failures": list(gate_result.failures),
            "promotion_type": gate_result.promotion_type,
        },
        metrics_snapshot={
            "sharpe_ratio": gate_result.sharpe_ratio,
            "max_drawdown_pct": gate_result.max_drawdown_pct,
            "trade_count": gate_result.trade_count,
        },
        event_store=event_store,
    )

    promotion = transition(
        config_path=config_path,
        to_stage=to_stage,
        gate_result=gate_result,
        evidence=evidence,
        approved_by=args.approved_by,
        event_store=event_store,
        backtest_run_id=args.run_id,
        notes=args.notes,
    )

    data = {
        "strategy_id": args.strategy_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "promoted": True,
        "promotion_type": gate_result.promotion_type,
        "promotion_id": promotion.id,
        "manifest_id": promotion.manifest_id,
        "evidence": evidence.as_dict(),
    }
    lines = [
        "Strategy Promotion",
        f"Strategy:        {args.strategy_id}",
        f"Stage:           {from_stage} -> {to_stage}",
        f"Type:            {gate_result.promotion_type}",
        f"Manifest hash:   {manifest_hash[:12]}...",
        f"Evidence items:  recommendation + {len(args.known_risks)} risk(s)",
        "Result:          promotion recorded, manifest frozen, YAML updated.",
    ]
    return CommandResult(command="promotion.promote", data=data, human_lines=lines)


def _require_evidence_inputs(args: argparse.Namespace) -> None:
    missing: list[str] = []
    if not args.recommendation or not args.recommendation.strip():
        missing.append("--recommendation")
    if not args.known_risks or not any(r and r.strip() for r in args.known_risks):
        missing.append("--risk")
    if missing:
        msg = (
            "Missing required evidence fields: "
            + ", ".join(missing)
            + ". Per R-PRM-008 the CLI refuses promotion without these."
        )
        raise ValueError(msg)


def _metrics_from_run(
    run_id: str | None, event_store: Any
) -> tuple[float | None, float | None, int | None]:
    if run_id is None:
        return None, None, None
    from milodex.cli.commands.analytics import metrics_for_run

    run_ = event_store.get_backtest_run(run_id)
    if run_ is None:
        raise ValueError(f"Backtest run not found: {run_id}")
    metrics = metrics_for_run(run_, event_store)
    return metrics.sharpe_ratio, metrics.max_drawdown_pct, metrics.trade_count


def _compute_post_update_hash(raw_data: dict, to_stage: str) -> str:
    strategy = dict(raw_data["strategy"])
    strategy["stage"] = to_stage
    canonical = canonicalize_config_data({**raw_data, "strategy": strategy})
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _promote_blocked_result(
    strategy_id: str, from_stage: str, to_stage: str, gate_result
) -> CommandResult:
    data = {
        "strategy_id": strategy_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "promoted": False,
        "promotion_type": gate_result.promotion_type,
        "gate_failures": list(gate_result.failures),
    }
    lines = [
        "Strategy Promotion — BLOCKED",
        f"Strategy:   {strategy_id}",
        f"Stage:      {from_stage} -> {to_stage}",
        "Gate check failures:",
    ]
    for failure in gate_result.failures:
        lines.append(f"  - {failure}")
    return CommandResult(
        command="promotion.promote",
        status="error",
        data=data,
        human_lines=lines,
        errors=[{"code": "gate_check_failed", "message": f} for f in gate_result.failures],
    )


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
