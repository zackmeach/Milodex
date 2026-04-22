"""Read-only ``milodex report`` command.

Phase 1.4 opener — three subcommands, all read-only, all passing through
``CommandResult`` so the R-CLI-009 JSON contract applies automatically:

* ``milodex report``              — primary trust report (R-CLI-012).
* ``milodex report daily``        — today's operational summary (R-ANA-008).
* ``milodex report strategy <id>``— per-strategy minimum analytics set (R-ANA-006).

The command never calls ``ExecutionService.submit_*`` or ``RiskEvaluator``.
Kill-switch state is read from the event store; nothing writes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from milodex.analytics.metrics import compute_metrics
from milodex.broker import BrokerError
from milodex.cli._shared import (
    CommandContext,
    add_global_flags,
    error_result,
    format_money,
    parse_iso_date,
    performance_metrics_to_dict,
    position_to_dict,
)
from milodex.cli.commands.analytics import equity_curve_from_trades
from milodex.cli.formatter import CommandResult
from milodex.core.event_store import (
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    KillSwitchEvent,
    TradeEvent,
)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> None:
    report_parser = subparsers.add_parser(
        "report",
        help="Show the primary trust report or a scoped summary (read-only).",
    )
    add_global_flags(report_parser)
    report_subparsers = report_parser.add_subparsers(dest="report_command", required=False)

    daily_parser = report_subparsers.add_parser(
        "daily",
        help="Operational summary of today's activity (R-ANA-008).",
    )
    add_global_flags(daily_parser)
    daily_parser.add_argument(
        "--date",
        dest="report_date",
        help="Override the reporting date (YYYY-MM-DD, UTC). Defaults to today.",
    )

    strategy_parser = report_subparsers.add_parser(
        "strategy",
        help="Per-strategy minimum analytics set from the latest backtest (R-ANA-006).",
    )
    add_global_flags(strategy_parser)
    strategy_parser.add_argument("strategy_id", help="Strategy ID to report on.")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    subcommand = getattr(args, "report_command", None)
    event_store = ctx.get_event_store()

    if subcommand is None:
        return _build_trust_result(event_store, ctx)
    if subcommand == "daily":
        target = parse_iso_date(args.report_date) if args.report_date else _today_utc()
        return _build_daily_result(event_store, ctx, target_date=target)
    if subcommand == "strategy":
        return _build_strategy_result(event_store, args.strategy_id)

    raise ValueError(f"Unsupported report command: {subcommand}")


# ---------------------------------------------------------------------------
# Trust report (default)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _StrategySnapshot:
    strategy_id: str
    stage: str
    config_fingerprint: str | None
    last_action: ExplanationEvent | None


def _build_trust_result(event_store: EventStore, ctx: CommandContext) -> CommandResult:
    kill_switch = _kill_switch_state(event_store)
    broker_info = _broker_info(ctx)
    strategies = _collect_strategy_snapshots(event_store)
    freshness = _data_freshness(event_store)

    warnings = _trust_warnings(kill_switch, broker_info, freshness, strategies)
    operator_action = kill_switch["active"] or any(
        s.stage in {"live", "micro_live"} and not broker_info["connected"] for s in strategies
    )

    data: dict[str, Any] = {
        "strategies": [_strategy_snapshot_to_dict(s) for s in strategies],
        "kill_switch": kill_switch,
        "broker": broker_info,
        "data_freshness": freshness,
        "operator_action_required": operator_action,
        "incidents": [],
    }

    lines = _trust_human_lines(strategies, kill_switch, broker_info, freshness, operator_action)
    return CommandResult(
        command="report",
        data=data,
        human_lines=lines,
        warnings=warnings,
    )


def _collect_strategy_snapshots(event_store: EventStore) -> list[_StrategySnapshot]:
    seen: dict[str, _StrategySnapshot] = {}

    strategy_ids: set[str] = set()
    for run_ in event_store.list_strategy_runs():
        strategy_ids.add(run_.strategy_id)
    for promo in event_store.list_promotions():
        strategy_ids.add(promo.strategy_id)
    for bt in event_store.list_backtest_runs():
        strategy_ids.add(bt.strategy_id)

    explanations_by_strategy: dict[str, ExplanationEvent] = {}
    for exp in event_store.list_explanations():
        if exp.strategy_name:
            strategy_ids.add(exp.strategy_name)
            explanations_by_strategy[exp.strategy_name] = exp  # last one wins (ORDER BY id ASC)

    for strategy_id in sorted(strategy_ids):
        latest_promo = event_store.get_latest_promotion_for_strategy(strategy_id)
        stage = latest_promo.to_stage if latest_promo is not None else "backtest"
        last_action = explanations_by_strategy.get(strategy_id)
        fingerprint = last_action.config_hash if last_action is not None else None
        seen[strategy_id] = _StrategySnapshot(
            strategy_id=strategy_id,
            stage=stage,
            config_fingerprint=fingerprint,
            last_action=last_action,
        )

    return list(seen.values())


def _strategy_snapshot_to_dict(snap: _StrategySnapshot) -> dict[str, Any]:
    last_action = _last_action_to_dict(snap.last_action)
    confidence = _strategy_confidence(snap)
    return {
        "strategy_id": snap.strategy_id,
        "stage": snap.stage,
        "config_fingerprint": snap.config_fingerprint,
        "last_action": last_action,
        "next_expected_action": _next_expected_action(snap),
        "confidence": confidence,
        "warnings": [],
    }


def _last_action_to_dict(exp: ExplanationEvent | None) -> dict[str, Any] | None:
    if exp is None:
        return None
    return {
        "timestamp": exp.recorded_at.isoformat(),
        "decision_type": exp.decision_type,
        "symbol": exp.symbol,
        "side": exp.side,
        "quantity": exp.quantity,
        "risk_allowed": exp.risk_allowed,
        "risk_summary": exp.risk_summary,
        "reason_codes": list(exp.reason_codes),
    }


def _strategy_confidence(snap: _StrategySnapshot) -> dict[str, str]:
    if snap.last_action is None:
        return {
            "label": "insufficient_evidence",
            "reason": "no recorded activity for this strategy",
        }
    if snap.stage == "backtest":
        return {"label": "preliminary", "reason": "strategy has not progressed past backtest"}
    return {"label": "preliminary", "reason": f"stage={snap.stage}, paper evidence still accruing"}


def _next_expected_action(snap: _StrategySnapshot) -> str:
    if snap.stage in {"paper", "micro_live", "live"}:
        return "evaluate at next scheduled run"
    return "run backtest or promote to paper to begin evaluation"


def _kill_switch_state(event_store: EventStore) -> dict[str, Any]:
    event: KillSwitchEvent | None = event_store.get_latest_kill_switch_event()
    if event is None or event.event_type != "activated":
        return {"active": False, "reason": None, "last_triggered_at": None}
    return {
        "active": True,
        "reason": event.reason,
        "last_triggered_at": event.recorded_at.isoformat(),
    }


def _broker_info(ctx: CommandContext) -> dict[str, Any]:
    try:
        broker = ctx.broker_factory()
        market_open = broker.is_market_open()
    except BrokerError as exc:
        return {"connected": False, "market_open": None, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — report never crashes on broker I/O
        return {"connected": False, "market_open": None, "error": str(exc)}
    return {"connected": True, "market_open": bool(market_open), "error": None}


def _data_freshness(event_store: EventStore) -> dict[str, Any]:
    explanations = event_store.list_explanations()
    latest_bar_ts: datetime | None = None
    for exp in reversed(explanations):
        if exp.latest_bar_timestamp is not None:
            latest_bar_ts = exp.latest_bar_timestamp
            break
    if latest_bar_ts is None:
        return {"latest_bar_timestamp": None, "stale": None}
    age_hours = (datetime.now(tz=UTC) - _aware(latest_bar_ts)).total_seconds() / 3600.0
    return {
        "latest_bar_timestamp": latest_bar_ts.isoformat(),
        "stale": age_hours > 24.0,
    }


def _trust_warnings(
    kill_switch: dict[str, Any],
    broker_info: dict[str, Any],
    freshness: dict[str, Any],
    strategies: list[_StrategySnapshot],
) -> list[str]:
    warnings: list[str] = []
    if kill_switch["active"]:
        warnings.append(f"Kill switch active: {kill_switch.get('reason') or 'no reason recorded'}")
    if not broker_info["connected"]:
        warnings.append(f"Broker unreachable: {broker_info.get('error') or 'unknown error'}")
    if freshness.get("stale"):
        warnings.append(f"Market data is stale (latest bar {freshness['latest_bar_timestamp']})")
    if not strategies:
        warnings.append("No strategies have run yet — event store is empty.")
    return warnings


def _trust_human_lines(
    strategies: list[_StrategySnapshot],
    kill_switch: dict[str, Any],
    broker_info: dict[str, Any],
    freshness: dict[str, Any],
    operator_action: bool,
) -> list[str]:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = [f"Milodex Trust Report — {now}", ""]

    if kill_switch["active"]:
        lines.append("!! KILL SWITCH ACTIVE !!")
        lines.append(f"   Reason: {kill_switch.get('reason') or 'no reason recorded'}")
        lines.append(f"   Since:  {kill_switch.get('last_triggered_at')}")
        lines.append("")

    lines.append("Strategies")
    if not strategies:
        lines.append("  (none — no strategies have run yet)")
    else:
        for snap in strategies:
            last = snap.last_action
            last_desc = (
                f"{last.decision_type} {last.side} {last.symbol} "
                f"({'allowed' if last.risk_allowed else 'blocked'})"
                if last is not None
                else "no recorded activity"
            )
            conf = _strategy_confidence(snap)
            lines.append(f"  {snap.strategy_id:<40} stage: {snap.stage}")
            lines.append(f"    last action: {last_desc}")
            lines.append(f"    next:        {_next_expected_action(snap)}")
            lines.append(f"    confidence:  {conf['label']} ({conf['reason']})")
    lines.append("")

    lines.append("System State")
    lines.append(f"  Kill switch: {'active' if kill_switch['active'] else 'inactive'}")
    if broker_info["connected"]:
        market = "open" if broker_info["market_open"] else "closed"
        lines.append(f"  Broker:      connected  market: {market}")
    else:
        lines.append(f"  Broker:      UNREACHABLE ({broker_info.get('error') or 'unknown'})")
    if freshness["latest_bar_timestamp"] is None:
        lines.append("  Data:        no bars recorded yet")
    else:
        status = "stale" if freshness["stale"] else "fresh"
        lines.append(f"  Data:        {status} (latest bar {freshness['latest_bar_timestamp']})")
    lines.append("")

    lines.append(f"Operator action: {'REQUIRED' if operator_action else 'none required'}")
    return lines


# ---------------------------------------------------------------------------
# Daily report
# ---------------------------------------------------------------------------


def _build_daily_result(
    event_store: EventStore,
    ctx: CommandContext,
    *,
    target_date: date,
) -> CommandResult:
    trades_today = [
        t for t in event_store.list_trades() if _event_date(t.recorded_at) == target_date
    ]
    explanations_today = [
        e for e in event_store.list_explanations() if _event_date(e.recorded_at) == target_date
    ]
    kill_switch = _kill_switch_state(event_store)
    portfolio = _portfolio_snapshot(ctx)

    data: dict[str, Any] = {
        "date": target_date.isoformat(),
        "portfolio": portfolio,
        "trades_today": [_trade_event_to_dict(t) for t in trades_today],
        "explanations_today": [_explanation_brief(e) for e in explanations_today],
        "kill_switch": kill_switch,
        "next_expected_action": "evaluate at next scheduled run",
    }

    lines = _daily_human_lines(
        target_date, portfolio, trades_today, explanations_today, kill_switch
    )
    return CommandResult(command="report.daily", data=data, human_lines=lines)


def _portfolio_snapshot(ctx: CommandContext) -> dict[str, Any]:
    try:
        broker = ctx.broker_factory()
        account = broker.get_account()
        positions = broker.get_positions()
    except BrokerError as exc:
        return {"connected": False, "error": str(exc), "positions": []}
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "error": str(exc), "positions": []}
    return {
        "connected": True,
        "equity": account.equity,
        "cash": account.cash,
        "portfolio_value": account.portfolio_value,
        "daily_pnl": account.daily_pnl,
        "positions": [position_to_dict(p) for p in positions],
    }


def _trade_event_to_dict(t: TradeEvent) -> dict[str, Any]:
    return {
        "recorded_at": t.recorded_at.isoformat(),
        "symbol": t.symbol,
        "side": t.side,
        "quantity": t.quantity,
        "estimated_unit_price": t.estimated_unit_price,
        "status": t.status,
        "source": t.source,
        "strategy_name": t.strategy_name,
    }


def _explanation_brief(e: ExplanationEvent) -> dict[str, Any]:
    return {
        "recorded_at": e.recorded_at.isoformat(),
        "strategy_name": e.strategy_name,
        "decision_type": e.decision_type,
        "symbol": e.symbol,
        "side": e.side,
        "risk_allowed": e.risk_allowed,
        "reason_codes": list(e.reason_codes),
    }


def _daily_human_lines(
    target_date: date,
    portfolio: dict[str, Any],
    trades: list[TradeEvent],
    explanations: list[ExplanationEvent],
    kill_switch: dict[str, Any],
) -> list[str]:
    lines = [f"Milodex Daily Report — {target_date.isoformat()}", ""]

    if kill_switch["active"]:
        lines.append("!! KILL SWITCH ACTIVE !!")
        lines.append(f"   Reason: {kill_switch.get('reason') or 'no reason recorded'}")
        lines.append("")

    lines.append("Portfolio")
    if portfolio.get("connected"):
        lines.append(
            f"  Equity: {format_money(portfolio['equity'])}   "
            f"Cash: {format_money(portfolio['cash'])}   "
            f"Positions: {len(portfolio['positions'])}"
        )
    else:
        lines.append(f"  (broker unreachable: {portfolio.get('error') or 'unknown'})")
    lines.append("")

    rejections = sum(1 for e in explanations if not e.risk_allowed)
    submitted = sum(1 for t in trades if t.status == "submitted")
    lines.append("Today's Activity")
    lines.append(
        f"  {len(explanations)} explanations, {submitted} submitted trades, {rejections} rejections"
    )
    for t in trades:
        ts = t.recorded_at.strftime("%H:%M")
        lines.append(
            f"  - {ts}  {t.symbol:<6} {t.side:<4} {t.quantity:>8.2f} @ "
            f"{format_money(t.estimated_unit_price)}  {t.status}"
        )
    lines.append("")

    lines.append(f"Kill switch: {'active' if kill_switch['active'] else 'inactive'}")
    lines.append("Next expected: evaluate at next scheduled run")
    return lines


# ---------------------------------------------------------------------------
# Strategy report
# ---------------------------------------------------------------------------


def _build_strategy_result(event_store: EventStore, strategy_id: str) -> CommandResult:
    latest_run = _latest_backtest_for_strategy(event_store, strategy_id)
    if latest_run is None:
        return error_result(
            command="report.strategy",
            message=(
                f"No backtest run found for strategy '{strategy_id}'. "
                "Milodex has no evidence to summarize for this strategy. "
                "Run a backtest (milodex backtest <strategy_id>) and re-try "
                "milodex report strategy."
            ),
            code="no_backtest_run",
        )

    if latest_run.id is None:
        raise ValueError(f"Backtest run has no DB id: {latest_run.run_id}")

    raw_trades = event_store.list_trades_for_backtest_run(latest_run.id)
    equity_curve = equity_curve_from_trades(raw_trades, latest_run.metadata or {})
    trades_dicts = [
        {
            "symbol": t.symbol,
            "side": t.side,
            "quantity": t.quantity,
            "estimated_unit_price": t.estimated_unit_price,
            "recorded_at": t.recorded_at.isoformat(),
        }
        for t in raw_trades
    ]
    metadata = latest_run.metadata or {}
    initial_equity = float(metadata.get("initial_equity", 100_000.0))
    metrics = compute_metrics(
        run_id=latest_run.run_id,
        strategy_id=latest_run.strategy_id,
        start_date=latest_run.start_date.date(),
        end_date=latest_run.end_date.date(),
        initial_equity=initial_equity,
        equity_curve=equity_curve,
        trades=trades_dicts,
    )

    latest_promo = event_store.get_latest_promotion_for_strategy(strategy_id)
    stage = latest_promo.to_stage if latest_promo is not None else "backtest"
    confidence = {
        "label": metrics.confidence_label,
        "reason": f"trade_count={metrics.trade_count}",
    }

    data: dict[str, Any] = {
        "strategy_id": strategy_id,
        "stage": stage,
        "config_fingerprint": latest_run.config_hash,
        "latest_backtest_run_id": latest_run.run_id,
        "metrics": performance_metrics_to_dict(metrics),
        "confidence": confidence,
        "known_weaknesses": None,
        "paper_vs_backtest": None,
    }

    lines = [
        f"Milodex Strategy Report — {strategy_id}",
        f"  Stage:               {stage}",
        f"  Latest backtest:     {latest_run.run_id}",
        f"  Config fingerprint:  {latest_run.config_hash or 'n/a'}",
        "",
        "Performance",
        f"  Period:          {metrics.start_date} to {metrics.end_date}",
        f"  Total return:    {metrics.total_return_pct:+.2f}%",
        f"  Max drawdown:    {metrics.max_drawdown_pct:.2f}%",
        f"  Sharpe:          {metrics.sharpe_ratio:.2f}"
        if metrics.sharpe_ratio is not None
        else "  Sharpe:          n/a",
        f"  Sortino:         {metrics.sortino_ratio:.2f}"
        if metrics.sortino_ratio is not None
        else "  Sortino:         n/a",
        f"  Trades:          {metrics.trade_count} "
        f"({metrics.buy_count}B/{metrics.sell_count}S, "
        f"{metrics.winning_trades}W/{metrics.losing_trades}L)",
        f"  Win rate:        {metrics.win_rate_pct:.1f}%"
        if metrics.win_rate_pct is not None
        else "  Win rate:        n/a",
        f"  Avg hold:        {metrics.avg_hold_days:.1f}d"
        if metrics.avg_hold_days is not None
        else "  Avg hold:        n/a",
        "",
        f"Confidence: {confidence['label']} ({confidence['reason']})",
        "Known weaknesses:   not recorded yet",
        "Paper vs backtest:  not available — strategy has no paper trading history",
    ]

    return CommandResult(command="report.strategy", data=data, human_lines=lines)


def _latest_backtest_for_strategy(
    event_store: EventStore, strategy_id: str
) -> BacktestRunEvent | None:
    completed = [
        run_
        for run_ in event_store.list_backtest_runs()
        if run_.strategy_id == strategy_id and run_.status == "completed"
    ]
    if not completed:
        return None
    return completed[-1]  # list_backtest_runs orders ascending by id; last is newest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_utc() -> date:
    return datetime.now(tz=UTC).date()


def _event_date(value: datetime) -> date:
    return _aware(value).astimezone(UTC).date()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
