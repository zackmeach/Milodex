"""Configuration loaders for execution and risk services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RiskDefaults:
    """Global execution and risk guardrails."""

    kill_switch_enabled: bool
    kill_switch_max_drawdown_pct: float
    require_manual_reset: bool
    max_single_position_pct: float
    max_concurrent_positions: int
    max_total_exposure_pct: float
    max_daily_loss_pct: float
    max_trades_per_day: int
    max_order_value_pct: float
    duplicate_order_window_seconds: int
    max_data_staleness_seconds: int


@dataclass(frozen=True)
class StrategyExecutionConfig:
    """Execution-relevant strategy settings."""

    name: str
    enabled: bool
    stage: str
    max_position_pct: float
    max_positions: int
    daily_loss_cap_pct: float
    stop_loss_pct: float
    path: Path


def load_risk_defaults(path: Path) -> RiskDefaults:
    """Load global risk defaults from YAML."""
    data = _load_yaml(path)
    kill_switch = _mapping(data.get("kill_switch"), "kill_switch", path)
    portfolio = _mapping(data.get("portfolio"), "portfolio", path)
    daily_limits = _mapping(data.get("daily_limits"), "daily_limits", path)
    order_safety = _mapping(data.get("order_safety"), "order_safety", path)

    return RiskDefaults(
        kill_switch_enabled=bool(kill_switch["enabled"]),
        kill_switch_max_drawdown_pct=float(kill_switch["max_drawdown_pct"]),
        require_manual_reset=bool(kill_switch["require_manual_reset"]),
        max_single_position_pct=float(portfolio["max_single_position_pct"]),
        max_concurrent_positions=int(portfolio["max_concurrent_positions"]),
        max_total_exposure_pct=float(portfolio["max_total_exposure_pct"]),
        max_daily_loss_pct=float(daily_limits["max_daily_loss_pct"]),
        max_trades_per_day=int(daily_limits["max_trades_per_day"]),
        max_order_value_pct=float(order_safety["max_order_value_pct"]),
        duplicate_order_window_seconds=int(order_safety["duplicate_order_window_seconds"]),
        max_data_staleness_seconds=int(order_safety["max_data_staleness_seconds"]),
    )


def load_strategy_execution_config(path: Path) -> StrategyExecutionConfig:
    """Load execution-relevant strategy settings from YAML."""
    data = _load_yaml(path)
    strategy = _mapping(data.get("strategy"), "strategy", path)
    risk = _mapping(strategy.get("risk"), "strategy.risk", path)

    return StrategyExecutionConfig(
        name=str(strategy["name"]),
        enabled=bool(strategy["enabled"]),
        stage=str(strategy["stage"]),
        max_position_pct=float(risk["max_position_pct"]),
        max_positions=int(risk["max_positions"]),
        daily_loss_cap_pct=float(risk["daily_loss_cap_pct"]),
        stop_loss_pct=float(risk["stop_loss_pct"]),
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
