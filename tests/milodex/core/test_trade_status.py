"""Contract tests for the shared trade-status vocabulary (P2-10).

``core/trade_status.py`` is the single home for the position-affecting
status set that risk/attribution.py and operations/reconciliation.py
previously each carried a private copy of. These tests pin (a) that both
consumers bind the *same object* — a re-divergence would have to delete
an import to sneak past — and (b) fold parity: given identical trades in
every status, the per-strategy attribution fold and the reconciliation
account fold count the same statuses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count

import pytest

from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.core.trade_status import POSITION_AFFECTING_STATUSES
from milodex.operations import reconciliation
from milodex.risk import attribution
from milodex.risk.attribution import strategy_positions

_NOW = datetime(2026, 6, 12, 18, 0, tzinfo=UTC)
_ORDER_IDS = count(1)


def test_attribution_constant_is_the_shared_object():
    assert attribution._POSITION_AFFECTING_STATUSES is POSITION_AFFECTING_STATUSES  # noqa: SLF001


def test_reconciliation_constant_is_the_shared_object():
    assert reconciliation.POSITION_AFFECTING_STATUSES is POSITION_AFFECTING_STATUSES


def test_shared_value_pins_the_fill_vocabulary():
    assert POSITION_AFFECTING_STATUSES == frozenset({"submitted", "accepted", "filled"})


# ---------------------------------------------------------------------------
# Fold parity
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "milodex.db")


def _record_trade(
    store: EventStore,
    *,
    side: str,
    quantity: float,
    status: str,
    symbol: str = "SPY",
    strategy_name: str = "regime",
) -> None:
    """Insert a paired explanation+trade row (one unique broker order each)."""
    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=_NOW,
            decision_type="submit",
            status=status,
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash=None,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=_NOW,
            latest_bar_close=100.0,
            account_equity=10_000.0,
            account_cash=10_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=status == "submitted",
            risk_summary="parity fixture",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id="test-trade-status-parity",
        )
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=_NOW,
            status=status,
            source="paper",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=quantity * 100.0,
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="strategy_runner",
            broker_order_id=f"broker-{next(_ORDER_IDS)}",
            broker_status=None,
            message=None,
        )
    )


def test_attribution_and_reconciliation_folds_count_the_same_statuses(store):
    """One trade per status; both folds must net to the same quantity.

    Position-affecting (submitted/accepted/filled) buys minus the
    submitted sell -> 10 + 4 + 2 - 6 = 10. The preview/blocked/cancelled
    rows are non-fills for both folds. (The fixture avoids corrective
    sync rows and net-short sequences, where the two folds intentionally
    diverge — see _fetch_submitted_trade_rows_for_strategy's docstring.)
    """
    _record_trade(store, side="buy", quantity=10, status="submitted")
    _record_trade(store, side="buy", quantity=4, status="filled")
    _record_trade(store, side="buy", quantity=2, status="accepted")
    _record_trade(store, side="buy", quantity=7, status="preview")
    _record_trade(store, side="buy", quantity=5, status="blocked")
    _record_trade(store, side="buy", quantity=3, status="cancelled")
    _record_trade(store, side="sell", quantity=6, status="submitted")

    per_strategy = strategy_positions("regime", store)
    assert per_strategy == {"SPY": 10.0}

    account_fold = reconciliation.fold_positions(store.iter_trades(), [], as_of=_NOW)
    assert {sym: pos.quantity for sym, pos in account_fold.items()} == {"SPY": 10.0}
