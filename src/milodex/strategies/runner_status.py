"""Runner status truth — GUI-free owner of runner liveness and status.

Single source of truth for "what is this runner doing right now": the
4-state liveness resolver, the identity-verified lock probes, the
lock-mtime heartbeat label, and the per-strategy status snapshot consumed
by ``milodex strategy status``.

The resolver and lock probes previously lived in
``milodex.gui._event_queries`` (GUI hardening PR6). They moved here so
non-GUI surfaces (the CLI) can consume runner liveness without importing
the GUI package; ``milodex.gui._event_queries`` re-exports them for its
existing callers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from milodex.core.advisory_lock import AdvisoryLock, live_lock_holder
from milodex.strategies.loader import StrategyConfig, load_strategy_config
from milodex.strategies.paper_runner_control import (
    controlled_stop_request_path,
    runner_lock_name,
)

if TYPE_CHECKING:
    from milodex.core.advisory_lock import LockHolder
    from milodex.core.event_store import EventStore, StrategyRunEvent

logger = logging.getLogger(__name__)

_FAILURE_EXIT_REASONS = frozenset(
    {
        "crashed",
        "failed",
        "kill_switch",
        "orphan_recovered",
        "orphaned_no_live_runner",
        "error",
    }
)
"""Exit reasons that classify a *closed* runner session as ``"failed"``.

Two distinct orphan closures exist and both belong here:
``orphan_recovered`` (runner startup self-reconcile,
``EventStore.reconcile_orphan_strategy_runs``) and
``orphaned_no_live_runner`` (the GUI bootstrap/periodic reaper,
``strategies/orphan_reconciliation.py``). The ``crashed:<detail>`` prefix is
handled separately by :func:`resolve_runner_liveness`.
"""

_DEFAULT_CADENCE_SECONDS = 60.0

_STOP_UNCONSUMED_CADENCE_MULTIPLE = 3.0
"""Poll cycles a controlled-stop request may sit unconsumed before a *live*
runner is judged **wedged**.

