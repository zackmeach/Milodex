# Milodex

A personal, research-led trading system that discovers, validates, deploys, and monitors strategies without fooling its operator.

The name is a nod to Milo — a golden retriever — and the word "Index." Loyal, tireless, and always fetching returns.

## Quick Start

```bash
# Clone and install in editable mode with dev dependencies
git clone <repo-url>
cd milodex
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Copy environment config and add your Alpaca API keys
cp .env.example .env

# Run tests
pytest

# Lint
ruff check src/ tests/
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
tests/             Test suite (mirrors src structure)
```

## Vision

See [docs/VISION.md](docs/VISION.md) for the full project vision, principles, and phase one scope.
