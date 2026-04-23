"""Daily portfolio snapshot recorder.

Writes one ``portfolio_snapshots`` row per call, capturing broker-side
account state (equity, cash, positions). Callers invoke this at each
trading-day close; analytics reads the resulting history to assemble
trust reports and equity curves independent of the trade ledger.

Snapshot writes live behind this dedicated module (not inside
``ExecutionService``) because a snapshot is a *read* of broker state,
not a *write* of trade state — keeping the event-store writer surface
narrow per ADR 0011.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from milodex.core.event_store import PortfolioSnapshotEvent

if TYPE_CHECKING:
    from milodex.broker.client import BrokerClient
    from milodex.core.event_store import EventStore


@dataclass(frozen=True)
class PortfolioSnapshot:
    """In-memory view of a recorded snapshot, returned by the recorder."""

    recorded_at: datetime
    session_id: str
    strategy_id: str
    equity: float
    cash: float
    portfolio_value: float
    daily_pnl: float
    positions: list[dict]


def record_daily_snapshot(
    event_store: EventStore,
    broker: BrokerClient,
    *,
    session_id: str,
    strategy_id: str,
    recorded_at: datetime | None = None,
) -> PortfolioSnapshot:
    """Capture broker account + positions and persist as a snapshot row.

    Returns the :class:`PortfolioSnapshot` that was written.
    """
    account = broker.get_account()
    positions = broker.get_positions()
    positions_dicts = [
        {
            "symbol": p.symbol,
            "quantity": p.quantity,
            "avg_entry_price": p.avg_entry_price,
            "current_price": p.current_price,
            "market_value": p.market_value,
            "unrealized_pnl": p.unrealized_pnl,
            "unrealized_pnl_pct": p.unrealized_pnl_pct,
        }
        for p in positions
    ]
    ts = recorded_at if recorded_at is not None else datetime.now()

    event = PortfolioSnapshotEvent(
        recorded_at=ts,
        session_id=session_id,
        strategy_id=strategy_id,
        equity=account.equity,
        cash=account.cash,
        portfolio_value=account.portfolio_value,
        daily_pnl=account.daily_pnl,
        positions=positions_dicts,
    )
    event_store.append_portfolio_snapshot(event)

    return PortfolioSnapshot(
        recorded_at=ts,
        session_id=session_id,
        strategy_id=strategy_id,
        equity=account.equity,
        cash=account.cash,
        portfolio_value=account.portfolio_value,
        daily_pnl=account.daily_pnl,
        positions=positions_dicts,
    )
