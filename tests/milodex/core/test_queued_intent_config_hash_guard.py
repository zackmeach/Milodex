"""Drain-time config_hash guard for queue-at-open (I-7).

Two layers under test:

* :func:`milodex.strategies.loader.compute_config_hash_or_none` — the non-raising,
  CRLF-insensitive wrapper the drain authority calls. Must return ``None`` (not
  raise) on a missing/unreadable path so the guard can DROP rather than crash the
  drain loop.
* :meth:`EventStore.get_active_queued_intents` config_hash re-verification — a
  candidate row that already passed the base status/expiry filter AND the
  clean-exit fence is still DROPPED unless its stored ``config_hash`` equals the
  freshly recomputed hash of the on-disk config. Fails closed on every uncertain
  branch (stored None / recompute None / mismatch), on BOTH fence arms.
"""

from __future__ import annotations

from datetime import UTC, datetime

from milodex.core.event_store import EventStore
from milodex.strategies.loader import compute_config_hash, compute_config_hash_or_none

_NOW = datetime(2026, 6, 23, 14, 0, tzinfo=UTC)


# ─── compute_config_hash_or_none (non-raising, CRLF-insensitive) ─────────────


def test_missing_path_returns_none(tmp_path):
    assert compute_config_hash_or_none(tmp_path / "nope.yaml") is None


def test_matches_strict_hash_for_existing(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    assert compute_config_hash_or_none(p) == compute_config_hash(p)


def test_crlf_insensitive(tmp_path):
    """A checkout that flipped LF<->CRLF must not invalidate an otherwise-equal config.

    Pins the line-ending property at the helper boundary: if a future refactor
    moves hashing off the YAML-load path, this fails loudly rather than silently
    dropping valid intents at the open.
    """
    lf = tmp_path / "lf.yaml"
    crlf = tmp_path / "crlf.yaml"
    lf.write_bytes(b"strategy:\n  id: mom.atr.swing.v1\n")
    crlf.write_bytes(b"strategy:\r\n  id: mom.atr.swing.v1\r\n")
    assert compute_config_hash_or_none(lf) == compute_config_hash_or_none(crlf)


def test_heterogeneous_key_yaml_returns_none(tmp_path):
    """A mapping with mixed key types (numeric/bool key beside a string key) makes
    the canonicalizing sorted()/json.dumps raise TypeError in compute_config_hash;
    the wrapper MUST swallow it and return None (fail-closed), never propagate."""
    p = tmp_path / "mixed.yaml"
    p.write_bytes(b"1: foo\nbar: baz\n")
    assert compute_config_hash_or_none(p) is None


# ─── drain-time config_hash re-verification in get_active_queued_intents ─────
#
# These use the SAME-running-session row so the config_hash check is isolated
# from the clean-exit fence: a hash mismatch must drop even on the live-handoff
# path (running_session_id == session_id). A separate test exercises the
# controlled_stop arm to prove the guard runs on BOTH fence arms.


def _append(store, *, config_path, config_hash, **overrides):
    from tests.milodex.core.conftest import _append_queued_intent

    return _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|SPY",
        strategy_config_path=config_path,
        config_hash=config_hash,
        **overrides,
    )


def _drainable(store, *, running_session_id="sess-A"):
    rows = store.get_active_queued_intents(
        "rsi2.v1", now=_NOW, running_session_id=running_session_id
    )
    return [r.session_id for r in rows]


def _seed_run(db_path, session_id, exit_reason):
    import sqlite3

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO strategy_runs (session_id, strategy_id, started_at, ended_at, "
            "exit_reason, metadata_json) VALUES (?, 'rsi2.v1', ?, ?, ?, '{}')",
            (session_id, "2026-06-22T20:00:00+00:00", "2026-06-22T20:05:00+00:00", exit_reason),
        )
        con.commit()


