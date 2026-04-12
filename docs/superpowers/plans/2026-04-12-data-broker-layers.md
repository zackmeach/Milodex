# Data & Broker Layers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the foundational data acquisition and brokerage layers so the rest of Milodex (risk, strategies, backtesting, analytics, CLI) can fetch market data and execute trades through clean abstract interfaces.

**Architecture:** Two abstract base classes (`DataProvider`, `BrokerClient`) with Alpaca implementations behind each. A shared `config.py` handles credentials and environment. Market data is cached locally as Parquet files. Nothing outside the two Alpaca implementation files ever imports `alpaca-py`.

**Tech Stack:** Python 3.11+, alpaca-py, pandas, pyarrow, python-dotenv, pytest, ruff

**Spec:** `docs/superpowers/specs/2026-04-12-data-broker-layers-design.md`

---

## File Map

### New files to create:
| File | Responsibility |
|------|----------------|
| `src/milodex/config.py` | Shared config: credentials, trading mode, cache dir |
| `src/milodex/data/models.py` | `Timeframe` enum, `Bar` dataclass, `BarSet` wrapper |
| `src/milodex/data/provider.py` | `DataProvider` ABC |
| `src/milodex/data/cache.py` | `ParquetCache` — local Parquet read/write/merge |
| `src/milodex/data/alpaca_provider.py` | `AlpacaDataProvider` — Alpaca SDK implementation |
| `src/milodex/broker/models.py` | `OrderSide`, `OrderType`, `OrderStatus`, `TimeInForce`, `Order`, `Position`, `AccountInfo` |
| `src/milodex/broker/exceptions.py` | `BrokerConnectionError`, `BrokerAuthError`, `InsufficientFundsError`, `OrderRejectedError` |
| `src/milodex/broker/client.py` | `BrokerClient` ABC |
| `src/milodex/broker/alpaca_client.py` | `AlpacaBrokerClient` — Alpaca SDK implementation |
| `tests/milodex/data/test_models.py` | Tests for `Timeframe`, `Bar`, `BarSet` |
| `tests/milodex/data/test_cache.py` | Tests for `ParquetCache` |
| `tests/milodex/data/test_alpaca_provider.py` | Tests for `AlpacaDataProvider` (mocked SDK) |
| `tests/milodex/broker/test_models.py` | Tests for broker model dataclasses |
| `tests/milodex/broker/test_alpaca_client.py` | Tests for `AlpacaBrokerClient` (mocked SDK) |
| `tests/milodex/test_config.py` | Tests for config loading |
| `tests/conftest.py` | Shared fixtures: sample bars, orders, positions |
| `tests/integration/__init__.py` | Integration test package marker |
| `tests/integration/test_alpaca_smoke.py` | Integration smoke tests (manual, skip in CI) |

**Note:** `tests/milodex/data/__init__.py` and `tests/milodex/broker/__init__.py` already exist from the scaffold. No need to create them.

### Files to modify:
| File | Change |
|------|--------|
| `pyproject.toml` | Add runtime dependencies + pytest integration marker |
| `.gitignore` | Add `market_cache/` |
| `src/milodex/data/__init__.py` | Re-export public API |
| `src/milodex/broker/__init__.py` | Re-export public API |

---

## Task 1: Add Dependencies and Project Config

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: Add runtime dependencies to pyproject.toml**

Add a `[project.dependencies]` section and update pytest config:

```toml
[project]
# ... existing fields ...
dependencies = [
    "alpaca-py>=0.35.0",
    "pandas>=2.0",
    "pyarrow>=15.0",
    "python-dotenv>=1.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
markers = [
    "integration: marks tests that hit real APIs (deselect with '-m \"not integration\"')",
]
```

- [ ] **Step 2: Add market_cache/ to .gitignore**

Append to `.gitignore`:

```
# Market data cache (local Parquet files)
market_cache/
```

- [ ] **Step 3: Install updated dependencies**

Run: `pip install -e ".[dev]"`
Expected: successful install with alpaca-py, pandas, pyarrow, python-dotenv

- [ ] **Step 4: Verify install**

Run: `python -c "import alpaca; import pandas; import pyarrow; import dotenv; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore
git commit -m "feat: add runtime dependencies for data and broker layers"
```

---

## Task 2: Shared Config Module

**Files:**
- Create: `src/milodex/config.py`
- Create: `tests/milodex/test_config.py`

- [ ] **Step 1: Write failing tests for config**

```python
# tests/milodex/test_config.py
"""Tests for shared configuration."""

from pathlib import Path
from unittest.mock import patch

import pytest

from milodex.config import get_alpaca_credentials, get_cache_dir, get_trading_mode


class TestGetAlpacaCredentials:
    def test_returns_key_and_secret_from_env(self):
        with patch.dict("os.environ", {
            "ALPACA_API_KEY": "test-key",
            "ALPACA_SECRET_KEY": "test-secret",
        }):
            key, secret = get_alpaca_credentials()
            assert key == "test-key"
            assert secret == "test-secret"

    def test_raises_when_api_key_missing(self):
        with patch.dict("os.environ", {"ALPACA_SECRET_KEY": "secret"}, clear=True):
            with pytest.raises(ValueError, match="ALPACA_API_KEY"):
                get_alpaca_credentials()

    def test_raises_when_secret_key_missing(self):
        with patch.dict("os.environ", {"ALPACA_API_KEY": "key"}, clear=True):
            with pytest.raises(ValueError, match="ALPACA_SECRET_KEY"):
                get_alpaca_credentials()


class TestGetTradingMode:
    def test_returns_paper_mode(self):
        with patch.dict("os.environ", {"TRADING_MODE": "paper"}):
            assert get_trading_mode() == "paper"

    def test_returns_live_mode(self):
        with patch.dict("os.environ", {"TRADING_MODE": "live"}):
            assert get_trading_mode() == "live"

    def test_defaults_to_paper_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            assert get_trading_mode() == "paper"

    def test_raises_on_invalid_mode(self):
        with patch.dict("os.environ", {"TRADING_MODE": "yolo"}):
            with pytest.raises(ValueError, match="TRADING_MODE"):
                get_trading_mode()


class TestGetCacheDir:
    def test_returns_path_object(self):
        result = get_cache_dir()
        assert isinstance(result, Path)

    def test_default_is_market_cache(self):
        result = get_cache_dir()
        assert result.name == "market_cache"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/milodex/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_alpaca_credentials'`

- [ ] **Step 3: Implement config module**

