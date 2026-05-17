"""Tests for the universe-coverage assertion in BacktestEngine.prefetch_bars.

PR 1.2: refuse to run a backtest when fewer than the configured fraction of
declared-universe symbols have bars in the requested window.
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
import yaml

from milodex.backtesting.engine import BacktestEngine, UniverseCoverageError
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_barset(n_rows: int, start: date) -> BarSet:
    """Return a non-empty BarSet with ``n_rows`` daily bars starting on ``start``."""
    rows = []
    d = start
    for _ in range(n_rows):
        rows.append(
            {
                "timestamp": pd.Timestamp(d, tz="UTC"),
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1_000,
                "vwap": 100.0,
            }
        )
        d += timedelta(days=1)
    return BarSet(pd.DataFrame(rows))


def _empty_barset() -> BarSet:
    """Return an empty BarSet (symbol was returned by the provider but has no rows)."""
    return BarSet(
        pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])
    )


def _make_loaded_strategy(
    universe: tuple[str, ...],
    *,
    strategy_id: str = "test.coverage.v1",
    risk_override: dict | None = None,
) -> MagicMock:
    """Return a minimal mock LoadedStrategy."""
    tmp_dir = Path(tempfile.mkdtemp())
    config_path = tmp_dir / "strategy.yaml"
    config_path.write_text("", encoding="utf-8")

    risk_section: dict = {
        "max_position_pct": 0.10,
        "max_positions": 2,
        "daily_loss_cap_pct": 0.02,
        "stop_loss_pct": 0.05,
    }
    if risk_override:
        risk_section.update(risk_override)

    config = MagicMock()
    config.strategy_id = strategy_id
    config.family = "test"
    config.template = "test.template"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.001, "commission_per_trade": 0.0}
    config.universe = universe
    config.risk = risk_section

    context = StrategyContext(
        strategy_id=strategy_id,
        family="test",
        template="test.template",
        variant="default",
        version=1,
        config_hash="testcoverage",
        parameters={},
        universe=universe,
        universe_ref=None,
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


def _make_event_store() -> EventStore:
    tmp = tempfile.mktemp(suffix=".db")
    return EventStore(Path(tmp))


def _make_engine(
    loaded: MagicMock,
    provider: MagicMock,
    *,
    risk_defaults_path: Path | None = None,
) -> BacktestEngine:
    return BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=_make_event_store(),
        risk_defaults_path=risk_defaults_path,
    )


# Reference window used throughout
_START = date(2024, 1, 2)
_END = date(2024, 1, 31)


# ---------------------------------------------------------------------------
# Step 2 / 5: Coverage below threshold raises UniverseCoverageError
# ---------------------------------------------------------------------------


def test_coverage_below_threshold_raises():
    """50% coverage < 80% default threshold → UniverseCoverageError raised."""
    universe = tuple(f"SYM{i}" for i in range(10))  # 10 declared symbols
    loaded = _make_loaded_strategy(universe)

    # Only 5 symbols get bars; the other 5 are absent from the provider response.
    covered = universe[:5]
    bars = {sym: _make_barset(20, _START) for sym in covered}

    provider = MagicMock()
    provider.get_bars.return_value = bars

    engine = _make_engine(loaded, provider)

    with pytest.raises(UniverseCoverageError) as exc_info:
        engine.prefetch_bars(_START, _END)

    msg = str(exc_info.value)
    # Percentage and count format must be accurate.
    assert "50.0%" in msg, f"Expected '50.0%' in: {msg}"
    assert "80.0%" in msg, f"Expected '80.0%' in: {msg}"
    assert "5/10" in msg, f"Expected '5/10' in: {msg}"
    # At least some missing symbols named.
    assert any(s in msg for s in universe[5:]), f"Expected at least one missing symbol in: {msg}"


def test_coverage_below_threshold_empty_barset_counts_as_missing():
    """A symbol returned by the provider but with zero rows counts as missing."""
    universe = ("SYMA", "SYMB", "SYMC", "SYMD", "SYME", "SYMF", "SYMG", "SYMH", "SYMI", "SYMJ")
    loaded = _make_loaded_strategy(universe)

    # 5 real bars, 5 returned but empty.
    bars = {sym: _make_barset(20, _START) for sym in universe[:5]}
    for sym in universe[5:]:
        bars[sym] = _empty_barset()

    provider = MagicMock()
    provider.get_bars.return_value = bars

    engine = _make_engine(loaded, provider)

    with pytest.raises(UniverseCoverageError) as exc_info:
        engine.prefetch_bars(_START, _END)

    msg = str(exc_info.value)
    assert "50.0%" in msg
    assert "5/10" in msg


def test_coverage_counts_only_requested_window_bars():
    """Warmup-only data must not satisfy coverage for the requested run window."""
    universe = ("SYMA", "SYMB")
    loaded = _make_loaded_strategy(universe)

    bars = {
        "SYMA": _make_barset(20, _START),
        "SYMB": _make_barset(5, _START - timedelta(days=10)),
    }
    provider = MagicMock()
    provider.get_bars.return_value = bars

    engine = _make_engine(loaded, provider)

    with pytest.raises(UniverseCoverageError) as exc_info:
        engine.prefetch_bars(_START, _END)

    msg = str(exc_info.value)
    assert "50.0%" in msg
    assert "1/2" in msg
    assert "SYMB" in msg


def test_prefetch_keeps_warmup_bars_for_symbols_with_window_coverage():
    """Coverage hardening should not strip warmup rows needed by indicators."""
    universe = ("SYMA",)
    loaded = _make_loaded_strategy(universe)

    bars = {"SYMA": _make_barset(20, _START - timedelta(days=5))}
    provider = MagicMock()
    provider.get_bars.return_value = bars

    engine = _make_engine(loaded, provider)

    result = engine.prefetch_bars(_START, _END)

    df = result["SYMA"].to_dataframe()
    assert df["timestamp"].dt.date.min() < _START


# ---------------------------------------------------------------------------
# Step 6: 100% coverage — no error raised
# ---------------------------------------------------------------------------


def test_full_coverage_does_not_raise():
    """100% of declared symbols have bars → no error, bars dict returned."""
    universe = ("SYMA", "SYMB", "SYMC")
    loaded = _make_loaded_strategy(universe)

    bars = {sym: _make_barset(20, _START) for sym in universe}
    provider = MagicMock()
    provider.get_bars.return_value = bars

    engine = _make_engine(loaded, provider)

    result = engine.prefetch_bars(_START, _END)
    assert set(result.keys()) == set(universe)


# ---------------------------------------------------------------------------
# Step 7: Per-strategy override permits lower coverage
# ---------------------------------------------------------------------------


def test_strategy_override_permits_lower_coverage():
    """Strategy sets min_universe_coverage_pct: 0.50; 60% coverage passes."""
    universe = tuple(f"SYM{i}" for i in range(10))  # 10 symbols declared
    loaded = _make_loaded_strategy(universe, risk_override={"min_universe_coverage_pct": 0.50})

    # 6 of 10 symbols have bars → 60% coverage, above the 50% override.
    covered = universe[:6]
    bars = {sym: _make_barset(20, _START) for sym in covered}

    provider = MagicMock()
    provider.get_bars.return_value = bars

    engine = _make_engine(loaded, provider)

    # Must not raise.
    result = engine.prefetch_bars(_START, _END)
    assert len(result) == 6


def test_strategy_override_at_zero_permits_any_coverage():
    """Strategy sets min_universe_coverage_pct: 0.0; even 0% coverage passes."""
    universe = ("SYMA", "SYMB", "SYMC")
    loaded = _make_loaded_strategy(universe, risk_override={"min_universe_coverage_pct": 0.0})

    # No symbols have bars.
    provider = MagicMock()
    provider.get_bars.return_value = {}

    engine = _make_engine(loaded, provider)

    result = engine.prefetch_bars(_START, _END)
    assert result == {}


# ---------------------------------------------------------------------------
# Error message format validation (matches plan spec)
# ---------------------------------------------------------------------------


def test_error_message_format():
    """Error message matches the documented format with pct and counts."""
    universe = ("SYMA", "SYMB", "SYMC", "SYMD", "SYME")
    loaded = _make_loaded_strategy(universe)

    # 0 of 5 covered → 0.0%
    provider = MagicMock()
    provider.get_bars.return_value = {}

    engine = _make_engine(loaded, provider)

    with pytest.raises(UniverseCoverageError) as exc_info:
        engine.prefetch_bars(_START, _END)

    msg = str(exc_info.value)
    assert "0.0%" in msg
    assert "80.0%" in msg
    assert "0/5" in msg
    # 5 missing symbols → list is NOT truncated, so "..." must be absent.
    assert "..." not in msg, f"Unexpected '...' in message with short missing list: {msg}"


def test_error_message_suffix_absent_when_list_short():
    """'...' suffix is absent when 10 or fewer symbols are missing."""
    universe = tuple(f"SYM{i:02d}" for i in range(10))  # exactly 10 symbols
    loaded = _make_loaded_strategy(universe)

    # All 10 are missing.
    provider = MagicMock()
    provider.get_bars.return_value = {}

    engine = _make_engine(loaded, provider)

    with pytest.raises(UniverseCoverageError) as exc_info:
        engine.prefetch_bars(_START, _END)

    msg = str(exc_info.value)
    # Exactly 10 missing → not truncated → no suffix.
    assert "..." not in msg, f"Unexpected '...' with exactly 10 missing: {msg}"


def test_error_message_suffix_present_when_list_truncated():
    """'...' suffix is present when more than 10 symbols are missing."""
    universe = tuple(f"SYM{i:02d}" for i in range(15))  # 15 symbols
    loaded = _make_loaded_strategy(universe)

    # All 15 are missing.
    provider = MagicMock()
    provider.get_bars.return_value = {}

    engine = _make_engine(loaded, provider)

    with pytest.raises(UniverseCoverageError) as exc_info:
        engine.prefetch_bars(_START, _END)

    msg = str(exc_info.value)
    # 15 missing → truncated to 10 → suffix must be present.
    assert "..." in msg, f"Expected '...' with 15 missing symbols: {msg}"


# ---------------------------------------------------------------------------
# Tier-2: global risk_defaults.yaml as second tier of threshold resolution
# ---------------------------------------------------------------------------


def _write_risk_defaults_yaml(path: Path, coverage_pct: float) -> None:
    """Write a minimal risk_defaults.yaml with only the backtesting section."""
    data = {
        "kill_switch": {
            "enabled": True,
            "max_drawdown_pct": 0.10,
            "require_manual_reset": True,
        },
        "portfolio": {
            "max_single_position_pct": 0.10,
            "max_concurrent_positions": 10,
            "max_total_exposure_pct": 0.50,
        },
        "daily_limits": {
            "max_daily_loss_pct": 0.03,
            "max_trades_per_day": 20,
        },
        "order_safety": {
            "max_order_value_pct": 0.15,
            "duplicate_order_window_seconds": 60,
            "max_data_staleness_seconds": 300,
        },
        "backtesting": {
            "min_universe_coverage_pct": coverage_pct,
        },
    }
    path.write_text(yaml.dump(data), encoding="utf-8")


def test_tier2_global_default_fires_when_strategy_has_no_override():
    """Tier 2 fires: strategy has no override → global default (0.95) is used.

    Universe has 90% coverage.  With global default = 0.95, 0.90 < 0.95
    so UniverseCoverageError must be raised.
    """
    universe = tuple(f"SYM{i:02d}" for i in range(10))  # 10 symbols
    # Strategy does NOT set min_universe_coverage_pct.
    loaded = _make_loaded_strategy(universe)

    # 9 of 10 symbols covered → 90% coverage.
    covered = universe[:9]
    bars = {sym: _make_barset(20, _START) for sym in covered}
    provider = MagicMock()
    provider.get_bars.return_value = bars

    # Write a risk_defaults.yaml that sets global threshold to 95%.
    tmp_dir = Path(tempfile.mkdtemp())
    risk_path = tmp_dir / "risk_defaults.yaml"
    _write_risk_defaults_yaml(risk_path, 0.95)

    engine = _make_engine(loaded, provider, risk_defaults_path=risk_path)

    with pytest.raises(UniverseCoverageError) as exc_info:
        engine.prefetch_bars(_START, _END)

    msg = str(exc_info.value)
    # Should report 90% coverage vs 95% threshold from tier 2.
    assert "90.0%" in msg, f"Expected '90.0%' in: {msg}"
    assert "95.0%" in msg, f"Expected '95.0%' in: {msg}"


def test_tier2_global_default_passes_when_coverage_meets_it():
    """Tier 2 fires: global default = 0.80; 90% coverage passes."""
    universe = tuple(f"SYM{i:02d}" for i in range(10))
    loaded = _make_loaded_strategy(universe)

    # 9 of 10 symbols covered → 90% coverage.
    covered = universe[:9]
    bars = {sym: _make_barset(20, _START) for sym in covered}
    provider = MagicMock()
    provider.get_bars.return_value = bars

    tmp_dir = Path(tempfile.mkdtemp())
    risk_path = tmp_dir / "risk_defaults.yaml"
    _write_risk_defaults_yaml(risk_path, 0.80)

    engine = _make_engine(loaded, provider, risk_defaults_path=risk_path)

    # 90% > 80% global default → must not raise.
    result = engine.prefetch_bars(_START, _END)
    assert len(result) == 9


def test_tier1_strategy_override_takes_precedence_over_tier2():
    """Tier 1 wins: strategy sets 0.50; global default is 0.95; 60% passes."""
    universe = tuple(f"SYM{i:02d}" for i in range(10))
    # Strategy overrides to 50%.
    loaded = _make_loaded_strategy(universe, risk_override={"min_universe_coverage_pct": 0.50})

    # 6 of 10 → 60% coverage, which is above 50% (tier 1) but below 95% (tier 2).
    covered = universe[:6]
    bars = {sym: _make_barset(20, _START) for sym in covered}
    provider = MagicMock()
    provider.get_bars.return_value = bars

    tmp_dir = Path(tempfile.mkdtemp())
    risk_path = tmp_dir / "risk_defaults.yaml"
    _write_risk_defaults_yaml(risk_path, 0.95)

    engine = _make_engine(loaded, provider, risk_defaults_path=risk_path)

    # Must NOT raise — tier 1 (50%) is used, not tier 2 (95%).
    result = engine.prefetch_bars(_START, _END)
    assert len(result) == 6


def test_tier3_fallback_used_when_risk_defaults_file_absent():
    """Tier 3 fires: no risk_defaults.yaml exists → fallback 0.80 is used.

    50% coverage < 80% fallback → raises.
    """
    universe = tuple(f"SYM{i:02d}" for i in range(10))
    loaded = _make_loaded_strategy(universe)

    covered = universe[:5]
    bars = {sym: _make_barset(20, _START) for sym in covered}
    provider = MagicMock()
    provider.get_bars.return_value = bars

    # Point at a non-existent file — tier 3 should kick in.
    nonexistent = Path(tempfile.mkdtemp()) / "does_not_exist.yaml"

    engine = _make_engine(loaded, provider, risk_defaults_path=nonexistent)

    with pytest.raises(UniverseCoverageError) as exc_info:
        engine.prefetch_bars(_START, _END)

    msg = str(exc_info.value)
    assert "80.0%" in msg, f"Expected tier-3 fallback 80.0% in: {msg}"
