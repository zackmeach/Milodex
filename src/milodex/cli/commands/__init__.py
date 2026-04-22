"""CLI command modules. Each module exposes ``register`` and ``run``."""

from milodex.cli.commands import (
    analytics,
    backtest,
    config,
    data,
    promote,
    status,
    strategy,
    trade,
)

ALL = (status, data, config, trade, strategy, backtest, analytics, promote)

__all__ = [
    "ALL",
    "analytics",
    "backtest",
    "config",
    "data",
    "promote",
    "status",
    "strategy",
    "trade",
]
