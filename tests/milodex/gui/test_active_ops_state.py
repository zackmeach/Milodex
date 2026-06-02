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
    def _now(self) -> datetime:
        return datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)

    def test_none_last_eval(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        assert _heartbeat(None, self._now(), 60) == "no activity"

    def test_on_schedule_at_exactly_1_5x(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        now = self._now()
        # age = exactly 1.5 * 60 = 90s -- on schedule (boundary inclusive)
        last_eval = (now - timedelta(seconds=90)).isoformat()
        assert _heartbeat(last_eval, now, 60) == "on schedule"

    def test_just_over_1_5x_is_overdue(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        now = self._now()
        # age = 91s -- just over the 90s threshold
        last_eval = (now - timedelta(seconds=91)).isoformat()
        result = _heartbeat(last_eval, now, 60)
        assert result.startswith("overdue by ")

    def test_overdue_minutes_format(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        now = self._now()
        last_eval = (now - timedelta(minutes=10)).isoformat()
        result = _heartbeat(last_eval, now, 60)
        assert result == "overdue by 10m"

    def test_tz_naive_last_eval_does_not_raise(self) -> None:
        from milodex.gui.active_ops_state import _heartbeat

        now = self._now()
        naive_iso = "2026-05-16T11:59:30"  # no tz
        result = _heartbeat(naive_iso, now, 60)
        assert isinstance(result, str)


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
    """A runner with no explanations rows has heartbeat = 'no activity'."""
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


def test_read_only_connection_blocks_writes(tmp_path) -> None:
    """Verify the read-only URI connection pattern blocks writes."""
    db = tmp_path / "ro_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t(a INTEGER)")
    conn.commit()
    conn.close()

    ro_conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        ro_conn.execute("CREATE TABLE x(a)")
    ro_conn.close()


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
    state._thread_pool.waitForDone(2000)  # noqa: SLF001
    QCoreApplication.processEvents()
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
    """A runner with no explanations rows exposes heartbeat='no activity'."""
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
