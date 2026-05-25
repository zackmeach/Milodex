"""Intraday regression suite for BacktestEngine — equity-curve baseline.

Analog of :mod:`test_engine_daily_regression` for the intraday simulation path
(``_simulate_intraday``).  Captures equity-curve values + exact trade counts
from the current intraday kernel.  Any future code change that shifts these
numbers will fail one of these tests, surfacing intraday-specific drift that
helper-level intraday tests (``test_engine_intraday.py``) and the daily
regression suite would not catch.

Why this matters
----------------
The Opus regression reviewer 2026-05-24 flagged the lack of an end-to-end
intraday equity-curve regression test as "the strongest single missing
coverage" for the kernel-extract stack (PRs #187-#189).  Helper-level
intraday tests pin event-timeline ordering, opens-at-timestamp resolution,
and mark-to-market arithmetic in isolation — but none of them detect a
fence-post error in the cross-day-boundary path through
``BacktestEngine.run(...)``.

The engine is NOT daily-only (despite a stale CLAUDE.md gotcha that survives
until the kernel-extract stack lands on master).  ``_simulate`` at
``engine.py:820-851`` dispatches on the ``Timeframe`` enum; any strategy with
``tempo.bar_size != "1D"`` reaches ``_simulate_intraday`` via this dispatch.

Test design notes
-----------------
- 2 trading sessions (Jan 8-9, 2024) — covers ONE cross-day boundary,
  the minimum needed to detect day-boundary fence-post errors.
- Custom ``fake_evaluate`` side_effect: BUY at 9:30 ET (first bar), SELL at
  15:55 ET (last bar).  Per the existing intraday smoke test, BUY at bar T
  fills at bar T+1's open; SELL at the last bar of a session fills at the
  next session's 9:30 open.
- slippage_pct=0.0, commission_per_trade=0.0 — equity arithmetic is exact.
- Initial equity = 100_000.0.

If you are changing intraday behavior intentionally, re-baseline these
numbers from a clean run and update the asserts.  Do not weaken the
assertions to fit a drift; investigate the drift first.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from milodex.backtesting.engine import BacktestEngine
from milodex.broker.models import OrderSide, OrderType
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies.base import DecisionReasoning, StrategyContext, StrategyDecision

# ---------------------------------------------------------------------------
# Fixtures + helpers (mirrors test_engine_intraday._build_synthetic_5min_barset
# and test_engine_daily_regression._make_loaded_strategy patterns)
# ---------------------------------------------------------------------------


def _decision(intents: list) -> StrategyDecision:
    return StrategyDecision(
        intents=list(intents),
        reasoning=DecisionReasoning(rule="no_signal", narrative="intraday regression stub"),
    )


def _build_synthetic_5min_barset(date_strs: list[str], symbol: str = "SPY") -> BarSet:
    """Build a multi-day OHLCV BarSet of 5min bars for the given session dates.

    Each session is a full 9:30-16:00 ET (78 bars).  Prices are deterministic:
    open = 500 + (session_index * 0.10) + (bar_index * 0.01); close = open + 0.02.
    Mirrors :func:`tests.milodex.backtesting.test_engine_intraday._build_synthetic_5min_barset`
    exactly so the same OHLC arithmetic underlies both suites.
    """
    rows: list[dict] = []
    for sess_idx, date_str in enumerate(date_strs):
        open_et = pd.Timestamp(f"{date_str} 09:30:00").tz_localize("America/New_York")
        open_utc = open_et.tz_convert("UTC")
        for bar_idx in range(78):
            bar_ts = open_utc + pd.Timedelta(minutes=5 * bar_idx)
            base = 500.0 + sess_idx * 0.10 + bar_idx * 0.01
            rows.append(
                {
                    "timestamp": bar_ts,
                    "open": round(base, 4),
                    "high": round(base + 0.05, 4),
                    "low": round(base - 0.05, 4),
                    "close": round(base + 0.02, 4),
                    "volume": 100_000,
                    "vwap": round(base + 0.01, 4),
                }
            )

    df = pd.DataFrame(rows)
    return BarSet(df)


def _make_intraday_loaded_strategy(strategy_id: str, universe: tuple[str, ...]) -> MagicMock:
    """Mock LoadedStrategy configured for 5min intraday backtest.

    Identical config shape to
    :func:`tests.milodex.backtesting.test_engine_intraday._make_intraday_loaded_strategy`
    — kept separate so future intraday-config tweaks for the smoke suite do
    not silently shift this regression fixture.
    """
    tmp_dir = Path(tempfile.mkdtemp())
    yaml_text = """\