A healthy runner consumes the request at the top of its next poll (within one
``cadence_seconds`` cycle). Three cycles is comfortable margin against a single
slow ``run_cycle()`` (e.g. a 1D runner mid-fetch) so a healthy runner never
flaps into the wedged state, while a truly stuck process is surfaced within a
few minutes. Derived from the per-strategy poll interval
(``runner._resolve_poll_interval``), not a hard-coded number, so intraday
cadences scale down proportionally.
"""

_IDLE_BY_DESIGN_NOTE = (
    "daily (1D) tempo — evaluates once after market close; "
    "idle while the market is open is by design"
)
_PHANTOM_NOTE = (
    "open session row with no live process — close it with "
    "`milodex maintenance reap-orphans` (the GUI bootstrap reaper also closes it)"
)
_STOP_WEDGED_NOTE = (
    "controlled-stop request UNCONSUMED for {age} — the runner lock is live but "
    "the process is not draining the request (wedged; a healthy runner consumes "
    "it within one poll). Controlled stop will not complete: hard-kill the PID "
    "and clear data/locks/*.lock (see docs/TROUBLESHOOTING.md)."
)
_STOP_MOOT_NOTE = (
    "controlled-stop request present but no live runner holds the lock — the "
    "process is already gone, so the stop request is moot; clear the leftover "
    "data/locks/*.lock file if it lingers."
)


def resolve_runner_liveness(
    *, ended_at: str | None, exit_reason: str | None, lock_live: bool
) -> str:
    """Resolve a single runner session to ``running | phantom | stopped | failed``.

    Pure function — the one place the system decides what a runner row *is*
    for display-trust and which-actions purposes. Lock-verified liveness
    arrives as the pre-computed ``lock_live`` flag so this stays
    side-effect-free and trivially testable.

    - ``ended_at`` is ``None``/``""`` (open session):
        ``"running"`` if ``lock_live`` else ``"phantom"`` — a hard-killed runner
        whose row never closed is a phantom, not a live runner.
    - closed session with a failure exit reason (in :data:`_FAILURE_EXIT_REASONS`
      or with a ``"crashed:"`` prefix): ``"failed"``.
    - any other closed session: ``"stopped"``.
    """
    if ended_at in (None, ""):
        return "running" if lock_live else "phantom"
    reason = exit_reason or ""
    if reason in _FAILURE_EXIT_REASONS or reason.startswith("crashed:"):
        return "failed"
    return "stopped"


def runner_lock_holder(strategy_id: str, locks_dir: Path | None) -> LockHolder | None:
    """Return the identity-verified live holder of ``strategy_id``'s runner lock.

    ``None`` when ``locks_dir`` is ``None``, no lock exists, the holder is a
    stale / recycled-PID lock file, or the lock cannot be read.
    """
    if locks_dir is None:
        return None
    try:
        lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
        return live_lock_holder(lock)
    except Exception as exc:  # noqa: BLE001
        logger.warning("runner_lock_holder: lock read failed for %s: %s", strategy_id, exc)
        return None


def runner_lock_live(strategy_id: str, locks_dir: Path | None) -> bool:
    """Return ``True`` iff a genuinely-live process holds ``strategy_id``'s runner lock.

    Identity-verified liveness via :func:`milodex.core.advisory_lock.live_lock_holder`
    — a stale / recycled-PID lock file reads as *not* live. Returns ``False`` when
    ``locks_dir`` is ``None`` (no lock surface to inspect) or on any read error.
    """
    return runner_lock_holder(strategy_id, locks_dir) is not None


def runner_lock_mtime_age(strategy_id: str, locks_dir: Path | None, now: datetime) -> float | None:
    """Seconds since the runner lock file was last refreshed (per-poll heartbeat).

    Returns ``None`` when ``locks_dir`` is ``None``, the lock file is absent,
    or the stat fails.  The lock file mtime is updated by
    :meth:`AdvisoryLock.refresh` once per runner poll cycle, so its age is
    an accurate per-poll check-in signal independent of how often the
    strategy actually writes an explanation row.
    """
    if locks_dir is None:
        return None
    try:
        lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=locks_dir)
        mtime = lock.path.stat().st_mtime
        return now.timestamp() - mtime
    except Exception as exc:  # noqa: BLE001
        logger.warning("runner_lock_mtime_age: stat failed for %s: %s", strategy_id, exc)
        return None


def controlled_stop_request_age(
    strategy_id: str, locks_dir: Path | None, now: datetime
) -> float | None:
    """Seconds since the controlled-stop request file was written, or ``None``.

    Mtime-based, mirroring :func:`runner_lock_mtime_age`. The request file is
    written once (atomic ``tmp.replace``) and never rewritten, so its mtime is
    the request timestamp — the age is how long the request has gone
    un-consumed. Returns ``None`` when ``locks_dir`` is ``None``, the request
    file is absent, or the stat fails (fail-safe: an unknown age never proves a
    wedged runner).
    """
    if locks_dir is None:
        return None
    try:
        path = controlled_stop_request_path(locks_dir, strategy_id)
        return now.timestamp() - path.stat().st_mtime
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "controlled_stop_request_age: stat failed for %s: %s", strategy_id, exc
        )
        return None


def classify_stop_request(
    *,
    stop_requested: bool,
    age_seconds: float | None,
    lock_live: bool,
    cadence_seconds: float,
) -> str | None:
    """Classify an in-flight controlled-stop request against runner liveness.

    This is the wedged-vs-phantom distinction, made explicit:

    - ``None``      — no request file present.
    - ``"pending"`` — request present, a live runner holds the lock, and the
      request is younger than the wedged threshold (a healthy runner is
      expected to consume it imminently).
    - ``"wedged"``  — request present, a live runner holds the lock, but the
      request has sat un-consumed past ``cadence_seconds *
      _STOP_UNCONSUMED_CADENCE_MULTIPLE``. The process is ALIVE but not
      draining the request: the controlled stop will not complete without
      operator intervention.
    - ``"moot"``    — request present but no live runner holds the lock. The
      process is already gone (phantom/dead), so the request can never be
      consumed but is harmless — phantom semantics win, this is not "wedged".

    ``age_seconds`` of ``None`` (file present but stat failed) is treated as
    *not* wedged — an unknown age must never fabricate the alarm.
    """
    if not stop_requested:
        return None
    if not lock_live:
        return "moot"
    threshold = cadence_seconds * _STOP_UNCONSUMED_CADENCE_MULTIPLE
    if age_seconds is not None and age_seconds >= threshold:
        return "wedged"
    return "pending"


def heartbeat_label(lock_age_seconds: float | None, cadence_seconds: float) -> str:
    """Classify runner health from advisory-lock mtime age.

    ``cadence_seconds`` is the runner poll interval (``tempo.bar_size``
    default per ``runner._POLL_INTERVAL_BY_BAR_SIZE``). The threshold is
    ``cadence_seconds * 2.0``: the runner refreshes its lock at the *top* of
    the loop, before ``run_cycle()``, so the real inter-refresh gap is
    ``sleep(cadence_seconds) + run_cycle_duration`` — a 1.5× threshold would
    flap a healthy 1D runner mid-fetch.

    Returns
    -------
    ``"no activity"``  — lock age unavailable (no check-in surface).
    ``"on schedule"``  — lock age ≤ cadence_seconds * 2.0.
    ``"overdue by Nm"`` — lock age exceeds threshold; N whole minutes stale.
    ``"overdue by Ns"`` — lock age exceeds threshold and < 60 s stale (sub-minute
        overdue unit so the label is honest for intraday cadences, e.g. 5Min
        poll=10 s → threshold 20 s, an age of 25 s shows "overdue by 25s" rather
        than the misleading "overdue by 0m").
        The ``"overdue by "`` prefix is load-bearing for
        ``DeskSurface.qml``'s ``indexOf("overdue")===0`` colour rule.
    """
    if lock_age_seconds is None:
        return "no activity"
    if lock_age_seconds <= cadence_seconds * 2.0:
        return "on schedule"
    mins = int(lock_age_seconds // 60)
    unit = f"{mins}m" if mins >= 1 else f"{int(lock_age_seconds)}s"
    return f"overdue by {unit}"


def collect_runner_statuses(
    event_store: EventStore,
    *,
    config_dir: Path,
    locks_dir: Path,
    strategy_id: str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Assemble per-strategy runner status snapshots.

    One entry per strategy with a recorded ``strategy_runs`` session (latest
    session wins), or exactly one entry when ``strategy_id`` is given. A
    ``strategy_id`` with a resolvable config but no recorded session reports
    ``state="never_ran"``; an unknown ``strategy_id`` raises ``ValueError``.

    Read-only: event-store reads plus filesystem lock/sentinel inspection.
    """
    now = now or datetime.now(tz=UTC)
    latest: dict[str, StrategyRunEvent] = {}
    for run in event_store.list_strategy_runs():
        current = latest.get(run.strategy_id)
        if current is None or (run.id or 0) > (current.id or 0):
            latest[run.strategy_id] = run

    selected: dict[str, StrategyRunEvent | None]
    if strategy_id is not None:
        if strategy_id in latest:
            selected = {strategy_id: latest[strategy_id]}
        else:
            if _config_for(strategy_id, config_dir) is None:
                msg = f"Strategy config not found for strategy_id: {strategy_id}"
                raise ValueError(msg)
            selected = {strategy_id: None}
    else:
        selected = dict(latest)

    return [
        _status_entry(event_store, sid, selected[sid], config_dir, locks_dir, now)
        for sid in sorted(selected)
    ]


