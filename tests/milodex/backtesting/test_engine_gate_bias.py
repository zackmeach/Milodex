"""Regression tests for three backtest-correctness bugs that silently biased
capital-readiness gate results.

Each test is written to FAIL against the pre-fix codebase and PASS after
the corresponding minimal fix lands.  Tests are grouped by bug:

  Bug 1 — WARMUP_UNDER_SIZING   (~line 1216, engine.py)
  Bug 2 — UNIVERSE0_DAY_GATING  (~line 700, engine.py)
  Bug 3 — CACHE_MERGE_NO_DTYPE  (cache.py merge)
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.core.event_store import EventStore
from milodex.data.cache import ParquetCache
from milodex.data.models import BarSet, Timeframe
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_event_store() -> EventStore:
    tmp = tempfile.mktemp(suffix=".db")
    return EventStore(Path(tmp))


def _make_barset(closes: list[float], start: date) -> BarSet:
    """Build a BarSet with one bar per calendar day starting at `start`."""
    rows = []
    d = start
    for close in closes:
        rows.append(
            {
                "timestamp": pd.Timestamp(d, tz="UTC"),
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
                "vwap": close,
            }
        )
        d += timedelta(days=1)
    return BarSet(pd.DataFrame(rows))


def _make_trading_day_barset(n_bars: int, end: date) -> BarSet:
    """Build a BarSet of exactly `n_bars` Mon–Fri trading days ending on `end`.

    This produces a realistic dataset where 365 calendar days translates to
    only ~252 bars — the weekend gap that makes the warmup under-sizing bug
    observable.
    """
    rows = []
    d = end
    count = 0
    while count < n_bars:
        if d.weekday() < 5:  # Monday=0 … Friday=4
            rows.append(
                {
                    "timestamp": pd.Timestamp(d, tz="UTC"),
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000,
                    "vwap": 100.0,
                }
            )
            count += 1
        d -= timedelta(days=1)
    rows.reverse()
    return BarSet(pd.DataFrame(rows))


def _decision_no_signal() -> StrategyDecision:
    return StrategyDecision(
        intents=[],
        reasoning=DecisionReasoning(rule="no_signal", narrative="test stub"),
    )


def _make_loaded(
    strategy_id: str,
    universe: tuple[str, ...],
    parameters: dict,
    strategy_obj: Strategy | None = None,
    tmp_dir: Path | None = None,
) -> MagicMock:
    """Return a mock LoadedStrategy with explicit parameters dict."""
    cfg_dir = tmp_dir or Path(tempfile.mkdtemp())
    config_path = cfg_dir / "strategy.yaml"
    config_path.write_text(
        """
strategy:
  id: "test"
  family: "regime"
  template: "daily.sma200_rotation"
  variant: "test"
  version: 1
  description: "test"
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: null
  risk:
    max_position_pct: 1.0
    max_positions: 1
    daily_loss_cap_pct: 1.0
    stop_loss_pct: null
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
  disable_conditions_additional: []
