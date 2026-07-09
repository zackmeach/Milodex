"""``milodex halt`` — operator manual kill-switch trip (ADR 0005 Addendum / D-9).

Top-level emergency affordance (founder-selected variant A2): trip the kill
switch via the shared halt path (best-effort ``cancel_all_orders`` then the
durable state flip) AND issue a controlled stop to every live runner, failing
soft — a wedged or dead runner never blocks the trip, which has already
completed.

``--confirm`` is required. ``--reason`` is optional (default
``"operator manual trip"``): reset's investigate-first friction is deliberate
in the *dangerous* direction; a fail-safe halt must not inherit it.
"""

from __future__ import annotations

import argparse
from typing import Any

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult
from milodex.strategies.paper_runner_control import (
    PaperRunnerControl,
    live_runner_holders,
)

_DEFAULT_REASON = "operator manual trip"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "halt",
        help="Operator emergency halt: trip the kill switch and stop all live runners.",
        description=(
            "Operator-initiated manual kill-switch trip (ADR 0005 Addendum / D-9). "
            "Cancels resting orders best-effort, engages the manual-reset-only kill "
            "switch, then issues a controlled stop to every live runner (fail-soft). "
            "Open positions are stranded from automation while the switch is active — "
            "flatten manually at the broker, or investigate and run "
            "`milodex trade kill-switch reset --confirm`."
        ),
    )
    add_global_flags(parser)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required acknowledgement that the operator intends to halt all trading.",
    )
    parser.add_argument(
        "--reason",
        default=_DEFAULT_REASON,
        help=f"Optional reason recorded on the kill-switch event (default: {_DEFAULT_REASON!r}).",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if not args.confirm:
        raise ValueError(
            "milodex halt requires --confirm. This trips the kill switch (manual "
            "reset only) and stops every live runner."
        )
    reason = (getattr(args, "reason", None) or _DEFAULT_REASON).strip() or _DEFAULT_REASON

    service = ctx.get_execution_service()
    outcome = service.halt_trading(reason)
    state = service.get_kill_switch_state()
    runners = _stop_live_runners(ctx)
    return _build_result(reason, outcome, state, runners)


def _stop_live_runners(ctx: CommandContext) -> list[dict[str, Any]]:
    """Fan a controlled stop out to every live runner, failing soft.

    A per-runner failure (or a wedged/dead runner) never blocks the halt: the
    trip has already completed. Each runner's outcome is reported individually.
    """
    holders = live_runner_holders(ctx.config_dir, ctx.locks_dir)
    if not holders:
        return []
    control = PaperRunnerControl(locks_dir=ctx.locks_dir)
    outcomes: list[dict[str, Any]] = []
    for strategy_id, holder in sorted(holders.items()):
        try:
            result = control.request_controlled_stop(strategy_id, holder=holder)
        except Exception as exc:  # noqa: BLE001 — fail soft: one runner never blocks the fleet halt
            outcomes.append(
                {
                    "strategy_id": strategy_id,
                    "stop_requested": False,
                    "pid": holder.get("pid"),
                    "request_path": None,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        outcomes.append(
            {
                "strategy_id": strategy_id,
                "stop_requested": True,
                "pid": holder.get("pid"),
                "request_path": str(result.request_path),
                "error": None,
            }
        )
    return outcomes


def _build_result(
    reason: str,
    outcome: Any,
    state: Any,
    runners: list[dict[str, Any]],
) -> CommandResult:
    lines = ["Operator Manual Halt", f"Reason: {reason}"]

    # (a) orders cancelled or cancel failure
    if outcome.orders_cancelled:
        lines.append("Orders: resting orders cancelled (cancel_all_orders succeeded).")
    else:
        lines.append(
            f"Orders: cancel_all_orders FAILED ({outcome.cancel_error}) — "
            "kill switch engaged anyway."
        )

    # (b) kill switch active
    lines.append(f"Kill switch: {'active' if state.active else 'INACTIVE (unexpected)'}")

    # (c) per-runner controlled-stop outcomes
    if not runners:
        lines.append("Live runners: none found — nothing to stop.")
    else:
        stopped = sum(1 for r in runners if r["stop_requested"])
        lines.append(
            f"Live runners: {len(runners)} found, controlled-stop requested for {stopped}:"
        )
        for runner in runners:
            pid = runner["pid"]
            if runner["stop_requested"]:
                lines.append(f"  [OK] {runner['strategy_id']} (pid {pid}): stop requested")
            else:
                lines.append(f"  [FAIL] {runner['strategy_id']} (pid {pid}): {runner['error']}")

    lines.append(
        "Positions are stranded from automation while the switch is active (exits are "
        "vetoed and any resting protective orders were cancelled). Flatten manually at "
        "the broker, or investigate and run `milodex trade kill-switch reset --confirm`."
    )

    data: dict[str, Any] = {
        "reason": reason,
        "orders_cancelled": outcome.orders_cancelled,
        "cancel_error": outcome.cancel_error,
        "kill_switch_active": state.active,
        "kill_switch_reason": state.reason,
        "last_triggered_at": state.last_triggered_at,
        "runners": runners,
    }
    return CommandResult(command="halt", data=data, human_lines=lines)
