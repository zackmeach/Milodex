"""Tests for the random matched-exposure intraday long baseline (E-PR2).

The baseline replaces *signal* with *chance*: per session a deterministic RNG
seeded from ``(symbol, session_date, seed)`` decides whether to enter and at
which random in-window minute, then holds to the session time-stop. No stop,
no signal exit.

The four hardest invariants (each adversarially flagged) have a named test:
  1. streaming entry — fire at the first PRESENT in-window bar >= target,
     never exact ``==`` (so a missing target bar still fires next present bar);
  2. one-entry-per-session re-scan guard surviving the T+1 fill gap;
  3. determinism across a SEQUENCE of growing (cursor-advancing) barsets;
  4. RNG derived per-call from (symbol, session_date, seed), never global.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies.base import StrategyContext
from milodex.strategies.bench_random_matched_exposure_long import (
    BenchRandomMatchedExposureLongStrategy,
)

# Full-session 5min grid 09:30..15:55 ET (78 bars). Enough to exercise the whole
# entry window [10:00, 15:00) and the 15:55 time-stop bar.
_FULL_SESSION_TIMES = [
    f"{h:02d}:{m:02d}"
    for h in range(9, 16)
    for m in range(0, 60, 5)
    if (h, m) >= (9, 30) and (h, m) <= (15, 55)
]


def test_entry_rate_zero_never_enters() -> None:
    """rate=0.0 -> no BUY ever, at any in-window bar."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    bars = _intraday_bars(date_et="2024-01-15", times_et=_FULL_SESSION_TIMES, close=500.0)
    # Evaluate at every growing cursor — none may emit a BUY.
    for cut in range(1, len(_FULL_SESSION_TIMES) + 1):
        sub = _intraday_bars(date_et="2024-01-15", times_et=_FULL_SESSION_TIMES[:cut], close=500.0)
        decision = strategy.evaluate(sub, _context(bars=sub, positions={}, session_entry_rate=0.0))
        assert all(i.side != OrderSide.BUY for i in decision.intents)
    _ = bars


def test_entry_rate_one_enters_at_real_in_window_bar() -> None:
    """rate=1.0 -> exactly one BUY, fired at a PRESENT in-window bar."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    buy_offsets: list[int] = []
    for cut in range(1, len(_FULL_SESSION_TIMES) + 1):
        sub = _intraday_bars(date_et="2024-01-15", times_et=_FULL_SESSION_TIMES[:cut], close=500.0)
        decision = strategy.evaluate(sub, _context(bars=sub, positions={}, session_entry_rate=1.0))
        buys = [i for i in decision.intents if i.side == OrderSide.BUY]
        if buys:
            buy_offsets.append(_offset_min(_FULL_SESSION_TIMES[cut - 1]))
    # rate=1 fires exactly once, at an in-window offset [30, 330).
    assert len(buy_offsets) == 1
    assert 30 <= buy_offsets[0] < 330


def test_entry_fires_first_bar_at_or_after_target() -> None:
    """Target between bars -> BUY at the first present bar with offset >= target."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    # seed=7 produces a target that is NOT a 5min-grid offset. Read the real target
    # from extras so we're asserting against the implementation's own output, not a
    # re-derivation of the hashing scheme.
    target = _read_target(strategy, "2024-01-15", seed=7)
    assert target % 5 != 0  # the whole point of this test: target is off-grid
    decision_offsets = _firing_offsets(strategy, "2024-01-15", rate=1.0, seed=7)
    assert len(decision_offsets) == 1
    assert decision_offsets[0] >= target
    assert decision_offsets[0] == _first_grid_at_or_after(target)


