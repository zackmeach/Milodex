"""Property tests locking the canonical-config hash recipe (architecture audit finding #4).

The SHA-256-over-canonical-JSON recipe that the manifest-drift veto compares is
spelled at four sites. Two of them — the runtime hash (``loader.compute_config_hash``)
and the frozen-manifest recipe (``manifest._hash_canonical`` over
``canonicalize_config_data(config.raw_data)``) — have **no** same-process guard
asserting they agree; correctness rests on byte-identity by convention. If the
two canonicalization paths ever diverge (different separators, sort, or
canonicalization), every paper+ strategy would fail the manifest-drift veto and
the fleet would halt.

These tests turn that convention into a construction guarantee:
  1. runtime hash == frozen recipe, over every shipped config.
  2. the ``state_machine`` and ``manifest`` recipe copies are byte-identical.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from milodex.promotion.manifest import _hash_canonical as manifest_hash_canonical
from milodex.promotion.state_machine import _hash_canonical as state_machine_hash_canonical
from milodex.strategies.loader import (
    canonicalize_config_data,
    compute_config_hash,
    load_strategy_config,
)

_CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs"
_NON_STRATEGY = frozenset({"risk_defaults.yaml", "sample_strategy.yaml"})


def _shipped_strategy_configs() -> list[Path]:
    paths: list[Path] = []
    for p in sorted(_CONFIGS_DIR.glob("*.yaml")):
        if p.name.startswith("universe_") or p.name in _NON_STRATEGY:
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and "strategy" in data:
            paths.append(p)
    return paths


_CONFIG_PATHS = _shipped_strategy_configs()


def test_there_are_shipped_configs_to_check():
    assert _CONFIG_PATHS, "No strategy configs found under configs/ — check the test helper"


@pytest.mark.parametrize("path", _CONFIG_PATHS, ids=lambda p: p.name)
def test_runtime_hash_equals_frozen_manifest_recipe(path: Path):
    """The runtime config hash must equal the frozen-manifest recipe over the
    same config data — the unguarded byte-identity the drift veto depends on."""
    runtime = compute_config_hash(path)
    config = load_strategy_config(path)
    frozen = manifest_hash_canonical(canonicalize_config_data(config.raw_data))
    assert runtime == frozen, (
        f"{path.name}: runtime hash {runtime[:12]}… != frozen recipe {frozen[:12]}… — "
        "the manifest-drift veto would reject this strategy and halt the fleet"
    )


@pytest.mark.parametrize("path", _CONFIG_PATHS, ids=lambda p: p.name)
def test_state_machine_and_manifest_recipes_are_byte_identical(path: Path):
    """The two ``_hash_canonical`` copies (state_machine, manifest) must agree."""
    canonical = canonicalize_config_data(load_strategy_config(path).raw_data)
    assert state_machine_hash_canonical(canonical) == manifest_hash_canonical(canonical)
