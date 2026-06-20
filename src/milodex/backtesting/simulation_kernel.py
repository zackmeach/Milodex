"""Shared mechanics for daily and intraday backtest simulations.

Pragmatic boundary, not pure-functional kernel
-----------------------------------------------
This module is the "kernel" half of the kernel/shell split extracted from
``BacktestEngine`` in PRs #187-#189.  But it is NOT a Hickey-style
pure-functional kernel — it imports and orchestrates real I/O surfaces:

- :class:`milodex.core.event_store.EventStore` — writes ``ExplanationEvent``
  and ``TradeEvent`` rows.
- :func:`milodex.analytics.snapshots.record_backtest_equity_snapshot` —
  writes equity rows to ``backtest_equity_snapshots``.
- :class:`milodex.broker.simulated.SimulatedBroker` — mutates broker state.

"Kernel" here means "factored out of the engine for testability and reuse
across daily/intraday paths," not "no side effects."  Treat it as a
pragmatic seam, not a referentially-transparent core.  If you weaken
something on the assumption that the kernel is pure, the bug will land
in equity-curve audit data and the event store.

``sync_broker_state(day=day, ...)`` passes the OUTER trading day
---------------------------------------------------------------
In the intraday simulation path (``engine.py:_simulate_intraday``,
specifically the inner loop at ~line 1223), the kernel call uses the
outer trading-day variable as ``day``:

    kernel.sync_broker_state(day=day, closes=..., equity=...)

It does NOT use ``ts.date()`` (the inner bar timestamp's calendar date).
This is intentional and matches the pre-extraction behavior — broker
snapshots key on the outer trading day so cross-UTC-day intraday bars
(e.g. an Asian-session bar whose UTC date is the *next* calendar day
relative to the trading session) do not split into a second snapshot.

A future refactor "fixing" this to ``ts.date()`` would silently shift
broker snapshots and equity audit rows on UTC/ET-boundary intraday bars.
Do not change it without re-baselining
``tests/milodex/backtesting/test_engine_daily_regression.py`` AND any
intraday regression suite (see ``test_engine_intraday_regression.py``).

``tick_held_days()`` ticks per outer-day iteration, not per evaluation
---------------------------------------------------------------------
See the docstring on :meth:`tick_held_days` (currently at line 289-291).
The increment is unconditional once per outer trading day on both the
daily and intraday paths.  A strategy with ``held_days >= max_hold`` will
exit on the **first intraday tick of the next day**, not at EOD of the
entry day.  Preserved deliberately; pinned here for the next reader.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path

import pandas as pd

from milodex.analytics.snapshots import record_backtest_equity_snapshot
from milodex.broker.models import AccountInfo, OrderSide, OrderType, Position
from milodex.broker.simulated import SimulatedBroker
from milodex.core.event_store import EventStore, ExplanationEvent, TradeEvent
from milodex.data.models import BarSet
from milodex.data.simulated import SimulatedDataProvider
from milodex.execution.models import ExecutionStatus, TradeIntent
from milodex.execution.service import ExecutionService
from milodex.execution.state import KillSwitchStateStore
from milodex.risk import RiskEvaluator
from milodex.strategies.base import DecisionReasoning, StrategyDecision

logger = logging.getLogger(__name__)


class MissingOpenPolicy(Enum):
    """How a pending order behaves when the next open is unavailable."""

    SKIP = "skip"
    RETAIN = "retain"


@dataclass(frozen=True)
class PendingOrder:
    """An intent emitted by a daily bar decision, awaiting the next open."""

    intent: TradeIntent
    reasoning: DecisionReasoning
    decision_day: date | None = None


@dataclass(frozen=True)
class IntradayPendingOrder:
    """An intent emitted by an intraday decision, awaiting a later open."""

    intent: TradeIntent
    reasoning: DecisionReasoning
    decision_timestamp: pd.Timestamp


@dataclass(frozen=True)
class PendingDrainResult:
    """Counts and carry-over orders returned by a pending-order drain."""

    buy_count: int
    sell_count: int
    skipped_count: int
    remaining: list[PendingOrder | IntradayPendingOrder]


class BacktestSimulationKernel:
    """Mutable simulation state and shared order/audit mechanics."""

    def __init__(
        self,
        *,
        event_store: EventStore,
        all_bars: dict[str, BarSet],
        strategy_id: str,
        strategy_stage: str,
        strategy_config_path: Path | None,
        config_hash: str,
        risk_defaults_path: Path,
        risk_evaluator: RiskEvaluator,
        slippage_pct: float,
        commission_per_trade: float,
        initial_cash: float,
        max_positions: int | None,
        max_position_pct: float | None,
        daily_loss_cap_pct: float | None,
    ) -> None:
        self.event_store = event_store
        self.strategy_id = strategy_id
        self.strategy_stage = strategy_stage
        self.strategy_config_path = strategy_config_path
        self.config_hash = config_hash
        self.slippage_pct = slippage_pct
        self.commission_per_trade = commission_per_trade
        self.max_positions = max_positions
        self.max_position_pct = max_position_pct
        self.daily_loss_cap_pct = daily_loss_cap_pct

        self.sim_broker = SimulatedBroker(
            slippage_pct=slippage_pct,
            commission_per_trade=commission_per_trade,
        )
        self.sim_data_provider = SimulatedDataProvider(all_bars)
        self.execution_service = ExecutionService(
            broker_client=self.sim_broker,
            data_provider=self.sim_data_provider,
            kill_switch_store=KillSwitchStateStore(event_store=event_store),
            risk_defaults_path=risk_defaults_path,
            risk_evaluator=risk_evaluator,
            event_store=event_store,
            is_backtest=True,
        )

        self.cash = initial_cash
        self.positions: dict[str, tuple[float, float]] = {}
        self.entry_state: dict[str, dict] = {}
        self.sym_fills: dict[str, dict[str, int]] = {}
        # Set when the final ADR 0053 snapshot write fails; the engine
        # surfaces it into the run's final metadata as `snapshot_write_error`.
        self.snapshot_write_error: str | None = None

    def round_trip_count(self) -> int:
        """Return completed round trips by symbol."""
        return sum(min(counts["buys"], counts["sells"]) for counts in self.sym_fills.values())

    def sync_broker_state(
        self,
        *,
        day: date,
        closes: dict[str, float],
        equity: float | None = None,
    ) -> None:
        """Inject simulated account/position state into broker and data provider."""
        effective_equity = (
            equity if equity is not None else compute_equity(self.cash, self.positions, closes)
        )
        day_dt = day_to_dt(day)
        self.sim_broker.set_simulation_day(day=day_dt, closes=closes)
        self.sim_data_provider.set_simulation_day(day)
        self.sim_broker.update_account(
            AccountInfo(
                equity=effective_equity,
                cash=self.cash,
                buying_power=self.cash,
                portfolio_value=effective_equity,
                daily_pnl=0.0,
            )
        )
        reported_positions = []
        for sym, (qty, entry_price) in self.positions.items():
            current_price = closes.get(sym, entry_price)
            reported_positions.append(
                Position(
                    symbol=sym,
                    quantity=qty,
                    avg_entry_price=entry_price,
                    current_price=current_price,
                    market_value=current_price * qty,
                    unrealized_pnl=(current_price - entry_price) * qty,
                    unrealized_pnl_pct=(
                        0.0 if entry_price == 0 else (current_price - entry_price) / entry_price
                    ),
                )
            )
        self.sim_broker.set_positions(reported_positions)

    def record_no_action(
        self,
        *,
        symbol: str,
        latest_bar_timestamp: datetime,
        latest_bar_close: float,
        session_id: str,
        reasoning: DecisionReasoning,
        db_run_id: int,
    ) -> None:
        """Record a backtest no-action explanation through ExecutionService."""
        self.execution_service.record_no_action(
            strategy_name=self.strategy_id,
            strategy_stage=self.strategy_stage,
            strategy_config_path=self.strategy_config_path,
            config_hash=self.config_hash,
            symbol=symbol,
            latest_bar_timestamp=latest_bar_timestamp,
            latest_bar_close=latest_bar_close,
            session_id=session_id,
            reasoning=reasoning,
            submitted_by="backtest_engine",
            backtest_run_id=db_run_id,
        )

    def simulate_decision_step(
        self,
        *,
        universe: list[str],
        primary_bars: BarSet,
        primary_symbol_present: bool,
        bars_by_symbol: dict[str, BarSet],
        closes: dict[str, float],
        equity: float,
        sync_day: date,
        make_pending: Callable[
            [TradeIntent, DecisionReasoning], PendingOrder | IntradayPendingOrder
        ],
        session_id: str,
        db_run_id: int,
        evaluate_strategy: Callable[..., StrategyDecision],
    ) -> list[PendingOrder | IntradayPendingOrder]:
        """Single source of the sync / evaluate / primary-symbol guard / no-action / enqueue cycle.

        Replaces duplicated decision-block logic that used to live in both
        ``BacktestEngine._simulate_daily`` (engine.py:980-1039 pre-refactor)
        and ``BacktestEngine._simulate_intraday`` (engine.py:1204-1261
        pre-refactor). The primary-symbol guard (never substitute
        ``universe[0]``; never emit a ``record_no_action`` row with a
        borrowed symbol's bar) is enforced HERE — this is the invariant
        that has already produced one real regime-analytics regression.

        Coupling note: this method teaches the kernel about ``StrategyDecision``,
        ``universe[0]`` semantics, and the no-action audit shape. That is real
        new coupling — the kernel previously did not know about strategy
        structure. The trade is "kernel grows one decision-step concept" vs
        "two simulators duplicate the primary-symbol invariant." A future
        reviewer must NOT extract this back out as "kernel shouldn't know
        about strategies" — the duplication it eliminates has documented
        bug history.

        Caller responsibilities:
          - Substitute an empty BarSet for ``primary_bars`` when
            ``universe[0]`` is absent, and pass ``primary_symbol_present=False``.
          - Pre-compute ``closes`` (latest closes per symbol) and ``equity``.
            Daily path uses a pre-fetched ``latest_closes`` dict; intraday
            computes ``latest_closes_at_ts`` live. Both end up as the same
            ``dict[str, float]`` shape.
          - Provide ``make_pending`` closure that captures ``day`` (daily) or
            ``ts`` (intraday) for the resulting ``PendingOrder`` / ``IntradayPendingOrder``.
          - Provide ``evaluate_strategy`` callable — typically
            ``BacktestEngine._evaluate_strategy`` bound to the engine instance.

        Behavior:
          1. ``sync_broker_state(day=sync_day, closes=closes, equity=equity)``.
          2. ``decision = evaluate_strategy(primary_bars=, bars_by_symbol=,
             equity=, positions=self.positions, entry_state=self.entry_state)``.
             Always called — even when ``primary_symbol_present=False`` — so
             ``context.bars_by_symbol``-consuming strategies still evaluate
             normally. The caller's empty-BarSet substitution trips the
             insufficient-history guard for direct-``primary_bars`` consumers.
          3. If ``decision.intents`` is non-empty: build
             ``[make_pending(intent, decision.reasoning) for intent in intents]``
             and return. ONE ``decision.reasoning`` is reused across all
             intents (per-cycle reasoning, not per-intent).
          4. Else if ``primary_symbol_present``: emit ``record_no_action`` with
             ``primary_bars.latest()``'s timestamp and close, ``symbol=universe[0]``,
             ``reasoning=decision.reasoning``. Return ``[]``.
          5. Else (no intents AND primary absent): return ``[]`` without any
             audit row. No honest primary bar to quote → no record.
        """
        self.sync_broker_state(day=sync_day, closes=closes, equity=equity)
        decision = evaluate_strategy(
            primary_bars=primary_bars,
            bars_by_symbol=bars_by_symbol,
            equity=equity,
            positions=self.positions,
            entry_state=self.entry_state,
        )
        if decision.intents:
            return [make_pending(intent, decision.reasoning) for intent in decision.intents]
        if primary_symbol_present:
            latest_bar = primary_bars.latest()
            self.record_no_action(
                symbol=universe[0],
                latest_bar_timestamp=latest_bar.timestamp,
                latest_bar_close=latest_bar.close,
                session_id=session_id,
                reasoning=decision.reasoning,
                db_run_id=db_run_id,
            )
        return []

    def tick_held_days(self) -> None:
        """Increment ``held_days`` for every symbol with an open position.

        Replaces the inline pattern previously duplicated at engine.py:919-921
        (daily) and engine.py:1164-1166 (intraday). The engine should not be
        reaching into ``entry_state`` dict shape.

        Documented intentional behavior:
          - Ticks every calendar iteration the caller invokes, regardless of
            whether the strategy actually evaluated that iteration. Daily
            path calls this once per ``trading_days`` iteration; intraday
            calls once per OUTER-day iteration (NOT per intraday bar). This
            means an intraday strategy crossing a ``held_days`` threshold
            fires on the FIRST intraday tick of the next day, not at EOD of
            the entry day. Pre-existing behavior, preserved deliberately.
          - A future reviewer must not "fix" this without auditing every
            ``held_days`` consumer.
        """
        for sym in self.entry_state:
            self.entry_state[sym]["held_days"] = int(self.entry_state[sym]["held_days"]) + 1

    def drain_pending_orders(
        self,
        *,
        pending: list[PendingOrder | IntradayPendingOrder],
        opens: dict[str, float],
        day: date,
        session_id: str,
        db_run_id: int,
        missing_open_policy: MissingOpenPolicy,
    ) -> PendingDrainResult:
        """Fill pending orders at available opens, preserving daily/intraday differences."""
        remaining: list[PendingOrder | IntradayPendingOrder] = []
        to_process: list[PendingOrder | IntradayPendingOrder] = []
        if missing_open_policy is MissingOpenPolicy.RETAIN:
            for order in pending:
                if order.intent.normalized_symbol() not in opens:
                    remaining.append(order)
                else:
                    to_process.append(order)
        else:
            to_process = list(pending)

        if not to_process:
            return PendingDrainResult(
                buy_count=0,
                sell_count=0,
                skipped_count=0,
                remaining=remaining,
            )

        sells = [order for order in to_process if order.intent.side is OrderSide.SELL]
        buys = [order for order in to_process if order.intent.side is OrderSide.BUY]

        equity_pre_drain = compute_equity(self.cash, self.positions, opens)
        self.sync_broker_state(day=day, closes=opens, equity=equity_pre_drain)

        sell_count = 0
        skipped_count = 0
        for order in sells:
            if self._skip_missing_or_invalid_sell(
                order=order,
                opens=opens,
                day=day,
                session_id=session_id,
                db_run_id=db_run_id,
                equity=equity_pre_drain,
            ):
                skipped_count += 1
                continue

            sym = order.intent.normalized_symbol()
            latest_open = opens[sym]
            qty, _ = self.positions[sym]
            decorated = self._decorate_intent(order.intent, quantity_override=qty)
            result = self.execution_service.submit_backtest(
                decorated,
                session_id=session_id,
                backtest_run_id=db_run_id,
                reasoning=order.reasoning,
            )
            if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                continue

            fill_price = float(result.order.filled_avg_price or 0.0)
            proceeds = fill_price * qty - self.commission_per_trade
            self.cash += proceeds
            del self.positions[sym]
            self.entry_state.pop(sym, None)
            sell_count += 1
            self.sym_fills.setdefault(sym, {"buys": 0, "sells": 0})["sells"] += 1

        intermediate_equity = compute_equity(self.cash, self.positions, opens)
        self.sync_broker_state(day=day, closes=opens, equity=intermediate_equity)

        buy_count = 0
        for order in buys:
            skip_result = self._buy_skip_reason(
                order=order,
                opens=opens,
                projected_fill=None,
            )
            if skip_result == "non_positive_quantity":
                continue
            if skip_result is not None:
                sym = order.intent.normalized_symbol()
                latest_open = opens.get(sym)
                projected_fill = (
                    latest_open * (1.0 + self.slippage_pct)
                    if latest_open is not None and math.isfinite(latest_open)
                    else latest_open
                )
                self._record_buy_skip(
                    order=order,
                    reason_code=skip_result,
                    latest_open=latest_open,
                    projected_fill=projected_fill,
                    day=day,
                    session_id=session_id,
                    db_run_id=db_run_id,
                    equity=intermediate_equity,
                )
                skipped_count += 1
                continue

            sym = order.intent.normalized_symbol()
            latest_open = opens[sym]
            decorated = self._decorate_intent(order.intent)
            result = self.execution_service.submit_backtest(
                decorated,
                session_id=session_id,
                backtest_run_id=db_run_id,
                reasoning=order.reasoning,
            )
            if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                continue

            qty = float(order.intent.quantity)
            fill_price = float(result.order.filled_avg_price or 0.0)
            realized_cost = fill_price * qty + self.commission_per_trade
            self.cash -= realized_cost
            self.positions[sym] = (qty, fill_price)
            self.entry_state[sym] = {"entry_price": fill_price, "held_days": 0}
            buy_count += 1
            self.sym_fills.setdefault(sym, {"buys": 0, "sells": 0})["buys"] += 1

        return PendingDrainResult(
            buy_count=buy_count,
            sell_count=sell_count,
            skipped_count=skipped_count,
            remaining=remaining,
        )

    def liquidate_open_positions(
        self,
        *,
        closes: dict[str, float],
        day: date,
        session_id: str,
        db_run_id: int,
        reason: DecisionReasoning,
        skip_symbols: frozenset[str] = frozenset(),
    ) -> int:
        """Force-flatten open positions at ``closes`` and return the sell count.

        Engine-level liquidation (NOT a strategy intent). Used by the intraday
        path to guarantee an intraday position is flat at session end even when
        the strategy never emits its own exit (e.g. a missing time-stop bar —
        the N2 strand). Each open position is sold in full at its entry in
        ``closes`` via the SAME execution + cash/position/ledger accounting that
        :meth:`drain_pending_orders` uses for a normal SELL fill — submit through
        ``submit_backtest``, then ``cash += fill_price*qty - commission``, clear
        the position and its entry_state, and bump ``sym_fills[...]["sells"]`` so
        the liquidation counts as a real trade / round-trip in the metrics.

        ``skip_symbols`` are symbols that already have a pending exit (SELL)
        queued for the normal T+1 drain — i.e. the strategy DID self-exit and
        the fill is merely deferred to the next session's open. Those are left
        untouched so the flatten never double-sells a self-exiting strategy
        (the benchmark holds overnight by design: SELL queued at 15:55 fills at
        the next session's 9:30 open). The flatten is the fail-safe ONLY for a
        position with no queued exit.

        ``closes`` must be the day's last available close per symbol (the same
        price :func:`_mark_to_market_at_day_end` values the position at), so the
        equity curve is continuous across the flatten. A position whose symbol
        is absent from ``closes`` (no resolvable close) is left untouched — it
        will be marked-to-market at the prior close exactly as before.

        Does NOT touch the pending/drain queue: this is a separate EOD action
        and the T+1 fill model for strategy-intent trades is unaffected.
        """
        if not self.positions:
            return 0
        # Snapshot symbols first: the loop mutates self.positions.
        symbols = list(self.positions.keys())
        equity_pre = compute_equity(self.cash, self.positions, closes)
        self.sync_broker_state(day=day, closes=closes, equity=equity_pre)

        sell_count = 0
        for sym in symbols:
            if sym in skip_symbols:
                continue
            latest_close = closes.get(sym)
            if latest_close is None or not math.isfinite(latest_close) or latest_close <= 0:
                # No honest day-end price to fill at → leave the position open;
                # it is marked-to-market at the prior close as before.
                continue
            qty, _ = self.positions[sym]
            intent = TradeIntent(
                symbol=sym,
                side=OrderSide.SELL,
                quantity=qty,
                order_type=OrderType.MARKET,
            )
            decorated = self._decorate_intent(intent, quantity_override=qty)
            result = self.execution_service.submit_backtest(
                decorated,
                session_id=session_id,
                backtest_run_id=db_run_id,
                reasoning=reason,
            )
            if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                continue
            fill_price = float(result.order.filled_avg_price or 0.0)
            proceeds = fill_price * qty - self.commission_per_trade
            self.cash += proceeds
            del self.positions[sym]
            self.entry_state.pop(sym, None)
            sell_count += 1
            self.sym_fills.setdefault(sym, {"buys": 0, "sells": 0})["sells"] += 1
        return sell_count

    def record_stranded_orders(
        self,
        *,
        pending: list[PendingOrder | IntradayPendingOrder],
        day: date,
        latest_closes: dict[str, float],
        session_id: str,
        db_run_id: int,
    ) -> int:
        """Audit pending orders that cannot fill before the backtest window ends."""
        equity = compute_equity(self.cash, self.positions, latest_closes)
        skipped_count = 0
        for order in pending:
            sym = order.intent.normalized_symbol()
            latest_price = latest_closes.get(sym)
            context: dict[str, object] = {"reason_code": "backtest_no_next_bar"}
            if isinstance(order, IntradayPendingOrder):
                context["decision_timestamp"] = order.decision_timestamp.isoformat()
            elif order.decision_day is not None:
                context["decision_day"] = order.decision_day.isoformat()
            self._record_skipped_order(
                intent=order.intent,
                reason_code="backtest_no_next_bar",
                message=(
                    f"Skipped backtest {order.intent.side.value} for {sym}: "
                    "no next bar available before run end."
                ),
                session_id=session_id,
                db_run_id=db_run_id,
                day=day,
                cash=self.cash,
                equity=equity,
                latest_price=latest_price,
                unit_price=latest_price,
                context=context,
            )
            skipped_count += 1
        return skipped_count

    def record_final_snapshot(
        self,
        *,
        session_id: str,
        db_run_id: int,
        recorded_at: datetime,
    ) -> None:
        """Best-effort ADR 0053 snapshot write for a simulation sweep.

        A failure never fails the run, but it is not silent: it is logged and
        recorded on ``self.snapshot_write_error`` so the engine can surface it
        in the run's final metadata (audit trail), instead of being
        indistinguishable from a legitimately empty simulation.
        """
        try:
            record_backtest_equity_snapshot(
                event_store=self.event_store,
                broker=self.sim_broker,
                session_id=session_id,
                strategy_id=self.strategy_id,
                backtest_run_id=db_run_id,
                recorded_at=recorded_at,
            )
        except Exception as exc:  # noqa: BLE001 - snapshot is best-effort
            self.snapshot_write_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Final backtest equity snapshot write failed for %s (session %s): %s",
                self.strategy_id,
                session_id,
                exc,
                exc_info=True,
            )

    def _skip_missing_or_invalid_sell(
        self,
        *,
        order: PendingOrder | IntradayPendingOrder,
        opens: dict[str, float],
        day: date,
        session_id: str,
        db_run_id: int,
        equity: float,
    ) -> bool:
        sym = order.intent.normalized_symbol()
        if sym not in self.positions:
            self._record_skipped_order(
                intent=order.intent,
                reason_code="backtest_sell_without_position",
                message=f"Skipped backtest sell for {sym}: no open position.",
                session_id=session_id,
                db_run_id=db_run_id,
                day=day,
                cash=self.cash,
                equity=equity,
                latest_price=opens.get(sym),
                unit_price=opens.get(sym),
                context={"reason_code": "backtest_sell_without_position"},
            )
            return True
        latest_open = opens.get(sym)
        if latest_open is None:
            self._record_skipped_order(
                intent=order.intent,
                reason_code="backtest_missing_next_open",
                message=f"Skipped backtest sell for {sym}: missing next open price.",
                session_id=session_id,
                db_run_id=db_run_id,
                day=day,
                cash=self.cash,
                equity=equity,
                latest_price=None,
                unit_price=None,
                context={"reason_code": "backtest_missing_next_open"},
            )
            return True
        if not math.isfinite(latest_open) or latest_open <= 0:
            self._record_skipped_order(
                intent=order.intent,
                reason_code="backtest_invalid_next_open",
                message=f"Skipped backtest sell for {sym}: invalid next open price.",
                session_id=session_id,
                db_run_id=db_run_id,
                day=day,
                cash=self.cash,
                equity=equity,
                latest_price=latest_open,
                unit_price=latest_open,
                context={"reason_code": "backtest_invalid_next_open"},
            )
            return True
        return False

    def _buy_skip_reason(
        self,
        *,
        order: PendingOrder | IntradayPendingOrder,
        opens: dict[str, float],
        projected_fill: float | None,
    ) -> str | None:
        sym = order.intent.normalized_symbol()
        if sym in self.positions:
            return "backtest_duplicate_position"
        qty = float(order.intent.quantity)
        if qty <= 0:
            return "non_positive_quantity"
        latest_open = opens.get(sym)
        if latest_open is None:
            return "backtest_missing_next_open"
        if not math.isfinite(latest_open) or latest_open <= 0:
            return "backtest_invalid_next_open"
        effective_projected_fill = projected_fill or latest_open * (1.0 + self.slippage_pct)
        cost = effective_projected_fill * qty + self.commission_per_trade
        if self.cash < cost:
            return "backtest_insufficient_cash"
        return None

    def _record_buy_skip(
        self,
        *,
        order: PendingOrder | IntradayPendingOrder,
        reason_code: str,
        latest_open: float | None,
        projected_fill: float | None,
        day: date,
        session_id: str,
        db_run_id: int,
        equity: float,
    ) -> None:
        sym = order.intent.normalized_symbol()
        message_by_reason = {
            "backtest_duplicate_position": (
                f"Skipped backtest buy for {sym}: position already open."
            ),
            "backtest_missing_next_open": (
                f"Skipped backtest buy for {sym}: missing next open price."
            ),
            "backtest_invalid_next_open": (
                f"Skipped backtest buy for {sym}: invalid next open price."
            ),
            "backtest_insufficient_cash": f"Skipped backtest buy for {sym}: insufficient cash.",
        }
        context: dict[str, object] = {"reason_code": reason_code}
        if reason_code == "backtest_insufficient_cash":
            qty = float(order.intent.quantity)
            projected_cost = (
                None if projected_fill is None else projected_fill * qty + self.commission_per_trade
            )
            context.update({"cash": self.cash, "projected_cost": projected_cost})
        self._record_skipped_order(
            intent=order.intent,
            reason_code=reason_code,
            message=message_by_reason[reason_code],
            session_id=session_id,
            db_run_id=db_run_id,
            day=day,
            cash=self.cash,
            equity=equity,
            latest_price=latest_open,
            unit_price=projected_fill,
            context=context,
        )

    def _record_skipped_order(
        self,
        *,
        intent: TradeIntent,
        reason_code: str,
        message: str,
        session_id: str,
        db_run_id: int,
        day: date,
        cash: float,
        equity: float,
        latest_price: float | None,
        unit_price: float | None,
        context: dict[str, object],
    ) -> None:
        decorated = self._decorate_intent(intent)
        recorded_at = day_to_dt(day)
        estimated_unit_price = 0.0 if unit_price is None else float(unit_price)
        estimated_order_value = estimated_unit_price * decorated.quantity
        explanation_id = self.event_store.append_explanation(
            ExplanationEvent(
                recorded_at=recorded_at,
                decision_type="backtest_skip",
                status="skipped",
                strategy_name=self.strategy_id,
                strategy_stage=self.strategy_stage,
                strategy_config_path=str(self.strategy_config_path),
                config_hash=self.config_hash,
                symbol=decorated.normalized_symbol(),
                side=decorated.side.value,
                quantity=decorated.quantity,
                order_type=decorated.order_type.value,
                time_in_force=decorated.time_in_force.value,
                submitted_by="backtest_engine",
                market_open=True,
                latest_bar_timestamp=recorded_at,
                latest_bar_close=latest_price,
                account_equity=equity,
                account_cash=cash,
                account_portfolio_value=equity,
                account_daily_pnl=0.0,
                risk_allowed=True,
                risk_summary="Backtest order skipped before execution.",
                reason_codes=[reason_code],
                risk_checks=[],
                context={"message": message, **context},
                session_id=session_id,
                backtest_run_id=db_run_id,
            )
        )
        self.event_store.append_trade(
            TradeEvent(
                explanation_id=explanation_id,
                recorded_at=recorded_at,
                status="skipped",
                source="backtest",
                symbol=decorated.normalized_symbol(),
                side=decorated.side.value,
                quantity=decorated.quantity,
                order_type=decorated.order_type.value,
                time_in_force=decorated.time_in_force.value,
                estimated_unit_price=estimated_unit_price,
                estimated_order_value=estimated_order_value,
                strategy_name=self.strategy_id,
                strategy_stage=self.strategy_stage,
                strategy_config_path=str(self.strategy_config_path),
                submitted_by="backtest_engine",
                broker_order_id=None,
                broker_status=None,
                message=message,
                session_id=session_id,
                backtest_run_id=db_run_id,
            )
        )

    def _decorate_intent(
        self,
        intent: TradeIntent,
        *,
        quantity_override: float | None = None,
    ) -> TradeIntent:
        return TradeIntent(
            symbol=intent.symbol,
            side=intent.side,
            quantity=float(quantity_override if quantity_override is not None else intent.quantity),
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            strategy_config_path=self.strategy_config_path,
            submitted_by="backtest_engine",
            expected_stage=self.strategy_stage,
            expected_max_positions=self.max_positions,
            expected_max_position_pct=self.max_position_pct,
            expected_daily_loss_cap_pct=self.daily_loss_cap_pct,
        )


def compute_equity(
    cash: float,
    positions: dict[str, tuple[float, float]],
    latest_closes: dict[str, float],
) -> float:
    market_value = sum(
        qty * latest_closes.get(sym, entry_price) for sym, (qty, entry_price) in positions.items()
    )
    return cash + market_value


def day_to_dt(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)
