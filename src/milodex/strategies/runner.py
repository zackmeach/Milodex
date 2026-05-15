"""Foreground strategy runtime for paper-trading sessions."""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from milodex.analytics.snapshots import record_daily_snapshot
from milodex.broker import BrokerClient
from milodex.core.event_store import EventStore, StrategyRunEvent
from milodex.data import DataProvider, Timeframe
from milodex.execution.config import load_strategy_execution_config
from milodex.execution.models import ExecutionResult, TradeIntent
from milodex.execution.service import ExecutionService
from milodex.strategies.base import DecisionReasoning
from milodex.strategies.loader import StrategyLoader, load_strategy_config
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
        self._pending_lockin_signature: tuple[float, float, float, float, int] | None = None
        self._pending_lockin_seen_at: datetime | None = None
        self._lockin_started_at: datetime | None = None
        self._processed_intent_keys: set[tuple[datetime, str, str]] = set()
        self._processed_intent_bar_at: datetime | None = None
        self._requested_shutdown: str | None = None
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
        """Process one new daily close when available.

        Only records ``_last_processed_bar_at`` when the market is closed: a
        1D bar fetched while the market is open is still in-progress and
        shares its timestamp with the post-close finalized bar, so advancing
        the watermark mid-session would suppress the authoritative close
        evaluation via the same-timestamp ``already_seen`` check.

        Market-hours gate (Spec B): if the market is closed AND the lockin
        watermark has advanced (i.e. today's close bar is confirmed final),
        skip the API fetch entirely.  Weekends, holidays, and post-close idle
        polling all benefit — no Alpaca call is made until the next session
        opens.  The gate checks ``is_market_open()`` FIRST, before any network
        I/O, so closed-market cycles are cheap regardless of fetch cost.
        """
        market_open = self._broker.is_market_open()
        is_daily_bar = self._is_daily_bar()
        if is_daily_bar and market_open:
            return []
        if not market_open and self._last_processed_bar_at is not None:
            # Market is closed and today's close has been confirmed via the
            # lockin stability window.  Nothing new to process until open.
            return []

        bars_by_symbol = self._fetch_bars_by_symbol()
        primary_bars = bars_by_symbol[self._evaluation_symbol()]
        latest_bar = primary_bars.latest()
        already_seen = (
            self._last_processed_bar_at is not None
            and latest_bar.timestamp <= self._last_processed_bar_at
        )
        if already_seen:
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

        if not intents:
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
            self._broker.cancel_all_orders()
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

    def _now(self) -> datetime:
        return datetime.now(tz=UTC)

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

    def _resolve_config_path(self) -> Path:
        for path in sorted(self._config_dir.glob("*.yaml")):
            try:
                config = load_strategy_config(path)
            except ValueError:
                continue
            if config.strategy_id == self._strategy_id:
                return path
        msg = f"Strategy config not found for strategy id: {self._strategy_id}"
        raise ValueError(msg)

    def _fetch_bars_by_symbol(self) -> dict[str, Any]:
        """Fetch bars for every universe symbol over the history window."""
        universe = list(self._loaded.context.universe)
        timeframe = _timeframe_from_bar_size(self._loaded.config.tempo["bar_size"])
        end = date.today()
        start = end - timedelta(days=self._history_window_days())
        return self._data_provider.get_bars(universe, timeframe, start, end)

    def _evaluation_symbol(self) -> str:
        if self._loaded.context.universe:
            return self._loaded.context.universe[0]
        msg = f"Strategy '{self._strategy_id}' has no resolvable universe for runtime execution."
        raise ValueError(msg)

    def _build_entry_state(self) -> dict[str, dict[str, Any]]:
        """Build entry_state for each open position from broker + trade history.

        ``entry_price`` comes from the broker's reported average fill price.
        ``held_days`` is derived from the most recent paper BUY trade in the
        event store for each symbol.  Falls back to 0 when no trade record
        exists (e.g. position opened outside this system).
        """
        positions = self._broker.get_positions()
        if not positions:
            return {}

        today = date.today()
        last_buy_date: dict[str, date] = {}
        for trade in self._event_store.list_trades():
            if trade.side.lower() == "buy" and trade.source == "paper":
                sym = trade.symbol.upper()
                trade_date = trade.recorded_at.date()
                if sym not in last_buy_date or trade_date > last_buy_date[sym]:
                    last_buy_date[sym] = trade_date

        entry_state: dict[str, dict[str, Any]] = {}
        for position in positions:
            sym = position.symbol.upper()
            buy_date = last_buy_date.get(sym)
            held_days = (today - buy_date).days if buy_date is not None else 0
            entry_state[sym] = {
                "entry_price": float(position.avg_entry_price),
                "held_days": held_days,
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
        return {
            position.symbol.upper(): float(position.quantity)
            for position in self._broker.get_positions()
            if float(position.quantity) > 0
        }

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


def _timeframe_from_bar_size(value: str) -> Timeframe:
    mapping = {
        "1D": Timeframe.DAY_1,
        "1H": Timeframe.HOUR_1,
        "15Min": Timeframe.MINUTE_15,
        "5Min": Timeframe.MINUTE_5,
        "1Min": Timeframe.MINUTE_1,
    }
    return mapping[value]


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
