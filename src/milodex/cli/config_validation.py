"""Helpers for validating Milodex YAML configuration files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_STRATEGY_REQUIRED_KEYS: dict[str, set[str]] = {
    "strategy": {
        "name",
        "version",
        "description",
        "enabled",
        "universe",
        "parameters",
        "tempo",
        "risk",
        "stage",
        "backtest",
    },
    "strategy.tempo": {"bar_size", "min_hold_days", "max_hold_days"},
    "strategy.risk": {
        "max_position_pct",
        "max_positions",
        "daily_loss_cap_pct",
        "stop_loss_pct",
    },
    "strategy.backtest": {"slippage_pct", "commission_per_trade", "min_trades_required"},
}

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

_VALID_STAGES = {"backtest", "paper", "micro_live", "live"}
_VALID_BAR_SIZES = {"1D", "1H", "15Min", "5Min", "1Min"}


def validate_config_file(path: Path, kind: str | None = None) -> list[str]:
    """Validate a Milodex YAML config file and return success lines."""
    if not path.exists():
        msg = f"Config file does not exist: {path}"
        raise ValueError(msg)

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

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
    _require_keys(data, {"strategy"}, "root", path)

    strategy = _as_mapping(data["strategy"], "strategy", path)
    _require_keys(strategy, _STRATEGY_REQUIRED_KEYS["strategy"], "strategy", path)

    tempo = _as_mapping(strategy["tempo"], "strategy.tempo", path)
    _require_keys(tempo, _STRATEGY_REQUIRED_KEYS["strategy.tempo"], "strategy.tempo", path)

    risk = _as_mapping(strategy["risk"], "strategy.risk", path)
    _require_keys(risk, _STRATEGY_REQUIRED_KEYS["strategy.risk"], "strategy.risk", path)

    backtest = _as_mapping(strategy["backtest"], "strategy.backtest", path)
    _require_keys(
        backtest,
        _STRATEGY_REQUIRED_KEYS["strategy.backtest"],
        "strategy.backtest",
        path,
    )

    universe = strategy["universe"]
    if not isinstance(universe, list) or not universe or not all(
        isinstance(symbol, str) and symbol.strip() for symbol in universe
    ):
        msg = f"{path}: strategy.universe must be a non-empty list of symbols"
        raise ValueError(msg)

    if strategy["stage"] not in _VALID_STAGES:
        msg = (
            f"{path}: strategy.stage must be one of "
            f"{', '.join(sorted(_VALID_STAGES))}"
        )
        raise ValueError(msg)

    if tempo["bar_size"] not in _VALID_BAR_SIZES:
        valid_sizes = ", ".join(sorted(_VALID_BAR_SIZES))
        msg = f"{path}: strategy.tempo.bar_size must be one of {valid_sizes}"
        raise ValueError(msg)


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
