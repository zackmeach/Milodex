"""SPY/SHY 200-DMA regime strategy."""

from __future__ import annotations

from typing import Any

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


class RegimeSpyShy200DmaStrategy(Strategy):
    """Rotate between SPY and SHY using a moving-average regime filter."""

    family = "regime"
    template = "daily.sma200_rotation"
    parameter_specs = (
        StrategyParameterSpec("ma_filter_length", expected_types=(int,)),
        StrategyParameterSpec("risk_on_symbol", expected_types=(str,)),
        StrategyParameterSpec("risk_off_symbol", expected_types=(str,)),
        StrategyParameterSpec("allocation_pct", expected_types=(int, float)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        dataframe = bars.to_dataframe()
        ma_filter_length = int(_required_parameter(context, "ma_filter_length"))
        if ma_filter_length < 1:
            msg = "ma_filter_length must be >= 1"
            raise ValueError(msg)
        if len(dataframe) < ma_filter_length:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative=(
                        f"only {len(dataframe)} bars available — "
                        f"need {ma_filter_length} for the MA filter"
                    ),
                    triggering_values={"bars_available": len(dataframe)},
                    threshold={"ma_filter_length": ma_filter_length},
                ),
            )

        risk_on_symbol = str(_required_parameter(context, "risk_on_symbol")).upper()
        risk_off_symbol = str(_required_parameter(context, "risk_off_symbol")).upper()
        allocation_pct = float(_required_parameter(context, "allocation_pct"))
        if allocation_pct <= 0:
            msg = "allocation_pct must be > 0"
            raise ValueError(msg)

        latest_close = float(dataframe["close"].iloc[-1])
        moving_average = float(dataframe["close"].tail(ma_filter_length).mean())
        target_symbol = risk_on_symbol if latest_close > moving_average else risk_off_symbol

        normalized_positions = {
            symbol.upper(): float(quantity)
            for symbol, quantity in context.positions.items()
            if float(quantity) > 0
        }
        current_symbols = tuple(normalized_positions)

        triggering_values = {
            "latest_close": latest_close,
            f"ma_{ma_filter_length}": moving_average,
            "target_symbol": target_symbol,
        }
        threshold = {f"ma_{ma_filter_length}": moving_average}

        if len(current_symbols) == 1 and current_symbols[0] == target_symbol:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="regime.hold",
                    narrative=(
                        f"already in target {target_symbol}; "
                        f"latest close {latest_close:.2f} "
                        f"{'above' if latest_close > moving_average else 'at-or-below'} "
                        f"{ma_filter_length}-DMA {moving_average:.2f} → hold"
                    ),
                    triggering_values=triggering_values,
                    threshold=threshold,
                ),
            )

        intents: list[TradeIntent] = []
        for symbol, quantity in normalized_positions.items():
            if symbol != target_symbol:
                intents.append(
                    TradeIntent(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=quantity,
                        order_type=OrderType.MARKET,
                    )
                )

        if target_symbol not in normalized_positions:
            # `allocation_pct` is a fraction of account equity, not a raw
            # share count. Size with the shared utility so `regime` and
            # (future) `meanrev` families agree on the arithmetic.
            # Phase 1 simplification: size using the evaluation bars'
            # latest close, not the target symbol's own quote.
            shares = shares_for_notional_pct(
                equity=context.equity,
                notional_pct=allocation_pct,
                unit_price=latest_close,
            )
            if shares > 0:
                intents.append(
                    TradeIntent(
                        symbol=target_symbol,
                        side=OrderSide.BUY,
                        quantity=float(shares),
                        order_type=OrderType.MARKET,
                    )
                )

        if not intents:
            # Target symbol was resolved, but we can't afford a share and
            # we're not currently holding anything to sell. Still a decision.
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative=(
                        f"target {target_symbol} resolved but "
                        f"equity {context.equity:.2f} cannot afford one share "
                        f"at {latest_close:.2f}"
                    ),
                    triggering_values={**triggering_values, "equity": context.equity},
                    threshold=threshold,
                ),
            )

        relation = "above" if latest_close > moving_average else "at-or-below"
        action = (
            f"rotate to {target_symbol}"
            if target_symbol not in current_symbols
            else f"trim non-target positions to {target_symbol}"
        )
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule="regime.ma_filter_cross",
                narrative=(
                    f"latest close {latest_close:.2f} {relation} "
                    f"{ma_filter_length}-DMA {moving_average:.2f} → {action}"
                ),
                triggering_values=triggering_values,
                threshold=threshold,
            ),
        )


def _required_parameter(context: StrategyContext, name: str) -> Any:
    if name not in context.parameters:
        msg = f"Missing required strategy parameter: {name}"
        raise ValueError(msg)
    return context.parameters[name]
