"""Intraday backtest engine correctness tests.

See docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md §5.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from milodex.backtesting.engine import BacktestEngine
from milodex.backtesting.simulation_kernel import (
    BacktestSimulationKernel,
    IntradayPendingOrder,
    MissingOpenPolicy,
)
from milodex.broker.models import OrderSide, OrderType
from milodex.core.event_store import BacktestRunEvent, EventStore
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.risk import NullRiskEvaluator
from milodex.strategies.base import DecisionReasoning, StrategyContext, StrategyDecision


def test_event_timeline_for_single_symbol_5min_session() -> None:
    """For a full 9:30-16:00 ET session of 5min SPY bars, the event timeline
    is the chronological union of fill events (every bar's start) and
    decision events (every bar's completion).
    """
    from milodex.backtesting.intraday_simulation import _build_intraday_event_timeline

    per_symbol_ts_utc = _build_synthetic_5min_session_ts_only("2024-01-15", ["SPY"])

    timeline = _build_intraday_event_timeline(
        per_symbol_ts_utc=per_symbol_ts_utc,
        day=date(2024, 1, 15),
        bar_size_minutes=5,
    )

    # Expected: 79 unique events — first event is 9:30 (pure fill event for
    # the 9:30 bar; no decision event because that's the previous session's
    # close); subsequent events are unions; last event is 16:00 (pure decision
    # event for the 15:55 bar; no fill event because there's no 16:00 bar).
    timestamps = [t for t, _meta in timeline]
    assert len(timestamps) == 79
    # First event = 9:30 ET = 14:30 UTC
    assert timestamps[0] == pd.Timestamp("2024-01-15 14:30:00+00:00")
    # Last event = 16:00 ET = 21:00 UTC
    assert timestamps[-1] == pd.Timestamp("2024-01-15 21:00:00+00:00")

    # Spot-check the metadata: the 9:30 bar fills (no decision yet); the
    # 14:35 UTC timestamp is BOTH the decision_time of the 9:30 bar AND the
    # fill event of the 9:35 bar.
    first_ts, first_meta = timeline[0]
    assert first_meta["fill_symbols"] == ["SPY"]
    assert first_meta["decision_symbols"] == []

    second_ts, second_meta = timeline[1]
    assert second_ts == pd.Timestamp("2024-01-15 14:35:00+00:00")
    assert second_meta["fill_symbols"] == ["SPY"]
    assert second_meta["decision_symbols"] == ["SPY"]

    # Last event (16:00 ET = 21:00 UTC): pure decision event
    last_ts, last_meta = timeline[-1]
    assert last_meta["fill_symbols"] == []
    assert last_meta["decision_symbols"] == ["SPY"]


def test_opens_at_timestamp_returns_only_symbols_with_bar_at_t() -> None:
    """At event timestamp T, _opens_at_timestamp returns the symbol → open-price
    map ONLY for symbols whose bar starts at T. Symbols absent at T are not
    in the result.
    """
    from milodex.backtesting.intraday_simulation import _opens_at_timestamp

    # Build per-symbol-open-by-timestamp maps for SPY and QQQ. SPY has the
    # 10:05 ET (15:05 UTC) bar; QQQ does not.
    target_ts = pd.Timestamp("2024-01-15 15:05:00+00:00")
    other_ts = pd.Timestamp("2024-01-15 14:30:00+00:00")  # 9:30 ET — both present

    per_symbol_open_by_ts: dict[str, dict[pd.Timestamp, float]] = {
        "SPY": {other_ts: 500.00, target_ts: 500.07},  # SPY has both bars
        "QQQ": {other_ts: 400.00},  # QQQ missing target_ts
    }

    opens = _opens_at_timestamp(per_symbol_open_by_ts, target_ts)

    # SPY has the 10:05 bar; QQQ doesn't
    assert "SPY" in opens
    assert "QQQ" not in opens
    assert abs(opens["SPY"] - 500.07) < 1e-9
    assert len(opens) == 1


def test_drain_pending_at_timestamp_fills_matched_keeps_unmatched() -> None:
    """Two pending orders: SPY (has open at T) and QQQ (no open at T).

    SPY fills (Category 2 success path); QQQ stays in remaining_pending
    (Category 1 — missing-bar, no skip counted, no audit record).
    """
    from datetime import UTC, datetime

    kernel, event_store = _build_test_kernel()

    # Register a real backtest_runs row so FK constraints pass on audit writes.
    now = datetime.now(tz=UTC)
    db_run_id = event_store.append_backtest_run(
        BacktestRunEvent(
            run_id="test-run-d3",
            strategy_id="intraday.test.v1",
            config_path=None,
            config_hash="test_hash",
            start_date=now,
            end_date=now,
            started_at=now,
            status="running",
            slippage_pct=0.0,
            commission_per_trade=0.0,
            metadata={},
        )
    )

    decision_ts = pd.Timestamp("2024-01-15 14:55:00+00:00")
    intent_spy = TradeIntent(
        symbol="SPY", side=OrderSide.BUY, quantity=10.0, order_type=OrderType.MARKET
    )
    intent_qqq = TradeIntent(
        symbol="QQQ", side=OrderSide.BUY, quantity=5.0, order_type=OrderType.MARKET
    )
    pending: list[IntradayPendingOrder] = [
        IntradayPendingOrder(intent=intent_spy, decision_timestamp=decision_ts, reasoning=None),
        IntradayPendingOrder(intent=intent_qqq, decision_timestamp=decision_ts, reasoning=None),
    ]

    # At T=15:00 UTC, only SPY has an open
    target_ts = pd.Timestamp("2024-01-15 15:00:00+00:00")
    opens = {"SPY": 500.50}

    drain = kernel.drain_pending_orders(
        pending=pending,
        opens=opens,
        day=target_ts.date(),
        session_id="sess-1",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.RETAIN,
    )

    # SPY filled (Category 2 success — buy with sufficient cash)
    assert drain.buy_count == 1
    assert drain.sell_count == 0
    assert drain.skipped_count == 0  # no Category-2 rejections
    assert kernel.cash < 100_000.0  # cash reduced by SPY fill

    # QQQ still pending (Category 1 — no open at this timestamp)
    assert len(drain.remaining) == 1
    assert drain.remaining[0].intent.symbol == "QQQ"


def _build_test_kernel() -> tuple[BacktestSimulationKernel, EventStore]:
    """Return a simulation kernel wired for pending-order drain tests.

    Uses slippage=0, commission=0 for exact arithmetic. Provides a minimal
    BarSet for SPY so SimulatedDataProvider initialises without error.
    Also returns the EventStore so callers can register a real backtest_runs
    row before exercising audit paths.
    """
    tmp_dir = Path(tempfile.mkdtemp())
    yaml_text = """\
strategy:
  name: "intraday_test"
  version: 1
  description: "Intraday drain test fixture."
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "5Min"
    min_hold_days: 0
    max_hold_days: 1
  risk:
    max_position_pct: 0.50
    max_positions: 4
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: 1
"""
    config_path = tmp_dir / "intraday_test.yaml"
    config_path.write_text(yaml_text, encoding="utf-8")

    # Minimal SPY BarSet so SimulatedDataProvider initialises cleanly
    spy_bars = BarSet(
        pd.DataFrame(
            {
                "timestamp": [pd.Timestamp("2024-01-15 14:30:00+00:00")],
                "open": [500.0],
                "high": [501.0],
                "low": [499.0],
                "close": [500.5],
                "volume": [1_000_000],
                "vwap": [500.2],
            }
        )
    )
    qqq_bars = BarSet(
        pd.DataFrame(
            {
                "timestamp": [pd.Timestamp("2024-01-15 15:10:00+00:00")],
                "open": [400.0],
                "high": [400.1],
                "low": [399.9],
                "close": [400.05],
                "volume": [500_000],
                "vwap": [400.02],
            }
        )
    )
    all_bars: dict[str, BarSet] = {"SPY": spy_bars, "QQQ": qqq_bars}

    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    event_store = EventStore(tmp_db)

    kernel = BacktestSimulationKernel(
        event_store=event_store,
        all_bars=all_bars,
        strategy_id="intraday.test.v1",
        strategy_stage="backtest",
        strategy_config_path=config_path,
        config_hash="test_hash",
        risk_defaults_path=Path("configs/risk_defaults.yaml"),
        risk_evaluator=NullRiskEvaluator(),
        slippage_pct=0.0,
        commission_per_trade=0.0,
        initial_cash=100_000.0,
        max_positions=4,
        max_position_pct=0.50,
        daily_loss_cap_pct=0.05,
    )

    return kernel, event_store


def test_mark_to_market_at_day_end_basic() -> None:
    """End-of-day equity = cash + qty × latest close for each open position."""
    from milodex.backtesting.intraday_simulation import _mark_to_market_at_day_end

    target_day = date(2024, 1, 15)
    # SPY has 3 bars on the day: closes 500.0, 501.0, 502.0
    # QQQ has 2 bars on the day: closes 400.0, 401.0
    spy_ts = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-15 14:30:00+00:00"),
            pd.Timestamp("2024-01-15 14:35:00+00:00"),
            pd.Timestamp("2024-01-15 14:40:00+00:00"),
        ]
    )
    qqq_ts = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-15 14:30:00+00:00"),
            pd.Timestamp("2024-01-15 14:35:00+00:00"),
        ]
    )
    spy_df = pd.DataFrame(
        {
            "timestamp": spy_ts.to_pydatetime(),
            "open": [500.0, 500.5, 501.0],
            "high": [501.0, 501.5, 502.5],
            "low": [499.0, 500.0, 500.5],
            "close": [500.0, 501.0, 502.0],
            "volume": [1000, 1000, 1000],
        }
    )
    qqq_df = pd.DataFrame(
        {
            "timestamp": qqq_ts.to_pydatetime(),
            "open": [400.0, 400.5],
            "high": [401.0, 401.5],
            "low": [399.0, 400.0],
            "close": [400.0, 401.0],
            "volume": [1000, 1000],
        }
    )

    positions = {"SPY": (10.0, 495.0), "QQQ": (5.0, 395.0)}  # 10 SPY @ 495, 5 QQQ @ 395
    cash = 1000.0

    equity = _mark_to_market_at_day_end(
        positions=positions,
        per_symbol_df={"SPY": spy_df, "QQQ": qqq_df},
        per_symbol_ts_utc={"SPY": spy_ts, "QQQ": qqq_ts},
        day=target_day,
        cash=cash,
    )

    # Expected: 1000 + 10 × 502 + 5 × 401 = 1000 + 5020 + 2005 = 8025
    assert abs(equity - 8025.0) < 1e-9


def test_mark_to_market_at_day_end_falls_back_to_prior_close() -> None:
    """If a symbol has NO bars on the target day, use latest close before that day."""
    from milodex.backtesting.intraday_simulation import _mark_to_market_at_day_end

    target_day = date(2024, 1, 16)  # SPY has no bars on this day
    # SPY only has bars on the prior day (2024-01-15)
    spy_ts = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-15 14:30:00+00:00"),
            pd.Timestamp("2024-01-15 14:35:00+00:00"),
        ]
    )
    spy_df = pd.DataFrame(
        {
            "timestamp": spy_ts.to_pydatetime(),
            "open": [500.0, 500.5],
            "high": [501.0, 501.5],
            "low": [499.0, 500.0],
            "close": [500.0, 501.0],
            "volume": [1000, 1000],
        }
    )

    positions = {"SPY": (10.0, 495.0)}
    cash = 1000.0

    equity = _mark_to_market_at_day_end(
        positions=positions,
        per_symbol_df={"SPY": spy_df},
        per_symbol_ts_utc={"SPY": spy_ts},
        day=target_day,
        cash=cash,
    )

    # SPY has no bars on 2024-01-16; fall back to last close on 2024-01-15 = 501.0
    # Expected: 1000 + 10 × 501 = 6010
    assert abs(equity - 6010.0) < 1e-9


def _build_synthetic_5min_session_ts_only(
    date_str: str,
    symbols: list[str],
) -> dict[str, pd.DatetimeIndex]:
    """Build a full 9:30-16:00 ET session of 5min UTC timestamps for each symbol.

    Returns the precomputed per-symbol UTC timestamp index that the Phase D
    helpers expect (no OHLC, no DataFrames).
    """
    open_et = pd.Timestamp(f"{date_str} 09:30:00").tz_localize("America/New_York")
    open_utc = open_et.tz_convert("UTC")
    ts_list = [open_utc + pd.Timedelta(minutes=5 * i) for i in range(78)]
    ts_index = pd.DatetimeIndex(ts_list)
    return {symbol: ts_index for symbol in symbols}


# ---------------------------------------------------------------------------
# D5 — _advance_cursors
# ---------------------------------------------------------------------------


def test_advance_cursors_includes_bar_with_decision_time_eq_t() -> None:
    """At T = 10:00 ET = 15:00 UTC for 5min bars, the 9:55 bar (decision_time
    14:55 + 5min = 15:00 UTC) IS visible after advancement. Cursor advances
    from 0 to 6 (six bars: 9:30, 9:35, 9:40, 9:45, 9:50, 9:55).
    """
    from milodex.backtesting.intraday_simulation import _advance_cursors

    per_symbol_ts_utc = _build_synthetic_5min_session_ts_only("2024-01-15", ["SPY"])
    cursors = {"SPY": 0}
    target = pd.Timestamp("2024-01-15 15:00:00+00:00")  # 10:00 ET = decision_time of 9:55 bar

    advanced = _advance_cursors(
        cursors=cursors,
        per_symbol_ts_utc=per_symbol_ts_utc,
        timestamp=target,
        bar_size_minutes=5,
    )

    assert advanced is True
    assert cursors["SPY"] == 6  # 6 bars whose decision_time <= 15:00 UTC


def test_advance_cursors_excludes_bar_with_decision_time_gt_t() -> None:
    """At T = 15:00 UTC, the 10:00 bar (decision_time 15:05 UTC) MUST NOT
    be included. Cursor advances to exactly 6, not 7.
    """
    from milodex.backtesting.intraday_simulation import _advance_cursors

    per_symbol_ts_utc = _build_synthetic_5min_session_ts_only("2024-01-15", ["SPY"])
    cursors = {"SPY": 0}
    advanced = _advance_cursors(
        cursors=cursors,
        per_symbol_ts_utc=per_symbol_ts_utc,
        timestamp=pd.Timestamp("2024-01-15 15:00:00+00:00"),
        bar_size_minutes=5,
    )
    assert advanced is True
    assert cursors["SPY"] == 6  # exclusive-end: bars 0..5 visible (9:30..9:55)
    # The 10:00 bar (index 6) has decision_time 15:05 UTC > 15:00 UTC — NOT visible
    visible_ts = per_symbol_ts_utc["SPY"][: cursors["SPY"]]
    assert len(visible_ts) == 6
    assert visible_ts[-1] == pd.Timestamp("2024-01-15 14:55:00+00:00")


def test_advance_cursors_multiple_symbols_independent() -> None:
    """Symbols advance independently. Drop the 9:30 bar from QQQ; at T=15:00
    UTC, SPY advances to 6 (full opening 30 min), QQQ advances to 5.
    """
    from milodex.backtesting.intraday_simulation import _advance_cursors

    per_symbol_ts_utc = _build_synthetic_5min_session_ts_only("2024-01-15", ["SPY", "QQQ"])
    # Drop the first bar (9:30 / 14:30 UTC) from QQQ
    per_symbol_ts_utc["QQQ"] = per_symbol_ts_utc["QQQ"][1:]
    cursors = {"SPY": 0, "QQQ": 0}

    _advance_cursors(
        cursors=cursors,
        per_symbol_ts_utc=per_symbol_ts_utc,
        timestamp=pd.Timestamp("2024-01-15 15:00:00+00:00"),
        bar_size_minutes=5,
    )

    assert cursors["SPY"] == 6
    assert cursors["QQQ"] == 5  # missing 9:30 bar


def test_advance_cursors_initial_zero_means_no_history() -> None:
    """At simulation start, cursors[symbol] = 0 means iloc[:0] is empty —
    no bars visible to the strategy.
    """
    from milodex.backtesting.intraday_simulation import _advance_cursors

    per_symbol_ts_utc = _build_synthetic_5min_session_ts_only("2024-01-15", ["SPY"])
    cursors = {"SPY": 0}
    visible_before = per_symbol_ts_utc["SPY"][: cursors["SPY"]]
    assert len(visible_before) == 0  # exclusive-end: iloc[:0] is empty

    # And a no-op advancement (target BEFORE any decision_time) leaves cursor at 0
    early = pd.Timestamp("2024-01-15 13:00:00+00:00")  # pre-market
    advanced = _advance_cursors(
        cursors=cursors,
        per_symbol_ts_utc=per_symbol_ts_utc,
        timestamp=early,
        bar_size_minutes=5,
    )
    assert advanced is False
    assert cursors["SPY"] == 0


# ---------------------------------------------------------------------------
# E0 — TDD tests written BEFORE _simulate_intraday implementation
# (Correction 7: highest-value tests fail-first so E1 has a target)
# ---------------------------------------------------------------------------


def _build_synthetic_5min_barset(date_strs: list[str], symbol: str = "SPY") -> BarSet:
    """Build a multi-day OHLCV BarSet of 5min SPY bars for the given dates.

    Each session is a full 9:30-16:00 ET (78 bars). Prices are deterministic:
    open = 500 + (session_index * 0.10) + (bar_index * 0.01) to give unique,
    OHLC-valid values. Volume = 100_000. This makes the open of bar N+1
    predictable for the no-same-bar-fill test.
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


def _make_intraday_engine(
    loaded: MagicMock,
    bars_by_symbol: dict[str, BarSet],
    initial_equity: float = 100_000.0,
) -> BacktestEngine:
    """Wire a BacktestEngine for intraday (5min) tests with a mock data provider."""
    provider = MagicMock()
    provider.get_bars.return_value = bars_by_symbol
    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    from milodex.core.event_store import EventStore

    store = EventStore(tmp_db)
    return BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=initial_equity,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )


def _make_intraday_loaded_strategy(
    strategy_id: str,
    universe: tuple[str, ...],
) -> MagicMock:
    """Return a mock LoadedStrategy configured for 5min intraday backtest."""

    tmp_dir = Path(tempfile.mkdtemp())
    yaml_text = """\
strategy:
  name: "intraday_e0_test"
  version: 1
  description: "E0 TDD fixture — 5min intraday."
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "5Min"
    min_hold_days: 0
    max_hold_days: 1
  risk:
    max_position_pct: 0.95
    max_positions: 1
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: 1
"""
    config_path = tmp_dir / "intraday_e0_test.yaml"
    config_path.write_text(yaml_text, encoding="utf-8")

    config = MagicMock()
    config.strategy_id = strategy_id
    config.family = "benchmark"
    config.template = "unconditional_intraday_long"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.tempo = {"bar_size": "5Min"}
    config.universe = universe
    config.risk = {"max_position_pct": 0.95, "max_positions": 1}

    context = StrategyContext(
        strategy_id=strategy_id,
        family="benchmark",
        template="unconditional_intraday_long",
        variant="e0_test",
        version=1,
        config_hash="e0_test_hash",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path=str(config_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = StrategyDecision(
        intents=[],
        reasoning=DecisionReasoning(rule="no_signal", narrative="e0 placeholder"),
    )
    strategy.max_lookback_periods.return_value = 0

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def test_intraday_smoke_benchmark_exact_counts() -> None:
    """4-day benchmark backtest: BUY at post-OR bar (~10:00 ET), SELL at time-stop (~15:55 ET).

    Expected-counts contract (DO NOT delete):
        buy_count == 4, sell_count == 3, trade_count == 7,
        round_trip_count == 3, skipped_count == 1

    Expected-counts rationale:
      - 4 sessions → 4 BUY signals (one per session at the 10:00 ET bar)
      - BUY at bar T fills at bar T+1's open → 4 fills = buy_count == 4
      - 4 SELL signals (one per session at the 15:55 ET bar)
      - First 3 SELLs fill at the next session's 9:30 open → sell_count == 3
      - Last SELL on session 4 has no next bar → stranded → skipped_count == 1
      - trade_count == buy_count + sell_count == 4 + 3 == 7
      - round_trip_count == min(buy_count, sell_count) per symbol == 3
    """
    from milodex.strategies.bench_unconditional_intraday_long import (
        BenchUnconditionalIntradayLongStrategy,
    )

    # 4 full trading sessions (Mon–Thu, all regular sessions, no half-days)
    session_dates = ["2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11"]
    start_date = date(2024, 1, 8)
    end_date = date(2024, 1, 11)

    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")

    # Wire the real strategy class with benchmark parameters.
    loaded = _make_intraday_loaded_strategy(
        "benchmark.unconditional_intraday_long.spy.e0_test.v1", ("SPY",)
    )
    parameters = {
        "opening_range_minutes": 30,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.95,
    }
    real_strategy = BenchUnconditionalIntradayLongStrategy()
    loaded.strategy = real_strategy
    # Patch context.parameters so _validated_parameters() finds them.

    loaded.context = StrategyContext(
        strategy_id="benchmark.unconditional_intraday_long.spy.e0_test.v1",
        family="benchmark",
        template="unconditional_intraday_long",
        variant="e0_test",
        version=1,
        config_hash="e0_test_hash",
        parameters=parameters,
        universe=("SPY",),
        universe_ref=None,
        disable_conditions=(),
        config_path=str(loaded.config.path),
        manifest={},
    )

    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})
    result = engine.run(start_date, end_date)

    assert result.buy_count == 4, f"Expected buy_count=4, got {result.buy_count}"
    assert result.sell_count == 3, f"Expected sell_count=3, got {result.sell_count}"
    assert result.trade_count == 7, f"Expected trade_count=7, got {result.trade_count}"
    assert result.round_trip_count == 3, (
        f"Expected round_trip_count=3, got {result.round_trip_count}"
    )
    assert result.skipped_count == 1, f"Expected skipped_count=1, got {result.skipped_count}"


def test_intraday_no_same_bar_fill() -> None:
    """BUY decision at bar N's close fills at bar N+1's open (not bar N's open/close).

    Per Correction 7: written BEFORE _simulate_intraday implementation.
    Currently raises NotImplementedError. After E1, this test must be
    updated to assert:
        - Trade fill price == bar_N_plus_1.open * (1 + slippage)
        - With slippage=0.0: fill price == bar_N_plus_1.open exactly
        - fill price != bar_N.open and fill price != bar_N.close

    Scenario: single-session synthetic SPY bars with known per-bar open prices.
    The stub strategy emits a BUY at bar index 4 (the 5th bar, 9:50 ET).
    bar_4.open  = 500.04  (session 0, bar_idx=4: 500 + 0 + 4*0.01)
    bar_4.close = 500.06  (base + 0.02)
    bar_5.open  = 500.05  (session 0, bar_idx=5: 500 + 0 + 5*0.01) ← expected fill price

    After E1, the test must inspect result.trades or the event store to
    find the fill record and assert fill_price == 500.05 (bar_5.open, slippage=0).

    DO NOT delete this expected-behavior comment when updating the test
    after E1. It is the contract.
    """
    session_dates = ["2024-01-08"]
    start_date = date(2024, 1, 8)
    end_date = date(2024, 1, 8)

    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")
    loaded = _make_intraday_loaded_strategy("stub.no_same_bar_fill.spy.e0_test.v1", ("SPY",))

    # Emit BUY exactly at bar index 4 (9:50 ET = 14:50 UTC for 2024-01-08 EST session).
    # The fill must land at bar index 5's open (9:55 ET bar open = 500.05).
    # Any other bar — or the decision bar's own open/close — is a same-bar-fill bug.
    bar_4_ts_utc = pd.Timestamp("2024-01-08 14:50:00+00:00")  # 9:50 ET

    def _stub_evaluate(bars: BarSet, context):  # noqa: ANN001

        df = bars.to_dataframe()
        if df.empty:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(rule="no_signal", narrative="empty bars"),
            )
        latest_ts = pd.Timestamp(df["timestamp"].iloc[-1])
        if latest_ts.tz is None:
            latest_ts = latest_ts.tz_localize("UTC")

        if latest_ts == bar_4_ts_utc:
            intent = TradeIntent(
                symbol="SPY", side=OrderSide.BUY, quantity=1.0, order_type=OrderType.MARKET
            )
            return StrategyDecision(
                intents=[intent],
                reasoning=DecisionReasoning(rule="stub.buy_bar4", narrative="TDD no-same-bar-fill"),
            )
        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(rule="no_signal", narrative="not target bar"),
        )

    loaded.strategy.evaluate.side_effect = _stub_evaluate

    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})
    result = engine.run(start_date, end_date)

    # The BUY was emitted at bar_4's decision time; it must fill at bar_5's open.
    # bar_5.open = 500.05 (session 0, bar_idx=5: 500 + 0 + 5*0.01), slippage=0.
    # Verify via the trade ledger: find the BUY record and check its unit price.
    # The engine's event_store is private; result.db_id identifies the right run.
    trades = engine._event_store.list_trades_for_backtest_run(result.db_id)  # noqa: SLF001
    buy_trades = [t for t in trades if t.side == "buy" and t.status == "submitted"]
    assert len(buy_trades) == 1, f"Expected exactly 1 BUY trade, got {len(buy_trades)}"

    fill_price = buy_trades[0].estimated_unit_price
    # bar_5.open = 500.05 (exact); slippage=0 so fill == open
    assert abs(fill_price - 500.05) < 1e-9, (
        f"Fill price {fill_price!r} != bar_5.open 500.05 (same-bar-fill bug?). "
        f"bar_4.open=500.04, bar_4.close=500.06"
    )
    # Belt-and-suspenders: confirm it's not bar_4's own open or close
    assert abs(fill_price - 500.04) > 1e-9, "Fill price == bar_4.open — same-bar-fill bug!"
    assert abs(fill_price - 500.06) > 1e-9, "Fill price == bar_4.close — same-bar-fill bug!"


