"""Daily 52-week high proximity momentum strategy."""

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

_VALID_RANKING_METRICS = {"proximity_then_return"}
_VALID_SIZING_RULES = {"equal_notional", "fixed_notional"}


class Momentum52wHighProximityStrategy(Strategy):
    """Long-only daily momentum near the trailing 52-week high."""

    family = "momentum"
    template = "daily.52w_high_proximity"
    parameter_specs = (
        StrategyParameterSpec("lookback_days", expected_types=(int,), minimum=2),
        StrategyParameterSpec(
            "proximity_threshold", expected_types=(int, float), exclusive_minimum=0, maximum=1
        ),
        StrategyParameterSpec("sma_exit_length", expected_types=(int,), minimum=1),
        StrategyParameterSpec("max_hold_days", expected_types=(int,), minimum=1),
        StrategyParameterSpec(
            "stop_loss_pct",
            expected_types=(int, float),
            exclusive_minimum=0,
            exclusive_maximum=1,
        ),
        StrategyParameterSpec("max_concurrent_positions", expected_types=(int,), minimum=1),
        StrategyParameterSpec(
            "sizing_rule", expected_types=(str,), choices=tuple(sorted(_VALID_SIZING_RULES))
        ),
        StrategyParameterSpec(
            "per_position_notional_pct",
            expected_types=(int, float),
            exclusive_minimum=0,
            maximum=1,
        ),
        StrategyParameterSpec("ranking_enabled", expected_types=(bool,)),
        StrategyParameterSpec(
            "ranking_metric", expected_types=(str,), choices=tuple(sorted(_VALID_RANKING_METRICS))
        ),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars
        parameters = _validated_parameters(context)
        universe = {symbol.upper() for symbol in context.universe}
        open_positions = {
            symbol.upper(): float(quantity)
            for symbol, quantity in context.positions.items()
            if float(quantity) > 0 and symbol.upper() in universe
        }
        bars_by_symbol = {
            symbol.upper(): barset for symbol, barset in context.bars_by_symbol.items()
        }

        exits = _exit_intents(open_positions, bars_by_symbol, context, parameters)
        if exits:
            first_intent, first_rule = exits[0]
            return StrategyDecision(
                intents=[intent for intent, _rule in exits],
                reasoning=DecisionReasoning(
                    rule=first_rule,
                    narrative=_exit_narrative(first_rule, first_intent.symbol, parameters),
                    triggering_values={"symbol": first_intent.symbol},
                    threshold=_exit_threshold(first_rule, parameters),
                ),
            )

        capacity = max(0, parameters["max_concurrent_positions"] - len(open_positions))
        if capacity <= 0:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative="at capacity",
                    triggering_values={"open_positions": len(open_positions)},
                    threshold={"max_concurrent_positions": parameters["max_concurrent_positions"]},
                ),
            )

        rejected: list[dict[str, Any]] = []
        candidates = _entry_candidates(
            context.universe,
            bars_by_symbol,
            open_positions,
            parameters,
            rejected,
        )
        if parameters["ranking_enabled"]:
            candidates.sort(
                key=lambda item: (
                    -item["proximity"],
                    -item["latest_return"],
                    item["symbol"],
                )
            )

        selected = candidates[:capacity]
        for candidate in candidates[capacity:]:
            rejected.append(
                {
                    "symbol": candidate["symbol"],
                    "reason": (
                        f"capacity {capacity} full; ranked below selection "
                        f"(proximity={candidate['proximity']:.4f})"
                    ),
                }
            )

        intents: list[TradeIntent] = []
        for candidate in selected:
            shares = shares_for_notional_pct(
                equity=context.equity,
                notional_pct=parameters["per_position_notional_pct"],
                unit_price=candidate["latest_close"],
            )
            if shares <= 0:
                rejected.append(
                    {
                        "symbol": candidate["symbol"],
                        "reason": (
                            f"equity {context.equity:.2f} cannot afford one share "
                            f"at {candidate['latest_close']:.2f} for "
                            f"{parameters['per_position_notional_pct']:.2%} allocation"
                        ),
                    }
                )
                continue
            intents.append(
                TradeIntent(
                    symbol=candidate["symbol"],
                    side=OrderSide.BUY,
                    quantity=float(shares),
                    order_type=OrderType.MARKET,
                )
            )

        ranking_payload = [
            {
                "symbol": candidate["symbol"],
                "proximity": candidate["proximity"],
                "latest_return": candidate["latest_return"],
                "latest_close": candidate["latest_close"],
            }
            for candidate in candidates
        ]

        if intents:
            primary = selected[0]
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="momentum.52w_high_entry",
                    narrative=(
                        f"{primary['symbol']} close is {primary['proximity']:.2%} of "
                        "the trailing 52-week high and above prior close"
                    ),
                    triggering_values={
                        "selected_symbol": primary["symbol"],
                        "selected_proximity": primary["proximity"],
                        "selected_latest_return": primary["latest_return"],
                    },
                    threshold={"proximity_threshold": parameters["proximity_threshold"]},
                    ranking=ranking_payload,
                    rejected_alternatives=rejected,
                ),
            )

        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=(
                    f"no 52-week high proximity candidates qualified - {len(rejected)} rejected"
                ),
                triggering_values={"universe_size": len(context.universe)},
                threshold={"proximity_threshold": parameters["proximity_threshold"]},
                rejected_alternatives=rejected,
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    params = context.parameters
    lookback_days = int(params["lookback_days"])
    proximity_threshold = float(params["proximity_threshold"])
    sma_exit_length = int(params["sma_exit_length"])
    max_hold_days = int(params["max_hold_days"])
    stop_loss_pct = float(params["stop_loss_pct"])
    max_concurrent_positions = int(params["max_concurrent_positions"])
    sizing_rule = str(params["sizing_rule"])
    ranking_metric = str(params["ranking_metric"])
    per_position_notional_pct = float(params["per_position_notional_pct"])
    if lookback_days < 2:
        raise ValueError("lookback_days must be >= 2")
    if not 0 < proximity_threshold <= 1:
        raise ValueError("proximity_threshold must be in (0, 1]")
    if sma_exit_length < 1:
        raise ValueError("sma_exit_length must be >= 1")
    if max_hold_days < 1:
        raise ValueError("max_hold_days must be >= 1")
    if not 0 < stop_loss_pct < 1:
        raise ValueError("stop_loss_pct must be in (0, 1)")
    if max_concurrent_positions < 1:
        raise ValueError("max_concurrent_positions must be >= 1")
    if sizing_rule not in _VALID_SIZING_RULES:
        raise ValueError(f"sizing_rule must be one of {sorted(_VALID_SIZING_RULES)}")
    if ranking_metric not in _VALID_RANKING_METRICS:
        raise ValueError(f"ranking_metric must be one of {sorted(_VALID_RANKING_METRICS)}")
    if not 0 < per_position_notional_pct <= 1:
        raise ValueError("per_position_notional_pct must be in (0, 1]")
    return {
        "lookback_days": lookback_days,
        "proximity_threshold": proximity_threshold,
        "sma_exit_length": sma_exit_length,
        "max_hold_days": max_hold_days,
        "stop_loss_pct": stop_loss_pct,
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
) -> list[dict[str, float | str]]:
    candidates: list[dict[str, float | str]] = []
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
        if len(frame) < parameters["lookback_days"]:
            rejected.append({"symbol": normalized, "reason": "insufficient history"})
            continue
        highs = frame["high"].astype(float).tail(parameters["lookback_days"])
        annual_high = float(highs.max())
        if annual_high <= 0:
            rejected.append({"symbol": normalized, "reason": "invalid annual high"})
            continue
        closes = frame["close"].astype(float)
        latest_close = float(closes.iloc[-1])
        prior_close = float(closes.iloc[-2])
        proximity = latest_close / annual_high
        if proximity <= parameters["proximity_threshold"]:
            rejected.append({"symbol": normalized, "reason": "below proximity threshold"})
            continue
        if latest_close <= prior_close:
            rejected.append({"symbol": normalized, "reason": "close not above prior close"})
            continue
        candidates.append(
            {
                "symbol": normalized,
                "latest_close": latest_close,
                "proximity": proximity,
                "latest_return": latest_close / prior_close - 1.0,
            }
        )
    return candidates


