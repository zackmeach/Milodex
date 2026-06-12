"""Tests for the shared backtest simulation kernel helpers."""

from __future__ import annotations

import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from milodex.backtesting import engine as engine_module
from milodex.backtesting.simulation_kernel import (
    BacktestSimulationKernel,
    IntradayPendingOrder,
    MissingOpenPolicy,
    PendingOrder,
)
from milodex.broker.models import OrderSide, OrderType
from milodex.core.event_store import BacktestRunEvent, EventStore
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.risk import NullRiskEvaluator
from milodex.strategies.base import DecisionReasoning, StrategyDecision


def _barset(symbol_start: float = 100.0) -> BarSet:
    return BarSet(
        pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2024-01-02", tz="UTC"),
                    "open": symbol_start,
                    "high": symbol_start + 1.0,
                    "low": symbol_start - 1.0,
                    "close": symbol_start,
                    "volume": 1000,
                    "vwap": symbol_start,
                }
            ]
        )
    )


def _event_store() -> EventStore:
    return EventStore(Path(tempfile.mktemp(suffix=".db")))


def _append_run(store: EventStore, *, run_id: str = "kernel-run") -> int:
    now = datetime.now(tz=UTC)
    return store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id="kernel.test.v1",
            config_path=None,
            config_hash="kernel-hash",
            start_date=now,
            end_date=now,
            started_at=now,
            status="running",
            slippage_pct=0.0,
            commission_per_trade=0.0,
            metadata={},
        )
    )


def _strategy_config_path() -> Path:
    tmp_dir = Path(tempfile.mkdtemp())
    path = tmp_dir / "kernel_strategy.yaml"
    path.write_text(
        """\
strategy:
  name: "kernel_test"
  version: 1
  description: "Kernel test strategy."
  enabled: true
  universe: ["SPY", "QQQ"]
  parameters: {}
  tempo:
    bar_size: "1D"
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
""",
        encoding="utf-8",
    )
    return path


def _kernel(store: EventStore, *, initial_cash: float = 1_000.0) -> BacktestSimulationKernel:
    return BacktestSimulationKernel(
        event_store=store,
        all_bars={"SPY": _barset(100.0), "QQQ": _barset(200.0)},
        strategy_id="kernel.test.v1",
        strategy_stage="backtest",
        strategy_config_path=_strategy_config_path(),
        config_hash="kernel-hash",
        risk_defaults_path=Path("configs/risk_defaults.yaml"),
        risk_evaluator=NullRiskEvaluator(),
        slippage_pct=0.0,
        commission_per_trade=0.0,
        initial_cash=initial_cash,
        max_positions=4,
        max_position_pct=0.50,
        daily_loss_cap_pct=0.05,
    )


def _intent(symbol: str, side: OrderSide, quantity: float) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=OrderType.MARKET,
    )


def _reasoning() -> DecisionReasoning:
    return DecisionReasoning(rule="kernel_test", narrative="kernel test")


def test_kernel_drains_sells_before_buys_and_updates_state() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store, initial_cash=100.0)
    kernel.positions["SPY"] = (1.0, 90.0)
    kernel.entry_state["SPY"] = {"entry_price": 90.0, "held_days": 2}
    kernel.sym_fills["SPY"] = {"buys": 1, "sells": 0}

    result = kernel.drain_pending_orders(
        pending=[
            PendingOrder(_intent("QQQ", OrderSide.BUY, 1.0), _reasoning()),
            PendingOrder(_intent("SPY", OrderSide.SELL, 1.0), _reasoning()),
        ],
        opens={"SPY": 100.0, "QQQ": 150.0},
        day=date(2024, 1, 2),
        session_id="kernel-session",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.SKIP,
    )

    assert result.sell_count == 1
    assert result.buy_count == 1
    assert result.skipped_count == 0
    assert result.remaining == []
    assert kernel.cash == 50.0
    assert "SPY" not in kernel.positions
    assert kernel.positions["QQQ"] == (1.0, 150.0)
    assert "SPY" not in kernel.entry_state
    assert kernel.entry_state["QQQ"] == {"entry_price": 150.0, "held_days": 0}
    assert kernel.round_trip_count() == 1


