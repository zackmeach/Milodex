# Data & Broker Layers Design

**Date:** 2026-04-12
**Status:** Approved
**Scope:** `src/milodex/data/` and `src/milodex/broker/` modules, plus shared config

## Summary

Design for Milodex's foundational data acquisition and brokerage layers. Both use Alpaca as the sole phase-one provider, hidden behind abstract interfaces so the rest of the system never imports `alpaca-py`. Data is cached locally as Parquet files.

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Local data storage | Parquet files | Fast columnar reads, pandas-native, no DB overhead |
| Broker abstraction | Abstract base class + concrete impl | Clean swap path, independent testability, VISION mandate |
| Data granularity | Configurable per strategy (1m, 5m, 15m, 1h, daily) | Strategy configs declare `bar_size`; data layer honors it |
| Yahoo Finance | Planned, not built | YAGNI — Alpaca covers phase one. Interface supports adding it later |
| Architecture | Two separate ABCs (DataProvider + BrokerClient) | Single responsibility, independent mocking, Yahoo only implements DataProvider |

## Data Layer (`src/milodex/data/`)

### Models (`data/models.py`)

Standardized data types consumed by the rest of the system:

- **`Timeframe`** — Enum: `MINUTE_1`, `MINUTE_5`, `MINUTE_15`, `HOUR_1`, `DAY_1`. Maps to Alpaca's timeframe internally.
- **`Bar`** — Dataclass: `timestamp`, `open`, `high`, `low`, `close`, `volume`, `vwap` (optional). One OHLCV row.
- **`BarSet`** — Thin wrapper around a pandas DataFrame with typed columns. Provides convenience methods like `.to_dataframe()` and `.latest()`. This is what strategies and backtesting consume.

### Abstract Interface (`data/provider.py`)

```python
class DataProvider(ABC):
    @abstractmethod
    def get_bars(
        self,
        symbols: list[str],
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> dict[str, BarSet]:
        """Fetch OHLCV bars for one or more symbols."""

    @abstractmethod
    def get_latest_bar(self, symbol: str) -> Bar:
        """Fetch the most recent bar (for live/paper trading)."""

    @abstractmethod
    def get_tradeable_assets(self) -> list[str]:
        """Return symbols available for trading."""
```

Three methods cover all phase-one needs: historical data for backtesting, latest data for live signals, and asset discovery for universe filtering.

### Alpaca Implementation (`data/alpaca_provider.py`)