""",
        encoding="utf-8",
    )

    config = MagicMock()
    config.strategy_id = strategy_id
    config.family = "regime"
    config.template = "daily.sma200_rotation"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = parameters
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.tempo = {"bar_size": "1D"}
    config.universe = universe
    config.risk = {}

    context = StrategyContext(
        strategy_id=strategy_id,
        family="regime",
        template="daily.sma200_rotation",
        variant="test",
        version=1,
        config_hash="abc123",
        parameters=parameters,
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path=str(config_path),
        manifest={},
    )

    strategy_mock = MagicMock()
    strategy_mock.evaluate.return_value = _decision_no_signal()
    strategy_mock.max_lookback_periods.return_value = 0
    if strategy_obj is not None:
        # Use the real strategy object instead of a mock.
        strategy_mock = strategy_obj

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy_mock
    return loaded


# ===========================================================================
# Bug 1 — WARMUP UNDER-SIZING
#
# _warmup_calendar_days() reflects over config.parameters and picks the max
# *integer* value.  A lookback stored as a float (e.g. 200.0) or nested in a
# sub-dict is silently excluded, causing the warmup to floor at 365 calendar
# days (~252 trading days) even when the declared lookback is larger.
#
# Fix: derive warmup from strategy.max_lookback_periods() (or equivalent),
# which handles float whole-numbers and nested params.
# ===========================================================================


class _LookbackRecordingStrategy(Strategy):
    """Minimal strategy that records whether it received enough bars."""

    family = "test"
    template = "test.warmup"

    def __init__(self, declared_lookback: int) -> None:
        self._declared = declared_lookback
        self.nan_on_first_day: bool | None = None  # set by first evaluate() call
        self._first_day_seen = False

    def max_lookback_periods(self) -> int:
        return self._declared

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        if not self._first_day_seen:
            df = bars.to_dataframe()
            closes = df["close"].astype(float)
            # Simulate the indicator needing `declared` bars: check whether
            # we have at least `declared` rows on the very first evaluation day.
            self.nan_on_first_day = len(closes) < self._declared
            self._first_day_seen = True
        return _decision_no_signal()


def _date_respecting_provider(full_barset_by_symbol: dict) -> MagicMock:
    """Provider whose get_bars respects the `start` date argument.

    The real data provider only returns bars on or after `start`.  Mocking
    this behaviour is required for warmup tests: we need to verify that the
    engine requests a start date that is early enough to cover the declared
    lookback, not just that the engine's simulation loop receives enough bars.
    """
    provider = MagicMock()

    def _get_bars(symbols, timeframe, start, end):  # noqa: ARG001
        result = {}
        for sym in symbols:
            barset = full_barset_by_symbol.get(sym)
            if barset is None:
                continue
            df = barset.to_dataframe()
            ts = pd.to_datetime(df["timestamp"], utc=True)
            mask = (ts.dt.date >= start) & (ts.dt.date <= end)
            result[sym] = BarSet(df[mask].reset_index(drop=True))
        return result

    provider.get_bars.side_effect = _get_bars
    return provider


def _make_loaded_with_float_lookback(lookback_float: float, tmp_dir: Path) -> MagicMock:
    """Loaded strategy whose config stores the lookback as a float, not int."""
    params = {"lookback": lookback_float}  # float param — skipped by old heuristic
    strategy = _LookbackRecordingStrategy(int(lookback_float))
    loaded = _make_loaded(
        strategy_id="test.float_lookback.v1",
        universe=("SPY",),
        parameters=params,
        strategy_obj=strategy,
        tmp_dir=tmp_dir,
    )
    # Attach the real strategy object so evaluate() is called
    loaded.strategy = strategy
    return loaded, strategy


def test_warmup_float_lookback_provides_sufficient_bars(tmp_path):
    """A strategy with a float-typed lookback parameter (e.g. 300.0) must
    receive at least `lookback` bars on its first evaluation day.

    Pre-fix: the warmup heuristic skips float params, warmup floors at 365
    calendar days (~252 trading days).  For a declared lookback of 300 trading
    days, the engine requests only 365 calendar days of history.  With a
    Mon–Fri synthetic bar series (realistic), that is only ~261 bars — fewer
    than 300 — so indicators emit NaN on the first evaluation cycle, silently
    biasing early signals and the metrics fed to the capital-readiness gate.

    Post-fix: warmup is derived from strategy.max_lookback_periods(), which
    correctly covers the 300-period declared lookback, so the engine requests
    enough history and the strategy receives >= 300 bars on its first eval day.

    The provider used here is date-respecting (only returns bars on/after the
    requested `start` date) and uses a Mon–Fri bar series so that the calendar-
    day → trading-day gap is realistic.
    """
    declared_lookback = 300  # trading days — larger than the ~261 day floor

    loaded, strategy = _make_loaded_with_float_lookback(float(declared_lookback), tmp_path)

    run_start = date(2025, 1, 2)  # Thursday
    run_end = date(2025, 1, 8)  # Wednesday

    # Build enough Mon–Fri history to cover any correct warmup window.
    # declared_lookback * 2 trading days covers 600 trading days ≈ 840 calendar.
    full_barset = _make_trading_day_barset(declared_lookback * 2, run_end)

    provider = _date_respecting_provider({"SPY": full_barset})

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    engine.run(run_start, run_end)

    assert strategy.nan_on_first_day is not None, "strategy was never evaluated"
    assert not strategy.nan_on_first_day, (
        f"strategy received fewer than {declared_lookback} bars on its first evaluation day "
        f"— warmup is under-sized for a float-typed lookback param"
    )


def test_warmup_int_lookback_behavior_unchanged(tmp_path):
    """Strategies with integer lookback params must behave exactly as before.

    This guards against regressions: the fix must only *extend* warmup to the
    under-warmed cases, not change anything for strategies already correct.
    """
    # Integer param — old heuristic picks this up correctly; fix must preserve.
    declared_lookback = 50
    params = {"lookback": declared_lookback}  # integer
    strategy = _LookbackRecordingStrategy(declared_lookback)
    loaded = _make_loaded(
        strategy_id="test.int_lookback.v1",
        universe=("SPY",),
        parameters=params,
        strategy_obj=strategy,
        tmp_dir=tmp_path,
    )
    loaded.strategy = strategy

    run_start = date(2025, 1, 2)
    run_end = date(2025, 1, 8)
    full_barset = _make_trading_day_barset(declared_lookback * 10, run_end)

    provider = _date_respecting_provider({"SPY": full_barset})

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    engine.run(run_start, run_end)

    assert strategy.nan_on_first_day is not None, "strategy was never evaluated"
    assert not strategy.nan_on_first_day, (
        f"strategy received fewer than {declared_lookback} bars — int-lookback warmup regressed"
    )


# ===========================================================================
# Bug 2 — UNIVERSE[0] DAY-GATING DROPS DAYS
#
# When `universe[0]` has no bar for a given trading day, the engine `continue`s
# AFTER draining pending fills but BEFORE appending an equity point or calling
# strategy.evaluate().  In a multi-symbol universe with mismatched calendars,
# days where universe[0] is absent but other symbols are active are silently
# skipped — producing holes in the equity curve and missed signals.
#
# Fix: gate on "any universe symbol has a bar" instead of "universe[0] has a
# bar".
# ===========================================================================


def test_universe0_absent_day_strategy_still_evaluated(tmp_path):
    """When universe[0] has no bar on a day but universe[1] does, strategy.evaluate()
    must still be called and an equity point must be appended.

    Pre-fix: the day is skipped entirely — the strategy sees fewer trading days
    than it should and the equity curve has holes, biasing metrics.
    """
    params = {"ma_filter_length": 3, "allocation_pct": 0.9}
    loaded = _make_loaded(
        strategy_id="test.multiuiv.v1",
        universe=("PRIMARY", "SECONDARY"),
        parameters=params,
        tmp_dir=tmp_path,
    )

    evaluate_call_count: list[int] = [0]

    def _recording_evaluate(bars, context):
        evaluate_call_count[0] += 1
        return _decision_no_signal()

    loaded.strategy.evaluate.side_effect = _recording_evaluate

    # PRIMARY has bars only on day3 and day4 (starts late — simulates a newly
    # listed ETF, a different exchange holiday calendar, or a universe[0] that
    # didn't exist before day3).
    # SECONDARY has bars on day1, day2, day3, day4.
    #
    # On day1 and day2: PRIMARY has no bars yet (len == 0 after slice).
    # That is the exact bug condition: `bars_by_symbol.get(universe[0])` is
    # None (or empty), so the old code `continue`s — skipping equity recording
    # and strategy evaluation — even though SECONDARY is active.
    base = date(2025, 1, 2)
    day1 = base
    day2 = base + timedelta(days=1)
    day3 = base + timedelta(days=2)
    day4 = base + timedelta(days=3)

    def _barset_from_dates(days: list[date]) -> BarSet:
        rows = [
            {
                "timestamp": pd.Timestamp(d, tz="UTC"),
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0,
                "volume": 1000,
                "vwap": 100.0,
            }
            for d in days
        ]
        return BarSet(pd.DataFrame(rows))

    # PRIMARY starts only on day3 — no bars before that.
    primary_barset = _barset_from_dates([day3, day4])
    secondary_barset = _barset_from_dates([day1, day2, day3, day4])

    provider = MagicMock()
    provider.get_bars.return_value = {
        "PRIMARY": primary_barset,
        "SECONDARY": secondary_barset,
    }

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    result = engine.run(day1, day4)

    # All 4 trading days must have triggered an evaluate() call.
    # Pre-fix: day1 and day2 are skipped (PRIMARY has no bars yet, len==0 after
    # slice) → only 2 evaluate calls.
    # Post-fix: any-symbol gate keeps day1 and day2 → 4 evaluate calls.
    assert evaluate_call_count[0] == 4, (
        f"strategy.evaluate() was called {evaluate_call_count[0]} times, expected 4. "
        f"Days {day1} and {day2} (PRIMARY has no bars yet, SECONDARY active) were "
        f"likely skipped by universe[0] day-gating."
    )

    # Equity curve must contain an entry for day1 and day2.
    equity_dates = [d for d, _ in result.equity_curve]
    assert day1 in equity_dates, (
        f"equity curve is missing {day1} — day was silently skipped. equity_dates={equity_dates}"
    )
    assert day2 in equity_dates, (
        f"equity curve is missing {day2} — day was silently skipped. equity_dates={equity_dates}"
    )


def test_universe0_absent_single_symbol_still_skips(tmp_path):
    """Single-symbol universes: a day with no bar at all must still be skipped.

    This is the preserved behaviour — we gate on "any symbol has a bar", and
    when the only symbol has no bar, the day should not appear in the equity
    curve.
    """
    params = {"ma_filter_length": 3, "allocation_pct": 0.9}
    loaded = _make_loaded(
        strategy_id="test.singleuiv.v1",
        universe=("SPY",),
        parameters=params,
        tmp_dir=tmp_path,
    )

    base = date(2025, 1, 2)
    day1 = base
    day2 = base + timedelta(days=1)
    # day2 has no bar → must not appear in equity curve
    barset = _make_barset([100.0], start=day1)  # only day1

    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    result = engine.run(day1, day2)

    equity_dates = [d for d, _ in result.equity_curve]
    assert day2 not in equity_dates, (
        f"day2 has no bar for the sole universe symbol but appeared in equity curve: {equity_dates}"
    )


# ===========================================================================
# Bug 2b — REGIME WRONG-SYMBOL SUBSTITUTION (safety blocker)
#
# The Bug-2 fix kept a day live when universe[0] is absent but another
# universe symbol is active.  Its first cut substituted "the first available
# symbol's BarSet" as the primary_bars argument.  Strategies that consume
# `bars` DIRECTLY (RegimeSpyShy200DmaStrategy: bars.to_dataframe(), MA,
# position sizing) would then compute a confidently-wrong risk-on/off
# decision and position size from the WRONG symbol's prices (e.g. SHY, a
# bond ETF, standing in for an absent SPY) — corrupting the regime equity
# curve and every gate/analytics number derived from it.
#
# Fix: when universe[0] is absent for the day, do NOT substitute a different
# symbol's barset as primary_bars.  Keep the day live (bars_by_symbol
# strategies still evaluate, equity still recorded) but feed the direct-bars
# consumer an empty primary barset so it correctly returns no_signal.
# ===========================================================================


class _BarsBySymbolProbe(Strategy):
    """A strategy that ignores `bars` and reads context.bars_by_symbol.

    Mirrors how meanrev/breakout consume data.  Records the days it was
    evaluated so we can prove the universe[0]-absent day stays live for
    these (safe) strategies even after the wrong-symbol substitution is
    removed.
    """

    family = "test"
    template = "test.bars_by_symbol"

    def __init__(self) -> None:
        self.evaluated_secondary_closes: list[float] = []

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:  # noqa: ARG002
        _ = bars  # intentionally unused — this family reads bars_by_symbol
        secondary = context.bars_by_symbol.get("SHY")
        if secondary is not None and len(secondary) > 0:
            self.evaluated_secondary_closes.append(
                float(secondary.to_dataframe()["close"].iloc[-1])
            )
        return _decision_no_signal()


def _regime_strategy() -> Strategy:
    from milodex.strategies.regime_spy_shy_200dma import RegimeSpyShy200DmaStrategy

    return RegimeSpyShy200DmaStrategy()


def _spy_absent_universe(tmp_path):
    """Build the SPY-absent-day scenario shared by the blocker tests.

    Universe is ("SPY", "SHY").  SHY has a clean 4-day uptrend on
    day1..day4; SPY only has bars on day3, day4.  On day1 and day2 SPY
    (universe[0]) is absent but SHY is active.

    SHY's series is engineered so that IF the regime strategy were fed
    SHY's barset as primary_bars on day3 (3 SHY bars available, SPY still
    absent), it would resolve a target and emit a BUY intent (latest close
    strictly above the 3-bar MA, and the account can afford a share at that
    price).  That is the wrong-symbol bias we are guarding against — a real
    fill driven entirely by the wrong symbol's prices.
    """
    base = date(2025, 1, 6)  # Monday
    day1 = base
    day2 = base + timedelta(days=1)
    day3 = base + timedelta(days=2)
    day4 = base + timedelta(days=3)
    day5 = base + timedelta(days=4)

    def _barset(dates_and_closes: list[tuple[date, float]]) -> BarSet:
        rows = [
            {
                "timestamp": pd.Timestamp(d, tz="UTC"),
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1000,
                "vwap": c,
            }
            for d, c in dates_and_closes
        ]
        return BarSet(pd.DataFrame(rows))

    # SHY: strong uptrend. On day3 SHY has 3 bars (10, 12, 14) so a 3-bar MA
    # = 12 and latest close 14 > 12 -> if regime were fed SHY it resolves a
    # target and, with a $100k account, can afford a share at 14 -> emits a
    # BUY intent that fills on day4's SHY open. That fill is pure wrong-symbol
    # contamination (SPY is still absent through day3).
    shy_barset = _barset([(day1, 10.0), (day2, 12.0), (day3, 14.0), (day4, 16.0), (day5, 18.0)])
    # SPY absent on day1..day3; present only day4, day5 — and with just 2 SPY
    # bars the regime's 3-bar MA is never satisfied, so a CORRECT engine
    # produces zero regime trades across the whole run.
    spy_barset = _barset([(day4, 400.0), (day5, 402.0)])

    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": spy_barset, "SHY": shy_barset}
    return provider, (day1, day2, day3, day4, day5)


def test_regime_does_not_emit_wrong_symbol_decision_when_universe0_absent(tmp_path):
    """The REAL regime strategy must NOT produce an SHY-derived decision on a
    day where SPY (universe[0]) is absent but SHY is present.

    Pre-fix (wrong-symbol substitution): on day1/day2 the engine feeds SHY's
    barset to regime as primary_bars.  Regime computes its MA filter and
    position size from SHY (bond ETF) prices and emits a BUY intent — a
    confidently-wrong decision that corrupts the regime equity curve.

    Post-fix: regime receives an empty primary barset on the SPY-absent days
    and correctly returns no_signal (no intents, no fills) for those days,
    while the day stays live (equity recorded).
    """
    params = {
        "ma_filter_length": 3,
        "risk_on_symbol": "SPY",
        "risk_off_symbol": "SHY",
        "allocation_pct": 0.5,
    }
    regime = _regime_strategy()
    loaded = _make_loaded(
        strategy_id="regime.spy_shy.v1",
        universe=("SPY", "SHY"),
        parameters=params,
        strategy_obj=regime,
        tmp_dir=tmp_path,
    )

    provider, (day1, day2, day3, day4, day5) = _spy_absent_universe(tmp_path)
    spy_absent_days = (day1, day2, day3)

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    result = engine.run(day1, day5)

    # The decisive assertion: a CORRECT engine produces ZERO regime trades.
    # SPY (universe[0]) is absent day1..day3 and present only day4/day5 with
    # just 2 bars — never enough for the 3-bar MA — so regime never has a
    # valid signal. Under the pre-fix wrong-symbol substitution, day3 feeds
    # SHY's 3-bar barset (10,12,14; latest 14 > MA 12) to regime, which
    # resolves a target and BUYs, filling on day4's SHY open. Any trade here
    # is therefore pure SHY-derived contamination.
    assert result.trade_count == 0, (
        f"regime executed {result.trade_count} trade(s); SPY is absent "
        "day1..day3 and has <3 bars thereafter, so a correct engine never "
        "trades — any trade is SHY-derived wrong-symbol contamination"
    )

    # No trade events at all may exist (regime never had a valid SPY signal).
    assert len(store.list_trades()) == 0, (
        "regime produced trade events with SPY absent/under-supplied — "
        "SHY-derived wrong-symbol contamination"
    )

    # The audit trail must NOT contain a no_trade row anchored on an
    # SHY-derived close (10.0 / 12.0 / 14.0) for a SPY-absent day. A
    # non-universe[0] close on a row labelled symbol=SPY is exactly the
    # corrupted-audit symptom the fix removes (pre-fix the substituted SHY
    # barset's latest() leaked onto these rows).
    explanations = store.list_explanations()
    no_action_days = [e for e in explanations if e.decision_type == "no_trade"]
    for e in no_action_days:
        if e.latest_bar_timestamp is None:
            continue
        ts = pd.Timestamp(e.latest_bar_timestamp).date()
        if ts in spy_absent_days:
            assert e.symbol == "SPY"
            assert e.latest_bar_close not in (10.0, 12.0, 14.0), (
                f"no_trade row for {ts} recorded an SHY-derived close "
                f"{e.latest_bar_close} — wrong-symbol audit contamination"
            )

    # Bug-2 fix preserved: equity recorded for the SPY-absent days too.
    equity_dates = [d for d, _ in result.equity_curve]
    for d in spy_absent_days:
        assert d in equity_dates, (
            f"equity curve missing SPY-absent day {d} "
            f"(equity_dates={equity_dates}); the legitimate Bug-2 "
            "keep-day-live fix regressed"
        )


def test_bars_by_symbol_strategy_still_evaluated_on_universe0_absent_day(tmp_path):
    """A bars_by_symbol-consuming strategy must STILL be evaluated (and see
    the active secondary symbol) on a day where universe[0] is absent.

    This proves the wrong-symbol fix did not regress the legitimate Bug-2
    behaviour for the safe strategy family.
    """
    probe = _BarsBySymbolProbe()
    loaded = _make_loaded(
        strategy_id="probe.bars_by_symbol.v1",
        universe=("SPY", "SHY"),
        parameters={"ma_filter_length": 3, "allocation_pct": 0.5},
        strategy_obj=probe,
        tmp_dir=tmp_path,
    )

    provider, (day1, day2, day3, day4, day5) = _spy_absent_universe(tmp_path)

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    result = engine.run(day1, day5)

    # The probe must have been evaluated on the SPY-absent days and seen the
    # active SHY bars there (closes 10.0 on day1, 12.0 on day2).
    assert 10.0 in probe.evaluated_secondary_closes, (
        "bars_by_symbol probe was not evaluated on day1 (SPY absent, SHY "
        f"active); evaluated_secondary_closes={probe.evaluated_secondary_closes}"
    )
    assert 12.0 in probe.evaluated_secondary_closes, (
        "bars_by_symbol probe was not evaluated on day2 (SPY absent, SHY "
        f"active); evaluated_secondary_closes={probe.evaluated_secondary_closes}"
    )
    equity_dates = [d for d, _ in result.equity_curve]
    assert day1 in equity_dates and day2 in equity_dates


# ===========================================================================
# Bug 3 — CACHE MERGE NO DTYPE VALIDATION
#
# ParquetCache.merge() concatenates existing and new DataFrames without
# checking that columns or dtypes match.  A dtype drift in new_data silently
# produces a mixed-dtype parquet that later reads back with object columns,
# breaking all numeric operations downstream with no pointer to the merge.
#
# Fix: validate new_data columns and dtypes against existing before concat.
# On mismatch: coerce new_data to existing schema, or raise a clear error.
# ===========================================================================


_BASE_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "vwap"]


def _base_df() -> pd.DataFrame:
    """Canonical bar DataFrame with the expected columns and dtypes."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-13", "2025-01-14"], utc=True),
            "open": pd.array([100.0, 101.0], dtype="float64"),
            "high": pd.array([102.0, 103.0], dtype="float64"),
            "low": pd.array([99.0, 100.0], dtype="float64"),
            "close": pd.array([101.0, 102.0], dtype="float64"),
            "volume": pd.array([1_000_000, 1_100_000], dtype="int64"),
            "vwap": pd.array([100.5, 101.5], dtype="float64"),
        }
    )


