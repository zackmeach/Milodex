"""Local Parquet cache for market data.

Stores OHLCV bars as Parquet files organized by version, timeframe and symbol.
Layout: {cache_dir}/{version}/{timeframe_value}/{SYMBOL}.parquet

Incrementing the version segment forces cache invalidation — existing parquets
under the old version directory are ignored and fresh data is fetched.

The cache is append-only for historical data. Today's bar is always
considered stale (re-fetched) since the market may still be open.
"""

from __future__ import annotations

import logging
import os
import random
import time
import uuid
from datetime import date
from pathlib import Path

import pandas as pd

from milodex.data.models import Timeframe

_logger = logging.getLogger(__name__)

# --- Rename-contention retry budget (writer vs. open-handle collision) --------
# Windows cannot replace a file another process holds open: ``os.replace`` needs
# DELETE access on the destination, and a plain open handle (``pd.read_parquet``
# holds one for the whole read) denies it -> PermissionError [WinError 5].
# Under same-symbol co-run the destination is held open by SOME reader a large
# fraction of wall time — five co-running SPY intraday runners exhausted the
# previous ~70 ms budget (4 attempts, 10 ms base) and crashed two sessions on
# 2026-07-23. The budget below spans several back-to-back reader holds (a
# 126k-row parquet read is ~100-300 ms): worst-case sleep is
# sum(base * 2**n) for n in 0..4 = 1.55 s, plus up to +25% jitter ≈ 1.9 s.
# Rare (only under sustained contention) and small next to the shortest poll
# interval, so a stalled write cannot starve the poll loop.
_RENAME_MAX_ATTEMPTS = 6
_RENAME_BASE_DELAY_SECONDS = 0.05
# Co-running writers that collide once are polling in phase and would re-collide
# on every lockstep retry; the random pad de-synchronizes them.
_RENAME_JITTER_FRACTION = 0.25


class CacheWriteContentionError(OSError):
    """Cache persistence failed: the atomic rename stayed blocked past the budget.

    Raised by :meth:`ParquetCache.write` when ``os.replace`` onto the
    destination kept failing with ``PermissionError`` (Windows sharing
    violation — a co-running process held the file open) through all
    ``_RENAME_MAX_ATTEMPTS`` attempts. Deliberately NOT a ``PermissionError``
    subclass: the budget is spent, so transient-retry handlers must not catch
    it. Distinct so poll-path callers can fail soft on the persistence step
    alone (serve in-memory bars, re-persist next poll) without widening their
    handling to fetch or data-integrity errors, which must stay loud. The
    underlying ``PermissionError`` is chained as ``__cause__``.
    """


