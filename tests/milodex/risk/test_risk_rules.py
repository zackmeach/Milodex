"""Direct behavioral tests for each RiskEvaluator rule.

The execution-service tests already exercise risk via the submit path,
but several rules (daily-loss, order-value, single-position,
total-exposure, concurrent-positions) had no direct assertions. The
"risk layer is sacred" rule in `CLAUDE.md` / `AGENTS.md` is only
credible if every rule has a passing-case and a failing-case with an
expected reason code. That is what this file provides.

Rules exercised elsewhere (kill_switch, paper_mode, strategy_stage,
market_hours, data_staleness, duplicate_order) keep their coverage in
`tests/milodex/execution/test_service.py`.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

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
from milodex.data.models import Bar
from milodex.execution.config import StrategyExecutionConfig
from milodex.execution.models import ExecutionRequest, TradeIntent
from milodex.execution.state import KillSwitchState
from milodex.risk import (
    EvaluationContext,
    RiskCheckResult,
    RiskDecision,
    RiskDefaults,
    RiskEvaluator,
)

DEFAULT_RISK_DEFAULTS = RiskDefaults(
    kill_switch_enabled=True,
    kill_switch_max_drawdown_pct=0.10,
    require_manual_reset=True,
    max_single_position_pct=0.20,
    max_concurrent_positions=3,
    max_total_exposure_pct=0.80,
    max_daily_loss_pct=0.03,
    max_trades_per_day=20,
    max_order_value_pct=0.15,
    duplicate_order_window_seconds=60,
    max_data_staleness_seconds=300,
)


def _fresh_bar() -> Bar:
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=10),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )


def _position(symbol: str, quantity: float, unit_price: float = 100.0) -> Position:
    market_value = quantity * unit_price
    return Position(
        symbol=symbol,
        quantity=quantity,
        avg_entry_price=unit_price,
        current_price=unit_price,
        market_value=market_value,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def make_context(
    *,
    side: OrderSide = OrderSide.BUY,
    symbol: str = "SPY",
    quantity: float = 10.0,
    estimated_unit_price: float = 100.0,
    estimated_order_value: float | None = None,
    positions: Iterable[Position] = (),
    recent_orders: Iterable[Order] = (),
    account_portfolio_value: float = 10_000.0,
    account_daily_pnl: float = 0.0,
    risk_defaults: RiskDefaults = DEFAULT_RISK_DEFAULTS,
    strategy_config: StrategyExecutionConfig | None = None,
    preview_only: bool = False,
    market_open: bool = True,
    trading_mode: str = "paper",
    kill_switch_active: bool = False,
    latest_bar: Bar | None = None,
    runtime_config_hash: str | None = None,
    frozen_manifest_hash: str | None = None,
) -> EvaluationContext:
    """Build an ``EvaluationContext`` pre-configured to pass every rule
    except the one under test."""
    order_value = (
        estimated_order_value
        if estimated_order_value is not None
        else quantity * estimated_unit_price
    )
    intent = TradeIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )
    request = ExecutionRequest(
        symbol=symbol.upper(),
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        estimated_unit_price=estimated_unit_price,
        estimated_order_value=order_value,
    )
    account = AccountInfo(
        equity=account_portfolio_value,
        cash=account_portfolio_value,
        buying_power=account_portfolio_value,
        portfolio_value=account_portfolio_value,
        daily_pnl=account_daily_pnl,
    )
    # Promoted stages now require both manifest hashes (RuntimeError otherwise).
    # Default to matching dummy hashes when the caller hasn't specified — this
    # mirrors the production wiring in ExecutionService._evaluate. Tests that
    # exercise drift behavior pass explicit hash values and override these.
    if (
        strategy_config is not None
        and strategy_config.stage in {"paper", "micro_live", "live"}
        and runtime_config_hash is None
        and frozen_manifest_hash is None
    ):
        runtime_config_hash = "0" * 64
        frozen_manifest_hash = "0" * 64
    return EvaluationContext(
        intent=intent,
        request=request,
        account=account,
        positions=list(positions),
        recent_orders=list(recent_orders),
        latest_bar=latest_bar or _fresh_bar(),
        market_open=market_open,
        trading_mode=trading_mode,
        preview_only=preview_only,
        kill_switch_state=KillSwitchState(active=kill_switch_active),
        risk_defaults=risk_defaults,
        strategy_config=strategy_config,
        runtime_config_hash=runtime_config_hash,
        frozen_manifest_hash=frozen_manifest_hash,
    )


def check_result(decision: RiskDecision, name: str) -> RiskCheckResult:
    for check in decision.checks:
        if check.name == name:
            return check
    msg = f"No check named '{name}' in decision: {[c.name for c in decision.checks]}"
    raise AssertionError(msg)


# --- _check_daily_loss -----------------------------------------------------


def test_daily_loss_passes_when_pnl_positive():
    decision = RiskEvaluator().evaluate(make_context(account_daily_pnl=150.0))

    assert check_result(decision, "daily_loss").passed is True


def test_daily_loss_passes_when_loss_within_cap():
    # Loss of $100 on a ~$10,100 pre-loss base = ~0.99% < 3% cap.
    decision = RiskEvaluator().evaluate(make_context(account_daily_pnl=-100.0))

    assert check_result(decision, "daily_loss").passed is True


def test_daily_loss_fails_when_loss_between_cap_and_kill_switch():
    # Loss of $400 on ~$10,400 pre-loss base ≈ 3.85%. Exceeds 3% cap,
    # below 10% kill switch threshold.
    decision = RiskEvaluator().evaluate(make_context(account_daily_pnl=-400.0))

    result = check_result(decision, "daily_loss")
    assert result.passed is False
    assert result.reason_code == "daily_loss_cap_exceeded"


def test_daily_loss_triggers_kill_switch_reason_when_over_kill_threshold():
    # Loss of $2,000 on ~$12,000 pre-loss base ≈ 16.7%. Well over 10%
    # kill-switch threshold; kill-switch reason takes precedence over
    # ordinary cap-exceeded reason.
    decision = RiskEvaluator().evaluate(make_context(account_daily_pnl=-2_000.0))

    result = check_result(decision, "daily_loss")
    assert result.passed is False
    assert result.reason_code == "kill_switch_threshold_breached"


def test_daily_loss_cap_tightened_by_strategy_config():
    strategy_config = StrategyExecutionConfig(
        name="tight_strategy",
        enabled=True,
        stage="paper",
        max_position_pct=0.20,
        max_positions=3,
        daily_loss_cap_pct=0.005,  # 0.5% — much tighter than 3%
        stop_loss_pct=None,
        path=None,  # type: ignore[arg-type]
    )
    # Loss of $100 ≈ 0.99% of base — under global 3% cap but over the
    # strategy's 0.5% cap. Should fail via the effective-min rule.
    decision = RiskEvaluator().evaluate(
        make_context(account_daily_pnl=-100.0, strategy_config=strategy_config)
    )

    result = check_result(decision, "daily_loss")
    assert result.passed is False
    assert result.reason_code == "daily_loss_cap_exceeded"


# --- _check_order_value ----------------------------------------------------


def test_order_value_passes_below_cap():
    # cap = 10,000 * 0.15 = 1,500. Order value 1,000 is under.
    decision = RiskEvaluator().evaluate(make_context(estimated_order_value=1_000.0))

    assert check_result(decision, "order_value").passed is True


def test_order_value_passes_at_cap_exactly():
    # Rule uses strict `>`, so equality passes.
    decision = RiskEvaluator().evaluate(make_context(estimated_order_value=1_500.0))

    assert check_result(decision, "order_value").passed is True


def test_order_value_fails_above_cap():
    decision = RiskEvaluator().evaluate(make_context(estimated_order_value=1_500.01))

    result = check_result(decision, "order_value")
    assert result.passed is False
    assert result.reason_code == "max_order_value_exceeded"


# --- _check_single_position_limit ------------------------------------------


def test_single_position_passes_fresh_buy_below_cap():
    # Single-position cap = 10,000 * 0.20 = 2,000. Buy value = 1,500.
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=1_500.0,
            # Raise the order-value cap high enough to isolate this check.
            risk_defaults=_with_overrides(max_order_value_pct=0.50),
        )
    )

    assert check_result(decision, "single_position").passed is True


def test_single_position_fails_when_buy_adds_to_existing_over_cap():
    # Existing SPY position worth $1,500 + BUY adds $700 → projected
    # $2,200 > $2,000 cap.
    existing = _position("SPY", 15.0, 100.0)
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=700.0,
            positions=[existing],
            risk_defaults=_with_overrides(max_order_value_pct=0.50),
        )
    )

    result = check_result(decision, "single_position")
    assert result.passed is False
    assert result.reason_code == "max_single_position_exceeded"


def test_single_position_cap_tightened_by_strategy_config():
    strategy_config = StrategyExecutionConfig(
        name="narrow",
        enabled=True,
        stage="paper",
        max_position_pct=0.05,  # 5% → $500 cap
        max_positions=3,
        daily_loss_cap_pct=0.05,
        stop_loss_pct=None,
        path=None,  # type: ignore[arg-type]
    )
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=600.0,
            strategy_config=strategy_config,
            risk_defaults=_with_overrides(max_order_value_pct=0.50),
        )
    )

    result = check_result(decision, "single_position")
    assert result.passed is False
    assert result.reason_code == "max_single_position_exceeded"


def test_single_position_allows_sell_that_reduces_position():
    existing = _position("SPY", 30.0, 100.0)  # current value $3,000 (over cap)
    decision = RiskEvaluator().evaluate(
        make_context(
            side=OrderSide.SELL,
            estimated_order_value=2_500.0,
            positions=[existing],
            risk_defaults=_with_overrides(max_order_value_pct=0.50),
            recent_orders=[],
        )
    )

    # Projected = max(0, 3000 - 2500) = 500, under $2,000 cap → pass.
    assert check_result(decision, "single_position").passed is True


# --- _check_total_exposure -------------------------------------------------


def test_total_exposure_passes_fresh_buy_below_cap():
    # Cap = 10,000 * 0.80 = 8,000. Buy $1,000 → exposure 1,000.
    decision = RiskEvaluator().evaluate(make_context(estimated_order_value=1_000.0))

    assert check_result(decision, "total_exposure").passed is True


def test_total_exposure_fails_when_buy_pushes_over_cap():
    # Existing $7,500 exposure + $1,000 BUY = $8,500 > $8,000 cap.
    existing = _position("QQQ", 75.0, 100.0)  # $7,500
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=1_000.0,
            positions=[existing],
        )
    )

    result = check_result(decision, "total_exposure")
    assert result.passed is False
    assert result.reason_code == "max_total_exposure_exceeded"


def test_total_exposure_passes_exactly_at_cap():
    # Existing $7,500 + BUY $500 = $8,000 — equality passes.
    existing = _position("QQQ", 75.0, 100.0)
    decision = RiskEvaluator().evaluate(
        make_context(
            estimated_order_value=500.0,
            positions=[existing],
        )
    )

    assert check_result(decision, "total_exposure").passed is True


def test_total_exposure_allows_sell_that_reduces_exposure():
    # Existing $9,000 exposure (over cap already); SELL reduces to $6,500.
    existing = _position("QQQ", 90.0, 100.0)
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="QQQ",
            side=OrderSide.SELL,
            quantity=25.0,
            estimated_unit_price=100.0,
            positions=[existing],
        )
    )

    assert check_result(decision, "total_exposure").passed is True


# --- _check_concurrent_positions ------------------------------------------


def test_concurrent_positions_passes_when_buy_fits_within_limit():
    # Two held + 1 new = 3 under default limit of 3 (strict `>`, equality
    # passes).
    held = [_position("QQQ", 10.0), _position("IWM", 5.0)]
    decision = RiskEvaluator().evaluate(make_context(positions=held, estimated_order_value=500.0))

    assert check_result(decision, "concurrent_positions").passed is True


def test_concurrent_positions_fails_when_new_symbol_exceeds_limit():
    # Three held + new BUY for a 4th symbol → projected 4 > 3 limit.
    held = [
        _position("QQQ", 10.0),
        _position("IWM", 5.0),
        _position("DIA", 2.0),
    ]
    decision = RiskEvaluator().evaluate(make_context(positions=held, estimated_order_value=500.0))

    result = check_result(decision, "concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_concurrent_positions_exceeded"


def test_concurrent_positions_passes_when_buying_more_of_existing():
    # Three held (at limit); buying MORE of an existing symbol does not
    # add a slot.
    held = [
        _position("SPY", 1.0),
        _position("IWM", 5.0),
        _position("DIA", 2.0),
    ]
    decision = RiskEvaluator().evaluate(make_context(positions=held, estimated_order_value=500.0))

    assert check_result(decision, "concurrent_positions").passed is True


def test_concurrent_positions_allows_sell_that_closes_slot():
    # Four held (already over limit), full SELL of one should drop count to 3.
    held = [
        _position("SPY", 10.0),
        _position("IWM", 5.0),
        _position("DIA", 2.0),
        _position("QQQ", 1.0),
    ]
    # Risk defaults allow 3. A full-close SELL of SPY: projected 3.
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.SELL,
            quantity=10.0,
            positions=held,
            risk_defaults=_with_overrides(
                max_single_position_pct=1.0,
                max_total_exposure_pct=1.0,
                max_order_value_pct=1.0,
            ),
        )
    )

    assert check_result(decision, "concurrent_positions").passed is True


def test_concurrent_positions_cap_tightened_by_strategy_config():
    strategy_config = StrategyExecutionConfig(
        name="single_slot",
        enabled=True,
        stage="paper",
        max_position_pct=1.0,
        max_positions=1,
        daily_loss_cap_pct=0.05,
        stop_loss_pct=None,
        path=None,  # type: ignore[arg-type]
    )
    held = [_position("QQQ", 5.0)]
    decision = RiskEvaluator().evaluate(
        make_context(
            positions=held,
            estimated_order_value=500.0,
            strategy_config=strategy_config,
        )
    )

    result = check_result(decision, "concurrent_positions")
    assert result.passed is False
    assert result.reason_code == "max_concurrent_positions_exceeded"


# --- helpers ---------------------------------------------------------------


def _with_overrides(**overrides) -> RiskDefaults:
    """Return a copy of DEFAULT_RISK_DEFAULTS with selected fields replaced."""
    base = DEFAULT_RISK_DEFAULTS
    return RiskDefaults(
        kill_switch_enabled=overrides.get("kill_switch_enabled", base.kill_switch_enabled),
        kill_switch_max_drawdown_pct=overrides.get(
            "kill_switch_max_drawdown_pct", base.kill_switch_max_drawdown_pct
        ),
        require_manual_reset=overrides.get("require_manual_reset", base.require_manual_reset),
        max_single_position_pct=overrides.get(
            "max_single_position_pct", base.max_single_position_pct
        ),
        max_concurrent_positions=overrides.get(
            "max_concurrent_positions", base.max_concurrent_positions
        ),
        max_total_exposure_pct=overrides.get("max_total_exposure_pct", base.max_total_exposure_pct),
        max_daily_loss_pct=overrides.get("max_daily_loss_pct", base.max_daily_loss_pct),
        max_trades_per_day=overrides.get("max_trades_per_day", base.max_trades_per_day),
        max_order_value_pct=overrides.get("max_order_value_pct", base.max_order_value_pct),
        duplicate_order_window_seconds=overrides.get(
            "duplicate_order_window_seconds", base.duplicate_order_window_seconds
        ),
        max_data_staleness_seconds=overrides.get(
            "max_data_staleness_seconds", base.max_data_staleness_seconds
        ),
    )


# --- _check_manifest_drift -------------------------------------------------


def _strategy_config(stage: str = "paper", enabled: bool = True) -> StrategyExecutionConfig:
    from pathlib import Path as _Path

    return StrategyExecutionConfig(
        name="demo_strategy",
        enabled=enabled,
        stage=stage,
        max_position_pct=0.10,
        max_positions=1,
        daily_loss_cap_pct=0.05,
        stop_loss_pct=None,
        path=_Path("configs/demo.yaml"),
    )


def test_manifest_drift_passes_for_manual_trade():
    """Manual trades (no strategy_config) are exempt."""
    decision = RiskEvaluator().evaluate(make_context(strategy_config=None))

    assert check_result(decision, "manifest_drift").passed is True


def test_manifest_drift_passes_for_backtest_stage():
    """Backtest stage has no promoted state to freeze."""
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="backtest"),
            runtime_config_hash=None,
            frozen_manifest_hash=None,
        )
    )

    assert check_result(decision, "manifest_drift").passed is True


def test_manifest_drift_blocks_paper_stage_without_frozen_manifest():
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            runtime_config_hash="abc" * 10,
            frozen_manifest_hash=None,
        )
    )

    result = check_result(decision, "manifest_drift")
    assert result.passed is False
    assert result.reason_code == "no_frozen_manifest"


def test_manifest_drift_blocks_when_hashes_diverge():
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="b" * 64,
        )
    )

    result = check_result(decision, "manifest_drift")
    assert result.passed is False
    assert result.reason_code == "manifest_drift"


def test_manifest_drift_passes_when_hashes_match():
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    assert check_result(decision, "manifest_drift").passed is True


def test_manifest_drift_raises_when_promoted_stage_missing_runtime_hash():
    """Promoted stages must supply runtime_config_hash; None is a programmer error."""
    for stage in ("paper", "micro_live", "live"):
        with pytest.raises(RuntimeError, match="requires runtime_config_hash"):
            RiskEvaluator().evaluate(
                make_context(
                    strategy_config=_strategy_config(stage=stage),
                    runtime_config_hash=None,
                    frozen_manifest_hash="a" * 64,
                )
            )


def test_manifest_drift_applies_at_micro_live_and_live_stages():
    for stage in ("micro_live", "live"):
        decision = RiskEvaluator().evaluate(
            make_context(
                strategy_config=_strategy_config(stage=stage),
                runtime_config_hash="a" * 64,
                frozen_manifest_hash="b" * 64,
            )
        )
        result = check_result(decision, "manifest_drift")
        assert result.passed is False, f"stage={stage} should be blocked on drift"
        assert result.reason_code == "manifest_drift"


# --- sanity: reference the pytest and Order/OrderStatus imports so ruff
#     does not flag them unused if tests are trimmed. The pytest import
#     stays available for future parametrization.
_ = (pytest, Order, OrderStatus)
