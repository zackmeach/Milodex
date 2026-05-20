"""Intraday backtest engine correctness tests.

See docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md §5.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.broker.models import OrderSide, OrderType
from milodex.broker.simulated import SimulatedBroker
from milodex.core.event_store import BacktestRunEvent, EventStore
from milodex.data.models import BarSet
from milodex.data.simulated import SimulatedDataProvider
from milodex.execution.models import TradeIntent
from milodex.execution.service import ExecutionService
from milodex.risk import NullRiskEvaluator
from milodex.strategies.base import DecisionReasoning, StrategyContext, StrategyDecision


def test_event_timeline_for_single_symbol_5min_session() -> None:
    """For a full 9:30-16:00 ET session of 5min SPY bars, the event timeline
    is the chronological union of fill events (every bar's start) and
    decision events (every bar's completion).
    """
    from milodex.backtesting.engine import _build_intraday_event_timeline

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
    from milodex.backtesting.engine import _opens_at_timestamp

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

    from milodex.backtesting.engine import _IntradayPendingOrder

    engine, sim_broker, sim_data_provider, execution_service, event_store = _build_test_engine()

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
    pending: list[_IntradayPendingOrder] = [
        _IntradayPendingOrder(intent=intent_spy, decision_timestamp=decision_ts, reasoning=None),
        _IntradayPendingOrder(intent=intent_qqq, decision_timestamp=decision_ts, reasoning=None),
    ]

    # At T=15:00 UTC, only SPY has an open
    target_ts = pd.Timestamp("2024-01-15 15:00:00+00:00")
    opens = {"SPY": 500.50}

    new_cash, buys, sells, skipped, remaining = engine._drain_pending_at_timestamp(
        pending=pending,
        opens=opens,
        cash=100_000.0,
        positions={},
        entry_state={},
        sim_broker=sim_broker,
        sim_data_provider=sim_data_provider,
        execution_service=execution_service,
        timestamp=target_ts,
        session_id="sess-1",
        db_run_id=db_run_id,
        sym_fills={},
    )

    # SPY filled (Category 2 success — buy with sufficient cash)
    assert buys == 1
    assert sells == 0
    assert skipped == 0  # no Category-2 rejections
    assert new_cash < 100_000.0  # cash reduced by SPY fill

    # QQQ still pending (Category 1 — no open at this timestamp)
    assert len(remaining) == 1
    assert remaining[0].intent.symbol == "QQQ"


def _build_test_engine() -> (
    tuple[BacktestEngine, SimulatedBroker, SimulatedDataProvider, ExecutionService, EventStore]
):
    """Return a BacktestEngine wired with minimal stubs for _drain_pending_at_timestamp tests.

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
    bar_size: "5min"
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

    config = MagicMock()
    config.strategy_id = "intraday.test.v1"
    config.family = "momentum"
    config.template = "intraday.orb"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.tempo = {"bar_size": "5min"}
    config.universe = ("SPY",)
    config.risk = {"max_position_pct": 0.50, "max_positions": 4}

    context = StrategyContext(
        strategy_id="intraday.test.v1",
        family="momentum",
        template="intraday.orb",
        variant="test",
        version=1,
        config_hash="test_hash",
        parameters={},
        universe=("SPY",),
        universe_ref=None,
        disable_conditions=(),
        config_path=str(config_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = StrategyDecision(
        intents=[],
        reasoning=DecisionReasoning(rule="no_signal", narrative="test"),
    )
    strategy.max_lookback_periods.return_value = 0

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy

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
    all_bars: dict[str, BarSet] = {"SPY": spy_bars}

    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    event_store = EventStore(tmp_db)

    sim_broker = SimulatedBroker(slippage_pct=0.0, commission_per_trade=0.0)
    sim_data_provider = SimulatedDataProvider(all_bars)
    execution_service = ExecutionService(
        broker_client=sim_broker,
        data_provider=sim_data_provider,
        kill_switch_store=None,
        risk_evaluator=NullRiskEvaluator(),
        event_store=event_store,
        is_backtest=True,
    )

    provider = MagicMock()
    provider.get_bars.return_value = all_bars

    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=event_store,
        initial_equity=100_000.0,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )

    return engine, sim_broker, sim_data_provider, execution_service, event_store


def test_mark_to_market_at_day_end_basic() -> None:
    """End-of-day equity = cash + qty × latest close for each open position."""
    from milodex.backtesting.engine import _mark_to_market_at_day_end

    target_day = date(2024, 1, 15)
    # SPY has 3 bars on the day: closes 500.0, 501.0, 502.0
    # QQQ has 2 bars on the day: closes 400.0, 401.0
    spy_ts = pd.DatetimeIndex([
        pd.Timestamp("2024-01-15 14:30:00+00:00"),
        pd.Timestamp("2024-01-15 14:35:00+00:00"),
        pd.Timestamp("2024-01-15 14:40:00+00:00"),
    ])
    qqq_ts = pd.DatetimeIndex([
        pd.Timestamp("2024-01-15 14:30:00+00:00"),
        pd.Timestamp("2024-01-15 14:35:00+00:00"),
    ])
    spy_df = pd.DataFrame({
        "timestamp": spy_ts.to_pydatetime(),
        "open": [500.0, 500.5, 501.0],
        "high": [501.0, 501.5, 502.5],
        "low": [499.0, 500.0, 500.5],
        "close": [500.0, 501.0, 502.0],
        "volume": [1000, 1000, 1000],
    })
    qqq_df = pd.DataFrame({
        "timestamp": qqq_ts.to_pydatetime(),
        "open": [400.0, 400.5],
        "high": [401.0, 401.5],
        "low": [399.0, 400.0],
        "close": [400.0, 401.0],
        "volume": [1000, 1000],
    })

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
    from milodex.backtesting.engine import _mark_to_market_at_day_end

    target_day = date(2024, 1, 16)  # SPY has no bars on this day
    # SPY only has bars on the prior day (2024-01-15)
    spy_ts = pd.DatetimeIndex([
        pd.Timestamp("2024-01-15 14:30:00+00:00"),
        pd.Timestamp("2024-01-15 14:35:00+00:00"),
    ])
    spy_df = pd.DataFrame({
        "timestamp": spy_ts.to_pydatetime(),
        "open": [500.0, 500.5],
        "high": [501.0, 501.5],
        "low": [499.0, 500.0],
        "close": [500.0, 501.0],
        "volume": [1000, 1000],
    })

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
    from milodex.backtesting.engine import _advance_cursors

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
    from milodex.backtesting.engine import _advance_cursors

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
    from milodex.backtesting.engine import _advance_cursors

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
    from milodex.backtesting.engine import _advance_cursors

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
            rows.append({
                "timestamp": bar_ts,
                "open": round(base, 4),
                "high": round(base + 0.05, 4),
                "low": round(base - 0.05, 4),
                "close": round(base + 0.02, 4),
                "volume": 100_000,
                "vwap": round(base + 0.01, 4),
            })

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
    from milodex.strategies.base import DecisionReasoning, StrategyContext, StrategyDecision

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

    Per Correction 7: this test is written BEFORE _simulate_intraday's
    implementation. Currently it expects NotImplementedError; after E1
    lands, the test body must be updated to assert the exact counts:

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

    DO NOT delete this expected-counts comment when updating the test
    body after E1. It is the contract for the assertions the implementer adds.

    Stub note: The strategy is a MagicMock that will emit BUY/SELL intents
    matching the benchmark's behavior when evaluate() is called by the real
    _simulate_intraday loop. Until E1 lands and evaluate() is called, the
    NotImplementedError fires before any strategy.evaluate() invocation.
    For E1, replace the MagicMock strategy with BenchUnconditionalIntradayLongStrategy
    and wire the context.parameters with opening_range_minutes=30,
    exit_minutes_before_close=5, per_position_notional_pct=0.95 — or keep the
    stub with a side_effect that mirrors the benchmark rule exactly.
    """
    # 4 full trading sessions (Mon–Thu, all regular sessions, no half-days)
    session_dates = ["2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11"]
    start_date = date(2024, 1, 8)
    end_date = date(2024, 1, 11)

    spy_bars = _build_synthetic_5min_barset(session_dates, symbol="SPY")
    loaded = _make_intraday_loaded_strategy(
        "benchmark.unconditional_intraday_long.spy.e0_test.v1", ("SPY",)
    )
    engine = _make_intraday_engine(loaded, {"SPY": spy_bars})

    # Per Correction 7: currently raises NotImplementedError because
    # _simulate_intraday is a placeholder. After E1 lands, replace this
    # raises block with positive assertions (see expected-counts above).
    with pytest.raises(NotImplementedError):
        engine.run(start_date, end_date)


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
    loaded = _make_intraday_loaded_strategy(
        "stub.no_same_bar_fill.spy.e0_test.v1", ("SPY",)
    )

    # Emit BUY exactly at bar index 4 (9:50 ET = 14:50 UTC for 2024-01-08 EST session).
    # The fill must land at bar index 5's open (9:55 ET bar open = 500.05).
    # Any other bar — or the decision bar's own open/close — is a same-bar-fill bug.
    bar_4_ts_utc = pd.Timestamp("2024-01-08 14:50:00+00:00")  # 9:50 ET

    def _stub_evaluate(bars: BarSet, context):  # noqa: ANN001
        from milodex.strategies.base import DecisionReasoning, StrategyDecision

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

    # Per Correction 7: currently raises NotImplementedError because
    # _simulate_intraday is a placeholder. After E1 lands, replace this
    # raises block with assertions that the fill price == 500.05 (bar_5.open)
    # and != 500.04 (bar_4.open) and != 500.06 (bar_4.close).
    with pytest.raises(NotImplementedError):
        engine.run(start_date, end_date)
