"""Walk-forward window splitter for out-of-sample backtest validation.

Implements the rolling train/test split required by R-BKT-002.  All
splits are index-based (counted in *trading days*, not calendar days),
so the windows are directly tied to actual market sessions.

Usage example::

    splitter = WalkForwardSplitter()
    for train_start, train_end, test_start, test_end in splitter.split(
        trading_days,
        train_days=252,   # ~1 year in-sample
        test_days=63,     # ~1 quarter out-of-sample
        step_days=63,     # advance one quarter per window
    ):
        ...

"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date


class WalkForwardSplitter:
    """Generates rolling train/test windows over a list of trading days.

    All parameters are in *trading-day* counts (not calendar days).
    """

    def split(
        self,
        trading_days: list[date],
        *,
        train_days: int,
        test_days: int,
        step_days: int,
    ) -> Iterator[tuple[date, date, date, date]]:
        """Yield ``(train_start, train_end, test_start, test_end)`` tuples.

        Args:
            trading_days: Sorted list of trading dates available for splitting.
            train_days: Number of trading days in the in-sample window.
            test_days: Number of trading days in the out-of-sample holdout.
            step_days: Number of trading days to advance between windows.

        Raises:
            ValueError: If any parameter is < 1 or the total required window
                exceeds the number of available trading days.
        """
        if train_days < 1:
            msg = "train_days must be >= 1"
            raise ValueError(msg)
        if test_days < 1:
            msg = "test_days must be >= 1"
            raise ValueError(msg)
        if step_days < 1:
            msg = "step_days must be >= 1"
            raise ValueError(msg)

        n = len(trading_days)
        window = train_days + test_days

        if n < window:
            return

        i = 0
        while i + window <= n:
            yield (
                trading_days[i],
                trading_days[i + train_days - 1],
                trading_days[i + train_days],
                trading_days[i + window - 1],
            )
            i += step_days
