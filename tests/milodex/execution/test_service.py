"""Tests for execution and risk services."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from milodex.broker.exceptions import InsufficientFundsError, OrderRejectedError
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.core.advisory_lock import AdvisoryLock
from milodex.core.event_store import (
    EventStore,
    ExecutionAttemptEvent,
    ReconciliationRunEvent,
)
from milodex.data.models import Bar
from milodex.execution import (
    ExecutionService,
    ExecutionStatus,
    TradeIntent,
    UnsupportedOrderTypeError,
)
from milodex.execution.state import KillSwitchStateStore
from tests.milodex.core.conftest import _append_queued_intent


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
        self.cancel_all_calls = 0

    def get_account(self) -> AccountInfo:
        return self.account

    def get_positions(self) -> list[Position]:
        return list(self.positions)

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        return list(self.orders)[:limit]

    def is_market_open(self) -> bool:
        return self.market_open

    def latest_completed_session(self, now: datetime) -> date:
        # Test double: the latest completed session is "today" so the 1D
        # staleness gate treats the fresh (today-dated) ``latest_bar`` fixture
        # as current. Tests exercising staleness BLOCK paths supply their own
        # bar/now; this default keeps the non-staleness submit tests green.
        return now.date()

    def submit_order(self, **kwargs) -> Order:
        self.submit_calls.append(kwargs)
        assert self.submit_order_result is not None
        return self.submit_order_result

    def get_order(self, order_id: str) -> Order:
        return next(order for order in self.orders if order.id == order_id)

    def cancel_order(self, order_id: str) -> bool:
        return any(order.id == order_id for order in self.orders)

    def cancel_all_orders(self) -> list[Order]:
        self.cancel_all_calls += 1
        return []


class RejectingStubBroker(StubBroker):
    """Broker stub that raises on submit_order."""

    def __init__(self, rejection: Exception, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rejection = rejection

    def submit_order(self, **kwargs) -> Order:
        self.submit_calls.append(kwargs)
        raise self._rejection


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
    locks_dir: Path | None = None,
    submit_lock_timeout_seconds: float | None = None,
) -> tuple[ExecutionService, StubBroker]:
    broker = StubBroker(
        account=sample_account,
        positions=positions,
        orders=orders,
        market_open=market_open,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    extra: dict[str, object] = {}
    if locks_dir is not None:
        extra["locks_dir"] = locks_dir
    if submit_lock_timeout_seconds is not None:
        extra["submit_lock_timeout_seconds"] = submit_lock_timeout_seconds
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
        **extra,
    )
    return service, broker


def _append_clean_reconciliation_run(event_store: EventStore, *, when: datetime | None = None):
    _append_reconciliation_run(event_store, status="clean", when=when)


def _append_reconciliation_run(
    event_store: EventStore,
    *,
    status: str,
    when: datetime | None = None,
    broker_connected: bool = True,
    reason_codes: list[str] | None = None,
):
    recorded_at = when or datetime.now(tz=UTC)
    event_store.append_reconciliation_run(
        ReconciliationRunEvent(
            run_id=f"test-reconcile-{recorded_at.timestamp()}",
            recorded_at=recorded_at,
            as_of=recorded_at,
            local_trading_day=recorded_at.astimezone(ZoneInfo("America/New_York"))
            .date()
            .isoformat(),
            status=status,
            broker_connected=broker_connected,
            market_open=True,
            checked_dimensions_version="R-OPS-004.v1.1",
            checked_dimensions=["positions", "open_orders"],
            deferred_checks=[
                "filled_since_last_sync",
                "canceled_since_last_sync",
                "strategy_linkage",
            ],
            incident_hash="test-incident-hash" if reason_codes else None,
            incident_recorded=False,
            incident_deduplicated=False,
            reason_codes=reason_codes or [],
            summary={},
        )
    )


def test_preview_does_not_submit(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """R-EXE-003: preview() runs evaluation and never reaches the broker."""
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


def test_submit_paper_records_broker_order_rejection_without_raising(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    rejection = OrderRejectedError("potential wash trade detected ... 40310000")
    broker = RejectingStubBroker(
        rejection,
        account=sample_account,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.REJECTED
    assert result.risk_decision.allowed is True
    assert result.order is None
    assert "wash trade" in (result.message or "").lower()
    explanations = event_store.list_explanations()
    assert len(explanations) == 1
    assert explanations[0].status == ExecutionStatus.REJECTED.value
    assert explanations[0].decision_type == "submit"
    trades = event_store.list_trades()
    assert len(trades) == 1
    assert trades[0].status == ExecutionStatus.REJECTED.value


def test_submit_paper_records_insufficient_funds_rejection_without_raising(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    broker = RejectingStubBroker(
        InsufficientFundsError("insufficient buying power"),
        account=sample_account,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.REJECTED
    assert result.risk_decision.allowed is True
    assert event_store.list_explanations()[0].status == ExecutionStatus.REJECTED.value


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
    """R-BRK-007: non-market orders raise UnsupportedOrderTypeError before the broker call."""
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
    # PR5: the duplicate veto is now durable + per-strategy (the account-scoped
    # broker recent_orders path was removed). An operator intent dedups against
    # operator-attributed history, so seed a recent operator SPY BUY.
    service, broker, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )
    _seed_submitted_trade(
        event_store,
        symbol="SPY",
        side="buy",
        recorded_at=datetime.now(tz=UTC) - timedelta(seconds=30),
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


def test_strategy_stage_must_be_paper(
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


def test_backtest_stage_cannot_submit_paper_orders(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    strategy_file,
):
    """SRS R-PRM-002 / review P1-01: stage ``backtest`` is not in
    ``ALLOWED_STAGES_BY_MODE['paper']`` — the risk layer must refuse the
    submission even though the CLI launch gate would normally have caught
    it (the risk layer is the final arbiter, not a duplicate of the CLI)."""
    updated_contents = strategy_file.read_text(encoding="utf-8").replace(
        'stage: "paper"',
        'stage: "backtest"',
    )
    strategy_file.write_text(updated_contents, encoding="utf-8")
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
        strategy_config_path=strategy_file,
    )

    preview = service.preview(intent)
    submit = service.submit_paper(intent)

    # Preview mirrors submit: stage eligibility does not depend on whether
    # the order is actually sent.
    assert "strategy_stage_ineligible" in preview.risk_decision.reason_codes
    assert submit.risk_decision.allowed is False
    assert "strategy_stage_ineligible" in submit.risk_decision.reason_codes
    # Chokepoint invariant: a blocked submit must NEVER reach the broker.
    assert broker.submit_calls == []


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
    """R-EXE-005: daily-loss breach halts submission and cancels open orders."""
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
    # R-P2-5: the risk-triggered activation path cancels open orders, same as
    # the operator SIGINT path — "halt all trading" includes resting orders.
    assert broker.cancel_all_calls == 1


def test_risk_triggered_kill_switch_activates_even_when_cancel_fails(
    tmp_path, risk_defaults_file, latest_bar, submitted_order
):
    # R-P2-5 fail-safe posture: a broker cancel failure must NEVER block kill
    # switch activation. The switch engages regardless; the failure is logged.
    class CancelFailingBroker(StubBroker):
        def cancel_all_orders(self) -> list[Order]:
            self.cancel_all_calls += 1
            raise RuntimeError("broker connection lost")

    account = AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=-1_500.0,
    )
    broker = CancelFailingBroker(account=account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert "kill_switch_threshold_breached" in result.risk_decision.reason_codes
    assert broker.cancel_all_calls == 1
    assert service.get_kill_switch_state().active is True


def test_risk_triggered_kill_switch_no_duplicate_cancel_when_already_active(
    tmp_path, risk_defaults_file, latest_bar, submitted_order
):
    """HR-13 item 9: _maybe_activate_kill_switch must be a no-op when the switch is already active.

    Pre-fix: every blocked submit after the first threshold breach would re-call
    cancel_all_orders() and re-write the activated event (sustained-breach
    repeat-cancel on every blocked submit).  The fix returns early when the store
    already shows active=True.
    """
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
    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    # First submit trips the threshold and activates.
    result1 = service.submit_paper(intent)
    assert "kill_switch_threshold_breached" in result1.risk_decision.reason_codes
    assert service.get_kill_switch_state().active is True
    assert broker.cancel_all_calls == 1

    # Subsequent submits while already-active must NOT re-call cancel_all_orders.
    service.submit_paper(intent)
    service.submit_paper(intent)
    assert broker.cancel_all_calls == 1, (
        "cancel_all_orders must not be called again after the kill switch is already active"
    )


def test_halt_trading_cancels_orders_then_activates(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    """halt_trading cancels resting orders BEFORE the durable kill-switch flip.

    ADR 0005 Addendum point 2: one halt, one meaning — cancel-then-flip. The
    probe captures the switch state at the moment cancel runs; it must still be
    inactive, proving cancel precedes activation.
    """

    class OrderProbingBroker(StubBroker):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.active_at_cancel: bool | None = None
            self.state_getter = None

        def cancel_all_orders(self) -> list[Order]:
            self.cancel_all_calls += 1
            if self.state_getter is not None:
                self.active_at_cancel = self.state_getter().active
            return []

    broker = OrderProbingBroker(account=sample_account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(event_store=event_store, legacy_path=tmp_path / "ks.json")
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
    )
    broker.state_getter = service.get_kill_switch_state

    outcome = service.halt_trading("operator manual trip")

    assert broker.cancel_all_calls == 1
    assert broker.active_at_cancel is False, "cancel must run before the switch activates"
    assert service.get_kill_switch_state().active is True
    assert outcome.orders_cancelled is True
    assert outcome.cancel_error is None


def test_halt_trading_activates_even_when_cancel_fails(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    """A broker cancel failure NEVER blocks the halt — the switch engages regardless."""

    class CancelFailingBroker(StubBroker):
        def cancel_all_orders(self) -> list[Order]:
            self.cancel_all_calls += 1
            raise RuntimeError("broker connection lost")

    broker = CancelFailingBroker(account=sample_account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(event_store=event_store, legacy_path=tmp_path / "ks.json")
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
    )

    outcome = service.halt_trading("operator manual trip")

    assert broker.cancel_all_calls == 1
    assert service.get_kill_switch_state().active is True
    assert outcome.orders_cancelled is False
    assert "broker connection lost" in (outcome.cancel_error or "")


def test_halt_trading_records_reason_and_counts_as_activation(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    """A manual trip lands as an ``activated`` event carrying the operator reason.

    The provenance rides the ``reason`` string with NO new ``event_type`` value,
    so the two hard-coded counters that key on ``activated`` — the promotion
    evidence trip counter (``promotion/evidence.py``) and the trade-report
    kill-switch reader (``cli/commands/report.py``) — both count a manual trip.
    """
    broker = StubBroker(account=sample_account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(event_store=event_store, legacy_path=tmp_path / "ks.json")
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
    )

    service.halt_trading("operator judgement: pull the plug")

    events = event_store.list_kill_switch_events()
    assert [e.event_type for e in events] == ["activated"]
    assert events[0].reason == "operator judgement: pull the plug"
    # Mirrors the promotion evidence counter (evidence.py) and report reader.
    activation_count = sum(1 for e in events if e.event_type == "activated")
    assert activation_count == 1


def test_preview_and_submit_record_explanations_and_trades(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    broker = StubBroker(
        account=sample_account,
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
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
    # 17 checks = ADR 0024 baseline (12) + ADR 0029 strategy_concurrent_positions
    # + R-OPS-004 reconciliation + HR-7 max_trades_per_day
    # + R-STR-014 disable_conditions (P2-07)
    # + concurrent-intraday PR2 opposite_side_order.
    assert len(explanations[0].risk_checks) == 17
    # R-STR-014: the disable-condition check flows into the explanation
    # record through the existing risk_checks serialization automatically.
    assert any(check["name"] == "disable_conditions" for check in explanations[0].risk_checks)
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
    _append_clean_reconciliation_run(event_store)
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
    _append_clean_reconciliation_run(event_store)
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
    _append_clean_reconciliation_run(event_store)
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
    _append_clean_reconciliation_run(event_store)
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


@pytest.mark.parametrize(
    "seed, expected_reason",
    [
        ("missing", "reconciliation_required"),
        ("dirty", "reconciliation_drift"),
        ("stale", "reconciliation_stale"),
        ("incomplete", "reconciliation_incomplete"),
    ],
)
def test_reconciliation_readiness_blocks_exposure_increasing_preview_and_submit(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    seed,
    expected_reason,
):
    broker = StubBroker(account=sample_account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    if seed == "dirty":
        _append_reconciliation_run(
            event_store,
            status="dirty",
            reason_codes=["position_local_only"],
        )
    elif seed == "stale":
        _append_clean_reconciliation_run(
            event_store,
            when=datetime.now(tz=UTC) - timedelta(days=1),
        )
    elif seed == "incomplete":
        _append_reconciliation_run(
            event_store,
            status="incomplete",
            broker_connected=False,
        )
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

    assert preview.status == ExecutionStatus.PREVIEW
    assert preview.risk_decision.allowed is False
    assert expected_reason in preview.risk_decision.reason_codes
    assert submit.status == ExecutionStatus.BLOCKED
    assert expected_reason in submit.risk_decision.reason_codes
    assert broker.submit_calls == []


def test_reconciliation_readiness_does_not_block_reducing_sell(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    long_position = Position(
        symbol="SPY",
        quantity=10.0,
        avg_entry_price=100.0,
        current_price=100.0,
        market_value=1000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )
    sell_order = Order(
        id="order-sell-1",
        symbol="SPY",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        quantity=5.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.PENDING,
        submitted_at=datetime.now(tz=UTC),
    )
    broker = StubBroker(
        account=sample_account,
        positions=[long_position],
        submit_order=sell_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_reconciliation_run(
        event_store,
        status="dirty",
        reason_codes=["position_local_only"],
    )
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
        TradeIntent(symbol="SPY", side=OrderSide.SELL, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.SUBMITTED
    assert "reconciliation_drift" not in result.risk_decision.reason_codes
    assert len(broker.submit_calls) == 1


def test_reconciliation_readiness_blocks_sell_beyond_broker_position(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    broker_position = Position(
        symbol="SPY",
        quantity=10.0,
        avg_entry_price=100.0,
        current_price=100.0,
        market_value=1000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )
    broker = StubBroker(
        account=sample_account,
        positions=[broker_position],
        submit_order=submitted_order,
    )
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_reconciliation_run(
        event_store,
        status="dirty",
        reason_codes=["position_local_only"],
    )
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
        TradeIntent(symbol="SPY", side=OrderSide.SELL, quantity=15, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.BLOCKED
    assert "reconciliation_drift" in result.risk_decision.reason_codes
    assert broker.submit_calls == []


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
    """R-EXE-002. Chokepoint invariant: for EVERY block reason, the broker receives zero
    submit calls.

    Meaningfulness verified: temporarily mutating the chokepoint in service.py
    to call ``self._broker.submit_order(...)`` unconditionally (before the
    ``if not result.risk_decision.allowed: return`` guard) causes the final
    ``broker.submit_calls == []`` assertion to fail for every scenario — the
    block reason differs but the chokepoint guarantee is the same. The
    parametrize coverage is therefore genuine for every entry listed here.
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
        service, broker, event_store = _build_service_with_store(
            tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
        )
        _seed_submitted_trade(
            event_store,
            symbol="SPY",
            side="buy",
            recorded_at=datetime.now(tz=UTC) - timedelta(seconds=30),
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
    _append_clean_reconciliation_run(event_store)
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


# ---------------------------------------------------------------------------
# Execution-attempt outbox (P1-02): durable attempt row BEFORE broker submit
# ---------------------------------------------------------------------------


def _build_service_with_store(
    tmp_path: Path,
    risk_defaults_file: Path,
    latest_bar: Bar,
    sample_account: AccountInfo,
    submitted_order: Order,
    *,
    broker: StubBroker | None = None,
) -> tuple[ExecutionService, StubBroker, EventStore]:
    broker = broker or StubBroker(account=sample_account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
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
    return service, broker, event_store


def test_record_execution_reconciles_quantity_to_broker_filled_qty(
    tmp_path, risk_defaults_file, latest_bar, sample_account
):
    """When the broker reports a synchronous fill, the trade row records the
    ACTUAL filled quantity and value, not the requested quantity.

    Partial-fill ledger reconciliation: the recorded ``quantity`` drove the
    strategy-scoped ledger (``strategy_positions``) off the *requested* amount,
    so a partial fill mis-stated the position. The synchronous path (backtest /
    instant-fill brokers / synchronously-reported partial fills) now records
    ``filled_quantity``. Mirrors the pre-existing fill-price reconciliation.
    """
    partial_fill = Order(
        id="order-partial-1",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=5.0,  # requested
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.FILLED,
        submitted_at=datetime.now(tz=UTC),
        filled_quantity=3.0,  # broker actually filled 3
        filled_avg_price=100.0,
        filled_at=datetime.now(tz=UTC),
    )
    service, _broker, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, partial_fill
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.SUBMITTED
    trades = event_store.list_trades()
    assert len(trades) == 1
    assert trades[0].quantity == 3.0  # filled, not requested 5
    assert trades[0].estimated_order_value == 300.0  # 3 x 100, not 5 x 100
    assert trades[0].estimated_unit_price == 100.0


def test_record_execution_keeps_requested_quantity_when_no_synchronous_fill(
    tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
):
    """Async path (Alpaca paper): submit returns PENDING with no fill info, so
    the optimistic trade row keeps the requested quantity (the fill is unknown
    at record time; reconciliation corrects it later)."""
    service, _broker, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )

    service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    trades = event_store.list_trades()
    assert len(trades) == 1
    assert trades[0].quantity == 5.0  # requested preserved (submitted_order is PENDING, no fill)


def test_submit_paper_attempt_lifecycle_happy_path(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """One attempt per submit: pending pre-broker, finalized 'submitted' with
    the broker order id; the client_order_id is threaded to the broker."""
    service, broker, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.SUBMITTED
    [attempt] = event_store.list_execution_attempts()
    assert attempt.status == "submitted"
    assert attempt.broker_order_id == submitted_order.id
    assert attempt.finalized_at is not None
    assert attempt.symbol == "SPY"
    assert attempt.side == "buy"
    # The pre-generated idempotency key reached the broker.
    assert broker.submit_calls[0]["client_order_id"] == attempt.client_order_id


def test_submit_paper_attempt_survives_failed_explanation_trade_write(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
    monkeypatch,
):
    """Broker success then a DB failure in the atomic explanation+trade write:
    the attempt row (status='submitted' + broker_order_id) is the surviving
    durable evidence — the P1-02 crash window."""
    service, broker, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )

    def _boom(**_kwargs):
        raise RuntimeError("injected DB failure after broker success")

    monkeypatch.setattr(event_store, "append_explanation_and_trade", _boom)

    with pytest.raises(RuntimeError, match="injected DB failure"):
        service.submit_paper(
            TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
        )

    assert broker.submit_calls, "the broker order WAS placed"
    assert event_store.list_trades() == [], "no trade row landed"
    [attempt] = event_store.list_execution_attempts()
    assert attempt.status == "submitted"
    assert attempt.broker_order_id == submitted_order.id


def test_duplicate_order_blocked_by_submitted_attempt_without_trade(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """Crash-after-broker-success dedup: a recent 'submitted' attempt with NO
    trade row must still veto a duplicate, with the existing reason code."""
    service, broker, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )
    event_store.append_execution_attempt(
        ExecutionAttemptEvent(
            client_order_id="ghost-attempt-1",
            symbol="SPY",
            side="buy",
            quantity=5.0,
            order_type="market",
            created_at=datetime.now(tz=UTC) - timedelta(seconds=30),
            status="submitted",
            broker_order_id="ghost-broker-1",
        )
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.BLOCKED
    assert "duplicate_order_window" in result.risk_decision.reason_codes
    assert broker.submit_calls == []
    # The blocked intent never reached the outbox boundary: still only the
    # seeded ghost attempt.
    assert len(event_store.list_execution_attempts()) == 1


def test_risk_blocked_submit_creates_no_attempt_row(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """A risk-blocked intent never reached the outbox boundary — no attempt row."""
    service, broker, event_store = _build_service_with_store(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )
    service.trigger_kill_switch("test: block everything")

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.BLOCKED
    assert broker.submit_calls == []
    assert event_store.list_execution_attempts() == []


def test_broker_rejection_finalizes_attempt_as_rejected(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """Broker rejection: the attempt is finalized 'rejected' with the broker's
    message, and stops counting toward the dedup veto."""
    broker = RejectingStubBroker(
        OrderRejectedError("potential wash trade detected ... 40310000"),
        account=sample_account,
        submit_order=submitted_order,
    )
    service, _, event_store = _build_service_with_store(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
        broker=broker,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.REJECTED
    [attempt] = event_store.list_execution_attempts()
    assert attempt.status == "rejected"
    assert attempt.broker_order_id is None
    assert "wash trade" in attempt.failure_detail


def test_unexpected_broker_exception_finalizes_attempt_as_error(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """A non-rejection broker exception still re-raises (fail-loud path is
    unchanged) but the attempt is finalized 'error' with the detail."""
    broker = RejectingStubBroker(
        ConnectionError("socket timed out mid-submit"),
        account=sample_account,
        submit_order=submitted_order,
    )
    service, _, event_store = _build_service_with_store(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
        broker=broker,
    )

    with pytest.raises(ConnectionError):
        service.submit_paper(
            TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
        )

    [attempt] = event_store.list_execution_attempts()
    assert attempt.status == "error"
    assert "socket timed out" in attempt.failure_detail


# --- Cross-process submit serialization (Option A, ADR 0056) -----------------


def _serialization_intent(strategy_file: Path, stage: str | None) -> TradeIntent:
    return TradeIntent(
        symbol="SPY",
        side=OrderSide.BUY,
        quantity=1.0,
        order_type=OrderType.MARKET,
        strategy_config_path=strategy_file,
        expected_stage=stage,
    )


def test_serializes_paper_micro_live_and_live_but_never_backtest(
    tmp_path, risk_defaults_file, strategy_file, latest_bar, sample_account, submitted_order
):
    service, _ = build_service(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )
    assert service._should_serialize_submit(
        _serialization_intent(strategy_file, "micro_live"), "paper"
    )
    assert service._should_serialize_submit(_serialization_intent(strategy_file, "live"), "paper")
    # Paper now serializes too: once many paper runners share one account
    # (concurrent-intraday plan, 2026-06-15), two simultaneous fires could
    # both clear an account cap on a stale snapshot. ADR 0056 amended.
    assert service._should_serialize_submit(_serialization_intent(strategy_file, "paper"), "paper")
    # Backtest source never serializes (simulated broker, single process).
    assert not service._should_serialize_submit(
        _serialization_intent(strategy_file, "micro_live"), "backtest"
    )
    assert not service._should_serialize_submit(
        _serialization_intent(strategy_file, "paper"), "backtest"
    )
    # A config-less operator submit (no expected_stage, no strategy_config_path,
    # so _effective_stage is None) still serializes on a non-backtest source — it
    # is a live-account submit that races runner submits for the same account cap.
    # Skipping it (the prior behavior) was the ADR 0056 race on the operator path.
    configless = TradeIntent(
        symbol="SPY", side=OrderSide.BUY, quantity=1.0, order_type=OrderType.MARKET
    )
    assert service._should_serialize_submit(configless, "paper")
    assert not service._should_serialize_submit(configless, "backtest")


def test_effective_stage_prefers_expected_stage_then_config(
    tmp_path, risk_defaults_file, strategy_file, latest_bar, sample_account, submitted_order
):
    service, _ = build_service(
        tmp_path, risk_defaults_file, latest_bar, sample_account, submitted_order
    )
    # Runner-bound expected_stage wins.
    micro_live = _serialization_intent(strategy_file, "micro_live")
    assert service._effective_stage(micro_live) == "micro_live"
    # Falls back to the config stage (the fixture is "paper") when unset.
    no_stage = TradeIntent(
        symbol="SPY",
        side=OrderSide.BUY,
        quantity=1.0,
        order_type=OrderType.MARKET,
        strategy_config_path=strategy_file,
    )
    assert service._effective_stage(no_stage) == "paper"


def test_micro_live_submit_fails_closed_when_lock_held(
    tmp_path, risk_defaults_file, strategy_file, latest_bar, sample_account, submitted_order
):
    locks_dir = tmp_path / "locks"
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
        locks_dir=locks_dir,
        submit_lock_timeout_seconds=0.2,
    )
    blocker = AdvisoryLock(service._submit_lock_name(), locks_dir=locks_dir)
    blocker.acquire()
    try:
        result = service.submit_paper(_serialization_intent(strategy_file, "micro_live"))
    finally:
        blocker.release()

    assert result.status is ExecutionStatus.BLOCKED
    assert "submit_serialization_unavailable" in result.risk_decision.reason_codes
    # Fail-closed: never reached the broker.
    assert broker.submit_calls == []


def test_paper_submit_fails_closed_when_lock_held(
    tmp_path, risk_defaults_file, strategy_file, latest_bar, sample_account, submitted_order
):
    # Two concurrent paper submits on one shared account must not both clear an
    # account cap on the same stale snapshot. The per-account submit lock makes
    # the read-snapshot -> evaluate-caps -> submit sequence mutually exclusive:
    # while writer A holds the lock (modeled here by an externally-held blocker),
    # writer B is declined fail-closed rather than proceeding against A's
    # pre-submit snapshot. ADR 0056 amended to include paper.
    locks_dir = tmp_path / "locks"
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
        locks_dir=locks_dir,
        submit_lock_timeout_seconds=0.2,
    )
    blocker = AdvisoryLock(service._submit_lock_name(), locks_dir=locks_dir)
    blocker.acquire()
    try:
        result = service.submit_paper(_serialization_intent(strategy_file, "paper"))
    finally:
        blocker.release()

    assert result.status is ExecutionStatus.BLOCKED
    assert "submit_serialization_unavailable" in result.risk_decision.reason_codes
    # Fail-closed: never reached the broker.
    assert broker.submit_calls == []


def test_micro_live_submit_fails_closed_on_lock_filesystem_error(
    tmp_path, risk_defaults_file, strategy_file, latest_bar, sample_account, submitted_order
):
    # A plain file where the locks dir should be: the lock's mkdir/open raise
    # OSError, not AdvisoryLockError. The submit must still fail closed (decline,
    # no order, session survives) rather than crash the runner.
    locks_path = tmp_path / "locks_is_a_file"
    locks_path.write_text("not a directory", encoding="utf-8")
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
        locks_dir=locks_path,
        submit_lock_timeout_seconds=0.2,
    )

    result = service.submit_paper(_serialization_intent(strategy_file, "micro_live"))

    assert result.status is ExecutionStatus.BLOCKED
    assert "submit_serialization_unavailable" in result.risk_decision.reason_codes
    assert broker.submit_calls == []


def test_submit_paper_defaults_time_in_force_to_day(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """R-BRK-010: order TIF defaults to DAY; an intent omitting it reaches the broker as DAY."""
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    # Deliberately omit time_in_force to exercise the default.
    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )

    assert result.status == ExecutionStatus.SUBMITTED
    assert broker.submit_calls, "broker.submit_order was never called"
    assert broker.submit_calls[0]["time_in_force"] == TimeInForce.DAY


def test_kill_switch_state_identical_for_rule_and_operator_paths(
    tmp_path, risk_defaults_file, latest_bar, submitted_order
):
    """Rule-triggered and operator-invoked kill switches reach identical persisted state.

    Both persist an "activated" event (active=True) that survives a fresh store read
    (manual reset required on next startup). NOTE: this proves state-identity only — it
    does NOT prove the operator path cancels open orders at the broker (trigger_kill_switch
    only activates; broker cancellation on the operator path is the runner's job and is not
    exercised here), so it does not close the kill-switch path-equivalence requirement.
    """
    loss_account = AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=-1_500.0,
    )
    provider = StubProvider(latest_bar)

    # Path A: rule-triggered (daily-loss breach via submit_paper).
    broker_a = StubBroker(account=loss_account, submit_order=submitted_order)
    event_store_a = EventStore(tmp_path / "a" / "milodex.db")
    _append_clean_reconciliation_run(event_store_a)
    store_a = KillSwitchStateStore(event_store=event_store_a, legacy_path=tmp_path / "a.json")
    service_a = ExecutionService(
        broker_client=broker_a,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store_a,
        event_store=event_store_a,
    )
    service_a.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )
    assert service_a.get_kill_switch_state().active is True
    assert broker_a.cancel_all_calls == 1

    # Path B: operator-invoked (StrategyRunner -> service.trigger_kill_switch).
    broker_b = StubBroker(account=loss_account, submit_order=submitted_order)
    event_store_b = EventStore(tmp_path / "b" / "milodex.db")
    _append_clean_reconciliation_run(event_store_b)
    store_b = KillSwitchStateStore(event_store=event_store_b, legacy_path=tmp_path / "b.json")
    service_b = ExecutionService(
        broker_client=broker_b,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store_b,
        event_store=event_store_b,
    )
    service_b.trigger_kill_switch("operator requested kill switch")
    assert service_b.get_kill_switch_state().active is True

    # Identical persisted state: each path wrote exactly one "activated" event ...
    assert [e.event_type for e in event_store_a.list_kill_switch_events()] == ["activated"]
    assert [e.event_type for e in event_store_b.list_kill_switch_events()] == ["activated"]

    # ... that survives a fresh store reading the same durable log (manual reset required).
    fresh_a = KillSwitchStateStore(
        event_store=EventStore(tmp_path / "a" / "milodex.db"), legacy_path=tmp_path / "a.json"
    )
    fresh_b = KillSwitchStateStore(
        event_store=EventStore(tmp_path / "b" / "milodex.db"), legacy_path=tmp_path / "b.json"
    )
    assert fresh_a.get_state().active is True
    assert fresh_b.get_state().active is True


def test_kill_switch_trip_writes_reviewable_incident_record(
    tmp_path, risk_defaults_file, latest_bar, submitted_order
):
    """A kill-switch trip writes a durable, inspectable activation + reset record.

    Covers the triggering condition captured in the durable event reason, the durable
    activation mark, read-only state inspection during the halt, and an explicit reset
    writing a linked follow-on "reset" event with the activation record preserved.
    This is NOT the full reviewable-incident-record requirement: the local/broker/strategy
    state snapshot, scope mark, operator-facing summary, and re-enable governance linkage
    are not yet in the kill_switch_events schema (deferred).
    """
    loss_account = AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=-1_500.0,
    )
    broker = StubBroker(account=loss_account, submit_order=submitted_order)
    provider = StubProvider(latest_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(event_store=event_store, legacy_path=tmp_path / "kill_switch.json")
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )
    assert "kill_switch_threshold_breached" in result.risk_decision.reason_codes

    # (a) + (c): a durable "activated" event capturing a non-empty triggering condition.
    events = event_store.list_kill_switch_events()
    assert len(events) == 1
    activation = events[0]
    assert activation.event_type == "activated"
    assert activation.reason

    # (d) + (f): read-only state inspection works during the halt; no order was submitted.
    during_halt = service.get_kill_switch_state()
    assert during_halt.active is True
    assert during_halt.reason
    assert during_halt.last_triggered_at is not None
    assert broker.submit_calls == []

    # (e): an explicit reset writes a linked follow-on "reset" event; activation preserved.
    service.reset_kill_switch()
    events_after = event_store.list_kill_switch_events()
    assert [e.event_type for e in events_after] == ["activated", "reset"]
    assert events_after[0].id == activation.id
    assert service.get_kill_switch_state().active is False


