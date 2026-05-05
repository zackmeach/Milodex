"""Tests for the 52-week high proximity momentum strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.momentum_52w_high_proximity import Momentum52wHighProximityStrategy


def test_52w_high_proximity_enters_near_annual_high_with_positive_day() -> None:
    strategy = Momentum52wHighProximityStrategy()
    bars = _entry_bars(latest_close=99.0, prior_close=98.0, annual_high=100.0)
    context = _context(bars_by_symbol={"AAPL": _barset(bars)}, equity=10_000.0)

    decision = strategy.evaluate(_barset(bars), context)

    assert [(i.side, i.symbol) for i in decision.intents] == [(OrderSide.BUY, "AAPL")]
    assert decision.reasoning.rule == "momentum.52w_high_entry"


def test_52w_high_proximity_rejects_when_close_is_not_above_prior_close() -> None:
    strategy = Momentum52wHighProximityStrategy()
    bars = _entry_bars(latest_close=99.0, prior_close=99.5, annual_high=100.0)
    context = _context(bars_by_symbol={"AAPL": _barset(bars)}, equity=10_000.0)

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert decision.reasoning.rejected_alternatives[0]["reason"] == "close not above prior close"


def test_52w_high_proximity_exits_on_sma_break() -> None:
    strategy = Momentum52wHighProximityStrategy()
    bars = _exit_bars(latest_close=96.0)
    context = _context(
        positions={"AAPL": 10.0},
        bars_by_symbol={"AAPL": _barset(bars)},
        entry_state={"AAPL": {"entry_price": 100.0, "held_days": 2}},
    )

    decision = strategy.evaluate(_barset(bars), context)

    assert [(i.side, i.symbol, i.quantity) for i in decision.intents] == [
        (OrderSide.SELL, "AAPL", 10.0)
    ]
    assert decision.reasoning.rule == "momentum.52w_sma_exit"


def test_52w_high_proximity_exits_on_stop_loss() -> None:
    strategy = Momentum52wHighProximityStrategy()
    bars = _exit_bars(latest_close=94.0)
    context = _context(
        positions={"AAPL": 10.0},
        bars_by_symbol={"AAPL": _barset(bars)},
        entry_state={"AAPL": {"entry_price": 100.0, "held_days": 2}},
    )

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.rule == "momentum.52w_stop_loss"
    assert decision.intents[0].side == OrderSide.SELL


def test_52w_high_proximity_exits_on_max_hold() -> None:
    strategy = Momentum52wHighProximityStrategy()
    bars = _exit_bars(latest_close=102.0)
    context = _context(
        positions={"AAPL": 10.0},
        bars_by_symbol={"AAPL": _barset(bars)},
        entry_state={"AAPL": {"entry_price": 100.0, "held_days": 5}},
    )

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.reasoning.rule == "momentum.52w_max_hold"
    assert decision.intents[0].side == OrderSide.SELL


def test_52w_high_proximity_ranks_by_proximity_then_latest_return() -> None:
    strategy = Momentum52wHighProximityStrategy()
    aapl = _entry_bars(latest_close=98.0, prior_close=97.0, annual_high=100.0)
    msft = _entry_bars(latest_close=98.0, prior_close=96.0, annual_high=100.0)
    nvda = _entry_bars(latest_close=99.0, prior_close=98.5, annual_high=100.0)
    context = _context(
        bars_by_symbol={
            "AAPL": _barset(aapl),
            "MSFT": _barset(msft),
            "NVDA": _barset(nvda),
        },
        universe=("AAPL", "MSFT", "NVDA"),
        equity=10_000.0,
    )

    decision = strategy.evaluate(_barset(aapl), context)

    assert [intent.symbol for intent in decision.intents] == ["NVDA", "MSFT"]
    assert [row["symbol"] for row in decision.reasoning.ranking or []] == [
        "NVDA",
        "MSFT",
        "AAPL",
    ]


def test_52w_high_proximity_records_no_signal_reasoning() -> None:
    strategy = Momentum52wHighProximityStrategy()
    bars = _entry_bars(latest_close=96.0, prior_close=95.0, annual_high=100.0)
    context = _context(bars_by_symbol={"AAPL": _barset(bars)}, equity=10_000.0)

    decision = strategy.evaluate(_barset(bars), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert decision.reasoning.threshold["proximity_threshold"] == 0.97
    assert decision.reasoning.rejected_alternatives[0]["reason"] == "below proximity threshold"


def test_loader_resolves_52w_high_proximity_strategy_config() -> None:
    loaded = StrategyLoader().load(
        Path("configs/momentum_daily_52w_high_proximity_largecap_v1.yaml")
    )

    assert isinstance(loaded.strategy, Momentum52wHighProximityStrategy)
    assert loaded.context.strategy_id == "momentum.daily.52w_high_proximity.largecap.v1"
    assert loaded.context.universe_ref == "universe.sp100_liquid.v1"


def _context(
    *,
    positions: dict[str, float] | None = None,
    bars_by_symbol: dict[str, BarSet],
    universe: tuple[str, ...] = ("AAPL",),
    equity: float = 10_000.0,
    entry_state: dict[str, dict[str, object]] | None = None,
) -> StrategyContext:
    return StrategyContext(
        strategy_id="momentum.daily.52w_high_proximity.largecap.v1",
        family="momentum",
        template="daily.52w_high_proximity",
        variant="largecap",
        version=1,
        config_hash="hash",
        parameters={
            "lookback_days": 252,
            "proximity_threshold": 0.97,
            "sma_exit_length": 20,
            "max_hold_days": 5,
            "stop_loss_pct": 0.05,
            "max_concurrent_positions": 2,
            "sizing_rule": "equal_notional",
            "per_position_notional_pct": 0.20,
            "ranking_enabled": True,
            "ranking_metric": "proximity_then_return",
        },
        universe=universe,
        universe_ref="universe.sp100_liquid.v1",
        disable_conditions=(),
        config_path="configs/momentum_daily_52w_high_proximity_largecap_v1.yaml",
        manifest={},
        positions=positions or {},
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _barset(bars: list[tuple[float, float, float, float]]) -> BarSet:
    timestamps = pd.date_range("2024-01-01", periods=len(bars), freq="D", tz=UTC)
    dataframe = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [1_000_000] * len(bars),
            "vwap": [b[3] for b in bars],
        }
    )
    return BarSet(dataframe)


def _entry_bars(
    *,
    latest_close: float,
    prior_close: float,
    annual_high: float,
) -> list[tuple[float, float, float, float]]:
    bars = [(90.0, 95.0, 89.0, 92.0) for _ in range(250)]
    bars.append((prior_close - 1.0, annual_high, prior_close - 2.0, prior_close))
    bars.append((latest_close - 1.0, annual_high, latest_close - 2.0, latest_close))
    return bars


def _exit_bars(*, latest_close: float) -> list[tuple[float, float, float, float]]:
    bars = [(100.0, 103.0, 99.0, 101.0) for _ in range(19)]
    bars.append((latest_close + 1.0, latest_close + 2.0, latest_close - 1.0, latest_close))
    return bars
