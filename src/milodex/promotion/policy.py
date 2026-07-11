"""Typed promotion-policy source of truth.

This module is the single authority for promotion *gate-decision* policy:
the research-target statistical tiers and the lifecycle-proof operational
gate *definition*. Structural transition legality (stage order, no-skip,
Phase-1 live-lock) and transition mechanics remain in
``milodex.promotion.state_machine`` and are deliberately NOT owned here.

Governance, not configuration. These values change only by a deliberate ADR
(see ADR 0052), never by config edit, strategy, model, or agent.
``ACTIVE_PROMOTION_POLICY`` is the single named current policy; it is NOT
runtime-selectable. Profile selection is deferred future work.

Reference: SRS R-PRM-004; ADR 0052.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STAGE_PAPER = "paper"
CAPITAL_STAGES: frozenset[str] = frozenset({"micro_live", "live"})


def _fmt_or_none(value: float | int | None) -> str:
    return "None" if value is None else str(value)


@dataclass(frozen=True)
class PromotionCheckResult:
    """Gate check outcome for a single promotion request.

    Public shape preserved verbatim from the pre-consolidation
    ``state_machine.PromotionCheckResult``; re-exported there for import
    stability.
    """

    allowed: bool
    promotion_type: str
    failures: list[str] = field(default_factory=list)
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None


@dataclass(frozen=True)
class GateThresholds:
    """One statistical tier's numeric thresholds."""

    min_sharpe: float
    max_drawdown_pct: float


# Freshness bound for the operational lifecycle criteria (ADR 0058 M4
# enforcement addendum). Criteria (a) "a successful deterministic backtest run"
# and (c) "the risk layer having rejected at least one synthetic fault-injection
# trade" are only admissible as evidence when they are recent: the most recent
# successful backtest run, and the most recent synthetic fault-injection veto,
# must each fall within this many days of the promotion attempt. The regime
# strategy trades on a slow (200-DMA) cadence and is re-promoted rarely, so a
# generous-but-bounded window keeps a years-old artifact from silently standing
# in as fresh evidence (the "unbounded staleness" trap ADR 0058 named for
# criterion (a)) without forcing a re-run on every routine re-promotion.
# FOUNDER-REVIEW: 90 days is a first-pass value chosen for a low-cadence regime
# strategy; revisit once real re-promotion frequency is observed.
LIFECYCLE_EVIDENCE_MAX_AGE_DAYS = 90


@dataclass(frozen=True)
class LifecycleGateDefinition:
    """Typed model of the SRS R-PRM-004 lifecycle-proof gate.

    ``applies_to`` is the typed identity source-of-truth for "which strategy
    ids are lifecycle-proof" (ADR 0058). The orchestrator scopes the
    ``lifecycle_exempt`` promotion path to exactly these ids; a lifecycle-exempt
    request for any other id is refused (fail closed), and a general operator
    override is offered as a separate, loudly-recorded mechanism.

    ``enforced`` is True as of the ADR 0058 M4 enforcement addendum: a
    lifecycle-exempt promotion is admitted only when all three operational
    criteria (a)/(b)/(c) pass, evaluated against the event store and fail-closed
    (see :mod:`milodex.promotion.lifecycle_criteria`). ``check_gate`` is
    UNCHANGED — its ``lifecycle_exempt`` short-circuit still returns
    ``allowed=True``; the criteria enforcement lives entirely in the orchestrator
    seam (ADR 0058: state_machine and the risk layer are not touched).

    ``evidence_max_age_days`` is the freshness bound applied to criteria (a) and
    (c) (see :data:`LIFECYCLE_EVIDENCE_MAX_AGE_DAYS`).
    """

    criteria: tuple[str, ...]
    description: str
    applies_to: tuple[str, ...] = ()
    enforced: bool = False
    evidence_max_age_days: int = LIFECYCLE_EVIDENCE_MAX_AGE_DAYS


