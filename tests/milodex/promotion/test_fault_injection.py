"""Tests for the synthetic fault-injection self-test (R-PRM-004 criterion c, ADR 0058 M4).

The fault-check must run the REAL risk layer against a deliberately oversized
synthetic intent, record the veto with a synthetic marker, and SCREAM (record
nothing satisfying) if the risk layer ever approves it or vetoes for the wrong
reason.
"""

from __future__ import annotations

import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from milodex.core.event_store import EventStore
from milodex.promotion.fault_injection import (
    EXPECTED_GUARDRAIL_REASON_CODE,
    SYNTHETIC_FAULT_DECISION_TYPE,
    SYNTHETIC_FAULT_SUBMITTED_BY,
    SyntheticFaultApprovedError,
    SyntheticFaultGuardrailError,
    run_synthetic_fault_injection,
)
from milodex.risk.models import RiskCheckResult, RiskDecision

_STRATEGY_ID = "regime.daily.sma200_rotation.spy_shy.v1"
_NOW = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def _write_config(tmp_path: Path, *, stage: str = "backtest") -> Path:
    path = tmp_path / "regime.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            strategy:
              id: "{_STRATEGY_ID}"
              family: "regime"
              template: "daily.sma200_rotation"
              variant: "spy_shy"
              version: 1
              description: "test"
              enabled: true
              universe:
                - "SPY"
                - "SHY"
              parameters:
                ma_filter_length: 200
                risk_on_symbol: "SPY"
                risk_off_symbol: "SHY"
                allocation_pct: 0.09
              tempo:
                bar_size: "1D"
                min_hold_days: 1
                max_hold_days: null
              risk:
                max_position_pct: 0.10
                max_positions: 1
                daily_loss_cap_pct: 0.05
                stop_loss_pct: null
              stage: "{stage}"
              backtest:
                slippage_pct: 0.001
                commission_per_trade: 0.00
                min_trades_required: null
                walk_forward_windows: 1
              disable_conditions_additional: []
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return path


class _AllowAllEvaluator:
    """A stub that approves everything — simulates a risk-layer regression."""

    def evaluate(self, context):  # noqa: ARG002 — deliberately ignores the context
        return RiskDecision(allowed=True, summary="Allowed", checks=[], reason_codes=[])


class _WrongReasonEvaluator:
    """A stub that vetoes, but not via the targeted fat-finger guardrail."""

    def evaluate(self, context):  # noqa: ARG002
        return RiskDecision(
            allowed=False,
            summary="Blocked",
            checks=[RiskCheckResult("market_open", False, "closed", "market_closed")],
            reason_codes=["market_closed"],
        )


def test_fault_injection_records_marked_veto(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg = _write_config(tmp_path)

    result = run_synthetic_fault_injection(_STRATEGY_ID, cfg, store, now=_NOW)

    assert result.strategy_id == _STRATEGY_ID
    assert result.explanation_id is not None
    # The targeted fat-finger guardrail must be among the reasons.
    assert EXPECTED_GUARDRAIL_REASON_CODE in result.reason_codes

    # The recorded row is durably marked synthetic and is a genuine veto.
    rows = store.list_explanations()
    synthetic = [r for r in rows if r.decision_type == SYNTHETIC_FAULT_DECISION_TYPE]
    assert len(synthetic) == 1
    row = synthetic[0]
    assert row.risk_allowed is False
    assert row.strategy_name == _STRATEGY_ID
    assert row.submitted_by == SYNTHETIC_FAULT_SUBMITTED_BY
    assert row.context["synthetic_fault_injection"] is True
    assert row.context["expected_reason_code"] == EXPECTED_GUARDRAIL_REASON_CODE
    assert EXPECTED_GUARDRAIL_REASON_CODE in row.reason_codes


def test_fault_injection_queryable_by_criterion_c(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg = _write_config(tmp_path)
    run_synthetic_fault_injection(_STRATEGY_ID, cfg, store, now=_NOW)

    record = store.get_latest_synthetic_fault_injection_veto(_STRATEGY_ID)
    assert record is not None
    assert record.risk_allowed is False
    # A different strategy id finds nothing.
    assert store.get_latest_synthetic_fault_injection_veto("other.strat.v1") is None


def test_fault_injection_screams_when_risk_layer_approves(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg = _write_config(tmp_path)

    with pytest.raises(SyntheticFaultApprovedError):
        run_synthetic_fault_injection(
            _STRATEGY_ID, cfg, store, risk_evaluator=_AllowAllEvaluator(), now=_NOW
        )

    # Nothing satisfying was recorded — the criterion must stay UNMET.
    assert store.get_latest_synthetic_fault_injection_veto(_STRATEGY_ID) is None
    assert store.list_explanations() == []


def test_fault_injection_screams_when_wrong_guardrail_fires(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg = _write_config(tmp_path)

    with pytest.raises(SyntheticFaultGuardrailError):
        run_synthetic_fault_injection(
            _STRATEGY_ID, cfg, store, risk_evaluator=_WrongReasonEvaluator(), now=_NOW
        )

    assert store.get_latest_synthetic_fault_injection_veto(_STRATEGY_ID) is None
    assert store.list_explanations() == []