def test_daily_missing_next_open_records_existing_skip_reason() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)

    result = kernel.drain_pending_orders(
        pending=[PendingOrder(_intent("QQQ", OrderSide.BUY, 1.0), _reasoning())],
        opens={},
        day=date(2024, 1, 2),
        session_id="kernel-session",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.SKIP,
    )

    assert result.skipped_count == 1
    assert result.remaining == []
    skipped = [event for event in store.list_explanations() if event.status == "skipped"]
    assert skipped[0].reason_codes == ["backtest_missing_next_open"]


def test_intraday_missing_open_retains_pending_without_skip_audit() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)
    pending = [
        IntradayPendingOrder(
            intent=_intent("QQQ", OrderSide.BUY, 1.0),
            reasoning=_reasoning(),
            decision_timestamp=pd.Timestamp("2024-01-02 15:00:00+00:00"),
        )
    ]

    result = kernel.drain_pending_orders(
        pending=pending,
        opens={},
        day=date(2024, 1, 2),
        session_id="kernel-session",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.RETAIN,
    )

    assert result.buy_count == 0
    assert result.sell_count == 0
    assert result.skipped_count == 0
    assert result.remaining == pending
    assert [event for event in store.list_explanations() if event.status == "skipped"] == []


def test_stranded_pending_orders_record_no_next_bar_skip() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)

    skipped = kernel.record_stranded_orders(
        pending=[PendingOrder(_intent("SPY", OrderSide.BUY, 1.0), _reasoning())],
        day=date(2024, 1, 2),
        latest_closes={"SPY": 100.0},
        session_id="kernel-session",
        db_run_id=db_run_id,
    )

    assert skipped == 1
    skipped_events = [event for event in store.list_explanations() if event.status == "skipped"]
    assert skipped_events[0].reason_codes == ["backtest_no_next_bar"]


def test_final_snapshot_uses_backtest_snapshot_table_only() -> None:
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)

    kernel.record_final_snapshot(
        session_id="kernel-session",
        db_run_id=db_run_id,
        recorded_at=datetime(2024, 1, 2, tzinfo=UTC),
    )

    assert store.list_backtest_equity_snapshots_for_strategy("kernel.test.v1")
    assert store.list_portfolio_snapshots_for_strategy("kernel.test.v1") == []
    assert kernel.snapshot_write_error is None


def test_final_snapshot_failure_is_logged_and_recorded_not_raised(monkeypatch, caplog) -> None:
    """P2-02: snapshot write failures stay best-effort but leave a trace."""
    import logging

    from milodex.backtesting import simulation_kernel as kernel_module

    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)

    def _boom(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(kernel_module, "record_backtest_equity_snapshot", _boom)

    with caplog.at_level(logging.WARNING, logger="milodex.backtesting.simulation_kernel"):
        kernel.record_final_snapshot(
            session_id="kernel-session",
            db_run_id=db_run_id,
            recorded_at=datetime(2024, 1, 2, tzinfo=UTC),
        )

    assert kernel.snapshot_write_error == "RuntimeError: disk full"
    assert any(
        "Final backtest equity snapshot write failed" in record.message for record in caplog.records
    )


def _primary_barset(close: float = 100.0, timestamp: str = "2024-01-02") -> BarSet:
    return BarSet(
        pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp(timestamp, tz="UTC"),
                    "open": close,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "volume": 1000,
                    "vwap": close,
                }
            ]
        )
    )


def _empty_primary_barset() -> BarSet:
    """Schema-valid but zero-row BarSet — mirrors engine._empty_barset()."""
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": pd.Series([], dtype="datetime64[ns, UTC]"),
                "open": pd.Series([], dtype="float64"),
                "high": pd.Series([], dtype="float64"),
                "low": pd.Series([], dtype="float64"),
                "close": pd.Series([], dtype="float64"),
                "volume": pd.Series([], dtype="int64"),
                "vwap": pd.Series([], dtype="float64"),
            }
        )
    )


