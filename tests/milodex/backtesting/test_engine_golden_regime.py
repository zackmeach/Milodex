"""End-to-end golden test for the regime strategy + backtest engine.

Runs the full :class:`BacktestEngine` against the real
:class:`RegimeSpyShy200DmaStrategy` over a deterministic synthetic
SPY/SHY bar history and asserts the exact ``(side, symbol, quantity,
fill_price)`` sequence of trades written to the event store.

Design notes
------------
- Uses a minimal regime YAML with ``ma_filter_length=3`` and
  ``allocation_pct=1.0`` so share counts fall out to whole numbers
  readable by a human from the closes alone.
- Slippage and commission are zeroed so ``filled_avg_price ==
  day_close``. The engine's slippage / commission arithmetic is
  exercised by unit tests in ``test_engine.py``; this test checks
  rotation logic end-to-end, not fill-price math.
- SPY and SHY share the same close each day — simpler for the reader to
  verify manually, and doesn't change what the strategy does (it only
  looks at the primary symbol's MA).
"""

from __future__ import annotations

from datetime import UTC, date
from pathlib import Path

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestEngine
from milodex.core.event_store import EventStore
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader

_REGIME_YAML = """\
strategy:
  id: "regime.daily.sma200_rotation.spy_shy_test.v1"
  family: "regime"
  template: "daily.sma200_rotation"
  variant: "spy_shy_test"
  version: 1
  description: "Test regime for engine golden."
  enabled: true
  universe:
    - "SPY"
    - "SHY"
  parameters:
    ma_filter_length: 3
    risk_on_symbol: "SPY"
    risk_off_symbol: "SHY"
    allocation_pct: 1.0
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: null
  risk:
    max_position_pct: 1.0
    max_positions: 1
    daily_loss_cap_pct: 1.0
    stop_loss_pct: null
  stage: "backtest"
  backtest:
    slippage_pct: 0.0
    commission_per_trade: 0.0
    min_trades_required: null
    walk_forward_windows: 1
  disable_conditions_additional: []
"""


class _StubProvider:
    """Returns a canned {symbol: BarSet} regardless of the requested window.

    The engine filters bars to the requested date range itself via
    ``_trading_days_in_range``, so returning the full series here is
    equivalent to what a real provider would do.
    """

    def __init__(self, bars: dict[str, BarSet]) -> None:
        self._bars = bars

    def get_bars(self, symbols, timeframe, start, end):  # noqa: ARG002
        return {sym: self._bars[sym] for sym in symbols if sym in self._bars}

    def get_latest_bar(self, symbol):  # pragma: no cover - unused here
        raise NotImplementedError


def _barset(closes: list[float], start: str = "2025-01-01") -> BarSet:
    timestamps = pd.date_range(start, periods=len(closes), freq="D", tz=UTC)
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


def test_regime_engine_golden_trade_sequence(tmp_path: Path):
    # Scenario: 7 daily bars with identical SPY and SHY closes so the
    # reader can verify the rotation logic purely from the SPY series:
    #
    #   closes = [10, 10, 10, 12, 13, 8, 7]  (idx 0..6)
    #
    # ma_filter_length=3, allocation_pct=1.0, starting equity $10,000:
    #   idx 2 (2025-01-03) close=10, MA(10,10,10)=10   → target SHY, no pos
    #                                                     → BUY 1000 SHY @ 10
    #   idx 3 (2025-01-04) close=12, MA(10,10,12)=10.67 → target SPY, pos=SHY
    #                                                     → SELL 1000 SHY @ 12
    #                                                       (cash then $12k)
    #                                                     → BUY 1000 SPY @ 12
    #   idx 4 (2025-01-05) close=13, MA(10,12,13)=11.67 → target SPY, already
    #                                                     holding; no trade
    #   idx 5 (2025-01-06) close=8,  MA(12,13,8)=11     → target SHY, pos=SPY
    #                                                     → SELL 1000 SPY @ 8
    #                                                       (cash then $8k)
    #                                                     → BUY 1000 SHY @ 8
    #   idx 6 (2025-01-07) close=7,  MA(13,8,7)=9.33    → target SHY, already
    #                                                     holding; no trade
    closes = [10.0, 10.0, 10.0, 12.0, 13.0, 8.0, 7.0]
    bars = {"SPY": _barset(closes), "SHY": _barset(closes)}

    config_path = tmp_path / "regime_test.yaml"
    config_path.write_text(_REGIME_YAML, encoding="utf-8")
    loader = StrategyLoader()
    loaded = loader.load(config_path)

    store = EventStore(tmp_path / "milodex.db")

    engine = BacktestEngine(
        loaded=loaded,
        data_provider=_StubProvider(bars),
        event_store=store,
        initial_equity=10_000.0,
        slippage_pct=0.0,
        commission_per_trade=0.0,
    )

    result = engine.run(
        start_date=date(2025, 1, 3),
        end_date=date(2025, 1, 7),
    )

    trades = store.list_trades_for_backtest_run(result.db_id)
    actual = [(t.side, t.symbol, t.quantity, t.estimated_unit_price) for t in trades]
    expected = [
        ("buy", "SHY", 1000.0, 10.0),
        ("sell", "SHY", 1000.0, 12.0),
        ("buy", "SPY", 1000.0, 12.0),
        ("sell", "SPY", 1000.0, 8.0),
        ("buy", "SHY", 1000.0, 8.0),
    ]
    assert actual == expected

    # Sanity: engine summary agrees with the trade ledger.
    assert result.trade_count == 5
    assert result.buy_count == 3
    assert result.sell_count == 2
    assert result.trading_days == 5

    # Final equity at close of the last day: cash=0, position=1000 SHY
    # at day-7 close of $7.00 → $7,000.
    assert result.final_equity == pytest.approx(7_000.0)

    # Every recorded trade should be tagged as source='backtest' and
    # linked to this run's backtest_runs row — the unified-path
    # invariant that motivated commit 4.
    assert all(t.source == "backtest" for t in trades)
    assert all(t.backtest_run_id == result.db_id for t in trades)
