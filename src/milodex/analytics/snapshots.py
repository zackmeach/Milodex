"""Portfolio and backtest equity snapshot recorders (ADR 0053).

Two distinct tables, two distinct writers:

**Broker portfolio snapshots** (``portfolio_snapshots``)
    Broker-side account state only. One row per session end from the real
    Alpaca paper/live account. Written by
    ``milodex.strategies.runner.StrategyRunner.shutdown`` via
    :func:`record_daily_snapshot`. Do NOT write backtest data here.

**Backtest equity snapshots** (``backtest_equity_snapshots``)
    Simulated equity points from the backtest engine. One row per
    simulation end, scoped to a walk-forward window or whole-period run.
    Written by ``milodex.backtesting.engine.BacktestEngine._simulate``
    via :func:`record_backtest_equity_snapshot`.

Both call sites wrap the recorder in a defensive try/except so a failure
during snapshot write does not block the canonical session record.

Mixing these tables causes the ALL-PAPER trust metric to report nonsense
(the +9865% incident, ADR 0053 context). The table split is the
enforcement boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from milodex.core.event_store import BacktestEquitySnapshotEvent, PortfolioSnapshotEvent

if TYPE_CHECKING:
    from milodex.broker.client import BrokerClient
    from milodex.core.event_store import EventStore


@dataclass(frozen=True)
class PortfolioSnapshot:
    """In-memory view of a broker snapshot row, returned by record_daily_snapshot."""

    recorded_at: datetime
    session_id: str
    strategy_id: str
    equity: float
    cash: float
    portfolio_value: float
    daily_pnl: float
    positions: list[dict]


@dataclass(frozen=True)
class BacktestEquitySnapshot:
    """In-memory view of a backtest equity row, returned by record_backtest_equity_snapshot."""

    recorded_at: datetime
    session_id: str
    strategy_id: str
    equity: float
    cash: float
    portfolio_value: float
    daily_pnl: float | None
    positions: list[dict]
    backtest_run_id: int | None


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
    ts = recorded_at if recorded_at is not None else datetime.now(tz=UTC)

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


def record_backtest_equity_snapshot(
    event_store: EventStore,
    broker: BrokerClient,
    *,
    session_id: str,
    strategy_id: str,
    backtest_run_id: int | None = None,
    recorded_at: datetime | None = None,
) -> BacktestEquitySnapshot:
    """Capture simulated broker state and persist as a backtest equity snapshot row.

    Writes to ``backtest_equity_snapshots``, never ``portfolio_snapshots``
    (ADR 0053). Called by ``BacktestEngine._simulate`` at the end of each
    simulation window.

    Returns the :class:`BacktestEquitySnapshot` that was written.
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
    ts = recorded_at if recorded_at is not None else datetime.now(tz=UTC)

    event = BacktestEquitySnapshotEvent(
        recorded_at=ts,
        session_id=session_id,
        strategy_id=strategy_id,
        equity=account.equity,
        cash=account.cash,
        portfolio_value=account.portfolio_value,
        daily_pnl=None,  # backtests don't track daily PnL the same way
        positions=positions_dicts,
        backtest_run_id=backtest_run_id,
    )
    event_store.append_backtest_equity_snapshot(event)

    return BacktestEquitySnapshot(
        recorded_at=ts,
        session_id=session_id,
        strategy_id=strategy_id,
        equity=account.equity,
        cash=account.cash,
        portfolio_value=account.portfolio_value,
        daily_pnl=None,
        positions=positions_dicts,
        backtest_run_id=backtest_run_id,
    )
