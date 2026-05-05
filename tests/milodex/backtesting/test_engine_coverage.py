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
        pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"]
        )
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

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def _make_event_store() -> EventStore:
    tmp = tempfile.mktemp(suffix=".db")
    return EventStore(Path(tmp))


def _make_engine(loaded: MagicMock, provider: MagicMock) -> BacktestEngine:
    return BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=_make_event_store(),
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
    # The trailing "..." ellipsis marker must be present.
    assert "..." in msg
