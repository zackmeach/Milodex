"""Risk-layer configuration.

``RiskDefaults`` is the global risk-guardrail dataclass consumed by
:class:`milodex.risk.evaluator.RiskEvaluator`. It lives in the risk
module per ADR 0019 so the risk layer owns its own inputs.

``load_active_risk_profile()`` is the **runtime consumer entry point** per
ADR 0054: it merges ``configs/risk_defaults.yaml`` with the active operator
profile overlay and validates the result against ``_ABSOLUTE_CEILINGS``.
``execution/service.py`` routes through this; the backtest engine intentionally
stays on the base ``load_risk_defaults()`` (ADR 0054 §3).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from milodex.config import get_data_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Account-level absolute ceilings. NOT EDITABLE via YAML or env vars.
# Per ADR 0054 §4 and FOUNDER_INTENT.md:137 "the operator cannot disable the floor."
#
# Each value is the maximum permitted value across ANY risk profile.
# To change a ceiling, amend ADR 0054 and modify this literal — no other path.
#
# Justification (revisit only via ADR amendment):
# - kill_switch.max_drawdown_pct = 0.20: above Aggressive's 0.15 by a safety
#   margin, well below the 25% institutional pension-fund tolerance band.
#   Sub-$1k Phase-1 capital — a 20% drawdown is ~$200, recoverable.
# - portfolio.max_total_exposure_pct = 0.85: above Aggressive's 0.75; keeps
#   minimum 15% cash buffer regardless of profile.
# - daily_limits.max_daily_loss_pct = 0.08: above Aggressive's 0.05; single-
#   session loss never exceeds 8% even under elevated posture.
# ---------------------------------------------------------------------------
_ABSOLUTE_CEILINGS: Mapping[str, float] = MappingProxyType({
    "kill_switch.max_drawdown_pct": 0.20,
    "portfolio.max_total_exposure_pct": 0.85,
    "daily_limits.max_daily_loss_pct": 0.08,
})

# Permitted profile names. Explicit allowlist — not a file-system scan.
_KNOWN_PROFILES: frozenset[str] = frozenset({"conservative", "standard", "aggressive"})


class CeilingViolationError(RuntimeError):
    """Raised when a risk profile's resolved values exceed an account-level ceiling.

    Per ADR 0054 §4: ceiling violations refuse startup, they do not silently fall back.
    """


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


def load_backtesting_defaults(path: Path) -> dict[str, Any]:
    """Load the ``backtesting`` section from a risk-defaults YAML.

    Returns the raw mapping so callers can extract individual keys without
    requiring a full dataclass for the (currently small) backtesting section.
    Returns an empty dict when the file exists but has no ``backtesting`` key,
    preserving backward compatibility.
    """
    data = _load_yaml(path)
    backtesting = data.get("backtesting")
    if backtesting is None:
        return {}
    if not isinstance(backtesting, dict):
        msg = f"{path}: backtesting must be a mapping"
        raise ValueError(msg)
    return dict(backtesting)


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


# ---------------------------------------------------------------------------
# ADR 0054: active risk-profile loader
# ---------------------------------------------------------------------------


def get_active_profile_name() -> str:
    """Return the active profile name from ``{data_dir}/risk_profile.txt``.

    Uses ``get_data_dir()`` so the path is consistent with the rest of the
    system and benefits from the ``MILODEX_DATA_DIR`` env-var isolation used
    in tests (see ``tests/conftest.py:_isolate_milodex_data_dirs``).

    Three-case fallback per ADR 0054 §2:
    - File absent or unreadable → ``"conservative"`` (silent, no warning).
    - File present but empty → ``"conservative"`` (silent).
    - File present with content → return stripped, lower-cased value.
      The caller (``load_active_risk_profile``) validates against the allowlist.
    """
    profile_file = get_data_dir() / "risk_profile.txt"
    if not profile_file.exists():
        return "conservative"
    try:
        name = profile_file.read_text(encoding="utf-8").strip().lower()
        return name or "conservative"
    except (OSError, UnicodeDecodeError):
        return "conservative"


def _load_overlay(profile_name: str, configs_dir: Path | None = None) -> dict[str, Any]:
    """Load ``configs/risk_profiles/{profile_name}.yaml`` as a dict.

    Returns an empty dict when the file does not exist or fails to parse —
    the caller handles the unknown-profile case before calling here.
    """
    base_dir = configs_dir or Path("configs")
    path = base_dir / "risk_profiles" / f"{profile_name}.yaml"
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            logger.warning(
                "Risk profile overlay at %s is not a YAML mapping; got %s. "
                "Falling back to base risk_defaults.",
                path,
                type(data).__name__,
            )
            return {}
        return data
    except yaml.YAMLError as exc:
        logger.warning(
            "Risk profile overlay at %s failed to parse: %s. "
            "Falling back to base risk_defaults.",
            path,
            exc,
        )
        return {}


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge; overlay keys win over base keys."""
    out: dict[str, Any] = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _get_by_path(d: dict[str, Any], dotted_path: str) -> Any:
    """Walk a dotted path through nested dicts. Returns None if any segment missing."""
    cur: Any = d
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _validate_against_ceilings(profile: dict[str, Any]) -> None:
    """Raise ``CeilingViolationError`` if any path in ``_ABSOLUTE_CEILINGS`` is exceeded
    or is missing from the merged profile.

    Per ADR 0054 §4: ceilings refuse startup; they do not fall back silently.
    Missing required paths are also refused — a missing key causes a confusing
    downstream KeyError; raising here shifts the failure earlier with a clear message.
    """
    for dotted_path, ceiling in _ABSOLUTE_CEILINGS.items():
        value = _get_by_path(profile, dotted_path)
        if value is None:
            raise CeilingViolationError(
                f"Required risk-config path {dotted_path} is missing from merged profile; "
                f"cannot validate against ceiling. See ADR 0054 §4."
            )
        if float(value) > ceiling:
            raise CeilingViolationError(
                f"Profile value {dotted_path}={value} exceeds account-level ceiling "
                f"{ceiling}. Edit the active overlay in configs/risk_profiles/ to comply. "
                f"See ADR 0054 §4."
            )


