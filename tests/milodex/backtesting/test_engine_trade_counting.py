"""Tests for round_trip_count tracking in BacktestEngine and walk-forward runner.

round_trip_count = sum over symbols of min(buy_count, sell_count).
For long-only strategies this equals the number of completed (closed) positions.
trade_count remains unchanged — it counts every fill (buy + sell).
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

from milodex.backtesting.engine import BacktestEngine
from milodex.backtesting.walk_forward_runner import (
    WalkForwardWindow,
    _aggregate_oos,
    run_walk_forward,
)
from milodex.broker.models import OrderSide, OrderType
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies.base import DecisionReasoning, StrategyDecision

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _decision(intents: list) -> StrategyDecision:
    return StrategyDecision(
        intents=list(intents),
        reasoning=DecisionReasoning(rule="no_signal", narrative="stub"),
    )


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


def _make_intent(symbol: str, side: OrderSide, qty: float = 1.0) -> TradeIntent:
    return TradeIntent(
        symbol=symbol,
        side=side,
        quantity=qty,
        order_type=OrderType.MARKET,
    )


_STRATEGY_YAML = """\
strategy:
  name: "trade_counting_test"
  version: 1
  description: "Test strategy for round-trip counting tests."
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 5
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: 1
"""


def _make_loaded_strategy(universe: tuple[str, ...]):
    from milodex.strategies.base import StrategyContext

    tmp_dir = Path(tempfile.mkdtemp())
    yaml_path = tmp_dir / "strategy.yaml"
    yaml_path.write_text(_STRATEGY_YAML, encoding="utf-8")

    config = MagicMock()
    config.strategy_id = "test.roundtrip.v1"
    config.family = "test"
    config.template = "daily.test"
    config.stage = "backtest"
    config.path = yaml_path
    config.parameters = {}
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.universe = universe
    config.risk = {}

    real_context = StrategyContext(
        strategy_id=config.strategy_id,
        family="test",
        template="daily.test",
        variant="v",
        version=1,
        config_hash="testhash",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path=str(yaml_path),
        manifest={},
    )

    strategy = MagicMock()
    strategy.evaluate.return_value = _decision([])

    loaded = MagicMock()
    loaded.config = config
    loaded.context = real_context
    loaded.strategy = strategy
    return loaded, strategy


def _make_event_store() -> EventStore:
    return EventStore(Path(tempfile.mktemp(suffix=".db")))


def _make_engine(
    universe: tuple[str, ...] = ("SPY",),
    bars: dict[str, BarSet] | None = None,
    bar_count: int = 20,
    bars_start: date = date(2024, 1, 2),
):
    loaded, strategy = _make_loaded_strategy(universe)
    if bars is None:
        barset = _make_barset([100.0 + i for i in range(bar_count)], start=bars_start)
        bars = {sym: barset for sym in universe}
    provider = MagicMock()
    provider.get_bars.return_value = bars
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )
    return engine, strategy, store, bars_start


# ---------------------------------------------------------------------------
# Step 3 / 4: partial-close — 3 BUYs, 2 SELLs across 3 distinct symbols
# trade_count=5, round_trip_count=2
# ---------------------------------------------------------------------------


def test_partial_close_three_buys_two_sells():
    """3 BUY fills + 2 SELL fills across 3 symbols → trade_count=5, round_trip_count=2.

    Sequence:
      Day 1 close: enqueue BUY SPY, BUY AAPL, BUY TSLA
      Day 2 open:  fills for SPY, AAPL, TSLA  (buy_count=3)
      Day 2 close: enqueue SELL SPY, SELL AAPL   (not TSLA)
      Day 3 open:  fills for SPY, AAPL (sell_count=2)
    """
    symbols = ("SPY", "AAPL", "TSLA")
    bars_start = date(2024, 1, 2)
    bar_count = 5

    closes = [100.0 + i for i in range(bar_count)]
    barsets = {sym: _make_barset(closes, start=bars_start) for sym in symbols}
    loaded, strategy = _make_loaded_strategy(symbols)
    provider = MagicMock()
    provider.get_bars.return_value = barsets
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )

    call_count = [0]

    def fake_evaluate(bars, context):
        call_count[0] += 1
        n = call_count[0]
        if n == 1:
            # Day 1: enqueue buy all three
            return _decision([
                _make_intent("SPY", OrderSide.BUY),
                _make_intent("AAPL", OrderSide.BUY),
                _make_intent("TSLA", OrderSide.BUY),
            ])
        if n == 2:
            # Day 2 (after buys filled): sell SPY and AAPL only
            return _decision([
                _make_intent("SPY", OrderSide.SELL),
                _make_intent("AAPL", OrderSide.SELL),
            ])
        return _decision([])

    strategy.evaluate.side_effect = fake_evaluate

    start = bars_start
    end = bars_start + timedelta(days=bar_count - 1)
    result = engine.run(start, end)

    assert result.trade_count == 5, f"expected 5, got {result.trade_count}"
    assert result.round_trip_count == 2, f"expected 2, got {result.round_trip_count}"


# ---------------------------------------------------------------------------
# Step 5: all-closed — 3 buys, 3 sells across 3 distinct symbols
# trade_count=6, round_trip_count=3
# ---------------------------------------------------------------------------


def test_all_closed_three_symbols():
    """3 BUY fills + 3 SELL fills across 3 symbols → trade_count=6, round_trip_count=3."""
    symbols = ("SPY", "AAPL", "TSLA")
    bars_start = date(2024, 1, 2)
    bar_count = 5

    closes = [100.0 + i for i in range(bar_count)]
    barsets = {sym: _make_barset(closes, start=bars_start) for sym in symbols}
    loaded, strategy = _make_loaded_strategy(symbols)
    provider = MagicMock()
    provider.get_bars.return_value = barsets
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )

    call_count = [0]

    def fake_evaluate(bars, context):
        call_count[0] += 1
        n = call_count[0]
        if n == 1:
            return _decision([
                _make_intent("SPY", OrderSide.BUY),
                _make_intent("AAPL", OrderSide.BUY),
                _make_intent("TSLA", OrderSide.BUY),
            ])
        if n == 2:
            return _decision([
                _make_intent("SPY", OrderSide.SELL),
                _make_intent("AAPL", OrderSide.SELL),
                _make_intent("TSLA", OrderSide.SELL),
            ])
        return _decision([])

    strategy.evaluate.side_effect = fake_evaluate

    start = bars_start
    end = bars_start + timedelta(days=bar_count - 1)
    result = engine.run(start, end)

    assert result.trade_count == 6, f"expected 6, got {result.trade_count}"
    assert result.round_trip_count == 3, f"expected 3, got {result.round_trip_count}"


# ---------------------------------------------------------------------------
# Step 6: multi-cycle — same symbol, 2 buys + 2 sells
# trade_count=4, round_trip_count=2
# ---------------------------------------------------------------------------


def test_multi_cycle_same_symbol():
    """Same symbol: 2 buy-sell cycles → trade_count=4, round_trip_count=2."""
    symbols = ("SPY",)
    bars_start = date(2024, 1, 2)
    bar_count = 8

    closes = [100.0 + i for i in range(bar_count)]
    barsets = {sym: _make_barset(closes, start=bars_start) for sym in symbols}
    loaded, strategy = _make_loaded_strategy(symbols)
    provider = MagicMock()
    provider.get_bars.return_value = barsets
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )

    call_count = [0]

    def fake_evaluate(bars, context):
        call_count[0] += 1
        n = call_count[0]
        if n == 1:
            return _decision([_make_intent("SPY", OrderSide.BUY)])
        if n == 2:
            return _decision([_make_intent("SPY", OrderSide.SELL)])
        if n == 3:
            return _decision([_make_intent("SPY", OrderSide.BUY)])
        if n == 4:
            return _decision([_make_intent("SPY", OrderSide.SELL)])
        return _decision([])

    strategy.evaluate.side_effect = fake_evaluate

    start = bars_start
    end = bars_start + timedelta(days=bar_count - 1)
    result = engine.run(start, end)

    assert result.trade_count == 4, f"expected 4, got {result.trade_count}"
    assert result.round_trip_count == 2, f"expected 2, got {result.round_trip_count}"


# ---------------------------------------------------------------------------
# Step 7 / 8: walk-forward — round_trip_count summed across windows
# ---------------------------------------------------------------------------


def test_walk_forward_round_trip_count_summed_across_windows():
    """Walk-forward oos_round_trip_count sums per-window round_trip_count values.

    Each window sees 1 BUY + 1 SELL on SPY → round_trip_count=1 per window.
    With 2 windows, oos_round_trip_count should be 2.
    """
    symbols = ("SPY",)
    bars_start = date(2024, 1, 2)
    bar_count = 30

    closes = [100.0 + i for i in range(bar_count)]
    barsets = {sym: _make_barset(closes, start=bars_start) for sym in symbols}
    loaded, strategy = _make_loaded_strategy(symbols)
    provider = MagicMock()
    provider.get_bars.return_value = barsets
    store = _make_event_store()
    engine = BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )

    call_count = [0]

    def fake_evaluate(bars, context):
        call_count[0] += 1
        n = call_count[0]
        # On every odd call, buy; on every even call, sell.
        if n % 2 == 1:
            return _decision([_make_intent("SPY", OrderSide.BUY)])
        return _decision([_make_intent("SPY", OrderSide.SELL)])

    strategy.evaluate.side_effect = fake_evaluate

    start = bars_start
    end = bars_start + timedelta(days=bar_count - 1)

    result = run_walk_forward(
        engine,
        start_date=start,
        end_date=end,
        train_days=10,
        test_days=5,
        step_days=5,
        initial_equity=100_000.0,
    )

    # oos_round_trip_count must be the sum across windows, not 0
    assert result.oos_round_trip_count >= 1, (
        f"expected oos_round_trip_count >= 1, got {result.oos_round_trip_count}"
    )

    # verify the per-window values sum to the aggregate
    expected = sum(w.round_trip_count for w in result.windows)
    assert result.oos_round_trip_count == expected, (
        f"aggregate {result.oos_round_trip_count} != sum of windows {expected}"
    )

    # and the stored metadata carries it too
    stored = store.get_backtest_run(result.run_id)
    assert stored is not None
    assert "round_trip_count" in stored.metadata["oos_aggregate"]
    assert stored.metadata["oos_aggregate"]["round_trip_count"] == expected


# ---------------------------------------------------------------------------
# Backward compat: default=0 when no trades
# ---------------------------------------------------------------------------


def test_round_trip_count_defaults_to_zero_when_no_trades():
    """A no-signal strategy produces round_trip_count=0 without breaking."""
    engine, strategy, store, bars_start = _make_engine(bar_count=10)
    # strategy.evaluate already returns _decision([]) by default
    result = engine.run(bars_start, bars_start + timedelta(days=9))
    assert result.round_trip_count == 0
    assert result.trade_count == 0


# ---------------------------------------------------------------------------
# _aggregate_oos unit test: round_trip_count summed, not averaged
# ---------------------------------------------------------------------------


def test_aggregate_oos_sums_round_trip_count():
    """_aggregate_oos sums round_trip_count across windows."""
    # Build minimal WalkForwardWindow objects using keyword overrides
    def _win(idx: int, rt: int) -> WalkForwardWindow:
        d = date(2024, 1, 1) + timedelta(days=idx * 5)
        curve = [(d + timedelta(days=i), 100_000.0) for i in range(5)]
        return WalkForwardWindow(
            index=idx,
            train_start=d - timedelta(days=10),
            train_end=d - timedelta(days=1),
            test_start=d,
            test_end=d + timedelta(days=4),
            trading_days=5,
            trade_count=rt * 2,
            initial_equity=100_000.0,
            final_equity=100_000.0,
            total_return_pct=0.0,
            sharpe=None,
            max_drawdown_pct=0.0,
            equity_curve=curve,
            round_trip_count=rt,
        )

    windows = [_win(0, 3), _win(1, 2), _win(2, 1)]
    agg = _aggregate_oos(windows, initial_equity=100_000.0)
    assert agg.round_trip_count == 6  # 3 + 2 + 1
    assert agg.trade_count == 12     # (3+2+1)*2