strategy:
  name: "intraday_regression_test"
  version: 1
  description: "Intraday regression fixture (5min)."
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "5Min"
    min_hold_days: 0
    max_hold_days: 1
  risk:
    max_position_pct: 0.50
    max_positions: 1
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: 1
"""
    config_path = tmp_dir / "intraday_regression.yaml"
    config_path.write_text(yaml_text, encoding="utf-8")

    config = MagicMock()
    config.strategy_id = strategy_id
    config.family = "regression"
    config.template = "intraday.fixed_long"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.tempo = {"bar_size": "5Min"}
    config.universe = universe
    config.risk = {"max_position_pct": 0.50, "max_positions": 1}

    context = StrategyContext(
        strategy_id=strategy_id,
        family="regression",
        template="intraday.fixed_long",
        variant="regression",
        version=1,
        config_hash="intraday_regression_hash",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path=str(config_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = _decision([])
    strategy.max_lookback_periods.return_value = 0

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def _make_intraday_engine(
    loaded: MagicMock,
    bars_by_symbol: dict[str, BarSet],
    initial_equity: float = 100_000.0,
) -> BacktestEngine:
    """Wire an intraday BacktestEngine with a mock provider."""
    provider = MagicMock()
    provider.get_bars.return_value = bars_by_symbol
    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    store = EventStore(tmp_db)
    return BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=initial_equity,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )


# ---------------------------------------------------------------------------
# Regression test 1: intra-session round trip with cross-day fence-post
#
# Pins the intraday equity-curve shape across a session boundary.  A future
# refactor that mis-keys broker snapshots (e.g. switches sync_day from outer
# `day` to inner `ts.date()`) would shift equity-curve dates / values and
# break this test.
#
# Strategy logic: on the first ``evaluate`` call within each session, emit a
# BUY 5-share intent; on the last evaluate of each session (the ~15:55 ET
# bar), emit a SELL.  Per the existing intraday engine semantics:
#   - BUY at bar T fills at bar T+1's open
#   - SELL at the final bar of a session fills at the next session's 9:30 open
#   - SELL at the final bar of the FINAL session has no next session → stranded
#
# Universe: SPY only. 2 sessions (Mon 2024-01-08, Tue 2024-01-09).
# ---------------------------------------------------------------------------


def test_intraday_regression_cross_day_round_trip() -> None:
    """One round trip per session over 2 sessions; final SELL is stranded.

    Pinned counts AND equity_curve shape are baselined from the current
    kernel — any drift surfaces here before it lands in production audit data.
    """
    session_dates = ["2024-01-08", "2024-01-09"]
    start_date = date(2024, 1, 8)
    end_date = date(2024, 1, 9)

    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")

    loaded = _make_intraday_loaded_strategy("regression.intraday.fixed_long.spy.v1", ("SPY",))

    # Track per-session "have we emitted BUY yet?" and "is this the last bar?"
    # state.  The last bar of the 9:30-16:00 ET session is bar_index 77 of 78
    # (since bar 78 doesn't exist — 9:30+5*78=15:55 is the start of the last
    # 5min bar; the next event is 16:00 which is a pure decision tick for the
    # 15:55 bar).
    buys_emitted_per_day: dict[date, bool] = {}
    sells_emitted_per_day: dict[date, bool] = {}

    def fake_evaluate(bars: BarSet, context):  # noqa: ANN001
        df = bars.to_dataframe()
        if df.empty:
            return _decision([])

        latest_ts = pd.to_datetime(df["timestamp"], utc=True).max()
        latest_date = latest_ts.date()

        # Detect "last bar of the day" by checking ET wall clock (the 15:55 ET
        # bar is the final session bar; its timestamp is 20:55 UTC).
        latest_et = latest_ts.tz_convert("America/New_York")
        is_last_bar_of_day = latest_et.hour == 15 and latest_et.minute >= 55

        has_position = "SPY" in context.positions and context.positions["SPY"] > 0

        # BUY at first decision tick of each session if no position.
        if not has_position and not buys_emitted_per_day.get(latest_date, False):
            buys_emitted_per_day[latest_date] = True
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY",
                        side=OrderSide.BUY,
                        quantity=5.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )

        # SELL at last bar of each session if we have a position.
        if (
            has_position
            and is_last_bar_of_day
            and not sells_emitted_per_day.get(latest_date, False)
        ):
            sells_emitted_per_day[latest_date] = True
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY",
                        side=OrderSide.SELL,
                        quantity=5.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )

        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})
    result = engine.run(start_date, end_date)

    # --- Encoded from baseline run (intraday kernel as of 2026-05-24) ---
    # Each session emits 1 BUY + 1 SELL.
    # Day 1 BUY: fills at next-bar open (9:35 ET on Day 1).
    # Day 1 SELL: fills at next session's 9:30 open (Day 2).
    # Day 2 BUY: fills at next-bar open (9:35 ET on Day 2).
    # Day 2 SELL: NO next session → stranded → skipped_count = 1.
    # Net: 2 BUY fills, 1 SELL fill, 1 stranded SELL.
    assert result.buy_count == 2, f"expected 2 BUY fills, got {result.buy_count}"
    assert result.sell_count == 1, f"expected 1 SELL fill, got {result.sell_count}"
    assert result.trade_count == 3, f"expected 3 total fills, got {result.trade_count}"
    assert result.skipped_count == 1, (
        f"expected 1 stranded SELL (last session's SELL has no next bar), "
        f"got {result.skipped_count}"
    )
    assert result.round_trip_count == 1, (
        f"expected 1 round trip (Day 1 BUY closed by Day 2 SELL fill); "
        f"Day 2 BUY never closes; got {result.round_trip_count}"
    )

    # Equity-curve shape: one point per outer trading day (sync_broker_state
    # uses outer `day`, NOT `ts.date()` — this is the key intraday invariant
    # we are pinning).
    assert len(result.equity_curve) == 2, (
        f"expected one equity_curve point per OUTER trading day "
        f"(intraday sync_broker_state(day=outer_day) invariant); "
        f"got {len(result.equity_curve)}"
    )
    assert result.equity_curve[0][0] == start_date
    assert result.equity_curve[1][0] == end_date

    # Final equity: 1 closed round trip (5 shares × ~$0.39 spread between
    # entry fill at Day 1 9:35 open and exit fill at Day 2 9:30 open) +
    # 1 open position marked to last seen close.  Loose bound: P&L is
    # bounded in [-10, +10] dollars over $100,000.  Any value outside that
    # range indicates a serious arithmetic drift.
    pnl = result.final_equity - 100_000.0
    assert -10.0 < pnl < 10.0, (
        f"intraday round trip P&L should be in [-10, +10] over deterministic "
        f"$500-base bars; got {pnl:.4f}"
    )
    # Tighter pin to catch silent drift: final equity must equal the
    # day-2 equity_curve point exactly (consistency invariant).
    assert abs(result.final_equity - result.equity_curve[-1][1]) < 0.01, (
        f"final_equity ({result.final_equity:.4f}) must equal last "
        f"equity_curve point ({result.equity_curve[-1][1]:.4f}) "
        f"— intraday consistency invariant"
    )


# ---------------------------------------------------------------------------
# Regression test 2: held-days tick-per-outer-day invariant
#
# Pins the documented contract that ``tick_held_days()`` ticks ONCE per outer
# trading day (not per intraday evaluate).  A future refactor that ticks per
# evaluate would cause max_hold_days exits to fire mid-session instead of at
# the next session's first bar.
#
# Strategy: BUY on Day 1 first bar.  max_hold_days=1.  Expected: SELL fires
# on Day 2's first evaluate (held_days flipped to 1 at Day 2 start).
# ---------------------------------------------------------------------------


def test_intraday_regression_held_days_ticks_per_outer_day() -> None:
    """held_days increments per outer trading day, not per intraday tick.

    Pinned by checking that a max_hold_days=1 strategy entering on Day 1
    sees ``held_days >= 1`` only on Day 2's first evaluate — not after
    several intraday bars on Day 1.
    """
    session_dates = ["2024-01-08", "2024-01-09", "2024-01-10"]
    start_date = date(2024, 1, 8)
    end_date = date(2024, 1, 10)

    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")
    loaded = _make_intraday_loaded_strategy("regression.intraday.held_days_tick.spy.v1", ("SPY",))

    # Track the per-evaluate (held_days, latest_date) observation.
    observed_held_per_day: dict[date, list[int]] = {}
    bought = {"done": False}

    def fake_evaluate(bars: BarSet, context):  # noqa: ANN001
        df = bars.to_dataframe()
        if df.empty:
            return _decision([])
        latest_date = pd.to_datetime(df["timestamp"], utc=True).max().date()
        held = context.entry_state.get("SPY", {}).get("held_days", 0)
        observed_held_per_day.setdefault(latest_date, []).append(held)

        if not bought["done"]:
            bought["done"] = True
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY",
                        side=OrderSide.BUY,
                        quantity=5.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})
    engine.run(start_date, end_date)

    # Day 1: held_days observations should ALL be 0 (we hadn't entered yet on
    # the first evaluate, then entered; tick_held_days runs at start of NEXT
    # outer day).
    day_1 = date(2024, 1, 8)
    assert all(h == 0 for h in observed_held_per_day.get(day_1, [])), (
        f"held_days must remain 0 throughout the entry day; "
        f"observed {observed_held_per_day.get(day_1, [])}"
    )

    # Day 2: held_days observations should ALL be 1 (tick_held_days ran once
    # at start of Day 2; no further ticks during Day 2's many intraday bars).
    day_2 = date(2024, 1, 9)
    day_2_observations = observed_held_per_day.get(day_2, [])
    assert day_2_observations, "expected evaluations on Day 2"
    assert all(h == 1 for h in day_2_observations), (
        f"held_days must be 1 throughout Day 2 (NOT incrementing per intraday "
        f"tick); observed {day_2_observations}"
    )

    # Day 3: held_days observations should ALL be 2.
    day_3 = date(2024, 1, 10)
    day_3_observations = observed_held_per_day.get(day_3, [])
    if day_3_observations:  # Day 3 may not be reached if a stop closed earlier
        assert all(h == 2 for h in day_3_observations), (
            f"held_days must be 2 throughout Day 3; observed {day_3_observations}"
        )
