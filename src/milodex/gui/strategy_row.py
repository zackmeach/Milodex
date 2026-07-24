"""The shared ``_StrategyRow`` contract for GUI read models.

``_StrategyRow`` is the immutable per-strategy projection that every bench /
kanban / front-page snapshot builder assembles and that ``as_qml()`` serialises
into the QML payload.  ``as_qml()`` is the one runtime edge into
``bench_actions`` (it calls ``_compute_bench_action_menu`` and
``_evidence_packet``); that import is performed lazily inside the method so the
top-level module graph stays a one-way DAG (strategy_row → bench_actions).

Extracted verbatim from ``read_models.py`` (PR12 decompose). No behavior
changed — definitions were moved, not rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from milodex.gui.bench_v1 import EvidenceRecord, Stage

# Provenance stamp for the raw sharpe/maxDrawdownPct/tradeCount fields the Bench
# ladder renders directly (BenchSurface.qml formattedSharpe/formattedMaxDD/
# formattedTrades). These are the same read-model-snapshot values carried in
# evidencePacket.metrics — this constant just makes the non-authoritative,
# not-reconstructed provenance visible next to the ladder metrics themselves,
# not only inside the per-row Evidence dossier. See bench_actions._evidence_packet
# for the packet-level contract this mirrors (D-8 deferral / M2 item c).
METRICS_PROVENANCE = "read-model snapshot — not reconstructed"


def classify_archetype(
    family: str,
    stage: str,
    promotion_type: str | None,
    gate_failures: list[str],
) -> str:
    """Classify a strategy row into one of five operator-facing archetypes.

    Returns exactly one of: ``"canary" | "baseline" | "paper" | "blocked" |
    "research"``. These make the BENCH surface's roster visibly distinct
    (roadmap M2 outcome): canaries, baselines, research (deciders), blocked,
    and promoted paper strategies each read as their own kind.

    "Decision-only" is deliberately NOT an archetype (D-1 resolved: daily
    strategies execute — the decider paradigm is a *how*, not a lifecycle
    class), so decider families (``scored`` / ``tree``) fold into ``research``.

    Priority-ordered — FIRST MATCH WINS. The order is load-bearing because
    the archetypes overlap (e.g. one intraday canary is benchmark-family, and
    the deciders sit at backtest with failing gates). Roster semantics per
    docs/STRATEGY_BANK.md:

    1. ``promotion_type == "lifecycle_exempt"`` AND ``family != "regime"``
       → ``"canary"``. The five intraday SPY harness canaries were promoted
       lifecycle-exempt pre-ADR-0058; post-0058 the flag is scoped to the
       regime strategy, so a non-regime lifecycle-exempt promotion is durably
       the canary signature. Latent accepted edge: a demoted canary would
       still read ``canary`` via its retained lifecycle_exempt promotion row
       (``_latest_promotions`` skips demotion rows) — accepted; no roster
       member is in this state.
    2. ``family == "benchmark"`` → ``"baseline"`` (no_trade /
       time_of_day_null / unconditional_intraday_long / random_matched_exposure
       null templates).
    3. ``stage in ("paper", "micro_live", "live")`` → ``"paper"`` (promoted
       edge; includes the regime lifecycle-proof strategy, which is exempt
       from rule 1 above and lands here).
    4. ``family in ("scored", "tree")`` → ``"research"`` (never-promote
       seam-proof deciders; they carry failing/NULL-WF backtest gates but must
       NOT read as ``blocked`` — docs/STRATEGY_BANK.md "Decision-layer
       seam-proof deciders". This rule MUST precede rule 5.).
    5. ``stage == "backtest"`` AND ``gate_failures`` non-empty → ``"blocked"``.
       The builder passes evidence-grounded failures only: a never-evaluated
       row (all metrics None) feeds ``[]`` here and falls through to rule 6,
       matching ``_status_copy``'s "Config valid — awaiting evidence" read.
    6. else → ``"research"``.
    """
    if promotion_type == "lifecycle_exempt" and family != "regime":
        return "canary"
    if family == "benchmark":
        return "baseline"
    if stage in ("paper", "micro_live", "live"):
        return "paper"
    if family in ("scored", "tree"):
        return "research"
    if stage == "backtest" and gate_failures:
        return "blocked"
    return "research"


@dataclass(frozen=True)
class _StrategyRow:
    strategy_id: str
    name: str
    display_name_source: str
    stage: str
    description: str
    config_path: str
    family: str
    template: str
    enabled: bool
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None
    evidence_run_id: str = ""
    # Backtest run's ``started_at`` (ISO-8601) for the current evidence — the
    # same value assemble_evidence_package derives as ``backtest_run_started_at``
    # (promotion/evidence.py). Read-model display only; consumed by
    # bench_actions._action_intent_preview to render the D-8 Promote-to-Paper
    # evidence-age + freshness caveat. Empty string when unavailable.
    backtest_run_started_at: str = ""
    promoted_at: str = ""
    promotion_type: str = ""
    # Manifest-frozen-but-promotion-unrecorded state (honest display, not a
    # stage claim): True when the YAML claims a promoted stage, an active
    # frozen manifest exists AT that stage (so the risk layer's
    # no_frozen_manifest veto is cleared and the strategy is runnable), but
    # the promotion ledger holds no promotion row. Section placement and the
    # group rollup still key on promotion records — the row stays clamped to
    # backtest; these fields only make the in-between state visible.
    frozen_unrecorded: bool = False
    frozen_stage: str = ""
    gate_failures: tuple[str, ...] = ()
    archetype: str = ""
    status_kind: str = "info"
    status_word: str = "Configured"
    status_tail: str = "ready for evidence review."
    meta_line: str = ""
    meta_config_key: str = ""
    meta_stage: str = ""
    meta_evidence_label: str = ""
    meta_evidence_at: str = ""
    session_state: str = "not_running"
    session_id: str = ""
    session_detail: str = ""
    paper_evidence: dict[str, Any] = field(default_factory=dict)
    job_id: str = ""
    job_status: str = ""
    job_action_type: str = ""
    job_detail: str = ""
    visual_priority: int = 0
    # Bench v1 read-model schema (ADR 0050). Populated empty by default;
    # PR G will wire the menu computation and PR E will populate fixtures.
    # Not exposed in `as_qml()` yet — that wiring is PR G's scope.
    evidence_by_stage: dict[Stage, EvidenceRecord] = field(default_factory=dict)
    runs_in_flight: dict[Stage, bool] = field(default_factory=dict)

    def as_qml(self) -> dict[str, Any]:
        from milodex.gui.bench_actions import _compute_bench_action_menu, _evidence_packet

        return {
            "strategyId": self.strategy_id,
            "name": self.name,
            "displayName": self.name,
            "displayNameSource": self.display_name_source,
            "stage": self.stage,
            "description": self.description,
            "configPath": self.config_path,
            "family": self.family,
            "template": self.template,
            "enabled": self.enabled,
            "sharpe": self.sharpe,
            "maxDrawdownPct": self.max_drawdown_pct,
            "tradeCount": self.trade_count or 0,
            "metricsProvenance": METRICS_PROVENANCE,
            "evidenceRunId": self.evidence_run_id,
            "promotedAt": self.promoted_at,
            "promotionType": self.promotion_type,
            "frozenUnrecorded": self.frozen_unrecorded,
            "frozenStage": self.frozen_stage,
            "gateFailures": list(self.gate_failures),
            "archetype": self.archetype,
            "statusKind": self.status_kind,
            "statusWord": self.status_word,
            "statusTail": self.status_tail,
            "metaLine": self.meta_line,
            "metaConfigKey": self.meta_config_key,
            "metaStage": self.meta_stage,
            "metaEvidenceLabel": self.meta_evidence_label,
            "metaEvidenceAt": self.meta_evidence_at,
            "sessionState": self.session_state,
            "sessionId": self.session_id,
            "sessionDetail": self.session_detail,
            "paperEvidence": dict(self.paper_evidence),
            "jobId": self.job_id,
            "jobStatus": self.job_status,
            "jobActionType": self.job_action_type,
            "jobDetail": self.job_detail,
            "visualPriority": self.visual_priority,
            "actions": _compute_bench_action_menu(self),
            "evidencePacket": _evidence_packet(self),
        }
