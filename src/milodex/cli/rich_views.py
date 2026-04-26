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
