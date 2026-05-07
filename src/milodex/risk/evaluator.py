"""Risk evaluator and supporting types.

The risk layer sits between every strategy decision and every trade
execution with veto power. ``RiskEvaluator.evaluate`` runs every rule
defined by `docs/RISK_POLICY.md` and returns a structured
``RiskDecision`` — the rest of the system never bypasses it.

This module lives in ``milodex.risk`` to match the module map documented
in ``CLAUDE.md`` / ``AGENTS.md`` / ``docs/VISION.md``. The old import
path ``milodex.execution.risk`` is preserved as a thin re-export for
backwards compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from milodex.broker.models import OrderSide, OrderStatus
from milodex.risk.config import RiskDefaults
from milodex.risk.models import RiskCheckResult, RiskDecision

if TYPE_CHECKING:
    from milodex.broker.models import AccountInfo, Order, Position
    from milodex.core.event_store import EventStore
    from milodex.data.models import Bar
    from milodex.execution.config import StrategyExecutionConfig
    from milodex.execution.models import ExecutionRequest, TradeIntent
    from milodex.execution.state import KillSwitchState


@dataclass(frozen=True)
class EvaluationContext:
    """Inputs used by the risk evaluator."""

    intent: TradeIntent
    request: ExecutionRequest
    account: AccountInfo
    positions: list[Position]
    recent_orders: list[Order]
    latest_bar: Bar | None
    market_open: bool
    trading_mode: str
    preview_only: bool
    kill_switch_state: KillSwitchState
    risk_defaults: RiskDefaults
    strategy_config: StrategyExecutionConfig | None = None
    runtime_config_hash: str | None = None
    frozen_manifest_hash: str | None = None
    # Runner-bound stage, set once at strategy-runner startup and immutable for
    # the life of the runner session. When present, the manifest_drift exemption
    # is keyed off this value instead of the per-cycle YAML stage field — the
    # YAML on disk can flap (parallel agent edits, git checkouts, backtest runs)
    # but the runner's bound stage cannot. None for operator manual trades and
    # for legacy callers that haven't been routed through the runner — those
    # fall back to ``strategy_config.stage`` (current behavior). See
    # ``docs/reviews/2026-05-06-manifest-drift-toctou-race.md``.
    expected_stage: str | None = None
    # Runner-bound risk envelope, snapshot of the strategy YAML's risk fields
    # at config-load time. Same TOCTOU class as ``expected_stage``: a parallel
    # writer raising ``max_positions`` (or any cap) mid-session must not let a
    # cycle in flight take a position that exceeds the runner's bound envelope.
    # ``min(global_default, per_strategy)`` already provides defense against
    # mid-session raises being neutralized by the global cap, but it doesn't
    # guarantee cycle-to-cycle consistency across a long-running runner; these
    # bindings do. None for callers not routed through a runner — those fall
    # back to ``strategy_config.<field>`` (current behavior).
    expected_max_positions: int | None = None
    expected_max_position_pct: float | None = None
    expected_daily_loss_cap_pct: float | None = None
    # Event store for per-strategy attribution reconstruction (ADR 0029).
    # The new ``_check_strategy_concurrent_positions`` consults trade
    # history to count positions attributed to the proposing strategy.
    # When None, the per-strategy check is skipped — it is a no-op for
    # callers that haven't been routed through the execution service
    # (e.g. legacy entry points). The account-scoped check
    # (``_check_concurrent_positions``, ADR 0024) is unaffected.
    event_store: EventStore | None = None


class RiskEvaluator:
    """Evaluate a trade request against Milodex paper-mode risk rules."""

    def evaluate(self, context: EvaluationContext) -> RiskDecision:
        checks = [
            self._check_kill_switch(context),
            self._check_trading_mode(context),
            self._check_strategy_stage(context),
            self._check_manifest_drift(context),
            self._check_market_open(context),
            self._check_data_staleness(context),
            self._check_daily_loss(context),
            self._check_order_value(context),
            self._check_single_position_limit(context),
            self._check_total_exposure(context),
            self._check_concurrent_positions(context),
            self._check_strategy_concurrent_positions(context),
            self._check_duplicate_order(context),
        ]

        allowed = all(check.passed for check in checks)
        reason_codes = [
            check.reason_code for check in checks if not check.passed and check.reason_code
        ]
        summary = "Allowed" if allowed else "Blocked by risk checks"
        return RiskDecision(
            allowed=allowed,
            summary=summary,
            checks=checks,
            reason_codes=reason_codes,
        )

    def _check_kill_switch(self, context: EvaluationContext) -> RiskCheckResult:
        if context.kill_switch_state.active:
            return RiskCheckResult(
                name="kill_switch",
                passed=False,
                message="Kill switch is active. Manual reset is required before trading.",
                reason_code="kill_switch_active",
            )
        return RiskCheckResult("kill_switch", True, "Kill switch is inactive.")

    def _check_trading_mode(self, context: EvaluationContext) -> RiskCheckResult:
        if context.preview_only:
            return RiskCheckResult("paper_mode", True, "Preview does not require paper mode.")
        if context.trading_mode != "paper":
            return RiskCheckResult(
                name="paper_mode",
                passed=False,
                message="Trade submit is paper-only in this milestone.",
                reason_code="paper_mode_required",
            )
        return RiskCheckResult("paper_mode", True, "Paper trading mode confirmed.")

    def _check_strategy_stage(self, context: EvaluationContext) -> RiskCheckResult:
        if context.strategy_config is None:
            return RiskCheckResult("strategy_stage", True, "Manual trade using global defaults.")

        if not context.strategy_config.enabled:
            return RiskCheckResult(
                name="strategy_stage",
                passed=False,
                message=f"Strategy '{context.strategy_config.name}' is disabled.",
                reason_code="strategy_disabled",
            )

        # Prefer the runner-bound stage when present (closes the same TOCTOU
        # class as ``_check_manifest_drift``: a parallel writer flipping the
        # YAML's stage field mid-session must not change the eligibility
        # decision for a runner already in flight). Falls back to the YAML's
        # per-cycle stage for callers without runner binding.
        effective_stage = context.expected_stage or context.strategy_config.stage
        if effective_stage not in {"backtest", "paper"}:
            return RiskCheckResult(
                name="strategy_stage",
                passed=False,
                message=(
                    f"Strategy '{context.strategy_config.name}' stage "
                    f"'{effective_stage}' is not eligible for paper submission."
                ),
                reason_code="strategy_stage_ineligible",
            )

        return RiskCheckResult(
            "strategy_stage",
            True,
            f"Strategy '{context.strategy_config.name}' is eligible for paper execution.",
        )

    def _check_manifest_drift(self, context: EvaluationContext) -> RiskCheckResult:
        """ADR 0015: refuse execution when YAML has drifted from the frozen snapshot.

        Scoped to promoted stages (``paper``, ``micro_live``, ``live``). Manual
        operator trades (no ``strategy_config``) and ``backtest``-stage strategies
        are exempt — the former have no strategy to anchor, the latter have no
        promoted state to freeze.

        The exemption decision is keyed off ``context.expected_stage`` when set
        (the strategy runner's bound stage at startup) instead of
        ``context.strategy_config.stage`` (re-loaded per cycle from disk). This
        closes a TOCTOU race in which a parallel writer flipping the YAML's
        stage field to ``backtest`` for one read cycle could fire a paper order
        through the gate. See
        ``docs/reviews/2026-05-06-manifest-drift-toctou-race.md``.
        """
        if context.strategy_config is None:
            return RiskCheckResult(
                "manifest_drift",
                True,
                "Manual trade; manifest drift check not applicable.",
            )
        # Prefer the runner-bound stage when present. Falls back to the YAML's
        # per-cycle stage for callers that haven't been routed through a runner
        # (operator manual flows, legacy entry points). The fallback preserves
        # the original behavior for those paths.
        effective_stage = context.expected_stage or context.strategy_config.stage
        if effective_stage not in {"paper", "micro_live", "live"}:
            return RiskCheckResult(
                "manifest_drift",
                True,
                f"Stage '{effective_stage}' is exempt from manifest drift.",
            )
        # Promoted stages MUST supply the runtime hash. A None here means the
        # caller skipped manifest plumbing — that is a programmer error, not a
        # trade-level rejection. Fail loud so it cannot silently regress.
        if context.runtime_config_hash is None:
            raise RuntimeError(
                f"Promoted stage '{effective_stage}' for strategy "
                f"'{context.strategy_config.name}' requires runtime_config_hash; "
                "caller must populate EvaluationContext.runtime_config_hash."
            )
        if context.frozen_manifest_hash is None:
            return RiskCheckResult(
                name="manifest_drift",
                passed=False,
                message=(
                    f"Strategy '{context.strategy_config.name}' has no frozen manifest "
                    f"at stage '{effective_stage}'. Run "
                    "'milodex promotion freeze' to snapshot the current config."
                ),
                reason_code="no_frozen_manifest",
            )
        if context.runtime_config_hash != context.frozen_manifest_hash:
            return RiskCheckResult(
                name="manifest_drift",
                passed=False,
                message=(
                    f"Runtime config hash {context.runtime_config_hash[:12]} "
                    f"differs from frozen manifest {context.frozen_manifest_hash[:12]} "
                    f"at stage '{effective_stage}'."
                ),
                reason_code="manifest_drift",
            )
        return RiskCheckResult(
            "manifest_drift",
            True,
            "Runtime config matches frozen manifest.",
        )

    def _check_market_open(self, context: EvaluationContext) -> RiskCheckResult:
        if context.preview_only:
            return RiskCheckResult("market_hours", True, "Preview allowed outside market hours.")
        if not context.market_open:
            return RiskCheckResult(
                name="market_hours",
                passed=False,
                message="Market is closed; paper submit is blocked.",
                reason_code="market_closed",
            )
        return RiskCheckResult("market_hours", True, "Market is open.")

    def _check_data_staleness(self, context: EvaluationContext) -> RiskCheckResult:
        if context.latest_bar is None:
            return RiskCheckResult(
                name="data_staleness",
                passed=False,
                message="No latest bar available for risk evaluation.",
                reason_code="no_latest_bar",
            )

        age = datetime.now(tz=UTC) - context.latest_bar.timestamp
        max_age = timedelta(seconds=context.risk_defaults.max_data_staleness_seconds)
        if age > max_age:
            return RiskCheckResult(
                name="data_staleness",
                passed=False,
                message=f"Latest bar is stale by {int(age.total_seconds())} seconds.",
                reason_code="stale_market_data",
            )
        return RiskCheckResult("data_staleness", True, "Latest bar is within staleness limits.")

    def _check_daily_loss(self, context: EvaluationContext) -> RiskCheckResult:
        equity_base = max(context.account.portfolio_value - context.account.daily_pnl, 1.0)
        current_loss_pct = max(0.0, -context.account.daily_pnl / equity_base)

        if (
            context.risk_defaults.kill_switch_enabled
            and current_loss_pct > context.risk_defaults.kill_switch_max_drawdown_pct
        ):
            return RiskCheckResult(
                name="daily_loss",
                passed=False,
                message=(
                    f"Daily loss {current_loss_pct:.2%} exceeded kill switch threshold "
                    f"{context.risk_defaults.kill_switch_max_drawdown_pct:.2%}."
                ),
                reason_code="kill_switch_threshold_breached",
            )

        max_daily_loss = self._effective_daily_loss_pct(context)
        if current_loss_pct > max_daily_loss:
            return RiskCheckResult(
                name="daily_loss",
                passed=False,
                message=f"Daily loss {current_loss_pct:.2%} exceeds cap {max_daily_loss:.2%}.",
                reason_code="daily_loss_cap_exceeded",
            )

        return RiskCheckResult("daily_loss", True, "Daily loss is within configured limits.")

    def _check_order_value(self, context: EvaluationContext) -> RiskCheckResult:
        max_value = context.account.portfolio_value * context.risk_defaults.max_order_value_pct
        if context.request.estimated_order_value > max_value:
            return RiskCheckResult(
                name="order_value",
                passed=False,
                message=(
                    f"Estimated order value {_fmt_money(context.request.estimated_order_value)} "
                    f"exceeds limit {_fmt_money(max_value)}."
                ),
                reason_code="max_order_value_exceeded",
            )
        return RiskCheckResult("order_value", True, "Estimated order value is within limits.")

    def _check_single_position_limit(self, context: EvaluationContext) -> RiskCheckResult:
        projected_value = self._projected_position_value(context)
        max_pct = self._effective_position_pct(context)
        max_value = context.account.portfolio_value * max_pct
        if projected_value > max_value:
            return RiskCheckResult(
                name="single_position",
                passed=False,
                message=(
                    f"Projected position value {_fmt_money(projected_value)} "
                    f"exceeds limit {_fmt_money(max_value)}."
                ),
                reason_code="max_single_position_exceeded",
            )
        return RiskCheckResult("single_position", True, "Projected position size is within limits.")

    def _check_total_exposure(self, context: EvaluationContext) -> RiskCheckResult:
        current_exposure = sum(position.market_value for position in context.positions)
        delta = context.request.estimated_order_value
        if context.intent.side == OrderSide.BUY:
            projected_exposure = current_exposure + delta
        else:
            projected_exposure = max(0.0, current_exposure - delta)
        max_exposure = (
            context.account.portfolio_value * context.risk_defaults.max_total_exposure_pct
        )
        if projected_exposure > max_exposure:
            return RiskCheckResult(
                name="total_exposure",
                passed=False,
                message=(
                    f"Projected exposure {_fmt_money(projected_exposure)} "
                    f"exceeds limit {_fmt_money(max_exposure)}."
                ),
                reason_code="max_total_exposure_exceeded",
            )
        return RiskCheckResult("total_exposure", True, "Projected total exposure is within limits.")

    def _check_concurrent_positions(self, context: EvaluationContext) -> RiskCheckResult:
        existing = {position.symbol for position in context.positions if position.quantity > 0}
        projected_count = len(existing)
        symbol = context.intent.normalized_symbol()
        if context.intent.side == OrderSide.BUY and symbol not in existing:
            projected_count += 1
        if context.intent.side == OrderSide.SELL and symbol in existing:
            position = next(position for position in context.positions if position.symbol == symbol)
            if context.intent.quantity >= position.quantity:
                projected_count -= 1

        max_positions = self._effective_max_positions(context)
        if projected_count > max_positions:
            return RiskCheckResult(
                name="concurrent_positions",
                passed=False,
                message=(
                    f"Projected open positions {projected_count} exceeds limit {max_positions}."
                ),
                reason_code="max_concurrent_positions_exceeded",
            )
        return RiskCheckResult(
            "concurrent_positions",
            True,
            "Projected open positions are within limits.",
        )

    def _check_strategy_concurrent_positions(self, context: EvaluationContext) -> RiskCheckResult:
        """Per-strategy concurrent-positions cap (ADR 0029).

        Counts current broker positions whose attribution (reconstructed
        from the durable ``trades`` history per
        :func:`milodex.risk.attribution.attribute_position`) matches the
        proposing strategy, projects the post-trade count using the same
        rules as :meth:`_check_concurrent_positions`, and refuses when
        the projected count would exceed the strategy's own declared
        cap.

        Reads ``context.expected_max_positions`` directly per ADR 0029
        Decision 6 — the per-strategy cap is an independent ceiling and
        MUST NOT be clamped by the global default via
        :meth:`_effective_max_positions`. That clamp is for the
        account-scoped check.

        Skipped (returns a passing :class:`RiskCheckResult`) when:
          - ``context.expected_max_positions`` is None (operator manual
            trades, or a strategy YAML with no ``risk.max_positions``
            field).
          - ``context.event_store`` is None (caller not routed through
            the execution service — preserves the pre-ADR-0029 behavior
            for legacy entry points).
          - ``context.request.strategy_name`` is None (operator
            attribution; the operator pseudo-strategy has no per-
            strategy cap).

        Independent of the existing account-scoped check
        (:meth:`_check_concurrent_positions`, ADR 0024). Both must pass
        for the trade to be allowed; either failure produces a distinct
        reason code in :attr:`RiskDecision.reason_codes`.
        """
        if context.expected_max_positions is None:
            return RiskCheckResult(
                "strategy_concurrent_positions",
                True,
                "No per-strategy cap declared.",
            )
        if context.event_store is None:
            return RiskCheckResult(
                "strategy_concurrent_positions",
                True,
                "No event store available; skipping per-strategy attribution.",
            )
        proposing_strategy = context.request.strategy_name
        if proposing_strategy is None:
            return RiskCheckResult(
                "strategy_concurrent_positions",
                True,
                "Operator-attributed trade has no per-strategy cap.",
            )

        # Lazy import keeps this module importable without circular issues.
        from milodex.risk.attribution import attribute_position

        existing_symbols = {
            position.symbol.upper() for position in context.positions if position.quantity > 0
        }
        owned = 0
        for symbol in existing_symbols:
            owner = attribute_position(symbol=symbol, event_store=context.event_store)
            if owner == proposing_strategy:
                owned += 1

        symbol = context.intent.normalized_symbol()
        projected_count = owned
        if context.intent.side == OrderSide.BUY:
            # An entry adds 1 only if the strategy doesn't already hold
            # the symbol (mirrors _check_concurrent_positions). Whether
            # the strategy *currently* holds the symbol is determined by
            # attribution: strategy A buying more of a symbol it owns
            # adds zero slots; strategy B buying a symbol that strategy
            # A holds adds 1 (under per-strategy semantics).
            currently_owned_by_strategy = (
                symbol in existing_symbols
                and attribute_position(symbol=symbol, event_store=context.event_store)
                == proposing_strategy
            )
            if not currently_owned_by_strategy:
                projected_count += 1
        elif context.intent.side == OrderSide.SELL and symbol in existing_symbols:
            position = next(pos for pos in context.positions if pos.symbol.upper() == symbol)
            owner = attribute_position(symbol=symbol, event_store=context.event_store)
            if owner == proposing_strategy and context.intent.quantity >= position.quantity:
                projected_count -= 1

        cap = context.expected_max_positions
        if projected_count > cap:
            return RiskCheckResult(
                name="strategy_concurrent_positions",
                passed=False,
                message=(
                    f"Projected positions {projected_count} attributed to strategy "
                    f"'{proposing_strategy}' exceeds per-strategy limit {cap}."
                ),
                reason_code="max_strategy_positions_exceeded",
            )
        return RiskCheckResult(
            "strategy_concurrent_positions",
            True,
            f"Projected positions for strategy '{proposing_strategy}' are within limits.",
        )

    def _check_duplicate_order(self, context: EvaluationContext) -> RiskCheckResult:
        window = timedelta(seconds=context.risk_defaults.duplicate_order_window_seconds)
        now = datetime.now(tz=UTC)
        duplicates = [
            order
            for order in context.recent_orders
            if order.symbol.upper() == context.intent.normalized_symbol()
            and order.side == context.intent.side
            and order.status not in {OrderStatus.CANCELLED, OrderStatus.REJECTED}
            and now - order.submitted_at <= window
        ]
        if duplicates:
            return RiskCheckResult(
                name="duplicate_order",
                passed=False,
                message="Recent matching order found within duplicate-order window.",
                reason_code="duplicate_order_window",
            )
        return RiskCheckResult("duplicate_order", True, "No duplicate orders detected.")

    def _effective_daily_loss_pct(self, context: EvaluationContext) -> float:
        if context.strategy_config is None:
            return context.risk_defaults.max_daily_loss_pct
        # Prefer the runner-bound cap when present (TOCTOU follow-up: a
        # parallel writer raising ``daily_loss_cap_pct`` mid-session must not
        # let a cycle in flight exceed the runner's bound envelope). Falls
        # back to the YAML value for callers without runner binding.
        per_strategy = (
            context.expected_daily_loss_cap_pct
            if context.expected_daily_loss_cap_pct is not None
            else context.strategy_config.daily_loss_cap_pct
        )
        return min(context.risk_defaults.max_daily_loss_pct, per_strategy)

    def _effective_position_pct(self, context: EvaluationContext) -> float:
        if context.strategy_config is None:
            return context.risk_defaults.max_single_position_pct
        per_strategy = (
            context.expected_max_position_pct
            if context.expected_max_position_pct is not None
            else context.strategy_config.max_position_pct
        )
        return min(context.risk_defaults.max_single_position_pct, per_strategy)

    def _effective_max_positions(self, context: EvaluationContext) -> int:
        if context.strategy_config is None:
            return context.risk_defaults.max_concurrent_positions
        per_strategy = (
            context.expected_max_positions
            if context.expected_max_positions is not None
            else context.strategy_config.max_positions
        )
        return min(context.risk_defaults.max_concurrent_positions, per_strategy)

    def _projected_position_value(self, context: EvaluationContext) -> float:
        current = next(
            (
                position.market_value
                for position in context.positions
                if position.symbol.upper() == context.intent.normalized_symbol()
            ),
            0.0,
        )
        delta = context.request.estimated_order_value
        if context.intent.side == OrderSide.BUY:
            return current + delta
        return max(0.0, current - delta)


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"
