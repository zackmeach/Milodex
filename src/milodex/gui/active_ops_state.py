"""Live active-operations model exposed to QML.

Owns runner state for each strategy: session status, cadence label, last
evaluation time, heartbeat health, advisory-lock hold status, controlled-stop
sentinel presence, and session age.  Refreshed periodically from the
``strategy_runs`` and ``explanations`` SQLite tables.

Threading model
---------------
Identical to :mod:`milodex.gui.performance_state`.

Read-only guarantee
-------------------
All SQLite connections are opened ``file:<path>?mode=ro`` (URI mode).
Advisory-lock and stop-sentinel inspection are filesystem reads only.

Cadence derivation (operator checkpoint)
-----------------------------------------
The real SQLite DB has no ``strategy_manifests`` table (empty DB at
inspection time).  All strategy YAMLs uniformly use ``tempo.bar_size: "1D"``
with no discrete schedule field.

Decision-rule branch taken: **only bar_size-style values exist**.

Cadence label  : ``"daily (1D)"`` from ``tempo.bar_size = "1D"``.
Cadence seconds: ``60`` -- runner poll interval for 1D bars per
                 ``milodex.strategies.runner._POLL_INTERVAL_BY_BAR_SIZE``.

Heartbeat signal
-----------------
``heartbeat`` reflects the age of the runner's advisory-lock file mtime,
which :meth:`AdvisoryLock.refresh` bumps every poll cycle (≈60 s for 1D
bars) *before* the market-hours gate.  This is a true per-poll check-in
signal independent of bar cadence — a healthy 1D runner that has not
evaluated today still has a fresh lock (≤60 s old) and reads "on schedule".
The prior explanation-recency approach caused daily runners to read
"overdue" all day, making genuine stalls indistinguishable from idle.
The ``lastEval`` field still carries the genuine last-evaluation timestamp
(MAX ``explanations.recorded_at``) for informational display.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, Signal  # pragma: no cover

from milodex.gui._event_queries import (
    resolve_runner_liveness,
    runner_lock_live,
    runner_lock_mtime_age,
)
from milodex.gui.polling_lifecycle import PollingReadModel
from milodex.gui.row_formatters import _short_strategy_name
from milodex.strategies.paper_runner_control import controlled_stop_request_path
from milodex.strategies.runner_status import heartbeat_label

logger = logging.getLogger(__name__)

_DEFAULT_CADENCE_LABEL = "daily (1D)"
_DEFAULT_CADENCE_SECONDS = 60

_BAR_SIZE_TO_LABEL: dict[str, str] = {
    "1D": "daily (1D)",
    "1H": "hourly (1H)",
    "15Min": "15-min (15Min)",
    "5Min": "5-min (5Min)",
    "1Min": "1-min (1Min)",
}

_BAR_SIZE_TO_SECONDS: dict[str, int] = {
    "1D": 60,
    "1H": 30,
    "15Min": 15,
    "5Min": 10,
    "1Min": 5,
}


def _cadence_label(config: dict[str, Any] | None) -> str:
    """Return a human-readable cadence string from a strategy config dict."""
    if config is None:
        return _DEFAULT_CADENCE_LABEL
    try:
        bar_size = config["strategy"]["tempo"]["bar_size"]
        return _BAR_SIZE_TO_LABEL.get(bar_size, f"bar {bar_size}")
    except (KeyError, TypeError):
        return _DEFAULT_CADENCE_LABEL


def _cadence_seconds(config: dict[str, Any] | None) -> int:
    """Return runner poll interval in seconds from a strategy config dict."""
    if config is None:
        return _DEFAULT_CADENCE_SECONDS
    try:
        bar_size = config["strategy"]["tempo"]["bar_size"]
        return _BAR_SIZE_TO_SECONDS.get(bar_size, _DEFAULT_CADENCE_SECONDS)
    except (KeyError, TypeError):
        return _DEFAULT_CADENCE_SECONDS


# Heartbeat classification moved to milodex.strategies.runner_status so the
# CLI status surface shares one definition; aliased to keep this module's
# call sites and tests stable.
_heartbeat = heartbeat_label


def _session_age(started_at_iso: str, now: datetime) -> str:
    started = datetime.fromisoformat(started_at_iso)
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    secs = int((now - started).total_seconds())
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


_SQL_LATEST_RUNS = """
SELECT
    strategy_id,
    session_id,
    started_at,
    ended_at,
    exit_reason
