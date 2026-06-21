"""Intraday-aware data-readiness scanner.

``bar_quality.scan_backtest_bars`` is daily-shaped (it collapses timestamps to
calendar dates). Intraday evidence needs per-session completeness against an
EXPECTED bar grid (390 regular-session minutes full / 210 half-day, divided by
the bar timeframe). This scanner answers "is this intraday data good enough to
support an evidence verdict?" — distinct from the backtest pre-flight integrity
check, which it deliberately does not replace.

Checks (all WARNING severity — readiness informs, it does not block a backtest):
  - intraday_no_bars
  - intraday_session_coverage_below_threshold  (observed/expected per session)
  - intraday_missing_session_open_bar / _close_bar
  - intraday_zero_volume_bars                   (volume == 0 in a regular bar)
  - intraday_intra_session_gap                  (>1 consecutive missing bar)
  - intraday_stale_dataset_tail                 (data ends before requested_end)
  - iex_inward_price_bias                        (added in PR4, via reference cross-check)

Per symbol it also records a deterministic content hash (canonicalized OHLCV,
order-invariant — NOT the DataFrame repr) and the feed-quality label.

Import note: the three session-time helpers live under ``milodex.strategies`` —
importing that package eagerly loads the whole strategy fleet. To keep the data
layer's import graph light (and avoid a data->strategies->execution top-level
edge), they are imported lazily inside :func:`scan_intraday_readiness`. The clean
long-term fix is lifting the pure-time helpers to a neutral shared module.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

from milodex.data.bar_quality import DataQualityIssue, DataQualitySeverity
from milodex.data.models import BarSet

#: A dataset whose latest session is more than this far before requested_end is
#: flagged stale. One week mirrors bar_quality.REQUESTED_WINDOW_EDGE_TOLERANCE.
STALE_TAIL_TOLERANCE = timedelta(days=7)

#: Per-session observed/expected coverage below this is a warning.
SESSION_COVERAGE_FLOOR = 0.90


@dataclass(frozen=True)
class SymbolReadiness:
    symbol: str
    content_hash: str
    sessions_observed: int
    expected_bars: int
    observed_bars: int

    @property
    def coverage_pct(self) -> float:
        return (self.observed_bars / self.expected_bars * 100.0) if self.expected_bars else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "content_hash": self.content_hash,
            "sessions_observed": self.sessions_observed,
            "expected_bars": self.expected_bars,
            "observed_bars": self.observed_bars,
            "coverage_pct": round(self.coverage_pct, 1),
        }


@dataclass(frozen=True)
class ReadinessReport:
    requested_start: date
    requested_end: date
    timeframe_minutes: int
    feed_label: str
    scanned_symbols: tuple[str, ...]
    per_symbol: tuple[SymbolReadiness, ...]
    issues: tuple[DataQualityIssue, ...] = field(default_factory=tuple)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity is DataQualitySeverity.WARNING)

    @property
    def status(self) -> str:
        # The scanner only emits WARNING issues today, so status is pass or
        # pass_with_warnings. (Parity with DataQualityReport's tri-state.)
        return "pass_with_warnings" if self.warning_count else "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "timeframe_minutes": self.timeframe_minutes,
            "feed_label": self.feed_label,
            "scanned_symbols": list(self.scanned_symbols),
            "warning_count": self.warning_count,
            "issue_codes": [i.code for i in self.issues],
            "issues": [i.to_dict() for i in self.issues],
            "per_symbol": [s.to_dict() for s in self.per_symbol],
        }


def _warn(symbol: str, code: str, message: str, context: dict[str, Any]) -> DataQualityIssue:
    return DataQualityIssue(
        code=code,
        severity=DataQualitySeverity.WARNING,
        symbol=symbol,
        message=message,
        context=context,
    )


def _content_hash(df: pd.DataFrame) -> str:
    """Deterministic sha256 of canonicalized OHLCV — row-order invariant.

    Relies on the BarSet float64/int64 column contract (``models.py``); values
    are coerced to float64 and rounded to 6 dp to absorb trivial float noise, then
    sorted by timestamp so row order never affects the hash. This is NOT claimed
    to be invariant across an *upstream* dtype change that already lost precision
    — the BarSet contract guarantees the columns this assumes.
    """
    cols = ["timestamp", "open", "high", "low", "close", "volume"]
    sub = pd.DataFrame({c: df[c] for c in cols})
    sub["timestamp"] = pd.to_datetime(sub["timestamp"], utc=True).astype("int64")
    for c in ("open", "high", "low", "close", "volume"):
        sub[c] = pd.to_numeric(sub[c], errors="coerce").astype("float64").round(6)
    sub = sub.sort_values("timestamp").reset_index(drop=True)
    payload = sub.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def scan_intraday_readiness(
    bars_by_symbol: dict[str, BarSet],
    *,
    timeframe_minutes: int,
    requested_start: date,
    requested_end: date,
    feed_label: str = "fallback",
    reference_daily_by_symbol: dict[str, pd.DataFrame] | None = None,
) -> ReadinessReport:
    if timeframe_minutes <= 0:
        raise ValueError("timeframe_minutes must be positive")

    # Lazy import: keeps milodex.strategies (and its eager strategy fleet) out of
    # the data layer's top-level import graph. See module docstring.
    from milodex.strategies._session_intraday import (
        regular_session_bars,
        session_close_offset_minutes,
        session_date_et,
        to_eastern,
    )

    def _offset_min(ts: Any) -> int:
        et = to_eastern(ts)
        return (et.hour - 9) * 60 + (et.minute - 30)

    issues: list[DataQualityIssue] = []
    per_symbol: list[SymbolReadiness] = []

    for symbol in sorted(bars_by_symbol):
        df = bars_by_symbol[symbol].to_dataframe()
        if df.empty:
            issues.append(_warn(symbol, "intraday_no_bars", f"{symbol} has no bars.", {}))
            per_symbol.append(SymbolReadiness(symbol, "", 0, 0, 0))
            continue

        ts = pd.to_datetime(df["timestamp"], utc=True)
        session_days = sorted({session_date_et(t) for t in ts})
        total_expected = 0
        total_observed = 0
        for day in session_days:
            close_off = session_close_offset_minutes(day)
            # Count of regular-session bar slots = grid offsets in [0, close_off)
            # stepping by timeframe = ceil(close_off / tf). For timeframes that don't
            # divide the session (e.g. 1h into 390min: offsets 0,60..360 = 7 bars,
            # not 390//60=6) floor-division undercounts by one.
            expected = -(-close_off // timeframe_minutes)
            last_grid_offset = (expected - 1) * timeframe_minutes
            session = regular_session_bars(df, day)
            # Derive raw_offsets list once; reuse for on-grid count, gap scan, and
            # dup/off-grid tally. ponytail: distinct-on-grid-offset count, not a
            # min(100, …) cap — a cap silently re-opens the evidence blind spot.
            raw_offsets = (
                [_offset_min(t) for t in pd.to_datetime(session["timestamp"], utc=True)]
                if len(session)
                else []
            )
            # offsets set (all unique observed offsets) — used by gap scan and open/close checks.
            offsets = set(raw_offsets)
            # on_grid: distinct on-grid offsets within [0, last_grid_offset]; dups collapse,
            # off-grid bars excluded — so observed <= expected by construction.
            on_grid = {
                off
                for off in offsets
                if off % timeframe_minutes == 0 and 0 <= off <= last_grid_offset
            }
            observed = len(on_grid)
            total_expected += expected
            total_observed += observed
            # Surface off-grid and duplicate bars rather than silently dropping them.
            n_offgrid = sum(1 for off in raw_offsets if off % timeframe_minutes != 0)
            n_duplicate = len(raw_offsets) - len(offsets)
            if n_offgrid or n_duplicate:
                issues.append(
                    _warn(
                        symbol,
                        "intraday_offgrid_or_duplicate_bars",
                        f"{symbol} {day}: {n_offgrid} off-grid, {n_duplicate} duplicate bar(s).",
                        {
                            "session": day.isoformat(),
                            "off_grid": n_offgrid,
                            "duplicate": n_duplicate,
                        },
                    )
                )
            if 0 not in offsets:
                issues.append(
                    _warn(
                        symbol,
                        "intraday_missing_session_open_bar",
                        f"{symbol} {day}: missing the 9:30 ET session-open bar.",
                        {"session": day.isoformat()},
                    )
                )
            if last_grid_offset not in offsets:
                issues.append(
                    _warn(
                        symbol,
                        "intraday_missing_session_close_bar",
                        f"{symbol} {day}: missing the final regular-session bar.",
                        {"session": day.isoformat()},
                    )
                )
            if expected and observed / expected < SESSION_COVERAGE_FLOOR:
                issues.append(
                    _warn(
                        symbol,
                        "intraday_session_coverage_below_threshold",
                        f"{symbol} {day}: {observed}/{expected} bars ({observed / expected:.0%}).",
                        {"session": day.isoformat(), "observed": observed, "expected": expected},
                    )
                )
            max_gap = _max_intra_session_gap(on_grid, timeframe_minutes)
            if max_gap > 1:
                issues.append(
                    _warn(
                        symbol,
                        "intraday_intra_session_gap",
                        f"{symbol} {day}: {max_gap} consecutive missing bars.",
                        {"session": day.isoformat(), "max_gap_bars": max_gap},
                    )
                )
            zero_vol = (
                int((pd.to_numeric(session["volume"], errors="coerce") == 0).sum())
                if observed
                else 0
            )
            if zero_vol:
                issues.append(
                    _warn(
                        symbol,
                        "intraday_zero_volume_bars",
                        f"{symbol} {day}: {zero_vol} zero-volume regular-session bar(s).",
                        {"session": day.isoformat(), "zero_volume_bars": zero_vol},
                    )
                )

        last_session = max(session_days)
        if last_session < requested_end - STALE_TAIL_TOLERANCE:
            issues.append(
                _warn(
                    symbol,
                    "intraday_stale_dataset_tail",
                    f"{symbol}: latest session {last_session} is materially before "
                    f"requested_end {requested_end}.",
                    {
                        "last_session": last_session.isoformat(),
                        "requested_end": requested_end.isoformat(),
                    },
                )
            )

        per_symbol.append(
            SymbolReadiness(
                symbol=symbol,
                content_hash=_content_hash(df),
                sessions_observed=len(session_days),
                expected_bars=total_expected,
                observed_bars=total_observed,
            )
        )

    if reference_daily_by_symbol:
        # IEX price-fidelity gate (advisory). No feed_label demotion: an inward-bias
        # warning is the v1 signal — the label is operator-supplied, not derived here.
        from milodex.data.consolidated_reference import cross_check_session_extremes

        issues.extend(cross_check_session_extremes(bars_by_symbol, reference_daily_by_symbol))

    return ReadinessReport(
        requested_start=requested_start,
        requested_end=requested_end,
        timeframe_minutes=timeframe_minutes,
        feed_label=feed_label,
        scanned_symbols=tuple(sorted(bars_by_symbol)),
        per_symbol=tuple(per_symbol),
        issues=tuple(issues),
    )


def _max_intra_session_gap(offsets: set[int], step: int) -> int:
    """Max run of consecutive missing expected bars between first and last observed."""
    if not offsets:
        return 0
    max_gap = current = 0
    for off in range(min(offsets), max(offsets) + step, step):
        if off in offsets:
            current = 0
        else:
            current += 1
            max_gap = max(max_gap, current)
    return max_gap
