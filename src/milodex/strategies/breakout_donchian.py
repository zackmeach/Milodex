"""Daily Donchian channel breakout strategy.

Implements the ``breakout`` family's ``daily.donchian_20_10`` template per
``docs/strategy-families.md``:

- Long-only daily swing on liquid sector ETFs
- Entry: today's close exceeds the highest high of the prior
  ``entry_channel_length`` bars (excluding today) AND ``Close > SMA(ma_filter_length)``
- Exit (most-specific first): ATR stop, percent stop, max_hold time stop,
  channel-low exit
- Ranking: ``breakout_strength_descending`` — strongest breakout first

Look-ahead safety is a family invariant. The entry-channel max is
``max(high[-entry_channel_length-1:-1])`` — the latest bar's own high is
**excluded** from the reference. Including it would make the trigger
trivially true on every breakout day.

The ``20_10`` in the template name reflects the Turtle System 1 default
(20-day entry channel, 10-day exit channel). The numbers are configurable
within the family's parameter surface.

Evidence: Faith 2007 *Way of the Turtle*; Clenow 2019 *Trading Evolved*;
Szakmary et al. 2010 *Journal of Banking & Finance*. Daily-swing fit
caveat at the family level — the 5-day cap truncates the right tail that
makes classical breakout systems profitable; expect material PF degradation
vs. published results. Each instance must measure that degradation rather
than claim away from it.
"""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies._indicators import atr as _atr
from milodex.strategies.base import (
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
    relation_less_than,
)
from milodex.strategies.daily_cross_sectional import (
    assemble_entry_decision,
    evaluate_pre_entry_gates,
    normalize_universe_and_positions,
    rank_candidates,
)

_VALID_SIZING_RULES = {"equal_notional", "fixed_notional"}
_VALID_RANKING_METRICS = {"breakout_strength_descending"}


