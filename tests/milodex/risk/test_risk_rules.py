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
    expected_stage: str | None = None,
    expected_max_positions: int | None = None,
    expected_max_position_pct: float | None = None,
    expected_daily_loss_cap_pct: float | None = None,
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
        expected_stage=expected_stage,
        expected_max_positions=expected_max_positions,
        expected_max_position_pct=expected_max_position_pct,
        expected_daily_loss_cap_pct=expected_daily_loss_cap_pct,
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


def _strategy_config(
    stage: str = "paper",
    enabled: bool = True,
    max_position_pct: float = 0.10,
    max_positions: int = 1,
    daily_loss_cap_pct: float = 0.05,
) -> StrategyExecutionConfig:
    from pathlib import Path as _Path

    return StrategyExecutionConfig(
        name="demo_strategy",
        enabled=enabled,
        stage=stage,
        max_position_pct=max_position_pct,
        max_positions=max_positions,
        daily_loss_cap_pct=daily_loss_cap_pct,
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


def test_manifest_drift_uses_runner_bound_stage_when_yaml_transiently_flips_to_backtest():
    """TOCTOU race fix: a paper-bound runner must NOT exempt drift just because
    the YAML on disk transiently reads ``stage: backtest`` (parallel agent edit,
    git checkout, in-place YAML rewrite, etc.). The runner's bound
    ``expected_stage`` wins — drift gates on the bound stage, not on whatever
    the file happens to say at config-read time.

    Surfaced 2026-05-06 (one cycle out of 459 fired SELL NVDA x50 because Opus
    4.7 in parallel had the YAML in a ``stage: backtest`` state for one read).
    See docs/reviews/2026-05-06-manifest-drift-toctou-race.md.
    """
    decision = RiskEvaluator().evaluate(
        make_context(
            # Runner was started at paper. YAML on disk is currently backtest
            # (mid-edit by a parallel writer). Drift hashes diverge.
            strategy_config=_strategy_config(stage="backtest"),
            expected_stage="paper",
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="b" * 64,
        )
    )

    result = check_result(decision, "manifest_drift")
    assert result.passed is False, (
        "paper-bound runner must block drift even when YAML reads backtest"
    )
    assert result.reason_code == "manifest_drift"


def test_manifest_drift_remains_exempt_when_runner_bound_to_backtest():
    """Regression guard for the legitimate exemption: a runner started against
    a backtest-stage YAML must stay drift-exempt across YAML edits (research
    iteration). The runner's bound ``expected_stage`` keeps it exempt regardless
    of whether the YAML transiently reads paper/micro_live/live mid-session."""
    for transient_yaml_stage in ("paper", "micro_live", "live"):
        decision = RiskEvaluator().evaluate(
            make_context(
                strategy_config=_strategy_config(stage=transient_yaml_stage),
                expected_stage="backtest",
                # Promoted-stage hashes provided to satisfy the
                # raise-on-missing-hash invariant; they should be ignored
                # because the runner-bound stage is exempt.
                runtime_config_hash="a" * 64,
                frozen_manifest_hash="b" * 64,
            )
        )

        result = check_result(decision, "manifest_drift")
        assert result.passed is True, (
            f"backtest-bound runner must stay exempt even when YAML transiently "
            f"reads {transient_yaml_stage}"
        )


def test_manifest_drift_falls_back_to_yaml_stage_when_no_expected_stage():
    """Backward-compat: callers that haven't been routed through a runner (e.g.
    operator manual trades, legacy entry points) supply ``expected_stage=None``
    and the existing YAML-stage logic applies unchanged."""
    # backtest-stage YAML, no expected_stage → exempt (existing behavior)
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="backtest"),
            expected_stage=None,
            runtime_config_hash=None,
            frozen_manifest_hash=None,
        )
    )
    assert check_result(decision, "manifest_drift").passed is True

    # paper-stage YAML with diverging hashes, no expected_stage → blocks
    # (existing behavior)
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(stage="paper"),
            expected_stage=None,
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="b" * 64,
        )
    )
    result = check_result(decision, "manifest_drift")
    assert result.passed is False
    assert result.reason_code == "manifest_drift"


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