def _risk_defaults_from_dict(data: dict[str, Any]) -> RiskDefaults:
    """Construct a ``RiskDefaults`` from the merged YAML dict structure."""
    kill_switch = _mapping(data.get("kill_switch"), "kill_switch", Path("<merged>"))
    portfolio = _mapping(data.get("portfolio"), "portfolio", Path("<merged>"))
    daily_limits = _mapping(data.get("daily_limits"), "daily_limits", Path("<merged>"))
    order_safety = _mapping(data.get("order_safety"), "order_safety", Path("<merged>"))
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


def load_active_risk_profile(
    base_path: Path | None = None,
) -> RiskDefaults:
    """Load the active operator risk profile and return a validated ``RiskDefaults``.

    Merges the base risk-defaults YAML with the active profile overlay from
    ``configs/risk_profiles/{name}.yaml`` (sibling to the base path), validates
    the result against ``_ABSOLUTE_CEILINGS``, and returns a ``RiskDefaults``
    instance.

    This is the **runtime consumer entry point** per ADR 0054.
    ``execution/service.py`` MUST route through this function.
    The backtest engine intentionally uses ``load_risk_defaults()`` instead
    (ADR 0054 §3: backtests evaluate strategy potential, not operator posture).

    Profile file path: ``{get_data_dir()}/risk_profile.txt`` — consistent with
    the rest of the system's use of ``get_data_dir()`` (env-var overrideable for
    test isolation via ``MILODEX_DATA_DIR``).

    Three-case fallback semantics (ADR 0054):
    - Missing ``risk_profile.txt`` → Conservative, silently.
    - Unknown profile name or malformed YAML → Conservative + WARNING log.
    - Resolved values exceed a ceiling → raise ``CeilingViolationError`` (refuse startup).
    """
    _base_path = base_path or Path("configs/risk_defaults.yaml")
    with _base_path.open(encoding="utf-8") as fh:
        base: dict[str, Any] = yaml.safe_load(fh)

    # configs_dir is the parent of risk_defaults.yaml (typically configs/).
    # risk_profiles/ overlay directory is a sibling of the base YAML.
    configs_dir = _base_path.parent

    active = get_active_profile_name()

    if active not in _KNOWN_PROFILES:
        logger.warning(
            "Risk profile %r is unknown; falling back to 'conservative'. "
            "Edit %s to one of: %s. (ADR 0054 §2)",
            active,
            get_data_dir() / "risk_profile.txt",
            ", ".join(sorted(_KNOWN_PROFILES)),
        )
        active = "conservative"

    overlay = _load_overlay(active, configs_dir=configs_dir)
    merged = _merge(base, overlay)
    _validate_against_ceilings(merged)
    return _risk_defaults_from_dict(merged)
