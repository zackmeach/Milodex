"""Daily ATR/Keltner channel breakout strategy."""

from __future__ import annotations

from typing import Any

import pandas as pd

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
    rank_candidates,
)


class BreakoutAtrChannelStrategy(Strategy):
    """ATR-width channel breakout daily swing strategy."""

    family = "breakout"
    template = "daily.atr_channel"
    parameter_specs = (
        StrategyParameterSpec("ema_length", expected_types=(int,)),
        StrategyParameterSpec("atr_lookback", expected_types=(int,)),
        StrategyParameterSpec("atr_entry_multiplier", expected_types=(int, float)),
        StrategyParameterSpec("atr_stop_multiplier", expected_types=(int, float)),
        StrategyParameterSpec("max_hold_days", expected_types=(int,)),
        StrategyParameterSpec("max_concurrent_positions", expected_types=(int,)),
        StrategyParameterSpec("sizing_rule", expected_types=(str,)),
        StrategyParameterSpec("per_position_notional_pct", expected_types=(int, float)),
        StrategyParameterSpec("ranking_enabled", expected_types=(bool,)),
        StrategyParameterSpec("ranking_metric", expected_types=(str,)),
        StrategyParameterSpec("market_regime_symbol", expected_types=(str,)),
        StrategyParameterSpec("market_regime_ma_length", expected_types=(int,)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars
        parameters = _validated(context)
        norm = normalize_universe_and_positions(context)
        rejected_alternatives: list[dict[str, Any]] = []

        exit_details = _exit_intents(norm.open_positions, norm.bars_by_symbol, context, parameters)
        gated = evaluate_pre_entry_gates(
            norm=norm,
            parameters=parameters,
            exit_details=exit_details,
            exit_narrative=_exit_narrative,
            exit_threshold=_exit_threshold,
            regime_filter_enabled=True,  # atr_channel HAS regime params
        )
        if isinstance(gated, StrategyDecision):
            return gated
        intents = gated.intents
        remaining_after_exits = gated.remaining_after_exits
        capacity = gated.capacity

        candidates = _entry_candidates(
            context.universe,
            norm.bars_by_symbol,
            remaining_after_exits,
            parameters,
            rejected_alternatives,
        )
        if parameters["ranking_enabled"]:
            candidates = rank_candidates(candidates, key_fn=lambda c: (-c[2], c[0]))

        def entry_narrative(
            primary: tuple[str, float, float], entry_intents: list[TradeIntent]
        ) -> str:
            return (
                f"close {primary[1]:.2f} cleared ATR channel upper band "
                f"(strength {primary[2]:.4f}); "
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
            signal_label="breakout_strength",
            signal_format=".4f",
            ranking_enabled=parameters["ranking_enabled"],
            entry_rule="breakout.atr_channel_entry",
            entry_threshold_keys=("atr_entry_multiplier", "ema_length", "atr_lookback"),
            entry_narrative_fn=entry_narrative,
        )


def _validated(context: StrategyContext) -> dict[str, Any]:
    params = context.parameters
    ranking_metric = str(params["ranking_metric"])
    sizing_rule = str(params["sizing_rule"])
    if ranking_metric != "atr_channel_strength_descending":
        raise ValueError("ranking_metric must be atr_channel_strength_descending")
    if sizing_rule not in {"equal_notional", "fixed_notional"}:
        raise ValueError("invalid sizing_rule")
    return {
        "ema_length": int(params["ema_length"]),
        "atr_lookback": int(params["atr_lookback"]),
        "atr_entry_multiplier": float(params["atr_entry_multiplier"]),
        "atr_stop_multiplier": float(params["atr_stop_multiplier"]),
        "max_hold_days": int(params["max_hold_days"]),
        "max_concurrent_positions": int(params["max_concurrent_positions"]),
        "sizing_rule": sizing_rule,
        "per_position_notional_pct": float(params["per_position_notional_pct"]),
        "ranking_enabled": bool(params["ranking_enabled"]),
        "ranking_metric": ranking_metric,
        "market_regime_symbol": str(params["market_regime_symbol"]).upper(),
        "market_regime_ma_length": int(params["market_regime_ma_length"]),
    }


def _entry_candidates(
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    already_open: set[str],
    params: dict[str, Any],
    rejected: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    candidates: list[tuple[str, float, float]] = []
    for symbol in universe:
        normalized = symbol.upper()
        if normalized in already_open:
            continue
        barset = bars_by_symbol.get(normalized)
        if barset is None:
            rejected.append({"symbol": normalized, "reason": "no bar data"})
            continue
        frame = barset.to_dataframe()
        if len(frame) < max(params["ema_length"], params["atr_lookback"] + 1):
            rejected.append({"symbol": normalized, "reason": "insufficient history"})
            continue
        close = float(frame["close"].iloc[-1])
        ema = _ema(frame["close"].astype(float), params["ema_length"])
        atr = _atr(frame, params["atr_lookback"])
        if atr is None:
            rejected.append({"symbol": normalized, "reason": "ATR indeterminate"})
            continue
        upper = ema + params["atr_entry_multiplier"] * atr
        if close <= upper:
            rejected.append({"symbol": normalized, "reason": "close did not clear ATR channel"})
            continue
        candidates.append((normalized, close, (close - upper) / upper))
    return candidates


def _exit_intents(
    positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    params: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    results: list[tuple[TradeIntent, str]] = []
    for symbol, quantity in positions.items():
        frame = bars_by_symbol[symbol].to_dataframe()
        if len(frame) < max(params["ema_length"], params["atr_lookback"] + 1):
            continue
        close = float(frame["close"].iloc[-1])
        ema = _ema(frame["close"].astype(float), params["ema_length"])
        atr = _atr(frame, params["atr_lookback"])
        state = context.entry_state.get(symbol, {}) if context.entry_state else {}
        entry_price = state.get("entry_price")
        held_days = state.get("held_days")
        rule: str | None = None
        if (
            isinstance(entry_price, int | float)
            and atr is not None
            and close <= float(entry_price) - params["atr_stop_multiplier"] * atr
        ):
            rule = "breakout.atr_stop"
        elif isinstance(held_days, int | float) and int(held_days) >= params["max_hold_days"]:
            rule = "breakout.max_hold"
        elif close < ema:
            rule = "breakout.atr_channel_exit"
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


def _exit_narrative(rule: str, symbol: str, params: dict[str, Any]) -> str:
    if rule == "breakout.atr_stop":
        return f"close breached entry × {params['atr_stop_multiplier']} ATR stop → sell {symbol}"
    if rule == "breakout.max_hold":
        return f"held >= max_hold_days {params['max_hold_days']} → sell {symbol}"
    if rule == "breakout.atr_channel_exit":
        return f"close fell below EMA → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, params: dict[str, Any]) -> dict[str, Any]:
    if rule == "breakout.atr_stop":
        return {"atr_stop_multiplier": params["atr_stop_multiplier"]}
    if rule == "breakout.max_hold":
        return {"max_hold_days": params["max_hold_days"]}
    return {}


def _ema(closes: pd.Series, length: int) -> float:
    return float(closes.ewm(span=length, adjust=False).mean().iloc[-1])


def _atr(frame: Any, lookback: int) -> float | None:
    if len(frame) <= lookback:
        return None
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return float(true_range.tail(lookback).mean())
