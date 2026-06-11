"""Tests for the strategy runtime runner."""

from __future__ import annotations

import signal
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.exceptions import OrderRejectedError
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.execution import ExecutionService
from milodex.execution.models import ExecutionStatus
from milodex.execution.state import KillSwitchStateStore
from milodex.strategies.runner import StrategyRunner


class StubBroker:
    """Broker stub that supports runner and execution-service flows."""

    def __init__(
        self,
        *,
        account: AccountInfo,
        positions: list[Position] | None = None,
        orders: list[Order] | None = None,
        market_open: bool = True,
    ) -> None:
        self.account = account
        self.positions = positions or []
        self.orders = orders or []
        self.submit_calls: list[dict[str, object]] = []
        self.cancel_all_orders_calls = 0
        self._market_open = market_open

    def get_account(self) -> AccountInfo:
        return self.account

    def get_positions(self) -> list[Position]:
        return list(self.positions)

    def get_position(self, symbol: str) -> Position | None:
        normalized = symbol.upper()
        return next(
            (position for position in self.positions if position.symbol == normalized), None
        )

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        return list(self.orders)[:limit]

    def is_market_open(self) -> bool:
        return self._market_open

    def submit_order(self, **kwargs) -> Order:
        self.submit_calls.append(kwargs)
        return Order(
            id=f"order-{len(self.submit_calls)}",
            symbol=str(kwargs["symbol"]),
            side=kwargs["side"],
            order_type=kwargs["order_type"],
            quantity=float(kwargs["quantity"]),
            time_in_force=kwargs["time_in_force"],
            status=OrderStatus.PENDING,
            submitted_at=datetime.now(tz=UTC),
        )

    def get_order(self, order_id: str) -> Order:
        return Order(
            id=order_id,
            symbol="SHY",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=1.0,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.PENDING,
            submitted_at=datetime.now(tz=UTC),
        )

    def cancel_order(self, order_id: str) -> bool:
        return True

    def cancel_all_orders(self) -> list[Order]:
        self.cancel_all_orders_calls += 1
        return []


class CancelFailingStubBroker(StubBroker):
    """Broker stub whose cancel_all_orders raises (simulates broker outage at shutdown)."""

    def cancel_all_orders(self) -> list[Order]:
        raise RuntimeError("broker unreachable")


class RejectingStubBroker(StubBroker):
    """Broker stub that raises OrderRejectedError on submit."""

    def __init__(self, rejection: OrderRejectedError, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rejection = rejection

    def submit_order(self, **kwargs) -> Order:
        self.submit_calls.append(kwargs)
        raise self._rejection


class StubProvider:
    """Data provider stub for runner tests."""

    def __init__(self, bars_by_symbol: dict[str, object]) -> None:
        self._bars_by_symbol = bars_by_symbol
        self.get_bars_calls: list[tuple[list[str], object, date, date]] = []
        self.get_latest_bar_calls: list[str] = []

    def get_bars(self, symbols: list[str], timeframe, start: date, end: date):
        self.get_bars_calls.append((symbols, timeframe, start, end))
        return {symbol: self._bars_by_symbol[symbol] for symbol in symbols}

    def get_latest_bar(self, symbol: str):
        self.get_latest_bar_calls.append(symbol)
        return self._bars_by_symbol[symbol].latest()


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
  max_single_position_pct: 1.00
  max_concurrent_positions: 3
  max_total_exposure_pct: 0.85
daily_limits:
  max_daily_loss_pct: 0.03
  max_trades_per_day: 20
order_safety:
  max_order_value_pct: 1.00
  duplicate_order_window_seconds: 60
  max_data_staleness_seconds: 999999
""".strip(),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def strategy_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "regime_runner.yaml").write_text(
        """
strategy:
  id: "regime.daily.sma200_rotation.spy_shy.v1"
  family: "regime"
  template: "daily.sma200_rotation"
  variant: "spy_shy"
  version: 1
  description: "Runner test strategy"
  enabled: true
  universe:
    - "SPY"
    - "SHY"
  parameters:
    ma_filter_length: 3
    risk_on_symbol: "SPY"
    risk_off_symbol: "SHY"
    allocation_pct: 1.0
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 1.0
    max_positions: 1
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "paper"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )
    return config_dir


def build_barset(closes: list[float]):
    from milodex.data.models import BarSet

    end = datetime.now(tz=UTC).replace(hour=21, minute=0, second=0, microsecond=0)
    timestamps = pd.date_range(end=end, periods=len(closes), freq="D", tz=UTC)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * len(closes),
                "vwap": closes,
            }
        )
    )


def build_service(
    *,
    tmp_path: Path,
    broker: StubBroker,
    provider: StubProvider,
    risk_defaults_file: Path,
) -> tuple[ExecutionService, EventStore, KillSwitchStateStore]:
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "logs" / "kill_switch_state.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
    )
    return service, event_store, kill_switch_store


def make_regime_config_intraday(config_dir: Path, bar_size: str = "1Min") -> Path:
    config_path = config_dir / "regime_runner.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'bar_size: "1D"', f'bar_size: "{bar_size}"'
        ),
        encoding="utf-8",
    )
    return config_path


def pin_clock_after_latest_bar(
    runner: StrategyRunner, provider: StubProvider, symbol: str = "SPY"
) -> None:
    """Pin the runner clock just past the latest fixture bar's window close.

    HR-2's completed-bar rule evaluates only bars with
    ``timestamp + bar_size <= now``. ``build_barset`` ends its bars at today
    21:00 UTC, which is in the future for most of the trading day — without a
    pinned clock the latest fixture bar would count as still-forming and be
    truncated, making intraday tests time-of-day dependent.
    """
    latest_ts = provider._bars_by_symbol[symbol].latest().timestamp
    pinned = latest_ts.to_pydatetime() + timedelta(hours=1)
    runner._now = lambda: pinned


def test_runner_submits_regime_signal_through_execution_service(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    config_path = make_regime_config_intraday(strategy_config_dir)
    # ADR 0054 §4: max_total_exposure_pct ceiling is 0.85. allocation_pct=1.0
    # would size a 100%-of-equity SHY order (~100% exposure), which exceeds the
    # ceiling and causes the risk evaluator to reject it. Lower allocation to
    # 0.80 so the order stays within the 0.85 ceiling (800 shares × $10 = $8000
    # = 80% exposure on $10k equity). The fixture's max_total_exposure_pct stays
    # at 0.85 — the scenario is rebalanced, not the ceiling.
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "allocation_pct: 1.0", "allocation_pct: 0.80"
        ),
        encoding="utf-8",
    )
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, config_path)
    pin_clock_after_latest_bar(runner, provider)

    results = runner.run_cycle()
    runner.shutdown(mode="controlled")

    assert len(results) == 1
    assert broker.submit_calls[0]["symbol"] == "SHY"
    assert provider.get_latest_bar_calls == ["SHY"]
    assert [record.session_id for record in event_store.list_explanations()] == [runner.session_id]
    assert [record.session_id for record in event_store.list_trades()] == [runner.session_id]
    assert event_store.list_strategy_runs()[0].exit_reason == "controlled_stop"


def test_runner_run_cycle_survives_broker_order_rejection(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    config_path = make_regime_config_intraday(strategy_config_dir)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "allocation_pct: 1.0", "allocation_pct: 0.80"
        ),
        encoding="utf-8",
    )
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    rejection = OrderRejectedError("potential wash trade detected ... 40310000")
    broker = RejectingStubBroker(
        rejection,
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, config_path)
    pin_clock_after_latest_bar(runner, provider)

    results = runner.run_cycle()
    runner.shutdown(mode="controlled")

    assert len(results) == 1
    assert results[0].status == ExecutionStatus.REJECTED
    submit_explanations = [
        event for event in event_store.list_explanations() if event.decision_type == "submit"
    ]
    assert len(submit_explanations) == 1
    assert submit_explanations[0].status == ExecutionStatus.REJECTED.value
    assert event_store.list_strategy_runs()[0].exit_reason == "controlled_stop"


