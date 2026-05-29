"""Unit tests for ``milodex.operations.reconciliation`` helpers.

Covers two unbounded-load fixes from the 2026-05-29 OOM-freeze incident
(`docs/incidents/2026-05-29-runner-fleet-oom-freeze.md`):

* ``incident_already_logged`` — the startup-reconciliation idempotency check
  every runner hits — must not materialize the entire ``explanations`` table.
* ``run_reconciliation`` must stream trades (``iter_trades``) rather than load
  the whole ``trades`` table twice via ``list_trades``.

Both fixes carry a regression guard that fails if the unbounded load returns.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.operations.reconciliation import (
    incident_already_logged,
    local_open_orders_from_trades,
    run_reconciliation,
)


def _incident_kwargs(**overrides) -> dict:
    """Minimal valid ``reconcile_incident`` ExplanationEvent payload."""
    recorded_at = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
    base = {
        "recorded_at": recorded_at,
        "decision_type": "reconcile_incident",
        "status": "incident",
        "strategy_name": None,
        "strategy_stage": None,
        "strategy_config_path": None,
        "config_hash": "hash-A",
        "symbol": "SYSTEM",
        "side": "hold",
        "quantity": 0.0,
        "order_type": "none",
        "time_in_force": "day",
        "submitted_by": "reconcile",
        "market_open": True,
        "latest_bar_timestamp": None,
        "latest_bar_close": None,
        "account_equity": 0.0,
        "account_cash": 0.0,
        "account_portfolio_value": 0.0,
        "account_daily_pnl": 0.0,
        "risk_allowed": False,
        "risk_summary": "incident",
        "reason_codes": [],
        "risk_checks": [],
        "context": {},
    }
    base.update(overrides)
    return base


def test_incident_already_logged_true_when_latest_matches(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_explanation(ExplanationEvent(**_incident_kwargs(config_hash="hash-A")))
    assert incident_already_logged(store, "hash-A") is True


def test_incident_already_logged_false_when_no_incident(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert incident_already_logged(store, "hash-A") is False


def test_incident_already_logged_uses_only_the_most_recent_incident(tmp_path):
    """Only the latest incident counts; an older matching hash must not."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_explanation(ExplanationEvent(**_incident_kwargs(config_hash="hash-A")))
    store.append_explanation(ExplanationEvent(**_incident_kwargs(config_hash="hash-B")))
    assert incident_already_logged(store, "hash-A") is False
    assert incident_already_logged(store, "hash-B") is True


def test_incident_already_logged_does_not_load_all_explanations(tmp_path, monkeypatch):
    """Regression guard for the 2026-05-29 OOM freeze: the startup idempotency
    check must NOT materialize the whole explanations table."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_explanation(ExplanationEvent(**_incident_kwargs(config_hash="hash-A")))

    def _boom(*_args, **_kwargs):
        raise AssertionError("incident_already_logged must not call list_explanations()")

    monkeypatch.setattr(store, "list_explanations", _boom)
    assert incident_already_logged(store, "hash-A") is True


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


# ---------------------------------------------------------------------------
# local_open_orders_from_trades: an order is open iff its LATEST status is open.
# A later terminal row (filled/canceled/...) must close it. Regression for the
# 2026-05-29 stale-order drift: the fold only ever added open orders and never
# removed them on terminal rows, so every submitted order stayed "locally open"
# forever (64 stale orders accumulated, arming the runner-start readiness veto).
# ---------------------------------------------------------------------------

_ORDER_BASE = datetime(2026, 5, 29, 14, 0, tzinfo=UTC)


def _order_trade(*, status: str, broker_order_id: str, seq: int) -> TradeEvent:
    """Build a paper order TradeEvent at a monotonic id/time (id-ASC == time-ASC)."""
    return TradeEvent(
        explanation_id=1,
        recorded_at=_ORDER_BASE + timedelta(seconds=seq),
        status=status,
        source="paper",
        symbol="SPY",
        side="buy",
        quantity=10.0,
        order_type="market",
        time_in_force="day",
        estimated_unit_price=400.0,
        estimated_order_value=4000.0,
        strategy_name="s",
        strategy_stage="paper",
        strategy_config_path="c.yaml",
        submitted_by="operator",
        broker_order_id=broker_order_id,
        broker_status=None,
        message=None,
        id=seq,
    )


_AS_OF = _ORDER_BASE + timedelta(hours=1)


def test_local_open_orders_submitted_only_stays_open():
    """A genuinely-open order (latest status submitted) is local-open."""
    trades = [_order_trade(status="submitted", broker_order_id="ord-1", seq=1)]
    result = local_open_orders_from_trades(trades, as_of=_AS_OF)
    assert "ord-1" in result


def test_local_open_orders_filled_closes_order():
    """submitted -> filled: the order is NOT local-open (latest status terminal)."""
    trades = [
        _order_trade(status="submitted", broker_order_id="ord-1", seq=1),
        _order_trade(status="filled", broker_order_id="ord-1", seq=2),
    ]
    result = local_open_orders_from_trades(trades, as_of=_AS_OF)
    assert "ord-1" not in result


def test_local_open_orders_canceled_closes_order():
    """submitted -> canceled: the order is NOT local-open."""
    trades = [
        _order_trade(status="submitted", broker_order_id="ord-1", seq=1),
        _order_trade(status="canceled", broker_order_id="ord-1", seq=2),
    ]
    result = local_open_orders_from_trades(trades, as_of=_AS_OF)
    assert "ord-1" not in result


def test_local_open_orders_accepted_stays_open():
    """submitted -> accepted: still open (accepted is an open status)."""
    trades = [
        _order_trade(status="submitted", broker_order_id="ord-1", seq=1),
        _order_trade(status="accepted", broker_order_id="ord-1", seq=2),
    ]
    result = local_open_orders_from_trades(trades, as_of=_AS_OF)
    assert "ord-1" in result


def test_local_open_orders_terminal_after_as_of_keeps_point_in_time_open():
    """A terminal row recorded AFTER as_of must not close the order at as_of."""
    trades = [
        _order_trade(status="submitted", broker_order_id="ord-1", seq=1),
        _order_trade(status="filled", broker_order_id="ord-1", seq=9999),
    ]
    # as_of between the two rows: only the submitted row is in-window.
    result = local_open_orders_from_trades(trades, as_of=_ORDER_BASE + timedelta(seconds=100))
    assert "ord-1" in result
