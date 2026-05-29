"""Backtest-focused OHLCV bar integrity checks."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any

import pandas as pd

from milodex.data.models import BarSet

REQUESTED_WINDOW_EDGE_TOLERANCE = timedelta(days=7)


class DataQualitySeverity(Enum):
    BLOCKER = "blocker"
    WARNING = "warning"


@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    severity: DataQualitySeverity
    symbol: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "symbol": self.symbol,
            "message": self.message,
            "context": dict(self.context),
        }


@dataclass(frozen=True)
class DataQualityReport:
    requested_start: date
    requested_end: date
    scanned_symbols: tuple[str, ...]
    issues: tuple[DataQualityIssue, ...]

    @property
    def blocker_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity is DataQualitySeverity.BLOCKER)

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity is DataQualitySeverity.WARNING)

    @property
    def status(self) -> str:
        if self.blocker_count:
            return "fail"
        if self.warning_count:
            return "pass_with_warnings"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "scanned_symbols": list(self.scanned_symbols),
            "blocker_count": self.blocker_count,
            "warning_count": self.warning_count,
            "issue_codes": [issue.code for issue in self.issues],
            "issues": [issue.to_dict() for issue in self.issues],
        }


class DataQualityError(ValueError):
    """Raised when backtest bar quality blockers are present."""

    def __init__(self, report: DataQualityReport) -> None:
        self.report = report
        blockers = [
            issue for issue in report.issues if issue.severity is DataQualitySeverity.BLOCKER
        ]
        shown = ", ".join(f"{issue.symbol}:{issue.code}" for issue in blockers[:10])
        suffix = "..." if len(blockers) > 10 else ""
        super().__init__(f"Data quality failed with {len(blockers)} blocker(s): {shown}{suffix}")


def scan_backtest_bars(
    bars_by_symbol: dict[str, BarSet],
    *,
    requested_start: date,
    requested_end: date,
) -> DataQualityReport:
    """Scan historical daily bars before a backtest simulation mutates state."""
    issues: list[DataQualityIssue] = []
    requested_dates_by_symbol: dict[str, set[date]] = {}

    for symbol in sorted(bars_by_symbol):
        df = bars_by_symbol[symbol].to_dataframe()
        if df.empty:
            requested_dates_by_symbol[symbol] = set()
            continue
        timestamps = pd.to_datetime(df["timestamp"], utc=True)
        requested_mask = (timestamps.dt.date >= requested_start) & (
            timestamps.dt.date <= requested_end
        )
        requested_dates = set(timestamps.loc[requested_mask].dt.date)
        requested_dates_by_symbol[symbol] = requested_dates
        issues.extend(_structural_issues(symbol, df, timestamps))
        issues.extend(
            _requested_window_edge_warnings(
                symbol,
                requested_dates,
                requested_start=requested_start,
                requested_end=requested_end,
            )
        )

    expected_dates = (
        set().union(*requested_dates_by_symbol.values()) if requested_dates_by_symbol else set()
    )
    if expected_dates:
        for symbol in sorted(requested_dates_by_symbol):
            observed = requested_dates_by_symbol[symbol]
            coverage = len(observed) / len(expected_dates)
            if coverage < 0.98:
                issues.append(
                    DataQualityIssue(
                        code="requested_window_coverage_below_98pct",
                        severity=DataQualitySeverity.WARNING,
                        symbol=symbol,
                        message=(f"{symbol} has {coverage:.1%} requested-window bar coverage."),
                        context={
                            "coverage_pct": round(coverage * 100, 1),
                            "observed_sessions": len(observed),
                            "expected_sessions": len(expected_dates),
                        },
                    )
                )
            max_gap = _max_missing_session_gap(observed, expected_dates)
            if max_gap > 2:
                issues.append(
                    DataQualityIssue(
                        code="requested_window_gap_over_2_sessions",
                        severity=DataQualitySeverity.WARNING,
                        symbol=symbol,
                        message=f"{symbol} has a gap of {max_gap} expected sessions.",
                        context={"max_gap_sessions": max_gap},
                    )
                )

    return DataQualityReport(
        requested_start=requested_start,
        requested_end=requested_end,
        scanned_symbols=tuple(sorted(bars_by_symbol)),
        issues=tuple(issues),
    )


def _structural_issues(
    symbol: str, df: pd.DataFrame, timestamps: pd.Series
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    if timestamps.duplicated().any():
        issues.append(
            _blocker(symbol, "duplicate_timestamps", f"{symbol} has duplicate timestamps.")
        )
    elif not timestamps.is_monotonic_increasing:
        issues.append(
            _blocker(symbol, "non_monotonic_timestamps", f"{symbol} timestamps are not sorted.")
        )

    prices = df[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    invalid_price = prices.apply(
        lambda column: column.map(lambda value: not _is_positive_finite(value))
    ).any(axis=1)
    if invalid_price.any():
        issues.append(_blocker(symbol, "invalid_price", f"{symbol} has invalid price values."))
    valid_prices = prices.loc[~invalid_price]
    invalid_ohlc = (
        (valid_prices["low"] > valid_prices["high"])
        | (valid_prices["open"] > valid_prices["high"])
        | (valid_prices["open"] < valid_prices["low"])
        | (valid_prices["close"] > valid_prices["high"])
        | (valid_prices["close"] < valid_prices["low"])
    )
    if invalid_ohlc.any():
        issues.append(
            _blocker(symbol, "invalid_ohlc_relationship", f"{symbol} has impossible OHLC.")
        )

    volume = pd.to_numeric(df["volume"], errors="coerce")
    invalid_volume = volume.map(lambda value: not _is_non_negative_finite(value))
    if invalid_volume.any():
        issues.append(_blocker(symbol, "invalid_volume", f"{symbol} has invalid volume."))

    return issues


def _requested_window_edge_warnings(
    symbol: str,
    observed: set[date],
    *,
    requested_start: date,
    requested_end: date,
) -> list[DataQualityIssue]:
    if not observed:
        return []

    issues: list[DataQualityIssue] = []
    first_bar_date = min(observed)
    last_bar_date = max(observed)
    if first_bar_date > requested_start + REQUESTED_WINDOW_EDGE_TOLERANCE:
        issues.append(
            DataQualityIssue(
                code="requested_window_starts_after_requested_start",
                severity=DataQualitySeverity.WARNING,
                symbol=symbol,
                message=f"{symbol} starts materially after the requested backtest window.",
                context={
                    "requested_start": requested_start.isoformat(),
                    "first_bar_date": first_bar_date.isoformat(),
                    "tolerance_days": REQUESTED_WINDOW_EDGE_TOLERANCE.days,
                },
            )
        )
    if last_bar_date < requested_end - REQUESTED_WINDOW_EDGE_TOLERANCE:
        issues.append(
            DataQualityIssue(
                code="requested_window_ends_before_requested_end",
                severity=DataQualitySeverity.WARNING,
                symbol=symbol,
                message=f"{symbol} ends materially before the requested backtest window.",
                context={
                    "requested_end": requested_end.isoformat(),
                    "last_bar_date": last_bar_date.isoformat(),
                    "tolerance_days": REQUESTED_WINDOW_EDGE_TOLERANCE.days,
                },
            )
        )
    return issues


def _blocker(symbol: str, code: str, message: str) -> DataQualityIssue:
    return DataQualityIssue(
        code=code,
        severity=DataQualitySeverity.BLOCKER,
        symbol=symbol,
        message=message,
    )


def _is_positive_finite(value: object) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric > 0


def _is_non_negative_finite(value: object) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(numeric) and numeric >= 0


def _max_missing_session_gap(observed: set[date], expected: set[date]) -> int:
    max_gap = 0
    current = 0
    for day in sorted(expected):
        if day in observed:
            current = 0
            continue
        current += 1
        max_gap = max(max_gap, current)
    return max_gap
