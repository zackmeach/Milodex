"""Foreground strategy runtime for paper-trading sessions."""

from __future__ import annotations

import logging
import math
import signal
import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from milodex.analytics.snapshots import record_daily_snapshot
from milodex.broker import BrokerClient, BrokerConnectionError
from milodex.broker.models import OrderSide
from milodex.core.event_store import (
    EventStore,
    ExplanationEvent,
    OperatorAlertEvent,
    QueuedIntentEvent,
    StrategyRunEvent,
)
from milodex.data import DataProvider
from milodex.data.models import Bar, BarSet
from milodex.data.timeframes import bar_size_minutes_from_timeframe, timeframe_from_bar_size
from milodex.execution.config import load_strategy_execution_config
from milodex.execution.models import ExecutionResult, ExecutionStatus, TradeIntent
from milodex.execution.service import ExecutionService
from milodex.operations.reconciliation import (
    ET_TZ,
    local_trading_day,
    run_reconciliation,
    sync_local_only_orders,
)
from milodex.risk.attribution import strategy_open_lots, strategy_positions
from milodex.runner.drain_policy import tradable_drop_decision
from milodex.strategies.base import DecisionReasoning
from milodex.strategies.loader import StrategyLoader, compute_config_hash
from milodex.strategies.paper_runner_control import consume_controlled_stop_request

logger = logging.getLogger(__name__)

# Default poll interval per bar size. Strategies that finalize once a day at
# close have no reason to poll every 5 seconds — the 5.0 s default from Phase 1
# was fine for integration testing but wasteful in real sessions.
_POLL_INTERVAL_BY_BAR_SIZE: dict[str, float] = {
    "1D": 60.0,
    "1H": 30.0,
    "15Min": 15.0,
    "5Min": 10.0,
    "1Min": 5.0,
}

# Consecutive-connectivity-outage budget for the poll loop (R-OPS-005
# "retry per a conservative policy"). A BrokerConnectionError raised by a
# poll cycle is treated as a failed poll and retried on the next cycle (the
# poll interval is the backoff) rather than crashing the runner: a transient
# network/broker blip killed all six paper runners at once on 2026-07-15
# 23:24 UTC, and an unclean death also voids the queued-intent clean-handoff
# fence (I-4) — so a crash costs both the session AND the next open's drain.
# The budget is bounded: once an outage episode exceeds this window the next
# connectivity failure re-raises and the runner crashes exactly as before
# (fail-closed; RISK_POLICY.md lists sustained connectivity loss as a
# kill-switch trigger — wiring that trigger is the kill-switch-triggers
# workstream, not this constant). Only BrokerConnectionError is retried:
# every other exception still crashes on first raise. Module constant, not
# config: promote to configs/risk_defaults.yaml when an operator actually
# needs to tune it.
_CONNECTIVITY_RETRY_BUDGET_SECONDS: float = 30.0 * 60.0

# Bounded retry window for an EXIT whose drain finds no confirmable fresh price.
# Why 30 minutes: IEX-thin symbols (observed 2026-07-20: XLF/XLV/JNJ ~17s after
# the 09:30 bell) can lack a current-session minute bar in the open's first
# minutes — the feed's thin-open coverage, not an outage. 30 minutes comfortably
# covers that thin-open span while still bounding how long an undrained EXIT
# stays operator-invisible before the exit_intent_dropped alert fires. Module
# constant, not config (same rationale as the connectivity budget above).
_DRAIN_EXIT_NO_FRESH_PRICE_RETRY_WINDOW_SECONDS: float = 30.0 * 60.0