```python
# src/milodex/config.py
"""Shared configuration for Milodex.

Loads environment variables from .env and provides typed accessors
for credentials, trading mode, and file paths. Single source of truth —
no other module reads .env or os.environ for these values.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (if it exists)
load_dotenv()


def get_alpaca_credentials() -> tuple[str, str]:
    """Load ALPACA_API_KEY and ALPACA_SECRET_KEY from environment.

    Returns:
        Tuple of (api_key, secret_key).

    Raises:
        ValueError: If either key is missing or empty.
    """
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()

    if not api_key:
        raise ValueError(
            "ALPACA_API_KEY is not set. "
            "Copy .env.example to .env and fill in your Alpaca credentials."
        )
    if not secret_key:
        raise ValueError(
            "ALPACA_SECRET_KEY is not set. "
            "Copy .env.example to .env and fill in your Alpaca credentials."
        )
    return api_key, secret_key


def get_trading_mode() -> str:
    """Return 'paper' or 'live' from TRADING_MODE env var.

    Defaults to 'paper' if unset. Raises on invalid values.
    """
    mode = os.environ.get("TRADING_MODE", "paper").strip().lower()
    if mode not in ("paper", "live"):
        raise ValueError(
            f"TRADING_MODE must be 'paper' or 'live', got '{mode}'. "
            "Check your .env file."
        )
    return mode


def get_cache_dir() -> Path:
    """Return path for local market data cache.

    Default: {project_root}/market_cache/
    Override with MILODEX_CACHE_DIR env var.
    """
    override = os.environ.get("MILODEX_CACHE_DIR", "").strip()
    if override:
        return Path(override)

    # Walk up from this file to find project root (where pyproject.toml lives)
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current / "market_cache"
        current = current.parent

    # Fallback: relative to cwd
    return Path.cwd() / "market_cache"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/milodex/test_config.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Lint**

Run: `ruff check src/milodex/config.py tests/milodex/test_config.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/milodex/config.py tests/milodex/test_config.py
git commit -m "feat: add shared config module for credentials and trading mode"
```

---

## Task 3: Data Models (Timeframe, Bar, BarSet)

**Files:**
- Create: `src/milodex/data/models.py`
- Create: `tests/milodex/data/test_models.py`

- [ ] **Step 1: Write failing tests for data models**

```python
# tests/milodex/data/test_models.py
"""Tests for data layer models."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from milodex.data.models import Bar, BarSet, Timeframe


class TestTimeframe:
    def test_has_expected_members(self):
        assert Timeframe.MINUTE_1.value == "1Min"
        assert Timeframe.MINUTE_5.value == "5Min"
        assert Timeframe.MINUTE_15.value == "15Min"
        assert Timeframe.HOUR_1.value == "1Hour"
        assert Timeframe.DAY_1.value == "1Day"

    def test_all_members_are_strings(self):
        for member in Timeframe:
            assert isinstance(member.value, str)


class TestBar:
    def test_create_bar(self):
        ts = datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc)
        bar = Bar(
            timestamp=ts,
            open=150.0,
            high=152.0,
            low=149.5,
            close=151.0,
            volume=1000000,
        )
        assert bar.timestamp == ts
        assert bar.open == 150.0
        assert bar.close == 151.0
        assert bar.volume == 1000000
        assert bar.vwap is None

    def test_bar_with_vwap(self):
        ts = datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc)
        bar = Bar(
            timestamp=ts,
            open=150.0,
            high=152.0,
            low=149.5,
            close=151.0,
            volume=1000000,
            vwap=150.8,
        )
        assert bar.vwap == 150.8


class TestBarSet:
    @pytest.fixture()
    def sample_df(self):
        return pd.DataFrame({
            "timestamp": pd.to_datetime(
                ["2025-01-13", "2025-01-14", "2025-01-15"], utc=True
            ),
            "open": [148.0, 149.0, 150.0],
            "high": [149.0, 150.0, 152.0],
            "low": [147.0, 148.5, 149.5],
            "close": [148.5, 149.5, 151.0],
            "volume": [900000, 950000, 1000000],
            "vwap": [148.3, 149.2, 150.8],
        })

    def test_create_from_dataframe(self, sample_df):
        barset = BarSet(sample_df)
        assert len(barset) == 3

    def test_to_dataframe_returns_copy(self, sample_df):
        barset = BarSet(sample_df)
        df = barset.to_dataframe()
        assert isinstance(df, pd.DataFrame)
        # Modifying the copy should not affect the original
        df.iloc[0, df.columns.get_loc("close")] = 999.0
        assert barset.to_dataframe().iloc[0]["close"] != 999.0

    def test_latest_returns_bar(self, sample_df):
        barset = BarSet(sample_df)
        bar = barset.latest()
        assert isinstance(bar, Bar)
        assert bar.close == 151.0

    def test_latest_raises_on_empty(self):
        empty_df = pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume", "vwap"]
        )
        barset = BarSet(empty_df)
        with pytest.raises(ValueError, match="empty"):
            barset.latest()

    def test_validates_required_columns(self):
        bad_df = pd.DataFrame({"timestamp": [], "open": [], "close": []})
        with pytest.raises(ValueError, match="column"):
            BarSet(bad_df)

    def test_len(self, sample_df):
        barset = BarSet(sample_df)
        assert len(barset) == 3

    def test_vwap_nullable(self, sample_df):
        sample_df["vwap"] = [None, None, None]
        barset = BarSet(sample_df)
        assert len(barset) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/milodex/data/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Bar'`

- [ ] **Step 3: Implement data models**

```python
# src/milodex/data/models.py
"""Standardized data types for market data.

These types are the contract between the data layer and the rest of the system.
No Alpaca-specific types leak past this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

import pandas as pd

# Required columns in every BarSet DataFrame
BARSET_REQUIRED_COLUMNS = frozenset({
    "timestamp", "open", "high", "low", "close", "volume",
})
# vwap is always present but nullable
BARSET_ALL_COLUMNS = BARSET_REQUIRED_COLUMNS | {"vwap"}


class Timeframe(Enum):
    """Supported bar timeframes.

    Values match Alpaca's naming convention for easy translation,
    but consumers should use the enum members, not the string values.
    """

    MINUTE_1 = "1Min"
    MINUTE_5 = "5Min"
    MINUTE_15 = "15Min"
    HOUR_1 = "1Hour"
    DAY_1 = "1Day"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None


class BarSet:
    """A collection of OHLCV bars backed by a pandas DataFrame.

    Column contract:
    - Always present: timestamp, open, high, low, close, volume, vwap
    - Price columns: float64
    - volume: int64
    - timestamp: datetime64[ns, UTC]
    - vwap: float64, nullable (may contain NaN)
    """

    def __init__(self, df: pd.DataFrame) -> None:
        missing = BARSET_REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(
                f"BarSet missing required column(s): {', '.join(sorted(missing))}"
            )

        # Ensure vwap column exists (nullable)
        if "vwap" not in df.columns:
            df = df.copy()
            df["vwap"] = pd.NA

        self._df = df.copy()

    def to_dataframe(self) -> pd.DataFrame:
        """Return a copy of the underlying DataFrame."""
        return self._df.copy()

    def latest(self) -> Bar:
        """Return the most recent bar.

        Raises:
            ValueError: If the BarSet is empty.
        """
        if self._df.empty:
            raise ValueError("Cannot get latest bar from an empty BarSet.")

        row = self._df.iloc[-1]
        vwap_val = row["vwap"] if pd.notna(row["vwap"]) else None
        return Bar(
            timestamp=row["timestamp"],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
            vwap=float(vwap_val) if vwap_val is not None else None,
        )

    def __len__(self) -> int:
        return len(self._df)

    def __repr__(self) -> str:
        return f"BarSet({len(self._df)} bars)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/milodex/data/test_models.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Lint**

Run: `ruff check src/milodex/data/models.py tests/milodex/data/test_models.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/milodex/data/models.py tests/milodex/data/test_models.py
git commit -m "feat: add data models — Timeframe, Bar, BarSet"
```

---

## Task 4: DataProvider ABC

**Files:**
- Create: `src/milodex/data/provider.py`

- [ ] **Step 1: Write the DataProvider abstract base class**

```python
# src/milodex/data/provider.py
"""Abstract interface for market data providers.

All data consumers (strategies, backtesting, analytics) depend on this
interface — never on a specific provider implementation. To add a new
data source, implement this ABC without changing any consuming code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from milodex.data.models import Bar, BarSet, Timeframe


class DataProvider(ABC):
    """Abstract market data provider."""

    @abstractmethod
    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, BarSet]:
        """Fetch OHLCV bars for one or more symbols.

        Args:
            symbols: List of ticker symbols (e.g., ["AAPL", "SPY"]).
            timeframe: Bar timeframe (e.g., Timeframe.DAY_1).
            start: Start date (inclusive).
            end: End date (inclusive).

        Returns:
            Dict mapping each symbol to its BarSet.
        """

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Bar:
        """Fetch the most recent bar for a symbol.

        When the market is closed, returns the last available bar
        (e.g., Friday's close on a Saturday). Does not raise.
        Callers should check is_market_open() if they need to
        distinguish "latest" from "live."
        """

    @abstractmethod
    def get_tradeable_assets(self) -> list[str]:
        """Return ticker symbols available for trading.

        Returns the full broker-eligible universe with no filtering.
        Strategy-level universe filtering is the strategy layer's
        responsibility.
        """
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python -c "from milodex.data.provider import DataProvider; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Lint**

Run: `ruff check src/milodex/data/provider.py`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add src/milodex/data/provider.py
git commit -m "feat: add DataProvider ABC"
```

---

## Task 5: ParquetCache

**Files:**
- Create: `src/milodex/data/cache.py`
- Create: `tests/milodex/data/test_cache.py`

