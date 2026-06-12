"""Shared broker/local reconciliation service for R-OPS-004."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from milodex.broker import BrokerError
from milodex.broker.models import AccountInfo, Order, OrderStatus, Position
from milodex.core.event_store import (
    EventStore,
    ExplanationEvent,
    ReconciliationAdjustmentEvent,
    ReconciliationRunEvent,
    TradeEvent,
)
from milodex.core.trade_status import POSITION_AFFECTING_STATUSES
from milodex.risk.attribution import strategy_positions
from milodex.risk.models import ReconciliationReadiness

ET_TZ = ZoneInfo("America/New_York")
CHECKED_DIMENSIONS_VERSION = "R-OPS-004.v1.1"
CHECKED_DIMENSIONS: tuple[str, ...] = (
    "positions",
    "open_orders",
    "account_snapshot",
    "order_ids",
    "halt_incident_status",
)
# scaffolded: deferred reconciliation checks (R-OPS-004 v1.2). Three of the
# eight OPERATIONS.md "State Reconciliation" dimensions are surfaced as
# warnings only and not yet enforced. See docs/OPERATIONS.md and
# docs/ENGINEERING_STANDARDS.md "Scaffolded Inventory".
DEFERRED_CHECKS: tuple[str, ...] = (
    "filled_since_last_sync",
    "canceled_since_last_sync",
    "strategy_linkage",
)
LOCAL_ONLY_INCIDENT_WINDOW = timedelta(hours=24)

# POSITION_AFFECTING_STATUSES is imported from core/trade_status.py — the
# shared home it splits with risk/attribution.py (P2-10). It remains
# re-exported from this module's namespace for existing callers.
OPEN_ORDER_STATUSES = frozenset({"submitted", "accepted"})


class ResolvePositionError(ValueError):
    """Raised when an audited position correction cannot be applied."""


@dataclass(frozen=True)
class BrokerSnapshot:
    connected: bool
    market_open: bool | None
    account: AccountInfo | None
    positions: tuple[Position, ...]
    open_orders: tuple[Order, ...]
    error: str | None

    def positions_by_symbol(self) -> dict[str, Position]:
        return {p.symbol.upper(): p for p in self.positions}

    def open_orders_by_id(self) -> dict[str, Order]:
        return {o.id: o for o in self.open_orders}

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "market_open": self.market_open,
            "error": self.error,
            "account": _account_to_dict(self.account) if self.account is not None else None,
        }


@dataclass(frozen=True)
class LocalPosition:
    symbol: str
    quantity: float


@dataclass(frozen=True)
class LocalOpenOrder:
    broker_order_id: str
    symbol: str
    side: str
    quantity: float
    recorded_at: datetime


@dataclass(frozen=True)
class PositionRow:
    symbol: str
    kind: str
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
class OrderRow:
    broker_order_id: str
    kind: str
    symbol: str | None
    local: dict[str, Any] | None
    broker: dict[str, Any] | None
    incident: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker_order_id": self.broker_order_id,
            "kind": self.kind,
            "symbol": self.symbol,
            "local": self.local,
            "broker": self.broker,
            "incident": self.incident,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    as_of: datetime
    recorded_at: datetime
    broker: BrokerSnapshot
    position_rows: list[PositionRow]
    order_rows: list[OrderRow]
    incident_reason_codes: list[str]
    incident_hash: str
    incident_recorded: bool
    incident_deduplicated: bool
    run_id: str
    run_db_id: int | None

    @property
    def status(self) -> str:
        if not self.broker.connected:
            return "incomplete"
        return "dirty" if self.incident_reason_codes else "clean"

    @property
    def reconciliation_clean(self) -> bool:
        return self.status == "clean"

    def to_dict(self) -> dict[str, Any]:
        data = {
            "as_of": self.as_of.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "local_trading_day": local_trading_day(self.recorded_at),
            "status": self.status,
            "broker": self.broker.to_dict(),
            "positions": {
                "ok": [r.to_dict() for r in self.position_rows if r.kind == "ok"],
                "mismatches": [r.to_dict() for r in self.position_rows if r.kind != "ok"],
            },
            "orders": {
                "ok": [r.to_dict() for r in self.order_rows if r.kind == "ok"],
                "mismatches": [r.to_dict() for r in self.order_rows if r.kind != "ok"],
            },
            "checked_dimensions_version": CHECKED_DIMENSIONS_VERSION,
            "checked_dimensions": list(CHECKED_DIMENSIONS),
            "deferred_checks": list(DEFERRED_CHECKS),
            "reconciliation_clean": self.reconciliation_clean,
            "incident_recorded": self.incident_recorded,
            "incident_deduplicated": self.incident_deduplicated,
            "incident_hash": self.incident_hash if self.incident_reason_codes else None,
            "incident_reason_codes": list(self.incident_reason_codes),
            "run_id": self.run_id,
            "run_db_id": self.run_db_id,
        }
        return data


def run_reconciliation(
    *,
    event_store: EventStore,
    broker: Any,
    as_of: datetime | None = None,
    persist: bool = True,
    now: datetime | None = None,
) -> ReconciliationResult:
    """Compare broker state with local durable state and optionally persist the run."""
    fold_as_of = _aware(as_of or datetime.now(tz=UTC))
    recorded_at = _aware(now or datetime.now(tz=UTC))
    broker_snapshot = snapshot_broker(broker)
    # Stream trades (iter_trades) rather than loading the whole table twice
    # (list_trades): the unbounded full-table load OOM-froze the workstation
    # when the runner fleet launched concurrently
    # (docs/incidents/2026-05-29-runner-fleet-oom-freeze.md). The folds below
    # are single-pass over id-ASC order, so a one-row-at-a-time generator is a
    # drop-in with identical semantics and flat memory.
    local_positions = fold_positions(
        event_store.iter_trades(),
        event_store.list_reconciliation_adjustments(),
        as_of=fold_as_of,
    )
    local_open_orders = local_open_orders_from_trades(event_store.iter_trades(), as_of=fold_as_of)

    position_rows = compare_positions(
        local=local_positions,
        broker=broker_snapshot.positions_by_symbol(),
    )
    order_rows = compare_orders(
        local=local_open_orders,
        broker=broker_snapshot.open_orders_by_id(),
        now=recorded_at,
    )
    incident_reasons = incident_reason_codes(position_rows, order_rows)
    incident_hash = incident_content_hash(position_rows, order_rows)

    incident_recorded = False
    incident_deduplicated = False
    if incident_reasons and broker_snapshot.connected:
        if incident_already_logged(event_store, incident_hash):
            incident_deduplicated = True
        else:
            record_incident_event(
                event_store,
                account=broker_snapshot.account,
                market_open=broker_snapshot.market_open or False,
                reason_codes=incident_reasons,
                content_hash=incident_hash,
                summary=incident_summary(position_rows, order_rows),
                position_rows=position_rows,
                order_rows=order_rows,
                as_of=fold_as_of,
                recorded_at=recorded_at,
            )
            incident_recorded = True

    result = ReconciliationResult(
        as_of=fold_as_of,
        recorded_at=recorded_at,
        broker=broker_snapshot,
        position_rows=position_rows,
        order_rows=order_rows,
        incident_reason_codes=incident_reasons,
        incident_hash=incident_hash,
        incident_recorded=incident_recorded,
        incident_deduplicated=incident_deduplicated,
        run_id=str(uuid4()),
        run_db_id=None,
    )
    if persist:
        db_id = event_store.append_reconciliation_run(_run_event_from_result(result))
        result = ReconciliationResult(
            as_of=result.as_of,
            recorded_at=result.recorded_at,
            broker=result.broker,
            position_rows=result.position_rows,
            order_rows=result.order_rows,
            incident_reason_codes=result.incident_reason_codes,
            incident_hash=result.incident_hash,
            incident_recorded=result.incident_recorded,
            incident_deduplicated=result.incident_deduplicated,
            run_id=result.run_id,
            run_db_id=db_id,
        )
    return result


def resolve_position(
    *,
    event_store: EventStore,
    broker: Any,
    symbol: str,
    reason: str,
    approved_by: str = "operator",
    as_of: datetime | None = None,
    now: datetime | None = None,
) -> ReconciliationAdjustmentEvent:
    """Append a compensating adjustment for a current local-only position drift."""
    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ResolvePositionError("symbol is required")
    if not reason or not reason.strip():
        raise ResolvePositionError("--reason is required for audited position resolution")

    current = run_reconciliation(
        event_store=event_store,
        broker=broker,
        as_of=as_of,
        persist=False,
        now=now,
    )
    if not current.broker.connected:
        raise ResolvePositionError("broker is unreachable; cannot compute live correction delta")
    row = next(
        (candidate for candidate in current.position_rows if candidate.symbol == normalized_symbol),
        None,
    )
    if row is None or row.kind == "ok":
        raise ResolvePositionError(f"{normalized_symbol} has no current position mismatch")
    if row.kind != "local_only":
        raise ResolvePositionError(
            f"{normalized_symbol} mismatch kind is {row.kind}; resolve-position only handles "
            "local-only position drift"
        )
    if current.incident_hash is None or not current.incident_reason_codes:
        raise ResolvePositionError("no active incident hash is available for this mismatch")

    broker_qty = float(row.broker_qty or 0.0)
    local_qty = float(row.local_qty or 0.0)
    delta_qty = broker_qty - local_qty
    if delta_qty == 0.0:
        raise ResolvePositionError(f"{normalized_symbol} correction delta is zero")

    recorded_at = _aware(now or datetime.now(tz=UTC))
    adjustment = ReconciliationAdjustmentEvent(
        adjustment_id=str(uuid4()),
        recorded_at=recorded_at,
        effective_at=recorded_at,
        approved_by=approved_by,
        symbol=normalized_symbol,
        local_qty_before=local_qty,
        broker_qty=broker_qty,
        delta_qty=delta_qty,
        reason=reason.strip(),
        source_incident_hash=current.incident_hash,
        context={
            "as_of": current.as_of.isoformat(),
            "position_row": row.to_dict(),
            "incident_reason_codes": list(current.incident_reason_codes),
        },
    )
    db_id = event_store.append_reconciliation_adjustment(adjustment)
    return ReconciliationAdjustmentEvent(
        id=db_id,
        adjustment_id=adjustment.adjustment_id,
        recorded_at=adjustment.recorded_at,
        effective_at=adjustment.effective_at,
        approved_by=adjustment.approved_by,
        symbol=adjustment.symbol,
        local_qty_before=adjustment.local_qty_before,
        broker_qty=adjustment.broker_qty,
        delta_qty=adjustment.delta_qty,
        reason=adjustment.reason,
        source_incident_hash=adjustment.source_incident_hash,
        context=adjustment.context,
    )


class SyncOrdersError(ValueError):
    """Raised when an audited order-status sync cannot be performed."""


@dataclass(frozen=True)
class SyncedOrder:
    broker_order_id: str
    symbol: str
    recorded_status: str
    broker_status: str
    position_affecting: bool


@dataclass(frozen=True)
class SyncOrdersResult:
    explanation_id: int | None
    synced: list[SyncedOrder]
    skipped: list[SyncedOrder]
    adjustment_warnings: list[str]


# Broker terminal OrderStatus -> recorded local trades.status. A status absent
# here (PENDING / PARTIALLY_FILLED) is still live at the broker => skip, do not
# force-close.
_BROKER_TERMINAL_TO_LOCAL: dict[OrderStatus, str] = {
    OrderStatus.FILLED: "filled",  # position-affecting (correctly moves the position)
    OrderStatus.CANCELLED: "cancelled",
    OrderStatus.REJECTED: "rejected",
}


def sync_local_only_orders(
    *,
    event_store: EventStore,
    broker: Any,
    reason: str,
    approved_by: str = "operator",
    broker_order_id: str | None = None,
    as_of: datetime | None = None,
    now: datetime | None = None,
) -> SyncOrdersResult:
    """Record the broker's terminal status for local-only open orders.

    For each paper order that is locally "open" but not open at the broker,
    query the broker's actual terminal status and append a corrective terminal
    ``TradeEvent`` so the order fold (``local_open_orders_from_trades``) closes
    it. Implements the R-OPS-004 deferred ``canceled``/``filled``-since-last-sync
    dimensions as an explicit, audited operator action.

    This NEVER goes through the risk evaluator — it records observed broker
    truth, it does not submit a new trade (``append_trade`` sits below
    ``ExecutionService``). See docs/incidents/2026-05-29-runner-fleet-oom-freeze.md.
    """
    if not reason or not reason.strip():
        raise SyncOrdersError("--reason is required for an audited order-status sync")
    target = broker_order_id.strip() if broker_order_id else None

    current = run_reconciliation(
        event_store=event_store, broker=broker, as_of=as_of, persist=False, now=now
    )
    if not current.broker.connected:
        raise SyncOrdersError("broker is unreachable; cannot sync order status")

    candidates = [r for r in current.order_rows if r.kind == "local_only"]
    if target is not None:
        candidates = [r for r in candidates if r.broker_order_id == target]
        if not candidates:
            raise SyncOrdersError(
                f"order {target} is not a current local-only order; nothing to sync"
            )

    def _net_adjustment(symbol: str) -> float:
        return sum(a.delta_qty for a in event_store.list_reconciliation_adjustments(symbol=symbol))

    recorded_at = _aware(now or datetime.now(tz=UTC))
    synced: list[SyncedOrder] = []
    skipped: list[SyncedOrder] = []
    adjustment_warnings: list[str] = []
    to_append: list[tuple[OrderRow, str, str]] = []  # (row, recorded_status, broker_status)

    for row in candidates:
        oid = row.broker_order_id
        local = row.local or {}
        symbol = (row.symbol or local.get("symbol") or "").upper()
        try:
            order = broker.get_order(oid)
            broker_status = order.status.value
            recorded = _BROKER_TERMINAL_TO_LOCAL.get(order.status)
        except BrokerError:
            # Broker has no record of the order (purged) => not open. Best-effort
            # 'cancelled'; if it had actually filled, the post-sync reconcile
            # re-flags it as a position_broker_only mismatch (the backstop).
            broker_status = "not_found"
            recorded = "cancelled"

        if recorded is None:  # PENDING / PARTIALLY_FILLED: still live at broker
            skipped.append(SyncedOrder(oid, symbol, "skipped", broker_status, False))
            continue

        position_affecting = recorded in POSITION_AFFECTING_STATUSES
        if position_affecting and _net_adjustment(symbol) != 0.0:
            adjustment_warnings.append(
                f"{symbol} order {oid}: broker reports FILLED, but a live reconciliation "
                f"adjustment still offsets {symbol}. Recording the fill now would create a "
                "fresh position mismatch. Retire the adjustment "
                "(`milodex reconcile resolve-position`) first, then re-run sync-orders. Skipped."
            )
            skipped.append(SyncedOrder(oid, symbol, "blocked_adjustment", broker_status, True))
            continue

        synced.append(SyncedOrder(oid, symbol, recorded, broker_status, position_affecting))
        to_append.append((row, recorded, broker_status))

    explanation_id: int | None = None
    if to_append:
        account = current.broker.account
        explanation_id = event_store.append_explanation(
            ExplanationEvent(
                recorded_at=recorded_at,
                decision_type="reconcile_order_sync",
                status="sync",
                strategy_name=None,
                strategy_stage=None,
                strategy_config_path=None,
                config_hash=None,
                symbol="SYSTEM",
                side="hold",
                quantity=0.0,
                order_type="none",
                time_in_force="day",
                submitted_by="reconcile",
                market_open=current.broker.market_open or False,
                latest_bar_timestamp=None,
                latest_bar_close=None,
                account_equity=0.0 if account is None else account.equity,
                account_cash=0.0 if account is None else account.cash,
                account_portfolio_value=0.0 if account is None else account.portfolio_value,
                account_daily_pnl=0.0 if account is None else account.daily_pnl,
                risk_allowed=False,
                risk_summary=f"Order-status sync: {len(synced)} order(s) closed from broker truth.",
                reason_codes=[],
                risk_checks=[],
                context={
                    "reason": reason.strip(),
                    "approved_by": approved_by,
                    "as_of": current.as_of.isoformat(),
                    "synced": [asdict(s) for s in synced],
                },
            )
        )
        for row, recorded, broker_status in to_append:
            local = row.local or {}
            event_store.append_trade(
                TradeEvent(
                    explanation_id=explanation_id,
                    recorded_at=recorded_at,
                    status=recorded,
                    source="paper",
                    symbol=(row.symbol or local.get("symbol") or "").upper(),
                    side=str(local.get("side", "buy")),
                    quantity=float(local.get("quantity", 0.0)),
                    order_type="market",
                    time_in_force="day",
                    estimated_unit_price=0.0,
                    estimated_order_value=0.0,
                    strategy_name=None,
                    strategy_stage="paper",
                    strategy_config_path=None,
                    submitted_by="reconcile",
                    broker_order_id=row.broker_order_id,
                    broker_status=broker_status,
                    message=f"order-status sync: {reason.strip()}",
                )
            )

    return SyncOrdersResult(
        explanation_id=explanation_id,
        synced=synced,
        skipped=skipped,
        adjustment_warnings=adjustment_warnings,
    )


def latest_readiness(
    event_store: EventStore,
    *,
    now: datetime | None = None,
) -> ReconciliationReadiness:
    """Return the risk-layer readiness verdict for the latest persisted run."""
    current = _aware(now or datetime.now(tz=UTC))
    latest = event_store.get_latest_reconciliation_run()
    today = local_trading_day(current)
    if latest is None:
        return ReconciliationReadiness(
            ready=False,
            reason_code="reconciliation_required",
            message="No durable reconciliation run has been recorded today.",
            context={"today": today},
        )
    context: dict[str, object] = {
        "run_id": latest.run_id,
        "status": latest.status,
        "local_trading_day": latest.local_trading_day,
        "today": today,
        "reason_codes": list(latest.reason_codes),
    }
    if not latest.broker_connected or latest.status == "incomplete":
        return ReconciliationReadiness(
            ready=False,
            reason_code="reconciliation_incomplete",
            message="Latest reconciliation is incomplete because broker state was unavailable.",
            recorded_at=latest.recorded_at,
            local_trading_day=latest.local_trading_day,
            status=latest.status,
            broker_connected=latest.broker_connected,
            incident_hash=latest.incident_hash,
            context=context,
        )
    if latest.status == "dirty":
        return ReconciliationReadiness(
            ready=False,
            reason_code="reconciliation_drift",
            message="Latest reconciliation detected broker/local drift.",
            recorded_at=latest.recorded_at,
            local_trading_day=latest.local_trading_day,
            status=latest.status,
            broker_connected=latest.broker_connected,
            incident_hash=latest.incident_hash,
            context=context,
        )
    if latest.local_trading_day != today:
        return ReconciliationReadiness(
            ready=False,
            reason_code="reconciliation_stale",
            message="Latest clean reconciliation is stale for the current New York trading day.",
            recorded_at=latest.recorded_at,
            local_trading_day=latest.local_trading_day,
            status=latest.status,
            broker_connected=latest.broker_connected,
            incident_hash=latest.incident_hash,
            context=context,
        )
    return ReconciliationReadiness(
        ready=True,
        reason_code=None,
        message="Latest clean reconciliation is current for today's New York date.",
        recorded_at=latest.recorded_at,
        local_trading_day=latest.local_trading_day,
        status=latest.status,
        broker_connected=latest.broker_connected,
        incident_hash=latest.incident_hash,
        context=context,
    )


def snapshot_broker(broker: Any) -> BrokerSnapshot:
    try:
        account = broker.get_account()
        positions = tuple(broker.get_positions())
        all_orders = broker.get_orders(status="open", limit=100)
        market_open = broker.is_market_open()
    except BrokerError as exc:
        return BrokerSnapshot(False, None, None, (), (), str(exc))
    except Exception as exc:  # noqa: BLE001 - reconcile must persist incomplete state
        return BrokerSnapshot(False, None, None, (), (), str(exc))

    return BrokerSnapshot(
        connected=True,
        market_open=bool(market_open),
        account=account,
        positions=positions,
        open_orders=tuple(o for o in all_orders if _is_open_status(o.status)),
        error=None,
    )


def fold_positions(
    trades: Iterable[TradeEvent],
    adjustments: list[ReconciliationAdjustmentEvent],
    *,
    as_of: datetime,
) -> dict[str, LocalPosition]:
    running: dict[str, float] = {}
    # Latest in-window status per broker_order_id wins (trades are id-ASC) — the
    # position twin of local_open_orders_from_trades. A later terminal row
    # reverses an order's optimistic 'submitted' contribution: submitted->filled
    # counts once, submitted->cancelled counts zero. Without this, the order-sync
    # (sync_local_only_orders) appending a terminal row would double-count against
    # the original submitted row. Rows without a broker_order_id (legacy / non-order
    # rows) are counted individually as before.
    latest_by_order: dict[str, TradeEvent] = {}

    def _add(trade: TradeEvent) -> None:
        if trade.status not in POSITION_AFFECTING_STATUSES:
            return
        sign = 1.0 if trade.side.lower() == "buy" else -1.0
        symbol = trade.symbol.upper()
        running[symbol] = running.get(symbol, 0.0) + sign * trade.quantity

    for trade in trades:
        if trade.source != "paper":
            continue
        if _aware(trade.recorded_at) > as_of:
            continue
        if trade.broker_order_id is None:
            _add(trade)
        else:
            latest_by_order[trade.broker_order_id] = trade
    for trade in latest_by_order.values():
        _add(trade)
    for adjustment in adjustments:
        if _aware(adjustment.effective_at) > as_of:
            continue
        symbol = adjustment.symbol.upper()
        running[symbol] = running.get(symbol, 0.0) + adjustment.delta_qty
    return {
        symbol: LocalPosition(symbol=symbol, quantity=qty)
        for symbol, qty in running.items()
        if qty != 0.0
    }


def local_open_orders_from_trades(
    trades: Iterable[TradeEvent],
    *,
    as_of: datetime,
) -> dict[str, LocalOpenOrder]:
    result: dict[str, LocalOpenOrder] = {}
    for trade in trades:
        if trade.source != "paper" or trade.broker_order_id is None:
            continue
        if _aware(trade.recorded_at) > as_of:
            continue
        # Latest in-window status per broker_order_id wins (trades are id-ASC).
        # An order is locally open only if its most-recent status is still open;
        # a later terminal row (filled/canceled/expired/rejected) closes it. The
        # prior code only ever added open orders and skipped terminal rows, so it
        # never removed a closed order — every submitted order stayed "locally
        # open" forever, accumulating drift that armed the runner-start readiness
        # veto (docs/incidents/2026-05-29-runner-fleet-oom-freeze.md follow-up).
        if trade.status in OPEN_ORDER_STATUSES:
            result[trade.broker_order_id] = LocalOpenOrder(
                broker_order_id=trade.broker_order_id,
                symbol=trade.symbol.upper(),
                side=trade.side,
                quantity=trade.quantity,
                recorded_at=_aware(trade.recorded_at),
            )
        else:
            result.pop(trade.broker_order_id, None)
    return result


def compare_positions(
    *,
    local: dict[str, LocalPosition],
    broker: dict[str, Position],
) -> list[PositionRow]:
    rows: list[PositionRow] = []
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
        rows.append(PositionRow(symbol, kind, local_qty, broker_qty))
    return rows


def compare_orders(
    *,
    local: dict[str, LocalOpenOrder],
    broker: dict[str, Order],
    now: datetime,
) -> list[OrderRow]:
    rows: list[OrderRow] = []
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
            assert l_ord is not None
            kind = "local_only"
            incident = (now - l_ord.recorded_at) <= LOCAL_ONLY_INCIDENT_WINDOW
        rows.append(
            OrderRow(
                broker_order_id=order_id,
                kind=kind,
                symbol=(b_ord.symbol.upper() if b_ord is not None else l_ord.symbol),
                local=_local_order_to_dict(l_ord) if l_ord is not None else None,
                broker=_broker_order_to_dict(b_ord) if b_ord is not None else None,
                incident=incident,
            )
        )
    return rows


def incident_reason_codes(
    position_rows: list[PositionRow],
    order_rows: list[OrderRow],
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


def incident_content_hash(
    position_rows: list[PositionRow],
    order_rows: list[OrderRow],
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
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def incident_already_logged(event_store: EventStore, content_hash: str) -> bool:
    # Compare against the most recent reconcile_incident only (matching the prior
    # reversed-scan semantics). A targeted single-row query is used instead of
    # loading every explanation: the full-table load OOM-froze the workstation
    # when the runner fleet launched concurrently
    # (docs/incidents/2026-05-29-runner-fleet-oom-freeze.md).
    latest_hash = event_store.latest_reconcile_incident_hash()
    return latest_hash is not None and latest_hash == content_hash


def incident_summary(
    position_rows: list[PositionRow],
    order_rows: list[OrderRow],
) -> str:
    position_mismatches = sum(1 for r in position_rows if r.kind != "ok")
    order_incidents = sum(1 for r in order_rows if r.incident)
    return (
        f"Reconciliation incident: {position_mismatches} position "
        f"mismatch(es), {order_incidents} order mismatch(es)."
    )


def record_incident_event(
    event_store: EventStore,
    *,
    account: AccountInfo | None,
    market_open: bool,
    reason_codes: list[str],
    content_hash: str,
    summary: str,
    position_rows: list[PositionRow],
    order_rows: list[OrderRow],
    as_of: datetime,
    recorded_at: datetime,
) -> None:
    context = {
        "as_of": as_of.isoformat(),
        "positions": [r.to_dict() for r in position_rows if r.kind != "ok"],
        "orders": [r.to_dict() for r in order_rows if r.incident],
        "deferred_checks": list(DEFERRED_CHECKS),
    }
    event_store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
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
            account_equity=0.0 if account is None else account.equity,
            account_cash=0.0 if account is None else account.cash,
            account_portfolio_value=0.0 if account is None else account.portfolio_value,
            account_daily_pnl=0.0 if account is None else account.daily_pnl,
            risk_allowed=False,
            risk_summary=summary,
            reason_codes=list(reason_codes),
            risk_checks=[],
            context=context,
        )
    )


def build_warnings(result: ReconciliationResult, event_store: EventStore) -> list[str]:
    warnings: list[str] = []
    if not result.broker.connected:
        warnings.append(
            f"Broker unreachable: {result.broker.error or 'unknown error'}. "
            "Reconciliation incomplete - no drift can be confirmed while the broker is down."
        )
    warnings.append("Deferred checks (R-OPS-004 v1.1): " + ", ".join(DEFERRED_CHECKS))
    for row in result.order_rows:
        if row.kind == "local_only" and not row.incident:
            warnings.append(
                f"Stale local-only order {row.broker_order_id} "
                f"({row.symbol}) is older than 24h and not present at the broker - "
                "likely already closed; not treated as an incident."
            )
    if result.incident_reason_codes:
        warnings.append(
            "Drift detected - R-OPS-004 now blocks exposure-increasing paper previews "
            "and submits until reconciliation is clean for the current New York date."
        )
    warnings.extend(stale_pending_attempt_warnings(event_store))
    warnings.extend(risk_profile_audit_warnings(event_store))
    return warnings


def stale_pending_attempt_warnings(event_store: EventStore) -> list[str]:
    """Informational WARN for execution attempts stuck at 'pending' (P1-02).

    A 'pending' outbox row older than the staleness threshold means a process
    died between the pre-submit outbox write and the broker call/finalize —
    the order may or may not exist at the broker. Informational only (does
    not feed ``incident_reason_codes`` or readiness): the attempt already
    counts toward the duplicate-order veto, and the operator can match the
    ``client_order_id`` exactly against the broker's order list.
    """
    return [
        f"Execution attempt {attempt.client_order_id} ({attempt.symbol} "
        f"{attempt.side} x{attempt.quantity:g}) has been 'pending' since "
        f"{attempt.created_at.isoformat()} - likely crash between outbox write "
        "and broker submit/finalize. Verify against the broker's order list "
        "(client_order_id matches exactly); informational only."
        for attempt in event_store.list_stale_pending_execution_attempts()
    ]


def risk_profile_audit_warnings(event_store: EventStore) -> list[str]:
    """Informational WARN when ``data/risk_profile.txt`` diverges from the audit trail.

    P2-06: a hand-edit of the profile file changes runtime behavior with no
    ``risk_profile_changes`` row. Does not feed ``incident_reason_codes`` or
    readiness — surfaced exactly like the per-strategy ledger WARNs above.
    """
    # Imported lazily: profile_activation imports execution.state, whose package
    # __init__ pulls ExecutionService, which imports this module. A module-level
    # import here would close that cycle.
    from milodex.risk.profile_activation import reconcile_profile_against_audit

    divergence = reconcile_profile_against_audit(
        event_store._path  # noqa: SLF001 — same private-access seam as _connect (ADR 0029)
    )
    return [] if divergence is None else [divergence.message]


def per_strategy_ledger_warnings(
    event_store: EventStore,
    result: ReconciliationResult,
) -> list[str]:
    """Informational WARN when per-strategy ledgers sum != broker net (ADR 0055).

      Does not feed ``incident_reason_codes`` or readiness — concurrent same-symbol
    trading can legitimately diverge (e.g. rsi2 +13 / broker flat).
    """
    if not result.broker.connected:
        return []

    broker_by_symbol = {
        position.symbol.upper(): float(position.quantity) for position in result.broker.positions
    }
    strategy_ids = _distinct_strategy_ids_with_submitted_trades(event_store)
    ledger_by_strategy = {
        strategy_id: strategy_positions(strategy_id, event_store) for strategy_id in strategy_ids
    }
    symbols: set[str] = set(broker_by_symbol)
    for positions in ledger_by_strategy.values():
        symbols.update(positions)

    warnings: list[str] = []
    epsilon = 1e-6
    for symbol in sorted(symbols):
        broker_qty = broker_by_symbol.get(symbol, 0.0)
        breakdown = {
            strategy_id: positions[symbol]
            for strategy_id, positions in ledger_by_strategy.items()
            if positions.get(symbol, 0.0) > 0
        }
        ledger_sum = sum(breakdown.values())
        if abs(ledger_sum - broker_qty) <= epsilon:
            continue
        parts = ", ".join(
            f"{strategy_id}={qty:g}" for strategy_id, qty in sorted(breakdown.items())
        )
        warnings.append(
            f"Per-strategy ledger divergence on {symbol}: broker net {broker_qty:g}, "
            f"sum of strategy ledgers {ledger_sum:g} ({parts}). "
            "Expected during concurrent same-symbol trading (ADR 0055); informational only."
        )
    return warnings


def _distinct_strategy_ids_with_submitted_trades(event_store: EventStore) -> list[str]:
    with event_store._connect() as connection:  # noqa: SLF001 — ADR 0029 pattern
        rows = connection.execute(
            """
            SELECT DISTINCT strategy_name
            FROM trades
            WHERE status = 'submitted' AND strategy_name IS NOT NULL
            ORDER BY strategy_name ASC
            """
        ).fetchall()
    return [str(row["strategy_name"]) for row in rows]


def human_lines(result: ReconciliationResult, event_store: EventStore) -> list[str]:
    now_label = result.recorded_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"Milodex Reconciliation - {now_label}",
        f"  as-of fold: {result.as_of.isoformat()}",
        f"  NY trading day: {local_trading_day(result.recorded_at)}",
        "",
    ]
    if result.broker.connected:
        market = "open" if result.broker.market_open else "closed"
        lines.append(f"Broker:    connected  market: {market}")
        if result.broker.account is not None:
            acc = result.broker.account
            lines.append(
                f"Account:   equity {_format_money(acc.equity)}   "
                f"cash {_format_money(acc.cash)}   "
                f"buying power {_format_money(acc.buying_power)}"
            )
    else:
        lines.append(f"Broker:    UNREACHABLE ({result.broker.error or 'unknown'})")
    lines.append("")
    position_mismatch_count = sum(1 for r in result.position_rows if r.kind != "ok")
    lines.append(
        f"Positions ({position_mismatch_count} mismatch(es) of "
        f"{len(result.position_rows)} symbol(s))"
    )
    if not result.position_rows:
        lines.append("  (none)")
    else:
        for row in result.position_rows:
            marker = "ok" if row.kind == "ok" else "**"
            local_label = "-" if row.local_qty is None else f"{row.local_qty:g}"
            broker_label = "-" if row.broker_qty is None else f"{row.broker_qty:g}"
            lines.append(
                f"  {marker}  {row.symbol:<6}  local {local_label:<8}  "
                f"broker {broker_label:<8}  kind: {row.kind}"
            )
    ledger_warnings = per_strategy_ledger_warnings(event_store, result)
    if ledger_warnings:
        lines.append("")
        lines.append("Per-strategy ledger (informational, ADR 0055):")
        for warning in ledger_warnings:
            lines.append(f"  WARN  {warning}")
    profile_warnings = risk_profile_audit_warnings(event_store)
    if profile_warnings:
        lines.append("")
        lines.append("Risk profile (informational, P2-06):")
        for warning in profile_warnings:
            lines.append(f"  WARN  {warning}")
    lines.append("")
    order_mismatch_count = sum(1 for r in result.order_rows if r.kind != "ok")
    lines.append(
        f"Open orders ({order_mismatch_count} mismatch(es) of {len(result.order_rows)} order(s))"
    )
    if not result.order_rows:
        lines.append("  (none)")
    else:
        for row in result.order_rows:
            marker = "ok" if row.kind == "ok" else ("**" if row.incident else "~ ")
            lines.append(
                f"  {marker}  {row.broker_order_id}  symbol: {row.symbol or '-'}   kind: {row.kind}"
            )
    lines.append("")
    lines.append("Deferred checks (R-OPS-004 v1.1): " + ", ".join(DEFERRED_CHECKS))
    lines.append("")
    if result.incident_reason_codes:
        if result.incident_recorded:
            lines.append(
                f"Result: DRIFT DETECTED - incident recorded (hash {result.incident_hash[:12]})."
            )
        elif result.incident_deduplicated:
            lines.append(
                f"Result: DRIFT DETECTED - matches prior incident "
                f"(hash {result.incident_hash[:12]}); not re-logged (R-OPS-010)."
            )
        else:
            lines.append("Result: DRIFT DETECTED - incident NOT recorded (broker unreachable).")
        lines.append(
            "Exposure-increasing paper previews/submits are blocked until a "
            "current-day clean reconciliation is recorded."
        )
    elif result.status == "incomplete":
        lines.append("Result: INCOMPLETE - broker state unavailable; readiness fails closed.")
    else:
        lines.append("Result: CLEAN - broker and event store agree on all checked dimensions.")
    return lines


def local_trading_day(value: datetime) -> str:
    return _aware(value).astimezone(ET_TZ).date().isoformat()


def _run_event_from_result(result: ReconciliationResult) -> ReconciliationRunEvent:
    return ReconciliationRunEvent(
        run_id=result.run_id,
        recorded_at=result.recorded_at,
        as_of=result.as_of,
        local_trading_day=local_trading_day(result.recorded_at),
        status=result.status,
        broker_connected=result.broker.connected,
        market_open=result.broker.market_open,
        checked_dimensions_version=CHECKED_DIMENSIONS_VERSION,
        checked_dimensions=list(CHECKED_DIMENSIONS),
        deferred_checks=list(DEFERRED_CHECKS),
        incident_hash=result.incident_hash if result.incident_reason_codes else None,
        incident_recorded=result.incident_recorded,
        incident_deduplicated=result.incident_deduplicated,
        reason_codes=list(result.incident_reason_codes),
        summary={
            "position_mismatches": [
                row.to_dict() for row in result.position_rows if row.kind != "ok"
            ],
            "order_mismatches": [row.to_dict() for row in result.order_rows if row.kind != "ok"],
            "broker": result.broker.to_dict(),
        },
    )


def _is_open_status(status: OrderStatus) -> bool:
    return status in {OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED}


def _local_order_to_dict(order: LocalOpenOrder) -> dict[str, Any]:
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


def _account_to_dict(account: AccountInfo) -> dict[str, Any]:
    return {
        "equity": account.equity,
        "cash": account.cash,
        "buying_power": account.buying_power,
        "portfolio_value": account.portfolio_value,
        "daily_pnl": account.daily_pnl,
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _format_money(value: float) -> str:
    return f"${value:,.2f}"
