"""Error-path tests for ``load_strategy_execution_config``.

The happy path (legacy-lenient defaults, family/disable_conditions_additional,
tempo.bar_size) is already covered in
``tests/milodex/risk/test_disable_conditions.py`` (lines 331-419). This module
pins the loader's failure modes: missing file, non-mapping YAML root,
non-mapping ``strategy.risk`` section, and a missing required key.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from milodex.execution.config import load_strategy_execution_config


def test_missing_file_raises_value_error(tmp_path: Path) -> None:
    """A nonexistent path raises ValueError (config.py lines 76-78)."""
    path = tmp_path / "does_not_exist.yaml"

    with pytest.raises(ValueError, match="does not exist"):
        load_strategy_execution_config(path)


def test_non_mapping_yaml_root_raises_value_error(tmp_path: Path) -> None:
    """A YAML document whose root is not a mapping raises ValueError (lines 83-85)."""
    path = tmp_path / "list_root.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be a mapping"):
        load_strategy_execution_config(path)


def test_non_mapping_strategy_risk_section_raises_value_error(tmp_path: Path) -> None:
    """A non-mapping ``strategy.risk`` section raises ValueError via ``_mapping``."""
    path = tmp_path / "bad_risk.yaml"
    path.write_text(
        """
strategy:
  name: "bad"
  enabled: true
  stage: "paper"
  risk: "not_a_mapping"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="strategy.risk must be a mapping"):
        load_strategy_execution_config(path)


def test_missing_required_key_raises_key_error(tmp_path: Path) -> None:
    """A missing required key (e.g. ``enabled``) currently raises a raw KeyError,
    not a clean ValueError. Pinning this today's behavior — NOT a design
    endorsement; a friendlier error would be a production-code change out of
    scope for this test-only addition."""
    path = tmp_path / "missing_enabled.yaml"
    path.write_text(
        """
strategy:
  name: "incomplete"
  stage: "paper"
  risk:
    max_position_pct: 0.10
    max_positions: 2
    daily_loss_cap_pct: 0.02
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(KeyError, match="enabled"):
        load_strategy_execution_config(path)