- [ ] **Step 1: Write failing tests for cache**

```python
# tests/milodex/data/test_cache.py
"""Tests for ParquetCache."""

from datetime import date

import pandas as pd
import pytest

from milodex.data.cache import ParquetCache
from milodex.data.models import Timeframe


@pytest.fixture()
def cache_dir(tmp_path):
    return tmp_path / "market_cache"


@pytest.fixture()
def cache(cache_dir):
    return ParquetCache(cache_dir)


@pytest.fixture()
def sample_df():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(
            ["2025-01-13", "2025-01-14", "2025-01-15"], utc=True
        ),
        "open": [148.0, 149.0, 150.0],
        "high": [149.0, 150.0, 152.0],
        "low": [147.0, 148.5, 149.5],
        "close": [148.5, 149.5, 151.0],
        "volume": [900000, 950000, 1000000],
        "vwap": [148.3, 149.2, 150.8],
    })


class TestParquetCache:
    def test_creates_directory_on_init(self, cache, cache_dir):
        assert cache_dir.exists()

    def test_read_returns_none_for_empty_cache(self, cache):
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert result is None

    def test_write_and_read_roundtrip(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert result is not None
        assert len(result) == 3
        assert list(result.columns) == list(sample_df.columns)

    def test_get_cached_range_returns_none_for_empty(self, cache):
        result = cache.get_cached_range("AAPL", Timeframe.DAY_1)
        assert result is None

    def test_get_cached_range_returns_min_max_dates(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        start, end = cache.get_cached_range("AAPL", Timeframe.DAY_1)
        assert start == date(2025, 1, 13)
        assert end == date(2025, 1, 15)

    def test_merge_appends_new_data(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)

        new_data = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-01-16", "2025-01-17"], utc=True),
            "open": [151.0, 152.0],
            "high": [153.0, 154.0],
            "low": [150.5, 151.5],
            "close": [152.0, 153.0],
            "volume": [1100000, 1200000],
            "vwap": [151.5, 152.5],
        })
        cache.merge("AAPL", Timeframe.DAY_1, new_data)

        result = cache.read("AAPL", Timeframe.DAY_1)
        assert len(result) == 5

    def test_merge_deduplicates_by_timestamp(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)

        # Overlapping data — Jan 15 is a duplicate
        overlap = pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-01-15", "2025-01-16"], utc=True),
            "open": [150.0, 151.0],
            "high": [152.0, 153.0],
            "low": [149.5, 150.5],
            "close": [151.0, 152.0],
            "volume": [1000000, 1100000],
            "vwap": [150.8, 151.5],
        })
        cache.merge("AAPL", Timeframe.DAY_1, overlap)

        result = cache.read("AAPL", Timeframe.DAY_1)
        assert len(result) == 4  # 3 original + 1 new, not 5

    def test_merge_into_empty_cache(self, cache, sample_df):
        cache.merge("AAPL", Timeframe.DAY_1, sample_df)
        result = cache.read("AAPL", Timeframe.DAY_1)
        assert len(result) == 3

    def test_different_timeframes_are_separate(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        assert cache.read("AAPL", Timeframe.HOUR_1) is None

    def test_different_symbols_are_separate(self, cache, sample_df):
        cache.write("AAPL", Timeframe.DAY_1, sample_df)
        assert cache.read("SPY", Timeframe.DAY_1) is None

    def test_merge_fills_gap_in_middle(self, cache):
        """Cache has Jan 13-15 and Jan 20-22. Fill Jan 16-19."""
        early = pd.DataFrame({
            "timestamp": pd.to_datetime(
                ["2025-01-13", "2025-01-14", "2025-01-15"], utc=True
            ),
            "open": [148.0, 149.0, 150.0],
            "high": [149.0, 150.0, 152.0],
            "low": [147.0, 148.5, 149.5],
            "close": [148.5, 149.5, 151.0],
            "volume": [900000, 950000, 1000000],
            "vwap": [148.3, 149.2, 150.8],
        })
        late = pd.DataFrame({
            "timestamp": pd.to_datetime(
                ["2025-01-20", "2025-01-21", "2025-01-22"], utc=True
            ),
            "open": [153.0, 154.0, 155.0],
            "high": [154.0, 155.0, 156.0],
            "low": [152.0, 153.0, 154.0],
            "close": [153.5, 154.5, 155.5],
            "volume": [1100000, 1200000, 1300000],
            "vwap": [153.2, 154.2, 155.2],
        })
        cache.write("AAPL", Timeframe.DAY_1, pd.concat([early, late]))

        # Fill the gap
        middle = pd.DataFrame({
            "timestamp": pd.to_datetime(
                ["2025-01-16", "2025-01-17"], utc=True
            ),
            "open": [151.0, 152.0],
            "high": [152.0, 153.0],
            "low": [150.0, 151.0],
            "close": [151.5, 152.5],
            "volume": [1050000, 1100000],
            "vwap": [151.2, 152.2],
        })
        cache.merge("AAPL", Timeframe.DAY_1, middle)

        result = cache.read("AAPL", Timeframe.DAY_1)
        assert len(result) == 8  # 3 + 2 + 3
        # Verify sorted order
        timestamps = pd.to_datetime(result["timestamp"])
        assert timestamps.is_monotonic_increasing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/milodex/data/test_cache.py -v`
Expected: FAIL — `ImportError: cannot import name 'ParquetCache'`

- [ ] **Step 3: Implement ParquetCache**

```python
# src/milodex/data/cache.py
"""Local Parquet cache for market data.

Stores OHLCV bars as Parquet files organized by timeframe and symbol.
Layout: {cache_dir}/{timeframe_value}/{SYMBOL}.parquet

The cache is append-only for historical data. Today's bar is always
considered stale (re-fetched) since the market may still be open.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from milodex.data.models import Timeframe


class ParquetCache:
    """File-based Parquet cache for market data bars."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: Timeframe) -> Path:
        """Return the Parquet file path for a symbol/timeframe pair."""
        dir_path = self._cache_dir / timeframe.value
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path / f"{symbol.upper()}.parquet"

    def read(self, symbol: str, timeframe: Timeframe) -> pd.DataFrame | None:
        """Read cached bars for a symbol/timeframe.

        Returns None if no cache file exists.
        """
        path = self._path(symbol, timeframe)
        if not path.exists():
            return None
        return pd.read_parquet(path)

    def write(self, symbol: str, timeframe: Timeframe, df: pd.DataFrame) -> None:
        """Write bars to cache, replacing any existing data."""
        path = self._path(symbol, timeframe)
        df.to_parquet(path, index=False)

    def get_cached_range(
        self, symbol: str, timeframe: Timeframe
    ) -> tuple[date, date] | None:
        """Return the (start, end) date range of cached data.

        Returns None if no cache exists for this symbol/timeframe.
        """
        df = self.read(symbol, timeframe)
        if df is None or df.empty:
            return None
        timestamps = pd.to_datetime(df["timestamp"])
        return timestamps.min().date(), timestamps.max().date()

    def merge(
        self, symbol: str, timeframe: Timeframe, new_data: pd.DataFrame
    ) -> None:
        """Merge new data into existing cache.

        Algorithm: load existing → concatenate → deduplicate by timestamp
        (keeping the newest row) → sort by timestamp → write back.
        """
        existing = self.read(symbol, timeframe)
        if existing is None:
            self.write(symbol, timeframe, new_data)
            return

        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
        combined = combined.sort_values("timestamp").reset_index(drop=True)
        self.write(symbol, timeframe, combined)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/milodex/data/test_cache.py -v`
Expected: all 10 tests PASS

- [ ] **Step 5: Lint**

Run: `ruff check src/milodex/data/cache.py tests/milodex/data/test_cache.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/milodex/data/cache.py tests/milodex/data/test_cache.py
git commit -m "feat: add ParquetCache for local market data storage"
```

---

## Task 6: AlpacaDataProvider

**Files:**
- Create: `src/milodex/data/alpaca_provider.py`
- Create: `tests/milodex/data/test_alpaca_provider.py`
- Modify: `src/milodex/data/__init__.py`

