"""Promotion lifecycle surface — frozen manifests and (future) state transitions.

This slice ships the frozen-manifest half of ADR 0015: a snapshot of the
strategy YAML at its current stage, plus a helper for the risk layer to read
back the active hash. The promotion state machine and evidence-package
assembly arrive in slice 2.
"""

from milodex.promotion.manifest import (
    freeze_manifest,
    get_active_manifest_hash,
    resolve_strategy_config_path,
)

__all__ = [
    "freeze_manifest",
    "get_active_manifest_hash",
    "resolve_strategy_config_path",
]
