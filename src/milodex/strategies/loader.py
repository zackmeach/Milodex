"""Strategy config loading, validation, and canonical hashing."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from milodex.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyParameterRelation,
    StrategyParameterSpec,
)
from milodex.strategies.instrument_eligibility import reject_ineligible_instruments

logger = logging.getLogger(__name__)

_VALID_STAGES = {"idle", "backtest", "paper", "micro_live", "live"}
_VALID_BAR_SIZES = {"1D", "1H", "30Min", "15Min", "5Min", "1Min"}
_VALID_POSITION_LIFECYCLES = {"same_session", "multi_session"}


@dataclass(frozen=True)
class StrategyConfig:
    """Structured strategy config loaded from YAML."""

    strategy_id: str
    family: str
    template: str
    variant: str
    version: int
    display_name: str | None
    description: str
    enabled: bool
    universe: tuple[str, ...]
    universe_ref: str | None
    baseline_ref: str | None
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
        validate_strategy_parameters(
            config.parameters,
            strategy_cls.parameter_specs,
            path,
            relations=strategy_cls.parameter_relations,
        )
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
        reject_ineligible_instruments(symbols, source=str(manifest_path))
        return tuple(sorted(set(symbols)))
    msg = (
        f"{config_path}: universe_ref '{universe_ref}' not found in any "
        f"manifest under {configs_dir}"
    )
    raise ValueError(msg)


def resolve_universe_survivorship_corrected(universe_ref: str, config_path: Path) -> bool:
    """Return whether the named universe is point-in-time corrected for survivorship.

    A universe declares ``survivorship_corrected: true`` when its membership
    has been reconstructed against historical-as-of dates rather than applied
    retroactively from a present-day list. ETF-only universes (where the
    constituents are stable instruments that have not been delisted) typically
    qualify; stock universes assembled from a current ticker list do not.

    The default for a manifest that does not declare the field is ``False`` —
    the conservative answer. Backtest credibility commentary (per
    ``docs/RISK_POLICY.md`` "Known Backtest Limitations and Biases") relies on
    this being a presence-or-absence-of-evidence flag, not an opinion.
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
        return bool(universe.get("survivorship_corrected", False))
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
    # Only bar_size is load-bearing. Hold limits live in parameters.max_hold_days
    # (the live time-stop); tempo.min/max_hold_days were never read — don't require them.
    _require_keys(tempo, {"bar_size"}, "strategy.tempo", path)

    risk = _mapping(strategy.get("risk"), "strategy.risk", path)
    _require_keys(
        risk,
        {"max_position_pct", "max_positions", "daily_loss_cap_pct"},
        "strategy.risk",
        path,
    )

    backtest = _mapping(strategy.get("backtest"), "strategy.backtest", path)
    _require_keys(
        backtest,
        {"commission_per_trade", "min_trades_required"},
        "strategy.backtest",
        path,
    )

    parameters = _mapping(strategy.get("parameters"), "strategy.parameters", path)

    # Cross-check: when both risk.stop_loss_pct and parameters.stop_loss_pct are
    # present and numeric they must agree within floating-point tolerance (1e-9).
    # Silent divergence of two identically-named fields is the failure mode this
    # guards: the strategy code uses parameters.stop_loss_pct for the live stop;
    # risk.stop_loss_pct is OPTIONAL and inert — unconsumed at runtime (HR-7 /
    # R-P2-1 / P2-03). If both are present and they diverge, the operator has
    # mis-specified the config — refuse loudly rather than silently running with
    # a wrong stop. Absent risk.stop_loss_pct, or only one twin present: no
    # cross-check, no error.
    _risk_stop = risk.get("stop_loss_pct")
    _param_stop = parameters.get("stop_loss_pct")
    if (
        _risk_stop is not None
        and _param_stop is not None
        and abs(float(_risk_stop) - float(_param_stop)) > 1e-9
    ):
        msg = (
            f"{path}: risk.stop_loss_pct ({_risk_stop}) does not match "
            f"parameters.stop_loss_pct ({_param_stop}). "
            "Both fields are present; they must agree. "
            "Update one field so they match, or remove the one you do not intend to use."
        )
        raise ValueError(msg)

    display_name = strategy.get("display_name")
    if display_name is not None:
        if not isinstance(display_name, str) or not display_name.strip():
            msg = f"{path}: strategy.display_name must be a non-empty string when provided"
            raise ValueError(msg)
        display_name = display_name.strip()
    baseline_ref = strategy.get("baseline_ref")
    if baseline_ref is not None:
        if not isinstance(baseline_ref, str) or not baseline_ref.strip():
            msg = f"{path}: strategy.baseline_ref must be a non-empty string when provided"
            raise ValueError(msg)
        baseline_ref = baseline_ref.strip()
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
    position_lifecycle = tempo.get("position_lifecycle")
    if tempo["bar_size"] != "1D" and position_lifecycle is None:
        raise ValueError(
            f"{path}: strategy.tempo.position_lifecycle is required for non-daily strategies"
        )
    if (
        position_lifecycle is not None
        and position_lifecycle not in _VALID_POSITION_LIFECYCLES
    ):
        msg = (
            f"{path}: strategy.tempo.position_lifecycle must be one of "
            f"{', '.join(sorted(_VALID_POSITION_LIFECYCLES))}"
        )
        raise ValueError(msg)

    _validate_strategy_id(strategy, path)

    return StrategyConfig(
        strategy_id=str(strategy["id"]),
        family=str(strategy["family"]),
        template=str(strategy["template"]),
        variant=str(strategy["variant"]),
        version=int(strategy["version"]),
        display_name=display_name,
        description=str(strategy["description"]),
        enabled=bool(strategy["enabled"]),
        universe=universe,
        universe_ref=universe_ref,
        baseline_ref=baseline_ref,
        parameters=dict(parameters),
        tempo=dict(tempo),
        risk=dict(risk),
        stage=str(strategy["stage"]),
        backtest=dict(backtest),
        disable_conditions_additional=tuple(disable_conditions),
        path=path,
        raw_data=data,
    )


