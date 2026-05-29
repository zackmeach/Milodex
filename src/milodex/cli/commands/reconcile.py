"""``milodex reconcile`` command facade for R-OPS-004."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.cli.rich_views import build_reconcile_view
from milodex.core.advisory_lock import AdvisoryLock
from milodex.operations.reconciliation import (
    ResolvePositionError,
    SyncOrdersError,
    build_warnings,
    human_lines,
    resolve_position,
    run_reconciliation,
    sync_local_only_orders,
)


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "reconcile",
        help="Compare broker state against the event store and flag drift.",
        description=(
            "Compare broker state against the local event store, persist the "
            "durable reconciliation verdict, and flag drift. Three R-OPS-004 "
            "v1.2 dimensions remain warnings only: filled-since-last-sync, "
            "canceled-since-last-sync, and strategy-linkage."
        ),
    )
    add_global_flags(parser)
    parser.add_argument(
        "reconcile_action",
        nargs="?",
        choices=("resolve-position", "sync-orders"),
        help="Optional audited correction action.",
    )
    parser.add_argument("symbol", nargs="?", help="Symbol for resolve-position.")
    parser.add_argument(
        "--broker-order-id",
        dest="reconcile_broker_order_id",
        help="Optional single order id for sync-orders (default: all local-only orders).",
    )
    parser.add_argument(
        "--reason",
        dest="reconcile_reason",
        help="Required reason for resolve-position audited correction.",
    )
    parser.add_argument(
        "--approved-by",
        default="operator",
        dest="reconcile_approved_by",
        help="Operator identity for resolve-position audit records.",
    )
    parser.add_argument(
        "--as-of",
        dest="reconcile_as_of",
        help=(
            "Optional UTC ISO-8601 timestamp to scope the local event fold. "
            "Broker state is always live."
        ),
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    as_of = _parse_as_of(getattr(args, "reconcile_as_of", None))
    event_store = ctx.get_event_store()
    broker = ctx.broker_factory()

    with AdvisoryLock(
        "milodex.runtime",
        locks_dir=ctx.locks_dir,
        holder_name="milodex reconcile",
    ):
        action = getattr(args, "reconcile_action", None)
        if action == "resolve-position":
            return _run_resolve_position(event_store, broker, args, as_of=as_of)
        if action == "sync-orders":
            return _run_sync_orders(event_store, broker, args, as_of=as_of)
        return _run_reconcile(event_store, broker, as_of=as_of)


def _run_reconcile(event_store, broker, *, as_of: datetime) -> CommandResult:
    result = run_reconciliation(event_store=event_store, broker=broker, as_of=as_of)
    data = result.to_dict()
    renderable = build_reconcile_view(
        broker=data["broker"],
        positions_ok=data["positions"]["ok"],
        positions_mismatched=data["positions"]["mismatches"],
        orders_ok=data["orders"]["ok"],
        orders_mismatched=data["orders"]["mismatches"],
        deferred_checks=data["deferred_checks"],
        reconciliation_clean=data["reconciliation_clean"],
        incident_recorded=data["incident_recorded"],
        incident_deduplicated=data["incident_deduplicated"],
        incident_hash=data["incident_hash"],
        as_of=data["as_of"],
    )
    return CommandResult(
        command="reconcile",
        data=data,
        human_lines=human_lines(result),
        renderable=renderable,
        warnings=build_warnings(result),
    )


def _run_resolve_position(
    event_store,
    broker,
    args: argparse.Namespace,
    *,
    as_of: datetime,
) -> CommandResult:
    symbol = getattr(args, "symbol", None)
    if not symbol:
        raise ValueError("resolve-position requires SYMBOL")
    try:
        adjustment = resolve_position(
            event_store=event_store,
            broker=broker,
            symbol=symbol,
            reason=getattr(args, "reconcile_reason", None) or "",
            approved_by=getattr(args, "reconcile_approved_by", "operator"),
            as_of=as_of,
        )
    except ResolvePositionError as exc:
        raise ValueError(str(exc)) from exc

    data: dict[str, Any] = {
        "adjustment_id": adjustment.adjustment_id,
        "db_id": adjustment.id,
        "recorded_at": adjustment.recorded_at.isoformat(),
        "effective_at": adjustment.effective_at.isoformat(),
        "approved_by": adjustment.approved_by,
        "symbol": adjustment.symbol,
        "local_qty_before": adjustment.local_qty_before,
        "broker_qty": adjustment.broker_qty,
        "delta_qty": adjustment.delta_qty,
        "source_incident_hash": adjustment.source_incident_hash,
        "reason": adjustment.reason,
        "context": adjustment.context,
    }
    human = [
        f"Recorded reconciliation adjustment {adjustment.adjustment_id}",
        (
            f"  {adjustment.symbol}: local {adjustment.local_qty_before:g} -> "
            f"broker {adjustment.broker_qty:g} "
            f"(delta {adjustment.delta_qty:g})"
        ),
        f"  bound incident: {adjustment.source_incident_hash[:12]}",
        "Run `milodex reconcile` again to persist the post-correction verdict.",
        (
            "Note: a position correction is necessary but not sufficient for a clean "
            "verdict if recent local-only order drift remains."
        ),
    ]
    return CommandResult(command="reconcile.resolve-position", data=data, human_lines=human)


def _run_sync_orders(
    event_store,
    broker,
    args: argparse.Namespace,
    *,
    as_of: datetime,
) -> CommandResult:
    try:
        result = sync_local_only_orders(
            event_store=event_store,
            broker=broker,
            reason=getattr(args, "reconcile_reason", None) or "",
            approved_by=getattr(args, "reconcile_approved_by", "operator"),
            broker_order_id=getattr(args, "reconcile_broker_order_id", None),
            as_of=as_of,
        )
    except SyncOrdersError as exc:
        raise ValueError(str(exc)) from exc

    filled = sum(1 for s in result.synced if s.recorded_status == "filled")
    cancelled = sum(1 for s in result.synced if s.recorded_status == "cancelled")
    rejected = sum(1 for s in result.synced if s.recorded_status == "rejected")
    data: dict[str, Any] = {
        "explanation_id": result.explanation_id,
        "synced": [asdict(s) for s in result.synced],
        "skipped": [asdict(s) for s in result.skipped],
        "adjustment_warnings": result.adjustment_warnings,
    }
    human = [
        (
            f"Synced {len(result.synced)} order(s): {filled} filled, {cancelled} cancelled, "
            f"{rejected} rejected; {len(result.skipped)} skipped."
        ),
        *result.adjustment_warnings,
        "Run `milodex reconcile` again to persist the clean verdict and clear the readiness veto.",
    ]
    return CommandResult(
        command="reconcile.sync-orders",
        data=data,
        human_lines=human,
        warnings=result.adjustment_warnings,
    )


def _parse_as_of(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(tz=UTC)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"Invalid --as-of value '{raw}'. Use an ISO-8601 timestamp (e.g. 2026-04-22T14:30:00Z)."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
