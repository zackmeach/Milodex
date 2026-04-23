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
from milodex.strategies.base import DecisionReasoning, StrategyDecision


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
    """Strategy emits BUY on day 1, SELL on day 3 — engine should record 2 trades."""
    from milodex.broker.models import OrderSide, OrderType
    from milodex.execution.models import TradeIntent

    start = date(2024, 1, 2)
    end = date(2024, 1, 4)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.strat.v1", universe)

    day_calls: list[date] = []

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
        if current_day == date(2024, 1, 4):
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


def test_engine_invalid_date_range_raises():
    loaded = _make_loaded_strategy("test.strat.v1", ("SPY",))
    store = _make_event_store()
    engine = BacktestEngine(loaded=loaded, data_provider=MagicMock(), event_store=store)
    with pytest.raises(ValueError, match="end_date"):
        engine.run(date(2024, 6, 1), date(2024, 5, 1))


def test_engine_slippage_increases_buy_cost():
    """Slippage should increase the fill price for buys."""
    from milodex.broker.models import OrderSide, OrderType
    from milodex.execution.models import TradeIntent

    start = date(2024, 1, 2)
    end = date(2024, 1, 2)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.strat.v1", universe)
    loaded.strategy.evaluate.return_value = _decision(
        [TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=1.0, order_type=OrderType.MARKET)]
    )

    barset = _make_barset([100.0], start=start)
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