def test_missing_target_bar_still_fires_next_present_bar() -> None:
    """Target offset has no bar -> the next present in-window bar fires (B2)."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    target = _read_target(strategy, "2024-01-15", seed=7)
    # Drop the exact target bar (and everything within the same 5min cell) so the
    # offset has no bar; keep a present bar just after it.
    times = [t for t in _FULL_SESSION_TIMES if _offset_min(t) != target]
    fired = _firing_offsets(strategy, "2024-01-15", rate=1.0, seed=7, times=times)
    assert len(fired) == 1
    # The fired bar is the first PRESENT in-window bar with offset > target.
    assert fired[0] > target
    next_present = min(o for o in (_offset_min(t) for t in times) if 30 <= o < 330 and o >= target)
    assert fired[0] == next_present


def test_target_past_last_bar_no_entry() -> None:
    """Target beyond the last present in-window bar -> no BUY (documented edge)."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    target = _read_target(strategy, "2024-01-15", seed=7)
    # Truncate the session so the last in-window bar is strictly before target.
    times = [t for t in _FULL_SESSION_TIMES if _offset_min(t) < target]
    fired = _firing_offsets(strategy, "2024-01-15", rate=1.0, seed=7, times=times)
    assert fired == []


def test_exit_on_time_stop_only() -> None:
    """Holding + is_time_stop_bar -> SELL; there is NO stop-loss path."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    bars = _intraday_bars(date_et="2024-01-15", times_et=["15:55"], close=505.0)
    decision = strategy.evaluate(
        bars, _context(bars=bars, positions={"SPY": 3.0}, session_entry_rate=1.0)
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].side == OrderSide.SELL
    assert decision.intents[0].quantity == 3.0

    # A large adverse move mid-session must NOT trigger an exit (no stop loss).
    mid = _intraday_bars(
        date_et="2024-01-15",
        times_et=[t for t in _FULL_SESSION_TIMES if _offset_min(t) <= 200],
        close=400.0,  # far below any plausible entry
    )
    held = strategy.evaluate(
        mid, _context(bars=mid, positions={"SPY": 3.0}, session_entry_rate=1.0)
    )
    assert held.intents == []


def test_one_round_trip_per_session_across_fill_gap() -> None:
    """positions empty across post-target bars -> still exactly ONE BUY (B3 guard).

    The T+1 fill gap leaves ``positions`` flat for at least one bar after the
    BUY. A positions-only flat-check would re-emit. The prior-bar re-scan guard
    must suppress all subsequent BUYs this session.
    """
    strategy = BenchRandomMatchedExposureLongStrategy()
    buys = 0
    # Walk the growing barset with positions ALWAYS empty (simulating the fill
    # not having landed) — only the re-scan guard prevents re-entry.
    for cut in range(1, len(_FULL_SESSION_TIMES) + 1):
        sub = _intraday_bars(date_et="2024-01-15", times_et=_FULL_SESSION_TIMES[:cut], close=500.0)
        decision = strategy.evaluate(
            sub, _context(bars=sub, positions={}, session_entry_rate=1.0, seed=7)
        )
        buys += sum(1 for i in decision.intents if i.side == OrderSide.BUY)
    assert buys == 1


def test_determinism_stable_offset_across_growing_barsets() -> None:
    """Across growing cursor-advancing barsets, exactly one BUY at a stable offset."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    fired_first = _firing_offsets(strategy, "2024-01-15", rate=1.0, seed=99)
    fired_again = _firing_offsets(strategy, "2024-01-15", rate=1.0, seed=99)
    assert len(fired_first) == 1
    assert fired_first == fired_again
    # extras must report the same target across every call.
    targets = set()
    for cut in range(1, len(_FULL_SESSION_TIMES) + 1):
        sub = _intraday_bars(date_et="2024-01-15", times_et=_FULL_SESSION_TIMES[:cut], close=500.0)
        decision = strategy.evaluate(
            sub, _context(bars=sub, positions={}, session_entry_rate=1.0, seed=99)
        )
        targets.add(decision.reasoning.extras["target_offset_min"])
    assert len(targets) == 1


