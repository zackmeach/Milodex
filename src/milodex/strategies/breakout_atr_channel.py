"""Daily ATR/Keltner channel breakout strategy."""

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

        if not _regime_bullish(bars_by_symbol, params):
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative="market regime bearish; entries suppressed",
                    triggering_values={"market_regime_symbol": params["market_regime_symbol"]},
                ),
            )

        capacity = max(0, params["max_concurrent_positions"] - len(positions))
        rejected: list[dict[str, Any]] = []
        candidates = _entry_candidates(
            context.universe, bars_by_symbol, positions, params, rejected
        )
        if params["ranking_enabled"]:
            candidates = sorted(candidates, key=lambda item: (-item[2], item[0]))

        intents: list[TradeIntent] = []
        for symbol, close, _strength in candidates[:capacity]:
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
                    rule="breakout.atr_channel_entry",
                    narrative=f"ATR channel breakout: buy {', '.join(i.symbol for i in intents)}",
                    triggering_values={"selected_symbol": intents[0].symbol},
                    threshold={"atr_entry_multiplier": params["atr_entry_multiplier"]},
                    rejected_alternatives=rejected,
                ),
            )
        return StrategyDecision(
            intents=[],
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=f"no ATR channel candidates qualified — {len(rejected)} rejected",
                triggering_values={"universe_size": len(context.universe)},
                rejected_alternatives=rejected,
            ),
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
    positions: dict[str, float],
    params: dict[str, Any],
    rejected: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    candidates: list[tuple[str, float, float]] = []
    for symbol in universe:
        normalized = symbol.upper()
        if normalized in positions:
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


def _regime_bullish(bars_by_symbol: dict[str, BarSet], params: dict[str, Any]) -> bool:
    symbol = params["market_regime_symbol"]
    if not symbol:
        return True
    barset = bars_by_symbol.get(symbol)
    if barset is None:
        return True
    closes = barset.to_dataframe()["close"].astype(float)
    if len(closes) < params["market_regime_ma_length"]:
        return True
    return float(closes.iloc[-1]) > float(closes.tail(params["market_regime_ma_length"]).mean())


def _ema(closes: pd.Series, length: int) -> float:
    return float(closes.ewm(span=length, adjust=False).mean().iloc[-1])


def _atr(frame, lookback: int) -> float | None:
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
