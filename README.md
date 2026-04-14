# Milodex

A personal, research-led trading system that discovers, validates, deploys, and monitors strategies without fooling its operator.

The name is a nod to Milo - a golden retriever - and the word "Index." Loyal, tireless, and always fetching returns.

## Quick Start

```powershell
# Clone and install in editable mode with dev dependencies
git clone <repo-url>
cd Milodex
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Copy environment config and add your Alpaca API keys
Copy-Item .env.example .env
```

Set the following values in `.env` before running the app:

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `TRADING_MODE=paper` or `TRADING_MODE=live`

## Running the App

Milodex currently ships as a read-only CLI. The first commands available are:

```powershell
milodex --help
milodex status
milodex positions
milodex orders
```

If the `milodex` command is not available in your shell yet, run it through the project venv:

```powershell
.\.venv\Scripts\python.exe -m milodex.cli.main --help
.\.venv\Scripts\python.exe -m milodex.cli.main status
.\.venv\Scripts\python.exe -m milodex.cli.main positions
.\.venv\Scripts\python.exe -m milodex.cli.main orders
```

Current CLI scope:

- `status` shows trading mode, market open/closed, and account summary
- `positions` lists open positions
- `orders` lists recent orders

There is no desktop GUI yet.

## Development

Use the project venv's Python when running tests and commands.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

## Project Structure

```
src/milodex/
    broker/        Brokerage API integration (Alpaca)
    strategies/    Strategy definitions and execution
    risk/          Risk management layer (veto power over all trades)
    backtesting/   Backtest engine and walk-forward validation
    data/          Market data acquisition and storage
    analytics/     Performance metrics, reporting, and benchmarking
    cli/           Command-line interface
configs/           Strategy and risk configuration (YAML)
logs/              Trade logs and daily portfolio snapshots
docs/              Project documentation
docs/reviews/      Dated project reviews and implementation audits
tests/             Test suite (mirrors src structure)
```

## Vision

See [docs/VISION.md](docs/VISION.md) for the full project vision, principles, and phase one scope.

The current UI state review lives at [docs/reviews/2026-04-14-ui-state-review.md](docs/reviews/2026-04-14-ui-state-review.md).
