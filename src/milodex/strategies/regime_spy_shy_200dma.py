"""SPY/SHY 200-DMA regime strategy."""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies.base import Strategy, StrategyContext, StrategyParameterSpec


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

    def evaluate(self, bars: BarSet, context: StrategyContext) -> list[TradeIntent]:
        dataframe = bars.to_dataframe()
        ma_filter_length = int(_required_parameter(context, "ma_filter_length"))
        if ma_filter_length < 1:
            msg = "ma_filter_length must be >= 1"
            raise ValueError(msg)
        if len(dataframe) < ma_filter_length:
            return []

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

        if len(current_symbols) == 1 and current_symbols[0] == target_symbol:
            return []

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

        return intents


def _required_parameter(context: StrategyContext, name: str) -> Any:
    if name not in context.parameters:
        msg = f"Missing required strategy parameter: {name}"
        raise ValueError(msg)
    return context.parameters[name]