- [ ] **Step 1: Write failing tests for AlpacaDataProvider**

```python
# tests/milodex/data/test_alpaca_provider.py
"""Tests for AlpacaDataProvider.

All tests mock the Alpaca SDK — no real API calls.
"""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from milodex.data.alpaca_provider import AlpacaDataProvider
from milodex.data.models import Bar, BarSet, Timeframe


@pytest.fixture()
def mock_alpaca_bar():
    """Create a mock Alpaca bar object."""
    bar = MagicMock()
    bar.timestamp = datetime(2025, 1, 15, 5, 0, tzinfo=timezone.utc)
    bar.open = 150.0
    bar.high = 152.0
    bar.low = 149.5
    bar.close = 151.0
    bar.volume = 1000000
    bar.vwap = 150.8
    return bar


@pytest.fixture()
def provider(tmp_path):
    """Create an AlpacaDataProvider with mocked credentials and cache."""
    with patch("milodex.data.alpaca_provider.get_alpaca_credentials") as mock_creds:
        mock_creds.return_value = ("test-key", "test-secret")
        with patch("milodex.data.alpaca_provider.get_cache_dir") as mock_cache:
            mock_cache.return_value = tmp_path / "market_cache"
            with patch("milodex.data.alpaca_provider.StockHistoricalDataClient"):
                yield AlpacaDataProvider()


class TestGetBars:
    def test_returns_dict_of_barsets(self, provider, mock_alpaca_bar):
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [mock_alpaca_bar]}
        )
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert "AAPL" in result
        assert isinstance(result["AAPL"], BarSet)
        assert len(result["AAPL"]) == 1

    def test_returns_empty_barset_for_unknown_symbol(self, provider):
        provider._client.get_stock_bars.return_value = MagicMock(data={})
        result = provider.get_bars(
            symbols=["ZZZZZ"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert "ZZZZZ" in result
        assert len(result["ZZZZZ"]) == 0


class TestGetLatestBar:
    def test_returns_bar(self, provider, mock_alpaca_bar):
        provider._client.get_stock_latest_bar.return_value = {"AAPL": mock_alpaca_bar}
        result = provider.get_latest_bar("AAPL")
        assert isinstance(result, Bar)
        assert result.close == 151.0
        assert result.vwap == 150.8


class TestGetBarsCaching:
    def test_cache_hit_avoids_api_call(self, provider, mock_alpaca_bar):
        """When cache fully covers the request and end < today, no API call."""
        # First call populates cache
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [mock_alpaca_bar]}
        )
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        call_count_after_first = provider._client.get_stock_bars.call_count

        # Second call should use cache (end date is in the past)
        result = provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=date(2025, 1, 15),
            end=date(2025, 1, 15),
        )
        assert provider._client.get_stock_bars.call_count == call_count_after_first
        assert "AAPL" in result

    def test_today_always_refetched(self, provider, mock_alpaca_bar):
        """Bars for today should always hit the API even if cached."""
        today = datetime.now(tz=timezone.utc).date()
        mock_alpaca_bar.timestamp = datetime(
            today.year, today.month, today.day, 14, 30, tzinfo=timezone.utc
        )
        provider._client.get_stock_bars.return_value = MagicMock(
            data={"AAPL": [mock_alpaca_bar]}
        )
        # First call
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=today,
            end=today,
        )
        first_count = provider._client.get_stock_bars.call_count

        # Second call — today should still hit API
        provider.get_bars(
            symbols=["AAPL"],
            timeframe=Timeframe.DAY_1,
            start=today,
            end=today,
        )
        assert provider._client.get_stock_bars.call_count > first_count


class TestGetTradeableAssets:
    def test_returns_list_of_symbols(self, provider):
        asset1 = MagicMock()
        asset1.symbol = "AAPL"
        asset1.tradable = True
        asset1.status = "active"

        asset2 = MagicMock()
        asset2.symbol = "GOOG"
        asset2.tradable = True
        asset2.status = "active"

        # Untradeable asset should be filtered out
        asset3 = MagicMock()
        asset3.symbol = "DELISTED"
        asset3.tradable = False
        asset3.status = "inactive"

        provider._trading_client = MagicMock()
        provider._trading_client.get_all_assets.return_value = [asset1, asset2, asset3]

        result = provider.get_tradeable_assets()
        assert "AAPL" in result
        assert "GOOG" in result
        assert "DELISTED" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/milodex/data/test_alpaca_provider.py -v`
Expected: FAIL — `ImportError: cannot import name 'AlpacaDataProvider'`

- [ ] **Step 3: Implement AlpacaDataProvider**

```python
# src/milodex/data/alpaca_provider.py
"""Alpaca implementation of DataProvider.

This is the ONLY file in the data layer that imports alpaca-py.
All Alpaca-specific types are translated to milodex models before
being returned to callers.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestBarRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.trading.client import TradingClient

from milodex.config import get_alpaca_credentials, get_cache_dir, get_trading_mode
from milodex.data.cache import ParquetCache
from milodex.data.models import Bar, BarSet, Timeframe
from milodex.data.provider import DataProvider

# Map our Timeframe enum to Alpaca's TimeFrame objects
_TIMEFRAME_MAP: dict[Timeframe, TimeFrame] = {
    Timeframe.MINUTE_1: TimeFrame(1, TimeFrameUnit.Minute),
    Timeframe.MINUTE_5: TimeFrame(5, TimeFrameUnit.Minute),
    Timeframe.MINUTE_15: TimeFrame(15, TimeFrameUnit.Minute),
    Timeframe.HOUR_1: TimeFrame(1, TimeFrameUnit.Hour),
    Timeframe.DAY_1: TimeFrame(1, TimeFrameUnit.Day),
}


class AlpacaDataProvider(DataProvider):
    """Market data provider backed by Alpaca's API.

    Uses StockHistoricalDataClient for bar data and TradingClient
    for asset discovery. Caches fetched data locally as Parquet files.
    """

    def __init__(self) -> None:
        api_key, secret_key = get_alpaca_credentials()
        self._client = StockHistoricalDataClient(api_key, secret_key)
        paper = get_trading_mode() == "paper"
        self._trading_client = TradingClient(api_key, secret_key, paper=paper)
        self._cache = ParquetCache(get_cache_dir())

    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, BarSet]:
        """Fetch OHLCV bars, using cache where available.

        Cache strategy:
        - If cache fully covers the range and end < today, use cache only.
        - Otherwise, identify missing date ranges and fetch only those.
        - Today's date is always re-fetched (market may still be open).
        - Merge fetched data into cache for future use.
        """
        alpaca_tf = _TIMEFRAME_MAP[timeframe]
        today = datetime.now(tz=timezone.utc).date()
        result: dict[str, BarSet] = {}

        for symbol in symbols:
            cached_df = self._cache.read(symbol, timeframe)
            cached_range = self._cache.get_cached_range(symbol, timeframe)

            # Full cache hit: range covered and not requesting today
            if (
                cached_range is not None
                and cached_df is not None
                and cached_range[0] <= start
                and cached_range[1] >= end
                and end < today
            ):
                ts = pd.to_datetime(cached_df["timestamp"])
                mask = (ts.dt.date >= start) & (ts.dt.date <= end)
                result[symbol] = BarSet(cached_df[mask].reset_index(drop=True))
                continue

            # Determine what ranges to fetch from Alpaca
            ranges_to_fetch: list[tuple[date, date]] = []
            if cached_range is None or cached_df is None:
                # No cache — fetch everything
                ranges_to_fetch.append((start, end))
            else:
                cache_start, cache_end = cached_range
                cached_dates = set(
                    pd.to_datetime(cached_df["timestamp"]).dt.date
                )
                # Before cache start
                if start < cache_start:
                    ranges_to_fetch.append((start, min(end, cache_start)))
                # After cache end (or today needs re-fetch)
                if end > cache_end or end >= today:
                    fetch_from = max(start, cache_end)
                    ranges_to_fetch.append((fetch_from, end))
                # Gaps in the middle: check for missing dates in range
                if start >= cache_start and end <= cache_end:
                    from datetime import timedelta

                    check = max(start, cache_start)
                    gap_start = None
                    while check <= min(end, cache_end):
                        if check not in cached_dates:
                            if gap_start is None:
                                gap_start = check
                        elif gap_start is not None:
                            ranges_to_fetch.append((gap_start, check))
                            gap_start = None
                        check += timedelta(days=1)
                    if gap_start is not None:
                        ranges_to_fetch.append((gap_start, check))

            # Fetch each missing range from Alpaca
            all_new_dfs: list[pd.DataFrame] = []
            for fetch_start, fetch_end in ranges_to_fetch:
                request = StockBarsRequest(
                    symbol_or_symbols=symbol,
                    timeframe=alpaca_tf,
                    start=datetime(
                        fetch_start.year, fetch_start.month, fetch_start.day,
                        tzinfo=timezone.utc,
                    ),
                    end=datetime(
                        fetch_end.year, fetch_end.month, fetch_end.day,
                        23, 59, 59, tzinfo=timezone.utc,
                    ),
                )
                response = self._client.get_stock_bars(request)
                bars_data = response.data.get(symbol, [])
                if bars_data:
                    df = pd.DataFrame([
                        {
                            "timestamp": b.timestamp,
                            "open": float(b.open),
                            "high": float(b.high),
                            "low": float(b.low),
                            "close": float(b.close),
                            "volume": int(b.volume),
                            "vwap": float(b.vwap) if b.vwap else None,
                        }
                        for b in bars_data
                    ])
                    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                    all_new_dfs.append(df)

            # Merge new data into cache
            if all_new_dfs:
                new_data = pd.concat(all_new_dfs, ignore_index=True)
                self._cache.merge(symbol, timeframe, new_data)

            # Read full cache and slice to requested range
            full_cache = self._cache.read(symbol, timeframe)
            if full_cache is not None and not full_cache.empty:
                ts = pd.to_datetime(full_cache["timestamp"])
                mask = (ts.dt.date >= start) & (ts.dt.date <= end)
                result[symbol] = BarSet(full_cache[mask].reset_index(drop=True))
            else:
                result[symbol] = BarSet(
                    pd.DataFrame(
                        columns=[
                            "timestamp", "open", "high", "low",
                            "close", "volume", "vwap",
                        ]
                    )
                )

        return result

    def get_latest_bar(self, symbol: str) -> Bar:
        """Fetch the most recent bar from Alpaca."""
        response = self._client.get_stock_latest_bar(
            StockLatestBarRequest(symbol_or_symbols=symbol)
        )
        alpaca_bar = response[symbol]
        return Bar(
            timestamp=alpaca_bar.timestamp,
            open=float(alpaca_bar.open),
            high=float(alpaca_bar.high),
            low=float(alpaca_bar.low),
            close=float(alpaca_bar.close),
            volume=int(alpaca_bar.volume),
            vwap=float(alpaca_bar.vwap) if alpaca_bar.vwap else None,
        )

    def get_tradeable_assets(self) -> list[str]:
        """Return all tradeable symbols from Alpaca."""
        assets = self._trading_client.get_all_assets()
        return [
            a.symbol for a in assets
            if a.tradable and str(
                a.status.value if hasattr(a.status, "value") else a.status
            ) == "active"
        ]
```