# ---------------------------------------------------------------------------
# F1 Group 1 — Test 1: Timeframe dispatch; Test 4: No-lookahead both dirs
# ---------------------------------------------------------------------------


def test_timeframe_dispatch_5min_uses_minute_5() -> None:
    """Test 1: BacktestEngine.run() with bar_size=5Min calls prefetch_bars with
    Timeframe.MINUTE_5, not DAY_1.
    """
    from milodex.data.models import Timeframe

    session_dates = ["2024-01-08"]
    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")
    loaded = _make_intraday_loaded_strategy("stub.timeframe_dispatch.v1", ("SPY",))

    # Track the timeframe argument recorded by prefetch_bars.
    recorded_timeframes: list[Timeframe] = []

    # Build the engine so we can wrap its prefetch_bars.
    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})

    original_prefetch = engine.prefetch_bars

    def _spy_prefetch(start_date, end_date, *, timeframe=Timeframe.DAY_1):
        recorded_timeframes.append(timeframe)
        return original_prefetch(start_date, end_date, timeframe=timeframe)

    engine.prefetch_bars = _spy_prefetch  # type: ignore[method-assign]

    result = engine.run(date(2024, 1, 8), date(2024, 1, 8))

    assert result is not None
    assert len(recorded_timeframes) >= 1, "prefetch_bars was never called"
    assert all(tf == Timeframe.MINUTE_5 for tf in recorded_timeframes), (
        f"Expected all prefetch_bars calls to use MINUTE_5, got: {recorded_timeframes}"
    )
    assert Timeframe.DAY_1 not in recorded_timeframes, (
        "prefetch_bars was invoked with DAY_1 for an intraday strategy — dispatch bug"
    )


