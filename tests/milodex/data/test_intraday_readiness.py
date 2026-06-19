"""Intraday-aware data-readiness scanner."""

from __future__ import annotations

from datetime import date

import pandas as pd

from milodex.data.intraday_readiness import scan_intraday_readiness
from milodex.data.models import BarSet


def _session_5min(
    day: str,
    *,
    n_bars: int = 78,
    volume: float = 1_000.0,
    open_offset_skip: int = 0,
    zero_vol_bars: int = 0,
    drop_offsets: tuple[int, ...] = (),
) -> list[dict]:
    """Build 5-min bars starting 9:30 ET on ``day`` (full session = 78 bars).

    ``open_offset_skip`` starts at bar index N (drops the open). ``drop_offsets``
    removes specific bar indices (interior-gap fixtures). ``zero_vol_bars`` zeroes
    the first N volumes.
    """
    start = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    rows = []
    for i in range(open_offset_skip, n_bars):
        if i in drop_offsets:
            continue
        ts = (start + pd.Timedelta(minutes=5 * i)).tz_convert("UTC")
        vol = 0.0 if i < zero_vol_bars else volume
        rows.append(
            {
                "timestamp": ts,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": vol,
                "vwap": 100.2,
            }
        )
    return rows


def _barset(rows: list[dict]) -> BarSet:
    return BarSet(pd.DataFrame(rows))


def _scan(rows_by_symbol, *, start, end, timeframe_minutes=5, feed_label="fallback"):
    return scan_intraday_readiness(
        {s: _barset(r) for s, r in rows_by_symbol.items()},
        timeframe_minutes=timeframe_minutes,
        requested_start=start,
        requested_end=end,
        feed_label=feed_label,
    )


def test_clean_full_session_passes():
    report = _scan(
        {"SPY": _session_5min("2025-06-17")},
        start=date(2025, 6, 17),
        end=date(2025, 6, 17),
    )
    assert report.status == "pass"
    sr = report.per_symbol[0]
    assert sr.expected_bars == 78 and sr.observed_bars == 78
    assert sr.content_hash  # non-empty


def test_content_hash_is_order_invariant():
    rows = _session_5min("2025-06-17")
    d = date(2025, 6, 17)
    h_plain = _scan({"SPY": rows}, start=d, end=d).per_symbol[0].content_hash
    h_shuffled = _scan({"SPY": list(reversed(rows))}, start=d, end=d).per_symbol[0].content_hash
    assert h_plain == h_shuffled


def test_content_hash_differs_on_different_data():
    a = _scan({"SPY": _session_5min("2025-06-17")}, start=date(2025, 6, 17), end=date(2025, 6, 17))
    b = _scan(
        {"SPY": _session_5min("2025-06-17", volume=2_000.0)},
        start=date(2025, 6, 17),
        end=date(2025, 6, 17),
    )
    assert a.per_symbol[0].content_hash != b.per_symbol[0].content_hash


def test_zero_volume_bars_warn():
    report = _scan(
        {"SPY": _session_5min("2025-06-17", zero_vol_bars=3)},
        start=date(2025, 6, 17),
        end=date(2025, 6, 17),
    )
    assert "intraday_zero_volume_bars" in report.to_dict()["issue_codes"]


def test_missing_session_open_bar_warns_without_coverage_flag():
    # Drop only the 9:30 bar (offset 0). 77/78 = 98.7% coverage is ABOVE the
    # 0.90 floor, so the coverage code must NOT fire — only the open-bar code.
    report = _scan(
        {"SPY": _session_5min("2025-06-17", open_offset_skip=1)},
        start=date(2025, 6, 17),
        end=date(2025, 6, 17),
    )
    codes = report.to_dict()["issue_codes"]
    assert "intraday_missing_session_open_bar" in codes
    assert "intraday_session_coverage_below_threshold" not in codes


def test_coverage_below_threshold_warns_when_many_bars_missing():
    # Drop 9 of 78 (open + 8 more) -> 69/78 = 88% < 90% floor.
    report = _scan(
        {"SPY": _session_5min("2025-06-17", open_offset_skip=9)},
        start=date(2025, 6, 17),
        end=date(2025, 6, 17),
    )
    assert "intraday_session_coverage_below_threshold" in report.to_dict()["issue_codes"]


