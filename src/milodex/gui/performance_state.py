"""Live performance model exposed to QML.

Owns Week / Month / YTD / All-Paper slices computed from ``portfolio_snapshots``
(SQLite) and the SPY benchmark computed from the Parquet market cache.  "Today"
is a zeroed placeholder — the QML layer binds ``OperationalState.dailyPnl`` for
the live intra-day figure.

Table contract (ADR 0053)
-------------------------
``portfolio_snapshots`` holds **broker-side account state only** — one row per
session end from the real Alpaca paper/live account.  Simulated equity points
from the backtest engine live in ``backtest_equity_snapshots``.

``_SQL_ALL_PAPER`` and ``_SQL_RANGED`` query ``portfolio_snapshots`` and do NOT
need to be updated — the table is clean by construction after migration 010.
The +9865% / -99% incident (ADR 0053 context) cannot recur here as long as the
writer contract is respected and migration 010 has been applied.

If you are reading this because the ALL-PAPER metric looks wrong:
1. Confirm migration 010 has been applied (``SELECT version FROM _schema_version``
   should be >= 10).
2. Confirm ``portfolio_snapshots`` has no rows with ``session_id LIKE '%:w%'``
   (that is the symptom of a writer bypass, not a reader bug).

Threading model
---------------
Identical to :mod:`milodex.gui.strategy_bank_state`:

- A :class:`QTimer` fires every ``refresh_interval_ms`` (default 30 s) on the
  main thread.
- The timer schedules a :class:`QRunnable` on a *per-instance*
  :class:`QThreadPool` (``maxThreadCount=1``).
- Results flow back via :class:`Qt.ConnectionType.QueuedConnection`.
- :meth:`stop` drains in-flight workers via ``waitForDone(2000)`` and
  defensively disconnects signals.

Read-only guarantee
-------------------
All SQLite connections are opened ``file:<path>?mode=ro`` (URI mode) to ensure
the performance worker never mutates the event store.

Staleness
---------
:func:`_is_stale` flags the data as stale if the newest snapshot is more than
2 calendar days old (spec §8 simplification — no trading-calendar utility
exists yet).

Slices
------
``SLICES = ("Today", "Week", "Month", "YTD", "All-Paper")``.

- ``Today`` — always placeholder zeros; QML overlays ``OperationalState.dailyPnl``.
- ``Week`` — ``[now - 7 days, now]``
- ``Month`` — ``[now - 30 days, now]``
- ``YTD`` — ``[Jan 1 of current year, now]``
- ``All-Paper`` — ``[earliest snapshot, now]``

SPY benchmark
-------------
Reads the highest ``vN`` version directory from the market cache via
:func:`_latest_cache_version`, then computes ``_period_return`` on the ``close``
column over each slice window.  Missing cache → ``None`` returns.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import (  # pragma: no cover
    Property,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)

from milodex.config import get_cache_dir
from milodex.gui._market_cache import _latest_cache_version  # noqa: PLC2701

logger = logging.getLogger(__name__)

SLICES = ("Today", "Week", "Month", "YTD", "All-Paper")

# ---------------------------------------------------------------------------
# Pure helpers (spec-mandated implementations — do not modify)
# ---------------------------------------------------------------------------


def _period_return(equity_series: list[float]) -> float | None:
    # Negative starting equity is assumed impossible (paper trading), so
    # a zero check is sufficient — sign inversion is acceptable here.
    if len(equity_series) < 2 or equity_series[0] == 0:
        return None
    return (equity_series[-1] / equity_series[0]) - 1.0


def _max_drawdown(equity_series: list[float]) -> float | None:
    if not equity_series:
        return None
    peak = equity_series[0]
    mdd = 0.0
    for v in equity_series:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, (v / peak) - 1.0)
    return mdd  # <= 0.0


def _is_stale(newest_iso: str | None, now: datetime, max_trading_days: int = 2) -> bool:
    # spec §8: threshold pinned here at 2 calendar-day proxy for trading days
    # (documented simplification; refine only if a trading-calendar util exists).
    # An empty series (newest_iso is None) is NOT stale — it is "no data".
    # Stale means: a snapshot exists but is older than the freshness threshold.
    if newest_iso is None:
        return False
    newest = datetime.fromisoformat(newest_iso)
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    return (now - newest).days > max_trading_days


# ---------------------------------------------------------------------------
# Slice window helpers
# ---------------------------------------------------------------------------


def _slice_windows(now: datetime) -> dict[str, tuple[datetime, datetime] | None]:
    """Return {slice_name: (start, end)} for each non-Today slice.

    ``All-Paper`` start is derived from DB; returns ``None`` as placeholder.
    ``Today`` returns ``None`` (placeholder — not computed here).
    """
    jan1 = datetime(now.year, 1, 1, tzinfo=UTC)
    return {
        "Today": None,
        "Week": (now - timedelta(days=7), now),
        "Month": (now - timedelta(days=30), now),
        "YTD": (jan1, now),
        "All-Paper": None,  # filled from DB
    }


# ---------------------------------------------------------------------------
# SQL query
# ---------------------------------------------------------------------------

_SQL_SNAPSHOTS_WINDOW = """
SELECT recorded_at,
       equity
