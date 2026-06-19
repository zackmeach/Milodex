"""Smoke tests for ``research fan-out`` CLI subcommand."""

from __future__ import annotations

import shutil
from pathlib import Path

from milodex.cli.commands import research

_CONFIGS_DIR = Path(__file__).parents[3] / "configs"
_BASE_CONFIG = _CONFIGS_DIR / "meanrev_rsi2_intraday_spy_v1.yaml"
_UNIVERSE_MANIFEST = _CONFIGS_DIR / "universe_liquid_etf_core_v1.yaml"


def _setup_tmp(tmp_path: Path) -> Path:
    """Copy base config + universe manifest into tmp_path; return base config path."""
    shutil.copy(_BASE_CONFIG, tmp_path / _BASE_CONFIG.name)
    shutil.copy(_UNIVERSE_MANIFEST, tmp_path / _UNIVERSE_MANIFEST.name)
    return tmp_path / _BASE_CONFIG.name


def test_fanout_cli_writes_16_configs_and_exits_cleanly(tmp_path: Path, monkeypatch) -> None:
    """fan-out via --config path writes 16 files and returns exit-0 CommandResult."""
    base = _setup_tmp(tmp_path)
    out_dir = tmp_path / "out"

    import argparse

    args = argparse.Namespace(
        research_command="fan-out",
        fanout_strategy_id=None,
        fanout_config=str(base),
        fanout_universe_ref="universe.liquid_etf_core.v1",
        fanout_out=str(out_dir),
    )

    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.config_dir = tmp_path

    result = research.run(args, ctx)

    assert result.data["generated_count"] == 16
    assert len(list(out_dir.glob("*.yaml"))) == 16
    # base config is not in the output dir
    assert not (out_dir / base.name).exists()