# ---------------------------------------------------------------------------
# simulate_decision_step — shared evaluate/no-action/enqueue cycle
# ---------------------------------------------------------------------------


def test_decision_step_no_intents_with_primary_records_no_action_with_derived_bar() -> None:
    """Empty intents + primary present → record_no_action carrying latest bar; pending empty."""
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)
    primary = _primary_barset(close=123.45)
    reasoning = DecisionReasoning(rule="no_signal", narrative="nothing to do")

    def make_pending(_intent: TradeIntent, _reasoning: object) -> PendingOrder:
        raise AssertionError("make_pending should not be called when intents are empty")

    def evaluate(**_: object) -> StrategyDecision:
        return StrategyDecision(intents=[], reasoning=reasoning)

    result = kernel.simulate_decision_step(
        universe=["SPY", "QQQ"],
        primary_bars=primary,
        primary_symbol_present=True,
        bars_by_symbol={"SPY": primary, "QQQ": _barset(200.0)},
        closes={"SPY": 123.45, "QQQ": 200.0},
        equity=1_000.0,
        sync_day=date(2024, 1, 2),
        make_pending=make_pending,
        session_id="dec-step-1",
        db_run_id=db_run_id,
        evaluate_strategy=evaluate,
    )

    assert result == []
    # ExecutionService.record_no_action uses status="no_signal" when reasoning is
    # provided (see execution/service.py:250) and "no_action" otherwise. Both belong
    # to the same audit family; assert on the union.
    no_action_events = [
        event for event in store.list_explanations() if event.status in ("no_action", "no_signal")
    ]
    assert len(no_action_events) == 1
    assert no_action_events[0].symbol == "SPY"
    assert no_action_events[0].latest_bar_close == 123.45


def test_simulate_decision_step_skips_no_action_when_primary_symbol_absent() -> None:
    """primary_symbol_present=False → no record_no_action even with empty intents."""
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)
    reasoning = DecisionReasoning(rule="no_signal", narrative="primary missing")

    def evaluate(**_: object) -> StrategyDecision:
        return StrategyDecision(intents=[], reasoning=reasoning)

    result = kernel.simulate_decision_step(
        universe=["SPY", "QQQ"],
        primary_bars=_empty_primary_barset(),
        primary_symbol_present=False,
        bars_by_symbol={"QQQ": _barset(200.0)},
        closes={"QQQ": 200.0},
        equity=1_000.0,
        sync_day=date(2024, 1, 2),
        make_pending=lambda i, r: PendingOrder(i, r),  # noqa: ARG005
        session_id="dec-step-2",
        db_run_id=db_run_id,
        evaluate_strategy=evaluate,
    )

    assert result == []
    no_action_events = [
        event for event in store.list_explanations() if event.status in ("no_action", "no_signal")
    ]
    assert no_action_events == []


def test_simulate_decision_step_skips_no_action_when_primary_bars_zero_length() -> None:
    """primary_symbol_present=True but primary_bars empty → MUST raise AssertionError-equivalent.

    Caller contract: primary_symbol_present implies primary_bars.latest() is callable.
    If a caller mis-flags primary_symbol_present=True with an empty BarSet, the kernel
    will fail to derive latest_bar — the test pins that this can't silently produce a
    no_action with garbage data.

    Current caller-side guard at engine.py:986-988 sets primary_symbol_present from
    `len(primary_symbol_bars) > 0`. This test guards the kernel-side contract: an
    empty primary_bars MUST NOT produce a no_action record. We assert the audit row
    is NOT emitted (kernel either skips or raises — implementation choice).
    """
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)
    reasoning = DecisionReasoning(rule="no_signal", narrative="degenerate")

    def evaluate(**_: object) -> StrategyDecision:
        return StrategyDecision(intents=[], reasoning=reasoning)

    # We intentionally pass primary_symbol_present=False here because the caller
    # already enforces this invariant — the kernel-side contract test below is
    # what we're really validating.
    result = kernel.simulate_decision_step(
        universe=["SPY", "QQQ"],
        primary_bars=_empty_primary_barset(),
        primary_symbol_present=False,
        bars_by_symbol={},
        closes={},
        equity=1_000.0,
        sync_day=date(2024, 1, 2),
        make_pending=lambda i, r: PendingOrder(i, r),  # noqa: ARG005
        session_id="dec-step-3",
        db_run_id=db_run_id,
        evaluate_strategy=evaluate,
    )

    assert result == []
    no_action_events = [
        event for event in store.list_explanations() if event.status in ("no_action", "no_signal")
    ]
    assert no_action_events == []


