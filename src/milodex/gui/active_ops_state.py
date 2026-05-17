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
                 Limitation: this is the poll period, not the bar period (86400s).
                 Heartbeat reflects check-in within 90s, not bar arrival.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import (  # pragma: no cover
    Property,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)

from milodex.core.advisory_lock import AdvisoryLock
from milodex.strategies.paper_runner_control import (
    controlled_stop_request_path,
    runner_lock_name,
)

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


def _session_state(ended_at: str | None, exit_reason: str | None) -> str:
    return "running" if not ended_at else "stopped:" + (exit_reason or "unknown")


def _heartbeat(last_eval_iso: str | None, now: datetime, cadence_seconds: int) -> str:
    if last_eval_iso is None:
        return "no activity"
    last_eval = datetime.fromisoformat(last_eval_iso)
    if last_eval.tzinfo is None:
        last_eval = last_eval.replace(tzinfo=UTC)
    age = (now - last_eval).total_seconds()
    return "on schedule" if age <= cadence_seconds * 1.5 else f"overdue by {int(age // 60)}m"


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

_SQL_LAST_EVAL = """
SELECT session_id, MAX(recorded_at) AS last_eval
FROM explanations
WHERE session_id IN ({placeholders})
GROUP BY session_id
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
        session_ids = [r["session_id"] for r in runs]
        placeholders = ",".join("?" * len(session_ids))
        eval_rows = conn.execute(
            _SQL_LAST_EVAL.format(placeholders=placeholders),
            session_ids,
        ).fetchall()
    finally:
        conn.close()

    last_eval_by_session: dict[str, str | None] = {
        r["session_id"]: r["last_eval"] for r in eval_rows
    }

    result: list[dict[str, Any]] = []
    for run in runs:
        strategy_id: str = run["strategy_id"]
        session_id: str = run["session_id"]

        config = _load_config(strategy_id, configs_dir)
        label = _cadence_label(config)
        cad_secs = _cadence_seconds(config)

        last_eval: str | None = last_eval_by_session.get(session_id)

        runner_lock = "released"
        if locks_dir is not None:
            lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
            try:
                holder = lock._read_holder()  # noqa: SLF001
                if holder is not None:
                    runner_lock = "held"
            except Exception as exc:  # noqa: BLE001
                logger.warning("ActiveOpsState: lock read failed for %s: %s", strategy_id, exc)

        stop_requested = False
        if locks_dir is not None:
            sentinel_path = controlled_stop_request_path(locks_dir, strategy_id)
            try:
                stop_requested = sentinel_path.exists()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ActiveOpsState: sentinel check failed for %s: %s", strategy_id, exc
                )

        result.append(
            {
                "strategyId": strategy_id,
                "sessionState": _session_state(run["ended_at"], run["exit_reason"]),
                "cadence": label,
                "lastEval": last_eval,
                "heartbeat": _heartbeat(last_eval, now, cad_secs),
                "runnerLock": runner_lock,
                "stopRequested": stop_requested,
                "sessionAge": _session_age(run["started_at"], now),
            }
        )

    return result


def _load_config(strategy_id: str, configs_dir: Path | None) -> dict[str, Any] | None:
    """Load a strategy YAML by strategy_id; return None on any failure."""
    if configs_dir is None:
        return None
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return None

    slug = strategy_id.replace(".", "_")
    candidate = configs_dir / f"{slug}.yaml"
    if candidate.exists():
        try:
            with candidate.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ActiveOpsState: failed to load config %s: %s", candidate, exc)
            return None

    try:
        for yaml_path in configs_dir.glob("*.yaml"):
            try:
                with yaml_path.open(encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
                if (
                    isinstance(data, dict)
                    and data.get("strategy", {}).get("id") == strategy_id
                ):
                    return data
            except Exception:  # noqa: BLE001
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("ActiveOpsState: config glob failed: %s", exc)

    return None


class _ActiveOpsRefreshSignals(QObject):
    """Signal carrier for the ActiveOpsState refresh worker."""

    completed = Signal(list)
    failed = Signal(str)


class _ActiveOpsRefreshRunnable(QRunnable):
    """One-shot refresh executed on a QThreadPool worker thread."""

    def __init__(
        self,
        db_path: Path,
        configs_dir: Path | None,
        locks_dir: Path | None,
        signals: _ActiveOpsRefreshSignals,
    ) -> None:
        super().__init__()
        self._db_path = db_path
        self._configs_dir = configs_dir
        self._locks_dir = locks_dir
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover
        try:
            now = datetime.now(tz=UTC)
            runners = _query_active_ops(
                self._db_path,
                now,
                configs_dir=self._configs_dir,
                locks_dir=self._locks_dir,
            )
            self._signals.completed.emit(runners)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ActiveOpsState: DB refresh failed: %s", exc)
            self._signals.failed.emit(str(exc))


class ActiveOpsState(QObject):
    """Active runner operations state exposed to QML as Q_PROPERTYs."""

    runnersChanged = Signal()  # noqa: N815
    lastRefreshedAtChanged = Signal()  # noqa: N815
    dataStatusChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path | None = None,
        configs_dir: Path | None = None,
        locks_dir: Path | None = None,
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

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

        self._refresh_interval_ms = max(1, refresh_interval_ms)
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)

        self._runners: list[dict[str, Any]] = []
        self._last_refreshed_at: str = ""
        self._data_status: str = "loading"
        self._data_error_message: str = ""

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self._refresh_interval_ms)
        self._refresh_timer.timeout.connect(self._kick_refresh)

        self._refresh_signals = _ActiveOpsRefreshSignals(self)
        self._refresh_signals.completed.connect(
            self._on_refresh_complete, Qt.ConnectionType.QueuedConnection
        )
        self._refresh_signals.failed.connect(
            self._on_refresh_failed, Qt.ConnectionType.QueuedConnection
        )

        self._refresh_in_flight: bool = False

    def start(self) -> None:
        """Begin periodic DB polling with an immediate first refresh."""
        self._kick_refresh()
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def stop(self) -> None:
        """Halt polling and drain any in-flight DB worker."""
        self._refresh_timer.stop()
        self._thread_pool.waitForDone(2000)
        try:
            self._refresh_signals.completed.disconnect(self._on_refresh_complete)
            self._refresh_signals.failed.disconnect(self._on_refresh_failed)
        except (RuntimeError, TypeError):
            pass

    def _kick_refresh(self) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        runnable = _ActiveOpsRefreshRunnable(
            self._db_path,
            self._configs_dir,
            self._locks_dir,
            self._refresh_signals,
        )
        self._thread_pool.start(runnable)

    @Slot(list)
    def _on_refresh_complete(self, runners: list[dict[str, Any]]) -> None:
        self._refresh_in_flight = False
        now_iso = datetime.now(tz=UTC).isoformat()
        runners_changed = runners != self._runners
        self._runners = runners
        self._last_refreshed_at = now_iso
        self.lastRefreshedAtChanged.emit()
        if runners_changed:
            self.runnersChanged.emit()
        if self._data_status != "ready" or self._data_error_message:
            self._data_status = "ready"
            self._data_error_message = ""
            self.dataStatusChanged.emit()

    @Slot(str)
    def _on_refresh_failed(self, message: str) -> None:
        self._refresh_in_flight = False
        if self._data_status != "error" or self._data_error_message != message:
            self._data_status = "error"
            self._data_error_message = message
            self.dataStatusChanged.emit()

    def _get_runners(self) -> list:
        return self._runners

    def _get_last_refreshed_at(self) -> str:
        return self._last_refreshed_at

    def _get_data_status(self) -> str:
        return self._data_status

    def _get_data_error_message(self) -> str:
        return self._data_error_message

    runners = Property("QVariantList", _get_runners, notify=runnersChanged)
    lastRefreshedAt = Property(  # noqa: N815
        str, _get_last_refreshed_at, notify=lastRefreshedAtChanged
    )
    dataStatus = Property(str, _get_data_status, notify=dataStatusChanged)  # noqa: N815
    dataErrorMessage = Property(  # noqa: N815
        str, _get_data_error_message, notify=dataStatusChanged
    )
