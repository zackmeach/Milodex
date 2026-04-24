"""Foreground strategy runtime for paper-trading sessions."""

from __future__ import annotations

import signal
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from milodex.broker import BrokerClient
from milodex.core.event_store import EventStore, StrategyRunEvent
from milodex.data import DataProvider, Timeframe
from milodex.execution.models import ExecutionResult, TradeIntent
from milodex.execution.service import ExecutionService
from milodex.strategies.base import DecisionReasoning
from milodex.strategies.loader import StrategyLoader, load_strategy_config
from milodex.strategies.positions import compute_ledger_positions


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
        poll_interval_seconds: float = 5.0,
        prompt_fn: Callable[[], str] | None = None,
        on_cycle_result: Callable[[list[ExecutionResult]], None] | None = None,
    ) -> None:
        self._strategy_id = strategy_id
        self._config_dir = config_dir
        self._broker = broker_client
        self._data_provider = data_provider
        self._execution_service = execution_service
        self._event_store = event_store
        self._poll_interval_seconds = poll_interval_seconds
        self._prompt_fn = prompt_fn or self._prompt_shutdown_choice
        self._on_cycle_result = on_cycle_result
        self._loaded = StrategyLoader().load(self._resolve_config_path())
        self._session_id = str(uuid4())
        self._started_at = datetime.now(tz=UTC)
        self._last_processed_bar_at: datetime | None = None
        self._requested_shutdown: str | None = None
        self._closed = False
        self._dialog_open = False

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
        """Run the strategy loop until the operator stops it."""
        previous_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        try:
            while True:
                if self._requested_shutdown is not None:
                    break
                self.run_cycle()
                if self._requested_shutdown is not None:
                    break
                time.sleep(self._poll_interval_seconds)
        finally:
            signal.signal(signal.SIGINT, previous_handler)

        self.shutdown(mode=self._requested_shutdown or "controlled")

    def run_cycle(self) -> list[ExecutionResult]:
        """Process one new daily close when available.

        Only records ``_last_processed_bar_at`` when the market is closed: a
        1D bar fetched while the market is open is still in-progress and
        shares its timestamp with the post-close finalized bar, so advancing
        the watermark mid-session would suppress the authoritative close
        evaluation via the same-timestamp ``already_seen`` check.
        """
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
        ledger_positions = self._current_positions()
        context = replace(
            self._loaded.context,
            positions=ledger_positions,
            equity=account.equity,
            bars_by_symbol=bars_by_symbol,
            entry_state=self._build_entry_state(ledger_positions),
        )
        decision = self._loaded.strategy.evaluate(primary_bars, context)
        intents = decision.intents
        if not self._broker.is_market_open():
            self._last_processed_bar_at = latest_bar.timestamp

        if not intents:
            self._record_no_action(
                latest_bar.timestamp, latest_bar.close, reasoning=decision.reasoning
            )
            if self._on_cycle_result is not None:
                self._on_cycle_result([])
            return []

        results: list[ExecutionResult] = []
        for intent in intents:
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
        """Shut down the current strategy session."""
        if self._closed:
            return

        exit_reason = "controlled_stop"
        if mode == "kill_switch":
            self._broker.cancel_all_orders()
            self._execution_service.trigger_kill_switch("Operator requested kill switch.")
            exit_reason = "kill_switch"

        self._event_store.append_strategy_run(
            StrategyRunEvent(
                session_id=self._session_id,
                strategy_id=self._strategy_id,
                started_at=self._started_at,
                ended_at=datetime.now(tz=UTC),
                exit_reason=exit_reason,
                metadata={
                    "config_path": str(self._loaded.config.path),
                    "stage": self._loaded.config.stage,
                },
            )
        )
        self._closed = True

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

    def _build_entry_state(
        self, ledger_positions: dict[str, float] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Build entry_state for each ledger-owned position.

        Only symbols in ``ledger_positions`` (derived from this strategy's
        own trade history per ADR 0021) are considered — a position the
        broker reports for some other strategy must not appear here.

        ``entry_price`` prefers the broker's reported ``avg_entry_price``
        for the symbol when available (authoritative actual-fill price);
        falls back to the strategy's ledger VWAP over submitted paper
        BUYs when the broker has no record (rare: position opened under a
        previous strategy session but broker state since cleared).
        ``held_days`` is derived from this strategy's most recent paper
        BUY for the symbol.
        """
        if ledger_positions is None:
            ledger_positions = self._current_positions()
        if not ledger_positions:
            return {}

        broker_avg_by_symbol: dict[str, float] = {}
        for position in self._broker.get_positions():
            broker_avg_by_symbol[position.symbol.upper()] = float(position.avg_entry_price)

        today = date.today()
        last_buy_date: dict[str, date] = {}
        ledger_vwap_num: dict[str, float] = {}
        ledger_vwap_den: dict[str, float] = {}
        for trade in self._event_store.list_trades_for_strategy(self._strategy_id):
            sym = trade.symbol.upper()
            if sym not in ledger_positions:
                continue
            if trade.side.lower() != "buy":
                continue
            trade_date = trade.recorded_at.date()
            if sym not in last_buy_date or trade_date > last_buy_date[sym]:
                last_buy_date[sym] = trade_date
            price = float(trade.estimated_unit_price)
            qty = float(trade.quantity)
            ledger_vwap_num[sym] = ledger_vwap_num.get(sym, 0.0) + price * qty
            ledger_vwap_den[sym] = ledger_vwap_den.get(sym, 0.0) + qty

        entry_state: dict[str, dict[str, Any]] = {}
        for sym in ledger_positions:
            entry_price = broker_avg_by_symbol.get(sym)
            if entry_price is None and ledger_vwap_den.get(sym, 0.0) > 0:
                entry_price = ledger_vwap_num[sym] / ledger_vwap_den[sym]
            if entry_price is None:
                continue
            buy_date = last_buy_date.get(sym)
            held_days = (today - buy_date).days if buy_date is not None else 0
            entry_state[sym] = {
                "entry_price": float(entry_price),
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
        """Return positions owned by *this strategy*, derived from its ledger.

        Per ADR 0021, strategies must not read the account-wide broker
        position list directly: that list reflects every strategy's trades
        and would let one strategy act on another's open positions (as
        observed 2026-04-24 with meanrev attempting to exit the regime
        strategy's SPY position). The authoritative view is the trade
        ledger filtered by ``strategy_name``.
        """
        return compute_ledger_positions(self._event_store, self._strategy_id)

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
