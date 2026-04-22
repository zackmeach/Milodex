"""Unit tests for the promotion gate module.

All functions under test are pure (no I/O), so no fixtures are required.
"""

from __future__ import annotations

import pytest

from milodex.strategies.promotion import (
    MAX_DRAWDOWN_PCT,
    MIN_SHARPE,
    MIN_TRADES,
    STAGE_ORDER,
    check_gate,
    validate_stage_transition,
)

# ---------------------------------------------------------------------------
# validate_stage_transition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_stage", "to_stage"),
    [
        ("backtest", "paper"),
        ("paper", "micro_live"),
        ("micro_live", "live"),
    ],
)
def test_valid_stage_transitions(from_stage: str, to_stage: str) -> None:
    validate_stage_transition(from_stage, to_stage)  # must not raise


def test_same_stage_raises() -> None:
    with pytest.raises(ValueError, match="already at stage"):
        validate_stage_transition("paper", "paper")


def test_downgrade_raises() -> None:
    with pytest.raises(ValueError, match="Cannot downgrade"):
        validate_stage_transition("paper", "backtest")


def test_skip_stage_raises() -> None:
    with pytest.raises(ValueError, match="Skipping stages"):
        validate_stage_transition("backtest", "micro_live")


def test_skip_to_live_raises() -> None:
    with pytest.raises(ValueError, match="Skipping stages"):
        validate_stage_transition("paper", "live")


def test_unknown_from_stage_raises() -> None:
    with pytest.raises(ValueError, match="Unknown from_stage"):
        validate_stage_transition("staging", "paper")


def test_unknown_to_stage_raises() -> None:
    with pytest.raises(ValueError, match="Unknown to_stage"):
        validate_stage_transition("paper", "production")


def test_stage_order_is_complete() -> None:
    assert STAGE_ORDER == ["backtest", "paper", "micro_live", "live"]


# ---------------------------------------------------------------------------
# check_gate — statistical path
# ---------------------------------------------------------------------------


def test_gate_passes_with_all_thresholds_met() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.75,
        max_drawdown_pct=10.0,
        trade_count=50,
    )
    assert result.allowed is True
    assert result.promotion_type == "statistical"
    assert result.failures == []


def test_gate_fails_at_sharpe_boundary() -> None:
    # Sharpe must be strictly > MIN_SHARPE (0.5); 0.5 itself fails
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=MIN_SHARPE,
        max_drawdown_pct=10.0,
        trade_count=50,
    )
    assert result.allowed is False
    assert any("Sharpe" in f for f in result.failures)


def test_gate_fails_low_sharpe() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.3,
        max_drawdown_pct=10.0,
        trade_count=50,
    )
    assert result.allowed is False
    assert any("Sharpe" in f for f in result.failures)


def test_gate_fails_at_drawdown_boundary() -> None:
    # Max drawdown must be strictly < MAX_DRAWDOWN_PCT (15.0); 15.0 itself fails
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.75,
        max_drawdown_pct=MAX_DRAWDOWN_PCT,
        trade_count=50,
    )
    assert result.allowed is False
    assert any("drawdown" in f.lower() for f in result.failures)


def test_gate_fails_high_drawdown() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.75,
        max_drawdown_pct=20.0,
        trade_count=50,
    )
    assert result.allowed is False
    assert any("drawdown" in f.lower() for f in result.failures)


def test_gate_fails_at_trade_count_boundary() -> None:
    # Trade count must be >= MIN_TRADES (30); 29 fails
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.75,
        max_drawdown_pct=10.0,
        trade_count=MIN_TRADES - 1,
    )
    assert result.allowed is False
    assert any("Trade count" in f for f in result.failures)


def test_gate_fails_all_three() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.1,
        max_drawdown_pct=25.0,
        trade_count=5,
    )
    assert result.allowed is False
    assert len(result.failures) == 3


def test_gate_fails_with_none_metrics() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=None,
        max_drawdown_pct=None,
        trade_count=None,
    )
    assert result.allowed is False
    assert len(result.failures) == 3


def test_gate_passes_minimum_qualifying_metrics() -> None:
    # sharpe=0.51 (just above boundary), dd=14.9 (just below), trades=30 (at min)
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.51,
        max_drawdown_pct=14.9,
        trade_count=MIN_TRADES,
    )
    assert result.allowed is True
    assert result.failures == []


# ---------------------------------------------------------------------------
# check_gate — lifecycle-exempt path
# ---------------------------------------------------------------------------


def test_gate_lifecycle_exempt_passes_without_metrics() -> None:
    result = check_gate(
        lifecycle_exempt=True,
        sharpe_ratio=None,
        max_drawdown_pct=None,
        trade_count=None,
    )
    assert result.allowed is True
    assert result.promotion_type == "lifecycle_exempt"
    assert result.failures == []


def test_gate_lifecycle_exempt_passes_with_poor_metrics() -> None:
    # Even terrible metrics are ignored when lifecycle_exempt=True
    result = check_gate(
        lifecycle_exempt=True,
        sharpe_ratio=0.02,
        max_drawdown_pct=23.0,
        trade_count=49,
    )
    assert result.allowed is True
    assert result.promotion_type == "lifecycle_exempt"
    assert result.failures == []


# ---------------------------------------------------------------------------
# PromotionCheckResult field preservation
# ---------------------------------------------------------------------------


def test_check_gate_preserves_metric_values() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.8,
        max_drawdown_pct=12.0,
        trade_count=100,
    )
    assert result.sharpe_ratio == 0.8
    assert result.max_drawdown_pct == 12.0
    assert result.trade_count == 100


def test_check_gate_lifecycle_exempt_preserves_metrics() -> None:
    result = check_gate(
        lifecycle_exempt=True,
        sharpe_ratio=0.38,
        max_drawdown_pct=23.44,
        trade_count=49,
    )
    assert result.sharpe_ratio == 0.38
    assert result.max_drawdown_pct == 23.44
    assert result.trade_count == 49
