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
