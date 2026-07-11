"""The M4 drill matrix: one function per fault-injection cell.

Each cell provisions a throwaway scratch env, injects a *real* fault, invokes
the *real* operator surface (a ``python -m milodex.cli.main`` subprocess and/or
a real event-store query), asserts on the operator-facing message content plus
the durable record, and returns a :class:`~scripts.drills.harness.DrillResult`.

Grounding (verified against the current source, not assumed):

* stale-data idle alert + heal pointer — ``strategies/runner.py``
  ``_emit_stale_bar_idle_alert`` (``stale_market_data_idle`` /
  ``append_operator_alert`` / the ``fetch-universe`` warning); rendered by
  ``cli/commands/strategy.py`` ``_format_operator_alerts_block``.
* event_store_locked / event_store_corrupt — ``cli/main.py`` ``sqlite3.DatabaseError``
  handler.
* BrokerAuthError / BrokerConnectionError actionable translation —
  ``broker/alpaca_client.py`` ``_read_call``.
* phantom + reap note / ``orphaned_no_live_runner`` closure —
  ``strategies/runner_status.py`` ``_PHANTOM_NOTE`` and
  ``strategies/orphan_reconciliation.py`` ``_ORPHAN_EXIT_REASON``.
* wedged / moot controlled-stop classification — ``strategies/runner_status.py``
  ``classify_stop_request`` + ``cli/commands/strategy.py`` ``_STOP_REQUEST_LABELS``.
* halt fail-soft + kill-switch activate/reset rows — ``cli/commands/halt.py``,
  ``cli/commands/trade.py``, ``execution/state.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from scripts.drills.harness import (
    BOGUS_API_KEY,
    RISK_DEFAULTS_YAML,
    DrillResult,
    StubBroker,
    StubProvider,
    _trim,
    build_barset,
    provision_scratch,
    run_cli,
    spawn_dead_pid,
    spawn_live_process,
    write_lock_file,
    write_regime_config,
)

_REGIME_SID = "regime.daily.sma200_rotation.spy_shy.v1"


class _ListHandler(logging.Handler):
    """Collect log records in-memory for assertion."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _fmt_checks(checks: list[tuple[str, bool]]) -> str:
    return "\n".join(f"  [{'PASS' if ok else 'FAIL'}] {label}" for label, ok in checks)


# --- cell 1: stale_market_data --------------------------------------------


