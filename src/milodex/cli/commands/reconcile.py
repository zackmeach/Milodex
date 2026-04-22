"""Read-only ``milodex reconcile`` command (R-CLI-015, R-OPS-004).

Compares execution-critical state between the broker (Alpaca) and the local
event store. Broker state is never mutated. When an execution-critical
mismatch is detected, a reconciliation-incident ``ExplanationEvent`` is
written to the event store per R-OPS-004 ("the mismatch **shall** be logged
as an incident"), idempotently keyed by a content hash so repeat runs do
not duplicate the incident (R-OPS-010).

Scope (v1) covers five of the eight OPERATIONS.md "State Reconciliation"
dimensions: current positions, open orders, account snapshot, order IDs,
and halt/incident status. Fills-since-last-sync, canceled-since-last-sync,
and strategy-to-order linkage are deferred and surfaced as warnings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from milodex.broker import BrokerError
from milodex.broker.models import AccountInfo, Order, OrderStatus, Position
from milodex.cli._shared import (
    CommandContext,
    account_to_dict,
    add_global_flags,
    format_money,
)
from milodex.cli.formatter import CommandResult
from milodex.core.advisory_lock import AdvisoryLock
from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent

_DEFERRED_CHECKS: tuple[str, ...] = (
    "filled_since_last_sync",
    "canceled_since_last_sync",
    "strategy_linkage",
)

# An open local order becomes execution-critical drift only when recent. Older
# ``local_only`` submissions are almost always just orders that filled and
# rolled off the broker's open-orders window; treat them as warnings.
_LOCAL_ONLY_INCIDENT_WINDOW = timedelta(hours=24)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "reconcile",
        help="Compare broker state against the event store and flag drift (read-only).",
    )
    add_global_flags(parser)
    parser.add_argument(
        "--as-of",
        dest="reconcile_as_of",
        help=(
            "Optional UTC ISO-8601 timestamp to scope the local event fold. "
            "Broker state is always live."
        ),
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    as_of = _parse_as_of(getattr(args, "reconcile_as_of", None))
    event_store = ctx.get_event_store()

    with AdvisoryLock(
        "milodex.runtime",
        locks_dir=ctx.locks_dir,
        holder_name="milodex reconcile",
    ):
        return _run_reconcile(event_store, ctx, as_of=as_of)


def _run_reconcile(
    event_store: EventStore,
    ctx: CommandContext,
    *,
    as_of: datetime,
) -> CommandResult:
    broker_snapshot = _snapshot_broker(ctx)
    local_positions = _fold_positions(event_store.list_trades(), as_of=as_of)
    local_open_orders = _local_open_orders(event_store.list_trades(), as_of=as_of)

    position_rows = _compare_positions(
        local=local_positions,
        broker=broker_snapshot.positions_by_symbol(),
    )
    order_rows = _compare_orders(
        local=local_open_orders,
        broker=broker_snapshot.open_orders_by_id(),
        now=datetime.now(tz=UTC),
    )

    incident_reasons = _incident_reason_codes(position_rows, order_rows)
    incident_hash = _incident_content_hash(position_rows, order_rows)

    incident_recorded = False
    incident_deduplicated = False
    if incident_reasons and broker_snapshot.connected:
        if _incident_already_logged(event_store, incident_hash):
            incident_deduplicated = True
        else:
            _record_incident_event(
                event_store,
                account=broker_snapshot.account,
                market_open=broker_snapshot.market_open or False,
                reason_codes=incident_reasons,
                content_hash=incident_hash,
                summary=_incident_summary(position_rows, order_rows),
                position_rows=position_rows,
                order_rows=order_rows,
                as_of=as_of,
            )
            incident_recorded = True

    reconciliation_clean = not incident_reasons
    data: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "broker": broker_snapshot.to_dict(),
        "positions": {
            "ok": [r.to_dict() for r in position_rows if r.kind == "ok"],
            "mismatches": [r.to_dict() for r in position_rows if r.kind != "ok"],
        },
        "orders": {
            "ok": [r.to_dict() for r in order_rows if r.kind == "ok"],
            "mismatches": [r.to_dict() for r in order_rows if r.kind != "ok"],
        },
        "deferred_checks": list(_DEFERRED_CHECKS),
        "reconciliation_clean": reconciliation_clean,
        "incident_recorded": incident_recorded,
        "incident_deduplicated": incident_deduplicated,
        "incident_hash": incident_hash if incident_reasons else None,
        "incident_reason_codes": incident_reasons,
    }

    warnings = _build_warnings(
        broker_snapshot=broker_snapshot,
        position_rows=position_rows,
        order_rows=order_rows,
        incident_reasons=incident_reasons,
    )
    human_lines = _human_lines(
        broker_snapshot=broker_snapshot,
        position_rows=position_rows,
        order_rows=order_rows,
        incident_reasons=incident_reasons,
        incident_hash=incident_hash,
        incident_recorded=incident_recorded,
        incident_deduplicated=incident_deduplicated,
        as_of=as_of,
    )

    return CommandResult(
        command="reconcile",
        data=data,
        human_lines=human_lines,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Broker snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _BrokerSnapshot:
    connected: bool
    market_open: bool | None
    account: AccountInfo | None
    positions: tuple[Position, ...]
    open_orders: tuple[Order, ...]
    error: str | None

    def positions_by_symbol(self) -> dict[str, Position]:
        return {p.symbol: p for p in self.positions}

    def open_orders_by_id(self) -> dict[str, Order]:
        return {o.id: o for o in self.open_orders}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "connected": self.connected,
            "market_open": self.market_open,
            "error": self.error,
            "account": account_to_dict(self.account) if self.account is not None else None,
        }
        return payload


def _snapshot_broker(ctx: CommandContext) -> _BrokerSnapshot:
    try:
        broker = ctx.broker_factory()
        account = broker.get_account()
        positions = tuple(broker.get_positions())
        all_orders = broker.get_orders(status="open", limit=100)
        market_open = broker.is_market_open()
    except BrokerError as exc:
        return _BrokerSnapshot(
            connected=False,
            market_open=None,
            account=None,
            positions=(),
            open_orders=(),
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 — reconcile never crashes on broker I/O
        return _BrokerSnapshot(
            connected=False,
            market_open=None,
            account=None,
            positions=(),
            open_orders=(),
            error=str(exc),
        )

    open_orders = tuple(o for o in all_orders if _is_open_status(o.status))
    return _BrokerSnapshot(
        connected=True,
        market_open=bool(market_open),
        account=account,
        positions=positions,
        open_orders=open_orders,
        error=None,
    )


def _is_open_status(status: OrderStatus) -> bool:
    return status in {OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED}


# ---------------------------------------------------------------------------
# Local state projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LocalPosition:
    symbol: str
    quantity: float  # signed; BUY adds, SELL subtracts


# Trade statuses that indicate the submission was accepted by the broker and
# should therefore affect our view of intended position state. "rejected" and
# similar are ignored.
_POSITION_AFFECTING_STATUSES = frozenset({"submitted", "accepted", "filled"})

_OPEN_ORDER_STATUSES = frozenset({"submitted", "accepted"})


def _fold_positions(
    trades: list[TradeEvent],
    *,
    as_of: datetime,
) -> dict[str, _LocalPosition]:
    """Fold trade events by symbol into a signed-quantity local position map."""
    running: dict[str, float] = {}
    for trade in trades:
        if trade.source != "paper":
            continue
        if trade.status not in _POSITION_AFFECTING_STATUSES:
            continue
        if _aware(trade.recorded_at) > as_of:
            continue
        sign = 1.0 if trade.side.lower() == "buy" else -1.0
        running[trade.symbol] = running.get(trade.symbol, 0.0) + sign * trade.quantity
    return {
        symbol: _LocalPosition(symbol=symbol, quantity=qty)
        for symbol, qty in running.items()
        if qty != 0.0
    }


@dataclass(frozen=True)
class _LocalOpenOrder:
    broker_order_id: str
    symbol: str
    side: str
    quantity: float
    recorded_at: datetime


def _local_open_orders(
    trades: list[TradeEvent],
    *,
    as_of: datetime,
) -> dict[str, _LocalOpenOrder]:
    """Return local open orders keyed by broker_order_id."""
    result: dict[str, _LocalOpenOrder] = {}
    for trade in trades:
        if trade.source != "paper":
            continue
        if trade.broker_order_id is None:
            continue
        if _aware(trade.recorded_at) > as_of:
            continue
        if trade.status not in _OPEN_ORDER_STATUSES:
            continue
        result[trade.broker_order_id] = _LocalOpenOrder(
            broker_order_id=trade.broker_order_id,
            symbol=trade.symbol,
            side=trade.side,
            quantity=trade.quantity,
            recorded_at=_aware(trade.recorded_at),
        )
    return result


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PositionRow:
    symbol: str
    kind: str  # "ok" | "qty_mismatch" | "broker_only" | "local_only"
    local_qty: float | None
    broker_qty: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "kind": self.kind,
            "local_qty": self.local_qty,
            "broker_qty": self.broker_qty,
        }


@dataclass(frozen=True)
class _OrderRow:
    broker_order_id: str
    kind: str  # "ok" | "broker_only" | "local_only"
    symbol: str | None
    local: dict[str, Any] | None
    broker: dict[str, Any] | None
    incident: bool  # whether this row triggers an incident (vs. warning-only)

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker_order_id": self.broker_order_id,
            "kind": self.kind,
            "symbol": self.symbol,
            "local": self.local,
            "broker": self.broker,
            "incident": self.incident,
        }


def _compare_positions(
    *,
    local: dict[str, _LocalPosition],
    broker: dict[str, Position],
) -> list[_PositionRow]:
    rows: list[_PositionRow] = []
    for symbol in sorted(set(local) | set(broker)):
        l_pos = local.get(symbol)
        b_pos = broker.get(symbol)
        local_qty = l_pos.quantity if l_pos is not None else None
        broker_qty = b_pos.quantity if b_pos is not None else None
        if l_pos is None and b_pos is not None:
            kind = "broker_only"
        elif l_pos is not None and b_pos is None:
            kind = "local_only"
        elif local_qty == broker_qty:
            kind = "ok"
        else:
            kind = "qty_mismatch"
        rows.append(
            _PositionRow(
                symbol=symbol,
                kind=kind,
                local_qty=local_qty,
                broker_qty=broker_qty,
            )
        )
    return rows


def _compare_orders(
    *,
    local: dict[str, _LocalOpenOrder],
    broker: dict[str, Order],
    now: datetime,
) -> list[_OrderRow]:
    rows: list[_OrderRow] = []
    for order_id in sorted(set(local) | set(broker)):
        l_ord = local.get(order_id)
        b_ord = broker.get(order_id)
        if l_ord is not None and b_ord is not None:
            kind = "ok"
            incident = False
        elif b_ord is not None:
            kind = "broker_only"
            incident = True
        else:
            assert l_ord is not None  # mypy narrow
            kind = "local_only"
            age = now - l_ord.recorded_at
            incident = age <= _LOCAL_ONLY_INCIDENT_WINDOW

        rows.append(
            _OrderRow(
                broker_order_id=order_id,
                kind=kind,
                symbol=(b_ord.symbol if b_ord is not None else l_ord.symbol if l_ord else None),
                local=_local_order_to_dict(l_ord) if l_ord is not None else None,
                broker=_broker_order_to_dict(b_ord) if b_ord is not None else None,
                incident=incident,
            )
        )
    return rows


def _local_order_to_dict(order: _LocalOpenOrder) -> dict[str, Any]:
    return {
        "symbol": order.symbol,
        "side": order.side,
        "quantity": order.quantity,
        "recorded_at": order.recorded_at.isoformat(),
    }


def _broker_order_to_dict(order: Order) -> dict[str, Any]:
    return {
        "symbol": order.symbol,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "quantity": order.quantity,
        "status": order.status.value,
        "submitted_at": order.submitted_at.isoformat(),
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
    }


# ---------------------------------------------------------------------------
# Incident classification & dedupe
# ---------------------------------------------------------------------------


def _incident_reason_codes(
    position_rows: list[_PositionRow],
    order_rows: list[_OrderRow],
) -> list[str]:
    codes: set[str] = set()
    for row in position_rows:
        if row.kind == "qty_mismatch":
            codes.add("position_qty_mismatch")
        elif row.kind == "broker_only":
            codes.add("position_broker_only")
        elif row.kind == "local_only":
            codes.add("position_local_only")
    for row in order_rows:
        if not row.incident:
            continue
        if row.kind == "broker_only":
            codes.add("order_broker_only")
        elif row.kind == "local_only":
            codes.add("order_local_only_recent")
    return sorted(codes)


def _incident_content_hash(
    position_rows: list[_PositionRow],
    order_rows: list[_OrderRow],
) -> str:
    position_payload = sorted(
        (r.to_dict() for r in position_rows if r.kind != "ok"),
        key=lambda r: r["symbol"],
    )
    order_payload = sorted(
        (
            {
                "broker_order_id": r.broker_order_id,
                "kind": r.kind,
                "symbol": r.symbol,
            }
            for r in order_rows
            if r.incident
        ),
        key=lambda r: r["broker_order_id"],
    )
    payload = {"positions": position_payload, "orders": order_payload}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return digest


def _incident_already_logged(event_store: EventStore, content_hash: str) -> bool:
    """Return True if the latest ``reconcile_incident`` has this content hash.

    Idempotency keyed by ``config_hash`` column: per R-OPS-010, running
    reconcile twice against identical state must not duplicate the incident.
    We key on the *latest* incident rather than any historical match so a
    mismatch that reappears after being resolved is still logged.
    """
    explanations = event_store.list_explanations()
    for exp in reversed(explanations):
        if exp.decision_type == "reconcile_incident":
            return exp.config_hash == content_hash
    return False


def _incident_summary(
    position_rows: list[_PositionRow],
    order_rows: list[_OrderRow],
) -> str:
    position_mismatches = sum(1 for r in position_rows if r.kind != "ok")
    order_incidents = sum(1 for r in order_rows if r.incident)
    return (
        f"Reconciliation incident: {position_mismatches} position "
        f"mismatch(es), {order_incidents} order mismatch(es)."
    )


def _record_incident_event(
    event_store: EventStore,
    *,
    account: AccountInfo,
    market_open: bool,
    reason_codes: list[str],
    content_hash: str,
    summary: str,
    position_rows: list[_PositionRow],
    order_rows: list[_OrderRow],
    as_of: datetime,
) -> None:
    context = {
        "as_of": as_of.isoformat(),
        "positions": [r.to_dict() for r in position_rows if r.kind != "ok"],
        "orders": [r.to_dict() for r in order_rows if r.incident],
        "deferred_checks": list(_DEFERRED_CHECKS),
    }
    event = ExplanationEvent(
        recorded_at=datetime.now(tz=UTC),
        decision_type="reconcile_incident",
        status="incident",
        strategy_name=None,
        strategy_stage=None,
        strategy_config_path=None,
        config_hash=content_hash,
        symbol="SYSTEM",
        side="hold",
        quantity=0.0,
        order_type="none",
        time_in_force="day",
        submitted_by="reconcile",
        market_open=market_open,
        latest_bar_timestamp=None,
        latest_bar_close=None,
        account_equity=account.equity,
        account_cash=account.cash,
        account_portfolio_value=account.portfolio_value,
        account_daily_pnl=account.daily_pnl,
        risk_allowed=False,
        risk_summary=summary,
        reason_codes=list(reason_codes),
        risk_checks=[],
        context=context,
    )
    event_store.append_explanation(event)


# ---------------------------------------------------------------------------
# Warnings & human output
# ---------------------------------------------------------------------------


def _build_warnings(
    *,
    broker_snapshot: _BrokerSnapshot,
    position_rows: list[_PositionRow],
    order_rows: list[_OrderRow],
    incident_reasons: list[str],
) -> list[str]:
    warnings: list[str] = []
    if not broker_snapshot.connected:
        warnings.append(
            f"Broker unreachable: {broker_snapshot.error or 'unknown error'}. "
            "Reconciliation incomplete — no drift can be confirmed while the broker is down."
        )
    warnings.append("Deferred checks (R-OPS-004 v1.1): " + ", ".join(_DEFERRED_CHECKS))
    # Stale local_only orders → warning but no incident.
    for row in order_rows:
        if row.kind == "local_only" and not row.incident:
            warnings.append(
                f"Stale local-only order {row.broker_order_id} "
                f"({row.symbol}) is older than 24h and not present at the broker — "
                "likely already closed; not treated as an incident."
            )
    if incident_reasons:
        warnings.append(
            "Drift detected — per R-OPS-004 exposure-increasing submits should be "
            "refused until resolved. (Submit-gate wiring is a follow-up: operator "
            "must self-enforce for now.)"
        )
    return warnings


def _human_lines(
    *,
    broker_snapshot: _BrokerSnapshot,
    position_rows: list[_PositionRow],
    order_rows: list[_OrderRow],
    incident_reasons: list[str],
    incident_hash: str,
    incident_recorded: bool,
    incident_deduplicated: bool,
    as_of: datetime,
) -> list[str]:
    now_label = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines: list[str] = [
        f"Milodex Reconciliation — {now_label}",
        f"  as-of fold: {as_of.isoformat()}",
        "",
    ]

    if broker_snapshot.connected:
        market = "open" if broker_snapshot.market_open else "closed"
        lines.append(f"Broker:    connected  market: {market}")
        if broker_snapshot.account is not None:
            acc = broker_snapshot.account
            lines.append(
                f"Account:   equity {format_money(acc.equity)}   "
                f"cash {format_money(acc.cash)}   "
                f"buying power {format_money(acc.buying_power)}"
            )
    else:
        lines.append(f"Broker:    UNREACHABLE ({broker_snapshot.error or 'unknown'})")
    lines.append("")

    position_mismatch_count = sum(1 for r in position_rows if r.kind != "ok")
    lines.append(
        f"Positions ({position_mismatch_count} mismatch(es) of {len(position_rows)} symbol(s))"
    )
    if not position_rows:
        lines.append("  (none)")
    else:
        for row in position_rows:
            marker = "ok" if row.kind == "ok" else "**"
            local_label = "-" if row.local_qty is None else f"{row.local_qty:g}"
            broker_label = "-" if row.broker_qty is None else f"{row.broker_qty:g}"
            lines.append(
                f"  {marker}  {row.symbol:<6}  local {local_label:<8}  "
                f"broker {broker_label:<8}  kind: {row.kind}"
            )
    lines.append("")

    order_mismatch_count = sum(1 for r in order_rows if r.kind != "ok")
    lines.append(f"Open orders ({order_mismatch_count} mismatch(es) of {len(order_rows)} order(s))")
    if not order_rows:
        lines.append("  (none)")
    else:
        for row in order_rows:
            marker = "ok" if row.kind == "ok" else ("**" if row.incident else "~ ")
            lines.append(
                f"  {marker}  {row.broker_order_id}  symbol: {row.symbol or '-'}   kind: {row.kind}"
            )
    lines.append("")

    lines.append("Deferred checks (R-OPS-004 v1.1): " + ", ".join(_DEFERRED_CHECKS))
    lines.append("")

    if incident_reasons:
        if incident_recorded:
            lines.append(f"Result: DRIFT DETECTED — incident recorded (hash {incident_hash[:12]}).")
        elif incident_deduplicated:
            lines.append(
                f"Result: DRIFT DETECTED — matches prior incident "
                f"(hash {incident_hash[:12]}); not re-logged (R-OPS-010)."
            )
        else:
            # Broker unreachable or other suppression.
            lines.append("Result: DRIFT DETECTED — incident NOT recorded (broker unreachable).")
        lines.append("Per R-OPS-004, exposure-increasing submits should be refused until resolved.")
        lines.append("(Submit-gate wiring is not yet implemented — operator must self-enforce.)")
    else:
        lines.append("Result: CLEAN — broker and event store agree on all checked dimensions.")

    return lines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
