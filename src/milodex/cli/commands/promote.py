"""promote command."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.commands.analytics import metrics_for_run
from milodex.cli.formatter import CommandResult


def register(subparsers: argparse._SubParsersAction) -> None:
    promote_parser = subparsers.add_parser(
        "promote",
        help="Advance a strategy to the next stage after passing gate checks.",
    )
    add_global_flags(promote_parser)
    promote_parser.add_argument(
        "strategy_id",
        help="Strategy identifier from the YAML config.",
    )
    promote_parser.add_argument(
        "--to",
        required=True,
        dest="to_stage",
        choices=("paper", "micro_live", "live"),
        help="Target stage to promote to.",
    )
    promote_parser.add_argument(
        "--run-id",
        default=None,
        help="Backtest run ID (UUID) to use as promotion evidence.",
    )
    promote_parser.add_argument(
        "--lifecycle-exempt",
        action="store_true",
        help="Bypass statistical thresholds (for lifecycle-proof / regime strategies).",
    )
    promote_parser.add_argument(
        "--approved-by",
        default="operator",
        help="Name or identifier of the operator approving the promotion.",
    )
    promote_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required safety flag when promoting to 'live'.",
    )
    promote_parser.add_argument(
        "--notes",
        default=None,
        help="Optional free-form notes recorded with the promotion event.",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    from milodex.core.event_store import PromotionEvent
    from milodex.promotion import check_gate, validate_stage_transition
    from milodex.strategies.loader import load_strategy_config

    config_path = resolve_strategy_config(args.strategy_id, ctx.config_dir)
    config = load_strategy_config(config_path)
    from_stage = config.stage
    to_stage = args.to_stage

    validate_stage_transition(from_stage, to_stage)

    if to_stage == "live" and not args.confirm:
        raise ValueError(
            "Promoting to 'live' requires --confirm. "
            "This action will be recorded and is irreversible."
        )

    sharpe_ratio = None
    max_drawdown_pct = None
    trade_count = None
    if args.run_id is not None:
        event_store = ctx.get_event_store()
        run_ = event_store.get_backtest_run(args.run_id)
        if run_ is None:
            raise ValueError(f"Backtest run not found: {args.run_id}")
        metrics = metrics_for_run(run_, event_store)
        sharpe_ratio = metrics.sharpe_ratio
        max_drawdown_pct = metrics.max_drawdown_pct
        trade_count = metrics.trade_count

    gate_result = check_gate(
        lifecycle_exempt=args.lifecycle_exempt,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
    )

    if not gate_result.allowed:
        return _build_promotion_result(
            strategy_id=args.strategy_id,
            from_stage=from_stage,
            to_stage=to_stage,
            gate_result=gate_result,
            promoted=False,
        )

    event_store = ctx.get_event_store()
    event_store.append_promotion(
        PromotionEvent(
            strategy_id=args.strategy_id,
            from_stage=from_stage,
            to_stage=to_stage,
            promotion_type=gate_result.promotion_type,
            approved_by=args.approved_by,
            recorded_at=datetime.now(UTC),
            backtest_run_id=args.run_id,
            sharpe_ratio=gate_result.sharpe_ratio,
            max_drawdown_pct=gate_result.max_drawdown_pct,
            trade_count=gate_result.trade_count,
            notes=args.notes,
        )
    )
    _update_stage_in_config(config_path, from_stage, to_stage)
    return _build_promotion_result(
        strategy_id=args.strategy_id,
        from_stage=from_stage,
        to_stage=to_stage,
        gate_result=gate_result,
        promoted=True,
    )


def resolve_strategy_config(strategy_id: str, config_dir: Path = Path("configs")) -> Path:
    """Locate the YAML file whose strategy.id matches ``strategy_id``."""
    from milodex.strategies.loader import load_strategy_config

    for path in sorted(config_dir.glob("*.yaml")):
        try:
            config = load_strategy_config(path)
        except ValueError:
            continue
        if config.strategy_id == strategy_id:
            return path
    msg = f"Strategy config not found for strategy id: {strategy_id}"
    raise ValueError(msg)


def _update_stage_in_config(path: Path, from_stage: str, to_stage: str) -> None:
    """Replace the ``stage:`` value in a strategy YAML config in-place.

    Uses simple text replacement so all comments and formatting are preserved.
    Raises ``ValueError`` if the expected string is not found.
    """
    content = path.read_text(encoding="utf-8")
    old = f'stage: "{from_stage}"'
    new = f'stage: "{to_stage}"'
    if old not in content:
        msg = (
            f"Could not find 'stage: \"{from_stage}\"' in {path}. "
            "Was the config modified externally?"
        )
        raise ValueError(msg)
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def _build_promotion_result(
    *,
    strategy_id: str,
    from_stage: str,
    to_stage: str,
    gate_result: Any,
    promoted: bool,
) -> CommandResult:
    data: dict[str, Any] = {
        "strategy_id": strategy_id,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "promoted": promoted,
        "promotion_type": gate_result.promotion_type,
        "gate_failures": list(gate_result.failures),
        "metrics": {
            "sharpe_ratio": gate_result.sharpe_ratio,
            "max_drawdown_pct": gate_result.max_drawdown_pct,
            "trade_count": gate_result.trade_count,
        },
    }
    if promoted:
        lines: list[str] = [
            "Strategy Promotion",
            f"Strategy:       {strategy_id}",
            f"Stage:          {from_stage} -> {to_stage}",
            f"Type:           {gate_result.promotion_type}",
            "Gate checks:    all passed",
            "Result:         promotion recorded, config updated.",
        ]
        return CommandResult(command="promote", data=data, human_lines=lines)

    lines = [
        "Strategy Promotion — BLOCKED",
        f"Strategy:       {strategy_id}",
        f"Stage:          {from_stage} -> {to_stage}",
        "Gate check failures:",
    ]
    for failure in gate_result.failures:
        lines.append(f"  - {failure}")
    return CommandResult(
        command="promote",
        status="error",
        data=data,
        human_lines=lines,
        errors=[{"code": "gate_check_failed", "message": f} for f in gate_result.failures],
    )
