"""Daily NR7 volatility-contraction breakout strategy."""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
)
from milodex.strategies.daily_cross_sectional import (
    assemble_entry_decision,
    evaluate_pre_entry_gates,
    normalize_universe_and_positions,
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
        norm = normalize_universe_and_positions(context)
        rejected_alternatives: list[dict[str, Any]] = []

        exit_details = _exit_intents(norm.open_positions, norm.bars_by_symbol, context, parameters)
        # NR7 has no regime params — regime_filter_enabled=False
        gated = evaluate_pre_entry_gates(
            norm=norm,
            parameters=parameters,
            exit_details=exit_details,
            exit_narrative=_exit_narrative,
            exit_threshold=_exit_threshold,
            regime_filter_enabled=False,
        )
        if isinstance(gated, StrategyDecision):
            return gated
        intents, remaining_after_exits, capacity = gated

        candidates = _entry_candidates(
            context.universe, norm.bars_by_symbol, remaining_after_exits, parameters,
            rejected_alternatives
        )
        # NR7 ranks ascending by range (tightest contraction first); pre-rank before helper
        if parameters["ranking_enabled"]:
            candidates = sorted(candidates, key=lambda item: (item[2], item[0]))

        def entry_narrative(
            primary: tuple[str, float, float], entry_intents: list[TradeIntent]
        ) -> str:
            return (
                f"NR7 contraction: {primary[0]} range {primary[2]:.4f} is the tightest "
                f"in {parameters['range_lookback']} bars; "
                f"buy {len(entry_intents)} candidate(s): "
                f"{', '.join(intent.symbol for intent in entry_intents)}"
            )

        return assemble_entry_decision(
            intents=intents,
            candidates=candidates,
            capacity=capacity,
            context=context,
            parameters=parameters,
            rejected_alternatives=rejected_alternatives,
            signal_label="range_value",
            signal_format=".4f",
            ranking_enabled=parameters["ranking_enabled"],
            entry_rule="breakout.nr7_entry",
            entry_threshold_keys=("range_lookback", "ma_filter_length"),
            entry_narrative_fn=entry_narrative,
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
    already_open: set[str],
    parameters: dict[str, Any],
    rejected: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    candidates: list[tuple[str, float, float]] = []
    min_required = max(parameters["range_lookback"], parameters["ma_filter_length"])
    for symbol in universe:
        normalized = symbol.upper()
        if normalized in already_open:
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


def _exit_narrative(rule: str, symbol: str, parameters: dict[str, Any]) -> str:
    if rule == "breakout.nr7_initial_stop":
        return f"close hit initial stop → sell {symbol}"
    if rule == "breakout.nr7_max_hold":
        return f"held >= max_hold_days {parameters['max_hold_days']} → sell {symbol}"
    if rule == "breakout.nr7_trailing_low":
        return f"close fell below prior-day low while profitable → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, parameters: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "breakout.nr7_max_hold":
        return {"max_hold_days": parameters["max_hold_days"]}
    return {}
