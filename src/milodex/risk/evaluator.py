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
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

from milodex.broker.models import OrderSide
from milodex.risk.config import RiskDefaults
from milodex.risk.disable_conditions import effective_disable_conditions
from milodex.risk.exposure import exposure_increasing_notional, is_exposure_increasing
from milodex.risk.models import ReconciliationReadiness, RiskCheckResult, RiskDecision
from milodex.risk.staleness import staleness_verdict

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
    # Latest completed exchange session (calendar date), resolved from the
    # broker at context-assembly time. Authoritative for the 1D staleness
    # gate: a daily bar is fresh iff its session date equals this. None means
    # the calendar could not be resolved (or the caller did not supply it,
    # e.g. operator-manual / legacy paths) — the 1D path fails closed on None,
    # and the non-1D path ignores it (300s wall clock). See
    # ``milodex.risk.staleness.staleness_verdict``.
    latest_completed_session: date | None = None
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
        "_check_disable_conditions",
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
        "_check_opposite_side_order",
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
        # Consume the canonical stage/mode table (SRS R-PRM-002): the risk
        # layer must never be looser than the promotion policy the CLI and
        # bench preflight already enforce. Unrecognized modes resolve to an
        # empty allow-set — fail closed. Preview is intentionally NOT exempt:
        # stage eligibility does not depend on whether the order is actually
        # sent, and a preview verdict must predict the submit verdict.
        #
        # Lazy import: a module-scope import is a circular-import landmine —
        # ``milodex.promotion.__init__`` eagerly loads ``manifest`` →
        # ``strategies.__init__`` → strategy classes → ``execution.__init__``
        # → ``service`` → ``milodex.risk`` (mid-init). Same cycle and same
        # fix as the ``get_active_manifest_hash`` import in
        # ``execution/service.py``.
        from milodex.promotion.stage_compat import ALLOWED_STAGES_BY_MODE

        allowed_stages = ALLOWED_STAGES_BY_MODE.get(context.trading_mode, frozenset())
        if effective_stage not in allowed_stages:
            return RiskCheckResult(
                name="strategy_stage",
                passed=False,
                message=(
                    f"Strategy '{context.strategy_config.name}' stage "
                    f"'{effective_stage}' is not eligible for "
                    f"'{context.trading_mode}'-mode submission."
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

    def _check_disable_conditions(self, context: EvaluationContext) -> RiskCheckResult:
        """SRS R-STR-014: halt the strategy when any active disable condition is true.

        The effective catalog is the strategy family's defaults (per
        ``docs/strategy-families.md``, mirrored in
        :mod:`milodex.risk.disable_conditions`) plus the config's
        ``disable_conditions_additional`` strings. Only the auto-evaluable
        subset can veto; declared-only conditions (no signal available in
        this context) are surfaced in the passing message but never block —
        see the module-level triage table in
        :mod:`milodex.risk.disable_conditions`.

        Fail-closed per evaluator: an evaluator that raises is treated as
        ACTIVE (veto) with the error in the message — a broken safety
        evaluator must not silently pass. This is deliberately stricter than
        the generic ``_run_check`` wrapper: the reason code stays
        ``disable_condition_active`` and names the condition, so the
        explanation record attributes the halt to the catalog rather than a
        generic ``risk_check_error``.

        Exemptions: manual operator trades carry no strategy and therefore
        no catalog; backtests sit below the risk layer by design (ADR 0030)
        and never reach this check via the production evaluator either —
        the ``is_backtest`` short-circuit mirrors ``_check_manifest_drift``
        as defense in depth for future research-mode contexts.
        """
        if context.is_backtest:
            return RiskCheckResult(
                "disable_conditions",
                True,
                "backtest mode — disable conditions not enforced (ADR 0030)",
            )
        if context.strategy_config is None:
            return RiskCheckResult(
                "disable_conditions",
                True,
                "Manual trade; no strategy disable-condition catalog applies.",
            )
        conditions = effective_disable_conditions(
            context.strategy_config.family,
            context.strategy_config.disable_conditions_additional,
        )
        active_details: list[str] = []
        evaluated = 0
        declared_only = 0
        for condition in conditions:
            if condition.evaluator is None:
                declared_only += 1
                continue
            evaluated += 1
            try:
                outcome = condition.evaluator(context)
            except Exception as exc:  # noqa: BLE001 — fail-closed by design
                active_details.append(
                    f"{condition.condition_id}: evaluator raised {exc!r}; "
                    "failing closed (a broken safety evaluator must not silently pass)"
                )
                continue
            if outcome.active:
                active_details.append(f"{condition.condition_id}: {outcome.detail}")
        if active_details:
            return RiskCheckResult(
                name="disable_conditions",
                passed=False,
                message=(
                    f"Active disable condition(s) for strategy "
                    f"'{context.strategy_config.name}': " + "; ".join(active_details)
                ),
                reason_code="disable_condition_active",
            )
        return RiskCheckResult(
            "disable_conditions",
            True,
            (
                f"No active disable conditions ({evaluated} auto-evaluated inactive; "
                f"{declared_only} declared-only, not auto-evaluable)."
            ),
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
        # The "no latest bar" case keeps its own reason code (``no_latest_bar``)
        # — distinct from a stale-but-present bar. The shared policy treats both
        # as stale, so branch on the bar's presence first, then delegate the
        # fresh-vs-stale decision to the single staleness policy (which both
        # this veto and the ``data_quality_issue`` disable condition consult,
        # so the two gates cannot diverge).
        if context.latest_bar is None:
            return RiskCheckResult(
                name="data_staleness",
                passed=False,
                message="No latest bar available for risk evaluation.",
                reason_code="no_latest_bar",
            )
        verdict = staleness_verdict(context, datetime.now(tz=UTC))
        if verdict.is_stale:
            return RiskCheckResult(
                name="data_staleness",
                passed=False,
                message=f"Latest bar rejected: {verdict.detail}.",
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
            # A naked short or sell-beyond-held INCREASES exposure; only the
            # portion covered by a held long genuinely nets down (A-6). Without
            # this split a short read as benign long-side notional and slipped
            # past the cap. exposure_increasing_notional shares is_exposure_
            # increasing's held-qty logic, so all three caps agree on direction.
            increasing = exposure_increasing_notional(
                context.intent, context.request, context.positions
            )
            reducing = delta - increasing  # the covered portion that truly nets down
            projected_exposure = max(0.0, current_exposure - reducing) + increasing
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

        # Account-scoped cap (ADR 0024): ``projected_count`` above counts EVERY
        # broker position + in-flight BUY regardless of which strategy opened it,
        # so it is bounded by the GLOBAL account cap alone — never clamped by the
        # proposing strategy's own ``risk.max_positions``. That per-strategy bound
        # is a separate, additive ceiling enforced in
        # ``_check_strategy_concurrent_positions`` (reason code
        # ``max_strategy_positions_exceeded``, ADR 0029). Clamping the account cap
        # down to one strategy's ``max_positions`` deadlocked the whole paper fleet
        # on 2026-07-13/14: 3 unrelated broker positions + regime's
        # ``max_positions=1`` vetoed every fleet BUY with "4 exceeds limit 1".
        max_positions = context.risk_defaults.max_concurrent_positions
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
        """Per-strategy concurrent-positions cap (ADR 0029 / ADR 0055).

        Counts the symbols this strategy currently holds, projects the
        post-trade count using the same rules as
        :meth:`_check_concurrent_positions`, and refuses when the
        projected count would exceed the strategy's own declared cap.

        The owned set comes from the strategy-scoped submitted-fill ledger
        (:func:`milodex.risk.attribution.strategy_positions`) on the
        live/paper path — the same source the runner trusts — so a
        sibling's offsetting position that nets the broker flat (ADR 0055)
        cannot hide this strategy's lot and fail the cap open. The backtest
        path (ADR 0030 ``is_backtest``) keeps the broker-net +
        :func:`milodex.risk.attribution.attribute_position` reconstruction.

        Reads ``context.expected_max_positions`` directly per ADR 0029
        Decision 6 — the per-strategy cap is an independent ceiling that
        neither clamps nor is clamped by the global account cap. The
        account-scoped check (:meth:`_check_concurrent_positions`) enforces
        ``risk_defaults.max_concurrent_positions`` on its own (ADR 0024).

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

        # The owned set — symbols this strategy currently holds, with quantities.
        #
        # Live/paper: read the strategy's OWN submitted-fill ledger
        # (``strategy_positions``), NOT broker-net ``context.positions``. When a
        # sibling's offsetting position nets the broker flat for a symbol this
        # strategy holds (ADR 0055: rsi2 +13 / vwap_trend −13 → account flat), a
        # broker-net enumeration never sees the symbol and the cap undercounts —
        # failing OPEN. The ledger is the same source the runner already trusts
        # (``runner.py`` ``_current_positions`` → ``strategy_positions``), so the
        # cap and the runner now agree on what the strategy holds.
        #
        # Backtest (ADR 0030 ``is_backtest``): a single-strategy replay has no
        # sibling netting, and ``strategy_positions`` is paper-source-only, so
        # keep the broker-net + ``attribute_position(source="backtest")`` path —
        # its positions were opened by source='backtest' fills (R-P0-1).
        #
        # Lazy imports keep this module importable without circular issues.
        if context.is_backtest:
            from milodex.risk.attribution import attribute_position

            owned_quantities = {
                position.symbol.upper(): position.quantity
                for position in context.positions
                if position.quantity > 0
                and attribute_position(
                    symbol=position.symbol.upper(),
                    event_store=context.event_store,
                    source="backtest",
                )
                == proposing_strategy
            }
        else:
            from milodex.risk.attribution import strategy_positions

            owned_quantities = strategy_positions(proposing_strategy, context.event_store)

        owned_symbols = set(owned_quantities)
        symbol = context.intent.normalized_symbol()
        projected_count = len(owned_symbols)
        if context.intent.side == OrderSide.BUY:
            # An entry adds a slot only if the strategy doesn't already hold the
            # symbol (mirrors _check_concurrent_positions). Buying more of a
            # held symbol adds zero slots.
            if symbol not in owned_symbols:
                projected_count += 1
        elif context.intent.side == OrderSide.SELL and symbol in owned_symbols:
            # A full exit of the strategy's own lot frees its slot. Sized against
            # the strategy's ledger quantity, not broker-net.
            if context.intent.quantity >= owned_quantities[symbol]:
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
        """Per-strategy duplicate-order veto (RISK_POLICY "Duplicate-Order Policy").

        A duplicate is the *same strategy's* recent same-side order on the same
        symbol. The broker ``recent_orders`` fetch is account-scoped and carries
        no strategy tag (Alpaca has no strategy concept; ``client_order_id`` is a
        uuid), so it cannot be scoped per-strategy — and an account-wide match
        would false-veto a *different* strategy's legitimate same-symbol entry
        under same-symbol co-run (the bug the launch guard used to mask;
        concurrent-intraday PR5). The durable event-store history is the
        authoritative, untruncated, strategy-attributed source: every Milodex
        submit writes an ``execution_attempts`` row BEFORE the broker call and a
        ``trades`` row after, so a strategy's own in-flight order is always
        durably visible — the broker path adds nothing for this strategy's own
        orders, only the cross-strategy false-veto. We therefore rely solely on
        the durable query, scoped to the proposing strategy (``None`` = operator,
        which scopes to operator-attributed rows).

        No event store (legacy / non-service callers) → skip, consistent with
        the per-strategy cap. If the query errors, FAIL CLOSED: a dedup veto that
        cannot verify must block, not silently allow.
        """
        if context.event_store is None:
            return RiskCheckResult(
                "duplicate_order",
                True,
                "No event store available; skipping duplicate-order check.",
            )
        window = timedelta(seconds=context.risk_defaults.duplicate_order_window_seconds)
        now = datetime.now(tz=UTC)
        try:
            durable_matches = context.event_store.count_recent_submitted_orders(
                symbol=context.intent.normalized_symbol(),
                side=context.intent.side.value,
                since=now - window,
                strategy_name=context.request.strategy_name,
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
                    "Recent matching order for this strategy found in durable "
                    "history within duplicate-order window."
                ),
                reason_code="duplicate_order_window",
            )
        return RiskCheckResult("duplicate_order", True, "No duplicate orders detected.")

    def _check_opposite_side_order(self, context: EvaluationContext) -> RiskCheckResult:
        """Decline an intent when an OPEN order on the same symbol rests on the
        opposite side (concurrent-intraday plan, invariant 2).

        Once many strategies share one Alpaca account+symbol, one can submit a
        BUY while a sibling's SELL still rests (or vice versa). Alpaca rejects
        that as a wash trade (``40310000``). That broker reject is survivable —
        caught in the execution service, recorded REJECTED, no crash — but it is
        audit noise. The risk layer declines it first (risk disposes), keyed off
        the **account-scoped order book** (``context.recent_orders``, already
        fetched for the duplicate-order veto — no new broker call). The order
        book is the only source that can see a *resting* (unfilled) order: the
        per-strategy ``trades`` ledger folds filled/submitted lots and carries no
        order-level attribution.

        Only OPEN orders rest (``Order.is_open`` → PENDING / PARTIALLY_FILLED).
        A FILLED order is already a position; a CANCELLED / REJECTED one is gone.

        The veto is symmetric: it can transiently decline an exit SELL while a
        sibling's BUY rests (or vice versa). In the Phase-1 market-only regime
        (ADR 0013) a resting order is momentary — the next runner cycle
        re-fetches ``recent_orders`` and the exit clears — so no strategy is
        trapped. If a future phase admits resting limit orders, this could delay
        an exit until the contra order resolves; the fix then is an
        exposure-reducing exemption on the *incoming* side (mirroring
        ``_check_reconciliation_readiness``). ponytail: market-only ceiling.
        """
        symbol = context.intent.normalized_symbol()
        side = context.intent.side
        for order in context.recent_orders:
            if order.is_open and order.symbol.upper() == symbol and order.side != side:
                return RiskCheckResult(
                    name="opposite_side_order",
                    passed=False,
                    message=(
                        f"An open {order.side.value} order on {symbol} rests while a "
                        f"{side.value} order is being evaluated; submitting it would trip a "
                        "wash-trade reject (40310000)."
                    ),
                    reason_code="opposite_side_order_open",
                )
        return RiskCheckResult(
            "opposite_side_order",
            True,
            "No opposite-side resting order on this symbol.",
        )

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
        # Mirror _check_total_exposure: a naked/over-held short adds its excess
        # leg rather than netting the whole order off the held value (A-6).
        increasing = exposure_increasing_notional(
            context.intent, context.request, context.positions
        )
        reducing = delta - increasing
        return max(0.0, current - reducing) + increasing


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"
