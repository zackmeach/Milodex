"""Risk management layer.

Sits between every strategy decision and every trade execution with veto
power. Enforces position sizing limits, daily loss caps, kill switch
thresholds, and fat-finger protections. The strategy proposes; risk
management disposes.

Per ADR 0019 the risk module owns its own inputs and outputs
(``RiskDefaults``, ``RiskDecision``, ``RiskCheckResult``). ``execution/``
depends on ``risk/``, never the reverse.
"""

from milodex.risk.config import RiskDefaults, load_risk_defaults
from milodex.risk.evaluator import EvaluationContext, RiskEvaluator
from milodex.risk.models import RiskCheckResult, RiskDecision

__all__ = [
    "EvaluationContext",
    "RiskCheckResult",
    "RiskDecision",
    "RiskDefaults",
    "RiskEvaluator",
    "load_risk_defaults",
]
