"""Daily turn-of-month seasonality strategy."""

from __future__ import annotations

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
)


class SeasonalityTurnOfMonthStrategy(Strategy):
    """Buy SPY around month-end and exit early in the new month."""

    family = "seasonality"
    template = "daily.turn_of_month"
    parameter_specs = (
        StrategyParameterSpec("target_symbol", expected_types=(str,)),
        # Only offset 0 is supported in v1 (see _is_entry_day).
        StrategyParameterSpec("entry_trading_day_offset", expected_types=(int,), choices=(0,)),
        StrategyParameterSpec("exit_trading_day_of_month", expected_types=(int,)),
        StrategyParameterSpec("allocation_pct", expected_types=(int, float)),
        StrategyParameterSpec("sizing_rule", expected_types=(str,)),
        StrategyParameterSpec("low_cadence_exemption", expected_types=(bool,)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars
        target = str(context.parameters["target_symbol"]).upper()
        barset = context.bars_by_symbol.get(target)
        if barset is None:
            return _no_signal("target symbol has no bars")
        frame = barset.to_dataframe()
        if frame.empty:
            return _no_signal("target symbol has empty bars")
        latest = frame.iloc[-1]
        latest_date = pd.Timestamp(latest["timestamp"]).date()
        positions = {
            symbol.upper(): float(quantity)
            for symbol, quantity in context.positions.items()
            if float(quantity) > 0
        }

        if target in positions and _trading_day_of_month(frame) == int(
            context.parameters["exit_trading_day_of_month"]
        ):
            return StrategyDecision(
                intents=[
                    TradeIntent(
                        symbol=target,
                        side=OrderSide.SELL,
                        quantity=positions[target],
                        order_type=OrderType.MARKET,
                    )
                ],
                reasoning=DecisionReasoning(
                    rule="seasonality.turn_of_month_exit",
                    narrative=f"third trading day exit for {target}",
                    triggering_values={"date": latest_date.isoformat()},
                ),
            )

        if target not in positions and _is_entry_day(
            latest_date, int(context.parameters["entry_trading_day_offset"])
        ):
            close = float(latest["close"])
            shares = shares_for_notional_pct(
                equity=context.equity,
                notional_pct=float(context.parameters["allocation_pct"]),
                unit_price=close,
            )
            if shares > 0:
                return StrategyDecision(
                    intents=[
                        TradeIntent(
                            symbol=target,
                            side=OrderSide.BUY,
                            quantity=float(shares),
                            order_type=OrderType.MARKET,
                        )
                    ],
                    reasoning=DecisionReasoning(
                        rule="seasonality.turn_of_month_entry",
                        narrative=f"last trading day entry for {target}",
                        triggering_values={"date": latest_date.isoformat()},
                    ),
                )

        return _no_signal("not a turn-of-month action day")


def _is_entry_day(latest_date, offset: int) -> bool:
    if offset != 0:
        raise ValueError("Only entry_trading_day_offset=0 is supported in v1")
    next_business_day = pd.Timestamp(latest_date) + pd.offsets.BDay(1)
    return next_business_day.month != latest_date.month


def _trading_day_of_month(frame) -> int:
    timestamps = pd.to_datetime(frame["timestamp"])
    latest = timestamps.iloc[-1]
    same_month = timestamps[
        (timestamps.dt.year == latest.year) & (timestamps.dt.month == latest.month)
    ]
    return len(same_month)


def _no_signal(reason: str) -> StrategyDecision:
    return StrategyDecision(
        intents=[],
        reasoning=DecisionReasoning(
            rule="no_signal",
            narrative=reason,
        ),
    )
