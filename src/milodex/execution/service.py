"""Paper execution orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from milodex.broker import BrokerClient, Order
from milodex.broker.models import OrderType
from milodex.config import get_logs_dir, get_trading_mode
from milodex.data import DataProvider
from milodex.execution.config import (
    StrategyExecutionConfig,
    load_risk_defaults,
    load_strategy_execution_config,
)
from milodex.execution.models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    TradeIntent,
)
from milodex.execution.risk import EvaluationContext, RiskEvaluator
from milodex.execution.state import KillSwitchStateStore


class ExecutionService:
    """Service layer for paper-mode trade preview and submission."""

    def __init__(
        self,
        broker_client: BrokerClient,
        data_provider: DataProvider,
        *,
        risk_defaults_path: Path | None = None,
        kill_switch_store: KillSwitchStateStore | None = None,
        risk_evaluator: RiskEvaluator | None = None,
    ) -> None:
        self._broker = broker_client
        self._data_provider = data_provider
        self._risk_defaults_path = risk_defaults_path or Path("configs/risk_defaults.yaml")
        self._kill_switch_store = kill_switch_store or KillSwitchStateStore(
            get_logs_dir() / "kill_switch_state.json"
        )
        self._risk_evaluator = risk_evaluator or RiskEvaluator()

    def preview(self, intent: TradeIntent) -> ExecutionResult:
        """Preview a trade without submitting to the broker."""
        return self._evaluate(intent, preview_only=True)

    def submit_paper(self, intent: TradeIntent) -> ExecutionResult:
        """Submit a paper trade after passing risk evaluation."""
        result = self._evaluate(intent, preview_only=False)
        if not result.risk_decision.allowed:
            self._maybe_activate_kill_switch(result)
            return result

        order = self._broker.submit_order(
            symbol=result.execution_request.symbol,
            side=result.execution_request.side,
            quantity=result.execution_request.quantity,
            order_type=result.execution_request.order_type,
            limit_price=result.execution_request.limit_price,
            stop_price=result.execution_request.stop_price,
            time_in_force=result.execution_request.time_in_force,
        )

        return ExecutionResult(
            status=ExecutionStatus.SUBMITTED,
            execution_request=result.execution_request,
            risk_decision=result.risk_decision,
            account=result.account,
            market_open=result.market_open,
            latest_bar=result.latest_bar,
            order=order,
            message=f"Order submitted successfully: {order.id}",
            recorded_at=datetime.now(tz=UTC),
        )

    def get_order_status(self, order_id: str) -> Order:
        """Return the current broker order state."""
        return self._broker.get_order(order_id)

    def cancel_order(self, order_id: str) -> tuple[bool, Order | None]:
        """Cancel an order and return the latest status when available."""
        cancelled = self._broker.cancel_order(order_id)
        order = None
        if cancelled:
            try:
                order = self._broker.get_order(order_id)
            except Exception:
                order = None
        return cancelled, order

    def get_kill_switch_state(self):
        """Return current kill-switch state."""
        return self._kill_switch_store.get_state()

    def _evaluate(self, intent: TradeIntent, *, preview_only: bool) -> ExecutionResult:
        normalized_intent = self._normalize_intent(intent)
        risk_defaults = load_risk_defaults(self._risk_defaults_path)
        strategy_config = self._load_strategy_config(normalized_intent.strategy_config_path)

        latest_bar = self._data_provider.get_latest_bar(normalized_intent.normalized_symbol())
        account = self._broker.get_account()
        positions = self._broker.get_positions()
        recent_orders = self._broker.get_orders(limit=100)
        market_open = self._broker.is_market_open()
        trading_mode = get_trading_mode()

        request = self._build_execution_request(
            normalized_intent,
            latest_bar.close,
            strategy_config,
        )
        context = EvaluationContext(
            intent=normalized_intent,
            request=request,
            account=account,
            positions=positions,
            recent_orders=recent_orders,
            latest_bar=latest_bar,
            market_open=market_open,
            trading_mode=trading_mode,
            preview_only=preview_only,
            kill_switch_state=self._kill_switch_store.get_state(),
            risk_defaults=risk_defaults,
            strategy_config=strategy_config,
        )
        decision = self._risk_evaluator.evaluate(context)
        status = ExecutionStatus.PREVIEW if preview_only else (
            ExecutionStatus.SUBMITTED if decision.allowed else ExecutionStatus.BLOCKED
        )
        message = "Preview complete." if preview_only else (
            "Order blocked by risk checks." if not decision.allowed else "Ready to submit."
        )
        return ExecutionResult(
            status=status,
            execution_request=request,
            risk_decision=decision,
            account=account,
            market_open=market_open,
            latest_bar=latest_bar,
            message=message,
            recorded_at=datetime.now(tz=UTC),
        )

    def _normalize_intent(self, intent: TradeIntent) -> TradeIntent:
        symbol = intent.normalized_symbol()
        if not symbol:
            msg = "Symbol is required."
            raise ValueError(msg)
        if intent.quantity <= 0:
            msg = "Quantity must be greater than zero."
            raise ValueError(msg)

        if (
            intent.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}
            and intent.limit_price is None
        ):
            msg = "Limit price is required for limit and stop-limit orders."
            raise ValueError(msg)
        if (
            intent.order_type in {OrderType.STOP, OrderType.STOP_LIMIT}
            and intent.stop_price is None
        ):
            msg = "Stop price is required for stop and stop-limit orders."
            raise ValueError(msg)

        return TradeIntent(
            symbol=symbol,
            side=intent.side,
            quantity=float(intent.quantity),
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            limit_price=float(intent.limit_price) if intent.limit_price is not None else None,
            stop_price=float(intent.stop_price) if intent.stop_price is not None else None,
            strategy_config_path=intent.strategy_config_path,
            submitted_by=intent.submitted_by,
        )

    def _build_execution_request(
        self,
        intent: TradeIntent,
        latest_price: float,
        strategy_config: StrategyExecutionConfig | None,
    ) -> ExecutionRequest:
        estimated_unit_price = self._estimate_unit_price(intent, latest_price)
        return ExecutionRequest(
            symbol=intent.normalized_symbol(),
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            estimated_unit_price=estimated_unit_price,
            estimated_order_value=estimated_unit_price * intent.quantity,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            strategy_name=strategy_config.name if strategy_config else None,
            strategy_stage=strategy_config.stage if strategy_config else None,
            strategy_config_path=intent.strategy_config_path,
        )

    def _estimate_unit_price(self, intent: TradeIntent, latest_price: float) -> float:
        candidate_prices = [latest_price]
        if intent.limit_price is not None:
            candidate_prices.append(intent.limit_price)
        if intent.stop_price is not None:
            candidate_prices.append(intent.stop_price)
        return max(candidate_prices)

    def _load_strategy_config(self, path: Path | None) -> StrategyExecutionConfig | None:
        if path is None:
            return None
        return load_strategy_execution_config(path)

    def _maybe_activate_kill_switch(self, result: ExecutionResult) -> None:
        if "kill_switch_threshold_breached" in result.risk_decision.reason_codes:
            self._kill_switch_store.activate("Daily loss exceeded kill switch threshold.")
