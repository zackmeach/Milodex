"""Unit tests for the promotion evidence-package assembler."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from milodex.core.event_store import (
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    KillSwitchEvent,
    TradeEvent,
)
from milodex.promotion.evidence import EvidencePackage, assemble_evidence_package

_STRATEGY_ID = "regime.daily.sma200_rotation.cli_test.v1"
_ASSEMBLED_AT = datetime(2026, 4, 23, 18, 0, tzinfo=UTC)
_MANIFEST_HASH = "a" * 64


def _make_store(tmp_path) -> EventStore:
    return EventStore(tmp_path / "milodex.db")


def _seed_backtest_run(store: EventStore, run_id: str = "run-abc") -> int:
    return store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=_STRATEGY_ID,
            config_path="configs/test.yaml",
            config_hash=_MANIFEST_HASH,
            start_date=datetime(2024, 1, 1, tzinfo=UTC),
            end_date=datetime(2024, 12, 31, tzinfo=UTC),
            started_at=datetime(2026, 4, 20, 10, 0, tzinfo=UTC),
            status="completed",
            slippage_pct=0.002,
            commission_per_trade=0.0,
            metadata={},
        )
    )


def _seed_paper_trade(
    store: EventStore,
    *,
    strategy_name: str = _STRATEGY_ID,
    risk_allowed: bool = True,
    status: str = "submitted",
    stage: str = "paper",
) -> None:
    recorded_at = datetime(2026, 4, 22, 14, 0, tzinfo=UTC)
    explanation_id = store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type="submit",
            status=status,
            strategy_name=strategy_name,
            strategy_stage=stage,
            strategy_config_path="configs/test.yaml",
            config_hash=_MANIFEST_HASH,
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="operator",
            market_open=True,
            latest_bar_timestamp=recorded_at,
            latest_bar_close=450.0,
            account_equity=10_000.0,
            account_cash=9_550.0,
            account_portfolio_value=10_000.0,
            account_daily_pnl=0.0,
            risk_allowed=risk_allowed,
            risk_summary="",
            reason_codes=[],
            risk_checks=[],
            context={},
        )
    )
    if risk_allowed:
        store.append_trade(
            TradeEvent(
                explanation_id=explanation_id,
                recorded_at=recorded_at,
                status=status,
                source="paper",
                symbol="SPY",
                side="buy",
                quantity=1.0,
                order_type="market",
                time_in_force="day",
                estimated_unit_price=450.0,
                estimated_order_value=450.0,
                strategy_name=strategy_name,
                strategy_stage=stage,
                strategy_config_path="configs/test.yaml",
                submitted_by="operator",
                broker_order_id=None,
                broker_status=None,
                message=None,
            )
        )


def _default_kwargs(store: EventStore, **overrides):
    base = {
        "strategy_id": _STRATEGY_ID,
        "from_stage": "paper",
        "to_stage": "micro_live",
        "manifest_hash": _MANIFEST_HASH,
        "backtest_run_id": None,
        "recommendation": "ready for micro_live",
        "known_risks": ["manifest drift if YAML edited after freeze"],
        "promotion_type": "statistical",
        "gate_check_outcome": {"sharpe_ok": True, "dd_ok": True, "trades_ok": True},
        "metrics_snapshot": {
            "sharpe_ratio": 0.9,
            "max_drawdown_pct": 8.0,
            "trade_count": 42,
        },
        "event_store": store,
        "now": _ASSEMBLED_AT,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Construction + JSON round-trip
# ---------------------------------------------------------------------------


def test_evidence_package_round_trips_through_json():
    package = EvidencePackage(
        strategy_id=_STRATEGY_ID,
        from_stage="backtest",
        to_stage="paper",
        manifest_hash=_MANIFEST_HASH,
        backtest_run_id="run-abc",
        backtest_run_started_at="2026-04-20T10:00:00+00:00",
        paper_trade_count=None,
        paper_rejection_count=None,
        kill_switch_trip_count=0,
        metrics_snapshot={"sharpe_ratio": 0.9, "max_drawdown_pct": 8.0, "trade_count": 42},
        recommendation="ship it",
        known_risks=["risk A"],
        promotion_type="statistical",
        gate_check_outcome={"sharpe_ok": True},
        assembled_at="2026-04-23T18:00:00+00:00",
    )

    as_dict = package.as_dict()
    assert as_dict["schema_version"] == 1
    payload = json.dumps(as_dict)
    round_tripped = json.loads(payload)
    assert round_tripped == as_dict


# ---------------------------------------------------------------------------
# Assembler derivations
# ---------------------------------------------------------------------------


def test_assemble_derives_backtest_run_started_at(tmp_path):
    store = _make_store(tmp_path)
    _seed_backtest_run(store)

    package = assemble_evidence_package(**_default_kwargs(store, backtest_run_id="run-abc"))

    assert package.backtest_run_id == "run-abc"
    assert package.backtest_run_started_at == "2026-04-20T10:00:00+00:00"


def test_assemble_handles_missing_backtest_run(tmp_path):
    store = _make_store(tmp_path)

    package = assemble_evidence_package(**_default_kwargs(store, backtest_run_id=None))

    assert package.backtest_run_id is None
    assert package.backtest_run_started_at is None


def test_assemble_derives_paper_trade_count_for_micro_live(tmp_path):
    store = _make_store(tmp_path)
    for _ in range(3):
        _seed_paper_trade(store)
    _seed_paper_trade(store, strategy_name="some.other.strategy.v1")

    package = assemble_evidence_package(**_default_kwargs(store, to_stage="micro_live"))

    assert package.paper_trade_count == 3


def test_assemble_skips_paper_counts_for_paper_target(tmp_path):
    store = _make_store(tmp_path)
    _seed_paper_trade(store)

    package = assemble_evidence_package(
        **_default_kwargs(store, from_stage="backtest", to_stage="paper")
    )

    assert package.paper_trade_count is None
    assert package.paper_rejection_count is None


def test_assemble_derives_paper_rejection_count(tmp_path):
    store = _make_store(tmp_path)
    _seed_paper_trade(store, risk_allowed=True)
    _seed_paper_trade(store, risk_allowed=False, status="blocked")
    _seed_paper_trade(store, risk_allowed=False, status="blocked")

    package = assemble_evidence_package(**_default_kwargs(store, to_stage="micro_live"))

    assert package.paper_rejection_count == 2


def test_assemble_counts_kill_switch_trips(tmp_path):
    store = _make_store(tmp_path)
    for _ in range(2):
        store.append_kill_switch_event(
            KillSwitchEvent(
                event_type="activated",
                recorded_at=_ASSEMBLED_AT,
                reason="daily loss",
            )
        )
    store.append_kill_switch_event(
        KillSwitchEvent(
            event_type="reset",
            recorded_at=_ASSEMBLED_AT,
            reason=None,
        )
    )

    package = assemble_evidence_package(**_default_kwargs(store))

    assert package.kill_switch_trip_count == 2


def test_assemble_stamps_assembled_at(tmp_path):
    store = _make_store(tmp_path)

    package = assemble_evidence_package(**_default_kwargs(store))

    assert package.assembled_at == "2026-04-23T18:00:00+00:00"


# ---------------------------------------------------------------------------
# Required-field refusal
# ---------------------------------------------------------------------------


def test_assemble_rejects_empty_recommendation(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="recommendation"):
        assemble_evidence_package(**_default_kwargs(store, recommendation="   "))


def test_assemble_rejects_empty_risks_list(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="known_risks"):
        assemble_evidence_package(**_default_kwargs(store, known_risks=[]))


def test_assemble_rejects_blank_risk_entry(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="known_risks"):
        assemble_evidence_package(**_default_kwargs(store, known_risks=["real risk", "  "]))