def test_runner_records_no_action_explanation_when_strategy_holds_target(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    make_regime_config_intraday(strategy_config_dir)
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=8_000.0,
            buying_power=8_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        positions=[
            Position(
                symbol="SHY",
                quantity=1.0,
                avg_entry_price=20.0,
                current_price=20.0,
                market_value=20.0,
                unrealized_pnl=0.0,
                unrealized_pnl_pct=0.0,
            )
        ],
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    strategy_id = "regime.daily.sma200_rotation.spy_shy.v1"
    buy_at = datetime.now(tz=UTC)
    explanation_id = event_store.append_explanation(
        ExplanationEvent(
            recorded_at=buy_at,
            decision_type="submit",
            status="submitted",
            strategy_name=strategy_id,
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash=None,
            symbol="SHY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=buy_at,
            latest_bar_close=20.0,
            account_equity=10_000.0,
            account_cash=8_000.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="OK",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id="hold-target-session",
        )
    )
    event_store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=buy_at,
            status="submitted",
            source="paper",
            symbol="SHY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=20.0,
            estimated_order_value=20.0,
            strategy_name=strategy_id,
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="strategy_runner",
            broker_order_id="shy-hold-seed",
            broker_status=None,
            message=None,
        )
    )
    runner = StrategyRunner(
        strategy_id=strategy_id,
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    pin_clock_after_latest_bar(runner, provider)

    results = runner.run_cycle()
    runner.shutdown(mode="controlled")

    assert results == []
    no_trade = [
        event for event in event_store.list_explanations() if event.decision_type == "no_trade"
    ]
    assert len(no_trade) == 1
    assert no_trade[0].status in {"no_signal", "no_action"}
    assert no_trade[0].session_id == runner.session_id
    assert len(event_store.list_trades()) == 1


def test_runner_kill_switch_shutdown_cancels_orders_and_activates_halt(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, kill_switch_store = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    runner.shutdown(mode="kill_switch")

    assert broker.cancel_all_orders_calls == 1
    assert kill_switch_store.get_state().active is True
    assert event_store.list_strategy_runs()[0].exit_reason == "kill_switch"


def test_runner_kill_switch_shutdown_activates_even_if_cancel_all_fails(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """HR-13 item 8: a broker failure in cancel_all_orders must not block kill-switch activation.

    The pre-fix code called cancel_all_orders() bare (no try/except) — a broker
    outage at shutdown would raise before trigger_kill_switch(), leaving the switch
    inactive (fail-open).  The fix wraps the cancel in try/except and activates
    unconditionally.
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = CancelFailingStubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, kill_switch_store = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    # Must not raise even though cancel_all_orders raises.
    runner.shutdown(mode="kill_switch")

    assert kill_switch_store.get_state().active is True, (
        "Kill switch must activate even when cancel_all_orders raises"
    )
    assert event_store.list_strategy_runs()[0].exit_reason == "kill_switch"


def test_runner_ignores_non_strategy_yaml_when_resolving_config(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    make_regime_config_intraday(strategy_config_dir)
    (strategy_config_dir / "aaa_risk_defaults.yaml").write_text(
        """
kill_switch:
  enabled: true
""".strip(),
        encoding="utf-8",
    )
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )

    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )
    pin_clock_after_latest_bar(runner, provider)

    assert runner.run_cycle()


# ---------------------------------------------------------------------------
# New tests: multi-symbol fetch and entry_state
# ---------------------------------------------------------------------------


@pytest.fixture()
def meanrev_config_dir(tmp_path: Path) -> Path:
    config_dir = tmp_path / "configs_meanrev"
    config_dir.mkdir()
    (config_dir / "meanrev_runner.yaml").write_text(
        """
strategy:
  id: "meanrev.daily.pullback_rsi2.test_runner.v1"
  family: "meanrev"
  template: "daily.pullback_rsi2"
  variant: "test_runner"
  version: 1
  description: "Runner test meanrev strategy"
  enabled: true
  universe:
    - "AAPL"
    - "MSFT"
  parameters:
    rsi_lookback: 2
    rsi_entry_threshold: 70
    rsi_exit_threshold: 80
    ma_filter_length: 2
    stop_loss_pct: 0.10
    max_hold_days: 5
    max_concurrent_positions: 3
    sizing_rule: "equal_notional"
    per_position_notional_pct: 0.10
    ranking_enabled: false
    ranking_metric: "rsi_ascending"
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.20
    max_positions: 3
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "paper"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )
    return config_dir


def test_runner_requests_all_universe_symbols_from_data_provider(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """run_cycle() must fetch bars for every symbol in the universe, not only universe[0]."""
    make_regime_config_intraday(strategy_config_dir)
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    runner.run_cycle()

    assert len(provider.get_bars_calls) == 1
    requested_symbols = sorted(provider.get_bars_calls[0][0])
    assert requested_symbols == ["SHY", "SPY"]


def test_runner_meanrev_fires_entry_signal_via_bars_by_symbol(
    tmp_path: Path,
    meanrev_config_dir: Path,
    risk_defaults_file: Path,
):
    """MeanRev strategy emits a BUY intent when bars_by_symbol is populated correctly."""
    config_path = meanrev_config_dir / "meanrev_runner.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('bar_size: "1D"', 'bar_size: "1Min"'),
        encoding="utf-8",
    )
    # AAPL: latest (11.0) > SMA(2) of [9.0, 11.0]=10.0; RSI(2) ≈ 66.7 < 70 → entry
    # MSFT: flat, will not meet entry conditions with ma_filter_length=2 (equal bars)
    provider = StubProvider(
        {
            "AAPL": build_barset([10.0, 9.0, 11.0]),
            "MSFT": build_barset([50.0, 50.0, 50.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, config_path)
    runner = StrategyRunner(
        strategy_id="meanrev.daily.pullback_rsi2.test_runner.v1",
        config_dir=meanrev_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    pin_clock_after_latest_bar(runner, provider, symbol="AAPL")

    results = runner.run_cycle()
    runner.shutdown(mode="controlled")

    assert len(results) >= 1
    submitted_symbols = [call["symbol"] for call in broker.submit_calls]
    assert "AAPL" in submitted_symbols


def test_runner_builds_entry_state_from_positions_and_paper_trades(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """_build_entry_state() maps avg_entry_price and held_days for open positions."""
    event_store = EventStore(tmp_path / "data" / "milodex.db")

    # Seed a paper BUY trade for SPY 7 days ago.  Anchor to date.today() so that
    # _build_entry_state's (date.today() - trade.recorded_at.date()).days is
    # exactly 7 regardless of timezone offset between UTC and local time.
    buy_date = datetime.combine(date.today() - timedelta(days=7), datetime.min.time(), tzinfo=UTC)
    explanation_id = event_store.append_explanation(
        ExplanationEvent(
            recorded_at=buy_date,
            decision_type="strategy_evaluate",
            status="approved",
            strategy_name="regime.daily.sma200_rotation.spy_shy.v1",
            strategy_stage="paper",
            strategy_config_path="configs/regime.yaml",
            config_hash="abc123",
            symbol="SPY",
            side="buy",
            quantity=10.0,
            order_type="market",
            time_in_force="day",
            submitted_by="strategy_runner",
            market_open=True,
            latest_bar_timestamp=buy_date,
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
    event_store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=buy_date,
            status="submitted",
            source="paper",
            symbol="SPY",
            side="buy",
            quantity=10.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=100.0,
            estimated_order_value=1_000.0,
            strategy_name="regime.daily.sma200_rotation.spy_shy.v1",
            strategy_stage="paper",
            strategy_config_path="configs/regime.yaml",
            submitted_by="strategy_runner",
            broker_order_id=None,
            broker_status=None,
            message=None,
        )
    )

    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=9_000.0,
            buying_power=9_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        positions=[
            Position(
                symbol="SPY",
                quantity=10.0,
                avg_entry_price=100.0,
                current_price=105.0,
                market_value=1_050.0,
                unrealized_pnl=50.0,
                unrealized_pnl_pct=0.05,
            )
        ],
    )
    kill_switch_store = KillSwitchStateStore(
        event_store=event_store,
        legacy_path=tmp_path / "logs" / "kill_switch_state.json",
    )
    service = ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=kill_switch_store,
        event_store=event_store,
    )

    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    entry_state = runner._build_entry_state()

    assert "SPY" in entry_state
    assert entry_state["SPY"]["entry_price"] == 100.0
    assert entry_state["SPY"]["held_days"] == 7


def test_runner_builds_empty_entry_state_when_no_positions(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """_build_entry_state() returns {} when the broker reports no open positions."""
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    assert runner._build_entry_state() == {}


def test_runner_current_positions_uses_strategy_ledger_not_broker_net(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """ADR 0055 / 2026-06-03: sibling sell flattening broker net must not hide A's lot."""
    strategy_a = "regime.daily.sma200_rotation.spy_shy.v1"
    strategy_b = "momentum.vwap_trend.spy_5min.v1"
    event_store = EventStore(tmp_path / "data" / "milodex.db")
    buy_at = datetime(2026, 6, 3, 14, 51, 7, tzinfo=UTC)
    sell_at = datetime(2026, 6, 3, 14, 51, 12, tzinfo=UTC)

    def _submitted_trade(
        *,
        strategy_name: str,
        side: str,
        quantity: float,
        recorded_at: datetime,
        unit_price: float,
    ) -> None:
        explanation_id = event_store.append_explanation(
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
                latest_bar_close=unit_price,
                account_equity=10_000.0,
                account_cash=10_000.0,
                account_portfolio_value=10_000.0,
                account_daily_pnl=0.0,
                risk_allowed=True,
                risk_summary="OK",
                reason_codes=[],
                risk_checks=[],
                context={},
                session_id="sibling-session",
            )
        )
        event_store.append_trade(
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
                estimated_unit_price=unit_price,
                estimated_order_value=quantity * unit_price,
                strategy_name=strategy_name,
                strategy_stage="paper",
                strategy_config_path=None,
                submitted_by="strategy_runner",
                broker_order_id=f"{strategy_name}-{side}",
                broker_status=None,
                message=None,
            )
        )

    _submitted_trade(
        strategy_name=strategy_a,
        side="buy",
        quantity=13.0,
        recorded_at=buy_at,
        unit_price=590.0,
    )
    _submitted_trade(
        strategy_name=strategy_b,
        side="sell",
        quantity=13.0,
        recorded_at=sell_at,
        unit_price=590.0,
    )

    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        positions=[],
    )
    service, _, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id=strategy_a,
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    assert runner._current_positions() == {"SPY": 13.0}

    entry_state = runner._build_entry_state()
    assert entry_state["SPY"]["entry_price"] == 590.0
    assert entry_state["SPY"]["held_days"] == (date.today() - buy_at.date()).days


def test_runner_evaluation_symbol_uses_resolved_context_universe(
    tmp_path: Path,
    risk_defaults_file: Path,
):
    """_evaluation_symbol() must resolve through context.universe, not config.universe.

    Strategies that use ``universe_ref`` have an empty ``config.universe`` but
    a populated ``context.universe`` after the loader resolves the ref.
    This test uses a universe_ref strategy to confirm the runner doesn't raise.
    """
    config_dir = tmp_path / "configs_ref"
    config_dir.mkdir()

    (config_dir / "universe_test.yaml").write_text(
        """
universe:
  id: "test_ref_universe.v1"
  etfs: []
  stocks:
    - "AAPL"
    - "MSFT"
""".strip(),
        encoding="utf-8",
    )

    (config_dir / "meanrev_ref.yaml").write_text(
        """
strategy:
  id: "meanrev.daily.pullback_rsi2.ref_test.v1"
  family: "meanrev"
  template: "daily.pullback_rsi2"
  variant: "ref_test"
  version: 1
  description: "Universe-ref runner test"
  enabled: true
  universe_ref: "test_ref_universe.v1"
  parameters:
    rsi_lookback: 2
    rsi_entry_threshold: 70
    rsi_exit_threshold: 80
    ma_filter_length: 2
    stop_loss_pct: 0.10
    max_hold_days: 5
    max_concurrent_positions: 3
    sizing_rule: "equal_notional"
    per_position_notional_pct: 0.10
    ranking_enabled: false
    ranking_metric: "rsi_ascending"
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.20
    max_positions: 3
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "paper"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )

    provider = StubProvider(
        {
            "AAPL": build_barset([10.0, 9.0, 11.0]),
            "MSFT": build_barset([50.0, 50.0, 50.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )

    runner = StrategyRunner(
        strategy_id="meanrev.daily.pullback_rsi2.ref_test.v1",
        config_dir=config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    # Should not raise; primary symbol comes from resolved context.universe
    assert runner._evaluation_symbol() in ("AAPL", "MSFT")


# ---------------------------------------------------------------------------
# SIGINT dual-stop dialog (ADR 0012)
# ---------------------------------------------------------------------------


def _build_sigint_runner(
    *,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    prompt_fn,
):
    """Wire a runner + stub broker/provider for SIGINT path tests."""
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, kill_switch_store = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
        poll_interval_seconds=0.0,
        prompt_fn=prompt_fn,
    )
    return runner, broker, event_store, kill_switch_store


def test_runner_sigint_controlled_path_finishes_current_eval_cleanly(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """First Ctrl-C + operator picks 'c' => clean exit, no order cancel."""
    runner, broker, event_store, kill_switch_store = _build_sigint_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        prompt_fn=lambda: "c",
    )

    # Simulate SIGINT arriving during the post-cycle sleep of the first loop turn.
    from milodex.strategies import runner as runner_module

    fired = {"count": 0}

    def fake_sleep(_seconds: float) -> None:
        if fired["count"] == 0:
            fired["count"] += 1
            runner._handle_sigint(signal.SIGINT, None)

    monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

    runner.run()

    assert broker.cancel_all_orders_calls == 0
    assert kill_switch_store.get_state().active is False
    runs = event_store.list_strategy_runs()
    assert len(runs) == 1
    assert runs[0].exit_reason == "controlled_stop"


def test_runner_sigint_kill_path_cancels_orders_and_activates_halt(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """First Ctrl-C + operator picks 'k' => cancel_all_orders + kill_switch on."""
    runner, broker, event_store, kill_switch_store = _build_sigint_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        prompt_fn=lambda: "k",
    )

    from milodex.strategies import runner as runner_module

    fired = {"count": 0}

    def fake_sleep(_seconds: float) -> None:
        if fired["count"] == 0:
            fired["count"] += 1
            runner._handle_sigint(signal.SIGINT, None)

    monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

    runner.run()

    assert broker.cancel_all_orders_calls == 1
    assert kill_switch_store.get_state().active is True
    runs = event_store.list_strategy_runs()
    assert len(runs) == 1
    assert runs[0].exit_reason == "kill_switch"


def test_runner_second_sigint_during_prompt_forces_kill_switch(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Second Ctrl-C while prompt is open => safer-when-in-doubt kill switch (ADR 0012)."""

    def interrupted_prompt() -> str:
        raise KeyboardInterrupt

    runner, broker, event_store, kill_switch_store = _build_sigint_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        prompt_fn=interrupted_prompt,
    )

    from milodex.strategies import runner as runner_module

    fired = {"count": 0}

    def fake_sleep(_seconds: float) -> None:
        if fired["count"] == 0:
            fired["count"] += 1
            runner._handle_sigint(signal.SIGINT, None)

    monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

    runner.run()

    assert broker.cancel_all_orders_calls == 1
    assert kill_switch_store.get_state().active is True
    assert event_store.list_strategy_runs()[0].exit_reason == "kill_switch"


def test_runner_heartbeats_its_lock_each_poll_cycle(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """The runner must refresh its advisory lock once per poll cycle.

    Regression for the fix/event-store-integrity fail-open: ``run()`` is an
    unbounded loop documented as safe to leave running all day/overnight,
    holding the per-strategy advisory lock the whole time. Without a
    per-cycle heartbeat the lock-file mtime never moves, so the 12h age
    fallback would let a second invocation STEAL the lock from the still
    -working process → duplicate trade submission. Assert the runner
    invokes its injected ``lock_heartbeat`` on every loop iteration.
    """
    heartbeats = {"count": 0}

    runner, _broker, _event_store, _kill = _build_sigint_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        prompt_fn=lambda: "c",
    )
    runner.set_lock_heartbeat(lambda: heartbeats.__setitem__("count", heartbeats["count"] + 1))

    from milodex.strategies import runner as runner_module

    fired = {"count": 0}

    def fake_sleep(_seconds: float) -> None:
        # Run a few full cycles, then ask for a controlled stop so the
        # unbounded loop terminates.
        fired["count"] += 1
        if fired["count"] >= 3:
            runner._handle_sigint(signal.SIGINT, None)

    monkeypatch.setattr(runner_module.time, "sleep", fake_sleep)

    runner.run()

    # At least one heartbeat per completed poll cycle.
    assert heartbeats["count"] >= fired["count"] >= 3


# ---------------------------------------------------------------------------
# CI-1 (PHASE2_PLANNING.md): post-close watermark advance is gated on bar
# finalization stability — two consecutive identical OHLCV fetches separated
# by at least the lockin min-interval, with a max-wait fallback for the
# (rare) bar that never stabilizes.
# ---------------------------------------------------------------------------


def _build_lockin_runner(
    *,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    initial_bars: dict | None = None,
    market_open: bool = False,
    close_lockin_min_interval_seconds: float = 30.0,
    close_lockin_max_wait_seconds: float = 300.0,
):
    """Wire a runner whose clock is monkey-patchable for stability-window tests."""
    if initial_bars is None:
        initial_bars = {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    provider = StubProvider(initial_bars)
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        market_open=market_open,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, strategy_config_dir / "regime_runner.yaml")
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
        close_lockin_min_interval_seconds=close_lockin_min_interval_seconds,
        close_lockin_max_wait_seconds=close_lockin_max_wait_seconds,
    )
    return runner, broker, provider, event_store


def test_runner_post_close_first_cycle_does_not_advance_watermark_pending_stability(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """First post-close cycle observes the bar but does not advance the watermark.

    Lockin requires two consecutive identical OHLCV fetches; the first
    observation initializes the pending state and waits for confirmation.
    """
    runner, _, _, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )

    runner.run_cycle()

    assert runner._last_processed_bar_at is None, (
        "first post-close cycle must not advance the watermark; CI-1 requires "
        "two consecutive identical OHLCV fetches before lockin."
    )


def test_runner_post_close_advances_watermark_after_stable_consecutive_fetches(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Two identical fetches separated by at least min-interval advance the watermark."""
    runner, _, _, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
        close_lockin_min_interval_seconds=30.0,
    )
    fake_now = [datetime(2026, 5, 4, 16, 0, 0, tzinfo=UTC)]
    runner._now = lambda: fake_now[0]

    runner.run_cycle()
    assert runner._last_processed_bar_at is None
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    runner.run_cycle()

    assert runner._last_processed_bar_at is not None


def test_runner_post_close_does_not_advance_watermark_when_interval_too_short(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Two identical fetches less than min-interval apart still wait."""
    runner, _, _, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
        close_lockin_min_interval_seconds=30.0,
    )
    fake_now = [datetime(2026, 5, 4, 16, 0, 0, tzinfo=UTC)]
    runner._now = lambda: fake_now[0]

    runner.run_cycle()
    fake_now[0] = fake_now[0] + timedelta(seconds=15)
    runner.run_cycle()

    assert runner._last_processed_bar_at is None


def test_runner_post_close_resets_stability_when_bar_changes_between_fetches(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A bar OHLCV change between fetches resets the stability clock to the new bar."""
    runner, _, provider, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
        close_lockin_min_interval_seconds=30.0,
        close_lockin_max_wait_seconds=999_999.0,
    )
    fake_now = [datetime(2026, 5, 4, 16, 0, 0, tzinfo=UTC)]
    runner._now = lambda: fake_now[0]

    runner.run_cycle()
    assert runner._last_processed_bar_at is None

    provider._bars_by_symbol = {
        "SPY": build_barset([10.0, 10.0, 10.5]),
        "SHY": build_barset([10.0, 10.0, 10.0]),
    }
    fake_now[0] = fake_now[0] + timedelta(seconds=60)
    runner.run_cycle()

    assert runner._last_processed_bar_at is None, (
        "bar change between fetches must reset the stability clock; the "
        "second fetch becomes the first observation of the new bar shape."
    )


def test_runner_post_close_timeout_advances_watermark_even_without_stability(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """If max-wait elapses without stability, advance the watermark anyway.

    Fail-mode (a): a never-stable bar (broken provider) must not loop
    forever. The audit trail of repeated explanations preserves forensic
    visibility; advancing here unblocks subsequent cycles.
    """
    runner, _, provider, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
        close_lockin_min_interval_seconds=30.0,
        close_lockin_max_wait_seconds=300.0,
    )
    fake_now = [datetime(2026, 5, 4, 16, 0, 0, tzinfo=UTC)]
    runner._now = lambda: fake_now[0]

    runner.run_cycle()
    for i in range(1, 7):
        provider._bars_by_symbol = {
            "SPY": build_barset([10.0, 10.0, 10.0 + i * 0.1]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
        fake_now[0] = fake_now[0] + timedelta(seconds=60)
        runner.run_cycle()

    assert runner._last_processed_bar_at is not None, (
        "after max-wait timeout, watermark must advance even without stability; "
        "CI-1 fail-mode (a) prevents indefinite loops on a broken provider."
    )


# ---------------------------------------------------------------------------
# Pre-open poisoning: a stale prior-session bar must never lock in
# ---------------------------------------------------------------------------


def test_daily_pre_open_stale_bar_does_not_lock_in(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Pre-open launch: the latest available daily bar is a PRIOR session's
    close (bar date < today). The runner must NOT lock it in -- doing so would
    poison the watermark and suppress today's real post-close evaluation."""
    runner, _, provider, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    # Bars end at "real today" (build_barset). Make them STALE relative to now
    # by advancing the runner clock two days past the latest bar -- i.e. the
    # latest available bar is from a prior session, exactly the pre-open case.
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime() + timedelta(days=2)]
    runner._now = lambda: fake_now[0]

    results = runner.run_cycle()
    # Even after the stability interval elapses, a stale bar never locks in.
    fake_now[0] = fake_now[0] + timedelta(seconds=60)
    runner.run_cycle()

    assert results == []
    assert runner._last_processed_bar_at is None


def test_daily_post_close_current_bar_locks_in(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Post-close (incl. cold launch): latest bar is TODAY's close
    (bar date == now date). The current-session guard must allow lock-in."""
    runner, _, provider, _ = _build_lockin_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
        close_lockin_min_interval_seconds=30.0,
    )
    # Anchor the clock to the SAME UTC date as the latest bar -> "today's close".
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner._now = lambda: fake_now[0]

    runner.run_cycle()  # first cycle: pending stability
    assert runner._last_processed_bar_at is None
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    runner.run_cycle()  # second cycle: lock in

    assert runner._last_processed_bar_at is not None


# ---------------------------------------------------------------------------
# Regression: in-progress bar must not poison _last_processed_bar_at
# ---------------------------------------------------------------------------


def test_runner_reevaluates_same_bar_after_close_when_intraday_was_in_progress(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """An in-progress bar must not poison the same-timestamp post-close bar.

    Regression for the 2026-04-23 bug: a mid-day cycle used to mark today's
    in-progress bar as seen, which caused the same-timestamp finalized bar
    to be skipped forever by the ``already_seen`` check.

    With CI-1 lockin (PHASE2_PLANNING.md), post-close re-evaluation continues
    until two consecutive identical OHLCV fetches confirm the bar has settled,
    at which point the watermark advances and subsequent cycles short-circuit.
    The "in-progress doesn't poison" property is preserved — intraday cycles
    still leave the watermark at None; the post-close path now requires
    stability before advancing.
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        market_open=True,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )

    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, strategy_config_dir / "regime_runner.yaml")

    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
        close_lockin_min_interval_seconds=30.0,
    )
    fake_now = [datetime(2026, 5, 4, 16, 0, 0, tzinfo=UTC)]
    runner._now = lambda: fake_now[0]

    # --- Cycle 1: market is open, bar is in-progress -------------------------
    results_during_market = runner.run_cycle()

    assert results_during_market == [], "Daily strategies must not submit on in-progress bars."
    assert provider.get_bars_calls == [], "Open-market daily polling should not fetch bars."
    assert runner._last_processed_bar_at is None, (
        "In-progress bar must not advance _last_processed_bar_at; doing so "
        "would suppress the same-timestamp finalized-bar evaluation below."
    )

    # --- Cycle 2: market closed, first post-close observation ----------------
    broker._market_open = False
    results_post_close = runner.run_cycle()

    assert results_post_close == [], "First post-close observation only starts lockin."
    assert runner._last_processed_bar_at is None, (
        "First post-close observation initializes lockin pending; CI-1 "
        "requires a confirming second fetch before advancing the watermark."
    )

    # --- Cycle 3: still post-close, same bar, 30s elapsed → stability lockin --
    fake_now[0] = fake_now[0] + timedelta(seconds=30)
    results_after_lockin = runner.run_cycle()

    assert len(results_after_lockin) >= 1, "Stable post-close bar should evaluate exactly once."
    assert runner._last_processed_bar_at is not None, (
        "Once the post-close bar passes the stability check, the watermark advances."
    )

    # --- Cycle 4: still post-close, watermark set, short-circuit -------------
    results_after_seen = runner.run_cycle()
    assert results_after_seen == [], (
        "Once the watermark is set, further polls on the same bar are already-seen."
    )


def test_runner_suppresses_duplicate_same_bar_intents(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A repeated signal for the same latest bar must not spam execution records."""
    config_path = make_regime_config_intraday(strategy_config_dir)
    # ADR 0054 §4: max_total_exposure_pct ceiling is 0.85. allocation_pct=1.0
    # would size a 100%-of-equity SHY order (~100% exposure), which exceeds the
    # ceiling and causes the risk evaluator to reject it. Lower allocation to
    # 0.80 so the order stays within the 0.85 ceiling (800 shares × $10 = $8000
    # = 80% exposure on $10k equity). The fixture's max_total_exposure_pct stays
    # at 0.85 — the scenario is rebalanced, not the ceiling.
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "allocation_pct: 1.0", "allocation_pct: 0.80"
        ),
        encoding="utf-8",
    )
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        market_open=True,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )

    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, config_path)
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    pin_clock_after_latest_bar(runner, provider)

    first_results = runner.run_cycle()
    second_results = runner.run_cycle()

    assert len(first_results) == 1
    assert second_results == []
    assert len(broker.submit_calls) == 1
    assert len(event_store.list_trades()) == 1

    # Dedup set is scoped to the current bar — no unbounded growth across bars.
    assert len(runner._processed_intent_keys) == 1
    assert runner._processed_intent_bar_at is not None

    # Simulate stale state from an older bar: keys for a prior timestamp must
    # be cleared on the next cycle so the set cannot grow without bound.
    # Clear the HR-2 intraday watermark so the cycle re-evaluates the bar and
    # reaches the rollover-clear (otherwise already_seen short-circuits first).
    stale_ts = runner._processed_intent_bar_at - timedelta(days=1)
    runner._processed_intent_bar_at = stale_ts
    runner._processed_intent_keys = {(stale_ts, "OLD", "buy")}
    runner._last_processed_bar_at = None
    runner.run_cycle()
    assert all(ts != stale_ts for ts, _, _ in runner._processed_intent_keys), (
        "Dedup set must not retain keys from prior bars."
    )


# ---------------------------------------------------------------------------
# HR-2 (R-P1-1, R-P1-2): intraday bar watermark, completed-bar-only
# evaluation, and the closed-market early-out for intraday runners.
# Daily-path behavior is pinned by the lockin tests above and must not change.
# ---------------------------------------------------------------------------


def build_intraday_barset(closes: list[float], *, end: datetime, freq: str = "5min"):
    """Intraday-spaced sibling of build_barset with an explicit end timestamp."""
    from milodex.data.models import BarSet

    timestamps = pd.date_range(end=end, periods=len(closes), freq=freq)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * len(closes),
                "vwap": closes,
            }
        )
    )


def _build_hr2_runner(
    *,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    bars_by_symbol: dict,
    market_open: bool,
):
    provider = StubProvider(bars_by_symbol)
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        market_open=market_open,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )
    return runner, broker, provider, event_store


def test_intraday_watermark_advances_and_short_circuits_repeat_cycles(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """R-P1-1: the intraday path advances _last_processed_bar_at after each
    evaluation, so re-polling the same bar is already_seen — no re-evaluation,
    no second explanation. The fetch itself still happens while the market is
    open (new bars can only be discovered by fetching).

    Two bars < ma_filter_length=3 gives a deterministic no_signal evaluation
    that records exactly one no_trade explanation per evaluated bar — the
    pre-fix behavior wrote one per 10s poll cycle, 24/7.
    """
    make_regime_config_intraday(strategy_config_dir)
    runner, _, provider, event_store = _build_hr2_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        bars_by_symbol={
            "SPY": build_barset([10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0]),
        },
        market_open=True,
    )
    pin_clock_after_latest_bar(runner, provider)
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp

    first = runner.run_cycle()

    no_trade = [e for e in event_store.list_explanations() if e.decision_type == "no_trade"]
    assert first == []
    assert len(no_trade) == 1
    assert runner._last_processed_bar_at == latest_ts, (
        "intraday evaluation must advance the watermark to the evaluated bar"
    )

    second = runner.run_cycle()

    no_trade_after = [e for e in event_store.list_explanations() if e.decision_type == "no_trade"]
    assert second == []
    assert len(no_trade_after) == 1, (
        "re-polling the same bar must short-circuit via already_seen — no new explanation"
    )
    assert len(provider.get_bars_calls) == 2, "open-market intraday cycles still fetch"


def test_intraday_forming_bar_not_evaluated_until_window_closes(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """R-P1-2: mid-window, the still-forming bar is invisible to the strategy;
    the cycle evaluates the last COMPLETED bar instead. Once the window closes
    (timestamp + bar_size <= now), the same bar becomes the evaluation target
    — matching the backtest contract (decision_time = bar_ts + bar_size)."""
    config_path = make_regime_config_intraday(strategy_config_dir, bar_size="5Min")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "allocation_pct: 1.0", "allocation_pct: 0.80"
        ),
        encoding="utf-8",
    )
    # Anchor near the real clock so the risk layer's data-staleness check
    # (which compares against the real now) stays satisfied.
    end = datetime.now(tz=UTC).replace(second=0, microsecond=0) - timedelta(hours=1)
    runner, broker, _, event_store = _build_hr2_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        bars_by_symbol={
            "SPY": build_intraday_barset([10.0, 10.0, 10.0], end=end),
            "SHY": build_intraday_barset([10.0, 10.0, 10.0], end=end),
        },
        market_open=True,
    )
    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, config_path)
    fake_now = [end + timedelta(minutes=2)]  # latest bar's [end, end+5Min) window still open
    runner._now = lambda: fake_now[0]

    mid_window = runner.run_cycle()

    assert mid_window == []
    assert broker.submit_calls == [], "a still-forming bar must never fire a submission"
    # Only two completed bars were visible (< ma_filter_length=3 → no_signal),
    # and the watermark sits on the last COMPLETED bar, not the forming one.
    assert runner._last_processed_bar_at == end - timedelta(minutes=5)

    fake_now[0] = end + timedelta(minutes=5)  # the latest bar's window has now closed
    closed = runner.run_cycle()

    assert len(closed) == 1, "the same bar must be evaluated once its window closes"
    assert broker.submit_calls[0]["symbol"] == "SHY"
    assert runner._last_processed_bar_at == end


def test_intraday_closed_market_early_out_after_session_drained(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """R-P1-1: once a closed-market cycle confirms the session's last completed
    bar is processed, later closed-market cycles skip the fetch entirely and
    write no explanations. The first open-market cycle resumes fetching."""
    make_regime_config_intraday(strategy_config_dir)
    runner, broker, provider, event_store = _build_hr2_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        bars_by_symbol={
            "SPY": build_barset([10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0]),
        },
        market_open=False,
    )
    pin_clock_after_latest_bar(runner, provider)

    runner.run_cycle()  # evaluates the session's final completed bar
    assert len(provider.get_bars_calls) == 1
    assert runner._last_processed_bar_at is not None

    runner.run_cycle()  # quiet cycle 1 — straggler grace, must NOT arm yet
    assert len(provider.get_bars_calls) == 2
    assert runner._intraday_session_drained is False

    runner.run_cycle()  # quiet cycle 2 + wall-clock margin → arms the early-out
    assert len(provider.get_bars_calls) == 3
    assert runner._intraday_session_drained is True

    for _ in range(3):
        assert runner.run_cycle() == []
    assert len(provider.get_bars_calls) == 3, (
        "closed-market cycles after the session is drained must not fetch"
    )
    assert len(event_store.list_explanations()) == 1, (
        "overnight/weekend polling must not write no-signal explanations"
    )

    broker._market_open = True
    runner.run_cycle()
    assert len(provider.get_bars_calls) == 4, "the first open-market cycle resumes fetching"
    assert runner._intraday_session_drained is False
    assert runner._intraday_quiet_closed_cycles == 0


def test_intraday_drained_flag_requires_wall_clock_margin(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Round-3 regression: quiet-cycle count alone must NOT arm the early-out.

    With the clock pinned INSIDE the publication-lag margin (2 bar-widths +
    max(3 bar-widths, 10 min) past the newest bar), arbitrarily many quiet
    closed-market cycles keep fetching — a straggler could still publish.
    Once the clock passes the margin, the next quiet cycle arms. Deleting
    the wall-clock condition makes this test fail (review round 2 showed
    the counter alone reproduced every prior pinned transition).
    """
    make_regime_config_intraday(strategy_config_dir)
    base_end = datetime(2026, 6, 10, 19, 50, tzinfo=UTC)
    runner, broker, provider, event_store = _build_hr2_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        bars_by_symbol={
            "SPY": build_intraday_barset([10.0, 10.0], end=base_end, freq="1min"),
            "SHY": build_intraday_barset([10.0, 10.0], end=base_end, freq="1min"),
        },
        market_open=False,
    )
    # Margin for 1Min bars = 2min + max(3min, 10min) = 12min. Pin inside it.
    runner._now = lambda: base_end + timedelta(minutes=5)

    runner.run_cycle()  # evaluates the latest completed bar
    for _ in range(4):  # quiet cycles well past the >=2 count requirement
        runner.run_cycle()
    assert runner._intraday_session_drained is False, (
        "quiet-cycle count alone must not arm inside the publication-lag margin"
    )
    fetches_inside_margin = len(provider.get_bars_calls)
    assert fetches_inside_margin == 5, "fetching must continue inside the margin"

    runner._now = lambda: base_end + timedelta(minutes=13)  # past the 12min margin
    runner.run_cycle()  # quiet cycle past the margin → arms
    assert runner._intraday_session_drained is True
    runner.run_cycle()
    assert len(provider.get_bars_calls) == fetches_inside_margin + 1, (
        "post-arm closed-market cycles must not fetch"
    )


def test_intraday_drained_flag_waits_for_late_published_final_bar(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Round-2 regression: the session's final bar can publish AFTER the first
    closed-market cycle (feed aggregate lag). The drained flag must not arm on
    the first quiet cycle — the straggler must still be fetched and evaluated
    by a later cycle, and only then may the early-out arm."""
    make_regime_config_intraday(strategy_config_dir)
    base_end = datetime(2026, 6, 10, 19, 50, tzinfo=UTC)
    straggler_end = base_end + timedelta(minutes=1)
    runner, broker, provider, event_store = _build_hr2_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        bars_by_symbol={
            "SPY": build_intraday_barset([10.0, 10.0], end=base_end, freq="1min"),
            "SHY": build_intraday_barset([10.0, 10.0], end=base_end, freq="1min"),
        },
        market_open=False,
    )
    pinned = straggler_end.replace(tzinfo=UTC) + timedelta(hours=1)
    runner._now = lambda: pinned

    runner.run_cycle()  # evaluates the latest published completed bar
    assert len(provider.get_bars_calls) == 1

    runner.run_cycle()  # quiet cycle 1: straggler not yet published — no arm
    assert runner._intraday_session_drained is False

    # The feed publishes the session's true final bar late (aggregate lag).
    provider._bars_by_symbol = {
        "SPY": build_intraday_barset([10.0, 10.0, 10.0], end=straggler_end, freq="1min"),
        "SHY": build_intraday_barset([10.0, 10.0, 10.0], end=straggler_end, freq="1min"),
    }

    runner.run_cycle()  # straggler is new → evaluated, quiet count restarts
    assert runner._last_processed_bar_at is not None
    assert runner._last_processed_bar_at == straggler_end
    assert runner._intraday_session_drained is False

    runner.run_cycle()  # quiet cycle 1 (post-straggler)
    assert runner._intraday_session_drained is False
    runner.run_cycle()  # quiet cycle 2 + margin → arm
    assert runner._intraday_session_drained is True


def test_intraday_processed_intent_keys_remain_submission_backstop(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """HR-2 keeps _processed_intent_keys as the submission backstop: even if
    the watermark is lost and the same bar re-evaluates, an intent already
    submitted for that bar is not re-submitted."""
    config_path = make_regime_config_intraday(strategy_config_dir)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "allocation_pct: 1.0", "allocation_pct: 0.80"
        ),
        encoding="utf-8",
    )
    runner, broker, provider, event_store = _build_hr2_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        bars_by_symbol={
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        },
        market_open=True,
    )
    from tests.milodex._helpers.promotion import seed_frozen_manifest

    seed_frozen_manifest(event_store, config_path)
    pin_clock_after_latest_bar(runner, provider)

    first = runner.run_cycle()
    assert len(first) == 1
    assert len(broker.submit_calls) == 1

    runner._last_processed_bar_at = None  # simulate watermark loss → same bar re-evaluates

    second = runner.run_cycle()
    assert second == []
    assert len(broker.submit_calls) == 1, (
        "the per-bar intent-key dedup must block a duplicate submission"
    )
    assert len(event_store.list_trades()) == 1


# ---------------------------------------------------------------------------
# Portfolio-snapshot wiring (closes analytics/snapshots.py scaffolded marker)
# ---------------------------------------------------------------------------


def test_runner_records_portfolio_snapshot_on_controlled_shutdown(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Controlled shutdown captures a portfolio_snapshots row keyed on session_id.

    Closes the `# scaffolded:` marker on `analytics/snapshots.py` —
    `record_daily_snapshot` was a working primitive without a production
    caller; the runner is the live-side caller.
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_500.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_500.0,
            daily_pnl=42.5,
        ),
        positions=[
            Position(
                symbol="SPY",
                quantity=1.0,
                avg_entry_price=500.0,
                current_price=520.0,
                market_value=520.0,
                unrealized_pnl=20.0,
                unrealized_pnl_pct=0.04,
            )
        ],
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    runner.shutdown(mode="controlled")

    snapshots = event_store.list_portfolio_snapshots_for_session(runner.session_id)
    assert len(snapshots) == 1, (
        "Controlled shutdown should record exactly one portfolio_snapshots row "
        "(R-XC-016 closure for analytics/snapshots.py)."
    )
    snapshot = snapshots[0]
    assert snapshot.session_id == runner.session_id
    assert snapshot.strategy_id == "regime.daily.sma200_rotation.spy_shy.v1"
    assert snapshot.equity == 10_500.0
    assert snapshot.cash == 10_000.0
    assert snapshot.daily_pnl == 42.5
    assert any(p["symbol"] == "SPY" and p["quantity"] == 1.0 for p in snapshot.positions)


def test_runner_records_portfolio_snapshot_on_kill_switch_shutdown(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Kill-switch shutdown still produces a snapshot — equity at halt time is forensic data."""
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=9_700.0,
            cash=9_700.0,
            buying_power=9_700.0,
            portfolio_value=9_700.0,
            daily_pnl=-300.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    runner.shutdown(mode="kill_switch")

    snapshots = event_store.list_portfolio_snapshots_for_session(runner.session_id)
    assert len(snapshots) == 1
    assert snapshots[0].equity == 9_700.0
    assert snapshots[0].daily_pnl == -300.0


# ---------------------------------------------------------------------------
# CI-2 (PHASE2_PLANNING.md): strategy_runs row is written at startup, not
# only at shutdown, so `WHERE ended_at IS NULL` enumerates active runners.
# ---------------------------------------------------------------------------


def test_runner_startup_creates_strategy_runs_row_with_null_ended_at(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Constructing a StrategyRunner records an open strategy_runs row.

    Before this test, the row was only written at shutdown — the canonical
    "is a runner active?" query (`WHERE ended_at IS NULL`) returned zero
    rows even when a runner was actively recording cycle explanations.
    Closes CI-2 from docs/PHASE2_PLANNING.md.
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )

    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    runs = event_store.list_strategy_runs()
    assert len(runs) == 1
    assert runs[0].session_id == runner.session_id
    assert runs[0].strategy_id == "regime.daily.sma200_rotation.spy_shy.v1"
    assert runs[0].ended_at is None
    assert runs[0].exit_reason is None


def test_runner_shutdown_updates_open_strategy_runs_row_without_duplicating(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Shutdown UPDATEs the row written at startup; never produces a duplicate.

    The audit shape is one row per session. Two-row history would break the
    canonical "one strategy_run row per session" invariant relied on by the
    existing reporting surfaces (see CLI report.py / SC-6 evidence assembly).
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )
    assert len(event_store.list_strategy_runs()) == 1

    runner.shutdown(mode="controlled")

    runs = event_store.list_strategy_runs()
    assert len(runs) == 1, "shutdown must UPDATE the existing row, not INSERT a duplicate"
    assert runs[0].session_id == runner.session_id
    assert runs[0].ended_at is not None
    assert runs[0].exit_reason == "controlled_stop"


def test_runner_active_session_findable_via_ended_at_is_null_query(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """`WHERE ended_at IS NULL` enumerates exactly the currently-active runners.

    The operational point of CI-2: an operator or audit tool can ask the
    event store "what's running?" and get a useful answer mid-session.

    Production-faithful setup: a runner that previously ran and shut down
    cleanly, followed by a runner that is currently alive. The advisory lock
    in `cli/commands/strategy.py` (per ADR 0026, scoped per strategy_id)
    prevents two runners for the same strategy from coexisting in real use,
    and startup reconciliation closes any prior `ended_at IS NULL` rows for
    the same strategy_id — together those guarantee the open-row set tracks
    exactly the live runners.
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    completed_runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )
    completed_runner.shutdown(mode="controlled")
    active_runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    active_sessions = [
        run.session_id for run in event_store.list_strategy_runs() if run.ended_at is None
    ]
    assert active_sessions == [active_runner.session_id]


def test_runner_snapshot_failure_does_not_block_shutdown(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch,
):
    """A broker failure during snapshot must not prevent strategy_run row write.

    The snapshot is forensic; the strategy-run row is the canonical session
    record. Losing the snapshot is a degraded outcome but losing the run row
    would be a worse one. The shutdown path must complete either way.
    """
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )

    class BrokenBroker(StubBroker):
        def get_account(self):  # type: ignore[override]
            raise RuntimeError("simulated broker connectivity failure")

    broker = BrokenBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    runner.shutdown(mode="controlled")

    runs = event_store.list_strategy_runs()
    assert len(runs) == 1, "shutdown must still record the strategy_run row even if snapshot fails"
    assert runs[0].exit_reason == "controlled_stop"
    snapshots = event_store.list_portfolio_snapshots_for_session(runner.session_id)
    assert snapshots == [], "snapshot failure should leave no half-written row"


def test_runner_startup_reconciles_orphan_strategy_runs_for_same_strategy(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    # A runner that dies without writing ended_at (machine sleep, terminal
    # close, OOM, OS crash) leaves a strategy_runs row with ended_at=NULL.
    # Reports that count active sessions by `WHERE ended_at IS NULL` then
    # see a phantom session that lasts forever. Startup of the next runner
    # for the same strategy_id must close out those orphans with
    # exit_reason='orphan_recovered'.
    from milodex.core.event_store import StrategyRunEvent

    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )

    strategy_id = "regime.daily.sma200_rotation.spy_shy.v1"
    other_strategy_id = "meanrev.daily.pullback_rsi2.curated_largecap.v1"
    orphan_started_at = datetime(2026, 5, 6, 15, 2, 57, tzinfo=UTC)
    other_orphan_started_at = datetime(2026, 5, 6, 15, 3, 8, tzinfo=UTC)

    event_store.append_strategy_run(
        StrategyRunEvent(
            session_id="orphan-session-of-killed-runner",
            strategy_id=strategy_id,
            started_at=orphan_started_at,
            ended_at=None,
            exit_reason=None,
            metadata={"stage": "paper"},
        )
    )
    event_store.append_strategy_run(
        StrategyRunEvent(
            session_id="orphan-session-of-different-strategy",
            strategy_id=other_strategy_id,
            started_at=other_orphan_started_at,
            ended_at=None,
            exit_reason=None,
            metadata={"stage": "backtest"},
        )
    )

    runner = StrategyRunner(
        strategy_id=strategy_id,
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    runs_by_session = {r.session_id: r for r in event_store.list_strategy_runs()}

    reconciled = runs_by_session["orphan-session-of-killed-runner"]
    assert reconciled.ended_at is not None, (
        "orphan row for the same strategy must have ended_at populated at runner startup"
    )
    assert reconciled.exit_reason == "orphan_recovered"

    # An orphan for a *different* strategy is not this runner's responsibility
    # to clean up — leave it for that strategy's next startup.
    untouched = runs_by_session["orphan-session-of-different-strategy"]
    assert untouched.ended_at is None
    assert untouched.exit_reason is None

    # The new runner's own row is still open (ended_at=NULL) — startup
    # reconciliation must not accidentally close the row it just appended.
    fresh = runs_by_session[runner.session_id]
    assert fresh.ended_at is None
    assert fresh.exit_reason is None

    runner.shutdown(mode="controlled")


# ---------------------------------------------------------------------------
# PR #54 (2026-05-07 audit): runner lifecycle hardening
#   Spec A — graceful shutdown (orphan-session prevention)
#   Spec B — market-hours gate before fetch
#   Spec C — poll_interval_seconds default + config plumbing
# ---------------------------------------------------------------------------


def _build_crash_runner(
    *,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    market_open: bool = False,
):
    """Build a minimal runner wired for lifecycle-hardening tests."""
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        market_open=market_open,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
        poll_interval_seconds=0.0,
    )
    return runner, broker, provider, event_store


# --- Spec A: graceful shutdown ---


def test_runner_run_writes_crashed_exit_reason_on_unhandled_exception(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Unhandled exception in run_cycle must leave exit_reason starting with 'crashed:'.

    Before this fix the finally block in run() did not call shutdown(), so
    strategy_runs rows were left with ended_at=NULL and exit_reason=NULL when
    the runner died on an uncaught exception.
    """
    runner, _, _, event_store = _build_crash_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )

    boom = RuntimeError("simulated provider crash")

    from milodex.strategies import runner as runner_module

    def _exploding_fetch():
        raise boom

    monkeypatch.setattr(runner, "_fetch_bars_by_symbol", _exploding_fetch)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="simulated provider crash"):
        runner.run()

    runs = event_store.list_strategy_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.ended_at is not None, "ended_at must be set even when runner crashes"
    assert run.exit_reason is not None and run.exit_reason.startswith("crashed:"), (
        f"exit_reason should start with 'crashed:'; got {run.exit_reason!r}"
    )


def test_runner_run_writes_interrupted_exit_reason_on_keyboard_interrupt(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """KeyboardInterrupt in run() must leave exit_reason='interrupted'.

    Simulates a raw KeyboardInterrupt that bypasses _handle_sigint (e.g.
    the interrupt arrives while the loop is in time.sleep() before the signal
    handler has been installed, or in some other pre-handler window).
    """
    runner, _, _, event_store = _build_crash_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
    )

    from milodex.strategies import runner as runner_module

    def _ki_fetch():
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "_fetch_bars_by_symbol", _ki_fetch)
    monkeypatch.setattr(runner_module.time, "sleep", lambda _: None)

    # KeyboardInterrupt is caught internally; run() returns normally.
    runner.run()

    runs = event_store.list_strategy_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run.ended_at is not None, "ended_at must be set on KeyboardInterrupt exit"
    assert run.exit_reason == "interrupted", (
        f"exit_reason should be 'interrupted'; got {run.exit_reason!r}"
    )


