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
    promoted_at: str = ""
    promotion_type: str = ""
    gate_failures: tuple[str, ...] = ()
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
            "evidenceRunId": self.evidence_run_id,
            "promotedAt": self.promoted_at,
            "promotionType": self.promotion_type,
            "gateFailures": list(self.gate_failures),
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
