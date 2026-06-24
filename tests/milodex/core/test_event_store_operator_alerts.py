from datetime import UTC, datetime

from milodex.core.event_store import (
    MIN_COMPATIBLE_SCHEMA_VERSION,
    EventStore,
    OperatorAlertEvent,
)


def test_schema_version_is_17_after_operator_alerts_migration(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.schema_version == 17


def test_min_compatible_schema_unchanged(tmp_path):
    assert MIN_COMPATIBLE_SCHEMA_VERSION == 12


def test_append_and_list_operator_alert_roundtrip(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    new_id = store.append_operator_alert(
        OperatorAlertEvent(
            alert_type="exit_intent_dropped",
            severity="warning",
            summary="EXIT intent for SPY dropped: clean-handoff ambiguity.",
            strategy_id="rsi2.mr.etf.v1",
            session_id="sess-1",
            symbol="SPY",
            side="sell",
            context_json={
                "reason": "no_clean_handoff",
                "idempotency_key": "rsi2.mr.etf.v1|2026-06-22|sell|SPY",
            },
            recorded_at=datetime(2026, 6, 23, 15, tzinfo=UTC),
        )
    )
    assert isinstance(new_id, int)
    rows = store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(rows) == 1
    assert rows[0].symbol == "SPY" and rows[0].severity == "warning"
    assert rows[0].context_json["reason"] == "no_clean_handoff"


def test_list_operator_alerts_filters_by_type(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    base = dict(severity="info", summary="x", recorded_at=datetime(2026, 6, 23, tzinfo=UTC))
    store.append_operator_alert(OperatorAlertEvent(alert_type="a", **base))
    store.append_operator_alert(OperatorAlertEvent(alert_type="b", **base))
    assert len(store.list_operator_alerts(alert_type="a")) == 1
    assert len(store.list_operator_alerts()) == 2
