"""Tests for the lower-Bollinger-band mean-reversion strategy."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pandas as pd

from milodex.broker.models import OrderSide
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.meanrev_bbands_lowerband import MeanrevBbandsLowerbandStrategy


def test_bbands_enters_when_close_below_lower_band_and_above_sma() -> None:
    strategy = MeanrevBbandsLowerbandStrategy()
    closes = _oversold_bband_closes()
    context = _context(bars_by_symbol={"AAPL": _barset(closes)})

    decision = strategy.evaluate(_barset(closes), context)

    assert [(i.side, i.symbol) for i in decision.intents] == [(OrderSide.BUY, "AAPL")]
    assert decision.reasoning.rule == "meanrev.bbands_entry"


def test_bbands_rejects_when_close_not_below_lower_band() -> None:
    strategy = MeanrevBbandsLowerbandStrategy()
    closes = [100.0] * 20
    context = _context(bars_by_symbol={"AAPL": _barset(closes)})

    decision = strategy.evaluate(_barset(closes), context)

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"


def test_bbands_exits_when_close_above_middle_band() -> None:
    strategy = MeanrevBbandsLowerbandStrategy()
    closes = [95.0] * 19 + [105.0]
    context = _context(
        positions={"AAPL": 10.0},
        bars_by_symbol={"AAPL": _barset(closes)},
        entry_state={"AAPL": {"entry_price": 100.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_barset(closes), context)

    assert [(i.side, i.symbol, i.quantity) for i in decision.intents] == [
        (OrderSide.SELL, "AAPL", 10.0)
    ]
    assert decision.reasoning.rule == "meanrev.bbands_exit"


def test_bbands_exits_on_stop_loss() -> None:
    strategy = MeanrevBbandsLowerbandStrategy()
    closes = [94.0] * 20
    context = _context(
        positions={"AAPL": 10.0},
        bars_by_symbol={"AAPL": _barset(closes)},
        entry_state={"AAPL": {"entry_price": 100.0, "held_days": 1}},
    )

    decision = strategy.evaluate(_barset(closes), context)

    assert decision.reasoning.rule == "meanrev.bbands_stop_loss"


def test_bbands_entry_payload_is_rich() -> None:
    """Entry triggering_values include selected_close and selected_zscore."""
    strategy = MeanrevBbandsLowerbandStrategy()
    closes = _oversold_bband_closes()
    context = _context(bars_by_symbol={"AAPL": _barset(closes)})

    decision = strategy.evaluate(_barset(closes), context)

    assert decision.reasoning.rule == "meanrev.bbands_entry"
    tv = decision.reasoning.triggering_values
    assert "selected_symbol" in tv
    assert "selected_close" in tv
    assert "selected_zscore" in tv
    assert tv["selected_symbol"] == "AAPL"
    assert isinstance(tv["selected_close"], float)
    assert isinstance(tv["selected_zscore"], float)
    # zscore for an oversold bar should be negative
    assert tv["selected_zscore"] < 0
    assert "bbands_stddev" in decision.reasoning.threshold
    assert "bbands_lookback" in decision.reasoning.threshold
    assert "ma_filter_length" in decision.reasoning.threshold


def test_bbands_at_capacity_returns_verbose_decision() -> None:
    """After migration, at-capacity returns the shared helper's verbose no_signal decision.

    The old bbands code had no explicit capacity-zero short-circuit — it sliced
    candidates[:0] and fell through to "no candidates qualified." The shared helper
    NOW returns the verbose at-capacity message when capacity <= 0. This test
    pins the new (more informative) behavior.
    """
    strategy = MeanrevBbandsLowerbandStrategy()
    closes = _oversold_bband_closes()
    # One position open, max_concurrent_positions=1 → capacity=0
    context = _context(
        positions={"AAPL": 10.0},
        bars_by_symbol={"AAPL": _barset(closes)},
        entry_state={"AAPL": {"entry_price": 100.0, "held_days": 1}},
        override_parameters={"max_concurrent_positions": 1},
    )

    decision = strategy.evaluate(_barset(closes), context)

    assert decision.reasoning.rule == "no_signal"
    assert "capacity" in decision.reasoning.narrative


def test_loader_resolves_bbands_strategy_config() -> None:
    loaded = StrategyLoader().load(Path("configs/meanrev_daily_bbands_lowerband_v1.yaml"))

    assert isinstance(loaded.strategy, MeanrevBbandsLowerbandStrategy)
    assert loaded.context.strategy_id == "meanrev.daily.bbands_lowerband.curated_largecap.v1"


def _context(
    *,
    positions: dict[str, float] | None = None,
    bars_by_symbol: dict[str, BarSet],
    entry_state: dict[str, dict[str, object]] | None = None,
    override_parameters: dict[str, object] | None = None,
) -> StrategyContext:
    parameters: dict[str, object] = {
        "bbands_lookback": 20,
        "bbands_stddev": 2.0,
        "ma_filter_length": 40,
        "stop_loss_pct": 0.06,
        "max_hold_days": 5,
        "max_concurrent_positions": 2,
        "sizing_rule": "equal_notional",
        "per_position_notional_pct": 0.20,
        "ranking_enabled": True,
        "ranking_metric": "bbands_zscore_ascending",
    }
    if override_parameters:
        parameters.update(override_parameters)
    return StrategyContext(
        strategy_id="meanrev.daily.bbands_lowerband.curated_largecap.v1",
        family="meanrev",
        template="daily.bbands_lowerband",
        variant="curated_largecap",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=("AAPL",),
        universe_ref="universe.curated_largecap.v2",
        disable_conditions=(),
        config_path="configs/meanrev_daily_bbands_lowerband_v1.yaml",
        manifest={},
        positions=positions or {},
        equity=10_000.0,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _barset(closes: list[float]) -> BarSet:
    timestamps = pd.date_range("2025-01-01", periods=len(closes), freq="D", tz=UTC)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * len(closes),
                "vwap": closes,
            }
        )
    )


def _oversold_bband_closes() -> list[float]:
    return [50.0] * 20 + [120.0] * 19 + [100.0]
