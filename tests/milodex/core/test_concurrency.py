"""Cross-process / threaded concurrency tests for durable-state integrity.

Covers three hazards that the single-operator concurrency model
(``docs/OPERATIONS.md``) nonetheless exposes during normal startup,
because several components (``ExecutionService``, ``KillSwitchStateStore``,
the runner, the backtest engine) each construct their own
:class:`~milodex.core.event_store.EventStore`, and several entry points
each construct an :class:`~milodex.core.advisory_lock.AdvisoryLock`:

1. Concurrent :class:`EventStore` construction must apply each migration
   exactly once and converge on a single, consistent ``_schema_version``.
2. Concurrent advisory-lock acquisition must let exactly one winner
   through; every loser raises :class:`AdvisoryLockError`.
3. Concurrent event-store *writes* must not lose or corrupt rows.

Multiprocess vs threads
-----------------------
The advisory lock keys off the OS PID, so a *true* cross-process test is
required for the contention and ``O_EXCL`` race cases — threads in one
process share a PID and would not exercise the holder-liveness path.
Those use ``multiprocessing`` with the **spawn** start method (the
Windows default; we force it explicitly so the suite behaves the same on
Linux CI). Worker callables are module-level so they are picklable under
spawn.

The migration-race and concurrent-write cases are about SQLite
transaction isolation on one DB *file*. SQLite's locking is per
connection/file, not per PID, so threads contend on the database engine
exactly as separate processes do, and threads give a far tighter,
deterministic race window (a shared ``threading.Barrier`` releases all
workers simultaneously). They therefore use threads + a barrier by
design; this is called out where it applies.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from milodex.core.advisory_lock import AdvisoryLock, AdvisoryLockError
from milodex.core.event_store import (
    MIN_COMPATIBLE_SCHEMA_VERSION,
    EventStore,
    ExplanationEvent,
    TradeEvent,
)

_SPAWN_CTX = mp.get_context("spawn")


# --------------------------------------------------------------------------- #
# 1. Concurrent EventStore construction → migrations applied exactly once.
# --------------------------------------------------------------------------- #


def _construct_event_store_worker(db_path_str: str, barrier: threading.Barrier) -> None:
    """Construct an EventStore against a shared path, all workers at once."""
    barrier.wait()
    EventStore(Path(db_path_str))


def test_concurrent_event_store_construction_migrates_exactly_once(tmp_path):
    """N threads constructing one fresh DB must converge on a coherent schema.

    Threads (not processes) by design: SQLite serializes on the database
    *file*, so concurrent threads hammer the migration critical section
    exactly as separate processes would, and a shared barrier gives a
    deterministic simultaneous-start race window. With the pre-fix code
    (``executescript`` auto-commits, breaking DDL/version atomicity, and
    the version is read once *before* the loop) the racing constructors
    re-run an ``ALTER TABLE ... ADD COLUMN`` migration → ``OperationalError:
    duplicate column name`` and/or a multi-row / stale ``_schema_version``.
    """
    db_path = tmp_path / "concurrent_construct.db"
    worker_count = 8
    barrier = threading.Barrier(worker_count)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def run() -> None:
        try:
            _construct_event_store_worker(str(db_path), barrier)
        except BaseException as exc:  # noqa: BLE001 - we re-raise via assertion
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not any(t.is_alive() for t in threads), "a constructor thread hung"
    assert not errors, f"concurrent construction raised: {errors!r}"

    # Exactly one _schema_version row, at head, and >= the compat floor.
    store = EventStore(db_path)
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT version FROM _schema_version").fetchall()
    finally:
        connection.close()
    assert len(rows) == 1, f"expected exactly one _schema_version row, got {rows!r}"
    assert store.schema_version == rows[0][0]
    assert store.schema_version >= MIN_COMPATIBLE_SCHEMA_VERSION

    # Schema is coherent: a known table from the *last* migration exists,
    # proving every migration ran (not just the early ones).
    assert "orchestration_jobs" in store.list_table_names()
    assert "explanations" in store.list_table_names()


def test_split_sql_statements_handles_semicolons_in_literals_and_comments():
    """The migration splitter must not break on ``;`` in literals/comments.

    Regression lock for the statement splitter ``_apply_migrations``
    relies on: a naive ``str.split(';')`` mis-splits the real migrations
    (006 has ``;`` and ``'`` inside SQL comments). The splitter uses
    SQLite's own tokenizer; assert the tricky cases stay intact.
    """
    from milodex.core.event_store import _split_sql_statements

    sql = (
        "-- a comment with a ; and an apostrophe's quote\n"
        "CREATE TABLE t (a TEXT);\n"
        "INSERT INTO t(a) VALUES ('x;y');\n"
        "CREATE INDEX i ON t(a)\n"  # no trailing semicolon
    )
    statements = _split_sql_statements(sql)
    # The ';' in the comment and in 'x;y' must NOT create extra splits.
    # (Leading comments stay attached to the next statement; SQLite
    # ignores them, so that is correct and harmless.)
    assert len(statements) == 3, statements
    assert "CREATE TABLE t" in statements[0]
    assert "'x;y'" in statements[1]
    assert "CREATE INDEX i" in statements[2]

    # And it round-trips every real migration through SQLite without error.
    import sqlite3 as _sqlite3

    migrations_dir = Path(__file__).resolve().parents[3] / "src" / "milodex" / "core" / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))
    assert sql_files, "no migration files found"
    connection = _sqlite3.connect(":memory:")
    try:
        for path in sql_files:
            for statement in _split_sql_statements(path.read_text(encoding="utf-8")):
                connection.execute(statement)
    finally:
        connection.close()


# --------------------------------------------------------------------------- #
# 2. Multiprocess advisory-lock contention → exactly one winner.
# --------------------------------------------------------------------------- #


def _lock_contender(
    locks_dir_str: str,
    start_evt,
    result_q,
) -> None:
    """Block on a barrier-ish event, then race to acquire the lock."""
    start_evt.wait(timeout=30)
    lock = AdvisoryLock("milodex.runtime", locks_dir=Path(locks_dir_str))
    try:
        lock.acquire()
    except AdvisoryLockError:
        result_q.put(("blocked", os.getpid()))
        return
    # Hold briefly so the other contender definitely sees us live, then
    # report success WITHOUT releasing (the test asserts on raw outcomes).
    result_q.put(("acquired", os.getpid()))
    # Keep the process alive long enough for the sibling to observe the
    # held lock, then exit (cleanup is the test's responsibility).
    import time

    time.sleep(2.0)


def test_multiprocess_advisory_lock_contention_single_winner(tmp_path):
    """Two real processes race for one lock: exactly one wins, one is blocked.

    True multiprocess (spawn): the advisory lock keys off the OS PID and
    its holder-liveness check, so threads (shared PID) would not exercise
    the real contention path. Picklable module-level worker; generous
    timeouts; temp dir auto-cleaned by the ``tmp_path`` fixture.
    """
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    start_evt = _SPAWN_CTX.Event()
    result_q: mp.Queue = _SPAWN_CTX.Queue()

    procs = [
        _SPAWN_CTX.Process(
            target=_lock_contender,
            args=(str(locks_dir), start_evt, result_q),
        )
        for _ in range(2)
    ]
    for proc in procs:
        proc.start()
    start_evt.set()

    results = []
    for _ in range(2):
        results.append(result_q.get(timeout=30))
    for proc in procs:
        proc.join(timeout=30)
        assert not proc.is_alive(), "a lock contender process hung"

    outcomes = sorted(outcome for outcome, _pid in results)
    assert outcomes == ["acquired", "blocked"], f"unexpected outcomes: {results!r}"


# --------------------------------------------------------------------------- #
# 3. Concurrent event-store writes → no lost or corrupted rows.
# --------------------------------------------------------------------------- #


def _writer_worker(db_path_str: str, writer_id: int, rows_each: int, barrier) -> None:
    """Append ``rows_each`` explanation+trade pairs to a shared DB."""
    barrier.wait()
    store = EventStore(Path(db_path_str))
    for i in range(rows_each):
        exp = ExplanationEvent(
            recorded_at=datetime.now(tz=UTC),
            decision_type="preview",
            status="ok",
            strategy_name=f"w{writer_id}",
            strategy_stage="paper",
            strategy_config_path=None,
            config_hash=None,
            symbol="SPY",
            side="buy",
            quantity=1.0,
            order_type="market",
            time_in_force="day",
            submitted_by="operator",
            market_open=True,
            latest_bar_timestamp=None,
            latest_bar_close=None,
            account_equity=1000.0,
            account_cash=1000.0,
            account_portfolio_value=1000.0,
            account_daily_pnl=0.0,
            risk_allowed=True,
            risk_summary="ok",
            reason_codes=[],
            risk_checks=[],
            context={"writer": writer_id, "i": i},
        )
        exp_id = store.append_explanation(exp)
        store.append_trade(
            TradeEvent(
                explanation_id=exp_id,
                recorded_at=datetime.now(tz=UTC),
                status="submitted",
                source="paper",
                symbol="SPY",
                side="buy",
                quantity=1.0,
                order_type="market",
                time_in_force="day",
                estimated_unit_price=1.0,
                estimated_order_value=1.0,
                strategy_name=f"w{writer_id}",
                strategy_stage="paper",
                strategy_config_path=None,
                submitted_by="operator",
                broker_order_id=None,
                broker_status=None,
                message=None,
            )
        )


def test_concurrent_event_store_writes_no_lost_rows(tmp_path):
    """Many concurrent writers; final state has every row, schema coherent.

    Threads + barrier by design (same rationale as the migration test:
    SQLite contends per-file, threads give a tight deterministic window,
    and ``sqlite3`` releases the GIL around the C calls so the writes
    genuinely interleave). Each writer also constructs its own
    ``EventStore`` first, doubling as extra migration-race pressure.
    """
    db_path = tmp_path / "concurrent_writes.db"
    # Pre-create so the schema exists before the racing writers start;
    # the writers still each construct their own store (migration re-check).
    EventStore(db_path)

    writer_count = 6
    rows_each = 25
    barrier = threading.Barrier(writer_count)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def run(writer_id: int) -> None:
        try:
            _writer_worker(str(db_path), writer_id, rows_each, barrier)
        except BaseException as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(exc)

    threads = [threading.Thread(target=run, args=(wid,)) for wid in range(writer_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not any(t.is_alive() for t in threads), "a writer thread hung"
    assert not errors, f"concurrent writes raised: {errors!r}"

    store = EventStore(db_path)
    explanations = store.list_explanations()
    trades = store.list_trades()
    expected = writer_count * rows_each
    assert len(explanations) == expected, (
        f"lost explanation rows: {len(explanations)} != {expected}"
    )
    assert len(trades) == expected, f"lost trade rows: {len(trades)} != {expected}"
    # Every trade points at a real explanation (no corruption / FK drift).
    explanation_ids = {e.id for e in explanations}
    assert all(t.explanation_id in explanation_ids for t in trades)
    assert store.schema_version >= MIN_COMPATIBLE_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# 4. Cross-process O_EXCL acquire race → FileExistsError branch.
# --------------------------------------------------------------------------- #


def _exclusive_creator(lock_path_str: str, start_evt, result_q) -> None:
    """Simulate the holder that already created the lock file."""
    start_evt.wait(timeout=30)
    # Win the file-creation race by creating the lock file directly with
    # this process's pid recorded, so the sibling's os.open(O_EXCL) fails.
    import json

    Path(lock_path_str).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": "host",
                "holder_name": "milodex",
                "started_at": datetime.now(tz=UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    result_q.put(("creator", os.getpid()))
    import time

    time.sleep(2.0)


def _exclusive_racer(locks_dir_str: str, start_evt, result_q) -> None:
    """Race into acquire() after the creator has placed the file."""
    start_evt.wait(timeout=30)
    # Give the creator a head start so the lock file already exists and
    # its PID is live → we must hit the held-lock path (or, if our
    # stale-check passes, the O_EXCL FileExistsError branch).
    import time

    time.sleep(0.5)
    lock = AdvisoryLock("milodex.runtime", locks_dir=Path(locks_dir_str))
    try:
        lock.acquire()
        result_q.put(("acquired", os.getpid()))
    except AdvisoryLockError:
        result_q.put(("blocked", os.getpid()))


def test_cross_process_oexcl_acquire_race_blocks_racer(tmp_path):
    """Racer must NOT acquire a lock whose file exists and whose PID is live.

    Exercises the cross-process branch around ``os.open(... O_EXCL ...)``
    in ``advisory_lock.acquire`` (``FileExistsError`` → re-read holder →
    ``AdvisoryLockError``) and the held-fresh-lock guard. True
    multiprocess (spawn) because both the liveness check and the file
    race are PID-scoped. The creator is a *live* sibling process holding
    a fresh lock, so the racer must be blocked.
    """
    locks_dir = tmp_path / "locks"
    locks_dir.mkdir()
    lock_path = locks_dir / "milodex.runtime.lock"
    start_evt = _SPAWN_CTX.Event()
    result_q: mp.Queue = _SPAWN_CTX.Queue()

    creator = _SPAWN_CTX.Process(
        target=_exclusive_creator, args=(str(lock_path), start_evt, result_q)
    )
    racer = _SPAWN_CTX.Process(target=_exclusive_racer, args=(str(locks_dir), start_evt, result_q))
    creator.start()
    racer.start()
    start_evt.set()

    results = {}
    for _ in range(2):
        outcome, pid = result_q.get(timeout=30)
        results[outcome] = pid
    creator.join(timeout=30)
    racer.join(timeout=30)
    assert not creator.is_alive() and not racer.is_alive(), "a process hung"

    assert "creator" in results
    assert "blocked" in results, (
        f"racer must be blocked by the live fresh-lock holder, got {results!r}"
    )
    assert "acquired" not in results
