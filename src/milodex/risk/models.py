"""Risk-layer output types.

``RiskCheckResult`` and ``RiskDecision`` are the outputs of
:class:`milodex.risk.evaluator.RiskEvaluator`. They live in the risk
module so the dependency graph reflects the architectural rule: risk
sits above execution, and ``execution/`` depends on ``risk/`` — never
the reverse (ADR 0019).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskCheckResult:
    """Single risk check outcome."""

    name: str
    passed: bool
    message: str
    reason_code: str | None = None


@dataclass(frozen=True)
class RiskDecision:
    """Aggregate risk decision."""

    allowed: bool
    summary: str
    checks: list[RiskCheckResult]
    reason_codes: list[str] = field(default_factory=list)
