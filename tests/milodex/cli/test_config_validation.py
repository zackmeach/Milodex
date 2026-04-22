"""Tests for config validation helpers."""

from __future__ import annotations

from pathlib import Path

from milodex.cli.config_validation import validate_config_file


def test_validate_sample_strategy_config():
    lines = validate_config_file(Path("configs/sample_strategy.yaml"))

    assert "Config validation passed: configs\\sample_strategy.yaml" in lines
    assert "Detected kind: strategy" in lines


def test_validate_phase1_strategy_configs():
    spy_lines = validate_config_file(Path("configs/spy_shy_200dma_v1.yaml"))
    meanrev_lines = validate_config_file(Path("configs/meanrev_daily_rsi2pullback_v1.yaml"))

    assert "Detected kind: strategy" in spy_lines
    assert "Detected kind: strategy" in meanrev_lines
