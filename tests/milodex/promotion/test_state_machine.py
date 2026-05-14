"""Unit tests for the promotion gate module.

All functions under test are pure (no I/O), so no fixtures are required.
"""

from __future__ import annotations

import pytest

from milodex.promotion import (
    MAX_DRAWDOWN_PCT,
    MIN_SHARPE,
    MIN_TRADES,
    PAPER_MAX_DRAWDOWN_PCT,
    PAPER_MIN_SHARPE,
    STAGE_ORDER,
    check_gate,
    validate_stage_transition,
)

# ---------------------------------------------------------------------------
# validate_stage_transition
# ---------------------------------------------------------------------------


def test_backtest_to_paper_is_valid() -> None:
    validate_stage_transition("backtest", "paper")  # must not raise


@pytest.mark.parametrize(
    ("from_stage", "to_stage"),
    [
        ("paper", "micro_live"),
        ("micro_live", "live"),
    ],
)
def test_phase_one_blocks_micro_live_and_live(from_stage: str, to_stage: str) -> None:
    with pytest.raises(ValueError, match="blocked during Phase 1"):
        validate_stage_transition(from_stage, to_stage)


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


def test_gate_accepts_configured_min_trade_count() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        to_stage="paper",
        sharpe_ratio=0.75,
        max_drawdown_pct=10.0,
        trade_count=20,
        min_trade_count=20,
    )

    assert result.allowed is True
    assert result.failures == []


def test_gate_fails_configured_min_trade_count_and_names_floor() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        to_stage="paper",
        sharpe_ratio=0.75,
        max_drawdown_pct=10.0,
        trade_count=19,
        min_trade_count=20,
    )

    assert result.allowed is False
    assert any("Trade count" in f and "20" in f for f in result.failures)


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
# C-2 (PHASE2_PLANNING.md): honest-signal regression — the canonical Phase 1
# truthful-failure scenario. Meanrev's walk-forward run `54e71b30…` produced
# OOS-aggregate Sharpe 0.327 / max DD 6.41% / 752 trades over 2015→2024. The
# platform refused promotion because Sharpe < 0.50, despite the other two
# thresholds being met handily. ADR 0023's thesis depends on this property.
# Locking it here means a silent change to MIN_SHARPE (or to how the gate
# combines failures) cannot pass CI without tripping this test.
# ---------------------------------------------------------------------------


def test_gate_refuses_meanrev_shape_evidence_on_sharpe_alone() -> None:
    """The honest-signal regression: meanrev's actual Phase 1 numbers must refuse.

    Sharpe 0.327 fails (<0.50), max drawdown 6.41% passes, trade count 752
    passes. Refusal must therefore name Sharpe specifically — not collapse
    into a generic "gate failed" or quietly relax the threshold. Without
    this test, a future change that relaxed MIN_SHARPE could land silently.
    """
    result = check_gate(
        lifecycle_exempt=False,
        sharpe_ratio=0.327,
        max_drawdown_pct=6.41,
        trade_count=752,
    )

    assert result.allowed is False, (
        "honest-signal regression: meanrev's actual Phase 1 evidence must refuse — "
        "Sharpe 0.327 < MIN_SHARPE 0.50 is the load-bearing fact ADR 0023 stands on"
    )
    assert len(result.failures) == 1, (
        f"only Sharpe should fail (drawdown 6.41 < 15.0, trades 752 >= 30); "
        f"got {len(result.failures)} failures: {result.failures}"
    )
    assert any("Sharpe" in f for f in result.failures), (
        f"refusal must name Sharpe specifically; got {result.failures!r}"
    )
    assert result.sharpe_ratio == 0.327
    assert result.max_drawdown_pct == 6.41
    assert result.trade_count == 752


def test_paper_readiness_gate_passes_positive_sharpe_below_live_threshold() -> None:
    """Backtest->paper uses a permissive readiness gate, not the live-capital gate."""
    result = check_gate(
        lifecycle_exempt=False,
        to_stage="paper",
        sharpe_ratio=0.327,
        max_drawdown_pct=6.41,
        trade_count=752,
    )

    assert result.allowed is True
    assert result.failures == []


def test_paper_readiness_gate_fails_non_positive_sharpe() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        to_stage="paper",
        sharpe_ratio=0.0,
        max_drawdown_pct=6.41,
        trade_count=752,
    )

    assert result.allowed is False
    assert any("Sharpe" in f for f in result.failures)
    assert any(str(PAPER_MIN_SHARPE) in f for f in result.failures)


def test_paper_readiness_gate_uses_twenty_five_percent_drawdown_cap() -> None:
    result = check_gate(
        lifecycle_exempt=False,
        to_stage="paper",
        sharpe_ratio=0.327,
        max_drawdown_pct=PAPER_MAX_DRAWDOWN_PCT,
        trade_count=752,
    )

    assert result.allowed is False
    assert any("drawdown" in f.lower() for f in result.failures)
    assert any(str(PAPER_MAX_DRAWDOWN_PCT) in f for f in result.failures)


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


# ---------------------------------------------------------------------------
# Threshold literal pins (mutation audit Critical #2)
#
# The other gate tests use the constants symbolically (``MIN_SHARPE - 1``,
# ``MAX_DRAWDOWN_PCT``) — an edit that silently changed any of the three
# values would not trip them. SRS R-PRM-001/002/003 name these literals
# explicitly; the assertions below lock them so a silent relaxation cannot
# slip past CI.
# ---------------------------------------------------------------------------


def test_min_sharpe_is_pinned_to_exactly_zero_point_five() -> None:
    """Kills mutation: state_machine.py:43 ``MIN_SHARPE: float = 0.5``
    -> any other literal (e.g. 1.5, 0.25, 0.0).

    SRS R-PRM-001 names ``Sharpe > 0.5`` as the statistical-promotion
    threshold. A relaxation would silently lower the bar.
    """
    assert MIN_SHARPE == 0.5


def test_paper_min_sharpe_is_pinned_to_exactly_zero() -> None:
    assert PAPER_MIN_SHARPE == 0.0


def test_max_drawdown_pct_is_pinned_to_exactly_fifteen() -> None:
    """Kills mutation: state_machine.py:44 ``MAX_DRAWDOWN_PCT: float = 15.0``
    -> any other literal (e.g. 16.0).

    SRS R-PRM-002 names ``Max drawdown < 15%`` as the statistical-promotion
    threshold. A relaxation would silently widen the acceptable drawdown.
    """
    assert MAX_DRAWDOWN_PCT == 15.0


def test_paper_max_drawdown_pct_is_pinned_to_exactly_twenty_five() -> None:
    assert PAPER_MAX_DRAWDOWN_PCT == 25.0


def test_min_trades_is_pinned_to_exactly_thirty() -> None:
    """Kills mutation: state_machine.py:45 ``MIN_TRADES: int = 30``
    -> any other literal (e.g. 31, 1, 0).

    SRS R-PRM-003 / R-BKT-003 name ``>= 30 trades`` as the
    statistical-promotion floor.
    """
    assert MIN_TRADES == 30


def test_stage_order_literal_contents_are_pinned() -> None:
    """Kills mutation: state_machine.py:35 STAGE_ORDER element substitutions.

    The existing ``test_stage_order_is_complete`` already pins this; this
    test is intentional duplication to make the 'literal contents pinned'
    intent explicit and survive a future refactor that loosens the
    other test.
    """
    assert STAGE_ORDER == ["backtest", "paper", "micro_live", "live"]
