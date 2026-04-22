"""Backtest engine and walk-forward validation.

Runs strategies against historical data using rolling train/test windows
with out-of-sample holdout. Applies conservative slippage estimates and
enforces minimum trade counts before drawing statistical conclusions.
"""

from milodex.backtesting.engine import BacktestEngine, BacktestResult
from milodex.backtesting.walk_forward import WalkForwardSplitter

__all__ = ["BacktestEngine", "BacktestResult", "WalkForwardSplitter"]