FROM (
    SELECT recorded_at,
           equity,
           ROW_NUMBER() OVER (PARTITION BY recorded_at ORDER BY id DESC) AS rn
    FROM portfolio_snapshots
    WHERE recorded_at >= ? AND recorded_at <= ?
) sub
WHERE rn = 1
ORDER BY recorded_at
"""

_SQL_ALL_PAPER = """
SELECT recorded_at,
       equity
FROM (
    SELECT recorded_at,
           equity,
           ROW_NUMBER() OVER (PARTITION BY recorded_at ORDER BY id DESC) AS rn
    FROM portfolio_snapshots
) sub
WHERE rn = 1
ORDER BY recorded_at
"""

_SQL_NEWEST = "SELECT MAX(recorded_at) AS newest FROM portfolio_snapshots"


def _query_performance(
    db_path: Path,
    now: datetime,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Query portfolio_snapshots and compute slice metrics.

    Opens a read-only SQLite connection.  Raises on missing / unreadable DB.

    Returns a dict with keys:
    - ``by_slice``: {slice → {"return": float|None, "drawdown": float|None}}
    - ``benchmark_by_slice``: {slice → {"spyReturn": float|None, "excess": float|None}}
    - ``sparkline``: list[float] — equity series for All-Paper window
    - ``newest_recorded_at``: str|None
    """
    if cache_dir is None:
        cache_dir = get_cache_dir()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Newest timestamp for stale check
        row = conn.execute(_SQL_NEWEST).fetchone()
        newest_recorded_at: str | None = row["newest"] if row else None

        # All-Paper series (all rows deduplicated by highest id per timestamp)
        all_rows = conn.execute(_SQL_ALL_PAPER).fetchall()
        all_paper_series = [r["equity"] for r in all_rows]
        all_paper_times = [r["recorded_at"] for r in all_rows]

        # Build slice equity series
        windows = _slice_windows(now)

        by_slice: dict[str, dict[str, float | None]] = {}
        equity_by_slice: dict[str, list[float]] = {}
        time_by_slice: dict[str, list[str]] = {}

        for slice_name in SLICES:
            if slice_name == "Today":
                # Placeholder — QML layer overlays OperationalState.dailyPnl
                by_slice[slice_name] = {"return": None, "drawdown": None}
                equity_by_slice[slice_name] = []
                time_by_slice[slice_name] = []
                continue

            if slice_name == "All-Paper":
                series = all_paper_series
                times = all_paper_times
            else:
                window = windows[slice_name]
                assert window is not None
                start_iso = window[0].isoformat()
                end_iso = window[1].isoformat()
                rows = conn.execute(_SQL_SNAPSHOTS_WINDOW, (start_iso, end_iso)).fetchall()
                series = [r["equity"] for r in rows]
                times = [r["recorded_at"] for r in rows]

            equity_by_slice[slice_name] = series
            time_by_slice[slice_name] = times
            by_slice[slice_name] = {
                "return": _period_return(series),
                "drawdown": _max_drawdown(series),
            }

    finally:
        conn.close()

    # SPY benchmark
    benchmark_by_slice = _compute_benchmark(
        cache_dir=cache_dir,
        now=now,
        windows=windows,
        time_by_slice=time_by_slice,
        by_slice=by_slice,
    )

    return {
        "by_slice": by_slice,
        "benchmark_by_slice": benchmark_by_slice,
        "sparkline": all_paper_series,
        "newest_recorded_at": newest_recorded_at,
    }