def cell_stale_market_data() -> DrillResult:
    scratch = provision_scratch("milodex-drill-stale-")
    handler = _ListHandler()
    runner_logger = logging.getLogger("milodex.strategies.runner")
    runner_logger.addHandler(handler)
    prior_level = runner_logger.level
    runner_logger.setLevel(logging.WARNING)
    try:
        from milodex.broker.models import AccountInfo
        from milodex.core.event_store import EventStore
        from milodex.execution import ExecutionService
        from milodex.execution.state import KillSwitchStateStore
        from milodex.promotion import freeze_manifest
        from milodex.strategies.runner import StrategyRunner

        config_dir = write_regime_config(scratch.root / "configs")
        risk_path = scratch.root / "risk_defaults.yaml"
        risk_path.write_text(RISK_DEFAULTS_YAML, encoding="utf-8")

        event_store = EventStore(scratch.db_path)
        provider = StubProvider(
            {"SPY": build_barset([10.0, 10.0, 10.0]), "SHY": build_barset([10.0, 10.0, 10.0])}
        )
        broker = StubBroker(
            account=AccountInfo(
                equity=10_000.0,
                cash=10_000.0,
                buying_power=10_000.0,
                portfolio_value=10_000.0,
                daily_pnl=0.0,
            ),
            market_open=False,
        )
        kill_switch_store = KillSwitchStateStore(
            event_store=event_store,
            legacy_path=scratch.logs_dir / "kill_switch_state.json",
        )
        service = ExecutionService(
            broker_client=broker,
            data_provider=provider,
            risk_defaults_path=risk_path,
            kill_switch_store=kill_switch_store,
            event_store=event_store,
        )
        freeze_manifest(config_dir / "regime_runner.yaml", event_store=event_store)
        runner = StrategyRunner(
            strategy_id=_REGIME_SID,
            config_dir=config_dir,
            broker_client=broker,
            data_provider=provider,
            execution_service=service,
            event_store=event_store,
        )
        # Fault: pin the clock +2 days past the latest bar so the newest
        # available daily bar is a PRIOR session's close (the stale-decline case).
        latest_ts = provider.get_latest_bar("SPY").timestamp
        fake_now = latest_ts.to_pydatetime() + timedelta(days=2)
        runner._now = lambda: fake_now  # noqa: SLF001

        for _ in range(3):
            runner.run_cycle()

        alerts = event_store.list_operator_alerts(alert_type="stale_market_data_idle")
        warnings = [r.getMessage() for r in handler.records if "idling on STALE" in r.getMessage()]

        # Operator surface: the CLI renders the durable alert.
        status = run_cli(["strategy", "status"], scratch)

        checks = [
            ("exactly one durable stale_market_data_idle alert row", len(alerts) == 1),
            (
                "durable alert severity is warning",
                bool(alerts) and alerts[0].severity == "warning",
            ),
            (
                "logged warning names the fetch-universe heal",
                any("fetch-universe" in w for w in warnings),
            ),
            ("`strategy status` exits 0", status.returncode == 0),
            ("`strategy status` renders the alert", "stale_market_data_idle" in status.stdout),
            ("`strategy status` marks it [warning]", "[warning]" in status.stdout),
        ]
        passed = all(ok for _, ok in checks)
        durable = (
            f"list_operator_alerts(alert_type='stale_market_data_idle') -> {len(alerts)} row(s)\n"
            + (f"summary: {alerts[0].summary}" if alerts else "")
        )
        return DrillResult(
            name="stale_market_data",
            status="PASS" if passed else "FAIL",
            fault="in-process daily StrategyRunner polled with clock +2d past the latest bar "
            "(stub provider returns a prior-session daily bar) against a scratch event store",
            operator_output=_trim(
                status.stdout, keep=("operator alerts", "stale_market_data_idle", "[warning]")
            )
            + "\n-- logged warning --\n"
            + (warnings[0] if warnings else "(none)"),
            durable_record=durable,
            detail=_fmt_checks(checks),
        )
    finally:
        runner_logger.removeHandler(handler)
        runner_logger.setLevel(prior_level)
        scratch.cleanup()


# --- cell 2: locked_db (slow: ~30s busy_timeout) ---------------------------


def cell_locked_db() -> DrillResult:
    import sqlite3

    scratch = provision_scratch("milodex-drill-locked-")
    holder: sqlite3.Connection | None = None
    try:
        from milodex.core.event_store import EventStore

        EventStore(scratch.db_path)  # create + migrate the scratch store

        # Fault: hold the write lock on a second connection so the CLI's write
        # blocks for the full busy_timeout (30s) then fails "database is locked".
        holder = sqlite3.connect(str(scratch.db_path))
        holder.execute("PRAGMA busy_timeout=0")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute(
            "INSERT INTO kill_switch_events (event_type, recorded_at) VALUES ('drill_hold', ?)",
            (datetime.now(tz=UTC).isoformat(),),
        )

        run = run_cli(["trade", "kill-switch", "reset", "--confirm"], scratch, timeout=90)

        combined = run.combined
        checks = [
            ("CLI exits nonzero (fail-closed)", run.returncode != 0),
            ("message reports the store is locked", "is locked" in combined.lower()),
            ("message points at TROUBLESHOOTING", "troubleshooting" in combined.lower()),
        ]
        passed = all(ok for _, ok in checks)
        return DrillResult(
            name="locked_db",
            status="PASS" if passed else "FAIL",
            fault="a second connection holds BEGIN IMMEDIATE on the scratch DB; the CLI write "
            "blocks for the 30s busy_timeout then errors",
            operator_output=_trim(combined, keep=("error", "locked", "troubleshooting")),
            durable_record=f"exit code {run.returncode}",
            detail=_fmt_checks(checks),
            slow=True,
        )
    finally:
        if holder is not None:
            holder.rollback()
            holder.close()
        scratch.cleanup()


