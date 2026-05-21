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


_TIMEFRAME_TO_MINUTES: dict[Timeframe, int] = {
    Timeframe.MINUTE_1: 1,
    Timeframe.MINUTE_5: 5,
    Timeframe.MINUTE_15: 15,
    Timeframe.HOUR_1: 60,
}


def timeframe_from_bar_size(value: str) -> Timeframe:
    """Return the Timeframe enum matching ``value``.

    Raises:
        KeyError: when ``value`` is not a recognized bar-size string.
    """
    return _BAR_SIZE_TO_TIMEFRAME[value]


def bar_size_minutes_from_timeframe(tf: Timeframe) -> int:
    """Return the bar size in minutes for an intraday ``Timeframe``.

    Used by the intraday backtest engine to drive the event timeline and
    cursor-advance logic.

    Raises:
        ValueError: when ``tf`` is ``DAY_1`` or otherwise has no minute count.
    """
    minutes = _TIMEFRAME_TO_MINUTES.get(tf)
    if minutes is None:
        msg = f"Timeframe {tf!r} has no minute count (not an intraday bar size)"
        raise ValueError(msg)
    return minutes
