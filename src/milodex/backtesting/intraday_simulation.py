"""Intraday-only helpers used by :meth:`BacktestEngine._simulate_intraday`.

These functions were extracted from ``engine.py`` (see PR
``refactor/backtest-intraday-helpers-split``) so the engine module no longer
carries ~220 lines of intraday timeline / cursor / mark-to-market machinery
below a 1,277-line class. The daily simulation path does NOT import this
module — every function here is exclusively called by the intraday event loop.

Functions:

- :func:`_build_intraday_event_timeline` — chronological union of fill events
  and decision events for a trading day.
- :func:`_opens_at_timestamp` — open prices for symbols whose bar starts at a
  given timestamp.
- :func:`_mark_to_market_at_day_end` — EOD equity using each symbol's latest
  available close on or before the day.
- :func:`_build_visible_bars` — per-symbol :class:`BarSet` view truncated to
  the symbol's current cursor.
- :func:`_latest_close_at_ts` — latest close per symbol at or before a
  timestamp (or end-of-history when timestamp is ``None``).
- :func:`_advance_cursors` — advance per-symbol cursors past bars whose
  decision time has elapsed; in-place mutation.

Spec reference: ``docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md``
§3 (component #3 event timeline) and Corrections 6 / 8.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import numpy as np
import pandas as pd

from milodex.data.models import BarSet

_EASTERN_TZ = "America/New_York"
_RTH_OPEN = time(9, 30)
_RTH_CLOSE = time(16, 0)


def _is_rth_bar(bar_ts: pd.Timestamp, day: date) -> bool:
    """Return whether ``bar_ts`` starts inside ``day``'s US-equity cash session."""
    eastern = bar_ts.tz_convert(_EASTERN_TZ)
    return eastern.date() == day and _RTH_OPEN <= eastern.time() < _RTH_CLOSE


def _regular_session_mask(ts_index: pd.DatetimeIndex) -> np.ndarray:
    """Return a vectorized mask for US-equity regular-session bar starts."""
    eastern = ts_index.tz_convert(_EASTERN_TZ)
    minute_of_day = eastern.hour * 60 + eastern.minute
    return np.asarray((minute_of_day >= 9 * 60 + 30) & (minute_of_day < 16 * 60))


def _build_intraday_event_timeline(
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    day: date,
    bar_size_minutes: int,
    *,
    regular_session_only: bool = False,
) -> list[tuple[pd.Timestamp, dict[str, Any]]]:
    """Return the chronological event timeline for one trading day.

    Each entry is ``(timestamp, metadata)`` where ``metadata`` carries:
    - ``fill_symbols``: list of symbols with a bar at ``bar_timestamp == timestamp``
    - ``decision_symbols``: list of symbols with a bar at ``decision_time == timestamp``

    The timeline is the chronological union of fill events (bar starts) and
    decision events (bar completions) for all universe symbols whose bars
    fall in ``day``. See spec §3 component #3.

    Args:
        per_symbol_ts_utc: precomputed UTC-tz-aware DatetimeIndex per symbol.
            The Phase E ``_simulate_intraday`` builds these once at the top
            of the simulation to avoid redundant ``to_dataframe()`` calls
            inside the event loop (Correction 6).
        day: the trading day to scope the timeline to (UTC dates may differ
            from ET dates due to timezone; the helper filters on the UTC
            timestamp's ``.date()`` value).
        bar_size_minutes: bar size in minutes, used to compute
            ``decision_time = bar_timestamp + bar_size``.
    """
    bar_size = pd.Timedelta(minutes=bar_size_minutes)
    fill_map: dict[pd.Timestamp, list[str]] = {}
    decision_map: dict[pd.Timestamp, list[str]] = {}

    for symbol, ts_index in per_symbol_ts_utc.items():
        for bar_ts in ts_index:
            if regular_session_only:
                if not _is_rth_bar(bar_ts, day):
                    continue
            elif bar_ts.date() != day:
                continue
            fill_map.setdefault(bar_ts, []).append(symbol)
            decision_ts = bar_ts + bar_size
            decision_map.setdefault(decision_ts, []).append(symbol)

    all_event_times = sorted(set(fill_map.keys()) | set(decision_map.keys()))
    return [
        (
            t,
            {
                "fill_symbols": fill_map.get(t, []),
                "decision_symbols": decision_map.get(t, []),
            },
        )
        for t in all_event_times
    ]