def test_idempotency_key_threads_to_submit_locked_without_changing_behavior(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """An explicit idempotency_key threads to _submit_locked and admits the first submit.

    The key is accompanied by a freshly-queued intent row (the real drain
    contract: the runner persists the row at lock-in, then resubmits with the
    same key). The CAS consumes that row (rowcount 1) and the submit proceeds —
    proving the kwarg reaches the chokepoint without otherwise altering a clean
    first submit.
    """
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )
    key = "rsi2.2026-06-22.BUY.SPY"
    # Future expiry so the CAS expiry fence (P1-1) passes under the real wall
    # clock (_submit_locked uses datetime.now, not the fixed test _NOW).
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    seen: dict[str, object] = {}
    original = service._submit_locked

    def _spy(intent, **kwargs):
        seen.update(kwargs)
        return original(intent, **kwargs)

    service._submit_locked = _spy  # type: ignore[method-assign]

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        # session_id matches the seeded row's session so the CAS clean-handoff
        # fence (P1-1) passes: same running session may drain its own row.
        session_id="sess-A",
        idempotency_key=key,
    )

    assert result.status == ExecutionStatus.SUBMITTED
    assert seen["idempotency_key"] == key
    assert broker.submit_calls


def _disable_duplicate_order_window(monkeypatch, service) -> None:
    """Neutralize the 60s duplicate-order veto so the second submit reaches the CAS.

    In production the two drains are an OVERNIGHT apart, so the 60s duplicate
    window (``risk_defaults.duplicate_order_window_seconds``) has long expired by
    the morning re-fire — the row-scoped CAS is the *only* gate that catches the
    duplicate. These single-process tests fire both ``_submit_locked`` calls
    within that 60s window, so the durable duplicate-order check would otherwise
    block the second call BEFORE it reaches the CAS, masking the behavior under
    test. Patching ``count_recent_submitted_orders`` to 0 reproduces the expired
    window and isolates the CAS as the gate (exactly-once is still proven by the
    broker submit count).
    """
    monkeypatch.setattr(
        service._event_store,
        "count_recent_submitted_orders",
        lambda **kwargs: 0,
    )