def resolve_config_path(strategy_id: str, config_dir: Path = Path("configs")) -> Path:
    """Canonical strategy-id -> config-path resolver.

    Locate the YAML file under ``config_dir`` whose ``strategy.id`` equals
    ``strategy_id``. This is the single owner of the glob-load-match loop that
    was previously copied across the promotion, CLI, runner, and backtest
    layers; those sites now delegate here.

    Invalid configs (bad YAML or failed validation) are skipped, not fatal.
    Raises ``ValueError`` if no config matches ``strategy_id``.
    """
    for path in sorted(config_dir.glob("*.yaml")):
        try:
            config = load_strategy_config(path)
        except (ValueError, yaml.YAMLError) as exc:
            logger.debug("Skipping invalid config %s: %s", path, exc)
            continue
        if config.strategy_id == strategy_id:
            return path
    msg = f"Strategy config not found for strategy id: {strategy_id}"
    raise ValueError(msg)


def validate_strategy_parameters(
    parameters: dict[str, Any],
    specs: Iterable[StrategyParameterSpec],
    path: Path,
    relations: Iterable[StrategyParameterRelation] = (),
) -> None:
    """Validate parameters against the strategy-declared parameter spec.

    Enforces presence, type, declared value bounds/choices, and named
    cross-parameter ``relations`` at config-load time (P2-04), so a bad
    config fails here — naming the file, the parameter, and the violated
    constraint — instead of at first evaluation.
    """
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
        _validate_parameter_constraints(spec, value, path)
    for relation in relations:
        detail = relation.check(parameters)
        if detail is not None:
            msg = f"{path}: parameter constraint '{relation.name}' violated: {detail}"
            raise ValueError(msg)


def _validate_parameter_constraints(spec: StrategyParameterSpec, value: Any, path: Path) -> None:
    """Enforce one spec's declared bounds/choices against a type-valid value."""
    if spec.choices is not None and value not in spec.choices:
        allowed = ", ".join(repr(choice) for choice in spec.choices)
        msg = f"{path}: parameter '{spec.name}' must be one of ({allowed}), got {value!r}"
        raise ValueError(msg)
    if not isinstance(value, int | float):
        return
    number = float(value)
    if spec.minimum is not None and number < spec.minimum:
        msg = f"{path}: parameter '{spec.name}' must be >= {spec.minimum}, got {value!r}"
        raise ValueError(msg)
    if spec.maximum is not None and number > spec.maximum:
        msg = f"{path}: parameter '{spec.name}' must be <= {spec.maximum}, got {value!r}"
        raise ValueError(msg)
    if spec.exclusive_minimum is not None and number <= spec.exclusive_minimum:
        msg = f"{path}: parameter '{spec.name}' must be > {spec.exclusive_minimum}, got {value!r}"
        raise ValueError(msg)
    if spec.exclusive_maximum is not None and number >= spec.exclusive_maximum:
        msg = f"{path}: parameter '{spec.name}' must be < {spec.exclusive_maximum}, got {value!r}"
        raise ValueError(msg)


