"""Tests for R-PRM-004 lifecycle-criteria enforcement (ADR 0058 M4 addendum).

Every criterion has three cardinal states — satisfied / unmet / unevaluatable —
and each must fail closed with an actionable message. The zero-signals case must
SATISFY criterion (b) (the regime strategy legitimately produces no signals), and
the integer-FK join must be the one used (a UUID-column join silently returns
nothing).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from milodex.core.event_store import BacktestRunEvent, EventStore, ExplanationEvent
from milodex.promotion.fault_injection import SYNTHETIC_FAULT_DECISION_TYPE
from milodex.promotion.lifecycle_criteria import evaluate_lifecycle_criteria
from milodex.promotion.policy import ACTIVE_PROMOTION_POLICY

_STRATEGY_ID = "regime.daily.sma200_rotation.spy_shy.v1"
_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
_MAX_AGE = ACTIVE_PROMOTION_POLICY.lifecycle_gate.evidence_max_age_days


def _outcome(result, criterion: str):
    return next(o for o in result.outcomes if o.criterion == criterion)


def _seed_completed_run(
    store: EventStore,
    *,
    run_id: str = "run-1",
    started_at: datetime,
    metadata: dict,
) -> int:
    return store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=_STRATEGY_ID,
            config_path="configs/regime.yaml",
            config_hash="h",
            start_date=started_at - timedelta(days=30),
            end_date=started_at,
            started_at=started_at,
            status="completed",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata=metadata,
        )
    )


def _seed_explanation(store: EventStore, *, backtest_run_id: int) -> None:
    store.append_explanation(
        ExplanationEvent(
            recorded_at=_NOW,
            decision_type="submit",
            status="submitted",
            strategy_name=_STRATEGY_ID,
            strategy_stage="backtest",
            strategy_config_path="configs/regime.yaml",
            config_hash="h",
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="backtest_engine",
            market_open=True,
            latest_bar_timestamp=_NOW,
            latest_bar_close=100.0,
            account_equity=1000.0,
            account_cash=1000.0,
            account_portfolio_value=1000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="ok",
            reason_codes=[],
            risk_checks=[],
            context={},
            session_id=None,
            backtest_run_id=backtest_run_id,
        )
    )


def _seed_fault_veto(store: EventStore, *, recorded_at: datetime) -> None:
    store.append_explanation(
        ExplanationEvent(
            recorded_at=recorded_at,
            decision_type=SYNTHETIC_FAULT_DECISION_TYPE,
            status="blocked",
            strategy_name=_STRATEGY_ID,
            strategy_stage="backtest",
            strategy_config_path="configs/regime.yaml",
            config_hash=None,
            symbol="SYNTHETIC",
            side="buy",
            quantity=10000.0,
            order_type="market",
            time_in_force="day",
            submitted_by="promotion_fault_check",
            market_open=True,
            latest_bar_timestamp=recorded_at,
            latest_bar_close=1000.0,
            account_equity=1000.0,
            account_cash=1000.0,
            account_portfolio_value=1000.0,
            account_daily_pnl=0.0,
            risk_allowed=False,
            risk_summary="Blocked by risk checks",
            reason_codes=["max_order_value_exceeded"],
            risk_checks=[],
            context={"synthetic_fault_injection": True},
            session_id=None,
            backtest_run_id=None,
        )
    )


# ---------------------------------------------------------------------------
# Criterion (a) — a successful deterministic backtest run
# ---------------------------------------------------------------------------


def test_criterion_a_unmet_when_no_run(tmp_path):
    store = EventStore(tmp_path / "m.db")
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    a = _outcome(result, "a")
    assert a.satisfied is False
    assert "No successful" in a.detail
    assert not result.satisfied


def test_criterion_a_unmet_when_only_failed_run(tmp_path):
    store = EventStore(tmp_path / "m.db")
    # A failed run must not stand in as success evidence.
    store.append_backtest_run(
        BacktestRunEvent(
            run_id="failed-1",
            strategy_id=_STRATEGY_ID,
            config_path="configs/regime.yaml",
            config_hash="h",
            start_date=_NOW - timedelta(days=2),
            end_date=_NOW,
            started_at=_NOW - timedelta(days=1),
            status="failed",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={"signal_count": 0},
        )
    )
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    assert _outcome(result, "a").satisfied is False


def test_criterion_a_unmet_when_run_is_stale(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_completed_run(
        store,
        started_at=_NOW - timedelta(days=_MAX_AGE + 5),
        metadata={"signal_count": 0},
    )
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    a = _outcome(result, "a")
    assert a.satisfied is False
    assert "freshness bound" in a.detail
    assert str(_MAX_AGE) in a.detail


def test_criterion_a_satisfied_when_recent_completed_run(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_completed_run(store, started_at=_NOW - timedelta(days=3), metadata={"signal_count": 0})
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    assert _outcome(result, "a").satisfied is True


# ---------------------------------------------------------------------------
# Criterion (b) — explanation records for every simulated signal
# ---------------------------------------------------------------------------


def test_criterion_b_zero_signals_is_satisfied(tmp_path):
    """The regime strategy legitimately yields zero signals — vacuously satisfied."""
    store = EventStore(tmp_path / "m.db")
    _seed_completed_run(store, started_at=_NOW - timedelta(days=1), metadata={"signal_count": 0})
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    b = _outcome(result, "b")
    assert b.satisfied is True
    assert "vacuously satisfied" in b.detail


def test_criterion_b_unevaluatable_without_signal_count_metadata(tmp_path):
    """A pre-enforcement run lacking signal_count cannot be evaluated → fail closed."""
    store = EventStore(tmp_path / "m.db")
    _seed_completed_run(store, started_at=_NOW - timedelta(days=1), metadata={"trade_count": 3})
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    b = _outcome(result, "b")
    assert b.satisfied is False
    assert "signal-count metadata" in b.detail


def test_criterion_b_unevaluatable_with_malformed_signal_count(tmp_path):
    """Malformed signal_count metadata refuses cleanly — never a traceback."""
    store = EventStore(tmp_path / "m.db")
    _seed_completed_run(
        store, started_at=_NOW - timedelta(days=1), metadata={"signal_count": "not-a-number"}
    )
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    b = _outcome(result, "b")
    assert b.satisfied is False
    assert "malformed signal-count metadata" in b.detail
    assert result.satisfied is False


def test_criterion_b_unmet_when_signals_but_no_explanations(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_completed_run(store, started_at=_NOW - timedelta(days=1), metadata={"signal_count": 3})
    # No explanation rows linked → missing audit trail.
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    b = _outcome(result, "b")
    assert b.satisfied is False
    assert "missing" in b.detail


def test_criterion_b_satisfied_uses_integer_fk_join_not_uuid(tmp_path):
    """Regression guard: (b) must join explanations on the INTEGER backtest_runs.id,
    NOT the UUID run_id. Explanations are linked here by the integer db id; the
    correct join finds them (SATISFIED). A join on the UUID run_id column would
    match zero rows and wrongly report (b) as UNMET — this test would then fail.
    """
    store = EventStore(tmp_path / "m.db")
    db_id = _seed_completed_run(
        store,
        run_id="0733d4d1-cafe-cafe-cafe-000000000000",  # UUID, ≠ the integer db id
        started_at=_NOW - timedelta(days=1),
        metadata={"signal_count": 2},
    )
    _seed_explanation(store, backtest_run_id=db_id)
    _seed_explanation(store, backtest_run_id=db_id)

    # The integer-FK count is authoritative and non-zero.
    assert store.count_explanations_for_backtest_run(db_id) == 2

    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    b = _outcome(result, "b")
    assert b.satisfied is True, (
        "criterion (b) must use the integer backtest_runs.id join; a UUID-column "
        "join returns zero rows and would wrongly report UNMET"
    )
    assert b.evidence["signal_count"] == 2
    assert b.evidence["explanation_count"] == 2


# ---------------------------------------------------------------------------
# Criterion (c) — synthetic fault-injection veto
# ---------------------------------------------------------------------------


def test_criterion_c_unmet_when_no_fault_veto(tmp_path):
    store = EventStore(tmp_path / "m.db")
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    c = _outcome(result, "c")
    assert c.satisfied is False
    assert "fault-check" in c.detail


def test_criterion_c_unmet_when_veto_is_stale(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_fault_veto(store, recorded_at=_NOW - timedelta(days=_MAX_AGE + 5))
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    c = _outcome(result, "c")
    assert c.satisfied is False
    assert "freshness bound" in c.detail


def test_criterion_c_satisfied_with_recent_veto(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_fault_veto(store, recorded_at=_NOW - timedelta(days=1))
    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    assert _outcome(result, "c").satisfied is True


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


def test_all_three_satisfied_is_aggregate_satisfied(tmp_path):
    store = EventStore(tmp_path / "m.db")
    _seed_completed_run(store, started_at=_NOW - timedelta(days=2), metadata={"signal_count": 0})
    _seed_fault_veto(store, recorded_at=_NOW - timedelta(days=1))

    result = evaluate_lifecycle_criteria(_STRATEGY_ID, store, now=_NOW)
    assert result.satisfied is True
    assert result.failing() == []
    evidence = result.as_evidence_dict()
    assert evidence["enforced"] is True
    assert evidence["satisfied"] is True
    assert evidence["evidence_max_age_days"] == _MAX_AGE
    assert len(evidence["criteria"]) == 3