def test_idempotency_cas_admits_exactly_one_broker_submit_for_repeated_key(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """Repeated _submit_locked for one idempotency_key -> exactly one broker call."""
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )
    _disable_duplicate_order_window(monkeypatch, service)
    key = "rsi2.2026-06-22.BUY.SPY"
    # Seed the queued intent the runner would have persisted (Phase-1 shared
    # helper). Future expiry so the CAS expiry fence (P1-1) passes under the real
    # wall clock.
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    # session_id matches the seeded row so the CAS clean-handoff fence (P1-1)
    # passes; the second call still loses on status='queued' (already consumed).
    first = service._submit_locked(intent, source="paper", session_id="sess-A", idempotency_key=key)
    second = service._submit_locked(
        intent, source="paper", session_id="sess-A", idempotency_key=key
    )

    assert first.status == ExecutionStatus.SUBMITTED
    assert second.status == ExecutionStatus.BLOCKED
    assert "idempotency_suppressed" in second.risk_decision.reason_codes
    # The CAS, not the broker, is the gate: exactly one order left the building.
    assert len(broker.submit_calls) == 1
    # And the second call wrote a suppressed explanation, not a second outbox attempt.
    attempts = service._event_store.list_execution_attempts()
    assert len([a for a in attempts if a.symbol == "SPY"]) == 1


