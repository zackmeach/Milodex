"""Freeze a strategy's YAML config at its current stage and read it back.

See ADR 0015 and the Phase 1.4 slice-1 plan for rationale. The freeze path
produces an immutable :class:`StrategyManifestEvent`; the read path is a
hash-only lookup used by the risk layer's drift check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from milodex.core.event_store import StrategyManifestEvent
from milodex.strategies.loader import (
    canonicalize_config_data,
    compute_config_hash,
    load_strategy_config,
)

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore

_FROZEN_STAGES = frozenset({"paper", "micro_live", "live"})


def freeze_manifest(
    config_path: Path,
    *,
    event_store: EventStore,
    frozen_by: str = "operator",
    now: datetime | None = None,
) -> StrategyManifestEvent:
    """Snapshot the strategy YAML at ``config_path`` into the event store.

    Reads the stage from the YAML (AD-8); refuses to freeze a ``backtest``-stage
    config because there is no promoted state to snapshot yet. Always appends
    a new row — never skips when the hash matches a prior freeze (AD-5).
    """
    config = load_strategy_config(config_path)
    if config.stage not in _FROZEN_STAGES:
        msg = (
            f"Cannot freeze strategy at stage '{config.stage}'. "
            f"Freezing is only valid for promoted stages ({', '.join(sorted(_FROZEN_STAGES))})."
        )
        raise ValueError(msg)

    canonical = canonicalize_config_data(config.raw_data)
    config_hash = compute_config_hash(config_path)

    event = StrategyManifestEvent(
        strategy_id=config.strategy_id,
        stage=config.stage,
        config_hash=config_hash,
        config_json=canonical,
        config_path=str(config_path),
        frozen_at=now or datetime.now(tz=UTC),
        frozen_by=frozen_by,
    )
    event_id = event_store.append_strategy_manifest(event)
    return StrategyManifestEvent(
        id=event_id,
        strategy_id=event.strategy_id,
        stage=event.stage,
        config_hash=event.config_hash,
        config_json=event.config_json,
        config_path=event.config_path,
        frozen_at=event.frozen_at,
        frozen_by=event.frozen_by,
    )


def get_active_manifest_hash(
    strategy_id: str,
    stage: str,
    event_store: EventStore,
) -> str | None:
    """Return the active frozen hash for ``(strategy_id, stage)`` or ``None``."""
    manifest = event_store.get_active_manifest_for_strategy(strategy_id, stage)
    return None if manifest is None else manifest.config_hash


def resolve_strategy_config_path(strategy_id: str, config_dir: Path = Path("configs")) -> Path:
    """Locate the YAML file whose ``strategy.id`` matches ``strategy_id``."""
    for path in sorted(config_dir.glob("*.yaml")):
        try:
            config = load_strategy_config(path)
        except (ValueError, yaml.YAMLError):
            continue
        if config.strategy_id == strategy_id:
            return path
    msg = f"Strategy config not found for strategy id: {strategy_id}"
    raise ValueError(msg)
