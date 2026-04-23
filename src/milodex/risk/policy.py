"""Risk evaluation policy.

Two policies are supported:

- ``ENFORCE`` — the standard policy for paper and live submission. The
  full ``RiskEvaluator`` runs.
- ``BYPASS`` — the backtest policy. ``NullRiskEvaluator`` short-circuits
  to an always-allowed decision with a documented reason. CLAUDE.md
  states the backtest engine sits *below* the risk layer: risk is
  enforced at promotion, not simulation. This module makes that stance
  explicit and auditable instead of achieved by having a parallel
  execution path.

No code path constructs ``NullRiskEvaluator`` by accident. It lives in
``milodex.risk`` alongside the real evaluator so reviewers see both
together, and every callsite that injects it is greppable.
"""

from __future__ import annotations

from enum import Enum

from milodex.risk.evaluator import EvaluationContext, RiskEvaluator
from milodex.risk.models import RiskDecision

BYPASS_SUMMARY = "Backtest fill \u2014 risk layer not applied."


class RiskPolicy(Enum):
    """Policy marker for how a submission should treat risk."""

    ENFORCE = "enforce"
    BYPASS = "bypass"


def synthetic_bypass_decision() -> RiskDecision:
    """Return the canonical always-allowed decision used in backtest mode."""
    return RiskDecision(
        allowed=True,
        summary=BYPASS_SUMMARY,
        checks=[],
        reason_codes=[],
    )


class NullRiskEvaluator(RiskEvaluator):
    """Risk evaluator that always allows. Used only for backtest simulation.

    Every call returns :func:`synthetic_bypass_decision`. This is the
    documented seam for CLAUDE.md's "backtesting is intentionally below
    the risk layer" stance.
    """

    def evaluate(self, context: EvaluationContext) -> RiskDecision:  # noqa: ARG002
        return synthetic_bypass_decision()
