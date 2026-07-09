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

from milodex.core.event_store import (
    EventStore,
    ExecutionAttemptEvent,
    ExplanationEvent,
    TradeEvent,
)
from milodex.operations.reconciliation import (
    build_warnings,
    human_lines,
    incident_already_logged,
    incident_reason_codes,
    latest_readiness,
    local_open_orders_from_trades,
    run_reconciliation,
    stale_pending_attempt_warnings,
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


# ---------------------------------------------------------------------------
# sync_local_only_orders: record the broker's terminal status for local-only
# open orders so the next fold closes them. Implements the deferred
# canceled_since_last_sync / filled_since_last_sync (R-OPS-004). Mirrors
# resolve_position. See docs/incidents/2026-05-29-runner-fleet-oom-freeze.md.
# ---------------------------------------------------------------------------

import pytest  # noqa: E402

from milodex.broker import BrokerError  # noqa: E402
from milodex.broker.models import (  # noqa: E402
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from milodex.core.event_store import ReconciliationAdjustmentEvent  # noqa: E402
from milodex.operations.reconciliation import (  # noqa: E402
    SyncOrdersError,
    sync_local_only_orders,
)

_SYNC_NOW = datetime(2026, 5, 29, 18, 0, tzinfo=UTC)


class _OrderSyncBroker:
    """Connected broker with NO open orders/positions; get_order scripted per id."""

    def __init__(self, order_status=None, raise_ids=()):
        self._order_status = order_status or {}
        self._raise_ids = set(raise_ids)

    def get_account(self):
        return AccountInfo(
            equity=100_000.0,
            cash=100_000.0,
            buying_power=200_000.0,
            portfolio_value=100_000.0,
            daily_pnl=0.0,
        )

    def get_positions(self):
        return []

    def get_orders(self, status=None, limit=None):
        return []

    def is_market_open(self):
        return True

    def get_order(self, order_id):
        if order_id in self._raise_ids:
            raise BrokerError(f"order {order_id} not found")
        status = self._order_status.get(order_id, OrderStatus.CANCELLED)
        return Order(
            id=order_id,
            symbol="SPY",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=10.0,
            time_in_force=TimeInForce.DAY,
            status=status,
            submitted_at=_SYNC_NOW,
        )


def _seed_open_paper_order(
    store,
    *,
    broker_order_id,
    symbol="SPY",
    side="sell",
    quantity=10.0,
    status="submitted",
    strategy_name="s",
    session_id=None,
):
    """Append an explanation + a paper trade left open (locally open).

    ``status`` defaults to 'submitted' (the strategy-linked path). Passing
    'accepted' (also a member of OPEN_ORDER_STATUSES) seeds a locally-open
    order with no 'submitted' row of its own -- the operator-attributed /
    legacy-order fallback case for strategy-linkage lookup.
    """
    recorded_at = datetime(2026, 5, 28, 19, 0, tzinfo=UTC)
    eid = store.append_explanation(
        ExplanationEvent(
            **_incident_kwargs(
                decision_type="submit",
                status="submitted",
                strategy_name=strategy_name,
                strategy_stage="paper",
                symbol=symbol,
                side=side,
                quantity=quantity,
                order_type="market",
                submitted_by="operator",
            )
        )
    )
    store.append_trade(
        TradeEvent(
            explanation_id=eid,
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
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path="c.yaml",
            submitted_by="operator",
            broker_order_id=broker_order_id,
            broker_status="pending",
            message=None,
            session_id=session_id,
        )
    )


def _local_open_count(store):
    res = run_reconciliation(
        event_store=store, broker=_OrderSyncBroker(), persist=False, now=_SYNC_NOW
    )
    return sum(1 for r in res.order_rows if r.kind == "local_only")


def test_sync_records_cancelled_for_order_broker_no_longer_has(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-1")
    assert _local_open_count(store) == 1
    broker = _OrderSyncBroker(order_status={"ord-1": OrderStatus.CANCELLED})
    result = sync_local_only_orders(
        event_store=store, broker=broker, reason="close stale", now=_SYNC_NOW
    )
    assert len(result.synced) == 1
    assert result.synced[0].recorded_status == "cancelled"
    assert _local_open_count(store) == 0


def test_sync_records_filled_updates_position(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-1", side="buy", quantity=5.0)
    broker = _OrderSyncBroker(order_status={"ord-1": OrderStatus.FILLED})
    result = sync_local_only_orders(
        event_store=store, broker=broker, reason="sync fills", now=_SYNC_NOW
    )
    assert result.synced[0].recorded_status == "filled"
    assert result.synced[0].position_affecting is True
    res = run_reconciliation(
        event_store=store, broker=_OrderSyncBroker(), persist=False, now=_SYNC_NOW
    )
    assert any(r.symbol == "SPY" and r.local_qty == 5.0 for r in res.position_rows)


def test_sync_not_found_records_cancelled(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-1")
    broker = _OrderSyncBroker(raise_ids={"ord-1"})
    result = sync_local_only_orders(
        event_store=store, broker=broker, reason="purged", now=_SYNC_NOW
    )
    assert result.synced[0].recorded_status == "cancelled"
    assert result.synced[0].broker_status == "not_found"
    assert _local_open_count(store) == 0


def test_sync_skips_partially_filled_and_pending(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-pf")
    _seed_open_paper_order(store, broker_order_id="ord-pend")
    broker = _OrderSyncBroker(
        order_status={"ord-pf": OrderStatus.PARTIALLY_FILLED, "ord-pend": OrderStatus.PENDING}
    )
    result = sync_local_only_orders(event_store=store, broker=broker, reason="r", now=_SYNC_NOW)
    assert len(result.synced) == 0
    assert len(result.skipped) == 2
    assert _local_open_count(store) == 2


def test_sync_is_idempotent(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-1")
    broker = _OrderSyncBroker(order_status={"ord-1": OrderStatus.CANCELLED})
    sync_local_only_orders(event_store=store, broker=broker, reason="r", now=_SYNC_NOW)
    again = sync_local_only_orders(event_store=store, broker=broker, reason="r", now=_SYNC_NOW)
    assert len(again.synced) == 0


def test_sync_blocks_filled_when_offsetting_adjustment_exists(tmp_path):
    """A live offsetting adjustment + recording a fill would create a fresh
    qty_mismatch. The fill must be blocked, NOT recorded, and post-sync
    reconcile must stay clean."""
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-1", side="buy", quantity=10.0)
    store.append_reconciliation_adjustment(
        ReconciliationAdjustmentEvent(
            adjustment_id="adj-1",
            recorded_at=_SYNC_NOW,
            effective_at=_SYNC_NOW,
            approved_by="operator",
            symbol="SPY",
            local_qty_before=10.0,
            broker_qty=0.0,
            delta_qty=-10.0,
            reason="prior offset",
            source_incident_hash="h",
            context={},
        )
    )
    broker = _OrderSyncBroker(order_status={"ord-1": OrderStatus.FILLED})
    result = sync_local_only_orders(event_store=store, broker=broker, reason="r", now=_SYNC_NOW)
    assert all(s.recorded_status != "filled" for s in result.synced)
    assert result.adjustment_warnings
    res = run_reconciliation(
        event_store=store, broker=_OrderSyncBroker(), persist=False, now=_SYNC_NOW
    )
    assert not any(r.symbol == "SPY" and r.kind == "qty_mismatch" for r in res.position_rows)


def test_sync_requires_reason(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-1")
    with pytest.raises(SyncOrdersError):
        sync_local_only_orders(
            event_store=store, broker=_OrderSyncBroker(), reason="  ", now=_SYNC_NOW
        )


def test_sync_raises_when_broker_disconnected(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-1")

    class _DownBroker(_OrderSyncBroker):
        def get_account(self):
            raise BrokerError("down")

    with pytest.raises(SyncOrdersError):
        sync_local_only_orders(event_store=store, broker=_DownBroker(), reason="r", now=_SYNC_NOW)


def test_sync_single_order_by_id(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-1")
    _seed_open_paper_order(store, broker_order_id="ord-2")
    broker = _OrderSyncBroker(
        order_status={"ord-1": OrderStatus.CANCELLED, "ord-2": OrderStatus.CANCELLED}
    )
    result = sync_local_only_orders(
        event_store=store, broker=broker, reason="r", broker_order_id="ord-1", now=_SYNC_NOW
    )
    assert len(result.synced) == 1
    assert result.synced[0].broker_order_id == "ord-1"
    assert _local_open_count(store) == 1


def test_sync_inherits_strategy_linkage_from_submitted_row(tmp_path):
    """M1 retro item (c): a synced terminal row must inherit strategy_name,
    strategy_stage, strategy_config_path, and session_id from the original
    'submitted' trade row for the same broker_order_id, instead of the
    hardcoded strategy_name=None/strategy_stage='paper'/no session_id."""
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(
        store,
        broker_order_id="ord-1",
        strategy_name="momo.rule.v1",
        session_id="sess-abc",
    )
    broker = _OrderSyncBroker(order_status={"ord-1": OrderStatus.CANCELLED})
    result = sync_local_only_orders(
        event_store=store, broker=broker, reason="close stale", now=_SYNC_NOW
    )
    assert result.synced[0].recorded_status == "cancelled"
    terminal = next(
        t for t in store.list_trades() if t.broker_order_id == "ord-1" and t.status == "cancelled"
    )
    assert terminal.strategy_name == "momo.rule.v1"
    assert terminal.strategy_stage == "paper"
    assert terminal.strategy_config_path == "c.yaml"
    assert terminal.session_id == "sess-abc"


def test_sync_fallback_preserves_null_linkage_when_no_submitted_match(tmp_path):
    """A locally-open order with no 'submitted' row of its own (e.g. left in
    'accepted' -- also OPEN_ORDER_STATUSES) has no strategy row to inherit
    from; today's None/'paper'/no-session fallback behavior must be
    preserved (operator-attributed or legacy orders)."""
    store = EventStore(tmp_path / "m.db")
    _seed_open_paper_order(store, broker_order_id="ord-2", status="accepted")
    broker = _OrderSyncBroker(order_status={"ord-2": OrderStatus.CANCELLED})
    result = sync_local_only_orders(
        event_store=store, broker=broker, reason="close stale", now=_SYNC_NOW
    )
    assert result.synced[0].recorded_status == "cancelled"
    terminal = next(
        t for t in store.list_trades() if t.broker_order_id == "ord-2" and t.status == "cancelled"
    )
    assert terminal.strategy_name is None
    assert terminal.strategy_stage == "paper"
    assert terminal.strategy_config_path is None
    assert terminal.session_id is None


# ---------------------------------------------------------------------------
# Per-strategy ledger breakdown (ADR 0055) — warnings only, not incidents
# ---------------------------------------------------------------------------

_RSI2 = "meanrev.rsi2.spy_5min.v1"
_VWAP = "momentum.vwap_trend.spy_5min.v1"
_LEDGER_AS_OF = datetime(2026, 6, 3, 16, 0, tzinfo=UTC)


def _submitted_strategy_trade(
    store: EventStore,
    *,
    strategy_name: str,
    side: str,
    quantity: float,
    recorded_at: datetime,
) -> None:
    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type="submit",
            status="submitted",
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash=None,
            symbol="SPY",
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=recorded_at,
            latest_bar_close=590.0,
            account_equity=10_000.0,
            account_cash=10_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="OK",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id="ledger-divergence-session",
        )
    )
    store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=recorded_at,
            status="submitted",
            source="paper",
            symbol="SPY",
            side=side,
            quantity=quantity,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=590.0,
            estimated_order_value=quantity * 590.0,
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="strategy_runner",
            broker_order_id=None,
            broker_status=None,
            message=None,
        )
    )


class _BrokerFlatWithSpy:
    def get_account(self):
        return None

    def get_positions(self):
        return []

    def get_orders(self, status=None, limit=None):
        return []

    def is_market_open(self):
        return True


def test_per_strategy_ledger_divergence_warns_without_incident(tmp_path):
    """2026-06-03: per-strategy sum can diverge from broker net without arming R-OPS-004."""
    store = EventStore(tmp_path / "milodex.db")
    _submitted_strategy_trade(
        store,
        strategy_name=_RSI2,
        side="buy",
        quantity=13.0,
        recorded_at=datetime(2026, 6, 3, 14, 51, 7, tzinfo=UTC),
    )
    _submitted_strategy_trade(
        store,
        strategy_name=_VWAP,
        side="sell",
        quantity=13.0,
        recorded_at=datetime(2026, 6, 3, 14, 51, 12, tzinfo=UTC),
    )

    result = run_reconciliation(
        event_store=store,
        broker=_BrokerFlatWithSpy(),
        persist=True,
        now=_LEDGER_AS_OF,
    )

    assert result.incident_reason_codes == []
    assert incident_reason_codes(result.position_rows, result.order_rows) == []

    warnings = build_warnings(result, store)
    assert any("per-strategy ledger" in w.lower() for w in warnings)
    assert any(_RSI2 in w for w in warnings)
    assert any("SPY" in w for w in warnings)

    lines = human_lines(result, store)
    assert any("per-strategy ledger" in ln.lower() for ln in lines)

    readiness = latest_readiness(store, now=_LEDGER_AS_OF)
    assert readiness.ready is True


# ── P2-06: risk-profile file vs audit-trail divergence (informational) ────────


def test_risk_profile_divergence_warns_without_incident(tmp_path):
    """A hand-edited risk_profile.txt surfaces as a WARN line, never an incident."""
    from milodex.config import get_data_dir

    store = EventStore(tmp_path / "milodex.db")
    profile_file = get_data_dir() / "risk_profile.txt"
    profile_file.parent.mkdir(parents=True, exist_ok=True)
    profile_file.write_text("aggressive\n", encoding="utf-8")

    result = run_reconciliation(
        event_store=store,
        broker=_BrokerFlatWithSpy(),
        persist=True,
        now=_LEDGER_AS_OF,
    )

    assert result.incident_reason_codes == []

    warnings = build_warnings(result, store)
    assert any("risk-profile" in w.lower() and "aggressive" in w for w in warnings)

    lines = human_lines(result, store)
    assert any("Risk profile (informational, P2-06):" in ln for ln in lines)
    assert any("WARN" in ln and "aggressive" in ln for ln in lines)

    readiness = latest_readiness(store, now=_LEDGER_AS_OF)
    assert readiness.ready is True


def test_risk_profile_clean_emits_no_profile_warning(tmp_path):
    """No profile file + no audit history = the implicit default; no WARN line."""
    store = EventStore(tmp_path / "milodex.db")

    result = run_reconciliation(
        event_store=store,
        broker=_BrokerFlatWithSpy(),
        persist=True,
        now=_LEDGER_AS_OF,
    )

    warnings = build_warnings(result, store)
    assert not any("risk-profile" in w.lower() for w in warnings)
    lines = human_lines(result, store)
    assert not any("Risk profile (informational, P2-06):" in ln for ln in lines)


def test_stale_pending_attempt_surfaced_as_informational_warning(tmp_path):
    """P1-02: an execution attempt stuck 'pending' past the staleness window
    appears in build_warnings, but never arms R-OPS-004 incidents/readiness."""
    store = EventStore(tmp_path / "milodex.db")
    now = datetime.now(tz=UTC)
    store.append_execution_attempt(
        ExecutionAttemptEvent(
            client_order_id="coid-stale-1",
            symbol="SPY",
            side="buy",
            quantity=5.0,
            order_type="market",
            created_at=now - timedelta(minutes=30),
            status="pending",
        )
    )
    store.append_execution_attempt(
        ExecutionAttemptEvent(
            client_order_id="coid-fresh-1",
            symbol="SPY",
            side="buy",
            quantity=5.0,
            order_type="market",
            created_at=now,
            status="pending",
        )
    )

    attempt_warnings = stale_pending_attempt_warnings(store)
    assert len(attempt_warnings) == 1
    assert "coid-stale-1" in attempt_warnings[0]
    assert "informational only" in attempt_warnings[0]

    result = run_reconciliation(event_store=store, broker=_BrokerFlatWithSpy(), persist=False)
    warnings = build_warnings(result, store)
    assert any("coid-stale-1" in w for w in warnings)
    assert not any("coid-fresh-1" in w for w in warnings)
    # Informational only — no incident armed by a stale attempt.
    assert result.incident_reason_codes == []