def _compute_benchmark(
    *,
    cache_dir: Path,
    now: datetime,
    windows: dict[str, tuple[datetime, datetime] | None],
    time_by_slice: dict[str, list[str]],
    by_slice: dict[str, dict[str, float | None]],
) -> dict[str, dict[str, float | None]]:
    """Read SPY parquet and compute benchmark returns per slice.

    Returns {slice → {"spyReturn": float|None, "excess": float|None}}.
    """
    import pandas as pd

    from milodex.data.cache import ParquetCache
    from milodex.data.models import Timeframe

    version = _latest_cache_version(cache_dir)
    spy_df: pd.DataFrame | None = None
    if version is not None:
        cache = ParquetCache(cache_dir, version=version)
        spy_df = cache.read("SPY", Timeframe.DAY_1)

    benchmark: dict[str, dict[str, float | None]] = {}

    for slice_name in SLICES:
        if slice_name == "Today":
            benchmark[slice_name] = {"spyReturn": None, "excess": None}
            continue

        spy_return: float | None = None

        if spy_df is not None and not spy_df.empty:
            try:
                ts = pd.to_datetime(spy_df["timestamp"], utc=True)
                if slice_name == "All-Paper":
                    # Use same time bounds as the portfolio series
                    times = time_by_slice.get("All-Paper", [])
                    if times:
                        t0 = pd.Timestamp(times[0])
                        t1 = pd.Timestamp(times[-1])
                        start = t0.tz_localize("UTC") if t0.tzinfo is None else t0
                        end = t1.tz_localize("UTC") if t1.tzinfo is None else t1
                        mask = (ts >= start) & (ts <= end)
                    else:
                        mask = pd.Series(False, index=spy_df.index)
                else:
                    window = windows[slice_name]
                    assert window is not None
                    start_ts = pd.Timestamp(window[0])
                    end_ts = pd.Timestamp(window[1])
                    mask = (ts >= start_ts) & (ts <= end_ts)

                subset = spy_df.loc[mask].sort_values("timestamp")
                if not subset.empty:
                    closes = subset["close"].tolist()
                    spy_return = _period_return(closes)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "PerformanceState: SPY benchmark failed for %s: %s",
                    slice_name,
                    exc,
                )
                spy_return = None

        strat_return = by_slice[slice_name]["return"]
        excess: float | None = None
        if spy_return is not None and strat_return is not None:
            excess = strat_return - spy_return

        benchmark[slice_name] = {"spyReturn": spy_return, "excess": excess}

    return benchmark


# ---------------------------------------------------------------------------
# Worker scaffold
# ---------------------------------------------------------------------------


class _PerfRefreshSignals(QObject):
    """Signal carrier for the PerformanceState refresh worker."""

    completed = Signal(dict)
    failed = Signal(str)