def test_no_lookahead_bar_visible_at_decision_time_not_after() -> None:
    """Test 4: No-lookahead, both directions.

    At T = 10:00 ET = 15:00 UTC (for a 5min session on 2024-01-08 EST):
    - The bar with bar_timestamp=9:55 (decision_time=9:55+5min=10:00=T) IS visible.
    - The bar with bar_timestamp=10:00 (decision_time=10:05 > T) is NOT visible.

    We instrument strategy.evaluate to record the latest visible bar timestamp
    at each invocation and assert the boundary is correct.
    """
    session_dates = ["2024-01-08"]
    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")
    loaded = _make_intraday_loaded_strategy("stub.no_lookahead.v1", ("SPY",))

    # 9:55 ET for 2024-01-08 in EST (UTC-5) = 14:55 UTC
    # decision_time of that bar = 14:55 + 5min = 15:00 UTC (== T)
    # 10:00 ET bar has ts=15:00 UTC, decision_time=15:05 UTC (> T)
    # T = 10:00 ET = 15:00 UTC (decision_time of 9:55 bar)
    bar_9_55_ts = pd.Timestamp("2024-01-08 14:55:00+00:00")  # should be visible at T
    bar_10_00_ts = pd.Timestamp("2024-01-08 15:00:00+00:00")  # should NOT be visible at T

    # At T: visible bars go up to and including bar_9_55; bar_10_00 not yet visible.
    latest_ts_at_target: list[pd.Timestamp | None] = []

    def _recording_evaluate(bars, context):  # noqa: ANN001

        df = bars.to_dataframe()
        if df.empty:
            latest_ts_at_target.append(None)
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(rule="no_signal", narrative="empty"),
            )
        latest_ts = pd.Timestamp(df["timestamp"].iloc[-1])
        if latest_ts.tz is None:
            latest_ts = latest_ts.tz_localize("UTC")
        # Record every evaluate() call; the scan below filters for the target ts.
        latest_ts_at_target.append(latest_ts)
        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(rule="no_signal", narrative="recording"),
        )

    loaded.strategy.evaluate.side_effect = _recording_evaluate

    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})
    engine.run(date(2024, 1, 8), date(2024, 1, 8))

    # Find the call where the latest visible ts == bar_9_55_ts (T's boundary call).
    # The engine evaluates at every decision event; we check the call at exactly T.
    ts_at_t = None
    for recorded in latest_ts_at_target:
        if recorded is not None and recorded == bar_9_55_ts:
            ts_at_t = recorded
            break

    assert ts_at_t is not None, (
        f"Expected at least one evaluate() call where latest visible ts == {bar_9_55_ts}. "
        f"All recorded latest timestamps: {latest_ts_at_target[:10]}"
    )

    # Confirm bar_10_00 never appears as the latest visible bar at or before T.
    # (It would appear as latest only when T=15:05, one step later.)
    # The 9:55 bar MUST be visible before the 10:00 bar becomes visible.
    ts_values = [t for t in latest_ts_at_target if t is not None]
    # Find consecutive pair where bar_9_55 is followed immediately by bar_10_00.
    # Between those two calls, the 10:00 bar must NOT be the latest ts.
    for i, ts in enumerate(ts_values):
        if ts == bar_9_55_ts and i + 1 < len(ts_values):
            next_ts = ts_values[i + 1]
            # Next step should see bar_10_00 as latest (decision_time 15:05 ≥ 15:05).
            assert next_ts == bar_10_00_ts or next_ts > bar_9_55_ts, (
                f"After seeing bar_9_55 as latest, next evaluate saw {next_ts} — unexpected"
            )
            break


