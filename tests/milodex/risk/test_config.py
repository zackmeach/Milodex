"""Tests for milodex.risk.config — absolute ceilings and risk-profile loader.

Per ADR 0054: code-level ceilings are not YAML-editable; Conservative is the
safe default; ceiling violations refuse startup (CeilingViolationError); unknown
profile names fall back to Conservative with a WARNING log.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest
import yaml

# Resolve repo root at import time (before any monkeypatch.chdir changes cwd).
# Path(__file__) may be relative when pytest collects; resolve() makes it absolute
# while cwd is still the repo root (the default pytest invocation directory).
_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Task 26: _ABSOLUTE_CEILINGS constants
# ---------------------------------------------------------------------------


def test_absolute_ceilings_defined():
    from milodex.risk.config import _ABSOLUTE_CEILINGS

    assert "kill_switch.max_drawdown_pct" in _ABSOLUTE_CEILINGS
    assert "portfolio.max_total_exposure_pct" in _ABSOLUTE_CEILINGS
    assert "daily_limits.max_daily_loss_pct" in _ABSOLUTE_CEILINGS
    # Verify against documented justification (ADR 0054 §4):
    assert _ABSOLUTE_CEILINGS["kill_switch.max_drawdown_pct"] == 0.20
    assert _ABSOLUTE_CEILINGS["portfolio.max_total_exposure_pct"] == 0.85
    assert _ABSOLUTE_CEILINGS["daily_limits.max_daily_loss_pct"] == 0.08


def test_ceiling_violation_is_runtime_error():
    from milodex.risk.config import CeilingViolationError

    assert issubclass(CeilingViolationError, RuntimeError)


def test_all_three_shipped_profiles_pass_ceiling_validation():
    """Aggressive.yaml MUST be within ceilings; guards against accidentally
    raising aggressive past the documented absolute maxima."""
    from milodex.risk.config import _load_overlay, _merge, _validate_against_ceilings

    base_path = Path("configs/risk_defaults.yaml")
    with open(base_path) as f:
        base = yaml.safe_load(f)

    for profile_name in ["conservative", "standard", "aggressive"]:
        overlay = _load_overlay(profile_name)
        merged = _merge(base, overlay)
        _validate_against_ceilings(merged)  # raises CeilingViolationError if bad


# ---------------------------------------------------------------------------
# Task 27: loader fallback semantics
# ---------------------------------------------------------------------------


def test_default_to_conservative_when_file_absent(tmp_path, monkeypatch, caplog):
    """ADR 0054 §2: missing data/risk_profile.txt → silently default to conservative."""
    monkeypatch.setenv("MILODEX_DATA_DIR", str(tmp_path / "data"))
    with caplog.at_level(logging.WARNING, logger="milodex.risk.config"):
        from milodex.risk.config import get_active_profile_name

        result = get_active_profile_name()
    assert result == "conservative"
    # Silent-default invariant: no WARNING when file is simply absent (ADR §2).
    assert caplog.records == [], (
        f"Silent-default path should emit no warnings; got: {[r.message for r in caplog.records]}"
    )


def test_malformed_profile_falls_back_to_conservative_with_warning(
    tmp_path, monkeypatch, caplog
):
    """Unknown profile name → fallback + loud warning."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "risk_profile.txt").write_text("nonexistent_profile\n")

    # load_active_risk_profile needs configs/ available — copy the real ones in
    import shutil

    real_root = _REPO_ROOT
    shutil.copytree(real_root / "configs", tmp_path / "configs")

    with caplog.at_level(logging.WARNING, logger="milodex.risk.config"):
        from milodex.risk.config import load_active_risk_profile

        profile = load_active_risk_profile()

    # Fell back to conservative
    assert profile.kill_switch_max_drawdown_pct == 0.05
    # Warning emitted
    assert any(
        "malformed" in rec.message.lower() or "unknown" in rec.message.lower()
        for rec in caplog.records
    )


