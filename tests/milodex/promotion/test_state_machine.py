"""Unit tests for the promotion gate module.

All functions under test are pure (no I/O), so no fixtures are required.
"""

from __future__ import annotations

import pytest

from milodex.promotion import (
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
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.75,
        max_drawdown_pct=10.0,
        trade_count=50,
    )
    assert result.allowed is True
    assert result.promotion_type == "statistical"
    assert result.failures == []


def test_gate_fails_at_sharpe_boundary() -> None:
    # Sharpe must be strictly > MIN_SHARPE (0.5 for live); 0.5 itself fails
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=MIN_SHARPE,
        max_drawdown_pct=10.0,
        trade_count=50,
    )
    assert result.allowed is False
    assert any("Sharpe" in f for f in result.failures)


def test_gate_fails_low_sharpe() -> None:
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.3,
        max_drawdown_pct=10.0,
        trade_count=50,
    )
    assert result.allowed is False
    assert any("Sharpe" in f for f in result.failures)


def test_gate_fails_at_drawdown_boundary() -> None:
    # Max drawdown must be strictly < MAX_DRAWDOWN_PCT (15.0 for live); 15.0 fails
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.75,
        max_drawdown_pct=MAX_DRAWDOWN_PCT,
        trade_count=50,
    )
    assert result.allowed is False
    assert any("drawdown" in f.lower() for f in result.failures)


def test_gate_fails_high_drawdown() -> None:
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.75,
        max_drawdown_pct=20.0,
        trade_count=50,
    )
    assert result.allowed is False
    assert any("drawdown" in f.lower() for f in result.failures)


def test_gate_fails_at_trade_count_boundary() -> None:
    # Trade count must be >= MIN_TRADES (30 for live); 29 fails
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.75,
        max_drawdown_pct=10.0,
        trade_count=MIN_TRADES - 1,
    )
    assert result.allowed is False
    assert any("Trade count" in f for f in result.failures)


def test_gate_fails_all_three() -> None:
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.1,
        max_drawdown_pct=30.0,  # > 25% so it fails paper-readiness too
        trade_count=5,
    )
    assert result.allowed is False
    assert len(result.failures) == 3


def test_gate_fails_with_none_metrics() -> None:
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=None,
        max_drawdown_pct=None,
        trade_count=None,
    )
    assert result.allowed is False
    assert len(result.failures) == 3


def test_gate_passes_minimum_qualifying_metrics() -> None:
    # sharpe=0.51 (just above live boundary), dd=14.9 (just below), trades=30 (at min)
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.51,
        max_drawdown_pct=14.9,
        trade_count=MIN_TRADES,
    )
    assert result.allowed is True
    assert result.failures == []


# ---------------------------------------------------------------------------
# Honest-signal regression — the canonical Phase 1 truthful-failure
# scenario. Meanrev's walk-forward produced Sharpe 0.327 / DD 6.41% /
# 752 trades over 2015→2024.
#
# Pre-ADR-0028 (single-threshold formulation): the gate refused this evidence
# at every stage because Sharpe < 0.50.
#
# Post-ADR-0028 (stage-aware): the SAME evidence must:
#   - PASS the backtest → paper gate (Sharpe 0.327 > 0.0, DD 6.41 < 25.0,
#     752 trades >> 15). Paper-readiness is intentionally permissive.
#   - FAIL the paper → live gate (Sharpe 0.327 <= 0.50). Live-readiness
#     keeps the strict threshold.
#
# This locks both behaviors so a future change to either threshold dict
# (or to how the gate selects them) trips CI.
# ---------------------------------------------------------------------------


def test_meanrev_shape_evidence_passes_paper_readiness() -> None:
    """Sharpe 0.327 / DD 6.41 / 752 trades clears the backtest→paper gate."""
    result = check_gate(
        to_stage="paper",
        lifecycle_exempt=False,
        sharpe_ratio=0.327,
        max_drawdown_pct=6.41,
        trade_count=752,
    )
    assert result.allowed is True, (
        f"paper-readiness gate must accept this evidence; failures={result.failures}"
    )
    assert result.failures == []
    assert result.sharpe_ratio == 0.327