def test_idempotency_suppressed_does_not_activate_kill_switch(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """A suppressed (rowcount 0) submit is a benign race-loss; the kill switch stays inactive."""
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )
    _disable_duplicate_order_window(monkeypatch, service)
    key = "rsi2.2026-06-22.BUY.SPY"
    # Future expiry so the CAS expiry fence (P1-1) passes under the real wall clock.
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    # First call wins the CAS and submits; second loses (rowcount 0) -> suppressed.
    # session_id matches the seeded row so the clean-handoff fence (P1-1) passes.
    service._submit_locked(intent, source="paper", session_id="sess-A", idempotency_key=key)
    suppressed = service._submit_locked(
        intent, source="paper", session_id="sess-A", idempotency_key=key
    )

    assert suppressed.status == ExecutionStatus.BLOCKED
    assert "idempotency_suppressed" in suppressed.risk_decision.reason_codes
    # The race-loss must NOT trip the kill switch (it never reaches
    # _maybe_activate_kill_switch — that path is reserved for genuine risk blocks).
    assert service.get_kill_switch_state().active is False
    assert broker.cancel_all_calls == 0
    assert service._event_store.list_kill_switch_events() == []


def test_idempotency_suppressed_records_explanation_with_reason_code(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """A suppressed submit writes an auditable explanation carrying idempotency_suppressed."""
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )
    _disable_duplicate_order_window(monkeypatch, service)
    key = "rsi2.2026-06-22.BUY.SPY"
    # Future expiry so the CAS expiry fence (P1-1) passes under the real wall clock.
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    # session_id matches the seeded row so the clean-handoff fence (P1-1) passes.
    service._submit_locked(intent, source="paper", session_id="sess-A", idempotency_key=key)
    service._submit_locked(intent, source="paper", session_id="sess-A", idempotency_key=key)

    explanations = service._event_store.list_explanations()
    suppressed_rows = [
        e
        for e in explanations
        if e.decision_type == "submit" and "idempotency_suppressed" in e.reason_codes
    ]
    assert len(suppressed_rows) == 1
    assert suppressed_rows[0].status == ExecutionStatus.BLOCKED.value