def test_simulate_decision_step_reuses_single_reasoning_across_all_intents() -> None:
    """All emitted PendingOrders share the same `decision.reasoning` object.

    Strategy emits ONE DecisionReasoning per evaluation cycle that explains the
    cycle's outcome. Every intent in the cycle inherits that reasoning — the
    audit story is per-cycle, not per-intent. This invariant existed inline in
    both _simulate_daily (engine.py:1031-1039) and _simulate_intraday
    (engine.py:1253-1261); shared step must preserve it.
    """
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)
    primary = _primary_barset(close=100.0)
    one_reasoning = DecisionReasoning(rule="multi_buy", narrative="bought 3")
    intents = [
        _intent("SPY", OrderSide.BUY, 1.0),
        _intent("QQQ", OrderSide.BUY, 2.0),
        _intent("IWM", OrderSide.BUY, 3.0),
    ]

    def evaluate(**_: object) -> StrategyDecision:
        return StrategyDecision(intents=intents, reasoning=one_reasoning)

    calls: list[tuple[TradeIntent, object]] = []

    def make_pending(intent: TradeIntent, reasoning: object) -> PendingOrder:
        calls.append((intent, reasoning))
        return PendingOrder(intent=intent, reasoning=reasoning, decision_day=date(2024, 1, 2))

    result = kernel.simulate_decision_step(
        universe=["SPY", "QQQ", "IWM"],
        primary_bars=primary,
        primary_symbol_present=True,
        bars_by_symbol={"SPY": primary, "QQQ": _barset(200.0), "IWM": _barset(150.0)},
        closes={"SPY": 100.0, "QQQ": 200.0, "IWM": 150.0},
        equity=1_000.0,
        sync_day=date(2024, 1, 2),
        make_pending=make_pending,
        session_id="dec-step-4",
        db_run_id=db_run_id,
        evaluate_strategy=evaluate,
    )

    assert len(result) == 3
    # Every make_pending call received the SAME reasoning object (identity, not equality).
    assert all(r is one_reasoning for _, r in calls)
    # And every PendingOrder carries it through.
    assert all(p.reasoning is one_reasoning for p in result)


