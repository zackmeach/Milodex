"""Backtest engine: replays a strategy day-by-day over historical bars.

The engine rides the **same** execution path the live paper runner uses:
every ``TradeIntent`` emitted by ``Strategy.evaluate()`` is submitted
through :class:`milodex.execution.service.ExecutionService`. Two
dependencies are swapped for the backtest:

- :class:`milodex.broker.simulated.SimulatedBroker` fills at the
  simulation day's close (with slippage and commission applied).
- :class:`milodex.risk.NullRiskEvaluator` makes risk evaluation a no-op.
  Backtesting is intentionally below the risk layer per ``CLAUDE.md``;
  the bypass is **declared** (injected), not implicit.

The engine still owns cash / position / equity bookkeeping — it
snapshot-injects the broker's reported account and positions at the
top of each day's loop so that intents submitted through
``ExecutionService`` observe consistent state. This is the
data-layer counterpart to the architectural "same strategy code runs
historical and live with no branches" guarantee.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from milodex.broker.models import AccountInfo, OrderSide, Position
from milodex.broker.simulated import SimulatedBroker
from milodex.core.event_store import BacktestRunEvent, EventStore
from milodex.data.models import BarSet, Timeframe
from milodex.data.simulated import SimulatedDataProvider
from milodex.execution.models import ExecutionStatus, TradeIntent
from milodex.execution.service import ExecutionService
from milodex.execution.state import KillSwitchStateStore
from milodex.risk import NullRiskEvaluator
from milodex.strategies.loader import LoadedStrategy

if TYPE_CHECKING:
    from milodex.data.provider import DataProvider


@dataclass
class BacktestResult:
    """Summary returned by :meth:`BacktestEngine.run`."""

    run_id: str
    strategy_id: str
    start_date: date
    end_date: date
    initial_equity: float
    final_equity: float
    total_return_pct: float
    trade_count: int
    buy_count: int
    sell_count: int
    slippage_pct: float
    commission_per_trade: float
    trading_days: int
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    db_id: int | None = None


@dataclass
class _SimulationOutput:
    """Raw outputs from a single simulation sweep over a list of trading days.

    Intentionally narrower than :class:`BacktestResult`: it carries only the
    window-local bookkeeping so the walk-forward runner can stitch multiple
    sweeps together without the engine pre-computing a full ``BacktestResult``
    per window.
    """

    equity_curve: list[tuple[date, float]]
    trade_count: int
    buy_count: int
    sell_count: int
    final_equity: float


class BacktestEngine:
    """Replay a loaded strategy over historical bar data.

    Args:
        loaded: Strategy + config produced by :class:`~milodex.strategies.loader.StrategyLoader`.
        data_provider: Market data source (used only to prefetch bars for the run window).
        event_store: Persistent ledger for backtest runs and simulated trades.
        initial_equity: Starting simulated account equity in USD.
        slippage_pct: Per-trade fill slippage as a fraction (e.g. ``0.001`` = 0.1%).
            Defaults to the value in the strategy config's ``backtest.slippage_pct``.
        commission_per_trade: Fixed commission deducted per executed trade in USD.
            Defaults to the value in the strategy config's ``backtest.commission_per_trade``.
    """

    def __init__(
        self,
        *,
        loaded: LoadedStrategy,
        data_provider: DataProvider,
        event_store: EventStore,
        initial_equity: float = 100_000.0,
        slippage_pct: float | None = None,
        commission_per_trade: float | None = None,
    ) -> None:
        self._loaded = loaded
        self._data_provider = data_provider
        self._event_store = event_store
        self._initial_equity = initial_equity
        self._slippage_pct = (
            slippage_pct
            if slippage_pct is not None
            else float(loaded.config.backtest.get("slippage_pct", 0.001))
        )
        self._commission = (
            commission_per_trade
            if commission_per_trade is not None
            else float(loaded.config.backtest.get("commission_per_trade", 0.0))
        )

    def run(
        self,
        start_date: date,
        end_date: date,
        *,
        run_id: str | None = None,
    ) -> BacktestResult:
        """Run the backtest and return a :class:`BacktestResult`.

        Writes ``backtest_runs``, ``explanations``, and ``trades`` rows to the
        event store as it executes.  The status is set to ``'running'`` at the
        start and updated to ``'completed'`` or ``'failed'`` at the end.
        """
        if end_date < start_date:
            msg = "end_date must be on or after start_date"
            raise ValueError(msg)

        effective_run_id = run_id or str(uuid.uuid4())
        started_at = datetime.now(tz=UTC)

        db_run_id = self._event_store.append_backtest_run(
            BacktestRunEvent(
                run_id=effective_run_id,
                strategy_id=self._loaded.config.strategy_id,
                config_path=str(self._loaded.config.path),
                config_hash=self._loaded.context.config_hash,
                start_date=datetime.combine(start_date, datetime.min.time(), tzinfo=UTC),
                end_date=datetime.combine(end_date, datetime.min.time(), tzinfo=UTC),
                started_at=started_at,
                status="running",
                slippage_pct=self._slippage_pct,
                commission_per_trade=self._commission,
                metadata={},
            )
        )

        try:
            result = self._execute(
                start_date=start_date,
                end_date=end_date,
                run_id=effective_run_id,
                db_run_id=db_run_id,
            )
        except Exception:
            self._event_store.update_backtest_run_status(
                effective_run_id,
                status="failed",
                ended_at=datetime.now(tz=UTC),
            )
            raise

        self._event_store.update_backtest_run_status(
            effective_run_id,
            status="completed",
            ended_at=datetime.now(tz=UTC),
        )
        self._event_store.update_backtest_run_metadata(
            effective_run_id,
            metadata={
                "initial_equity": result.initial_equity,
                "final_equity": result.final_equity,
                "total_return_pct": result.total_return_pct,
                "trade_count": result.trade_count,
                "trading_days": result.trading_days,
                "equity_curve": [[d.isoformat(), v] for d, v in result.equity_curve],
            },
        )
        return result

    # ------------------------------------------------------------------
    # Private execution core
    # ------------------------------------------------------------------

    def prefetch_bars(self, start_date: date, end_date: date) -> dict[str, BarSet]:
        """Fetch bars for the universe over ``[start_date - warmup, end_date]``.

        Exposed so the walk-forward runner can fetch once and re-use across
        windows, avoiding N×warmup fetches for N windows.
        """
        universe = list(self._loaded.context.universe)
        if not universe:
            msg = "Strategy must resolve a non-empty universe before backtesting."
            raise ValueError(msg)
        warmup_start = start_date - timedelta(days=self._warmup_calendar_days())
        return self._data_provider.get_bars(
            symbols=universe,
            timeframe=Timeframe.DAY_1,
            start=warmup_start,
            end=end_date,
        )

    def simulate_window(
        self,
        *,
        all_bars: dict[str, BarSet],
        trading_days: list[date],
        db_run_id: int,
        session_id: str,
        initial_equity: float | None = None,
    ) -> _SimulationOutput:
        """Run the simulation loop on ``trading_days`` using ``all_bars``.

        Intended for the walk-forward runner, which owns bar prefetch and
        window splitting. Each call resets equity, positions, and entry-state
        to a fresh start; persistence (trades, explanations) flows through the
        caller-provided ``db_run_id`` so all windows from one walk-forward
        invocation land under the same parent ``BacktestRunEvent``.
        ``session_id`` distinguishes windows within a single parent run.
        """
        equity = initial_equity if initial_equity is not None else self._initial_equity
        return self._simulate(
            all_bars=all_bars,
            trading_days=trading_days,
            db_run_id=db_run_id,
            session_id=session_id,
            initial_equity=equity,
        )

    def _execute(
        self,
        *,
        start_date: date,
        end_date: date,
        run_id: str,
        db_run_id: int,
    ) -> BacktestResult:
        all_bars = self.prefetch_bars(start_date, end_date)

        trading_days = _trading_days_in_range(all_bars, start_date, end_date)
        if not trading_days:
            return BacktestResult(
                run_id=run_id,
                strategy_id=self._loaded.config.strategy_id,
                start_date=start_date,
                end_date=end_date,
                initial_equity=self._initial_equity,
                final_equity=self._initial_equity,
                total_return_pct=0.0,
                trade_count=0,
                buy_count=0,
                sell_count=0,
                slippage_pct=self._slippage_pct,
                commission_per_trade=self._commission,
                trading_days=0,
                equity_curve=[],
                db_id=db_run_id,
            )

        output = self._simulate(
            all_bars=all_bars,
            trading_days=trading_days,
            db_run_id=db_run_id,
            session_id=run_id,
            initial_equity=self._initial_equity,
        )
        total_return = (output.final_equity - self._initial_equity) / self._initial_equity
        return BacktestResult(
            run_id=run_id,
            strategy_id=self._loaded.config.strategy_id,
            start_date=start_date,
            end_date=end_date,
            initial_equity=self._initial_equity,
            final_equity=output.final_equity,
            total_return_pct=total_return * 100.0,
            trade_count=output.trade_count,
            buy_count=output.buy_count,
            sell_count=output.sell_count,
            slippage_pct=self._slippage_pct,
            commission_per_trade=self._commission,
            trading_days=len(trading_days),
            equity_curve=output.equity_curve,
            db_id=db_run_id,
        )

    def _simulate(
        self,
        *,
        all_bars: dict[str, BarSet],
        trading_days: list[date],
        db_run_id: int,
        session_id: str,
        initial_equity: float,
    ) -> _SimulationOutput:
        universe = list(self._loaded.context.universe)
        if not universe:
            msg = "Strategy must resolve a non-empty universe before backtesting."
            raise ValueError(msg)
        if not trading_days:
            return _SimulationOutput(
                equity_curve=[],
                trade_count=0,
                buy_count=0,
                sell_count=0,
                final_equity=initial_equity,
            )

        sim_broker = SimulatedBroker(
            slippage_pct=self._slippage_pct,
            commission_per_trade=self._commission,
        )
        sim_data_provider = SimulatedDataProvider(all_bars)
        execution_service = ExecutionService(
            broker_client=sim_broker,
            data_provider=sim_data_provider,
            kill_switch_store=KillSwitchStateStore(event_store=self._event_store),
            risk_evaluator=NullRiskEvaluator(),
            event_store=self._event_store,
        )

        cash = initial_equity
        positions: dict[str, tuple[float, float]] = {}
        entry_state: dict[str, dict] = {}

        equity_curve: list[tuple[date, float]] = []
        buy_count = 0
        sell_count = 0
        trade_count = 0

        for day in trading_days:
            for sym in entry_state:
                entry_state[sym]["held_days"] = int(entry_state[sym]["held_days"]) + 1

            bars_by_symbol = _slice_bars_to_day(all_bars, day)
            primary_bars = bars_by_symbol.get(universe[0])
            if primary_bars is None or len(primary_bars) == 0:
                continue

            latest_closes = _latest_closes(bars_by_symbol)
            equity = _compute_equity(cash, positions, latest_closes)

            self._sync_broker_state(
                sim_broker=sim_broker,
                sim_data_provider=sim_data_provider,
                day=day,
                closes=latest_closes,
                cash=cash,
                equity=equity,
                positions=positions,
            )

            context = replace(
                self._loaded.context,
                positions={sym: qty for sym, (qty, _) in positions.items()},
                equity=equity,
                bars_by_symbol=bars_by_symbol,
                entry_state=entry_state,
            )

            decision = self._loaded.strategy.evaluate(primary_bars, context)
            intents = decision.intents

            if not intents:
                latest_bar = primary_bars.latest()
                execution_service.record_no_action(
                    strategy_name=self._loaded.config.strategy_id,
                    strategy_stage=self._loaded.config.stage,
                    strategy_config_path=self._loaded.config.path,
                    config_hash=self._loaded.context.config_hash,
                    symbol=universe[0],
                    latest_bar_timestamp=latest_bar.timestamp,
                    latest_bar_close=latest_bar.close,
                    session_id=session_id,
                    reasoning=decision.reasoning,
                    submitted_by="backtest_engine",
                )

            # Process SELLs first to free up cash for BUYs on the same day.
            for intent in intents:
                if intent.side is not OrderSide.SELL:
                    continue
                sym = intent.symbol.upper()
                if sym not in positions:
                    continue
                if latest_closes.get(sym, 0.0) <= 0:
                    continue

                qty, _ = positions[sym]
                decorated = self._decorate_intent(intent, quantity_override=qty)
                result = execution_service.submit_backtest(
                    decorated,
                    session_id=session_id,
                    backtest_run_id=db_run_id,
                    reasoning=decision.reasoning,
                )
                if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                    continue

                fill_price = float(result.order.filled_avg_price or 0.0)
                proceeds = fill_price * qty - self._commission
                cash += proceeds
                del positions[sym]
                entry_state.pop(sym, None)
                sell_count += 1
                trade_count += 1

            # Re-sync after sells so BUY affordability checks reflect freed cash.
            latest_closes = _latest_closes(bars_by_symbol)
            equity = _compute_equity(cash, positions, latest_closes)
            self._sync_broker_state(
                sim_broker=sim_broker,
                sim_data_provider=sim_data_provider,
                day=day,
                closes=latest_closes,
                cash=cash,
                equity=equity,
                positions=positions,
            )

            for intent in intents:
                if intent.side is not OrderSide.BUY:
                    continue
                sym = intent.symbol.upper()
                if sym in positions:
                    continue
                qty = float(intent.quantity)
                if qty <= 0:
                    continue
                latest_close = latest_closes.get(sym)
                if latest_close is None or latest_close <= 0:
                    continue
                projected_fill = latest_close * (1.0 + self._slippage_pct)
                cost = projected_fill * qty + self._commission
                if cash < cost:
                    continue

                decorated = self._decorate_intent(intent)
                result = execution_service.submit_backtest(
                    decorated,
                    session_id=session_id,
                    backtest_run_id=db_run_id,
                    reasoning=decision.reasoning,
                )
                if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                    continue

                fill_price = float(result.order.filled_avg_price or 0.0)
                realized_cost = fill_price * qty + self._commission
                cash -= realized_cost
                positions[sym] = (qty, fill_price)
                entry_state[sym] = {"entry_price": fill_price, "held_days": 0}
                buy_count += 1
                trade_count += 1

            # Mark-to-market at end of day.
            latest_closes = _latest_closes(bars_by_symbol)
            equity = _compute_equity(cash, positions, latest_closes)
            equity_curve.append((day, equity))

        final_equity = equity_curve[-1][1] if equity_curve else initial_equity
        return _SimulationOutput(
            equity_curve=equity_curve,
            trade_count=trade_count,
            buy_count=buy_count,
            sell_count=sell_count,
            final_equity=final_equity,
        )

    def _decorate_intent(
        self, intent: TradeIntent, *, quantity_override: float | None = None
    ) -> TradeIntent:
        """Attach strategy + submitter metadata to a bare strategy intent.

        Mirrors what :class:`milodex.strategies.runner.StrategyRunner`
        does for the paper path, so the recorded trade rows carry the
        same provenance whether they came from a live session or a
        backtest replay.
        """
        return TradeIntent(
            symbol=intent.symbol,
            side=intent.side,
            quantity=float(quantity_override if quantity_override is not None else intent.quantity),
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            strategy_config_path=self._loaded.config.path,
            submitted_by="backtest_engine",
        )

    def _sync_broker_state(
        self,
        *,
        sim_broker: SimulatedBroker,
        sim_data_provider: SimulatedDataProvider,
        day: date,
        closes: dict[str, float],
        cash: float,
        equity: float,
        positions: dict[str, tuple[float, float]],
    ) -> None:
        day_dt = _day_to_dt(day)
        sim_broker.set_simulation_day(day=day_dt, closes=closes)
        sim_data_provider.set_simulation_day(day)
        sim_broker.update_account(
            AccountInfo(
                equity=equity,
                cash=cash,
                buying_power=cash,
                portfolio_value=equity,
                daily_pnl=0.0,
            )
        )
        reported_positions = []
        for sym, (qty, entry_price) in positions.items():
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
        sim_broker.set_positions(reported_positions)

    def _warmup_calendar_days(self) -> int:
        integer_params = [
            v for v in self._loaded.config.parameters.values() if isinstance(v, int) and v > 0
        ]
        largest = max(integer_params, default=30)
        return max(365, largest * 3)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _trading_days_in_range(
    all_bars: dict[str, BarSet], start_date: date, end_date: date
) -> list[date]:
    """Return sorted unique trading days present in bar data within [start, end]."""
    days: set[date] = set()
    for barset in all_bars.values():
        df = barset.to_dataframe()
        if df.empty or "timestamp" not in df.columns:
            continue
        timestamps = pd.to_datetime(df["timestamp"], utc=True)
        for ts in timestamps:
            d = ts.date()
            if start_date <= d <= end_date:
                days.add(d)
    return sorted(days)


def _slice_bars_to_day(all_bars: dict[str, BarSet], day: date) -> dict[str, BarSet]:
    """Return a dict of BarSets each truncated to bars on or before ``day``."""
    result: dict[str, BarSet] = {}
    for sym, barset in all_bars.items():
        df = barset.to_dataframe()
        if df.empty:
            continue
        timestamps = pd.to_datetime(df["timestamp"], utc=True)
        mask = timestamps.dt.date <= day
        sliced = df.loc[mask]
        if not sliced.empty:
            result[sym] = BarSet(sliced.reset_index(drop=True))
    return result


def _latest_closes(bars_by_symbol: dict[str, BarSet]) -> dict[str, float]:
    closes: dict[str, float] = {}
    for sym, barset in bars_by_symbol.items():
        df = barset.to_dataframe()
        if not df.empty:
            closes[sym] = float(df["close"].iloc[-1])
    return closes


def _compute_equity(
    cash: float,
    positions: dict[str, tuple[float, float]],
    latest_closes: dict[str, float],
) -> float:
    market_value = sum(
        qty * latest_closes.get(sym, entry_p) for sym, (qty, entry_p) in positions.items()
    )
    return cash + market_value


def _day_to_dt(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)
