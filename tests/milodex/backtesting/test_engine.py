"""Unit tests for the BacktestEngine."""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from milodex.backtesting.engine import (
    BacktestEngine,
    BacktestResult,
    _compute_equity,
    _slice_bars_to_day,
    _trading_days_in_range,
)
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.strategies.base import DecisionReasoning, StrategyContext, StrategyDecision


def _decision(intents: list) -> StrategyDecision:
    """Wrap a list of intents in a ``StrategyDecision`` for mocked strategies."""
    return StrategyDecision(
        intents=list(intents),
        reasoning=DecisionReasoning(rule="no_signal", narrative="test stub"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_barset(closes: list[float], start: date) -> BarSet:
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


_STRATEGY_YAML = """\
strategy:
  name: "test_strategy"
  version: 1
  description: "Test strategy for engine tests."
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 2
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
"""


def _write_strategy_yaml(tmp_dir: Path) -> Path:
    path = tmp_dir / "strategy.yaml"
    path.write_text(_STRATEGY_YAML, encoding="utf-8")
    return path


def _make_loaded_strategy(
    strategy_id: str, universe: tuple[str, ...], config_path: Path | None = None
):
    """Return a mock LoadedStrategy for engine tests."""
    from milodex.strategies.base import StrategyContext

    effective_path = config_path or _write_strategy_yaml(Path(tempfile.mkdtemp()))

    config = MagicMock()
    config.strategy_id = strategy_id
    config.family = "regime"
    config.template = "daily.sma200_rotation"
    config.stage = "backtest"
    config.path = effective_path
    config.parameters = {"ma_filter_length": 3, "allocation_pct": 0.9}
    config.backtest = {"slippage_pct": 0.001, "commission_per_trade": 0.0}
    config.universe = universe

    context = StrategyContext(
        strategy_id=strategy_id,
        family="regime",
        template="daily.sma200_rotation",
        variant="test",
        version=1,
        config_hash="abc123",
        parameters={
            "ma_filter_length": 3,
            "allocation_pct": 0.9,
            "risk_on_symbol": "SPY",
            "risk_off_symbol": "SHY",
        },
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path="configs/fake.yaml",
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = _decision([])

    loaded = MagicMock()
    loaded.config = config
    loaded.context = context
    loaded.strategy = strategy
    return loaded


def _make_event_store() -> EventStore:
    tmp = tempfile.mktemp(suffix=".db")
    return EventStore(Path(tmp))


# ---------------------------------------------------------------------------
# _trading_days_in_range
# ---------------------------------------------------------------------------


def test_trading_days_in_range_filters_to_window():
    start = date(2024, 1, 3)
    end = date(2024, 1, 5)
    barset = _make_barset([100.0, 101.0, 102.0, 103.0, 104.0], start=date(2024, 1, 1))
    result = _trading_days_in_range({"SPY": barset}, start, end)
    assert result == [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]


def test_trading_days_in_range_empty_bars():
    result = _trading_days_in_range({}, date(2024, 1, 1), date(2024, 1, 5))
    assert result == []


def test_trading_days_in_range_no_overlap():
    barset = _make_barset([100.0, 101.0], start=date(2024, 1, 1))
    result = _trading_days_in_range({"SPY": barset}, date(2024, 2, 1), date(2024, 2, 5))
    assert result == []


# ---------------------------------------------------------------------------
# _slice_bars_to_day
# ---------------------------------------------------------------------------


def test_slice_bars_to_day_trims_future_bars():
    start = date(2024, 1, 1)
    barset = _make_barset([100.0, 101.0, 102.0, 103.0], start=start)
    sliced = _slice_bars_to_day({"SPY": barset}, date(2024, 1, 2))
    assert "SPY" in sliced
    df = sliced["SPY"].to_dataframe()
    assert len(df) == 2
    assert float(df["close"].iloc[-1]) == 101.0


def test_slice_bars_to_day_excludes_symbols_with_no_prior_data():
    barset = _make_barset([100.0, 101.0], start=date(2024, 1, 5))
    sliced = _slice_bars_to_day({"SPY": barset}, date(2024, 1, 3))
    assert "SPY" not in sliced


# ---------------------------------------------------------------------------
# _compute_equity
# ---------------------------------------------------------------------------


def test_compute_equity_with_no_positions():
    equity = _compute_equity(50_000.0, {}, {})
    assert equity == 50_000.0


def test_compute_equity_marks_to_market():
    positions = {"SPY": (10.0, 400.0)}  # bought at 400
    closes = {"SPY": 450.0}
    equity = _compute_equity(10_000.0, positions, closes)
    assert equity == pytest.approx(10_000.0 + 10 * 450.0)


# ---------------------------------------------------------------------------
# BacktestEngine.run — integration with mock strategy
# ---------------------------------------------------------------------------


def test_engine_empty_range_returns_zero_trades():
    start = date(2024, 3, 1)
    end = date(2024, 3, 31)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))

    barset = _make_barset([100.0] * 20, start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)
    result = engine.run(start, end)

    assert isinstance(result, BacktestResult)
    assert result.trade_count == 0
    assert result.initial_equity == result.final_equity


def test_engine_buy_sell_round_trip():
    """BUY enqueued on day 1 fills day 2; SELL enqueued day 2 fills day 3 — 2 trades."""
    from milodex.broker.models import OrderSide, OrderType
    from milodex.execution.models import TradeIntent

    start = date(2024, 1, 2)
    end = date(2024, 1, 4)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.strat.v1", universe)

    day_calls: list[date] = []
    sell_decision_day = date(2024, 1, 3)  # day 2: SELL enqueued, fills day 3

    def fake_evaluate(bars, context):
        import pandas as pd

        df = bars.to_dataframe()
        timestamps = pd.to_datetime(df["timestamp"], utc=True)
        current_day = timestamps.dt.date.max()
        day_calls.append(current_day)
        if current_day == start:
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY", side=OrderSide.BUY, quantity=10.0, order_type=OrderType.MARKET
                    )
                ]
            )
        if current_day == sell_decision_day:
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY",
                        side=OrderSide.SELL,
                        quantity=10.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    barset = _make_barset([100.0, 102.0, 104.0], start=start)
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
    result = engine.run(start, end)

    assert result.buy_count == 1
    assert result.sell_count == 1
    assert result.trade_count == 2
    trades = store.list_trades_for_backtest_run(result.db_id)
    assert len(trades) == 2
    assert all(t.source == "backtest" for t in trades)
    assert all(t.backtest_run_id == result.db_id for t in trades)

    # Every explanation written by the backtest engine must be parented to
    # the run it produced. Closes the orphan-evaluation gap (migration 008).
    explanations = [e for e in store.list_explanations() if e.submitted_by == "backtest_engine"]
    assert explanations, "engine should have written at least one explanation"
    assert all(e.backtest_run_id == result.db_id for e in explanations), (
        "every backtest_engine explanation must carry backtest_run_id"
    )


