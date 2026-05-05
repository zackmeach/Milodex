"""Daily NR7 volatility-contraction breakout strategy."""

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

_VALID_RANKING_METRICS = {"nr7_range_ascending"}
_VALID_SIZING_RULES = {"equal_notional", "fixed_notional"}


class BreakoutNr7InsideStrategy(Strategy):
    """NR7 volatility-contraction daily breakout."""

    family = "breakout"
    template = "daily.nr7_inside"
    parameter_specs = (
        StrategyParameterSpec("range_lookback", expected_types=(int,)),
        StrategyParameterSpec("ma_filter_length", expected_types=(int,)),
        StrategyParameterSpec("max_hold_days", expected_types=(int,)),
        StrategyParameterSpec("max_concurrent_positions", expected_types=(int,)),
        StrategyParameterSpec("sizing_rule", expected_types=(str,)),
        StrategyParameterSpec("per_position_notional_pct", expected_types=(int, float)),
        StrategyParameterSpec("ranking_enabled", expected_types=(bool,)),
        StrategyParameterSpec("ranking_metric", expected_types=(str,)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars
        parameters = _validated_parameters(context)
        universe = {symbol.upper() for symbol in context.universe}
        positions = {
            symbol.upper(): float(quantity)
            for symbol, quantity in context.positions.items()
            if float(quantity) > 0 and symbol.upper() in universe
        }
        bars_by_symbol = {
            symbol.upper(): barset for symbol, barset in context.bars_by_symbol.items()
        }

        exits = _exit_intents(positions, bars_by_symbol, context, parameters)
        if exits:
            intent, rule = exits[0]
            return StrategyDecision(
                intents=[intent for intent, _rule in exits],
                reasoning=DecisionReasoning(
                    rule=rule,
                    narrative=f"{rule}: sell {intent.symbol}",
                    triggering_values={"symbol": intent.symbol},
                    threshold=_exit_threshold(rule, parameters),
                ),
            )

        capacity = max(0, parameters["max_concurrent_positions"] - len(positions))
        if capacity <= 0:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative="at capacity",
                    triggering_values={"open_positions": len(positions)},
                    threshold={"max_concurrent_positions": parameters["max_concurrent_positions"]},
                ),
            )

        rejected: list[dict[str, Any]] = []
        candidates = _entry_candidates(
            context.universe, bars_by_symbol, positions, parameters, rejected
        )
        if parameters["ranking_enabled"]:
            candidates = sorted(candidates, key=lambda item: (item[2], item[0]))

        intents: list[TradeIntent] = []
        for symbol, close, _range_value in candidates[:capacity]:
            shares = shares_for_notional_pct(
                equity=context.equity,
                notional_pct=parameters["per_position_notional_pct"],
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
                    rule="breakout.nr7_entry",
                    narrative=f"NR7 contraction entry: buy {', '.join(i.symbol for i in intents)}",
                    triggering_values={"selected_symbol": intents[0].symbol},
                    threshold={"range_lookback": parameters["range_lookback"]},
                    ranking=[
                        {"symbol": symbol, "range": range_value, "latest_close": close}
                        for symbol, close, range_value in candidates
                    ],
                    rejected_alternatives=rejected,
                ),
            )

        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=f"no NR7 candidates qualified — {len(rejected)} rejected",
                triggering_values={"universe_size": len(context.universe)},
                threshold={"range_lookback": parameters["range_lookback"]},
                rejected_alternatives=rejected,
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    params = context.parameters
    range_lookback = int(params["range_lookback"])
    ma_filter_length = int(params["ma_filter_length"])
    max_hold_days = int(params["max_hold_days"])
    max_concurrent_positions = int(params["max_concurrent_positions"])
    sizing_rule = str(params["sizing_rule"])
    ranking_metric = str(params["ranking_metric"])
    per_position_notional_pct = float(params["per_position_notional_pct"])
    if range_lookback < 2:
        raise ValueError("range_lookback must be >= 2")
    if ma_filter_length < 1:
        raise ValueError("ma_filter_length must be >= 1")
    if max_hold_days < 1:
        raise ValueError("max_hold_days must be >= 1")
    if max_concurrent_positions < 1:
        raise ValueError("max_concurrent_positions must be >= 1")
    if sizing_rule not in _VALID_SIZING_RULES:
        raise ValueError(f"sizing_rule must be one of {sorted(_VALID_SIZING_RULES)}")
    if ranking_metric not in _VALID_RANKING_METRICS:
        raise ValueError(f"ranking_metric must be one of {sorted(_VALID_RANKING_METRICS)}")
    if not 0 < per_position_notional_pct <= 1:
        raise ValueError("per_position_notional_pct must be in (0, 1]")
    return {
        "range_lookback": range_lookback,
        "ma_filter_length": ma_filter_length,
        "max_hold_days": max_hold_days,
        "max_concurrent_positions": max_concurrent_positions,
        "sizing_rule": sizing_rule,
        "per_position_notional_pct": per_position_notional_pct,
        "ranking_enabled": bool(params["ranking_enabled"]),
        "ranking_metric": ranking_metric,
    }


