"""Tests for strategy loading and config hashing."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies.base import Strategy, StrategyContext, StrategyParameterSpec
from milodex.strategies.loader import (
    StrategyConfig,
    StrategyLoader,
    StrategyRegistry,
    build_default_registry,
    compute_config_hash,
    load_strategy_config,
    resolve_universe_survivorship_corrected,
)

# ---------------------------------------------------------------------------
# Helpers for the self-discovery guard tests
# ---------------------------------------------------------------------------

_CONFIGS_DIR = Path(__file__).parents[3] / "configs"

# Files that are not per-strategy configs and should be skipped by the guard tests.
_NON_STRATEGY_FILENAMES = frozenset(
    {
        "risk_defaults.yaml",
        "sample_strategy.yaml",
    }
)


def _strategy_yaml_paths() -> list[Path]:
    """Return configs/*.yaml paths that are real (non-sample) strategy configs."""
    paths = []
    for p in sorted(_CONFIGS_DIR.glob("*.yaml")):
        if p.name.startswith("universe_"):
            continue
        if p.name in _NON_STRATEGY_FILENAMES:
            continue
        # Quick structural check: must be a mapping with a 'strategy' key.
        try:
            with p.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and "strategy" in data:
            paths.append(p)
    return paths


class DummyStrategy(Strategy):
    family = "dummy"
    template = "daily.test"
    parameter_specs = (
        StrategyParameterSpec("required_threshold", expected_types=(int, float)),
        StrategyParameterSpec("optional_flag", expected_types=(bool,), required=False),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> list[TradeIntent]:
        return [
            TradeIntent(
                symbol="SPY",
                side=OrderSide.BUY,
                quantity=1.0,
                order_type=OrderType.MARKET,
            )
        ]


@pytest.fixture()
def registry() -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register(DummyStrategy)
    return registry


@pytest.fixture()
def valid_strategy_config(tmp_path: Path) -> Path:
    path = tmp_path / "dummy_strategy.yaml"
    path.write_text(
        """
strategy:
  id: "dummy.daily.test.paper.v1"
  family: "dummy"
  template: "daily.test"
  variant: "paper"
  version: 1
  description: "Dummy strategy for tests."
  enabled: true
  universe:
    - "SPY"
  parameters:
    required_threshold: 10
    optional_flag: true
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional:
    - "manual_test_pause"
""".strip(),
        encoding="utf-8",
    )
    return path


def test_loader_rejects_unknown_strategy_id(tmp_path: Path):
    path = tmp_path / "unknown_strategy.yaml"
    path.write_text(
        """
strategy:
  id: "unknown.daily.test.paper.v1"
  family: "unknown"
  template: "daily.test"
  variant: "paper"
  version: 1
  description: "Unknown strategy."
  enabled: true
  universe:
    - "SPY"
  parameters:
    lookback: 10
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )

    loader = StrategyLoader()

    with pytest.raises(ValueError, match="No strategy is registered"):
        loader.load(path)


def test_loader_rejects_missing_required_params(
    valid_strategy_config: Path, registry: StrategyRegistry
):
    contents = valid_strategy_config.read_text(encoding="utf-8").replace(
        "required_threshold: 10\n",
        "",
    )
    valid_strategy_config.write_text(contents, encoding="utf-8")

    loader = StrategyLoader(registry=registry)

    with pytest.raises(ValueError, match="missing required parameter"):
        loader.load(valid_strategy_config)


def test_identical_configs_hash_identically(tmp_path: Path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text(
        """
strategy:
  id: "dummy.daily.test.paper.v1"
  family: "dummy"
  template: "daily.test"
  variant: "paper"
  version: 1
  description: "Dummy strategy."
  enabled: true
  universe:
    - "SPY"
  parameters:
    required_threshold: 10
    optional_flag: true
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )
    second.write_text(
        """
strategy:
  description: "Dummy strategy."
  version: 1
  variant: "paper"
  family: "dummy"
  id: "dummy.daily.test.paper.v1"
  template: "daily.test"
  enabled: true
  parameters:
    optional_flag: true
    required_threshold: 10
  universe:
    - "SPY"
  risk:
    stop_loss_pct: 0.05
    max_positions: 1
    daily_loss_cap_pct: 0.02
    max_position_pct: 0.10
  tempo:
    max_hold_days: 5
    bar_size: "1D"
    min_hold_days: 1
  stage: "backtest"
  disable_conditions_additional: []
  backtest:
    min_trades_required: 30
    commission_per_trade: 0.00
    slippage_pct: 0.001
""".strip(),
        encoding="utf-8",
    )

    assert compute_config_hash(first) == compute_config_hash(second)


def test_loader_builds_strategy_context(valid_strategy_config: Path, registry: StrategyRegistry):
    loader = StrategyLoader(registry=registry)

    loaded = loader.load(valid_strategy_config)

    assert isinstance(loaded.strategy, DummyStrategy)
    assert isinstance(loaded.config, StrategyConfig)
    assert loaded.config.strategy_id == "dummy.daily.test.paper.v1"
    assert loaded.context.config_hash == compute_config_hash(valid_strategy_config)
    assert loaded.context.disable_conditions == ("manual_test_pause",)
    assert loaded.context.universe == ("SPY",)


def test_loader_accepts_optional_display_name(
    valid_strategy_config: Path, registry: StrategyRegistry
):
    contents = valid_strategy_config.read_text(encoding="utf-8").replace(
        '  description: "Dummy strategy for tests."\n',
        '  display_name: "Dummy Paper Test"\n  description: "Dummy strategy for tests."\n',
    )
    valid_strategy_config.write_text(contents, encoding="utf-8")

    loaded = StrategyLoader(registry=registry).load(valid_strategy_config)

    assert loaded.config.display_name == "Dummy Paper Test"


def test_loader_defaults_missing_display_name_to_none(
    valid_strategy_config: Path, registry: StrategyRegistry
):
    loaded = StrategyLoader(registry=registry).load(valid_strategy_config)

    assert loaded.config.display_name is None


def test_loader_rejects_non_string_display_name(
    valid_strategy_config: Path, registry: StrategyRegistry
):
    contents = valid_strategy_config.read_text(encoding="utf-8").replace(
        '  description: "Dummy strategy for tests."\n',
        '  display_name: 123\n  description: "Dummy strategy for tests."\n',
    )
    valid_strategy_config.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="strategy.display_name must be a non-empty string"):
        StrategyLoader(registry=registry).load(valid_strategy_config)


def test_display_name_does_not_change_config_hash(valid_strategy_config: Path):
    baseline_hash = compute_config_hash(valid_strategy_config)
    contents = valid_strategy_config.read_text(encoding="utf-8").replace(
        '  description: "Dummy strategy for tests."\n',
        '  display_name: "Operator Label"\n  description: "Dummy strategy for tests."\n',
    )
    valid_strategy_config.write_text(contents, encoding="utf-8")

    assert compute_config_hash(valid_strategy_config) == baseline_hash


# --- Survivorship-bias disclosure ---------------------------------------


def _write_universe_manifest(
    tmp_path: Path,
    *,
    universe_id: str,
    survivorship_corrected: bool | None,
) -> Path:
    """Write a minimal universe manifest YAML with optional survivorship flag.

    ``survivorship_corrected=None`` writes a manifest *without* the field, to
    exercise the default-False path.
    """
    lines = [
        "universe:",
        f'  id: "{universe_id}"',
        "  version: 1",
        "  description: 'Test manifest.'",
        "  etfs:",
        '    - "SPY"',
        "  stocks: []",
    ]
    if survivorship_corrected is not None:
        lines.append(f"  survivorship_corrected: {str(survivorship_corrected).lower()}")
    manifest_path = tmp_path / f"universe_{universe_id.replace('.', '_')}.yaml"
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # The lookup APIs take a *config path* whose parent is scanned for
    # universe_*.yaml siblings — so produce a placeholder strategy-side path
    # that lives next to the manifest.
    sibling = tmp_path / "_dummy_strategy.yaml"
    sibling.write_text("placeholder", encoding="utf-8")
    return sibling


def test_resolve_universe_survivorship_corrected_returns_true_when_flag_true(tmp_path: Path):
    sibling = _write_universe_manifest(
        tmp_path, universe_id="universe.etf_only.v1", survivorship_corrected=True
    )
    assert resolve_universe_survivorship_corrected("universe.etf_only.v1", sibling) is True


def test_resolve_universe_survivorship_corrected_returns_false_when_flag_false(tmp_path: Path):
    sibling = _write_universe_manifest(
        tmp_path, universe_id="universe.has_stocks.v1", survivorship_corrected=False
    )
    assert resolve_universe_survivorship_corrected("universe.has_stocks.v1", sibling) is False


def test_resolve_universe_survivorship_corrected_defaults_false_when_field_missing(tmp_path: Path):
    """A manifest predating this field defaults to ``False`` — the conservative
    answer when survivorship status is undeclared."""
    sibling = _write_universe_manifest(
        tmp_path, universe_id="universe.legacy.v1", survivorship_corrected=None
    )
    assert resolve_universe_survivorship_corrected("universe.legacy.v1", sibling) is False


def test_resolve_universe_survivorship_corrected_raises_on_unknown_ref(tmp_path: Path):
    sibling = _write_universe_manifest(
        tmp_path, universe_id="universe.exists.v1", survivorship_corrected=True
    )
    with pytest.raises(ValueError, match="universe_ref 'universe.missing.v1' not found"):
        resolve_universe_survivorship_corrected("universe.missing.v1", sibling)


# ---------------------------------------------------------------------------
# Self-discovery guard tests (PR5)
# ---------------------------------------------------------------------------


def test_every_shipped_config_resolves_in_default_registry():
    """Every real configs/*.yaml strategy must have a registered class.

    This test catches the P5 failure mode: a developer adds a new strategy
    class + YAML config but forgets to register it.  With self-discovery the
    registry builds automatically, but if the new module's class is broken
    (import error, no family/template, abstract) the scan won't find it and
    this test will fail with a clear "resolve returned None" message.
    """
    registry = build_default_registry()
    strategy_paths = _strategy_yaml_paths()
    assert strategy_paths, "No strategy config files found under configs/ — check test helper"
    for path in strategy_paths:
        config = load_strategy_config(path)
        resolved = registry.resolve(config.family, config.template)
        assert resolved is not None, (
            f"{path.name}: no class registered for "
            f"family='{config.family}' template='{config.template}' — "
            "add a concrete Strategy subclass with matching family/template attributes"
        )


def test_default_registry_discovers_all_known_strategy_keys():
    """The self-discovery scan must find every (family, template) key that was
    in the old hand-maintained explicit list.

    The expected set is derived from the real config files so it stays in sync
    automatically.  We assert at least as many entries as there are distinct
    (family, template) pairs across configs, and spot-check a representative
    sample of known keys.
    """
    registry = build_default_registry()

    # Derive expected set from the real config files (ground truth).
    config_keys: set[tuple[str, str]] = set()
    for path in _strategy_yaml_paths():
        config = load_strategy_config(path)
        config_keys.add((config.family, config.template))

    registry_keys = set(registry._strategies.keys())
    missing = config_keys - registry_keys
    assert not missing, (
        f"Registry is missing {len(missing)} key(s) that appear in configs: "
        + ", ".join(f"{fam!r}/{tpl!r}" for fam, tpl in sorted(missing))
    )

    # Registry size >= number of distinct config keys (may have extra classes
    # not yet wired to a config, which is fine).
    assert len(registry._strategies) >= len(config_keys), (
        f"Registry has {len(registry._strategies)} entries but "
        f"configs reference {len(config_keys)} distinct keys"
    )

    # Spot-check a representative subset of well-known strategies.
    known_sample = [
        ("regime", "daily.sma200_rotation"),
        ("seasonality", "daily.turn_of_month"),
        ("meanrev", "daily.ibs_lowclose"),
        ("momentum", "daily.tsmom"),
        ("breakout", "daily.atr_channel"),
        ("scored", "daily.linear_features"),
        ("tree", "daily.bucketed_lookup"),
    ]
    for family, template in known_sample:
        assert registry.resolve(family, template) is not None, (
            f"Expected strategy ({family!r}, {template!r}) not found in registry"
        )


def test_abstract_base_strategy_not_registered():
    """The abstract Strategy base class must not appear in the registry."""
    registry = build_default_registry()
    # Strategy has family='' and template='' (annotations only, no values).
    # Verify it was excluded by confirming no entry with blank keys exists.
    assert registry.resolve("", "") is None, (
        "Abstract Strategy base (family='', template='') was registered — "
        "the filter for empty family/template is broken"
    )
    # Also confirm Strategy itself is not one of the registered values.
    assert Strategy not in registry._strategies.values(), (
        "Abstract Strategy base class appeared as a registered value"
    )


def test_no_duplicate_family_template_keys_in_scan():
    """The default registry scan must contain no duplicate (family, template) pairs.

    This exercises the duplicate-detection branch of build_default_registry():
    if any two concrete classes share a key, that function raises ValueError
    before returning.  A successful return here proves no duplicates exist in
    the real strategy set.
    """
    # If build_default_registry() raises ValueError, the test fails with that
    # error message, which names both offending classes.
    registry = build_default_registry()
    keys = list(registry._strategies.keys())
    assert len(keys) == len(set(keys)), (
        "Duplicate (family, template) keys found in registry — "
        "this should have been caught by build_default_registry()"
    )


def test_registry_excludes_foreign_strategy_subclasses():
    """build_default_registry() must only register classes from milodex.strategies.

    Defines a concrete Strategy subclass with a unique family/template right
    here in the test module, calls build_default_registry(), and asserts the
    foreign class was NOT registered — proving that package-scoping holds even
    when a foreign subclass is loaded in-process.

    This test FAILS if the ``__module__.startswith(pkg_prefix)`` guard in
    build_default_registry() is removed or weakened.
    """
    import milodex.strategies as _pkg

    class _ForeignStrategy(Strategy):
        family = "foreign"
        template = "test.guard"
        parameter_specs = ()

        def evaluate(self, bars, context):  # type: ignore[override]
            return []

    # The foreign class is now a live Strategy subclass in this process.
    assert issubclass(_ForeignStrategy, Strategy)
    # Its module is this test module — NOT under milodex.strategies.
    assert not _ForeignStrategy.__module__.startswith(_pkg.__name__ + ".")

    registry = build_default_registry()
    assert registry.resolve("foreign", "test.guard") is None, (
        "Foreign Strategy subclass (family='foreign', template='test.guard') "
        "was registered by build_default_registry() — the __module__ package-"
        "scoping guard is missing or broken"
    )

    # Sanity: confirm ALL registered classes are from milodex.strategies.
    pkg_prefix = _pkg.__name__ + "."
    for (fam, tpl), cls in registry._strategies.items():
        assert cls.__module__.startswith(pkg_prefix), (
            f"Registered class {cls.__qualname__!r} has module "
            f"{cls.__module__!r} which is outside the milodex.strategies "
            "package — package-scoping guard is broken"
        )


# ---------------------------------------------------------------------------
# stop_loss_pct cross-check (HR-7 / R-P2-1)
# ---------------------------------------------------------------------------


def _make_stop_loss_config(
    tmp_path: Path,
    *,
    risk_stop: object,
    param_stop: object,
) -> Path:
    """Write a minimal strategy YAML with the given stop_loss_pct values.

    ``risk_stop`` goes into ``strategy.risk.stop_loss_pct``;
    ``param_stop`` goes into ``strategy.parameters.stop_loss_pct``.
    Pass ``None`` to omit the field entirely (not write a YAML null).
    """
    risk_line = f"    stop_loss_pct: {risk_stop}" if risk_stop is not None else ""
    param_line = f"    stop_loss_pct: {param_stop}" if param_stop is not None else ""

    yaml_text = f"""
strategy:
  id: "dummy.daily.test.paper.v1"
  family: "dummy"
  template: "daily.test"
  variant: "paper"
  version: 1
  description: "Dummy."
  enabled: true
  universe:
    - "SPY"
  parameters:
    required_threshold: 10
{param_line}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
{risk_line}
  stage: "backtest"
  backtest:
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional: []
""".strip()
    path = tmp_path / "stop_loss_test.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return path


def test_stop_loss_cross_check_passes_when_both_match(tmp_path: Path):
    """Matching values in both risk and parameters: config loads without error."""
    path = _make_stop_loss_config(tmp_path, risk_stop=0.05, param_stop=0.05)
    config = load_strategy_config(path)
    assert config.risk["stop_loss_pct"] == 0.05
    assert config.parameters["stop_loss_pct"] == 0.05


def test_stop_loss_cross_check_raises_on_divergence(tmp_path: Path):
    """risk.stop_loss_pct != parameters.stop_loss_pct → loud ValueError."""
    path = _make_stop_loss_config(tmp_path, risk_stop=0.05, param_stop=0.03)
    with pytest.raises(ValueError, match="stop_loss_pct.*does not match"):
        load_strategy_config(path)


def test_stop_loss_cross_check_no_error_when_only_risk_field_present(tmp_path: Path):
    """Only risk.stop_loss_pct (no parameter twin) → no cross-check, loads fine."""
    path = _make_stop_loss_config(tmp_path, risk_stop=0.05, param_stop=None)
    config = load_strategy_config(path)
    assert config.risk["stop_loss_pct"] == 0.05
    assert "stop_loss_pct" not in config.parameters


def test_risk_stop_loss_pct_is_optional(tmp_path: Path):
    """risk.stop_loss_pct absent (parameters twin present) → loads fine (P2-03)."""
    path = _make_stop_loss_config(tmp_path, risk_stop=None, param_stop=0.05)
    config = load_strategy_config(path)
    assert "stop_loss_pct" not in config.risk
    assert config.parameters["stop_loss_pct"] == 0.05


def test_risk_stop_loss_pct_absent_in_both_sections_loads(tmp_path: Path):
    """Neither risk.stop_loss_pct nor parameters.stop_loss_pct → loads fine (P2-03)."""
    path = _make_stop_loss_config(tmp_path, risk_stop=None, param_stop=None)
    config = load_strategy_config(path)
    assert "stop_loss_pct" not in config.risk
    assert "stop_loss_pct" not in config.parameters
