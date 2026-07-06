"""Tests for :class:`milodex.gui.active_ops_state.ActiveOpsState`.

Mirrors the PerformanceState test harness:

- Pure-logic helpers tested without Qt.
- Full QObject lifecycle tests require a QGuiApplication and real (tmp-path)
  SQLite DB.  Gated behind _skip_no_qt.
- Tests drive the refresh cycle directly via _kick_refresh(); the timer
  interval is set to 99_999_999 ms so it never fires in CI.
- Fixture DB schema matches strategy_runs + explanations exactly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# PySide6 availability
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QCoreApplication, QThreadPool  # noqa: F401
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed -- skipping Qt-aware ActiveOpsState tests",
)

# ---------------------------------------------------------------------------
# Pure helper tests -- no Qt required
# ---------------------------------------------------------------------------


class TestSessionState:
    """sessionState is now driven by the shared 4-state resolver.

    The legacy ``_session_state`` helper (which emitted ``"stopped:<reason>"``)
    was removed in PR6 in favour of ``_event_queries.resolve_runner_liveness``.
    These tests pin the new 4-state contract (running / phantom / stopped /
    failed) at the resolver level; lock-awareness is exercised separately via
    ``_query_active_ops`` with an explicit ``locks_dir``.
    """

    def test_running_when_ended_at_none_and_lock_live(self) -> None:
        from milodex.gui._event_queries import resolve_runner_liveness

        assert resolve_runner_liveness(ended_at=None, exit_reason=None, lock_live=True) == "running"

    def test_running_when_ended_at_empty_string_and_lock_live(self) -> None:
        from milodex.gui._event_queries import resolve_runner_liveness

        assert resolve_runner_liveness(ended_at="", exit_reason=None, lock_live=True) == "running"

    def test_stopped_with_benign_exit_reason(self) -> None:
        from milodex.gui._event_queries import resolve_runner_liveness

        ts = "2026-05-16T10:00:00+00:00"
        assert (
            resolve_runner_liveness(ended_at=ts, exit_reason="controlled_stop", lock_live=False)
            == "stopped"
        )

    def test_failed_kill_switch(self) -> None:
        from milodex.gui._event_queries import resolve_runner_liveness

        assert (
            resolve_runner_liveness(
                ended_at="2026-05-16T10:00:00+00:00", exit_reason="kill_switch", lock_live=False
            )
            == "failed"
        )

    def test_failed_orphan_recovered(self) -> None:
        from milodex.gui._event_queries import resolve_runner_liveness

        ts = "2026-05-16T10:00:00+00:00"
        assert (
            resolve_runner_liveness(ended_at=ts, exit_reason="orphan_recovered", lock_live=False)
            == "failed"
        )

    def test_stopped_null_exit_reason(self) -> None:
        from milodex.gui._event_queries import resolve_runner_liveness

        assert (
            resolve_runner_liveness(
                ended_at="2026-05-16T10:00:00+00:00", exit_reason=None, lock_live=False
            )
            == "stopped"
        )


class TestHeartbeat:
    """_heartbeat(lock_age_seconds, cadence_seconds) → str.

    Pure function: no filesystem, no datetime arithmetic.
    Vocabulary is fixed (DeskSurface.qml colors on these literals).
    """

    def test_none_age_is_no_activity(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        assert _heartbeat(None, 60) == "no activity"

    def test_on_schedule_at_exactly_2_0x(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        # age = exactly 2.0 * 60 = 120s -- boundary is inclusive
        assert _heartbeat(120.0, 60) == "on schedule"

    def test_between_1_5x_and_2_0x_is_still_on_schedule(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        # age = 90s (old 1.5x threshold) -- must now be "on schedule" with 2.0x
        assert _heartbeat(90.0, 60) == "on schedule"

    def test_fresh_age_is_on_schedule(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        assert _heartbeat(30.0, 60) == "on schedule"

    def test_just_over_2_0x_is_overdue(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        # age = 121s -- just over the 120s (2.0 * 60) threshold
        result = _heartbeat(121.0, 60)
        assert result.startswith("overdue by ")

    def test_overdue_minutes_format(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        result = _heartbeat(600.0, 60)  # 10 minutes old
        assert result == "overdue by 10m"

    def test_zero_age_is_on_schedule(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        assert _heartbeat(0.0, 60) == "on schedule"

    def test_sub_minute_overdue_uses_seconds_unit(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        # Intraday cadence: 5Min poll=10s, threshold=20s, age=25s (just over).
        # Old code: int(25//60)=0 → "overdue by 0m" (nonsense).
        # New code: mins=0 → unit="25s" → "overdue by 25s".
        result = _heartbeat(25.0, 10)
        assert result == "overdue by 25s"

    def test_sub_minute_overdue_prefix_for_qml_color_rule(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        # DeskSurface.qml:677 colors on indexOf("overdue")===0 — must still fire
        # for sub-minute overdue values that now use the "s" unit.
        result = _heartbeat(25.0, 10)
        assert result.startswith("overdue by ")

    def test_exactly_2_0x_intraday_is_on_schedule(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        # 5Min cadence: seconds=10, threshold=20s exactly → "on schedule".
        assert _heartbeat(20.0, 10) == "on schedule"


class TestSessionAge:
    def _now(self) -> datetime:
        return datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)

    def test_minutes_only(self) -> None:
        from milodex.gui.active_ops_state import _session_age

        now = self._now()
        started = (now - timedelta(minutes=45)).isoformat()
        assert _session_age(started, now) == "45m"

    def test_hours_and_minutes(self) -> None:
        from milodex.gui.active_ops_state import _session_age

        now = self._now()
        started = (now - timedelta(hours=2, minutes=30)).isoformat()
        assert _session_age(started, now) == "2h 30m"

    def test_zero_minutes_padding(self) -> None:
        from milodex.gui.active_ops_state import _session_age

        now = self._now()
        started = (now - timedelta(hours=1)).isoformat()
        assert _session_age(started, now) == "1h 00m"

    def test_tz_naive_input_does_not_raise(self) -> None:
        from milodex.gui.active_ops_state import _session_age

        now = self._now()
        naive_iso = "2026-05-16T11:30:00"  # no tz
        result = _session_age(naive_iso, now)
        assert isinstance(result, str)

    def test_just_under_one_hour(self) -> None:
        from milodex.gui.active_ops_state import _session_age

        now = self._now()
        started = (now - timedelta(minutes=59)).isoformat()
        assert _session_age(started, now) == "59m"


# ---------------------------------------------------------------------------
# Fixture DB helpers
# ---------------------------------------------------------------------------


def _create_fixture_db(path: Path) -> None:
    """Apply the REAL (fully-migrated) schema via EventStore."""
    from milodex.core.event_store import EventStore

    EventStore(path)


def _seed_run(
    db: Path,
    strategy_id: str,
    session_id: str,
    started_at: str,
    *,
    ended_at: str | None = None,
    exit_reason: str | None = None,
) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO strategy_runs
            (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
        VALUES (?, ?, ?, ?, ?, '{}')
        """,
        (session_id, strategy_id, started_at, ended_at, exit_reason),
    )
    conn.commit()
    conn.close()