def compute_config_hash(path: Path) -> str:
    """Return a SHA-256 hash of the canonicalized YAML contents."""
    if not path.exists():
        msg = f"Config file does not exist: {path}"
        raise ValueError(msg)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    canonical_json = json.dumps(
        canonicalize_config_data(data),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_config_hash_or_none(path: Path) -> str | None:
    """CRLF-insensitive config hash that returns ``None`` instead of raising.

    Drain-time guard helper (I-7): the queue-at-open drain authority
    (:meth:`milodex.core.event_store.EventStore.get_active_queued_intents`)
    re-resolves a queued intent's config at the open and DROPS the intent when
    its config can no longer be hashed (path deleted / moved / unreadable / not
    parseable) — exactly the cases :func:`compute_config_hash` raises on. The
    drain loop must drop, never raise, so it calls this non-raising wrapper.

    Hashing is CRLF-insensitive because the underlying YAML load normalizes line
    endings before canonicalization, so a checkout that flipped LF<->CRLF does
    not spuriously invalidate an otherwise-identical config.

    Caught exception families: ``ValueError`` (missing path), ``OSError``
    (unreadable), ``yaml.YAMLError`` (unparseable), and ``TypeError`` — a config
    whose parsed mapping has heterogeneous keys (e.g. an unquoted numeric or bool
    key alongside string keys) makes the canonicalizing ``sorted()`` /
    ``json.dumps(sort_keys=True)`` raise ``'<' not supported`` ``TypeError``;
    that must drop the intent (fail-closed), never escape into the drain loop.
    """
    try:
        return compute_config_hash(path)
    except (ValueError, OSError, TypeError, yaml.YAMLError):
        return None


def build_default_registry() -> StrategyRegistry:
    """Return a registry preloaded with all concrete strategy classes.

    Discovery is automatic: every module under ``milodex.strategies`` is
    imported (in sorted order for determinism) and all concrete
    :class:`Strategy` subclasses — including non-rule decision-layer seam
    proofs (ScoredLinearFeatures, TreeBucketedLookup, backtest-only /
    lifecycle-exempt) — are registered.  Import errors propagate loudly so a
    broken strategy module can never silently drop its class from the registry.

    Duplicate-key detection: if two distinct classes claim the same
    ``(family, template)`` pair a :class:`ValueError` is raised immediately,
    naming both classes.  This catches config-id collisions at startup rather
    than silently last-wins.
    """
    import importlib
    import inspect
    import pkgutil

    import milodex.strategies as _strategies_pkg

    _pkg_prefix = _strategies_pkg.__name__ + "."

    # Import every module in the package in sorted order for a stable scan.
    for module_info in sorted(
        pkgutil.iter_modules(_strategies_pkg.__path__, prefix=_pkg_prefix),
        key=lambda m: m.name,
    ):
        importlib.import_module(module_info.name)  # import errors propagate

    # Collect all concrete Strategy subclasses via a transitive walk.
    def _all_subclasses(cls: type) -> list[type]:
        result: list[type] = []
        for sub in cls.__subclasses__():
            result.append(sub)
            result.extend(_all_subclasses(sub))
        return result

    candidates: list[type[Strategy]] = [
        cls
        for cls in _all_subclasses(Strategy)
        if (
            cls is not Strategy
            and not inspect.isabstract(cls)
            and getattr(cls, "__module__", "").startswith(_pkg_prefix)
            and getattr(cls, "family", "") != ""
            and getattr(cls, "template", "") != ""
        )
    ]

    # Sort deterministically; include module+qualname so duplicate-key error
    # messages are order-independent when two classes tie on (family, template).
    candidates.sort(key=lambda cls: (cls.family, cls.template, cls.__module__, cls.__qualname__))

    # Build registry with duplicate-key detection.
    registry = StrategyRegistry()
    seen: dict[tuple[str, str], type[Strategy]] = {}
    for cls in candidates:
        key = (cls.family, cls.template)
        if key in seen:
            msg = (
                f"Duplicate strategy key {key!r}: "
                f"'{cls.__qualname__}' in '{cls.__module__}' conflicts with "
                f"'{seen[key].__qualname__}' in '{seen[key].__module__}'"
            )
            raise ValueError(msg)
        seen[key] = cls
        registry.register(cls)

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
        resolved = tuple(symbol.strip().upper() for symbol in universe)
        reject_ineligible_instruments(resolved, source=str(path))
        return resolved, None

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
    canonical = _canonicalize_data(value)
    if isinstance(canonical, dict):
        strategy = canonical.get("strategy")
        if isinstance(strategy, dict):
            strategy = dict(strategy)
            strategy.pop("display_name", None)
            strategy.pop("baseline_ref", None)
            canonical = dict(canonical)
            canonical["strategy"] = strategy
    return canonical


def _canonicalize_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize_data(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize_data(item) for item in value]
    return value
