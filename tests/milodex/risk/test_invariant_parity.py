"""Parity tests locking the duplicated risk math (architecture audit finding #3).

The disable-condition evaluators in ``risk/disable_conditions.py``
(``_evaluate_drawdown_breach``, ``_evaluate_data_quality``) are hand-maintained
copies of the veto checks in ``risk/evaluator.py`` (``_check_daily_loss``,
``_check_data_staleness``). Their docstrings assert the two verdicts "can never
diverge" — but nothing enforced it. These tests turn that convention into a
construction guarantee: they sweep across every threshold band and assert the
veto verdict and the disable-condition verdict agree at every point, so a future
edit to one side that forgets the other fails CI.

Both verdicts are read through the public interface: ``RiskEvaluator.evaluate``
plus ``check_result`` for the veto side, the module function for the condition
side. The relation under test is: veto **passed** iff condition **not active**.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from milodex.data.models import Bar
from milodex.risk import RiskEvaluator
from milodex.risk.disable_conditions import _evaluate_data_quality, _evaluate_drawdown_breach

from .test_disable_conditions import _strategy_config
from .test_risk_rules import DEFAULT_RISK_DEFAULTS, check_result, make_context


def _veto_passed(ctx, name: str) -> bool:
    return check_result(RiskEvaluator().evaluate(ctx), name).passed


# --- daily loss: veto _check_daily_loss vs condition _evaluate_drawdown_breach ---

# Loss fractions bracketing both the 3% per-strategy cap and the 10% kill-switch
# drawdown threshold (DEFAULT_RISK_DEFAULTS), plus values either side of each.
_LOSS_FRACTIONS = [0.0, 0.01, 0.029, 0.03, 0.031, 0.05, 0.099, 0.10, 0.101, 0.15, 0.30, 0.50]


@pytest.mark.parametrize("loss_fraction", _LOSS_FRACTIONS)
def test_daily_loss_veto_and_disable_condition_never_diverge(loss_fraction: float):
    pv = 10_000.0
    ctx = make_context(
        account_portfolio_value=pv,
        account_daily_pnl=-loss_fraction * pv,
        strategy_config=_strategy_config("meanrev"),  # daily_loss_cap_pct=0.03, stage paper
    )
    veto_passed = _veto_passed(ctx, "daily_loss")
    condition_active = _evaluate_drawdown_breach(ctx).active
    assert veto_passed == (not condition_active), (
        f"daily-loss divergence at loss {loss_fraction:.1%}: "
        f"veto passed={veto_passed}, disable-condition active={condition_active}"
    )


@pytest.mark.parametrize("expected_cap", [None, 0.01, 0.02, 0.05])
@pytest.mark.parametrize("loss_fraction", [0.005, 0.015, 0.025, 0.04, 0.06])
def test_daily_loss_parity_holds_under_runner_bound_cap_preference(
    expected_cap: float | None, loss_fraction: float
):
    """The runner-bound ``expected_daily_loss_cap_pct`` preference is applied
    identically on both sides — parity must hold across the cap matrix."""
    pv = 10_000.0
    ctx = make_context(
        account_portfolio_value=pv,
        account_daily_pnl=-loss_fraction * pv,
        strategy_config=_strategy_config("meanrev"),
        expected_daily_loss_cap_pct=expected_cap,
    )
    veto_passed = _veto_passed(ctx, "daily_loss")
    condition_active = _evaluate_drawdown_breach(ctx).active
    assert veto_passed == (not condition_active), (
        f"divergence at loss {loss_fraction:.1%}, expected_cap={expected_cap}: "
        f"veto passed={veto_passed}, condition active={condition_active}"
    )


# --- data staleness: veto _check_data_staleness vs condition _evaluate_data_quality ---


def _bar_aged(seconds: float) -> Bar:
    return Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(seconds=seconds),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )


@pytest.mark.parametrize("age_factor", [0.0, 0.5, 0.99, 1.0, 1.01, 2.0, 100.0])
def test_data_staleness_veto_and_disable_condition_never_diverge(age_factor: float):
    max_age = DEFAULT_RISK_DEFAULTS.max_data_staleness_seconds
    ctx = make_context(
        strategy_config=_strategy_config("meanrev"),
        latest_bar=_bar_aged(max_age * age_factor),
    )
    veto_passed = _veto_passed(ctx, "data_staleness")
    condition_active = _evaluate_data_quality(ctx).active
    assert veto_passed == (not condition_active), (
        f"staleness divergence at age x{age_factor}: "
        f"veto passed={veto_passed}, disable-condition active={condition_active}"
    )
