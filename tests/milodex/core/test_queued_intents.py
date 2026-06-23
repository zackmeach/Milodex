"""Tests for the queued-intents lifecycle store (queue-at-open, migration 016)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from milodex.core.event_store import EventStore, QueuedIntentEvent

_NOW = datetime(2026, 6, 23, 14, 0, tzinfo=UTC)


def _intent(idempotency_key: str = "rsi2.v1|2026-06-23|buy|SPY", **overrides) -> QueuedIntentEvent:
    fields: dict[str, object] = {
        "idempotency_key": idempotency_key,
        "strategy_id": "rsi2.v1",
        "strategy_config_path": "configs/rsi2.yaml",
        "config_hash": "c" * 64,
        "session_id": "sess-A",
        "trading_session": "2026-06-23",
        "locked_in_bar_timestamp": "2026-06-22T20:00:00+00:00",
        "symbol": "SPY",
        "side": "buy",
        "intent_class": "entry",
        "notional_pct": 0.1,
        "expected_stage": "paper",
        "expected_max_positions": 3,
        "expected_max_position_pct": 0.2,
        "expected_daily_loss_cap_pct": 0.05,
        "intent_payload_json": {"qty": 4, "limit_price": 500.0},
        "reasoning_json": {"signal": "rsi<10"},
        "created_at": datetime(2026, 6, 22, 20, 0, tzinfo=UTC),
        "expires_at": datetime(2026, 6, 23, 19, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return QueuedIntentEvent(**fields)  # type: ignore[arg-type]


def _seed_run(db_path, session_id: str, exit_reason):
    import sqlite3

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO strategy_runs (session_id, strategy_id, started_at, ended_at, "
            "exit_reason, metadata_json) VALUES (?, 'rsi2.v1', ?, ?, ?, '{}')",
            (session_id, "2026-06-22T20:00:00+00:00", "2026-06-22T20:05:00+00:00", exit_reason),
        )
        con.commit()


# ─── Schema / table presence (Task 1) ───────────────────────────────────────


def test_schema_version_is_16_after_construction(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.schema_version == 16


def test_queued_intents_table_exists(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert "queued_intents" in store.list_table_names()


def test_queued_intents_indexes_exist(tmp_path):
    """The two access-path indexes are part of the migration contract.

    idx_queued_intents_status_expires backs the get_active_queued_intents
    status+expiry scan; idx_queued_intents_strategy_status backs the per-
    strategy active read.
    """
    import sqlite3

    db = tmp_path / "milodex.db"
    EventStore(db)
    with sqlite3.connect(db) as con:
        names = {row[1] for row in con.execute("PRAGMA index_list(queued_intents)")}
    assert "idx_queued_intents_status_expires" in names
    assert "idx_queued_intents_strategy_status" in names


# ─── Roundtrip / append (Tasks 2-3) ─────────────────────────────────────────


def test_append_then_active_roundtrips_all_fields(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    new_id = store.append_queued_intent(_intent())
    assert isinstance(new_id, int)

    active = store.get_active_queued_intents(
        "rsi2.v1",
        now=datetime(2026, 6, 23, 14, 0, tzinfo=UTC),
        running_session_id="sess-A",
    )
    assert len(active) == 1
    e = active[0]
    assert e.id == new_id
    assert e.idempotency_key == "rsi2.v1|2026-06-23|buy|SPY"
    assert e.symbol == "SPY"
    assert e.side == "buy"
    assert e.intent_class == "entry"
    assert e.notional_pct == 0.1
    assert e.expected_max_positions == 3
    assert e.expected_daily_loss_cap_pct == 0.05
    assert e.intent_payload_json == {"qty": 4, "limit_price": 500.0}
    assert e.reasoning_json == {"signal": "rsi<10"}
    assert e.status == "queued"
    assert e.consumed_at is None and e.consumed_by is None


def test_unique_idempotency_key_rejects_duplicate(tmp_path):
    import sqlite3

    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    with pytest.raises(sqlite3.IntegrityError):
        store.append_queued_intent(_intent())


def test_json_and_nullable_fields_roundtrip(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(
        _intent("k|s|buy|QQQ", symbol="QQQ", intent_payload_json=None, reasoning_json=None)
    )
    active = store.get_active_queued_intents(
        "rsi2.v1", now=datetime(2026, 6, 23, 14, 0, tzinfo=UTC), running_session_id="sess-A"
    )
    match = [e for e in active if e.symbol == "QQQ"][0]
    assert match.intent_payload_json is None
    assert match.reasoning_json is None


def test_get_queued_intent_by_id_roundtrips(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    new_id = store.append_queued_intent(_intent())
    fetched = store.get_queued_intent(new_id)
    assert fetched is not None
    assert fetched.id == new_id
    assert fetched.idempotency_key == "rsi2.v1|2026-06-23|buy|SPY"


def test_get_queued_intent_unknown_id_returns_none(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.get_queued_intent(999) is None


def test_shared_append_helper_persists_a_drainable_intent(tmp_path):
    """The shared conftest helper (imported by Phases 3/5/6) round-trips."""
    from tests.milodex.core.conftest import _append_queued_intent

    store = EventStore(tmp_path / "milodex.db")
    new_id = _append_queued_intent(store, idempotency_key="rsi2.v1|2026-06-23|buy|SPY")
    fetched = store.get_queued_intent(new_id)
    assert fetched is not None
    assert fetched.idempotency_key == "rsi2.v1|2026-06-23|buy|SPY"
    assert fetched.status == "queued"


# ─── get_active_queued_intents fence matrix (Task 4) ─────────────────────────


def test_expired_intent_is_not_active(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(expires_at=datetime(2026, 6, 23, 13, 0, tzinfo=UTC)))
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A") == []


def test_same_session_intent_is_active(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(session_id="sess-A"))
    active = store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A")
    assert len(active) == 1


def test_cross_session_controlled_stop_is_active(tmp_path):
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    store.append_queued_intent(_intent(session_id="sess-OLD"))
    _seed_run(db, "sess-OLD", "controlled_stop")
    active = store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-NEW")
    assert len(active) == 1


@pytest.mark.parametrize(
    "exit_reason", ["interrupted", "crashed", "kill_switch", "orphan_recovered", None]
)
def test_cross_session_dirty_exit_is_dropped(tmp_path, exit_reason):
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    store.append_queued_intent(_intent(session_id="sess-OLD"))
    _seed_run(db, "sess-OLD", exit_reason)
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-NEW") == []


def test_cross_session_no_run_row_is_dropped(tmp_path):
    """No strategy_runs row for the originating session => not controlled_stop => DROP."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(session_id="sess-GHOST"))
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-NEW") == []


