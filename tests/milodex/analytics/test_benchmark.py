"""Unit tests for the SPY buy-and-hold benchmark builder.

The real data provider is stubbed so these tests are fully offline and
deterministic. The fake ``BarSet`` implements the minimal surface
``compute_benchmark`` depends on (``__len__`` and ``to_dataframe``).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from milodex.analytics.benchmark import compute_benchmark


class _FakeBarSet:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def __len__(self) -> int:
        return len(self._df)

    def to_dataframe(self) -> pd.DataFrame:
        return self._df


class _StubProvider:
    def __init__(self, df: pd.DataFrame | None) -> None:
        self._df = df

    def get_bars(self, *, symbols, timeframe, start, end):
        if self._df is None:
            return {"SPY": _FakeBarSet(pd.DataFrame(columns=["timestamp", "close"]))}
        return {"SPY": _FakeBarSet(self._df)}


def _frame(closes: list[float], dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": pd.to_datetime(dates, utc=True), "close": closes})


def test_compute_benchmark_flat_price_produces_zero_return() -> None:
    df = _frame([100.0, 100.0, 100.0], ["2024-01-02", "2024-01-03", "2024-01-04"])
    metrics = compute_benchmark(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        initial_equity=100_000.0,
        data_provider=_StubProvider(df),
    )

    assert metrics.strategy_id == "benchmark.spy.buy_and_hold"
    assert metrics.run_id == "benchmark.spy"
    assert metrics.total_return_pct == pytest.approx(0.0, abs=1e-9)
    assert metrics.max_drawdown_pct == pytest.approx(0.0, abs=1e-9)


def test_compute_benchmark_rising_price_positive_return() -> None:
    df = _frame([100.0, 110.0, 120.0], ["2024-01-02", "2024-01-03", "2024-01-04"])
    metrics = compute_benchmark(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        initial_equity=100_000.0,
        data_provider=_StubProvider(df),
    )

    # Buy at 100, sell at 120 → +20%
    assert metrics.total_return_pct == pytest.approx(20.0, rel=1e-6)
    assert metrics.max_drawdown_pct == pytest.approx(0.0, abs=1e-9)


def test_compute_benchmark_captures_drawdown() -> None:
    df = _frame(
        [100.0, 120.0, 90.0, 110.0],
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
    )
    metrics = compute_benchmark(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 5),
        initial_equity=100_000.0,
        data_provider=_StubProvider(df),
    )

    # Peak 120 → trough 90 = 25% drawdown.
    assert metrics.max_drawdown_pct == pytest.approx(25.0, rel=1e-3)


def test_compute_benchmark_raises_when_provider_returns_nothing() -> None:
    with pytest.raises(ValueError, match="No SPY bars"):
        compute_benchmark(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_equity=100_000.0,
            data_provider=_StubProvider(None),
        )


def test_compute_benchmark_rejects_zero_first_close() -> None:
    df = _frame([0.0, 10.0], ["2024-01-02", "2024-01-03"])
    with pytest.raises(ValueError, match="zero or negative"):
        compute_benchmark(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
            initial_equity=100_000.0,
            data_provider=_StubProvider(df),
        )
