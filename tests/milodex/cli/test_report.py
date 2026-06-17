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
    StrategyManifestEvent,
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
    backtest_run_id: int | None = None,
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
            backtest_run_id=backtest_run_id,
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


def _seed_walk_forward_backtest_run(
    event_store: EventStore,
    *,
    run_id: str,
    strategy_id: str,
    total_return_pct: float = 4.34,
    sharpe: float = 0.327,
    max_drawdown_pct: float = 6.41,
    trading_days: int = 752,
    trade_pairs: int = 5,
) -> int:
    """Seed a walk-forward run (no equity curve, OOS aggregate metadata)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 3, 1, tzinfo=UTC)
    db_id = event_store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=strategy_id,
            config_path="configs/test.yaml",
            config_hash="fp-walkforward",
            start_date=start,
            end_date=end,
            started_at=start,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={
                "initial_equity": 100_000.0,
                "walk_forward": True,
                "oos_aggregate": {
                    "total_return_pct": total_return_pct,
                    "sharpe": sharpe,
                    "max_drawdown_pct": max_drawdown_pct,
                    "trading_days": trading_days,
                },
            },
        )
    )
    event_store.update_backtest_run_status(run_id, status="completed", ended_at=end)
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
        sell_exp_id = _append_explanation(event_store, strategy_name=strategy_id, when=sell_at)
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


def test_report_default_uses_manifest_stage_when_promotion_lacks_manifest(
    tmp_path: Path,
) -> None:
    """Trust report agrees with `report strategy <id>`: manifest wins.

    Mirrors the §7 stage-source consistency fix that landed for
    `report strategy <id>`. When a strategy's latest promotion event has
    no associated manifest (typical of pre-Phase-1.4 bookkeeping), the
    default trust report should display the *manifest's* stage — that's
    what the runtime actually treats the strategy as.
    """
    event_store = EventStore(tmp_path / "milodex.db")
    base = datetime(2026, 4, 22, 16, 0, tzinfo=UTC)

    # Pre-Phase-1.4 promotions, no manifest_id:
    event_store.append_promotion(
        PromotionEvent(
            strategy_id="meanrev.v1",
            from_stage="backtest",
            to_stage="paper",
            promotion_type="statistical",
            approved_by="owner",
            recorded_at=base,
            manifest_id=None,
        )
    )
    event_store.append_promotion(
        PromotionEvent(
            strategy_id="meanrev.v1",
            from_stage="paper",
            to_stage="micro_live",
            promotion_type="statistical",
            approved_by="owner",
            recorded_at=base + timedelta(minutes=1),
            manifest_id=None,
        )
    )
    # Phase-1.4 manifest freeze at "paper":
    event_store.append_strategy_manifest(
        StrategyManifestEvent(
            strategy_id="meanrev.v1",
            stage="paper",
            config_hash="hash-paper",
            config_path="configs/meanrev.yaml",
            config_json={"strategy": {"id": "meanrev.v1"}},
            frozen_at=base + timedelta(days=2),
            frozen_by="operator",
        )
    )

    exit_code, out, _ = _run(["report", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    strategies = payload["data"]["strategies"]
    matching = [s for s in strategies if s["strategy_id"] == "meanrev.v1"]
    assert matching, "test fixture must produce a meanrev row"
    snap = matching[0]
    assert snap["stage"] == "paper", (
        "manifest at 'paper' must override the bookkeeping-only "
        "'micro_live' promotion that lacks a frozen manifest"
    )
    assert snap["stage_source"] == "manifest"
    assert snap["latest_promotion_stage"] == "micro_live"
    assert snap["stage_disagreement"] is True


def test_report_default_falls_back_to_promotion_when_no_manifest(tmp_path: Path) -> None:
    """When no manifest exists at all, the default report uses the promotion-log stage."""
    event_store = EventStore(tmp_path / "milodex.db")
    event_store.append_promotion(
        PromotionEvent(
            strategy_id="early.v1",
            from_stage="backtest",
            to_stage="paper",
            promotion_type="statistical",
            approved_by="owner",
            recorded_at=datetime(2026, 4, 1, tzinfo=UTC),
            manifest_id=None,
        )
    )

    exit_code, out, _ = _run(["report", "--json"], tmp_path)
    assert exit_code == 0
    snap = next(
        s
        for s in json.loads(out.getvalue())["data"]["strategies"]
        if s["strategy_id"] == "early.v1"
    )
    assert snap["stage"] == "paper"
    assert snap["stage_source"] == "promotion_log"
    assert snap["stage_disagreement"] is False


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


def test_report_daily_excludes_backtest_rows(tmp_path: Path) -> None:
    """`report daily` counts only live operational activity, not backtest-engine rows
    (D-2). A backtest trade AND explanation dated today are excluded even though they
    share the date with a live row — the date filter alone is insufficient."""
    event_store = EventStore(tmp_path / "milodex.db")
    target = datetime(2026, 1, 15, 14, 30, tzinfo=UTC)

    bt_id = event_store.append_backtest_run(
        BacktestRunEvent(
            run_id="bt-contam",
            strategy_id="meanrev.v1",
            config_path="configs/test.yaml",
            config_hash="fp-bt",
            start_date=target,
            end_date=target,
            started_at=target,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={"initial_equity": 100_000.0},
        )
    )
    bt_exp = _append_explanation(
        event_store, strategy_name="meanrev.v1", when=target, backtest_run_id=bt_id
    )
    _append_trade(
        event_store,
        explanation_id=bt_exp,
        when=target,
        source="backtest",
        backtest_run_id=bt_id,
        strategy_name="meanrev.v1",
    )

    # A live paper trade + explanation dated the same day.
    live_exp = _append_explanation(event_store, strategy_name="spy_shy_regime", when=target)
    _append_trade(event_store, explanation_id=live_exp, when=target, strategy_name="spy_shy_regime")

    exit_code, out, _ = _run(["report", "daily", "--date", "2026-01-15", "--json"], tmp_path)
    assert exit_code == 0
    data = json.loads(out.getvalue())["data"]
    assert len(data["trades_today"]) == 1  # backtest trade excluded
    assert len(data["explanations_today"]) == 1  # backtest explanation excluded


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
        "result_type",
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


def test_report_strategy_labels_walk_forward_metrics_per_metric(tmp_path: Path) -> None:
    """`report strategy` against a walk-forward backtest run labels each OOS metric.

    Closes P-1 (PHASE2_PLANNING.md) option (a). Without per-metric labels,
    an operator reading "Total return: +4.34%" cannot distinguish whole-period
    equity-curve return from OOS-aggregate stitched across walk-forward
    windows. The trust-report surface that closes SC-6 must surface the
    distinction at the metric line, not just elsewhere.
    """
    event_store = EventStore(tmp_path / "milodex.db")
    _seed_walk_forward_backtest_run(
        event_store,
        run_id="bt-wf-report",
        strategy_id="meanrev.v1",
        total_return_pct=4.34,
        sharpe=0.327,
        max_drawdown_pct=6.41,
        trading_days=752,
    )

    exit_code, out, _ = _run(["report", "strategy", "meanrev.v1"], tmp_path)
    assert exit_code == 0
    output = out.getvalue()

    for line_prefix in ("Total return:", "Max drawdown:", "Sharpe:"):
        line = next((line for line in output.splitlines() if line_prefix in line), None)
        assert line is not None, f"missing line for {line_prefix!r}"
        assert "OOS" in line or "walk-forward" in line.lower(), (
            f"line {line!r} (prefix {line_prefix!r}) must carry an OOS / walk-forward "
            f"label so the trust-report cannot mislead an operator about scope"
        )


# ---------------------------------------------------------------------------
# Stage-source consistency (§7 finding closure)
# ---------------------------------------------------------------------------


def _seed_promotion(
    event_store: EventStore,
    *,
    strategy_id: str,
    from_stage: str,
    to_stage: str,
    when: datetime,
    manifest_id: int | None = None,
) -> int:
    return event_store.append_promotion(
        PromotionEvent(
            strategy_id=strategy_id,
            from_stage=from_stage,
            to_stage=to_stage,
            promotion_type="statistical",
            approved_by="owner",
            recorded_at=when,
            manifest_id=manifest_id,
        )
    )


def _seed_manifest(
    event_store: EventStore,
    *,
    strategy_id: str,
    stage: str,
    when: datetime,
    config_hash: str = "fp-test-manifest",
) -> int:
    return event_store.append_strategy_manifest(
        StrategyManifestEvent(
            strategy_id=strategy_id,
            stage=stage,
            config_hash=config_hash,
            config_path="configs/test.yaml",
            config_json={"strategy": {"id": strategy_id}},
            frozen_at=when,
            frozen_by="operator",
        )
    )


def test_report_strategy_uses_manifest_stage_when_promotion_lacks_manifest(
    tmp_path: Path,
) -> None:
    """Promotion-log says one stage, manifest says another → manifest wins.

    Closes the §7 finding surfaced 2026-04-26: meanrev had a 2026-04-22
    paper→micro_live promotion with `manifest_id: null` (predating the
    2026-04-23 freeze + live-refusal hooks), then a 2026-04-24 manifest
    freeze at "paper". `report strategy` previously displayed the
    promotion-log stage (`micro_live`), creating a false impression of
    runtime state. The runtime drift check uses the manifest's stage, so
    the manifest is the source of truth for what the system actually does.
    """
    event_store = EventStore(tmp_path / "milodex.db")
    _seed_backtest_run(event_store, run_id="bt-1", strategy_id="meanrev_v1", trade_pairs=5)

    base_time = datetime(2026, 4, 22, 16, 0, tzinfo=UTC)
    # Pre-Phase-1.4-style promotions with no associated manifest:
    _seed_promotion(
        event_store,
        strategy_id="meanrev_v1",
        from_stage="backtest",
        to_stage="paper",
        when=base_time,
        manifest_id=None,
    )
    _seed_promotion(
        event_store,
        strategy_id="meanrev_v1",
        from_stage="paper",
        to_stage="micro_live",
        when=base_time + timedelta(minutes=1),
        manifest_id=None,
    )
    # Then a Phase-1.4 manifest freeze at "paper":
    _seed_manifest(
        event_store,
        strategy_id="meanrev_v1",
        stage="paper",
        when=base_time + timedelta(days=2),
    )

    exit_code, out, _ = _run(["report", "strategy", "meanrev_v1", "--json"], tmp_path)
    assert exit_code == 0
    payload = json.loads(out.getvalue())
    data = payload["data"]
    assert data["stage"] == "paper", (
        "manifest at 'paper' must override the bookkeeping-only "
        "'micro_live' promotion that lacks a frozen manifest"
    )
    assert data["stage_source"] == "manifest"
    # When the two disagree, the report surfaces both for forensics.
    assert data["latest_promotion_stage"] == "micro_live"
    assert data["stage_disagreement"] is True


def test_report_strategy_falls_back_to_promotion_when_no_manifest(tmp_path: Path) -> None:
    """No frozen manifest yet → use the promotion-log stage as a best-effort fallback."""
    event_store = EventStore(tmp_path / "milodex.db")
    _seed_backtest_run(event_store, run_id="bt-1", strategy_id="early_strat", trade_pairs=5)
    _seed_promotion(
        event_store,
        strategy_id="early_strat",
        from_stage="backtest",
        to_stage="paper",
        when=datetime(2026, 4, 1, tzinfo=UTC),
        manifest_id=None,
    )

    exit_code, out, _ = _run(["report", "strategy", "early_strat", "--json"], tmp_path)
    assert exit_code == 0
    data = json.loads(out.getvalue())["data"]
    assert data["stage"] == "paper"
    assert data["stage_source"] == "promotion_log"
    assert data["stage_disagreement"] is False


def test_report_strategy_default_stage_when_neither_exists(tmp_path: Path) -> None:
    """No promotion, no manifest → stage defaults to 'backtest'."""
    event_store = EventStore(tmp_path / "milodex.db")
    _seed_backtest_run(event_store, run_id="bt-1", strategy_id="fresh_strat", trade_pairs=5)

    exit_code, out, _ = _run(["report", "strategy", "fresh_strat", "--json"], tmp_path)
    assert exit_code == 0
    data = json.loads(out.getvalue())["data"]
    assert data["stage"] == "backtest"
    assert data["stage_source"] == "default"
    assert data["stage_disagreement"] is False