def test_malformed_non_conservative_overlay_falls_back_to_conservative(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MILODEX_DATA_DIR", str(tmp_path / "data"))
    shutil.copytree(_REPO_ROOT / "configs", tmp_path / "configs")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "risk_profile.txt").write_text("standard\n", encoding="utf-8")
    (tmp_path / "configs" / "risk_profiles" / "standard.yaml").write_text(
        "kill_switch:\n  max_drawdown_pct: [\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="milodex.risk.config"):
        from milodex.risk.config import load_active_risk_profile

        profile = load_active_risk_profile(tmp_path / "configs" / "risk_defaults.yaml")

    assert profile.kill_switch_max_drawdown_pct == 0.05
    assert profile.max_total_exposure_pct == 0.30
    assert any("falling back to conservative" in rec.message.lower() for rec in caplog.records)


def test_malformed_conservative_overlay_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MILODEX_DATA_DIR", str(tmp_path / "data"))
    shutil.copytree(_REPO_ROOT / "configs", tmp_path / "configs")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "risk_profile.txt").write_text("conservative\n", encoding="utf-8")
    (tmp_path / "configs" / "risk_profiles" / "conservative.yaml").write_text(
        "kill_switch:\n  max_drawdown_pct: [\n",
        encoding="utf-8",
    )

    from milodex.risk.config import load_active_risk_profile

    with pytest.raises(RuntimeError, match="Conservative.*overlay.*malformed"):
        load_active_risk_profile(tmp_path / "configs" / "risk_defaults.yaml")


def test_ceiling_violation_refuses_startup(tmp_path, monkeypatch):
    """Profile resolving above a ceiling → CeilingViolationError raised (refuse startup)."""
    monkeypatch.chdir(tmp_path)

    import shutil

    real_root = _REPO_ROOT
    # Copy real configs/ but override with a bad profile
    shutil.copytree(real_root / "configs", tmp_path / "configs")
    bad_profile_dir = tmp_path / "configs" / "risk_profiles"
    bad_profile_dir.mkdir(parents=True, exist_ok=True)
    (bad_profile_dir / "badprofile.yaml").write_text(
        "kill_switch:\n  max_drawdown_pct: 0.99\n"  # exceeds ceiling 0.20
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "risk_profile.txt").write_text("badprofile\n")

    # The bridge allowlist won't include 'badprofile', so the loader will fall back
    # to conservative — we need to test _validate_against_ceilings directly for a
    # crafted merged dict that breaches the ceiling.
    from milodex.risk.config import CeilingViolationError, _validate_against_ceilings

    bad_merged = {
        "kill_switch": {"max_drawdown_pct": 0.99, "enabled": True, "require_manual_reset": True},
        "portfolio": {"max_total_exposure_pct": 0.50, "max_single_position_pct": 0.10,
                      "max_concurrent_positions": 10},
        "daily_limits": {"max_daily_loss_pct": 0.03, "max_trades_per_day": 20},
        "order_safety": {"max_order_value_pct": 0.15, "duplicate_order_window_seconds": 60,
                         "max_data_staleness_seconds": 300},
    }
    with pytest.raises(CeilingViolationError):
        _validate_against_ceilings(bad_merged)


def test_load_active_risk_profile_returns_risk_defaults_instance(tmp_path, monkeypatch):
    """load_active_risk_profile() returns a RiskDefaults instance (ADR 0054 §9)."""
    monkeypatch.chdir(tmp_path)

    import shutil

    real_root = _REPO_ROOT
    shutil.copytree(real_root / "configs", tmp_path / "configs")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "risk_profile.txt").write_text("conservative\n")

    from milodex.risk.config import RiskDefaults, load_active_risk_profile

    result = load_active_risk_profile()
    assert isinstance(result, RiskDefaults)


def test_conservative_profile_values_are_tighter_than_base(tmp_path, monkeypatch):
    """Conservative overlay tightens the key limits relative to risk_defaults.yaml."""
    monkeypatch.chdir(tmp_path)

    import shutil

    real_root = _REPO_ROOT
    shutil.copytree(real_root / "configs", tmp_path / "configs")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "risk_profile.txt").write_text("conservative\n")

    from milodex.risk.config import load_active_risk_profile, load_risk_defaults

    conservative = load_active_risk_profile()
    base = load_risk_defaults(tmp_path / "configs" / "risk_defaults.yaml")

    assert conservative.kill_switch_max_drawdown_pct < base.kill_switch_max_drawdown_pct
    assert conservative.max_total_exposure_pct < base.max_total_exposure_pct
    assert conservative.max_daily_loss_pct < base.max_daily_loss_pct
