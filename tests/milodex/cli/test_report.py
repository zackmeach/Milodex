"""CLI integration tests for ``milodex report``.

Each test injects an ``event_store_factory`` backed by a tmp SQLite DB
and a stub broker / data provider so nothing touches real resources.
All three subcommands are covered for both human and ``--json`` output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from milodex.broker.exceptions import BrokerAuthError
from milodex.broker.models import AccountInfo, Position
from milodex.cli.formatter import JSON_SCHEMA_VERSION
from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import (
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    KillSwitchEvent,
    PromotionEvent,
    TradeEvent,
)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubBroker:
    def __init__(
        self,
        *,
        account: AccountInfo | None = None,
        market_open: bool = True,
        positions: list[Position] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._account = account or AccountInfo(
            equity=1000.0, cash=500.0, buying_power=500.0, portfolio_value=1000.0, daily_pnl=0.0
        )
        self._market_open = market_open
        self._positions = positions or []
        self._error = error

    def get_account(self) -> AccountInfo:
        if self._error:
            raise self._error
        return self._account

    def is_market_open(self) -> bool:
        if self._error:
            raise self._error
        return self._market_open

    def get_positions(self) -> list[Position]:
        if self._error:
            raise self._error
        return self._positions


def _refuse_data_provider():
    raise AssertionError("data provider should not be needed by report")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _run(
    argv: list[str],
    tmp_path: Path,
    *,
    broker: _StubBroker | None = None,
) -> tuple[int, StringIO, StringIO]:
    out = StringIO()
    err = StringIO()
    broker = broker if broker is not None else _StubBroker()
    exit_code = cli_entrypoint(
        argv,
        event_store_factory=lambda: EventStore(tmp_path / "milodex.db"),
        broker_factory=lambda: broker,
        data_provider_factory=_refuse_data_provider,
        stdout=out,
        stderr=err,
    )
    return exit_code, out, err


def _append_explanation(
    event_store: EventStore,
    *,
    strategy_name: str,
    when: datetime,
    symbol: str = "SPY",
    side: str = "buy",
    risk_allowed: bool = True,
    reason_codes: list[str] | None = None,
    latest_bar_timestamp: datetime | None = None,
) -> int:
    return event_store.append_explanation(
        ExplanationEvent(
            recorded_at=when,
            decision_type="preview",
            status="preview",
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash="fp-abcdef",
            symbol=symbol,
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="test",
            market_open=True,
            latest_bar_timestamp=latest_bar_timestamp or when,
            latest_bar_close=100.0,
            account_equity=1000.0,
            account_cash=500.0,
            account_portfolio_value=1000.0,
            account_daily_pnl=0.0,
            risk_allowed=risk_allowed,
            risk_summary="ok" if risk_allowed else "blocked",
            reason_codes=reason_codes or [],
            risk_checks=[],
            context={},
        )
    )


def _append_trade(
    event_store: EventStore,
    *,
    explanation_id: int,
    when: datetime,
    symbol: str = "SPY",
    side: str = "buy",
    status: str = "submitted",
    source: str = "paper",
    price: float = 100.0,
    backtest_run_id: int | None = None,
    strategy_name: str | None = None,
) -> None:
    event_store.append_trade(
        TradeEvent(
            explanation_id=explanation_id,
            recorded_at=when,
            status=status,
            source=source,
            symbol=symbol,
            side=side,
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            estimated_unit_price=price,
            estimated_order_value=price,
            strategy_name=strategy_name,
            strategy_stage="paper",
            strategy_config_path=None,
            submitted_by="test",
            broker_order_id=None,
            broker_status=None,
            message=None,
            backtest_run_id=backtest_run_id,
        )
    )


def _seed_backtest_run(
    event_store: EventStore,
    *,
    run_id: str,
    strategy_id: str,
    trade_pairs: int = 5,
) -> int:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 3, 1, tzinfo=UTC)
    equity_curve = [
        ((start + timedelta(days=i)).date().isoformat(), 100_000.0 + i * 10.0) for i in range(60)
    ]
    db_id = event_store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=strategy_id,
            config_path="configs/test.yaml",
            config_hash="fp-backtest",
            start_date=start,
            end_date=end,
            started_at=start,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={
                "initial_equity": 100_000.0,
                "equity_curve": equity_curve,
            },
        )
    )
    event_store.update_backtest_run_status(run_id, status="completed", ended_at=end)

    # Seed paired buy/sell trades so win-rate / avg-hold populate.
    for i in range(trade_pairs):
        buy_at = start + timedelta(days=i * 2)
        sell_at = start + timedelta(days=i * 2 + 1)
        buy_exp_id = _append_explanation(event_store, strategy_name=strategy_id, when=buy_at)
        _append_trade(
            event_store,
            explanation_id=buy_exp_id,
            when=buy_at,
            side="buy",
            source="backtest",
            price=100.0 + i,
            backtest_run_id=db_id,
            strategy_name=strategy_id,
        )
        sell_exp_id = _append_explanation(
            event_store, strategy_name=strategy_id, when=sell_at, side="sell"
        )
        _append_trade(
            event_store,
            explanation_id=sell_exp_id,
            when=sell_at,
            side="sell",
            source="backtest",
            price=101.0 + i,
            backtest_run_id=db_id,
            strategy_name=strategy_id,
        )
    return db_id


# ---------------------------------------------------------------------------
# Default `milodex report` — primary trust report
# ---------------------------------------------------------------------------


def test_report_default_empty_state(tmp_path: Path) -> None:
    exit_code, out, err = _run(["report"], tmp_path)

    assert exit_code == 0
    output = out.getvalue()
    assert "Milodex Trust Report" in output
    assert "Strategies" in output
    assert "(none" in output
    assert "System State" in output
    assert "Operator action: none required" in output
    assert err.getvalue() == ""


def test_report_default_with_strategy_shows_stage_and_confidence(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "milodex.db")
    _append_explanation(
        event_store,
        strategy_name="spy_shy_regime",
        when=datetime.now(tz=UTC) - timedelta(hours=2),
    )
    event_store.append_promotion(
        PromotionEvent(
            strategy_id="spy_shy_regime",
            from_stage="backtest",
            to_stage="paper",
            promotion_type="lifecycle_exempt",
            approved_by="operator",
            recorded_at=datetime.now(tz=UTC),
        )
    )

    exit_code, out, err = _run(["report"], tmp_path)
    assert exit_code == 0
    output = out.getvalue()
    assert "spy_shy_regime" in output
    assert "stage: paper" in output
    assert "confidence:" in output
    assert err.getvalue() == ""


def test_report_default_json_schema_contract(tmp_path: Path) -> None:
    exit_code, out, _ = _run(["report", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())

    # R-CLI-017: every payload includes these fields.
    assert payload["schema_version"] == JSON_SCHEMA_VERSION
    assert payload["command"] == "report"
    assert payload["status"] == "success"
    assert "timestamp" in payload and payload["timestamp"]
    assert isinstance(payload["warnings"], list)
    assert isinstance(payload["errors"], list)
    assert isinstance(payload["summary"], list)

    data = payload["data"]
    assert set(data) >= {
        "strategies",
        "kill_switch",
        "broker",
        "data_freshness",
        "operator_action_required",
        "incidents",
    }
    assert data["kill_switch"]["active"] is False
    assert data["broker"]["connected"] is True
    assert data["operator_action_required"] is False


def test_report_kill_switch_active_surfaces_banner(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "milodex.db")
    event_store.append_kill_switch_event(
        KillSwitchEvent(
            event_type="activated",
            recorded_at=datetime.now(tz=UTC),
            reason="daily_loss_cap exceeded",
        )
    )

    exit_code, out, _ = _run(["report", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["data"]["kill_switch"]["active"] is True
    assert payload["data"]["kill_switch"]["reason"] == "daily_loss_cap exceeded"
    assert payload["data"]["operator_action_required"] is True
    assert any("Kill switch active" in w for w in payload["warnings"])


def test_report_broker_unavailable_degrades_gracefully(tmp_path: Path) -> None:
    broker = _StubBroker(error=BrokerAuthError("no credentials"))
    exit_code, out, err = _run(["report", "--json"], tmp_path, broker=broker)

    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["data"]["broker"]["connected"] is False
    assert "no credentials" in payload["data"]["broker"]["error"]
    assert any("Broker unreachable" in w for w in payload["warnings"])
    assert err.getvalue() == ""


# ---------------------------------------------------------------------------
# `milodex report daily`
# ---------------------------------------------------------------------------


def test_report_daily_no_activity(tmp_path: Path) -> None:
    exit_code, out, _ = _run(["report", "daily"], tmp_path)
    assert exit_code == 0
    output = out.getvalue()
    assert "Milodex Daily Report" in output
    assert "0 explanations, 0 submitted trades, 0 rejections" in output


def test_report_daily_with_trades_and_rejection(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "milodex.db")
    today = datetime.now(tz=UTC)
    allowed_id = _append_explanation(event_store, strategy_name="spy_shy_regime", when=today)
    _append_trade(
        event_store,
        explanation_id=allowed_id,
        when=today,
        strategy_name="spy_shy_regime",
    )
    _append_explanation(
        event_store,
        strategy_name="mean_reversion",
        when=today,
        risk_allowed=False,
        reason_codes=["max_positions_exceeded"],
    )

    exit_code, out, _ = _run(["report", "daily", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    data = payload["data"]
    assert len(data["trades_today"]) == 1
    assert data["trades_today"][0]["symbol"] == "SPY"
    assert len(data["explanations_today"]) == 2
    assert any(e["risk_allowed"] is False for e in data["explanations_today"])
    assert data["portfolio"]["connected"] is True


def test_report_daily_honors_date_override(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "milodex.db")
    target = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)
    exp_id = _append_explanation(event_store, strategy_name="spy_shy_regime", when=target)
    _append_trade(event_store, explanation_id=exp_id, when=target, strategy_name="spy_shy_regime")
    # Add an entry on a different day that must NOT appear.
    other = datetime(2026, 1, 16, 14, 30, tzinfo=UTC)
    _append_explanation(event_store, strategy_name="spy_shy_regime", when=other)

    exit_code, out, _ = _run(["report", "daily", "--date", "2026-01-15", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    assert payload["data"]["date"] == "2026-01-15"
    assert len(payload["data"]["trades_today"]) == 1
    assert len(payload["data"]["explanations_today"]) == 1


# ---------------------------------------------------------------------------
# `milodex report strategy <id>`
# ---------------------------------------------------------------------------


def test_report_strategy_happy_path(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "milodex.db")
    _seed_backtest_run(event_store, run_id="bt-1", strategy_id="spy_shy_regime", trade_pairs=5)

    exit_code, out, _ = _run(["report", "strategy", "spy_shy_regime", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    data = payload["data"]
    assert data["strategy_id"] == "spy_shy_regime"
    assert data["latest_backtest_run_id"] == "bt-1"
    metrics = data["metrics"]
    expected_metric_fields = {
        "run_id",
        "strategy_id",
        "start_date",
        "end_date",
        "initial_equity",
        "final_equity",
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "max_drawdown_duration_days",
        "sharpe_ratio",
        "sortino_ratio",
        "trade_count",
        "buy_count",
        "sell_count",
        "win_rate_pct",
        "avg_hold_days",
        "winning_trades",
        "losing_trades",
        "avg_win_usd",
        "avg_loss_usd",
        "profit_factor",
        "trading_days",
        "confidence_label",
    }
    assert set(metrics) == expected_metric_fields
    assert metrics["trade_count"] == 10
    assert data["confidence"]["label"] == metrics["confidence_label"]
    assert "trade_count=" in data["confidence"]["reason"]


def test_report_strategy_missing_backtest_returns_structured_error(tmp_path: Path) -> None:
    exit_code, out, err = _run(["report", "strategy", "unknown_strategy", "--json"], tmp_path)
    assert exit_code == 1
    payload = json.loads(err.getvalue())
    assert payload["status"] == "error"
    assert payload["errors"][0]["code"] == "no_backtest_run"
    # R-CLI-020 four-question phrasing: what, what Milodex did, what to do next.
    message = payload["errors"][0]["message"]
    assert "No backtest run found" in message
    assert "no evidence" in message
    assert "milodex backtest" in message


def test_report_strategy_human_output_includes_analytics_headers(tmp_path: Path) -> None:
    event_store = EventStore(tmp_path / "milodex.db")
    _seed_backtest_run(event_store, run_id="bt-2", strategy_id="mean_reversion", trade_pairs=5)

    exit_code, out, _ = _run(["report", "strategy", "mean_reversion"], tmp_path)
    assert exit_code == 0
    output = out.getvalue()
    assert "Performance" in output
    assert "Total return:" in output
    assert "Max drawdown:" in output
    assert "Trades:" in output
    assert "Confidence:" in output
