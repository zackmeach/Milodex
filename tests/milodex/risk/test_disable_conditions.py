"""Disable-condition catalog and risk-layer halt tests (SRS R-STR-014).

Covers: catalog completeness against ``docs/strategy-families.md``, merge
semantics (family defaults + ``disable_conditions_additional``), the
structural impossibility of removing a family default, the
``disable_conditions`` risk check (active → veto with
``disable_condition_active``; inactive → pass; evaluator raising →
fail-closed veto; declared-only never vetoes), and that existing strategy
configs still load with the extended execution-config loader.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from milodex.data.models import Bar
from milodex.execution.config import StrategyExecutionConfig, load_strategy_execution_config
from milodex.risk import RiskEvaluator
from milodex.risk.disable_conditions import (
    ALL_FAMILIES,
    CATALOG,
    ConditionEvaluation,
    DisableCondition,
    default_conditions_for_family,
    effective_disable_conditions,
)

from .test_risk_rules import check_result, make_context

REPO_ROOT = Path(__file__).resolve().parents[3]
FAMILIES_DOC = REPO_ROOT / "docs" / "strategy-families.md"


def _strategy_config(
    family: str = "meanrev",
    additional: tuple[str, ...] = (),
    stage: str = "paper",
) -> StrategyExecutionConfig:
    return StrategyExecutionConfig(
        name=f"{family}_test_strategy",
        enabled=True,
        stage=stage,
        max_position_pct=0.20,
        max_positions=3,
        daily_loss_cap_pct=0.03,
        path=Path("test_strategy.yaml"),
        family=family,
        disable_conditions_additional=additional,
    )


# --- Catalog completeness vs docs/strategy-families.md ----------------------


def _doc_conditions_by_family() -> dict[str, list[str]]:
    """Extract numbered disable-condition prose per family from the doc.

    Tracks the current ``## Family: `name``` heading; collects numbered list
    items under each "Default disable conditions" heading. Template-level
    sections that say "Same eight conditions..." carry no numbered items and
    contribute nothing (they inherit the family block).
    """
    family_re = re.compile(r"^#{2,3} Family: `([a-z_]+)`")
    heading_re = re.compile(r"^#{3,5} Default disable conditions")
    item_re = re.compile(r"^(\d+)\.\s+(.*\S)\s*$")
    conditions: dict[str, list[str]] = {}
    current_family: str | None = None
    in_section = False
    for line in FAMILIES_DOC.read_text(encoding="utf-8").splitlines():
        family_match = family_re.match(line)
        if family_match:
            current_family = family_match.group(1)
            in_section = False
            continue
        if heading_re.match(line):
            in_section = True
            continue
        if in_section:
            item = item_re.match(line)
            if item and current_family is not None:
                conditions.setdefault(current_family, []).append(item.group(2))
            elif line.startswith("#"):
                in_section = False
    return conditions


def test_every_documented_condition_has_a_catalog_entry():
    """R-STR-014: every prose condition in strategy-families.md maps to the catalog."""
    doc_conditions = _doc_conditions_by_family()
    assert doc_conditions, "doc parser found no disable-condition sections — parser broken?"
    descriptions = [condition.description for condition in CATALOG]
    for family, items in doc_conditions.items():
        for prose in items:
            matched = any(
                prose == desc or desc.startswith(prose) or prose.startswith(desc)
                for desc in descriptions
            )
            assert matched, f"{family}: no catalog entry for documented condition '{prose}'"


def test_family_defaults_match_documented_counts():
    """R-STR-014: family default sets mirror the documented per-family catalogs."""
    doc_conditions = _doc_conditions_by_family()
    for family, items in doc_conditions.items():
        defaults = default_conditions_for_family(family)
        assert len(defaults) == len(items), (
            f"{family}: doc lists {len(items)} conditions, catalog defaults provide {len(defaults)}"
        )
        # Every documented prose item must be one of the family's defaults.
        default_descriptions = [condition.description for condition in defaults]
        for prose in items:
            assert any(
                prose == desc or desc.startswith(prose) or prose.startswith(desc)
                for desc in default_descriptions
            ), f"{family}: documented condition '{prose}' missing from family defaults"


def test_condition_ids_are_stable_snake_case_and_unique():
    ids = [condition.condition_id for condition in CATALOG]
    assert len(ids) == len(set(ids))
    for condition_id in ids:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", condition_id), condition_id


def test_unknown_family_gets_exactly_the_universal_subset():
    """Undocumented families (benchmark/scored/tree/sample) get the ALL-owned baseline."""
    for family in ("benchmark", "scored", "tree", "sample", ""):
        defaults = default_conditions_for_family(family)
        assert {c.condition_id for c in defaults} == {
            "data_quality_issue",
            "broker_execution_instability",
            "operator_declared_pause",
        }, family
        for condition in defaults:
            assert ALL_FAMILIES in condition.families


# --- Merge semantics ---------------------------------------------------------


def test_effective_conditions_are_defaults_plus_additional_free_form():
    free_form = "Data cannot identify the entry bar's low for the initial stop."
    effective = effective_disable_conditions("breakout", (free_form,))
    defaults = default_conditions_for_family("breakout")
    assert effective[: len(defaults)] == defaults
    extra = effective[-1]
    assert extra.condition_id == free_form
    assert extra.evaluator is None, "free-form additional strings are declared-only"


def test_additional_string_matching_catalog_id_resolves_to_catalog_entry():
    effective = effective_disable_conditions("regime", ("drawdown_risk_budget_breach",))
    resolved = next(c for c in effective if c.condition_id == "drawdown_risk_budget_breach")
    assert resolved is next(c for c in CATALOG if c.condition_id == "drawdown_risk_budget_breach")
    assert resolved.evaluator is not None


def test_duplicate_additional_entries_are_deduplicated():
    effective = effective_disable_conditions(
        "regime",
        ("operator_declared_pause", "custom condition", "custom condition"),
    )
    ids = [c.condition_id for c in effective]
    assert ids.count("operator_declared_pause") == 1
    assert ids.count("custom condition") == 1


def test_family_defaults_cannot_be_removed_structurally():
    """R-STR-014: the catalog may be extended, not reduced.

    There is no subtraction key in the YAML schema and
    ``effective_disable_conditions`` only ever appends to the family default
    set — removal is structurally inexpressible. This test pins that: no
    ``additional`` input can shrink the default set.
    """
    defaults = default_conditions_for_family("meanrev")
    for additional in ((), ("operator_declared_pause",), ("anything at all",)):
        effective = effective_disable_conditions("meanrev", additional)
        assert set(defaults).issubset(set(effective))
        assert len(effective) >= len(defaults)


# --- Risk-layer halt (RiskEvaluator._check_disable_conditions) ---------------


def test_active_condition_vetoes_with_disable_condition_active():
    """R-STR-014: an active catalog condition halts the strategy with the
    ``disable_condition_active`` reason code naming the condition id."""
    decision = RiskEvaluator().evaluate(
        make_context(strategy_config=_strategy_config(), kill_switch_active=True)
    )
    check = check_result(decision, "disable_conditions")
    assert check.passed is False
    assert check.reason_code == "disable_condition_active"
    assert "operator_declared_pause" in check.message
    assert decision.allowed is False
    assert "disable_condition_active" in decision.reason_codes


def test_stale_bar_activates_data_quality_condition():
    stale_bar = Bar(
        timestamp=datetime.now(tz=UTC) - timedelta(hours=2),
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1_000,
        vwap=100.0,
    )
    decision = RiskEvaluator().evaluate(
        make_context(strategy_config=_strategy_config(), latest_bar=stale_bar)
    )
    check = check_result(decision, "disable_conditions")
    assert check.passed is False
    assert "data_quality_issue" in check.message


def test_drawdown_breach_activates_for_eight_condition_family_only():
    """R-STR-014: drawdown breach is a meanrev/momentum/breakout default,
    not a regime default — the catalog is family-scoped."""
    # ~3.85% loss: above the 3% cap, below the 10% kill-switch threshold.
    meanrev = RiskEvaluator().evaluate(
        make_context(strategy_config=_strategy_config("meanrev"), account_daily_pnl=-400.0)
    )
    check = check_result(meanrev, "disable_conditions")
    assert check.passed is False
    assert "drawdown_risk_budget_breach" in check.message

    regime = RiskEvaluator().evaluate(
        make_context(strategy_config=_strategy_config("regime"), account_daily_pnl=-400.0)
    )
    assert check_result(regime, "disable_conditions").passed is True
    # The account-level daily_loss check still fires — the disable-condition
    # catalog narrows nothing; it only adds the family-scoped halt.
    assert check_result(regime, "daily_loss").passed is False


def test_inactive_conditions_pass_and_report_declared_only_subset():
    decision = RiskEvaluator().evaluate(make_context(strategy_config=_strategy_config()))
    check = check_result(decision, "disable_conditions")
    assert check.passed is True
    assert check.reason_code is None
    # meanrev: 3 auto-evaluated, 5 declared-only.
    assert "3 auto-evaluated inactive" in check.message
    assert "5 declared-only" in check.message
    assert decision.allowed is True


def test_evaluator_raising_fails_closed_with_disable_condition_active(monkeypatch):
    """R-STR-014 fail-closed: a broken safety evaluator must not silently pass."""

    def _boom(context):
        raise RuntimeError("evaluator exploded")

    broken = DisableCondition(
        condition_id="broken_condition",
        description="synthetic broken evaluator",
        families=(ALL_FAMILIES,),
        evaluator=_boom,
    )
    monkeypatch.setattr(
        "milodex.risk.evaluator.effective_disable_conditions",
        lambda family, additional: (broken,),
    )
    decision = RiskEvaluator().evaluate(make_context(strategy_config=_strategy_config()))
    check = check_result(decision, "disable_conditions")
    assert check.passed is False
    assert check.reason_code == "disable_condition_active"
    assert "broken_condition" in check.message
    assert "failing closed" in check.message
    assert decision.allowed is False


def test_declared_only_conditions_never_veto(monkeypatch):
    declared_only = DisableCondition(
        condition_id="spread_liquidity_deterioration",
        description="Significant spread expansion or liquidity deterioration",
        families=(ALL_FAMILIES,),
        evaluator=None,
    )
    monkeypatch.setattr(
        "milodex.risk.evaluator.effective_disable_conditions",
        lambda family, additional: (declared_only,),
    )
    # Even with the kill switch active (which would trip operator_declared_pause
    # were it present), a catalog of only declared-only entries cannot veto.
    decision = RiskEvaluator().evaluate(
        make_context(strategy_config=_strategy_config(), kill_switch_active=True)
    )
    check = check_result(decision, "disable_conditions")
    assert check.passed is True
    assert "1 declared-only" in check.message


def test_manual_trade_without_strategy_is_exempt():
    decision = RiskEvaluator().evaluate(make_context(strategy_config=None))
    check = check_result(decision, "disable_conditions")
    assert check.passed is True
    assert "Manual trade" in check.message


def test_backtest_context_is_exempt():
    """ADR 0030: backtests sit below the risk layer; no disable-condition
    evaluation in backtest contexts."""
    decision = RiskEvaluator().evaluate(
        make_context(
            strategy_config=_strategy_config(),
            is_backtest=True,
            kill_switch_active=True,
        )
    )
    check = check_result(decision, "disable_conditions")
    assert check.passed is True
    assert "backtest" in check.message


def test_active_condition_evaluation_dataclass_shape():
    outcome = ConditionEvaluation(active=True, detail="why")
    assert outcome.active is True
    assert outcome.detail == "why"


# --- Existing configs still load ---------------------------------------------


def test_existing_strategy_configs_load_with_family_and_additional():
    """R-STR-014: family defaults apply WITHOUT config changes — every
    existing strategy YAML loads through the extended execution-config
    loader, carrying its family and free-form additional strings."""
    configs_dir = REPO_ROOT / "configs"
    strategy_paths = []
    for path in sorted(configs_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("strategy"), dict):
            if isinstance(data["strategy"].get("risk"), dict):
                strategy_paths.append(path)
    assert strategy_paths, "no strategy configs found — repo layout changed?"
    for path in strategy_paths:
        config = load_strategy_execution_config(path)
        assert config.family, f"{path.name}: family missing"
        assert isinstance(config.disable_conditions_additional, tuple)
        # Free-form additional strings stay accepted as declared-only.
        for raw in config.disable_conditions_additional:
            effective = effective_disable_conditions(
                config.family, config.disable_conditions_additional
            )
            entry = next(c for c in effective if c.condition_id == raw)
            assert entry.evaluator is None or raw in {c.condition_id for c in CATALOG}


def test_execution_config_defaults_stay_lenient(tmp_path):
    """Legacy YAML without family / disable_conditions_additional still loads."""
    path = tmp_path / "legacy.yaml"
    path.write_text(
        """
