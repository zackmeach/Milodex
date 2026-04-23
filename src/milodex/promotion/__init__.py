"""Promotion lifecycle surface — frozen manifests and stage-transition governance.

Slice 1 shipped the frozen-manifest half of ADR 0015: a snapshot of the strategy
YAML at its current stage plus a helper for the risk layer to read back the
active hash.

Slice 2 adds the state-machine surface (stage-transition validation + gate
checks) that was previously under ``milodex.strategies.promotion``. The evidence
package and transactional ``transition()`` helper land in subsequent commits.
"""

from milodex.promotion.evidence import EvidencePackage, assemble_evidence_package
from milodex.promotion.manifest import (
    freeze_manifest,
    get_active_manifest_hash,
    resolve_strategy_config_path,
)
from milodex.promotion.state_machine import (
    MAX_DRAWDOWN_PCT,
    MIN_SHARPE,
    MIN_TRADES,
    STAGE_ORDER,
    PromotionCheckResult,
    check_gate,
    validate_stage_transition,
)

__all__ = [
    "MAX_DRAWDOWN_PCT",
    "MIN_SHARPE",
    "MIN_TRADES",
    "STAGE_ORDER",
    "EvidencePackage",
    "PromotionCheckResult",
    "assemble_evidence_package",
    "check_gate",
    "freeze_manifest",
    "get_active_manifest_hash",
    "resolve_strategy_config_path",
    "validate_stage_transition",
]
