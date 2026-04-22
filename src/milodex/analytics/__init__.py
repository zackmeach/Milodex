"""Performance metrics, reporting, and benchmarking.

Provides trade logs with reasoning, daily portfolio snapshots, key metrics
(Sharpe, Sortino, max drawdown, win rate), benchmark comparison vs S&P 500,
and exportable reports. This is how you know if the system is working.
"""

from milodex.analytics.benchmark import compute_benchmark
from milodex.analytics.metrics import PerformanceMetrics, compute_metrics

__all__ = ["PerformanceMetrics", "compute_benchmark", "compute_metrics"]