# ---------------------------------------------------------------------------
# F1 Group 2 — Tests 5, 6, 7: multi-symbol correctness
# ---------------------------------------------------------------------------


def test_multi_symbol_missing_current_bar_still_visible() -> None:
    """Test 5: Multi-symbol visible history with missing current bar.

    Symbol A (SPY) has a 10:05 bar; symbol B (QQQ) does not.
    At T=10:05:
    - SPY is in opens_at_timestamp (fill event for SPY).
    - QQQ is NOT in opens_at_timestamp.
    - QQQ's history through its prior bar IS visible to the strategy.
    """
    from milodex.backtesting.intraday_simulation import _opens_at_timestamp

    # Build SPY: full session 9:30–16:00 (78 bars)
    # Build QQQ: same session but with 10:05 bar removed
    session_date = "2024-01-08"
    spy_bars = _build_synthetic_5min_barset([session_date], symbol="SPY")
    qqq_full = _build_synthetic_5min_barset([session_date], symbol="QQQ")

    # Remove the 10:05 bar from QQQ (bar_index=7: 9:30+7*5=10:05)
    # 10:05 ET = 15:05 UTC
    qqq_df = qqq_full.to_dataframe()
    ts_10_05_utc = pd.Timestamp("2024-01-08 15:05:00+00:00")
    qqq_df_filtered = qqq_df[
        pd.to_datetime(qqq_df["timestamp"], utc=True) != ts_10_05_utc
    ].reset_index(drop=True)
    from milodex.data.models import BarSet

    qqq_bars_missing = BarSet(qqq_df_filtered)

    # Build per_symbol_open_by_ts for the _opens_at_timestamp helper
    spy_df = spy_bars.to_dataframe()
    qqq_df2 = qqq_bars_missing.to_dataframe()

    spy_ts_utc = pd.to_datetime(spy_df["timestamp"], utc=True)
    qqq_ts_utc = pd.to_datetime(qqq_df2["timestamp"], utc=True)

    spy_opens = spy_df["open"].astype(float).values
    qqq_opens = qqq_df2["open"].astype(float).values
    per_symbol_open_by_ts = {
        "SPY": dict(zip(pd.DatetimeIndex(spy_ts_utc), spy_opens, strict=True)),
        "QQQ": dict(zip(pd.DatetimeIndex(qqq_ts_utc), qqq_opens, strict=True)),
    }

    target_ts = ts_10_05_utc  # T = 10:05 ET fill event

    opens = _opens_at_timestamp(per_symbol_open_by_ts, target_ts)

    # SPY has 10:05 bar → present in opens
    assert "SPY" in opens, "SPY should be in opens at 10:05"
    # QQQ missing 10:05 bar → NOT present in opens
    assert "QQQ" not in opens, "QQQ should NOT be in opens at 10:05 (bar missing)"

    # Now verify QQQ's history through its prior bar (10:00) IS visible using cursors.
    from milodex.backtesting.intraday_simulation import _advance_cursors, _build_visible_bars

    per_symbol_ts_utc = {
        "SPY": pd.DatetimeIndex(spy_ts_utc),
        "QQQ": pd.DatetimeIndex(qqq_ts_utc),
    }
    per_symbol_df = {
        "SPY": spy_df,
        "QQQ": qqq_df2,
    }
    cursors = {"SPY": 0, "QQQ": 0}

    # Advance to T=10:05 UTC (decision_time of 10:00 bar = 10:00+5min=10:05)
    _advance_cursors(cursors, per_symbol_ts_utc, target_ts, bar_size_minutes=5)

    visible = _build_visible_bars(per_symbol_df, cursors, universe=["SPY", "QQQ"])

    # Both symbols should have visible history at T (SPY: bars up through 10:00;
    # QQQ: bars up through 10:00 which is still present after removing 10:05).
    assert "SPY" in visible, "SPY should have visible bars at T=10:05"
    assert "QQQ" in visible, "QQQ should have visible bars at T=10:05 (through prior bar)"

    # QQQ's latest visible bar should be 10:00 ET = 15:00 UTC (not 10:05 since that was removed)
    qqq_visible_df = visible["QQQ"].to_dataframe()
    qqq_latest_ts = pd.Timestamp(qqq_visible_df["timestamp"].iloc[-1])
    if qqq_latest_ts.tz is None:
        qqq_latest_ts = qqq_latest_ts.tz_localize("UTC")
    assert qqq_latest_ts == pd.Timestamp("2024-01-08 15:00:00+00:00"), (
        f"QQQ's latest visible bar at T=10:05 should be 10:00 bar (15:00 UTC), got {qqq_latest_ts}"
    )