def test_engine_skips_buy_when_insufficient_cash():
    """Strategy asks to buy more than cash allows — trade should be skipped."""
    from milodex.broker.models import OrderSide, OrderType
    from milodex.execution.models import TradeIntent

    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.strat.v1", universe)

    loaded.strategy.evaluate.return_value = _decision(
        [
            TradeIntent(
                symbol="SPY", side=OrderSide.BUY, quantity=99_999.0, order_type=OrderType.MARKET
            )
        ]
    )

    barset = _make_barset([500.0, 500.0], start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=1_000.0,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    result = engine.run(start, end)

    assert result.buy_count == 0


def test_engine_status_set_to_completed():
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))

    barset = _make_barset([100.0, 101.0], start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)
    result = engine.run(start, end)

    run_record = store.get_backtest_run(result.run_id)
    assert run_record is not None
    assert run_record.status == "completed"


def test_engine_startup_reconciles_orphan_backtest_runs_for_same_strategy():
    """A backtest engine that dies mid-run leaves a ``backtest_runs`` row with
    ``status='running'`` and ``ended_at IS NULL``. The next backtest for the
    same strategy must close out that orphan at startup with
    ``status='orphan_recovered'``.

    Mirrors the runner-side contract from PR #44
    (``test_runner_startup_reconciles_orphan_strategy_runs_for_same_strategy``):
    same-strategy orphans are reconciled, other-strategy orphans are left for
    that strategy's next startup, and the freshly-inserted row for this run
    must NOT itself be swept by the WHERE clause.
    """
    from datetime import UTC, datetime

    from milodex.core.event_store import BacktestRunEvent

    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    strategy_id = "momentum.daily.tsmom.curated_largecap.v1"
    other_strategy_id = "breakout.daily.donchian_20_10.sector_etfs.v1"

    loaded = _make_loaded_strategy(strategy_id, ("SPY",))
    barset = _make_barset([100.0, 101.0], start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}
    store = _make_event_store()

    # Pre-seed an orphan row for THIS strategy and one for a DIFFERENT
    # strategy. Only the same-strategy one should be reconciled.
    store.append_backtest_run(
        BacktestRunEvent(
            run_id="orphan-same-strategy",
            strategy_id=strategy_id,
            config_path="configs/momentum_tsmom_v1.yaml",
            config_hash="hash-1",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 12, 31, tzinfo=UTC),
            started_at=datetime(2026, 5, 6, 16, 50, 23, tzinfo=UTC),
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={"walk_forward": True, "windows_planned": 4},
        )
    )
    store.append_backtest_run(
        BacktestRunEvent(
            run_id="orphan-other-strategy",
            strategy_id=other_strategy_id,
            config_path="configs/breakout_donchian_v1.yaml",
            config_hash="hash-2",
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 12, 31, tzinfo=UTC),
            started_at=datetime(2026, 5, 6, 17, 2, 24, tzinfo=UTC),
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={"walk_forward": True, "windows_planned": 4},
        )
    )

    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)
    result = engine.run(start, end)

    same = store.get_backtest_run("orphan-same-strategy")
    assert same is not None
    assert same.status == "orphan_recovered"
    assert same.ended_at is not None

    other = store.get_backtest_run("orphan-other-strategy")
    assert other is not None
    assert other.status == "running"
    assert other.ended_at is None

    # The newly-started run must reach a terminal status (completed) — not
    # be swept by its own startup reconcile.
    fresh = store.get_backtest_run(result.run_id)
    assert fresh is not None
    assert fresh.status == "completed"