def _extra_column_df() -> pd.DataFrame:
    """New data with an unexpected extra column not present in existing."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-15"], utc=True),
            "open": pd.array([103.0], dtype="float64"),
            "high": pd.array([104.0], dtype="float64"),
            "low": pd.array([102.0], dtype="float64"),
            "close": pd.array([103.5], dtype="float64"),
            "volume": pd.array([1_200_000], dtype="int64"),
            "vwap": pd.array([103.2], dtype="float64"),
            "extra_signal": pd.array([42.0], dtype="float64"),  # ← schema drift
        }
    )


def _missing_column_df() -> pd.DataFrame:
    """New data missing a column that exists in the cached frame."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-15"], utc=True),
            "open": pd.array([103.0], dtype="float64"),
            "high": pd.array([104.0], dtype="float64"),
            "low": pd.array([102.0], dtype="float64"),
            "close": pd.array([103.5], dtype="float64"),
            "volume": pd.array([1_200_000], dtype="int64"),
            # "vwap" omitted — schema drift
        }
    )


def test_merge_extra_column_does_not_silently_corrupt(tmp_path):
    """Merging new_data that has an extra column must not silently inject that
    column into the on-disk parquet.

    Pre-fix: pd.concat silently widens the schema.  After the merge, the
    parquet has an extra column that downstream BarSet consumers do not expect,
    causing silent NaN propagation for existing rows and confusion about the
    schema contract.

    Post-fix: the merge either strips the extra column from new_data (coerce to
    existing schema) or raises a clear, actionable error before writing.
    """
    cache = ParquetCache(tmp_path / "cache")
    cache.write("AAPL", Timeframe.DAY_1, _base_df())

    try:
        cache.merge("AAPL", Timeframe.DAY_1, _extra_column_df())
    except (ValueError, TypeError):
        return  # loud failure at merge — acceptable

    result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None
    assert "extra_signal" not in result.columns, (
        "extra column 'extra_signal' from new_data was silently injected into the "
        "on-disk parquet — schema drift propagated without validation"
    )
    assert list(result.columns) == _BASE_COLUMNS, (
        f"on-disk schema changed after merge: expected {_BASE_COLUMNS}, got {list(result.columns)}"
    )


