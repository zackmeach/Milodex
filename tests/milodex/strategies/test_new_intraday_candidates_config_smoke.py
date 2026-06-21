"""Config smoke tests for the three new intraday candidate strategies (phase-2).

Each test:
 - loads the SPY base config via load_strategy_config
 - resolves (family, template) via build_default_registry
 - asserts the strategy id equals ``{family}.{template}.spy.v1``
 - picks one fanned config and asserts it resolves to exactly one eligible symbol
"""

from __future__ import annotations

from pathlib import Path

from milodex.strategies.breakout_opening_range_retest_intraday import (
    BreakoutOpeningRangeRetestIntradayStrategy,
)
from milodex.strategies.gap_continuation_intraday import GapContinuationIntradayStrategy
from milodex.strategies.loader import build_default_registry, load_strategy_config
from milodex.strategies.momentum_late_session_intraday import (
    MomentumLateSessionIntradayStrategy,
)

_CONFIGS = Path(__file__).resolve().parents[3] / "configs"


# ---------------------------------------------------------------------------
# S1 — gap continuation
# ---------------------------------------------------------------------------


def test_gap_continuation_spy_config_loads() -> None:
    config = load_strategy_config(_CONFIGS / "gap_continuation_intraday_spy_v1.yaml")
    assert config.family == "gap"
    assert config.template == "gap_continuation.intraday"
    assert config.strategy_id == "gap.gap_continuation.intraday.spy.v1"


def test_gap_continuation_registry_resolves() -> None:
    registry = build_default_registry()
    cls = registry.resolve("gap", "gap_continuation.intraday")
    assert cls is GapContinuationIntradayStrategy


def test_gap_continuation_fanned_qqq_resolves_one_symbol() -> None:
    config = load_strategy_config(_CONFIGS / "gap_continuation_intraday_qqq_v1.yaml")
    # Fanned configs embed an inline universe list rather than a universe_ref.
    assert config.universe is not None
    assert len(config.universe) == 1
    assert "QQQ" in config.universe


# ---------------------------------------------------------------------------
# S2 — late-session momentum
# ---------------------------------------------------------------------------


def test_late_session_spy_config_loads() -> None:
    config = load_strategy_config(_CONFIGS / "momentum_late_session_intraday_spy_v1.yaml")
    assert config.family == "momentum"
    assert config.template == "late_session.intraday"
    assert config.strategy_id == "momentum.late_session.intraday.spy.v1"


def test_late_session_registry_resolves() -> None:
    registry = build_default_registry()
    cls = registry.resolve("momentum", "late_session.intraday")
    assert cls is MomentumLateSessionIntradayStrategy


def test_late_session_fanned_qqq_resolves_one_symbol() -> None:
    config = load_strategy_config(_CONFIGS / "momentum_late_session_intraday_qqq_v1.yaml")
    # Fanned configs embed an inline universe list rather than a universe_ref.
    assert config.universe is not None
    assert len(config.universe) == 1
    assert "QQQ" in config.universe


# ---------------------------------------------------------------------------
# S3 — opening-range retest
# ---------------------------------------------------------------------------


def test_orb_retest_spy_config_loads() -> None:
    config = load_strategy_config(_CONFIGS / "breakout_opening_range_retest_intraday_spy_v1.yaml")
    assert config.family == "breakout"
    assert config.template == "opening_range_retest.intraday"
    assert config.strategy_id == "breakout.opening_range_retest.intraday.spy.v1"


def test_orb_retest_registry_resolves() -> None:
    registry = build_default_registry()
    cls = registry.resolve("breakout", "opening_range_retest.intraday")
    assert cls is BreakoutOpeningRangeRetestIntradayStrategy


def test_orb_retest_fanned_qqq_resolves_one_symbol() -> None:
    config = load_strategy_config(_CONFIGS / "breakout_opening_range_retest_intraday_qqq_v1.yaml")
    # Fanned configs embed an inline universe list rather than a universe_ref.
    assert config.universe is not None
    assert len(config.universe) == 1
    assert "QQQ" in config.universe
