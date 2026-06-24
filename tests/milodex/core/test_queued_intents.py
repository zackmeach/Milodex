"""Tests for the queued-intents lifecycle store (queue-at-open, migration 016)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from milodex.core.event_store import EventStore, ExecutionAttemptEvent, QueuedIntentEvent
from milodex.strategies.loader import compute_config_hash

_NOW = datetime(2026, 6, 23, 14, 0, tzinfo=UTC)

# Populated per-test by the autouse fixture below so a default-built intent points
# at a real on-disk config whose stored hash MATCHES — required to survive the I-7
# config_hash guard in get_active_queued_intents. Drop-branch tests override these.
_DEFAULT_CFG_PATH = "configs/rsi2.yaml"
_DEFAULT_CFG_HASH = "c" * 64


@pytest.fixture(autouse=True)
def _seed_default_config(tmp_path):
    """Write a real default config under tmp_path and expose its (path, hash).

    The drain authority re-verifies a queued intent's config_hash against the
    on-disk config (I-7); a placeholder hash against a non-existent path would be
    dropped by the guard. This makes the shared ``_intent()`` default produce a
    guard-clean row.
    """
    global _DEFAULT_CFG_PATH, _DEFAULT_CFG_HASH
    cfg = tmp_path / "rsi2.yaml"
    cfg.write_text("strategy:\n  id: rsi2.mr.swing.v1\n", encoding="utf-8")
    _DEFAULT_CFG_PATH = str(cfg)
    _DEFAULT_CFG_HASH = compute_config_hash(cfg)
    yield


def _intent(idempotency_key: str = "rsi2.v1|2026-06-23|buy|SPY", **overrides) -> QueuedIntentEvent:
    fields: dict[str, object] = {
        "idempotency_key": idempotency_key,
        "strategy_id": "rsi2.v1",
        "strategy_config_path": _DEFAULT_CFG_PATH,
        "config_hash": _DEFAULT_CFG_HASH,
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


def _status_of(db_path, idempotency_key: str) -> str | None:
    import sqlite3

    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT status FROM queued_intents WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
    return None if row is None else row[0]


# ─── Schema / table presence (Task 1) ───────────────────────────────────────


def test_schema_version_is_17_after_construction(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.schema_version == 17


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
        "rsi2.v1|2026-06-23|buy|SPY",
        now=_NOW,
        running_session_id="sess-A",
        consumed_by="sess-A",
        consumed_at=_NOW,
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

    first = store.mark_queued_intent_consumed(
        key, now=_NOW, running_session_id="sess-A", consumed_by="sess-A", consumed_at=_NOW
    )
    assert first == 1
    # Second CAS on the same (now non-'queued') row loses: rowcount 0.
    second = store.mark_queued_intent_consumed(
        key, now=_NOW, running_session_id="sess-A", consumed_by="sess-B", consumed_at=_NOW
    )
    assert second == 0


def test_consume_sets_audit_columns(tmp_path):
    import sqlite3

    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    store.mark_queued_intent_consumed(
        "rsi2.v1|2026-06-23|buy|SPY",
        now=_NOW,
        running_session_id="sess-A",
        consumed_by="sess-A",
        consumed_at=_NOW,
    )
    with sqlite3.connect(tmp_path / "milodex.db") as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM queued_intents").fetchone()
    assert row["status"] == "consumed"
    assert row["consumed_by"] == "sess-A"
    assert row["consumed_at"] == _NOW.isoformat()


def test_consume_unknown_key_returns_zero(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert (
        store.mark_queued_intent_consumed(
            "nope", now=_NOW, running_session_id="sess-A", consumed_by="x", consumed_at=_NOW
        )
        == 0
    )


# ─── consume CAS re-asserts the FULL drain predicates (P1-1) ─────────────────
#
# The CAS authorizes the broker submit. Its WHERE must re-assert the SAME fences
# get_active_queued_intents filters on (expiry + clean-handoff + config_hash) so
# an expired-but-still-'queued' row (sweep not yet run), an unclean-handoff row,
# or a config-drifted row cannot be claimed and submitted via a TOCTOU between
# enumerate and consume, or a direct caller bypassing get_active.


def test_consume_drops_expired_but_still_queued_row(tmp_path):
    """Expired (expires_at <= now) but still status='queued' -> CAS returns 0.

    The expiry sweep may not have run yet; the CAS must not claim a row whose
    open window has passed.
    """
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(expires_at=datetime(2026, 6, 23, 13, 0, tzinfo=UTC)))
    assert (
        store.mark_queued_intent_consumed(
            "rsi2.v1|2026-06-23|buy|SPY",
            now=_NOW,  # 14:00 > 13:00 expiry
            running_session_id="sess-A",
            consumed_by="sess-A",
            consumed_at=_NOW,
        )
        == 0
    )
    # And it remains 'queued' (untouched), not 'consumed'.
    assert _status_of(tmp_path / "milodex.db", "rsi2.v1|2026-06-23|buy|SPY") == "queued"


@pytest.mark.parametrize(
    "exit_reason", ["interrupted", "crashed", "kill_switch", "orphan_recovered", None]
)
def test_consume_drops_unclean_handoff_row(tmp_path, exit_reason):
    """Cross-session row whose originating run did NOT controlled_stop -> 0.

    Same clean-handoff fence get_active uses: a different running session may
    only consume a prior session's row if that session exited via
    controlled_stop. A dirty/no-row exit fails closed.
    """
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    store.append_queued_intent(_intent(session_id="sess-OLD"))
    _seed_run(db, "sess-OLD", exit_reason)
    assert (
        store.mark_queued_intent_consumed(
            "rsi2.v1|2026-06-23|buy|SPY",
            now=_NOW,
            running_session_id="sess-NEW",
            consumed_by="sess-NEW",
            consumed_at=_NOW,
        )
        == 0
    )


def test_consume_allows_cross_session_controlled_stop_row(tmp_path):
    """Clean handoff (originating run controlled_stop) -> CAS succeeds (1)."""
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    store.append_queued_intent(_intent(session_id="sess-OLD"))
    _seed_run(db, "sess-OLD", "controlled_stop")
    assert (
        store.mark_queued_intent_consumed(
            "rsi2.v1|2026-06-23|buy|SPY",
            now=_NOW,
            running_session_id="sess-NEW",
            consumed_by="sess-NEW",
            consumed_at=_NOW,
        )
        == 1
    )


def test_consume_drops_config_drifted_row(tmp_path):
    """Stored config_hash != recomputed on-disk hash -> CAS returns 0.

    The config drifted after enumeration (or never matched). The intent must
    replay against the EXACT config it was evaluated under, or not at all.
    """
    store = EventStore(tmp_path / "milodex.db")
    # Stored hash deliberately does NOT match the on-disk config's real hash.
    store.append_queued_intent(_intent(config_hash="d" * 64))
    assert (
        store.mark_queued_intent_consumed(
            "rsi2.v1|2026-06-23|buy|SPY",
            now=_NOW,
            running_session_id="sess-A",
            consumed_by="sess-A",
            consumed_at=_NOW,
        )
        == 0
    )


def test_consume_drops_when_config_path_unhashable(tmp_path):
    """Config path missing/unreadable (recompute None) -> CAS returns 0."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(
        _intent(strategy_config_path=str(tmp_path / "does_not_exist.yaml"))
    )
    assert (
        store.mark_queued_intent_consumed(
            "rsi2.v1|2026-06-23|buy|SPY",
            now=_NOW,
            running_session_id="sess-A",
            consumed_by="sess-A",
            consumed_at=_NOW,
        )
        == 0
    )