- [ ] **Step 4: Update data __init__.py with re-exports**

```python
# src/milodex/data/__init__.py
"""Market data acquisition and storage.

Handles fetching, caching, and serving OHLCV market data via a pluggable
provider interface. Phase one uses Alpaca as the sole data source.
The interface supports adding alternative providers (e.g., Yahoo Finance)
without changing consuming code.
"""

from milodex.data.models import Bar, BarSet, Timeframe
from milodex.data.provider import DataProvider

__all__ = ["Bar", "BarSet", "DataProvider", "Timeframe"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/milodex/data/test_alpaca_provider.py -v`
Expected: all 4 tests PASS

- [ ] **Step 6: Run all data tests together**

Run: `pytest tests/milodex/data/ -v`
Expected: all data tests PASS (models + cache + provider)

- [ ] **Step 7: Lint**

Run: `ruff check src/milodex/data/ tests/milodex/data/`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add src/milodex/data/ tests/milodex/data/
git commit -m "feat: add AlpacaDataProvider with cache integration"
```

---

## Task 7: Broker Models

**Files:**
- Create: `src/milodex/broker/models.py`
- Create: `tests/milodex/broker/test_models.py`

- [ ] **Step 1: Write failing tests for broker models**

```python
# tests/milodex/broker/test_models.py
"""Tests for broker layer models."""

from datetime import datetime, timezone

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)


class TestEnums:
    def test_order_side_members(self):
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_order_type_members(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.STOP.value == "stop"
        assert OrderType.STOP_LIMIT.value == "stop_limit"

    def test_order_status_members(self):
        assert OrderStatus.PENDING.value == "pending"
        assert OrderStatus.FILLED.value == "filled"
        assert OrderStatus.PARTIALLY_FILLED.value == "partially_filled"
        assert OrderStatus.CANCELLED.value == "cancelled"
        assert OrderStatus.REJECTED.value == "rejected"

    def test_time_in_force_members(self):
        assert TimeInForce.DAY.value == "day"
        assert TimeInForce.GTC.value == "gtc"


class TestOrder:
    def test_create_market_order(self):
        now = datetime.now(tz=timezone.utc)
        order = Order(
            id="order-123",
            symbol="AAPL",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=10.0,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.PENDING,
            submitted_at=now,
        )
        assert order.id == "order-123"
        assert order.limit_price is None
        assert order.stop_price is None
        assert order.filled_quantity is None
        assert order.filled_avg_price is None
        assert order.filled_at is None

    def test_create_limit_order(self):
        now = datetime.now(tz=timezone.utc)
        order = Order(
            id="order-456",
            symbol="SPY",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            quantity=5.0,
            limit_price=450.50,
            time_in_force=TimeInForce.GTC,
            status=OrderStatus.FILLED,
            submitted_at=now,
            filled_quantity=5.0,
            filled_avg_price=450.45,
            filled_at=now,
        )
        assert order.limit_price == 450.50
        assert order.filled_quantity == 5.0


class TestPosition:
    def test_create_position(self):
        pos = Position(
            symbol="AAPL",
            quantity=10.0,
            avg_entry_price=150.0,
            current_price=155.0,
            market_value=1550.0,
            unrealized_pnl=50.0,
            unrealized_pnl_pct=0.0333,
        )
        assert pos.symbol == "AAPL"
        assert pos.unrealized_pnl == 50.0


class TestAccountInfo:
    def test_create_account_info(self):
        acct = AccountInfo(
            equity=10000.0,
            cash=5000.0,
            buying_power=5000.0,
            portfolio_value=10000.0,
            daily_pnl=150.0,
        )
        assert acct.equity == 10000.0
        assert acct.daily_pnl == 150.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/milodex/broker/test_models.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement broker models**

```python
# src/milodex/broker/models.py
"""Standardized types for brokerage operations.

These types are the contract between the broker layer and the rest of
the system. No Alpaca-specific types leak past this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OrderSide(Enum):
    """Buy or sell."""

    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order execution type."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    """Lifecycle status of an order."""

    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class TimeInForce(Enum):
    """How long an order remains active."""

    DAY = "day"
    GTC = "gtc"


@dataclass(frozen=True)
class Order:
    """A trade order."""

    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    time_in_force: TimeInForce
    status: OrderStatus
    submitted_at: datetime
    limit_price: float | None = None
    stop_price: float | None = None
    filled_quantity: float | None = None
    filled_avg_price: float | None = None
    filled_at: datetime | None = None


@dataclass(frozen=True)
class Position:
    """An open position."""

    symbol: str
    quantity: float
    avg_entry_price: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


@dataclass(frozen=True)
class AccountInfo:
    """Account summary."""

    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    daily_pnl: float
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/milodex/broker/test_models.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Lint**

Run: `ruff check src/milodex/broker/models.py tests/milodex/broker/test_models.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/milodex/broker/models.py tests/milodex/broker/test_models.py
git commit -m "feat: add broker models — Order, Position, AccountInfo, enums"
```

---

## Task 8: Broker Exceptions + BrokerClient ABC

**Files:**
- Create: `src/milodex/broker/exceptions.py`
- Create: `src/milodex/broker/client.py`

- [ ] **Step 1: Implement broker exceptions**

```python
# src/milodex/broker/exceptions.py
"""Custom exceptions for the broker layer.

These exceptions are broker-agnostic — the rest of the system catches
these without knowing which broker implementation raised them.
"""


class BrokerError(Exception):
    """Base exception for all broker errors."""


class BrokerConnectionError(BrokerError):
    """Cannot reach the broker API."""


class BrokerAuthError(BrokerError):
    """Authentication failed (bad or expired credentials)."""


class InsufficientFundsError(BrokerError):
    """Not enough buying power to execute the order."""


class OrderRejectedError(BrokerError):
    """Broker rejected the order.

    Attributes:
        reason: Human-readable rejection reason from the broker.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Order rejected: {reason}")
```

- [ ] **Step 2: Implement BrokerClient ABC**

```python
# src/milodex/broker/client.py
"""Abstract interface for broker clients.

All trade execution flows through this interface — never through a
specific broker implementation. The risk layer, strategies, and CLI
depend on this ABC. To add a new broker, implement this without
changing any consuming code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderType,
    Position,
    TimeInForce,
)


