"""Free consolidated daily reference for the IEX price-fidelity cross-check.

DISTINCT from yahoo_provider.py (VIX-only by contract). This module fetches
arbitrary-symbol DAILY OHLC from a free consolidated source (Yahoo) for the SOLE
purpose of cross-validating IEX-derived intraday session extremes. Read-only,
best-effort (empty frame on any error), never feeds the trade path — it only
informs a data-readiness verdict.

Why this gate exists: IEX is ~2.5% of consolidated volume and under-samples
session high/low extremes (ADR 0017). A price-action verdict built on IEX bars
*looks* rigorous but rests on biased inputs, and baselines cannot detect this
(candidate and null see the same biased bars).

Adjustment-invariant comparison (the load-bearing design choice): Alpaca intraday
bars are split+dividend ADJUSTED; a raw daily reference would sit at a different
price LEVEL, swamping any microstructure signal. So the gate compares session
range SHAPE — ``(high - low) / close`` — not absolute levels. That ratio is
invariant to any uniform scaling of H/L/close (which is exactly what an
adjustment factor is). We additionally fetch the reference with
``auto_adjust=True`` as belt-and-suspenders.

Calibration caveat: the trigger (range-ratio floor, >50% of >= min_sessions) is a
heuristic. It has NOT been calibrated against a real IEX-vs-consolidated sample.
v1 output is a single advisory ``iex_inward_price_bias`` WARNING — it does not
gate promotion or block a backtest. Short windows (< min_sessions overlapping
sessions) yield no signal by design.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

from milodex.data.bar_quality import DataQualityIssue, DataQualitySeverity
from milodex.data.models import BarSet

_logger = logging.getLogger(__name__)

#: IEX must capture at least this fraction of the consolidated normalized range;
#: below it, that session counts as inward-biased. Heuristic, pending calibration.
DEFAULT_MIN_RANGE_RATIO = 0.80


def fetch_daily_ohlc(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Best-effort free daily OHLC for ``symbol`` ([start, end] inclusive).

    Returns the canonical bar schema (timestamp/open/high/low/close/volume/vwap),
    empty on any error. NOT a general provider — daily-only, cross-check-only.
    ``auto_adjust=True`` so the reference sits on the same adjusted basis as the
    Alpaca intraday bars.
    """
    import yfinance

    try:
        raw = yfinance.Ticker(symbol).history(
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=True,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("fetch_daily_ohlc(%s): %s", symbol, exc)
        return _empty()
    if raw is None or raw.empty:
        return _empty()
    try:
        return _reshape(raw)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("fetch_daily_ohlc(%s): reshape failed: %s", symbol, exc)
        return _empty()


def cross_check_session_extremes(
    intraday_by_symbol: dict[str, BarSet],
    reference_daily_by_symbol: dict[str, pd.DataFrame],
    *,
    min_range_ratio: float = DEFAULT_MIN_RANGE_RATIO,
    min_sessions: int = 5,
) -> list[DataQualityIssue]:
    """Flag symbols whose IEX session ranges are persistently NARROWER than the
    consolidated reference (inward bias from under-sampled extremes).

    Compares ``(high - low) / close`` per session (adjustment-invariant). A symbol
    is flagged when, over >= ``min_sessions`` overlapping sessions, more than half
    capture less than ``min_range_ratio`` of the consolidated normalized range.
    """
    # Lazy import: keep the strategy fleet out of the data import graph.
    from milodex.strategies._session_intraday import regular_session_bars, session_date_et

    issues: list[DataQualityIssue] = []
    for symbol in sorted(intraday_by_symbol):
        ref = reference_daily_by_symbol.get(symbol)
        if ref is None or ref.empty:
            continue
        ref_by_day = _daily_norm_range_by_session(ref)
        idf = intraday_by_symbol[symbol].to_dataframe()
        if idf.empty:
            continue
        ts = pd.to_datetime(idf["timestamp"], utc=True)
        sessions = sorted({session_date_et(t) for t in ts})
        compared = inward = 0
        for day in sessions:
            ref_norm = ref_by_day.get(day)
            if ref_norm is None or ref_norm <= 0:
                continue
            sess = regular_session_bars(idf, day)
            if sess.empty:
                continue
            iex_high = float(pd.to_numeric(sess["high"]).max())
            iex_low = float(pd.to_numeric(sess["low"]).min())
            iex_close = float(pd.to_numeric(sess["close"]).iloc[-1])
            if iex_close <= 0:
                continue
            iex_norm = (iex_high - iex_low) / iex_close
            compared += 1
            if iex_norm / ref_norm < min_range_ratio:
                inward += 1
        if compared >= min_sessions and inward / compared > 0.5:
            issues.append(
                DataQualityIssue(
                    code="iex_inward_price_bias",
                    severity=DataQualitySeverity.WARNING,
                    symbol=symbol,
                    message=(
                        f"{symbol}: IEX session range inward-biased vs consolidated reference "
                        f"on {inward}/{compared} sessions — price-action verdicts on this symbol "
                        f"are non-durable (heuristic; threshold pending real-data calibration)."
                    ),
                    context={"inward_sessions": inward, "compared_sessions": compared},
                )
            )
    return issues


def _daily_norm_range_by_session(ref: pd.DataFrame) -> dict[date, float]:
    """Map each reference session date -> normalized range ((high-low)/close).

    A daily bar's calendar date IS its session date — keyed by the UTC-normalized
    timestamp's date (midnight-stamped daily bars resolve to the correct day),
    NOT by ET-of-a-point-in-time conversion.
    """
    out: dict[date, float] = {}
    ts = pd.to_datetime(ref["timestamp"], utc=True)
    for i, t in enumerate(ts):
        close = float(ref["close"].iloc[i])
        if close <= 0:
            continue
        out[t.date()] = (float(ref["high"].iloc[i]) - float(ref["low"].iloc[i])) / close
    return out


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"])


def _reshape(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.reset_index()
    df.columns = [c.lower() for c in df.columns]
    ts_col = next((c for c in ("date", "datetime") if c in df.columns), None)
    if ts_col is None:
        raise KeyError(f"no timestamp column in {list(df.columns)}")
    return (
        pd.DataFrame(
            {
                "timestamp": pd.to_datetime(df[ts_col], utc=True),
                "open": pd.to_numeric(df["open"], errors="coerce").astype("float64"),
                "high": pd.to_numeric(df["high"], errors="coerce").astype("float64"),
                "low": pd.to_numeric(df["low"], errors="coerce").astype("float64"),
                "close": pd.to_numeric(df["close"], errors="coerce").astype("float64"),
                "volume": pd.to_numeric(df.get("volume", 0), errors="coerce")
                .fillna(0)
                .astype("int64"),
                "vwap": float("nan"),
            }
        )
        .dropna(subset=["close"])
        .reset_index(drop=True)
    )
