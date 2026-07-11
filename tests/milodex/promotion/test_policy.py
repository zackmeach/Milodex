# tests/milodex/promotion/test_policy.py
"""Unit tests for the typed promotion-policy source of truth."""

from milodex.promotion.policy import (
    ACTIVE_PROMOTION_POLICY,
    PHASE1_GOVERNANCE_V1,
    LifecycleGateDefinition,
    PromotionCheckResult,
    PromotionPolicy,
)


def test_active_policy_is_phase1_governance_v1() -> None:
    assert ACTIVE_PROMOTION_POLICY is PHASE1_GOVERNANCE_V1
    assert isinstance(ACTIVE_PROMOTION_POLICY, PromotionPolicy)


def test_phase1_values_match_legacy_constants() -> None:
    p = PHASE1_GOVERNANCE_V1
    assert p.paper_gate.min_sharpe == 0.0
    assert p.paper_gate.max_drawdown_pct == 25.0
    assert p.capital_gate.min_sharpe == 0.5
    assert p.capital_gate.max_drawdown_pct == 15.0
    assert p.default_trade_floor == 30


def test_lifecycle_gate_is_defined_and_enforced() -> None:
    # ADR 0058 M4 addendum: the lifecycle gate is now enforced against the event
    # store (fail-closed) — the criteria are no longer define-only.
    gate = PHASE1_GOVERNANCE_V1.lifecycle_gate
    assert isinstance(gate, LifecycleGateDefinition)
    assert gate.enforced is True
    assert len(gate.criteria) == 3
    assert gate.evidence_max_age_days == 90


def test_lifecycle_gate_applies_to_is_the_scoped_identity_source_of_truth() -> None:
    """ADR 0058: applies_to lists exactly the policy-listed lifecycle-proof ids
    (the SPY/SHY regime strategy) and is the identity the orchestrator scopes the
    lifecycle exemption to."""
    gate = PHASE1_GOVERNANCE_V1.lifecycle_gate
    assert gate.applies_to == ("regime.daily.sma200_rotation.spy_shy.v1",)


def test_evaluate_paper_gate_passing() -> None:
    r = PHASE1_GOVERNANCE_V1.evaluate_research_target(
        sharpe_ratio=0.1,
        max_drawdown_pct=20.0,
        trade_count=30,
        target_stage="paper",
        min_trade_count=30,
    )
    assert isinstance(r, PromotionCheckResult)
    assert r.allowed is True
    assert r.promotion_type == "statistical"
    assert r.failures == []


def test_evaluate_paper_gate_sharpe_boundary_is_exclusive() -> None:
    # Sharpe must be > min; exactly 0.0 fails on the paper tier.
    r = PHASE1_GOVERNANCE_V1.evaluate_research_target(
        sharpe_ratio=0.0,
        max_drawdown_pct=20.0,
        trade_count=30,
        target_stage="paper",
        min_trade_count=30,
    )
    assert r.allowed is False
    assert r.failures == ["Sharpe 0.0 must be > 0.0 (got 0.0)"]


def test_evaluate_capital_gate_thresholds() -> None:
    r = PHASE1_GOVERNANCE_V1.evaluate_research_target(
        sharpe_ratio=0.4,
        max_drawdown_pct=10.0,
        trade_count=30,
        target_stage="micro_live",
        min_trade_count=30,
    )
    assert r.allowed is False
    assert r.failures == ["Sharpe 0.4 must be > 0.5 (got 0.4)"]


def test_evaluate_none_metrics_all_fail() -> None:
    r = PHASE1_GOVERNANCE_V1.evaluate_research_target(
        sharpe_ratio=None,
        max_drawdown_pct=None,
        trade_count=None,
        target_stage="paper",
        min_trade_count=30,
    )
    assert r.allowed is False
    assert r.failures == [
        "Sharpe None must be > 0.0 (got None)",
        "Max drawdown None% must be < 25.0% (got None)",
        "Trade count must be >= 30 (got None)",
    ]


def test_evaluate_unknown_stage_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unknown to_stage"):
        PHASE1_GOVERNANCE_V1.evaluate_research_target(
            sharpe_ratio=1.0,
            max_drawdown_pct=1.0,
            trade_count=99,
            target_stage="banana",
            min_trade_count=30,
        )


def test_unknown_stage_message_is_pinned_verbatim() -> None:
    import pytest

    with pytest.raises(ValueError) as excinfo:
        PHASE1_GOVERNANCE_V1.evaluate_research_target(
            sharpe_ratio=1.0,
            max_drawdown_pct=1.0,
            trade_count=99,
            target_stage="banana",
            min_trade_count=30,
        )
    assert str(excinfo.value) == (
        "Unknown to_stage 'banana'. Valid stages: ['backtest', 'paper', 'micro_live', 'live']."
    )
