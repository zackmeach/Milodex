"""Behavioral tests for the five risk rules that had zero prior coverage.

Rules under test
----------------
- _check_daily_loss            (daily_loss_cap_exceeded reason code)
- _check_order_value           (max_order_value_exceeded)
- _check_single_position_limit (max_single_position_exceeded)
- _check_total_exposure        (max_total_exposure_exceeded)
- _check_concurrent_positions  (max_concurrent_positions_exceeded)

All tests drive through ExecutionService.preview() so that trading-mode and
market-hours guards auto-pass, leaving only the rule under test as the
blocking condition.

Risk config used throughout
---------------------------
  kill_switch.max_drawdown_pct   = 0.10   ($1 000 on $10 k portfolio)
  daily_limits.max_daily_loss_pct = 0.03  ($300 on $10 k portfolio)
  order_safety.max_order_value_pct = 0.15 ($1 500 on $10 k portfolio)
  portfolio.max_single_position_pct = 0.20 ($2 000 on $10 k portfolio)
  portfolio.max_total_exposure_pct = 0.80  ($8 000 on $10 k portfolio)
  portfolio.max_concurrent_positions = 3
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderType,
    Position,
)
from milodex.data.models import Bar
from milodex.execution import ExecutionService, TradeIntent
from milodex.execution.state import KillSwitchStateStore

# ---------------------------------------------------------------------------
# Internal stubs
# ---------------------------------------------------------------------------


class _StubBroker:
    def __init__(
        self,
        *,
        account: AccountInfo,
        positions: list[Position] | None = None,
    ) -> None:
        self._account = account
        self._positions = positions or []

    def get_account(self) -> AccountInfo:
        return self._account

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:  # noqa: ARG002
        return []

    def is_market_open(self) -> bool:
        return True

    def submit_order(self, **_kwargs) -> Order:
        raise AssertionError("submit_order must not be called from preview()")

    def get_order(self, order_id: str) -> Order:  # noqa: ARG002
        raise AssertionError("get_order must not be called from preview()")

    def cancel_order(self, order_id: str) -> bool:  # noqa: ARG002
        return False


class _StubProvider:
    def __init__(self, bar: Bar) -> None:
        self._bar = bar

    def get_latest_bar(self, symbol: str) -> Bar:  # noqa: ARG002
        return self._bar


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def _make_position(
    symbol: str,
    *,
    market_value: float,
    quantity: float = 1.0,
) -> Position:
    price = market_value / quantity
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=price,
        current_price=price,
        market_value=market_value,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def _build_service(
    tmp_path: Path,
    risk_defaults_file: Path,
    bar: Bar,
    account: AccountInfo,
    *,
    positions: list[Position] | None = None,
) -> ExecutionService:
    broker = _StubBroker(account=account, positions=positions)
    provider = _StubProvider(bar)
    store = KillSwitchStateStore(tmp_path / "kill_switch.json")
    return ExecutionService(
        broker_client=broker,
        data_provider=provider,
        risk_defaults_path=risk_defaults_file,
        kill_switch_store=store,
    )


def _buy(symbol: str = "SPY", qty: float = 5.0) -> TradeIntent:
    return TradeIntent(
        symbol=symbol, side=OrderSide.BUY, quantity=qty, order_type=OrderType.MARKET
    )


def _sell(symbol: str = "SPY", qty: float = 5.0) -> TradeIntent:
    return TradeIntent(
        symbol=symbol, side=OrderSide.SELL, quantity=qty, order_type=OrderType.MARKET
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def risk_defaults_file(tmp_path: Path) -> Path:
    """Risk config with known thresholds (see module docstring)."""
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
def fresh_bar() -> Bar:
    """Bar at price=100.0, 30 seconds old — within staleness limit."""
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=30),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=10_000,
        vwap=100.0,
    )


@pytest.fixture()
def healthy_account() -> AccountInfo:
    """portfolio_value=$10,000, positive daily P&L — passes all portfolio rules."""
    return AccountInfo(
        equity=10_050.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=50.0,
    )


# ===========================================================================
# _check_daily_loss
# ===========================================================================


class TestDailyLossCheck:
    """portfolio_value=$10,000 · daily_loss_cap=3% ($300) · kill_switch=10% ($1,000)."""

    def test_daily_loss_cap_exceeded_blocks(
        self, tmp_path: Path, risk_defaults_file: Path, fresh_bar: Bar
    ) -> None:
        # daily_pnl=-350 → equity_base=10,350 → loss=3.38% > 3% cap, < 10% kill switch
        account = AccountInfo(
            equity=9_650.0,
            cash=8_000.0,
            buying_power=8_000.0,
            portfolio_value=10_000.0,
            daily_pnl=-350.0,
        )
        service = _build_service(tmp_path, risk_defaults_file, fresh_bar, account)

        result = service.preview(_buy())

        assert "daily_loss_cap_exceeded" in result.risk_decision.reason_codes
        assert "kill_switch_threshold_breached" not in result.risk_decision.reason_codes

    def test_daily_loss_within_cap_is_allowed(
        self, tmp_path: Path, risk_defaults_file: Path, fresh_bar: Bar
    ) -> None:
        # daily_pnl=-200 → equity_base=10,200 → loss=1.96% < 3% cap
        account = AccountInfo(
            equity=9_800.0,
            cash=8_000.0,
            buying_power=8_000.0,
            portfolio_value=10_000.0,
            daily_pnl=-200.0,
        )
        service = _build_service(tmp_path, risk_defaults_file, fresh_bar, account)

        result = service.preview(_buy())

        assert "daily_loss_cap_exceeded" not in result.risk_decision.reason_codes
        assert "kill_switch_threshold_breached" not in result.risk_decision.reason_codes

    def test_strategy_daily_loss_cap_tighter_than_global(
        self, tmp_path: Path, risk_defaults_file: Path, fresh_bar: Bar
    ) -> None:
        # Strategy cap=2%, global=3%. daily_pnl=-250 → loss≈2.44% > 2% strategy cap.
        strategy_path = tmp_path / "strategy.yaml"
        strategy_path.write_text(
            """