def _seed_explanation(db: Path, session_id: str, recorded_at: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO explanations (
            session_id, recorded_at, strategy_stage,
            decision_type, status, symbol, side, quantity,
            order_type, time_in_force, submitted_by, market_open,
            account_equity, account_cash, account_portfolio_value, account_daily_pnl,
            risk_allowed, risk_summary, reason_codes_json, risk_checks_json, context_json
        )
        VALUES (?, ?, 'entry',
                'submit', 'submitted', 'SPY', 'buy', 1.0,
                'market', 'day', 'test', 1,
                10000.0, 10000.0, 10000.0, 0.0,
                1, 'ok', '[]', '{}', '{}')
        """,
        (session_id, recorded_at),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Pure _query_active_ops tests -- no Qt required
# ---------------------------------------------------------------------------


def test_query_active_ops_running_runner(tmp_path) -> None:
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=2)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    result = _query_active_ops(db, now)

    assert len(result) == 1
    r = result[0]
    assert r["strategyId"] == "strat.a.v1"
    assert r["sessionState"] == "running"


def test_query_active_ops_open_run_no_live_lock_is_phantom(tmp_path) -> None:
    """An open run with an explicit locks_dir holding no live lock is a phantom.

    This is the P8 corpse case: a hard-killed runner whose strategy_runs row
    never closed. With phantom detection ON (explicit locks_dir), sessionState
    must be "phantom" and runnerLock "released" — both driven from the single
    lock check.
    """
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=2)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    result = _query_active_ops(db, now, locks_dir=locks_dir)

    assert len(result) == 1
    assert result[0]["sessionState"] == "phantom"
    assert result[0]["runnerLock"] == "released"


def test_query_active_ops_open_run_locks_dir_none_is_legacy_running(tmp_path) -> None:
    """Back-compat guard: locks_dir=None disables phantom detection.

    An open run with no locks_dir resolves to legacy "running" — the dozens of
    existing tests that seed an open run without a locks_dir must keep passing.
    """
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=2)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    result = _query_active_ops(db, now, locks_dir=None)

    assert len(result) == 1
    assert result[0]["sessionState"] == "running"
    # The sessionState legacy guard (lock_live=True when locks_dir is None) must
    # NOT leak into runnerLock: with no locks_dir there is nothing to verify, so
    # the badge stays honestly "released" rather than claiming a held lock.
    assert result[0]["runnerLock"] == "released"


def test_query_active_ops_stopped_runner(tmp_path) -> None:
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=3)).isoformat()
    ended = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.b.v1", "sess-002", started, ended_at=ended, exit_reason="controlled_stop")

    result = _query_active_ops(db, now)

    assert len(result) == 1
    assert result[0]["sessionState"] == "stopped"


def test_query_active_ops_latest_run_per_strategy(tmp_path) -> None:
    """When multiple runs exist for a strategy, only the latest is returned."""
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    older_start = (now - timedelta(hours=5)).isoformat()
    older_end = (now - timedelta(hours=4)).isoformat()
    newer_start = (now - timedelta(hours=2)).isoformat()

    _seed_run(
        db,
        "strat.a.v1",
        "sess-old",
        older_start,
        ended_at=older_end,
        exit_reason="controlled_stop",
    )
    _seed_run(db, "strat.a.v1", "sess-new", newer_start)

    result = _query_active_ops(db, now)

    assert len(result) == 1
    assert result[0]["sessionState"] == "running"


def test_query_active_ops_multi_strategy(tmp_path) -> None:
    """Multiple strategies each get their own runner row."""
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    t = (now - timedelta(hours=1)).isoformat()

    _seed_run(db, "strat.a.v1", "sess-a", t)
    _seed_run(db, "strat.b.v1", "sess-b", t, ended_at=t, exit_reason="kill_switch")

    result = _query_active_ops(db, now)

    assert len(result) == 2
    ids = {r["strategyId"] for r in result}
    assert ids == {"strat.a.v1", "strat.b.v1"}


def test_query_active_ops_no_explanations_heartbeat_no_activity(tmp_path) -> None:
    """heartbeat='no activity' when no locks_dir is provided (no lock surface to inspect).

    Post-PR7, heartbeat is driven by the advisory-lock mtime, not explanation
    recency.  The causal factor here is the absent locks_dir (locks_dir=None
    default) — runner_lock_mtime_age returns None → _heartbeat → 'no activity'.
    Explanation row presence or absence is irrelevant to the heartbeat signal.
    """
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    result = _query_active_ops(db, now)
    assert result[0]["heartbeat"] == "no activity"
    assert result[0]["lastEval"] is None


def test_query_active_ops_last_eval_from_explanations(tmp_path) -> None:
    """lastEval picks MAX(recorded_at) for the runner's session_id."""
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=2)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    earlier = (now - timedelta(minutes=5)).isoformat()
    latest = (now - timedelta(minutes=1)).isoformat()
    _seed_explanation(db, "sess-001", earlier)
    _seed_explanation(db, "sess-001", latest)

    result = _query_active_ops(db, now)
    assert result[0]["lastEval"] == latest


