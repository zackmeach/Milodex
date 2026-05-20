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
