"""Tests for the strategy runtime runner."""

from __future__ import annotations

import signal
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
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
from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.execution import ExecutionService
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
  max_total_exposure_pct: 1.00
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


def test_runner_submits_regime_signal_through_execution_service(
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

    seed_frozen_manifest(event_store, strategy_config_dir / "regime_runner.yaml")

    results = runner.run_cycle()
    runner.shutdown(mode="controlled")

    assert len(results) == 1
    assert broker.submit_calls[0]["symbol"] == "SHY"
    assert provider.get_latest_bar_calls == ["SHY"]
    assert [record.session_id for record in event_store.list_explanations()] == [runner.session_id]
    assert [record.session_id for record in event_store.list_trades()] == [runner.session_id]
    assert event_store.list_strategy_runs()[0].exit_reason == "controlled_stop"


def test_runner_records_no_action_explanation_when_strategy_holds_target(
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
    runner = StrategyRunner(
        strategy_id="regime.daily.sma200_rotation.spy_shy.v1",
        config_dir=strategy_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

    results = runner.run_cycle()
    runner.shutdown(mode="controlled")

    assert results == []
    explanations = event_store.list_explanations()
    assert len(explanations) == 1
    assert explanations[0].decision_type == "no_trade"
    assert explanations[0].status in {"no_signal", "no_action"}
    assert explanations[0].session_id == runner.session_id
    assert event_store.list_trades() == []


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


def test_runner_ignores_non_strategy_yaml_when_resolving_config(
    tmp_path: Path,
    strategy_config_dir: Path,
    risk_defaults_file: Path,
):
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

    seed_frozen_manifest(event_store, meanrev_config_dir / "meanrev_runner.yaml")
    runner = StrategyRunner(
        strategy_id="meanrev.daily.pullback_rsi2.test_runner.v1",
        config_dir=meanrev_config_dir,
        broker_client=broker,
        data_provider=provider,
        execution_service=service,
        event_store=event_store,
    )

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

    # Seed a paper BUY trade for SPY 5 days ago
    buy_date = datetime.now(tz=UTC) - timedelta(days=5)
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
            status="filled",
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
    assert entry_state["SPY"]["held_days"] >= 5


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

    The fix still allows run_cycle to evaluate (and possibly submit) during
    market hours — that's the runner's intended design — but withholds the
    ``_last_processed_bar_at`` watermark update until the market is closed.
    That way the post-close cycle finds ``_last_processed_bar_at = None``
    (or ``< today``) and re-evaluates the now-finalized bar.
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
    )

    # --- Cycle 1: market is open, bar is in-progress -------------------------
    results_during_market = runner.run_cycle()

    assert len(results_during_market) >= 1, (
        "Runner must still evaluate during market hours; only the watermark update is withheld."
    )
    assert runner._last_processed_bar_at is None, (
        "In-progress bar must not advance _last_processed_bar_at; doing so "
        "would suppress the same-timestamp finalized-bar evaluation below."
    )

    # --- Cycle 2: market closed, same bar is now final ------------------------
    broker._market_open = False
    results_post_close = runner.run_cycle()

    assert len(results_post_close) >= 1, (
        "Once the market closes on the same-day bar, the runner must "
        "re-evaluate it instead of skipping forever."
    )
    assert runner._last_processed_bar_at is not None, (
        "Post-close evaluation must advance the watermark so subsequent "
        "cycles on the same bar are correctly treated as already-seen."
    )

    # --- Cycle 3: still post-close, same bar, watermark now set --------------
    results_next_poll = runner.run_cycle()
    assert results_next_poll == [], (
        "Once the watermark is set, further polls on the same bar must be treated as already-seen."
    )
