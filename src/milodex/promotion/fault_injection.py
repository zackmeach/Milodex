"""Synthetic fault-injection self-test for the R-PRM-004 lifecycle gate (ADR 0058 M4).

Criterion (c) of the SRS R-PRM-004 lifecycle-proof gate requires evidence that
"the risk layer having rejected at least one synthetic fault-injection trade".
This module produces that evidence.

It constructs a deliberately guardrail-violating synthetic intent (a BUY whose
notional is far above the fat-finger cap), runs it through the REAL risk
evaluator — the exact :class:`milodex.risk.evaluator.RiskEvaluator` the execution
service uses, NOT a stub and NOT a bypass — in *evaluation only* mode, and
durably records the resulting veto as an explanation row that carries an
explicit synthetic marker.

Safety invariants (the synthetic intent must be impossible to leak into real
execution):

- **No broker.** This module never constructs or calls a broker client. It calls
  ``RiskEvaluator.evaluate`` directly on a hand-built :class:`EvaluationContext`;
  nothing reaches an order-submission path.
- **Marker present.** The recorded explanation's ``context_json`` carries
  ``{"synthetic_fault_injection": true, ...}`` and its ``decision_type`` is
  :data:`SYNTHETIC_FAULT_DECISION_TYPE`, so every operator surface can label it
  as a self-test rather than a real veto.
- **Scream on approval.** If the risk layer ever ALLOWS the oversized synthetic
  intent, that is a risk-layer regression: this module raises
  :class:`SyntheticFaultApprovedError` and records NOTHING that could satisfy
  criterion (c). If the veto fires but omits the targeted guardrail reason code,
  it raises :class:`SyntheticFaultGuardrailError` for the same reason.

The strategy the proof belongs to is recorded in the explanation's
``strategy_name`` field. The risk *evaluation* itself is run in manual-trade
mode (``strategy_config=None``) so the account-level fat-finger guardrail is
exercised in isolation, free of stage/manifest wiring — the fat-finger cap is an
account-level guardrail, not a strategy-specific one, so this is a faithful test
of the sacred layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from milodex.broker.models import AccountInfo, OrderSide, OrderType, TimeInForce
from milodex.core.event_store import ExplanationEvent
from milodex.data.models import Bar
from milodex.execution.models import ExecutionRequest, TradeIntent
from milodex.execution.state import KillSwitchState
from milodex.risk.config import load_risk_defaults
from milodex.risk.evaluator import EvaluationContext, RiskEvaluator
from milodex.risk.models import ReconciliationReadiness
from milodex.strategies.loader import load_strategy_config

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore

# Durable convention (queried by lifecycle criterion (c)). The decision_type is
# the discriminator the event-store query filters on; the context marker is the
# second, human-readable signal for operator surfaces.
SYNTHETIC_FAULT_DECISION_TYPE = "synthetic_fault_injection"
SYNTHETIC_FAULT_MARKER_KEY = "synthetic_fault_injection"
SYNTHETIC_FAULT_SUBMITTED_BY = "promotion_fault_check"

# The specific guardrail this self-test targets. A fat-finger oversized entry
# must trip the max-order-value cap; if it does not, the guardrail regressed.
EXPECTED_GUARDRAIL_REASON_CODE = "max_order_value_exceeded"

# A synthetic account small enough that any sane fat-finger percentage cap is a
# tiny fraction of the oversized order below.
_SYNTH_PORTFOLIO_VALUE = 1_000.0
_SYNTH_UNIT_PRICE = 1_000.0
_SYNTH_QUANTITY = 10_000.0  # → $10,000,000 notional, dwarfing any cap
_SYNTH_SYMBOL = "SYNTHETIC"


class SyntheticFaultApprovedError(RuntimeError):
    """The risk layer APPROVED the oversized synthetic intent — a regression.

    The fat-finger guardrail must veto a deliberately oversized entry. An
    approval means the sacred risk layer failed to block it; the fault-check
    refuses to record satisfying evidence and fails loud.
    """


class SyntheticFaultGuardrailError(RuntimeError):
    """The synthetic intent was blocked, but NOT by the targeted guardrail.

    The intent is oversized specifically to trip the fat-finger max-order-value
    cap. If the veto fires without that reason code, the guardrail under test did
    not evaluate as expected — treated as a regression, not valid evidence.
    """


@dataclass(frozen=True)
class FaultInjectionResult:
    """Outcome of a recorded synthetic fault-injection self-test."""

    strategy_id: str
    explanation_id: int
    reason_codes: list[str] = field(default_factory=list)
    recorded_at: datetime | None = None


def _build_synthetic_context(risk_defaults_path: Path) -> EvaluationContext:
    """Assemble an EvaluationContext for an oversized synthetic BUY (manual mode).

    ``strategy_config=None`` runs the sweep in manual-trade mode so stage /
    manifest / disable checks pass cleanly and the account-level fat-finger cap
    is the guardrail under test. ``event_store=None`` skips the attribution /
    duplicate / daily-count checks (they pass gracefully when absent).
    """
    now = datetime.now(tz=UTC)
    account = AccountInfo(
        equity=_SYNTH_PORTFOLIO_VALUE,
        cash=_SYNTH_PORTFOLIO_VALUE,
        buying_power=_SYNTH_PORTFOLIO_VALUE * 2,
        portfolio_value=_SYNTH_PORTFOLIO_VALUE,
        daily_pnl=0.0,
    )
    intent = TradeIntent(
        symbol=_SYNTH_SYMBOL,
        side=OrderSide.BUY,
        quantity=_SYNTH_QUANTITY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        strategy_config_path=None,
        submitted_by=SYNTHETIC_FAULT_SUBMITTED_BY,
    )
    request = ExecutionRequest(
        symbol=_SYNTH_SYMBOL,
        side=OrderSide.BUY,
        quantity=_SYNTH_QUANTITY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        estimated_unit_price=_SYNTH_UNIT_PRICE,
        estimated_order_value=_SYNTH_UNIT_PRICE * _SYNTH_QUANTITY,
        strategy_name=None,
        strategy_stage=None,
        strategy_config_path=None,
    )
    latest_bar = Bar(
        timestamp=now,
        open=_SYNTH_UNIT_PRICE,
        high=_SYNTH_UNIT_PRICE,
        low=_SYNTH_UNIT_PRICE,
        close=_SYNTH_UNIT_PRICE,
        volume=1_000_000,
    )
    readiness = ReconciliationReadiness(
        ready=True,
        reason_code=None,
        message="Synthetic fault-injection context — reconciliation not applicable.",
    )
    return EvaluationContext(
        intent=intent,
        request=request,
        account=account,
        positions=[],
        recent_orders=[],
        reconciliation_readiness=readiness,
        latest_bar=latest_bar,
        market_open=True,
        trading_mode="paper",
        preview_only=True,
        kill_switch_state=KillSwitchState(active=False),
        risk_defaults=load_risk_defaults(risk_defaults_path),
        strategy_config=None,
        event_store=None,
    )


def run_synthetic_fault_injection(
    strategy_id: str,
    config_path: Path,
    event_store: EventStore,
    *,
    risk_evaluator: RiskEvaluator | None = None,
    risk_defaults_path: Path | None = None,
    now: datetime | None = None,
) -> FaultInjectionResult:
    """Run the synthetic fault-injection self-test and record the veto.

    Loads ``config_path`` only to read the strategy's current stage (recorded on
    the explanation for provenance), builds the oversized synthetic intent, runs
    it through the REAL :class:`RiskEvaluator`, and — on a correct veto that
    includes the fat-finger guardrail reason — records a durably-marked
    explanation row.

    Raises:
        SyntheticFaultApprovedError: the risk layer allowed the oversized intent.
        SyntheticFaultGuardrailError: the veto omitted the targeted reason code.
    """
    config = load_strategy_config(config_path)
    evaluator = risk_evaluator or RiskEvaluator()
    defaults_path = risk_defaults_path or Path("configs/risk_defaults.yaml")
    context = _build_synthetic_context(defaults_path)

    decision = evaluator.evaluate(context)

    if decision.allowed:
        raise SyntheticFaultApprovedError(
            "SYNTHETIC FAULT INJECTION APPROVED — the risk layer allowed a BUY of "
            f"{_SYNTH_QUANTITY:g} units at {_SYNTH_UNIT_PRICE:g} "
            f"(${_SYNTH_UNIT_PRICE * _SYNTH_QUANTITY:,.0f} notional against a "
            f"${_SYNTH_PORTFOLIO_VALUE:,.0f} portfolio). The fat-finger max-order-value "
            "cap must veto this. This is a risk-layer regression; refusing to record "
            "criterion (c) evidence."
        )
    if EXPECTED_GUARDRAIL_REASON_CODE not in decision.reason_codes:
        raise SyntheticFaultGuardrailError(
            "SYNTHETIC FAULT INJECTION blocked, but not by the targeted guardrail "
            f"'{EXPECTED_GUARDRAIL_REASON_CODE}'. Observed reason codes: "
            f"{decision.reason_codes}. The oversized entry must trip the fat-finger "
            "cap; its absence is a risk-layer regression, not valid evidence."
        )

    recorded_at = now or datetime.now(tz=UTC)
    context_json: dict[str, Any] = {
        SYNTHETIC_FAULT_MARKER_KEY: True,
        "expected_reason_code": EXPECTED_GUARDRAIL_REASON_CODE,
        "note": (
            "SYNTHETIC self-test (R-PRM-004 criterion c / ADR 0058) — a deliberately "
            "oversized intent evaluated by the real risk layer. It NEVER reached a "
            "broker; this is not a real trade rejection."
        ),
        "estimated_order_value": _SYNTH_UNIT_PRICE * _SYNTH_QUANTITY,
        "account_portfolio_value": _SYNTH_PORTFOLIO_VALUE,
    }
    explanation = ExplanationEvent(
        recorded_at=recorded_at,
        decision_type=SYNTHETIC_FAULT_DECISION_TYPE,
        status="blocked",
        strategy_name=strategy_id,
        strategy_stage=config.stage,
        strategy_config_path=str(config_path),
        config_hash=None,
        symbol=_SYNTH_SYMBOL,
        side=OrderSide.BUY.value,
        quantity=_SYNTH_QUANTITY,
        order_type=OrderType.MARKET.value,
        time_in_force=TimeInForce.DAY.value,
        submitted_by=SYNTHETIC_FAULT_SUBMITTED_BY,
        market_open=True,
        latest_bar_timestamp=recorded_at,
        latest_bar_close=_SYNTH_UNIT_PRICE,
        account_equity=_SYNTH_PORTFOLIO_VALUE,
        account_cash=_SYNTH_PORTFOLIO_VALUE,
        account_portfolio_value=_SYNTH_PORTFOLIO_VALUE,
        account_daily_pnl=0.0,
        risk_allowed=False,
        risk_summary=decision.summary,
        reason_codes=list(decision.reason_codes),
        risk_checks=[
            {
                "name": check.name,
                "passed": check.passed,
                "message": check.message,
                "reason_code": check.reason_code,
            }
            for check in decision.checks
        ],
        context=context_json,
        session_id=None,
        backtest_run_id=None,
    )
    explanation_id = event_store.append_explanation(explanation)
    return FaultInjectionResult(
        strategy_id=strategy_id,
        explanation_id=explanation_id,
        reason_codes=list(decision.reason_codes),
        recorded_at=recorded_at,
    )
