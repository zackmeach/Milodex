"""Unconditional intraday long benchmark on SPY.

Implements the ``benchmark`` family's ``unconditional_intraday_long`` template:

- Same tempo/universe/risk/slippage shape as the ORB strategy
- Rule: BUY at the first bar after the opening range (10:00 ET with
  ``opening_range_minutes=30``); SELL at the time-stop bar (15:55 ET with
  ``exit_minutes_before_close=5``). Every full session. No conditions.
- Half-day sessions: skipped, exactly like ORB — keeps the comparison fair.

Purpose: this isn't a real strategy. It's the floor any intraday signal
must beat. Long-only on an asset with secular drift will show positive
P&L from random entries. The honest question for any intraday strategy
is "does it beat unconditional intraday long after realistic slippage?"
— this template provides the answer.

See ``docs/STRATEGY_BANK.md`` for the rationale and the manual
promotion-gate rubric that pairs ORB with this benchmark.
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
)


class BenchUnconditionalIntradayLongStrategy(Strategy):
    """Buy at 10:00 ET, sell at 15:55 ET, every full session. SPY only."""

    family = "benchmark"
    template = "unconditional_intraday_long"
    parameter_specs = (
        StrategyParameterSpec(
            "opening_range_minutes", expected_types=(int,), minimum=5, maximum=120
        ),
        StrategyParameterSpec(
            "exit_minutes_before_close", expected_types=(int,), minimum=0, maximum=60
        ),
        StrategyParameterSpec(
            "per_position_notional_pct",
            expected_types=(int, float),
            exclusive_minimum=0,
            maximum=1,
        ),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars

        parameters = _validated_parameters(context)

        universe_symbols = sorted({symbol.upper() for symbol in context.universe})
        if not universe_symbols:
            return _no_signal("empty universe")
        primary_symbol = universe_symbols[0]

        barset = context.bars_by_symbol.get(primary_symbol)
        if barset is None or len(barset) == 0:
            return _no_signal(f"no bar data for {primary_symbol}")

        df = barset.to_dataframe()
        latest_ts = df["timestamp"].iloc[-1]
        latest_close = float(df["close"].iloc[-1])
        session_date = session_date_et(latest_ts)

        if is_half_day(session_date):
            # Defensive: close any unexpected position on a half-day (matches ORB).
            open_qty = float(context.positions.get(primary_symbol, 0.0))
            if open_qty > 0:
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="benchmark.intraday_long.half_day_close",
                    narrative=(
                        f"half-day session {session_date} but {primary_symbol} open — "
                        f"closing defensively"
                    ),
                )
            return _no_signal(f"half-day session {session_date}; benchmark skips half-days")

        open_qty = float(context.positions.get(primary_symbol, 0.0))
        has_position = open_qty > 0

        if has_position:
            if is_time_stop_bar(latest_ts, parameters["exit_minutes_before_close"]):
                return _exit_decision(
                    primary_symbol,
                    open_qty,
                    rule="benchmark.intraday_long.exit",
                    narrative=(
                        f"time-stop bar reached "
                        f"({parameters['exit_minutes_before_close']}min before close) → "
                        f"close {primary_symbol}"
                    ),
                )
            return _no_signal(f"holding {primary_symbol} until time-stop")

        # No position — fire BUY exactly at the post-opening-range bar.
        if not is_entry_signal_bar(latest_ts, parameters["opening_range_minutes"]):
            return _no_signal(
                "not the entry signal bar (waiting for post-opening-range entry tick)"
            )

        shares = shares_for_notional_pct(
            equity=context.equity,
            notional_pct=parameters["per_position_notional_pct"],
            unit_price=latest_close,
        )
        if shares <= 0:
            return _no_signal(
                f"insufficient equity {context.equity:.2f} for one share at {latest_close:.2f}"
            )
        intent = TradeIntent(
            symbol=primary_symbol,
            side=OrderSide.BUY,
            quantity=float(shares),
            order_type=OrderType.MARKET,
        )
        return StrategyDecision(
            intents=[intent],
            reasoning=DecisionReasoning(
                rule="benchmark.intraday_long.entry",
                narrative=(
                    f"unconditional intraday long: buy {primary_symbol} at post-opening-range tick"
                ),
                triggering_values={"latest_close": latest_close},
                threshold={"opening_range_minutes": parameters["opening_range_minutes"]},
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    opening_range_minutes = int(required("opening_range_minutes"))
    if opening_range_minutes < 5 or opening_range_minutes > 120:
        msg = f"opening_range_minutes must be in [5, 120], got {opening_range_minutes}"
        raise ValueError(msg)

    exit_minutes_before_close = int(required("exit_minutes_before_close"))
    if exit_minutes_before_close < 0 or exit_minutes_before_close > 60:
        msg = f"exit_minutes_before_close must be in [0, 60], got {exit_minutes_before_close}"
        raise ValueError(msg)

    per_position_notional_pct = float(required("per_position_notional_pct"))
    if not 0 < per_position_notional_pct <= 1:
        msg = f"per_position_notional_pct must be in (0, 1], got {per_position_notional_pct!r}"
        raise ValueError(msg)

    return {
        "opening_range_minutes": opening_range_minutes,
        "exit_minutes_before_close": exit_minutes_before_close,
        "per_position_notional_pct": per_position_notional_pct,
    }


def _no_signal(narrative: str) -> StrategyDecision:
    return StrategyDecision(
        intents=[],
        reasoning=DecisionReasoning(rule="no_signal", narrative=narrative),
    )


def _exit_decision(
    symbol: str,
    quantity: float,
    *,
    rule: str,
    narrative: str,
) -> StrategyDecision:
    intent = TradeIntent(
        symbol=symbol,
        side=OrderSide.SELL,
        quantity=float(quantity),
        order_type=OrderType.MARKET,
    )
    return StrategyDecision(
        intents=[intent],
        reasoning=DecisionReasoning(rule=rule, narrative=narrative),
    )