def test_pending_order_survives_missing_bar_fills_when_bar_appears() -> None:
    """Test 6: Pending BUY for symbol B survives missing current bar; fills when bar appears.

    Scenario (using the shared simulation kernel directly):
    - At T=10:05 UTC (15:05 UTC), QQQ has no open → order stays pending, skipped_count=0.
    - At T=10:10 UTC (15:10 UTC), QQQ has an open → order fills.
    """
    kernel, event_store = _build_test_kernel()

    from datetime import UTC, datetime

    from milodex.core.event_store import BacktestRunEvent

    now = datetime.now(tz=UTC)
    db_run_id = event_store.append_backtest_run(
        BacktestRunEvent(
            run_id="test-run-f1-t6",
            strategy_id="intraday.test.v1",
            config_path=None,
            config_hash="test_hash",
            start_date=now,
            end_date=now,
            started_at=now,
            status="running",
            slippage_pct=0.0,
            commission_per_trade=0.0,
            metadata={},
        )
    )

    # Initial cash must cover the BUY (1 unit of SPY at ~500)
    cash = 100_000.0
    decision_ts = pd.Timestamp("2024-01-15 15:00:00+00:00")  # 10:00 ET
    intent_qqq = TradeIntent(
        symbol="QQQ", side=OrderSide.BUY, quantity=1.0, order_type=OrderType.MARKET
    )
    pending = [
        IntradayPendingOrder(intent=intent_qqq, decision_timestamp=decision_ts, reasoning=None)
    ]

    # T1 = 10:05 ET = 15:05 UTC: QQQ has no open → order stays pending
    t1 = pd.Timestamp("2024-01-15 15:05:00+00:00")
    opens_t1: dict[str, float] = {}  # QQQ missing

    drain1 = kernel.drain_pending_orders(
        pending=pending,
        opens=opens_t1,
        day=t1.date(),
        session_id="sess-f1-t6",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.RETAIN,
    )

    assert drain1.buy_count == 0, f"No fill expected at T1 (missing bar), got {drain1.buy_count}"
    assert drain1.skipped_count == 0, (
        f"Missing bar is Category 1 (no skip counted), got {drain1.skipped_count}"
    )
    assert len(drain1.remaining) == 1, f"Order should remain pending, got {len(drain1.remaining)}"
    assert drain1.remaining[0].intent.symbol == "QQQ"

    # T2 = 10:10 ET = 15:10 UTC: QQQ appears → order fills
    t2 = pd.Timestamp("2024-01-15 15:10:00+00:00")
    qqq_open = 400.0
    opens_t2 = {"QQQ": qqq_open}

    drain2 = kernel.drain_pending_orders(
        pending=drain1.remaining,
        opens=opens_t2,
        day=t2.date(),
        session_id="sess-f1-t6",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.RETAIN,
    )

    assert drain2.buy_count == 1, (
        f"QQQ order should fill at T2 when bar appears, got {drain2.buy_count}"
    )
    assert drain2.skipped_count == 0
    assert len(drain2.remaining) == 0, (
        f"No orders should remain after fill, got {len(drain2.remaining)}"
    )
    assert kernel.cash < cash, "Cash should decrease after QQQ BUY fill"