def test_submit_paper_without_idempotency_key_is_unchanged(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """Legacy callers (no key) never touch the queued-intents CAS and submit exactly once."""
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
    assert len(broker.submit_calls) == 1


def test_drain_consume_and_outbox_attempt_commit_together(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """Fix #2: a winning drain consumes the row AND writes its 'pending' attempt atomically.

    On a successful drain the queued row flips to 'consumed' and exactly one
    outbox attempt exists, carrying the SAME client_order_id passed to the broker
    — the two writes commit together (one transaction), closing the CAS->outbox
    crash window. Uses a today-dated fresh provider bar (no override) so the 1D
    gate passes and Fix #2's coupling is the only thing under test.
    """
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )
    _disable_duplicate_order_window(monkeypatch, service)
    key = "rsi2.2026-06-22.BUY.SPY"
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        session_id="sess-A",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    result = service._submit_locked(
        intent, source="paper", session_id="sess-A", idempotency_key=key
    )

    assert result.status == ExecutionStatus.SUBMITTED
    # Both writes landed together.
    assert _queued_row_status(service, key) == "consumed"
    [attempt] = service._event_store.list_execution_attempts()
    assert attempt.symbol == "SPY"
    # The attempt's client_order_id is the one threaded to the broker.
    assert broker.submit_calls[0]["client_order_id"] == attempt.client_order_id


def test_drain_cas_loss_writes_neither_consume_nor_attempt_for_this_caller(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """Fix #2: a CAS-losing drain submits nothing and writes no second attempt.

    Two drains for one key: the first wins (consumes + writes its attempt + one
    broker call); the second loses the CAS — so it inserts NO attempt and makes
    NO broker call. Exactly one consumed row and one attempt remain (I-5).
    """
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )
    _disable_duplicate_order_window(monkeypatch, service)
    key = "rsi2.2026-06-22.BUY.SPY"
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        session_id="sess-A",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    service._submit_locked(intent, source="paper", session_id="sess-A", idempotency_key=key)
    second = service._submit_locked(
        intent, source="paper", session_id="sess-A", idempotency_key=key
    )

    assert second.status == ExecutionStatus.BLOCKED
    assert "idempotency_suppressed" in second.risk_decision.reason_codes
    # Exactly one broker submit and exactly one outbox attempt — the loser wrote
    # neither (the atomic method inserts the attempt only on rowcount == 1).
    assert len(broker.submit_calls) == 1
    assert len(service._event_store.list_execution_attempts()) == 1


# ---------------------------------------------------------------------------
# Queue-at-open bar-feeding override (D-1, Option A).
#
# A daily (1D) runner locks in on the prior session's close and the drain
# submits at the NEXT open. During RTH the live ``get_latest_bar`` returns an
# intraday latest-trade bar stamped *today*; the session-aware 1D staleness gate
# correctly rejects it (its date is not the latest completed session). The drain
# instead feeds the locked-in daily SESSION bar via ``latest_bar_override`` so
# the same gate sees the bar a 1D strategy legitimately prices and trades on.
#
# The override changes only WHICH bar the gate evaluates, never WHETHER the gate
# runs: a wrong/old override bar (session date != latest completed session) is
# still BLOCKED. ``risk/staleness.py``, ``risk/evaluator.py``, and
# ``risk/disable_conditions.py`` are untouched by this fix.
# ---------------------------------------------------------------------------


class _PriorSessionBroker(StubBroker):
    """Broker whose latest completed session is a FIXED prior date (RTH replay).

    Models the queue-at-open drain firing at the next open: the broker's latest
    completed session is yesterday's, while the live latest-trade bar is dated
    today. The default ``StubBroker.latest_completed_session`` returns *today*
    (so the existing fresh-bar fixtures pass the 1D gate); this subclass returns
    the prior session so the 1D gate's session-date comparison is actually
    exercised.
    """

    def __init__(self, *, prior_session: date, **kwargs) -> None:
        super().__init__(**kwargs)
        self._prior_session = prior_session

    def latest_completed_session(self, now: datetime) -> date:
        return self._prior_session


def _prior_session_dates() -> tuple[date, date]:
    """Return (prior_session, today) with the prior session one calendar day back.

    Day-of-week does not matter to the gate — it compares the bar's session date
    against the broker-reported latest completed session, both injected here.
    """
    today = datetime.now(tz=UTC).date()
    return today - timedelta(days=1), today


def _session_bar(session: date) -> Bar:
    """A daily SESSION-stamped bar for ``session`` (Alpaca daily-bar shape).

    Stamped at the session date in UTC so ``bar.timestamp.date() == session``
    (what the 1D gate compares) and the bar stays well inside the 7-day ceiling.
    """
    return Bar(
        timestamp=datetime(session.year, session.month, session.day, tzinfo=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )


def _today_intraday_bar(today: date) -> Bar:
    """A live intraday latest-trade bar stamped *today* (what RTH returns).

    Its ``.date()`` is today — NOT the prior completed session — so the
    session-aware 1D staleness gate fails closed on it. This is the bar the drain
    must NOT evaluate against; the override exists to replace it.
    """
    return Bar(
        timestamp=datetime.now(tz=UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )


def _build_queue_at_open_service(
    tmp_path: Path,
    risk_defaults_file: Path,
    strategy_file: Path,
    sample_account: AccountInfo,
    submitted_order: Order,
    monkeypatch,
    *,
    prior_session: date,
    live_bar: Bar,
) -> tuple[ExecutionService, _PriorSessionBroker]:
    """Build a 1D paper service for the queue-at-open scenario.

    Isolates STALENESS as the only variable: the frozen-manifest resolver is
    patched to return the matching runtime hash (so ``manifest_drift`` passes,
    exactly as ``test_frozen_manifest_hash_resolved_from_runner_bound_stage``
    does), the broker reports a prior completed session, and the live provider
    returns ``live_bar`` (the intraday today-dated bar the gate rejects).
    """
    import milodex.promotion.manifest as manifest_module
    from milodex.strategies.loader import compute_config_hash

    monkeypatch.setattr(
        manifest_module,
        "get_active_manifest_hash",
        lambda strategy_id, stage, event_store: compute_config_hash(strategy_file),
    )

    broker = _PriorSessionBroker(
        prior_session=prior_session,
        account=sample_account,
        submit_order=submitted_order,
    )
    provider = StubProvider(live_bar)
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    _append_clean_reconciliation_run(event_store)
    store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "kill_switch.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
        event_store=event_store,
    )
    return service, broker


def _queue_at_open_intent(strategy_file: Path) -> TradeIntent:
    return TradeIntent(
        symbol="SPY",
        side=OrderSide.BUY,
        quantity=5,
        order_type=OrderType.MARKET,
        strategy_config_path=strategy_file,
        expected_stage="paper",
    )


def test_queue_at_open_override_passes_session_staleness_both_gates_agree(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    strategy_file,
    sample_account,
    submitted_order,
):
    """The founder's headline: feeding the locked-in session bar passes the 1D gate.

    Queue-at-open drain at RTH: broker latest completed session is the PRIOR
    session; the live (un-overridden) bar is dated today and would fail the 1D
    staleness gate. With ``latest_bar_override`` = the prior-session bar, BOTH
    freshness gates agree it is fresh — the ``data_staleness`` veto passes and the
    ``data_quality_issue`` disable condition is inactive — and the submit is
    allowed.
    """
    prior_session, today = _prior_session_dates()
    service, broker = _build_queue_at_open_service(
        tmp_path,
        risk_defaults_file,
        strategy_file,
        sample_account,
        submitted_order,
        monkeypatch,
        prior_session=prior_session,
        live_bar=_today_intraday_bar(today),
    )
    # Seed the queued row the runner persisted, so the idempotency CAS succeeds
    # and evaluation runs the full risk battery (the drain supplies BOTH the key
    # and the override — they are orthogonal hooks; without the row the CAS would
    # suppress and short-circuit before staleness ever runs).
    key = "rsi2.2026-06-22.BUY.SPY"
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        session_id="sess-A",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    result = service.submit_paper(
        _queue_at_open_intent(strategy_file),
        # session_id matches the seeded row so the CAS clean-handoff fence (P1-1)
        # passes and the drain consumes its own session's row.
        session_id="sess-A",
        idempotency_key=key,
        latest_bar_override=_session_bar(prior_session),
        # A real drain always pairs the override with a fresh open price; supply it
        # so the Fix #5 fail-closed guard (no fresh price -> missing_fresh_price) is
        # not the thing under test here — staleness is.
        pricing_unit_price=100.0,
    )

    # Gate 1 (the data_staleness veto) passed positively on the override bar.
    staleness_checks = [c for c in result.risk_decision.checks if c.name == "data_staleness"]
    assert staleness_checks and staleness_checks[0].passed is True
    # Gate 2 (the data_quality_issue disable condition) is inactive: a stale bar
    # would have surfaced disable_condition_active.
    assert "disable_condition_active" not in result.risk_decision.reason_codes
    # Neither freshness gate fired.
    assert "stale_market_data" not in result.risk_decision.reason_codes
    # The submit is allowed and reached the broker.
    assert result.status == ExecutionStatus.SUBMITTED
    assert result.risk_decision.allowed is True
    assert len(broker.submit_calls) == 1


def test_queue_at_open_without_override_is_blocked_stale(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    strategy_file,
    sample_account,
    submitted_order,
):
    """Necessity / contrast: the SAME scenario without the override is BLOCKED.

    Proves the fix is load-bearing. The live latest-trade bar is dated today
    while the latest completed session is the prior session, so the 1D gate fails
    closed and no order reaches the broker.
    """
    prior_session, today = _prior_session_dates()
    service, broker = _build_queue_at_open_service(
        tmp_path,
        risk_defaults_file,
        strategy_file,
        sample_account,
        submitted_order,
        monkeypatch,
        prior_session=prior_session,
        live_bar=_today_intraday_bar(today),
    )

    result = service.submit_paper(_queue_at_open_intent(strategy_file))

    assert result.risk_decision.allowed is False
    assert "stale_market_data" in result.risk_decision.reason_codes
    # Chokepoint invariant: a blocked submit never reaches the broker.
    assert broker.submit_calls == []


def test_queue_at_open_wrong_date_override_is_still_blocked(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    strategy_file,
    sample_account,
    submitted_order,
):
    """Gate NOT weakened: an override bar with the WRONG session date is BLOCKED.

    The override changes only WHICH bar the gate sees, never whether the gate
    runs. A bar dated three sessions back (!= the latest completed session) must
    still be rejected — the override cannot smuggle a stale bar past the gate.
    """
    prior_session, today = _prior_session_dates()
    service, broker = _build_queue_at_open_service(
        tmp_path,
        risk_defaults_file,
        strategy_file,
        sample_account,
        submitted_order,
        monkeypatch,
        prior_session=prior_session,
        live_bar=_today_intraday_bar(today),
    )

    # An override bar three sessions old: its session date is not the latest
    # completed session, so the 1D gate must still block it. No idempotency key:
    # the block is unmistakably the staleness gate (which runs BEFORE the CAS in
    # _submit_locked), not CAS suppression.
    wrong_date = prior_session - timedelta(days=3)
    result = service.submit_paper(
        _queue_at_open_intent(strategy_file),
        latest_bar_override=_session_bar(wrong_date),
    )

    assert result.risk_decision.allowed is False
    assert "stale_market_data" in result.risk_decision.reason_codes
    assert broker.submit_calls == []


def test_submit_paper_suppresses_expired_queued_row_with_zero_broker_submits(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    strategy_file,
    sample_account,
    submitted_order,
):
    """P1-1 service-level probe: an EXPIRED queued row cannot be drained.

    Mirrors the reviewer's probe. The row is expired (its open window passed) but
    still status='queued' (the sweep has not run). ``get_active_queued_intents``
    correctly enumerates it as inactive (active_count == 0). Risk would ALLOW the
    submit (valid prior-session override passes the 1D gate), so the only thing
    that can stop the broker order is the consume CAS re-asserting expiry. Before
    P1-1 the CAS WHERE was only ``status = 'queued'`` and this submitted; now the
    CAS returns 0 and the submit is suppressed with ZERO broker submits.
    """
    prior_session, today = _prior_session_dates()
    service, broker = _build_queue_at_open_service(
        tmp_path,
        risk_defaults_file,
        strategy_file,
        sample_account,
        submitted_order,
        monkeypatch,
        prior_session=prior_session,
        live_bar=_today_intraday_bar(today),
    )

    key = "rsi2.v1|2026-06-23|buy|SPY"
    # Expired window; same running session so the ONLY failing drain predicate is
    # expiry (the clean-handoff fence passes when session_id == running session).
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        session_id="sess-A",
        expires_at=datetime.now(tz=UTC) - timedelta(hours=1),
    )

    # Enumeration agrees the row is inactive.
    active = service._event_store.get_active_queued_intents(
        "rsi2.v1", now=datetime.now(tz=UTC), running_session_id="sess-A"
    )
    assert active == []

    result = service.submit_paper(
        _queue_at_open_intent(strategy_file),
        session_id="sess-A",
        idempotency_key=key,
        latest_bar_override=_session_bar(prior_session),
        # Supply the fresh price so the Fix #5 guard passes and the EXPIRED-row CAS
        # (the thing under test) is what suppresses the submit.
        pricing_unit_price=100.0,
    )

    # Suppressed by the CAS (risk allowed), and NO order reached the broker.
    assert result.status == ExecutionStatus.BLOCKED
    assert "missing_fresh_price" not in result.risk_decision.reason_codes
    assert broker.submit_calls == []


def test_evaluate_latest_bar_override_none_uses_live_provider(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """No regression: latest_bar_override=None reads the live provider bar.

    The default None path must be byte-for-byte the existing behavior — the
    returned ExecutionResult carries the provider's live bar, not an override.
    """
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
    )

    assert result.status == ExecutionStatus.SUBMITTED
    # The live provider bar (the fixture) was used, not any override.
    assert result.latest_bar == latest_bar


# ---------------------------------------------------------------------------
# P1-3: latest_bar_override must NOT bypass non-1D staleness.
#
# The override is only legitimate for a 1D queued-intent drain. Two gates close
# the hole: (a) _submit_locked forwards the override to _evaluate only when a
# drain is in progress (idempotency_key is not None); (b) _evaluate USES the
# override only when the resolved strategy config is daily (bar_size == "1D").
# A non-1D or non-drain caller's override is inert — the live provider bar is
# used, so a stale provider bar still fails the wall-clock staleness gate.
# ---------------------------------------------------------------------------


@pytest.fixture()
def intraday_strategy_file(tmp_path: Path) -> Path:
    """A non-1D (5Min) strategy config: the override must be ignored for it."""
    path = tmp_path / "intraday_strategy.yaml"
    path.write_text(
        """
strategy:
  name: "paper_intraday"
  version: 1
  description: "Test intraday strategy"
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "5Min"
    position_lifecycle: "same_session"
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


def _hour_stale_bar() -> Bar:
    """A provider bar one hour old — well past the 300s wall-clock budget."""
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(hours=1),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )


def _fresh_override_bar() -> Bar:
    """A 1-second-old override the bug would let bypass the staleness gate."""
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=1),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )


def test_non_1d_override_does_not_bypass_staleness(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    intraday_strategy_file,
    sample_account,
    submitted_order,
):
    """A non-1D caller's fresh override over a stale provider bar is IGNORED.

    The bug: ``_evaluate`` preferred the override unconditionally, so a 1-second
    override on a 1-hour-stale provider bar bypassed the 300s wall-clock gate and
    reached the broker. With the fix the override is inert for a 5Min config: the
    stale provider bar is used and the submit is BLOCKED ``stale_market_data``,
    zero broker submits. An idempotency_key is supplied so the override is even
    forwarded to ``_evaluate`` — proving the gate is the bar_size==1D check, not
    merely the drain-in-progress check.

    The frozen-manifest resolver is patched to the matching hash (as the 1D
    queue-at-open fixtures do) so manifest drift is not a confounder and STALENESS
    is the only variable: pre-fix the override bypassed it (the block came from a
    different gate), post-fix the stale provider bar blocks here.
    """
    import milodex.promotion.manifest as manifest_module
    from milodex.strategies.loader import compute_config_hash

    monkeypatch.setattr(
        manifest_module,
        "get_active_manifest_hash",
        lambda strategy_id, stage, event_store: compute_config_hash(intraday_strategy_file),
    )

    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        _hour_stale_bar(),
        sample_account,
        submitted_order,
    )
    key = "intraday.2026-06-23.BUY.SPY"
    _append_queued_intent(service._event_store, idempotency_key=key)

    result = service.submit_paper(
        TradeIntent(
            symbol="SPY",
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.MARKET,
            strategy_config_path=intraday_strategy_file,
            expected_stage="paper",
        ),
        idempotency_key=key,
        latest_bar_override=_fresh_override_bar(),
    )

    assert result.risk_decision.allowed is False
    assert "stale_market_data" in result.risk_decision.reason_codes
    assert broker.submit_calls == []


def test_1d_override_without_idempotency_key_is_ignored(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    strategy_file,
    sample_account,
    submitted_order,
):
    """A 1D caller with NO idempotency_key supplying an override -> override ignored.

    The override is only legitimate for a drain (idempotency_key present).
    ``_submit_locked`` forwards the override to ``_evaluate`` only when a drain is
    in progress; a non-drain 1D caller's override is dropped before ``_evaluate``,
    so the live provider bar (here the today-dated intraday bar the 1D gate
    rejects) is used and the submit is BLOCKED — the override cannot smuggle a
    prior-session bar past the gate outside a drain.
    """
    prior_session, today = _prior_session_dates()
    service, broker = _build_queue_at_open_service(
        tmp_path,
        risk_defaults_file,
        strategy_file,
        sample_account,
        submitted_order,
        monkeypatch,
        prior_session=prior_session,
        live_bar=_today_intraday_bar(today),
    )

    # No idempotency_key -> not a drain -> the (otherwise valid prior-session)
    # override is dropped in _submit_locked; the today-dated live bar is used and
    # the 1D session-staleness gate blocks it.
    result = service.submit_paper(
        _queue_at_open_intent(strategy_file),
        latest_bar_override=_session_bar(prior_session),
    )

    assert result.risk_decision.allowed is False
    assert "stale_market_data" in result.risk_decision.reason_codes
    assert broker.submit_calls == []


# ---------------------------------------------------------------------------
# Fix #5: a 1D override drain must NEVER price the exposure cap on the stale
# override bar. When ``use_override`` is true the override bar is the locked
# (stale) session close; pricing the cap on it would re-open Blocker-1 across
# an overnight gap. The fix fails CLOSED: an override drain WITHOUT a fresh
# ``pricing_unit_price`` is BLOCKED ``missing_fresh_price`` — never a
# stale-priced submit. It is an ordinary pre-CAS risk block: no broker call, no
# queued row consumed, the kill switch stays inactive.
# ---------------------------------------------------------------------------


def _queued_row_status(service: ExecutionService, idempotency_key: str) -> str:
    """Return the durable status of the seeded queued row (asserts it exists)."""
    rows = service._event_store.list_queued_intents_by_status("queued")
    rows += service._event_store.list_queued_intents_by_status("consumed")
    match = [r for r in rows if r.idempotency_key == idempotency_key]
    assert len(match) == 1
    return match[0].status


def test_override_drain_without_fresh_price_blocks_missing_fresh_price(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    strategy_file,
    sample_account,
    submitted_order,
):
    """1D drain override + pricing_unit_price=None -> BLOCKED missing_fresh_price.

    The override bar would otherwise be a fresh prior-session bar that PASSES the
    staleness gate (so the block is unmistakably the new fail-closed price guard,
    not staleness). With no fresh price the cap can only fall back to the stale
    override close — exactly the Blocker-1 condition — so the submit must fail
    closed: no broker order, the queued row stays 'queued' (the pre-CAS block
    returns before the consume CAS), and the kill switch is untouched.
    """
    prior_session, today = _prior_session_dates()
    service, broker = _build_queue_at_open_service(
        tmp_path,
        risk_defaults_file,
        strategy_file,
        sample_account,
        submitted_order,
        monkeypatch,
        prior_session=prior_session,
        live_bar=_today_intraday_bar(today),
    )
    key = "rsi2.2026-06-22.BUY.SPY"
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        session_id="sess-A",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    result = service.submit_paper(
        _queue_at_open_intent(strategy_file),
        session_id="sess-A",
        idempotency_key=key,
        latest_bar_override=_session_bar(prior_session),
        # The drain hook present but NO fresh price: the fail-closed condition.
        pricing_unit_price=None,
    )

    assert result.status == ExecutionStatus.BLOCKED
    assert result.risk_decision.allowed is False
    assert "missing_fresh_price" in result.risk_decision.reason_codes
    # It is NOT a staleness block — the override bar is fresh for the gate.
    assert "stale_market_data" not in result.risk_decision.reason_codes
    # No order reached the broker.
    assert broker.submit_calls == []
    # Pre-CAS block: the queued row was NOT consumed (still claimable).
    assert _queued_row_status(service, key) == "queued"
    # An ordinary risk block — the kill switch stays inactive.
    assert service.get_kill_switch_state().active is False
    assert service._event_store.list_kill_switch_events() == []


def test_override_drain_with_fresh_price_is_unchanged(
    tmp_path,
    monkeypatch,
    risk_defaults_file,
    strategy_file,
    sample_account,
    submitted_order,
):
    """A normal drain WITH a fresh pricing_unit_price is unchanged (submits).

    The positive control for Fix #5: when the drain pairs the override bar with a
    fresh open price (every real drain does), the cap prices on the fresh price
    and the submit proceeds exactly as before — no missing_fresh_price block.
    """
    prior_session, today = _prior_session_dates()
    service, broker = _build_queue_at_open_service(
        tmp_path,
        risk_defaults_file,
        strategy_file,
        sample_account,
        submitted_order,
        monkeypatch,
        prior_session=prior_session,
        live_bar=_today_intraday_bar(today),
    )
    key = "rsi2.2026-06-22.BUY.SPY"
    _append_queued_intent(
        service._event_store,
        idempotency_key=key,
        session_id="sess-A",
        expires_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )

    result = service.submit_paper(
        _queue_at_open_intent(strategy_file),
        session_id="sess-A",
        idempotency_key=key,
        latest_bar_override=_session_bar(prior_session),
        pricing_unit_price=100.0,
    )

    assert result.status == ExecutionStatus.SUBMITTED
    assert result.risk_decision.allowed is True
    assert "missing_fresh_price" not in result.risk_decision.reason_codes
    assert len(broker.submit_calls) == 1
    assert _queued_row_status(service, key) == "consumed"


def test_non_override_manual_submit_unaffected_by_fresh_price_guard(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """A non-override manual submit (override None) is unchanged by Fix #5.

    The fail-closed guard rides the same drain-only ``use_override`` gate as the
    cap-pricing override: a plain manual submit (no override, no fresh price)
    still prices on the live ``get_latest_bar`` and submits — never blocked for
    missing_fresh_price.
    """
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
    )

    assert result.status == ExecutionStatus.SUBMITTED
    assert "missing_fresh_price" not in result.risk_decision.reason_codes
    assert result.latest_bar == latest_bar
    assert len(broker.submit_calls) == 1
