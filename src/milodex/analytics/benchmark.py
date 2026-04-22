"""SPY buy-and-hold benchmark comparison.

Fetches SPY bars for the same date range as a backtest and computes the
same set of performance metrics, allowing a fair apples-to-apples
comparison against the strategy under test.

The benchmark is always a simple buy-and-hold: buy SPY at the first
available close price, hold for the full window, sell at the last close.
"""

from __future__ import annotations

from datetime import date

from milodex.analytics.metrics import PerformanceMetrics, compute_metrics
from milodex.data.models import Timeframe

_SPY_SYMBOL = "SPY"


def compute_benchmark(
    *,
    start_date: date,
    end_date: date,
    initial_equity: float,
    data_provider,
) -> PerformanceMetrics:
    """Compute SPY buy-and-hold metrics for the given date range.

    Args:
        start_date: First day of the benchmark window (inclusive).
        end_date: Last day of the benchmark window (inclusive).
        initial_equity: Starting simulated equity in USD.
        data_provider: Any :class:`~milodex.data.provider.DataProvider` instance.

    Returns:
        :class:`~milodex.analytics.metrics.PerformanceMetrics` for SPY over the window.

    Raises:
        ValueError: If no SPY bars are available for the requested range.
    """
    bars_map = data_provider.get_bars(
        symbols=[_SPY_SYMBOL],
        timeframe=Timeframe.DAY_1,
        start=start_date,
        end=end_date,
    )
    barset = bars_map.get(_SPY_SYMBOL)
    if barset is None or len(barset) == 0:
        msg = f"No SPY bars available for {start_date} to {end_date}"
        raise ValueError(msg)

    df = barset.to_dataframe()
    if df.empty:
        msg = f"No SPY bars available for {start_date} to {end_date}"
        raise ValueError(msg)

    import pandas as pd

    timestamps = pd.to_datetime(df["timestamp"], utc=True)
    closes = df["close"].astype(float)
    first_close = float(closes.iloc[0])

    if first_close <= 0:
        msg = "SPY first close price is zero or negative — cannot compute benchmark."
        raise ValueError(msg)

    shares = initial_equity / first_close
    equity_curve: list[tuple[date, float]] = []
    for ts, close in zip(timestamps, closes, strict=False):
        equity_curve.append((ts.date(), shares * float(close)))

    buy_date = equity_curve[0][0].isoformat() if equity_curve else start_date.isoformat()
    sell_date = equity_curve[-1][0].isoformat() if equity_curve else end_date.isoformat()
    spy_trades: list[dict] = [
        {
            "symbol": _SPY_SYMBOL,
            "side": "buy",
            "quantity": shares,
            "estimated_unit_price": first_close,
            "recorded_at": buy_date,
        },
        {
            "symbol": _SPY_SYMBOL,
            "side": "sell",
            "quantity": shares,
            "estimated_unit_price": float(closes.iloc[-1]),
            "recorded_at": sell_date,
        },
    ]

    return compute_metrics(
        run_id="benchmark.spy",
        strategy_id="benchmark.spy.buy_and_hold",
        start_date=start_date,
        end_date=end_date,
        initial_equity=initial_equity,
        equity_curve=equity_curve,
        trades=spy_trades,
    )
