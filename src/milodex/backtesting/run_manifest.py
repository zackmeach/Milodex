"""Reproducibility manifests for backtest runs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date
from importlib import metadata as package_metadata
from pathlib import Path
from typing import Any

import yaml

from milodex.data.provider import DataProvider
from milodex.strategies.loader import LoadedStrategy, compute_config_hash


@dataclass(frozen=True)
class BacktestRunManifestInput:
    loaded: LoadedStrategy
    data_provider: DataProvider
    requested_start: date
    requested_end: date
    warmup_start: date
    risk_policy: str
    slippage_pct: float
    commission_per_trade: float
    initial_equity: float
    data_quality: dict[str, Any] | None
    coverage_threshold: float


def build_backtest_run_manifest(input_: BacktestRunManifestInput) -> dict[str, Any]:
    """Return a JSON-safe manifest describing what produced a backtest result."""
    universe_manifest = _resolve_universe_manifest(
        input_.loaded.context.universe_ref,
        input_.loaded.config.path,
    )
    data_provider = _data_provider_metadata(input_.data_provider)
    git = _git_metadata(Path(input_.loaded.context.config_path).resolve().parent)

    return {
        "schema_version": 1,
        "milodex_version": _milodex_version(),
        "code": git,
        "strategy": {
            "strategy_id": input_.loaded.config.strategy_id,
            "family": input_.loaded.context.family,
            "template": input_.loaded.context.template,
            "variant": input_.loaded.context.variant,
            "version": input_.loaded.context.version,
            "config_path": str(input_.loaded.config.path),
            "config_hash": input_.loaded.context.config_hash,
        },
        "universe": {
            "universe_ref": input_.loaded.context.universe_ref,
            "symbol_count": len(input_.loaded.context.universe),
            "symbols": list(input_.loaded.context.universe),
            "manifest_path": universe_manifest.get("path"),
            "manifest_hash": universe_manifest.get("hash"),
        },
        "execution_assumptions": {
            "risk_policy": input_.risk_policy,
            "slippage_pct": input_.slippage_pct,
            "commission_per_trade": input_.commission_per_trade,
            "initial_equity": input_.initial_equity,
        },
        "date_window": {
            "requested_start": input_.requested_start.isoformat(),
            "requested_end": input_.requested_end.isoformat(),
            "warmup_start": input_.warmup_start.isoformat(),
        },
        "data": {
            "provider": data_provider,
            "coverage_threshold": input_.coverage_threshold,
            "quality": _data_quality_summary(input_.data_quality),
        },
    }


def _resolve_universe_manifest(
    universe_ref: str | None,
    config_path: Path,
) -> dict[str, str | None]:
    if universe_ref is None:
        return {"path": None, "hash": None}

    for manifest_path in sorted(config_path.parent.glob("universe_*.yaml")):
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
        return {
            "path": str(manifest_path),
            "hash": compute_config_hash(manifest_path),
        }
    return {"path": None, "hash": None}


def _data_provider_metadata(provider: DataProvider) -> dict[str, Any]:
    provider_cls = provider.__class__
    module = provider_cls.__module__
    module_obj = __import__(module, fromlist=["CACHE_VERSION"])
    return {
        "class": f"{module}.{provider_cls.__name__}",
        "cache_version": getattr(module_obj, "CACHE_VERSION", None),
    }


def _data_quality_summary(data_quality: dict[str, Any] | None) -> dict[str, Any]:
    if not data_quality:
        return {
            "status": None,
            "blocker_count": None,
            "warning_count": None,
            "issue_codes": [],
        }
    return {
        "status": data_quality.get("status"),
        "blocker_count": data_quality.get("blocker_count"),
        "warning_count": data_quality.get("warning_count"),
        "issue_codes": list(data_quality.get("issue_codes", [])),
    }


def _milodex_version() -> str | None:
    try:
        return package_metadata.version("milodex")
    except package_metadata.PackageNotFoundError:
        return None


def _git_metadata(cwd: Path) -> dict[str, Any]:
    commit = _git_stdout(cwd, "rev-parse", "HEAD")
    dirty_output = _git_stdout(cwd, "status", "--short")
    return {
        "commit": commit,
        "dirty": None if dirty_output is None else bool(dirty_output.strip()),
        "available": commit is not None,
    }


def _git_stdout(cwd: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()