# --- cell 3: corrupt_db ----------------------------------------------------


def cell_corrupt_db() -> DrillResult:
    scratch = provision_scratch("milodex-drill-corrupt-")
    try:
        # Fault: place garbage bytes at the event-store path so the CLI's
        # EventStore construction fails to open it -> sqlite3.DatabaseError
        # ("file is not a database") -> event_store_corrupt.
        scratch.db_path.write_bytes(b"NOT-A-SQLITE-DATABASE " * 64)

        run = run_cli(["trade", "kill-switch", "status"], scratch)

        combined = run.combined
        checks = [
            ("CLI exits nonzero (fail-closed)", run.returncode != 0),
            (
                "message reports unreadable/corrupt store",
                "unreadable or corrupt" in combined.lower(),
            ),
            ("message points at the .pre-compact-*.bak restore", ".pre-compact-" in combined),
            ("message points at TROUBLESHOOTING", "troubleshooting" in combined.lower()),
        ]
        passed = all(ok for _, ok in checks)
        return DrillResult(
            name="corrupt_db",
            status="PASS" if passed else "FAIL",
            fault="garbage bytes written over the scratch milodex.db (WAL side files removed)",
            operator_output=_trim(
                combined, keep=("error", "corrupt", "pre-compact", "troubleshooting")
            ),
            durable_record=f"exit code {run.returncode}",
            detail=_fmt_checks(checks),
        )
    finally:
        scratch.cleanup()


# --- cell 4: broker_outage -------------------------------------------------


def cell_broker_outage() -> DrillResult:
    scratch = provision_scratch("milodex-drill-broker-")
    try:
        # Fault: syntactically-valid but bogus Alpaca keys. A broker read
        # (`milodex status` -> get_account) fails closed: an auth-classified
        # BrokerAuthError (naming ALPACA_API_KEY/.env) when the endpoint is
        # reachable, or a BrokerConnectionError when it is not. Either way the
        # CLI never proceeds as if connected.
        run = run_cli(["status"], scratch, creds="bogus", trading_mode="paper")

        combined = run.combined
        auth_classified = "ALPACA_API_KEY" in combined
        conn_classified = "could not reach the broker" in combined.lower()
        checks = [
            ("CLI exits nonzero (fail-closed)", run.returncode != 0),
            ("did NOT proceed as if connected (no account/equity line)", "Equity:" not in combined),
            (
                "broker-classified actionable error (auth names ALPACA_API_KEY, or connection)",
                auth_classified or conn_classified,
            ),
        ]
        passed = all(ok for _, ok in checks)
        classification = (
            "BrokerAuthError (endpoint reached, 401 on bogus keys)"
            if auth_classified
            else "BrokerConnectionError (endpoint unreachable)"
            if conn_classified
            else "unclassified"
        )
        return DrillResult(
            name="broker_outage",
            status="PASS" if passed else "FAIL",
            fault=f"bogus Alpaca credentials (key {BOGUS_API_KEY[:8]}…) on `milodex status`",
            operator_output=_trim(combined, keep=("error", "alpaca", "broker", "reach")),
            durable_record=f"exit code {run.returncode}; classification: {classification}",
            detail=_fmt_checks(checks)
            + "\n  note: variant (b) unreachable-endpoint requires a broker-URL seam that does "
            "not exist without a code change; the bogus-cred path exercises the same fail-closed "
            "classification (auth online, connection offline).",
        )
    finally:
        scratch.cleanup()


# --- cell 5: dead_runner ---------------------------------------------------

_DEAD_SID = "drill.dead.daily.rotation.v1"


