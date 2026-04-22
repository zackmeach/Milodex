"""Unit tests for analytics metrics computation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from milodex.analytics.metrics import (
    PerformanceMetrics,
    _cagr,
    _daily_returns,
    _max_drawdown,
    _sharpe,
    _sortino,
    _trade_stats,
    compute_metrics,
)


def _equity_curve(values: list[float], start: date) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def _trade(symbol: str, side: str, qty: float, price: float, day: date) -> dict:
    return {
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "estimated_unit_price": price,
        "recorded_at": day.isoformat(),
    }


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_daily_returns_empty_curve():
    assert _daily_returns([]) == []


def test_daily_returns_single_point():
    assert _daily_returns([(date(2024, 1, 1), 100.0)]) == []


def test_daily_returns_two_points():
    curve = [(date(2024, 1, 1), 100.0), (date(2024, 1, 2), 105.0)]
    returns = _daily_returns(curve)
    assert len(returns) == 1
    assert returns[0] == pytest.approx(0.05)


def test_cagr_one_year_double():
    # total_return=1.0 means 100% gain; over exactly 1 trading year → CAGR = 1.0 (100%)
    result = _cagr(1.0, 252)
    assert result == pytest.approx(1.0, rel=0.01)


def test_cagr_none_for_single_day():
    assert _cagr(0.10, 1) is None


def test_max_drawdown_no_drawdown():
    curve = _equity_curve([100.0, 110.0, 120.0, 130.0], start=date(2024, 1, 1))
    assert _max_drawdown(curve) == pytest.approx(0.0)


def test_max_drawdown_simple_case():
    curve = _equity_curve([100.0, 120.0, 80.0, 90.0], start=date(2024, 1, 1))
    dd = _max_drawdown(curve)
    assert dd == pytest.approx((120.0 - 80.0) / 120.0)


def test_max_drawdown_empty_curve():
    assert _max_drawdown([]) == 0.0


def test_sharpe_positive_returns():
    # Alternating returns so variance is non-zero
    returns = [0.01 if i % 2 == 0 else 0.005 for i in range(252)]
    s = _sharpe(returns)
    assert s is not None
    assert s > 0


def test_sharpe_zero_variance_returns_none():
    returns = [0.005] * 252
    s = _sharpe(returns)
    assert s is None


def test_sortino_no_downside_returns_none():
    returns = [0.01, 0.02, 0.005]
    result = _sortino(returns)
    assert result is None


def test_sortino_mixed_returns():
    returns = [0.01, -0.005, 0.02, -0.003]
    s = _sortino(returns)
    assert s is not None


# ---------------------------------------------------------------------------
# _trade_stats
# ---------------------------------------------------------------------------


def test_trade_stats_empty():
    wr, hold, w, loss_count = _trade_stats([])
    assert wr is None
    assert hold is None
    assert w == 0
    assert loss_count == 0


def test_trade_stats_profitable_round_trip():
    start = date(2024, 1, 2)
    trades = [
        _trade("SPY", "buy", 10.0, 100.0, start),
        _trade("SPY", "sell", 10.0, 110.0, start + timedelta(days=3)),
    ]
    wr, hold, w, loss_count = _trade_stats(trades)
    assert wr == 1.0
    assert w == 1
    assert loss_count == 0
    assert hold == pytest.approx(3.0)


def test_trade_stats_losing_round_trip():
    start = date(2024, 1, 2)
    trades = [
        _trade("SPY", "buy", 10.0, 100.0, start),
        _trade("SPY", "sell", 10.0, 90.0, start + timedelta(days=2)),
    ]
    wr, hold, w, loss_count = _trade_stats(trades)
    assert wr == 0.0
    assert w == 0
    assert loss_count == 1


def test_trade_stats_multiple_symbols():
    d = date(2024, 1, 2)
    trades = [
        _trade("AAPL", "buy", 5.0, 100.0, d),
        _trade("SPY", "buy", 10.0, 200.0, d),
        _trade("AAPL", "sell", 5.0, 110.0, d + timedelta(days=3)),
        _trade("SPY", "sell", 10.0, 180.0, d + timedelta(days=3)),
    ]
    wr, hold, w, loss_count = _trade_stats(trades)
    assert w == 1
    assert loss_count == 1
    assert wr == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# compute_metrics integration
# ---------------------------------------------------------------------------


def test_compute_metrics_flat_equity():
    equity = _equity_curve([100_000.0] * 252, start=date(2024, 1, 1))
    m = compute_metrics(
        run_id="test-run",
        strategy_id="test.strat.v1",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        initial_equity=100_000.0,
        equity_curve=equity,
        trades=[],
    )
    assert m.total_return_pct == pytest.approx(0.0)
    assert m.max_drawdown_pct == pytest.approx(0.0)
    assert m.trade_count == 0
    assert m.confidence_label == "insufficient_data"


def test_compute_metrics_positive_return():
    start = date(2024, 1, 1)
    equity = _equity_curve([100_000.0 * (1.001**i) for i in range(252)], start=start)
    m = compute_metrics(
        run_id="run1",
        strategy_id="s.v1",
        start_date=start,
        end_date=start + timedelta(days=251),
        initial_equity=100_000.0,
        equity_curve=equity,
        trades=[],
    )
    assert m.total_return_pct > 0
    assert m.cagr_pct is not None
    assert m.cagr_pct > 0
    assert m.sharpe_ratio is not None


def test_compute_metrics_confidence_labels():
    start = date(2024, 1, 1)
    equity = _equity_curve([100_000.0] * 10, start=start)

    def _m(trade_count: int) -> str:
        d = date(2024, 1, 2)
        trades = []
        for _ in range(trade_count // 2):
            trades.append(_trade("SPY", "buy", 1.0, 100.0, d))
            trades.append(_trade("SPY", "sell", 1.0, 100.0, d + timedelta(days=1)))
            d += timedelta(days=2)
        m = compute_metrics(
            run_id="r",
            strategy_id="s",
            start_date=start,
            end_date=start + timedelta(days=9),
            initial_equity=100_000.0,
            equity_curve=equity,
            trades=trades,
        )
        return m.confidence_label

    assert _m(10) == "insufficient_data"
    assert _m(30) == "preliminary"
    assert _m(100) == "meaningful"


def test_compute_metrics_returns_correct_types():
    equity = _equity_curve([100_000.0, 101_000.0, 100_500.0], start=date(2024, 1, 2))
    m = compute_metrics(
        run_id="r",
        strategy_id="s",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        initial_equity=100_000.0,
        equity_curve=equity,
        trades=[],
    )
    assert isinstance(m, PerformanceMetrics)
    assert isinstance(m.total_return_pct, float)
    assert isinstance(m.max_drawdown_pct, float)
    assert m.trading_days == 3