def test_merge_missing_column_does_not_silently_corrupt(tmp_path):
    """Merging new_data that is missing a column must not silently introduce
    NaN rows for that column in the on-disk parquet.

    Pre-fix: pd.concat fills missing-column cells with NaN and writes them.
    Downstream numeric operations on those rows silently produce NaN results
    that propagate into metrics without any error pointer to the merge.

    Post-fix: the merge either backfills the missing column on new_data using
    a safe default, or raises a clear, actionable error before writing.
    """
    cache = ParquetCache(tmp_path / "cache")
    cache.write("AAPL", Timeframe.DAY_1, _base_df())

    try:
        cache.merge("AAPL", Timeframe.DAY_1, _missing_column_df())
    except (ValueError, TypeError):
        return  # loud failure at merge — acceptable

    result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None
    # vwap must not have NaN — the missing-column row must either have been
    # coerced (backfilled, zeroed, etc.) or rejected with an error above.
    assert result["vwap"].notna().all(), (
        "vwap column contains NaN after merge of new_data missing that column — "
        "silent NaN injection propagates into downstream metrics without an error pointer"
    )


def test_merge_matching_schema_unchanged(tmp_path):
    """Normal-path: a new_data frame whose schema exactly matches existing
    must merge correctly, unchanged (no data loss, no dtype change).
    """
    cache = ParquetCache(tmp_path / "cache")
    base = _base_df()
    cache.write("AAPL", Timeframe.DAY_1, base)

    new = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-15"], utc=True),
            "open": pd.array([103.0], dtype="float64"),
            "high": pd.array([104.0], dtype="float64"),
            "low": pd.array([102.0], dtype="float64"),
            "close": pd.array([103.5], dtype="float64"),
            "volume": pd.array([1_200_000], dtype="int64"),
            "vwap": pd.array([103.2], dtype="float64"),
        }
    )
    cache.merge("AAPL", Timeframe.DAY_1, new)

    result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None
    assert len(result) == 3
    assert list(result.columns) == _BASE_COLUMNS
    assert result["close"].dtype == "float64"
    assert float(result["close"].iloc[-1]) == pytest.approx(103.5)


