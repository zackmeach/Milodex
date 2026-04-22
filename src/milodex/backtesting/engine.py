"""Backtest engine: replays a strategy day-by-day over historical bars.

Simulation rules
----------------
- Bars are fetched for the full range ``(start_date - warmup) … end_date``.
- Only days that appear in the bar data are iterated (weekends / holidays are
  naturally skipped because Alpaca returns no bars for them).
- Fill price is the day's closing price adjusted for slippage:
    BUY  fill = close * (1 + slippage_pct)
    SELL fill = close * (1 - slippage_pct)
- Commission is deducted per executed trade.
- BUY orders are skipped if available cash is insufficient to cover the full
  order cost (no fractional-cash execution).
- Risk layer is intentionally NOT applied — the backtest engine is below the
  risk layer in the architecture.  Risk limits are enforced at promotion time,
  not during simulation.
- Both strategy families are supported:
    - ``regime``: ``bars`` arg = primary symbol bars; ``bars_by_symbol``
      populated from universe.
    - ``meanrev``: ``bars`` arg ignored; ``context.bars_by_symbol`` carries
      all universe bars.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from milodex.core.event_store import BacktestRunEvent, EventStore, ExplanationEvent, TradeEvent
from milodex.data.models import BarSet, Timeframe
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


class BacktestEngine:
    """Replay a loaded strategy over historical bar data.

    Args:
        loaded: Strategy + config produced by :class:`~milodex.strategies.loader.StrategyLoader`.
        data_provider: Market data source.
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
        # Persist equity curve and summary stats in metadata for analytics queries
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

    def _execute(
        self,
        *,
        start_date: date,
        end_date: date,
        run_id: str,
        db_run_id: int,
    ) -> BacktestResult:
        universe = list(self._loaded.context.universe)
        if not universe:
            msg = "Strategy must resolve a non-empty universe before backtesting."
            raise ValueError(msg)

        warmup_start = start_date - timedelta(days=self._warmup_calendar_days())
        all_bars = self._data_provider.get_bars(
            symbols=universe,
            timeframe=Timeframe.DAY_1,
            start=warmup_start,
            end=end_date,
        )

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

        cash = self._initial_equity
        # positions: symbol -> (quantity, entry_price)
        positions: dict[str, tuple[float, float]] = {}
        # entry_state: symbol -> {"entry_price": float, "held_days": int}
        entry_state: dict[str, dict] = {}

        equity_curve: list[tuple[date, float]] = []
        buy_count = 0
        sell_count = 0
        trade_count = 0

        for day in trading_days:
            # Increment held_days for every open position
            for sym in entry_state:
                entry_state[sym]["held_days"] = int(entry_state[sym]["held_days"]) + 1

            bars_by_symbol = _slice_bars_to_day(all_bars, day)
            primary_bars = bars_by_symbol.get(universe[0])
            if primary_bars is None or len(primary_bars) == 0:
                continue

            latest_closes = _latest_closes(bars_by_symbol)
            equity = _compute_equity(cash, positions, latest_closes)

            context = replace(
                self._loaded.context,
                positions={sym: qty for sym, (qty, _) in positions.items()},
                equity=equity,
                bars_by_symbol=bars_by_symbol,
                entry_state=entry_state,
            )

            intents = self._loaded.strategy.evaluate(primary_bars, context)

            # Process SELLs first to free up cash
            for intent in intents:
                if intent.side.value.upper() != "SELL":
                    continue
                sym = intent.symbol.upper()
                if sym not in positions:
                    continue
                qty, _ = positions[sym]
                latest_close = latest_closes.get(sym)
                if latest_close is None or latest_close <= 0:
                    continue
                fill_price = latest_close * (1.0 - self._slippage_pct)
                proceeds = fill_price * qty - self._commission
                entry_p = entry_state.get(sym, {}).get("entry_price", fill_price)
                cash += proceeds
                del positions[sym]
                entry_state.pop(sym, None)

                explanation_id = self._record_explanation(
                    day=day,
                    sym=sym,
                    side="sell",
                    qty=qty,
                    fill_price=fill_price,
                    equity=equity,
                    cash=cash,
                    strategy_name=self._loaded.config.strategy_id,
                    strategy_stage=self._loaded.config.stage,
                    config_path=str(self._loaded.config.path),
                    config_hash=self._loaded.context.config_hash,
                    context={"entry_price": entry_p, "proceeds": proceeds},
                )
                self._event_store.append_trade(
                    TradeEvent(
                        explanation_id=explanation_id,
                        recorded_at=_day_to_dt(day),
                        status="filled",
                        source="backtest",
                        symbol=sym,
                        side="sell",
                        quantity=qty,
                        order_type="market",
                        time_in_force="day",
                        estimated_unit_price=fill_price,
                        estimated_order_value=fill_price * qty,
                        strategy_name=self._loaded.config.strategy_id,
                        strategy_stage=self._loaded.config.stage,
                        strategy_config_path=str(self._loaded.config.path),
                        submitted_by="backtest_engine",
                        broker_order_id=None,
                        broker_status="filled",
                        message=None,
                        session_id=run_id,
                        backtest_run_id=db_run_id,
                    )
                )
                sell_count += 1
                trade_count += 1

            # Recompute equity and closes after sells
            latest_closes = _latest_closes(bars_by_symbol)
            equity = _compute_equity(cash, positions, latest_closes)

            # Process BUYs
            for intent in intents:
                if intent.side.value.upper() != "BUY":
                    continue
                sym = intent.symbol.upper()
                if sym in positions:
                    continue
                qty = intent.quantity
                if qty <= 0:
                    continue
                latest_close = latest_closes.get(sym)
                if latest_close is None or latest_close <= 0:
                    continue
                fill_price = latest_close * (1.0 + self._slippage_pct)
                cost = fill_price * qty + self._commission
                if cash < cost:
                    continue
                cash -= cost
                positions[sym] = (qty, fill_price)
                entry_state[sym] = {"entry_price": fill_price, "held_days": 0}

                explanation_id = self._record_explanation(
                    day=day,
                    sym=sym,
                    side="buy",
                    qty=qty,
                    fill_price=fill_price,
                    equity=equity,
                    cash=cash,
                    strategy_name=self._loaded.config.strategy_id,
                    strategy_stage=self._loaded.config.stage,
                    config_path=str(self._loaded.config.path),
                    config_hash=self._loaded.context.config_hash,
                    context={"cost": cost},
                )
                self._event_store.append_trade(
                    TradeEvent(
                        explanation_id=explanation_id,
                        recorded_at=_day_to_dt(day),
                        status="filled",
                        source="backtest",
                        symbol=sym,
                        side="buy",
                        quantity=qty,
                        order_type="market",
                        time_in_force="day",
                        estimated_unit_price=fill_price,
                        estimated_order_value=fill_price * qty,
                        strategy_name=self._loaded.config.strategy_id,
                        strategy_stage=self._loaded.config.stage,
                        strategy_config_path=str(self._loaded.config.path),
                        submitted_by="backtest_engine",
                        broker_order_id=None,
                        broker_status="filled",
                        message=None,
                        session_id=run_id,
                        backtest_run_id=db_run_id,
                    )
                )
                buy_count += 1
                trade_count += 1

            # Final equity for this day (mark-to-market)
            latest_closes = _latest_closes(bars_by_symbol)
            equity = _compute_equity(cash, positions, latest_closes)
            equity_curve.append((day, equity))

        final_equity = equity_curve[-1][1] if equity_curve else self._initial_equity
        total_return = (final_equity - self._initial_equity) / self._initial_equity

        return BacktestResult(
            run_id=run_id,
            strategy_id=self._loaded.config.strategy_id,
            start_date=start_date,
            end_date=end_date,
            initial_equity=self._initial_equity,
            final_equity=final_equity,
            total_return_pct=total_return * 100.0,
            trade_count=trade_count,
            buy_count=buy_count,
            sell_count=sell_count,
            slippage_pct=self._slippage_pct,
            commission_per_trade=self._commission,
            trading_days=len(trading_days),
            equity_curve=equity_curve,
            db_id=db_run_id,
        )

    def _warmup_calendar_days(self) -> int:
        integer_params = [
            v
            for v in self._loaded.config.parameters.values()
            if isinstance(v, int) and v > 0
        ]
        largest = max(integer_params, default=30)
        return max(365, largest * 3)

    def _record_explanation(
        self,
        *,
        day: date,
        sym: str,
        side: str,
        qty: float,
        fill_price: float,
        equity: float,
        cash: float,
        strategy_name: str,
        strategy_stage: str,
        config_path: str,
        config_hash: str,
        context: dict,
    ) -> int:
        return self._event_store.append_explanation(
            ExplanationEvent(
                recorded_at=_day_to_dt(day),
                decision_type="backtest_fill",
                status="filled",
                strategy_name=strategy_name,
                strategy_stage=strategy_stage,
                strategy_config_path=config_path,
                config_hash=config_hash,
                symbol=sym,
                side=side,
                quantity=qty,
                order_type="market",
                time_in_force="day",
                submitted_by="backtest_engine",
                market_open=False,
                latest_bar_timestamp=_day_to_dt(day),
                latest_bar_close=fill_price,
                account_equity=equity,
                account_cash=cash,
                account_portfolio_value=equity,
                account_daily_pnl=0.0,
                risk_allowed=True,
                risk_summary="Backtest fill — risk layer not applied.",
                reason_codes=[],
                risk_checks=[],
                context={**context, "simulation_day": day.isoformat()},
            )
        )


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
        qty * latest_closes.get(sym, entry_p)
        for sym, (qty, entry_p) in positions.items()
    )
    return cash + market_value


def _day_to_dt(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)