# --- Spec B: market-hours gate before fetch ---


def test_runner_market_gate_skips_fetch_when_closed_and_watermark_set(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """No fetch when market is closed AND watermark is already advanced.

    Post-close idle polling (weekends, holidays, after lockin) must not
    call _fetch_bars_by_symbol at all.
    """
    runner, _, provider, _ = _build_crash_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )

    # Manually advance the watermark to simulate the lockin having completed.
    runner._last_processed_bar_at = datetime(2026, 5, 7, 21, 0, 0, tzinfo=UTC)

    initial_fetch_count = len(provider.get_bars_calls)
    result = runner.run_cycle()

    assert result == []
    assert len(provider.get_bars_calls) == initial_fetch_count, (
        "_fetch_bars_by_symbol must not be called when market is closed and watermark is set"
    )


def test_runner_market_gate_allows_fetch_when_market_open(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Intraday strategies still fetch when the market is open."""
    make_regime_config_intraday(strategy_config_dir)
    runner, _, provider, _ = _build_crash_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=True,
    )

    # Even with a watermark set (unusual but possible), an intraday open-market cycle fetches.
    runner._last_processed_bar_at = datetime(2026, 5, 7, 21, 0, 0, tzinfo=UTC)

    initial_fetch_count = len(provider.get_bars_calls)
    runner.run_cycle()

    assert len(provider.get_bars_calls) == initial_fetch_count + 1, (
        "_fetch_bars_by_symbol must be called for open-market intraday bars"
    )


def test_runner_market_gate_allows_fetch_when_closed_but_no_watermark(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Fetch IS called when market is closed but watermark has not been set.

    Cold-start scenario: first post-close cycle before lockin completes.
    The gate must not suppress the fetch because the lockin needs to observe
    the bar in order to advance the watermark.
    """
    runner, _, provider, _ = _build_crash_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )

    assert runner._last_processed_bar_at is None

    initial_fetch_count = len(provider.get_bars_calls)
    runner.run_cycle()

    assert len(provider.get_bars_calls) == initial_fetch_count + 1, (
        "_fetch_bars_by_symbol must be called when market is closed but watermark not yet set"
    )