class BrokerClient(ABC):
    """Abstract broker client."""

    @abstractmethod
    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> Order:
        """Submit an order. Returns the order with initial status.

        Raises:
            InsufficientFundsError: Not enough buying power.
            OrderRejectedError: Broker rejected the order.
            BrokerConnectionError: Cannot reach the API.
        """

    @abstractmethod
    def get_order(self, order_id: str) -> Order:
        """Get current status of an order."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successful."""

    @abstractmethod
    def cancel_all_orders(self) -> list[Order]:
        """Cancel all open orders. Used by kill switch for emergency halt."""

    @abstractmethod
    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        """Get recent orders.

        Args:
            status: Filter by "open", "closed", or "all".
            limit: Maximum number of orders to return.
        """

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Get all open positions."""

    @abstractmethod
    def get_position(self, symbol: str) -> Position | None:
        """Get position for a specific symbol, or None if not held."""

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get account summary."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if the market is currently open for trading."""
```

- [ ] **Step 3: Verify imports work**

Run: `python -c "from milodex.broker.client import BrokerClient; from milodex.broker.exceptions import BrokerConnectionError; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Lint**

Run: `ruff check src/milodex/broker/exceptions.py src/milodex/broker/client.py`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add src/milodex/broker/exceptions.py src/milodex/broker/client.py
git commit -m "feat: add BrokerClient ABC and broker exceptions"
```

---

## Task 9: AlpacaBrokerClient

**Files:**
- Create: `src/milodex/broker/alpaca_client.py`
- Create: `tests/milodex/broker/test_alpaca_client.py`
- Modify: `src/milodex/broker/__init__.py`

- [ ] **Step 1: Write failing tests for AlpacaBrokerClient**

```python
# tests/milodex/broker/test_alpaca_client.py
"""Tests for AlpacaBrokerClient.

All tests mock the Alpaca SDK — no real API calls.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.broker.exceptions import (
    BrokerAuthError,
    BrokerConnectionError,
    InsufficientFundsError,
    OrderRejectedError,
)
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)


@pytest.fixture()
def client():
    """Create an AlpacaBrokerClient with mocked credentials."""
    with patch("milodex.broker.alpaca_client.get_alpaca_credentials") as mock_creds:
        mock_creds.return_value = ("test-key", "test-secret")
        with patch("milodex.broker.alpaca_client.get_trading_mode") as mock_mode:
            mock_mode.return_value = "paper"
            with patch("milodex.broker.alpaca_client.TradingClient") as mock_cls:
                instance = AlpacaBrokerClient()
                instance._client = mock_cls.return_value
                yield instance


def _mock_alpaca_order(**overrides):
    """Create a mock Alpaca order object."""
    order = MagicMock()
    order.id = overrides.get("id", "order-abc-123")
    order.symbol = overrides.get("symbol", "AAPL")
    order.side = overrides.get("side", "buy")
    order.type = overrides.get("type", "market")
    order.qty = overrides.get("qty", "10")
    order.time_in_force = overrides.get("time_in_force", "day")
    order.status = overrides.get("status", "new")
    order.submitted_at = overrides.get(
        "submitted_at", datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc)
    )
    order.limit_price = overrides.get("limit_price", None)
    order.stop_price = overrides.get("stop_price", None)
    order.filled_qty = overrides.get("filled_qty", None)
    order.filled_avg_price = overrides.get("filled_avg_price", None)
    order.filled_at = overrides.get("filled_at", None)
    return order


class TestSubmitOrder:
    def test_submit_market_order(self, client):
        client._client.submit_order.return_value = _mock_alpaca_order()
        result = client.submit_order("AAPL", OrderSide.BUY, 10.0)
        assert isinstance(result, Order)
        assert result.symbol == "AAPL"
        assert result.side == OrderSide.BUY
        assert result.status == OrderStatus.PENDING

    def test_submit_limit_order(self, client):
        client._client.submit_order.return_value = _mock_alpaca_order(
            type="limit", limit_price="150.50"
        )
        result = client.submit_order(
            "AAPL", OrderSide.BUY, 10.0,
            order_type=OrderType.LIMIT, limit_price=150.50,
        )
        assert result.order_type == OrderType.LIMIT


class TestGetOrder:
    def test_get_order_by_id(self, client):
        client._client.get_order_by_id.return_value = _mock_alpaca_order(
            status="filled", filled_qty="10", filled_avg_price="151.25"
        )
        result = client.get_order("order-abc-123")
        assert isinstance(result, Order)
        assert result.status == OrderStatus.FILLED
        assert result.filled_quantity == 10.0


class TestCancelOrder:
    def test_cancel_returns_true(self, client):
        client._client.cancel_order_by_id.return_value = None
        assert client.cancel_order("order-abc-123") is True


class TestCancelAllOrders:
    def test_cancel_all_returns_list(self, client):
        client._client.cancel_orders.return_value = [
            _mock_alpaca_order(id="o1", status="pending_cancel"),
            _mock_alpaca_order(id="o2", status="pending_cancel"),
        ]
        result = client.cancel_all_orders()
        assert len(result) == 2


class TestGetOrders:
    def test_get_all_orders(self, client):
        client._client.get_orders.return_value = [
            _mock_alpaca_order(id="o1"),
            _mock_alpaca_order(id="o2"),
        ]
        result = client.get_orders()
        assert len(result) == 2
        assert all(isinstance(o, Order) for o in result)


class TestGetPositions:
    def test_get_positions(self, client):
        pos = MagicMock()
        pos.symbol = "AAPL"
        pos.qty = "10"
        pos.avg_entry_price = "150.0"
        pos.current_price = "155.0"
        pos.market_value = "1550.0"
        pos.unrealized_pl = "50.0"
        pos.unrealized_plpc = "0.0333"

        client._client.get_all_positions.return_value = [pos]
        result = client.get_positions()
        assert len(result) == 1
        assert isinstance(result[0], Position)
        assert result[0].symbol == "AAPL"

    def test_get_position_found(self, client):
        pos = MagicMock()
        pos.symbol = "AAPL"
        pos.qty = "10"
        pos.avg_entry_price = "150.0"
        pos.current_price = "155.0"
        pos.market_value = "1550.0"
        pos.unrealized_pl = "50.0"
        pos.unrealized_plpc = "0.0333"

        client._client.get_open_position.return_value = pos
        result = client.get_position("AAPL")
        assert isinstance(result, Position)

    def test_get_position_not_found(self, client):
        # Simulate Alpaca raising when position not found.
        # The implementation catches APIError — we use a generic Exception
        # subclass here to avoid importing alpaca in test code.
        client._client.get_open_position.side_effect = Exception("position does not exist")
        result = client.get_position("ZZZZZ")
        assert result is None


class TestGetAccount:
    def test_get_account(self, client):
        acct = MagicMock()
        acct.equity = "10000.0"
        acct.cash = "5000.0"
        acct.buying_power = "5000.0"
        acct.portfolio_value = "10000.0"
        acct.equity_previous_close = "9850.0"

        client._client.get_account.return_value = acct
        result = client.get_account()
        assert isinstance(result, AccountInfo)
        assert result.equity == 10000.0
        assert result.daily_pnl == 150.0  # 10000 - 9850


class TestIsMarketOpen:
    def test_market_open(self, client):
        clock = MagicMock()
        clock.is_open = True
        client._client.get_clock.return_value = clock
        assert client.is_market_open() is True

    def test_market_closed(self, client):
        clock = MagicMock()
        clock.is_open = False
        client._client.get_clock.return_value = clock
        assert client.is_market_open() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/milodex/broker/test_alpaca_client.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement AlpacaBrokerClient**

```python
# src/milodex/broker/alpaca_client.py
"""Alpaca implementation of BrokerClient.