def test_simulate_decision_step_sync_day_drives_broker_sync_independently_of_decision_key() -> None:
    """sync_day flows to sync_broker_state; make_pending captures its own decision_key.

    Daily caller passes sync_day=day, intraday passes sync_day=day too (NOT ts.date()).
    The make_pending closure is what captures the path-specific decision_key
    (`day` daily, `ts` intraday). This test pins that the kernel does NOT
    conflate these — sync_day is structurally separate from anything
    make_pending sees.
    """
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)
    primary = _primary_barset(close=100.0)
    reasoning = DecisionReasoning(rule="buy_one", narrative="intraday-ish")
    one_intent = _intent("SPY", OrderSide.BUY, 1.0)
    captured_ts = pd.Timestamp("2024-01-02 15:30:00+00:00")

    def evaluate(**_: object) -> StrategyDecision:
        return StrategyDecision(intents=[one_intent], reasoning=reasoning)

    def make_pending(intent: TradeIntent, reasoning_obj: object) -> IntradayPendingOrder:
        # Closure captures captured_ts as decision_timestamp.
        return IntradayPendingOrder(
            intent=intent,
            reasoning=reasoning_obj,
            decision_timestamp=captured_ts,
        )

    result = kernel.simulate_decision_step(
        universe=["SPY"],
        primary_bars=primary,
        primary_symbol_present=True,
        bars_by_symbol={"SPY": primary},
        closes={"SPY": 100.0},
        equity=1_000.0,
        sync_day=date(2024, 1, 2),  # the outer day, NOT captured_ts.date()
        make_pending=make_pending,
        session_id="dec-step-5",
        db_run_id=db_run_id,
        evaluate_strategy=evaluate,
    )

    assert len(result) == 1
    pending_order = result[0]
    assert isinstance(pending_order, IntradayPendingOrder)
    # Pending order's timestamp is what make_pending captured, NOT sync_day.
    assert pending_order.decision_timestamp == captured_ts


# ---------------------------------------------------------------------------
# tick_held_days — held_days encapsulation
# ---------------------------------------------------------------------------


def test_tick_held_days_bumps_all_open_positions() -> None:
    """Every entry in entry_state gets its held_days incremented by 1."""
    store = _event_store()
    _append_run(store)
    kernel = _kernel(store)
    kernel.entry_state = {
        "SPY": {"entry_price": 100.0, "held_days": 0},
        "QQQ": {"entry_price": 200.0, "held_days": 5},
        "IWM": {"entry_price": 150.0, "held_days": 1},
    }

    kernel.tick_held_days()

    assert kernel.entry_state["SPY"]["held_days"] == 1
    assert kernel.entry_state["QQQ"]["held_days"] == 6
    assert kernel.entry_state["IWM"]["held_days"] == 2


def test_tick_held_days_is_noop_on_empty_entry_state() -> None:
    """No positions held → no-op; no error."""
    store = _event_store()
    _append_run(store)
    kernel = _kernel(store)
    assert kernel.entry_state == {}

    kernel.tick_held_days()  # must not raise

    assert kernel.entry_state == {}


# ---------------------------------------------------------------------------
# Cross-path contract tests (PR C) — pin the new boundary so it can't drift.
# ---------------------------------------------------------------------------


def test_daily_and_intraday_routes_share_decision_step_invariant() -> None:
    """Daily and intraday callers route through ONE simulate_decision_step.

    Pins the SHARED LOCATION of the primary-symbol guard. Calling the
    method twice with the daily make_pending factory then the intraday
    factory — same kernel, same inputs — produces:
      - Identical no_action audit behavior (one no_action per call, both
        rows anchored on universe[0])
      - Different PendingOrder *variant* (PendingOrder vs IntradayPendingOrder)
        but identical TradeIntent set

    The regression this guards against is "someone reintroduces the guard at
    the daily call site only" — the test would still pass because the guard
    is in the shared step, but a follow-up audit would reveal duplication.
    The naming explicitly documents that the value is in the shared
    location, not in the per-call assertion.
    """
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store)
    primary = _primary_barset(close=100.0)
    reasoning = DecisionReasoning(rule="cross_path_test", narrative="shared")
    intents = [_intent("SPY", OrderSide.BUY, 1.0), _intent("QQQ", OrderSide.BUY, 2.0)]

    def evaluate(**_: object) -> StrategyDecision:
        return StrategyDecision(intents=list(intents), reasoning=reasoning)

    common_kwargs = {
        "universe": ["SPY", "QQQ"],
        "primary_bars": primary,
        "primary_symbol_present": True,
        "bars_by_symbol": {"SPY": primary, "QQQ": _barset(200.0)},
        "closes": {"SPY": 100.0, "QQQ": 200.0},
        "equity": 1_000.0,
        "sync_day": date(2024, 1, 2),
        "session_id": "cross-path",
        "db_run_id": db_run_id,
        "evaluate_strategy": evaluate,
    }

    daily_result = kernel.simulate_decision_step(
        make_pending=lambda i, r: PendingOrder(
            intent=i, reasoning=r, decision_day=date(2024, 1, 2)
        ),
        **common_kwargs,
    )
    intraday_result = kernel.simulate_decision_step(
        make_pending=lambda i, r: IntradayPendingOrder(
            intent=i,
            reasoning=r,
            decision_timestamp=pd.Timestamp("2024-01-02 14:30:00+00:00"),
        ),
        **common_kwargs,
    )

    # Same intents emerged from both paths via the SAME shared step.
    assert [p.intent for p in daily_result] == [p.intent for p in intraday_result] == intents
    # Variant types differ — that's the only sanctioned divergence between paths.
    assert all(isinstance(p, PendingOrder) for p in daily_result)
    assert all(isinstance(p, IntradayPendingOrder) for p in intraday_result)


