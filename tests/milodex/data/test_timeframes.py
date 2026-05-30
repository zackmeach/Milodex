"""Tests for timeframe_from_bar_size shared helper."""

from __future__ import annotations

import pytest

from milodex.data.models import Timeframe
from milodex.data.timeframes import (
    bar_size_minutes_from_timeframe,
    timeframe_from_bar_size,
)


def test_maps_each_valid_bar_size() -> None:
    assert timeframe_from_bar_size("1D") == Timeframe.DAY_1
    assert timeframe_from_bar_size("1H") == Timeframe.HOUR_1
    assert timeframe_from_bar_size("30Min") == Timeframe.MINUTE_30
    assert timeframe_from_bar_size("15Min") == Timeframe.MINUTE_15
    assert timeframe_from_bar_size("5Min") == Timeframe.MINUTE_5
    assert timeframe_from_bar_size("1Min") == Timeframe.MINUTE_1


def test_minute_30_has_minute_count() -> None:
    assert bar_size_minutes_from_timeframe(Timeframe.MINUTE_30) == 30


def test_raises_keyerror_on_unknown() -> None:
    with pytest.raises(KeyError):
        timeframe_from_bar_size("2H")
