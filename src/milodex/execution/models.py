"""Models for execution and risk evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from milodex.broker.models import AccountInfo, Order, OrderSide, OrderType, TimeInForce
from milodex.data.models import Bar


class ExecutionStatus(Enum):
    """Lifecycle status for an execution attempt."""

    PREVIEW = "preview"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TradeIntent:
    """Initial user- or strategy-originated trade proposal."""

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: float | None = None
    stop_price: float | None = None
    strategy_config_path: Path | None = None
    submitted_by: str = "operator"

    def normalized_symbol(self) -> str:
        """Return an uppercase symbol for downstream systems."""
        return self.symbol.strip().upper()


@dataclass(frozen=True)
class ExecutionRequest:
    """Normalized execution request ready for risk evaluation and submission."""

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType
    time_in_force: TimeInForce
    estimated_unit_price: float
    estimated_order_value: float
    limit_price: float | None = None
    stop_price: float | None = None
    strategy_name: str | None = None
    strategy_stage: str | None = None
    strategy_config_path: Path | None = None


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
class ExecutionResult:
    """Structured result of a preview or submission."""

    status: ExecutionStatus
    execution_request: ExecutionRequest
    risk_decision: RiskDecision
    account: AccountInfo
    market_open: bool
    latest_bar: Bar | None
    order: Order | None = None
    message: str | None = None
    recorded_at: datetime | None = None