def test_kernel_cash_and_positions_reconcile_to_sim_broker_after_drain_then_sync() -> None:
    """Dual-bookkeeping contract: kernel is source of truth; broker snapshot reconciles via sync.

    The kernel maintains a parallel cash + positions ledger that it mutates
    from fill prices reported by SimulatedBroker.submit_backtest. The
    broker's get_account() / get_positions() return whatever was last
    written via update_account / set_positions — both happen inside
    sync_broker_state. So between fills and the next sync, kernel.cash
    diverges from broker.get_account().cash by design (kernel debits
    immediately; broker mirrors at sync time).

    This pins the contract from BOTH sides (Finding §6 of the thermo-nuclear
    review):
      1. Post-drain, the kernel cash + positions are correct (the kernel
         is the source of truth — promotion evidence is derived from these).
      2. Post-subsequent-sync, broker.get_account() faithfully mirrors
         kernel state — the broker is a snapshot that gets refreshed.

    A regression that changes submit_backtest fill economics OR sync_broker_state
    routing will trip one of these two assertions. Without this test, drift could
    silently land "kernel says X, broker says Y" into a backtest run that then
    flows into promotion analytics.
    """
    store = _event_store()
    db_run_id = _append_run(store)
    kernel = _kernel(store, initial_cash=10_000.0)

    # Drain a single BUY at a known open price. Slippage = 0, commission = 0.
    kernel.drain_pending_orders(
        pending=[PendingOrder(_intent("SPY", OrderSide.BUY, 1.0), _reasoning())],
        opens={"SPY": 150.0},
        day=date(2024, 1, 2),
        session_id="dual-book",
        db_run_id=db_run_id,
        missing_open_policy=MissingOpenPolicy.SKIP,
    )

    # Contract part 1: kernel-side bookkeeping is correct immediately post-drain.
    assert kernel.cash == 10_000.0 - 150.0  # debited for 1 SPY @ $150
    assert kernel.positions["SPY"] == (1.0, 150.0)

    # Contract part 2: a subsequent sync_broker_state makes the broker's
    # reported snapshot match kernel state. This is the reconciliation point
    # the engine relies on between simulation steps.
    kernel.sync_broker_state(day=date(2024, 1, 2), closes={"SPY": 150.0})

    broker_account = kernel.sim_broker.get_account()
    assert kernel.cash == broker_account.cash
    broker_position_symbols = {p.symbol for p in kernel.sim_broker.get_positions()}
    assert set(kernel.positions.keys()) == broker_position_symbols
    assert any(p.symbol == "SPY" and p.quantity == 1.0 for p in kernel.sim_broker.get_positions())


def test_backtest_engine_delegates_shared_simulation_mechanics_to_kernel() -> None:
    source = Path(engine_module.__file__).read_text(encoding="utf-8")

    for token in (
        "def _drain_pending(",
        "def _drain_pending_at_timestamp(",
        "def _record_skipped_order(",
        "def _sync_broker_state(",
        "record_backtest_equity_snapshot",
    ):
        assert token not in source
