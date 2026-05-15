"""Backtest reproducibility manifest tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock

from milodex.backtesting.run_manifest import (
    BacktestRunManifestInput,
    build_backtest_run_manifest,
)
from milodex.strategies.loader import compute_config_hash

from .test_engine import _make_loaded_strategy, _write_strategy_yaml


def test_run_manifest_includes_universe_manifest_hash(tmp_path):
    config_path = _write_strategy_yaml(tmp_path)
    universe_path = tmp_path / "universe_test.yaml"
    universe_path.write_text(
        """\
universe:
  id: "universe.test.v1"
  etfs: ["SPY", "QQQ"]
  stocks: []
  survivorship_corrected: true
""",
        encoding="utf-8",
    )
    loaded = _make_loaded_strategy("test.strat.v1", ("QQQ", "SPY"), config_path)
    loaded.context = replace(
        loaded.context,
        universe_ref="universe.test.v1",
        config_path=str(config_path),
    )

    manifest = build_backtest_run_manifest(
        BacktestRunManifestInput(
            loaded=loaded,
            data_provider=MagicMock(),
            requested_start=date(2024, 1, 2),
            requested_end=date(2024, 1, 31),
            warmup_start=date(2023, 1, 2),
            risk_policy="bypass",
            slippage_pct=0.0005,
            commission_per_trade=0.0,
            initial_equity=10_000.0,
            data_quality={"status": "pass", "blocker_count": 0, "warning_count": 0},
            coverage_threshold=0.8,
        )
    )

    assert manifest["universe"]["universe_ref"] == "universe.test.v1"
    assert manifest["universe"]["manifest_path"] == str(universe_path)
    assert manifest["universe"]["manifest_hash"] == compute_config_hash(universe_path)