def test_consume_happy_path_active_clean_matching_config(tmp_path):
    """Active + same-session + matching config -> CAS returns 1, row consumed."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    assert (
        store.mark_queued_intent_consumed(
            "rsi2.v1|2026-06-23|buy|SPY",
            now=_NOW,
            running_session_id="sess-A",
            consumed_by="sess-A",
            consumed_at=_NOW,
        )
        == 1
    )
    assert _status_of(tmp_path / "milodex.db", "rsi2.v1|2026-06-23|buy|SPY") == "consumed"


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


# ─── expiry sweep (Phase 7) ──────────────────────────────────────────────────
#
# Launch-time bulk flip of stale 'queued' rows to 'expired'. Housekeeping only:
# get_active_queued_intents already excludes expired rows from the drain, so the
# sweep settles durable status for audit — it never gates a trade.


def test_expire_stale_flips_only_expired_queued_rows(tmp_path):
    """A stale (expires_at <= now) queued row flips to 'expired'; a fresh one stays 'queued'."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(
        _intent(
            "rsi2.v1|2026-06-23|buy|SPY",
            symbol="SPY",
            expires_at=datetime(2026, 6, 23, 13, 0, tzinfo=UTC),  # past _NOW (14:00) -> stale
        )
    )
    store.append_queued_intent(
        _intent(
            "rsi2.v1|2026-06-23|buy|QQQ",
            symbol="QQQ",
            expires_at=datetime(2026, 6, 23, 19, 0, tzinfo=UTC),  # future -> fresh
        )
    )

    assert len(store.expire_stale_queued_intents(now=_NOW)) == 1

    expired = store.list_queued_intents_by_status("expired")
    queued = store.list_queued_intents_by_status("queued")
    assert [e.symbol for e in expired] == ["SPY"]
    assert [e.symbol for e in queued] == ["QQQ"]


