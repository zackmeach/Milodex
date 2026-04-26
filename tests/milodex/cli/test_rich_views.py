"""Smoke tests for the rich-view builders.

Every public builder in ``cli/rich_views.py`` is invoked with realistic
test data and rendered through a forced-TTY ``rich.Console`` capture. The
assertions are deliberately lightweight — they verify:

- the builder doesn't raise,
- the output string contains the domain-relevant text (substring), and
- color toggles fire on the expected branches (e.g. red ``KILL SWITCH
  ACTIVE`` banner appears only when active).

These tests cover the TTY rendering path that the production CLI tests
deliberately don't exercise (those use a non-TTY ``StringIO``).
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from types import SimpleNamespace

from rich.console import Console

from milodex.cli.rich_views import (
    _kill_switch_banner,
    build_analytics_metrics_view,
    build_backtest_view,
    build_orders_view,
    build_positions_view,
    build_promotion_history_view,
    build_promotion_manifest_view,
    build_reconcile_view,
    build_status_view,
    build_strategy_report_view,
    build_trade_execution_view,
    build_trust_report_view,
    build_walk_forward_view,
)


def _render(renderable) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, soft_wrap=False, width=200)
    console.print(renderable)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# kill_switch_banner
# ---------------------------------------------------------------------------


def test_kill_switch_banner_returns_none_when_inactive():
    assert _kill_switch_banner(False) is None


def test_kill_switch_banner_includes_reason_when_active():
    panel = _kill_switch_banner(True, reason="manual operator trip")
    out = _render(panel)
    assert "KILL SWITCH ACTIVE" in out
    assert "manual operator trip" in out


# ---------------------------------------------------------------------------
# status / positions / orders
# ---------------------------------------------------------------------------


_ACCOUNT = {
    "equity": 100_000.0,
    "cash": 50_000.0,
    "buying_power": 100_000.0,
    "portfolio_value": 100_000.0,
    "daily_pnl": -125.50,
}


def test_status_view_shows_kill_switch_banner_when_active():
    out = _render(
        build_status_view(
            trading_mode="paper",
            market_open=False,
            account=_ACCOUNT,
            kill_switch_active=True,
            kill_switch_reason="forced",
        )
    )
    assert "KILL SWITCH ACTIVE" in out
    assert "Equity" in out


def test_status_view_no_banner_when_kill_switch_inactive():
    out = _render(
        build_status_view(
            trading_mode="paper",
            market_open=True,
            account=_ACCOUNT,
        )
    )
    assert "KILL SWITCH ACTIVE" not in out
    assert "Daily P&L" in out


def test_positions_view_empty():
    out = _render(build_positions_view(positions=[], sort_key="symbol", limit=20))
    assert "No open positions" in out


def test_positions_view_with_rows():
    pos = SimpleNamespace(
        symbol="SPY",
        quantity=10.0,
        avg_entry_price=500.0,
        current_price=520.0,
        market_value=5200.0,
        unrealized_pnl=200.0,
        unrealized_pnl_pct=0.04,
    )
    out = _render(build_positions_view(positions=[pos], sort_key="symbol", limit=20))
    assert "SPY" in out
    assert "Open Positions" in out


def test_orders_view_empty():
    out = _render(build_orders_view(orders=[], symbol_filter=None, verbose=False))
    assert "No matching orders" in out


def test_orders_view_with_rows():
    order = SimpleNamespace(
        id="abcdef0123456789",
        symbol="SPY",
        side=SimpleNamespace(value="buy"),
        order_type=SimpleNamespace(value="market"),
        status=SimpleNamespace(value="filled"),
        quantity=5.0,
        submitted_at=datetime(2026, 4, 26, 14, 30, tzinfo=UTC),
        limit_price=None,
        stop_price=None,
        filled_quantity=5.0,
        filled_avg_price=520.0,
    )
    out = _render(build_orders_view(orders=[order], symbol_filter=None, verbose=True))
    assert "SPY" in out
    assert "BUY" in out
    assert "filled" in out


# ---------------------------------------------------------------------------
# trust report (default) + strategy report
# ---------------------------------------------------------------------------


_TRUST_STRAT = {
    "strategy_id": "regime.v1",
    "stage": "paper",
    "config_fingerprint": "abc123",
    "last_action": {
        "decision_type": "submit",
        "symbol": "SPY",
        "risk_allowed": True,
    },
    "next_expected_action": "evaluate at next scheduled run",
    "confidence": {"label": "preliminary", "reason": "stage=paper"},
    "warnings": [],
}


def test_trust_report_view_with_kill_switch_banner():
    out = _render(
        build_trust_report_view(
            strategies=[_TRUST_STRAT],
            kill_switch={"active": True, "reason": "halt"},
            broker={"connected": True, "trading_mode": "paper"},
            data_freshness={"trading_days_behind": 0},
            operator_action_required=True,
        )
    )
    assert "KILL SWITCH ACTIVE" in out
    assert "regime.v1" in out
    assert "paper" in out


def test_trust_report_view_empty_strategies_panel():
    out = _render(
        build_trust_report_view(
            strategies=[],
            kill_switch={"active": False, "reason": None},
            broker={"connected": False, "trading_mode": "paper"},
            data_freshness={},
            operator_action_required=False,
        )
    )
    assert "UNREACHABLE" in out
    assert "No strategies have produced any activity" in out


def test_strategy_report_view_renders_disagreement_panel():
    out = _render(
        build_strategy_report_view(
            strategy_id="meanrev.v1",
            stage="paper",
            stage_source="manifest",
            latest_promotion_stage="micro_live",
            stage_disagreement=True,
            config_fingerprint="hash123",
            latest_backtest_run_id="run-1",
            metrics={
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "total_return_pct": 4.34,
                "max_drawdown_pct": 6.41,
                "sharpe_ratio": 0.33,
                "sortino_ratio": None,
                "trade_count": 752,
                "buy_count": 380,
                "sell_count": 372,
                "winning_trades": 263,
                "losing_trades": 138,
                "win_rate_pct": 65.6,
                "avg_hold_days": 0.0,
                "result_type": "walk_forward",
            },
            confidence={"label": "meaningful", "reason": "trade_count=752"},
        )
    )
    assert "meanrev.v1" in out
    assert "Stage bookkeeping mismatch" in out
    assert "+4.34%" in out
    assert "walk-forward" in out


def test_strategy_report_view_no_disagreement_branch():
    out = _render(
        build_strategy_report_view(
            strategy_id="regime.v1",
            stage="paper",
            stage_source="manifest",
            latest_promotion_stage=None,
            stage_disagreement=False,
            config_fingerprint=None,
            latest_backtest_run_id="run-2",
            metrics={
                "start_date": "2024-01-01",
                "end_date": "2024-06-30",
                "total_return_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": None,
                "sortino_ratio": None,
                "trade_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate_pct": None,
                "avg_hold_days": None,
                "result_type": "whole_period",
            },
            confidence={"label": "insufficient_data", "reason": "trade_count=0"},
        )
    )
    assert "Stage bookkeeping mismatch" not in out
    assert "Performance" in out
    assert "n/a" in out


# ---------------------------------------------------------------------------
# analytics metrics
# ---------------------------------------------------------------------------


def test_analytics_metrics_view_strategy_only():
    out = _render(
        build_analytics_metrics_view(
            strategy={
                "strategy_id": "x.v1",
                "run_id": "r1",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "trading_days": 252,
                "total_return_pct": 12.5,
                "cagr_pct": 12.5,
                "max_drawdown_pct": 8.0,
                "sharpe_ratio": 1.2,
                "sortino_ratio": 1.5,
                "win_rate_pct": 60.0,
                "avg_hold_days": 3.5,
                "trade_count": 100,
                "buy_count": 50,
                "sell_count": 50,
                "winning_trades": 60,
                "losing_trades": 40,
                "avg_win_usd": 200.0,
                "avg_loss_usd": -100.0,
                "profit_factor": 2.0,
                "confidence_label": "meaningful",
                "result_type": "whole_period",
            },
            benchmark=None,
        )
    )
    assert "Strategy" in out
    assert "+12.50%" in out


def test_analytics_metrics_view_with_benchmark_and_walkforward_label():
    out = _render(
        build_analytics_metrics_view(
            strategy={
                "strategy_id": "x.v1",
                "run_id": "r1",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "trading_days": 252,
                "total_return_pct": -5.0,
                "cagr_pct": None,
                "max_drawdown_pct": 20.0,
                "sharpe_ratio": -0.3,
                "sortino_ratio": None,
                "win_rate_pct": None,
                "avg_hold_days": None,
                "trade_count": 0,
                "buy_count": 0,
                "sell_count": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "avg_win_usd": None,
                "avg_loss_usd": None,
                "profit_factor": None,
                "confidence_label": "insufficient_data",
                "result_type": "walk_forward",
            },
            benchmark={
                "strategy_id": "spy",
                "run_id": "spy-r1",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "trading_days": 252,
                "total_return_pct": 25.0,
                "cagr_pct": 25.0,
                "max_drawdown_pct": 10.0,
                "sharpe_ratio": 1.0,
                "sortino_ratio": 1.1,
                "win_rate_pct": 100.0,
                "avg_hold_days": 252.0,
                "trade_count": 2,
                "buy_count": 1,
                "sell_count": 1,
                "winning_trades": 1,
                "losing_trades": 0,
                "avg_win_usd": 25_000.0,
                "avg_loss_usd": None,
                "profit_factor": float("inf"),
                "confidence_label": "insufficient_data",
                "result_type": "whole_period",
            },
        )
    )
    assert "SPY Benchmark" in out
    assert "walk-forward" in out


# ---------------------------------------------------------------------------
# promotion
# ---------------------------------------------------------------------------


def test_promotion_history_view_empty():
    out = _render(build_promotion_history_view(strategy_id="x.v1", events=[]))
    assert "No promotion history" in out


def test_promotion_history_view_with_events_flags_missing_manifest():
    events = [
        {
            "id": 1,
            "recorded_at": "2026-04-22T16:44:14+00:00",
            "from_stage": "backtest",
            "to_stage": "paper",
            "promotion_type": "statistical",
            "approved_by": "owner",
            "manifest_id": None,
            "reverses_event_id": None,
        },
        {
            "id": 2,
            "recorded_at": "2026-04-22T16:45:39+00:00",
            "from_stage": "paper",
            "to_stage": "micro_live",
            "promotion_type": "statistical",
            "approved_by": "owner",
            "manifest_id": None,
            "reverses_event_id": None,
        },
    ]
    out = _render(build_promotion_history_view(strategy_id="meanrev.v1", events=events))
    assert "meanrev.v1" in out
    assert "micro_live" in out
    assert "none" in out  # missing-manifest flag


def test_promotion_manifest_view_active():
    out = _render(
        build_promotion_manifest_view(
            strategy_id="x.v1",
            stage="paper",
            active_manifest={
                "config_hash": "deadbeef" * 8,
                "config_path": "configs/x.yaml",
                "frozen_at": "2026-04-26T17:00:00+00:00",
                "frozen_by": "operator",
            },
        )
    )
    assert "x.v1" in out
    assert "deadbeef" in out


def test_promotion_manifest_view_no_active():
    out = _render(
        build_promotion_manifest_view(
            strategy_id="x.v1",
            stage="paper",
            active_manifest=None,
        )
    )
    assert "No active manifest" in out
    assert "milodex promotion freeze" in out


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


def test_reconcile_view_clean_state():
    out = _render(
        build_reconcile_view(
            broker={"connected": True, "market_open": True, "account": {}},
            positions_ok=[],
            positions_mismatched=[],
            orders_ok=[],
            orders_mismatched=[],
            deferred_checks=[],
            reconciliation_clean=True,
            incident_recorded=False,
            incident_deduplicated=False,
            incident_hash=None,
            as_of="2026-04-26T17:00:00+00:00",
        )
    )
    assert "CLEAN" in out


def test_reconcile_view_drift_detected_with_incident_recorded():
    out = _render(
        build_reconcile_view(
            broker={"connected": True, "market_open": True, "account": {}},
            positions_ok=[],
            positions_mismatched=[
                {"symbol": "SPY", "local_qty": 10, "broker_qty": 8, "kind": "qty_mismatch"}
            ],
            orders_ok=[],
            orders_mismatched=[],
            deferred_checks=["filled_since_last_sync"],
            reconciliation_clean=False,
            incident_recorded=True,
            incident_deduplicated=False,
            incident_hash="abcdef1234567890",
            as_of="2026-04-26T17:00:00+00:00",
        )
    )
    assert "DRIFT DETECTED" in out
    assert "abcdef" in out
    assert "filled_since_last_sync" in out


# ---------------------------------------------------------------------------
# trade execution
# ---------------------------------------------------------------------------


def test_trade_execution_view_allow_path():
    out = _render(
        build_trade_execution_view(
            status="submitted",
            side="buy",
            symbol="SPY",
            quantity=12.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=500.0,
            estimated_order_value=6000.0,
            trading_mode="paper",
            market_open=True,
            strategy_name="regime.v1",
            strategy_stage="paper",
            risk_checks=[
                {"name": "max_order_value", "passed": True, "message": "ok"},
                {"name": "max_total_exposure", "passed": True, "message": "ok"},
            ],
            risk_allowed=True,
            broker_order_id="ord-1",
            broker_status="filled",
            message=None,
        )
    )
    assert "ALLOW" in out
    assert "BUY" in out
    assert "SPY" in out
    assert "max_order_value" in out


def test_trade_execution_view_block_path_with_failed_check():
    out = _render(
        build_trade_execution_view(
            status="rejected",
            side="buy",
            symbol="SPY",
            quantity=200.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=500.0,
            estimated_order_value=100_000.0,
            trading_mode="paper",
            market_open=True,
            strategy_name=None,
            strategy_stage=None,
            risk_checks=[
                {
                    "name": "max_order_value_exceeded",
                    "passed": False,
                    "message": "100000 > 10000",
                }
            ],
            risk_allowed=False,
            broker_order_id=None,
            broker_status=None,
            message="Risk layer blocked the order.",
        )
    )
    assert "BLOCK" in out
    assert "FAIL" in out
    assert "max_order_value_exceeded" in out


# ---------------------------------------------------------------------------
# backtest summaries
# ---------------------------------------------------------------------------


def test_backtest_view_renders_summary():
    out = _render(
        build_backtest_view(
            strategy_id="x.v1",
            run_id="r1",
            start_date="2024-01-01",
            end_date="2024-12-31",
            trading_days=252,
            initial_equity=100_000.0,
            final_equity=110_000.0,
            total_return_pct=10.0,
            trade_count=50,
            buy_count=25,
            sell_count=25,
            slippage_pct=0.001,
            commission_per_trade=0.0,
            confidence_label="meaningful",
            confidence_reason="trade_count=50",
        )
    )
    assert "x.v1" in out
    assert "+10.00%" in out
    assert "meaningful" in out


def test_walk_forward_view_renders_per_window_table_and_warns_on_swd():
    out = _render(
        build_walk_forward_view(
            strategy_id="meanrev.v1",
            run_id="r1",
            start_date="2015-01-01",
            end_date="2024-12-31",
            initial_equity=100_000.0,
            train_days=225,
            test_days=223,
            step_days=223,
            oos_trading_days=892,
            oos_trade_count=752,
            oos_total_return_pct=4.34,
            oos_sharpe=0.33,
            oos_max_drawdown_pct=6.41,
            stability={
                "sharpe_min": -1.4,
                "sharpe_max": 1.7,
                "sharpe_std": 1.2,
                "windows_positive": 2,
                "windows_negative": 2,
                "single_window_dependency": True,
            },
            windows=[
                {
                    "index": 0,
                    "test_start": "2021-06-16",
                    "test_end": "2022-05-03",
                    "trade_count": 197,
                    "total_return_pct": -4.4,
                    "sharpe": -1.4,
                    "max_drawdown_pct": 5.4,
                },
                {
                    "index": 1,
                    "test_start": "2022-05-04",
                    "test_end": "2023-03-23",
                    "trade_count": 120,
                    "total_return_pct": 7.1,
                    "sharpe": 1.7,
                    "max_drawdown_pct": 2.8,
                },
            ],
            extra_warnings=["fragile aggregate"],
        )
    )
    assert "meanrev.v1" in out
    assert "892" in out
    assert "+4.34%" in out
    assert "YES" in out  # single_window_dependency banner
    assert "fragile aggregate" in out
    assert "Per-window" in out
