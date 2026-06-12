"""Daily lower-Bollinger-band mean-reversion strategy."""

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

_VALID_SIZING_RULES = {"equal_notional", "fixed_notional"}
_VALID_RANKING_METRICS = {"bbands_zscore_ascending"}


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
        StrategyParameterSpec(
            "sizing_rule", expected_types=(str,), choices=tuple(sorted(_VALID_SIZING_RULES))
        ),
        StrategyParameterSpec("per_position_notional_pct", expected_types=(int, float)),
        StrategyParameterSpec("ranking_enabled", expected_types=(bool,)),
        StrategyParameterSpec(
            "ranking_metric", expected_types=(str,), choices=tuple(sorted(_VALID_RANKING_METRICS))
        ),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars
        parameters = _validated(context)
        norm = normalize_universe_and_positions(context)
        rejected_alternatives: list[dict[str, Any]] = []

        exit_details = _exit_intents(norm.open_positions, norm.bars_by_symbol, context, parameters)
        # bbands has no regime params — regime_filter_enabled=False
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
        # bbands ranks ascending by zscore (most negative / most oversold first)
        if parameters["ranking_enabled"]:
            candidates = rank_candidates(candidates, key_fn=lambda c: (c[2], c[0]))

        def entry_narrative(
            primary: tuple[str, float, float], entry_intents: list[TradeIntent]
        ) -> str:
            return (
                f"close {primary[1]:.2f} z-score {primary[2]:.3f} below entry threshold; "
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
            signal_label="zscore",
            signal_format=".3f",
            ranking_enabled=parameters["ranking_enabled"],
            entry_rule="meanrev.bbands_entry",
            entry_threshold_keys=("bbands_stddev", "bbands_lookback", "ma_filter_length"),
            entry_narrative_fn=entry_narrative,
        )


def _validated(context: StrategyContext) -> dict[str, Any]:
    params = context.parameters
    sizing_rule = str(params["sizing_rule"])
    ranking_metric = str(params["ranking_metric"])
    if sizing_rule not in _VALID_SIZING_RULES:
        raise ValueError(
            f"invalid sizing_rule {sizing_rule!r}; expected one of {sorted(_VALID_SIZING_RULES)}"
        )
    if ranking_metric not in _VALID_RANKING_METRICS:
        raise ValueError(
            f"invalid ranking_metric {ranking_metric!r}; "
            f"expected one of {sorted(_VALID_RANKING_METRICS)}"
        )
    return {
        "bbands_lookback": int(params["bbands_lookback"]),
        "bbands_stddev": float(params["bbands_stddev"]),
        "ma_filter_length": int(params["ma_filter_length"]),
        "stop_loss_pct": float(params["stop_loss_pct"]),
        "max_hold_days": int(params["max_hold_days"]),
        "max_concurrent_positions": int(params["max_concurrent_positions"]),
        "sizing_rule": sizing_rule,
        "per_position_notional_pct": float(params["per_position_notional_pct"]),
        "ranking_enabled": bool(params["ranking_enabled"]),
        "ranking_metric": ranking_metric,
    }


def _entry_candidates(
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    already_open: set[str],
    params: dict[str, Any],
    rejected: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    candidates: list[tuple[str, float, float]] = []
    required = max(params["bbands_lookback"], params["ma_filter_length"])
    for symbol in universe:
        normalized = symbol.upper()
        if normalized in already_open:
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


def _exit_narrative(rule: str, symbol: str, params: dict[str, Any]) -> str:
    if rule == "meanrev.bbands_stop_loss":
        return f"close breached stop_loss {params['stop_loss_pct']:.2%} from entry → sell {symbol}"
    if rule == "meanrev.bbands_max_hold":
        return f"held >= max_hold_days {params['max_hold_days']} → sell {symbol}"
    if rule == "meanrev.bbands_exit":
        return f"close reverted above middle Bollinger band → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, params: dict[str, Any]) -> dict[str, Any]:
    if rule == "meanrev.bbands_stop_loss":
        return {"stop_loss_pct": params["stop_loss_pct"]}
    if rule == "meanrev.bbands_max_hold":
        return {"max_hold_days": params["max_hold_days"]}
    return {}


def _bands(closes: pd.Series, params: dict[str, Any]) -> tuple[float, float, float]:
    window = closes.tail(params["bbands_lookback"])
    middle = float(window.mean())
    stddev = float(window.std(ddof=0))
    lower = middle - params["bbands_stddev"] * stddev
    zscore = 0.0 if stddev == 0 else (float(closes.iloc[-1]) - middle) / stddev
    return middle, lower, zscore