# ---------------------------------------------------------------------------
# Bug 3b — DTYPE / TZ DRIFT (the actual downstream-corruption vector)
#
# Column-set validation alone is insufficient: new_data can carry the right
# COLUMNS but the wrong DTYPES.  A numeric column arriving as object/string,
# or a tz-naive timestamp concatenated onto a tz-aware one, produces a mixed
# parquet that reads back with object columns — silently breaking every
# numeric op downstream with no pointer back to the merge.
# ---------------------------------------------------------------------------


def _numeric_as_object_df() -> pd.DataFrame:
    """New data whose `close` arrived as object (numeric-looking strings).

    This is the realistic provider-drift case: a JSON/CSV ingestion path
    that forgot to cast, so the values are coercible but the dtype is wrong.
    """
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-15"], utc=True),
            "open": pd.array([103.0], dtype="float64"),
            "high": pd.array([104.0], dtype="float64"),
            "low": pd.array([102.0], dtype="float64"),
            "close": pd.array(["103.5"], dtype="object"),  # ← dtype drift
            "volume": pd.array([1_200_000], dtype="int64"),
            "vwap": pd.array([103.2], dtype="float64"),
        }
    )


def _non_numeric_object_df() -> pd.DataFrame:
    """New data whose `close` is object AND not coercible to a number."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-15"], utc=True),
            "open": pd.array([103.0], dtype="float64"),
            "high": pd.array([104.0], dtype="float64"),
            "low": pd.array([102.0], dtype="float64"),
            "close": pd.array(["N/A"], dtype="object"),  # ← lossy / ambiguous
            "volume": pd.array([1_200_000], dtype="int64"),
            "vwap": pd.array([103.2], dtype="float64"),
        }
    )


def _tz_naive_timestamp_df() -> pd.DataFrame:
    """New data whose timestamp is tz-naive while existing is tz-aware (UTC)."""
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2025-01-15"]),  # ← tz-naive drift
            "open": pd.array([103.0], dtype="float64"),
            "high": pd.array([104.0], dtype="float64"),
            "low": pd.array([102.0], dtype="float64"),
            "close": pd.array([103.5], dtype="float64"),
            "volume": pd.array([1_200_000], dtype="int64"),
            "vwap": pd.array([103.2], dtype="float64"),
        }
    )


def test_merge_numeric_column_as_object_coerces_or_raises(tmp_path):
    """A numeric column arriving as object (numeric-looking strings) must be
    safely coerced to the existing numeric dtype — never silently concatenated
    as a mixed/object column.

    Pre-fix: pd.concat produces an object `close` column; the parquet reads
    back object and every float op downstream silently breaks.

    Post-fix: the merge coerces "103.5" → 103.5 (float64) because the
    conversion is unambiguous and lossless, OR raises a clear error. Either
    way the on-disk `close` must not be an object column.
    """
    cache = ParquetCache(tmp_path / "cache")
    cache.write("AAPL", Timeframe.DAY_1, _base_df())

    # "103.5" → 103.5 is unambiguous and lossless, so the merge MUST succeed
    # by coercing.  (Pre-fix this raises a cryptic pyarrow ArrowInvalid at
    # parquet-write time — deep in the write path, not actionable, and never
    # coerced.  That is the corruption vector this test pins down.)
    cache.merge("AAPL", Timeframe.DAY_1, _numeric_as_object_df())

    result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None
    assert result["close"].dtype == "float64", (
        f"close dtype drifted to {result['close'].dtype} — a numeric-looking "
        "object column was not coerced to the existing numeric dtype"
    )
    assert float(result["close"].iloc[-1]) == pytest.approx(103.5)
    # No NaN injected for the existing rows, and the existing dtype is intact.
    assert result["close"].notna().all()
    assert len(result) == 3


def test_merge_non_numeric_object_raises_clearly(tmp_path):
    """A numeric column arriving as object with NON-numeric values must raise a
    clear, actionable error — coercion would be lossy/ambiguous and a silent
    NaN concat is exactly the corruption vector.
    """
    cache = ParquetCache(tmp_path / "cache")
    cache.write("AAPL", Timeframe.DAY_1, _base_df())

    with pytest.raises((ValueError, TypeError)) as excinfo:
        cache.merge("AAPL", Timeframe.DAY_1, _non_numeric_object_df())

    # The error must be raised by the merge's own dtype validation — a clear,
    # actionable message that names the merge, the offending column, and the
    # dtype problem — NOT a cryptic pyarrow/pandas internal raised deep in the
    # parquet-write path (pre-fix behaviour).
    msg = str(excinfo.value)
    assert "ParquetCache.merge(" in msg, (
        "dtype drift was not caught at the merge boundary — the error did not "
        f"originate from merge's own validation: {excinfo.value!r}"
    )
    assert "close" in msg and "dtype" in msg.lower(), (
        f"error message is not actionable about the dtype drift: {excinfo.value!r}"
    )

    # The cache must be untouched (no partial/corrupt write).
    result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None
    assert len(result) == 2
    assert result["close"].dtype == "float64"


def test_merge_tz_mismatch_aligns_or_raises(tmp_path):
    """A tz-naive timestamp merged onto a tz-aware (UTC) cache must either be
    aligned to the existing tz or rejected — never silently concatenated into
    a mixed/object timestamp column.

    Pre-fix: pd.concat of tz-aware + tz-naive yields an object timestamp
    column; sort/dedup and every downstream `.dt` access silently breaks.

    Post-fix: new_data's timestamp is localized to the existing tz (UTC), OR
    a clear error is raised. The on-disk timestamp must remain a proper
    tz-aware datetime column.
    """
    cache = ParquetCache(tmp_path / "cache")
    cache.write("AAPL", Timeframe.DAY_1, _base_df())

    # Aligning a tz-naive timestamp to the existing UTC tz is unambiguous
    # (Milodex bars are UTC by contract — see BarSet column contract), so the
    # merge MUST succeed by localizing.  (Pre-fix this raises a TypeError from
    # pandas' concat of tz-aware + tz-naive — the corruption vector this test
    # pins down.)
    cache.merge("AAPL", Timeframe.DAY_1, _tz_naive_timestamp_df())

    result = cache.read("AAPL", Timeframe.DAY_1)
    assert result is not None
    ts_dtype = result["timestamp"].dtype
    assert isinstance(ts_dtype, pd.DatetimeTZDtype), (
        f"timestamp dtype drifted to {ts_dtype} — tz-naive row was not "
        "aligned to the existing tz-aware dtype"
    )
    assert str(ts_dtype.tz).upper() in ("UTC", "UTC+00:00")
    assert len(result) == 3
    assert result["timestamp"].notna().all()
    # The localized row must be present and correct (2025-01-15 UTC).
    assert pd.Timestamp("2025-01-15", tz="UTC") in set(result["timestamp"])