- Implements `DataProvider` using `alpaca-py`'s `StockHistoricalDataClient`
- Translates `Timeframe` enum → Alpaca's `TimeFrame` objects
- Returns standardized `BarSet` objects (never Alpaca's native types)

### Local Cache (`data/cache.py`)

- Parquet files at `market_cache/{timeframe}/{SYMBOL}.parquet`
- On `get_bars()`: check cache for existing date range → fetch only missing ranges from Alpaca → merge and save
- Cache invalidation: today's bar is always re-fetched (market might still be open). Historical bars are immutable.
- Simple file-based — no database, no expiry config.

### File Structure

```
src/milodex/data/
├── __init__.py          # Re-exports DataProvider, Timeframe, BarSet, Bar
├── models.py            # Timeframe, Bar, BarSet
├── provider.py          # DataProvider ABC
├── alpaca_provider.py   # AlpacaDataProvider implementation
└── cache.py             # ParquetCache
```

## Broker Layer (`src/milodex/broker/`)

### Models (`broker/models.py`)

Standardized types for all brokerage operations:

- **`OrderSide`** — Enum: `BUY`, `SELL`
- **`OrderType`** — Enum: `MARKET`, `LIMIT`, `STOP`, `STOP_LIMIT`
- **`OrderStatus`** — Enum: `PENDING`, `FILLED`, `PARTIALLY_FILLED`, `CANCELLED`, `REJECTED`
- **`TimeInForce`** — Enum: `DAY`, `GTC` (good-til-cancelled). DAY is the default for swing trades.
- **`Order`** — Dataclass: `id`, `symbol`, `side`, `order_type`, `quantity`, `limit_price` (optional), `stop_price` (optional), `time_in_force`, `status`, `filled_quantity`, `filled_avg_price`, `submitted_at`, `filled_at`
- **`Position`** — Dataclass: `symbol`, `quantity`, `avg_entry_price`, `current_price`, `market_value`, `unrealized_pnl`, `unrealized_pnl_pct`
- **`AccountInfo`** — Dataclass: `equity`, `cash`, `buying_power`, `portfolio_value`, `daily_pnl`

### Abstract Interface (`broker/client.py`)

```python
class BrokerClient(ABC):
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
        """Submit an order. Returns the order with initial status."""

    @abstractmethod
    def get_order(self, order_id: str) -> Order:
        """Get current status of an order."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if successful."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Get all open positions."""

    @abstractmethod
    def get_position(self, symbol: str) -> Position | None:
        """Get position for a specific symbol, or None."""

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get account summary."""

    @abstractmethod
    def cancel_all_orders(self) -> list[Order]:
        """Cancel all open orders. Used by kill switch for emergency halt."""

    @abstractmethod
    def get_orders(self, status: str = "all", limit: int = 100) -> list[Order]:
        """Get recent orders. Supports filtering by status (open/closed/all)."""

    @abstractmethod
    def is_market_open(self) -> bool:
        """Check if the market is currently open for trading."""
```

Nine methods covering order management, position queries, account info, market status, and emergency operations.

### Alpaca Implementation (`broker/alpaca_client.py`)

- Implements `BrokerClient` using `alpaca-py`'s `TradingClient`
- Reads credentials and trading mode from shared config
- `TRADING_MODE=paper` → Alpaca paper URL. `TRADING_MODE=live` → real money. This distinction lives only here.
- Translates all Alpaca-specific types to/from standardized models
- Raises custom exceptions on API errors

### Error Handling (`broker/exceptions.py`)

Custom exceptions not tied to Alpaca:

- **`BrokerConnectionError`** — Can't reach the API
- **`BrokerAuthError`** — Bad credentials
- **`InsufficientFundsError`** — Not enough buying power
- **`OrderRejectedError`** — Broker rejected the order (with reason)

### File Structure

```
src/milodex/broker/
├── __init__.py          # Re-exports BrokerClient, Order, Position, etc.
├── models.py            # OrderSide, OrderType, Order, Position, AccountInfo, etc.
├── client.py            # BrokerClient ABC
├── alpaca_client.py     # AlpacaBrokerClient implementation
└── exceptions.py        # BrokerConnectionError, OrderRejectedError, etc.
```

## Shared Config (`src/milodex/config.py`)

A thin module at the package level for shared configuration:

```python
def get_alpaca_credentials() -> tuple[str, str]:
    """Load ALPACA_API_KEY and ALPACA_SECRET_KEY from environment."""

def get_trading_mode() -> str:
    """Return 'paper' or 'live' from TRADING_MODE env var."""

def get_cache_dir() -> Path:
    """Return path for local data cache. Default: project_root/market_cache/"""
```

- Uses `python-dotenv` to load `.env`
- Both `AlpacaDataProvider` and `AlpacaBrokerClient` call these helpers
- Single source of truth for credentials and mode

## New Runtime Dependencies

| Package | Purpose | Justification |
|---------|---------|---------------|
| `alpaca-py` | Alpaca SDK for data + trading | Core broker/data integration |
| `pandas` | DataFrame operations | BarSet built on it; backtesting will need it |
| `pyarrow` | Parquet read/write | Required for parquet caching |
| `python-dotenv` | Load `.env` files | Credential management |

## Testing Strategy

- **Mocks over live calls.** Tests never hit Alpaca's API. Both ABCs are easily mockable.
- **`tests/milodex/data/`** — Cache logic (write/read/merge parquet), `AlpacaDataProvider` with mocked SDK, `BarSet` convenience methods.
- **`tests/milodex/broker/`** — `AlpacaBrokerClient` with mocked SDK, order translation, exception mapping.
- **Shared fixtures:** `conftest.py` with sample `Bar`, `BarSet`, `Order`, `Position` objects.

## System Integration

```
Strategy                Risk Layer              CLI
   │                       │                     │
   │  "I want bars"        │  "check account"    │  "show positions"
   ▼                       ▼                     ▼
DataProvider ABC      BrokerClient ABC      BrokerClient ABC
   │                       │                     │
   ▼                       ▼                     ▼
AlpacaDataProvider    AlpacaBrokerClient    AlpacaBrokerClient
   │                       │                     │
   ├── cache.py            └──────────┬──────────┘
   │   (Parquet)                      │
   └──────────┬───────────────────────┘
              │
         alpaca-py SDK
              │
         config.py (.env)
```

Nothing outside `data/alpaca_provider.py` and `broker/alpaca_client.py` ever imports `alpaca-py`. Swapping brokers means writing two new files and zero changes to consuming code.

## Edge Cases & Design Decisions

### Kill switch support
`cancel_all_orders()` exists specifically for the kill switch path. When risk triggers a halt, it calls this single method rather than iterating positions. The risk layer owns kill switch logic; the broker layer provides the emergency lever.

### Market hours
`is_market_open()` lets the risk layer and CLI prevent order submission outside trading hours. The system runs on evenings/weekends — this check prevents wasted API calls and confusing error states.

### Order history
`get_orders()` supports analytics trade logging and duplicate order detection. The risk layer uses it with a short lookback to enforce `duplicate_order_window_seconds` from `risk_defaults.yaml`. Analytics uses it for end-of-day trade logs.

### Data staleness
The risk layer enforces `max_data_staleness_seconds` by comparing `Bar.timestamp` to wall-clock time. `get_latest_bar()` returns the most recent bar the exchange has — on weekends, that's Friday's close. The risk layer must account for market-closed periods when checking staleness (use `is_market_open()` to distinguish stale data from expected weekend gaps).

### Partial fills
`Order.status == PARTIALLY_FILLED` is surfaced via `get_order()`. The risk layer is responsible for detecting this state and deciding whether to let the partial position stand or cancel the remainder. This is a risk-layer concern, not a broker-layer concern.

### Retry semantics
The Alpaca implementation does **not** retry on failure — it raises immediately. Callers (risk layer, CLI) decide whether to retry. This keeps the broker layer predictable and testable.

### `get_latest_bar()` when market is closed
Returns the most recent available bar (e.g., Friday's close on a Saturday). Does not raise. Callers should check `is_market_open()` if they need to distinguish "latest" from "live."

### Cache behavior
- `ParquetCache` creates directory structure on initialization if it doesn't exist.
- Cache path: `{project_root}/market_cache/{timeframe}/{SYMBOL}.parquet` (avoids collision with `src/milodex/data/`).
- Merge algorithm: load existing parquet, identify missing date ranges by comparing requested range to cached timestamps, fetch gaps from Alpaca, concatenate, deduplicate by timestamp, sort, and write back.
- Test cases must cover: empty cache, partial overlap, gap in middle, today re-fetch rule.

### `BarSet` column contract
`BarSet` always contains columns: `timestamp`, `open`, `high`, `low`, `close`, `volume`. The `vwap` column is always present but nullable — strategies can safely access it without checking for column existence. All price columns are `float64`; `volume` is `int64`; `timestamp` is timezone-aware `datetime64[ns, UTC]`.

### Backtesting usage
`get_bars()` returns the full requested range. The backtesting engine is responsible for windowing/slicing the `BarSet` to simulate point-in-time access during walk-forward validation. This is intentional — the data layer provides raw data, the backtest engine provides temporal discipline.

### Universe filtering
`get_tradeable_assets()` returns the full broker-eligible universe with no filtering. Strategy-level universe filtering (by config's `universe` list, minimum price, volume, etc.) is the strategy layer's responsibility.

## Integration Tests

In addition to the mock-based unit tests, the project should include an integration test suite (in `tests/integration/`) marked with `@pytest.mark.integration` that validates:
- Alpaca credential loading and authentication
- Real bar fetching (small request, e.g., 5 days of SPY daily bars)
- Account info retrieval
- Market open/closed check

These tests are skipped in CI (`pytest -m "not integration"`) and run manually to catch SDK/API contract changes.
