"""Backtest bar-integrity data quality checks."""

from __future__ import annotations

from datetime import date

import pandas as pd

from milodex.data.bar_quality import DataQualitySeverity, scan_backtest_bars
from milodex.data.models import BarSet


def _barset(rows: list[dict]) -> BarSet:
    return BarSet(pd.DataFrame(rows))


def _row(
    day: str,
    *,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.0,
    volume: float = 1_000,
) -> dict:
    return {
        "timestamp": pd.Timestamp(day, tz="UTC"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "vwap": close,
    }


def _report_for(rows_by_symbol: dict[str, list[dict]]):
    return scan_backtest_bars(
        {symbol: _barset(rows) for symbol, rows in rows_by_symbol.items()},
        requested_start=date(2024, 1, 2),
        requested_end=date(2024, 1, 5),
    )


def test_clean_daily_bars_pass_without_issues():
    report = _report_for(
        {
            "SPY": [
                _row("2024-01-02"),
                _row("2024-01-03"),
                _row("2024-01-04"),
                _row("2024-01-05"),
            ],
            "QQQ": [
                _row("2024-01-02"),
                _row("2024-01-03"),
                _row("2024-01-04"),
                _row("2024-01-05"),
            ],
        }
    )

    assert report.status == "pass"
    assert report.blocker_count == 0
    assert report.warning_count == 0
    assert report.issues == ()
    assert report.to_dict()["scanned_symbols"] == ["QQQ", "SPY"]


def test_duplicate_timestamps_are_blockers():
    report = _report_for({"SPY": [_row("2024-01-02"), _row("2024-01-02")]})

    assert report.status == "fail"
    assert report.blocker_count == 1
    assert report.issues[0].code == "duplicate_timestamps"
    assert report.issues[0].severity is DataQualitySeverity.BLOCKER


def test_non_monotonic_timestamps_are_blockers():
    report = _report_for({"SPY": [_row("2024-01-03"), _row("2024-01-02")]})

    assert report.status == "fail"
    assert [issue.code for issue in report.issues] == ["non_monotonic_timestamps"]


def test_impossible_ohlc_rows_are_blockers():
    report = _report_for(
        {
            "LOW": [_row("2024-01-02", low=102.0, high=101.0)],
            "OPEN": [_row("2024-01-02", open_=102.0, high=101.0)],
            "CLOSE": [_row("2024-01-02", close=98.0, low=99.0)],
            "NAN": [_row("2024-01-02", close=float("nan"))],
            "ZERO": [_row("2024-01-02", open_=0.0)],
        }
    )

    codes = {issue.symbol: issue.code for issue in report.issues}
    assert report.status == "fail"
    assert codes == {
        "LOW": "invalid_ohlc_relationship",
        "OPEN": "invalid_ohlc_relationship",
        "CLOSE": "invalid_ohlc_relationship",
        "NAN": "invalid_price",
        "ZERO": "invalid_price",
    }


def test_negative_or_non_finite_volume_is_blocker():
    report = _report_for(
        {
            "NEG": [_row("2024-01-02", volume=-1)],
            "NAN": [_row("2024-01-02", volume=float("nan"))],
        }
    )

    assert report.status == "fail"
    assert [issue.code for issue in report.issues] == ["invalid_volume", "invalid_volume"]


def test_low_requested_window_coverage_is_warning():
    report = _report_for(
        {
            "SPY": [
                _row("2024-01-02"),
                _row("2024-01-03"),
                _row("2024-01-04"),
                _row("2024-01-05"),
            ],
            "QQQ": [_row("2024-01-02"), _row("2024-01-03"), _row("2024-01-04")],
        }
    )

    assert report.status == "pass_with_warnings"
    assert report.blocker_count == 0
    assert report.warning_count == 1
    assert report.issues[0].code == "requested_window_coverage_below_98pct"
    assert report.issues[0].context["coverage_pct"] == 75.0


def test_gap_longer_than_two_expected_sessions_is_warning():
    report = scan_backtest_bars(
        {
            "SPY": _barset(
                [
                    _row("2024-01-02"),
                    _row("2024-01-03"),
                    _row("2024-01-04"),
                    _row("2024-01-05"),
                    _row("2024-01-08"),
                ]
            ),
            "QQQ": _barset([_row("2024-01-02"), _row("2024-01-08")]),
        },
        requested_start=date(2024, 1, 2),
        requested_end=date(2024, 1, 8),
    )

    codes = [issue.code for issue in report.issues]
    assert report.status == "pass_with_warnings"
    assert "requested_window_gap_over_2_sessions" in codes


def test_provider_wide_date_truncation_is_warning_even_when_symbols_match():
    report = scan_backtest_bars(
        {
            "SPY": _barset([_row("2024-07-01"), _row("2024-07-02")]),
            "QQQ": _barset([_row("2024-07-01"), _row("2024-07-02")]),
        },
        requested_start=date(2024, 1, 2),
        requested_end=date(2024, 12, 31),
    )

    assert report.status == "pass_with_warnings"
    codes = [issue.code for issue in report.issues]
    assert codes.count("requested_window_starts_after_requested_start") == 2
    assert codes.count("requested_window_ends_before_requested_end") == 2
