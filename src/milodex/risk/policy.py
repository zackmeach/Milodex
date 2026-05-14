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


class BacktestStructuralRiskEvaluator(RiskEvaluator):
    """Risk evaluator for policy-constrained historical replay.

    Enforces structural sizing and exposure checks using simulated account
    state. It intentionally skips wall-clock/runtime checks that would make
    historical bars depend on today's market state.
    """

    def evaluate(self, context: EvaluationContext) -> RiskDecision:
        checks = [
            self._check_order_value(context),
            self._check_single_position_limit(context),
            self._check_total_exposure(context),
            self._check_concurrent_positions(context),
            self._check_strategy_concurrent_positions(context),
        ]

        allowed = all(check.passed for check in checks)
        reason_codes = [
            check.reason_code for check in checks if not check.passed and check.reason_code
        ]
        summary = "Allowed" if allowed else "Blocked by structural backtest risk checks"
        return RiskDecision(
            allowed=allowed,
            summary=summary,
            checks=checks,
            reason_codes=reason_codes,
        )
