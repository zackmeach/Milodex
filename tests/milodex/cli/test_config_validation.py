"""Tests for config validation helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from milodex.cli.config_validation import validate_config_file


def test_validate_sample_strategy_config():
    path = Path("configs/sample_strategy.yaml")
    lines = validate_config_file(path)

    assert f"Config validation passed: {path}" in lines
    assert "Detected kind: strategy" in lines


def test_validate_phase1_strategy_configs():
    spy_lines = validate_config_file(Path("configs/spy_shy_200dma_v1.yaml"))
    meanrev_lines = validate_config_file(Path("configs/meanrev_daily_rsi2pullback_v1.yaml"))

    assert "Detected kind: strategy" in spy_lines
    assert "Detected kind: strategy" in meanrev_lines


def test_validate_rejects_directory_path_with_valueerror(tmp_path):
    """A directory passed where a config file is expected raises a clean ValueError
    (not a raw IsADirectoryError/PermissionError). FIX-1: is_file() guard."""
    with pytest.raises(ValueError, match="not a file"):
        validate_config_file(tmp_path)


def test_validate_rejects_malformed_yaml_with_valueerror(tmp_path):
    """Malformed YAML raises a clean ValueError chaining the original YAMLError,
    not a raw PyYAML traceback. FIX-1: wrap yaml.safe_load."""
    bad = tmp_path / "broken.yaml"
    bad.write_text("key: [unclosed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid YAML") as excinfo:
        validate_config_file(bad)

    assert isinstance(excinfo.value.__cause__, yaml.YAMLError)
