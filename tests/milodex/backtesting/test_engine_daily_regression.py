"""Daily regression suite for BacktestEngine — pre-refactor baseline.

These 4 tests capture expected output values from the CURRENT (unmodified)
engine. They are the safety net for the intraday refactor in Phases C–F: any
future task that accidentally changes daily simulation behavior will cause one
or more of these to fail.

Guarantee (from the intraday plan):
  - same equity_curve dates + values within $0.01
  - same exact int counts (trade_count, buy_count, sell_count, skipped_count,
    round_trip_count)

Design notes:
  - All 4 tests use the _make_loaded_strategy + MagicMock pattern from
    test_engine.py — no real YAML files needed.
  - Bars are built with _ohlc_barset (mirrors test_meanrev_ibs_lowclose.py).
  - All fixtures are deterministic: no randomness, fixed OHLC sequences.
  - slippage_pct=0.0, commission_per_trade=0.0 to make arithmetic exact.
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

# ---------------------------------------------------------------------------
# Fixtures + helpers (shared across all 4 tests)
# ---------------------------------------------------------------------------


def _decision(intents: list) -> StrategyDecision:
    return StrategyDecision(
        intents=list(intents),
        reasoning=DecisionReasoning(rule="no_signal", narrative="regression stub"),
    )


def _make_event_store() -> EventStore:
    tmp = tempfile.mktemp(suffix=".db")
    return EventStore(Path(tmp))


def _make_loaded_strategy(
    strategy_id: str,
    universe: tuple[str, ...],
    parameters: dict | None = None,
) -> MagicMock:
    """Return a mock LoadedStrategy for regression tests.

    Mirrors _make_loaded_strategy from test_engine.py but allows parameter
    injection for strategies that use them (e.g. IBS threshold).
    """
    tmp_dir = Path(tempfile.mkdtemp())
    yaml_text = """\