def test_config_hash_match_drainable(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg = tmp_path / "mom.yaml"
    cfg.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    _append(store, config_path=str(cfg), config_hash=compute_config_hash(cfg))
    assert _drainable(store) == ["sess-A"]


def test_config_hash_mismatch_dropped(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg = tmp_path / "mom.yaml"
    cfg.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    # Stored hash != on-disk hash (config edited overnight) => DROP.
    _append(store, config_path=str(cfg), config_hash="STALE_HASH")
    assert _drainable(store) == []


def test_config_path_missing_dropped(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    # Path gone => recompute None => DROP (fail-closed, no raise out of the loop).
    _append(store, config_path=str(tmp_path / "gone.yaml"), config_hash="anything")
    assert _drainable(store) == []


def test_stored_config_hash_none_dropped(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    cfg = tmp_path / "mom.yaml"
    cfg.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    # Stored hash None (never frozen) => DROP even though the on-disk hash is real.
    _append(store, config_path=str(cfg), config_hash=None)
    assert _drainable(store) == []


def test_config_path_none_dropped(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    # No durable config path => cannot re-resolve => DROP.
    _append(store, config_path=None, config_hash="anything")
    assert _drainable(store) == []


def test_crlf_config_edit_does_not_false_drop(tmp_path):
    """A pure line-ending flip on disk must NOT drop an otherwise-equal intent.

    The stored hash was frozen against an LF config; the on-disk file is now
    CRLF. Because compute_config_hash hashes the YAML-parsed structure (not raw
    bytes), the recomputed hash equals the stored one and the row drains.
    """
    store = EventStore(tmp_path / "milodex.db")
    cfg = tmp_path / "mom.yaml"
    cfg.write_bytes(b"strategy:\n  id: mom.atr.swing.v1\n")
    frozen = compute_config_hash(cfg)  # frozen at lock-in (LF)
    cfg.write_bytes(b"strategy:\r\n  id: mom.atr.swing.v1\r\n")  # overnight CRLF churn
    _append(store, config_path=str(cfg), config_hash=frozen)
    assert _drainable(store) == ["sess-A"]


def test_poison_config_drops_only_itself_not_the_whole_drain(tmp_path):
    """A single un-hashable (mixed-key) config must DROP only its own row — the
    sibling good row in the same result set still drains. Regression guard: the
    recompute must never raise out of get_active_queued_intents and suppress every
    queued intent (a false DROP of legitimate trades on the sacred drain path)."""
    from tests.milodex.core.conftest import _append_queued_intent

    store = EventStore(tmp_path / "milodex.db")
    good = tmp_path / "good.yaml"
    good.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    poison = tmp_path / "poison.yaml"
    poison.write_bytes(b"1: foo\nbar: baz\n")  # heterogeneous keys -> TypeError on hash

    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|SPY",
        symbol="SPY",
        strategy_config_path=str(good),
        config_hash=compute_config_hash(good),
    )
    # second row: poison config, different symbol/key so the UNIQUE constraint holds
    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|QQQ",
        symbol="QQQ",
        strategy_config_path=str(poison),
        config_hash="anything",
    )
    # The good row drains; the poison row is dropped; the call does NOT raise.
    rows = store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A")
    assert [r.symbol for r in rows] == ["SPY"]


def test_loop_backstop_drops_row_when_recompute_raises_unanticipated(tmp_path, monkeypatch):
    """Pin the in-loop ``except Exception`` backstop INDEPENDENTLY of the helper's
    except tuple. If a future change narrows ``compute_config_hash_or_none`` so it
    raises an unanticipated type, the drain loop in ``get_active_queued_intents``
    must STILL drop only that row and drain the sibling — never crash the whole
    drain. Without this, deleting either defense layer alone leaves the suite
    green; this test fails the moment the loop backstop is the one removed."""
    import milodex.strategies.loader as loader_mod
    from tests.milodex.core.conftest import _append_queued_intent

    store = EventStore(tmp_path / "milodex.db")
    good = tmp_path / "good.yaml"
    good.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    good_hash = compute_config_hash(good)
    poison = tmp_path / "poison.yaml"
    poison.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")

    def _raising(path):
        # Simulate a helper that lost its broad except and raises a type the loop
        # backstop is the only thing standing between and a crashed drain.
        if str(path) == str(poison):
            raise RuntimeError("unanticipated hashing failure")
        return good_hash

    monkeypatch.setattr(loader_mod, "compute_config_hash_or_none", _raising)

    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|SPY",
        symbol="SPY",
        strategy_config_path=str(good),
        config_hash=good_hash,
    )
    _append_queued_intent(
        store,
        idempotency_key="rsi2.v1|2026-06-23|buy|QQQ",
        symbol="QQQ",
        strategy_config_path=str(poison),
        config_hash=good_hash,
    )
    rows = store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A")
    assert [r.symbol for r in rows] == ["SPY"]


def test_config_hash_guard_runs_on_controlled_stop_arm(tmp_path):
    """The guard gates BOTH fence arms: a controlled_stop cross-session row with a
    stale config_hash is dropped, identically to the running-session arm."""
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    cfg = tmp_path / "mom.yaml"
    cfg.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    _append(store, config_path=str(cfg), config_hash="STALE_HASH", session_id="sess-OLD")
    _seed_run(db, "sess-OLD", "controlled_stop")
    # Passes the clean-exit fence (controlled_stop) but fails the config_hash guard.
    assert _drainable(store, running_session_id="sess-NEW") == []


def test_controlled_stop_arm_with_matching_hash_drains(tmp_path):
    """Counterpart: a controlled_stop cross-session row WITH a matching hash drains
    (proves the guard is not over-dropping the controlled_stop arm)."""
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    cfg = tmp_path / "mom.yaml"
    cfg.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    _append(
        store,
        config_path=str(cfg),
        config_hash=compute_config_hash(cfg),
        session_id="sess-OLD",
    )
    _seed_run(db, "sess-OLD", "controlled_stop")
    assert _drainable(store, running_session_id="sess-NEW") == ["sess-OLD"]