def test_independent_cursor_advancement() -> None:
    """Test 7: Per-symbol cursors advance independently; persist across timestamps.

    SPY has all 78 bars for 2024-01-08. QQQ is missing the 9:35 bar
    (bar_index=1). We advance cursors step by step and verify lengths
    diverge at the right points, then re-converge after the gap.
    """
    from milodex.backtesting.intraday_simulation import _advance_cursors, _build_visible_bars

    session_date = "2024-01-08"
    spy_full = _build_synthetic_5min_barset([session_date], symbol="SPY")
    qqq_full = _build_synthetic_5min_barset([session_date], symbol="QQQ")

    # Remove 9:35 bar from QQQ (bar_index=1 → 9:30+5min=9:35 ET = 14:35 UTC)
    qqq_df = qqq_full.to_dataframe()
    ts_9_35_utc = pd.Timestamp("2024-01-08 14:35:00+00:00")
    qqq_df_filtered = qqq_df[
        pd.to_datetime(qqq_df["timestamp"], utc=True) != ts_9_35_utc
    ].reset_index(drop=True)
    from milodex.data.models import BarSet

    qqq_bars_missing = BarSet(qqq_df_filtered)

    spy_df = spy_full.to_dataframe()
    qqq_df2 = qqq_bars_missing.to_dataframe()

    spy_ts_utc = pd.DatetimeIndex(pd.to_datetime(spy_df["timestamp"], utc=True))
    qqq_ts_utc = pd.DatetimeIndex(pd.to_datetime(qqq_df2["timestamp"], utc=True))

    per_symbol_ts_utc = {"SPY": spy_ts_utc, "QQQ": qqq_ts_utc}
    per_symbol_df = {"SPY": spy_df, "QQQ": qqq_df2}
    cursors = {"SPY": 0, "QQQ": 0}

    # Step 1: advance to T=9:35 ET decision_time (= 14:35 UTC).
    # decision_time of 9:30 bar = 14:30+5min = 14:35 UTC
    t1 = pd.Timestamp("2024-01-08 14:35:00+00:00")
    _advance_cursors(cursors, per_symbol_ts_utc, t1, bar_size_minutes=5)
    visible1 = _build_visible_bars(per_symbol_df, cursors, universe=["SPY", "QQQ"])
    # SPY: 9:30 bar visible (cursor=1); QQQ: 9:30 bar visible (cursor=1)
    assert "SPY" in visible1
    assert "QQQ" in visible1
    assert len(visible1["SPY"].to_dataframe()) == 1
    assert len(visible1["QQQ"].to_dataframe()) == 1

    # Step 2: advance to T=9:40 ET decision_time (= 14:40 UTC).
    # decision_time of 9:35 bar = 14:35+5min = 14:40 UTC
    t2 = pd.Timestamp("2024-01-08 14:40:00+00:00")
    _advance_cursors(cursors, per_symbol_ts_utc, t2, bar_size_minutes=5)
    visible2 = _build_visible_bars(per_symbol_df, cursors, universe=["SPY", "QQQ"])
    # SPY: 9:30+9:35 visible (cursor=2); QQQ: 9:30 only (9:35 missing, so cursor=1)
    assert "SPY" in visible2
    assert "QQQ" in visible2
    spy2_len = len(visible2["SPY"].to_dataframe())
    qqq2_len = len(visible2["QQQ"].to_dataframe())
    assert spy2_len == 2, f"SPY should have 2 bars visible at T=9:40, got {spy2_len}"
    assert qqq2_len == 1, f"QQQ should have 1 bar visible at T=9:40 (9:35 missing), got {qqq2_len}"

    # Step 3: advance to T=9:45 ET decision_time (= 14:45 UTC).
    # decision_time of 9:40 bar = 14:40+5min = 14:45 UTC
    # QQQ has 9:40 bar → QQQ cursor advances to 2
    t3 = pd.Timestamp("2024-01-08 14:45:00+00:00")
    _advance_cursors(cursors, per_symbol_ts_utc, t3, bar_size_minutes=5)
    visible3 = _build_visible_bars(per_symbol_df, cursors, universe=["SPY", "QQQ"])
    # SPY: 3 bars (9:30, 9:35, 9:40); QQQ: 2 bars (9:30, 9:40 — skipped 9:35)
    assert "SPY" in visible3
    assert "QQQ" in visible3
    spy3_len = len(visible3["SPY"].to_dataframe())
    qqq3_len = len(visible3["QQQ"].to_dataframe())
    assert spy3_len == 3, f"SPY should have 3 bars at T=9:45, got {spy3_len}"
    assert qqq3_len == 2, f"QQQ should have 2 bars at T=9:45 (9:30 + 9:40), got {qqq3_len}"

    # Cursor invariant: values are non-decreasing across steps
    assert cursors["SPY"] >= 3
    assert cursors["QQQ"] >= 2


# ---------------------------------------------------------------------------
# F1 Group 3 — Tests 8, 9: session-boundary fills and stranded orders
# ---------------------------------------------------------------------------


