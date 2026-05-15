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
    UniverseCoverageError,
    _compute_equity,
    _slice_bars_to_day,
    _trading_days_in_range,
)
from milodex.broker.models import OrderSide, OrderType
from milodex.core.event_store import EventStore
from milodex.data.bar_quality import DataQualityError
from milodex.data.models import BarSet
from milodex.execution import UnsupportedOrderTypeError
from milodex.execution.models import TradeIntent
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


def _make_barset_from_rows(rows: list[dict]) -> BarSet:
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


def _skipped_trades(store: EventStore):
    return [trade for trade in store.list_trades() if trade.status == "skipped"]


def _skipped_explanations(store: EventStore):
    return [event for event in store.list_explanations() if event.status == "skipped"]


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


def test_engine_fails_loudly_when_strategy_emits_non_market_order():
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))
    loaded.strategy.evaluate.return_value = _decision(
        [
            TradeIntent(
                symbol="SPY",
                side=OrderSide.BUY,
                quantity=10.0,
                order_type=OrderType.LIMIT,
                limit_price=100.0,
            )
        ]
    )

    barset = _make_barset([100.0, 101.0], start=start)
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

    with pytest.raises(UnsupportedOrderTypeError):
        engine.run(start, end, run_id="non-market-run")

    run_record = store.get_backtest_run("non-market-run")
    assert run_record is not None
    assert run_record.status == "failed"
    assert store.list_trades() == []
    assert store.list_explanations() == []


def test_engine_data_quality_blocker_fails_before_simulated_mutation():
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))
    loaded.strategy.evaluate.return_value = _decision(
        [
            TradeIntent(
                symbol="SPY",
                side=OrderSide.BUY,
                quantity=1.0,
                order_type=OrderType.MARKET,
            )
        ]
    )
    provider = MagicMock()
    provider.get_bars.return_value = {
        "SPY": _make_barset_from_rows(
            [
                {
                    "timestamp": pd.Timestamp(start, tz="UTC"),
                    "open": 100.0,
                    "high": 99.0,
                    "low": 101.0,
                    "close": 100.0,
                    "volume": 1000,
                    "vwap": 100.0,
                },
                {
                    "timestamp": pd.Timestamp(end, tz="UTC"),
                    "open": 101.0,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 101.0,
                    "volume": 1000,
                    "vwap": 101.0,
                },
            ]
        )
    }
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )

    with pytest.raises(DataQualityError) as excinfo:
        engine.run(start, end, run_id="bad-quality-run")

    assert excinfo.value.report.status == "fail"
    run_record = store.get_backtest_run("bad-quality-run")
    assert run_record is not None
    assert run_record.status == "failed"
    assert run_record.metadata["risk_policy"] == "bypass"
    assert run_record.metadata["data_quality"]["status"] == "fail"
    assert run_record.metadata["data_quality"]["blocker_count"] == 1
    manifest = run_record.metadata["run_manifest"]
    assert manifest["strategy"]["config_hash"] == loaded.context.config_hash
    assert manifest["execution_assumptions"]["risk_policy"] == "bypass"
    assert manifest["data"]["quality"]["status"] == "fail"
    assert store.list_trades() == []
    assert store.list_explanations() == []
    assert store.list_portfolio_snapshots_for_session("bad-quality-run") == []


def test_engine_data_quality_warning_allows_run_and_persists_metadata():
    start = date(2024, 1, 2)
    end = date(2024, 1, 5)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY", "QQQ"))
    loaded.strategy.evaluate.return_value = _decision([])
    provider = MagicMock()
    provider.get_bars.return_value = {
        "SPY": _make_barset([100.0, 101.0, 102.0, 103.0], start=start),
        "QQQ": _make_barset([100.0, 101.0, 102.0], start=start),
    }
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )

    result = engine.run(start, end, run_id="warning-quality-run")

    assert result.data_quality["status"] == "pass_with_warnings"
    assert result.data_quality["warning_count"] == 1
    run_record = store.get_backtest_run("warning-quality-run")
    assert run_record is not None
    assert run_record.status == "completed"
    assert run_record.metadata["data_quality"] == result.data_quality


def test_engine_persists_reproducibility_run_manifest():
    start = date(2024, 1, 2)
    end = date(2024, 1, 5)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))
    loaded.strategy.evaluate.return_value = _decision([])
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": _make_barset([100.0, 101.0, 102.0, 103.0], start)}
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=12_345.0,
        slippage_pct=0.0005,
        commission_per_trade=1.25,
    )

    result = engine.run(start, end, run_id="manifest-run")

    manifest = result.run_manifest
    assert manifest["schema_version"] == 1
    assert manifest["strategy"]["strategy_id"] == loaded.config.strategy_id
    assert manifest["strategy"]["config_hash"] == loaded.context.config_hash
    assert manifest["universe"]["symbols"] == ["SPY"]
    assert manifest["execution_assumptions"] == {
        "risk_policy": "bypass",
        "slippage_pct": 0.0005,
        "commission_per_trade": 1.25,
        "initial_equity": 12_345.0,
    }
    assert manifest["date_window"]["requested_start"] == "2024-01-02"
    assert manifest["date_window"]["requested_end"] == "2024-01-05"
    assert manifest["data"]["quality"]["status"] == "pass"
    assert "commit" in manifest["code"]
    run_record = store.get_backtest_run("manifest-run")
    assert run_record is not None
    assert run_record.metadata["run_manifest"] == manifest