strategy:
  name: "legacy"
  enabled: true
  stage: "paper"
  risk:
    max_position_pct: 0.10
    max_positions: 2
    daily_loss_cap_pct: 0.02
""".strip(),
        encoding="utf-8",
    )
    config = load_strategy_execution_config(path)
    assert config.family == ""
    assert config.disable_conditions_additional == ()
    # Empty family resolves to the universal subset, never an error.
    assert default_conditions_for_family(config.family)


def test_loader_reads_bar_size_from_tempo(tmp_path):
    """Loader surfaces strategy.tempo.bar_size onto the execution config."""
    path = tmp_path / "daily.yaml"
    path.write_text(
        """
strategy:
  name: "daily_demo"
  enabled: true
  stage: "paper"
  family: "momentum"
  tempo:
    bar_size: "1D"
  risk:
    max_position_pct: 0.10
    max_positions: 2
    daily_loss_cap_pct: 0.02
""".strip(),
        encoding="utf-8",
    )
    config = load_strategy_execution_config(path)
    assert config.bar_size == "1D"


def test_loader_bar_size_defaults_empty_when_tempo_absent(tmp_path):
    """Legacy YAML with no tempo block yields bar_size == '' (None-safe)."""
    path = tmp_path / "legacy.yaml"
    path.write_text(
        """
strategy:
  name: "legacy"
  enabled: true
  stage: "paper"
  risk:
    max_position_pct: 0.10
    max_positions: 2
    daily_loss_cap_pct: 0.02
""".strip(),
        encoding="utf-8",
    )
    config = load_strategy_execution_config(path)
    assert config.bar_size == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