def test_interior_gap_warns():
    # Drop two consecutive interior bars (offsets 40, 45 = 12:50/12:55-ish).
    report = _scan(
        {"SPY": _session_5min("2025-06-17", drop_offsets=(40, 41))},
        start=date(2025, 6, 17),
        end=date(2025, 6, 17),
    )
    assert "intraday_intra_session_gap" in report.to_dict()["issue_codes"]


def test_hourly_timeframe_full_session_passes():
    # 1h does not divide the 390-min session: a clean session has 7 bars
    # (9:30,10:30,..,15:30 = offsets 0,60,..,360), NOT 390//60=6. Regression for
    # the ceil-division / last-grid-offset fix — must report 100% coverage and no
    # spurious missing-close-bar.
    start = pd.Timestamp("2025-06-17 09:30", tz="America/New_York")
    rows = [
        {
            "timestamp": (start + pd.Timedelta(minutes=60 * i)).tz_convert("UTC"),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
            "vwap": 100.2,
        }
        for i in range(7)
    ]
    report = scan_intraday_readiness(
        {"SPY": _barset(rows)},
        timeframe_minutes=60,
        requested_start=date(2025, 6, 17),
        requested_end=date(2025, 6, 17),
    )
    sr = report.per_symbol[0]
    assert sr.expected_bars == 7 and sr.observed_bars == 7
    assert sr.coverage_pct == 100.0
    assert report.status == "pass"
    assert "intraday_missing_session_close_bar" not in report.to_dict()["issue_codes"]


def test_half_day_expected_count_is_42():
    # 2025-11-28 is a known half-day (210 min / 5 = 42 bars).
    report = _scan(
        {"SPY": _session_5min("2025-11-28", n_bars=42)},
        start=date(2025, 11, 28),
        end=date(2025, 11, 28),
    )
    assert report.per_symbol[0].expected_bars == 42
    assert report.status == "pass"


def test_stale_dataset_tail_warns():
    # last session 2025-06-10; requested_end 2025-06-18 (tolerance 7d):
    # 2025-06-10 < 2025-06-11 -> fires.
    report = _scan(
        {"SPY": _session_5min("2025-06-10")},
        start=date(2025, 6, 10),
        end=date(2025, 6, 18),
    )
    assert "intraday_stale_dataset_tail" in report.to_dict()["issue_codes"]


def test_empty_barset_warns_no_bars():
    report = scan_intraday_readiness(
        {
            "SPY": BarSet(
                pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"]
                )
            )
        },
        timeframe_minutes=5,
        requested_start=date(2025, 6, 17),
        requested_end=date(2025, 6, 17),
    )
    assert "intraday_no_bars" in report.to_dict()["issue_codes"]


def test_feed_label_recorded():
    report = _scan(
        {"SPY": _session_5min("2025-06-17")},
        start=date(2025, 6, 17),
        end=date(2025, 6, 17),
        feed_label="fallback",
    )
    assert report.to_dict()["feed_label"] == "fallback"


def test_reference_cross_check_folds_inward_bias():
    # Narrow IEX session (high 100.5/low 99.5) vs wide consolidated daily (102/98).
    rows = _session_5min("2025-06-17")
    for r in rows:
        r["high"], r["low"] = 100.5, 99.5
    reference = {
        "SPY": pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2025-06-17", tz="UTC"),
                    "open": 100.0,
                    "high": 102.0,
                    "low": 98.0,
                    "close": 100.0,
                    "volume": 1_000_000,
                    "vwap": float("nan"),
                }
            ]
        )
    }
    report = scan_intraday_readiness(
        {"SPY": _barset(rows)},
        timeframe_minutes=5,
        requested_start=date(2025, 6, 17),
        requested_end=date(2025, 6, 17),
        reference_daily_by_symbol=reference,
    )
    # min_sessions defaults to 5, so a single session does NOT flag — proves the
    # advisory gate is inert on short windows (documented behaviour).
    assert "iex_inward_price_bias" not in report.to_dict()["issue_codes"]