FROM (
    SELECT
        strategy_id,
        session_id,
        started_at,
        ended_at,
        exit_reason,
        ROW_NUMBER() OVER (
            PARTITION BY strategy_id
            ORDER BY started_at DESC, id DESC
        ) AS rn
    FROM strategy_runs
)
WHERE rn = 1
"""

# One aggregate pass per refresh (no N+1): lastEval plus the fleet-table
# today-counts, keyed on the STRATEGY (explanations.strategy_name stores the
# canonical strategy_id — runner.py sets strategy_name=self._strategy_id at
# every write site), NOT the latest session.  Session-keying lost a relaunched
# strategy's earlier same-day sessions from the Today columns (hard-kill +
# relaunch is a real fleet path); strategy-keying sums across all of today's
# sessions.  "Today" is the UTC calendar day — recorded_at is an ISO-8601 UTC
# string, so the lexical >= compare against midnight matches the MAX()
# ordering assumption above and RiskThroughputState's Today slice.
# Classification mirrors the throughput funnel: a veto is `risk_allowed = 0`
# (the risk layer said no), a submit is `status = 'submitted'` (an order
# actually went to the broker — blocked `decision_type='submit'` rows do not
# count).  Two scope guards (dual-ancestor model, migration 008):
#   session_id IS NOT NULL      — excludes synthetic fault-injection rows
#                                 (promotion/fault_injection.py writes
#                                 session_id=None) and pre-session legacy rows
#   backtest_run_id IS NULL     — the canonical live/backtest discriminator;
#                                 backtest rows carry BOTH a session_id and a
#                                 backtest_run_id (simulation_kernel.py), so a
#                                 same-day backtest of a fleet strategy would
#                                 otherwise flood the Today columns (the
#                                 2026-05-29 benchmark-leak pattern)
_SQL_STRATEGY_STATS = """
SELECT
    strategy_name,
    MAX(recorded_at) AS last_eval,
    SUM(CASE WHEN recorded_at >= ? THEN 1 ELSE 0 END) AS evals_today,
    SUM(CASE WHEN recorded_at >= ? AND risk_allowed = 0 THEN 1 ELSE 0 END) AS vetoes_today,
    SUM(CASE WHEN recorded_at >= ? AND status = 'submitted' THEN 1 ELSE 0 END) AS submits_today
FROM explanations
WHERE strategy_name IN ({placeholders})
  AND session_id IS NOT NULL
  AND backtest_run_id IS NULL
