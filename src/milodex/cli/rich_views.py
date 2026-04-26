"""Rich-terminal views (display layer only, R-CLI-009 contract untouched).

This module builds ``rich.console.Renderable`` objects (``Panel``,
``Table``, ``Group``, …) that the human formatter prints when stdout is
a TTY. The ``--json`` machine contract is unaffected — these views never
serialize and never round-trip through JSON.

Each builder takes plain Python data (or domain objects) and returns a
single renderable. Commands keep their structured ``data`` dict and
``human_lines`` exactly as before; the renderable is an *additional*
field on ``CommandResult`` that lights up only on TTY stdout.

Conventions:

- One builder per surface, named ``build_<surface>_view``.
- Color semantics are domain-aware: red for kill-switch / loss /
  drawdown beyond a guardrail, yellow for "below the promotion gate but
  not catastrophic," green for healthy / passing.
- Borders use the ``ROUNDED`` box style for panels and the default
  ``HEAVY_HEAD`` for tables — visually consistent across the app.
- Stay terse. A panel with five labeled values beats a sentence-form
  paragraph for an operator scanning the screen at a glance.
"""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _money_text(value: float, *, color: str | None = None) -> Text:
    """Render a USD value with the project's existing format_money style."""
    sign = "-" if value < 0 else ""
    return Text(f"{sign}${abs(value):,.2f}", style=color or "")


def _pnl_color(value: float) -> str:
    """Green for non-negative, red for negative — operator-scan friendly."""
    if value > 0:
        return "green"
    if value < 0:
        return "red"
    return ""


def _pct_text(value: float, *, color: str | None = None) -> Text:
    """Render a fraction like 0.0532 as ``+5.32%``."""
    return Text(f"{value * 100:+.2f}%", style=color or "")


def _kill_switch_banner(active: bool, reason: str | None = None) -> Panel | None:
    """Return a red banner Panel when the kill switch is active, else ``None``.

    Operators should see kill-switch state immediately on any status-shaped
    surface — it's the one piece of state that overrides everything else.
    """
    if not active:
        return None
    body = Text("KILL SWITCH ACTIVE", style="bold white on red", justify="center")
    if reason:
        body = Text.from_markup(
            f"[bold white on red]KILL SWITCH ACTIVE[/]\n"
            f"[red]Reason:[/] {reason}\n"
            "[red]Trading halted until manual reset "
            "(`milodex trade kill-switch reset --confirm`)[/]"
        )
    return Panel(body, border_style="red", box=box.HEAVY)


# ---------------------------------------------------------------------------
# status / positions / orders
# ---------------------------------------------------------------------------


def build_status_view(
    *,
    trading_mode: str,
    market_open: bool,
    account: dict[str, Any],
    kill_switch_active: bool = False,
    kill_switch_reason: str | None = None,
) -> Group:
    """Trading-mode + account snapshot panel, with optional kill-switch banner."""
    mode_color = "yellow" if trading_mode == "paper" else "red"
    market_marker = "[green]open[/green]" if market_open else "[dim]closed[/dim]"

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", style="bold")
    table.add_column(justify="right")
    table.add_row("Trading mode", f"[{mode_color}]{trading_mode}[/{mode_color}]")
    table.add_row("Market", market_marker)
    table.add_row("Equity", _money_text(float(account.get("equity", 0.0))))
    table.add_row("Cash", _money_text(float(account.get("cash", 0.0))))
    table.add_row("Buying power", _money_text(float(account.get("buying_power", 0.0))))
    table.add_row("Portfolio value", _money_text(float(account.get("portfolio_value", 0.0))))
    daily_pnl = float(account.get("daily_pnl", 0.0))
    table.add_row(
        "Daily P&L",
        _money_text(daily_pnl, color=_pnl_color(daily_pnl)),
    )

    body: list[Any] = []
    banner = _kill_switch_banner(kill_switch_active, kill_switch_reason)
    if banner is not None:
        body.append(banner)
    body.append(Panel(table, title="Milodex Status", border_style="cyan", box=box.ROUNDED))
    return Group(*body)