def _opens_at_timestamp(
    per_symbol_open_by_ts: dict[str, dict[pd.Timestamp, float]],
    timestamp: pd.Timestamp,
) -> dict[str, float]:
    """Return ``{symbol: open_price}`` for symbols with a bar at ``timestamp``.

    Symbols without a bar at ``timestamp`` are not in the result. This means
    the caller can safely iterate the returned dict and trust each key has
    a fill price.

    Args:
        per_symbol_open_by_ts: precomputed nested dict mapping symbol →
            {bar_timestamp: open_price}. The Phase E ``_simulate_intraday``
            builds this once at simulation start to avoid DataFrame scans
            inside the event loop (Correction 6).
        timestamp: UTC-tz-aware Timestamp to look up.
    """
    opens: dict[str, float] = {}
    for symbol, open_by_ts in per_symbol_open_by_ts.items():
        if timestamp in open_by_ts:
            opens[symbol] = open_by_ts[timestamp]
    return opens


def _latest_close_on_day_for_symbol(
    symbol: str,
    per_symbol_df: dict[str, pd.DataFrame],
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    day: date,
    *,
    regular_session_only: bool = False,
    allow_prior: bool = True,
) -> float | None:
    """Return ``symbol``'s latest close at or before ``day``, or ``None``.

    Selection: the symbol's last close whose bar falls on ``day``; if the
    symbol has no bars on ``day``, fall back to its last close strictly
    before ``day``. ``None`` when the symbol is unknown or has no bar on or
    before ``day``.

    This is the single source of the day-end valuation price for a symbol.
    Both :func:`_mark_to_market_at_day_end` and the engine's session-end
    force-flatten read from here so the liquidation fill price is byte-equal
    to the mark-to-market price (equity is continuous across the flatten).
    """
    if symbol not in per_symbol_df:
        return None
    df = per_symbol_df[symbol]
    ts_utc = per_symbol_ts_utc[symbol]
    if regular_session_only:
        day_indices = np.array(
            [i for i, bar_ts in enumerate(ts_utc) if _is_rth_bar(bar_ts, day)],
            dtype=int,
        )
        prior_indices = np.array(
            [
                i
                for i, bar_ts in enumerate(ts_utc)
                if bar_ts.tz_convert(_EASTERN_TZ).date() < day
                and _RTH_OPEN <= bar_ts.tz_convert(_EASTERN_TZ).time() < _RTH_CLOSE
            ],
            dtype=int,
        )
    else:
        date_array = ts_utc.date  # numpy array of date objects
        day_indices = np.flatnonzero(date_array == day)
        prior_indices = np.flatnonzero(date_array < day)
    if len(day_indices) > 0:
        return float(df["close"].iloc[day_indices[-1]])
    if not allow_prior:
        return None
    if len(prior_indices) == 0:
        return None
    return float(df["close"].iloc[prior_indices[-1]])


def _last_closes_on_day(
    symbols: list[str],
    per_symbol_df: dict[str, pd.DataFrame],
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    day: date,
    *,
    regular_session_only: bool = False,
    allow_prior: bool = True,
) -> dict[str, float]:
    """Return ``{symbol: latest_close_on_day}`` for the given symbols.

    Symbols with no resolvable close on or before ``day`` are omitted (same
    rule :func:`_mark_to_market_at_day_end` uses to skip a position from the
    equity sum). Used by the engine's session-end force-flatten to value the
    liquidation at the same price the day-end mark-to-market uses.
    """
    closes: dict[str, float] = {}
    for symbol in symbols:
        price = _latest_close_on_day_for_symbol(
            symbol,
            per_symbol_df,
            per_symbol_ts_utc,
            day,
            regular_session_only=regular_session_only,
            allow_prior=allow_prior,
        )
        if price is not None:
            closes[symbol] = price
    return closes


