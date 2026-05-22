"""Promotion lifecycle surface — frozen manifests and stage-transition governance.

Slice 1 shipped the frozen-manifest half of ADR 0015: a snapshot of the strategy
YAML at its current stage plus a helper for the risk layer to read back the
active hash.

Slice 2 adds the state-machine surface (stage-transition validation + gate
checks) that was previously under ``milodex.strategies.promotion``. The evidence
package and transactional ``transition()`` helper land in subsequent commits.

Public submodules:
- :mod:`milodex.promotion.run_evidence` — helpers for resolving backtest metrics
  and computing post-stage-update manifest hashes (shared by CLI and facade).
- :mod:`milodex.promotion.stage_compat` — authoritative table mapping trading
  modes to eligible promotion stages (shared by CLI and facade).
"""

from milodex.promotion.evidence import EvidencePackage, assemble_evidence_package
from milodex.promotion.manifest import (
    freeze_manifest,
    get_active_manifest_hash,
    resolve_strategy_config_path,
)
from milodex.promotion.orchestrator import (
    REASON_GATE_FAILED,
    REASON_INVALID_STAGE_TRANSITION,
    REASON_MISSING_BACKTEST_RUN,
    PromoteBlocked,
    PromoteError,
    PromoteRequest,
    PromoteResult,
    PromoteSuccess,
    prepare_and_record_promotion,
)
from milodex.promotion.run_evidence import compute_post_update_hash, metrics_from_run
from milodex.promotion.stage_compat import ALLOWED_STAGES_BY_MODE, RECOGNIZED_MODES
from milodex.promotion.state_machine import (
    MAX_DRAWDOWN_PCT,
    MIN_SHARPE,
    MIN_TRADES,
    PAPER_MAX_DRAWDOWN_PCT,
    PAPER_MIN_SHARPE,
    STAGE_ORDER,
    PromotionCheckResult,
    check_gate,
    validate_stage_transition,
)

__all__ = [
    "ALLOWED_STAGES_BY_MODE",
    "MAX_DRAWDOWN_PCT",
    "MIN_SHARPE",
    "MIN_TRADES",
    "PAPER_MAX_DRAWDOWN_PCT",
    "PAPER_MIN_SHARPE",
    "REASON_GATE_FAILED",
    "REASON_INVALID_STAGE_TRANSITION",
    "REASON_MISSING_BACKTEST_RUN",
    "RECOGNIZED_MODES",
    "STAGE_ORDER",
    "EvidencePackage",
    "PromoteBlocked",
    "PromoteError",
    "PromoteRequest",
    "PromoteResult",
    "PromoteSuccess",
    "PromotionCheckResult",
    "assemble_evidence_package",
    "check_gate",
    "compute_post_update_hash",
    "freeze_manifest",
    "get_active_manifest_hash",
    "metrics_from_run",
    "prepare_and_record_promotion",
    "resolve_strategy_config_path",
    "validate_stage_transition",
]
