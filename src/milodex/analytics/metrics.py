"""Performance metrics computed from a backtest equity curve and trade list.

All metrics use daily returns derived from the equity curve stored in
``backtest_runs.metadata_json``.  Win-rate and average-hold metrics are
computed from matched buy/sell trade pairs using FIFO accounting.

Confidence labels follow SRS thresholds:
  - ``"insufficient_data"`` — fewer than 30 trades (R-BKT-003)
  - ``"preliminary"``       — 30–99 trades
  - ``"meaningful"``        — 100+ trades
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date


@dataclass
class PerformanceMetrics:
    """Computed performance metrics for a single backtest run."""

    run_id: str
    strategy_id: str
    start_date: date
    end_date: date
    initial_equity: float
    final_equity: float

    # Return metrics
    total_return_pct: float
    cagr_pct: float | None

    # Risk metrics
    max_drawdown_pct: float
    sharpe_ratio: float | None
    sortino_ratio: float | None

    # Trade metrics
    trade_count: int
    buy_count: int
    sell_count: int
    win_rate_pct: float | None
    avg_hold_days: float | None
    winning_trades: int
    losing_trades: int

    # Meta
    trading_days: int
    confidence_label: str

    equity_curve: list[tuple[date, float]] = field(default_factory=list)


def compute_metrics(
    *,
    run_id: str,
    strategy_id: str,
    start_date: date,
    end_date: date,
    initial_equity: float,
    equity_curve: list[tuple[date, float]],
    trades: list[dict],
) -> PerformanceMetrics:
    """Compute all performance metrics from an equity curve and trade list.

    Args:
        run_id: Backtest run identifier.
        strategy_id: Strategy identifier.
        start_date: First day of the backtest window.
        end_date: Last day of the backtest window.
        initial_equity: Starting equity (USD).
        equity_curve: List of ``(date, portfolio_value)`` tuples, one per
            trading day, in ascending date order.
        trades: List of trade dicts.  Each dict must contain at minimum:
            ``symbol`` (str), ``side`` (``'buy'``/``'sell'``),
            ``quantity`` (float), ``estimated_unit_price`` (float),
            ``recorded_at`` (str ISO-8601).

    Returns:
        :class:`PerformanceMetrics` with all computable fields populated.
    """
    trading_days = len(equity_curve)
    final_equity = equity_curve[-1][1] if equity_curve else initial_equity
    total_return = (final_equity - initial_equity) / initial_equity if initial_equity else 0.0

    daily_returns = _daily_returns(equity_curve)

    cagr = _cagr(total_return, trading_days) if trading_days > 1 else None
    max_dd = _max_drawdown(equity_curve)
    sharpe = _sharpe(daily_returns) if len(daily_returns) >= 2 else None
    sortino = _sortino(daily_returns) if len(daily_returns) >= 2 else None

    buy_count = sum(1 for t in trades if str(t.get("side", "")).lower() == "buy")
    sell_count = sum(1 for t in trades if str(t.get("side", "")).lower() == "sell")
    trade_count = buy_count + sell_count

    win_rate, avg_hold, winning, losing = _trade_stats(trades)

    confidence = _confidence_label(trade_count)

    return PerformanceMetrics(
        run_id=run_id,
        strategy_id=strategy_id,
        start_date=start_date,
        end_date=end_date,
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return_pct=total_return * 100.0,
        cagr_pct=cagr * 100.0 if cagr is not None else None,
        max_drawdown_pct=max_dd * 100.0,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        trade_count=trade_count,
        buy_count=buy_count,
        sell_count=sell_count,
        win_rate_pct=win_rate * 100.0 if win_rate is not None else None,
        avg_hold_days=avg_hold,
        winning_trades=winning,
        losing_trades=losing,
        trading_days=trading_days,
        confidence_label=confidence,
        equity_curve=equity_curve,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _daily_returns(equity_curve: list[tuple[date, float]]) -> list[float]:
    if len(equity_curve) < 2:
        return []
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1][1]
        curr = equity_curve[i][1]
        if prev > 0:
            returns.append((curr - prev) / prev)
    return returns


def _cagr(total_return: float, trading_days: int) -> float | None:
    if trading_days < 2:
        return None
    years = trading_days / 252.0
    if years <= 0:
        return None
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def _max_drawdown(equity_curve: list[tuple[date, float]]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0][1]
    max_dd = 0.0
    for _, value in equity_curve:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _sharpe(daily_returns: list[float], risk_free_daily: float = 0.0) -> float | None:
    if len(daily_returns) < 2:
        return None
    n = len(daily_returns)
    mean = sum(daily_returns) / n - risk_free_daily
    variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0.0:
        return None
    return (mean / std) * math.sqrt(252)


def _sortino(daily_returns: list[float], risk_free_daily: float = 0.0) -> float | None:
    if len(daily_returns) < 2:
        return None
    n = len(daily_returns)
    mean = sum(daily_returns) / n - risk_free_daily
    downside = [r for r in daily_returns if r < 0]
    if not downside:
        return None
    downside_var = sum(r**2 for r in downside) / len(downside)
    downside_std = math.sqrt(downside_var) if downside_var > 0 else 0.0
    if downside_std == 0.0:
        return None
    return (mean / downside_std) * math.sqrt(252)


def _trade_stats(
    trades: list[dict],
) -> tuple[float | None, float | None, int, int]:
    """Return ``(win_rate, avg_hold_days, winning_count, losing_count)``.

    Pairs BUY/SELL trades using FIFO per symbol.  Only fully-matched round
    trips contribute to the statistics.
    """
    from collections import defaultdict, deque
    from datetime import datetime

    pending: dict[str, deque] = defaultdict(deque)
    pnls: list[float] = []
    hold_days: list[float] = []

    sorted_trades = sorted(trades, key=lambda t: str(t.get("recorded_at", "")))

    for trade in sorted_trades:
        side = str(trade.get("side", "")).lower()
        sym = str(trade.get("symbol", "")).upper()
        qty = float(trade.get("quantity", 0))
        price = float(trade.get("estimated_unit_price", 0))
        recorded_at_raw = trade.get("recorded_at")
        try:
            trade_date = (
                datetime.fromisoformat(str(recorded_at_raw)).date()
                if recorded_at_raw
                else None
            )
        except ValueError:
            trade_date = None

        if side == "buy":
            pending[sym].append({"qty": qty, "price": price, "date": trade_date})
        elif side == "sell" and pending[sym]:
            remaining_qty = qty
            while remaining_qty > 0 and pending[sym]:
                entry = pending[sym][0]
                matched = min(remaining_qty, entry["qty"])
                pnl_per_share = price - entry["price"]
                pnls.append(pnl_per_share * matched)
                if trade_date and entry["date"]:
                    days = (trade_date - entry["date"]).days
                    hold_days.append(float(days))
                entry["qty"] -= matched
                remaining_qty -= matched
                if entry["qty"] <= 1e-9:
                    pending[sym].popleft()

    if not pnls:
        return None, None, 0, 0

    winning = sum(1 for p in pnls if p > 0)
    losing = sum(1 for p in pnls if p <= 0)
    win_rate = winning / len(pnls)
    avg_hold = sum(hold_days) / len(hold_days) if hold_days else None
    return win_rate, avg_hold, winning, losing


def _confidence_label(trade_count: int) -> str:
    if trade_count < 30:
        return "insufficient_data"
    if trade_count < 100:
        return "preliminary"
    return "meaningful"
