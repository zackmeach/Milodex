"""Helpers for validating Milodex YAML configuration files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from milodex.strategies.loader import load_strategy_config

_RISK_REQUIRED_KEYS: dict[str, set[str]] = {
    "kill_switch": {"enabled", "max_drawdown_pct", "require_manual_reset"},
    "portfolio": {
        "max_single_position_pct",
        "max_concurrent_positions",
        "max_total_exposure_pct",
    },
    "daily_limits": {"max_daily_loss_pct", "max_trades_per_day"},
    "order_safety": {
        "max_order_value_pct",
        "duplicate_order_window_seconds",
        "max_data_staleness_seconds",
    },
}


def validate_config_file(path: Path, kind: str | None = None) -> list[str]:
    """Validate a Milodex YAML config file and return success lines."""
    if not path.exists():
        msg = f"Config file does not exist: {path}"
        raise ValueError(msg)
    if not path.is_file():
        msg = f"Config path is not a file: {path}"
        raise ValueError(msg)

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        msg = f"Config file is not valid YAML: {path}: {exc}"
        raise ValueError(msg) from exc

    if not isinstance(data, dict):
        msg = f"Config root must be a mapping: {path}"
        raise ValueError(msg)

    detected_kind = kind or _infer_kind(path)
    if detected_kind == "strategy":
        _validate_strategy_config(data, path)
    elif detected_kind == "risk":
        _validate_risk_config(data, path)
    else:
        msg = f"Unsupported config kind: {detected_kind}"
        raise ValueError(msg)

    return [
        f"Config validation passed: {path}",
        f"Detected kind: {detected_kind}",
    ]


def _infer_kind(path: Path) -> str:
    if path.name == "risk_defaults.yaml":
        return "risk"
    return "strategy"


def _validate_strategy_config(data: dict[str, Any], path: Path) -> None:
    load_strategy_config(path)


def _validate_risk_config(data: dict[str, Any], path: Path) -> None:
    for section, required_keys in _RISK_REQUIRED_KEYS.items():
        mapping = _as_mapping(data.get(section), section, path)
        _require_keys(mapping, required_keys, section, path)


def _as_mapping(value: Any, label: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{path}: {label} must be a mapping"
        raise ValueError(msg)
    return value


def _require_keys(
    value: dict[str, Any],
    required_keys: set[str],
    label: str,
    path: Path,
) -> None:
    missing = sorted(required_keys - set(value))
    if missing:
        msg = f"{path}: missing required key(s) in {label}: {', '.join(missing)}"
        raise ValueError(msg)
