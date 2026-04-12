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

- Parquet files at `data/cache/{timeframe}/{SYMBOL}.parquet`
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
```

Six methods covering order management, position queries, and account info.

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
    """Return path for local data cache. Default: project_root/data/cache/"""
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