def build_positions_view(
    *,
    positions: list[Any],
    sort_key: str,
    limit: int,
) -> Panel:
    """Open-positions table with PnL coloring."""
    if not positions:
        empty = Text("No open positions.", style="dim")
        return Panel(empty, title="Open Positions", border_style="cyan", box=box.ROUNDED)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("Symbol")
    table.add_column("Qty", justify="right")
    table.add_column("Avg entry", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Mkt value", justify="right")
    table.add_column("Unrlzd P&L", justify="right")
    table.add_column("Unrlzd P&L %", justify="right")

    for position in positions[:limit]:
        pnl = float(getattr(position, "unrealized_pnl", 0.0))
        pnl_pct = float(getattr(position, "unrealized_pnl_pct", 0.0))
        color = _pnl_color(pnl)
        table.add_row(
            getattr(position, "symbol", ""),
            f"{getattr(position, 'quantity', 0.0):.2f}",
            _money_text(float(getattr(position, "avg_entry_price", 0.0))),
            _money_text(float(getattr(position, "current_price", 0.0))),
            _money_text(float(getattr(position, "market_value", 0.0))),
            _money_text(pnl, color=color),
            _pct_text(pnl_pct, color=color),
        )
    title = f"Open Positions (sort: {sort_key}, limit: {limit})"
    return Panel(table, title=title, border_style="cyan", box=box.ROUNDED)


def build_orders_view(
    *,
    orders: list[Any],
    symbol_filter: str | None,
    verbose: bool,
) -> Panel:
    """Recent-orders table with status-aware coloring."""
    title = "Recent Orders"
    if symbol_filter:
        title += f" — symbol={symbol_filter}"

    if not orders:
        return Panel(
            Text("No matching orders.", style="dim"),
            title=title,
            border_style="cyan",
            box=box.ROUNDED,
        )

    status_color = {
        "filled": "green",
        "partially_filled": "yellow",
        "canceled": "yellow",
        "rejected": "red",
        "expired": "dim",
        "pending": "cyan",
        "accepted": "cyan",
        "new": "cyan",
    }

    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("ID")
    table.add_column("Symbol")
    table.add_column("Side")
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Qty", justify="right")
    table.add_column("Submitted")
    if verbose:
        table.add_column("Details")

    for order in orders:
        status = order.status.value if hasattr(order.status, "value") else str(order.status)
        side = order.side.value if hasattr(order.side, "value") else str(order.side)
        order_type = (
            order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type)
        )
        side_color = "green" if side.lower() == "buy" else "red"
        row = [
            order.id[:12],
            order.symbol,
            Text(side.upper(), style=side_color),
            order_type,
            Text(status, style=status_color.get(status.lower(), "")),
            f"{order.quantity:.2f}",
            order.submitted_at.isoformat(),
        ]
        if verbose:
            details = []
            if order.limit_price is not None:
                details.append(f"limit=${order.limit_price:,.2f}")
            if order.stop_price is not None:
                details.append(f"stop=${order.stop_price:,.2f}")
            if order.filled_quantity is not None:
                details.append(f"filled_qty={order.filled_quantity:.2f}")
            if order.filled_avg_price is not None:
                details.append(f"filled_avg=${order.filled_avg_price:,.2f}")
            row.append(", ".join(details) if details else "")
        table.add_row(*row)
    return Panel(table, title=title, border_style="cyan", box=box.ROUNDED)


# ---------------------------------------------------------------------------
# report (default trust report) and report strategy
# ---------------------------------------------------------------------------


def _stage_color(stage: str) -> str:
    """Color a stage name by exposure: live=red, micro_live=red, paper=yellow, backtest=cyan."""
    return {
        "live": "red",
        "micro_live": "red",
        "paper": "yellow",
        "backtest": "cyan",
        "disabled": "dim",
    }.get(stage, "")


def _confidence_color(label: str) -> str:
    """Color a confidence label by trustworthiness."""
    return {
        "meaningful": "green",
        "preliminary": "yellow",
        "insufficient_data": "red",
        "insufficient_evidence": "red",
    }.get(label, "")


