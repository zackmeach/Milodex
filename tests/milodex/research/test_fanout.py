"""Tests for the per-symbol config generator (A3)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from milodex.research.fanout import generate_per_symbol_configs
from milodex.strategies.loader import load_strategy_config

# Real config files (relative to the project root) copied into tmp_path for isolation.
_CONFIGS_DIR = Path(__file__).parents[3] / "configs"
_BASE_CONFIG = _CONFIGS_DIR / "meanrev_rsi2_intraday_spy_v1.yaml"
_UNIVERSE_MANIFEST = _CONFIGS_DIR / "universe_liquid_etf_core_v1.yaml"


def _setup_tmp(tmp_path: Path) -> Path:
    """Copy base config + universe manifest into tmp_path; return base config path."""
    shutil.copy(_BASE_CONFIG, tmp_path / _BASE_CONFIG.name)
    shutil.copy(_UNIVERSE_MANIFEST, tmp_path / _UNIVERSE_MANIFEST.name)
    return tmp_path / _BASE_CONFIG.name


def test_fanout_generates_one_config_per_non_base_symbol(tmp_path: Path) -> None:
    base = _setup_tmp(tmp_path)
    written = generate_per_symbol_configs(
        base_config_path=base,
        universe_ref="universe.liquid_etf_core.v1",
        out_dir=tmp_path,
    )
    # 17-ETF universe minus the base 'spy' variant == 16 generated
    assert len(written) == 16
    # the base config is never overwritten / collided with
    assert base not in written
    assert all(p.name != base.name for p in written)

    ids = set()
    for path in written:
        cfg = load_strategy_config(path)
        # resolves to exactly one eligible symbol (inline universe → guard already ran)
        assert len(cfg.universe) == 1
        sym = cfg.universe[0]
        assert sym.lower() != "spy"  # base variant skipped
        # id equals {family}.{template}.{variant}.v{version} with variant == symbol
        assert cfg.strategy_id.endswith(f".{sym.lower()}.v{cfg.version}")
        assert cfg.universe_ref is None
        ids.add(cfg.strategy_id)
    # 16 generated + base = 17 unique ids, no double-count under the screen glob
    assert len(ids) == 16
    assert load_strategy_config(base).strategy_id not in ids


def test_fanout_preserves_config_level_slippage(tmp_path: Path) -> None:
    # slippage immunity: each generated config keeps backtest.slippage_pct = 0.0005
    base = _setup_tmp(tmp_path)
    written = generate_per_symbol_configs(
        base_config_path=base,
        universe_ref="universe.liquid_etf_core.v1",
        out_dir=tmp_path,
    )
    for path in written:
        cfg = load_strategy_config(path)
        assert cfg.backtest["slippage_pct"] == 0.0005


def test_fanout_rejects_ineligible_symbol(tmp_path: Path) -> None:
    # a universe_ref pointing at a manifest with a forbidden ETP must raise
    # (proves the generator does not bypass ADR 0016).
    from milodex.strategies.instrument_eligibility import InstrumentEligibilityError

    base = _setup_tmp(tmp_path)
    # Write a tiny manifest containing a leveraged ETP (TQQQ is blocked by ADR 0016)
    bad_manifest = tmp_path / "universe_bad_v1.yaml"
    bad_manifest.write_text(
        'universe:\n  id: "universe.bad.v1"\n  version: 1\n  etfs:\n    - "TQQQ"\n  stocks: []\n',
        encoding="utf-8",
    )
    with pytest.raises(InstrumentEligibilityError):
        generate_per_symbol_configs(
            base_config_path=base,
            universe_ref="universe.bad.v1",
            out_dir=tmp_path,
        )
