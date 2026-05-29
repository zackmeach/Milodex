"""Tests for run_reconciliation's trade-loading path.

Guards the fix that swapped the two unbounded ``list_trades()`` loads in
``run_reconciliation`` for the streaming ``iter_trades()`` generator — the same
class of unbounded-load bug that OOM-froze the workstation via
``list_explanations`` (docs/incidents/2026-05-29-runner-fleet-oom-freeze.md).
"""

from __future__ import annotations

from datetime import UTC, datetime

from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.operations.reconciliation import run_reconciliation


class _FakeBroker:
    """Minimal broker: connected, market open, no positions or orders."""

    def get_account(self):
        return None

    def get_positions(self):
        return []

    def get_orders(self, status=None, limit=None):
        return []

    def is_market_open(self):
        return True


def _seed_paper_trade(
    store: EventStore,
    *,
    symbol: str = "SPY",
    side: str = "buy",
    quantity: float = 5.0,
    status: str = "filled",
) -> None:
    """Append an explanation + a paper trade so the fold has history to walk."""
    recorded_at = datetime(2026, 5, 7, 14, 0, tzinfo=UTC)
    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type="submit",
            status="submitted",
            strategy_name="regime.daily.sma200_rotation.spy_shy.v1",
            strategy_stage="paper",
            strategy_config_path="configs/regime.yaml",
            config_hash="abc",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            submitted_by="operator",
            market_open=True,
            latest_bar_timestamp=recorded_at,
            latest_bar_close=400.0,
            account_equity=10_000.0,
            account_cash=10_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="Allowed",
            reason_codes=[],
            risk_checks=[],
            context={},
        )
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=recorded_at,
            status=status,
            source="paper",
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=400.0,
            estimated_order_value=400.0 * quantity,
            strategy_name="regime.daily.sma200_rotation.spy_shy.v1",
            strategy_stage="paper",
            strategy_config_path="configs/regime.yaml",
            submitted_by="operator",
            broker_order_id=None,
            broker_status=None,
            message=None,
        )
    )


def test_run_reconciliation_does_not_load_all_trades(tmp_path, monkeypatch):
    """run_reconciliation must stream trades, never call the unbounded
    list_trades() full-table load. Regression guard for the OOM fix."""
    store = EventStore(tmp_path / "milodex.db")

    def _boom(*args, **kwargs):
        raise AssertionError("run_reconciliation must not call list_trades(); use iter_trades()")

    monkeypatch.setattr(store, "list_trades", _boom)

    # Must complete without touching list_trades().
    result = run_reconciliation(event_store=store, broker=_FakeBroker(), persist=False)
    assert result.broker.connected is True


def test_run_reconciliation_folds_paper_trade_into_local_position(tmp_path):
    """Characterization: a filled paper buy with no matching broker position
    surfaces as a local_only position mismatch. Pins fold behavior across the
    list_trades -> iter_trades swap."""
    store = EventStore(tmp_path / "milodex.db")
    _seed_paper_trade(store, symbol="SPY", side="buy", quantity=5.0, status="filled")

    result = run_reconciliation(event_store=store, broker=_FakeBroker(), persist=False)

    spy_rows = [r for r in result.position_rows if r.symbol == "SPY"]
    assert len(spy_rows) == 1
    assert spy_rows[0].kind == "local_only"
    assert spy_rows[0].local_qty == 5.0
    assert spy_rows[0].broker_qty is None
