"""Tests for execution and risk services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.core.event_store import EventStore
from milodex.data.models import Bar
from milodex.execution import ExecutionService, ExecutionStatus, TradeIntent
from milodex.execution.state import KillSwitchStateStore


class StubBroker:
    """Simple broker stub for execution tests."""

    def __init__(
        self,
        *,
        account: AccountInfo,
        positions: list[Position] | None = None,
        orders: list[Order] | None = None,
        market_open: bool = True,
        submit_order: Order | None = None,
    ) -> None:
        self.account = account
        self.positions = positions or []
        self.orders = orders or []
        self.market_open = market_open
        self.submit_order_result = submit_order
        self.submit_calls: list[dict[str, object]] = []

    def get_account(self) -> AccountInfo:
        return self.account

    def get_positions(self) -> list[Position]:
        return list(self.positions)

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        return list(self.orders)[:limit]

    def is_market_open(self) -> bool:
        return self.market_open

    def submit_order(self, **kwargs) -> Order:
        self.submit_calls.append(kwargs)
        assert self.submit_order_result is not None
        return self.submit_order_result

    def get_order(self, order_id: str) -> Order:
        return next(order for order in self.orders if order.id == order_id)

    def cancel_order(self, order_id: str) -> bool:
        return any(order.id == order_id for order in self.orders)


class StubProvider:
    """Simple market data provider stub."""

    def __init__(self, latest_bar: Bar) -> None:
        self.latest_bar = latest_bar

    def get_latest_bar(self, symbol: str) -> Bar:
        return self.latest_bar


@pytest.fixture()
def risk_defaults_file(tmp_path: Path) -> Path:
    path = tmp_path / "risk_defaults.yaml"
    path.write_text(
        """
kill_switch:
  enabled: true
  max_drawdown_pct: 0.10
  require_manual_reset: true
portfolio:
  max_single_position_pct: 0.20
  max_concurrent_positions: 3
  max_total_exposure_pct: 0.80
daily_limits:
  max_daily_loss_pct: 0.03
  max_trades_per_day: 20
order_safety:
  max_order_value_pct: 0.15
  duplicate_order_window_seconds: 60
  max_data_staleness_seconds: 300
""".strip(),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def strategy_file(tmp_path: Path) -> Path:
    path = tmp_path / "strategy.yaml"
    path.write_text(
        """