strategy:
  name: tight_cap_strategy
  version: 1
  description: Tight daily loss cap
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.20
    max_positions: 3
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
        account = AccountInfo(
            equity=9_750.0,
            cash=8_000.0,
            buying_power=8_000.0,
            portfolio_value=10_000.0,
            daily_pnl=-250.0,
        )
        service = _build_service(tmp_path, risk_defaults_file, fresh_bar, account)

        result = service.preview(
            TradeIntent(
                symbol="SPY",
                side=OrderSide.BUY,
                quantity=5,
                order_type=OrderType.MARKET,
                strategy_config_path=strategy_path,
            )
        )

        assert "daily_loss_cap_exceeded" in result.risk_decision.reason_codes


# ===========================================================================
# _check_order_value
# ===========================================================================


class TestOrderValueCheck:
    """portfolio_value=$10,000 · max_order_value=15% ($1,500) · price=$100."""

    def test_order_value_exceeded_blocks(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # qty=20 → estimated_order_value=$2,000 > $1,500 limit
        service = _build_service(tmp_path, risk_defaults_file, fresh_bar, healthy_account)

        result = service.preview(_buy(qty=20.0))

        assert "max_order_value_exceeded" in result.risk_decision.reason_codes

    def test_order_value_within_limit_is_allowed(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # qty=10 → estimated_order_value=$1,000 < $1,500 limit
        service = _build_service(tmp_path, risk_defaults_file, fresh_bar, healthy_account)

        result = service.preview(_buy(qty=10.0))

        assert "max_order_value_exceeded" not in result.risk_decision.reason_codes


# ===========================================================================
# _check_single_position_limit
# ===========================================================================


class TestSinglePositionLimitCheck:
    """portfolio_value=$10,000 · max_single_position=20% ($2,000) · price=$100."""

    def test_single_position_limit_exceeded_blocks(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # Existing SPY=$1,200; BUY qty=10 ($1,000) → projected=$2,200 > $2,000 limit
        # order_value=$1,000 < $1,500 → passes that check
        positions = [_make_position("SPY", market_value=1_200.0, quantity=12.0)]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_buy("SPY", qty=10.0))

        assert "max_single_position_exceeded" in result.risk_decision.reason_codes

    def test_single_position_within_limit_is_allowed(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # Existing SPY=$500; BUY qty=10 ($1,000) → projected=$1,500 < $2,000 limit
        positions = [_make_position("SPY", market_value=500.0, quantity=5.0)]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_buy("SPY", qty=10.0))

        assert "max_single_position_exceeded" not in result.risk_decision.reason_codes

    def test_single_position_sell_reduces_projected_value(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # Existing SPY=$2,500 (above limit); SELL qty=10 ($1,000) → projected=$1,500 < $2,000
        positions = [_make_position("SPY", market_value=2_500.0, quantity=25.0)]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_sell("SPY", qty=10.0))

        assert "max_single_position_exceeded" not in result.risk_decision.reason_codes


# ===========================================================================
# _check_total_exposure
# ===========================================================================


class TestTotalExposureCheck:
    """portfolio_value=$10,000 · max_total_exposure=80% ($8,000) · price=$100."""

    def test_total_exposure_exceeded_blocks(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # Existing SPY=$7,500; BUY AAPL qty=10 ($1,000) → projected=$8,500 > $8,000
        # order_value=$1,000 < $1,500 and AAPL single_pos=$1,000 < $2,000 → both pass
        positions = [_make_position("SPY", market_value=7_500.0, quantity=75.0)]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_buy("AAPL", qty=10.0))

        assert "max_total_exposure_exceeded" in result.risk_decision.reason_codes

    def test_total_exposure_within_limit_is_allowed(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # Existing SPY=$1,000; BUY AAPL qty=5 ($500) → projected=$1,500 < $8,000
        positions = [_make_position("SPY", market_value=1_000.0, quantity=10.0)]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_buy("AAPL", qty=5.0))

        assert "max_total_exposure_exceeded" not in result.risk_decision.reason_codes

    def test_total_exposure_sell_reduces_projected_exposure(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # SPY=$7,500 + AAPL=$500 → total=$8,000; SELL AAPL qty=10 ($1,000)
        # projected = max(0, 8,000 - 1,000) = $7,000 < $8,000
        positions = [
            _make_position("SPY", market_value=7_500.0, quantity=75.0),
            _make_position("AAPL", market_value=500.0, quantity=5.0),
        ]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_sell("AAPL", qty=10.0))

        assert "max_total_exposure_exceeded" not in result.risk_decision.reason_codes


# ===========================================================================
# _check_concurrent_positions
# ===========================================================================


class TestConcurrentPositionsCheck:
    """portfolio_value=$10,000 · max_concurrent_positions=3 · price=$100."""

    def test_concurrent_positions_exceeded_blocks_new_buy(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # 3 existing positions; BUY GOOGL (new symbol) → projected=4 > 3 limit
        positions = [
            _make_position("SPY", market_value=100.0),
            _make_position("AAPL", market_value=100.0),
            _make_position("MSFT", market_value=100.0),
        ]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_buy("GOOGL", qty=5.0))

        assert "max_concurrent_positions_exceeded" in result.risk_decision.reason_codes

    def test_concurrent_positions_at_limit_allows_new_buy(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # 2 existing positions; BUY GOOGL (new symbol) → projected=3 == 3 limit → allowed
        positions = [
            _make_position("SPY", market_value=100.0),
            _make_position("AAPL", market_value=100.0),
        ]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_buy("GOOGL", qty=5.0))

        assert "max_concurrent_positions_exceeded" not in result.risk_decision.reason_codes

    def test_concurrent_positions_adding_to_existing_symbol_is_allowed(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # 3 existing positions; BUY more SPY (already held) → count stays 3 → allowed
        positions = [
            _make_position("SPY", market_value=100.0),
            _make_position("AAPL", market_value=100.0),
            _make_position("MSFT", market_value=100.0),
        ]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_buy("SPY", qty=5.0))

        assert "max_concurrent_positions_exceeded" not in result.risk_decision.reason_codes

    def test_concurrent_positions_full_sell_reduces_count(
        self,
        tmp_path: Path,
        risk_defaults_file: Path,
        fresh_bar: Bar,
        healthy_account: AccountInfo,
    ) -> None:
        # 3 existing positions; SELL all 10 SPY → qty >= position.quantity → count=2 → allowed
        positions = [
            _make_position("SPY", market_value=1_000.0, quantity=10.0),
            _make_position("AAPL", market_value=100.0),
            _make_position("MSFT", market_value=100.0),
        ]
        service = _build_service(
            tmp_path, risk_defaults_file, fresh_bar, healthy_account, positions=positions
        )

        result = service.preview(_sell("SPY", qty=10.0))

        assert "max_concurrent_positions_exceeded" not in result.risk_decision.reason_codes