def test_engine_invalid_date_range_raises():
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))
    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=MagicMock(), event_store=store)
    with pytest.raises(ValueError, match="end_date"):
        engine.run(date(2024, 6, 1), date(2024, 5, 1))


def test_engine_slippage_increases_buy_cost():
    """Slippage increases fill price; under T+1 model fill price is the *next* bar's open."""
    from milodex.broker.models import OrderSide, OrderType
    from milodex.execution.models import TradeIntent

    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.strat.v1", universe)

    def fake_evaluate(bars, context):
        df = bars.to_dataframe()
        timestamps = pd.to_datetime(df["timestamp"], utc=True)
        current_day = timestamps.dt.date.max()
        if current_day == start:
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY",
                        side=OrderSide.BUY,
                        quantity=1.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    # Two-day fixture; both bars at $100 (open=close per _make_barset). The
    # strategy decides BUY on day 1; fill happens on day 2's open at $100.
    barset = _make_barset([100.0, 100.0], start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=10_000.0,
        slippage_pct=0.01,
        commission_per_trade=0.0,
    )
    engine.run(start, end)

    trades = store.list_trades()
    buy_trade = next(t for t in trades if t.side == "buy")
    assert buy_trade.estimated_unit_price == pytest.approx(101.0)


def test_engine_equity_curve_length_matches_trading_days():
    start = date(2024, 1, 2)
    end = date(2024, 1, 5)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))

    barset = _make_barset([100.0, 101.0, 102.0, 103.0], start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)
    result = engine.run(start, end)

    assert len(result.equity_curve) == result.trading_days


# ---------------------------------------------------------------------------
# Portfolio snapshot wiring (closes analytics/snapshots.py scaffolded marker)
# ---------------------------------------------------------------------------


def test_engine_records_portfolio_snapshot_at_run_end():
    """Backtest run produces a portfolio_snapshots row keyed on the run_id.

    Closes the BacktestEngine half of the `analytics/snapshots.py`
    scaffolded marker (R-XC-016). Walk-forward runs are covered separately
    in test_walk_forward_runner.py since they call simulate_window().
    """
    from milodex.broker.models import OrderSide, OrderType
    from milodex.execution.models import TradeIntent

    start = date(2024, 1, 2)
    end = date(2024, 1, 4)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.strat.v1", universe)

    def fake_evaluate(bars, context):
        df = bars.to_dataframe()
        timestamps = pd.to_datetime(df["timestamp"], utc=True)
        current_day = timestamps.dt.date.max()
        if current_day == start:
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY",
                        side=OrderSide.BUY,
                        quantity=10.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    barset = _make_barset([100.0, 102.0, 104.0], start=start)
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
    result = engine.run(start, end)

    snapshots = store.list_portfolio_snapshots_for_session(result.run_id)
    assert len(snapshots) == 1, (
        "Backtest run should record exactly one portfolio_snapshots row keyed "
        "on the run_id (R-XC-016 closure for analytics/snapshots.py)."
    )
    snapshot = snapshots[0]
    assert snapshot.session_id == result.run_id
    assert snapshot.strategy_id == "test.strat.v1"
    assert snapshot.equity == pytest.approx(result.final_equity)
    # The buy filled at $100, then last close is $104 — the simulated
    # broker should report 10 shares of SPY in the snapshot.
    assert any(p["symbol"] == "SPY" and p["quantity"] == 10.0 for p in snapshot.positions)