def _status_entry(
    event_store: EventStore,
    strategy_id: str,
    run: StrategyRunEvent | None,
    config_dir: Path,
    locks_dir: Path,
    now: datetime,
) -> dict[str, Any]:
    holder = runner_lock_holder(strategy_id, locks_dir)
    lock_live = holder is not None

    if run is None:
        # Lock-precedes-row ordering: a just-launched runner holds its lock
        # for a brief window before its strategy_runs row exists.
        state = "running" if lock_live else "never_ran"
    else:
        state = resolve_runner_liveness(
            ended_at=run.ended_at.isoformat() if run.ended_at is not None else None,
            exit_reason=run.exit_reason,
            lock_live=lock_live,
        )

    config = _config_for(strategy_id, config_dir)
    bar_size = config.tempo.get("bar_size") if config is not None else None

    cadence = _cadence_seconds(config)
    # Gate the mtime read on identity-verified liveness: a hard-killed
    # runner's lock file has a fresh mtime from moments before death, but its
    # PID is gone — reading it would claim "on schedule" for a phantom.
    lock_age = runner_lock_mtime_age(strategy_id, locks_dir, now) if lock_live else None
    heartbeat = heartbeat_label(lock_age, cadence)

    last_eval = (
        event_store.latest_explanation_recorded_at(run.session_id) if run is not None else None
    )

    stop_requested = False
    try:
        stop_requested = controlled_stop_request_path(locks_dir, strategy_id).exists()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "collect_runner_statuses: sentinel check failed for %s: %s", strategy_id, exc
        )

    # An unconsumed controlled-stop request is operator-observable: derive its
    # age and classify against liveness so a wedged runner (lock live, request
    # not draining) is distinguishable from a moot one (process already gone).
    stop_age = controlled_stop_request_age(strategy_id, locks_dir, now) if stop_requested else None
    stop_request_state = classify_stop_request(
        stop_requested=stop_requested,
        age_seconds=stop_age,
        lock_live=lock_live,
        cadence_seconds=cadence,
    )
    stop_request_note = _stop_request_note_for(stop_request_state, stop_age)

    return {
        "strategy_id": strategy_id,
        "state": state,
        "session_id": run.session_id if run is not None else None,
        "session_started_at": run.started_at.isoformat() if run is not None else None,
        "session_ended_at": (
            run.ended_at.isoformat() if run is not None and run.ended_at is not None else None
        ),
        "exit_reason": run.exit_reason if run is not None else None,
        "holder_pid": holder.pid if holder is not None else None,
        "holder_hostname": holder.hostname if holder is not None else None,
        "holder_started_at": holder.started_at.isoformat() if holder is not None else None,
        "lock_age_seconds": lock_age,
        "heartbeat": heartbeat,
        "last_eval_at": last_eval,
        "stop_requested": stop_requested,
        "stop_requested_age_seconds": stop_age,
        "stop_request_state": stop_request_state,
        "stop_request_note": stop_request_note,
        "bar_size": bar_size,
        "note": _note_for(state, bar_size),
    }


