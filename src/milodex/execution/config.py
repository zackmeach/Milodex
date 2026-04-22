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
    """Execution-relevant strategy settings."""

    name: str
    enabled: bool
    stage: str
    max_position_pct: float
    max_positions: int
    daily_loss_cap_pct: float
    stop_loss_pct: float | None
    path: Path


def load_strategy_execution_config(path: Path) -> StrategyExecutionConfig:
    """Load execution-relevant strategy settings from YAML."""
    data = _load_yaml(path)
    strategy = _mapping(data.get("strategy"), "strategy", path)
    risk = _mapping(strategy.get("risk"), "strategy.risk", path)

    return StrategyExecutionConfig(
        name=str(strategy.get("name") or strategy.get("id")),
        enabled=bool(strategy["enabled"]),
        stage=str(strategy["stage"]),
        max_position_pct=float(risk["max_position_pct"]),
        max_positions=int(risk["max_positions"]),
        daily_loss_cap_pct=float(risk["daily_loss_cap_pct"]),
        stop_loss_pct=(
            None if risk.get("stop_loss_pct") is None else float(risk["stop_loss_pct"])
        ),
        path=path,
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
