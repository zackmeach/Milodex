"""Models for execution and risk evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from milodex.broker.models import AccountInfo, Order, OrderSide, OrderType, TimeInForce
from milodex.data.models import Bar
from milodex.risk.models import RiskDecision

if TYPE_CHECKING:
    from milodex.strategies.base import DecisionReasoning


class ExecutionStatus(Enum):
    """Lifecycle status for an execution attempt."""

    PREVIEW = "preview"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class UnsupportedOrderTypeError(ValueError):
    """Raised when Phase 1 execution receives a non-market order type."""

    def __init__(self, order_type: OrderType) -> None:
        self.order_type = order_type
        self.supported_order_types = (OrderType.MARKET,)
        supported = ", ".join(item.value for item in self.supported_order_types)
        super().__init__(
            f"Unsupported order type '{order_type.value}': "
            f"Phase 1 execution supports market orders only. "
            f"Supported order types: {supported}."
        )


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
    # Stage the originating runner was bound to at startup. Strategy runners
    # set this from the YAML's stage field at config-load time and emit it on
    # every intent for the life of the session. The risk evaluator routes its
    # manifest_drift exemption through this when present, instead of the
    # per-cycle YAML stage field, closing the TOCTOU race surfaced 2026-05-06
    # (see docs/reviews/2026-05-06-manifest-drift-toctou-race.md). None for
    # operator manual trades.
    expected_stage: str | None = None
    # Per-strategy risk envelope snapshot, bound at runner startup and
    # immutable for the life of the runner. Same TOCTOU class as
    # ``expected_stage`` extended to the cap fields. None for operator manual
    # trades. See docs/reviews/2026-05-06-manifest-drift-toctou-race.md
    # (Action Item #4 follow-up).
    expected_max_positions: int | None = None
    expected_max_position_pct: float | None = None
    expected_daily_loss_cap_pct: float | None = None

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
    reasoning: DecisionReasoning | None = None


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