def _note_for(state: str, bar_size: str | None) -> str | None:
    if state == "phantom":
        return _PHANTOM_NOTE
    if state == "running" and bar_size == "1D":
        return _IDLE_BY_DESIGN_NOTE
    return None


def _format_stop_age(age_seconds: float | None) -> str:
    """Human age for a stop-request note ("Nm" / "Ns"), matching heartbeat style."""
    if age_seconds is None:
        return "an unknown interval"
    mins = int(age_seconds // 60)
    return f"{mins}m" if mins >= 1 else f"{int(age_seconds)}s"


def _stop_request_note_for(state: str | None, age_seconds: float | None) -> str | None:
    if state == "wedged":
        return _STOP_WEDGED_NOTE.format(age=_format_stop_age(age_seconds))
    if state == "moot":
        return _STOP_MOOT_NOTE
    return None


def _cadence_seconds(config: StrategyConfig | None) -> float:
    """Runner poll interval for ``config`` (heartbeat threshold input)."""
    if config is None:
        return _DEFAULT_CADENCE_SECONDS
    from milodex.strategies.runner import _resolve_poll_interval

    try:
        return _resolve_poll_interval(config.tempo, None)
    except Exception:  # noqa: BLE001
        return _DEFAULT_CADENCE_SECONDS


def _config_for(strategy_id: str, config_dir: Path) -> StrategyConfig | None:
    """Locate and load the config whose ``strategy.id`` matches; ``None`` if absent."""
    try:
        for path in sorted(Path(config_dir).glob("*.yaml")):
            try:
                config = load_strategy_config(path)
            except ValueError as exc:
                logger.debug("Skipping invalid config %s: %s", path, exc)
                continue
            if config.strategy_id == strategy_id:
                return config
    except Exception as exc:  # noqa: BLE001
        logger.warning("collect_runner_statuses: config scan failed: %s", exc)
    return None
