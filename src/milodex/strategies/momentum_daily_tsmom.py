"""Daily time-series momentum swing strategy.

Implements the ``momentum`` family per ``docs/strategy-families.md``:

- Long-only daily swing
- Entry: above the ``ma_filter_length`` SMA and momentum (return over
  ``momentum_lookback`` bars) at or above ``momentum_entry_threshold``
- Exit: momentum below ``momentum_exit_threshold``, ``max_hold_days``
  reached, or close-based stop triggered
- Ranking: ``momentum_descending`` (highest momentum first) when more
  candidates than open capacity

The strategy evaluates cross-sectionally across the resolved universe
using ``StrategyContext.bars_by_symbol``. Mirrors the structural shape
of the ``meanrev`` family deliberately — Phase 3's test for "harness
carries a second research thread" is exactly that the same harness
contract works for two materially-different signal shapes (continuation
vs. reversion).
"""

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
)

_VALID_SIZING_RULES = {"equal_notional", "fixed_notional"}
_VALID_RANKING_METRICS = {"momentum_descending"}


class MomentumDailyTsmomStrategy(Strategy):
    """Daily time-series momentum strategy."""

    family = "momentum"
    template = "daily.tsmom"
    parameter_specs = (
        StrategyParameterSpec("momentum_lookback", expected_types=(int,)),
        StrategyParameterSpec("momentum_entry_threshold", expected_types=(int, float)),
        StrategyParameterSpec("momentum_exit_threshold", expected_types=(int, float)),
        StrategyParameterSpec("ma_filter_length", expected_types=(int,)),
        StrategyParameterSpec("stop_loss_pct", expected_types=(int, float)),
        StrategyParameterSpec("max_hold_days", expected_types=(int,)),
        StrategyParameterSpec("max_concurrent_positions", expected_types=(int,)),
        StrategyParameterSpec("sizing_rule", expected_types=(str,)),
        StrategyParameterSpec("per_position_notional_pct", expected_types=(int, float)),
        StrategyParameterSpec("ranking_enabled", expected_types=(bool,)),
        StrategyParameterSpec("ranking_metric", expected_types=(str,)),
        StrategyParameterSpec("market_regime_symbol", expected_types=(str,), required=False),
        StrategyParameterSpec("market_regime_ma_length", expected_types=(int,), required=False),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars

        parameters = _validated_parameters(context)
        norm = normalize_universe_and_positions(context)
        rejected_alternatives: list[dict[str, Any]] = []

        exit_details = _exit_intents(norm.open_positions, norm.bars_by_symbol, context, parameters)
        gated = evaluate_pre_entry_gates(
            norm=norm,
            parameters=parameters,
            exit_details=exit_details,
            exit_narrative=_exit_narrative,
            exit_threshold=_exit_threshold,
        )
        if isinstance(gated, StrategyDecision):
            return gated
        intents = gated.intents
        remaining_after_exits = gated.remaining_after_exits
        capacity = gated.capacity

        candidates = _entry_candidates(
            universe=context.universe,
            bars_by_symbol=norm.bars_by_symbol,
            already_open=remaining_after_exits,
            parameters=parameters,
            rejected_alternatives=rejected_alternatives,
        )
        if parameters["ranking_enabled"]:
            candidates = _rank_candidates(candidates, parameters["ranking_metric"])

        def entry_narrative(
            primary: tuple[str, float, float], entry_intents: list[TradeIntent]
        ) -> str:
            return (
                f"momentum {primary[2]:.2%} at or above entry threshold "
                f"{parameters['momentum_entry_threshold']:.2%} and close above MA; "
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
            signal_label="momentum",
            signal_format=".4f",
            ranking_enabled=parameters["ranking_enabled"],
            entry_rule="momentum.tsmom_entry",
            entry_threshold_keys=("momentum_entry_threshold", "ma_filter_length"),
            entry_narrative_fn=entry_narrative,
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    momentum_lookback = int(required("momentum_lookback"))
    if momentum_lookback < 2:
        msg = "momentum_lookback must be >= 2"
        raise ValueError(msg)

    ma_filter_length = int(required("ma_filter_length"))
    if ma_filter_length < 1:
        msg = "ma_filter_length must be >= 1"
        raise ValueError(msg)

    max_hold_days = int(required("max_hold_days"))
    if max_hold_days < 1:
        msg = "max_hold_days must be >= 1"
        raise ValueError(msg)

    max_concurrent_positions = int(required("max_concurrent_positions"))
    if max_concurrent_positions < 1:
        msg = "max_concurrent_positions must be >= 1"
        raise ValueError(msg)

    sizing_rule = str(required("sizing_rule"))
    if sizing_rule not in _VALID_SIZING_RULES:
        msg = f"sizing_rule must be one of {sorted(_VALID_SIZING_RULES)}, got {sizing_rule!r}"
        raise ValueError(msg)

    ranking_metric = str(required("ranking_metric"))
    if ranking_metric not in _VALID_RANKING_METRICS:
        msg = (
            f"ranking_metric must be one of {sorted(_VALID_RANKING_METRICS)}, "
            f"got {ranking_metric!r}"
        )
        raise ValueError(msg)

    per_position_notional_pct = float(required("per_position_notional_pct"))
    if not 0 < per_position_notional_pct <= 1:
        msg = f"per_position_notional_pct must be in (0, 1], got {per_position_notional_pct!r}"
        raise ValueError(msg)

    stop_loss_pct = float(required("stop_loss_pct"))
    if stop_loss_pct <= 0:
        msg = f"stop_loss_pct must be > 0, got {stop_loss_pct!r}"
        raise ValueError(msg)

    momentum_entry_threshold = float(required("momentum_entry_threshold"))
    momentum_exit_threshold = float(required("momentum_exit_threshold"))
    if momentum_entry_threshold <= momentum_exit_threshold:
        msg = (
            "momentum_entry_threshold must be greater than momentum_exit_threshold "
            f"(got entry={momentum_entry_threshold!r}, exit={momentum_exit_threshold!r})"
        )
        raise ValueError(msg)

    market_regime_symbol = str(context.parameters.get("market_regime_symbol", "")).upper()
    market_regime_ma_length = int(context.parameters.get("market_regime_ma_length", 200))
    if market_regime_ma_length < 1:
        msg = "market_regime_ma_length must be >= 1"
        raise ValueError(msg)

    return {
        "momentum_lookback": momentum_lookback,
        "momentum_entry_threshold": momentum_entry_threshold,
        "momentum_exit_threshold": momentum_exit_threshold,
        "ma_filter_length": ma_filter_length,
        "stop_loss_pct": stop_loss_pct,
        "max_hold_days": max_hold_days,
        "max_concurrent_positions": max_concurrent_positions,
        "sizing_rule": sizing_rule,
        "per_position_notional_pct": per_position_notional_pct,
        "ranking_enabled": bool(required("ranking_enabled")),
        "ranking_metric": ranking_metric,
        "market_regime_symbol": market_regime_symbol,
        "market_regime_ma_length": market_regime_ma_length,
    }


def _entry_candidates(
    *,
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    already_open: set[str],
    parameters: dict[str, Any],
    rejected_alternatives: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    """Return (symbol, latest_close, momentum) for each eligible entry candidate."""
    candidates: list[tuple[str, float, float]] = []
    for symbol in universe:
        normalized = symbol.upper()
        if normalized in already_open:
            rejected_alternatives.append({"symbol": normalized, "reason": "already open"})
            continue
        barset = bars_by_symbol.get(normalized)
        if barset is None:
            rejected_alternatives.append({"symbol": normalized, "reason": "no bar data"})
            continue
        dataframe = barset.to_dataframe()
        min_required = max(parameters["ma_filter_length"], parameters["momentum_lookback"] + 1)
        if len(dataframe) < min_required:
            rejected_alternatives.append(
                {
                    "symbol": normalized,
                    "reason": (
                        f"insufficient history: {len(dataframe)} bars < "
                        f"max(ma={parameters['ma_filter_length']}, "
                        f"lookback+1={parameters['momentum_lookback'] + 1})"
                    ),
                }
            )
            continue
        closes = dataframe["close"].astype(float)
        latest_close = float(closes.iloc[-1])
        sma = float(closes.tail(parameters["ma_filter_length"]).mean())
        if latest_close <= sma:
            rejected_alternatives.append(
                {
                    "symbol": normalized,
                    "reason": (
                        f"close {latest_close:.2f} not above "
                        f"{parameters['ma_filter_length']}-SMA {sma:.2f}"
                    ),
                }
            )
            continue
        momentum = _momentum_signal(closes, parameters["momentum_lookback"])
        if momentum is None:
            rejected_alternatives.append({"symbol": normalized, "reason": "momentum indeterminate"})
            continue
        if momentum < parameters["momentum_entry_threshold"]:
            rejected_alternatives.append(
                {
                    "symbol": normalized,
                    "reason": (
                        f"momentum {momentum:.4f} below entry threshold "
                        f"{parameters['momentum_entry_threshold']:.4f}"
                    ),
                }
            )
            continue
        candidates.append((normalized, latest_close, momentum))
    return candidates


def _exit_intents(
    open_positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    parameters: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    """Return (intent, rule_name) per open position that triggers an exit.

    Order of precedence (most specific first): stop_loss > max_hold >
    momentum_exit. Mirrors meanrev's exit precedence.
    """
    results: list[tuple[TradeIntent, str]] = []
    for symbol, quantity in open_positions.items():
        barset = bars_by_symbol.get(symbol)
        if barset is None:
            continue
        dataframe = barset.to_dataframe()
        if dataframe.empty:
            continue
        closes = dataframe["close"].astype(float)
        latest_close = float(closes.iloc[-1])
        momentum = _momentum_signal(closes, parameters["momentum_lookback"])

        exit_rule: str | None = None

        state = context.entry_state.get(symbol) if context.entry_state else None
        if state is not None:
            entry_price = state.get("entry_price")
            held_days = state.get("held_days")
            if (
                isinstance(entry_price, int | float)
                and entry_price > 0
                and latest_close <= float(entry_price) * (1 - parameters["stop_loss_pct"])
            ):
                exit_rule = "momentum.stop_loss"
            elif (
                isinstance(held_days, int | float) and int(held_days) >= parameters["max_hold_days"]
            ):
                exit_rule = "momentum.max_hold"

        if (
            exit_rule is None
            and momentum is not None
            and momentum < parameters["momentum_exit_threshold"]
        ):
            exit_rule = "momentum.tsmom_exit"

        if exit_rule is not None:
            results.append(
                (
                    TradeIntent(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=float(quantity),
                        order_type=OrderType.MARKET,
                    ),
                    exit_rule,
                )
            )
    return results


def _exit_narrative(rule: str, symbol: str, parameters: dict[str, Any]) -> str:
    if rule == "momentum.tsmom_exit":
        return (
            f"momentum below exit threshold {parameters['momentum_exit_threshold']:.2%} "
            f"→ sell {symbol}"
        )
    if rule == "momentum.stop_loss":
        return (
            f"close breached stop_loss {parameters['stop_loss_pct']:.2%} from entry → sell {symbol}"
        )
    if rule == "momentum.max_hold":
        return f"held >= max_hold_days {parameters['max_hold_days']} → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, parameters: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "momentum.tsmom_exit":
        return {"momentum_exit_threshold": parameters["momentum_exit_threshold"]}
    if rule == "momentum.stop_loss":
        return {"stop_loss_pct": parameters["stop_loss_pct"]}
    if rule == "momentum.max_hold":
        return {"max_hold_days": parameters["max_hold_days"]}
    return {}


def _rank_candidates(
    candidates: list[tuple[str, float, float]],
    metric: str,
) -> list[tuple[str, float, float]]:
    if metric == "momentum_descending":
        # Highest momentum first; symbol break-tie ascending for determinism.
        return sorted(candidates, key=lambda entry: (-entry[2], entry[0]))
    return candidates


def _momentum_signal(closes: pd.Series, lookback: int) -> float | None:
    """Return ``(close[-1] / close[-1-lookback]) - 1`` — the return over
    the prior ``lookback`` bars.

    Returns ``None`` when there is insufficient history. ``lookback``
    bars before today is ``closes.iloc[-1 - lookback]`` in zero-indexed
    pandas; need at least ``lookback + 1`` total bars.
    """
    if len(closes) <= lookback:
        return None
    latest = float(closes.iloc[-1])
    reference = float(closes.iloc[-1 - lookback])
    if reference == 0.0:
        return None
    return latest / reference - 1.0