@dataclass(frozen=True)
class PromotionPolicy:
    """The typed gate-decision policy. Frozen; one named instance per ADR."""

    name: str
    paper_gate: GateThresholds
    capital_gate: GateThresholds
    default_trade_floor: int
    lifecycle_gate: LifecycleGateDefinition

    def _thresholds_for_stage(self, target_stage: str) -> GateThresholds:
        if target_stage == STAGE_PAPER:
            return self.paper_gate
        if target_stage in CAPITAL_STAGES:
            return self.capital_gate
        # The valid-stage list is kept as a deliberate byte-identical literal
        # matching state_machine.STAGE_ORDER's f-string repr, so the error
        # text does not silently desync if STAGE_ORDER is ever reordered, and
        # so policy stays decoupled from state_machine. Pre-consolidation
        # check_gate produced this exact string.
        msg = (
            f"Unknown to_stage '{target_stage}'. "
            "Valid stages: ['backtest', 'paper', 'micro_live', 'live']."
        )
        raise ValueError(msg)

    def evaluate_research_target(
        self,
        *,
        sharpe_ratio: float | None,
        max_drawdown_pct: float | None,
        trade_count: int | None,
        target_stage: str,
        min_trade_count: int,
    ) -> PromotionCheckResult:
        """Statistical verdict for a research-target strategy.

        Comparison operators, failure-message strings, and ``promotion_type``
        are byte-for-byte identical to the pre-consolidation ``check_gate``.
        """
        tier = self._thresholds_for_stage(target_stage)
        failures: list[str] = []

        if sharpe_ratio is None or sharpe_ratio <= tier.min_sharpe:
            failures.append(
                f"Sharpe {_fmt_or_none(sharpe_ratio)} must be > {tier.min_sharpe} "
                f"(got {_fmt_or_none(sharpe_ratio)})"
            )
        if max_drawdown_pct is None or max_drawdown_pct >= tier.max_drawdown_pct:
            failures.append(
                f"Max drawdown {_fmt_or_none(max_drawdown_pct)}% must be < "
                f"{tier.max_drawdown_pct}% "
                f"(got {_fmt_or_none(max_drawdown_pct)})"
            )
        if trade_count is None or trade_count < min_trade_count:
            failures.append(
                f"Trade count must be >= {min_trade_count} (got {_fmt_or_none(trade_count)})"
            )

        return PromotionCheckResult(
            allowed=len(failures) == 0,
            promotion_type="statistical",
            failures=failures,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
        )


PHASE1_GOVERNANCE_V1 = PromotionPolicy(
    name="phase1_governance_v1",
    paper_gate=GateThresholds(min_sharpe=0.0, max_drawdown_pct=25.0),
    capital_gate=GateThresholds(min_sharpe=0.5, max_drawdown_pct=15.0),
    default_trade_floor=30,
    lifecycle_gate=LifecycleGateDefinition(
        criteria=(
            "a successful deterministic backtest run",
            "explanation records (R-XC-008) generated for every simulated signal",
            "the risk layer having rejected at least one synthetic fault-injection trade",
        ),
        description=(
            "SRS R-PRM-004 lifecycle-proof paper gate for the regime strategy. "
            "Enforced (ADR 0058 M4 addendum): a lifecycle-exempt promotion is "
            "admitted only when all three criteria pass against the event store "
            "(fail-closed). check_gate is unchanged — enforcement lives in the "
            "orchestrator seam (milodex.promotion.lifecycle_criteria)."
        ),
        applies_to=("regime.daily.sma200_rotation.spy_shy.v1",),
        enforced=True,
        evidence_max_age_days=LIFECYCLE_EVIDENCE_MAX_AGE_DAYS,
    ),
)

# The single named current governance policy. NOT runtime-selectable (ADR 0052).
ACTIVE_PROMOTION_POLICY = PHASE1_GOVERNANCE_V1
