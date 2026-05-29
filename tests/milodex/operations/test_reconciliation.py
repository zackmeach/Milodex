"""Unit tests for ``milodex.operations.reconciliation`` helpers.

Focused on ``incident_already_logged``, the startup-reconciliation idempotency
check that every runner hits. The 2026-05-29 OOM-freeze incident
(`docs/incidents/2026-05-29-runner-fleet-oom-freeze.md`) traced to this function
materializing the entire ``explanations`` table; these tests pin the behaviour and
guard against a regression back to a full-table load.
"""

from __future__ import annotations

from datetime import UTC, datetime

from milodex.core.event_store import EventStore, ExplanationEvent
from milodex.operations.reconciliation import incident_already_logged


def _incident_kwargs(**overrides) -> dict:
    """Minimal valid ``reconcile_incident`` ExplanationEvent payload."""
    recorded_at = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
    base = {
        "recorded_at": recorded_at,
        "decision_type": "reconcile_incident",
        "status": "incident",
        "strategy_name": None,
        "strategy_stage": None,
        "strategy_config_path": None,
        "config_hash": "hash-A",
        "symbol": "SYSTEM",
        "side": "hold",
        "quantity": 0.0,
        "order_type": "none",
        "time_in_force": "day",
        "submitted_by": "reconcile",
        "market_open": True,
        "latest_bar_timestamp": None,
        "latest_bar_close": None,
        "account_equity": 0.0,
        "account_cash": 0.0,
        "account_portfolio_value": 0.0,
        "account_daily_pnl": 0.0,
        "risk_allowed": False,
        "risk_summary": "incident",
        "reason_codes": [],
        "risk_checks": [],
        "context": {},
    }
    base.update(overrides)
    return base


def test_incident_already_logged_true_when_latest_matches(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_explanation(ExplanationEvent(**_incident_kwargs(config_hash="hash-A")))
    assert incident_already_logged(store, "hash-A") is True


def test_incident_already_logged_false_when_no_incident(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert incident_already_logged(store, "hash-A") is False


def test_incident_already_logged_uses_only_the_most_recent_incident(tmp_path):
    """Only the latest incident counts; an older matching hash must not."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_explanation(ExplanationEvent(**_incident_kwargs(config_hash="hash-A")))
    store.append_explanation(ExplanationEvent(**_incident_kwargs(config_hash="hash-B")))
    assert incident_already_logged(store, "hash-A") is False
    assert incident_already_logged(store, "hash-B") is True


def test_incident_already_logged_does_not_load_all_explanations(tmp_path, monkeypatch):
    """Regression guard for the 2026-05-29 OOM freeze: the startup idempotency
    check must NOT materialize the whole explanations table."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_explanation(ExplanationEvent(**_incident_kwargs(config_hash="hash-A")))

    def _boom(*_args, **_kwargs):
        raise AssertionError("incident_already_logged must not call list_explanations()")

    monkeypatch.setattr(store, "list_explanations", _boom)
    assert incident_already_logged(store, "hash-A") is True