def test_different_session_dates_decorrelate() -> None:
    """Same seed, two dates -> independent target draws (no global seed)."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    # Read the real targets from the implementation's own extras — no re-derivation.
    t1 = _read_target(strategy, "2024-01-15", seed=42)
    t2 = _read_target(strategy, "2024-02-20", seed=42)
    fired1 = _firing_offsets(strategy, "2024-01-15", rate=1.0, seed=42)
    fired2 = _firing_offsets(strategy, "2024-02-20", rate=1.0, seed=42)
    assert fired1 and fired2
    assert fired1[0] == _first_grid_at_or_after(t1)
    assert fired2[0] == _first_grid_at_or_after(t2)
    # Same seed, different dates -> different draws -> different firing offsets:
    # proves the RNG is keyed per-session, not a process-global seed.
    assert t1 != t2
    assert fired1[0] != fired2[0]


def test_records_extras() -> None:
    """extras carries seed_basis, session_entry_rate, entered_session,
    target_offset_min, entry_offset_min."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    # An in-window bar at/after target with rate=1 fires the BUY.
    fired = _firing_offsets(strategy, "2024-01-15", rate=1.0, seed=7)
    assert len(fired) == 1
    # Read the real target from the implementation's own extras (pre-entry bar),
    # then re-run to exactly the firing bar and inspect its full extras blob.
    target = _read_target(strategy, "2024-01-15", seed=7)
    fire_offset = _first_grid_at_or_after(target)
    times = [t for t in _FULL_SESSION_TIMES if _offset_min(t) <= fire_offset]
    bars = _intraday_bars(date_et="2024-01-15", times_et=times, close=500.0)
    decision = strategy.evaluate(
        bars, _context(bars=bars, positions={}, session_entry_rate=1.0, seed=7)
    )
    extras = decision.reasoning.extras
    assert "seed_basis" in extras
    assert extras["seed_basis"] == "SPY:2024-01-15:7"
    assert extras["session_entry_rate"] == 1.0
    assert extras["entered_session"] is True
    assert 30 <= extras["target_offset_min"] < 330
    assert extras["entry_offset_min"] == _first_grid_at_or_after(extras["target_offset_min"])


def test_skips_half_day() -> None:
    """A half-day session -> no entry (matches siblings)."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    # 2024-11-29 is a known half-day.
    bars = _intraday_bars(
        date_et="2024-11-29",
        times_et=["09:30", "09:35", "10:00", "10:05", "10:30", "10:35"],
        close=500.0,
    )
    decision = strategy.evaluate(bars, _context(bars=bars, positions={}, session_entry_rate=1.0))
    assert decision.intents == []
    assert "half-day" in decision.reasoning.narrative


def test_empty_universe_no_signal() -> None:
    """Empty universe -> single_symbol(None) path, graceful no-signal."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    bars = _intraday_bars(date_et="2024-01-15", times_et=["10:00"], close=500.0)
    context = _context(bars=bars, positions={}, session_entry_rate=1.0)
    context = _with_universe(context, ())
    decision = strategy.evaluate(bars, context)
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_target_draw_clamped_to_firable_grid() -> None:
    """N1: drawn target is always <= OR + EW - _BAR_MINUTES (last on-grid in-window bar).

    OR=30, EW=300, _BAR_MINUTES=5 -> max firable = 325. Targets 326-329 are
    off-grid and can never fire; the clamped high bound (326 exclusive = 325+1)
    prevents drawing them.
    """
    from milodex.strategies.bench_random_matched_exposure_long import _BAR_MINUTES

    strategy = BenchRandomMatchedExposureLongStrategy()
    opening_range = 30
    entry_window = 300
    max_firable = opening_range + entry_window - _BAR_MINUTES  # 325

    # Collect targets from 500 distinct seed bases; all must be <= max_firable.
    targets: set[int] = set()
    for seed in range(500):
        target = _read_target(strategy, "2024-01-15", seed=seed)
        assert target <= max_firable, f"seed={seed}: target {target} > max firable {max_firable}"
        targets.add(target)

    # The max bound itself (325) must be reachable and is on-grid and in-window.
    assert max_firable % _BAR_MINUTES == 0, "max firable must be on-grid"
    assert opening_range <= max_firable < opening_range + entry_window, (
        "max firable must be inside the entry window"
    )
    assert max_firable in targets, (
        f"max firable {max_firable} never drawn across 500 seeds — clamping too tight"
    )


def test_max_lookback_periods_is_78() -> None:
    """The null declares its one-session bar bound so the warmup heuristic
    never treats the large ``seed`` parameter as a lookback period."""
    strategy = BenchRandomMatchedExposureLongStrategy()
    assert strategy.max_lookback_periods() == 78


