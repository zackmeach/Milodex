"""Tests for the mean-reversion IBS low-close daily strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.meanrev_ibs_lowclose import MeanrevIbsLowcloseStrategy


def test_ibs_enters_on_low_close_above_ma() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    # Warmup: flat at 80 (SMA anchor). Today: wide range, close near low but
    # above SMA. SMA(10) = (80*9 + 86)/10 = 80.6; close 86 > 80.6.
    # IBS = (86 - 85) / (110 - 85) = 0.04.
    bars = _ohlc_series(
        opens=[80.0] * 20 + [90.0],
        highs=[82.0] * 20 + [110.0],
        lows=[78.0] * 20 + [85.0],
        closes=[80.0] * 20 + [86.0],
    )
    context = _context(
        universe=("AAA",),
        positions={},
        bars_by_symbol={"AAA": bars},
        equity=10_000.0,
        overrides={"ma_filter_length": 10, "ibs_entry_threshold": 0.2},
    )

    decision = strategy.evaluate(_flat_barset(), context)

    assert len(decision.intents) == 1
    intent = decision.intents[0]
    assert intent.symbol == "AAA"
    assert intent.side == OrderSide.BUY
    assert intent.order_type == OrderType.MARKET
    assert decision.reasoning.rule == "meanrev.ibs_entry"


def test_ibs_rejects_when_close_not_above_ma() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    # MA anchored high, today closes well below it.
    bars = _ohlc_series(
        opens=[100.0] * 19 + [80.0],
        highs=[102.0] * 19 + [82.0],
        lows=[98.0] * 19 + [75.0],
        closes=[100.0] * 19 + [75.5],  # IBS low, but below SMA(10)
    )
    context = _context(
        universe=("AAA",),
        positions={},
        bars_by_symbol={"AAA": bars},
        equity=10_000.0,
        overrides={"ma_filter_length": 10},
    )

    decision = strategy.evaluate(_flat_barset(), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    rejections = {r["symbol"]: r["reason"] for r in decision.reasoning.rejected_alternatives}
    assert "AAA" in rejections
    assert "SMA" in rejections["AAA"] or "not above" in rejections["AAA"]


def test_ibs_rejects_when_ibs_above_threshold() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    # Close near the high → IBS ~ 1, rejected.
    bars = _ohlc_series(
        opens=[100.0] * 20 + [100.0],
        highs=[102.0] * 20 + [104.0],
        lows=[98.0] * 20 + [96.0],
        closes=[100.0] * 20 + [103.5],
    )
    context = _context(
        universe=("AAA",),
        positions={},
        bars_by_symbol={"AAA": bars},
        equity=10_000.0,
        overrides={"ma_filter_length": 10, "ibs_entry_threshold": 0.2},
    )

    decision = strategy.evaluate(_flat_barset(), context)
    assert decision.intents == []
    rejections = {r["symbol"]: r["reason"] for r in decision.reasoning.rejected_alternatives}
    assert "IBS" in rejections["AAA"]


def test_ibs_rejects_zero_range_bar() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    # Warmup at 80 so today's close 85 > SMA; today high == low (halted).
    bars = _ohlc_series(
        opens=[80.0] * 20 + [85.0],
        highs=[82.0] * 20 + [85.0],
        lows=[78.0] * 20 + [85.0],
        closes=[80.0] * 20 + [85.0],
    )
    context = _context(
        universe=("AAA",),
        positions={},
        bars_by_symbol={"AAA": bars},
        equity=10_000.0,
        overrides={"ma_filter_length": 10},
    )

    decision = strategy.evaluate(_flat_barset(), context)
    assert decision.intents == []
    rejections = {r["symbol"]: r["reason"] for r in decision.reasoning.rejected_alternatives}
    assert "zero-range" in rejections["AAA"]


def test_ibs_ranking_selects_lowest_ibs_first() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    # Warmup flat at 80. Today: AAA IBS=0.5, BBB IBS=0.1 (lowest → picked).
    warmup_opens = [80.0] * 20
    warmup_highs = [82.0] * 20
    warmup_lows = [78.0] * 20
    warmup_closes = [80.0] * 20

    aaa = _ohlc_series(
        opens=warmup_opens + [90.0],
        highs=warmup_highs + [100.0],
        lows=warmup_lows + [80.0],
        closes=warmup_closes + [90.0],  # IBS = (90-80)/(100-80) = 0.5
    )
    bbb = _ohlc_series(
        opens=warmup_opens + [90.0],
        highs=warmup_highs + [100.0],
        lows=warmup_lows + [80.0],
        closes=warmup_closes + [82.0],  # IBS = (82-80)/(100-80) = 0.1
    )

    context = _context(
        universe=("AAA", "BBB"),
        positions={},
        bars_by_symbol={"AAA": aaa, "BBB": bbb},
        equity=10_000.0,
        overrides={
            "ma_filter_length": 10,
            "ibs_entry_threshold": 0.6,
            "max_concurrent_positions": 1,
        },
    )

    decision = strategy.evaluate(_flat_barset(), context)
    assert len(decision.intents) == 1
    assert decision.intents[0].symbol == "BBB"


def test_ibs_exit_on_prior_high_break() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    # Prior high 104 → today closes 105 → prior-high exit fires.
    bars = _ohlc_series(
        opens=[100.0] * 19 + [100.0, 102.0],
        highs=[102.0] * 19 + [104.0, 106.0],
        lows=[98.0] * 19 + [96.0, 100.0],
        closes=[100.0] * 19 + [97.0, 105.0],
    )
    context = _context(
        universe=("AAA",),
        positions={"AAA": 10.0},
        bars_by_symbol={"AAA": bars},
        equity=5_000.0,
        overrides={"ma_filter_length": 10},
    )

    decision = strategy.evaluate(_flat_barset(), context)
    assert [(i.side.value, i.symbol, i.quantity) for i in decision.intents] == [
        ("sell", "AAA", 10.0),
    ]
    assert decision.reasoning.rule == "meanrev.ibs_exit"


def test_ibs_exit_on_stop_loss_from_entry_state() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    # Entry at 100; stop 3%; today closes 96 → stop-loss trips.
    bars = _ohlc_series(
        opens=[100.0] * 20 + [100.0],
        highs=[102.0] * 20 + [100.5],
        lows=[98.0] * 20 + [95.5],
        closes=[100.0] * 20 + [96.0],
    )
    context = _context(
        universe=("AAA",),
        positions={"AAA": 10.0},
        bars_by_symbol={"AAA": bars},
        equity=5_000.0,
        overrides={"ma_filter_length": 10, "stop_loss_pct": 0.03},
        entry_state={"AAA": {"entry_price": 100.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_flat_barset(), context)
    assert [(i.side.value, i.symbol) for i in decision.intents] == [("sell", "AAA")]
    assert decision.reasoning.rule == "meanrev.stop_loss"


def test_ibs_exit_on_max_hold_days_from_entry_state() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _ohlc_series(
        opens=[100.0] * 20 + [100.0],
        highs=[102.0] * 20 + [103.0],  # below prior high 102? actually 103 > 102
        lows=[98.0] * 20 + [99.0],
        closes=[100.0] * 20 + [100.0],  # not above prior_high=102
    )
    context = _context(
        universe=("AAA",),
        positions={"AAA": 10.0},
        bars_by_symbol={"AAA": bars},
        equity=5_000.0,
        overrides={"ma_filter_length": 10, "max_hold_days": 3},
        entry_state={"AAA": {"entry_price": 100.0, "held_days": 3}},
    )

    decision = strategy.evaluate(_flat_barset(), context)
    assert [(i.side.value, i.symbol) for i in decision.intents] == [("sell", "AAA")]
    assert decision.reasoning.rule == "meanrev.max_hold"


def test_ibs_ignores_positions_outside_declared_universe() -> None:
    """A position on a symbol outside the strategy's universe belongs to
    another strategy; IBS must not treat it as its own and emit an exit."""
    strategy = MeanrevIbsLowcloseStrategy()
    aaa = _ohlc_series(
        opens=[100.0] * 20 + [100.0],
        highs=[102.0] * 20 + [104.0],
        lows=[98.0] * 20 + [96.0],
        closes=[100.0] * 20 + [103.5],  # IBS high, no entry
    )
    spy = _ohlc_series(
        opens=[100.0] * 19 + [100.0, 102.0],
        highs=[102.0] * 19 + [104.0, 106.0],
        lows=[98.0] * 19 + [96.0, 100.0],
        closes=[100.0] * 19 + [97.0, 105.0],  # would trigger prior_high_exit
    )
    context = _context(
        universe=("AAA",),
        positions={"SPY": 13.0},
        bars_by_symbol={"AAA": aaa, "SPY": spy},
        equity=10_000.0,
        overrides={"ma_filter_length": 10},
    )

    decision = strategy.evaluate(_flat_barset(), context)
    assert [i for i in decision.intents if i.side == OrderSide.SELL] == []


def test_ibs_no_signal_when_nothing_qualifies() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    bars = _ohlc_series(
        opens=[100.0] * 20 + [100.0],
        highs=[102.0] * 20 + [104.0],
        lows=[98.0] * 20 + [96.0],
        closes=[100.0] * 20 + [103.5],  # IBS ~0.94
    )
    context = _context(
        universe=("AAA",),
        positions={},
        bars_by_symbol={"AAA": bars},
        equity=10_000.0,
        overrides={"ma_filter_length": 10, "ibs_entry_threshold": 0.2},
    )

    decision = strategy.evaluate(_flat_barset(), context)
    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_ibs_rejects_invalid_parameters() -> None:
    strategy = MeanrevIbsLowcloseStrategy()
    context = _context(
        universe=("AAA",),
        positions={},
        bars_by_symbol={
            "AAA": _ohlc_series(
                opens=[100.0] * 20, highs=[102.0] * 20, lows=[98.0] * 20, closes=[100.0] * 20
            )
        },
        equity=10_000.0,
        overrides={"ibs_entry_threshold": 1.5},
    )

    with pytest.raises(ValueError, match="ibs_entry_threshold must be in"):
        strategy.evaluate(_flat_barset(), context)


def test_default_loader_resolves_ibs_strategy() -> None:
    loader = StrategyLoader()
    loaded = loader.load(Path("configs/meanrev_daily_ibslowclose_index_etfs_v1.yaml"))

    assert isinstance(loaded.strategy, MeanrevIbsLowcloseStrategy)
    assert loaded.context.strategy_id == "meanrev.daily.ibs_lowclose.index_etfs.v1"
    assert loaded.context.universe_ref == "universe.index_etfs.v1"
    assert set(loaded.context.universe) == {"SPY", "QQQ", "IWM", "DIA"}


# ------------------------- helpers -------------------------


def _context(
    *,
    universe: tuple[str, ...],
    positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    equity: float,
    overrides: dict[str, object] | None = None,
    entry_state: dict[str, dict[str, object]] | None = None,
) -> StrategyContext:
    parameters: dict[str, object] = {
        "ibs_entry_threshold": 0.2,
        "prior_high_exit_enabled": True,
        "ma_filter_length": 200,
        "stop_loss_pct": 0.03,
        "max_hold_days": 3,
        "max_concurrent_positions": 2,
        "sizing_rule": "equal_notional",
        "per_position_notional_pct": 0.20,
        "ranking_enabled": True,
        "ranking_metric": "ibs_ascending",
        "market_regime_symbol": "",
        "market_regime_ma_length": 200,
    }
    if overrides:
        parameters.update(overrides)

    return StrategyContext(
        strategy_id="meanrev.daily.ibs_lowclose.index_etfs.v1",
        family="meanrev",
        template="daily.ibs_lowclose",
        variant="index_etfs",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=universe,
        universe_ref="universe.index_etfs.v1",
        disable_conditions=(),
        config_path="configs/meanrev_daily_ibslowclose_index_etfs_v1.yaml",
        manifest={},
        positions=positions,
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _ohlc_series(
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> BarSet:
    length = len(closes)
    timestamps = pd.date_range("2025-01-01", periods=length, freq="D", tz=UTC)
    dataframe = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000] * length,
            "vwap": closes,
        }
    )
    return BarSet(dataframe)


def _flat_barset() -> BarSet:
    return _ohlc_series(opens=[1.0], highs=[1.0], lows=[1.0], closes=[1.0])
