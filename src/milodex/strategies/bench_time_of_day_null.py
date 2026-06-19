"""Time-of-day null baseline.

Buys at a fixed configured clock offset after the open (``entry_offset_minutes``,
e.g. 180 -> 12:30 ET) and sells at the time-stop bar, every full session, with no
signal. Half-days skipped (matches the other intraday benchmarks).

Distinct from the unconditional-intraday-long benchmark (which anchors entry to the
post-opening-range tick): this null isolates "is the edge just being in the market
at a fixed clock time?" with a TUNABLE entry time decoupled from opening-range
semantics. A research instrument, never promoted past backtest.
"""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies._session_intraday import (
    is_entry_signal_bar,
    is_half_day,
    is_time_stop_bar,
    session_date_et,
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
    single_symbol,
)


class BenchTimeOfDayNullStrategy(Strategy):
    """Buy at ``entry_offset_minutes`` after open, sell at the time-stop. SPY config."""

    family = "benchmark"
    template = "time_of_day_null"
    parameter_specs = (
        StrategyParameterSpec(
            "entry_offset_minutes", expected_types=(int,), minimum=0, maximum=390
        ),
        StrategyParameterSpec(
            "exit_minutes_before_close", expected_types=(int,), minimum=0, maximum=60
        ),
        StrategyParameterSpec(
            "per_position_notional_pct", expected_types=(int, float), exclusive_minimum=0, maximum=1
        ),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars
        params = _validated_parameters(context)

        symbol = single_symbol(context.universe)
        if symbol is None:
            return _no_signal("empty universe")

        barset = context.bars_by_symbol.get(symbol)
        if barset is None or len(barset) == 0:
            return _no_signal(f"no bar data for {symbol}")

        df = barset.to_dataframe()
        latest_ts = df["timestamp"].iloc[-1]
        latest_close = float(df["close"].iloc[-1])
        session_date = session_date_et(latest_ts)

        if is_half_day(session_date):
            open_qty = float(context.positions.get(symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    symbol,
                    open_qty,
                    rule="benchmark.time_of_day_null.half_day_close",
                    narrative=f"half-day {session_date} but {symbol} open — closing defensively",
                )
            return _no_signal(f"half-day session {session_date}; baseline skips half-days")

        open_qty = float(context.positions.get(symbol, 0.0))
        if open_qty > 0:
            if is_time_stop_bar(latest_ts, params["exit_minutes_before_close"]):
                return _exit_decision(
                    symbol,
                    open_qty,
                    rule="benchmark.time_of_day_null.exit",
                    narrative=f"time-stop bar reached → close {symbol}",
                )
            return _no_signal(f"holding {symbol} until time-stop")

        if not is_entry_signal_bar(latest_ts, params["entry_offset_minutes"]):
            return _no_signal("not the configured time-of-day entry bar")

        shares = shares_for_notional_pct(
            equity=context.equity,
            notional_pct=params["per_position_notional_pct"],
            unit_price=latest_close,
        )
        if shares <= 0:
            return _no_signal(
                f"insufficient equity {context.equity:.2f} for one share at {latest_close:.2f}"
            )
        intent = TradeIntent(
            symbol=symbol, side=OrderSide.BUY, quantity=float(shares), order_type=OrderType.MARKET
        )
        return StrategyDecision(
            intents=[intent],
            reasoning=DecisionReasoning(
                rule="benchmark.time_of_day_null.entry",
                narrative=f"time-of-day null: buy {symbol} at +{params['entry_offset_minutes']}min",
                triggering_values={"latest_close": latest_close},
                threshold={"entry_offset_minutes": params["entry_offset_minutes"]},
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    entry_offset_minutes = int(required("entry_offset_minutes"))
    if not 0 <= entry_offset_minutes <= 390:
        msg = f"entry_offset_minutes must be in [0, 390], got {entry_offset_minutes}"
        raise ValueError(msg)

    exit_minutes_before_close = int(required("exit_minutes_before_close"))
    if not 0 <= exit_minutes_before_close <= 60:
        msg = f"exit_minutes_before_close must be in [0, 60], got {exit_minutes_before_close}"
        raise ValueError(msg)

    per_position_notional_pct = float(required("per_position_notional_pct"))
    if not 0 < per_position_notional_pct <= 1:
        msg = f"per_position_notional_pct must be in (0, 1], got {per_position_notional_pct!r}"
        raise ValueError(msg)

    return {
        "entry_offset_minutes": entry_offset_minutes,
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
    }


def _no_signal(narrative: str) -> StrategyDecision:
    return StrategyDecision(
        intents=[], reasoning=DecisionReasoning(rule="no_signal", narrative=narrative)
    )


def _exit_decision(symbol: str, quantity: float, *, rule: str, narrative: str) -> StrategyDecision:
    intent = TradeIntent(
        symbol=symbol, side=OrderSide.SELL, quantity=float(quantity), order_type=OrderType.MARKET
    )
    return StrategyDecision(
        intents=[intent], reasoning=DecisionReasoning(rule=rule, narrative=narrative)
    )
