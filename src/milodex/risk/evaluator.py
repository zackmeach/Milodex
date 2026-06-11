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
from milodex.risk.exposure import is_exposure_increasing
from milodex.risk.models import ReconciliationReadiness, RiskCheckResult, RiskDecision

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
    reconciliation_readiness: ReconciliationReadiness | None
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
    # ADR 0030: forward-compatibility hook for backtest-mode evaluation
    # contexts. When True, ``_check_manifest_drift`` short-circuits to a
    # passing result without inspecting ``runtime_config_hash``,
    # ``frozen_manifest_hash``, or the effective stage. The current
    # ``BacktestEngine`` already injects ``NullRiskEvaluator`` (which
    # short-circuits the entire evaluation), so no production call site
    # sets True today — this flag is the clean architectural seam for
    # future research-mode paths (e.g., a preview runner that uses the
    # full evaluator for all checks except manifest enforcement) without
    # paying the manifest-drift refusal cost on backtest-stage queries.
    is_backtest: bool = False


class RiskEvaluator:
    """Evaluate a trade request against Milodex paper-mode risk rules."""

    _CHECKS = (
        "_check_kill_switch",
        "_check_trading_mode",
        "_check_reconciliation_readiness",
        "_check_strategy_stage",
        "_check_manifest_drift",
        "_check_market_open",
        "_check_data_staleness",
        "_check_daily_loss",
        "_check_max_trades_per_day",
        "_check_order_value",
        "_check_single_position_limit",
        "_check_total_exposure",
        "_check_concurrent_positions",
        "_check_strategy_concurrent_positions",
        "_check_duplicate_order",
    )

    def evaluate(self, context: EvaluationContext) -> RiskDecision:
        # Fail-closed wrapper: an unexpected exception inside any single
        # check must NOT propagate out of evaluate() — that would leave the
        # call site with neither an explicit allow nor block. Convert it to
        # a blocking RiskCheckResult so the trade is refused, not undefined.
        checks = [self._run_check(name, context) for name in self._CHECKS]

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

    def _run_check(self, name: str, context: EvaluationContext) -> RiskCheckResult:
        try:
            return getattr(self, name)(context)
        except RuntimeError:
            # ``_check_manifest_drift`` intentionally raises RuntimeError when
            # a promoted stage is missing ``runtime_config_hash`` — that is a
            # caller-wiring programmer error that MUST stay loud (ADR 0015) so
            # it cannot silently regress into a swallowed trade rejection.
            if name == "_check_manifest_drift":
                raise
            return self._fail_closed_result(name)
        except Exception:
            # Fail closed: any unanticipated error blocks the trade rather
            # than aborting evaluate() and leaving the decision undefined.
            return self._fail_closed_result(name)

    @staticmethod
    def _fail_closed_result(check_name: str) -> RiskCheckResult:
        return RiskCheckResult(
            name=check_name.removeprefix("_check_"),
            passed=False,
            message=f"Risk check '{check_name}' raised an unexpected error; failing closed.",
            reason_code="risk_check_error",
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

    def _check_reconciliation_readiness(self, context: EvaluationContext) -> RiskCheckResult:
        if context.is_backtest:
            return RiskCheckResult(
                "reconciliation",
                True,
                "Backtest mode - reconciliation readiness not enforced.",
            )
        if not is_exposure_increasing(context.intent, context.positions):
            return RiskCheckResult(
                "reconciliation",
                True,
                "Intent reduces broker-held exposure; reconciliation gate does not block.",
            )
        readiness = context.reconciliation_readiness
        if readiness is None:
            return RiskCheckResult(
                name="reconciliation",
                passed=False,
                message="No reconciliation readiness verdict is available; failing closed.",
                reason_code="reconciliation_required",
            )
        if readiness.ready:
            return RiskCheckResult("reconciliation", True, readiness.message)
        return RiskCheckResult(
            name="reconciliation",
            passed=False,
            message=readiness.message,
            reason_code=readiness.reason_code or "reconciliation_required",
        )

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

        ADR 0030: ``context.is_backtest=True`` short-circuits the check to a
        passing result before any other inspection. The manifest-drift refusal
        is a production-evidence guarantee; backtests are exploratory simulations
        and run against the YAML at invocation time. The audit trail (the
        ``BacktestRunEvent.config_hash``) preserves what was actually tested.
        """
        if context.is_backtest:
            return RiskCheckResult(
                "manifest_drift",
                True,
                "backtest mode — manifest drift not enforced",
            )
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

        # Normalize to UTC-aware before subtracting: a naive bar timestamp
        # would raise TypeError against an aware ``now`` (offset-naive vs
        # offset-aware). A naive timestamp is assumed UTC — the system
        # stores and compares all market data in UTC.
        bar_ts = context.latest_bar.timestamp
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=UTC)
        age = datetime.now(tz=UTC) - bar_ts
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

    def _check_max_trades_per_day(self, context: EvaluationContext) -> RiskCheckResult:
        """Guard against runaway submission logic by counting today's paper trades.

        Counts ACCOUNT-WIDE submitted paper trades since UTC midnight today.
        Using a per-account count (rather than per-strategy) is the conservative,
        attribution-free reading of the "prevents runaway logic" rationale in
        ``configs/risk_defaults.yaml``: each strategy contributing toward a shared
        daily quota errs toward blocking when the fleet approaches the limit.

        Day boundary: UTC midnight (simplest; no DST ambiguity).

        Strict semantics: the check fires BEFORE the N+1th trade is written —
        i.e. when ``today_count >= limit`` the (N+1)th trade is blocked. So the
        Nth trade is the last one allowed; the (N+1)th is the first one blocked.

        Skipped gracefully (returns passing) when ``context.event_store`` is None
        (legacy callers / operator manual trades not routed through the execution
        service). This mirrors the skip pattern in
        :meth:`_check_strategy_concurrent_positions`.

        Fail-closed: a query error blocks the trade rather than silently allowing
        an uncountable submission, consistent with the duplicate-order backstop.
        """
        if context.event_store is None:
            return RiskCheckResult(
                "max_trades_per_day",
                True,
                "No event store available; skipping max-trades-per-day check.",
            )
        limit = context.risk_defaults.max_trades_per_day
        try:
            today_count = context.event_store.count_submitted_trades_today()
        except Exception:
            return RiskCheckResult(
                name="max_trades_per_day",
                passed=False,
                message=(
                    "Daily trade count query failed; failing closed "
                    "(cannot verify the daily trade limit)."
                ),
                reason_code="max_trades_per_day_exceeded",
            )
        if today_count >= limit:
            return RiskCheckResult(
                name="max_trades_per_day",
                passed=False,
                message=(
                    f"Account-wide paper trades today ({today_count}) has reached or "
                    f"exceeded the daily limit ({limit}). "
                    "Day resets at UTC midnight."
                ),
                reason_code="max_trades_per_day_exceeded",
            )
        return RiskCheckResult(
            "max_trades_per_day",
            True,
            f"Daily trade count ({today_count}) is within the limit ({limit}).",
        )

    def _check_order_value(self, context: EvaluationContext) -> RiskCheckResult:
        """Fat-finger cap on single-order notional value.

        The cap exists to stop oversized ENTRIES — a fat-fingered quantity
        opening or growing a position. Exposure-REDUCING orders are exempt
        (DC-1, 2026-06-10): a held position must always be exitable in full,
        even when its market value has grown past the cap (and the
        conservative ``_estimate_unit_price`` max() would otherwise inflate
        a legitimate exit toward the limit). Classification reuses
        :func:`milodex.risk.exposure.is_exposure_increasing` — the same
        plumbing as the reconciliation gate — so a sell with no covering
        broker position (short-opening) or beyond the held quantity counts
        as increasing and stays capped.
        """
        if not is_exposure_increasing(context.intent, context.positions):
            return RiskCheckResult(
                "order_value",
                True,
                "Exposure-reducing order is exempt from the max-order-value cap; "
                "the cap targets oversized entries and a held position must "
                "always be exitable.",
            )
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
        # In-flight (unfilled) BUY orders are real economic exposure before they
        # fill — count them too, so a burst of BUYs before any fill cannot
        # overshoot the cap (ADR 0024: caps bound real exposure, not only filled
        # positions). Add each open BUY's ``remaining_notional`` (the *unfilled*
        # portion): the already-filled portion of a partial fill is a broker
        # position already in ``positions.market_value``, so the remainder is
        # exactly the exposure not yet reflected there — no double-count, and no
        # held-symbol special case is needed.
        #
        # Known gaps (acceptable in Phase 1 paper; live-capital-gate items):
        #   - Unpriced pending *market* orders have no fill price yet, so
        #     ``remaining_notional`` is None and they are omitted from exposure
        #     (they still consume a concurrent-position slot in
        #     ``_check_concurrent_positions``). Phase 1 is market-only (ADR 0013).
        #   - ``context.recent_orders`` is the broker's ``get_orders(limit=100)``
        #     snapshot. Unlike ``_check_duplicate_order``, this cap check has no
        #     durable event-store backstop, so >100 in-window orders could drop a
        #     pending BUY (undercount). Bounded in Phase 1; tighten at the live gate.
        #   - The per-strategy cap (``_check_strategy_concurrent_positions``) does
        #     not yet count in-flight orders (broker orders carry no strategy
        #     attribution); see RISK_POLICY.md "Known limitations".
        current_exposure += sum(
            order.remaining_notional
            for order in context.recent_orders
            if order.is_open
            and order.side == OrderSide.BUY
            and order.remaining_notional is not None
        )
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
        existing = {
            position.symbol.upper() for position in context.positions if position.quantity > 0
        }
        # In-flight (unfilled) BUY orders occupy a concurrent-position slot too:
        # a burst of distinct-symbol BUYs before any fill must not exceed the
        # cap. Data is already in ``context.recent_orders`` (no new broker call).
        pending = {
            order.symbol.upper()
            for order in context.recent_orders
            if order.is_open and order.side == OrderSide.BUY
        }
        occupied = existing | pending
        projected_count = len(occupied)
        symbol = context.intent.normalized_symbol()
        if context.intent.side == OrderSide.BUY and symbol not in occupied:
            projected_count += 1
        # A full SELL frees a held slot only when no in-flight BUY for the same
        # symbol will re-open it (conservative: keep the slot if one is pending).
        if context.intent.side == OrderSide.SELL and symbol in existing and symbol not in pending:
            position = next(
                position for position in context.positions if position.symbol.upper() == symbol
            )
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

        # Attribution must walk the same trade universe that opened the
        # positions under evaluation: backtest contexts (ADR 0030
        # ``is_backtest``) opened theirs with source='backtest' fills,
        # live contexts with source='paper'. R-P0-1 scoped the walk by
        # source, making the two universes mutually invisible.
        attribution_source = "backtest" if context.is_backtest else "paper"

        existing_symbols = {
            position.symbol.upper() for position in context.positions if position.quantity > 0
        }
        owned = 0
        for symbol in existing_symbols:
            owner = attribute_position(
                symbol=symbol, event_store=context.event_store, source=attribution_source
            )
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
                and attribute_position(
                    symbol=symbol, event_store=context.event_store, source=attribution_source
                )
                == proposing_strategy
            )
            if not currently_owned_by_strategy:
                projected_count += 1
        elif context.intent.side == OrderSide.SELL and symbol in existing_symbols:
            position = next(pos for pos in context.positions if pos.symbol.upper() == symbol)
            owner = attribute_position(
                symbol=symbol, event_store=context.event_store, source=attribution_source
            )
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

        # Durable backstop: ``context.recent_orders`` is truncated at the
        # broker's limit=100 fetch, so with >100 orders inside the window
        # the matching prior order is silently dropped — precisely when
        # order volume is high. The event store is the authoritative,
        # untruncated trade history. Query it for the same symbol/side
        # within the same window. If the query errors, FAIL CLOSED: a
        # dedup veto that cannot verify must block, not silently allow.
        if context.event_store is not None:
            try:
                durable_matches = context.event_store.count_recent_submitted_orders(
                    symbol=context.intent.normalized_symbol(),
                    side=context.intent.side.value,
                    since=now - window,
                )
            except Exception:
                return RiskCheckResult(
                    name="duplicate_order",
                    passed=False,
                    message=(
                        "Duplicate-order history query failed; failing closed "
                        "(cannot verify the order is not a duplicate)."
                    ),
                    reason_code="duplicate_order_window",
                )
            if durable_matches > 0:
                return RiskCheckResult(
                    name="duplicate_order",
                    passed=False,
                    message=(
                        "Recent matching order found in durable history within "
                        "duplicate-order window."
                    ),
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
