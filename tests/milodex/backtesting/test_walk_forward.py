"""Unit tests for WalkForwardSplitter."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from milodex.backtesting.walk_forward import WalkForwardSplitter


def _days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(count)]


class TestWalkForwardSplitter:
    def test_basic_two_windows(self):
        trading_days = _days(date(2023, 1, 2), 10)
        splitter = WalkForwardSplitter()
        windows = list(splitter.split(trading_days, train_days=4, test_days=3, step_days=3))
        assert len(windows) == 2
        train_s, train_e, test_s, test_e = windows[0]
        assert train_s == trading_days[0]
        assert train_e == trading_days[3]
        assert test_s == trading_days[4]
        assert test_e == trading_days[6]

    def test_exact_fit_single_window(self):
        trading_days = _days(date(2023, 1, 2), 5)
        splitter = WalkForwardSplitter()
        windows = list(splitter.split(trading_days, train_days=3, test_days=2, step_days=2))
        assert len(windows) == 1
        ts, te, vs, ve = windows[0]
        assert ts == trading_days[0]
        assert te == trading_days[2]
        assert vs == trading_days[3]
        assert ve == trading_days[4]

    def test_insufficient_days_yields_nothing(self):
        trading_days = _days(date(2023, 1, 2), 4)
        splitter = WalkForwardSplitter()
        windows = list(splitter.split(trading_days, train_days=3, test_days=2, step_days=1))
        assert windows == []

    def test_empty_list_yields_nothing(self):
        splitter = WalkForwardSplitter()
        windows = list(splitter.split([], train_days=3, test_days=2, step_days=1))
        assert windows == []

    def test_invalid_train_days_raises(self):
        splitter = WalkForwardSplitter()
        days = _days(date(2023, 1, 2), 10)
        with pytest.raises(ValueError, match="train_days"):
            list(splitter.split(days, train_days=0, test_days=2, step_days=1))

    def test_invalid_test_days_raises(self):
        splitter = WalkForwardSplitter()
        days = _days(date(2023, 1, 2), 10)
        with pytest.raises(ValueError, match="test_days"):
            list(splitter.split(days, train_days=3, test_days=0, step_days=1))

    def test_invalid_step_days_raises(self):
        splitter = WalkForwardSplitter()
        days = _days(date(2023, 1, 2), 10)
        with pytest.raises(ValueError, match="step_days"):
            list(splitter.split(days, train_days=3, test_days=2, step_days=0))

    def test_windows_are_non_overlapping_test_periods(self):
        trading_days = _days(date(2023, 1, 2), 20)
        splitter = WalkForwardSplitter()
        windows = list(splitter.split(trading_days, train_days=5, test_days=3, step_days=3))
        test_ranges = [(vs, ve) for _, _, vs, ve in windows]
        for i in range(len(test_ranges) - 1):
            assert test_ranges[i][1] < test_ranges[i + 1][0]