def _mark_to_market_at_day_end(
    positions: dict[str, tuple[float, float]],
    per_symbol_df: dict[str, pd.DataFrame],
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    day: date,
    cash: float,
    *,
    regular_session_only: bool = False,
) -> float:
    """Return end-of-day equity = cash + sum(qty * latest_close_for_symbol_on_day).

    Uses each symbol's latest available close at or before the day's final
    timestamp. Critical for multi-symbol universes where one symbol may
    be missing the final bar of the day — falls back to prior day's last
    close.

    Args:
        positions: {symbol: (qty, avg_cost)} for open positions.
        per_symbol_df: precomputed OHLCV DataFrame per symbol. Must contain
            a "close" column.
        per_symbol_ts_utc: precomputed UTC-tz-aware DatetimeIndex per symbol,
            aligned row-for-row with ``per_symbol_df``.
        day: trading day to mark to.
        cash: current cash balance.

    Returns:
        Equity = cash + sum of (qty × latest close on day, or prior close
        if no bars on day).
    """
    equity = cash
    for symbol, (qty, _avg_cost) in positions.items():
        latest_close = _latest_close_on_day_for_symbol(
            symbol,
            per_symbol_df,
            per_symbol_ts_utc,
            day,
            regular_session_only=regular_session_only,
        )
        if latest_close is None:
            continue
        equity += qty * latest_close
    return equity


def _build_visible_bars(
    per_symbol_df: dict[str, pd.DataFrame],
    cursors: dict[str, int],
    universe: list[str],
) -> dict[str, BarSet]:
    """Return a {symbol: BarSet} view for every universe symbol, truncated to cursor.

    ``cursors[symbol]`` is the EXCLUSIVE end index (see D5 invariant): visible
    bars are ``df.iloc[:cursor]``.  Symbols with cursor == 0 (no history yet)
    are omitted from the result so downstream code that checks ``len(barset) > 0``
    behaves naturally.

    Only universe symbols are included — symbols outside the declared universe
    never appear in strategy.evaluate() regardless of bar availability.
    """
    result: dict[str, BarSet] = {}
    for sym in universe:
        df = per_symbol_df.get(sym)
        if df is None:
            continue
        cur = cursors.get(sym, 0)
        if cur == 0:
            continue
        sliced = df.iloc[:cur]
        if not sliced.empty:
            result[sym] = BarSet(sliced.reset_index(drop=True))
    return result


def _latest_close_at_ts(
    per_symbol_df: dict[str, pd.DataFrame],
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    ts: pd.Timestamp | None,
) -> dict[str, float]:
    """Return the latest close per symbol at or before ``ts``.

    When ``ts`` is ``None``, returns the very last close in each symbol's
    full bar history (used for end-of-backtest bookkeeping).

    Symbols with no bars at or before ``ts`` are omitted.
    """
    closes: dict[str, float] = {}
    for symbol, df in per_symbol_df.items():
        if df.empty:
            continue
        dti = per_symbol_ts_utc.get(symbol)
        if dti is None or len(dti) == 0:
            continue
        if ts is None:
            closes[symbol] = float(df["close"].iloc[-1])
        else:
            # Find the latest bar whose timestamp <= ts.
            idx = dti.searchsorted(ts, side="right") - 1
            if idx >= 0:
                closes[symbol] = float(df["close"].iloc[idx])
    return closes


def _advance_cursors(
    cursors: dict[str, int],
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    timestamp: pd.Timestamp,
    bar_size_minutes: int,
) -> bool:
    """Advance ``cursors[symbol]`` for each symbol whose next-unconsumed bar
    has ``decision_time <= timestamp``. Return True if any cursor advanced.

    Cursor invariant: ``cursor[symbol]`` is the EXCLUSIVE end index of the
    symbol's visible bar history. Visible = ``df.iloc[:cursor[symbol]]``.

    Args:
        cursors: per-symbol exclusive-end-index map. MUTATED IN PLACE.
        per_symbol_ts_utc: precomputed UTC-tz-aware DatetimeIndex per symbol
            (Correction 6).
        timestamp: the event timestamp to advance cursors up to.
        bar_size_minutes: bar size in minutes (decision_time = bar_ts + bar_size).

    Returns:
        True if any cursor advanced; False otherwise. Phase E uses this to
        decide whether to call _evaluate_strategy.
    """
    bar_size = pd.Timedelta(minutes=bar_size_minutes)
    advanced = False
    for symbol, ts_index in per_symbol_ts_utc.items():
        idx = cursors.get(symbol, 0)
        n = len(ts_index)
        while idx < n:
            bar_ts = ts_index[idx]
            decision_time = bar_ts + bar_size
            if decision_time <= timestamp:
                idx += 1
                advanced = True
            else:
                break
        cursors[symbol] = idx
    return advanced
