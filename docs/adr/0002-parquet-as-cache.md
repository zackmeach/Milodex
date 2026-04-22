# ADR 0002 — Parquet as Local Historical Cache

**Status:** Accepted
**Date:** 2026-04-16

## Context

Milodex fetches OHLCV bars for backtesting and live signal generation. Historical data is immutable once written; the same ranges are read repeatedly across backtest runs and walk-forward iterations. A local cache is needed to avoid hammering the Alpaca API and to make backtests fast and repeatable.

## Decision

Historical bars are cached as Parquet files on disk, keyed by `(symbol, timeframe, date-range)`. The `DataProvider` implementation reads the cache first and fetches from the upstream API only on miss.

## Rationale

- **Columnar format** matches the access pattern: backtesters scan one or two columns (`close`, `volume`) across many rows, which Parquet reads faster than row-oriented formats.
- **Pandas-native.** `pd.read_parquet` / `pd.to_parquet` are a one-line round trip, so there's no serialization code to maintain.
- **No database dependency.** A single-developer project shouldn't run a server (Postgres, DuckDB, etc.) for something a file does. Keeping state as plain files also makes the cache inspectable by the operator and trivially rebuildable — just delete the directory.
- **Compression.** Parquet's default compression yields roughly 5–10× smaller footprint than CSV for typical OHLCV data, which matters when caching years of minute-bar data.
- **Immutability fits the data.** Historical bars are never edited. The cache is append-only in practice, which dodges concurrency and write-contention concerns.