def test_engine_skips_buy_when_insufficient_cash():
    """Strategy asks to buy more than cash allows — trade should be skipped."""
    from milodex.broker.models import OrderSide, OrderType
    from milodex.execution.models import TradeIntent

    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.strat.v1", universe)

    def fake_evaluate(bars, _context):
        current_day = pd.to_datetime(bars.to_dataframe()["timestamp"], utc=True).dt.date.max()
        if current_day == start:
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY",
                        side=OrderSide.BUY,
                        quantity=99_999.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

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
    assert result.skipped_count == 1
    skipped = _skipped_trades(store)
    assert len(skipped) == 1
    assert skipped[0].status == "skipped"
    assert skipped[0].source == "backtest"
    assert skipped[0].backtest_run_id == result.db_id
    assert skipped[0].message == "Skipped backtest buy for SPY: insufficient cash."
    explanations = _skipped_explanations(store)
    assert explanations[0].reason_codes == ["backtest_insufficient_cash"]
    assert explanations[0].context["cash"] == pytest.approx(1_000.0)
    assert explanations[0].context["projected_cost"] > explanations[0].context["cash"]


def test_engine_audits_duplicate_buy_skip_without_mutating_position():
    start = date(2024, 1, 2)
    end = date(2024, 1, 4)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))

    def fake_evaluate(bars, context):
        current_day = pd.to_datetime(bars.to_dataframe()["timestamp"], utc=True).dt.date.max()
        if current_day in {start, date(2024, 1, 3)}:
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
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": _make_barset([100.0, 101.0, 102.0], start=start)}
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

    filled = [trade for trade in store.list_trades() if trade.status == "submitted"]
    skipped = _skipped_trades(store)
    assert result.buy_count == 1
    assert result.skipped_count == 1
    assert len(filled) == 1
    assert len(skipped) == 1
    assert skipped[0].message == "Skipped backtest buy for SPY: position already open."
    assert _skipped_explanations(store)[0].reason_codes == ["backtest_duplicate_position"]


def test_engine_audits_sell_without_position_skip():
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))
    def fake_evaluate(bars, _context):
        current_day = pd.to_datetime(bars.to_dataframe()["timestamp"], utc=True).dt.date.max()
        if current_day == start:
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
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": _make_barset([100.0, 101.0], start=start)}
    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)

    result = engine.run(start, end)

    assert result.sell_count == 0
    assert result.skipped_count == 1
    assert _skipped_trades(store)[0].message == "Skipped backtest sell for SPY: no open position."
    assert _skipped_explanations(store)[0].reason_codes == ["backtest_sell_without_position"]


def test_engine_audits_missing_next_open_skip():
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY", "AAPL"))
    def fake_evaluate(bars, _context):
        current_day = pd.to_datetime(bars.to_dataframe()["timestamp"], utc=True).dt.date.max()
        if current_day == start:
            return _decision(
                [
                    TradeIntent(
                        symbol="AAPL",
                        side=OrderSide.BUY,
                        quantity=1.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate
    provider = MagicMock()
    provider.get_bars.return_value = {
        "SPY": _make_barset([100.0, 101.0], start=start),
        "AAPL": _make_barset([200.0], start=start),
    }
    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)

    result = engine.run(start, end)

    assert result.trade_count == 0
    assert result.skipped_count == 1
    assert _skipped_trades(store)[0].symbol == "AAPL"
    assert _skipped_explanations(store)[0].reason_codes == ["backtest_missing_next_open"]


def test_engine_blocks_non_positive_next_open_as_data_quality_failure():
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))
    def fake_evaluate(bars, _context):
        current_day = pd.to_datetime(bars.to_dataframe()["timestamp"], utc=True).dt.date.max()
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
    provider = MagicMock()
    provider.get_bars.return_value = {
        "SPY": _make_barset_from_rows(
            [
                {
                    "timestamp": pd.Timestamp(start, tz="UTC"),
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 1000,
                    "vwap": 100.0,
                },
                {
                    "timestamp": pd.Timestamp(end, tz="UTC"),
                    "open": 0.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000,
                    "vwap": 100.0,
                },
            ]
        )
    }
    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)

    with pytest.raises(DataQualityError) as excinfo:
        engine.run(start, end, run_id="invalid-next-open-quality")

    assert excinfo.value.report.status == "fail"
    assert excinfo.value.report.issues[0].code == "invalid_price"
    run_record = store.get_backtest_run("invalid-next-open-quality")
    assert run_record is not None
    assert run_record.status == "failed"
    assert _skipped_explanations(store) == []


def test_engine_audits_no_next_bar_for_end_of_window_pending_order():
    start = date(2024, 1, 2)
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))
    loaded.strategy.evaluate.return_value = _decision(
        [
            TradeIntent(
                symbol="SPY",
                side=OrderSide.BUY,
                quantity=1.0,
                order_type=OrderType.MARKET,
            )
        ]
    )
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": _make_barset([100.0], start=start)}
    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=provider, event_store=store)

    result = engine.run(start, start)

    assert result.trade_count == 0
    assert result.skipped_count == 1
    assert _skipped_trades(store)[0].message == (
        "Skipped backtest buy for SPY: no next bar available before run end."
    )
    assert _skipped_explanations(store)[0].reason_codes == ["backtest_no_next_bar"]
    run_record = store.get_backtest_run(result.run_id)
    assert run_record is not None
    assert run_record.metadata["skipped_count"] == 1


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


def test_engine_run_window_without_coverage_fails_before_snapshots():
    """A backtest with no overlapping bars fails coverage and writes no snapshot."""
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
    run_id = "empty-run-date-coverage"

    with pytest.raises(UniverseCoverageError):
        engine.run(start, end, run_id=run_id)

    runs = store.list_backtest_runs()
    assert len(runs) == 1
    assert runs[0].status == "failed"
    snapshots = store.list_portfolio_snapshots_for_session(run_id)
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
