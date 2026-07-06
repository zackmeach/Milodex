"""Integration test: execution/service.py routes through load_active_risk_profile().

ADR 0054 + plan PR-7a Task 28: the active risk profile MUST flow through
execution/service.py's EvaluationContext. Without this test passing, the
profile system exists but doesn't affect enforcement.

Strategy: monkeypatch the cwd to a tmp_path with full configs/ layout, then
spy on the RiskEvaluator.evaluate() call to inspect the risk_defaults passed in.
"""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.data.models import Bar
from milodex.execution import ExecutionService, TradeIntent
from milodex.execution.state import KillSwitchStateStore
from milodex.risk import RiskDefaults
from milodex.risk.evaluator import RiskEvaluator

# Resolve repo root at import time (before any monkeypatch.chdir changes cwd).
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Minimal stubs (same pattern as tests/milodex/execution/test_service.py)
# ---------------------------------------------------------------------------


class _StubBroker:
    def __init__(self, account: AccountInfo, bar: Bar, order: Order) -> None:
        self._account = account
        self._bar = bar
        self._order = order

    def get_account(self) -> AccountInfo:
        return self._account

    def get_positions(self) -> list[Position]:
        return []

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        return []

    def is_market_open(self) -> bool:
        return True

    def latest_completed_session(self, now: datetime) -> date:
        # Test double: latest session is "today" so the 1D staleness gate
        # treats the fresh (today-dated) bar as current.
        return now.date()

    def submit_order(self, **kwargs) -> Order:
        return self._order

    def get_order(self, order_id: str) -> Order:
        return self._order

    def cancel_order(self, order_id: str) -> bool:
        return True


class _StubProvider:
    def __init__(self, bar: Bar) -> None:
        self._bar = bar

    def get_latest_bar(self, symbol: str) -> Bar:
        return self._bar


class _SpyEvaluator(RiskEvaluator):
    """Context-capturing evaluator that runs the REAL production check sweep.

    It deliberately does NOT override ``evaluate()`` — doing so would (correctly)
    be refused by the non-backtest ExecutionService G1 invariant, which requires
    the full ``RiskEvaluator._CHECKS`` sweep on any paper/live service. Instead
    it captures the ``EvaluationContext`` at sweep entry by overriding the first
    check hook (``_check_kill_switch``) and delegating to ``super()``, so the
    real sweep still runs. The tests below assert only on the captured context's
    ``risk_defaults`` (the active-profile routing under test); the allow/block
    decision is irrelevant to them, and ``preview()`` never submits to a broker."""

    def __init__(self) -> None:
        self.last_context = None

    def _check_kill_switch(self, context):  # type: ignore[override]
        self.last_context = context
        return super()._check_kill_switch(context)


def _fixtures() -> tuple[AccountInfo, Bar, Order]:
    account = AccountInfo(
        equity=10_000.0,
        cash=8_000.0,
        buying_power=8_000.0,
        portfolio_value=10_000.0,
        daily_pnl=0.0,
    )
    bar = Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=30),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000,
        vwap=100.0,
    )
    order = Order(
        id="o1",
        symbol="SPY",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.PENDING,
        submitted_at=datetime.now(tz=UTC),
    )
    return account, bar, order


def _make_service(tmp_path: Path, spy: _SpyEvaluator) -> ExecutionService:
    account, bar, order = _fixtures()
    return ExecutionService(
        broker_client=_StubBroker(account, bar, order),
        data_provider=_StubProvider(bar),
        kill_switch_store=KillSwitchStateStore(tmp_path / "kill_switch.json"),
        risk_evaluator=spy,
    )


def _setup_configs(tmp_path: Path, profile: str) -> None:
    """Copy real configs to tmp_path and set data/risk_profile.txt."""
    shutil.copytree(_REPO_ROOT / "configs", tmp_path / "configs")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "risk_profile.txt").write_text(f"{profile}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_execution_service_uses_active_profile_conservative(tmp_path, monkeypatch):
    """R-EXE-008 / ADR 0054: conservative profile flows into EvaluationContext.risk_defaults."""
    monkeypatch.chdir(tmp_path)
    _setup_configs(tmp_path, "conservative")

    spy = _SpyEvaluator()
    service = _make_service(tmp_path, spy)

    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET)
    service.preview(intent)

    assert spy.last_context is not None, "evaluate() was not called"
    risk: RiskDefaults = spy.last_context.risk_defaults

    # Conservative overlay: kill_switch.max_drawdown_pct = 0.05
    # Base default:                                         0.10
    # If still using the base, the value would be 0.10 — not 0.05.
    assert risk.kill_switch_max_drawdown_pct == 0.05, (
        f"Expected conservative drawdown 0.05, got {risk.kill_switch_max_drawdown_pct}. "
        "execution/service.py is not routing through load_active_risk_profile()."
    )
    assert risk.max_total_exposure_pct == 0.30
    assert risk.max_daily_loss_pct == 0.02


def test_execution_service_uses_active_profile_aggressive(tmp_path, monkeypatch):
    """ADR 0054 Task 28: aggressive profile flows into EvaluationContext.risk_defaults."""
    monkeypatch.chdir(tmp_path)
    _setup_configs(tmp_path, "aggressive")

    spy = _SpyEvaluator()
    service = _make_service(tmp_path, spy)

    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET)
    service.preview(intent)

    assert spy.last_context is not None
    risk: RiskDefaults = spy.last_context.risk_defaults

    # Aggressive overlay: kill_switch.max_drawdown_pct = 0.15
    assert risk.kill_switch_max_drawdown_pct == 0.15, (
        f"Expected aggressive drawdown 0.15, got {risk.kill_switch_max_drawdown_pct}. "
        "Profile is not flowing through."
    )
    assert risk.max_total_exposure_pct == 0.75
    assert risk.max_daily_loss_pct == 0.05


def test_execution_service_profile_switch_changes_limits(tmp_path, monkeypatch):
    """Switching from conservative to aggressive changes the enforced limits."""
    monkeypatch.chdir(tmp_path)
    _setup_configs(tmp_path, "conservative")

    spy1 = _SpyEvaluator()
    service1 = _make_service(tmp_path, spy1)
    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=1, order_type=OrderType.MARKET)
    service1.preview(intent)
    conservative_drawdown = spy1.last_context.risk_defaults.kill_switch_max_drawdown_pct

    # Switch to aggressive
    (tmp_path / "data" / "risk_profile.txt").write_text("aggressive\n", encoding="utf-8")
    spy2 = _SpyEvaluator()
    service2 = _make_service(tmp_path, spy2)
    service2.preview(intent)
    aggressive_drawdown = spy2.last_context.risk_defaults.kill_switch_max_drawdown_pct

    assert conservative_drawdown == 0.05
    assert aggressive_drawdown == 0.15
    assert aggressive_drawdown > conservative_drawdown
