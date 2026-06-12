"""Configuration loaders for execution services.

Global risk guardrails (``RiskDefaults`` + ``load_risk_defaults``) live
in :mod:`milodex.risk.config` per ADR 0019. This module holds only
execution-layer config: per-strategy execution caps.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class StrategyExecutionConfig:
    """Execution-relevant strategy settings.

    ``family`` and ``disable_conditions_additional`` feed the risk layer's
    disable-condition halt (SRS R-STR-014,
    :mod:`milodex.risk.disable_conditions`). They default leniently (empty)
    because this loader is intentionally more permissive than the strict
    strategy loader — legacy fixtures and manual-trade paths may omit them.
    An empty family resolves to the universal disable-condition subset.
    """

    name: str
    enabled: bool
    stage: str
    max_position_pct: float
    max_positions: int
    daily_loss_cap_pct: float
    path: Path
    family: str = ""
    disable_conditions_additional: tuple[str, ...] = ()


def load_strategy_execution_config(path: Path) -> StrategyExecutionConfig:
    """Load execution-relevant strategy settings from YAML."""
    data = _load_yaml(path)
    strategy = _mapping(data.get("strategy"), "strategy", path)
    risk = _mapping(strategy.get("risk"), "strategy.risk", path)

    raw_additional = strategy.get("disable_conditions_additional")
    additional: tuple[str, ...] = ()
    if isinstance(raw_additional, list):
        additional = tuple(
            item.strip() for item in raw_additional if isinstance(item, str) and item.strip()
        )

    return StrategyExecutionConfig(
        name=str(strategy.get("name") or strategy.get("id")),
        enabled=bool(strategy["enabled"]),
        stage=str(strategy["stage"]),
        max_position_pct=float(risk["max_position_pct"]),
        max_positions=int(risk["max_positions"]),
        daily_loss_cap_pct=float(risk["daily_loss_cap_pct"]),
        path=path,
        family=str(strategy.get("family") or ""),
        disable_conditions_additional=additional,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        msg = f"Config file does not exist: {path}"
        raise ValueError(msg)

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if not isinstance(data, dict):
        msg = f"Config root must be a mapping: {path}"
        raise ValueError(msg)
    return data


def _mapping(value: Any, label: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{path}: {label} must be a mapping"
        raise ValueError(msg)
    return value