def test_meanrev_shape_evidence_refuses_live_promotion_on_sharpe_alone() -> None:
    """The honest-signal regression preserved at the paper→live gate.

    Under stage-aware thresholds (ADR 0028), this evidence was originally
    blocked from live promotion because Sharpe 0.327 < 0.50. That refusal
    still holds. Refusal must name Sharpe specifically — not collapse into
    a generic "gate failed" or quietly relax the threshold.
    """
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.327,
        max_drawdown_pct=6.41,
        trade_count=752,
    )
    assert result.allowed is False, (
        "live-readiness must still refuse Sharpe 0.327 — the load-bearing fact "
        "ADR 0023 stands on, preserved across the ADR 0028 stage-aware refactor"
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


# ---------------------------------------------------------------------------
# check_gate — lifecycle-exempt path
# ---------------------------------------------------------------------------


def test_gate_lifecycle_exempt_passes_without_metrics() -> None:
    result = check_gate(
        to_stage="paper",
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
        to_stage="live",
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
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.8,
        max_drawdown_pct=12.0,
        trade_count=100,
    )
    assert result.sharpe_ratio == 0.8
    assert result.max_drawdown_pct == 12.0
    assert result.trade_count == 100
    assert result.to_stage == "live"


def test_check_gate_lifecycle_exempt_preserves_metrics() -> None:
    result = check_gate(
        to_stage="paper",
        lifecycle_exempt=True,
        sharpe_ratio=0.38,
        max_drawdown_pct=23.44,
        trade_count=49,
    )
    assert result.sharpe_ratio == 0.38
    assert result.max_drawdown_pct == 23.44
    assert result.trade_count == 49
    assert result.to_stage == "paper"


# ---------------------------------------------------------------------------
# Stage-aware threshold behavior (ADR 0028)
# ---------------------------------------------------------------------------


def test_paper_readiness_passes_evidence_that_fails_live_readiness() -> None:
    """Same evidence: paper-readiness PASS, live-readiness FAIL — the asymmetry."""
    evidence = dict(sharpe_ratio=0.3, max_drawdown_pct=10.0, trade_count=20)
    paper = check_gate(to_stage="paper", lifecycle_exempt=False, **evidence)
    live = check_gate(to_stage="live", lifecycle_exempt=False, **evidence)
    assert paper.allowed is True, f"paper-readiness must pass; failures={paper.failures}"
    assert live.allowed is False
    assert any("Sharpe" in f for f in live.failures)
    assert any("Trade" in f for f in live.failures)


def test_paper_readiness_thresholds_are_zero_sharpe_and_25pct_dd_and_15_trades() -> None:
    """Lock the exact paper-readiness threshold values."""
    # Just below each threshold → fails
    fail_sharpe = check_gate(
        to_stage="paper",
        lifecycle_exempt=False,
        sharpe_ratio=0.0,
        max_drawdown_pct=10.0,
        trade_count=20,
    )
    assert fail_sharpe.allowed is False
    assert any("Sharpe" in f for f in fail_sharpe.failures)

    fail_dd = check_gate(
        to_stage="paper",
        lifecycle_exempt=False,
        sharpe_ratio=0.5,
        max_drawdown_pct=25.0,
        trade_count=20,
    )
    assert fail_dd.allowed is False

    fail_trades = check_gate(
        to_stage="paper",
        lifecycle_exempt=False,
        sharpe_ratio=0.5,
        max_drawdown_pct=10.0,
        trade_count=14,
    )
    assert fail_trades.allowed is False

    # Just above each threshold → passes
    ok = check_gate(
        to_stage="paper",
        lifecycle_exempt=False,
        sharpe_ratio=0.01,
        max_drawdown_pct=24.99,
        trade_count=15,
    )
    assert ok.allowed is True, ok.failures


def test_micro_live_uses_live_readiness_thresholds() -> None:
    """paper → micro_live and micro_live → live both use the strict thresholds."""
    evidence = dict(sharpe_ratio=0.4, max_drawdown_pct=12.0, trade_count=50)
    micro = check_gate(to_stage="micro_live", lifecycle_exempt=False, **evidence)
    live = check_gate(to_stage="live", lifecycle_exempt=False, **evidence)
    # Same allowed/disallowed and same number of failures (messages naturally
    # differ on the to_stage substring).
    assert micro.allowed == live.allowed
    assert len(micro.failures) == len(live.failures)
    # Sharpe 0.4 < 0.5 should be the only failure on both
    assert micro.allowed is False
    assert any("Sharpe" in f for f in micro.failures)
    assert any("Sharpe" in f for f in live.failures)


def test_check_gate_rejects_unknown_to_stage() -> None:
    """to_stage must be one of paper/micro_live/live; backtest is not a target."""
    with pytest.raises(ValueError, match="to_stage"):
        check_gate(
            to_stage="backtest",
            lifecycle_exempt=False,
            sharpe_ratio=0.6,
            max_drawdown_pct=10.0,
            trade_count=50,
        )


def test_round_trip_count_used_when_provided() -> None:
    """When round_trip_count is provided, it overrides trade_count for the count check."""
    # trade_count satisfies, but round_trip_count does not
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.6,
        max_drawdown_pct=10.0,
        trade_count=100,
        round_trip_count=20,  # 20 < 30 → fails
    )
    assert result.allowed is False
    assert any("Round-trip" in f for f in result.failures)


def test_trade_count_fallback_when_round_trip_not_provided() -> None:
    """Pre-PR-2.3 callers without round_trip_count fall back to trade_count."""
    result = check_gate(
        to_stage="live",
        lifecycle_exempt=False,
        sharpe_ratio=0.6,
        max_drawdown_pct=10.0,
        trade_count=29,  # 29 < 30 → fails
    )
    assert result.allowed is False
    assert any("Trade" in f for f in result.failures)