strategy:
  name: "paper_momentum"
  version: 1
  description: "Test strategy"
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 2
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "paper"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
""".strip(),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def latest_bar() -> Bar:
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=30),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )


@pytest.fixture()
def sample_account() -> AccountInfo:
    return AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=50.0,
    )


@pytest.fixture()
def submitted_order() -> Order:
    return Order(
        id="order-paper-1",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=5.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.PENDING,
        submitted_at=datetime.now(tz=UTC),
    )


def build_service(
    tmp_path: Path,
    risk_defaults_file: Path,
    latest_bar: Bar,
    sample_account: AccountInfo,
    submitted_order: Order,
    *,
    positions: list[Position] | None = None,
    orders: list[Order] | None = None,
    market_open: bool = True,
) -> tuple[ExecutionService, StubBroker]:
    broker = StubBroker(
        account=sample_account,
        positions=positions,
        orders=orders,
        market_open=market_open,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    store = KillSwitchStateStore(tmp_path / "kill_switch.json")
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
    )
    return service, broker


def test_preview_does_not_submit(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    result = service.preview(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.PREVIEW
    assert result.risk_decision.allowed is True
    assert broker.submit_calls == []


def test_submit_paper_calls_broker(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.SUBMITTED
    assert result.order is not None
    assert broker.submit_calls


def test_limit_order_requires_limit_price(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    service, _ = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    with pytest.raises(ValueError, match="Limit price is required"):
        service.preview(
            TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.LIMIT)
        )


def test_stop_order_requires_stop_price(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    service, _ = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    with pytest.raises(ValueError, match="Stop price is required"):
        service.preview(
            TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.STOP)
        )


def test_duplicate_order_is_blocked(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    recent_order = Order(
        id="duplicate",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.PENDING,
        submitted_at=datetime.now(tz=UTC) - timedelta(seconds=30),
    )
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
        orders=[recent_order],
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.BLOCKED
    assert "duplicate_order_window" in result.risk_decision.reason_codes
    assert broker.submit_calls == []


def test_market_closed_blocks_submit_but_not_preview(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    service, _ = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
        market_open=False,
    )
    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    preview = service.preview(intent)
    submit = service.submit_paper(intent)

    assert preview.risk_decision.allowed is True
    assert submit.risk_decision.allowed is False
    assert "market_closed" in submit.risk_decision.reason_codes


def test_stale_data_is_blocked(tmp_path, risk_defaults_file, sample_account, submitted_order):
    stale_bar = Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(minutes=10),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )
    service, _ = build_service(
        tmp_path,
        risk_defaults_file,
        stale_bar,
        sample_account,
        submitted_order,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert "stale_market_data" in result.risk_decision.reason_codes


def test_strategy_stage_must_be_paper_or_backtest(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    strategy_file,
):
    updated_contents = strategy_file.read_text(encoding="utf-8").replace(
        'stage: "paper"',
        'stage: "live"',
    )
    strategy_file.write_text(updated_contents, encoding="utf-8")
    service, _ = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    result = service.preview(
        TradeIntent(
            symbol="SPY",
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.MARKET,
            strategy_config_path=strategy_file,
        )
    )

    assert "strategy_stage_ineligible" in result.risk_decision.reason_codes


def test_paper_submit_requires_paper_mode(
    tmp_path, risk_defaults_file, latest_bar, submitted_order, monkeypatch
):
    sample_account = AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=50.0,
    )
    monkeypatch.setattr("milodex.execution.service.get_trading_mode", lambda: "live")
    service, _ = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert "paper_mode_required" in result.risk_decision.reason_codes


def test_kill_switch_status_and_activation(
    tmp_path, risk_defaults_file, latest_bar, submitted_order
):
    account = AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=-1_500.0,
    )
    service, _ = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        account,
        submitted_order,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )
    state = service.get_kill_switch_state()

    assert "kill_switch_threshold_breached" in result.risk_decision.reason_codes
    assert state.active is True


def test_preview_and_submit_record_explanations_and_trades(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    broker = StubBroker(
        account=sample_account,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
    )
    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    preview = service.preview(intent)
    submit = service.submit_paper(intent)

    explanations = event_store.list_explanations()
    trades = event_store.list_trades()

    assert preview.status == ExecutionStatus.PREVIEW
    assert submit.status == ExecutionStatus.SUBMITTED
    assert [record.decision_type for record in explanations] == ["preview", "submit"]
    assert [record.status for record in trades] == ["preview", "submitted"]
    assert trades[0].explanation_id == explanations[0].id
    assert trades[1].explanation_id == explanations[1].id
    assert len(explanations[0].risk_checks) == 12
    assert explanations[1].risk_allowed is True


def test_strategy_config_hash_is_recorded_on_explanations(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    strategy_file,
):
    broker = StubBroker(
        account=sample_account,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
    )

    service.preview(
        TradeIntent(
            symbol="SPY",
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.MARKET,
            strategy_config_path=strategy_file,
        )
    )

    explanations = event_store.list_explanations()

    assert len(explanations) == 1
    assert explanations[0].config_hash is not None


def test_kill_switch_events_are_persisted_in_event_store(
    tmp_path, risk_defaults_file, latest_bar, submitted_order
):
    account = AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=-1_500.0,
    )
    broker = StubBroker(
        account=account,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert "kill_switch_threshold_breached" in result.risk_decision.reason_codes
    assert [event.event_type for event in event_store.list_kill_switch_events()] == ["activated"]


def test_submit_paper_records_runner_session_id(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    broker = StubBroker(
        account=sample_account,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
    )

    service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        session_id="session-123",
    )

    explanations = event_store.list_explanations()
    trades = event_store.list_trades()

    assert explanations[0].session_id == "session-123"
    assert trades[0].session_id == "session-123"


def test_submit_paper_records_pending_broker_status(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
):
    """A broker that returns a PENDING order (real Alpaca paper behavior) must
    have its status preserved verbatim in the trade event. Locks the
    contract that ExecutionService never silently rewrites broker status
    to 'filled' before the broker reports the actual fill."""
    pending_order = Order(
        id="order-pending-1",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=5.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.PENDING,
        submitted_at=datetime.now(tz=UTC),
    )
    broker = StubBroker(account=sample_account, submit_order=pending_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
    )

    assert result.status == ExecutionStatus.SUBMITTED
    assert result.order is not None
    assert result.order.status == OrderStatus.PENDING

    trades = event_store.list_trades()
    assert len(trades) == 1
    assert trades[0].broker_status == "pending"
    assert trades[0].broker_order_id == "order-pending-1"