def test_engine_empty_run_records_zero_snapshots():
    """A backtest with no overlapping trading days writes no snapshot.

    The empty-range early return in `_execute` short-circuits before
    `_simulate`, so the snapshot path isn't exercised. Locking in this
    behavior so a future refactor doesn't accidentally write a meaningless
    initial-equity row when there's no actual run.
    """
    bars_start = date(2024, 3, 1)
    barset = _make_barset([100.0] * 5, start=bars_start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    # Run window completely after the bar coverage.
    start = date(2024, 6, 1)
    end = date(2024, 6, 5)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))

    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)
    result = engine.run(start, end)

    assert result.trading_days == 0
    snapshots = store.list_portfolio_snapshots_for_session(result.run_id)
    assert snapshots == []


# ---------------------------------------------------------------------------
# ADR 0030 audit trail: BacktestRunEvent.config_hash captures invocation YAML
# ---------------------------------------------------------------------------


def test_backtest_run_config_hash_reflects_invocation_yaml_not_frozen():
    """Pins ADR 0030 Decision 4: ``BacktestRunEvent.config_hash`` captures the
    YAML hash that was actually used at backtest invocation, NOT a frozen
    manifest hash from a paper-stage strategy.

    This guarantees the audit trail when an operator backtests a paper-stage
    strategy against an edited config: the run row's ``config_hash`` reflects
    what was tested. An operator comparing a backtest result to the strategy's
    frozen manifest can detect divergence by hash inspection alone — and can
    do so without any demote-edit-promote ceremony.

    Test shape: two backtests of the same strategy with two different YAML
    hashes (modeled by the loaded strategy's ``context.config_hash``) produce
    distinct ``BacktestRunEvent.config_hash`` rows. The hash reflects what
    was used, not what's frozen.
    """
    start = date(2024, 1, 2)
    end = date(2024, 1, 4)

    # Run 1: simulate the operator backtesting against the YAML as-loaded —
    # config hash 'H_INVOCATION_1'.
    loaded_1 = _make_loaded_strategy("test.strat.v1", ("SPY",))
    loaded_1.context = StrategyContext(  # type: ignore[assignment]
        strategy_id=loaded_1.context.strategy_id,
        family=loaded_1.context.family,
        template=loaded_1.context.template,
        variant=loaded_1.context.variant,
        version=loaded_1.context.version,
        config_hash="H_INVOCATION_1",
        parameters=loaded_1.context.parameters,
        universe=loaded_1.context.universe,
        universe_ref=loaded_1.context.universe_ref,
        disable_conditions=loaded_1.context.disable_conditions,
        config_path=loaded_1.context.config_path,
        manifest=loaded_1.context.manifest,
    )
    barset = _make_barset([100.0, 101.0, 102.0], start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}
    store = _make_event_store()
    engine_1 = BacktestEngine(
        loaded=loaded_1,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    result_1 = engine_1.run(start, end)

    # Run 2: same strategy id, edited YAML — context.config_hash is now
    # 'H_INVOCATION_2'. Models the operator iterating on parameters mid-paper.
    loaded_2 = _make_loaded_strategy("test.strat.v1", ("SPY",))
    loaded_2.context = StrategyContext(  # type: ignore[assignment]
        strategy_id=loaded_2.context.strategy_id,
        family=loaded_2.context.family,
        template=loaded_2.context.template,
        variant=loaded_2.context.variant,
        version=loaded_2.context.version,
        config_hash="H_INVOCATION_2",
        parameters=loaded_2.context.parameters,
        universe=loaded_2.context.universe,
        universe_ref=loaded_2.context.universe_ref,
        disable_conditions=loaded_2.context.disable_conditions,
        config_path=loaded_2.context.config_path,
        manifest=loaded_2.context.manifest,
    )
    engine_2 = BacktestEngine(
        loaded=loaded_2,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    result_2 = engine_2.run(start, end)

    # Pull both rows from the event store and verify the config_hash captured
    # is the YAML-at-invocation hash, distinct between runs.
    run_1 = store.get_backtest_run(result_1.run_id)
    run_2 = store.get_backtest_run(result_2.run_id)
    assert run_1 is not None and run_2 is not None
    assert run_1.config_hash == "H_INVOCATION_1"
    assert run_2.config_hash == "H_INVOCATION_2"
    assert run_1.config_hash != run_2.config_hash, (
        "ADR 0030 Decision 4: each backtest run records the YAML hash actually "
        "tested, not a shared frozen-manifest hash. An operator iterating on "
        "parameters mid-paper must see distinct config_hash values per run."
    )
