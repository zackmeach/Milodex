"""Paper execution orchestration."""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from milodex.broker import BrokerClient, Order
from milodex.broker.exceptions import InsufficientFundsError, OrderRejectedError
from milodex.broker.models import OrderType
from milodex.config import get_data_dir, get_logs_dir, get_trading_mode
from milodex.core.advisory_lock import AdvisoryLock, AdvisoryLockError
from milodex.core.event_store import (
    EventStore,
    ExecutionAttemptEvent,
    ExplanationEvent,
    TradeEvent,
)
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
    UnsupportedOrderTypeError,
)
from milodex.execution.state import KillSwitchStateStore
from milodex.operations.reconciliation import latest_readiness
from milodex.risk import (
    EvaluationContext,
    NullRiskEvaluator,
    RiskEvaluator,
    load_active_risk_profile,
    load_risk_defaults,
)
from milodex.risk.models import RiskCheckResult, RiskDecision
from milodex.strategies.loader import compute_config_hash

_logger = logging.getLogger(__name__)

_DEFAULT_SUBMIT_LOCK_TIMEOUT_SECONDS = 30.0
"""Bounded wait for the per-account submit serialization lock (ADR 0056).

A submit critical section (read account snapshot -> evaluate caps -> submit) is
seconds at most, so this is generous slack, not a hot-path tax. On timeout the
submit is declined fail-closed (no order), never sent unserialized.
"""


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
        is_backtest: bool = False,
        locks_dir: Path | None = None,
        submit_lock_timeout_seconds: float = _DEFAULT_SUBMIT_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self._broker = broker_client
        self._data_provider = data_provider
        self._risk_defaults_path = risk_defaults_path or Path("configs/risk_defaults.yaml")
        # Per-account submit serialization (ADR 0056). Account-scoped advisory
        # lock dir; the lock only engages at micro_live/live (paper is
        # lock-free by design).
        self._locks_dir = locks_dir or (get_data_dir() / "locks")
        self._submit_lock_timeout_seconds = submit_lock_timeout_seconds
        self._event_store = event_store or EventStore(get_data_dir() / "milodex.db")
        self._kill_switch_store = kill_switch_store or KillSwitchStateStore(
            event_store=self._event_store,
            legacy_path=get_logs_dir() / "kill_switch_state.json",
        )
        self._risk_evaluator = risk_evaluator or RiskEvaluator()
        # Explicit backtest marker (ADR 0008/0030). When True the historical
        # replay must NOT bind to live trading-mode/env or perform a live
        # frozen-manifest lookup. This is decided by the caller (the backtest
        # engine), not inferred from ``isinstance(risk_evaluator,
        # NullRiskEvaluator)`` — the ENFORCE backtest path injects
        # ``BacktestStructuralRiskEvaluator`` (not a NullRiskEvaluator) and
        # must still be recognised as a backtest so it does not couple to
        # today's wall-clock/manifest state while its structural checks run.
        self._is_backtest = is_backtest

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
        """Serialize the submit critical section per account, then submit.

        ADR 0056: at micro_live/live stages the read-snapshot -> evaluate-caps
        -> submit sequence is held under a per-account advisory lock so two
        processes cannot both evaluate against a stale snapshot and both fill,
        overshooting an account-scoped cap. Paper stays lock-free (the accepted
        overshoot bound). On lock-acquire timeout the submit is declined
        fail-closed -- no order is sent.
        """
        if not self._should_serialize_submit(intent, source):
            return self._submit_locked(
                intent,
                source=source,
                session_id=session_id,
                backtest_run_id=backtest_run_id,
                reasoning=reasoning,
            )
        lock = self._submit_lock()
        try:
            lock.acquire_blocking(timeout_seconds=self._submit_lock_timeout_seconds)
        except AdvisoryLockError as exc:
            return self._declined_for_serialization(
                intent,
                source=source,
                session_id=session_id,
                backtest_run_id=backtest_run_id,
                reasoning=reasoning,
                error=exc,
            )
        try:
            return self._submit_locked(
                intent,
                source=source,
                session_id=session_id,
                backtest_run_id=backtest_run_id,
                reasoning=reasoning,
            )
        finally:
            lock.release()

    def _should_serialize_submit(self, intent: TradeIntent, source: str) -> bool:
        """Whether this submit must hold the per-account serialization lock.

        Engaged only for non-backtest submits at micro_live/live: backtests use
        the simulated broker in a single process, and paper is lock-free by
        design (ADR 0056 / ADR 0026 addendum).
        """
        if source == "backtest":
            return False
        return self._effective_stage(intent) in {"micro_live", "live"}

    def _effective_stage(self, intent: TradeIntent) -> str | None:
        """Resolve the stage governing this intent.

        The runner-bound ``expected_stage`` when present, else the strategy
        config's stage. Mirrors the manifest-drift stage resolution in
        ``_evaluate`` (audit finding #1/#2); a later PR consolidates both onto
        one source.
        """
        if intent.expected_stage is not None:
            return intent.expected_stage
        if intent.strategy_config_path is None:
            return None
        config = self._load_strategy_config(intent.strategy_config_path)
        return config.stage if config is not None else None

    def _submit_lock_name(self) -> str:
        """Account-scoped lock name. One Alpaca account per trading mode in
        Phase 1, so the trading mode keys the account."""
        return f"submit.{get_trading_mode()}"

    def _submit_lock(self) -> AdvisoryLock:
        return AdvisoryLock(
            self._submit_lock_name(),
            locks_dir=self._locks_dir,
            holder_name="milodex-submit",
        )

    def _declined_for_serialization(
        self,
        intent: TradeIntent,
        *,
        source: str,
        session_id: str | None,
        backtest_run_id: int | None,
        reasoning: DecisionReasoning | None,
        error: AdvisoryLockError,
    ) -> ExecutionResult:
        """Fail-closed result when the submit lock could not be acquired.

        Builds a fully-populated result via a preview (read-only; never
        submits), then overrides it to a serialization block. The runner treats
        this like any other blocked decision: no trade this cycle, recorded for
        audit, session continues.
        """
        preview = self._evaluate(intent, preview_only=True, reasoning=reasoning)
        decision = RiskDecision(
            allowed=False,
            summary=(
                "Declined to submit: per-account submit serialization lock "
                "unavailable (fail-closed). No order was sent."
            ),
            checks=[
                RiskCheckResult(
                    name="submit_serialization",
                    passed=False,
                    message=str(error),
                    reason_code="submit_serialization_unavailable",
                )
            ],
            reason_codes=["submit_serialization_unavailable"],
        )
        declined = replace(
            preview,
            status=ExecutionStatus.BLOCKED,
            risk_decision=decision,
            message="Submit declined: serialization lock unavailable (fail-closed).",
            recorded_at=datetime.now(tz=UTC),
        )
        self._record_execution(
            intent,
            declined,
            decision_type="submit",
            session_id=session_id,
            source=source,
            backtest_run_id=backtest_run_id,
        )
        return declined

    def _submit_locked(
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

        # Durable pre-submit outbox row (P1-02): committed BEFORE the broker
        # call so a crash anywhere past this point leaves evidence that an
        # order may exist at the broker (and the duplicate-order veto counts
        # it). Risk has already allowed the intent — blocked intents return
        # above and never reach the outbox boundary. Backtest submits skip
        # the outbox: the simulated broker holds no recoverable state, and a
        # per-fill outbox write+finalize would double the simulation's DB
        # traffic for zero audit value.
        client_order_id: str | None = None
        if source != "backtest":
            client_order_id = str(uuid.uuid4())
            self._event_store.append_execution_attempt(
                ExecutionAttemptEvent(
                    client_order_id=client_order_id,
                    strategy_name=result.execution_request.strategy_name,
                    strategy_config_path=(
                        str(result.execution_request.strategy_config_path)
                        if result.execution_request.strategy_config_path is not None
                        else None
                    ),
                    session_id=session_id,
                    symbol=result.execution_request.symbol,
                    side=result.execution_request.side.value,
                    quantity=result.execution_request.quantity,
                    order_type=result.execution_request.order_type.value,
                    created_at=datetime.now(tz=UTC),
                    status="pending",
                )
            )

        try:
            order = self._broker.submit_order(
                symbol=result.execution_request.symbol,
                side=result.execution_request.side,
                quantity=result.execution_request.quantity,
                order_type=result.execution_request.order_type,
                limit_price=result.execution_request.limit_price,
                stop_price=result.execution_request.stop_price,
                time_in_force=result.execution_request.time_in_force,
                client_order_id=client_order_id,
            )
        except (OrderRejectedError, InsufficientFundsError) as exc:
            if client_order_id is not None:
                self._event_store.finalize_execution_attempt(
                    client_order_id=client_order_id,
                    status="rejected",
                    finalized_at=datetime.now(tz=UTC),
                    failure_detail=str(exc),
                )
            rejected_result = ExecutionResult(
                status=ExecutionStatus.REJECTED,
                execution_request=result.execution_request,
                risk_decision=result.risk_decision,
                account=result.account,
                market_open=result.market_open,
                latest_bar=result.latest_bar,
                message=str(exc),
                recorded_at=datetime.now(tz=UTC),
            )
            self._record_execution(
                intent,
                rejected_result,
                decision_type="submit",
                session_id=session_id,
                source=source,
                backtest_run_id=backtest_run_id,
            )
            return rejected_result
        except Exception as exc:
            # Unexpected broker failure (connection drop, timeout, vendor
            # bug): record the outcome on the attempt row, then re-raise —
            # this path was always fail-loud. NOTE a timeout can occur after
            # the order reached the broker, so delivery is unknown: 'error'
            # attempts count toward the duplicate-order veto (fail-closed),
            # and the client_order_id stored here lets the operator/reconcile
            # match the broker's order list exactly when investigating.
            if client_order_id is not None:
                self._event_store.finalize_execution_attempt(
                    client_order_id=client_order_id,
                    status="error",
                    finalized_at=datetime.now(tz=UTC),
                    failure_detail=f"{type(exc).__name__}: {exc}",
                )
            raise

        # Broker accepted: record the outcome on the attempt row FIRST, so
        # the durable evidence (status='submitted' + broker_order_id)
        # survives even if the explanation/trade write below fails.
        if client_order_id is not None:
            self._event_store.finalize_execution_attempt(
                client_order_id=client_order_id,
                status="submitted",
                finalized_at=datetime.now(tz=UTC),
                broker_order_id=order.id,
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
                # Return contract unchanged (cancelled, None) — the order
                # was cancelled regardless. But the post-cancel status
                # fetch failing during shutdown/kill-switch cancellation
                # must be auditable, not silently swallowed.
                _logger.warning(
                    "cancel_order: broker.get_order(%s) failed after cancel; returning order=None",
                    order_id,
                    exc_info=True,
                )
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
        reasoning: DecisionReasoning | None = None,
        submitted_by: str = "strategy_runner",
        backtest_run_id: int | None = None,
    ) -> None:
        """Record a hold decision (no trade intents emitted) for the event log.

        Routed through the service so every explanation event — preview,
        submit, hold — is constructed in one place. ``backtest_run_id`` is
        the backtest engine's parent ``backtest_runs.id``; the runner path
        leaves it ``None`` and relies on ``session_id`` for ancestry. At
        least one must be supplied or
        :meth:`EventStore.append_explanation` rejects the write.
        """
        account = self._broker.get_account()
        context: dict[str, object | None] = {"message": message}
        if reasoning is not None:
            context["reasoning"] = reasoning.asdict()
        self._event_store.append_explanation(
            ExplanationEvent(
                recorded_at=datetime.now(tz=UTC),
                decision_type="no_trade",
                status="no_signal" if reasoning is not None else "no_action",
                strategy_name=strategy_name,
                strategy_stage=strategy_stage,
                strategy_config_path=str(strategy_config_path),
                config_hash=config_hash,
                symbol=symbol,
                side="hold",
                quantity=0.0,
                order_type="none",
                time_in_force="day",
                submitted_by=submitted_by,
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
                context=context,
                session_id=session_id,
                backtest_run_id=backtest_run_id,
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
        # Full short-circuit applies only to the BYPASS backtest policy
        # (NullRiskEvaluator). ``is_backtest`` is the broader, explicit
        # marker: it also covers the ENFORCE backtest path
        # (BacktestStructuralRiskEvaluator), which must still run its
        # structural checks but must NOT bind the historical replay to live
        # trading-mode/env or a live frozen-manifest lookup.
        bypass_mode = isinstance(self._risk_evaluator, NullRiskEvaluator)
        is_backtest = self._is_backtest or bypass_mode
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
            if is_backtest:
                # ENFORCE backtest (BacktestStructuralRiskEvaluator). The
                # structural checks must still run, but the historical replay
                # must not couple to today's trading-mode/env or perform a
                # live frozen-manifest lookup. ``is_backtest=True`` also
                # short-circuits ``_check_manifest_drift`` (ADR 0030). The
                # structural checks do not read trading_mode /
                # runtime_config_hash / frozen_manifest_hash, so leaving them
                # at their inert defaults is safe and avoids the live binding.
                runtime_config_hash = None
                frozen_manifest_hash = None
                trading_mode = "backtest"
            else:
                runtime_config_hash = (
                    compute_config_hash(normalized_intent.strategy_config_path)
                    if normalized_intent.strategy_config_path is not None
                    else None
                )
                # Lazy import: importing at module scope would create a cycle —
                # `milodex.execution.__init__` eagerly loads this module, and
                # any caller that touches `milodex.execution.models` (e.g. a
                # strategy importing `TradeIntent`) reaches us mid-init of
                # `milodex.promotion.__init__` if the test file is loading that
                # package directly. Deferring to call time breaks the cycle
                # while keeping the surface unchanged.
                from milodex.promotion.manifest import get_active_manifest_hash

                # Resolve the frozen manifest hash from the runner-bound
                # ``expected_stage`` when present, falling back to the YAML
                # stage only when no runner-bound stage exists. This mirrors
                # the evaluator's own ``effective_stage`` logic so BOTH sides
                # of the manifest-drift comparison key off the same stage.
                # Keying the lookup off ``strategy_config.stage`` (re-read
                # from the mutable on-disk YAML each cycle) while the
                # evaluator keys the exemption off ``intent.expected_stage``
                # is a TOCTOU race: a YAML stage flip between reads could let
                # a hash frozen for a different stage satisfy a paper
                # runner's drift check (ADR 0015).
                effective_stage = (
                    normalized_intent.expected_stage
                    if normalized_intent.expected_stage is not None
                    else (strategy_config.stage if strategy_config is not None else None)
                )
                frozen_manifest_hash = (
                    get_active_manifest_hash(
                        strategy_config.name,
                        effective_stage,
                        self._event_store,
                    )
                    if strategy_config is not None
                    else None
                )
                trading_mode = get_trading_mode()
            positions = self._broker.get_positions()
            reconciliation_readiness = latest_readiness(self._event_store)
            context = EvaluationContext(
                intent=normalized_intent,
                request=request,
                account=account,
                positions=positions,
                recent_orders=self._broker.get_orders(limit=100),
                reconciliation_readiness=reconciliation_readiness,
                latest_bar=latest_bar,
                market_open=market_open,
                trading_mode=trading_mode,
                preview_only=preview_only,
                kill_switch_state=self._kill_switch_store.get_state(),
                # ADR 0054 §3: backtests use base risk_defaults (not the active
                # operator profile) so historical replay evaluates strategy
                # potential under stable reference constraints.
                # Non-backtest paths route through load_active_risk_profile()
                # so the active operator posture actually affects enforcement.
                risk_defaults=(
                    load_risk_defaults(self._risk_defaults_path)
                    if is_backtest
                    else load_active_risk_profile(base_path=self._risk_defaults_path)
                ),
                strategy_config=strategy_config,
                runtime_config_hash=runtime_config_hash,
                frozen_manifest_hash=frozen_manifest_hash,
                is_backtest=is_backtest,
                # Plumb the runner-bound stage and risk envelope from the
                # intent through to the risk evaluator. None for operator
                # manual trades — the evaluator falls back to
                # ``strategy_config.<field>`` for those.
                expected_stage=normalized_intent.expected_stage,
                expected_max_positions=normalized_intent.expected_max_positions,
                expected_max_position_pct=normalized_intent.expected_max_position_pct,
                expected_daily_loss_cap_pct=normalized_intent.expected_daily_loss_cap_pct,
                # ADR 0029: provide the event store so the per-strategy
                # concurrent-positions check can reconstruct attribution
                # from the durable trades history.
                event_store=self._event_store,
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

        if intent.order_type is not OrderType.MARKET:
            raise UnsupportedOrderTypeError(intent.order_type)

        # Note: the limit/stop price checks that previously followed here were
        # unreachable — UnsupportedOrderTypeError fires first for any non-market
        # type (Phase 1 is market-only per ADR 0013). Removed as dead code (HR-7).

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
            expected_stage=intent.expected_stage,
            expected_max_positions=intent.expected_max_positions,
            expected_max_position_pct=intent.expected_max_position_pct,
            expected_daily_loss_cap_pct=intent.expected_daily_loss_cap_pct,
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
            # Sustained-breach guard: if the switch is already active (e.g.
            # another strategy on the same account tripped it first, or this
            # strategy is looping while blocked) skip the duplicate cancel +
            # activation sequence.  The switch is already engaged; no state
            # change is needed and cancel_all_orders would be a redundant
            # broker round-trip per blocked submit.
            if self._kill_switch_store.get_state().active:
                return
            # R-P2-5: "halt all trading" includes orders already resting at
            # the broker. Cancel open orders before activating, mirroring the
            # operator SIGINT path (StrategyRunner.shutdown mode="kill_switch").
            # Fail-safe posture: a cancel failure must NEVER block activation —
            # the switch engages regardless; the failure is logged for
            # forensics.
            try:
                self._broker.cancel_all_orders()
            except Exception:
                _logger.warning(
                    "Kill-switch activation: broker cancel_all_orders failed; "
                    "activating the switch anyway.",
                    exc_info=True,
                )
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
        explanation = ExplanationEvent(
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
            backtest_run_id=backtest_run_id,
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

        trade = TradeEvent(
            # Placeholder — append_explanation_and_trade overrides this with
            # the explanation id it inserts inside the shared transaction.
            explanation_id=0,
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
        # One transaction for the pair (P1-02): the audit trail must never
        # hold an explanation whose trade row was lost between commits (or
        # vice versa). All decision types route through the atomic write —
        # preview/blocked/rejected rows keep their exact prior shape, they
        # just can no longer be torn.
        self._event_store.append_explanation_and_trade(explanation=explanation, trade=trade)
