"""Daily lower-Bollinger-band mean-reversion strategy."""

from __future__ import annotations

from typing import Any

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


class MeanrevBbandsLowerbandStrategy(Strategy):
    """Lower Bollinger Band touch in an uptrend."""

    family = "meanrev"
    template = "daily.bbands_lowerband"
    parameter_specs = (
        StrategyParameterSpec("bbands_lookback", expected_types=(int,)),
        StrategyParameterSpec("bbands_stddev", expected_types=(int, float)),
        StrategyParameterSpec("ma_filter_length", expected_types=(int,)),
        StrategyParameterSpec("stop_loss_pct", expected_types=(int, float)),
        StrategyParameterSpec("max_hold_days", expected_types=(int,)),
        StrategyParameterSpec("max_concurrent_positions", expected_types=(int,)),
        StrategyParameterSpec("sizing_rule", expected_types=(str,)),
        StrategyParameterSpec("per_position_notional_pct", expected_types=(int, float)),
        StrategyParameterSpec("ranking_enabled", expected_types=(bool,)),
        StrategyParameterSpec("ranking_metric", expected_types=(str,)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars
        params = _validated(context)
        universe = {symbol.upper() for symbol in context.universe}
        positions = {
            symbol.upper(): float(quantity)
            for symbol, quantity in context.positions.items()
            if float(quantity) > 0 and symbol.upper() in universe
        }
        bars_by_symbol = {
            symbol.upper(): barset for symbol, barset in context.bars_by_symbol.items()
        }

        exits = _exit_intents(positions, bars_by_symbol, context, params)
        if exits:
            intent, rule = exits[0]
            return StrategyDecision(
                intents=[intent for intent, _rule in exits],
                reasoning=DecisionReasoning(
                    rule=rule,
                    narrative=f"{rule}: sell {intent.symbol}",
                    triggering_values={"symbol": intent.symbol},
                ),
            )

        capacity = max(0, params["max_concurrent_positions"] - len(positions))
        rejected: list[dict[str, Any]] = []
        candidates = _entry_candidates(
            context.universe, bars_by_symbol, positions, params, rejected
        )
        if params["ranking_enabled"]:
            candidates = sorted(candidates, key=lambda item: (item[2], item[0]))

        intents: list[TradeIntent] = []
        for symbol, close, _zscore in candidates[:capacity]:
            shares = shares_for_notional_pct(
                equity=context.equity,
                notional_pct=params["per_position_notional_pct"],
                unit_price=close,
            )
            if shares > 0:
                intents.append(
                    TradeIntent(
                        symbol=symbol,
                        side=OrderSide.BUY,
                        quantity=float(shares),
                        order_type=OrderType.MARKET,
                    )
                )
        if intents:
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="meanrev.bbands_entry",
                    narrative=f"lower Bollinger touch: buy {', '.join(i.symbol for i in intents)}",
                    triggering_values={"selected_symbol": intents[0].symbol},
                    threshold={"bbands_stddev": params["bbands_stddev"]},
                    rejected_alternatives=rejected,
                ),
            )
        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=f"no Bollinger candidates qualified — {len(rejected)} rejected",
                triggering_values={"universe_size": len(context.universe)},
                rejected_alternatives=rejected,
            ),
        )


def _validated(context: StrategyContext) -> dict[str, Any]:
    params = context.parameters
    return {
        "bbands_lookback": int(params["bbands_lookback"]),
        "bbands_stddev": float(params["bbands_stddev"]),
        "ma_filter_length": int(params["ma_filter_length"]),
        "stop_loss_pct": float(params["stop_loss_pct"]),
        "max_hold_days": int(params["max_hold_days"]),
        "max_concurrent_positions": int(params["max_concurrent_positions"]),
        "sizing_rule": str(params["sizing_rule"]),
        "per_position_notional_pct": float(params["per_position_notional_pct"]),
        "ranking_enabled": bool(params["ranking_enabled"]),
        "ranking_metric": str(params["ranking_metric"]),
    }


def _entry_candidates(
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    positions: dict[str, float],
    params: dict[str, Any],
    rejected: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    candidates: list[tuple[str, float, float]] = []
    required = max(params["bbands_lookback"], params["ma_filter_length"])
    for symbol in universe:
        normalized = symbol.upper()
        if normalized in positions:
            continue
        barset = bars_by_symbol.get(normalized)
        if barset is None:
            rejected.append({"symbol": normalized, "reason": "no bar data"})
            continue
        closes = barset.to_dataframe()["close"].astype(float)
        if len(closes) < required:
            rejected.append({"symbol": normalized, "reason": "insufficient history"})
            continue
        close = float(closes.iloc[-1])
        sma_filter = float(closes.tail(params["ma_filter_length"]).mean())
        if close <= sma_filter:
            rejected.append({"symbol": normalized, "reason": "close not above SMA"})
            continue
        middle, lower, zscore = _bands(closes, params)
        _ = middle
        if close >= lower:
            rejected.append({"symbol": normalized, "reason": "close not below lower band"})
            continue
        candidates.append((normalized, close, zscore))
    return candidates


def _exit_intents(
    positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    params: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    results: list[tuple[TradeIntent, str]] = []
    for symbol, quantity in positions.items():
        closes = bars_by_symbol[symbol].to_dataframe()["close"].astype(float)
        if len(closes) < params["bbands_lookback"]:
            continue
        close = float(closes.iloc[-1])
        middle, _lower, _zscore = _bands(closes, params)
        state = context.entry_state.get(symbol, {}) if context.entry_state else {}
        entry_price = state.get("entry_price")
        held_days = state.get("held_days")
        rule: str | None = None
        if isinstance(entry_price, int | float) and close <= float(entry_price) * (
            1 - params["stop_loss_pct"]
        ):
            rule = "meanrev.bbands_stop_loss"
        elif isinstance(held_days, int | float) and int(held_days) >= params["max_hold_days"]:
            rule = "meanrev.bbands_max_hold"
        elif close > middle:
            rule = "meanrev.bbands_exit"
        if rule:
            results.append(
                (
                    TradeIntent(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=float(quantity),
                        order_type=OrderType.MARKET,
                    ),
                    rule,
                )
            )
    return results


def _bands(closes: pd.Series, params: dict[str, Any]) -> tuple[float, float, float]:
    window = closes.tail(params["bbands_lookback"])
    middle = float(window.mean())
    stddev = float(window.std(ddof=0))
    lower = middle - params["bbands_stddev"] * stddev
    zscore = 0.0 if stddev == 0 else (float(closes.iloc[-1]) - middle) / stddev
    return middle, lower, zscore
