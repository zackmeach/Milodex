"""Canonical disable-condition catalog and evaluators (SRS R-STR-014).

``docs/strategy-families.md`` is the normative prose source for each
family's default disable conditions (R-STR-015). This module is the
executable mirror of that catalog: stable condition ids, family
ownership, and — where the risk-evaluation context already carries the
required signal — an evaluator callable that decides whether the
condition is currently active.

Placement rationale (why ``risk/`` and not ``strategies/``): the
R-STR-014 halt promise is risk-layer enforcement — "the risk layer
shall halt the strategy when any active condition in that catalog is
true". Per ``docs/FOUNDER_INTENT.md`` ("The Risk Layer — Operator
Preferences, System Enforcement") no strategy code may own or shape the
policy that halts it, so the callable-bearing registry must not live in
``strategies/`` where strategy modules could import and reason about
it. The prose catalog remains family metadata in the docs; this module
is its enforcement-side mirror, consumed only by
:class:`milodex.risk.evaluator.RiskEvaluator`. Import-wise this is also
the clean direction: ``risk/`` already sits above ``execution/`` (ADR
0019) and needs nothing from ``strategies/``; placing the catalog here
adds zero new edges to the import graph (the only reference to the
evaluation context is ``TYPE_CHECKING``).

Evaluability triage (honest subset — no invented data feeds, no broker
or network calls from evaluators):

==============================  =================  =========================
Condition id                    Auto-evaluated?    Why
==============================  =================  =========================
data_quality_issue              yes                ``latest_bar`` missing or
                                                   older than
                                                   ``max_data_staleness_seconds``
                                                   is a computable
                                                   data-quality failure.
drawdown_risk_budget_breach     yes                daily loss vs the kill-
                                                   switch drawdown threshold
                                                   and the effective daily
                                                   loss cap is computable
                                                   from ``account`` +
                                                   ``risk_defaults``.
operator_declared_pause         yes                the kill switch is the
                                                   operator's declared-pause
                                                   surface today;
                                                   ``kill_switch_state`` is
                                                   in the context.
abnormal_market_regime          declared-only      no regime/volatility
                                                   bounds signal in the
                                                   context.
spread_liquidity_deterioration  declared-only      no quote/spread data in
                                                   the context (bars only).
corporate_action_uncertainty    declared-only      no corporate-action feed.
broker_execution_instability    declared-only      no broker health/error
                                                   history in the context.
fill_divergence                 declared-only      the reconciliation gate
                                                   already enforces the
                                                   computable proxy
                                                   (ledger-vs-broker
                                                   divergence) and
                                                   deliberately exempts
                                                   exposure-reducing exits;
                                                   re-evaluating it here
                                                   would veto exits and
                                                   contradict that doctrine.
exchange_calendar_uncertainty   declared-only      no calendar-anomaly
                                                   signal in the context.
==============================  =================  =========================

The three auto-evaluated conditions intentionally mirror existing risk
checks (``data_staleness``, ``daily_loss``, ``kill_switch``) — they
co-fire rather than replace them, so the disable-condition halt can
never be *looser* than the existing checks and a strategy whose family
catalog includes them gains an explicit R-STR-014 audit trail entry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from milodex.risk.staleness import staleness_verdict

if TYPE_CHECKING:
    from milodex.risk.evaluator import EvaluationContext

#: Sentinel family meaning "owned by every strategy family". Every family
#: documented in ``docs/strategy-families.md`` includes the three universal
#: conditions (data quality, broker/execution instability, operator pause) —
#: the regime family's section explicitly notes its catalog is "intentionally
#: short" *because* it omits the others. Families not documented in
#: strategy-families.md (e.g. ``benchmark``, ``scored``, ``tree``) therefore
#: default to exactly the universal set: applying the documented baseline
#: without overclaiming family-specific conditions the doc never assigned.
ALL_FAMILIES = "all"


@dataclass(frozen=True)
class ConditionEvaluation:
    """Outcome of evaluating one auto-evaluable disable condition."""

    active: bool
    detail: str


@dataclass(frozen=True)
class DisableCondition:
    """One catalog entry.

    ``evaluator`` is either a callable over the risk evaluation context
    returning a :class:`ConditionEvaluation`, or ``None`` meaning the
    condition is declared (visible, non-removable) but not auto-evaluable
    from the data the risk context carries today. Declared-only conditions
    never veto.
    """

    condition_id: str
    description: str
    families: tuple[str, ...]
    evaluator: Callable[[EvaluationContext], ConditionEvaluation] | None


def _evaluate_data_quality(context: EvaluationContext) -> ConditionEvaluation:
    """Active when the latest bar is missing or stale.

    Delegates the fresh-vs-stale decision to the single
    :func:`milodex.risk.staleness.staleness_verdict` policy that
    ``RiskEvaluator._check_data_staleness`` also consults, so the veto and
    this disable condition cannot diverge by construction (the session-aware
    1D rule and the 300s non-1D budget both live in one place). ``now`` is
    read from this module's ``datetime`` (kept independently monkeypatchable).
    """
    verdict = staleness_verdict(context, datetime.now(tz=UTC))
    return ConditionEvaluation(active=verdict.is_stale, detail=verdict.detail)


def _evaluate_drawdown_breach(context: EvaluationContext) -> ConditionEvaluation:
    """Active when the daily loss breaches the kill-switch drawdown threshold
    or the effective daily loss cap.

    Mirrors the math of ``RiskEvaluator._check_daily_loss`` including the
    runner-bound cap preference (``expected_daily_loss_cap_pct``) so the two
    verdicts can never diverge.
    """
    equity_base = max(context.account.portfolio_value - context.account.daily_pnl, 1.0)
    current_loss_pct = max(0.0, -context.account.daily_pnl / equity_base)
    defaults = context.risk_defaults
    if defaults.kill_switch_enabled and current_loss_pct > defaults.kill_switch_max_drawdown_pct:
        return ConditionEvaluation(
            active=True,
            detail=(
                f"daily loss {current_loss_pct:.2%} breaches kill-switch drawdown "
                f"threshold {defaults.kill_switch_max_drawdown_pct:.2%}"
            ),
        )
    effective_cap = defaults.max_daily_loss_pct
    if context.strategy_config is not None:
        per_strategy = (
            context.expected_daily_loss_cap_pct
            if context.expected_daily_loss_cap_pct is not None
            else context.strategy_config.daily_loss_cap_pct
        )
        effective_cap = min(effective_cap, per_strategy)
    if current_loss_pct > effective_cap:
        return ConditionEvaluation(
            active=True,
            detail=(
                f"daily loss {current_loss_pct:.2%} breaches daily loss cap {effective_cap:.2%}"
            ),
        )
    return ConditionEvaluation(active=False, detail="daily loss within risk budget")


def _evaluate_operator_pause(context: EvaluationContext) -> ConditionEvaluation:
    """Active when the kill switch is active.

    The kill switch is the operator's declared-pause mechanism today —
    manual reset required, no auto-resume. If a softer operator-pause
    surface ever ships, this evaluator widens to include it.
    """
    if context.kill_switch_state.active:
        return ConditionEvaluation(
            active=True,
            detail="kill switch is active (operator-declared halt; manual reset required)",
        )
    return ConditionEvaluation(active=False, detail="no operator-declared pause in force")


#: Families documented with the full eight-condition list in
#: ``docs/strategy-families.md``.
_EIGHT_CONDITION_FAMILIES = ("meanrev", "momentum", "breakout")

#: The canonical catalog. Descriptions are verbatim from
#: ``docs/strategy-families.md`` — the completeness test pins this.
CATALOG: tuple[DisableCondition, ...] = (
    DisableCondition(
        condition_id="abnormal_market_regime",
        description="Abnormal market regime or volatility far outside tested bounds",
        families=_EIGHT_CONDITION_FAMILIES,
        evaluator=None,
    ),
    DisableCondition(
        condition_id="spread_liquidity_deterioration",
        description="Significant spread expansion or liquidity deterioration",
        families=_EIGHT_CONDITION_FAMILIES,
        evaluator=None,
    ),
    DisableCondition(
        condition_id="data_quality_issue",
        description="Major unresolved data-quality issues",
        families=(ALL_FAMILIES,),
        evaluator=_evaluate_data_quality,
    ),
    DisableCondition(
        condition_id="corporate_action_uncertainty",
        description="Corporate-action handling uncertainty",
        families=_EIGHT_CONDITION_FAMILIES,
        evaluator=None,
    ),
    DisableCondition(
        condition_id="broker_execution_instability",
        description="Broker / execution instability",
        families=(ALL_FAMILIES,),
        evaluator=None,
    ),
    DisableCondition(
        condition_id="fill_divergence",
        description="Repeated unexplained divergence between expected and actual fills",
        families=_EIGHT_CONDITION_FAMILIES,
        evaluator=None,
    ),
    DisableCondition(
        condition_id="drawdown_risk_budget_breach",
        description="Breach of drawdown or risk-budget limits",
        families=_EIGHT_CONDITION_FAMILIES,
        evaluator=_evaluate_drawdown_breach,
    ),
    DisableCondition(
        condition_id="operator_declared_pause",
        description="Operator-declared pause after unusual market events",
        families=(ALL_FAMILIES,),
        evaluator=_evaluate_operator_pause,
    ),
    DisableCondition(
        condition_id="exchange_calendar_uncertainty",
        description="Exchange-calendar uncertainty around holidays or unscheduled closures",
        families=("seasonality",),
        evaluator=None,
    ),
)

_CATALOG_BY_ID: dict[str, DisableCondition] = {
    condition.condition_id: condition for condition in CATALOG
}


def default_conditions_for_family(family: str) -> tuple[DisableCondition, ...]:
    """Return the family's default disable conditions in catalog order.

    Unknown families (including the empty string for legacy callers that
    carry no family) receive the universal ``ALL_FAMILIES``-owned subset —
    the documented baseline shared by every family in
    ``docs/strategy-families.md``.
    """
    return tuple(
        condition
        for condition in CATALOG
        if ALL_FAMILIES in condition.families or family in condition.families
    )


def effective_disable_conditions(
    family: str,
    additional: Iterable[str],
) -> tuple[DisableCondition, ...]:
    """Family defaults plus config ``disable_conditions_additional`` strings.

    Additional strings that match a catalog ``condition_id`` resolve to the
    catalog entry (and may therefore auto-evaluate). Unknown strings remain
    free-form declared-only entries — the loader has always accepted
    free-form prose here and existing configs rely on it.

    Removal of family defaults is structurally inexpressible: this function
    only ever ADDS to the family default set, and the YAML schema has no
    subtraction key (R-STR-014: the catalog "may be extended — not
    reduced"). A test pins this.
    """
    effective: list[DisableCondition] = list(default_conditions_for_family(family))
    seen = {condition.condition_id for condition in effective}
    for raw in additional:
        text = raw.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        resolved = _CATALOG_BY_ID.get(text)
        if resolved is not None:
            effective.append(resolved)
        else:
            effective.append(
                DisableCondition(
                    condition_id=text,
                    description=text,
                    families=(),
                    evaluator=None,
                )
            )
    return tuple(effective)
