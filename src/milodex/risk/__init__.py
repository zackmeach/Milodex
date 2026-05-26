"""Risk management layer.

Sits between every strategy decision and every trade execution with veto
power. Enforces position sizing limits, daily loss caps, kill switch
thresholds, and fat-finger protections. The strategy proposes; risk
management disposes.

Per ADR 0019 the risk module owns its own inputs and outputs
(``RiskDefaults``, ``RiskDecision``, ``RiskCheckResult``). ``execution/``
depends on ``risk/``, never the reverse.
"""

from milodex.risk.config import (
    CeilingViolationError,
    RiskDefaults,
    load_active_risk_profile,
    load_backtesting_defaults,
    load_risk_defaults,
)
from milodex.risk.evaluator import EvaluationContext, RiskEvaluator
from milodex.risk.models import ReconciliationReadiness, RiskCheckResult, RiskDecision
from milodex.risk.policy import (
    BYPASS_SUMMARY,
    BacktestStructuralRiskEvaluator,
    NullRiskEvaluator,
    RiskPolicy,
    synthetic_bypass_decision,
)

__all__ = [
    "BYPASS_SUMMARY",
    "BacktestStructuralRiskEvaluator",
    "CeilingViolationError",
    "EvaluationContext",
    "NullRiskEvaluator",
    "ReconciliationReadiness",
    "RiskCheckResult",
    "RiskDecision",
    "RiskDefaults",
    "RiskEvaluator",
    "RiskPolicy",
    "load_active_risk_profile",
    "load_backtesting_defaults",
    "load_risk_defaults",
    "synthetic_bypass_decision",
]