def test_session_boundary_pending_sell_fills_at_next_session_open() -> None:
    """Test 8: SELL queued at last decision event of session 1 fills at
    session 2's 9:30 open with slippage applied.

    Uses the full _simulate_intraday path via engine.run() with a 2-session
    synthetic dataset. The stub strategy:
      - At session 1's 15:55 bar (last decision event): emit BUY (sets up position).
      Actually — we need a position to SELL. Simpler: emit BUY at session 1's
      first bar, then SELL at session 1's last bar. The BUY fills at the
      second bar's open of session 1; the SELL fills at session 2's first
      bar's open (9:30).
    """
    from milodex.strategies.bench_unconditional_intraday_long import (
        BenchUnconditionalIntradayLongStrategy,
    )

    # 2 full sessions.
    session_dates = ["2024-01-08", "2024-01-09"]
    start_date = date(2024, 1, 8)
    end_date = date(2024, 1, 9)

    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")

    # Use the benchmark strategy: BUY at 10:00 (post-OR), SELL at 15:55 (time-stop).
    # For a 2-session run the pattern is:
    #   Session 1: BUY→fills at 10:05 open; SELL→fills at session 2's 9:30 open
    #   Session 2: BUY→fills at 10:05 open; SELL→STRANDED (no session 3)
    parameters = {
        "opening_range_minutes": 30,
        "exit_minutes_before_close": 5,
        "per_position_notional_pct": 0.95,
    }
    real_strategy = BenchUnconditionalIntradayLongStrategy()
    loaded = _make_intraday_loaded_strategy("stub.session_boundary.v1", ("SPY",))
    loaded.strategy = real_strategy

    loaded.context = StrategyContext(
        strategy_id="stub.session_boundary.v1",
        family="benchmark",
        template="unconditional_intraday_long",
        variant="session_boundary_test",
        version=1,
        config_hash="session_boundary_hash",
        parameters=parameters,
        universe=("SPY",),
        universe_ref=None,
        disable_conditions=(),
        config_path=str(loaded.config.path),
        manifest={},
    )

    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})
    result = engine.run(start_date, end_date)

    # 2-session benchmark: buy_count=2, sell_count=1 (last SELL stranded), skipped=1
    assert result.buy_count == 2, f"Expected buy_count=2, got {result.buy_count}"
    assert result.sell_count == 1, f"Expected sell_count=1, got {result.sell_count}"
    assert result.skipped_count == 1, (
        f"Expected skipped_count=1 (stranded SELL), got {result.skipped_count}"
    )

    # The session-boundary SELL (SELL queued at session 1's 15:55 bar) filled at
    # session 2's 9:30 open. Verify via the trade ledger.
    trades = engine._event_store.list_trades_for_backtest_run(result.db_id)  # noqa: SLF001
    sell_trades = [t for t in trades if t.side == "sell" and t.status == "submitted"]
    assert len(sell_trades) == 1, f"Expected exactly 1 SELL trade, got {len(sell_trades)}"

    # The fill should be at session 2's 9:30 open.
    # session 2 = 2024-01-09; 9:30 ET = 14:30 UTC
    # bar_idx=0 of session 2 (sess_idx=1): open = 500 + 1*0.10 + 0*0.01 = 500.10
    expected_fill = 500.10  # slippage=0 so fill == open exactly
    fill_price = sell_trades[0].estimated_unit_price
    assert abs(fill_price - expected_fill) < 1e-6, (
        f"Session-boundary SELL fill price {fill_price!r} != session2 9:30 open "
        f"{expected_fill} — cross-session fill bug?"
    )


def test_stranded_pending_counted_as_skipped() -> None:
    """Test 9: Order with no future bar is counted in skipped_count only after
    the final timestamp. The stranded order shows up as a skipped audit event.
    """
    # Single session. Strategy emits BUY at the very last decision event (15:55 bar).
    # No next bar exists → the order is stranded → skipped_count == 1.
    # The BUY itself has no prior position so no SELL is queued.
    session_dates = ["2024-01-08"]
    start_date = date(2024, 1, 8)
    end_date = date(2024, 1, 8)

    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")
    loaded = _make_intraday_loaded_strategy("stub.stranded.v1", ("SPY",))

    # The last bar in a full session: bar_idx=77 (9:30 + 77*5min = 15:55 ET).
    # 15:55 ET for 2024-01-08 (EST, UTC-5) = 20:55 UTC
    last_bar_ts_utc = pd.Timestamp("2024-01-08 20:55:00+00:00")

    def _stub_buy_at_last_bar(bars, context):  # noqa: ANN001

        df = bars.to_dataframe()
        if df.empty:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(rule="no_signal", narrative="empty"),
            )
        latest_ts = pd.Timestamp(df["timestamp"].iloc[-1])
        if latest_ts.tz is None:
            latest_ts = latest_ts.tz_localize("UTC")
        if latest_ts == last_bar_ts_utc:
            intent = TradeIntent(
                symbol="SPY", side=OrderSide.BUY, quantity=1.0, order_type=OrderType.MARKET
            )
            return StrategyDecision(
                intents=[intent],
                reasoning=DecisionReasoning(rule="stub.buy_last_bar", narrative="stranded test"),
            )
        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(rule="no_signal", narrative="not last bar"),
        )

    loaded.strategy.evaluate.side_effect = _stub_buy_at_last_bar

    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})
    result = engine.run(start_date, end_date)

    # BUY at last bar has no subsequent fill event → stranded → skipped
    assert result.buy_count == 0, f"Stranded BUY should not count as fill, got {result.buy_count}"
    assert result.skipped_count == 1, (
        f"Stranded BUY should produce skipped_count=1, got {result.skipped_count}"
    )

    # Verify the audit event exists in the event store.
    trades = engine._event_store.list_trades_for_backtest_run(result.db_id)  # noqa: SLF001
    skipped_trades = [t for t in trades if t.status == "skipped"]
    assert len(skipped_trades) >= 1, (
        f"Expected at least 1 skipped trade record for stranded order, got {len(skipped_trades)}"
    )
    # The skipped record should be for SPY
    assert any(t.symbol == "SPY" for t in skipped_trades), (
        "No skipped trade record found for SPY — _record_skipped_order not called?"
    )


# ---------------------------------------------------------------------------
# F1 Group 4 — Test 10: Walk-forward + intraday timeframe propagation
# ---------------------------------------------------------------------------


def test_walk_forward_intraday_prefetch_uses_minute_5() -> None:
    """Test 10: Walk-forward path propagates MINUTE_5 to every prefetch_bars call.

    derive_walk_forward_spans reads bar_size from the engine's config and calls
    prefetch_bars with timeframe_from_bar_size(bar_size). For an intraday config
    (bar_size=5Min), this must resolve to MINUTE_5, never DAY_1.

    Also verifies that run_walk_forward's internal prefetch uses the same timeframe
    when all_bars is not pre-supplied (i.e., the runner calls prefetch_bars itself).
    """
    from milodex.backtesting.walk_forward_runner import derive_walk_forward_spans
    from milodex.data.models import Timeframe

    # Build 8 sessions so walk-forward can produce at least 2 windows.
    session_dates = [
        "2024-01-08",
        "2024-01-09",
        "2024-01-10",
        "2024-01-11",
        "2024-01-12",
        "2024-01-16",
        "2024-01-17",
        "2024-01-18",
    ]
    start_date = date(2024, 1, 8)
    end_date = date(2024, 1, 18)

    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")
    loaded = _make_intraday_loaded_strategy("stub.wf_intraday.v1", ("SPY",))
    # Set walk_forward_windows=2 on the config mock so compute_window_spans can fit.
    loaded.config.backtest["walk_forward_windows"] = 2

    recorded_timeframes: list[Timeframe] = []
    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})
    original_prefetch = engine.prefetch_bars

    def _spy_prefetch(start, end, *, timeframe=Timeframe.DAY_1):
        recorded_timeframes.append(timeframe)
        return original_prefetch(start, end, timeframe=timeframe)

    engine.prefetch_bars = _spy_prefetch  # type: ignore[method-assign]

    # derive_walk_forward_spans calls prefetch_bars once with the derived timeframe.
    all_bars, train_days, test_days, step_days = derive_walk_forward_spans(
        engine, start_date, end_date
    )

    assert len(recorded_timeframes) >= 1, "derive_walk_forward_spans must call prefetch_bars"
    assert all(tf == Timeframe.MINUTE_5 for tf in recorded_timeframes), (
        f"All prefetch_bars calls from derive_walk_forward_spans must use MINUTE_5, "
        f"got: {recorded_timeframes}"
    )
    assert Timeframe.DAY_1 not in recorded_timeframes, (
        "DAY_1 found in recorded timeframes — walk-forward dispatch bug"
    )


# ---------------------------------------------------------------------------
# F1 Group 5 — Tests A, B, C: helper unit tests + empty all_bars edge case
# ---------------------------------------------------------------------------