This is the ONLY file in the broker layer that imports alpaca-py.
All Alpaca-specific types are translated to milodex models before
being returned to callers. Does not retry on failure — raises immediately.
"""

from __future__ import annotations

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide as AlpacaOrderSide
from alpaca.trading.enums import OrderType as AlpacaOrderType
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.enums import TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    MarketOrderRequest,
    LimitOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)

from milodex.broker.client import BrokerClient
from milodex.broker.exceptions import (
    BrokerAuthError,
    BrokerConnectionError,
    InsufficientFundsError,
    OrderRejectedError,
)
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.config import get_alpaca_credentials, get_trading_mode

# Map our enums to Alpaca's
_SIDE_MAP = {
    OrderSide.BUY: AlpacaOrderSide.BUY,
    OrderSide.SELL: AlpacaOrderSide.SELL,
}

_TIF_MAP = {
    TimeInForce.DAY: AlpacaTimeInForce.DAY,
    TimeInForce.GTC: AlpacaTimeInForce.GTC,
}

# Map Alpaca status strings to our OrderStatus
_STATUS_MAP = {
    "new": OrderStatus.PENDING,
    "accepted": OrderStatus.PENDING,
    "pending_new": OrderStatus.PENDING,
    "pending_cancel": OrderStatus.CANCELLED,
    "filled": OrderStatus.FILLED,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "canceled": OrderStatus.CANCELLED,
    "expired": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
}

_ORDER_TYPE_MAP = {
    "market": OrderType.MARKET,
    "limit": OrderType.LIMIT,
    "stop": OrderType.STOP,
    "stop_limit": OrderType.STOP_LIMIT,
}


class AlpacaBrokerClient(BrokerClient):
    """Broker client backed by Alpaca's Trading API.

    TRADING_MODE=paper uses Alpaca's paper trading environment.
    TRADING_MODE=live uses real money. This distinction lives only here.
    """

    def __init__(self) -> None:
        api_key, secret_key = get_alpaca_credentials()
        paper = get_trading_mode() == "paper"
        self._client = TradingClient(api_key, secret_key, paper=paper)

    def _translate_order(self, alpaca_order) -> Order:
        """Convert an Alpaca order object to our Order model."""
        status_str = str(alpaca_order.status)
        # Handle enum or string status
        if hasattr(alpaca_order.status, "value"):
            status_str = alpaca_order.status.value
        order_type_str = str(alpaca_order.type)
        if hasattr(alpaca_order.type, "value"):
            order_type_str = alpaca_order.type.value
        side_str = str(alpaca_order.side)
        if hasattr(alpaca_order.side, "value"):
            side_str = alpaca_order.side.value
        tif_str = str(alpaca_order.time_in_force)
        if hasattr(alpaca_order.time_in_force, "value"):
            tif_str = alpaca_order.time_in_force.value

        return Order(
            id=str(alpaca_order.id),
            symbol=alpaca_order.symbol,
            side=OrderSide.BUY if side_str == "buy" else OrderSide.SELL,
            order_type=_ORDER_TYPE_MAP.get(order_type_str, OrderType.MARKET),
            quantity=float(alpaca_order.qty),
            time_in_force=(
                TimeInForce.GTC if tif_str == "gtc" else TimeInForce.DAY
            ),
            status=_STATUS_MAP.get(status_str, OrderStatus.PENDING),
            submitted_at=alpaca_order.submitted_at,
            limit_price=(
                float(alpaca_order.limit_price)
                if alpaca_order.limit_price else None
            ),
            stop_price=(
                float(alpaca_order.stop_price)
                if alpaca_order.stop_price else None
            ),
            filled_quantity=(
                float(alpaca_order.filled_qty)
                if alpaca_order.filled_qty else None
            ),
            filled_avg_price=(
                float(alpaca_order.filled_avg_price)
                if alpaca_order.filled_avg_price else None
            ),
            filled_at=alpaca_order.filled_at,
        )

    def _translate_position(self, alpaca_pos) -> Position:
        """Convert an Alpaca position object to our Position model."""
        return Position(
            symbol=alpaca_pos.symbol,
            quantity=float(alpaca_pos.qty),
            avg_entry_price=float(alpaca_pos.avg_entry_price),
            current_price=float(alpaca_pos.current_price),
            market_value=float(alpaca_pos.market_value),
            unrealized_pnl=float(alpaca_pos.unrealized_pl),
            unrealized_pnl_pct=float(alpaca_pos.unrealized_plpc),
        )

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> Order:
        """Submit an order to Alpaca."""
        alpaca_side = _SIDE_MAP[side]
        alpaca_tif = _TIF_MAP[time_in_force]

        try:
            if order_type == OrderType.MARKET:
                request = MarketOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                )
            elif order_type == OrderType.LIMIT:
                request = LimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    limit_price=limit_price,
                )
            elif order_type == OrderType.STOP:
                request = StopOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    stop_price=stop_price,
                )
            elif order_type == OrderType.STOP_LIMIT:
                request = StopLimitOrderRequest(
                    symbol=symbol,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=alpaca_tif,
                    limit_price=limit_price,
                    stop_price=stop_price,
                )
            else:
                raise ValueError(f"Unsupported order type: {order_type}")

            alpaca_order = self._client.submit_order(request)
            return self._translate_order(alpaca_order)

        except APIError as e:
            error_msg = str(e).lower()
            if "forbidden" in error_msg or "auth" in error_msg:
                raise BrokerAuthError(str(e)) from e
            if "insufficient" in error_msg or "buying power" in error_msg:
                raise InsufficientFundsError(str(e)) from e
            raise OrderRejectedError(str(e)) from e
        except Exception as e:
            if "connect" in str(e).lower() or "timeout" in str(e).lower():
                raise BrokerConnectionError(str(e)) from e
            raise

    def get_order(self, order_id: str) -> Order:
        """Get order status from Alpaca."""
        alpaca_order = self._client.get_order_by_id(order_id)
        return self._translate_order(alpaca_order)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order. Returns True if successful."""
        try:
            self._client.cancel_order_by_id(order_id)
            return True
        except APIError:
            return False

    def cancel_all_orders(self) -> list[Order]:
        """Cancel all open orders."""
        cancelled = self._client.cancel_orders()
        return [self._translate_order(o) for o in cancelled]

    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        """Get recent orders from Alpaca."""
        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        request = GetOrdersRequest(
            status=status_map.get(status, QueryOrderStatus.ALL),
            limit=limit,
        )
        alpaca_orders = self._client.get_orders(request)
        return [self._translate_order(o) for o in alpaca_orders]

    def get_positions(self) -> list[Position]:
        """Get all open positions from Alpaca."""
        alpaca_positions = self._client.get_all_positions()
        return [self._translate_position(p) for p in alpaca_positions]

    def get_position(self, symbol: str) -> Position | None:
        """Get position for a symbol, or None if not held."""
        try:
            alpaca_pos = self._client.get_open_position(symbol)
            return self._translate_position(alpaca_pos)
        except (APIError, Exception):
            return None

    def get_account(self) -> AccountInfo:
        """Get account summary from Alpaca."""
        acct = self._client.get_account()
        equity = float(acct.equity)
        prev_close = float(acct.equity_previous_close)
        return AccountInfo(
            equity=equity,
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            portfolio_value=float(acct.portfolio_value),
            daily_pnl=equity - prev_close,
        )

    def is_market_open(self) -> bool:
        """Check if the market is currently open."""
        clock = self._client.get_clock()
        return clock.is_open