def build_trust_report_view(
    *,
    strategies: list[dict],
    kill_switch: dict,
    broker: dict,
    data_freshness: dict,
    operator_action_required: bool,
) -> Group:
    """Default `milodex report` — operator's daily orientation panel.

    Top-level kill-switch banner (when active), broker-state panel, then
    one row per strategy in a stage-colored table. The operator-action
    flag draws an unmistakable header so an unattended kill-switch trip
    or a stale broker connection doesn't get buried under strategy rows.
    """
    body: list[Any] = []

    banner = _kill_switch_banner(
        kill_switch.get("active", False),
        kill_switch.get("reason"),
    )
    if banner is not None:
        body.append(banner)
    elif operator_action_required:
        body.append(
            Panel(
                Text(
                    "OPERATOR ACTION REQUIRED",
                    style="bold black on yellow",
                    justify="center",
                ),
                border_style="yellow",
                box=box.HEAVY,
            )
        )

    broker_grid = Table.grid(padding=(0, 2))
    broker_grid.add_column(justify="left", style="bold")
    broker_grid.add_column(justify="left")
    if broker.get("connected"):
        broker_grid.add_row("Broker", "[green]connected[/green]")
    else:
        broker_grid.add_row("Broker", "[red]UNREACHABLE[/red]")
    broker_grid.add_row(
        "Trading mode",
        f"[yellow]{broker.get('trading_mode', 'unknown')}[/yellow]",
    )
    if data_freshness:
        if data_freshness.get("latest_bar_at"):
            broker_grid.add_row("Latest bar", str(data_freshness["latest_bar_at"]))
        if "trading_days_behind" in data_freshness:
            days = int(data_freshness["trading_days_behind"])
            color = "green" if days <= 1 else "yellow" if days <= 3 else "red"
            broker_grid.add_row(
                "Trading days behind",
                f"[{color}]{days}[/{color}]",
            )
    body.append(Panel(broker_grid, title="Environment", border_style="cyan", box=box.ROUNDED))

    if not strategies:
        body.append(
            Panel(
                Text(
                    "No strategies have produced any activity yet. Run a "
                    "backtest, strategy, or trade preview to populate this list.",
                    style="dim",
                ),
                title="Strategies",
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
        return Group(*body)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("Strategy")
    table.add_column("Stage")
    table.add_column("Confidence")
    table.add_column("Last action")
    table.add_column("Risk")
    for snap in strategies:
        stage = snap.get("stage", "")
        confidence = snap.get("confidence", {})
        last = snap.get("last_action") or {}
        risk_allowed = last.get("risk_allowed")
        risk_text = (
            Text("allowed", style="green")
            if risk_allowed is True
            else Text("blocked", style="red")
            if risk_allowed is False
            else Text("—", style="dim")
        )
        table.add_row(
            snap.get("strategy_id", ""),
            Text(stage, style=_stage_color(stage)),
            Text(
                confidence.get("label", "—"),
                style=_confidence_color(confidence.get("label", "")),
            ),
            f"{last.get('decision_type', '—')} {last.get('symbol') or ''}".strip() if last else "—",
            risk_text,
        )
    body.append(Panel(table, title="Strategies", border_style="cyan", box=box.ROUNDED))
    return Group(*body)


def build_strategy_report_view(
    *,
    strategy_id: str,
    stage: str,
    stage_source: str,
    latest_promotion_stage: str | None,
    stage_disagreement: bool,
    config_fingerprint: str | None,
    latest_backtest_run_id: str,
    metrics: dict,
    confidence: dict,
) -> Group:
    """Per-strategy `report strategy <id>` panel.

    Three stacked panels:
    1. Stage / manifest / config-fingerprint header (with disagreement
       warning surfaced as a red sub-banner when the promotion log
       disagrees with the active manifest).
    2. Performance — coloring keyed to promotion-gate thresholds
       (Sharpe ≥ 0.5 green / 0–0.5 yellow / negative red, max drawdown
       ≤ 15% green / > 15% red, per `R-PRM-001..007`).
    3. Confidence + known-weaknesses footer.
    """
    body: list[Any] = []

    header = Table.grid(padding=(0, 2))
    header.add_column(justify="left", style="bold")
    header.add_column(justify="left")
    stage_text = Text(stage, style=_stage_color(stage))
    if stage_source == "manifest":
        stage_text.append(" (frozen manifest)", style="dim")
    elif stage_source == "promotion_log":
        stage_text.append(" (no frozen manifest yet)", style="dim")
    header.add_row("Stage", stage_text)
    header.add_row("Latest backtest", latest_backtest_run_id)
    header.add_row("Config fingerprint", config_fingerprint or "n/a")
    body.append(
        Panel(
            header,
            title=f"Strategy Report — {strategy_id}",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    if stage_disagreement and latest_promotion_stage is not None:
        body.append(
            Panel(
                Text.from_markup(
                    f"[bold]Stage bookkeeping mismatch[/bold]\n"
                    f"Promotion log says [yellow]{latest_promotion_stage}[/yellow] "
                    f"but no manifest is frozen at that stage — runtime treats "
                    f"this strategy as [bold cyan]{stage}[/bold cyan] "
                    "(the manifest's stage).\n"
                    "Consider demoting + repromoting to reconcile."
                ),
                border_style="red",
                box=box.HEAVY,
            )
        )

    perf = Table.grid(padding=(0, 2))
    perf.add_column(justify="left", style="bold")
    perf.add_column(justify="right")
    period = f"{metrics.get('start_date')} to {metrics.get('end_date')}"
    if metrics.get("result_type") == "walk_forward":
        period += " [dim](OOS-aggregate, walk-forward)[/dim]"
    perf.add_row("Period", period)
    total_return = float(metrics.get("total_return_pct", 0.0))
    perf.add_row(
        "Total return",
        Text(
            f"{total_return:+.2f}%",
            style="green" if total_return > 0 else "red" if total_return < 0 else "",
        ),
    )
    max_dd = float(metrics.get("max_drawdown_pct", 0.0))
    dd_color = "red" if max_dd > 15.0 else "yellow" if max_dd > 7.5 else "green"
    perf.add_row(
        "Max drawdown",
        Text(f"{max_dd:.2f}%", style=dd_color),
    )
    sharpe = metrics.get("sharpe_ratio")
    if sharpe is not None:
        sharpe_val = float(sharpe)
        sharpe_color = "red" if sharpe_val < 0 else "yellow" if sharpe_val < 0.5 else "green"
        perf.add_row("Sharpe", Text(f"{sharpe_val:.2f}", style=sharpe_color))
    else:
        perf.add_row("Sharpe", Text("n/a", style="dim"))
    sortino = metrics.get("sortino_ratio")
    if sortino is not None:
        perf.add_row("Sortino", f"{float(sortino):.2f}")
    else:
        perf.add_row("Sortino", Text("n/a", style="dim"))
    trades = metrics.get("trade_count", 0)
    perf.add_row(
        "Trades",
        f"{trades} ({metrics.get('buy_count', 0)}B/{metrics.get('sell_count', 0)}S, "
        f"{metrics.get('winning_trades', 0)}W/{metrics.get('losing_trades', 0)}L)",
    )
    win_rate = metrics.get("win_rate_pct")
    perf.add_row(
        "Win rate",
        f"{float(win_rate):.1f}%" if win_rate is not None else Text("n/a", style="dim"),
    )
    body.append(Panel(perf, title="Performance", border_style="cyan", box=box.ROUNDED))

    confidence_label = confidence.get("label", "—")
    footer = Table.grid(padding=(0, 2))
    footer.add_column(justify="left", style="bold")
    footer.add_column(justify="left")
    footer.add_row(
        "Confidence",
        Text(
            f"{confidence_label} ({confidence.get('reason', '')})",
            style=_confidence_color(confidence_label),
        ),
    )
    footer.add_row("Known weaknesses", Text("not recorded yet", style="dim"))
    footer.add_row(
        "Paper vs backtest",
        Text("not available — no paper history yet", style="dim"),
    )
    body.append(Panel(footer, title="Trust", border_style="cyan", box=box.ROUNDED))
    return Group(*body)


# ---------------------------------------------------------------------------
# analytics metrics
# ---------------------------------------------------------------------------


def _format_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def _metrics_panel(metrics: dict, *, title: str) -> Panel:
    """Build a single-strategy metrics panel keyed to promotion-gate thresholds."""
    period = f"{metrics.get('start_date')} to {metrics.get('end_date')}"
    if metrics.get("result_type") == "walk_forward":
        period += " [dim](OOS-aggregate, walk-forward)[/dim]"

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", style="bold")
    table.add_column(justify="right")

    table.add_row("Strategy ID", metrics.get("strategy_id", ""))
    table.add_row("Run ID", metrics.get("run_id", ""))
    table.add_row("Period", period)
    table.add_row("Trading days", str(metrics.get("trading_days", 0)))

    total_return = float(metrics.get("total_return_pct", 0.0))
    table.add_row(
        "Total return",
        Text(
            f"{total_return:+.2f}%",
            style="green" if total_return > 0 else "red" if total_return < 0 else "",
        ),
    )

    cagr = metrics.get("cagr_pct")
    if cagr is None:
        table.add_row("CAGR", Text("n/a", style="dim"))
    else:
        cagr_val = float(cagr)
        table.add_row(
            "CAGR",
            Text(
                f"{cagr_val:+.2f}%",
                style="green" if cagr_val > 0 else "red" if cagr_val < 0 else "",
            ),
        )

    max_dd = float(metrics.get("max_drawdown_pct", 0.0))
    dd_color = "red" if max_dd > 15.0 else "yellow" if max_dd > 7.5 else "green"
    table.add_row("Max drawdown", Text(f"{max_dd:.2f}%", style=dd_color))

    sharpe = metrics.get("sharpe_ratio")
    if sharpe is None:
        table.add_row("Sharpe", Text("n/a", style="dim"))
    else:
        sharpe_val = float(sharpe)
        sharpe_color = "red" if sharpe_val < 0 else "yellow" if sharpe_val < 0.5 else "green"
        table.add_row("Sharpe", Text(f"{sharpe_val:.2f}", style=sharpe_color))

    sortino = metrics.get("sortino_ratio")
    if sortino is None:
        table.add_row("Sortino", Text("n/a", style="dim"))
    else:
        table.add_row("Sortino", f"{float(sortino):.2f}")

    win_rate = metrics.get("win_rate_pct")
    if win_rate is None:
        table.add_row("Win rate", Text("n/a", style="dim"))
    else:
        table.add_row("Win rate", f"{float(win_rate):.1f}%")

    avg_hold = metrics.get("avg_hold_days")
    if avg_hold is None:
        table.add_row("Avg hold", Text("n/a", style="dim"))
    else:
        table.add_row("Avg hold", f"{float(avg_hold):.1f}d")

    table.add_row(
        "Trades",
        f"{metrics.get('trade_count', 0)} "
        f"({metrics.get('buy_count', 0)}B/{metrics.get('sell_count', 0)}S, "
        f"{metrics.get('winning_trades', 0)}W/{metrics.get('losing_trades', 0)}L)",
    )

    avg_win = metrics.get("avg_win_usd")
    avg_loss = metrics.get("avg_loss_usd")
    table.add_row("Avg win", _format_money(avg_win) if avg_win is not None else "n/a")
    table.add_row("Avg loss", _format_money(avg_loss) if avg_loss is not None else "n/a")

    profit_factor = metrics.get("profit_factor")
    if profit_factor is None or profit_factor == float("inf"):
        table.add_row("Profit factor", "n/a" if profit_factor is None else "∞")
    else:
        pf = float(profit_factor)
        pf_color = "green" if pf >= 1.5 else "yellow" if pf >= 1.0 else "red"
        table.add_row("Profit factor", Text(f"{pf:.2f}", style=pf_color))

    confidence = str(metrics.get("confidence_label", ""))
    table.add_row(
        "Confidence",
        Text(confidence, style=_confidence_color(confidence)),
    )

    return Panel(table, title=title, border_style="cyan", box=box.ROUNDED)


def build_analytics_metrics_view(
    *,
    strategy: dict,
    benchmark: dict | None,
) -> Group:
    """`milodex analytics metrics` — strategy panel + optional SPY benchmark panel."""
    body: list[Any] = [_metrics_panel(strategy, title="Strategy")]
    if benchmark is not None:
        body.append(_metrics_panel(benchmark, title="SPY Benchmark"))
    return Group(*body)


# ---------------------------------------------------------------------------
# promotion history / promotion manifest
# ---------------------------------------------------------------------------


def build_promotion_history_view(
    *,
    strategy_id: str,
    events: list[dict],
) -> Panel:
    """Stage-transition timeline. Empty events → "no history" panel.

    Each row colors the stage cells (red live/micro_live, yellow paper,
    cyan backtest) and flags missing manifests as a yellow warning —
    mirroring the runtime drift policy. Reversal chains (`↩event_id`)
    appear inline against the original event id.
    """
    if not events:
        return Panel(
            Text(f"No promotion history for {strategy_id}.", style="dim"),
            title=f"Promotion History — {strategy_id}",
            border_style="cyan",
            box=box.ROUNDED,
        )

    table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    table.add_column("ID")
    table.add_column("Recorded at")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Type")
    table.add_column("Manifest")
    table.add_column("Approved by")

    for ev in events:
        id_cell = str(ev.get("id", ""))
        if ev.get("reverses_event_id") is not None:
            id_cell = f"{id_cell} (↩{ev['reverses_event_id']})"
        manifest_id = ev.get("manifest_id")
        manifest_cell: Any = (
            Text("none", style="yellow") if manifest_id is None else f"mid={manifest_id}"
        )
        table.add_row(
            id_cell,
            str(ev.get("recorded_at", "")),
            Text(str(ev.get("from_stage", "")), style=_stage_color(str(ev.get("from_stage", "")))),
            Text(str(ev.get("to_stage", "")), style=_stage_color(str(ev.get("to_stage", "")))),
            str(ev.get("promotion_type", "")),
            manifest_cell,
            str(ev.get("approved_by", "")),
        )
    title = f"Promotion History — {strategy_id} ({len(events)} event(s))"
    return Panel(table, title=title, border_style="cyan", box=box.ROUNDED)


# ---------------------------------------------------------------------------
# backtest result panels
# ---------------------------------------------------------------------------


def build_backtest_view(
    *,
    strategy_id: str,
    run_id: str,
    start_date: str,
    end_date: str,
    trading_days: int,
    initial_equity: float,
    final_equity: float,
    total_return_pct: float,
    trade_count: int,
    buy_count: int,
    sell_count: int,
    slippage_pct: float,
    commission_per_trade: float,
    confidence_label: str | None = None,
    confidence_reason: str | None = None,
    extra_warnings: list[str] | None = None,
) -> Group:
    """`milodex backtest <strategy>` (whole-period) result panel."""
    body: list[Any] = []
    summary = Table.grid(padding=(0, 2))
    summary.add_column(justify="left", style="bold")
    summary.add_column(justify="right")
    summary.add_row("Strategy", strategy_id)
    summary.add_row("Run ID", run_id)
    summary.add_row("Period", f"{start_date} to {end_date}")
    summary.add_row("Trading days", str(trading_days))
    summary.add_row("Initial equity", _money_text(float(initial_equity)))
    summary.add_row("Final equity", _money_text(float(final_equity)))
    summary.add_row(
        "Total return",
        Text(
            f"{total_return_pct:+.2f}%",
            style="green" if total_return_pct > 0 else "red" if total_return_pct < 0 else "",
        ),
    )
    summary.add_row("Trades", f"{trade_count} ({buy_count}B / {sell_count}S)")
    summary.add_row("Slippage", f"{slippage_pct * 100:.2f}%")
    summary.add_row("Commission", f"{_money_text(float(commission_per_trade))} / trade")
    if confidence_label:
        summary.add_row(
            "Confidence",
            Text(
                f"{confidence_label}" + (f" ({confidence_reason})" if confidence_reason else ""),
                style=_confidence_color(confidence_label),
            ),
        )
    body.append(Panel(summary, title="Backtest Result", border_style="cyan", box=box.ROUNDED))
    if extra_warnings:
        body.append(
            Panel(
                Text.from_markup("\n".join(f"[yellow]• {w}[/yellow]" for w in extra_warnings)),
                title="Notes",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
    return Group(*body)


def build_walk_forward_view(
    *,
    strategy_id: str,
    run_id: str,
    start_date: str,
    end_date: str,
    initial_equity: float,
    train_days: int,
    test_days: int,
    step_days: int,
    oos_trading_days: int,
    oos_trade_count: int,
    oos_total_return_pct: float,
    oos_sharpe: float | None,
    oos_max_drawdown_pct: float,
    stability: dict,
    windows: list[dict],
    extra_warnings: list[str] | None = None,
) -> Group:
    """`milodex backtest --walk-forward` result panel.

    Three stacked views:
    - OOS-aggregate panel (the metrics the promotion gate evaluates).
    - Stability panel — color-codes single-window-dependency in red.
    - Per-window table with return / Sharpe / maxDD per row.
    """
    body: list[Any] = []

    summary = Table.grid(padding=(0, 2))
    summary.add_column(justify="left", style="bold")
    summary.add_column(justify="right")
    summary.add_row("Strategy", strategy_id)
    summary.add_row("Run ID", run_id)
    summary.add_row("Period", f"{start_date} to {end_date}")
    summary.add_row(
        "Windows",
        f"{len(windows)} (train={train_days}d, test={test_days}d, step={step_days}d)",
    )
    summary.add_row("Initial equity", _money_text(float(initial_equity)))
    body.append(
        Panel(
            summary,
            title="Backtest Run (walk-forward)",
            border_style="cyan",
            box=box.ROUNDED,
        )
    )

    oos = Table.grid(padding=(0, 2))
    oos.add_column(justify="left", style="bold")
    oos.add_column(justify="right")
    oos.add_row("Trading days", str(oos_trading_days))
    oos.add_row("Trades", str(oos_trade_count))
    oos.add_row(
        "Total return",
        Text(
            f"{oos_total_return_pct:+.2f}%",
            style="green"
            if oos_total_return_pct > 0
            else "red"
            if oos_total_return_pct < 0
            else "",
        ),
    )
    if oos_sharpe is None:
        oos.add_row("Sharpe", Text("n/a", style="dim"))
    else:
        sharpe_color = "red" if oos_sharpe < 0 else "yellow" if oos_sharpe < 0.5 else "green"
        oos.add_row("Sharpe", Text(f"{oos_sharpe:.2f}", style=sharpe_color))
    dd_color = (
        "red"
        if oos_max_drawdown_pct > 15.0
        else "yellow"
        if oos_max_drawdown_pct > 7.5
        else "green"
    )
    oos.add_row(
        "Max drawdown",
        Text(f"{oos_max_drawdown_pct:.2f}%", style=dd_color),
    )
    body.append(Panel(oos, title="OOS Aggregate", border_style="cyan", box=box.ROUNDED))

    stab = Table.grid(padding=(0, 2))
    stab.add_column(justify="left", style="bold")
    stab.add_column(justify="right")

    def _opt(v: Any, fmt: str = ".2f") -> str:
        if v is None:
            return "n/a"
        return f"{v:{fmt}}"

    stab.add_row(
        "Sharpe min/max/std",
        f"{_opt(stability.get('sharpe_min'))} / "
        f"{_opt(stability.get('sharpe_max'))} / "
        f"{_opt(stability.get('sharpe_std'))}",
    )
    pos = stability.get("windows_positive", 0)
    neg = stability.get("windows_negative", 0)
    stab.add_row("Positive windows", f"{pos} / {len(windows)}")
    stab.add_row("Negative windows", f"{neg} / {len(windows)}")
    swd = bool(stability.get("single_window_dependency"))
    stab.add_row(
        "Single-window dependency",
        Text("YES — fragile", style="red") if swd else Text("no", style="green"),
    )
    body.append(Panel(stab, title="Stability", border_style="cyan", box=box.ROUNDED))

    if windows:
        wt = Table(box=box.SIMPLE_HEAD, header_style="bold")
        wt.add_column("#", justify="right")
        wt.add_column("Test start")
        wt.add_column("Test end")
        wt.add_column("Trades", justify="right")
        wt.add_column("Return", justify="right")
        wt.add_column("Sharpe", justify="right")
        wt.add_column("Max DD", justify="right")
        for w in windows:
            r = float(w.get("total_return_pct", 0.0))
            r_color = "green" if r > 0 else "red" if r < 0 else ""
            sharpe = w.get("sharpe")
            sharpe_text: Any = (
                Text("n/a", style="dim")
                if sharpe is None
                else Text(
                    f"{float(sharpe):.2f}",
                    style="red"
                    if float(sharpe) < 0
                    else "yellow"
                    if float(sharpe) < 0.5
                    else "green",
                )
            )
            dd = float(w.get("max_drawdown_pct", 0.0))
            dd_c = "red" if dd > 15.0 else "yellow" if dd > 7.5 else "green"
            wt.add_row(
                str(w.get("index", "")),
                str(w.get("test_start", "")),
                str(w.get("test_end", "")),
                str(w.get("trade_count", 0)),
                Text(f"{r:+.2f}%", style=r_color),
                sharpe_text,
                Text(f"{dd:.2f}%", style=dd_c),
            )
        body.append(Panel(wt, title="Per-window OOS Results", border_style="cyan", box=box.ROUNDED))

    if extra_warnings:
        body.append(
            Panel(
                Text.from_markup("\n".join(f"[yellow]• {w}[/yellow]" for w in extra_warnings)),
                title="Notes",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
    return Group(*body)


# ---------------------------------------------------------------------------
# trade preview / submit
# ---------------------------------------------------------------------------


def build_trade_execution_view(
    *,
    status: str,
    side: str,
    symbol: str,
    quantity: float,
    order_type: str,
    time_in_force: str,
    estimated_unit_price: float,
    estimated_order_value: float,
    trading_mode: str,
    market_open: bool,
    strategy_name: str | None,
    strategy_stage: str | None,
    risk_checks: list[dict],
    risk_allowed: bool,
    broker_order_id: str | None,
    broker_status: str | None,
    message: str | None,
) -> Group:
    """`milodex trade preview/submit` — order details + risk check breakdown.

    Top-level disposition banner (green ALLOW, red BLOCK) so the operator
    sees the decision before any details. Risk checks render as a table
    with PASS/FAIL color codes, mirroring the underlying decision record.
    """
    body: list[Any] = []

    disposition = (
        ("ALLOW", "bold black on green") if risk_allowed else ("BLOCK", "bold white on red")
    )
    body.append(
        Panel(
            Text(disposition[0], style=disposition[1], justify="center"),
            border_style="green" if risk_allowed else "red",
            box=box.HEAVY,
        )
    )

    order = Table.grid(padding=(0, 2))
    order.add_column(justify="left", style="bold")
    order.add_column(justify="right")
    side_color = "green" if side.lower() == "buy" else "red"
    order.add_row("Symbol", symbol)
    order.add_row("Side", Text(side.upper(), style=side_color))
    order.add_row("Type", order_type)
    order.add_row("Quantity", f"{quantity:.2f}")
    order.add_row("Time in force", time_in_force)
    order.add_row("Est. unit price", _money_text(float(estimated_unit_price)))
    order.add_row("Est. order value", _money_text(float(estimated_order_value)))
    mode_color = "yellow" if trading_mode == "paper" else "red"
    order.add_row("Trading mode", Text(trading_mode, style=mode_color))
    order.add_row(
        "Market",
        Text("open", style="green") if market_open else Text("closed", style="dim"),
    )
    if strategy_name:
        order.add_row("Strategy", f"{strategy_name} ({strategy_stage})")
    if broker_order_id:
        order.add_row("Broker order ID", broker_order_id)
    if broker_status:
        order.add_row("Broker status", broker_status)
    if status:
        order.add_row("Execution status", status)
    if message:
        order.add_row("Message", Text(message, style="yellow"))
    body.append(Panel(order, title="Order", border_style="cyan", box=box.ROUNDED))

    if risk_checks:
        risk_table = Table(box=box.SIMPLE_HEAD, header_style="bold")
        risk_table.add_column("")
        risk_table.add_column("Check")
        risk_table.add_column("Detail")
        for check in risk_checks:
            passed = bool(check.get("passed"))
            risk_table.add_row(
                Text("PASS", style="green") if passed else Text("FAIL", style="red"),
                str(check.get("name", "")),
                str(check.get("message", "")),
            )
        body.append(Panel(risk_table, title="Risk Checks", border_style="cyan", box=box.ROUNDED))
    return Group(*body)


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def build_reconcile_view(
    *,
    broker: dict,
    positions_ok: list[dict],
    positions_mismatched: list[dict],
    orders_ok: list[dict],
    orders_mismatched: list[dict],
    deferred_checks: list[str],
    reconciliation_clean: bool,
    incident_recorded: bool,
    incident_deduplicated: bool,
    incident_hash: str | None,
    as_of: str,
) -> Group:
    """`milodex reconcile` — broker-vs-event-store comparison panel."""
    body: list[Any] = []

    if not reconciliation_clean:
        if incident_recorded:
            severity_text = Text.from_markup(
                f"[bold white on red]DRIFT DETECTED[/]\n"
                f"[red]Incident recorded — hash {incident_hash or '?':.12s}[/]"
            )
        elif incident_deduplicated:
            severity_text = Text.from_markup(
                f"[bold white on yellow]DRIFT DETECTED (DEDUPLICATED)[/]\n"
                f"[yellow]Hash {incident_hash or '?':.12s} matches a prior "
                "incident — not re-logged (R-OPS-010).[/]"
            )
        else:
            severity_text = Text.from_markup(
                "[bold white on red]DRIFT DETECTED[/]\n"
                "[red]Incident NOT recorded — broker unreachable.[/]"
            )
        body.append(Panel(severity_text, border_style="red", box=box.HEAVY))

    env = Table.grid(padding=(0, 2))
    env.add_column(justify="left", style="bold")
    env.add_column(justify="left")
    env.add_row("As-of", as_of)
    env.add_row(
        "Broker",
        Text("connected", style="green")
        if broker.get("connected")
        else Text("UNREACHABLE", style="red"),
    )
    market = broker.get("market_open")
    if market is True:
        env.add_row("Market", Text("open", style="green"))
    elif market is False:
        env.add_row("Market", Text("closed", style="dim"))
    account = broker.get("account") or {}
    if account:
        env.add_row("Equity", _money_text(float(account.get("equity", 0.0))))
        env.add_row("Cash", _money_text(float(account.get("cash", 0.0))))
        env.add_row("Buying power", _money_text(float(account.get("buying_power", 0.0))))
    body.append(Panel(env, title="Environment", border_style="cyan", box=box.ROUNDED))

    pos_table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    pos_table.add_column("Symbol")
    pos_table.add_column("Local qty", justify="right")
    pos_table.add_column("Broker qty", justify="right")
    pos_table.add_column("Kind")
    if not positions_ok and not positions_mismatched:
        pos_table.add_row("(none)", "—", "—", "")
    else:
        for row in positions_mismatched + positions_ok:
            kind = str(row.get("kind", ""))
            kind_color = "red" if kind != "ok" else "green"
            pos_table.add_row(
                str(row.get("symbol", "")),
                "—" if row.get("local_qty") is None else f"{float(row['local_qty']):g}",
                "—" if row.get("broker_qty") is None else f"{float(row['broker_qty']):g}",
                Text(kind, style=kind_color),
            )
    pos_title = (
        f"Positions ({len(positions_mismatched)} mismatch(es) of "
        f"{len(positions_ok) + len(positions_mismatched)} symbol(s))"
    )
    body.append(Panel(pos_table, title=pos_title, border_style="cyan", box=box.ROUNDED))

    ord_table = Table(box=box.SIMPLE_HEAD, header_style="bold")
    ord_table.add_column("Order ID")
    ord_table.add_column("Symbol")
    ord_table.add_column("Kind")
    if not orders_ok and not orders_mismatched:
        ord_table.add_row("(none)", "—", "")
    else:
        for row in orders_mismatched + orders_ok:
            kind = str(row.get("kind", ""))
            kind_color = "red" if kind != "ok" else "green"
            ord_table.add_row(
                str(row.get("broker_order_id", ""))[:16],
                str(row.get("symbol") or "—"),
                Text(kind, style=kind_color),
            )
    ord_title = (
        f"Open orders ({len(orders_mismatched)} mismatch(es) of "
        f"{len(orders_ok) + len(orders_mismatched)} order(s))"
    )
    body.append(Panel(ord_table, title=ord_title, border_style="cyan", box=box.ROUNDED))

    if deferred_checks:
        body.append(
            Panel(
                Text.from_markup(
                    "[yellow]The following dimensions are scaffolded "
                    "(R-OPS-004 v1.1) and surfaced as warnings only:[/]\n"
                    + "\n".join(f"  • {c}" for c in deferred_checks)
                ),
                title="Deferred checks",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )

    if reconciliation_clean:
        body.append(
            Panel(
                Text(
                    "CLEAN — broker and event store agree on all checked dimensions.",
                    style="bold green",
                ),
                border_style="green",
                box=box.ROUNDED,
            )
        )
    return Group(*body)


def build_promotion_manifest_view(
    *,
    strategy_id: str,
    stage: str,
    active_manifest: dict | None,
) -> Panel:
    """Frozen-manifest panel. ``None`` active manifest → muted "no manifest" panel."""
    if active_manifest is None:
        return Panel(
            Text.from_markup(
                f"No active manifest for [bold]{strategy_id}[/bold] at stage "
                f"[{_stage_color(stage)}]{stage}[/{_stage_color(stage)}].\n"
                "[dim]Use `milodex promotion freeze` before promoting.[/dim]"
            ),
            title="Manifest",
            border_style="yellow",
            box=box.ROUNDED,
        )
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", style="bold")
    table.add_column(justify="left")
    table.add_row("Strategy", strategy_id)
    table.add_row("Stage", Text(stage, style=_stage_color(stage)))
    table.add_row("Config hash", active_manifest.get("config_hash", ""))
    table.add_row("Source", active_manifest.get("config_path", ""))
    table.add_row("Frozen at", active_manifest.get("frozen_at", ""))
    table.add_row("Frozen by", active_manifest.get("frozen_by", ""))
    return Panel(table, title="Active Manifest", border_style="cyan", box=box.ROUNDED)