def cell_dead_runner() -> DrillResult:
    scratch = provision_scratch("milodex-drill-dead-")
    try:
        from milodex.core.event_store import EventStore, StrategyRunEvent

        now = datetime.now(tz=UTC)
        event_store = EventStore(scratch.db_path)
        event_store.append_strategy_run(
            StrategyRunEvent(
                session_id="drill-dead-session",
                strategy_id=_DEAD_SID,
                started_at=now,
                ended_at=None,
                exit_reason=None,
                metadata={},
            )
        )
        # Fault: post-hard-kill state = open run row + a lock file naming a
        # genuinely-dead PID (a spawned-then-exited process).
        dead_pid = spawn_dead_pid()
        write_lock_file(scratch.locks_dir, _DEAD_SID, pid=dead_pid, started_at=now)

        status = run_cli(["strategy", "status", _DEAD_SID], scratch)
        reap = run_cli(["maintenance", "reap-orphans"], scratch)

        # Durable closure record (queried from a fresh reader).
        closed = None
        for run_row in EventStore(scratch.db_path).list_strategy_runs():
            if run_row.strategy_id == _DEAD_SID:
                closed = run_row
        durable_ok = (
            closed is not None
            and closed.ended_at is not None
            and closed.exit_reason == "orphaned_no_live_runner"
        )
        checks = [
            ("`strategy status` reports phantom", "phantom" in status.stdout),
            ("phantom note names reap-orphans", "reap-orphans" in status.stdout),
            ("reap-orphans exits 0", reap.returncode == 0),
            ("reap-orphans reports a closure", "Reaped 1" in reap.stdout),
            (
                "durable strategy_runs row closed with exit_reason=orphaned_no_live_runner",
                bool(durable_ok),
            ),
        ]
        passed = all(ok for _, ok in checks)
        durable = (
            f"strategy_runs[{_DEAD_SID}]: ended_at={closed.ended_at if closed else None}, "
            f"exit_reason={closed.exit_reason if closed else None}"
        )
        return DrillResult(
            name="dead_runner",
            status="PASS" if passed else "FAIL",
            fault=f"open strategy_runs row + lock file naming dead PID {dead_pid}",
            operator_output=_trim(status.stdout, keep=("phantom", "reap-orphans"))
            + "\n-- reap-orphans --\n"
            + _trim(reap.stdout, keep=("reaped",)),
            durable_record=durable,
            detail=_fmt_checks(checks),
        )
    finally:
        scratch.cleanup()


# --- cell 6: wedged_stop ---------------------------------------------------

_WEDGED_SID = "drill.wedged.daily.rotation.v1"