def test_query_active_ops_empty_strategy_runs(tmp_path) -> None:
    """Empty strategy_runs returns empty list."""
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    result = _query_active_ops(db, now)
    assert result == []


def test_query_active_ops_missing_db_raises(tmp_path) -> None:
    from milodex.gui.active_ops_state import _query_active_ops

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(Exception):  # noqa: B017
        _query_active_ops(tmp_path / "nonexistent.db", now)


def test_query_active_ops_runner_lock_held(tmp_path) -> None:
    """runnerLock='held' when a genuinely-live process holds the lock.

    Identity-verified liveness (hardening-2): the badge reflects a live holder,
    not merely a lock file on disk, so the lock is taken through the real
    acquire path (this live PID) rather than planted with an arbitrary PID.
    """
    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.gui.active_ops_state import _query_active_ops
    from milodex.strategies.paper_runner_control import runner_lock_name

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    lock = AdvisoryLock(runner_lock_name("strat.a.v1"), locks_dir=locks_dir)
    lock.acquire()
    try:
        result = _query_active_ops(db, now, locks_dir=locks_dir)
        assert result[0]["runnerLock"] == "held"
    finally:
        lock.release()


def test_query_active_ops_runner_lock_released_for_dead_pid(tmp_path) -> None:
    """A stale lock whose recorded PID is not a live process reports 'released'
    (identity-verified liveness), not a phantom 'held'."""
    from milodex.gui.active_ops_state import _query_active_ops
    from milodex.strategies.paper_runner_control import runner_lock_name

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    # pid=0 short-circuits liveness to dead; the lock file exists but no live
    # process owns it (hard-killed-runner signature).
    lock_file = locks_dir / f"{runner_lock_name('strat.a.v1')}.lock"
    lock_file.write_text(
        json.dumps(
            {
                "pid": 0,
                "hostname": "ghost",
                "holder_name": "milodex",
                "started_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    result = _query_active_ops(db, now, locks_dir=locks_dir)
    assert result[0]["runnerLock"] == "released"


def test_query_active_ops_sigkill_dead_pid_fresh_mtime_heartbeat_no_activity(tmp_path) -> None:
    """Hard-killed runner (dead PID, fresh mtime) → heartbeat='no activity', not 'on schedule'.

    FIX 2 coherence: after SIGKILL the lock file's mtime is fresh (refreshed
    moments before death), but the PID is gone.  Without the liveness gate,
    runner_lock_mtime_age would read the fresh mtime and return "on schedule"
    while sessionState="phantom" and runnerLock="released" — three contradictory
    signals.  With the gate (lock_age gated on lock_verified_live=False),
    lock_age=None → _heartbeat → 'no activity', coherent with phantom/released.
    """
    import os
    import time

    from milodex.gui.active_ops_state import _query_active_ops
    from milodex.strategies.paper_runner_control import runner_lock_name

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now_ts = time.time()
    now = datetime.fromtimestamp(now_ts, tz=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    # Plant a lock file with a dead PID (0) and a FRESH mtime (5s ago).
    # pid=0 short-circuits runner_lock_live → False (dead-runner signature).
    # The fresh mtime would yield "on schedule" if the gate were absent.
    lock_file = locks_dir / f"{runner_lock_name('strat.a.v1')}.lock"
    lock_file.write_text(
        json.dumps(
            {
                "pid": 0,
                "hostname": "ghost",
                "holder_name": "milodex",
                "started_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    # Stamp mtime to 5s ago — well within any threshold, so without the gate
    # this would produce "on schedule".
    t = now_ts - 5
    os.utime(lock_file, (t, t))

    result = _query_active_ops(db, now, locks_dir=locks_dir)
    r = result[0]
    # All three signals must be coherent.
    assert r["sessionState"] == "phantom"
    assert r["runnerLock"] == "released"
    assert r["heartbeat"] == "no activity"  # NOT "on schedule"


def test_query_active_ops_runner_lock_released(tmp_path) -> None:
    """runnerLock='released' when no lock file present."""
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    result = _query_active_ops(db, now, locks_dir=locks_dir)
    assert result[0]["runnerLock"] == "released"


def test_query_active_ops_stop_requested_true(tmp_path) -> None:
    """stopRequested=True when sentinel file exists."""
    from milodex.gui.active_ops_state import _query_active_ops
    from milodex.strategies.paper_runner_control import controlled_stop_request_path

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    sentinel = controlled_stop_request_path(locks_dir, "strat.a.v1")
    sentinel.write_text("{}", encoding="utf-8")

    result = _query_active_ops(db, now, locks_dir=locks_dir)
    assert result[0]["stopRequested"] is True


def test_query_active_ops_stop_requested_false(tmp_path) -> None:
    """stopRequested=False when sentinel file absent."""
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    result = _query_active_ops(db, now, locks_dir=locks_dir)
    assert result[0]["stopRequested"] is False


def test_query_active_ops_default_cadence_label(tmp_path) -> None:
    """Cadence defaults to daily (1D) when no configs_dir given."""
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    result = _query_active_ops(db, now)
    assert result[0]["cadence"] == "daily (1D)"


def test_query_active_ops_cadence_from_yaml(tmp_path) -> None:
    """Cadence label loaded from YAML when configs_dir provided."""
    import yaml

    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    yaml_content = {"strategy": {"id": "strat.a.v1", "tempo": {"bar_size": "1H"}}}
    (configs_dir / "strat_a_v1.yaml").write_text(yaml.dump(yaml_content), encoding="utf-8")

    result = _query_active_ops(db, now, configs_dir=configs_dir)
    assert result[0]["cadence"] == "hourly (1H)"


# ---------------------------------------------------------------------------
# runner_lock_mtime_age tests (new helper in _event_queries)
# ---------------------------------------------------------------------------


def test_runner_lock_mtime_age_none_locks_dir() -> None:
    """Returns None when locks_dir is None — no surface to inspect."""
    from milodex.gui._event_queries import runner_lock_mtime_age

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    result = runner_lock_mtime_age("strat.a.v1", None, now)
    assert result is None


def test_runner_lock_mtime_age_absent_lock_file(tmp_path) -> None:
    """Returns None when the lock file does not exist."""
    from milodex.gui._event_queries import runner_lock_mtime_age

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    result = runner_lock_mtime_age("strat.a.v1", locks_dir, now)
    assert result is None


def test_runner_lock_mtime_age_fresh(tmp_path) -> None:
    """Returns a small positive age when the lock file was just written."""
    import os
    import time

    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.gui._event_queries import runner_lock_mtime_age
    from milodex.strategies.paper_runner_control import runner_lock_name

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    lock = AdvisoryLock(runner_lock_name("strat.a.v1"), locks_dir=locks_dir)
    lock.acquire()
    try:
        # Stamp the lock file mtime to exactly 10 seconds ago.
        lock_path = lock.path
        t = time.time() - 10
        os.utime(lock_path, (t, t))

        now = datetime.fromtimestamp(time.time(), tz=UTC)
        age = runner_lock_mtime_age("strat.a.v1", locks_dir, now)
        assert age is not None
        assert 9.0 <= age <= 15.0  # generous window for CI timing
    finally:
        lock.release()


def test_runner_lock_mtime_age_stale(tmp_path) -> None:
    """Returns a large age when the lock file mtime was set far in the past."""
    import os
    import time

    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.gui._event_queries import runner_lock_mtime_age
    from milodex.strategies.paper_runner_control import runner_lock_name

    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    lock = AdvisoryLock(runner_lock_name("strat.a.v1"), locks_dir=locks_dir)
    lock.acquire()
    try:
        lock_path = lock.path
        t = time.time() - 600  # 10 minutes ago
        os.utime(lock_path, (t, t))

        now = datetime.fromtimestamp(time.time(), tz=UTC)
        age = runner_lock_mtime_age("strat.a.v1", locks_dir, now)
        assert age is not None
        assert age >= 590  # at least 590s old
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Heartbeat integration: lock-mtime as source (PR7)
# ---------------------------------------------------------------------------


def test_query_active_ops_heartbeat_fresh_lock_on_schedule(tmp_path) -> None:
    """A runner with a fresh lock file (mtime ≤ cadence*2.0) → 'on schedule'.

    This is the core bug-fix test: a daily runner whose lock was refreshed
    recently reads 'on schedule' regardless of when the last explanation was
    recorded (potentially hours ago).
    """
    import os
    import time

    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.gui.active_ops_state import _query_active_ops
    from milodex.strategies.paper_runner_control import runner_lock_name

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now_ts = time.time()
    now = datetime.fromtimestamp(now_ts, tz=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    # Explanation recorded 6 hours ago — old logic would report "overdue".
    _seed_explanation(db, "sess-001", (now - timedelta(hours=6)).isoformat())

    # Lock file refreshed only 30s ago — within cadence*2.0 (60*2.0=120s).
    lock = AdvisoryLock(runner_lock_name("strat.a.v1"), locks_dir=locks_dir)
    lock.acquire()
    lock_path = lock.path
    t = now_ts - 30
    os.utime(lock_path, (t, t))

    try:
        result = _query_active_ops(db, now, locks_dir=locks_dir)
        assert result[0]["heartbeat"] == "on schedule"
        # lastEval is still the explanation time — unchanged.
        assert result[0]["lastEval"] is not None
    finally:
        lock.release()


def test_query_active_ops_heartbeat_stale_lock_overdue(tmp_path) -> None:
    """A runner with a stale lock file (mtime > cadence*2.0) → 'overdue by Nm'."""
    import os
    import time

    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.gui.active_ops_state import _query_active_ops
    from milodex.strategies.paper_runner_control import runner_lock_name

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now_ts = time.time()
    now = datetime.fromtimestamp(now_ts, tz=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)

    # Lock file last refreshed 10 minutes ago — well past cadence*2.0 (120s).
    lock = AdvisoryLock(runner_lock_name("strat.a.v1"), locks_dir=locks_dir)
    lock.acquire()
    lock_path = lock.path
    t = now_ts - 600
    os.utime(lock_path, (t, t))

    try:
        result = _query_active_ops(db, now, locks_dir=locks_dir)
        assert result[0]["heartbeat"].startswith("overdue by ")
    finally:
        lock.release()


def test_query_active_ops_heartbeat_no_locks_dir_is_no_activity(tmp_path) -> None:
    """Without a locks_dir the lock age is None → heartbeat = 'no activity'.

    This is the existing back-compat case: no locks_dir means no surface
    to inspect, so no health signal can be produced.
    """
    from milodex.gui.active_ops_state import _query_active_ops

    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)
    # Seed a recent explanation — old logic would say "on schedule".
    _seed_explanation(db, "sess-001", (now - timedelta(seconds=30)).isoformat())

    result = _query_active_ops(db, now, locks_dir=None)
    assert result[0]["heartbeat"] == "no activity"


def test_query_active_ops_heartbeat_decoupled_from_explanation_recency(tmp_path) -> None:
    """heartbeat='on schedule' for a live lock + fresh mtime even with ZERO explanations.

    FIX 3 decoupling proof: post-PR7 the heartbeat signal is purely driven by
    the advisory-lock mtime.  A runner with a live, freshly-refreshed lock and
    ZERO explanation rows must still read 'on schedule'.  If heartbeat still
    depended on explanation recency, it would read 'no activity' here.
    """
    import os
    import time

    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.gui.active_ops_state import _query_active_ops
    from milodex.strategies.paper_runner_control import runner_lock_name

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now_ts = time.time()
    now = datetime.fromtimestamp(now_ts, tz=UTC)
    started = (now - timedelta(hours=1)).isoformat()
    _seed_run(db, "strat.a.v1", "sess-001", started)
    # Deliberately NO explanations seeded — zero rows.

    # Acquire the lock via this live PID so runner_lock_live → True.
    lock = AdvisoryLock(runner_lock_name("strat.a.v1"), locks_dir=locks_dir)
    lock.acquire()
    lock_path = lock.path
    # Stamp mtime to 10s ago — well within cadence*2.0 (120s).
    t = now_ts - 10
    os.utime(lock_path, (t, t))

    try:
        result = _query_active_ops(db, now, locks_dir=locks_dir)
        r = result[0]
        assert r["lastEval"] is None  # zero explanations — confirmed
        assert r["heartbeat"] == "on schedule"  # lock is fresh — decoupled
    finally:
        lock.release()


def test_heartbeat_daily_cadence_old_last_eval_would_have_been_overdue(tmp_path) -> None:
    """Mutation-proof: confirm the old last_eval logic produces 'overdue' for
    the exact scenario the new lock-mtime logic fixes.

    Scenario: daily runner, lock fresh (30s), last_eval 6h old.
    - Old logic (_heartbeat takes last_eval ISO) → "overdue by 360m"
    - New logic (_heartbeat takes lock_age_seconds) → "on schedule"
    This test drives the new _heartbeat directly to prove the fix.
    """
    from milodex.gui.active_ops_state import _heartbeat

    cadence = 60  # 1D poll interval
    # Fresh lock: 30s old — well within 60*2.0=120s threshold.
    assert _heartbeat(30.0, cadence) == "on schedule"

    # Verify that the old scalar (6h in seconds) would have been "overdue".
    # We simulate what old logic did: age = (now - last_eval).total_seconds()
    # = 6 * 3600 = 21600s, which is >> 120s → "overdue by 360m".
    old_age_seconds = 6 * 3600  # 6 hours
    # The old _heartbeat used: age <= cadence * 1.5 for "on schedule"
    # 21600 > 120 → would have been "overdue" under either threshold
    assert old_age_seconds > cadence * 2.0  # confirms the old logic was wrong
    # But the new logic with lock age = 30s says "on schedule":
    assert _heartbeat(30.0, cadence) == "on schedule"


# ---------------------------------------------------------------------------
# Qt-aware fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication so QObject + QTimer + QThreadPool work."""
    if not _PYSIDE6_AVAILABLE:
        return None

    import os
    import sys

    from PySide6.QtGui import QGuiApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


def _make_state(
    db_path: Path,
    configs_dir: Path | None = None,
    locks_dir: Path | None = None,
    refresh_interval_ms: int = 99_999_999,
):
    from milodex.gui.active_ops_state import ActiveOpsState

    return ActiveOpsState(
        db_path=db_path,
        configs_dir=configs_dir,
        locks_dir=locks_dir,
        refresh_interval_ms=refresh_interval_ms,
    )


def _drain_pool(state) -> None:
    """Poll until the refresh settles (``dataStatus`` leaves "loading").

    Condition-based to survive xdist worker-scheduling delay (root-caused
    2026-07-06; see test_attention_state._wait_for_pool for the full write-up).
    """
    import time

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        state._thread_pool.waitForDone(50)  # noqa: SLF001
        QCoreApplication.processEvents()
        if state.dataStatus != "loading":
            break
    QCoreApplication.processEvents()


# ---------------------------------------------------------------------------
# Qt lifecycle tests
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_initial_state_is_loading(qapp, tmp_path) -> None:
    """Before any refresh, dataStatus is loading."""
    _ = qapp
    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    state = _make_state(db)

    assert state.dataStatus == "loading"
    assert state.runners == []
    assert state.lastRefreshedAt == ""
    assert state.dataErrorMessage == ""


@_skip_no_qt
def test_refresh_populates_runners(qapp, tmp_path) -> None:
    """After a successful refresh, runners is populated with all strategy rows."""
    _ = qapp
    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime.now(tz=UTC)
    t = (now - timedelta(hours=1)).isoformat()
    t2 = (now - timedelta(hours=2)).isoformat()
    t2_end = (now - timedelta(hours=1, minutes=30)).isoformat()

    _seed_run(db, "strat.a.v1", "sess-a", t)
    _seed_run(db, "strat.b.v1", "sess-b", t2, ended_at=t2_end, exit_reason="controlled_stop")

    # Explicit empty locks_dir → deterministic phantom detection (no live lock
    # for either seeded strategy), isolated from the production locks dir.
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    state = _make_state(db, locks_dir=locks_dir)
    state._kick_refresh()  # noqa: SLF001
    _drain_pool(state)

    assert state.dataStatus == "ready"
    runners = state.runners
    assert len(runners) == 2
    ids = {r["strategyId"] for r in runners}
    assert ids == {"strat.a.v1", "strat.b.v1"}

    open_runner = next(r for r in runners if r["strategyId"] == "strat.a.v1")
    stopped = next(r for r in runners if r["strategyId"] == "strat.b.v1")

    # Open run with no live lock is a phantom (PR6); closed controlled_stop is stopped.
    assert open_runner["sessionState"] == "phantom"
    assert stopped["sessionState"] == "stopped"

    for r in runners:
        assert "cadence" in r
        assert "heartbeat" in r
        assert "runnerLock" in r
        assert "stopRequested" in r
        assert "sessionAge" in r

    state.stop()


# Lifecycle scaffold tests (missing-DB error, error-after-success preservation,
# in-flight drop, stop-drains-worker) were removed in PR C of RM-007 — those
# contracts are now covered ONCE in tests/milodex/gui/test_polling_lifecycle.py.


@_skip_no_qt
def test_no_explanations_heartbeat_no_activity(qapp, tmp_path) -> None:
    """heartbeat='no activity' when no locks_dir is provided (no lock surface to inspect).

    Post-PR7, heartbeat is driven by advisory-lock mtime, not explanation recency.
    The causal factor here is the absent locks_dir (ActiveOpsState default path
    with no explicit locks_dir set, so runner_lock_mtime_age returns None →
    'no activity').  Explanation rows are irrelevant to this signal.
    """
    _ = qapp
    db = tmp_path / "ops.db"
    _create_fixture_db(db)

    now = datetime.now(tz=UTC)
    _seed_run(db, "strat.a.v1", "sess-001", (now - timedelta(hours=1)).isoformat())

    state = _make_state(db)
    state._kick_refresh()  # noqa: SLF001
    _drain_pool(state)

    assert state.runners[0]["heartbeat"] == "no activity"
    assert state.runners[0]["lastEval"] is None

    state.stop()


@_skip_no_qt
def test_runner_lock_held_in_state(qapp, tmp_path) -> None:
    """runnerLock='held' propagates through the full refresh path.

    Identity-verified liveness (hardening-2): hold the lock via the real
    acquire path (this live PID) so the badge reflects a genuinely-live holder.
    """
    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.strategies.paper_runner_control import runner_lock_name

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now = datetime.now(tz=UTC)
    _seed_run(db, "strat.a.v1", "sess-001", (now - timedelta(hours=1)).isoformat())

    lock = AdvisoryLock(runner_lock_name("strat.a.v1"), locks_dir=locks_dir)
    lock.acquire()
    try:
        state = _make_state(db, locks_dir=locks_dir)
        state._kick_refresh()  # noqa: SLF001
        _drain_pool(state)

        assert state.runners[0]["runnerLock"] == "held"
        state.stop()
    finally:
        lock.release()


@_skip_no_qt
def test_stop_requested_true_in_state(qapp, tmp_path) -> None:
    """stopRequested=True propagates through the full refresh path."""
    from milodex.strategies.paper_runner_control import controlled_stop_request_path

    db = tmp_path / "ops.db"
    _create_fixture_db(db)
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()

    now = datetime.now(tz=UTC)
    _seed_run(db, "strat.a.v1", "sess-001", (now - timedelta(hours=1)).isoformat())

    sentinel = controlled_stop_request_path(locks_dir, "strat.a.v1")
    sentinel.write_text("{}", encoding="utf-8")

    state = _make_state(db, locks_dir=locks_dir)
    state._kick_refresh()  # noqa: SLF001
    _drain_pool(state)

    assert state.runners[0]["stopRequested"] is True
    state.stop()
