"""Tests for the T+1 open fill model in BacktestEngine.

These lock the look-ahead-bias correction landed in PR 2.1: a strategy
decides on bar T's close, but fills don't happen until bar T+1's open.
Pending orders left at the end of the trading window are dropped, not
force-filled — see the rejection-analysis report §6 for the rationale.
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from milodex.backtesting.engine import BacktestEngine
from milodex.broker.models import OrderSide, OrderType
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies.base import DecisionReasoning, StrategyContext, StrategyDecision


def _decision(intents: list) -> StrategyDecision:
    return StrategyDecision(
        intents=list(intents),
        reasoning=DecisionReasoning(rule="no_signal", narrative="test stub"),
    )


def _make_barset_ohlc(
    rows: list[tuple[float, float]],
    start: date,
) -> BarSet:
    """Build a BarSet from (open, close) pairs. high=max, low=min, simple model."""
    out = []
    d = start
    for o, c in rows:
        out.append(
            {
                "timestamp": pd.Timestamp(d, tz="UTC"),
                "open": o,
                "high": max(o, c),
                "low": min(o, c),
                "close": c,
                "volume": 1_000,
                "vwap": (o + c) / 2.0,
            }
        )
        d += timedelta(days=1)
    return BarSet(pd.DataFrame(out))


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


def _make_loaded_strategy(strategy_id: str, universe: tuple[str, ...]):
    config_path = _write_strategy_yaml(Path(tempfile.mkdtemp()))
    config = MagicMock()
    config.strategy_id = strategy_id
    config.family = "regime"
    config.template = "daily.sma200_rotation"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.001, "commission_per_trade": 0.0}
    config.universe = universe

    context = StrategyContext(
        strategy_id=strategy_id,
        family="regime",
        template="daily.sma200_rotation",
        variant="test",
        version=1,
        config_hash="abc123",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path="configs/fake.yaml",
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


def _make_event_store() -> EventStore:
    return EventStore(Path(tempfile.mktemp(suffix=".db")))


def test_buy_decided_on_t_close_fills_at_t_plus_1_open():
    """Decision-day price 100, next-day open 102: fill must be 102 * (1 + slippage)."""
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.t1.buy", universe)

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

    # Day 1: open=100, close=100. Day 2: open=102, close=110.
    # The strategy decides on day 1's close (100). The fill must use day 2's
    # open (102), NOT day 1's close (100) and NOT day 2's close (110).
    barset = _make_barset_ohlc([(100.0, 100.0), (102.0, 110.0)], start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    slippage = 0.01
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=10_000.0,
        slippage_pct=slippage,
        commission_per_trade=0.0,
    )
    result = engine.run(start, end)

    trades = store.list_trades_for_backtest_run(result.db_id)
    buys = [t for t in trades if t.side == "buy"]
    assert len(buys) == 1, f"expected exactly one BUY, got {len(buys)}: {trades!r}"
    expected_fill = 102.0 * (1.0 + slippage)
    actual_fill = buys[0].estimated_unit_price
    assert actual_fill == pytest_approx(expected_fill), (
        f"BUY decided on T close 100 must fill at T+1 open {102.0} * (1+slippage), "
        f"not at T close or T+1 close. expected={expected_fill}, got={actual_fill}"
    )


def test_sell_decided_on_t_close_fills_at_t_plus_1_open():
    """SELL decision-day close 105, next-day open 98: fill must be 98 * (1 - slippage)."""
    start = date(2024, 1, 2)
    end = date(2024, 1, 5)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.t1.sell", universe)

    sell_day = start + timedelta(days=2)  # day 3 (Jan 4)

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
        if current_day == sell_day:
            return _decision(
                [
                    TradeIntent(
                        symbol="SPY",
                        side=OrderSide.SELL,
                        quantity=1.0,
                        order_type=OrderType.MARKET,
                    )
                ]
            )
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    # Day 1 (Jan 2): open=100, close=100. BUY decision.
    # Day 2 (Jan 3): open=102, close=104. BUY fills @ 102.
    # Day 3 (Jan 4): open=104, close=105. SELL decision.
    # Day 4 (Jan 5): open=98, close=99. SELL fills @ 98.
    barset = _make_barset_ohlc(
        [(100.0, 100.0), (102.0, 104.0), (104.0, 105.0), (98.0, 99.0)],
        start=start,
    )
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    slippage = 0.01
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=10_000.0,
        slippage_pct=slippage,
        commission_per_trade=0.0,
    )
    result = engine.run(start, end)

    trades = store.list_trades_for_backtest_run(result.db_id)
    sells = [t for t in trades if t.side == "sell"]
    assert len(sells) == 1, f"expected exactly one SELL, got {len(sells)}: {trades!r}"
    expected_fill = 98.0 * (1.0 - slippage)
    actual_fill = sells[0].estimated_unit_price
    assert actual_fill == pytest_approx(expected_fill), (
        f"SELL decided on T close 105 must fill at T+1 open 98.0 * (1-slippage), "
        f"not at T close or T+1 close. expected={expected_fill}, got={actual_fill}"
    )


def test_pending_order_at_window_boundary_is_dropped():
    """Decision on the last bar has no T+1 to fill on — order is silently dropped."""
    start = date(2024, 1, 2)
    end = date(2024, 1, 3)
    universe = ("SPY",)
    loaded = _make_loaded_strategy("test.t1.boundary", universe)

    # Strategy decides BUY on EVERY bar — so it decides on the last bar too,
    # which has no T+1 to fill on.
    last_day = end

    def fake_evaluate(bars, context):
        df = bars.to_dataframe()
        timestamps = pd.to_datetime(df["timestamp"], utc=True)
        current_day = timestamps.dt.date.max()
        if current_day == last_day:
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

    # Two-day fixture. Strategy decides BUY only on day 2 (the last day).
    barset = _make_barset_ohlc([(100.0, 100.0), (102.0, 105.0)], start=start)
    provider = MagicMock()
    provider.get_bars.return_value = {"SPY": barset}

    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=10_000.0,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    result = engine.run(start, end)

    trades = store.list_trades_for_backtest_run(result.db_id)
    assert [trade.status for trade in trades] == ["skipped"]
    assert trades[0].message == (
        "Skipped backtest buy for SPY: no next bar available before run end."
    )
    assert result.buy_count == 0
    assert result.sell_count == 0
    assert result.trade_count == 0
    assert result.skipped_count == 1
    # Cash must be unchanged — no fill, no commission, nothing.
    assert result.final_equity == 10_000.0, (
        f"equity must be unchanged when boundary order is dropped; got {result.final_equity}"
    )


# pytest helpers --------------------------------------------------------------


def pytest_approx(value: float, rel: float = 1e-9, abs_: float = 1e-9):
    import pytest as _pytest

    return _pytest.approx(value, rel=rel, abs=abs_)