def cell_wedged_stop() -> DrillResult:
    scratch = provision_scratch("milodex-drill-wedged-")
    live_proc = None
    try:
        import os

        from milodex.core.event_store import EventStore, StrategyRunEvent
        from milodex.strategies.paper_runner_control import controlled_stop_request_path

        now = datetime.now(tz=UTC)
        event_store = EventStore(scratch.db_path)
        event_store.append_strategy_run(
            StrategyRunEvent(
                session_id="drill-wedged-session",
                strategy_id=_WEDGED_SID,
                started_at=now,
                ended_at=None,
                exit_reason=None,
                metadata={},
            )
        )
        # Fault (wedged): a genuinely-live process holds the runner lock, and a
        # controlled-stop request has sat unconsumed past 3x the 60s cadence.
        live_proc = spawn_live_process(600)
        # started_at must be captured AFTER the spawn: the identity check
        # accepts the holder only if proc_start <= started_at + 1s grace
        # (advisory_lock._PID_REUSE_GRACE), and on a slow CI runner the store
        # setup above can push the spawn >1s past an earlier `now`.
        lock_started_at = datetime.now(tz=UTC)
        write_lock_file(
            scratch.locks_dir, _WEDGED_SID, pid=live_proc.pid, started_at=lock_started_at
        )
        stop_path = controlled_stop_request_path(scratch.locks_dir, _WEDGED_SID)
        stop_path.write_text('{"requested_by": "drill"}', encoding="utf-8")
        backdated = (now - timedelta(seconds=300)).timestamp()
        os.utime(stop_path, (backdated, backdated))

        wedged = run_cli(["strategy", "status", _WEDGED_SID], scratch)

        # Moot variant: kill the holder so no live process holds the lock.
        live_proc.terminate()
        live_proc.wait(timeout=30)
        live_proc = None
        moot = run_cli(["strategy", "status", _WEDGED_SID], scratch)

        checks = [
            (
                "wedged: `strategy status` renders UNCONSUMED (runner wedged)",
                "UNCONSUMED (runner wedged)" in wedged.stdout,
            ),
            ("wedged: remediation names hard-kill", "hard-kill" in wedged.stdout),
            ("wedged: remediation points at TROUBLESHOOTING", "TROUBLESHOOTING" in wedged.stdout),
            ("moot variant: classified moot (runner not live)", "moot" in moot.stdout),
        ]
        passed = all(ok for _, ok in checks)
        return DrillResult(
            name="wedged_stop",
            status="PASS" if passed else "FAIL",
            fault="live process holds the runner lock + a controlled-stop request backdated 300s "
            "(past 3x the 60s cadence); moot variant re-checks with a dead holder",
            operator_output=_trim(
                wedged.stdout, keep=("stop requested", "stop request", "unconsumed", "wedged")
            )
            + "\n-- moot variant --\n"
            + _trim(moot.stdout, keep=("stop requested", "stop request", "moot")),
            durable_record="(filesystem-only: lock + controlled_stop.json under scratch locks dir)",
            detail=_fmt_checks(checks),
        )
    finally:
        if live_proc is not None:
            live_proc.terminate()
            try:
                live_proc.wait(timeout=30)
            except Exception:  # noqa: BLE001
                live_proc.kill()
        scratch.cleanup()


# --- cell 7: kill_switch_trip_reset ---------------------------------------


def cell_kill_switch_trip_reset() -> DrillResult:
    scratch = provision_scratch("milodex-drill-kill-")
    try:
        from milodex.core.event_store import EventStore

        EventStore(scratch.db_path)  # pre-create the scratch store

        # Fault/trip: operator manual halt. The broker cancel step fails on bogus
        # creds; the kill switch must still trip durably and report honestly.
        halt = run_cli(["halt", "--confirm"], scratch, creds="bogus")
        activated = EventStore(scratch.db_path).get_latest_kill_switch_event()

        ks_status = run_cli(["trade", "kill-switch", "status"], scratch, creds="bogus")

        # Reset must REQUIRE --confirm.
        no_confirm = run_cli(["trade", "kill-switch", "reset"], scratch, creds="bogus")

        # `trade kill-switch reset` takes only --confirm (no --reason flag; the
        # reset event records reason=None by design — execution/state.py reset()).
        reset = run_cli(
            ["trade", "kill-switch", "reset", "--confirm"],
            scratch,
            creds="bogus",
        )
        after_reset = EventStore(scratch.db_path).get_latest_kill_switch_event()

        checks = [
            ("halt exits 0 (fail-soft trip)", halt.returncode == 0),
            (
                "halt reports the cancel step failed on bogus creds",
                "cancel_all_orders FAILED" in halt.stdout,
            ),
            ("halt reports kill switch active", "Kill switch: active" in halt.stdout),
            (
                "durable kill_switch_events 'activated' row",
                activated is not None and activated.event_type == "activated",
            ),
            ("`kill-switch status` shows Active: yes", "Active: yes" in ks_status.stdout),
            ("reset WITHOUT --confirm is refused (nonzero)", no_confirm.returncode != 0),
            (
                "refusal names the --confirm requirement",
                "requires --confirm" in no_confirm.combined,
            ),
            (
                "reset --confirm exits 0 and clears it",
                reset.returncode == 0 and "Now active: no" in reset.stdout,
            ),
            (
                "durable kill_switch_events 'reset' row",
                after_reset is not None and after_reset.event_type == "reset",
            ),
        ]
        passed = all(ok for _, ok in checks)
        latest_after_reset = after_reset.event_type if after_reset else None
        durable = (
            f"after halt: latest kill_switch_event={activated.event_type if activated else None} "
            f"(reason={activated.reason if activated else None})\n"
            f"after reset: latest kill_switch_event={latest_after_reset}"
        )
        return DrillResult(
            name="kill_switch_trip_reset",
            status="PASS" if passed else "FAIL",
            fault="`milodex halt --confirm` with bogus creds (cancel fails) then a reset cycle; "
            "scratch event store only",
            operator_output=_trim(halt.stdout, keep=("halt", "orders:", "kill switch:"))
            + "\n-- kill-switch status --\n"
            + _trim(ks_status.stdout, keep=("active", "reason"))
            + "\n-- reset without --confirm --\n"
            + _trim(no_confirm.combined, keep=("requires --confirm",))
            + "\n-- reset --confirm --\n"
            + _trim(reset.stdout, keep=("now active",)),
            durable_record=durable,
            detail=_fmt_checks(checks),
        )
    finally:
        scratch.cleanup()