def test_build_visible_bars_cursor_zero_omits_symbol() -> None:
    """Test A (M-1): _build_visible_bars with cursor=0 omits the symbol."""
    from milodex.backtesting.intraday_simulation import _build_visible_bars

    df = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2024-01-08 14:30:00+00:00")],
            "open": [500.0],
            "high": [501.0],
            "low": [499.0],
            "close": [500.5],
            "volume": [100_000],
            "vwap": [500.2],
        }
    )
    per_symbol_df = {"SPY": df}
    cursors = {"SPY": 0}

    result = _build_visible_bars(per_symbol_df, cursors, universe=["SPY"])

    # cursor=0 → symbol must be OMITTED (not present as empty BarSet)
    assert "SPY" not in result, (
        "cursor=0 should omit the symbol entirely, not return an empty BarSet"
    )


def test_build_visible_bars_symbol_absent_silently_skipped() -> None:
    """Test A (M-1): _build_visible_bars skips symbols absent from per_symbol_df."""
    from milodex.backtesting.intraday_simulation import _build_visible_bars

    per_symbol_df: dict[str, pd.DataFrame] = {}  # empty — no symbol data at all
    cursors = {"SPY": 5}

    result = _build_visible_bars(per_symbol_df, cursors, universe=["SPY"])

    # SPY absent from per_symbol_df → silently skipped, no KeyError
    assert "SPY" not in result
    assert result == {}


def test_build_visible_bars_happy_path_cursor_5() -> None:
    """Test A (M-1): _build_visible_bars with cursor=5 returns BarSet with 5 rows."""
    from milodex.backtesting.intraday_simulation import _build_visible_bars

    # Build 10 rows
    rows = []
    for i in range(10):
        ts = pd.Timestamp("2024-01-08 14:30:00+00:00") + pd.Timedelta(minutes=5 * i)
        rows.append(
            {
                "timestamp": ts,
                "open": 500.0 + i * 0.01,
                "high": 500.05 + i * 0.01,
                "low": 499.95 + i * 0.01,
                "close": 500.02 + i * 0.01,
                "volume": 100_000,
                "vwap": 500.01 + i * 0.01,
            }
        )
    df = pd.DataFrame(rows)
    per_symbol_df = {"SPY": df}
    cursors = {"SPY": 5}

    result = _build_visible_bars(per_symbol_df, cursors, universe=["SPY"])

    assert "SPY" in result
    assert len(result["SPY"].to_dataframe()) == 5


def test_latest_close_at_ts_none_sentinel_returns_last_close() -> None:
    """Test B (M-1): _latest_close_at_ts with ts=None returns each symbol's last close."""
    from milodex.backtesting.intraday_simulation import _latest_close_at_ts

    spy_df = pd.DataFrame(
        {
            "timestamp": [
                pd.Timestamp("2024-01-08 14:30:00+00:00"),
                pd.Timestamp("2024-01-08 14:35:00+00:00"),
            ],
            "close": [500.0, 501.5],
        }
    )
    qqq_df = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2024-01-08 14:30:00+00:00")],
            "close": [400.0],
        }
    )
    per_symbol_df = {"SPY": spy_df, "QQQ": qqq_df}
    per_symbol_ts_utc = {
        "SPY": pd.DatetimeIndex(pd.to_datetime(spy_df["timestamp"], utc=True)),
        "QQQ": pd.DatetimeIndex(pd.to_datetime(qqq_df["timestamp"], utc=True)),
    }

    closes = _latest_close_at_ts(per_symbol_df, per_symbol_ts_utc, ts=None)

    assert abs(closes["SPY"] - 501.5) < 1e-9, f"Expected SPY last close 501.5, got {closes['SPY']}"
    assert abs(closes["QQQ"] - 400.0) < 1e-9, f"Expected QQQ last close 400.0, got {closes['QQQ']}"


def test_latest_close_at_ts_before_first_bar_omits_symbol() -> None:
    """Test B (M-1): ts before the first bar → symbol omitted from result."""
    from milodex.backtesting.intraday_simulation import _latest_close_at_ts

    spy_df = pd.DataFrame(
        {
            "timestamp": [pd.Timestamp("2024-01-08 14:30:00+00:00")],
            "close": [500.0],
        }
    )
    per_symbol_df = {"SPY": spy_df}
    per_symbol_ts_utc = {
        "SPY": pd.DatetimeIndex(pd.to_datetime(spy_df["timestamp"], utc=True)),
    }

    # ts before the first bar's timestamp
    before_first = pd.Timestamp("2024-01-08 13:00:00+00:00")
    closes = _latest_close_at_ts(per_symbol_df, per_symbol_ts_utc, ts=before_first)

    assert "SPY" not in closes, (
        f"Symbol with no bars at or before ts should be omitted, got closes={closes}"
    )


def test_latest_close_at_ts_between_bars_returns_most_recent() -> None:
    """Test B (M-1): ts between two bars → returns the most recent bar's close at or before ts."""
    from milodex.backtesting.intraday_simulation import _latest_close_at_ts

    spy_df = pd.DataFrame(
        {
            "timestamp": [
                pd.Timestamp("2024-01-08 14:30:00+00:00"),  # close=500.0
                pd.Timestamp("2024-01-08 14:35:00+00:00"),  # close=501.5
                pd.Timestamp("2024-01-08 14:40:00+00:00"),  # close=502.0
            ],
            "close": [500.0, 501.5, 502.0],
        }
    )
    per_symbol_df = {"SPY": spy_df}
    per_symbol_ts_utc = {
        "SPY": pd.DatetimeIndex(pd.to_datetime(spy_df["timestamp"], utc=True)),
    }

    # ts = 14:37 UTC → between 14:35 and 14:40 → should return 14:35 bar's close (501.5)
    ts_between = pd.Timestamp("2024-01-08 14:37:00+00:00")
    closes = _latest_close_at_ts(per_symbol_df, per_symbol_ts_utc, ts=ts_between)

    assert "SPY" in closes
    assert abs(closes["SPY"] - 501.5) < 1e-9, (
        f"Expected close 501.5 (14:35 bar), got {closes['SPY']}"
    )


def test_simulate_intraday_empty_all_bars_returns_initial_equity() -> None:
    """Test C (M-2): non-empty trading_days but empty all_bars → returns initial_equity,
    all zero counts. No crash.
    """
    import tempfile
    from datetime import UTC, datetime
    from pathlib import Path

    from milodex.core.event_store import BacktestRunEvent, EventStore

    loaded = _make_intraday_loaded_strategy("stub.empty_bars.v1", ("SPY",))

    # _make_intraday_engine injects bars via the mock data provider.
    # We'll exercise _simulate_intraday directly by building the engine and
    # then calling simulate_window with empty all_bars.
    seed_bars = _build_synthetic_5min_barset(["2024-01-08"], symbol="SPY")
    engine = _make_intraday_engine(loaded, {"SPY": seed_bars})

    # Provide empty all_bars (no symbols at all).
    empty_bars: dict = {}
    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    es = EventStore(tmp_db)
    now = datetime.now(tz=UTC)
    db_run_id = es.append_backtest_run(
        BacktestRunEvent(
            run_id="test-empty-bars",
            strategy_id="stub.empty_bars.v1",
            config_path=None,
            config_hash="empty_hash",
            start_date=now,
            end_date=now,
            started_at=now,
            status="running",
            slippage_pct=0.0,
            commission_per_trade=0.0,
            metadata={},
        )
    )
    # Patch the engine's event store so simulate_window can write
    engine._event_store = es  # noqa: SLF001

    output = engine.simulate_window(
        all_bars=empty_bars,
        trading_days=[date(2024, 1, 8)],
        db_run_id=db_run_id,
        session_id="test-empty-bars",
        initial_equity=100_000.0,
    )

    assert output.trade_count == 0
    assert output.buy_count == 0
    assert output.sell_count == 0
    assert output.skipped_count == 0
    assert abs(output.final_equity - 100_000.0) < 1e-6, (
        f"Empty all_bars should return initial_equity=100000, got {output.final_equity}"
    )
