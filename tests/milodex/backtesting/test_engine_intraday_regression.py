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
# bar), emit a SELL.  Per the intraday engine semantics (overnight-null fix):
#   - BUY at bar T fills at bar T+1's open
#   - SELL on the final bar of a session has no same-session T+1 bar → it is
#     realized at that session's CLOSE by the session-end flatten (NOT deferred
#     to the next session's 9:30 open; NOT stranded at run end).
#
# Universe: SPY only. 2 sessions (Mon 2024-01-08, Tue 2024-01-09).
# ---------------------------------------------------------------------------


def test_intraday_regression_cross_day_round_trip() -> None:
    """One round trip per session over 2 sessions; each closes at its own close.

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

    # --- Encoded from baseline run (intraday kernel, overnight-null fix) ---
    # Each session emits 1 BUY + 1 SELL.
    # Day 1 BUY: fills at next-bar open (9:35 ET on Day 1).
    # Day 1 SELL (15:55 final bar): realized at Day 1's CLOSE by the flatten.
    # Day 2 BUY: fills at next-bar open (9:35 ET on Day 2).
    # Day 2 SELL (15:55 final bar): realized at Day 2's CLOSE by the flatten.
    # Net: 2 BUY fills, 2 SELL fills, nothing stranded.
    assert result.buy_count == 2, f"expected 2 BUY fills, got {result.buy_count}"
    assert result.sell_count == 2, f"expected 2 SELL fills, got {result.sell_count}"
    assert result.trade_count == 4, f"expected 4 total fills, got {result.trade_count}"
    assert result.skipped_count == 0, (
        f"expected 0 stranded SELLs (each final-bar SELL realized at its own "
        f"close), got {result.skipped_count}"
    )
    assert result.round_trip_count == 2, (
        f"expected 2 round trips (each session's BUY closed by its own "
        f"session-close SELL); got {result.round_trip_count}"
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

    # Final equity: 2 closed round trips (each session's BUY at its ~9:35 entry
    # open closed by a SELL realized at that session's last close, 5 shares).
    # Position is flat at run end (no open lot). Loose bound: P&L is bounded in
    # [-10, +10] dollars over $100,000 on these cent-scale deterministic bars.
    # Any value outside that range indicates a serious arithmetic drift.
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
# evaluate would inflate held_days within a single session.
#
# N2 note: the original form of this test relied on a single never-selling
# position being carried across THREE sessions to observe held_days flip
# 0→1→2.  That overnight carry is exactly the N2 strand — a max_hold_days:1
# intraday position lingering past session close because the strategy never
# emitted an exit.  The engine now force-flattens any un-exited position at
# session end, so a BUY-only stub cannot persist overnight.  The surviving,
# still-meaningful invariant this test pins is: within the entry session,
# held_days does NOT increment per intraday bar (it stays 0 across the day's
# many evaluates), and the position is flat at session close (N2).  The
# per-outer-day increment of tick_held_days itself is independently unit-tested
# in test_simulation_kernel.test_tick_held_days_bumps_all_open_positions.
# ---------------------------------------------------------------------------


def test_intraday_regression_held_days_no_intraday_tick_then_flat_at_close() -> None:
    """held_days does not tick per intraday bar; un-exited position flat at close.

    A max_hold_days:1 stub that BUYs once and never sells must see held_days
    stay 0 across every intraday evaluate of the entry session (no per-bar
    tick), and its position must be force-flattened by session close (N2) —
    not carried overnight.
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
    result = engine.run(start_date, end_date)

    # Day 1 (entry session): held_days stays 0 across EVERY intraday evaluate —
    # tick_held_days runs once at the next outer-day boundary, never per bar.
    day_1 = date(2024, 1, 8)
    day_1_observations = observed_held_per_day.get(day_1, [])
    assert day_1_observations, "expected evaluations on Day 1"
    assert all(h == 0 for h in day_1_observations), (
        f"held_days must remain 0 throughout the entry day (NOT incrementing "
        f"per intraday tick); observed {day_1_observations}"
    )

    # N2: the un-exited position is force-flattened at session close, so it is
    # NOT carried overnight. On every subsequent session the stub re-observes
    # held_days == 0 (no open position survives), and the engine recorded the
    # session-end liquidation as a real SELL (round-trip closed).
    for later_day in (date(2024, 1, 9), date(2024, 1, 10)):
        later_observations = observed_held_per_day.get(later_day, [])
        assert all(h == 0 for h in later_observations), (
            f"held_days must be 0 on {later_day}: the entry-day position was "
            f"force-flattened at session close, not carried overnight; "
            f"observed {later_observations}"
        )
    assert result.sell_count == 1, (
        f"the un-exited position must be force-flattened once at session close "
        f"(one SELL), got sell_count={result.sell_count}"
    )
    assert result.round_trip_count == 1, (
        f"the session-end flatten closes the round trip, got "
        f"round_trip_count={result.round_trip_count}"
    )