GROUP BY strategy_name
"""


def _query_active_ops(
    db_path: Path,
    now: datetime,
    *,
    configs_dir: Path | None = None,
    locks_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Query strategy_runs and explanations; return per-runner dicts.

    Opens a read-only SQLite connection.  Raises on missing / unreadable DB.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        runs = conn.execute(_SQL_LATEST_RUNS).fetchall()
        if not runs:
            return []
        strategy_ids = [r["strategy_id"] for r in runs]
        placeholders = ",".join("?" * len(strategy_ids))
        today_start_iso = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        stat_rows = conn.execute(
            _SQL_STRATEGY_STATS.format(placeholders=placeholders),
            [today_start_iso, today_start_iso, today_start_iso, *strategy_ids],
        ).fetchall()
    finally:
        conn.close()

    stats_by_strategy: dict[str, sqlite3.Row] = {r["strategy_name"]: r for r in stat_rows}

    result: list[dict[str, Any]] = []
    for run in runs:
        strategy_id: str = run["strategy_id"]

        config = _load_config(strategy_id, configs_dir)
        label = _cadence_label(config)
        cad_secs = _cadence_seconds(config)
        display_name, family = _display_fields(strategy_id, config)

        stats = stats_by_strategy.get(strategy_id)
        last_eval: str | None = stats["last_eval"] if stats is not None else None

        # One identity-verified lock check, two distinct consumers (PR6).
        # `runner_lock_live` returns False when locks_dir is None (cannot
        # verify) — so runnerLock honestly reports "released" rather than
        # claiming a lock it never inspected. A genuinely-live process owning
        # the lock reads "held"; a hard-killed runner's stale lock reads
        # "released", surfacing the phantom (open row + released lock).
        lock_verified_live = runner_lock_live(strategy_id, locks_dir)
        runner_lock = "held" if lock_verified_live else "released"
        # sessionState back-compat guard: when locks_dir is None, phantom
        # detection is OFF — an open session resolves to legacy "running"
        # (lock_live=True). Only an explicit locks_dir engages phantom
        # detection. This guard must NOT leak into runnerLock above.
        session_lock_live = True if locks_dir is None else lock_verified_live
        session_state = resolve_runner_liveness(
            ended_at=run["ended_at"],
            exit_reason=run["exit_reason"],
            lock_live=session_lock_live,
        )

        stop_requested = False
        if locks_dir is not None:
            sentinel_path = controlled_stop_request_path(locks_dir, strategy_id)
            try:
                stop_requested = sentinel_path.exists()
            except Exception as exc:  # noqa: BLE001
                logger.warning("ActiveOpsState: sentinel check failed for %s: %s", strategy_id, exc)

        # Heartbeat is driven by the advisory-lock mtime (refreshed every poll
        # cycle, before the market-hours gate) rather than explanation recency.
        # A daily runner only writes an explanation once per day; its lock is
        # always ≤60 s old on a healthy session — the two signals are decoupled.
        #
        # Gate the mtime read on PID-verified liveness (lock_verified_live).
        # A hard-killed runner's lock file has a fresh mtime from moments before
        # death, but its PID is gone — reading the mtime would yield "on schedule"
        # while sessionState="phantom" and runnerLock="released".  Gating on
        # lock_verified_live makes all three signals coherent: PID dead →
        # lock_age=None → "no activity".  Also avoids the redundant I/O on the
        # dead-runner path.
        lock_age = (
            runner_lock_mtime_age(strategy_id, locks_dir, now) if lock_verified_live else None
        )

        result.append(
            {
                "strategyId": strategy_id,
                "displayName": display_name,
                "family": family,
                "sessionState": session_state,
                "cadence": label,
                "lastEval": last_eval,
                "heartbeat": _heartbeat(lock_age, cad_secs),
                # Raw lock-mtime age for the fleet table's compact ticking
                # column; None when unverifiable (same gating as heartbeat).
                "heartbeatAgeSeconds": lock_age,
                "runnerLock": runner_lock,
                "stopRequested": stop_requested,
                "sessionAge": _session_age(run["started_at"], now),
                "startedAt": str(run["started_at"] or ""),
                "endedAt": str(run["ended_at"] or ""),
                "evalsToday": int(stats["evals_today"]) if stats is not None else 0,
                "vetoesToday": int(stats["vetoes_today"]) if stats is not None else 0,
                "submitsToday": int(stats["submits_today"]) if stats is not None else 0,
            }
        )

    # Operator-facing order: live (PID-verified "running") sessions first,
    # then most recently started.  DeskSurface's Active Operations panel
    # defaults its selection to the first row, so an ancient stopped session
    # must never outrank a live — or newer — one.  ISO-8601 UTC strings
    # compare lexically, the same assumption _SQL_LATEST_RUNS makes.
    result.sort(key=lambda r: (r["sessionState"] == "running", r["startedAt"]), reverse=True)

    return result


def _display_fields(strategy_id: str, config: dict[str, Any] | None) -> tuple[str, str]:
    """Return (displayName, family) for a runner row.

    Same precedence as the Bench roster (row_formatters._display_name):
    ``strategy.display_name`` from the YAML when present, else the derived
    short name from the strategy id.  Family falls back to the id's first
    dotted segment when no config resolves (the id grammar is
    ``{family}.{template}.{variant}.v{version}``).
    """
    display_name = ""
    family = ""
    if config is not None:
        try:
            strategy = config.get("strategy") or {}
            raw_name = strategy.get("display_name")
            if isinstance(raw_name, str) and raw_name.strip():
                display_name = raw_name.strip()
            raw_family = strategy.get("family")
            if isinstance(raw_family, str) and raw_family.strip():
                family = raw_family.strip()
        except AttributeError:
            pass
    if not display_name:
        display_name = _short_strategy_name(strategy_id)
    if not family:
        family = strategy_id.split(".", 1)[0]
    return display_name, family


def _load_config(strategy_id: str, configs_dir: Path | None) -> dict[str, Any] | None:
    """Load a strategy YAML by strategy_id; return None on any failure.

    Strategy config filenames do not correspond predictably to strategy IDs
    (e.g. ``meanrev_daily_rsi2pullback_v1.yaml`` holds id
    ``meanrev.daily.pullback_rsi2.curated_largecap.v1``), so this always
    performs a glob-and-match scan rather than a slug-derived fast path.
    """
    if configs_dir is None:
        return None
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return None

    try:
        for yaml_path in configs_dir.glob("*.yaml"):
            try:
                with yaml_path.open(encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                if isinstance(data, dict) and data.get("strategy", {}).get("id") == strategy_id:
                    return data
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("ActiveOpsState: config glob failed: %s", exc)

    return None


def _build_active_ops_snapshot(
    db_path: Path,
    configs_dir: Path | None,
    locks_dir: Path | None,
) -> dict[str, Any]:
    """Adapter for ``PollingReadModel`` — wraps `_query_active_ops` list return
    into the dict shape the polling lifecycle expects (Opus B1 fix).

    Pre-RM-007 PR C, the worker emitted ``Signal(list)``; ``PollingReadModel``
    requires a dict with an optional ``lastRefreshedAt`` key. The closed-over
    ``configs_dir`` and ``locks_dir`` args (immutable ``Path`` objects in
    practice) are captured by the lambda in ``ActiveOpsState.__init__``.
    """
    now = datetime.now(tz=UTC)
    runners = _query_active_ops(
        db_path,
        now,
        configs_dir=configs_dir,
        locks_dir=locks_dir,
    )
    return {"runners": runners, "lastRefreshedAt": now.isoformat()}


class ActiveOpsState(PollingReadModel):
    """Active runner operations state exposed to QML as Q_PROPERTYs.

    Inherits the canonical polling lifecycle from
    :class:`milodex.gui.polling_lifecycle.PollingReadModel`. Worker payload
    was ``Signal(list)`` pre-migration — adapted in
    :func:`_build_active_ops_snapshot` to the dict shape the polling
    lifecycle expects (Opus B1 fix). ``_apply_result`` reads
    ``result["runners"]`` to restore the runner-list shape.

    Closed-over args: ``configs_dir`` and ``locks_dir`` are captured by the
    builder lambda in ``__init__``. They are immutable ``Path`` objects in
    practice; lambda capture is safe.
    """

    runnersChanged = Signal()  # noqa: N815
    liveCountChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path | None = None,
        configs_dir: Path | None = None,
        locks_dir: Path | None = None,
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        if db_path is None:
            from milodex.config import get_data_dir

            db_path = get_data_dir() / "milodex.db"
        self._db_path = db_path

        if configs_dir is None:
            from milodex.config import get_bundled_resource_dir

            configs_dir = get_bundled_resource_dir() / "configs"
        self._configs_dir = configs_dir

        if locks_dir is None:
            from milodex.config import get_locks_dir

            locks_dir = get_locks_dir()
        self._locks_dir = locks_dir

        self._runners: list[dict[str, Any]] = []
        self._live_count = 0
        super().__init__(
            builder=lambda: _build_active_ops_snapshot(db_path, configs_dir, locks_dir),
            refresh_interval_ms=refresh_interval_ms,
            parent=parent,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        runners = result["runners"]
        runners_changed = runners != self._runners
        # PID-verified liveness count (GUI audit finding #3 / M2 item b): the
        # ACTIVE OPERATIONS headline must count only rows whose sessionState
        # resolved to "running" (lock_verified_live), not every open row —
        # a phantom row stays visible in `runners` but is excluded here.
        live_count = sum(1 for r in runners if r.get("sessionState") == "running")
        live_count_changed = live_count != self._live_count
        self._runners = runners
        self._live_count = live_count
        if runners_changed:
            self.runnersChanged.emit()
        if live_count_changed:
            self.liveCountChanged.emit()

    def _get_runners(self) -> list:
        return self._runners

    def _get_live_count(self) -> int:
        return self._live_count

    runners = Property("QVariantList", _get_runners, notify=runnersChanged)
    liveCount = Property(int, _get_live_count, notify=liveCountChanged)  # noqa: N815

    # dataStatus, dataErrorMessage, lastRefreshedAt — inherited from PollingReadModel
