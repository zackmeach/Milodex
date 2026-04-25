"""Tests for ``compute_ledger_positions`` and ``list_trades_for_strategy``.

See ADR 0021 for the rationale: strategies must derive their own open
positions from the trade ledger, not the shared broker account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.strategies.positions import compute_ledger_positions

STRATEGY_A = "regime.daily.sma200_rotation.spy_shy.v1"
STRATEGY_B = "meanrev.daily.pullback_rsi2.curated_largecap.v1"


@pytest.fixture
def event_store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "data" / "milodex.db")


def _seed(
    store: EventStore,
    *,
    strategy_name: str,
    symbol: str,
    side: str,
    quantity: float,
    status: str = "submitted",
    source: str = "paper",
) -> None:
    now = datetime.now(tz=UTC)
    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=now,
            decision_type="strategy_evaluate",
            status="approved",
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=f"configs/{strategy_name}.yaml",
            config_hash="hash",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=now,
            latest_bar_close=100.0,
            account_equity=10_000.0,
            account_cash=9_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="OK",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id="test-session",
        )
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=now,
            status=status,
            source=source,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=100.0 * quantity,
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=f"configs/{strategy_name}.yaml",
            submitted_by="strategy_runner",
            broker_order_id=None,
            broker_status=None,
            message=None,
        )
    )


def test_positions_isolate_by_strategy_name(event_store: EventStore) -> None:
    """The 2026-04-24 regression, codified: strategy B must see zero SPY
    even when strategy A has an open SPY position on the shared account.
    """
    _seed(event_store, strategy_name=STRATEGY_A, symbol="SPY", side="buy", quantity=13.0)

    assert compute_ledger_positions(event_store, STRATEGY_A) == {"SPY": 13.0}
    assert compute_ledger_positions(event_store, STRATEGY_B) == {}


def test_net_zero_positions_are_excluded(event_store: EventStore) -> None:
    """A fully-exited position (BUY qty == SELL qty) must not surface."""
    _seed(event_store, strategy_name=STRATEGY_A, symbol="SPY", side="buy", quantity=10.0)
    _seed(event_store, strategy_name=STRATEGY_A, symbol="SPY", side="sell", quantity=10.0)

    assert compute_ledger_positions(event_store, STRATEGY_A) == {}


def test_partial_exit_surfaces_remaining_quantity(event_store: EventStore) -> None:
    _seed(event_store, strategy_name=STRATEGY_A, symbol="SPY", side="buy", quantity=10.0)
    _seed(event_store, strategy_name=STRATEGY_A, symbol="SPY", side="sell", quantity=3.0)

    assert compute_ledger_positions(event_store, STRATEGY_A) == {"SPY": 7.0}


def test_non_submitted_statuses_are_ignored(event_store: EventStore) -> None:
    """blocked / preview / cancelled rows never reached the broker and
    must not contribute to a derived position.
    """
    _seed(event_store, strategy_name=STRATEGY_A, symbol="SPY", side="buy", quantity=5.0)
    _seed(
        event_store,
        strategy_name=STRATEGY_A,
        symbol="SPY",
        side="buy",
        quantity=999.0,
        status="blocked",
    )
    _seed(
        event_store,
        strategy_name=STRATEGY_A,
        symbol="SPY",
        side="buy",
        quantity=999.0,
        status="preview",
    )

    assert compute_ledger_positions(event_store, STRATEGY_A) == {"SPY": 5.0}


def test_backtest_trades_do_not_leak_into_paper_positions(event_store: EventStore) -> None:
    _seed(
        event_store,
        strategy_name=STRATEGY_A,
        symbol="SPY",
        side="buy",
        quantity=100.0,
        source="backtest",
    )

    assert compute_ledger_positions(event_store, STRATEGY_A) == {}


def test_list_trades_for_strategy_default_filters(event_store: EventStore) -> None:
    _seed(event_store, strategy_name=STRATEGY_A, symbol="SPY", side="buy", quantity=1.0)
    _seed(
        event_store,
        strategy_name=STRATEGY_A,
        symbol="SPY",
        side="buy",
        quantity=2.0,
        status="blocked",
    )
    _seed(
        event_store,
        strategy_name=STRATEGY_A,
        symbol="SPY",
        side="buy",
        quantity=3.0,
        source="backtest",
    )
    _seed(event_store, strategy_name=STRATEGY_B, symbol="AAPL", side="buy", quantity=4.0)

    trades = event_store.list_trades_for_strategy(STRATEGY_A)
    assert [t.quantity for t in trades] == [1.0]
