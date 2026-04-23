"""trade preview, submit, order-status, cancel, kill-switch commands."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from milodex.broker.models import Order
from milodex.cli._shared import (
    ORDER_TYPE_CHOICES,
    SIDE_CHOICES,
    TIF_CHOICES,
    CommandContext,
    add_global_flags,
    add_trade_arguments,
    format_money,
    order_to_dict,
)
from milodex.cli.formatter import CommandResult
from milodex.config import get_locks_dir
from milodex.core.advisory_lock import AdvisoryLock
from milodex.execution import TradeIntent
from milodex.execution.models import ExecutionResult


def register(subparsers: argparse._SubParsersAction) -> None:
    trade_parser = subparsers.add_parser("trade", help="Preview and submit paper trades.")
    add_global_flags(trade_parser)
    trade_subparsers = trade_parser.add_subparsers(dest="trade_command", required=True)

    preview_parser = trade_subparsers.add_parser(
        "preview",
        help="Preview a trade through risk checks.",
    )
    add_global_flags(preview_parser)
    add_trade_arguments(preview_parser, require_paper_flag=False)

    submit_parser = trade_subparsers.add_parser(
        "submit",
        help="Submit a paper trade after risk checks.",
    )
    add_global_flags(submit_parser)
    add_trade_arguments(submit_parser, require_paper_flag=True)

    order_status_parser = trade_subparsers.add_parser(
        "order-status",
        help="Fetch current broker status for an order.",
    )
    add_global_flags(order_status_parser)
    order_status_parser.add_argument("order_id", help="Broker order ID.")

    cancel_parser = trade_subparsers.add_parser("cancel", help="Cancel an existing order.")
    add_global_flags(cancel_parser)
    cancel_parser.add_argument("order_id", help="Broker order ID.")

    kill_switch_parser = trade_subparsers.add_parser(
        "kill-switch",
        help="Inspect kill switch state.",
    )
    add_global_flags(kill_switch_parser)
    kill_switch_subparsers = kill_switch_parser.add_subparsers(
        dest="kill_switch_command",
        required=True,
    )
    kill_switch_status_parser = kill_switch_subparsers.add_parser(
        "status", help="Show current kill switch state."
    )
    add_global_flags(kill_switch_status_parser)

    kill_switch_reset_parser = kill_switch_subparsers.add_parser(
        "reset",
        help="Manually clear an active kill switch. Requires --confirm.",
    )
    add_global_flags(kill_switch_reset_parser)
    kill_switch_reset_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required acknowledgement that the operator has investigated "
        "the cause of the original kill-switch activation.",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    service = ctx.get_execution_service()
    trading_mode = ctx.get_trading_mode()
    if args.trade_command == "preview":
        return _build_execution_result(
            service.preview(_build_trade_intent(args)),
            command="trade.preview",
            trading_mode=trading_mode,
        )
    if args.trade_command == "submit":
        if not args.paper:
            raise ValueError("trade submit requires --paper.")
        with AdvisoryLock(
            "milodex.runtime",
            locks_dir=ctx.locks_dir or get_locks_dir(),
            holder_name="milodex trade submit",
        ):
            return _build_execution_result(
                service.submit_paper(_build_trade_intent(args)),
                command="trade.submit",
                trading_mode=trading_mode,
            )
    if args.trade_command == "order-status":
        return _build_order_status_result(service.get_order_status(args.order_id))
    if args.trade_command == "cancel":
        cancelled, order = service.cancel_order(args.order_id)
        return _build_cancel_result(cancelled, order, args.order_id)
    if args.trade_command == "kill-switch" and args.kill_switch_command == "status":
        state = service.get_kill_switch_state()
        return _build_kill_switch_result(state.active, state.reason, state.last_triggered_at)
    if args.trade_command == "kill-switch" and args.kill_switch_command == "reset":
        if not args.confirm:
            raise ValueError(
                "trade kill-switch reset requires --confirm. Investigate the "
                "cause of the original activation before re-enabling trading."
            )
        previous = service.get_kill_switch_state()
        service.reset_kill_switch()
        new_state = service.get_kill_switch_state()
        return _build_kill_switch_reset_result(previous, new_state)
    raise ValueError(f"Unsupported trade command: {args.trade_command}")


def _build_trade_intent(args: argparse.Namespace) -> TradeIntent:
    return TradeIntent(
        symbol=args.symbol,
        side=SIDE_CHOICES[args.side],
        quantity=args.quantity,
        order_type=ORDER_TYPE_CHOICES[args.order_type],
        time_in_force=TIF_CHOICES[args.time_in_force],
        limit_price=args.limit_price,
        stop_price=args.stop_price,
        strategy_config_path=Path(args.strategy_config) if args.strategy_config else None,
    )


def _execution_result_to_dict(result: ExecutionResult, trading_mode: str) -> dict[str, Any]:
    request = result.execution_request
    decision = result.risk_decision
    payload: dict[str, Any] = {
        "status": result.status.value,
        "trading_mode": trading_mode,
        "market_open": result.market_open,
        "request": {
            "symbol": request.symbol,
            "side": request.side.value,
            "order_type": request.order_type.value,
            "quantity": request.quantity,
            "time_in_force": request.time_in_force.value,
            "estimated_unit_price": request.estimated_unit_price,
            "estimated_order_value": request.estimated_order_value,
            "strategy_name": request.strategy_name,
            "strategy_stage": request.strategy_stage,
        },
        "risk_decision": {
            "allowed": decision.allowed,
            "summary": decision.summary,
            "checks": [
                {"name": check.name, "passed": check.passed, "message": check.message}
                for check in decision.checks
            ],
        },
        "order": order_to_dict(result.order) if result.order is not None else None,
        "latest_bar": (
            {
                "timestamp": result.latest_bar.timestamp.isoformat(),
                "close": result.latest_bar.close,
            }
            if result.latest_bar is not None
            else None
        ),
        "message": result.message,
    }
    return payload


def _build_execution_result(
    result: ExecutionResult, *, command: str, trading_mode: str
) -> CommandResult:
    request = result.execution_request
    lines = [
        "Trade Execution",
        f"Status: {result.status.value}",
        f"Symbol: {request.symbol}",
        f"Side: {request.side.value}",
        f"Order type: {request.order_type.value}",
        f"Quantity: {request.quantity:.2f}",
        f"Time in force: {request.time_in_force.value}",
        f"Estimated unit price: {format_money(request.estimated_unit_price)}",
        f"Estimated order value: {format_money(request.estimated_order_value)}",
        f"Trading mode: {trading_mode}",
        f"Market open: {'yes' if result.market_open else 'no'}",
    ]
    if request.strategy_name:
        lines.append(f"Strategy: {request.strategy_name} ({request.strategy_stage})")
    if result.latest_bar is not None:
        lines.append(
            "Latest bar: "
            f"{result.latest_bar.timestamp.isoformat()} "
            f"close={format_money(result.latest_bar.close)}"
        )
    lines.append("Risk checks:")
    for check in result.risk_decision.checks:
        prefix = "PASS" if check.passed else "FAIL"
        lines.append(f"  [{prefix}] {check.name}: {check.message}")
    lines.append(f"Decision: {'allow' if result.risk_decision.allowed else 'block'}")
    if result.order is not None:
        lines.append(f"Broker order ID: {result.order.id}")
        lines.append(f"Broker status: {result.order.status.value}")
    if result.message:
        lines.append(f"Message: {result.message}")
    return CommandResult(
        command=command,
        data=_execution_result_to_dict(result, trading_mode),
        human_lines=lines,
    )


def _build_order_status_result(order: Order) -> CommandResult:
    lines = [
        "Order Status",
        f"ID: {order.id}",
        f"Symbol: {order.symbol}",
        f"Side: {order.side.value}",
        f"Type: {order.order_type.value}",
        f"Status: {order.status.value}",
        f"Quantity: {order.quantity:.2f}",
        f"Submitted: {order.submitted_at.isoformat()}",
    ]
    if order.limit_price is not None:
        lines.append(f"Limit price: {format_money(order.limit_price)}")
    if order.stop_price is not None:
        lines.append(f"Stop price: {format_money(order.stop_price)}")
    if order.filled_quantity is not None:
        lines.append(f"Filled quantity: {order.filled_quantity:.2f}")
    if order.filled_avg_price is not None:
        lines.append(f"Filled average price: {format_money(order.filled_avg_price)}")
    if order.filled_at is not None:
        lines.append(f"Filled at: {order.filled_at.isoformat()}")
    return CommandResult(
        command="trade.order-status",
        data={"order": order_to_dict(order)},
        human_lines=lines,
    )


def _build_cancel_result(cancelled: bool, order: Order | None, order_id: str) -> CommandResult:
    if cancelled:
        lines = [f"Order cancel requested successfully: {order_id}"]
        if order is not None:
            lines.append(f"Latest status: {order.status.value}")
    else:
        lines = [f"Order could not be cancelled: {order_id}"]
    data = {
        "order_id": order_id,
        "cancelled": cancelled,
        "order": order_to_dict(order) if order is not None else None,
    }
    return CommandResult(
        command="trade.cancel",
        status="success" if cancelled else "error",
        data=data,
        human_lines=lines,
        errors=[]
        if cancelled
        else [{"code": "cancel_failed", "message": f"Order could not be cancelled: {order_id}"}],
    )


def _build_kill_switch_reset_result(previous, new_state) -> CommandResult:
    lines = [
        "Kill Switch Reset",
        f"Previously active: {'yes' if previous.active else 'no'}",
    ]
    if previous.reason:
        lines.append(f"Previous reason: {previous.reason}")
    if previous.last_triggered_at:
        lines.append(f"Previously triggered: {previous.last_triggered_at}")
    lines.append(f"Now active: {'yes' if new_state.active else 'no'}")
    return CommandResult(
        command="trade.kill-switch.reset",
        data={
            "previous": {
                "active": previous.active,
                "reason": previous.reason,
                "last_triggered_at": previous.last_triggered_at,
            },
            "current": {
                "active": new_state.active,
                "reason": new_state.reason,
                "last_triggered_at": new_state.last_triggered_at,
            },
        },
        human_lines=lines,
    )


def _build_kill_switch_result(
    active: bool, reason: str | None, triggered_at: str | None
) -> CommandResult:
    lines = [
        "Kill Switch",
        f"Active: {'yes' if active else 'no'}",
    ]
    if reason:
        lines.append(f"Reason: {reason}")
    if triggered_at:
        lines.append(f"Last triggered: {triggered_at}")
    return CommandResult(
        command="trade.kill-switch.status",
        data={
            "active": active,
            "reason": reason,
            "last_triggered_at": triggered_at,
        },
        human_lines=lines,
    )
