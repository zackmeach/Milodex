"""Risk-layer output types.

``RiskCheckResult`` and ``RiskDecision`` are the outputs of
:class:`milodex.risk.evaluator.RiskEvaluator`. They live in the risk
module so the dependency graph reflects the architectural rule: risk
sits above execution, and ``execution/`` depends on ``risk/`` — never
the reverse (ADR 0019).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


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


@dataclass(frozen=True)
class ReconciliationReadiness:
    """Risk-layer view of the latest durable reconciliation verdict."""

    ready: bool
    reason_code: str | None
    message: str
    recorded_at: datetime | None = None
    local_trading_day: str | None = None
    status: str | None = None
    broker_connected: bool | None = None
    incident_hash: str | None = None
    context: dict[str, object] = field(default_factory=dict)
