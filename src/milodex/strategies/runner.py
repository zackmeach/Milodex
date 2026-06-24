"""Foreground strategy runtime for paper-trading sessions."""

from __future__ import annotations

import logging
import signal
import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from milodex.analytics.snapshots import record_daily_snapshot
from milodex.broker import BrokerClient
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
from milodex.execution.models import ExecutionResult, TradeIntent
from milodex.execution.service import ExecutionService
from milodex.operations.reconciliation import local_trading_day, run_reconciliation
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
        self._requested_shutdown: str | None = None
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
                self.run_cycle()
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
            return []

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
        if (
            is_daily_bar
            and not market_open
            and not self._maybe_advance_lockin_watermark(latest_bar)
        ):
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
            if self._on_cycle_result is not None:
                self._on_cycle_result([])
            return []

        if self._processed_intent_bar_at != latest_bar.timestamp:
            # Bar rolled over; prior keys can never re-match because the
            # already_seen short-circuit above gates on _last_processed_bar_at.
            self._processed_intent_keys.clear()
            self._processed_intent_bar_at = latest_bar.timestamp

        if is_daily_bar and not market_open:
            # Phase-1 (queue-at-open, ADR 0057): a daily strategy must NOT submit
            # at the post-close lock-in — the market is closed and the order
            # would be vetoed (market_closed) or queued blind to an unknown
            # next-open price. Persist the locked-in intent; the next at-open
            # drain re-evaluates against fresh state and submits through the
            # chokepoint. The watermark already advanced above (exactly once).
            for intent in intents:
                intent_key = self._intent_key(intent, latest_bar.timestamp)
                if intent_key in self._processed_intent_keys:
                    continue
                self._processed_intent_keys.add(intent_key)
                self._persist_queued_intent(intent, latest_bar, decision.reasoning)
            if self._on_cycle_result is not None:
                self._on_cycle_result([])
            return []

        results: list[ExecutionResult] = []
        for intent in intents:
            intent_key = self._intent_key(intent, latest_bar.timestamp)
            if intent_key in self._processed_intent_keys:
                continue
            self._processed_intent_keys.add(intent_key)
            results.append(
                self._execution_service.submit_paper(
                    self._runner_intent(intent),
                    session_id=self._session_id,
                    reasoning=decision.reasoning,
                )
            )
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
            # Mirror the service path (HR-6 / R-P2-5): a broker failure must
            # NEVER block kill-switch activation — cancel best-effort, engage
            # unconditionally.
            try:
                self._broker.cancel_all_orders()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Kill-switch shutdown: cancel_all_orders failed; activating the switch anyway.",
                    exc_info=True,
                )
            self._execution_service.trigger_kill_switch("Operator requested kill switch.")
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

        Both sides are UTC: ``_now()`` returns ``datetime.now(tz=UTC)`` and
        BarSet timestamps are ``datetime64[ns, UTC]`` (data/models.py), so the
        comparison is dependency-free (no tzdata).  ``>=`` (not ``==``) is
        required: real bars are never future-dated in production, but the test
        harness builds today-dated bars against a fixed historical fake clock,
        so ``==`` would reject them.  A pre-open / weekend launch sees a
        prior-session bar (date < today) and is correctly declined.
        """
        return latest_bar.timestamp.date() >= self._now().date()

    def _maybe_advance_lockin_watermark(self, latest_bar) -> bool:
        """Gate post-close watermark advance on bar finalization stability.

        Two consecutive identical OHLCV fetches separated by at least
        ``_close_lockin_min_interval_seconds`` confirm the bar has settled.
        After ``_close_lockin_max_wait_seconds`` without confirmation the
        watermark advances anyway (CI-1 fail-mode (a)) — the per-cycle
        explanations preserve forensic visibility into the unstable window.
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
            self._last_processed_bar_at = latest_bar.timestamp
            self._reset_lockin()
            return True

        if signature != self._pending_lockin_signature:
            self._pending_lockin_signature = signature
            self._pending_lockin_seen_at = now
            return False

        if (
            now - self._pending_lockin_seen_at
        ).total_seconds() >= self._close_lockin_min_interval_seconds:
            self._last_processed_bar_at = latest_bar.timestamp
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

    # ------------------------------------------------------------------
    # queue-at-open (Phase-3 drain, ADR 0057)
    # ------------------------------------------------------------------

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
        bar as ``latest_bar_override`` (feeds the session-aware 1D staleness gate
        and the cap pricing). The drain NEVER touches ``_last_processed_bar_at``
        (I-3) and NEVER calls ``mark_queued_intent_consumed`` (the submit CAS owns
        that); the only status write here is ``obsolete`` for a flat-ledger exit.
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
                    logger.warning(
                        "drain: %s not tradable (%s); dropping", queued.symbol, td.reason
                    )
                    continue
                eval_bars = self._bars_through(
                    self._fetch_bars_by_symbol(), decision_bar.timestamp
                )
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
                    # 0-share entry / no re-derived match -> drop, leave queued to
                    # expire; NEVER submit a 0-share order.
                    continue
            except Exception as exc:  # noqa: BLE001 — pre-submit raise: nothing consumed
                # One poison intent must not abort draining its siblings; the pre-submit
                # body legitimately leaves the row queued for retry next open / sweep.
                logger.warning(
                    "drain: intent %s raised before submit; leaving queued (%s)",
                    queued.idempotency_key,
                    exc,
                )
                continue
            # ``match`` is a valid, positive-quantity intent. The submit is OUTSIDE the
            # pre-submit try: the consume CAS inside ``_submit_locked`` may flip the row
            # to 'consumed' BEFORE the broker call, so a raise here means the row is NOT
            # safely re-queuable — never claim 'leaving queued'. For an EXIT, surface it
            # (the position may be stranded); an ENTRY raise stays silent.
            try:
                self._execution_service.submit_paper(
                    self._runner_intent(match),
                    session_id=self._session_id,
                    reasoning=decision.reasoning,
                    idempotency_key=queued.idempotency_key,
                    latest_bar_override=decision_bar,
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

    def _bars_through(
        self, bars_by_symbol: dict[str, Any], cutoff_ts: datetime
    ) -> dict[str, Any]:
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