class StrategyRunner:
    """Manually-invoked foreground runtime for a single strategy."""

    def __init__(
        self,
        *,
        strategy_id: str,
        config_dir: Path,
        broker_client: BrokerClient,
        data_provider: DataProvider,
        execution_service: ExecutionService,
        event_store: EventStore,
        poll_interval_seconds: float | None = None,
        prompt_fn: Callable[[], str] | None = None,
        on_cycle_result: Callable[[list[ExecutionResult]], None] | None = None,
        lock_heartbeat: Callable[[], None] | None = None,
        controlled_stop_request_path: Path | None = None,
        close_lockin_min_interval_seconds: float = 30.0,
        close_lockin_max_wait_seconds: float = 300.0,
    ) -> None:
        self._strategy_id = strategy_id
        self._config_dir = config_dir
        self._broker = broker_client
        self._data_provider = data_provider
        self._execution_service = execution_service
        self._event_store = event_store
        # poll_interval_seconds priority: explicit arg > YAML tempo field > bar_size default.
        # Resolved after _loaded is set below.
        self._prompt_fn = prompt_fn or self._prompt_shutdown_choice
        self._on_cycle_result = on_cycle_result
        # Optional liveness heartbeat for the per-strategy advisory lock.
        # run() is an unbounded loop the operator may leave running all
        # day/overnight while the lock is held. The advisory lock's age
        # fallback (_STALE_LOCK_MAX_AGE_SECONDS) reclaims a lock whose
        # mtime is stale even if the PID looks live, to break a
        # recycled-PID deadlock; that is only safe if this live holder
        # refreshes the mtime each cycle. The CLI wires AdvisoryLock.refresh
        # here after acquiring the lock; tests inject a stub. None → no-op.
        self._lock_heartbeat = lock_heartbeat
        self._controlled_stop_request_path = controlled_stop_request_path
        self._close_lockin_min_interval_seconds = close_lockin_min_interval_seconds
        self._close_lockin_max_wait_seconds = close_lockin_max_wait_seconds
        self._loaded = StrategyLoader().load(self._resolve_config_path())
        self._poll_interval_seconds = _resolve_poll_interval(
            self._loaded.config.tempo, poll_interval_seconds
        )
        # Snapshot the strategy's risk envelope at startup. Every TradeIntent
        # this runner emits carries these bound values; the risk evaluator
        # routes its policy decisions through them, closing the TOCTOU class
        # where a parallel writer mid-session changes a per-strategy cap. See
        # docs/reviews/2026-05-06-manifest-drift-toctou-race.md (Action Item
        # #4 follow-up).
        self._risk_envelope = load_strategy_execution_config(self._loaded.config.path)
        self._session_id = str(uuid4())
        self._started_at = datetime.now(tz=UTC)
        self._last_processed_bar_at: datetime | None = None
        # Intraday only (HR-2): set when closed-market cycles confirm no
        # unprocessed completed bar remains, so subsequent closed-market
        # cycles skip the fetch entirely. Reset on the first open-market
        # cycle. Never set on the 1D path (daily uses the lockin watermark).
        #
        # Arming is deliberately conservative (review rounds 2-3): the feed
        # publishes a bar's aggregate with lag (seconds routinely, longer on
        # hiccups), and in the at-risk state the unpublished straggler
        # completes TWO bar-widths after the newest processed bar
        # (start-of-window timestamps). Arming requires BOTH two consecutive
        # quiet closed-market fetches (a straggler published within one poll
        # interval is caught by the next cycle) AND a wall-clock margin of
        # 2 bar-widths + max(3 bar-widths, 10 minutes) past the newest
        # processed bar — i.e. at least 10 minutes of publication-lag
        # tolerance past the straggler's own window close, at any tempo,
        # calendar-free. A straggler publishing later than that is
        # permanently skipped (accepted residual: session strategies are
        # flat by then via exit_minutes_before_close, and extended-hours
        # bars keep resetting the quiet count until ~20:00 ET on liquid
        # symbols). Cost: bounded extra post-close quiet polling.
        self._intraday_session_drained = False
        self._intraday_quiet_closed_cycles = 0
        self._pending_lockin_signature: tuple[float, float, float, float, int] | None = None
        self._pending_lockin_seen_at: datetime | None = None
        self._lockin_started_at: datetime | None = None
        self._processed_intent_keys: set[tuple[datetime, str, str]] = set()
        self._processed_intent_bar_at: datetime | None = None
        # Drain ENTRY-veto dedup (in-memory, this session only): the id of any
        # queued ENTRY whose drain submit returned BLOCKED (pre-CAS risk veto,
        # row stays 'queued' by design). Skipped on subsequent open polls this
        # session so a persistently-vetoed entry does not re-submit + re-write a
        # blocked explanation every ~60s. In-memory, so a runner restart retries
        # once (one extra veto explanation per restart — acceptable). EXITs are
        # never added: a blocked exit guards an open position and its veto can
        # clear mid-session, so it retries every poll by design.
        self._drain_vetoed_row_ids: set[int] = set()
        # EXIT no-fresh-price retry tracking (in-memory, this session only):
        # queued-row id -> the first drain attempt that found no confirmable
        # fresh price for that EXIT. The row stays 'queued' and retries every
        # ~60s open poll until _DRAIN_EXIT_NO_FRESH_PRICE_RETRY_WINDOW_SECONDS
        # elapses, then alerts + retires exactly once. In-memory by design (no
        # schema change): a runner restart mid-window restarts the window,
        # which is acceptable — the window bounds alert latency, not risk
        # (nothing ever submits without a confirmed fresh price).
        self._drain_exit_no_fresh_price_first_attempt: dict[int, datetime] = {}
        # Stale-daily-bar idle alert dedup (in-memory, this session only): the
        # session date of the stale bar for which a `stale_market_data_idle`
        # alert has already been emitted. The daily stale-decline branch fires
        # on every ~60s poll while the bar cache is stale, so the alert is
        # emitted ONCE per staleness episode (keyed by the stale bar's date) and
        # reset to None the moment a current-session bar is observed — a fresh
        # episode (even on the same date after a recovery) then re-alerts.
        self._stale_bar_alerted_for: date | None = None
        self._requested_shutdown: str | None = None
        # Monotonic timestamp of the first BrokerConnectionError of the current
        # outage episode; None when connectivity is healthy. Used by run() to
        # bound connectivity retries (_CONNECTIVITY_RETRY_BUDGET_SECONDS) and to
        # emit the degraded-connectivity operator alert once per episode.
        self._connectivity_outage_started: float | None = None
        self._startup_reconciled = False
        # NY trading day string (ISO date, ET) of the most recent reconciliation
        # run this session completed. Set by _ensure_startup_reconciliation on
        # first run; reset by _maybe_rollover_reconciliation on day rollover.
        # None until the first reconciliation completes.
        self._last_reconcile_ny_day: str | None = None
        self._closed = False
        self._dialog_open = False
        # Reconcile any prior strategy_runs row for this strategy still left
        # open by a runner that died without writing ended_at. Must precede
        # the append below — otherwise the WHERE ended_at IS NULL clause
        # would sweep up our own freshly-inserted row.
        self._event_store.reconcile_orphan_strategy_runs(
            strategy_id=self._strategy_id,
            ended_at=self._started_at,
            exit_reason="orphan_recovered",
        )
        self._event_store.append_strategy_run(
            StrategyRunEvent(
                session_id=self._session_id,
                strategy_id=self._strategy_id,
                started_at=self._started_at,
                ended_at=None,
                exit_reason=None,
                metadata={
                    "config_path": str(self._loaded.config.path),
                    "stage": self._loaded.config.stage,
                },
            )
        )

    @property
    def session_id(self) -> str:
        """Return the current strategy-run session identifier."""
        return self._session_id

    def set_on_cycle_result(self, callback: Callable[[list[ExecutionResult]], None] | None) -> None:
        """Register (or clear) a listener invoked after every ``run_cycle``.

        Lets the CLI stream per-decision output without the runner knowing
        about stdout; tests that don't install a callback remain silent.
        """
        self._on_cycle_result = callback

    def set_lock_heartbeat(self, heartbeat: Callable[[], None] | None) -> None:
        """Register (or clear) the per-cycle advisory-lock heartbeat.

        The CLI acquires the per-strategy :class:`AdvisoryLock` outside the
        runner and passes its ``refresh`` here so a long-running session
        keeps its lock-file mtime fresh and cannot be stolen mid-session by
        the lock's recycled-PID age fallback. The runner never owns or
        releases the lock — it only signals liveness.
        """
        self._lock_heartbeat = heartbeat

    def _heartbeat_lock(self) -> None:
        """Signal liveness on the held advisory lock; never raise.

        A failed heartbeat must not take down a live trading runner — the
        worst case is one stale cycle, still far inside the age threshold.
        """
        if self._lock_heartbeat is None:
            return
        try:
            self._lock_heartbeat()
        except Exception:  # noqa: BLE001 — liveness signal is best-effort
            logger.warning("Advisory-lock heartbeat failed; continuing", exc_info=True)

    def run(self) -> None:
        """Run the strategy loop until the operator stops it.

        Guarantees ``shutdown()`` is called on every exit path — normal
        completion, uncaught exception, and KeyboardInterrupt — so that the
        ``strategy_runs`` row is always closed with a non-NULL ``ended_at``
        and an appropriate ``exit_reason``.  The only exit that can leave the
        row open is SIGKILL, which has no Python hook.
        """
        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        _exit_mode = "controlled"
        try:
            while True:
                # Refresh the advisory lock's liveness mtime at the top of
                # every poll cycle. This long-running loop holds the
                # per-strategy lock for its whole lifetime; without this a
                # session left running past _STALE_LOCK_MAX_AGE_SECONDS
                # would look stale and a second invocation could steal the
                # lock → duplicate trade submission.
                self._heartbeat_lock()
                self._check_controlled_stop_request()
                if self._requested_shutdown is not None:
                    _exit_mode = self._requested_shutdown
                    break
                try:
                    self.run_cycle()
                except BrokerConnectionError as exc:
                    # Connectivity-classified only: treat as a failed poll and
                    # retry next cycle, bounded by the outage budget (which
                    # re-raises). Stop requests stay responsive below — they
                    # are local-filesystem reads, not broker calls.
                    self._register_connectivity_failure(exc)
                else:
                    self._clear_connectivity_outage()
                self._check_controlled_stop_request()
                if self._requested_shutdown is not None:
                    _exit_mode = self._requested_shutdown
                    break
                time.sleep(self._poll_interval_seconds)
        except KeyboardInterrupt:
            # Raw KeyboardInterrupt (not routed through _handle_sigint) — treat
            # as operator-requested interruption rather than a crash.
            _exit_mode = "interrupted"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unhandled exception in strategy runner loop")
            _exit_mode = f"crashed:{exc!r}"
            raise
        finally:
            signal.signal(signal.SIGINT, previous_handler)
            self.shutdown(mode=_exit_mode)

    def _register_connectivity_failure(self, exc: BrokerConnectionError) -> None:
        """Bound connectivity retries; re-raise ``exc`` once the budget is spent.

        First failure of an episode records the episode start and emits a
        single ``broker_connectivity_degraded`` operator alert (the event
        store is local SQLite — a broker outage does not affect it). Later
        failures only log. When the episode has lasted longer than
        ``_CONNECTIVITY_RETRY_BUDGET_SECONDS`` the original exception is
        re-raised, so the runner crashes with the exact pre-existing
        ``crashed:BrokerConnectionError(...)`` exit_reason. Observational
        only otherwise — no submit, no risk-state mutation.
        """
        now = time.monotonic()
        if self._connectivity_outage_started is None:
            self._connectivity_outage_started = now
            logger.warning(
                "Broker connectivity lost (%s); retrying each poll for up to %.0f s "
                "before crashing.",
                exc,
                _CONNECTIVITY_RETRY_BUDGET_SECONDS,
            )
            try:
                self._event_store.append_operator_alert(
                    OperatorAlertEvent(
                        alert_type="broker_connectivity_degraded",
                        severity="warning",
                        summary=(
                            f"Broker unreachable for {self._strategy_id}; retrying each poll "
                            f"for up to {_CONNECTIVITY_RETRY_BUDGET_SECONDS:.0f}s before crashing."
                        ),
                        strategy_id=self._strategy_id,
                        session_id=self._session_id,
                        symbol=None,
                        side=None,
                        context_json={
                            "error": str(exc),
                            "retry_budget_seconds": _CONNECTIVITY_RETRY_BUDGET_SECONDS,
                        },
                        recorded_at=self._now(),
                    )
                )
            except Exception:  # noqa: BLE001 — alert write must never break the poll loop
                # A concurrent-writer SQLite lock here would otherwise escape the
                # BrokerConnectionError handler into run()'s crash path — killing
                # the runner on exactly the fleet-wide-outage scenario this retry
                # exists for (6 runners + GUI share data/milodex.db). The episode
                # start is already recorded above, so a swallowed failure cannot
                # cause per-cycle alert spam. Mirrors _emit_stale_bar_idle_alert.
                logger.exception("Failed to record broker_connectivity_degraded alert; continuing.")
            return
        elapsed = now - self._connectivity_outage_started
        if elapsed > _CONNECTIVITY_RETRY_BUDGET_SECONDS:
            logger.error(
                "Broker connectivity outage exceeded the %.0f s retry budget "
                "(%.0f s elapsed); crashing.",
                _CONNECTIVITY_RETRY_BUDGET_SECONDS,
                elapsed,
            )
            raise exc
        logger.warning(
            "Broker still unreachable (%s); %.0f s of %.0f s retry budget elapsed.",
            exc,
            elapsed,
            _CONNECTIVITY_RETRY_BUDGET_SECONDS,
        )

    def _clear_connectivity_outage(self) -> None:
        """Reset the outage episode after a successful poll cycle."""
        if self._connectivity_outage_started is not None:
            logger.info(
                "Broker connectivity restored after %.0f s.",
                time.monotonic() - self._connectivity_outage_started,
            )
            self._connectivity_outage_started = None

    def run_cycle(self) -> list[ExecutionResult]:
        """Process one new completed bar when available.

        Daily (1D) path: only records ``_last_processed_bar_at`` when the
        market is closed: a 1D bar fetched while the market is open is still
        in-progress and shares its timestamp with the post-close finalized
        bar, so advancing the watermark mid-session would suppress the
        authoritative close evaluation via the same-timestamp
        ``already_seen`` check.

        Intraday path (HR-2): bars are truncated to those whose window has
        closed (``timestamp + bar_size <= now``) before evaluation — the
        provider returns the still-forming bar mid-window, and firing on it
        diverges from the completed-bar contract the strategies were
        promoted on. The watermark advances immediately after each
        evaluation: a completed bar is final by construction, so no lockin
        stability window is needed.

        Market-hours gate (Spec B): if the market is closed AND the lockin
        watermark has advanced (i.e. today's close bar is confirmed final),
        skip the API fetch entirely.  Weekends, holidays, and post-close idle
        polling all benefit — no Alpaca call is made until the next session
        opens.  The gate checks ``is_market_open()`` FIRST, before any network
        I/O, so closed-market cycles are cheap regardless of fetch cost.
        Intraday arms its own variant: once a closed-market cycle confirms
        the session's last completed bar is processed
        (``_intraday_session_drained``), later closed-market cycles skip the
        fetch until the next open.
        """
        self._ensure_startup_reconciliation()
        self._maybe_rollover_reconciliation()
        self._sweep_expired_queued_intents()
        market_open = self._broker.is_market_open()
        is_daily_bar = self._is_daily_bar()
        if is_daily_bar and market_open:
            # Phase-3 (queue-at-open, ADR 0057): the post-close lock-in enqueued
            # today's intent; at the next open re-evaluate it against a fresh
            # context and submit through the chokepoint. Runs AFTER the rollover
            # reconcile (above) and MUST NOT advance _last_processed_bar_at — the
            # authoritative post-close evaluation still owns the watermark (I-3).
            self._drain_queued_intents()
            return []
        if is_daily_bar and not market_open and self._last_processed_bar_at is not None:
            # Market is closed and today's close has been confirmed via the
            # lockin stability window.  Nothing new to process until open.
            return []
        if not is_daily_bar:
            if market_open:
                self._intraday_session_drained = False
                self._intraday_quiet_closed_cycles = 0
            elif self._intraday_session_drained:
                # Market is closed and prior closed-market cycles confirmed
                # the session's last completed bar is already processed.
                # Nothing new can appear until the next open — skip the fetch.
                return []

        bars_by_symbol = self._fetch_bars_by_symbol()
        if not is_daily_bar:
            bars_by_symbol = self._truncate_to_completed_bars(bars_by_symbol)
            if len(bars_by_symbol[self._evaluation_symbol()]) == 0:
                # Every fetched bar is still forming — nothing evaluable yet.
                return []
        primary_bars = bars_by_symbol[self._evaluation_symbol()]
        latest_bar = primary_bars.latest()
        already_seen = (
            self._last_processed_bar_at is not None
            and latest_bar.timestamp <= self._last_processed_bar_at
        )
        if already_seen:
            if not is_daily_bar and not market_open:
                # A quiet closed-market fetch: the newest completed bar is
                # already processed. Arm the early-out only once no straggler
                # can plausibly still be pending publication (see __init__
                # comment). The margin is anchored at the bar BEFORE a
                # pending straggler, which completes 2 bar-widths later —
                # hence 2 bar-widths plus a max(3 bar-widths, 10 min)
                # publication-lag allowance past the straggler's own close.
                self._intraday_quiet_closed_cycles += 1
                bar = self._bar_duration()
                margin = 2 * bar + max(3 * bar, timedelta(minutes=10))
                if (
                    self._intraday_quiet_closed_cycles >= 2
                    and self._now() - latest_bar.timestamp >= margin
                ):
                    self._intraday_session_drained = True
            return []

        if is_daily_bar and not market_open and not self._is_current_session_bar(latest_bar):
            # Pre-open / weekend launch: the latest available daily bar is a
            # PRIOR session's close (date < today). Locking it in would poison
            # the watermark and suppress today's real post-close evaluation.
            # Decline until a bar for the current session is available.
            #
            # Observability only (M4 PR-1): the decline itself is unchanged. An
            # operator seeing "runner alive, 0 explanations after close" gets no
            # signal about WHY without this — the branch is otherwise silent and
            # fires every ~60s poll while the cache is stale. Emit a warning +
            # durable operator alert ONCE per staleness episode.
            self._emit_stale_bar_idle_alert(latest_bar)
            return []
        # A current-session daily bar was observed -> a prior staleness episode
        # (if any) has resolved. Reset the dedup guard so a fresh episode re-alerts.
        if is_daily_bar:
            self._stale_bar_alerted_for = None

        account = self._broker.get_account()
        context = replace(
            self._loaded.context,
            positions=self._current_positions(),
            equity=account.equity,
            bars_by_symbol=bars_by_symbol,
            entry_state=self._build_entry_state(),
        )
        decision = self._loaded.strategy.evaluate(primary_bars, context)
        intents = decision.intents
        # Daily post-close: confirm the lock-in stability gate WITHOUT advancing
        # the watermark (Fix #1). The watermark is advanced exactly once below,
        # AFTER this cycle's durable work (the no-action record or the queued
        # persist) succeeds — so a persist failure leaves it unadvanced and the
        # next cycle re-evaluates the same locked bar and re-persists.
        daily_lockin_confirmed = is_daily_bar and not market_open
        if daily_lockin_confirmed and not self._lockin_confirmed(latest_bar):
            return []
        if not is_daily_bar:
            # HR-2: a completed intraday bar is final by construction (its
            # window has closed), so it is marked processed the moment it is
            # evaluated — the already_seen short-circuit above then suppresses
            # re-evaluation on every later poll of the same bar. The
            # _processed_intent_keys dedup below remains the submission
            # backstop. A new bar restarts the quiet-cycle count (a straggler
            # arriving post-close proves the session was not yet drained).
            self._last_processed_bar_at = latest_bar.timestamp
            self._intraday_quiet_closed_cycles = 0

        if not intents:
            if self._processed_intent_bar_at != latest_bar.timestamp:
                self._processed_intent_keys.clear()
                self._processed_intent_bar_at = latest_bar.timestamp
            self._record_no_action(
                latest_bar.timestamp, latest_bar.close, reasoning=decision.reasoning
            )
            if daily_lockin_confirmed:
                # Durable no-action recorded -> safe to advance the watermark
                # exactly once (Fix #1). The intraday no-intents path already
                # advanced above (line ~367) and is not gated here.
                self._last_processed_bar_at = latest_bar.timestamp
            if self._on_cycle_result is not None:
                self._on_cycle_result([])
            return []

        if self._processed_intent_bar_at != latest_bar.timestamp:
            # Bar rolled over; prior keys can never re-match because the
            # already_seen short-circuit above gates on _last_processed_bar_at.
            self._processed_intent_keys.clear()
            self._processed_intent_bar_at = latest_bar.timestamp

        if daily_lockin_confirmed:
            # Phase-1 (queue-at-open, ADR 0057): a daily strategy must NOT submit
            # at the post-close lock-in — the market is closed and the order
            # would be vetoed (market_closed) or queued blind to an unknown
            # next-open price. Persist the locked-in intent; the next at-open
            # drain re-evaluates against fresh state and submits through the
            # chokepoint.
            #
            # Fix #1: persist BEFORE advancing the watermark. A non-IntegrityError
            # failure (locked DB / unreadable config) leaves the watermark
            # unadvanced so the next cycle re-evaluates the same locked bar and
            # re-persists (idempotent: the same intent collides harmlessly on
            # UNIQUE(idempotency_key)), and emits a durable operator alert so the
            # failure is never silent. An IntegrityError is the idempotent
            # duplicate and is swallowed inside _persist_queued_intent — it does
            # NOT reach here, so the watermark advances normally.
            try:
                for intent in intents:
                    intent_key = self._intent_key(intent, latest_bar.timestamp)
                    if intent_key in self._processed_intent_keys:
                        continue
                    self._persist_queued_intent(intent, latest_bar, decision.reasoning)
                    self._processed_intent_keys.add(intent_key)
            except Exception as exc:  # noqa: BLE001 — persist failure must not silently strand
                # ``intent`` is the loop variable bound to the intent whose persist
                # raised (the for-loop above always runs at least once here — the
                # empty-intents case returned earlier — and Python leaves the loop
                # var bound), so the alert names the ACTUAL failed intent, not
                # intents[0].
                self._emit_queued_intent_persist_failure(intent, latest_bar, reason=repr(exc))
                if self._on_cycle_result is not None:
                    self._on_cycle_result([])
                return []
            # All intents persisted (or were idempotent duplicates) -> advance the
            # watermark exactly once.
            self._last_processed_bar_at = latest_bar.timestamp
            if self._on_cycle_result is not None:
                self._on_cycle_result([])
            return []

        results: list[ExecutionResult] = []
        for intent in intents:
            intent_key = self._intent_key(intent, latest_bar.timestamp)
            if intent_key in self._processed_intent_keys:
                continue
            # Mark processed BEFORE the submit. ``submit_paper`` here has no
            # idempotency_key/override, so a raise after the broker call but
            # before return may leave an order placed at the broker — the same
            # consumed-but-unsubmitted ambiguity the drain guards. Keep the key
            # marked so a re-poll of this bar never silently re-submits a
            # possibly-placed order (the intraday already-seen short-circuit
            # already gates re-evaluation of the same bar; this is the
            # submission backstop). Fail-safe toward never re-firing.
            self._processed_intent_keys.add(intent_key)
            try:
                results.append(
                    self._execution_service.submit_paper(
                        self._runner_intent(intent),
                        session_id=self._session_id,
                        reasoning=decision.reasoning,
                    )
                )
            except Exception as exc:  # noqa: BLE001 — one poison intent must not abort siblings
                # Mirror the drain's post-submit asymmetry guard: an EXIT raise
                # may strand (or have already placed, then failed to record) a
                # live position, so surface it; an ENTRY raise is log-only.
                logger.warning(
                    "submit raised for %s (%s); continuing to siblings: %s",
                    intent.normalized_symbol(),
                    intent.side.value,
                    exc,
                )
                if self._intent_class(intent) == "exit":
                    self._emit_exit_submit_failure_alert(intent, reason="submit_error")
                continue
        if self._on_cycle_result is not None:
            self._on_cycle_result(results)
        return results

    def shutdown(self, *, mode: str) -> None:
        """Shut down the current strategy session.

        Maps ``mode`` to the canonical ``exit_reason`` written to the
        ``strategy_runs`` row.  Idempotent — safe to call from multiple exit
        paths; subsequent calls after the first are no-ops.

        Recognised modes (callers may pass any string; unknown values are
        stored verbatim for forensics):
        - ``"controlled"`` / ``"controlled_stop"`` → ``"controlled_stop"``
        - ``"kill_switch"``                         → ``"kill_switch"``
        - ``"interrupted"``                         → ``"interrupted"``
        - ``"crashed:<repr>"``                      → stored verbatim (first
          100 chars) so the exception type is visible in the run record.
        """
        if self._closed:
            return

        if mode in ("controlled", "controlled_stop"):
            exit_reason = "controlled_stop"
        elif mode == "kill_switch":
            # Route through the shared halt (HR-6 / R-P2-5): best-effort
            # cancel_all_orders — a broker failure NEVER blocks activation —
            # then the durable flip. Same method the breach path and the
            # operator manual trip use (ADR 0005 Addendum / D-9).
            self._execution_service.halt_trading("Operator requested kill switch.")
            exit_reason = "kill_switch"
        elif mode == "interrupted":
            exit_reason = "interrupted"
        else:
            # Includes "crashed:<repr>" and any future extension strings.
            exit_reason = mode[:100]

        # Best-effort session-end snapshot. Forensic; a broker failure here must
        # not block the strategy_run row, which is the canonical session record.
        try:
            record_daily_snapshot(
                event_store=self._event_store,
                broker=self._broker,
                session_id=self._session_id,
                strategy_id=self._strategy_id,
                recorded_at=datetime.now(tz=UTC),
            )
        except Exception:  # noqa: BLE001 — snapshot is best-effort; see ENGINEERING_STANDARDS.md
            pass

        # Best-effort session-close order-status sync (R-OPS-004 v1.3, M1 retro
        # item (a)). On a cooperative stop (controlled_stop / interrupted),
        # record the broker's terminal status for THIS runner's own still-local-
        # open orders BEFORE the strategy_runs row closes, so an at-close fill
        # lands in the event store in-session instead of waiting for a later
        # operator reconcile (closes M1 retro deviations (1)/(2)). Scoped to
        # session_id, so a concurrent sibling runner's orders are never touched.
        #
        # NOT run on kill_switch or crash: a kill-switch halt must stay minimal
        # (mirror the cancel-best-effort posture above — no extra broker I/O on
        # the halt path), and a crashed loop must not do additional broker work
        # on its way down. Only exit_reason values reachable from a cooperative
        # stop are synced.
        #
        # No `milodex.runtime` advisory lock is taken — matching the runner's
        # existing in-loop reconcile pattern (_ensure_startup_reconciliation /
        # _maybe_rollover_reconciliation): the sync converges via the fold's
        # latest-terminal-wins dedup even against a concurrent CLI `reconcile
        # sync-orders`, so a lock would only add a shutdown-time stall for no
        # correctness gain. Wrapped best-effort: any broker/store failure logs a
        # warning and never blocks or delays the strategy_runs close.
        if exit_reason in ("controlled_stop", "interrupted"):
            try:
                sync_local_only_orders(
                    event_store=self._event_store,
                    broker=self._broker,
                    reason="session-close sync (R-OPS-004 v1.3)",
                    approved_by="strategy_runner",
                    session_id=self._session_id,
                )
            except Exception:  # noqa: BLE001 — session-close sync is best-effort
                logger.warning(
                    "Session-close order-status sync failed for session %s; "
                    "strategy_runs will still close.",
                    self._session_id,
                    exc_info=True,
                )

        self._event_store.update_strategy_run_end(
            session_id=self._session_id,
            ended_at=datetime.now(tz=UTC),
            exit_reason=exit_reason,
        )
        self._closed = True

    def _ensure_startup_reconciliation(self) -> None:
        if self._startup_reconciled:
            return
        run_reconciliation(
            event_store=self._event_store,
            broker=self._broker,
        )
        self._startup_reconciled = True
        self._last_reconcile_ny_day = local_trading_day(self._now())

    def _maybe_rollover_reconciliation(self) -> None:
        """Re-run reconciliation when the NY trading day has rolled over.

        HR-3 / R-P1-3: the runner reconciles once at startup; on day 2 of any
        multi-day session ``latest_readiness`` sees a stale reconciliation row
        and blocks every BUY with ``reconciliation_stale``. This method checks
        cheaply (one in-memory string compare, no broker call, no DB query) at
        the top of each cycle. When the NY day differs from the day of the last
        reconcile, it re-runs reconciliation — same ``run_reconciliation`` call,
        same failure posture as startup (no try/except: a raised exception
        propagates to run_cycle and then to the caller, matching the behaviour
        that a startup-reconcile failure would produce).

        Placement: called AFTER ``_ensure_startup_reconciliation`` (so
        ``_last_reconcile_ny_day`` is always set first) and BEFORE every
        early-out in run_cycle. An intraday runner idling overnight behind the
        ``_intraday_session_drained`` flag will still re-reconcile on the first
        cycle of the new NY day — the re-reconcile fires here and then the
        drained-flag early-out returns [], so the reconciliation row is fresh
        before the next open-market evaluation cycle.
        """
        if self._last_reconcile_ny_day is None:
            # Startup reconcile has not completed yet — nothing to roll over.
            return
        current_ny_day = local_trading_day(self._now())
        if current_ny_day == self._last_reconcile_ny_day:
            return
        logger.info(
            "NY trading day rolled over (%s → %s): re-running reconciliation.",
            self._last_reconcile_ny_day,
            current_ny_day,
        )
        run_reconciliation(
            event_store=self._event_store,
            broker=self._broker,
        )
        self._last_reconcile_ny_day = current_ny_day

    def _sweep_expired_queued_intents(self) -> None:
        """Flip expired ``queued`` rows to ``expired`` at the manual run-loop /
        reconcile cadence (I-9: NOT a daemon — no background thread/timer).

        Bookkeeping only: ``get_active_queued_intents`` already excludes expired
        rows from the drain, so this never gates a trade. It writes ONE durable
        audit explanation only when rows were actually swept (a quiet fleet emits
        no audit noise), and deliberately does NOT read or write
        ``_last_processed_bar_at`` / the lock-in watermark — it is entirely
        independent of bar processing.

        The sweep is GLOBAL (it flips every expired ``queued`` row regardless of
        owning strategy); the audit row records only the count and is attributed
        to the running session that performed the sweep — it does NOT claim the
        swept rows belong to this strategy.

        Exit-strand guarantee (B1): a swept EXIT leaves ``queued`` -> ``expired``
        and so is invisible to the still-``queued``-only stranded-exit alerter.
        The sweep therefore emits an ``exit_intent_dropped`` alert for every
        swept EXIT directly. The alert carries the OWNING ``strategy_id`` (so a
        cross-strategy swept exit is correctly attributed) and this session as
        the sweeping observer. Because the sweep CAS flips each row exactly once
        (``status='queued'`` predicate), exactly one runner sweeps a given
        expired exit -> exactly one alert (no cross-runner duplicate).
        """
        swept = self._event_store.expire_stale_queued_intents(now=self._now())
        if not swept:
            return
        for row in swept:
            if row.intent_class == "exit":
                self._emit_exit_drop_alert(row, reason="expired_undrained")
        account = self._broker.get_account()
        self._event_store.append_explanation(
            ExplanationEvent(
                recorded_at=self._now(),
                decision_type="no_action",
                status="no_action",
                strategy_name=self._strategy_id,
                strategy_stage=self._loaded.config.stage,
                strategy_config_path=str(self._loaded.config.path),
                config_hash=None,
                symbol=self._evaluation_symbol(),
                side="hold",
                quantity=0.0,
                order_type="none",
                time_in_force="day",
                submitted_by="strategy_runner",
                market_open=self._broker.is_market_open(),
                latest_bar_timestamp=None,
                latest_bar_close=None,
                account_equity=account.equity,
                account_cash=account.cash,
                account_portfolio_value=account.portfolio_value,
                account_daily_pnl=account.daily_pnl,
                risk_allowed=True,
                risk_summary=f"Expired {len(swept)} stale queued intent(s) at reconcile.",
                reason_codes=["queued_intent_expiry_sweep"],
                risk_checks=[],
                context={"swept": len(swept)},
                session_id=self._session_id,
            )
        )

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

    def _is_current_session_bar(self, latest_bar) -> bool:
        """True iff the latest daily bar is for the current session (or newer),
        not a prior session's stale close.

        The "now" side MUST be compared in ET, not UTC. A daily bar's UTC date
        equals its ET session date (Alpaca stamps 1D bars at ~04:00-05:00 UTC on
        the session date, so the UTC calendar date is the session date in either
        DST offset). But ``datetime.now(tz=UTC).date()`` rolls to the NEXT
        calendar day at ~20:00 ET, so a UTC comparison declines the *current*
        session's own close bar as "prior session" during the 20:00-24:00 ET
        post-close window — suppressing the day's evaluation until the next
        session (observed 2026-07). Normalize now to the ET session date so a
        post-close launch works at any evening hour.

        ``>=`` (not ``==``) is required: real bars are never future-dated in
        production, but the test harness builds today-dated bars against a fixed
        fake clock, so ``==`` would reject them.  A pre-open / weekend launch
        sees a prior-session bar (session date < today's ET session date) and is
        correctly declined.
        """
        now_session_date = self._now().astimezone(ET_TZ).date()
        return latest_bar.timestamp.date() >= now_session_date

    def _lockin_confirmed(self, latest_bar) -> bool:
        """Return whether the post-close lock-in stability gate is satisfied.

        Two consecutive identical OHLCV fetches separated by at least
        ``_close_lockin_min_interval_seconds`` confirm the bar has settled.
        After ``_close_lockin_max_wait_seconds`` without confirmation the gate
        opens anyway (CI-1 fail-mode (a)) — the per-cycle explanations preserve
        forensic visibility into the unstable window.

        IMPORTANT (Fix #1): this confirms ONLY; it MUST NOT write
        ``self._last_processed_bar_at``. The caller advances the watermark
        exactly once, AFTER the cycle's durable work (no-action record or queued
        persist) succeeds — so a persist failure leaves the watermark unadvanced
        and the next cycle re-evaluates and re-persists (idempotent). On
        confirmation the lock-in state machine is reset here (it has done its
        job); a later cycle re-arms it from scratch.
        """
        now = self._now()
        signature = (
            latest_bar.open,
            latest_bar.high,
            latest_bar.low,
            latest_bar.close,
            latest_bar.volume,
        )

        if self._pending_lockin_signature is None:
            self._pending_lockin_signature = signature
            self._pending_lockin_seen_at = now
            self._lockin_started_at = now
            return False

        if (now - self._lockin_started_at).total_seconds() > self._close_lockin_max_wait_seconds:
            self._reset_lockin()
            return True

        if signature != self._pending_lockin_signature:
            self._pending_lockin_signature = signature
            self._pending_lockin_seen_at = now
            return False

        if (
            now - self._pending_lockin_seen_at
        ).total_seconds() >= self._close_lockin_min_interval_seconds:
            self._reset_lockin()
            return True
        return False

    def _reset_lockin(self) -> None:
        self._pending_lockin_signature = None
        self._pending_lockin_seen_at = None
        self._lockin_started_at = None

    def _check_controlled_stop_request(self) -> None:
        if self._requested_shutdown is not None:
            return
        request = consume_controlled_stop_request(
            self._controlled_stop_request_path,
            strategy_id=self._strategy_id,
        )
        if request is not None:
            self._requested_shutdown = "controlled"

    def _is_daily_bar(self) -> bool:
        return self._loaded.config.tempo.get("bar_size") == "1D"

    def _intent_key(
        self, intent: TradeIntent, latest_bar_timestamp: datetime
    ) -> tuple[datetime, str, str]:
        return (
            latest_bar_timestamp,
            intent.normalized_symbol(),
            intent.side.value,
        )

    # ------------------------------------------------------------------
    # queue-at-open (Phase-1 persist, ADR 0057)
    # ------------------------------------------------------------------

    def _trading_session_label(self, bar_timestamp: datetime) -> str:
        return bar_timestamp.date().isoformat()

    def _idempotency_key(self, intent: TradeIntent, trading_session: str) -> str:
        # side.value is lowercase ("buy"/"sell"); symbol is uppercased.
        return (
            f"{self._strategy_id}|{trading_session}|"
            f"{intent.side.value}|{intent.normalized_symbol()}"
        )

    def _intent_class(self, intent: TradeIntent) -> str:
        return "entry" if intent.side == OrderSide.BUY else "exit"

    def _intent_notional_pct(self, intent: TradeIntent) -> float | None:
        return getattr(intent, "notional_pct", None)

    def _risk_config_hash(self) -> str:
        # Persist the hash over the SAME path the drain re-verifies against
        # (compute_config_hash_or_none in get_active_queued_intents); a mismatch
        # would silently drop the intent at the open.
        return compute_config_hash(self._loaded.config.path)

    def _serialize_locked_in_bar(self, bar) -> dict[str, Any]:
        return {
            "timestamp": bar.timestamp.isoformat(),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "vwap": bar.vwap,
        }

    def _persist_queued_intent(
        self, intent: TradeIntent, latest_bar, reasoning: DecisionReasoning | None
    ) -> None:
        """Persist a locked-in daily intent for drain at the next session open.

        Phase-1 of queue-at-open (ADR 0057): instead of submitting at the
        post-close lock-in (the market is closed and the order would be
        vetoed ``market_closed``), the intent is written to ``queued_intents``
        as an inert, expiring row. The next at-open drain re-evaluates it
        against fresh state and submits through the chokepoint.
        """
        runner_intent = self._runner_intent(intent)
        trading_session = self._trading_session_label(latest_bar.timestamp)
        idempotency_key = self._idempotency_key(runner_intent, trading_session)
        now = self._now()
        event = QueuedIntentEvent(
            idempotency_key=idempotency_key,
            strategy_id=self._strategy_id,
            strategy_config_path=str(self._loaded.config.path),
            config_hash=self._risk_config_hash(),
            session_id=self._session_id,
            trading_session=trading_session,
            locked_in_bar_timestamp=latest_bar.timestamp.isoformat(),
            symbol=runner_intent.normalized_symbol(),
            side=runner_intent.side.value,
            intent_class=self._intent_class(runner_intent),
            notional_pct=self._intent_notional_pct(runner_intent),
            expected_stage=runner_intent.expected_stage,
            expected_max_positions=runner_intent.expected_max_positions,
            expected_max_position_pct=runner_intent.expected_max_position_pct,
            expected_daily_loss_cap_pct=runner_intent.expected_daily_loss_cap_pct,
            intent_payload_json={
                "symbol": runner_intent.symbol,
                "side": runner_intent.side.value,
                "quantity": runner_intent.quantity,
                "order_type": runner_intent.order_type.value,
                "time_in_force": runner_intent.time_in_force.value,
                "locked_in_bar": self._serialize_locked_in_bar(latest_bar),
            },
            reasoning_json=(reasoning.asdict() if reasoning is not None else None),
            created_at=now,
            # 7-day TTL, NOT 1 day: a +1d window silently kills every Friday->
            # Monday daily intent (the next open is ~65-85h out across a
            # weekend/holiday). The session-aware staleness gate is the real
            # per-open guard, so the TTL only needs to outlast the longest
            # weekend/holiday gap to the next open.
            expires_at=now + timedelta(days=7),
            status="queued",
        )
        try:
            self._event_store.append_queued_intent(event)
        except sqlite3.IntegrityError:
            # UNIQUE(idempotency_key) collision = this logical intent is already
            # queued for this session; persist is idempotent, so swallow.
            logger.info(
                "Queued intent already persisted (idempotent): %s",
                idempotency_key,
            )
        # Supersede any OLDER queued row for the same logical intent
        # (strategy/symbol/side/class) left over from a prior session — the
        # session-scoped idempotency key mints a fresh row each lock-in, so a
        # vetoed/retryable prior row would otherwise co-drain and spam the
        # duplicate-order veto until TTL. Runs in BOTH the append and the
        # IntegrityError branch (a same-session re-persist still retires stale
        # cross-session rows). Fail-soft: a supersede failure must NEVER abort
        # the lock-in persist path.
        try:
            superseded = self._event_store.supersede_queued_intents(
                self._strategy_id,
                event.symbol,
                event.side,
                event.intent_class,
                keep_idempotency_key=idempotency_key,
            )
            if superseded > 0:
                logger.info(
                    "superseded %d stale queued intent(s) for %s %s",
                    superseded,
                    event.symbol,
                    event.side,
                )
        except Exception as exc:  # noqa: BLE001 — supersede must not abort persist
            logger.warning(
                "Failed to supersede stale queued intents for %s %s: %s",
                event.symbol,
                event.side,
                exc,
            )

    # ------------------------------------------------------------------
    # queue-at-open (Phase-3 drain, ADR 0057)
    # ------------------------------------------------------------------

    def _mark_drain_entry_dropped(
        self, queued: QueuedIntentEvent, decision_bar: Bar, *, reason: str
    ) -> None:
        """Terminally drop a DECIDED ENTRY at the drain + record a per-row audit row.

        Fix #3: a DECIDED entry drop — not-tradable/halt, signal no-match/side-flip,
        a re-derived/resized 0-share quantity, or a post-submit staleness veto
        (``stale_locked_veto``: the frozen locked bar can never freshen, so the
        risk veto is permanent) — is a determination made on the FROZEN locked
        bars. Leaving the row ``queued``
        re-drains it every open until TTL for no reason. Mark it ``'dropped'``
        (terminal: excluded from ``get_active_queued_intents`` and untouched by the
        expiry sweep) and write a ``no_trade`` explanation keyed to the drain
        session so the drop is auditable. ENTRY-only: EXITs alert + retire via
        ``_emit_exit_drop_alert`` / ``mark_queued_intent_obsolete`` and are not
        routed here. Must NOT be called for couldn't-evaluate drops (no fresh
        price / no sizing price) — those stay retryable.
        """
        self._execution_service.record_no_action(
            strategy_name=self._strategy_id,
            strategy_stage=self._loaded.config.stage,
            strategy_config_path=self._loaded.config.path,
            config_hash=self._loaded.context.config_hash,
            symbol=queued.symbol,
            latest_bar_timestamp=decision_bar.timestamp,
            latest_bar_close=decision_bar.close,
            session_id=self._session_id,
            message=f"Queued ENTRY for {queued.symbol} dropped at drain: {reason}.",
        )
        self._event_store.mark_queued_intent_dropped(queued.id)

    def _emit_exit_drop_alert(self, queued: QueuedIntentEvent, *, reason: str) -> None:
        """Durably record + warn when an EXIT intent is dropped for ambiguity.

        Asymmetry guard: an undrained entry is benign (no fire); an undrained
        exit can strand a live position, so it MUST be operator-visible. This is
        observational only — it does NOT submit, retry, or mutate risk state.
        """
        logger.warning(
            "EXIT intent dropped for %s (%s): %s. Position may remain open; "
            "operator review required.",
            queued.symbol,
            queued.side,
            reason,
        )
        self._event_store.append_operator_alert(
            OperatorAlertEvent(
                alert_type="exit_intent_dropped",
                severity="warning",
                summary=f"EXIT intent for {queued.symbol} dropped: {reason}.",
                strategy_id=queued.strategy_id,
                session_id=self._session_id,
                symbol=queued.symbol,
                side=queued.side,
                context_json={"reason": reason, "idempotency_key": queued.idempotency_key},
                recorded_at=self._now(),
            )
        )

    def _emit_exit_submit_failure_alert(self, intent: TradeIntent, *, reason: str) -> None:
        """Durably record + warn when a LIVE-loop EXIT submit raises.

        TradeIntent-keyed sibling of ``_emit_exit_drop_alert`` (which is
        QueuedIntentEvent-keyed for the drain). The live intraday submit loop
        holds a ``TradeIntent``, not a queued row, but the asymmetry guard is the
        same: an EXIT submit that raised may have stranded a live position (or
        placed an order the runner failed to record), so it MUST be operator-
        visible; an undrained ENTRY is benign and stays silent. Observational
        only — it does NOT submit, retry, or mutate risk state.
        """
        symbol = intent.normalized_symbol()
        side = intent.side.value
        logger.warning(
            "EXIT intent submit failed for %s (%s): %s. Position may remain open; "
            "operator review required.",
            symbol,
            side,
            reason,
        )
        self._event_store.append_operator_alert(
            OperatorAlertEvent(
                alert_type="exit_intent_dropped",
                severity="warning",
                summary=f"EXIT intent for {symbol} submit failed: {reason}.",
                strategy_id=self._strategy_id,
                session_id=self._session_id,
                symbol=symbol,
                side=side,
                context_json={"reason": reason},
                recorded_at=self._now(),
            )
        )

    def _emit_queued_intent_persist_failure(
        self, intent: TradeIntent, latest_bar, *, reason: str
    ) -> None:
        """Durably record + warn when persisting a locked-in queued intent fails.

        Fix #1: a non-``IntegrityError`` persist failure (locked DB / unreadable
        config) at the post-close lock-in must NOT silently strand the day's
        intent. The watermark is deliberately left unadvanced so the next cycle
        re-evaluates and re-persists; this alert guarantees the failure is
        operator-visible in the moment (log) and after the fact (ledger).
        """
        runner_intent = self._runner_intent(intent)
        symbol = runner_intent.normalized_symbol()
        side = runner_intent.side.value
        logger.warning(
            "Queued-intent persist failed for %s (%s): %s. Watermark NOT advanced; "
            "the next cycle will re-evaluate and re-persist.",
            symbol,
            side,
            reason,
        )
        self._event_store.append_operator_alert(
            OperatorAlertEvent(
                alert_type="queued_intent_persist_failed",
                severity="warning",
                summary=f"Queued intent for {symbol} failed to persist: {reason}.",
                strategy_id=self._strategy_id,
                session_id=self._session_id,
                symbol=symbol,
                side=side,
                context_json={
                    "reason": reason,
                    "locked_in_bar_timestamp": latest_bar.timestamp.isoformat(),
                },
                recorded_at=self._now(),
            )
        )

    def _emit_stale_bar_idle_alert(self, latest_bar) -> None:
        """Warn + durably record that a daily strategy is idling on a stale bar.

        Fires from the daily stale-decline branch: the latest available daily
        bar is a PRIOR session's close, so the runner correctly declines to
        evaluate (locking a stale bar would poison the watermark). Without this,
        the operator sees only "runner alive, 0 explanations after close" and
        must reverse-engineer the stale-cache cause (a documented diagnosis
        burden). Deduplicated to ONCE per staleness episode (keyed by the stale
        bar's session date) so the ~60s poll loop does not write a row per cycle.

        Observability only: it does NOT change the decline, submit, retry, or
        mutate any state beyond the in-memory dedup guard. The alert write is
        fail-soft — any failure is swallowed so a broken ledger can never break
        the poll loop.
        """
        stale_date = latest_bar.timestamp.date()
        if self._stale_bar_alerted_for == stale_date:
            return
        self._stale_bar_alerted_for = stale_date
        expected_date = self._now().astimezone(ET_TZ).date()
        try:
            symbol = self._evaluation_symbol()
        except ValueError:
            symbol = None
        logger.warning(
            "Daily strategy %s idling on STALE market data: latest bar is for "
            "session %s but the current session is %s. No evaluation until the "
            "cache is refreshed. Heal with `milodex data fetch-universe "
            "--universe-ref <ref> --start <before-gap> --end <today> --force` "
            "(see docs/TROUBLESHOOTING.md, Data: stale).",
            self._strategy_id,
            stale_date.isoformat(),
            expected_date.isoformat(),
        )
        try:
            self._event_store.append_operator_alert(
                OperatorAlertEvent(
                    alert_type="stale_market_data_idle",
                    severity="warning",
                    summary=(
                        f"Daily strategy {self._strategy_id} idling on stale market "
                        f"data: latest bar session {stale_date.isoformat()} < current "
                        f"session {expected_date.isoformat()}."
                    ),
                    strategy_id=self._strategy_id,
                    session_id=self._session_id,
                    symbol=symbol,
                    context_json={
                        "stale_bar_session_date": stale_date.isoformat(),
                        "expected_session_date": expected_date.isoformat(),
                        "stale_bar_timestamp": latest_bar.timestamp.isoformat(),
                    },
                    recorded_at=self._now(),
                )
            )
        except Exception:  # noqa: BLE001 — alert write must never break the poll loop
            logger.exception(
                "Failed to persist stale_market_data_idle alert for %s (non-fatal).",
                self._strategy_id,
            )

    def _alert_stranded_exit_intents(self, drainable: list[QueuedIntentEvent]) -> None:
        """Alert + retire any still-queued EXIT for THIS strategy that the drain
        authority excluded (clean-handoff fence I-4 failed, or config drift).
        A stranded exit can leave a position open, so it must surface. Marking
        the row obsolete after alerting prevents a re-alert every open cycle
        (the drain runs each ~60s open poll); the strategy re-emits a fresh exit
        next post-close if it still holds the position."""
        drainable_keys = {q.idempotency_key for q in drainable}
        for q in self._event_store.list_queued_intents_by_status("queued"):
            if (
                q.strategy_id == self._strategy_id
                and q.intent_class == "exit"
                and q.idempotency_key not in drainable_keys
            ):
                self._emit_exit_drop_alert(q, reason="no_clean_handoff")
                self._event_store.mark_queued_intent_obsolete(q.id)

    def _drain_queued_intents(self) -> None:
        """Re-evaluate this strategy's active queued intents at the open and submit.

        Phase-3 of queue-at-open (ADR 0057): at the next session open, drain the
        intents the post-close lock-in enqueued. ``get_active_queued_intents`` is
        the SOLE drain authority — it enforces ``status='queued'``, the expiry
        fence, the clean-handoff fence, and the config-hash guard in SQL + on-disk
        recompute. This loop adds NO redundant fence of its own. Each surviving
        intent is re-evaluated against a fresh context (current equity + positions)
        on history through the locked-in completed session bar, and submitted
        through the chokepoint with the persisted ``idempotency_key`` (the CAS
        inside ``_submit_locked`` claims the row) and the reconstructed locked-in
        bar as ``latest_bar_override`` (feeds ONLY the session-aware 1D staleness
        gate). Sizing + cap pricing use a FRESH open price fetched once here
        (``_fresh_pricing_bar``, ADR 0057 §2): an ENTRY's quantity is rescaled to
        the fresh price and the fresh price is threaded down as
        ``pricing_unit_price`` so the exposure cap prices on the real open, not the
        stale locked close.

        Drop taxonomy (Fix #3): a DECIDED ENTRY drop — not-tradable/halt, signal
        no-match/side-flip, or a re-derived/resized 0-share quantity — is a
        determination on the FROZEN locked bars, so the row is marked terminal
        ``'dropped'`` (+ an audit no-action explanation) and never re-drained. A
        COULDN'T-EVALUATE drop (no fresh price, no sizing price) stays ``'queued'``
        and is retried at the next open — critical for a pre-open launch, which has
        no fresh price yet. EXITs alert + retire (``obsolete``) — except the
        no-fresh-price case, which retries every open poll within a bounded
        in-memory window (IEX thin-open coverage) before alerting + retiring once.
        The drain NEVER touches ``_last_processed_bar_at`` (I-3) and NEVER calls
        ``consume_queued_intent_and_append_attempt`` (the submit CAS owns that); the status
        writes here are ``obsolete`` (stranded/flat exit) and ``dropped`` (decided
        entry).

        ENTRY-veto dedup (same entry/exit asymmetry): a queued ENTRY whose submit
        returns ``BLOCKED`` (pre-CAS risk veto — the row stays ``queued`` by design)
        is recorded in ``self._drain_vetoed_row_ids`` and skipped on subsequent open
        polls this session, so it does not re-submit + re-write a blocked explanation
        every ~60s. The set is in-memory only, so a runner restart retries the veto
        once (one extra veto explanation per restart — acceptable). EXITs are
        DELIBERATELY exempt: a blocked exit guards an open position and its veto
        (e.g. an opposite-side resting order) can clear mid-session, so it keeps
        retrying every poll. The vetoed row is retired for good by supersession at
        the next lock-in of the same logical intent (``supersede_queued_intents``),
        or by TTL expiry — no terminal-veto status is introduced.

        EXCEPTION — staleness veto (2026-07-23): a veto whose reason codes include
        ``stale_market_data`` evaluates the FROZEN locked-in decision bar, so it can
        never clear. Both classes retire durably after the FIRST such veto: EXIT
        alerts (``stale_locked_veto``) + ``obsolete``; ENTRY takes the terminal
        decided-drop path (``dropped`` + audit row, no alert).
        """
        intents = self._event_store.get_active_queued_intents(
            self._strategy_id,
            now=self._now(),
            running_session_id=self._session_id,
        )
        self._alert_stranded_exit_intents(intents)
        if not intents:
            return
        account = self._broker.get_account()
        for queued in intents:
            # Skip an ENTRY already vetoed (BLOCKED) on an earlier drain pass this
            # session — the row is still 'queued' by design (it retries next session
            # or is superseded/expired), but re-submitting every ~60s open poll would
            # re-write a blocked explanation each time. EXIT rows are never added to
            # this set, so a blocked exit still retries every poll (its veto can clear
            # mid-session).
            if queued.id in self._drain_vetoed_row_ids:
                continue
            # Per-intent fail-closed isolation (mirrors get_active_queued_intents'
            # per-row guard): a raise in the PRE-SUBMIT body — e.g. strategy.evaluate()
            # on a degenerate truncated BarSet — leaves the row queued by construction
            # (nothing was consumed). Swallow, log, and leave the row queued (retried
            # next open / expired by the sweep). NEVER change row status on a swallowed
            # pre-submit raise. The submit call is DELIBERATELY outside this try (below)
            # because the consume CAS may flip the row to 'consumed' before the broker
            # call — a raise there is NOT safely re-queuable.
            try:
                decision_bar = self._reconstruct_locked_in_bar(queued)
                td = tradable_drop_decision(self._broker, queued.symbol)
                if td.drop:
                    if queued.intent_class == "exit":
                        # Asymmetry guard: an undrained exit can strand a live
                        # position. Alert + retire (so a persistently-halted exit
                        # does not re-alert every cycle); the strategy re-emits a
                        # fresh exit next post-close if it still holds the lot.
                        self._emit_exit_drop_alert(queued, reason=td.reason or "not_tradable")
                        self._event_store.mark_queued_intent_obsolete(queued.id)
                    else:
                        # DECIDED ENTRY drop on the frozen locked bars (Fix #3):
                        # a halted/not-tradable symbol is a broker determination,
                        # not a transient can't-evaluate. Terminate the row so it
                        # is not re-drained every open until TTL.
                        self._mark_drain_entry_dropped(
                            queued, decision_bar, reason=td.reason or "not_tradable"
                        )
                    logger.warning(
                        "drain: %s not tradable (%s); dropping", queued.symbol, td.reason
                    )
                    continue
                eval_bars = self._bars_through(self._fetch_bars_by_symbol(), decision_bar.timestamp)
                primary_bars = eval_bars[self._evaluation_symbol()]
                context = replace(
                    self._loaded.context,
                    positions=self._current_positions(),
                    equity=account.equity,
                    bars_by_symbol=eval_bars,
                    entry_state=self._build_entry_state(),
                )
                decision = self._loaded.strategy.evaluate(primary_bars, context)
                match = self._match_drain_intent(decision.intents, queued)
                # ``not (q > 0)`` is NaN-safe (NaN > 0 is False, so NaN is treated as
                # non-positive and dropped); ``q <= 0`` is NOT (NaN <= 0 is False, so a
                # NaN quantity would slip through to submit).
                if match is None or not (match.quantity > 0):
                    if queued.intent_class == "exit":
                        if not self._current_positions().get(queued.symbol):
                            # A 0-share exit on an already-flat strategy ledger is moot —
                            # the position the exit would close no longer exists. Retire
                            # the row rather than leave it to expire undrained.
                            self._event_store.mark_queued_intent_obsolete(queued.id)
                        else:
                            # Position STILL HELD but the at-open re-eval derived no exit:
                            # a path-dependent exit may never re-emit -> surface it. Retire
                            # the row to avoid a re-alert every ~60s open poll; the strategy
                            # re-emits a fresh exit next post-close if it still decides to
                            # exit.
                            self._emit_exit_drop_alert(
                                queued, reason="reeval_no_exit_position_open"
                            )
                            self._event_store.mark_queued_intent_obsolete(queued.id)
                    else:
                        # DECIDED ENTRY drop on the frozen locked bars (Fix #3): the
                        # at-open re-eval produced no matching ENTRY (signal no-match
                        # or a side-flip — _match_drain_intent requires symbol+side
                        # equality), or a re-derived 0-share quantity. The strategy
                        # decided against this entry, so terminate the row rather than
                        # re-drain it every open until TTL. NEVER submit a 0-share order.
                        self._mark_drain_entry_dropped(
                            queued,
                            decision_bar,
                            reason="reeval_no_match" if match is None else "reeval_zero_share",
                        )
                    continue

                # The price the strategy actually sized ``match.quantity`` on is
                # the TRADED symbol's OWN locked close — NOT ``decision_bar.close``
                # (which is always universe[0]'s close, because the lock-in persists
                # the evaluation symbol's bar for EVERY intent regardless of the
                # symbol it trades). For a cross-sectional 1D strategy whose traded
                # symbol != universe[0], using universe[0]'s close as the rescale
                # numerator mixes two symbols' prices and grossly mis-sizes the
                # order. ``eval_bars[queued.symbol]`` is the traded symbol's bars
                # truncated to the locked session; its latest close is exactly what
                # the cross-sectional sizers used (``unit_price = latest_close``).
                # Read it BEFORE the fresh-price lookup so the entry/exit asymmetry
                # guard (silent ENTRY re-queue vs EXIT alert+retire) is uniform with
                # the existing fail-closed branches. Fail CLOSED if the traded
                # symbol is missing / its truncated BarSet is empty / its close is
                # non-finite or non-positive.
                sized_close = self._traded_symbol_locked_close(eval_bars, queued.symbol)
                if sized_close is None:
                    if queued.intent_class == "exit":
                        self._emit_exit_drop_alert(queued, reason="no_sizing_price")
                        self._event_store.mark_queued_intent_obsolete(queued.id)
                    logger.warning(
                        "drain: no sizing price for %s; dropping (fail-closed)",
                        queued.symbol,
                    )
                    continue

                # Fresh-price sizing + cap pricing (ADR 0057 §2). The locked-in
                # close sized the re-eval and gates staleness, but it must NOT
                # size entries or price the exposure cap across an overnight gap.
                # Fetch a confirmably-current fresh price; fail CLOSED if absent.
                fresh = self._fresh_pricing_bar(queued.symbol, decision_bar)
                if fresh is None:
                    if queued.intent_class == "exit":
                        # IEX-thin symbols (observed 2026-07-20: XLF/XLV/JNJ
                        # ~17s post-bell) have no current-session minute bar in
                        # the open's first minutes; retiring an EXIT on the
                        # first miss stranded live positions with zero risk
                        # evaluations. Leave the row 'queued' (untouched) so
                        # the next ~60s open poll retries it, bounded by the
                        # retry window below. Fail-closed is preserved: nothing
                        # submits without a confirmed fresh price — the window
                        # only extends the attempt horizon.
                        now = self._now()
                        first = self._drain_exit_no_fresh_price_first_attempt.setdefault(
                            queued.id, now
                        )
                        elapsed = (now - first).total_seconds()
                        if elapsed < _DRAIN_EXIT_NO_FRESH_PRICE_RETRY_WINDOW_SECONDS:
                            logger.warning(
                                "drain: no fresh price for EXIT %s; leaving queued to "
                                "retry (%.0fs into %.0fs window)",
                                queued.symbol,
                                elapsed,
                                _DRAIN_EXIT_NO_FRESH_PRICE_RETRY_WINDOW_SECONDS,
                            )
                            continue
                        # Window closed with still no fresh price. Asymmetry
                        # guard: an undrained exit can strand a live position.
                        # Alert + retire, exactly as pre-window (ONE alert per
                        # intent, on the final drop only — de-spam the ~60s
                        # open poll); the strategy re-emits a fresh exit next
                        # post-close.
                        self._emit_exit_drop_alert(queued, reason="no_fresh_price")
                        self._event_store.mark_queued_intent_obsolete(queued.id)
                    logger.warning(
                        "drain: no fresh price for %s; dropping (fail-closed)",
                        queued.symbol,
                    )
                    continue
                pricing_price = fresh.close
                submit_match = match
                if queued.intent_class != "exit":
                    # ENTRY: rescale the at-open quantity to the fresh price
                    # (notional sizing is linear in 1/price, so this is exactly
                    # the ADR §2 recompute, model-agnostic — no per-strategy
                    # notional param plumbing). The numerator is the TRADED symbol's
                    # own locked close (``sized_close``), the price the strategy
                    # sized on — NOT universe[0]'s ``decision_bar.close``. EXITs sell
                    # the held lot and are NEVER rescaled. ``not (q > 0)`` is NaN-safe.
                    fresh_qty = math.floor(match.quantity * sized_close / pricing_price)
                    if not (fresh_qty > 0):
                        # DECIDED ENTRY drop (Fix #3): the fresh open price is so high
                        # the resize floors to 0 shares — a determination against this
                        # entry, not a transient can't-evaluate. Terminate the row;
                        # NEVER submit a 0-share order.
                        self._mark_drain_entry_dropped(
                            queued, decision_bar, reason="fresh_price_zero_share"
                        )
                        continue
                    submit_match = replace(match, quantity=float(fresh_qty))
            except Exception as exc:  # noqa: BLE001 — pre-submit raise: nothing consumed
                # One poison intent must not abort draining its siblings; the pre-submit
                # body legitimately leaves the row queued for retry next open / sweep.
                # ``get_latest_bar`` raising lands here too -> ENTRY stays queued.
                logger.warning(
                    "drain: intent %s raised before submit; leaving queued (%s)",
                    queued.idempotency_key,
                    exc,
                )
                continue
            # ``submit_match`` is a valid, positive-quantity intent (entry resized to
            # the fresh price; exit unchanged). The submit is OUTSIDE the pre-submit
            # try: the consume CAS inside ``_submit_locked`` may flip the row to
            # 'consumed' BEFORE the broker call, so a raise here means the row is NOT
            # safely re-queuable — never claim 'leaving queued'. For an EXIT, surface
            # it (the position may be stranded); an ENTRY raise stays silent.
            try:
                result = self._execution_service.submit_paper(
                    self._runner_intent(submit_match),
                    session_id=self._session_id,
                    reasoning=decision.reasoning,
                    idempotency_key=queued.idempotency_key,
                    latest_bar_override=decision_bar,
                    pricing_unit_price=pricing_price,
                )
            except Exception as exc:  # noqa: BLE001 — post-CAS submit raise: row may be consumed
                logger.warning(
                    "drain: submit raised for %s (row may be consumed; not re-queued): %s",
                    queued.idempotency_key,
                    exc,
                )
                if queued.intent_class == "exit":
                    self._emit_exit_drop_alert(queued, reason="submit_error")
                continue
            # A routine broker rejection (OrderRejectedError / InsufficientFundsError)
            # is CAUGHT in the service and RETURNED as status=REJECTED, NOT raised. By
            # then the consume CAS has already flipped the row to 'consumed' (it runs
            # before the broker call), so a rejected EXIT is a consumed-but-unsubmitted
            # STRAND — surface it. status=REJECTED in the drain only ever occurs after
            # the CAS, so it is the exact strand condition; BLOCKED (idempotency-
            # suppressed race-loss, or a pre-CAS risk block that leaves the row queued)
            # is NOT a strand and must not alert. The row is already terminal — the
            # alert is observational; do not mutate status further. ENTRY stays silent.
            if queued.intent_class == "exit" and result.status == ExecutionStatus.REJECTED:
                self._emit_exit_drop_alert(queued, reason="submit_rejected")
            # A staleness veto (`stale_market_data`) on a drained intent is
            # PERMANENT by construction: the risk gate evaluates the
            # reconstructed locked-in decision bar (threaded as
            # `latest_bar_override`), which is frozen in the row — a fixed bar
            # only ever gets staler, so retrying can never succeed. Observed
            # 2026-07-23: intents locked at the 7/21 close drained after a
            # skipped session (7/22 no-op), were correctly vetoed, then retried
            # every open poll for hours (hundreds of blocked explanations).
            # Retire on the FIRST such veto. EXIT: alert with the DISTINCT
            # reason `stale_locked_veto` + `obsolete` — the strategy re-emits a
            # fresh exit at the next close-eval (the existing recovery model).
            # ENTRY: terminal drop via the decided-entry bookkeeping (audit
            # row, no alert spam). Transient data problems are untouched: the
            # #374 no-fresh-price EXIT retry never reaches submit, and
            # `no_latest_bar` / `idempotency_suppressed` /
            # `submit_serialization_unavailable` carry their own codes.
            if (
                result.status == ExecutionStatus.BLOCKED
                and result.risk_decision is not None
                and "stale_market_data" in result.risk_decision.reason_codes
            ):
                if queued.intent_class == "exit":
                    self._emit_exit_drop_alert(queued, reason="stale_locked_veto")
                    self._event_store.mark_queued_intent_obsolete(queued.id)
                else:
                    self._mark_drain_entry_dropped(queued, decision_bar, reason="stale_locked_veto")
                continue
            # A BLOCKED ENTRY (pre-CAS risk veto that leaves the row 'queued', OR an
            # idempotency-suppressed race-loss where another process already consumed
            # the row) must not re-submit every ~60s open poll for the rest of this
            # session — dedup it in memory. Skipping is correct for BOTH BLOCKED causes:
            # a race-loss means the row is already consumed elsewhere; a risk veto will
            # not clear before the row is superseded at the next lock-in or expires. The
            # row status is NOT touched (it stays 'queued' by design). EXITs are exempt:
            # a blocked exit guards an open position and its veto can clear mid-session,
            # so it keeps retrying every poll.
            if queued.intent_class != "exit" and result.status == ExecutionStatus.BLOCKED:
                self._drain_vetoed_row_ids.add(queued.id)

    def _fresh_pricing_bar(self, symbol: str, locked_bar: Bar) -> Bar | None:
        """Fetch a CONFIRMABLY-CURRENT fresh bar for drain sizing/cap pricing.

        ADR 0057 §2: the drain must size entries and price the exposure cap on a
        fresh open price, not the stale locked-in close. The fresh price comes
        from the live IEX minute bar (``get_latest_bar``), distinct from the
        locked daily SESSION bar that drives the staleness gate.

        Fails CLOSED (returns ``None``) when no usable fresh price is available:

        * ``get_latest_bar`` raised (provider outage) — caught HERE so the
          "can't be obtained" case routes through the same fail-closed branch as
          an unconfirmable bar (an EXIT must route through the bounded
          retry-then-alert branch, not be swallowed by the drain's generic
          pre-submit handler with no alert);
        * the fresh bar is not from the current session
          (``_is_current_session_bar`` — a stale provider returning a prior
          close is rejected);
        * the fresh bar is not strictly newer than the locked session bar
          (a provider echoing the locked bar carries no new price); or
        * the fresh close is non-finite / non-positive.

        Both date and strict-newness checks bias fail-closed: a drain that cannot
        confirm a fresh price must not submit on a stale one.
        """
        try:
            fresh = self._data_provider.get_latest_bar(symbol)
        except Exception as exc:  # noqa: BLE001 — provider outage -> fail closed
            logger.warning("drain: get_latest_bar failed for %s (%s)", symbol, exc)
            return None
        if not self._is_current_session_bar(fresh):
            return None
        if not (fresh.timestamp > locked_bar.timestamp):
            return None
        close = fresh.close
        if close is None or not (close > 0):  # NaN-safe (NaN > 0 is False)
            return None
        return fresh

    def _traded_symbol_locked_close(self, eval_bars: dict[str, Any], symbol: str) -> float | None:
        """Return the TRADED symbol's own locked close from the truncated bars.

        ``eval_bars`` is ``_fetch_bars_by_symbol`` truncated to the locked-in
        session (``_bars_through``); the latest close of ``eval_bars[symbol]`` is
        the exact price the strategy sized the at-open quantity on (the
        cross-sectional sizers use ``unit_price = candidate["latest_close"]``,
        which equals this value). This is the correct rescale numerator — distinct
        from ``decision_bar.close``, which is always universe[0]'s close.

        Fails CLOSED (returns ``None``) when the close is not obtainable: the
        symbol is missing from ``eval_bars``, its truncated BarSet is empty, or its
        latest close is non-finite / non-positive. The caller routes a ``None`` to
        the same fail-closed asymmetry guard as a missing fresh price.
        """
        bars = eval_bars.get(symbol)
        if bars is None or len(bars) == 0:
            return None
        close = bars.latest().close
        if close is None or not (close > 0):  # NaN-safe (NaN > 0 is False)
            return None
        return float(close)

    def _reconstruct_locked_in_bar(self, queued: QueuedIntentEvent) -> Bar:
        """Rebuild the locked-in SESSION bar from the persisted intent payload.

        Sourced from ``intent_payload_json["locked_in_bar"]`` (serialized at
        lock-in), NOT a re-fetch — so the drain prices and gates on the exact
        completed session bar the post-close evaluation locked in.
        """
        raw = queued.intent_payload_json["locked_in_bar"]
        return Bar(
            timestamp=datetime.fromisoformat(raw["timestamp"]),
            open=raw["open"],
            high=raw["high"],
            low=raw["low"],
            close=raw["close"],
            volume=raw["volume"],
            vwap=raw.get("vwap"),
        )

    def _bars_through(self, bars_by_symbol: dict[str, Any], cutoff_ts: datetime) -> dict[str, Any]:
        """Truncate every symbol's bars to those at/through ``cutoff_ts``.

        Re-evaluation must replay on history through the locked-in completed
        session bar, excluding any today-dated in-progress daily bar a live
        provider may return at the open. This re-validates the signal on the same
        completed-bar basis it was promoted on, while sizing recomputes against
        fresh equity in the drain context.
        """
        truncated: dict[str, Any] = {}
        for symbol, bars in bars_by_symbol.items():
            frame = bars.to_dataframe()
            truncated[symbol] = BarSet(frame.loc[frame["timestamp"] <= cutoff_ts])
        return truncated

    def _match_drain_intent(
        self, intents: list[TradeIntent], queued: QueuedIntentEvent
    ) -> TradeIntent | None:
        """Return the re-derived intent matching the queued symbol+side, else None."""
        for intent in intents:
            if intent.normalized_symbol() == queued.symbol and intent.side.value == queued.side:
                return intent
        return None

    def _resolve_config_path(self) -> Path:
        from milodex.strategies.loader import resolve_config_path

        return resolve_config_path(self._strategy_id, self._config_dir)

    def _fetch_bars_by_symbol(self) -> dict[str, Any]:
        """Fetch bars for every universe symbol over the history window."""
        universe = list(self._loaded.context.universe)
        timeframe = timeframe_from_bar_size(self._loaded.config.tempo["bar_size"])
        end = self._now().date()  # UTC, consistent with session-bar checks
        start = end - timedelta(days=self._history_window_days())
        return self._data_provider.get_bars(universe, timeframe, start, end)

    def _bar_duration(self) -> timedelta:
        """Return the bar window length for this strategy's intraday bar size.

        Intraday only — ``bar_size_minutes_from_timeframe`` raises on ``1D``,
        and the daily path never calls this.
        """
        timeframe = timeframe_from_bar_size(self._loaded.config.tempo["bar_size"])
        return timedelta(minutes=bar_size_minutes_from_timeframe(timeframe))

    def _truncate_to_completed_bars(self, bars_by_symbol: dict[str, Any]) -> dict[str, Any]:
        """Drop bars whose window has not closed yet (HR-2 / R-P1-2).

        The provider re-fetches today on every call, so mid-window the latest
        bar is the still-forming one (start-of-bar timestamped, mutating until
        the window closes). The backtest contract the strategies were promoted
        on decides strictly on completed bars — decision_time = bar_ts +
        bar_size (backtesting/intraday_simulation.py) — so live evaluation
        keeps only bars with ``timestamp + bar_size <= now``.
        """
        cutoff = self._now() - self._bar_duration()
        truncated: dict[str, Any] = {}
        for symbol, bars in bars_by_symbol.items():
            frame = bars.to_dataframe()
            truncated[symbol] = BarSet(frame.loc[frame["timestamp"] <= cutoff])
        return truncated

    def _evaluation_symbol(self) -> str:
        if self._loaded.context.universe:
            return self._loaded.context.universe[0]
        msg = f"Strategy '{self._strategy_id}' has no resolvable universe for runtime execution."
        raise ValueError(msg)

    def _build_entry_state(self) -> dict[str, dict[str, Any]]:
        """Build entry_state from this strategy's event-store open lots (ADR 0055)."""
        open_lots = strategy_open_lots(self._strategy_id, self._event_store)
        if not open_lots:
            return {}

        today = self._now().date()  # UTC, consistent with session-bar checks
        entry_state: dict[str, dict[str, Any]] = {}
        for sym, lot in open_lots.items():
            opened_at = lot["opened_at"]
            opened_date = opened_at.date() if isinstance(opened_at, datetime) else today
            entry_state[sym] = {
                "entry_price": float(lot["avg_entry_price"]),
                "held_days": (today - opened_date).days,
            }
        return entry_state

    def _history_window_days(self) -> int:
        integer_parameters = [
            value
            for value in self._loaded.config.parameters.values()
            if isinstance(value, int) and value > 0
        ]
        largest_parameter = max(integer_parameters, default=30)
        return max(365, largest_parameter * 3)

    def _current_positions(self) -> dict[str, float]:
        return strategy_positions(self._strategy_id, self._event_store)

    def _runner_intent(self, intent: TradeIntent) -> TradeIntent:
        return TradeIntent(
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            strategy_config_path=self._loaded.config.path,
            submitted_by="strategy_runner",
            # Bind the runner's stage and risk envelope as observed at
            # config-load time. The risk evaluator routes manifest_drift,
            # strategy_stage, and the cap-based checks (max_positions,
            # max_position_pct, daily_loss_cap_pct) through these instead of
            # the per-cycle YAML reads, closing the TOCTOU class surfaced
            # 2026-05-06. ``self._loaded`` and ``self._risk_envelope`` are
            # set once in ``__init__`` and are not refreshed for the life of
            # the runner — the values here are immutable across cycles by
            # construction.
            expected_stage=self._loaded.config.stage,
            expected_max_positions=self._risk_envelope.max_positions,
            expected_max_position_pct=self._risk_envelope.max_position_pct,
            expected_daily_loss_cap_pct=self._risk_envelope.daily_loss_cap_pct,
        )

    def _record_no_action(
        self,
        latest_bar_timestamp: datetime,
        latest_bar_close: float,
        *,
        reasoning: DecisionReasoning | None = None,
    ) -> None:
        self._execution_service.record_no_action(
            strategy_name=self._strategy_id,
            strategy_stage=self._loaded.config.stage,
            strategy_config_path=self._loaded.config.path,
            config_hash=self._loaded.context.config_hash,
            symbol=self._evaluation_symbol(),
            latest_bar_timestamp=latest_bar_timestamp,
            latest_bar_close=latest_bar_close,
            session_id=self._session_id,
            reasoning=reasoning,
        )

    def _handle_sigint(self, signum, frame) -> None:  # noqa: ARG002
        if self._dialog_open:
            self._requested_shutdown = "kill_switch"
            return

        self._dialog_open = True
        try:
            choice = self._prompt_fn().strip().lower()
        except KeyboardInterrupt:
            choice = "k"
        finally:
            self._dialog_open = False

        if choice == "c":
            self._requested_shutdown = "controlled"
        elif choice == "k":
            self._requested_shutdown = "kill_switch"

    def _prompt_shutdown_choice(self) -> str:
        print("  [c] controlled stop - finish current cycle, exit cleanly")
        print("  [k] kill switch     - cancel open orders, halt, require manual reset")
        print("  [n] nevermind       - keep running")
        return input("Choose shutdown mode [c/k/n]: ")


def _resolve_poll_interval(tempo: dict[str, Any], explicit: float | None) -> float:
    """Return the poll interval to use for a strategy.

    Priority (highest to lowest):
    1. ``explicit`` — caller-supplied value (e.g. from CLI flag or test fixture)
    2. ``tempo["poll_interval_seconds"]`` — optional per-strategy YAML override
    3. ``_POLL_INTERVAL_BY_BAR_SIZE[tempo["bar_size"]]`` — bar-size default
    4. 60.0 — absolute fallback if bar_size is unrecognised (shouldn't happen
       given loader validation, but defensive)
    """
    if explicit is not None:
        return explicit
    yaml_override = tempo.get("poll_interval_seconds")
    if yaml_override is not None:
        return float(yaml_override)
    bar_size = tempo.get("bar_size", "")
    return _POLL_INTERVAL_BY_BAR_SIZE.get(bar_size, 60.0)
