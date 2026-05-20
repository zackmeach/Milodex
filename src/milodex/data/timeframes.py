"""Shared bar-size → Timeframe mapping.

Used by both the live runner (strategies/runner.py) and the backtest engine
(backtesting/engine.py). Lives here so neither module imports the other.
"""

from __future__ import annotations

from milodex.data.models import Timeframe

_BAR_SIZE_TO_TIMEFRAME: dict[str, Timeframe] = {
    "1D": Timeframe.DAY_1,
    "1H": Timeframe.HOUR_1,
    "15Min": Timeframe.MINUTE_15,
    "5Min": Timeframe.MINUTE_5,
    "1Min": Timeframe.MINUTE_1,
}


def timeframe_from_bar_size(value: str) -> Timeframe:
    """Return the Timeframe enum matching ``value``.

    Raises:
        KeyError: when ``value`` is not a recognized bar-size string.
    """
    return _BAR_SIZE_TO_TIMEFRAME[value]