class _PerfRefreshRunnable(QRunnable):
    """One-shot refresh executed on a QThreadPool worker thread."""

    def __init__(
        self,
        db_path: Path,
        cache_dir: Path,
        signals: _PerfRefreshSignals,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._cache_dir = cache_dir
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover — exercised via tests with fixture DBs
        try:
            now = datetime.now(tz=UTC)
            result = _query_performance(self._db_path, now, cache_dir=self._cache_dir)
            result["refreshed_at"] = now.isoformat()
            self._signals.completed.emit(result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PerformanceState: DB refresh failed: %s", exc)
            self._signals.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# PerformanceState
# ---------------------------------------------------------------------------


class PerformanceState(QObject):
    """Portfolio performance state exposed to QML as Q_PROPERTYs.

    See module docstring for threading model, slice definitions, and SPY
    benchmark sourcing.
    """

    bySliceChanged = Signal()  # noqa: N815
    benchmarkBySliceChanged = Signal()  # noqa: N815
    sparklineChanged = Signal()  # noqa: N815
    isStaleChanged = Signal()  # noqa: N815
    hasSnapshotChanged = Signal()  # noqa: N815
    staleAsOfChanged = Signal()  # noqa: N815
    lastRefreshedAtChanged = Signal()  # noqa: N815
    dataStatusChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path | None = None,
        cache_dir: Path | None = None,
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        if db_path is None:
            from milodex.config import get_data_dir

            db_path = get_data_dir() / "milodex.db"
        self._db_path = db_path

        if cache_dir is None:
            cache_dir = get_cache_dir()
        self._cache_dir = cache_dir

        self._refresh_interval_ms = max(1, refresh_interval_ms)

        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)

        # State backing fields
        self._by_slice: dict[str, Any] = {}
        self._benchmark_by_slice: dict[str, Any] = {}
        self._sparkline: list[float] = []
        self._is_stale: bool = False
        self._has_snapshot: bool = False
        self._stale_as_of: str = ""
        self._last_refreshed_at: str = ""
        self._data_status: str = "loading"
        self._data_error_message: str = ""

        # QTimer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self._refresh_interval_ms)
        self._refresh_timer.timeout.connect(self._kick_refresh)

        # Signal carrier
        self._refresh_signals = _PerfRefreshSignals(self)
        self._refresh_signals.completed.connect(
            self._on_refresh_complete, Qt.ConnectionType.QueuedConnection
        )
        self._refresh_signals.failed.connect(
            self._on_refresh_failed, Qt.ConnectionType.QueuedConnection
        )

        self._refresh_in_flight: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin periodic DB polling with an immediate first refresh."""
        self._kick_refresh()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def stop(self) -> None:
        """Halt polling and drain any in-flight DB worker."""
        self._refresh_timer.stop()
        self._thread_pool.waitForDone(2000)
        try:
            self._refresh_signals.completed.disconnect(self._on_refresh_complete)
            self._refresh_signals.failed.disconnect(self._on_refresh_failed)
        except (RuntimeError, TypeError):
            pass

    # ------------------------------------------------------------------
    # Worker scheduling
    # ------------------------------------------------------------------

    def _kick_refresh(self) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        runnable = _PerfRefreshRunnable(self._db_path, self._cache_dir, self._refresh_signals)
        self._thread_pool.start(runnable)

    @Slot(dict)
    def _on_refresh_complete(self, result: dict[str, Any]) -> None:
        self._refresh_in_flight = False

        by_slice_changed = result["by_slice"] != self._by_slice
        benchmark_changed = result["benchmark_by_slice"] != self._benchmark_by_slice
        sparkline_changed = result["sparkline"] != self._sparkline

        self._by_slice = result["by_slice"]
        self._benchmark_by_slice = result["benchmark_by_slice"]
        self._sparkline = result["sparkline"]
        self._last_refreshed_at = result["refreshed_at"]

        # Compute staleness and snapshot presence
        now = datetime.now(tz=UTC)
        newest = result.get("newest_recorded_at")
        new_has_snapshot = newest is not None
        new_is_stale = _is_stale(newest, now)
        new_stale_as_of = newest or ""

        stale_changed = (
            new_is_stale != self._is_stale
            or new_stale_as_of != self._stale_as_of
            or new_has_snapshot != self._has_snapshot
        )
        self._is_stale = new_is_stale
        self._has_snapshot = new_has_snapshot
        self._stale_as_of = new_stale_as_of

        self.lastRefreshedAtChanged.emit()
        if by_slice_changed:
            self.bySliceChanged.emit()
        if benchmark_changed:
            self.benchmarkBySliceChanged.emit()
        if sparkline_changed:
            self.sparklineChanged.emit()
        if stale_changed:
            self.isStaleChanged.emit()
            self.hasSnapshotChanged.emit()
            self.staleAsOfChanged.emit()

        if self._data_status != "ready" or self._data_error_message:
            self._data_status = "ready"
            self._data_error_message = ""
            self.dataStatusChanged.emit()

    @Slot(str)
    def _on_refresh_failed(self, message: str) -> None:
        self._refresh_in_flight = False
        if self._data_status != "error" or self._data_error_message != message:
            self._data_status = "error"
            self._data_error_message = message
            self.dataStatusChanged.emit()

    # ------------------------------------------------------------------
    # Q_PROPERTY accessors
    # ------------------------------------------------------------------

    def _get_by_slice(self) -> dict:
        return self._by_slice

    def _get_benchmark_by_slice(self) -> dict:
        return self._benchmark_by_slice

    def _get_sparkline(self) -> list:
        return self._sparkline

    def _get_is_stale(self) -> bool:
        return self._is_stale

    def _get_has_snapshot(self) -> bool:
        return self._has_snapshot

    def _get_stale_as_of(self) -> str:
        return self._stale_as_of

    def _get_last_refreshed_at(self) -> str:
        return self._last_refreshed_at

    def _get_data_status(self) -> str:
        return self._data_status

    def _get_data_error_message(self) -> str:
        return self._data_error_message

    bySlice = Property(  # noqa: N815
        "QVariantMap", _get_by_slice, notify=bySliceChanged
    )
    benchmarkBySlice = Property(  # noqa: N815
        "QVariantMap", _get_benchmark_by_slice, notify=benchmarkBySliceChanged
    )
    sparkline = Property(  # noqa: N815
        "QVariantList", _get_sparkline, notify=sparklineChanged
    )
    isStale = Property(bool, _get_is_stale, notify=isStaleChanged)  # noqa: N815
    hasSnapshot = Property(bool, _get_has_snapshot, notify=hasSnapshotChanged)  # noqa: N815
    staleAsOf = Property(str, _get_stale_as_of, notify=staleAsOfChanged)  # noqa: N815
    lastRefreshedAt = Property(  # noqa: N815
        str, _get_last_refreshed_at, notify=lastRefreshedAtChanged
    )
    dataStatus = Property(str, _get_data_status, notify=dataStatusChanged)  # noqa: N815
    dataErrorMessage = Property(  # noqa: N815
        str, _get_data_error_message, notify=dataStatusChanged
    )