# --- TOCTOU follow-ups: runner-bound risk envelope wins over per-cycle YAML --
#
# These tests close the audit findings from PR #31 / Action Item #4 in
# docs/reviews/2026-05-06-manifest-drift-toctou-race.md. Same shape as the
# manifest_drift fix: a parallel writer raising a per-strategy cap mid-session
# must not let a cycle in flight take a position the runner's bound envelope
# would refuse. The ``min(global_default, per_strategy)`` defense partially
# mitigates the bypass but does not guarantee cycle-to-cycle consistency
# across a long-running runner; the bound values do.


def test_strategy_stage_uses_runner_bound_stage_when_yaml_flips_to_ineligible_stage():
    """A runner started against an ineligible stage (e.g. ``micro_live`` or
    ``live``) must not be permitted to submit just because the YAML
    transiently reads ``paper`` mid-cycle. The runner's bound stage wins."""
    decision = RiskEvaluator().evaluate(
        make_context(
            # YAML transiently reads paper (parallel writer mid-edit). Runner
            # is bound to micro_live (which is NOT eligible for paper-mode
            # submission per ADR 0004).
            strategy_config=_strategy_config(stage="paper"),
            expected_stage="micro_live",
            # Provide hashes so manifest_drift doesn't fire first and short-
            # circuit the test.
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    result = check_result(decision, "strategy_stage")
    assert result.passed is False, (
        "runner bound to micro_live must be refused even when YAML reads paper"
    )
    assert result.reason_code == "strategy_stage_ineligible"


def test_concurrent_positions_uses_runner_bound_max_positions():
    """A parallel writer raising ``max_positions`` from 1 to 10 mid-session
    must not let the runner take a second position. The runner's bound cap
    wins over the per-cycle YAML value."""
    from milodex.broker.models import Position

    existing_position = Position(
        symbol="AAPL",
        quantity=10,
        avg_entry_price=100.0,
        current_price=100.0,
        market_value=1000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="MSFT",
            side=OrderSide.BUY,
            quantity=10,
            estimated_unit_price=100.0,
            positions=[existing_position],
            # YAML on disk currently reads max_positions=10 (parallel writer
            # raised it mid-session). Runner is bound to max_positions=1.
            strategy_config=_strategy_config(stage="paper", max_positions=10),
            expected_max_positions=1,
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    result = check_result(decision, "concurrent_positions")
    assert result.passed is False, (
        "runner bound to max_positions=1 must refuse even when YAML reads 10"
    )
    assert result.reason_code == "max_concurrent_positions_exceeded"


def test_single_position_uses_runner_bound_max_position_pct():
    """A parallel writer raising ``max_position_pct`` from 5% to 50%
    mid-session must not let the runner take a position larger than the
    bound 5% envelope."""
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="SPY",
            side=OrderSide.BUY,
            # 10 shares × $100 = $1,000 = 10% of $10,000 portfolio.
            quantity=10,
            estimated_unit_price=100.0,
            account_portfolio_value=10_000.0,
            # YAML on disk currently reads 50% (parallel writer raised it).
            # Runner is bound to 5%.
            strategy_config=_strategy_config(
                stage="paper",
                max_position_pct=0.50,
            ),
            expected_max_position_pct=0.05,
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    result = check_result(decision, "single_position")
    assert result.passed is False, (
        "runner bound to max_position_pct=0.05 must refuse a 10% order even when YAML reads 0.50"
    )
    assert result.reason_code == "max_single_position_exceeded"


def test_daily_loss_uses_runner_bound_daily_loss_cap_pct():
    """A parallel writer raising ``daily_loss_cap_pct`` from 0.5% to 5%
    mid-session must not let the runner act when daily loss is past the
    bound 0.5% envelope."""
    decision = RiskEvaluator().evaluate(
        make_context(
            # Loss of $100 ≈ 1% of $10,000 portfolio.
            account_daily_pnl=-100.0,
            account_portfolio_value=10_000.0,
            # YAML on disk currently reads 5% cap (parallel writer raised it).
            # Runner is bound to 0.5%.
            strategy_config=_strategy_config(
                stage="paper",
                daily_loss_cap_pct=0.05,
            ),
            expected_daily_loss_cap_pct=0.005,
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    result = check_result(decision, "daily_loss")
    assert result.passed is False, (
        "runner bound to daily_loss_cap_pct=0.005 must refuse at 1% loss even when YAML reads 0.05"
    )
    assert result.reason_code == "daily_loss_cap_exceeded"


def test_toctou_followups_fall_back_to_yaml_when_no_binding():
    """Backward-compat: callers without runner-bound bindings (operator manual
    trades, legacy entry points) get unchanged behavior — the existing YAML
    read with ``min(global, per_strategy)`` applies."""
    from milodex.broker.models import Position

    # Without expected_max_positions, max_positions=10 from YAML applies
    # (capped by global default of 3). One existing position, one new.
    existing_position = Position(
        symbol="AAPL",
        quantity=10,
        avg_entry_price=100.0,
        current_price=100.0,
        market_value=1000.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )
    decision = RiskEvaluator().evaluate(
        make_context(
            symbol="MSFT",
            side=OrderSide.BUY,
            quantity=10,
            estimated_unit_price=100.0,
            positions=[existing_position],
            strategy_config=_strategy_config(stage="paper", max_positions=10),
            expected_max_positions=None,  # explicit None — no binding
            runtime_config_hash="a" * 64,
            frozen_manifest_hash="a" * 64,
        )
    )

    # Should pass — global default=3, YAML=10, min=3, current=1, projected=2,
    # 2 < 3 OK. (Confirms fallback path uses YAML, not the bound value.)
    assert check_result(decision, "concurrent_positions").passed is True


# --- decision.allowed aggregator (mutation audit Critical #1) -------------
#
# The risk evaluator's load-bearing AND across all checks
# (``allowed = all(check.passed for check in checks)`` at evaluator.py:91)
# was never asserted directly. The tests above pin individual
# ``check.passed`` values but a regression that returned ``None`` or a
# fixed ``True``/``False`` would slip through. The two tests below pin
# the global aggregation in both directions.


def test_decision_allowed_is_true_when_every_check_passes():
    """Kills mutation: evaluator.py:91 ``allowed = all(...)`` -> ``allowed = None``.

    Also kills the constant-flip mutations on the aggregator
    (``True`` -> ``False`` and vice versa). The default ``make_context``
    is engineered to satisfy every rule, so the global ``allowed`` must
    be the literal ``True``.
    """
    decision = RiskEvaluator().evaluate(make_context())

    # Every individual check passes — sanity-check the precondition before
    # the load-bearing global assertion.
    assert all(check.passed for check in decision.checks), (
        f"precondition failed: at least one check failed: "
        f"{[(c.name, c.passed, c.reason_code) for c in decision.checks if not c.passed]}"
    )
    assert decision.allowed is True
    assert decision.reason_codes == []
    assert decision.summary == "Allowed"


def test_decision_allowed_is_false_when_any_check_fails():
    """Kills mutation: evaluator.py:91 ``allowed = all(...)`` -> ``allowed = None``.

    Also kills the negation-flip on the aggregator
    (``all(...)`` -> ``not all(...)`` would invert this). Forces a single
    check to fail (kill switch active) and asserts the global decision
    flips to ``False`` with the failure surfaced in ``reason_codes``.
    """
    decision = RiskEvaluator().evaluate(make_context(kill_switch_active=True))

    assert decision.allowed is False
    assert "kill_switch_active" in decision.reason_codes
    assert decision.summary == "Blocked by risk checks"


# --- boundary tests (mutation audit Important #4) -------------------------


def test_daily_loss_passes_exactly_at_cap():
    """Kills mutation: evaluator.py:281 ``current_loss_pct > max_daily_loss``
    -> ``current_loss_pct >= max_daily_loss``.

    The rule uses strict ``>`` so equality must pass. Construct a loss
    that lands exactly on the 3% global cap: equity_base = portfolio - pnl
    = 10_000 - (-pnl) = 10_000 + |pnl|; current_loss_pct = |pnl| /
    (10_000 + |pnl|). Solving |pnl| / (10_000 + |pnl|) = 0.03 ->
    |pnl| = 300/0.97 ≈ 309.27835...
    """
    # |pnl| computed so current_loss_pct equals exactly max_daily_loss_pct (0.03).
    target_pct = DEFAULT_RISK_DEFAULTS.max_daily_loss_pct
    portfolio = 10_000.0
    # current_loss_pct = |pnl| / (portfolio + |pnl|) = target_pct
    # solve: |pnl| = target_pct * portfolio / (1 - target_pct)
    daily_pnl = -(target_pct * portfolio / (1.0 - target_pct))

    decision = RiskEvaluator().evaluate(
        make_context(
            account_portfolio_value=portfolio,
            account_daily_pnl=daily_pnl,
        )
    )

    result = check_result(decision, "daily_loss")
    assert result.passed is True, (
        "strict-> boundary: a loss exactly at the cap must pass; only > cap fails"
    )


def test_data_staleness_passes_exactly_at_max_age(monkeypatch):
    """Kills mutation: evaluator.py:254 ``age > max_age``
    -> ``age >= max_age``.

    The rule reads ``datetime.now(tz=UTC)`` internally; pin it via
    ``monkeypatch`` against ``milodex.risk.evaluator.datetime`` so we
    can engineer ``age == max_age`` exactly. Under strict ``>`` this
    must pass; the mutation to ``>=`` would flip it to fail.
    """
    from milodex.risk import evaluator as evaluator_module

    fixed_now = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)
    max_age_seconds = DEFAULT_RISK_DEFAULTS.max_data_staleness_seconds
    bar_timestamp = fixed_now - timedelta(seconds=max_age_seconds)

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(evaluator_module, "datetime", _FrozenDateTime)

    bar = Bar(
        timestamp=bar_timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )

    decision = RiskEvaluator().evaluate(make_context(latest_bar=bar))

    result = check_result(decision, "data_staleness")
    assert result.passed is True, (
        "strict-> boundary: a bar exactly at the staleness limit must pass; "
        "the > -> >= mutation would flip this to fail"
    )


def test_data_staleness_fails_just_over_max_age(monkeypatch):
    """Companion to ``test_data_staleness_passes_exactly_at_max_age``.

    Pin ``datetime.now`` and place the bar one microsecond beyond
    ``max_data_staleness_seconds``. Must fail under both the original
    and mutated comparison — but combined with the equality-passes
    test above, the pair pins the strict-``>`` semantic against the
    ``>=`` mutation.
    """
    from milodex.risk import evaluator as evaluator_module

    fixed_now = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)
    max_age_seconds = DEFAULT_RISK_DEFAULTS.max_data_staleness_seconds
    bar_timestamp = fixed_now - timedelta(seconds=max_age_seconds) - timedelta(microseconds=1)

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(evaluator_module, "datetime", _FrozenDateTime)

    bar = Bar(
        timestamp=bar_timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )

    decision = RiskEvaluator().evaluate(make_context(latest_bar=bar))

    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"


# --- sanity: reference the pytest and Order/OrderStatus imports so ruff
#     does not flag them unused if tests are trimmed. The pytest import
#     stays available for future parametrization.
_ = (pytest, Order, OrderStatus)
