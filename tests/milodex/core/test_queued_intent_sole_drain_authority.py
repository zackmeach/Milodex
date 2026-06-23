"""Sole-drain-authority proof for queue-at-open (Plan Contracts #8b; D-6 matrix).

This file is referenced by the Phase-7 D-6 assurance matrix. It pins the load-
bearing property that survives every layer added to the drain path:
``EventStore.get_active_queued_intents`` is THE sole authority for "is this
queued intent drainable?" — base status/expiry, the I-4 clean-exit handoff
fence, AND the I-7 config_hash re-verification are ALL enforced inside that one
method. A row that passes the base filter and the clean-exit fence but carries a
mismatched/missing config_hash is NOT returned. There is no second code path
that can declare a row drainable.

If a future change moves any of these gates out of ``get_active_queued_intents``
(e.g. into the runner-drain loop), these tests fail — that is the intended
tripwire: the gates must stay co-located in the sole authority.
"""

from __future__ import annotations

from datetime import UTC, datetime

from milodex.core.event_store import EventStore
from milodex.strategies.loader import compute_config_hash
from tests.milodex.core.conftest import _append_queued_intent

_NOW = datetime(2026, 6, 23, 14, 0, tzinfo=UTC)


def _seed_run(db_path, session_id, exit_reason):
    import sqlite3

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO strategy_runs (session_id, strategy_id, started_at, ended_at, "
            "exit_reason, metadata_json) VALUES (?, 'rsi2.v1', ?, ?, ?, '{}')",
            (session_id, "2026-06-22T20:00:00+00:00", "2026-06-22T20:05:00+00:00", exit_reason),
        )
        con.commit()


def _drainable_keys(store, *, running_session_id):
    return [
        r.idempotency_key
        for r in store.get_active_queued_intents(
            "rsi2.v1", now=_NOW, running_session_id=running_session_id
        )
    ]


def test_base_filter_plus_fence_pass_but_config_hash_mismatch_not_returned(tmp_path):
    """The keystone D-6 assertion: a row that passes the base status/expiry filter
    AND the clean-exit fence (same running session) is STILL not returned when its
    config_hash no longer matches the on-disk config — proving the config_hash
    layer is enforced inside the sole drain authority, not bolted on elsewhere.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg = tmp_path / "rsi2.yaml"
    cfg.write_text("strategy:\n  id: rsi2.mr.swing.v1\n", encoding="utf-8")

    # Row passes base filter (queued + not expired) and the fence (same session),
    # but its stored hash is stale vs the on-disk config.
    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|SPY",
        strategy_config_path=str(cfg),
        config_hash="STALE_OVERNIGHT_EDIT",
        session_id="sess-A",
    )
    assert _drainable_keys(store, running_session_id="sess-A") == []


def test_sole_authority_returns_only_the_fully_clean_row(tmp_path):
    """Across a mixed set, get_active_queued_intents returns ONLY the row that
    clears every gate (status, expiry, fence, config_hash). It is the single
    arbiter — no row reaches a caller without clearing all four here."""
    db = tmp_path / "milodex.db"
    store = EventStore(db)

    good = tmp_path / "good.yaml"
    good.write_text("strategy:\n  id: rsi2.mr.swing.v1\n", encoding="utf-8")
    good_hash = compute_config_hash(good)

    # (1) fully clean, same session  -> RETURNED
    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|SPY",
        strategy_config_path=str(good),
        config_hash=good_hash,
        session_id="sess-A",
    )
    # (2) clean config but dirty (interrupted) cross-session -> fence drops
    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|QQQ",
        symbol="QQQ",
        strategy_config_path=str(good),
        config_hash=good_hash,
        session_id="sess-DIRTY",
    )
    _seed_run(db, "sess-DIRTY", "interrupted")
    # (3) clean fence (controlled_stop cross-session) but stale config_hash -> guard drops
    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|IWM",
        symbol="IWM",
        strategy_config_path=str(good),
        config_hash="STALE",
        session_id="sess-CS",
    )
    _seed_run(db, "sess-CS", "controlled_stop")
    # (4) expired -> base filter drops
    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|DIA",
        symbol="DIA",
        strategy_config_path=str(good),
        config_hash=good_hash,
        session_id="sess-A",
        expires_at=datetime(2026, 6, 23, 13, 0, tzinfo=UTC),
    )

    assert _drainable_keys(store, running_session_id="sess-A") == ["rsi2.v1|2026-06-23|buy|SPY"]
