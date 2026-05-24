"""Daily mean-reversion Internal Bar Strength (IBS) strategy.

Implements the ``meanrev`` family's ``daily.ibs_lowclose`` template per
``docs/strategy-families.md``:

- Long-only daily swing on broad index ETFs
- Entry: ``IBS = (Close - Low) / (High - Low) < ibs_entry_threshold`` AND
  ``Close > SMA(ma_filter_length)``
- Exit: ``Close > prior_day_high`` (signal exit), ``max_hold_days`` reached
  (time stop), or close-based stop_loss_pct breach (loss stop)
- Ranking: ``ibs_ascending`` (lowest IBS first) when more candidates
  qualify than capacity allows

The template is structurally distinct from ``daily.pullback_rsi2``: IBS uses
intraday bar location (where today closed within today's range) rather than
a multi-day oscillator. The two templates therefore exercise different
oversold mechanics on different universe shapes (broad index ETFs vs.
curated single-name large-caps), which is the diversification point of
including both in the bank.

Evidence: Larsson & Lindahl 2013 "Mining for Three Dollars a Day"
(Quantpedia); Connors *Short Term Trading Strategies That Work* (2008).
Native daily-swing — no published-edge truncation from adapting to the
1-5 day hold window.
"""

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

_VALID_SIZING_RULES = {"equal_notional", "fixed_notional"}
_VALID_RANKING_METRICS = {"ibs_ascending"}


class MeanrevIbsLowcloseStrategy(Strategy):
    """Internal Bar Strength daily mean-reversion strategy on index ETFs."""

    family = "meanrev"
    template = "daily.ibs_lowclose"
    parameter_specs = (
        StrategyParameterSpec("ibs_entry_threshold", expected_types=(int, float)),
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

        parameters = _validated_parameters(context)
        norm = normalize_universe_and_positions(context)
        rejected_alternatives: list[dict[str, Any]] = []

        exit_details = _exit_intents(norm.open_positions, norm.bars_by_symbol, context, parameters)
        # IBS by design has no regime filter — the universe is broad index ETFs,
        # not single-name positions, so regime_filter_enabled=False is correct.
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
                f"IBS {primary[2]:.3f} below entry threshold "
                f"{parameters['ibs_entry_threshold']} and close above MA; "
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
            signal_label="ibs",
            signal_format=".3f",
            ranking_enabled=parameters["ranking_enabled"],
            entry_rule="meanrev.ibs_entry",
            entry_threshold_keys=("ibs_entry_threshold", "ma_filter_length"),
            entry_narrative_fn=entry_narrative,
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    ibs_entry_threshold = float(required("ibs_entry_threshold"))
    if not 0 < ibs_entry_threshold < 1:
        msg = f"ibs_entry_threshold must be in (0, 1), got {ibs_entry_threshold!r}"
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

    return {
        "ibs_entry_threshold": ibs_entry_threshold,
        "ma_filter_length": ma_filter_length,
        "stop_loss_pct": stop_loss_pct,
        "max_hold_days": max_hold_days,
        "max_concurrent_positions": max_concurrent_positions,
        "sizing_rule": sizing_rule,
        "per_position_notional_pct": per_position_notional_pct,
        "ranking_enabled": bool(required("ranking_enabled")),
        "ranking_metric": ranking_metric,
    }


def _ibs(high: float, low: float, close: float) -> float | None:
    """Return IBS = (Close - Low) / (High - Low), or None if range is zero.

    A zero range (High == Low) indicates an untraded or limit-locked bar; IBS
    is undefined in that case. Callers must treat None as a rejection rather
    than a defensible 0 or 0.5 — the strategy refuses to act on a degenerate
    bar.
    """
    band = high - low
    if band <= 0:
        return None
    return (close - low) / band


def _entry_candidates(
    *,
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    already_open: set[str],
    parameters: dict[str, Any],
    rejected_alternatives: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    """Return (symbol, latest_close, ibs) per qualifying candidate."""
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
        if len(dataframe) < parameters["ma_filter_length"]:
            rejected_alternatives.append(
                {
                    "symbol": normalized,
                    "reason": (
                        f"insufficient history: {len(dataframe)} bars < "
                        f"ma_filter_length {parameters['ma_filter_length']}"
                    ),
                }
            )
            continue
        closes = dataframe["close"].astype(float)
        highs = dataframe["high"].astype(float)
        lows = dataframe["low"].astype(float)
        latest_close = float(closes.iloc[-1])
        latest_high = float(highs.iloc[-1])
        latest_low = float(lows.iloc[-1])
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
        ibs = _ibs(latest_high, latest_low, latest_close)
        if ibs is None:
            rejected_alternatives.append(
                {"symbol": normalized, "reason": "IBS undefined (zero range bar)"}
            )
            continue
        if ibs >= parameters["ibs_entry_threshold"]:
            rejected_alternatives.append(
                {
                    "symbol": normalized,
                    "reason": (
                        f"IBS {ibs:.3f} not below entry threshold "
                        f"{parameters['ibs_entry_threshold']}"
                    ),
                }
            )
            continue
        candidates.append((normalized, latest_close, ibs))
    return candidates


def _exit_intents(
    open_positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    parameters: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    """Return (intent, rule_name) per open position that triggers an exit.

    Exit-rule precedence (most specific first): stop_loss > max_hold > signal.
    The signal exit fires when ``close > prior_day_high``, which is the
    canonical IBS exit per the research source.
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
        highs = dataframe["high"].astype(float)
        latest_close = float(closes.iloc[-1])

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
                exit_rule = "meanrev.stop_loss"
            elif (
                isinstance(held_days, int | float) and int(held_days) >= parameters["max_hold_days"]
            ):
                exit_rule = "meanrev.max_hold"

        if exit_rule is None and len(highs) >= 2:
            prior_high = float(highs.iloc[-2])
            if latest_close > prior_high:
                exit_rule = "meanrev.ibs_exit"

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
    if rule == "meanrev.ibs_exit":
        return f"close above prior day's high → sell {symbol}"
    if rule == "meanrev.stop_loss":
        return (
            f"close breached stop_loss {parameters['stop_loss_pct']:.2%} from entry → sell {symbol}"
        )
    if rule == "meanrev.max_hold":
        return f"held >= max_hold_days {parameters['max_hold_days']} → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, parameters: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "meanrev.ibs_exit":
        return {"signal": "close > prior_day_high"}
    if rule == "meanrev.stop_loss":
        return {"stop_loss_pct": parameters["stop_loss_pct"]}
    if rule == "meanrev.max_hold":
        return {"max_hold_days": parameters["max_hold_days"]}
    return {}


def _rank_candidates(
    candidates: list[tuple[str, float, float]],
    metric: str,
) -> list[tuple[str, float, float]]:
    if metric == "ibs_ascending":
        return sorted(candidates, key=lambda entry: (entry[2], entry[0]))
    return candidates
