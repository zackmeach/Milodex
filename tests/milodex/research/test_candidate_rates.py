"""Tests for the candidate round-trip rate-measurement helper (E-PR2 Task 2)."""

from __future__ import annotations

import logging
from datetime import date

from milodex.research.candidate_rates import measure_candidate_rates


class _StubResult:
    """Minimal stand-in for WalkForwardResult exposing only the read fields.

    Pins the exact field names the helper consumes — ``oos_round_trip_count``
    (round-trips ≈ entries) and ``oos_trading_days``, NOT ``oos_trade_count``
    (buy+sell fills ≈ 2× round-trips).
    """

    def __init__(self, *, round_trips: int, trading_days: int) -> None:
        self.oos_round_trip_count = round_trips
        self.oos_trading_days = trading_days
        # Present so a buggy implementation that grabs trade_count is caught by
        # the value assertions below (it is double the round-trip count here).
        self.oos_trade_count = round_trips * 2


def test_rate_is_round_trips_over_trading_days() -> None:
    """rate[symbol] = oos_round_trip_count / oos_trading_days (NOT trade_count)."""
    fixtures = {
        "benchmark.x.spy.v1": ("SPY", _StubResult(round_trips=60, trading_days=240)),
        "benchmark.x.qqq.v1": ("QQQ", _StubResult(round_trips=30, trading_days=240)),
    }

    def run_one(strategy_id, *, start, end):
        symbol, result = fixtures[strategy_id]
        return symbol, result

    rates = measure_candidate_rates(
        candidate_strategy_ids=list(fixtures),
        start=date(2023, 1, 1),
        end=date(2026, 6, 18),
        run_one=run_one,
    )
    assert rates == {"SPY": 0.25, "QQQ": 0.125}
    # The trade_count trap would have produced 0.5 / 0.25 — assert we did NOT.
    assert rates["SPY"] != 0.5


def test_rate_clamped_to_unit_interval() -> None:
    """A pathological round_trips > trading_days clamps to 1.0."""

    def run_one(strategy_id, *, start, end):
        return "SPY", _StubResult(round_trips=300, trading_days=240)

    rates = measure_candidate_rates(
        candidate_strategy_ids=["benchmark.x.spy.v1"],
        start=date(2023, 1, 1),
        end=date(2026, 6, 18),
        run_one=run_one,
    )
    assert rates == {"SPY": 1.0}


def test_zero_trade_symbol_warns(caplog) -> None:
    """A measured rate of exactly 0.0 is degenerate — emit a WARNING."""

    def run_one(strategy_id, *, start, end):
        return "GLD", _StubResult(round_trips=0, trading_days=240)

    with caplog.at_level(logging.WARNING):
        rates = measure_candidate_rates(
            candidate_strategy_ids=["benchmark.x.gld.v1"],
            start=date(2023, 1, 1),
            end=date(2026, 6, 18),
            run_one=run_one,
        )
    assert rates == {"GLD": 0.0}
    assert any("GLD" in rec.message and "0" in rec.message for rec in caplog.records)
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


def test_zero_trading_days_does_not_divide_by_zero() -> None:
    """Defensive: a window with zero trading days yields rate 0.0, not a crash."""

    def run_one(strategy_id, *, start, end):
        return "TLT", _StubResult(round_trips=0, trading_days=0)

    rates = measure_candidate_rates(
        candidate_strategy_ids=["benchmark.x.tlt.v1"],
        start=date(2023, 1, 1),
        end=date(2026, 6, 18),
        run_one=run_one,
    )
    assert rates == {"TLT": 0.0}
