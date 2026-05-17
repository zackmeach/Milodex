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
from milodex.execution import (
    ExecutionService,
    ExecutionStatus,
    TradeIntent,
    UnsupportedOrderTypeError,
)
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


def test_preview_rejects_limit_orders_before_risk_evaluation(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    with pytest.raises(UnsupportedOrderTypeError) as exc_info:
        service.preview(
            TradeIntent(
                symbol="SPY",
                side=OrderSide.BUY,
                quantity=5,
                order_type=OrderType.LIMIT,
                limit_price=99.0,
            )
        )

    assert exc_info.value.order_type == OrderType.LIMIT
    assert exc_info.value.supported_order_types == (OrderType.MARKET,)
    assert "market orders only" in str(exc_info.value)
    assert broker.submit_calls == []


@pytest.mark.parametrize(
    ("order_type", "limit_price", "stop_price"),
    [
        (OrderType.LIMIT, 99.0, None),
        (OrderType.STOP, None, 101.0),
        (OrderType.STOP_LIMIT, 99.0, 101.0),
    ],
)
def test_submit_paper_rejects_non_market_orders_before_broker_submission(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    order_type,
    limit_price,
    stop_price,
):
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    with pytest.raises(UnsupportedOrderTypeError) as exc_info:
        service.submit_paper(
            TradeIntent(
                symbol="SPY",
                side=OrderSide.BUY,
                quantity=5,
                order_type=order_type,
                limit_price=limit_price,
                stop_price=stop_price,
            )
        )

    assert exc_info.value.order_type == order_type
    assert broker.submit_calls == []


def test_missing_limit_price_still_reports_unsupported_order_type_first(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    service, _ = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    with pytest.raises(UnsupportedOrderTypeError, match="market orders only"):
        service.preview(
            TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.LIMIT)
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
    service, broker = build_service(
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
    # Chokepoint invariant: a blocked submit must NEVER reach the broker.
    assert broker.submit_calls == []


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
    service, broker = build_service(
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
    # Chokepoint invariant: a blocked submit must NEVER reach the broker.
    assert broker.submit_calls == []


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

    assert "paper_mode_required" in result.risk_decision.reason_codes
    # Chokepoint invariant: a blocked submit must NEVER reach the broker.
    assert broker.submit_calls == []


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
    service, broker = build_service(
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
    # Chokepoint invariant: a blocked submit must NEVER reach the broker.
    assert broker.submit_calls == []


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
    # 13 checks = ADR 0024 baseline (12) + ADR 0029
    # strategy_concurrent_positions.
    assert len(explanations[0].risk_checks) == 13
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


# ---------------------------------------------------------------------------
# Chokepoint parametrized invariant — every block reason must never submit
# ---------------------------------------------------------------------------


def _make_stale_bar() -> Bar:
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(minutes=10),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )


def _make_recent_duplicate_order() -> Order:
    return Order(
        id="dup-order",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.PENDING,
        submitted_at=datetime.now(tz=UTC) - timedelta(seconds=30),
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "kill_switch",
        "market_closed",
        "stale_data",
        "paper_mode_required",
        "duplicate_order",
    ],
)
def test_any_block_reason_never_submits(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    monkeypatch,
    scenario,
):
    """Chokepoint invariant: for EVERY block reason, the broker receives zero
    submit calls.

    Meaningfulness verified: temporarily mutating the chokepoint in service.py
    to call ``self._broker.submit_order(...)`` unconditionally (before the
    ``if not result.risk_decision.allowed: return`` guard) causes this test to
    fail for all non-duplicate-order scenarios, since StubBroker.submit_order
    raises AssertionError when ``submit_order_result is None`` (the duplicate
    path sets it to None to provoke that path). The parametrize coverage is
    therefore genuine for every entry listed here.
    """
    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    if scenario == "kill_switch":
        # Trigger kill switch via daily-loss breach (same approach as
        # test_kill_switch_status_and_activation above).
        loss_account = AccountInfo(
            equity=10_000.0,
            cash=8_000.0,
            buying_power=8_000.0,
            portfolio_value=10_000.0,
            daily_pnl=-1_500.0,  # exceeds 10% max_drawdown_pct threshold
        )
        service, broker = build_service(
            tmp_path, risk_defaults_file, latest_bar, loss_account, submitted_order
        )
        result = service.submit_paper(intent)
        assert "kill_switch_threshold_breached" in result.risk_decision.reason_codes

    elif scenario == "market_closed":
        service, broker = build_service(
            tmp_path,
            risk_defaults_file,
            latest_bar,
            sample_account,
            submitted_order,
            market_open=False,
        )
        result = service.submit_paper(intent)
        assert "market_closed" in result.risk_decision.reason_codes

    elif scenario == "stale_data":
        service, broker = build_service(
            tmp_path,
            risk_defaults_file,
            _make_stale_bar(),
            sample_account,
            submitted_order,
        )
        result = service.submit_paper(intent)
        assert "stale_market_data" in result.risk_decision.reason_codes

    elif scenario == "paper_mode_required":
        monkeypatch.setattr("milodex.execution.service.get_trading_mode", lambda: "live")
        service, broker = build_service(
            tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
        )
        result = service.submit_paper(intent)
        assert "paper_mode_required" in result.risk_decision.reason_codes

    elif scenario == "duplicate_order":
        service, broker = build_service(
            tmp_path,
            risk_defaults_file,
            latest_bar,
            sample_account,
            submitted_order,
            orders=[_make_recent_duplicate_order()],
        )
        result = service.submit_paper(intent)
        assert "duplicate_order_window" in result.risk_decision.reason_codes

    # The universal chokepoint assertion: regardless of WHY we blocked, the
    # broker must have received zero submit calls.
    assert result.risk_decision.allowed is False
    assert broker.submit_calls == [], (
        f"Scenario '{scenario}': broker was called despite block — "
        "the execution chokepoint is broken."
    )


def test_frozen_manifest_hash_resolved_from_runner_bound_stage(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    monkeypatch,
):
    """The frozen-manifest lookup must key off the runner-bound expected_stage.

    Regression for the manifest-drift TOCTOU race. ``service.py`` resolves
    the frozen manifest hash via ``get_active_manifest_hash(name, stage,
    ...)``; the evaluator keys its drift exemption off
    ``intent.expected_stage``. If the on-disk YAML stage flips between
    reads (e.g. paper -> micro_live), a hash frozen for the *wrong* stage
    can satisfy a paper runner's drift check, partially defeating the
    ADR-0015 drift guarantee. Both sides of the drift comparison must use
    the same stage: the runner-bound ``expected_stage`` when present.

    Here the YAML stage is ``micro_live`` but the runner bound
    ``expected_stage="paper"`` at startup. The frozen-manifest resolver
    MUST be queried with ``"paper"`` (the runner-bound stage), not
    ``"micro_live"`` (the mutable YAML field).
    """
    strategy_path = tmp_path / "strategy_micro.yaml"
    strategy_path.write_text(
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
  stage: "micro_live"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
""".strip(),
        encoding="utf-8",
    )

    captured_stages: list[str] = []

    import milodex.promotion.manifest as manifest_module

    def _spy_get_active_manifest_hash(strategy_id, stage, event_store):
        captured_stages.append(stage)
        # Return a hash that matches the runtime config hash so the drift
        # check passes for the (correct) paper-stage lookup.
        from milodex.strategies.loader import compute_config_hash

        return compute_config_hash(strategy_path)

    monkeypatch.setattr(manifest_module, "get_active_manifest_hash", _spy_get_active_manifest_hash)

    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    intent = TradeIntent(
        symbol="SPY",
        side=OrderSide.BUY,
        quantity=5,
        order_type=OrderType.MARKET,
        strategy_config_path=strategy_path,
        expected_stage="paper",
    )
    result = service.submit_paper(intent)

    assert captured_stages, "frozen-manifest resolver was never called"
    assert captured_stages == ["paper"], (
        "frozen manifest hash must be resolved from the runner-bound "
        f"expected_stage ('paper'), not the mutable YAML stage; got {captured_stages}"
    )
    # Drift check passes because both sides used the paper stage.
    assert result.status == ExecutionStatus.SUBMITTED


def test_cancel_order_logs_broker_get_order_error(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    caplog,
):
    """A raising broker ``get_order`` during cancel must be logged, not swallowed.

    Regression for the silent ``except Exception: pass`` in
    ``cancel_order``. Broker errors during shutdown / kill-switch
    cancellation were hidden, returning ``(cancelled, None)`` with no
    trace. The return contract is unchanged — the order is still reported
    cancelled — but the swallowed exception must now be logged with
    context so the failure is auditable.
    """

    class _RaisingGetOrderBroker(StubBroker):
        def get_order(self, order_id: str) -> Order:
            raise RuntimeError("broker get_order exploded during cancel")

    broker = _RaisingGetOrderBroker(
        account=sample_account,
        orders=[submitted_order],
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

    import logging

    with caplog.at_level(logging.WARNING):
        cancelled, order = service.cancel_order(submitted_order.id)

    # Return contract preserved.
    assert cancelled is True
    assert order is None
    # The swallowed exception is now logged with context.
    assert any(
        "broker get_order exploded during cancel" in record.getMessage()
        or "broker get_order exploded during cancel" in str(record.exc_info)
        for record in caplog.records
    ), f"expected the broker error to be logged; got {[r.getMessage() for r in caplog.records]}"


def _seed_submitted_trade(
    event_store: EventStore,
    *,
    symbol: str,
    side: str,
    recorded_at: datetime,
) -> None:
    """Insert a paired explanation+submitted-trade row into the event store.

    Uses ``submitted_by="operator"`` so the dual-ancestor rule (migration
    008) does not require a ``strategy_runs`` row — a realistic
    operator-submitted prior order is a valid dedup case.
    """
    from milodex.core.event_store import ExplanationEvent, TradeEvent

    explanation_id = event_store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type="submit",
            status="submitted",
            strategy_name=None,
            strategy_stage=None,
            strategy_config_path=None,
            config_hash=None,
            symbol=symbol,
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="operator",
            market_open=True,
            latest_bar_timestamp=recorded_at,
            latest_bar_close=100.0,
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
    event_store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=recorded_at,
            status="submitted",
            source="paper",
            symbol=symbol,
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=100.0,
            strategy_name=None,
            strategy_stage=None,
            strategy_config_path=None,
            submitted_by="operator",
            broker_order_id="broker-seed-1",
            broker_status="filled",
            message=None,
        )
    )


def test_duplicate_order_detected_via_event_store_when_broker_fetch_truncated(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """A true duplicate beyond the broker's limit=100 fetch must still veto.

    Regression for the duplicate-order truncation hole. The dedup veto
    sourced ``recent_orders`` from ``broker.get_orders(limit=100)``. With
    more than 100 orders inside the dedup window, the matching prior order
    is truncated out of the broker fetch and the veto silently fails —
    exactly when order volume is high. The durable event store is the
    authoritative, untruncated trade/order history; the dedup window must
    be sourced from it.

    Here the broker returns 100 NON-matching orders (so the broker-only
    scan finds nothing), but the event store holds a real submitted
    SPY/BUY 30s ago — inside the 60s window. The SPY/BUY submit must be
    BLOCKED with ``duplicate_order_window``.
    """
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _seed_submitted_trade(
        event_store,
        symbol="SPY",
        side="buy",
        recorded_at=datetime.now(tz=UTC) - timedelta(seconds=30),
    )

    # 100 broker orders that do NOT match (different symbol) — the broker
    # truncated fetch yields zero SPY/BUY matches on its own.
    noise_orders = [
        Order(
            id=f"noise-{i}",
            symbol="QQQ",
            side=OrderSide.SELL,
            order_type=OrderType.MARKET,
            quantity=1.0,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.FILLED,
            submitted_at=datetime.now(tz=UTC) - timedelta(seconds=10),
        )
        for i in range(100)
    ]
    broker = StubBroker(
        account=sample_account,
        orders=noise_orders,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
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

    assert result.status == ExecutionStatus.BLOCKED
    assert "duplicate_order_window" in result.risk_decision.reason_codes
    assert broker.submit_calls == []
