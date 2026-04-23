"""Tests for the backtest risk-policy seam."""

from __future__ import annotations

from unittest.mock import Mock

from milodex.risk import (
    BYPASS_SUMMARY,
    NullRiskEvaluator,
    RiskEvaluator,
    RiskPolicy,
    synthetic_bypass_decision,
)


def test_risk_policy_enum_values():
    assert RiskPolicy.ENFORCE.value == "enforce"
    assert RiskPolicy.BYPASS.value == "bypass"


def test_synthetic_bypass_decision_is_always_allowed():
    decision = synthetic_bypass_decision()
    assert decision.allowed is True
    assert decision.summary == BYPASS_SUMMARY
    assert decision.checks == []
    assert decision.reason_codes == []


def test_null_risk_evaluator_is_a_risk_evaluator_subclass():
    assert issubclass(NullRiskEvaluator, RiskEvaluator)


def test_null_risk_evaluator_returns_bypass_decision_for_any_context():
    evaluator = NullRiskEvaluator()
    decision = evaluator.evaluate(Mock())
    assert decision.allowed is True
    assert decision.summary == BYPASS_SUMMARY
    assert decision.reason_codes == []


def test_null_risk_evaluator_ignores_context_entirely():
    evaluator = NullRiskEvaluator()
    blocked_context = Mock()
    blocked_context.kill_switch_state.active = True
    blocked_context.trading_mode = "live"
    decision = evaluator.evaluate(blocked_context)
    assert decision.allowed is True