def test_multi_symbol_universe_raises() -> None:
    """A >1 universe trips the single_symbol cardinality guard."""
    import pytest

    strategy = BenchRandomMatchedExposureLongStrategy()
    bars = _intraday_bars(date_et="2024-01-15", times_et=["10:00"], close=500.0)
    context = _with_universe(
        _context(bars=bars, positions={}, session_entry_rate=1.0), ("SPY", "QQQ")
    )
    with pytest.raises(ValueError):
        strategy.evaluate(bars, context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_target(
    strategy: BenchRandomMatchedExposureLongStrategy,
    date_et: str,
    *,
    seed: int,
) -> int:
    """Read the strategy's deterministic target offset from its own extras.

    Evaluates with only the first bar of the session (inside the opening range,
    so no entry fires) and reads ``extras["target_offset_min"]`` — the
    implementation's single source of truth for the draw.
    """
    bars = _intraday_bars(date_et=date_et, times_et=["09:30"], close=500.0)
    decision = strategy.evaluate(
        bars, _context(bars=bars, positions={}, session_entry_rate=1.0, seed=seed)
    )
    return int(decision.reasoning.extras["target_offset_min"])


def _firing_offsets(
    strategy: BenchRandomMatchedExposureLongStrategy,
    date_et: str,
    *,
    rate: float,
    seed: int,
    times: list[str] | None = None,
) -> list[int]:
    """Walk a growing barset for one session; return the in-window offsets at
    which a BUY was emitted (positions stay flat throughout — the re-scan guard
    is the only thing preventing re-entry)."""
    grid = times if times is not None else _FULL_SESSION_TIMES
    fired: list[int] = []
    for cut in range(1, len(grid) + 1):
        sub = _intraday_bars(date_et=date_et, times_et=grid[:cut], close=500.0)
        decision = strategy.evaluate(
            sub, _context(bars=sub, positions={}, session_entry_rate=rate, seed=seed)
        )
        if any(i.side == OrderSide.BUY for i in decision.intents):
            fired.append(_offset_min(grid[cut - 1]))
    return fired


def _offset_min(time_et: str) -> int:
    h, m = (int(x) for x in time_et.split(":"))
    return (h - 9) * 60 + (m - 30)


def _first_grid_at_or_after(target: int) -> int:
    """Smallest 5min-grid offset >= target (the bar the streaming entry fires on)."""
    return ((target + 4) // 5) * 5


def _context(
    *,
    bars: BarSet,
    positions: dict[str, float],
    session_entry_rate: float,
    seed: int = 7,
    equity: float = 100_000.0,
) -> StrategyContext:
    return StrategyContext(
        strategy_id="benchmark.random_matched_exposure.intraday.spy.v1",
        family="benchmark",
        template="random_matched_exposure.intraday",
        variant="spy",
        version=1,
        config_hash="test_hash",
        parameters={
            "opening_range_minutes": 30,
            "entry_window_minutes": 300,
            "exit_minutes_before_close": 5,
            "per_position_notional_pct": 0.10,
            "session_entry_rate": session_entry_rate,
            "seed": seed,
        },
        universe=("SPY",),
        universe_ref=None,
        disable_conditions=(),
        config_path="/dev/null",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol={"SPY": bars},
        entry_state={},
    )


def _with_universe(context: StrategyContext, universe: tuple[str, ...]) -> StrategyContext:
    from dataclasses import replace

    bars = next(iter(context.bars_by_symbol.values()))
    by_symbol = {sym: bars for sym in universe} if universe else {}
    return replace(context, universe=universe, bars_by_symbol=by_symbol)


def _intraday_bars(*, date_et: str, times_et: list[str], close: float) -> BarSet:
    rows: list[dict[str, Any]] = []
    for time_str in times_et:
        et_ts = pd.Timestamp(f"{date_et} {time_str}:00").tz_localize("America/New_York")
        utc_ts = et_ts.tz_convert("UTC")
        rows.append(
            {
                "timestamp": utc_ts,
                "open": close,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1_000_000,
                "vwap": close,
            }
        )
    return BarSet(pd.DataFrame(rows))
