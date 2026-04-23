"""Paper execution orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from milodex.broker import BrokerClient, Order
from milodex.broker.models import OrderType
from milodex.config import get_data_dir, get_logs_dir, get_trading_mode
from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.data import DataProvider

if TYPE_CHECKING:
    from milodex.strategies.base import DecisionReasoning
from milodex.execution.config import (
    StrategyExecutionConfig,
    load_strategy_execution_config,
)
from milodex.execution.models import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    TradeIntent,
)
from milodex.execution.state import KillSwitchStateStore
from milodex.risk import (
    EvaluationContext,
    NullRiskEvaluator,
    RiskEvaluator,
    load_risk_defaults,
)
from milodex.strategies.loader import compute_config_hash


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
        event_store: EventStore | None = None,
    ) -> None:
        self._broker = broker_client
        self._data_provider = data_provider
        self._risk_defaults_path = risk_defaults_path or Path("configs/risk_defaults.yaml")
        self._event_store = event_store or EventStore(get_data_dir() / "milodex.db")
        self._kill_switch_store = kill_switch_store or KillSwitchStateStore(
            event_store=self._event_store,
            legacy_path=get_logs_dir() / "kill_switch_state.json",
        )
        self._risk_evaluator = risk_evaluator or RiskEvaluator()

    def preview(
        self,
        intent: TradeIntent,
        *,
        reasoning: DecisionReasoning | None = None,
    ) -> ExecutionResult:
        """Preview a trade without submitting to the broker."""
        result = self._evaluate(intent, preview_only=True, reasoning=reasoning)
        self._record_execution(intent, result, decision_type="preview")
        return result

    def submit_paper(
        self,
        intent: TradeIntent,
        *,
        session_id: str | None = None,
        reasoning: DecisionReasoning | None = None,
    ) -> ExecutionResult:
        """Submit a paper trade after passing risk evaluation."""
        return self._submit(intent, source="paper", session_id=session_id, reasoning=reasoning)

    def submit_backtest(
        self,
        intent: TradeIntent,
        *,
        session_id: str | None = None,
        backtest_run_id: int | None = None,
        reasoning: DecisionReasoning | None = None,
    ) -> ExecutionResult:
        """Submit a backtest trade through the same path as paper.

        Risk enforcement is delegated to whichever :class:`RiskEvaluator` was
        injected — backtest callers wire in
        :class:`milodex.risk.NullRiskEvaluator` so every intent is allowed
        with the synthetic bypass decision. The recording path tags the
        resulting :class:`TradeEvent` with ``source='backtest'`` and the
        originating ``backtest_run_id``.
        """
        return self._submit(
            intent,
            source="backtest",
            session_id=session_id,
            backtest_run_id=backtest_run_id,
            reasoning=reasoning,
        )

    def _submit(
        self,
        intent: TradeIntent,
        *,
        source: str,
        session_id: str | None = None,
        backtest_run_id: int | None = None,
        reasoning: DecisionReasoning | None = None,
    ) -> ExecutionResult:
        result = self._evaluate(intent, preview_only=False, reasoning=reasoning)
        if not result.risk_decision.allowed:
            self._maybe_activate_kill_switch(result)
            self._record_execution(
                intent,
                result,
                decision_type="submit",
                session_id=session_id,
                source=source,
                backtest_run_id=backtest_run_id,
            )
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

        submitted_result = ExecutionResult(
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
        self._record_execution(
            intent,
            submitted_result,
            decision_type="submit",
            session_id=session_id,
            source=source,
            backtest_run_id=backtest_run_id,
        )
        return submitted_result

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

    def trigger_kill_switch(self, reason: str) -> None:
        """Activate the kill switch. Single entry point for every trigger source.

        Callers include the risk-threshold path (daily-loss breach) and the
        operator-initiated SIGINT path in :class:`StrategyRunner`. Routing
        every activation through here keeps the audit trail in one place.
        """
        self._kill_switch_store.activate(reason)

    def reset_kill_switch(self) -> None:
        """Clear the kill switch. Operator-only path; manual confirmation
        is enforced at the CLI surface, not here.
        """
        self._kill_switch_store.reset()

    def record_no_action(
        self,
        *,
        strategy_name: str,
        strategy_stage: str,
        strategy_config_path: Path,
        config_hash: str | None,
        symbol: str,
        latest_bar_timestamp: datetime,
        latest_bar_close: float,
        session_id: str,
        message: str = "No trade intents emitted for this bar.",
    ) -> None:
        """Record a hold decision (no trade intents emitted) for the event log.

        Routed through the service so every explanation event — preview,
        submit, hold — is constructed in one place.
        """
        account = self._broker.get_account()
        self._event_store.append_explanation(
            ExplanationEvent(
                recorded_at=datetime.now(tz=UTC),
                decision_type="strategy_evaluate",
                status="no_action",
                strategy_name=strategy_name,
                strategy_stage=strategy_stage,
                strategy_config_path=str(strategy_config_path),
                config_hash=config_hash,
                symbol=symbol,
                side="hold",
                quantity=0.0,
                order_type="none",
                time_in_force="day",
                submitted_by="strategy_runner",
                market_open=self._broker.is_market_open(),
                latest_bar_timestamp=latest_bar_timestamp,
                latest_bar_close=latest_bar_close,
                account_equity=account.equity,
                account_cash=account.cash,
                account_portfolio_value=account.portfolio_value,
                account_daily_pnl=account.daily_pnl,
                risk_allowed=True,
                risk_summary="No strategy action required.",
                reason_codes=[],
                risk_checks=[],
                context={"message": message},
                session_id=session_id,
            )
        )

    def _evaluate(
        self,
        intent: TradeIntent,
        *,
        preview_only: bool,
        reasoning: DecisionReasoning | None = None,
    ) -> ExecutionResult:
        normalized_intent = self._normalize_intent(intent)
        bypass_mode = isinstance(self._risk_evaluator, NullRiskEvaluator)
        strategy_config = self._load_strategy_config(normalized_intent.strategy_config_path)

        latest_bar = self._data_provider.get_latest_bar(normalized_intent.normalized_symbol())
        account = self._broker.get_account()
        market_open = self._broker.is_market_open()

        request = self._build_execution_request(
            normalized_intent,
            latest_bar.close,
            strategy_config,
            reasoning=reasoning,
        )

        if bypass_mode:
            # Skip loading risk_defaults and querying positions / recent
            # orders — NullRiskEvaluator ignores them and the files may
            # not exist in a backtest environment.
            decision = self._risk_evaluator.evaluate(None)  # type: ignore[arg-type]
        else:
            context = EvaluationContext(
                intent=normalized_intent,
                request=request,
                account=account,
                positions=self._broker.get_positions(),
                recent_orders=self._broker.get_orders(limit=100),
                latest_bar=latest_bar,
                market_open=market_open,
                trading_mode=get_trading_mode(),
                preview_only=preview_only,
                kill_switch_state=self._kill_switch_store.get_state(),
                risk_defaults=load_risk_defaults(self._risk_defaults_path),
                strategy_config=strategy_config,
            )
            decision = self._risk_evaluator.evaluate(context)
        status = (
            ExecutionStatus.PREVIEW
            if preview_only
            else (ExecutionStatus.SUBMITTED if decision.allowed else ExecutionStatus.BLOCKED)
        )
        message = (
            "Preview complete."
            if preview_only
            else ("Order blocked by risk checks." if not decision.allowed else "Ready to submit.")
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
        *,
        reasoning: DecisionReasoning | None = None,
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
            reasoning=reasoning,
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
            self.trigger_kill_switch("Daily loss exceeded kill switch threshold.")

    def _record_execution(
        self,
        intent: TradeIntent,
        result: ExecutionResult,
        *,
        decision_type: str,
        session_id: str | None = None,
        source: str = "paper",
        backtest_run_id: int | None = None,
    ) -> None:
        config_hash = (
            None
            if result.execution_request.strategy_config_path is None
            else compute_config_hash(result.execution_request.strategy_config_path)
        )
        context: dict[str, object | None] = {
            "message": result.message,
            "latest_price": (None if result.latest_bar is None else result.latest_bar.close),
            "estimated_unit_price": result.execution_request.estimated_unit_price,
            "estimated_order_value": result.execution_request.estimated_order_value,
        }
        if result.execution_request.reasoning is not None:
            context["reasoning"] = result.execution_request.reasoning.asdict()
        explanation_id = self._event_store.append_explanation(
            ExplanationEvent(
                recorded_at=result.recorded_at or datetime.now(tz=UTC),
                decision_type=decision_type,
                status=result.status.value,
                strategy_name=result.execution_request.strategy_name,
                strategy_stage=result.execution_request.strategy_stage,
                strategy_config_path=(
                    str(result.execution_request.strategy_config_path)
                    if result.execution_request.strategy_config_path is not None
                    else None
                ),
                config_hash=config_hash,
                symbol=result.execution_request.symbol,
                side=result.execution_request.side.value,
                quantity=result.execution_request.quantity,
                order_type=result.execution_request.order_type.value,
                time_in_force=result.execution_request.time_in_force.value,
                submitted_by=intent.submitted_by,
                market_open=result.market_open,
                latest_bar_timestamp=(
                    None if result.latest_bar is None else result.latest_bar.timestamp
                ),
                latest_bar_close=None if result.latest_bar is None else result.latest_bar.close,
                account_equity=result.account.equity,
                account_cash=result.account.cash,
                account_portfolio_value=result.account.portfolio_value,
                account_daily_pnl=result.account.daily_pnl,
                risk_allowed=result.risk_decision.allowed,
                risk_summary=result.risk_decision.summary,
                reason_codes=list(result.risk_decision.reason_codes),
                risk_checks=[
                    {
                        "name": check.name,
                        "passed": check.passed,
                        "message": check.message,
                        "reason_code": check.reason_code,
                    }
                    for check in result.risk_decision.checks
                ],
                context=context,
                session_id=session_id,
            )
        )
        # When the broker has already reported a fill (synchronous path —
        # backtest, or a broker that fills immediately), prefer the actual
        # fill price over the pre-submission estimate so the trade row
        # reflects what really happened.
        recorded_unit_price = result.execution_request.estimated_unit_price
        recorded_order_value = result.execution_request.estimated_order_value
        if result.order is not None and result.order.filled_avg_price is not None:
            recorded_unit_price = float(result.order.filled_avg_price)
            recorded_order_value = recorded_unit_price * result.execution_request.quantity

        self._event_store.append_trade(
            TradeEvent(
                explanation_id=explanation_id,
                recorded_at=result.recorded_at or datetime.now(tz=UTC),
                status=result.status.value,
                source=source,
                symbol=result.execution_request.symbol,
                side=result.execution_request.side.value,
                quantity=result.execution_request.quantity,
                order_type=result.execution_request.order_type.value,
                time_in_force=result.execution_request.time_in_force.value,
                estimated_unit_price=recorded_unit_price,
                estimated_order_value=recorded_order_value,
                strategy_name=result.execution_request.strategy_name,
                strategy_stage=result.execution_request.strategy_stage,
                strategy_config_path=(
                    str(result.execution_request.strategy_config_path)
                    if result.execution_request.strategy_config_path is not None
                    else None
                ),
                submitted_by=intent.submitted_by,
                broker_order_id=None if result.order is None else result.order.id,
                broker_status=(None if result.order is None else result.order.status.value),
                message=result.message,
                session_id=session_id,
                backtest_run_id=backtest_run_id,
            )
        )
