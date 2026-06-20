"""Bijection tests for the 3 H-base intraday configs (Bug 1 regression).

Verifies that:
1. Fan-out of each base against universe.liquid_etf_core.v1 covers all 17
   universe symbols exactly once (SPY present, no duplicate DIA).
2. Loading the base config resolves to a single-symbol SPY universe (not DIA).
3. ORB partial-range guard: strategy returns no-signal when an opening-range
   bar is missing, and still enters on a complete range (Bug 2 regression).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from milodex.data.models import BarSet
from milodex.research.fanout import generate_per_symbol_configs
from milodex.strategies.base import StrategyContext
from milodex.strategies.breakout_opening_range_retest_intraday import (
    BreakoutOpeningRangeRetestIntradayStrategy,
)
from milodex.strategies.loader import load_strategy_config, resolve_universe_ref

_CONFIGS_DIR = Path(__file__).parents[3] / "configs"
_UNIVERSE_MANIFEST = _CONFIGS_DIR / "universe_liquid_etf_core_v1.yaml"
_UNIVERSE_SPY_MANIFEST = _CONFIGS_DIR / "universe_spy_only_v1.yaml"

_UNIVERSE_REF = "universe.liquid_etf_core.v1"
_EXPECTED_UNIVERSE_SIZE = 17

# The 3 base configs under test.
_H_BASES = [
    _CONFIGS_DIR / "gap_continuation_intraday_spy_v1.yaml",
    _CONFIGS_DIR / "momentum_late_session_intraday_spy_v1.yaml",
    _CONFIGS_DIR / "breakout_opening_range_retest_intraday_spy_v1.yaml",
]


def _setup_tmp(tmp_path: Path, base_path: Path) -> Path:
    """Copy base config + both universe manifests into tmp_path; return base path copy."""
    shutil.copy(base_path, tmp_path / base_path.name)
    shutil.copy(_UNIVERSE_MANIFEST, tmp_path / _UNIVERSE_MANIFEST.name)
    shutil.copy(_UNIVERSE_SPY_MANIFEST, tmp_path / _UNIVERSE_SPY_MANIFEST.name)
    return tmp_path / base_path.name


@pytest.mark.parametrize("base_path", _H_BASES, ids=[p.stem for p in _H_BASES])
def test_h_base_config_resolves_to_spy_not_dia(base_path: Path) -> None:
    """Base config universe_ref resolves to [SPY], not [DIA] or any other symbol.

    A universe_ref-based config leaves ``.universe`` empty at load time (resolved
    into the StrategyContext at runtime), so assert the *resolved* ref, matching
    every other single-symbol intraday config (null, rsi2).
    """
    cfg = load_strategy_config(base_path)
    assert resolve_universe_ref(cfg.universe_ref, base_path) == ("SPY",), (
        f"{base_path.name}: expected universe_ref to resolve to ('SPY',), got "
        f"ref={cfg.universe_ref!r} -> {resolve_universe_ref(cfg.universe_ref, base_path)!r}"
    )


@pytest.mark.parametrize("base_path", _H_BASES, ids=[p.stem for p in _H_BASES])
def test_fanout_bijection_covers_all_17_symbols_exactly_once(
    tmp_path: Path, base_path: Path
) -> None:
    """Fan-out + base together cover all 17 universe symbols, each exactly once.

    SPY must be present (via the base), DIA must appear exactly once (in the
    generated set), and no symbol appears in both the base and a generated config.
    """
    base = _setup_tmp(tmp_path, base_path)
    written = generate_per_symbol_configs(
        base_config_path=base,
        universe_ref=_UNIVERSE_REF,
        out_dir=tmp_path,
    )
    # 16 generated (17 universe - 1 base)
    assert len(written) == _EXPECTED_UNIVERSE_SIZE - 1, (
        f"{base_path.name}: expected 16 generated configs, got {len(written)}"
    )

    # Collect all symbols: base (resolved from its ref) + generated (inline universe).
    base_cfg = load_strategy_config(base)
    all_symbols: list[str] = list(resolve_universe_ref(base_cfg.universe_ref, base))  # ["SPY"]
    for path in written:
        cfg = load_strategy_config(path)
        assert len(cfg.universe) == 1
        all_symbols.append(cfg.universe[0])

    assert len(all_symbols) == _EXPECTED_UNIVERSE_SIZE, (
        f"{base_path.name}: symbol count {len(all_symbols)} != {_EXPECTED_UNIVERSE_SIZE}"
    )
    # Bijection: each symbol appears exactly once
    from collections import Counter

    counts = Counter(sym.upper() for sym in all_symbols)
    duplicates = {s: c for s, c in counts.items() if c > 1}
    assert not duplicates, (
        f"{base_path.name}: symbols appear more than once: {duplicates}"
    )

    # SPY present (via base), DIA present exactly once (via generated)
    assert "SPY" in counts, f"{base_path.name}: SPY missing from combined set"
    assert "DIA" in counts, f"{base_path.name}: DIA missing from combined set"
    assert counts["DIA"] == 1, f"{base_path.name}: DIA appears {counts['DIA']} times"


# ---------------------------------------------------------------------------
# ORB partial-range guard tests (Bug 2 regression)
# ---------------------------------------------------------------------------

_RANGE_HIGH = 505.0
_RANGE_LOW = 499.0


def _session_bars(date_et: str, rows: list[tuple[str, float, float, float, float]]) -> BarSet:
    records: list[dict[str, Any]] = []
    for time_str, o, h, low, c in rows:
        et = pd.Timestamp(f"{date_et} {time_str}:00").tz_localize("America/New_York")
        records.append(
            {
                "timestamp": et.tz_convert("UTC"),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": 1_000_000,
                "vwap": float(c),
            }
        )
    return BarSet(pd.DataFrame(records))


def _orb_context(
    bars: BarSet,
    positions: dict[str, float] | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id="breakout.opening_range_retest.intraday.spy.v1",
        family="breakout",
        template="opening_range_retest.intraday",
        variant="spy",
        version=1,
        config_hash="test_hash",
        parameters={
            "opening_range_minutes": 30,  # 6 bars at 5Min
            "entry_window_minutes": 90,
            "retest_band_pct": 0.003,
            "stop_loss_pct": 0.01,
            "exit_minutes_before_close": 5,
            "per_position_notional_pct": 0.10,
        },
        universe=("SPY",),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions=positions or {},
        equity=100_000.0,
        bars_by_symbol={"SPY": bars},
        entry_state={},
    )


def _complete_range_with_full_sequence() -> list[tuple[str, float, float, float, float]]:
    """6 opening-range bars (range_high=505, range_low=499) + 3-phase entry sequence."""
    return [
        # Opening range (6 bars)
        ("09:30", 500.0, 501.0, 499.0, 500.0),
        ("09:35", 500.0, 502.0, 499.5, 501.0),
        ("09:40", 501.0, 503.0, 500.0, 502.0),
        ("09:45", 502.0, 504.0, 500.5, 503.0),
        ("09:50", 503.0, 505.0, 501.0, 504.0),
        ("09:55", 504.0, 505.0, 502.0, 503.5),
        # Phase (a): breakout above range_high=505 at 10:00 (first entry-window bar)
        ("10:00", 504.0, 506.0, 503.0, 505.5),
        # Phase (b): retest at 10:05 (close <= 505)
        ("10:05", 505.5, 505.5, 504.0, 504.5),
        # Phase (c): reclaim at 10:10 (close > 505, low >= 505*(1-0.003)=503.485)
        ("10:10", 504.5, 507.0, 504.0, 506.0),
    ]


def test_orb_partial_range_returns_no_signal() -> None:
    """With only 5 of 6 expected opening-range bars, strategy must return no-signal."""
    # Only 5 opening-range bars (missing the 09:55 bar) + phase-sequence bars
    rows = [
        ("09:30", 500.0, 501.0, 499.0, 500.0),
        ("09:35", 500.0, 502.0, 499.5, 501.0),
        ("09:40", 501.0, 503.0, 500.0, 502.0),
        ("09:45", 502.0, 504.0, 500.5, 503.0),
        ("09:50", 503.0, 505.0, 501.0, 504.0),
        # 09:55 bar MISSING — only 5 of 6 range bars
        ("10:00", 504.0, 506.0, 503.0, 505.5),
        ("10:05", 505.5, 505.5, 504.0, 504.5),
        ("10:10", 504.5, 507.0, 504.0, 506.0),
    ]
    bars = _session_bars("2024-01-15", rows)
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    decision = strategy.evaluate(bars, _orb_context(bars))
    assert decision.intents == [], (
        f"Expected no-signal on partial range, got intents: {decision.intents}"
    )
    assert "incomplete opening range" in decision.reasoning.narrative, (
        f"Expected 'incomplete opening range' in narrative: {decision.reasoning.narrative!r}"
    )


def test_orb_complete_range_with_full_sequence_enters() -> None:
    """With all 6 opening-range bars and a complete 3-phase sequence, strategy emits BUY."""
    rows = _complete_range_with_full_sequence()
    bars = _session_bars("2024-01-15", rows)
    strategy = BreakoutOpeningRangeRetestIntradayStrategy()
    decision = strategy.evaluate(bars, _orb_context(bars))
    assert len(decision.intents) == 1, (
        f"Expected BUY intent on complete range+sequence, got: {decision.intents}"
    )
    from milodex.broker.models import OrderSide

    assert decision.intents[0].side == OrderSide.BUY