strategy:
  name: "regression_test"
  version: 1
  description: "Daily regression fixture."
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.50
    max_positions: 4
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: 30
"""
    config_path = tmp_dir / "regression.yaml"
    config_path.write_text(yaml_text, encoding="utf-8")

    effective_params = parameters or {}

    config = MagicMock()
    config.strategy_id = strategy_id
    config.family = "meanrev"
    config.template = "daily.ibs_lowclose"
    config.stage = "backtest"
    config.path = config_path
    config.parameters = effective_params
    config.backtest = {"slippage_pct": 0.0, "commission_per_trade": 0.0}
    config.universe = universe
    config.risk = {"max_position_pct": 0.50, "max_positions": 4}

    context = StrategyContext(
        strategy_id=strategy_id,
        family="meanrev",
        template="daily.ibs_lowclose",
        variant="regression",
        version=1,
        config_hash="regression_hash",
        parameters=effective_params,
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


def _ohlc_barset(rows: list[tuple[float, float, float, float]], start: date) -> BarSet:
    """Build a BarSet from (open, high, low, close) tuples on consecutive calendar days.

    Volume + vwap are deterministic placeholders. Mirrors the pattern from
    tests/milodex/strategies/test_meanrev_ibs_lowclose.py but takes an explicit
    start date so regression tests produce the exact same dates every run.
    """
    timestamps = [pd.Timestamp(start + timedelta(days=i), tz="UTC") for i in range(len(rows))]
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [1_000_000] * len(rows),
            "vwap": [r[3] for r in rows],
        }
    )
    return BarSet(df)


def _flat_ohlc(price: float, n: int, start: date) -> BarSet:
    """n days of flat price bars (open=high=low=close=price)."""
    rows = [(price, price, price, price)] * n
    return _ohlc_barset(rows, start)


def _ramp_ohlc(base: float, step: float, n: int, start: date) -> BarSet:
    """n days of steadily-rising bars (no intraday range)."""
    rows = []
    for i in range(n):
        p = base + i * step
        rows.append((p, p, p, p))
    return _ohlc_barset(rows, start)


def _make_engine(
    loaded: MagicMock,
    bars_by_symbol: dict[str, BarSet],
    initial_equity: float = 100_000.0,
) -> BacktestEngine:
    """Wire a BacktestEngine with a mock provider that returns pre-built bars."""
    provider = MagicMock()
    provider.get_bars.return_value = bars_by_symbol
    store = _make_event_store()
    return BacktestEngine(
        loaded=loaded,
        data_provider=provider,
        event_store=store,
        initial_equity=initial_equity,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )


# ---------------------------------------------------------------------------
# Regression test 1: simple long-only (single symbol, buy+sell round trips)
#
# Regression risk pinned: basic daily path — T+1 fill timing, equity curve
# accumulation, round_trip_count, held_days tracking.
#
# Strategy: buy 10 shares of SPY on day 1; sell on day 3 (held_days = 2 ≥
# max_hold_days=2). Then buy again on day 4; sell on day 6. Two round trips.
# 130 calendar days → 130 equity-curve entries.
# ---------------------------------------------------------------------------


def test_daily_regression_simple_long_only() -> None:
    """Single-symbol, two round trips over 130 days.

    Strategy logic is wired via side_effect: BUY on day 1 and day 4 (0-indexed
    simulation days within the run window), SELL after 2 held days.
    """
    n_days = 130
    start = date(2024, 1, 2)
    end = start + timedelta(days=n_days - 1)

    spy_price = 400.0
    loaded = _make_loaded_strategy("regression.daily.simple_long.v1", ("SPY",))

    # Flat bars at 400.0 — fill price is always 400.0 (slippage=0).
    spy_bars = _flat_ohlc(spy_price, n_days, start)

    # State we track manually to mirror the strategy's view.
    _bought_days: list[date] = []

    def fake_evaluate(bars: BarSet, context):  # noqa: ANN001
        df = bars.to_dataframe()
        if df.empty:
            return _decision([])
        current_day = pd.to_datetime(df["timestamp"], utc=True).dt.date.max()

        held = context.entry_state.get("SPY", {}).get("held_days", 0)
        has_position = "SPY" in context.positions and context.positions["SPY"] > 0

        if has_position and held >= 2:
            intent = TradeIntent(
                symbol="SPY", side=OrderSide.SELL, quantity=10.0, order_type=OrderType.MARKET
            )
            return _decision([intent])
        if not has_position and len(_bought_days) < 2:
            _bought_days.append(current_day)
            intent = TradeIntent(
                symbol="SPY", side=OrderSide.BUY, quantity=10.0, order_type=OrderType.MARKET
            )
            return _decision([intent])
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    engine = _make_engine(loaded, {"SPY": spy_bars})
    result = engine.run(start, end)

    # --- Encoded from first run ---
    assert result.buy_count == 2
    assert result.sell_count == 2
    assert result.trade_count == 4
    assert result.skipped_count == 0
    assert result.round_trip_count == 2
    assert len(result.equity_curve) == n_days
    assert result.equity_curve[0][0] == start
    # Flat bars + zero slippage/commission → no P&L.
    assert abs(result.final_equity - 100_000.0) < 0.01
    assert abs(result.equity_curve[-1][1] - 100_000.0) < 0.01


# ---------------------------------------------------------------------------
# Regression test 2: multi-symbol cross-sectional (dict-iteration order)
#
# Regression risk pinned: any future refactor that changes how bars_by_symbol
# is iterated (e.g. dict ordering, sorted() insertion) would change which
# symbol gets selected here, changing trade_count / equity_curve.
#
# Strategy: each day pick the symbol with the highest close (simple ranking).
# Buy the winner, sell when a different symbol takes the top rank.
# Universe: SPY, QQQ, IWM (3 symbols). Bars diverge after day 50 so the
# winner changes predictably.
# ---------------------------------------------------------------------------


def test_daily_regression_multi_symbol_cross_sectional() -> None:
    """3-symbol universe; winner rotates deterministically on day 51 and day 101.

    SPY starts at 400, QQQ at 350, IWM at 300.
    All three ramp at different rates so SPY > QQQ > IWM for days 1-50, then
    QQQ overtakes SPY on day 51, then IWM overtakes QQQ on day 101.
    The strategy tracks 1 position at a time, rotating to the top-ranked.
    """
    n_days = 125
    start = date(2024, 1, 2)
    end = start + timedelta(days=n_days - 1)

    # Step sizes: QQQ overtakes SPY after 50 days (350 + 50*step_q > 400 + 50*step_s).
    # SPY: base=400, step=0.5/day.  QQQ: base=350, step=1.5/day.  IWM: base=300, step=2.0/day.
    # After 50 days: SPY=425, QQQ=425 (tie at exactly 50) → use 51 to be safe.
    # After 100 days: SPY=450, QQQ=500, IWM=500 (tie at 100) → use 101 to be safe.
    spy_rows = [(400 + i * 0.5,) * 4 for i in range(n_days)]
    qqq_rows = [(350 + i * 1.5,) * 4 for i in range(n_days)]
    iwm_rows = [(300 + i * 2.0,) * 4 for i in range(n_days)]

    spy_bars = _ohlc_barset([(r[0], r[0], r[0], r[0]) for r in spy_rows], start)
    qqq_bars = _ohlc_barset([(r[0], r[0], r[0], r[0]) for r in qqq_rows], start)
    iwm_bars = _ohlc_barset([(r[0], r[0], r[0], r[0]) for r in iwm_rows], start)

    universe = ("SPY", "QQQ", "IWM")
    loaded = _make_loaded_strategy("regression.daily.xsec_rotation.v1", universe)

    def fake_evaluate(bars: BarSet, context):  # noqa: ANN001
        bars_by_sym = context.bars_by_symbol
        closes = {}
        for sym in universe:
            bs = bars_by_sym.get(sym)
            if bs is not None and len(bs) > 0:
                df = bs.to_dataframe()
                closes[sym] = float(df["close"].iloc[-1])
        if not closes:
            return _decision([])

        winner = max(closes, key=lambda s: closes[s])

        # Exit current position if winner changed.
        intents = []
        for sym, qty in context.positions.items():
            if sym != winner and qty > 0:
                intents.append(
                    TradeIntent(
                        symbol=sym, side=OrderSide.SELL, quantity=qty, order_type=OrderType.MARKET
                    )
                )
        # Enter winner if not already held.
        if winner not in context.positions or context.positions.get(winner, 0) <= 0:
            intents.append(
                TradeIntent(
                    symbol=winner, side=OrderSide.BUY, quantity=10.0, order_type=OrderType.MARKET
                )
            )
        return _decision(intents)

    loaded.strategy.evaluate.side_effect = fake_evaluate

    engine = _make_engine(loaded, {"SPY": spy_bars, "QQQ": qqq_bars, "IWM": iwm_bars})
    result = engine.run(start, end)

    # --- Encoded from first run ---
    # 3 buys (SPY→QQQ→IWM), 2 sells (SPY sold when QQQ wins, QQQ sold when IWM wins).
    # IWM position is open at end-of-window with no pending sell → skipped_count = 0.
    # round_trip_count: SPY (1 buy, 1 sell) + QQQ (1 buy, 1 sell) + IWM (1 buy, 0 sells)
    #   = min(1,1) + min(1,1) + min(1,0) = 2
    assert result.buy_count == 3
    assert result.sell_count == 2
    assert result.trade_count == 5
    assert result.skipped_count == 0
    assert result.round_trip_count == 2
    assert len(result.equity_curve) == n_days
    assert result.equity_curve[0][0] == start
    # Prices rise monotonically; sells happen at the open of the day after the
    # decision day. The sold positions were bought at flat open=close prices,
    # so the P&L is the difference between entry fill and exit fill.
    # The open IWM position is never sold — its unrealized P&L is included
    # in equity via mark-to-market.
    assert abs(result.final_equity - 101_445.0) < 0.01


# ---------------------------------------------------------------------------
# Regression test 3: stranded pending orders at end-of-window
#
# Regression risk pinned: the end-of-window pending-order drain path (the
# ``if pending and trading_days:`` block in _simulate). Covers skipped_count
# accumulation and the "backtest_no_next_bar" reason code.
#
# Strategy: emit BUY on the final day of the backtest window. There is no
# next bar to fill against, so the order is stranded → skipped_count = 1.
# ---------------------------------------------------------------------------


def test_daily_regression_stranded_pending_orders() -> None:
    """BUY on the last trading day → stranded pending → skipped_count = 1."""
    n_days = 10
    start = date(2024, 1, 2)
    end = start + timedelta(days=n_days - 1)

    spy_price = 300.0
    loaded = _make_loaded_strategy("regression.daily.stranded_pending.v1", ("SPY",))
    spy_bars = _flat_ohlc(spy_price, n_days, start)

    last_day = start + timedelta(days=n_days - 1)

    def fake_evaluate(bars: BarSet, _context):  # noqa: ANN001
        df = bars.to_dataframe()
        if df.empty:
            return _decision([])
        current_day = pd.to_datetime(df["timestamp"], utc=True).dt.date.max()
        if current_day == last_day:
            intent = TradeIntent(
                symbol="SPY", side=OrderSide.BUY, quantity=5.0, order_type=OrderType.MARKET
            )
            return _decision([intent])
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    engine = _make_engine(loaded, {"SPY": spy_bars})
    result = engine.run(start, end)

    # --- Encoded from first run ---
    assert result.buy_count == 0
    assert result.sell_count == 0
    assert result.trade_count == 0
    assert result.skipped_count == 1
    assert result.round_trip_count == 0
    assert len(result.equity_curve) == n_days
    assert result.equity_curve[0][0] == start
    assert abs(result.final_equity - 100_000.0) < 0.01


# ---------------------------------------------------------------------------
# Regression test 4: max_hold_days exit accounting
#
# Regression risk pinned: the held_days counter in the day loop. At the top
# of each day, every symbol in entry_state gets held_days += 1. The strategy
# reads context.entry_state["SPY"]["held_days"] to decide when to exit.
#
# Scenario: buy SPY on day 0. The strategy exits when held_days >= 3
# (max_hold_days=3). So:
#   day 1 → held_days=1, no exit
#   day 2 → held_days=2, no exit
#   day 3 → held_days=3, SELL
# After the sell, it buys again. Repeat N times.
# 120 days, buy on day 0 fills day 1, sell on day 4 (decision held_days=3
# on day 4 close, fills day 5). Then re-buy.
# Exact trade count depends on how many complete cycles fit in 120 days.
# ---------------------------------------------------------------------------


def test_daily_regression_max_hold_exits() -> None:
    """Held_days counter drives repeated buy→sell cycles with max_hold_days=3.

    buy fills on day T+1, held_days increments each loop iteration. SELL
    is emitted when held_days >= 3. Next BUY is emitted on the bar after
    the sell decision.
    """
    n_days = 50
    start = date(2024, 1, 2)
    end = start + timedelta(days=n_days - 1)

    spy_price = 200.0
    loaded = _make_loaded_strategy("regression.daily.max_hold_exits.v1", ("SPY",))
    spy_bars = _flat_ohlc(spy_price, n_days, start)

    # Track whether we've sent a BUY that hasn't filled yet (pending).
    _state: dict = {"buy_pending": False}

    def fake_evaluate(bars: BarSet, context):  # noqa: ANN001
        df = bars.to_dataframe()
        if df.empty:
            return _decision([])

        has_position = "SPY" in context.positions and context.positions["SPY"] > 0
        held = int(context.entry_state.get("SPY", {}).get("held_days", 0))

        # Exit when max hold reached.
        if has_position and held >= 3:
            _state["buy_pending"] = False
            intent = TradeIntent(
                symbol="SPY", side=OrderSide.SELL, quantity=5.0, order_type=OrderType.MARKET
            )
            return _decision([intent])
        # Enter if flat and no pending buy.
        if not has_position and not _state["buy_pending"]:
            _state["buy_pending"] = True
            intent = TradeIntent(
                symbol="SPY", side=OrderSide.BUY, quantity=5.0, order_type=OrderType.MARKET
            )
            return _decision([intent])
        # Clear buy_pending once the position appears.
        if has_position:
            _state["buy_pending"] = False
        return _decision([])

    loaded.strategy.evaluate.side_effect = fake_evaluate

    engine = _make_engine(loaded, {"SPY": spy_bars})
    result = engine.run(start, end)

    # --- Encoded from first run ---
    # Cycle: BUY decision day D, fills D+1. held_days=1 on D+2, =2 on D+3,
    # =3 on D+4 → SELL decision D+4, fills D+5. Re-BUY decision D+5, fills D+6.
    # Cycle length = 5 bars from buy-decision to next buy-decision.
    # 50 days → 10 buys, 9 sells fill, 10th buy stranded (emitted on last day,
    # no next bar available) → skipped_count=1.
    assert result.trade_count == 19
    assert result.buy_count == 10
    assert result.sell_count == 9
    assert result.skipped_count == 1
    assert result.round_trip_count == 9
    assert len(result.equity_curve) == n_days
    # Flat price + zero costs → equity unchanged.
    assert abs(result.final_equity - 100_000.0) < 0.01