def test_expire_stale_never_touches_consumed_or_obsolete(tmp_path):
    """A 'consumed' and an 'obsolete' row, both with past expires_at, are left untouched."""
    import sqlite3

    db = tmp_path / "milodex.db"
    store = EventStore(db)
    past = datetime(2026, 6, 23, 13, 0, tzinfo=UTC)  # <= _NOW

    obsolete_id = store.append_queued_intent(
        _intent("rsi2.v1|2026-06-23|buy|IWM", symbol="IWM", expires_at=past)
    )
    store.mark_queued_intent_obsolete(obsolete_id)
    # Directly stamp a 'consumed' row with a PAST expiry (the consume CAS would
    # reject an already-expired row, so set status straight in the store).
    store.append_queued_intent(
        _intent("rsi2.v1|2026-06-23|buy|DIA", symbol="DIA", expires_at=past)
    )
    with sqlite3.connect(db) as con:
        con.execute(
            "UPDATE queued_intents SET status = 'consumed' "
            "WHERE idempotency_key = 'rsi2.v1|2026-06-23|buy|DIA'"
        )
        con.commit()

    assert store.expire_stale_queued_intents(now=_NOW) == []
    assert store.list_queued_intents_by_status("expired") == []
    assert {e.symbol for e in store.list_queued_intents_by_status("consumed")} == {"DIA"}
    assert {e.symbol for e in store.list_queued_intents_by_status("obsolete")} == {"IWM"}


# ─── expire_stale returns the swept rows (B1) ───────────────────────────────
#
# The sweep must RETURN the rows it swept (not just a count) so the runner can
# alert on any swept EXIT — a swept exit leaves 'queued' status, becoming
# invisible to the still-'queued'-only stranded-exit alerter.


def test_expire_stale_returns_swept_rows(tmp_path):
    """The sweep returns the QueuedIntentEvent rows it flipped, not just a count.

    The owning strategy_id and idempotency_key must survive so a cross-strategy
    observer can attribute and alert on a swept EXIT.
    """
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(
        _intent(
            "rsi2.v1|2026-06-23|sell|SPY",
            symbol="SPY",
            side="sell",
            intent_class="exit",
            expires_at=datetime(2026, 6, 23, 13, 0, tzinfo=UTC),  # past _NOW -> stale
        )
    )
    store.append_queued_intent(
        _intent(
            "rsi2.v1|2026-06-23|buy|QQQ",
            symbol="QQQ",
            expires_at=datetime(2026, 6, 23, 19, 0, tzinfo=UTC),  # future -> fresh
        )
    )

    swept = store.expire_stale_queued_intents(now=_NOW)

    assert [e.symbol for e in swept] == ["SPY"]
    assert swept[0].intent_class == "exit"
    assert swept[0].strategy_id == "rsi2.v1"
    assert swept[0].idempotency_key == "rsi2.v1|2026-06-23|sell|SPY"
    # The returned set equals the set actually flipped to 'expired'.
    assert {e.idempotency_key for e in swept} == {
        e.idempotency_key for e in store.list_queued_intents_by_status("expired")
    }


# ─── operator-alert None-guard (n2) ─────────────────────────────────────────


def test_list_operator_alerts_tolerates_null_context_json(tmp_path):
    """A NULL operator_alerts.context_json must not crash list_operator_alerts.

    The column is nullable; _operator_alert_from_row must None-guard the JSON
    read (mirroring _queued_intent_from_row) and yield context_json == {}.
    """
    import sqlite3

    db = tmp_path / "milodex.db"
    store = EventStore(db)
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO operator_alerts "
            "(recorded_at, alert_type, severity, summary, context_json) "
            "VALUES (?, 'exit_intent_dropped', 'warning', 'null ctx', NULL)",
            (_NOW.isoformat(),),
        )
        con.commit()

    alerts = store.list_operator_alerts()

    assert len(alerts) == 1
    assert alerts[0].context_json == {}


# ─── atomic consume CAS + outbox attempt (Fix #2) ───────────────────────────
#
# consume_queued_intent_and_append_attempt folds the consume CAS and the
# 'pending' outbox insert into ONE transaction (one commit). It must:
#   * re-assert the SAME drain predicates as mark_queued_intent_consumed
#     (shared _consume_queued_intent_cas — they cannot drift);
#   * insert the attempt IFF rowcount == 1 (both committed together);
#   * on any CAS loss (rowcount 0) insert NOTHING and leave the row untouched.
# This closes the crash window where a 'consumed' row could exist with no order
# and no recoverable 'pending' attempt (a silent strand for an EXIT).