# --- Spec C: poll_interval_seconds default + config plumbing ---


def test_runner_poll_interval_defaults_to_60_for_1d_bar_size(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """A 1D strategy with no explicit poll_interval_seconds defaults to 60s."""
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    # strategy_config_dir has a 1D bar_size config; no poll_interval_seconds in YAML.
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
        # No poll_interval_seconds arg → should derive from bar_size.
    )

    assert runner._poll_interval_seconds == 60.0, (
        f"1D bar_size should default to 60s poll interval; got {runner._poll_interval_seconds}"
    )


def test_runner_poll_interval_explicit_arg_overrides_bar_size_default(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
    """Explicit poll_interval_seconds arg overrides the bar_size-derived default."""
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
        poll_interval_seconds=10.0,
    )

    assert runner._poll_interval_seconds == 10.0, (
        f"explicit poll_interval_seconds=10.0 should win over bar_size default; "
        f"got {runner._poll_interval_seconds}"
    )


def test_runner_poll_interval_yaml_override_wins_over_bar_size_default(
    tmp_path: Path,
    risk_defaults_file: Path,
):
    """YAML tempo.poll_interval_seconds overrides the bar_size-derived default."""
    config_dir = tmp_path / "configs_poll"
    config_dir.mkdir()
    (config_dir / "poll_override.yaml").write_text(
        """
strategy:
  id: "regime.daily.sma200_rotation.poll_test.v1"
  family: "regime"
  template: "daily.sma200_rotation"
  variant: "poll_test"
  version: 1
  description: "poll_interval YAML override test"
  enabled: true
  universe:
    - "SPY"
    - "SHY"
  parameters:
    ma_filter_length: 3
    risk_on_symbol: "SPY"
    risk_off_symbol: "SHY"
    allocation_pct: 1.0
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
    poll_interval_seconds: 42.0
  risk:
    max_position_pct: 1.0
    max_positions: 1
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "paper"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )

    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.poll_test.v1",
        config_dir=config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
        # No explicit arg → YAML override should win.
    )

    assert runner._poll_interval_seconds == 42.0, (
        f"tempo.poll_interval_seconds=42.0 in YAML should override bar_size default; "
        f"got {runner._poll_interval_seconds}"
    )


# ---------------------------------------------------------------------------
# HR-3 (R-P1-3): NY-day rollover re-reconciliation
# ---------------------------------------------------------------------------
#
# Four pins:
#   (a) Same-NY-day cycles do not re-reconcile (call-count pin).
#   (b) NY-day rollover triggers exactly one re-reconcile on the next cycle.
#   (c) Rollover during closed market + intraday_session_drained: the
#       re-reconcile fires on the FIRST cycle of the new NY day, even though
#       the drained early-out fires immediately after.
#   (d) Reconcile failure on rollover preserves the startup-path posture
#       (exception propagates; _last_reconcile_ny_day is NOT advanced).
# ---------------------------------------------------------------------------


def _build_hr3_runner(
    *,
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    market_open: bool = True,
    bar_size: str = "1Min",
):
    """Intraday runner wired for HR-3 tests; clock is always monkey-patchable."""
    make_regime_config_intraday(strategy_config_dir, bar_size=bar_size)
    provider = StubProvider(
        {
            "SPY": build_barset([10.0, 10.0, 10.0]),
            "SHY": build_barset([10.0, 10.0, 10.0]),
        }
    )
    broker = StubBroker(
        account=AccountInfo(
            equity=10_000.0,
            cash=10_000.0,
            buying_power=10_000.0,
            portfolio_value=10_000.0,
            daily_pnl=0.0,
        ),
        market_open=market_open,
    )
    service, event_store, _ = build_service(
        tmp_path=tmp_path,
        broker=broker,
        provider=provider,
        risk_defaults_file=risk_defaults_file,
    )
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )
    pin_clock_after_latest_bar(runner, provider)
    return runner, broker, provider, event_store


def test_hr3_same_ny_day_cycles_do_not_re_reconcile(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """(a) Multiple run_cycle calls on the same NY trading day must not
    trigger a rollover re-reconcile; the startup reconcile is the only one.

    Pin: run_reconciliation call count stays at 1 (startup) across N cycles.
    """
    from milodex.strategies import runner as runner_module

    call_count = {"n": 0}
    original = runner_module.run_reconciliation

    def counting_reconcile(**kwargs):
        call_count["n"] += 1
        return original(**kwargs)

    monkeypatch.setattr(runner_module, "run_reconciliation", counting_reconcile)

    runner, _, _, _ = _build_hr3_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=True,
    )
    # startup reconcile fires on first run_cycle
    runner.run_cycle()
    assert call_count["n"] == 1, "startup reconcile must fire on the first cycle"

    # Same NY day — additional cycles must not re-reconcile
    runner.run_cycle()
    runner.run_cycle()
    assert call_count["n"] == 1, (
        "same-NY-day cycles must not trigger rollover re-reconciliation"
    )


def test_hr3_ny_day_rollover_triggers_exactly_one_re_reconcile(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """(b) When the NY trading day advances between cycles, exactly one
    additional reconciliation run is triggered on the first post-rollover cycle.
    Subsequent same-day cycles do not re-reconcile again.

    Technique: pin the runner clock to a specific ET date (day 1), run a cycle
    to complete startup reconcile, then advance the clock to the next ET day
    (day 2) and verify one additional call fires — then verify no further calls
    on day 2.
    """
    from zoneinfo import ZoneInfo

    from milodex.strategies import runner as runner_module

    call_count = {"n": 0}
    original = runner_module.run_reconciliation

    def counting_reconcile(**kwargs):
        call_count["n"] += 1
        return original(**kwargs)

    monkeypatch.setattr(runner_module, "run_reconciliation", counting_reconcile)

    runner, _, _, _ = _build_hr3_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=True,
    )
    # Day 1: 14:00 ET on an arbitrary Tuesday → NY trading day = "2026-06-09"
    et_tz = ZoneInfo("America/New_York")
    day1 = datetime(2026, 6, 9, 14, 0, 0, tzinfo=et_tz).astimezone(UTC)
    day2 = datetime(2026, 6, 10, 14, 0, 0, tzinfo=et_tz).astimezone(UTC)

    runner._now = lambda: day1
    runner.run_cycle()
    assert call_count["n"] == 1, "startup reconcile must fire on the first cycle"
    assert runner._last_reconcile_ny_day == "2026-06-09"

    # Same day: no extra reconcile
    runner.run_cycle()
    assert call_count["n"] == 1

    # Day 2: first cycle of the new NY trading day
    runner._now = lambda: day2
    runner.run_cycle()
    assert call_count["n"] == 2, (
        "first cycle on a new NY trading day must trigger exactly one rollover reconcile"
    )
    assert runner._last_reconcile_ny_day == "2026-06-10"

    # Day 2: subsequent cycles must not re-reconcile
    runner.run_cycle()
    runner.run_cycle()
    assert call_count["n"] == 2, (
        "subsequent same-day cycles after rollover must not reconcile again"
    )


def test_hr3_rollover_during_closed_market_drained_flag(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """(c) An intraday runner idling overnight behind the drained flag still
    re-reconciles when the NY day rolls. The re-reconcile fires here and then
    the drained early-out returns [] — the reconciliation row is fresh before
    the next open-market evaluation cycle.

    Ordering: _maybe_rollover_reconciliation() is called BEFORE every
    early-out, so the reconcile happens on the first cycle of the new NY day
    regardless of the drained flag.
    """
    from zoneinfo import ZoneInfo

    from milodex.strategies import runner as runner_module

    call_count = {"n": 0}
    original = runner_module.run_reconciliation

    def counting_reconcile(**kwargs):
        call_count["n"] += 1
        return original(**kwargs)

    monkeypatch.setattr(runner_module, "run_reconciliation", counting_reconcile)

    runner, broker, _, _ = _build_hr3_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=False,
    )
    et_tz = ZoneInfo("America/New_York")
    day1_night = datetime(2026, 6, 9, 22, 0, 0, tzinfo=et_tz).astimezone(UTC)
    day2_night = datetime(2026, 6, 10, 22, 0, 0, tzinfo=et_tz).astimezone(UTC)

    runner._now = lambda: day1_night

    # Startup reconcile fires on first cycle; session gets drained after 3 cycles
    # (already_seen → arm the early-out) — force the drained state directly
    # to avoid depending on the quiet-cycle count / margin wall-clock logic.
    runner.run_cycle()
    assert call_count["n"] == 1
    runner._intraday_session_drained = True
    runner._last_reconcile_ny_day = "2026-06-09"

    # Drained overnight cycle on day 1: early-out, no re-reconcile
    result = runner.run_cycle()
    assert result == []
    assert call_count["n"] == 1, "drained cycle on same day must not reconcile"

    # NY day rolls to day 2 while still drained and market closed
    runner._now = lambda: day2_night
    result_day2 = runner.run_cycle()
    assert result_day2 == [], "drained early-out still fires (market closed, session drained)"
    assert call_count["n"] == 2, (
        "rollover reconcile must fire BEFORE the drained early-out on the "
        "first cycle of the new NY day"
    )
    assert runner._last_reconcile_ny_day == "2026-06-10"

    # Subsequent closed-market drained cycles on day 2: no further reconcile
    runner.run_cycle()
    assert call_count["n"] == 2, "no further reconcile on same day after rollover"


def test_hr3_reconcile_failure_on_rollover_preserves_startup_posture(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """(d) If run_reconciliation raises on a rollover attempt, the exception
    propagates from run_cycle — same posture as a startup reconcile failure
    (no try/except wrapping). _last_reconcile_ny_day is NOT advanced.

    A subsequent cycle on the same new NY day will retry the reconcile
    (since _last_reconcile_ny_day still holds the prior day).
    """
    from zoneinfo import ZoneInfo

    from milodex.strategies import runner as runner_module

    original = runner_module.run_reconciliation
    attempt = {"n": 0}

    def failing_on_rollover(**kwargs):
        attempt["n"] += 1
        # Allow the startup reconcile (first call) to succeed; fail on rollover
        if attempt["n"] == 1:
            return original(**kwargs)
        raise RuntimeError("simulated DB failure on rollover reconcile")

    monkeypatch.setattr(runner_module, "run_reconciliation", failing_on_rollover)

    runner, _, _, _ = _build_hr3_runner(
        tmp_path=tmp_path,
        strategy_config_dir=strategy_config_dir,
        risk_defaults_file=risk_defaults_file,
        market_open=True,
    )
    et_tz = ZoneInfo("America/New_York")
    day1 = datetime(2026, 6, 9, 14, 0, 0, tzinfo=et_tz).astimezone(UTC)
    day2 = datetime(2026, 6, 10, 14, 0, 0, tzinfo=et_tz).astimezone(UTC)

    runner._now = lambda: day1
    runner.run_cycle()
    assert runner._last_reconcile_ny_day == "2026-06-09"

    # Day 2: rollover reconcile fails → exception propagates from run_cycle
    runner._now = lambda: day2
    with pytest.raises(RuntimeError, match="simulated DB failure on rollover reconcile"):
        runner.run_cycle()

    # _last_reconcile_ny_day is NOT advanced — the runner still thinks it's on
    # day 1, so the next cycle will retry the reconcile.
    assert runner._last_reconcile_ny_day == "2026-06-09", (
        "a failed rollover reconcile must not advance _last_reconcile_ny_day; "
        "the next cycle must retry"
    )