def test_consumed_intent_is_not_active(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    store.mark_queued_intent_consumed(
        "rsi2.v1|2026-06-23|buy|SPY", consumed_by="sess-A", consumed_at=_NOW
    )
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A") == []


def test_scoped_to_strategy_id(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(
        _intent(strategy_id="other.v1", idempotency_key="other.v1|s|buy|SPY")
    )
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A") == []


# ─── consume CAS + expire/obsolete transitions (Task 5) ──────────────────────


def test_consume_cas_returns_one_then_zero(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    key = "rsi2.v1|2026-06-23|buy|SPY"

    first = store.mark_queued_intent_consumed(key, consumed_by="sess-A", consumed_at=_NOW)
    assert first == 1
    # Second CAS on the same (now non-'queued') row loses: rowcount 0.
    second = store.mark_queued_intent_consumed(key, consumed_by="sess-B", consumed_at=_NOW)
    assert second == 0


def test_consume_sets_audit_columns(tmp_path):
    import sqlite3

    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    store.mark_queued_intent_consumed(
        "rsi2.v1|2026-06-23|buy|SPY", consumed_by="sess-A", consumed_at=_NOW
    )
    with sqlite3.connect(tmp_path / "milodex.db") as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM queued_intents").fetchone()
    assert row["status"] == "consumed"
    assert row["consumed_by"] == "sess-A"
    assert row["consumed_at"] == _NOW.isoformat()


def test_consume_unknown_key_returns_zero(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.mark_queued_intent_consumed("nope", consumed_by="x", consumed_at=_NOW) == 0


def test_mark_expired_and_obsolete(tmp_path):
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    eid = store.append_queued_intent(_intent())
    store.mark_queued_intent_expired(eid)
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A") == []

    eid2 = store.append_queued_intent(_intent("rsi2.v1|2026-06-23|buy|IWM", symbol="IWM"))
    store.mark_queued_intent_obsolete(eid2)
    import sqlite3

    with sqlite3.connect(db) as con:
        rows = dict(con.execute("SELECT status, COUNT(*) FROM queued_intents GROUP BY status"))
    assert rows == {"expired": 1, "obsolete": 1}