def _replace_with_retry(
    src: Path,
    dst: Path,
    *,
    max_attempts: int = _RENAME_MAX_ATTEMPTS,
    base_delay: float = _RENAME_BASE_DELAY_SECONDS,
) -> None:
    """Atomic rename with bounded, jittered retry on Windows PermissionError.

    Two collision sources on Windows: a concurrent writer's in-flight rename
    briefly locking the destination, and — the dominant one under same-symbol
    co-run — a concurrent READER holding the destination open for the whole
    parquet read (see the budget rationale on the module constants).

    Re-raises non-``PermissionError`` ``OSError`` immediately (don't mask
    real permission/path bugs). Raises :class:`CacheWriteContentionError`
    (chaining the last ``PermissionError``) once all attempts are exhausted,
    so callers can distinguish exhausted contention from everything else.
    """
    last_err: PermissionError | None = None
    for attempt in range(max_attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            last_err = exc
            if attempt < max_attempts - 1:
                delay = base_delay * 2**attempt
                time.sleep(delay * (1.0 + random.uniform(0.0, _RENAME_JITTER_FRACTION)))
    assert last_err is not None
    msg = (
        f"cache rename onto {dst} stayed blocked (PermissionError) through "
        f"{max_attempts} attempts; a co-running process is holding the file open"
    )
    raise CacheWriteContentionError(msg) from last_err


def _read_parquet_with_retry(
    path: Path, *, max_attempts: int = 4, base_delay: float = 0.01
) -> pd.DataFrame:
    """Read a parquet with bounded retry on Windows PermissionError.

    The symmetric counterpart to ``_replace_with_retry``. A concurrent writer's
    ``os.replace`` briefly holds the destination locked on Windows; a sibling
    reader opening that path during the rename window gets ``PermissionError``
    [Errno 13]. Under same-symbol co-run (multiple runners reading and writing
    one symbol's parquet) this collision is otherwise fatal — it killed a runner
    on the shared 5Min SPY cache during the 2026-06-17 soak. The retry burst
    (~150 ms total) lets the rename settle before the read proceeds.

    Re-raises non-``PermissionError`` ``OSError`` immediately (don't mask real
    permission/path bugs). Re-raises the last ``PermissionError`` if all
    attempts are exhausted.
    """
    last_err: PermissionError | None = None
    for attempt in range(max_attempts):
        try:
            return pd.read_parquet(path)
        except PermissionError as exc:
            last_err = exc
            time.sleep(base_delay * 2**attempt)
    assert last_err is not None
    raise last_err


class ParquetCache:
    """File-based Parquet cache for market data bars."""

    def __init__(self, cache_dir: Path, version: str = "v1") -> None:
        self._cache_dir = Path(cache_dir)
        self._version = version
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: Timeframe) -> Path:
        """Return the Parquet file path for a symbol/timeframe pair."""
        if "/" in symbol or "\\" in symbol:
            raise ValueError(
                f"symbol {symbol!r} contains a path separator; ParquetCache has no "
                f"filesystem-safe key for such symbols (crypto cache-key support is deferred)"
            )
        dir_path = self._cache_dir / self._version / timeframe.value
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path / f"{symbol.upper()}.parquet"

    def read(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame | None:
        """Read cached bars for a symbol/timeframe. Returns None if no cache exists.

        A 0-byte parquet file (left over from a write interrupted mid-stream
        before the atomic-rename guard landed, or from any future cache layout
        regression) is treated as a cache miss with a logged warning rather
        than crashing pyarrow on the unreadable buffer. The caller's
        upstream-fetch path then re-acquires the data.
        """
        path = self._path(symbol, timeframe)
        if not path.exists():
            _logger.info(
                "cache_miss symbol=%s timeframe=%s path=%s",
                symbol.upper(),
                timeframe.value,
                path,
            )
            return None
        if path.stat().st_size == 0:
            _logger.warning(
                "cache_corrupt symbol=%s timeframe=%s path=%s reason=zero_byte_file",
                symbol.upper(),
                timeframe.value,
                path,
            )
            return None
        df = _read_parquet_with_retry(path)
        _logger.info(
            "cache_hit symbol=%s timeframe=%s rows=%d path=%s",
            symbol.upper(),
            timeframe.value,
            len(df),
            path,
        )
        return df

    def write(self, symbol: str, timeframe: Timeframe, df: pd.DataFrame) -> None:
        """Write bars to cache, replacing any existing data.

        Atomic on Windows and POSIX: write to a sibling tmp file, then
        ``os.replace`` it onto the destination. ``os.replace`` is atomic on
        the same filesystem, so the destination is never seen in a partial
        state by a concurrent reader, and a process death mid-write leaves
        only a stale temp file behind — never a 0-byte destination.

        The tmp filename is per-writer-unique (``{pid}.{uuid_hex}``) so that
        concurrent writers for the same symbol each land in their own tmp file
        and do not truncate one another's in-flight write.  On Windows, two
        concurrent writers' rename calls to the same destination can briefly
        contend; a bounded burst-retry on ``PermissionError`` resolves the
        collision (see ``_replace_with_retry``).  The existing
        ``try/except BaseException + unlink(missing_ok=True)`` cleanup is
        correct and unchanged: each writer is responsible for removing its own
        tmp on failure.
        """
        path = self._path(symbol, timeframe)
        tmp_path = path.with_suffix(f"{path.suffix}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            df.to_parquet(tmp_path, index=False)
            _replace_with_retry(tmp_path, path)
        except BaseException:
            # On any failure (including KeyboardInterrupt), discard the
            # half-written temp so a subsequent retry does not see stale state.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def get_cached_range(self, symbol: str, timeframe: Timeframe) -> tuple[date, date] | None:
        """Return the (start, end) date range of cached data. None if no cache."""
        df = self.read(symbol, timeframe)
        if df is None or df.empty:
            return None
        timestamps = pd.to_datetime(df["timestamp"])
        return timestamps.min().date(), timestamps.max().date()

    def merge(self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame) -> None:
        """Merge new data into existing cache.

        The combine step lives in :meth:`merged_view`; this method persists
        its result. On rename contention the write raises
        :class:`CacheWriteContentionError` — poll-path callers may catch that
        one error, serve :meth:`merged_view` in memory, and re-persist next
        poll; data-integrity ``ValueError`` from the combine must stay loud.
        """
        combined = self.merged_view(symbol, timeframe, new_data)
        self.write(symbol, timeframe, combined)

    def merged_view(
        self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame
    ) -> pd.DataFrame:
        """Return the frame :meth:`merge` would persist, computed without writing.

        Algorithm: load existing -> validate new_data schema -> coerce new_data
        to the existing schema -> concatenate -> deduplicate by timestamp
        (keeping the newest row) -> sort by timestamp. With no existing cache
        the new frame IS the view (mirroring ``merge``'s existing-None branch).

        Exists so the poll path can keep serving fresh bars when persistence
        is temporarily unavailable (:class:`CacheWriteContentionError`): the
        caller falls back to this in-memory view and re-persists on its next
        poll. Data-integrity errors (schema / dtype ``ValueError``) raise here
        exactly as they do in ``merge`` — this is a persistence fallback, not
        a validation bypass.

        Schema validation prevents silent downstream corruption: without it, an
        extra column in ``new_data`` widens the on-disk parquet (injecting NaN
        for existing rows), and a missing column fills the new rows with NaN —
        both propagate silently into metrics that feed the capital-readiness gate.

        Coercion strategy (prefer loud correctness over silent corruption):

        - **Extra columns** in ``new_data`` are stripped; they are not part of
          the established schema and their presence usually indicates a caller
          bug.
        - **Missing columns** in ``new_data`` cause a ``ValueError``: we cannot
          silently invent values for a column we know nothing about.  A NaN fill
          would be as wrong as omitting the column, and a loud failure at the
          merge site is far better than silent NaN propagation into indicators.
        - **Dtype / tz drift** on a shared column is reconciled *before* the
          concat.  Column-set parity is not enough: ``new_data`` can carry the
          right columns with the wrong dtypes (a numeric column arriving as
          object/string, or a tz-naive timestamp) and ``pd.concat`` would then
          silently produce a mixed/object column that reads back broken.  Where
          the coercion is unambiguous and lossless (numeric-looking strings →
          the existing numeric dtype; tz-naive timestamp → the existing tz) the
          value is coerced; where it would be lossy or ambiguous a clear,
          actionable ``ValueError`` is raised at the merge boundary instead of
          a cryptic pyarrow/pandas error deep in the write path.  A column whose
          dtype already matches is left byte-identical.
        """
        existing = self.read(symbol, timeframe)
        if existing is None:
            return new_data

        existing_cols = list(existing.columns)
        new_cols = set(new_data.columns)

        # Detect missing columns first — we cannot safely invent values.
        missing = [c for c in existing_cols if c not in new_cols]
        if missing:
            msg = (
                f"ParquetCache.merge({symbol!r}, {timeframe.value!r}): "
                f"new_data is missing column(s) {missing} that exist in the "
                f"cached frame.  Refusing to merge — a NaN fill would silently "
                f"corrupt downstream metrics.  Caller must supply all columns: "
                f"{existing_cols}"
            )
            raise ValueError(msg)

        # Strip extra columns silently — new_data may carry provider metadata
        # columns that are not part of the bar schema.
        extra = [c for c in new_data.columns if c not in existing_cols]
        if extra:
            _logger.warning(
                "cache_merge_schema_drift symbol=%s timeframe=%s extra_columns_stripped=%s",
                symbol.upper(),
                timeframe.value,
                extra,
            )
            new_data = new_data[existing_cols]

        # Reconcile per-column dtype / tz drift before concatenating. A column
        # whose dtype already matches is untouched, so the matching-schema
        # path stays byte-identical.
        new_data = self._reconcile_dtypes(symbol, timeframe, existing, new_data)

        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
        return combined.sort_values("timestamp").reset_index(drop=True)

    def _reconcile_dtypes(
        self,
        symbol: str,
        timeframe: Timeframe,
        existing: pd.DataFrame,
        new_data: pd.DataFrame,
    ) -> pd.DataFrame:
        """Align ``new_data`` column dtypes to ``existing`` before concat.

        Returns ``new_data`` (a copy only when a column needed coercion).
        Columns whose dtype already matches are left untouched so the
        matching-schema path is byte-identical to the pre-validation
        behaviour.  Raises ``ValueError`` with an actionable, merge-scoped
        message when a drift cannot be coerced losslessly.
        """
        prefix = f"ParquetCache.merge({symbol!r}, {timeframe.value!r})"
        result = new_data
        for col in existing.columns:
            exist_dtype = existing[col].dtype
            new_dtype = new_data[col].dtype
            if exist_dtype == new_dtype:
                continue  # matching dtype — leave byte-identical

            coerced = self._coerce_column(
                column=col,
                existing_series=existing[col],
                new_series=new_data[col],
                prefix=prefix,
            )
            if result is new_data:
                result = new_data.copy()
            result[col] = coerced
        return result

    @staticmethod
    def _coerce_column(
        *,
        column: str,
        existing_series: pd.Series,
        new_series: pd.Series,
        prefix: str,
    ) -> pd.Series:
        """Coerce one drifted column to the existing dtype, or raise clearly."""
        exist_dtype = existing_series.dtype
        new_dtype = new_series.dtype

        # --- timestamp / tz drift -------------------------------------------
        if isinstance(exist_dtype, pd.DatetimeTZDtype):
            if isinstance(new_dtype, pd.DatetimeTZDtype):
                # Both tz-aware but different tz → convert (lossless).
                return new_series.dt.tz_convert(exist_dtype.tz)
            if pd.api.types.is_datetime64_any_dtype(new_series):
                # tz-naive → localize to the existing tz. Milodex bars are
                # UTC by contract (see BarSet column contract); localizing a
                # naive wall-clock to that tz is the unambiguous alignment.
                return new_series.dt.tz_localize(exist_dtype.tz)
            msg = (
                f"{prefix}: column {column!r} has dtype {new_dtype} but the "
                f"cached frame stores it as tz-aware datetime {exist_dtype}. "
                f"Refusing to merge — concatenating these would silently "
                f"produce an object timestamp column. Caller must supply a "
                f"datetime column."
            )
            raise ValueError(msg)

        # --- numeric column arriving as object/string -----------------------
        if pd.api.types.is_numeric_dtype(exist_dtype) and not pd.api.types.is_numeric_dtype(
            new_dtype
        ):
            converted = pd.to_numeric(new_series, errors="coerce")
            # A non-null source value that became NaN is a lossy/ambiguous
            # conversion (e.g. "N/A") — refuse rather than inject NaN.
            introduced_nan = converted.isna() & new_series.notna()
            if introduced_nan.any():
                bad = new_series[introduced_nan].unique().tolist()
                msg = (
                    f"{prefix}: column {column!r} arrived with dtype "
                    f"{new_dtype} but the cached frame stores it as numeric "
                    f"{exist_dtype}, and value(s) {bad!r} cannot be coerced to "
                    f"a number. Refusing to merge — a silent NaN concat would "
                    f"corrupt downstream metrics."
                )
                raise ValueError(msg)
            try:
                return converted.astype(exist_dtype)
            except (ValueError, TypeError) as exc:
                msg = (
                    f"{prefix}: column {column!r} (dtype {new_dtype}) could "
                    f"not be safely cast to the cached numeric dtype "
                    f"{exist_dtype}: {exc}. Refusing to merge."
                )
                raise ValueError(msg) from exc

        # --- numeric ↔ numeric width/kind drift (e.g. int vs float) ---------
        if pd.api.types.is_numeric_dtype(exist_dtype) and pd.api.types.is_numeric_dtype(new_dtype):
            converted = new_series.astype(exist_dtype)
            # Round-trip guard: only accept a lossless cast (e.g. 103.0 int↔
            # float). A lossy narrowing must fail loudly.
            if not (converted.astype(new_dtype) == new_series).all():
                msg = (
                    f"{prefix}: column {column!r} (dtype {new_dtype}) cannot "
                    f"be cast to the cached numeric dtype {exist_dtype} "
                    f"without loss. Refusing to merge."
                )
                raise ValueError(msg)
            return converted

        # --- anything else: unsafe, refuse ----------------------------------
        msg = (
            f"{prefix}: column {column!r} has dtype {new_dtype} which is "
            f"incompatible with the cached dtype {exist_dtype}. Refusing to "
            f"merge — concatenating mismatched dtypes silently corrupts the "
            f"on-disk schema."
        )
        raise ValueError(msg)
