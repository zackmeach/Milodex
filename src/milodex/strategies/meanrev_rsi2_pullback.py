"""Daily mean-reversion RSI(2) pullback strategy.

Implements the ``meanrev`` family per ``docs/strategy-families.md``:

- Long-only daily swing
- Entry: above the ``ma_filter_length`` SMA and RSI below ``rsi_entry_threshold``
- Exit: RSI above ``rsi_exit_threshold``, ``max_hold_days`` reached, or
  close-based stop triggered
- Ranking: ``rsi_ascending`` (lowest RSI first) when more candidates than
  open capacity

The strategy evaluates cross-sectionally across the resolved universe
using ``StrategyContext.bars_by_symbol``. The ``bars`` argument passed
to ``evaluate`` is unused (only present to satisfy the base class
contract); callers are expected to populate ``bars_by_symbol`` for all
universe members.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies.base import Strategy, StrategyContext, StrategyParameterSpec

_VALID_SIZING_RULES = {"equal_notional", "fixed_notional"}
_VALID_RANKING_METRICS = {"rsi_ascending", "drawdown_deepest"}


class MeanrevRsi2PullbackStrategy(Strategy):
    """RSI(2) pullback daily mean-reversion strategy."""

    family = "meanrev"
    template = "daily.pullback_rsi2"
    parameter_specs = (
        StrategyParameterSpec("rsi_lookback", expected_types=(int,)),
        StrategyParameterSpec("rsi_entry_threshold", expected_types=(int, float)),
        StrategyParameterSpec("rsi_exit_threshold", expected_types=(int, float)),
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

    def evaluate(self, bars: BarSet, context: StrategyContext) -> list[TradeIntent]:
        _ = bars

        parameters = _validated_parameters(context)
        open_positions = {
            symbol.upper(): float(quantity)
            for symbol, quantity in context.positions.items()
            if float(quantity) > 0
        }
        bars_by_symbol = {
            symbol.upper(): barset for symbol, barset in context.bars_by_symbol.items()
        }

        intents: list[TradeIntent] = []
        intents.extend(_exit_intents(open_positions, bars_by_symbol, context, parameters))

        remaining_after_exits = set(open_positions) - {
            intent.symbol for intent in intents if intent.side == OrderSide.SELL
        }
        capacity = max(0, parameters["max_concurrent_positions"] - len(remaining_after_exits))
        if capacity <= 0:
            return intents

        if not _market_regime_is_bullish(bars_by_symbol, parameters):
            return intents

        candidates = _entry_candidates(
            universe=context.universe,
            bars_by_symbol=bars_by_symbol,
            already_open=remaining_after_exits,
            parameters=parameters,
        )

        if parameters["ranking_enabled"]:
            candidates = _rank_candidates(candidates, parameters["ranking_metric"])

        selected = candidates[:capacity]
        for symbol, latest_close, _rsi in selected:
            shares = shares_for_notional_pct(
                equity=context.equity,
                notional_pct=parameters["per_position_notional_pct"],
                unit_price=latest_close,
            )
            if shares <= 0:
                continue
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=float(shares),
                    order_type=OrderType.MARKET,
                )
            )

        return intents


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    rsi_lookback = int(required("rsi_lookback"))
    if rsi_lookback < 2:
        msg = "rsi_lookback must be >= 2"
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
        msg = (
            f"sizing_rule must be one of {sorted(_VALID_SIZING_RULES)}, "
            f"got {sizing_rule!r}"
        )
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

    rsi_entry_threshold = float(required("rsi_entry_threshold"))
    rsi_exit_threshold = float(required("rsi_exit_threshold"))
    if rsi_entry_threshold >= rsi_exit_threshold:
        msg = "rsi_entry_threshold must be less than rsi_exit_threshold"
        raise ValueError(msg)

    market_regime_symbol = str(context.parameters.get("market_regime_symbol", "")).upper()
    market_regime_ma_length = int(context.parameters.get("market_regime_ma_length", 200))
    if market_regime_ma_length < 1:
        msg = "market_regime_ma_length must be >= 1"
        raise ValueError(msg)

    return {
        "rsi_lookback": rsi_lookback,
        "rsi_entry_threshold": rsi_entry_threshold,
        "rsi_exit_threshold": rsi_exit_threshold,
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


def _market_regime_is_bullish(
    bars_by_symbol: dict[str, BarSet],
    parameters: dict[str, Any],
) -> bool:
    """Return True when the market-regime symbol is above its MA, or when no
    regime filter is configured.

    Failing to find data for the regime symbol causes the filter to *pass*
    (fail-open): we never silently block all entries due to missing data.
    The risk layer remains authoritative for hard stops.
    """
    regime_symbol = parameters.get("market_regime_symbol", "")
    if not regime_symbol:
        return True
    ma_length = int(parameters.get("market_regime_ma_length", 200))
    barset = bars_by_symbol.get(regime_symbol)
    if barset is None:
        return True
    dataframe = barset.to_dataframe()
    if len(dataframe) < ma_length:
        return True
    closes = dataframe["close"].astype(float)
    return float(closes.iloc[-1]) > float(closes.tail(ma_length).mean())


def _entry_candidates(
    *,
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    already_open: set[str],
    parameters: dict[str, Any],
) -> list[tuple[str, float, float]]:
    """Return (symbol, latest_close, rsi) for each eligible entry candidate."""
    candidates: list[tuple[str, float, float]] = []
    for symbol in universe:
        normalized = symbol.upper()
        if normalized in already_open:
            continue
        barset = bars_by_symbol.get(normalized)
        if barset is None:
            continue
        dataframe = barset.to_dataframe()
        if len(dataframe) < max(
            parameters["ma_filter_length"], parameters["rsi_lookback"] + 1
        ):
            continue
        closes = dataframe["close"].astype(float)
        latest_close = float(closes.iloc[-1])
        sma = float(closes.tail(parameters["ma_filter_length"]).mean())
        if latest_close <= sma:
            continue
        rsi = _wilder_rsi(closes, parameters["rsi_lookback"])
        if rsi is None:
            continue
        if rsi >= parameters["rsi_entry_threshold"]:
            continue
        candidates.append((normalized, latest_close, rsi))
    return candidates


def _exit_intents(
    open_positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    parameters: dict[str, Any],
) -> list[TradeIntent]:
    intents: list[TradeIntent] = []
    for symbol, quantity in open_positions.items():
        barset = bars_by_symbol.get(symbol)
        if barset is None:
            continue
        dataframe = barset.to_dataframe()
        if dataframe.empty:
            continue
        closes = dataframe["close"].astype(float)
        latest_close = float(closes.iloc[-1])
        rsi = _wilder_rsi(closes, parameters["rsi_lookback"])
        should_exit = False

        if rsi is not None and rsi > parameters["rsi_exit_threshold"]:
            should_exit = True

        state = context.entry_state.get(symbol) if context.entry_state else None
        if state is not None:
            entry_price = state.get("entry_price")
            held_days = state.get("held_days")
            if (
                isinstance(entry_price, int | float)
                and entry_price > 0
                and latest_close <= float(entry_price) * (1 - parameters["stop_loss_pct"])
            ):
                should_exit = True
            if (
                isinstance(held_days, int | float)
                and int(held_days) >= parameters["max_hold_days"]
            ):
                should_exit = True

        if should_exit:
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    quantity=float(quantity),
                    order_type=OrderType.MARKET,
                )
            )
    return intents


def _rank_candidates(
    candidates: list[tuple[str, float, float]],
    metric: str,
) -> list[tuple[str, float, float]]:
    if metric == "rsi_ascending":
        return sorted(candidates, key=lambda entry: (entry[2], entry[0]))
    if metric == "drawdown_deepest":
        # Phase 1 proxy: deepest drawdown ≈ lowest RSI; keep the same
        # ordering until a variant needs a different formal metric.
        return sorted(candidates, key=lambda entry: (entry[2], entry[0]))
    return candidates


def _wilder_rsi(closes: pd.Series, lookback: int) -> float | None:
    """Return Wilder-smoothed RSI on ``closes`` with period ``lookback``.

    Returns ``None`` when there is insufficient history to compute the
    indicator.
    """
    if len(closes) <= lookback:
        return None

    deltas = closes.diff().dropna()
    if len(deltas) < lookback:
        return None

    gains = deltas.clip(lower=0.0)
    losses = (-deltas).clip(lower=0.0)

    avg_gain = float(gains.iloc[:lookback].mean())
    avg_loss = float(losses.iloc[:lookback].mean())
    for gain, loss in zip(gains.iloc[lookback:], losses.iloc[lookback:], strict=False):
        avg_gain = (avg_gain * (lookback - 1) + float(gain)) / lookback
        avg_loss = (avg_loss * (lookback - 1) + float(loss)) / lookback

    if avg_loss == 0.0:
        if avg_gain == 0.0:
            return 50.0
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
