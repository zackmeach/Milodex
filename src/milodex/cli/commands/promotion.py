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
from milodex.promotion.state_machine import demote, transition
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

    demote_parser = promotion_subparsers.add_parser(
        "demote",
        help="Demote a strategy to backtest or disabled (always allowed).",
    )
    add_global_flags(demote_parser)
    demote_parser.add_argument("strategy_id", help="Strategy identifier from YAML config.")
    demote_parser.add_argument(
        "--to",
        required=True,
        dest="to_stage",
        choices=("backtest", "disabled"),
        help="Demotion target.",
    )
    demote_parser.add_argument(
        "--reason",
        required=True,
        help="Required: why the strategy is being demoted (non-blank).",
    )
    demote_parser.add_argument(
        "--evidence-ref",
        default=None,
        help="Optional ticket / incident ID supporting the demotion.",
    )
    demote_parser.add_argument(
        "--approved-by",
        default="operator",
        help="Operator approving the demotion.",
    )

    history_parser = promotion_subparsers.add_parser(
        "history",
        help="Show promotion + demotion history for a strategy (newest first).",
    )
    add_global_flags(history_parser)
    history_parser.add_argument("strategy_id", help="Strategy identifier from YAML config.")
    history_parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of rows to return (default: all).",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.promotion_command == "freeze":
        return _freeze(args, ctx)
    if args.promotion_command == "manifest":
        return _manifest_show(args, ctx)
    if args.promotion_command == "promote":
        return _promote(args, ctx)
    if args.promotion_command == "demote":
        return _demote(args, ctx)
    if args.promotion_command == "history":
        return _history(args, ctx)
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


def _demote(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    config_path = resolve_strategy_config_path(args.strategy_id, ctx.config_dir)
    event_store = ctx.get_event_store()

    promotion = demote(
        config_path=config_path,
        to_stage=args.to_stage,
        reason=args.reason,
        approved_by=args.approved_by,
        event_store=event_store,
        evidence_ref=args.evidence_ref,
    )

    data = {
        "strategy_id": args.strategy_id,
        "from_stage": promotion.from_stage,
        "to_stage": promotion.to_stage,
        "promotion_type": "demotion",
        "promotion_id": promotion.id,
        "reverses_event_id": promotion.reverses_event_id,
        "reason": args.reason,
        "evidence_ref": args.evidence_ref,
    }
    lines = [
        "Strategy Demotion",
        f"Strategy:        {args.strategy_id}",
        f"Stage:           {promotion.from_stage} -> {promotion.to_stage}",
        f"Reverses event:  {promotion.reverses_event_id or '(none)'}",
        f"Reason:          {args.reason}",
    ]
    if promotion.to_stage == "backtest":
        lines.append("Config YAML:     stage line updated to 'backtest'.")
    else:
        lines.append("Config YAML:     unchanged (ledger-only demotion).")
    return CommandResult(command="promotion.demote", data=data, human_lines=lines)


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


def _history(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    event_store = ctx.get_event_store()
    events = event_store.list_promotions_for_strategy(args.strategy_id, limit=args.limit)

    data = {
        "strategy_id": args.strategy_id,
        "count": len(events),
        "events": [
            {
                "id": e.id,
                "recorded_at": e.recorded_at.isoformat(),
                "from_stage": e.from_stage,
                "to_stage": e.to_stage,
                "promotion_type": e.promotion_type,
                "approved_by": e.approved_by,
                "manifest_id": e.manifest_id,
                "reverses_event_id": e.reverses_event_id,
                "notes": e.notes,
            }
            for e in events
        ],
    }

    if not events:
        lines = [f"No promotion history for {args.strategy_id}."]
        return CommandResult(command="promotion.history", data=data, human_lines=lines)

    lines = [
        f"Promotion History — {args.strategy_id}",
        f"{'id':<12}{'recorded_at':<22}{'from':<12}{'to':<12}{'type':<18}manifest",
    ]
    for e in events:
        id_cell = f"{e.id}"
        if e.reverses_event_id is not None:
            id_cell = f"{e.id} (\u21a9{e.reverses_event_id})"
        recorded = e.recorded_at.isoformat(timespec="seconds")
        manifest_cell = "-" if e.manifest_id is None else f"mid={e.manifest_id}"
        lines.append(
            f"{id_cell:<12}{recorded:<22}{e.from_stage:<12}{e.to_stage:<12}"
            f"{e.promotion_type:<18}{manifest_cell}"
        )
    return CommandResult(command="promotion.history", data=data, human_lines=lines)


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