```

- [ ] **Step 4: Update broker __init__.py with re-exports**

```python
# src/milodex/broker/__init__.py
"""Brokerage API integration.

Handles connection to brokers (starting with Alpaca), order submission,
position queries, and account status. The rest of the system interacts
with brokers exclusively through this module's interface.
"""

from milodex.broker.client import BrokerClient
from milodex.broker.exceptions import (
    BrokerAuthError,
    BrokerConnectionError,
    BrokerError,
    InsufficientFundsError,
    OrderRejectedError,
)
from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)

__all__ = [
    "AccountInfo",
    "BrokerAuthError",
    "BrokerClient",
    "BrokerConnectionError",
    "BrokerError",
    "InsufficientFundsError",
    "Order",
    "OrderRejectedError",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Position",
    "TimeInForce",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/milodex/broker/test_alpaca_client.py -v`
Expected: all 11 tests PASS

- [ ] **Step 6: Run all broker tests together**

Run: `pytest tests/milodex/broker/ -v`
Expected: all broker tests PASS (models + alpaca_client)

- [ ] **Step 7: Lint**

Run: `ruff check src/milodex/broker/ tests/milodex/broker/`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add src/milodex/broker/ tests/milodex/broker/
git commit -m "feat: add AlpacaBrokerClient with order, position, account support"
```

---

## Task 10: Shared Test Fixtures + Full Test Suite

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: Create shared conftest with fixtures**

```python
# tests/conftest.py
"""Shared test fixtures for Milodex."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from milodex.broker.models import (
    AccountInfo,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    TimeInForce,
)
from milodex.data.models import Bar, BarSet, Timeframe


@pytest.fixture()
def sample_bar():
    """A single AAPL daily bar."""
    return Bar(
        timestamp=datetime(2025, 1, 15, 5, 0, tzinfo=timezone.utc),
        open=150.0,
        high=152.0,
        low=149.5,
        close=151.0,
        volume=1000000,
        vwap=150.8,
    )


@pytest.fixture()
def sample_barset():
    """A 3-day AAPL BarSet."""
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(
            ["2025-01-13", "2025-01-14", "2025-01-15"], utc=True
        ),
        "open": [148.0, 149.0, 150.0],
        "high": [149.0, 150.0, 152.0],
        "low": [147.0, 148.5, 149.5],
        "close": [148.5, 149.5, 151.0],
        "volume": [900000, 950000, 1000000],
        "vwap": [148.3, 149.2, 150.8],
    })
    return BarSet(df)


@pytest.fixture()
def sample_order():
    """A filled AAPL market buy order."""
    return Order(
        id="order-test-123",
        symbol="AAPL",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=10.0,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.FILLED,
        submitted_at=datetime(2025, 1, 15, 14, 30, tzinfo=timezone.utc),
        filled_quantity=10.0,
        filled_avg_price=151.25,
        filled_at=datetime(2025, 1, 15, 14, 30, 5, tzinfo=timezone.utc),
    )


@pytest.fixture()
def sample_position():
    """An open AAPL position."""
    return Position(
        symbol="AAPL",
        quantity=10.0,
        avg_entry_price=150.0,
        current_price=155.0,
        market_value=1550.0,
        unrealized_pnl=50.0,
        unrealized_pnl_pct=0.0333,
    )


@pytest.fixture()
def sample_account():
    """A paper trading account."""
    return AccountInfo(
        equity=10000.0,
        cash=5000.0,
        buying_power=5000.0,
        portfolio_value=10000.0,
        daily_pnl=150.0,
    )
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest -v`
Expected: ALL tests pass (config + data models + cache + alpaca provider + broker models + alpaca client)

- [ ] **Step 3: Lint everything**

Run: `ruff check src/ tests/`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "feat: add shared test fixtures for data and broker layers"
```

---

## Task 11: Integration Smoke Tests

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_alpaca_smoke.py`

- [ ] **Step 1: Create integration test directory**

```bash
mkdir -p tests/integration
```

- [ ] **Step 2: Create __init__.py**

```python
# tests/integration/__init__.py
```

- [ ] **Step 3: Write integration smoke tests**

```python
# tests/integration/test_alpaca_smoke.py
"""Integration smoke tests against Alpaca paper trading.

These tests hit the real Alpaca API using credentials from .env.
They are skipped in CI and run manually:

    pytest tests/integration/ -v -m integration

Requires valid ALPACA_API_KEY and ALPACA_SECRET_KEY in .env.
"""

from datetime import date, timedelta

import pytest

from milodex.config import get_alpaca_credentials

# Skip all tests in this module if credentials aren't configured
try:
    get_alpaca_credentials()
    HAS_CREDENTIALS = True
except ValueError:
    HAS_CREDENTIALS = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not HAS_CREDENTIALS, reason="No Alpaca credentials in .env"),
]


class TestAlpacaDataSmoke:
    def test_fetch_spy_daily_bars(self):
        from milodex.data.alpaca_provider import AlpacaDataProvider
        from milodex.data.models import Timeframe

        provider = AlpacaDataProvider()
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=5)
        result = provider.get_bars(["SPY"], Timeframe.DAY_1, start, end)

        assert "SPY" in result
        assert len(result["SPY"]) > 0

    def test_get_latest_bar(self):
        from milodex.data.alpaca_provider import AlpacaDataProvider
        from milodex.data.models import Bar

        provider = AlpacaDataProvider()
        bar = provider.get_latest_bar("SPY")
        assert isinstance(bar, Bar)
        assert bar.close > 0


class TestAlpacaBrokerSmoke:
    def test_get_account(self):
        from milodex.broker.alpaca_client import AlpacaBrokerClient
        from milodex.broker.models import AccountInfo

        client = AlpacaBrokerClient()
        acct = client.get_account()
        assert isinstance(acct, AccountInfo)
        assert acct.equity > 0

    def test_is_market_open_returns_bool(self):
        from milodex.broker.alpaca_client import AlpacaBrokerClient

        client = AlpacaBrokerClient()
        result = client.is_market_open()
        assert isinstance(result, bool)
```

- [ ] **Step 4: Verify integration tests are skipped without credentials**

Run: `pytest tests/integration/ -v`
Expected: tests SKIPPED with reason "No Alpaca credentials"

- [ ] **Step 5: Commit**

```bash
git add tests/integration/
git commit -m "feat: add integration smoke tests for Alpaca (skipped without credentials)"
```

---

## Task 12: Final Verification

- [ ] **Step 1: Run full test suite with verbose output**

Run: `pytest -v --tb=short`
Expected: all tests PASS, no warnings

- [ ] **Step 2: Lint and format check**

Run: `ruff check src/ tests/ && ruff format --check src/ tests/`
Expected: no errors, no formatting changes needed

- [ ] **Step 3: Verify import structure is clean**

Run:
```bash
python -c "
from milodex.data import DataProvider, Bar, BarSet, Timeframe
from milodex.broker import BrokerClient, Order, Position, AccountInfo
from milodex.broker import OrderSide, OrderType, OrderStatus, TimeInForce
from milodex.broker import BrokerConnectionError, BrokerAuthError
from milodex.broker import InsufficientFundsError, OrderRejectedError
from milodex.config import get_alpaca_credentials, get_trading_mode, get_cache_dir
print('All imports OK')
"
```
Expected: `All imports OK`

- [ ] **Step 4: Verify no alpaca imports leak outside implementation files**

Run: `ruff check src/ tests/ && grep -r "from alpaca" src/milodex/ --include="*.py" | grep -v alpaca_provider | grep -v alpaca_client`
Expected: no output (only the two implementation files import alpaca)

- [ ] **Step 5: Final commit if any formatting changes were needed**

```bash
ruff format src/ tests/
git add -A
git commit -m "style: format data and broker layers"
```