class BreakoutDonchianStrategy(Strategy):
    """Donchian channel breakout daily swing strategy."""

    family = "breakout"
    template = "daily.donchian_20_10"
    parameter_specs = (
        StrategyParameterSpec("entry_channel_length", expected_types=(int,), minimum=2),
        StrategyParameterSpec("exit_channel_length", expected_types=(int,), minimum=2),
        StrategyParameterSpec("ma_filter_length", expected_types=(int,), minimum=1),
        StrategyParameterSpec("atr_lookback", expected_types=(int,), minimum=2),
        StrategyParameterSpec(
            "atr_stop_multiplier", expected_types=(int, float), exclusive_minimum=0
        ),
        StrategyParameterSpec("stop_loss_pct", expected_types=(int, float), exclusive_minimum=0),
        StrategyParameterSpec("max_hold_days", expected_types=(int,), minimum=1),
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
        StrategyParameterSpec("market_regime_symbol", expected_types=(str,), required=False),
        StrategyParameterSpec(
            "market_regime_ma_length", expected_types=(int,), required=False, minimum=1
        ),
    )
    parameter_relations = (relation_less_than("exit_channel_length", "entry_channel_length"),)

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
            regime_filter_enabled=True,
        )
        if isinstance(gated, StrategyDecision):
            return gated
        intents = gated.intents
        remaining_after_exits = gated.remaining_after_exits
        capacity = gated.capacity

        # _entry_candidates returns 4-tuples: (symbol, latest_close, breakout_strength,
        # channel_high). Capture channel_high per symbol before narrowing to 3-tuples
        # so the entry narrative and extra_triggering_values_fn can look it up.
        raw_candidates = _entry_candidates(
            universe=context.universe,
            bars_by_symbol=norm.bars_by_symbol,
            already_open=remaining_after_exits,
            parameters=parameters,
            rejected_alternatives=rejected_alternatives,
        )
        channel_highs_by_symbol: dict[str, float] = {
            sym: ch_high for sym, _close, _strength, ch_high in raw_candidates
        }
        # Narrow to 3-tuples for the shared helper.
        candidates: list[tuple[str, float, float]] = [
            (sym, close, strength) for sym, close, strength, _ch_high in raw_candidates
        ]
        if parameters["ranking_enabled"]:
            candidates = rank_candidates(candidates, key_fn=lambda c: (-c[2], c[0]))

        def entry_narrative(
            primary: tuple[str, float, float], entry_intents: list[TradeIntent]
        ) -> str:
            channel_high = channel_highs_by_symbol[primary[0]]
            return (
                f"close {primary[1]:.2f} cleared {parameters['entry_channel_length']}-day "
                f"channel high {channel_high:.2f} (strength {primary[2]:.4f}) and is "
                f"above MA; buy {len(entry_intents)} candidate(s): "
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
            entry_rule="breakout.channel_entry",
            entry_threshold_keys=("entry_channel_length", "ma_filter_length"),
            entry_narrative_fn=entry_narrative,
            extra_triggering_values_fn=lambda primary: {
                "selected_channel_high": channel_highs_by_symbol[primary[0]]
            },
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    entry_channel_length = int(required("entry_channel_length"))
    if entry_channel_length < 2:
        msg = "entry_channel_length must be >= 2"
        raise ValueError(msg)

    exit_channel_length = int(required("exit_channel_length"))
    if exit_channel_length < 2:
        msg = "exit_channel_length must be >= 2"
        raise ValueError(msg)
    if exit_channel_length >= entry_channel_length:
        msg = (
            "exit_channel_length must be less than entry_channel_length "
            "(exit channel is intentionally tighter so winners can run further than the trigger)"
        )
        raise ValueError(msg)

    ma_filter_length = int(required("ma_filter_length"))
    if ma_filter_length < 1:
        msg = "ma_filter_length must be >= 1"
        raise ValueError(msg)

    atr_lookback = int(required("atr_lookback"))
    if atr_lookback < 2:
        msg = "atr_lookback must be >= 2"
        raise ValueError(msg)

    atr_stop_multiplier = float(required("atr_stop_multiplier"))
    if atr_stop_multiplier <= 0:
        msg = f"atr_stop_multiplier must be > 0, got {atr_stop_multiplier!r}"
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

    market_regime_symbol = str(context.parameters.get("market_regime_symbol", "")).upper()
    market_regime_ma_length = int(context.parameters.get("market_regime_ma_length", 200))
    if market_regime_ma_length < 1:
        msg = "market_regime_ma_length must be >= 1"
        raise ValueError(msg)

    return {
        "entry_channel_length": entry_channel_length,
        "exit_channel_length": exit_channel_length,
        "ma_filter_length": ma_filter_length,
        "atr_lookback": atr_lookback,
        "atr_stop_multiplier": atr_stop_multiplier,
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
) -> list[tuple[str, float, float, float]]:
    """Return (symbol, latest_close, breakout_strength, channel_high) per candidate.

    ``breakout_strength`` is the bare-breakout overshoot:
    ``(close - prior_channel_high) / prior_channel_high``. Used by the
    ``breakout_strength_descending`` ranking metric.
    """
    candidates: list[tuple[str, float, float, float]] = []
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
        # Need: ma_filter_length closes for the MA filter; entry_channel_length+1 highs for
        # the look-ahead-safe channel max (exclude latest); atr_lookback+1 for ATR.
        min_required = max(
            parameters["ma_filter_length"],
            parameters["entry_channel_length"] + 1,
            parameters["atr_lookback"] + 1,
        )
        if len(dataframe) < min_required:
            rejected_alternatives.append(
                {
                    "symbol": normalized,
                    "reason": (
                        f"insufficient history: {len(dataframe)} bars < required {min_required}"
                    ),
                }
            )
            continue
        closes = dataframe["close"].astype(float)
        highs = dataframe["high"].astype(float)
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
        # Look-ahead-safe channel max: highs of the prior N bars EXCLUDING today.
        channel_window = highs.iloc[-(parameters["entry_channel_length"] + 1) : -1]
        if channel_window.empty:
            rejected_alternatives.append({"symbol": normalized, "reason": "channel window empty"})
            continue
        channel_high = float(channel_window.max())
        if latest_close <= channel_high:
            rejected_alternatives.append(
                {
                    "symbol": normalized,
                    "reason": (
                        f"close {latest_close:.2f} did not clear "
                        f"{parameters['entry_channel_length']}-day channel high "
                        f"{channel_high:.2f}"
                    ),
                }
            )
            continue
        if channel_high <= 0:
            rejected_alternatives.append(
                {"symbol": normalized, "reason": "channel high non-positive"}
            )
            continue
        breakout_strength = (latest_close - channel_high) / channel_high
        candidates.append((normalized, latest_close, breakout_strength, channel_high))
    return candidates


def _exit_intents(
    open_positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    parameters: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    """Return (intent, rule_name) per open position that triggers an exit.

    Exit-rule precedence (most-specific first):
        atr_stop > stop_loss > max_hold > channel_exit

    **Phase 1 ATR compromise:** the published Donchian/Turtle ATR stop freezes
    ATR at entry. Milodex's runner + backtest engine stamp ``entry_price`` and
    ``held_days`` into ``entry_state`` but not ``entry_atr``. Rather than
    plumb a new field through both, this template computes ATR **live** on
    each evaluation (over the latest ``atr_lookback`` bars) and uses it as the
    stop reference. This trades the published "frozen at entry" semantics for
    a self-adjusting stop that chases volatility — acceptable at backtest
    stage; promotion to paper would require freezing the snapshot. Recorded in
    docs/strategy-families.md as a known Phase 1 deviation from the published
    family behavior.
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
        lows = dataframe["low"].astype(float)
        latest_close = float(closes.iloc[-1])

        exit_rule: str | None = None

        state = context.entry_state.get(symbol) if context.entry_state else None
        if state is not None:
            entry_price = state.get("entry_price")
            held_days = state.get("held_days")
            live_atr = _atr(highs, lows, closes, parameters["atr_lookback"])
            if (
                isinstance(entry_price, int | float)
                and entry_price > 0
                and live_atr is not None
                and live_atr > 0
                and latest_close
                <= float(entry_price) - parameters["atr_stop_multiplier"] * live_atr
            ):
                exit_rule = "breakout.atr_stop"
            elif (
                isinstance(entry_price, int | float)
                and entry_price > 0
                and latest_close <= float(entry_price) * (1 - parameters["stop_loss_pct"])
            ):
                exit_rule = "breakout.stop_loss"
            elif (
                isinstance(held_days, int | float) and int(held_days) >= parameters["max_hold_days"]
            ):
                exit_rule = "breakout.max_hold"

        if exit_rule is None:
            channel_window = lows.iloc[-(parameters["exit_channel_length"] + 1) : -1]
            if not channel_window.empty:
                channel_low = float(channel_window.min())
                if latest_close < channel_low:
                    exit_rule = "breakout.channel_exit"

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
    if rule == "breakout.channel_exit":
        return f"close below {parameters['exit_channel_length']}-day channel low → sell {symbol}"
    if rule == "breakout.atr_stop":
        return (
            f"close breached entry_atr × {parameters['atr_stop_multiplier']} stop → sell {symbol}"
        )
    if rule == "breakout.stop_loss":
        return (
            f"close breached stop_loss {parameters['stop_loss_pct']:.2%} from entry → sell {symbol}"
        )
    if rule == "breakout.max_hold":
        return f"held >= max_hold_days {parameters['max_hold_days']} → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, parameters: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "breakout.channel_exit":
        return {"exit_channel_length": parameters["exit_channel_length"]}
    if rule == "breakout.atr_stop":
        return {"atr_stop_multiplier": parameters["atr_stop_multiplier"]}
    if rule == "breakout.stop_loss":
        return {"stop_loss_pct": parameters["stop_loss_pct"]}
    if rule == "breakout.max_hold":
        return {"max_hold_days": parameters["max_hold_days"]}
    return {}