# --- cell 8: clean_room (bonus) -------------------------------------------


def cell_clean_room() -> DrillResult:
    scratch = provision_scratch("milodex-drill-clean-")
    try:
        from milodex.core.event_store import EventStore

        # (a) No .env / no credentials -> `milodex status` fails CLOSED with the
        # copy-.env.example message.
        no_env = run_cli(["status"], scratch, creds="none", trading_mode=None)

        # (b) A fresh data dir auto-creates + migrates the event store.
        scratch2 = provision_scratch("milodex-drill-clean-db-")
        try:
            store = EventStore(scratch2.db_path)
            version = store.schema_version
            tables = store.list_table_names()
        finally:
            scratch2.cleanup()

        combined = no_env.combined
        checks = [
            ("`milodex status` exits nonzero (fail-closed)", no_env.returncode != 0),
            ("names the missing ALPACA_API_KEY", "ALPACA_API_KEY is not set" in combined),
            ("names the copy-.env.example fix", "Copy .env.example" in combined),
            ("fresh DB auto-migrated (schema_version > 0)", version > 0),
            ("fresh DB created its tables", len(tables) >= 5),
        ]
        passed = all(ok for _, ok in checks)
        return DrillResult(
            name="clean_room",
            status="PASS" if passed else "FAIL",
            fault="fresh scratch dir, no .env / no creds (part a) + empty data dir (part b)",
            operator_output=_trim(combined, keep=("error", "alpaca_api_key", ".env")),
            durable_record=f"fresh event store: schema_version={version}, {len(tables)} tables",
            detail=_fmt_checks(checks),
        )
    finally:
        scratch.cleanup()


# --- registry --------------------------------------------------------------

# name -> (callable, slow?). ``slow`` cells (locked_db holds the 30s busy_timeout)
# are excluded from the fast CI wrapper but always run in the standalone harness.
CELL_REGISTRY: dict[str, object] = {
    "stale_market_data": cell_stale_market_data,
    "locked_db": cell_locked_db,
    "corrupt_db": cell_corrupt_db,
    "broker_outage": cell_broker_outage,
    "dead_runner": cell_dead_runner,
    "wedged_stop": cell_wedged_stop,
    "kill_switch_trip_reset": cell_kill_switch_trip_reset,
    "clean_room": cell_clean_room,
}

SLOW_CELLS: frozenset[str] = frozenset({"locked_db"})

# Cells that make an outbound (unauthenticated, bogus-cred) Alpaca request. They
# are network-dependent for their exact classification and are excluded from the
# offline CI wrapper.
NETWORK_CELLS: frozenset[str] = frozenset({"broker_outage", "kill_switch_trip_reset"})