def _exit_intents(
    open_positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    parameters: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    results: list[tuple[TradeIntent, str]] = []
    for symbol, quantity in open_positions.items():
        barset = bars_by_symbol.get(symbol)
        if barset is None:
            continue
        frame = barset.to_dataframe()
        if len(frame) < parameters["sma_exit_length"]:
            continue
        closes = frame["close"].astype(float)
        latest_close = float(closes.iloc[-1])
        state = context.entry_state.get(symbol, {}) if context.entry_state else {}
        entry_price = state.get("entry_price")
        held_days = state.get("held_days")
        sma = float(closes.tail(parameters["sma_exit_length"]).mean())
        rule: str | None = None
        if (
            isinstance(entry_price, int | float)
            and entry_price > 0
            and latest_close <= float(entry_price) * (1 - parameters["stop_loss_pct"])
        ):
            rule = "momentum.52w_stop_loss"
        elif isinstance(held_days, int | float) and int(held_days) >= parameters["max_hold_days"]:
            rule = "momentum.52w_max_hold"
        elif latest_close < sma:
            rule = "momentum.52w_sma_exit"
        if rule is not None:
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


def _exit_narrative(rule: str, symbol: str, parameters: dict[str, Any]) -> str:
    if rule == "momentum.52w_stop_loss":
        return f"close breached {parameters['stop_loss_pct']:.2%} stop from entry -> sell {symbol}"
    if rule == "momentum.52w_max_hold":
        return f"held >= max_hold_days {parameters['max_hold_days']} -> sell {symbol}"
    if rule == "momentum.52w_sma_exit":
        return f"close below {parameters['sma_exit_length']}-SMA -> sell {symbol}"
    return f"exit triggered -> sell {symbol}"


def _exit_threshold(rule: str, parameters: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "momentum.52w_stop_loss":
        return {"stop_loss_pct": parameters["stop_loss_pct"]}
    if rule == "momentum.52w_max_hold":
        return {"max_hold_days": parameters["max_hold_days"]}
    if rule == "momentum.52w_sma_exit":
        return {"sma_exit_length": parameters["sma_exit_length"]}
    return {}