def _entry_candidates(
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    open_positions: dict[str, float],
    parameters: dict[str, Any],
    rejected: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    candidates: list[tuple[str, float, float]] = []
    min_required = max(parameters["range_lookback"], parameters["ma_filter_length"])
    for symbol in universe:
        normalized = symbol.upper()
        if normalized in open_positions:
            rejected.append({"symbol": normalized, "reason": "already open"})
            continue
        barset = bars_by_symbol.get(normalized)
        if barset is None:
            rejected.append({"symbol": normalized, "reason": "no bar data"})
            continue
        frame = barset.to_dataframe()
        if len(frame) < min_required:
            rejected.append({"symbol": normalized, "reason": "insufficient history"})
            continue
        latest = frame.iloc[-1]
        ranges = (frame["high"].astype(float) - frame["low"].astype(float)).tail(
            parameters["range_lookback"]
        )
        latest_range = float(ranges.iloc[-1])
        if latest_range > float(ranges.min()):
            rejected.append({"symbol": normalized, "reason": "latest bar is not NR7"})
            continue
        latest_close = float(latest["close"])
        latest_open = float(latest["open"])
        if latest_close <= latest_open:
            rejected.append({"symbol": normalized, "reason": "close not above open"})
            continue
        sma = float(frame["close"].astype(float).tail(parameters["ma_filter_length"]).mean())
        if latest_close <= sma:
            rejected.append({"symbol": normalized, "reason": "close not above SMA"})
            continue
        candidates.append((normalized, latest_close, latest_range))
    return candidates


def _exit_intents(
    positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    parameters: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    results: list[tuple[TradeIntent, str]] = []
    for symbol, quantity in positions.items():
        frame = bars_by_symbol[symbol].to_dataframe()
        if len(frame) < 2:
            continue
        latest_close = float(frame["close"].iloc[-1])
        prior_low = float(frame["low"].iloc[-2])
        state = context.entry_state.get(symbol, {}) if context.entry_state else {}
        entry_price = state.get("entry_price")
        held_days = state.get("held_days")
        stop_price = state.get("stop_price")
        rule: str | None = None
        if isinstance(stop_price, int | float) and latest_close <= float(stop_price):
            rule = "breakout.nr7_initial_stop"
        elif isinstance(held_days, int | float) and int(held_days) >= parameters["max_hold_days"]:
            rule = "breakout.nr7_max_hold"
        elif (
            isinstance(entry_price, int | float)
            and latest_close > float(entry_price)
            and latest_close <= prior_low
        ):
            rule = "breakout.nr7_trailing_low"
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


def _exit_threshold(rule: str, parameters: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "breakout.nr7_max_hold":
        return {"max_hold_days": parameters["max_hold_days"]}
    return {}
