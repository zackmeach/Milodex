"""Tests for universe-tiered slippage resolution in BacktestEngine.

PR 2.2: slippage resolution order is:
  1. Call-site override (``slippage_pct`` kwarg to BacktestEngine.__init__)
  2. Per-strategy config (``strategy.backtest.slippage_pct`` in the YAML)
  3. Universe manifest (``universe.slippage_pct`` in the matching universe YAML)
  4. Global default (``backtesting.slippage_pct_default`` in risk_defaults.yaml)
  5. Hardcoded fallback 0.0005
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from milodex.backtesting.engine import BacktestEngine
from milodex.core.event_store import EventStore
from milodex.strategies.base import StrategyContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_store() -> EventStore:
    tmp = tempfile.mktemp(suffix=".db")
    return EventStore(Path(tmp))


def _make_loaded(
    *,
    universe_ref: str | None,
    config_dir: Path,
    backtest_slippage: float | None = None,
    universe: tuple[str, ...] = ("SPY",),
) -> MagicMock:
    """Build a minimal mock LoadedStrategy.

    ``config_dir`` is where the strategy YAML would live; the engine uses it to
    scan for ``universe_*.yaml`` files when resolving universe-tier slippage.
    """
    config_path = config_dir / "strategy.yaml"
    # Write a placeholder so Path.exists() calls don't crash if the engine
    # tries to stat the strategy file.
    config_path.write_text("", encoding="utf-8")

    backtest_section: dict = {
        "commission_per_trade": 0.0,
        "min_trades_required": 30,
    }
    if backtest_slippage is not None:
        backtest_section["slippage_pct"] = backtest_slippage

    config = MagicMock()
    config.strategy_id = "test.slippage.v1"
    config.family = "test"
    config.template = "test.template"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = {}
    config.backtest = backtest_section
    config.tempo = {"bar_size": "1D"}
    config.risk = {}
    config.universe = universe

    context = StrategyContext(
        strategy_id="test.slippage.v1",
        family="test",
        template="test.template",
        variant="default",
        version=1,
        config_hash="testslippage",
        parameters={},
        universe=universe,
        universe_ref=universe_ref,
        disable_conditions=(),
        config_path=str(config_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.max_lookback_periods.return_value = 0

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def _write_universe_yaml(configs_dir: Path, universe_id: str, slippage_pct: float | None) -> None:
    """Write a minimal universe manifest into ``configs_dir``."""
    safe_name = universe_id.replace(".", "_").replace(" ", "_")
    manifest_path = configs_dir / f"universe_{safe_name}.yaml"
    data: dict = {"universe": {"id": universe_id, "version": 1, "etfs": ["SPY"], "stocks": []}}
    if slippage_pct is not None:
        data["universe"]["slippage_pct"] = slippage_pct
    manifest_path.write_text(yaml.dump(data), encoding="utf-8")


def _write_risk_defaults(path: Path, slippage_pct_default: float | None = None) -> None:
    """Write a minimal risk_defaults.yaml."""
    backtesting: dict = {"min_universe_coverage_pct": 0.0}
    if slippage_pct_default is not None:
        backtesting["slippage_pct_default"] = slippage_pct_default
    data = {
        "kill_switch": {"enabled": True, "max_drawdown_pct": 0.10, "require_manual_reset": True},
        "portfolio": {
            "max_single_position_pct": 0.10,
            "max_concurrent_positions": 10,
            "max_total_exposure_pct": 0.50,
        },
        "daily_limits": {"max_daily_loss_pct": 0.03, "max_trades_per_day": 20},
        "order_safety": {
            "max_order_value_pct": 0.15,
            "duplicate_order_window_seconds": 60,
            "max_data_staleness_seconds": 300,
        },
        "backtesting": backtesting,
    }
    path.write_text(yaml.dump(data), encoding="utf-8")


def _make_engine(
    loaded: MagicMock,
    *,
    slippage_override: float | None = None,
    risk_defaults_path: Path | None = None,
) -> BacktestEngine:
    provider = MagicMock()
    return BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=_make_event_store(),
        slippage_pct=slippage_override,
        risk_defaults_path=risk_defaults_path,
    )


# ---------------------------------------------------------------------------
# Tier 1 — call-site override beats everything
# ---------------------------------------------------------------------------


def test_tier1_call_site_override_beats_strategy_config():
    """Tier 1 fires: explicit kwarg overrides the strategy YAML value."""
    tmp = Path(tempfile.mkdtemp())
    loaded = _make_loaded(universe_ref=None, config_dir=tmp, backtest_slippage=0.001)

    engine = _make_engine(loaded, slippage_override=0.0002)

    assert engine._slippage_pct == pytest.approx(0.0002)


def test_tier1_override_beats_universe():
    """Tier 1 fires: call-site override beats universe manifest value."""
    tmp = Path(tempfile.mkdtemp())
    _write_universe_yaml(tmp, "universe.test.v1", slippage_pct=0.0003)

    loaded = _make_loaded(universe_ref="universe.test.v1", config_dir=tmp)

    engine = _make_engine(loaded, slippage_override=0.0002)

    assert engine._slippage_pct == pytest.approx(0.0002)


# ---------------------------------------------------------------------------
# Tier 2 — per-strategy config when no call-site override
# ---------------------------------------------------------------------------


def test_tier2_strategy_config_fires_when_no_override():
    """Tier 2 fires: strategy YAML declares slippage_pct; no call-site override."""
    tmp = Path(tempfile.mkdtemp())
    # Universe also has slippage_pct; tier 2 must win.
    _write_universe_yaml(tmp, "universe.test.v1", slippage_pct=0.0003)

    loaded = _make_loaded(universe_ref="universe.test.v1", config_dir=tmp, backtest_slippage=0.0007)

    engine = _make_engine(loaded)

    assert engine._slippage_pct == pytest.approx(0.0007)


# ---------------------------------------------------------------------------
# Tier 3 — universe manifest when strategy config omits slippage_pct
# ---------------------------------------------------------------------------


def test_tier3_universe_manifest_fires():
    """Tier 3 fires: strategy YAML has no slippage_pct; universe declares 0.0003."""
    tmp = Path(tempfile.mkdtemp())
    _write_universe_yaml(tmp, "universe.test.v1", slippage_pct=0.0003)

    # No backtest_slippage → strategy YAML omits slippage_pct.
    loaded = _make_loaded(universe_ref="universe.test.v1", config_dir=tmp)

    engine = _make_engine(loaded)

    assert engine._slippage_pct == pytest.approx(0.0003)


def test_tier3_universe_manifest_0005():
    """Tier 3 fires: universe declares 5 bps (sp100/phase1 tier)."""
    tmp = Path(tempfile.mkdtemp())
    _write_universe_yaml(tmp, "universe.sp100.v1", slippage_pct=0.0005)

    loaded = _make_loaded(universe_ref="universe.sp100.v1", config_dir=tmp)

    engine = _make_engine(loaded)

    assert engine._slippage_pct == pytest.approx(0.0005)


def test_tier3_skipped_for_inline_universe():
    """Tier 3 is skipped when strategy has inline universe (no universe_ref).

    Falls through to tier 4 (global default).
    """
    tmp = Path(tempfile.mkdtemp())
    risk_defaults = tmp / "risk_defaults.yaml"
    _write_risk_defaults(risk_defaults, slippage_pct_default=0.0004)

    # No universe_ref — tier 3 cannot fire.
    loaded = _make_loaded(universe_ref=None, config_dir=tmp)

    engine = _make_engine(loaded, risk_defaults_path=risk_defaults)

    assert engine._slippage_pct == pytest.approx(0.0004)


def test_tier3_skipped_when_universe_has_no_slippage_field():
    """Tier 3 matches the universe but finds no slippage_pct field → falls to tier 4."""
    tmp = Path(tempfile.mkdtemp())
    # Universe YAML exists and matches, but has no slippage_pct field.
    _write_universe_yaml(tmp, "universe.test.v1", slippage_pct=None)
    risk_defaults = tmp / "risk_defaults.yaml"
    _write_risk_defaults(risk_defaults, slippage_pct_default=0.0006)

    loaded = _make_loaded(universe_ref="universe.test.v1", config_dir=tmp)

    engine = _make_engine(loaded, risk_defaults_path=risk_defaults)

    assert engine._slippage_pct == pytest.approx(0.0006)


# ---------------------------------------------------------------------------
# Tier 4 — global risk_defaults.yaml
# ---------------------------------------------------------------------------


def test_tier4_global_default_fires():
    """Tier 4 fires: no override, no strategy config slippage, no universe ref."""
    tmp = Path(tempfile.mkdtemp())
    risk_defaults = tmp / "risk_defaults.yaml"
    _write_risk_defaults(risk_defaults, slippage_pct_default=0.00075)

    loaded = _make_loaded(universe_ref=None, config_dir=tmp)

    engine = _make_engine(loaded, risk_defaults_path=risk_defaults)

    assert engine._slippage_pct == pytest.approx(0.00075)


# ---------------------------------------------------------------------------
# Tier 5 — hardcoded fallback
# ---------------------------------------------------------------------------


def test_tier5_hardcoded_fallback():
    """Tier 5 fires: no override, no strategy slippage, no universe, no risk_defaults.yaml."""
    tmp = Path(tempfile.mkdtemp())

    loaded = _make_loaded(universe_ref=None, config_dir=tmp)

    # Point at a non-existent risk_defaults.yaml.
    nonexistent = tmp / "does_not_exist.yaml"

    engine = _make_engine(loaded, risk_defaults_path=nonexistent)

    assert engine._slippage_pct == pytest.approx(0.0005)


# ---------------------------------------------------------------------------
# Integration: real-shaped strategy config + universe YAML
# ---------------------------------------------------------------------------


def test_integration_strategy_without_slippage_gets_universe_value():
    """Integration: strategy YAML omits slippage_pct; universe declares 0.0003.

    Engine must resolve to 0.0003, and the value must actually flow into
    the slippage_pct field on a returned BacktestResult.
    """
    from datetime import timedelta

    import pandas as pd

    from milodex.data.models import BarSet

    tmp = Path(tempfile.mkdtemp())
    _write_universe_yaml(tmp, "universe.integration.v1", slippage_pct=0.0003)

    loaded = _make_loaded(
        universe_ref="universe.integration.v1",
        config_dir=tmp,
        universe=("SPY",),
    )

    # Provide enough bar data for a minimal run.
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    rows = [
        {
            "timestamp": pd.Timestamp(start + timedelta(days=i), tz="UTC"),
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1000,
            "vwap": 100.0,
        }
        for i in range(2)
    ]
    from milodex.strategies.base import DecisionReasoning, StrategyDecision

    loaded.strategy.evaluate.return_value = StrategyDecision(
        intents=[],
        reasoning=DecisionReasoning(rule="no_signal", narrative="test"),
    )

    barset = BarSet(pd.DataFrame(rows))
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    risk_defaults = tmp / "risk_defaults.yaml"
    _write_risk_defaults(risk_defaults, slippage_pct_default=0.001)

    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=_make_event_store(),
        risk_defaults_path=risk_defaults,
    )

    assert engine._slippage_pct == pytest.approx(0.0003)

    result = engine.run(start, end)
    assert result.slippage_pct == pytest.approx(0.0003)


def test_integration_universe_ref_matches_real_universe_yaml():
    """Integration: use a real universe_*.yaml file as produced in configs/.

    Verifies that the universe-scan logic correctly parses the ``id`` field
    inside the ``universe:`` mapping.
    """
    tmp = Path(tempfile.mkdtemp())

    # Write a realistic universe manifest (same shape as configs/universe_*.yaml).
    manifest = {
        "universe": {
            "id": "universe.sector_etfs_spdr.v1",
            "version": 1,
            "slippage_pct": 0.0003,
            "description": "SPDR sector ETFs",
            "etfs": ["XLB", "XLC", "XLE"],
            "stocks": [],
        }
    }
    (tmp / "universe_sector_etfs_spdr_v1.yaml").write_text(yaml.dump(manifest), encoding="utf-8")

    loaded = _make_loaded(
        universe_ref="universe.sector_etfs_spdr.v1",
        config_dir=tmp,
        universe=("XLB", "XLC", "XLE"),
    )

    engine = _make_engine(loaded)

    assert engine._slippage_pct == pytest.approx(0.0003)