def _attempt(client_order_id: str = "cid-1", **overrides) -> ExecutionAttemptEvent:
    fields: dict[str, object] = {
        "client_order_id": client_order_id,
        "strategy_name": "rsi2.v1",
        "strategy_config_path": _DEFAULT_CFG_PATH,
        "session_id": "sess-A",
        "symbol": "SPY",
        "side": "buy",
        "quantity": 4.0,
        "order_type": "market",
        "created_at": _NOW,
        "status": "pending",
    }
    fields.update(overrides)
    return ExecutionAttemptEvent(**fields)  # type: ignore[arg-type]


def test_combined_happy_path_consumes_and_writes_attempt_atomically(tmp_path):
    """rowcount 1: the row is 'consumed' AND the 'pending' attempt is present."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())

    rowcount = store.consume_queued_intent_and_append_attempt(
        "rsi2.v1|2026-06-23|buy|SPY",
        _attempt(),
        now=_NOW,
        running_session_id="sess-A",
        consumed_by="sess-A",
        consumed_at=_NOW,
    )

    assert rowcount == 1
    assert _status_of(tmp_path / "milodex.db", "rsi2.v1|2026-06-23|buy|SPY") == "consumed"
    attempts = store.list_execution_attempts()
    assert len(attempts) == 1
    assert attempts[0].client_order_id == "cid-1"
    assert attempts[0].status == "pending"


def test_combined_second_call_loses_cas_and_writes_no_attempt(tmp_path):
    """A duplicate (already-consumed) call returns 0 and inserts NO attempt."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    key = "rsi2.v1|2026-06-23|buy|SPY"

    first = store.consume_queued_intent_and_append_attempt(
        key, _attempt("cid-1"), now=_NOW, running_session_id="sess-A",
        consumed_by="sess-A", consumed_at=_NOW,
    )
    second = store.consume_queued_intent_and_append_attempt(
        key, _attempt("cid-2"), now=_NOW, running_session_id="sess-A",
        consumed_by="sess-B", consumed_at=_NOW,
    )

    assert first == 1
    assert second == 0
    # Exactly one attempt: the race-loser inserted nothing (I-5).
    attempts = store.list_execution_attempts()
    assert [a.client_order_id for a in attempts] == ["cid-1"]


def test_combined_drops_expired_row_and_writes_no_attempt(tmp_path):
    """Expired-but-still-'queued' -> rowcount 0, no attempt, row untouched."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(expires_at=datetime(2026, 6, 23, 13, 0, tzinfo=UTC)))

    rowcount = store.consume_queued_intent_and_append_attempt(
        "rsi2.v1|2026-06-23|buy|SPY",
        _attempt(),
        now=_NOW,  # 14:00 > 13:00 expiry
        running_session_id="sess-A",
        consumed_by="sess-A",
        consumed_at=_NOW,
    )

    assert rowcount == 0
    assert _status_of(tmp_path / "milodex.db", "rsi2.v1|2026-06-23|buy|SPY") == "queued"
    assert store.list_execution_attempts() == []


@pytest.mark.parametrize(
    "exit_reason", ["interrupted", "crashed", "kill_switch", "orphan_recovered", None]
)
def test_combined_drops_unclean_handoff_and_writes_no_attempt(tmp_path, exit_reason):
    """Cross-session row whose run did NOT controlled_stop -> 0, no attempt."""
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    store.append_queued_intent(_intent(session_id="sess-OLD"))
    _seed_run(db, "sess-OLD", exit_reason)

    rowcount = store.consume_queued_intent_and_append_attempt(
        "rsi2.v1|2026-06-23|buy|SPY",
        _attempt(),
        now=_NOW,
        running_session_id="sess-NEW",
        consumed_by="sess-NEW",
        consumed_at=_NOW,
    )

    assert rowcount == 0
    assert store.list_execution_attempts() == []


def test_combined_drops_config_drifted_row_and_writes_no_attempt(tmp_path):
    """Stored config_hash != recomputed on-disk hash -> 0, no attempt."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(config_hash="d" * 64))

    rowcount = store.consume_queued_intent_and_append_attempt(
        "rsi2.v1|2026-06-23|buy|SPY",
        _attempt(),
        now=_NOW,
        running_session_id="sess-A",
        consumed_by="sess-A",
        consumed_at=_NOW,
    )

    assert rowcount == 0
    assert _status_of(tmp_path / "milodex.db", "rsi2.v1|2026-06-23|buy|SPY") == "queued"
    assert store.list_execution_attempts() == []


def test_combined_unknown_key_returns_zero_and_writes_no_attempt(tmp_path):
    """An unknown idempotency_key -> 0, no attempt inserted."""
    store = EventStore(tmp_path / "milodex.db")

    rowcount = store.consume_queued_intent_and_append_attempt(
        "nope",
        _attempt(),
        now=_NOW,
        running_session_id="sess-A",
        consumed_by="x",
        consumed_at=_NOW,
    )

    assert rowcount == 0
    assert store.list_execution_attempts() == []
