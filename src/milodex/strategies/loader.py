"""Strategy config loading, validation, and canonical hashing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from milodex.strategies.base import Strategy, StrategyContext, StrategyParameterSpec

_VALID_STAGES = {"backtest", "paper", "micro_live", "live"}
_VALID_BAR_SIZES = {"1D", "1H", "15Min", "5Min", "1Min"}


@dataclass(frozen=True)
class StrategyConfig:
    """Structured strategy config loaded from YAML."""

    strategy_id: str
    family: str
    template: str
    variant: str
    version: int
    description: str
    enabled: bool
    universe: tuple[str, ...]
    universe_ref: str | None
    parameters: dict[str, Any]
    tempo: dict[str, Any]
    risk: dict[str, Any]
    stage: str
    backtest: dict[str, Any]
    disable_conditions_additional: tuple[str, ...]
    path: Path
    raw_data: dict[str, Any]


@dataclass(frozen=True)
class LoadedStrategy:
    """Loaded strategy object plus its validated config and runtime context."""

    strategy: Strategy
    config: StrategyConfig
    context: StrategyContext


class StrategyRegistry:
    """Registry mapping strategy families/templates to concrete classes."""

    def __init__(self) -> None:
        self._strategies: dict[tuple[str, str], type[Strategy]] = {}

    def register(self, strategy_cls: type[Strategy]) -> None:
        key = (strategy_cls.family, strategy_cls.template)
        self._strategies[key] = strategy_cls

    def resolve(self, family: str, template: str) -> type[Strategy] | None:
        return self._strategies.get((family, template))


class StrategyLoader:
    """Loads a strategy config, validates it, and instantiates the strategy."""

    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self._registry = registry or build_default_registry()

    def load(self, path: Path) -> LoadedStrategy:
        config = load_strategy_config(path)
        strategy_cls = self._registry.resolve(config.family, config.template)
        if strategy_cls is None:
            msg = (
                "No strategy is registered for "
                f"family='{config.family}' template='{config.template}' "
                f"(strategy.id='{config.strategy_id}')."
            )
            raise ValueError(msg)
        validate_strategy_parameters(config.parameters, strategy_cls.parameter_specs, path)
        strategy = strategy_cls()
        resolved_universe = config.universe
        if config.universe_ref is not None and not resolved_universe:
            resolved_universe = resolve_universe_ref(config.universe_ref, path)
        context = StrategyContext(
            strategy_id=config.strategy_id,
            family=config.family,
            template=config.template,
            variant=config.variant,
            version=config.version,
            config_hash=compute_config_hash(path),
            parameters=dict(config.parameters),
            universe=resolved_universe,
            universe_ref=config.universe_ref,
            disable_conditions=config.disable_conditions_additional,
            config_path=str(path),
            manifest=_canonicalize_data(config.raw_data),
            positions={},
        )
        return LoadedStrategy(strategy=strategy, config=config, context=context)


def resolve_universe_ref(universe_ref: str, config_path: Path) -> tuple[str, ...]:
    """Resolve a ``universe_ref`` string to the concrete symbol tuple.

    Scans ``config_path.parent`` for ``universe_*.yaml`` manifests and
    returns the sorted, deduplicated union of the ``etfs`` and ``stocks``
    membership of the manifest whose ``universe.id`` matches ``universe_ref``.
    """
    configs_dir = config_path.parent
    for manifest_path in sorted(configs_dir.glob("universe_*.yaml")):
        try:
            with manifest_path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        universe = data.get("universe")
        if not isinstance(universe, dict):
            continue
        if str(universe.get("id", "")) != universe_ref:
            continue
        symbols: list[str] = []
        for key in ("etfs", "stocks"):
            members = universe.get(key, [])
            if not isinstance(members, list):
                continue
            for sym in members:
                if isinstance(sym, str) and sym.strip():
                    symbols.append(sym.strip().upper())
        if not symbols:
            msg = f"{manifest_path}: universe '{universe_ref}' has no members"
            raise ValueError(msg)
        return tuple(sorted(set(symbols)))
    msg = (
        f"{config_path}: universe_ref '{universe_ref}' not found in any "
        f"manifest under {configs_dir}"
    )
    raise ValueError(msg)


def load_strategy_config(path: Path) -> StrategyConfig:
    """Load and validate a strategy config from YAML."""
    if not path.exists():
        msg = f"Config file does not exist: {path}"
        raise ValueError(msg)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        msg = f"Config root must be a mapping: {path}"
        raise ValueError(msg)

    strategy = _mapping(data.get("strategy"), "strategy", path)
    _require_keys(
        strategy,
        {
            "id",
            "family",
            "template",
            "variant",
            "version",
            "description",
            "enabled",
            "parameters",
            "tempo",
            "risk",
            "stage",
            "backtest",
            "disable_conditions_additional",
        },
        "strategy",
        path,
    )

    tempo = _mapping(strategy.get("tempo"), "strategy.tempo", path)
    _require_keys(tempo, {"bar_size", "min_hold_days", "max_hold_days"}, "strategy.tempo", path)

    risk = _mapping(strategy.get("risk"), "strategy.risk", path)
    _require_keys(
        risk,
        {"max_position_pct", "max_positions", "daily_loss_cap_pct", "stop_loss_pct"},
        "strategy.risk",
        path,
    )

    backtest = _mapping(strategy.get("backtest"), "strategy.backtest", path)
    _require_keys(
        backtest,
        {"slippage_pct", "commission_per_trade", "min_trades_required"},
        "strategy.backtest",
        path,
    )

    parameters = _mapping(strategy.get("parameters"), "strategy.parameters", path)
    disable_conditions = strategy.get("disable_conditions_additional")
    if not isinstance(disable_conditions, list) or not all(
        isinstance(item, str) and item.strip() for item in disable_conditions
    ):
        msg = f"{path}: strategy.disable_conditions_additional must be a list of non-empty strings"
        raise ValueError(msg)

    universe, universe_ref = _load_universe(strategy, path)

    if strategy["stage"] not in _VALID_STAGES:
        msg = f"{path}: strategy.stage must be one of {', '.join(sorted(_VALID_STAGES))}"
        raise ValueError(msg)
    if tempo["bar_size"] not in _VALID_BAR_SIZES:
        msg = (
            f"{path}: strategy.tempo.bar_size must be one of {', '.join(sorted(_VALID_BAR_SIZES))}"
        )
        raise ValueError(msg)

    _validate_strategy_id(strategy, path)

    return StrategyConfig(
        strategy_id=str(strategy["id"]),
        family=str(strategy["family"]),
        template=str(strategy["template"]),
        variant=str(strategy["variant"]),
        version=int(strategy["version"]),
        description=str(strategy["description"]),
        enabled=bool(strategy["enabled"]),
        universe=universe,
        universe_ref=universe_ref,
        parameters=dict(parameters),
        tempo=dict(tempo),
        risk=dict(risk),
        stage=str(strategy["stage"]),
        backtest=dict(backtest),
        disable_conditions_additional=tuple(disable_conditions),
        path=path,
        raw_data=data,
    )


def validate_strategy_parameters(
    parameters: dict[str, Any],
    specs: Iterable[StrategyParameterSpec],
    path: Path,
) -> None:
    """Validate parameters against the strategy-declared parameter spec."""
    for spec in specs:
        if spec.name not in parameters:
            if spec.required:
                msg = f"{path}: missing required parameter '{spec.name}'"
                raise ValueError(msg)
            continue
        value = parameters[spec.name]
        if value is None:
            if spec.allow_none:
                continue
            msg = f"{path}: parameter '{spec.name}' cannot be null"
            raise ValueError(msg)
        if not isinstance(value, spec.expected_types):
            expected = ", ".join(type_.__name__ for type_ in spec.expected_types)
            msg = f"{path}: parameter '{spec.name}' must be one of ({expected})"
            raise ValueError(msg)


def compute_config_hash(path: Path) -> str:
    """Return a SHA-256 hash of the canonicalized YAML contents."""
    if not path.exists():
        msg = f"Config file does not exist: {path}"
        raise ValueError(msg)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    canonical_json = json.dumps(_canonicalize_data(data), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def build_default_registry() -> StrategyRegistry:
    """Return a registry preloaded with built-in strategy classes."""
    from milodex.strategies.meanrev_ibs_lowclose import MeanrevIbsLowcloseStrategy
    from milodex.strategies.meanrev_rsi2_pullback import MeanrevRsi2PullbackStrategy
    from milodex.strategies.regime_spy_shy_200dma import RegimeSpyShy200DmaStrategy

    registry = StrategyRegistry()
    registry.register(RegimeSpyShy200DmaStrategy)
    registry.register(MeanrevRsi2PullbackStrategy)
    registry.register(MeanrevIbsLowcloseStrategy)
    return registry


def _load_universe(strategy: dict[str, Any], path: Path) -> tuple[tuple[str, ...], str | None]:
    has_inline_universe = "universe" in strategy
    has_universe_ref = "universe_ref" in strategy
    if not has_inline_universe and not has_universe_ref:
        msg = f"{path}: strategy must define either universe or universe_ref"
        raise ValueError(msg)
    if has_inline_universe and has_universe_ref:
        msg = f"{path}: strategy cannot define both universe and universe_ref"
        raise ValueError(msg)
    if has_inline_universe:
        universe = strategy["universe"]
        if (
            not isinstance(universe, list)
            or not universe
            or not all(isinstance(symbol, str) and symbol.strip() for symbol in universe)
        ):
            msg = f"{path}: strategy.universe must be a non-empty list of symbols"
            raise ValueError(msg)
        return tuple(symbol.strip().upper() for symbol in universe), None

    universe_ref = strategy["universe_ref"]
    if not isinstance(universe_ref, str) or not universe_ref.strip():
        msg = f"{path}: strategy.universe_ref must be a non-empty string"
        raise ValueError(msg)
    return (), universe_ref.strip()


def _validate_strategy_id(strategy: dict[str, Any], path: Path) -> None:
    strategy_id = str(strategy["id"])
    expected_suffix = f".v{int(strategy['version'])}"
    if not strategy_id.endswith(expected_suffix):
        msg = f"{path}: strategy.id must end with '{expected_suffix}'"
        raise ValueError(msg)
    expected_prefix = ".".join(
        (
            str(strategy["family"]),
            str(strategy["template"]),
            str(strategy["variant"]),
        )
    )
    if strategy_id != f"{expected_prefix}{expected_suffix}":
        msg = (
            f"{path}: strategy.id must match family/template/variant/version "
            f"('{expected_prefix}{expected_suffix}')"
        )
        raise ValueError(msg)


def _mapping(value: Any, label: str, path: Path) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{path}: {label} must be a mapping"
        raise ValueError(msg)
    return value


def _require_keys(value: dict[str, Any], required: set[str], label: str, path: Path) -> None:
    missing = sorted(required - set(value))
    if missing:
        msg = f"{path}: missing required key(s) in {label}: {', '.join(missing)}"
        raise ValueError(msg)


def canonicalize_config_data(value: Any) -> Any:
    """Return ``value`` with dict keys sorted recursively (canonical form).

    Shared between :func:`compute_config_hash` and the frozen-manifest freeze
    path so the ``config_hash`` column and the ``config_json`` column are
    always derived from the exact same canonical representation.
    """
    return _canonicalize_data(value)


def _canonicalize_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize_data(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize_data(item) for item in value]
    return value
