"""Shared evaluation flow for daily cross-sectional long-only strategies.

Several strategy families (``meanrev``, ``momentum``, and the deferred
breakout variants) share a near-identical ``evaluate`` skeleton:

1. Normalize the universe and open-position view.
2. Compute exit intents from the strategy-specific signal.
3. Short-circuit on exit-first, capacity-zero, or bearish regime.
4. Build entry candidates, rank them, then size the survivors.
5. Assemble the ``StrategyDecision`` / ``DecisionReasoning`` payload.

This module owns steps 1, 3, and 5. The strategy module owns the
signal computation, exit-rule names, narrative format strings, and
parameter validation. See ``docs/superpowers/specs/2026-05-23-rm-006-
daily-cross-sectional-first-slice-design.md`` for design rationale.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies.base import (
    DecisionReasoning,
    StrategyContext,
    StrategyDecision,
)


@dataclass(frozen=True)
class NormalizedInputs:
    """Universe-scoped, upper-cased view of a strategy context."""

    open_positions: dict[str, float]
    bars_by_symbol: dict[str, BarSet]


def normalize_universe_and_positions(context: StrategyContext) -> NormalizedInputs:
    """Return open positions and bar map scoped to ``context.universe``.

    Open positions outside the declared universe are dropped: they were
    not opened by this strategy (the broker account is shared in paper
    mode) and are not ours to unwind. Bar symbols are normalized to
    upper-case so downstream lookups have a single canonical key shape.
    """
    universe_symbols = {symbol.upper() for symbol in context.universe}
    open_positions = {
        symbol.upper(): float(quantity)
        for symbol, quantity in context.positions.items()
        if float(quantity) > 0 and symbol.upper() in universe_symbols
    }
    bars_by_symbol = {symbol.upper(): barset for symbol, barset in context.bars_by_symbol.items()}
    return NormalizedInputs(open_positions=open_positions, bars_by_symbol=bars_by_symbol)


def market_regime_is_bullish(
    bars_by_symbol: dict[str, BarSet],
    parameters: Mapping[str, Any],
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


# An early-return ``StrategyDecision`` OR a continuation tuple of
# (intents-so-far, remaining-after-exits, capacity).
PreEntryOutcome = StrategyDecision | tuple[list[TradeIntent], set[str], int]


def evaluate_pre_entry_gates(
    *,
    norm: NormalizedInputs,
    parameters: Mapping[str, Any],
    exit_details: list[tuple[TradeIntent, str]],
    exit_narrative: Callable[[str, str, Mapping[str, Any]], str],
    exit_threshold: Callable[[str, Mapping[str, Any]], dict[str, Any]],
    regime_filter_enabled: bool = True,
) -> PreEntryOutcome:
    """Apply the exit-first / capacity-zero / regime-bearish short-circuits.

    Order is fixed: exit-first, then capacity-zero, then regime-bearish.
    Returns a ``StrategyDecision`` when one of those branches fires; otherwise
    returns ``(intents, remaining_after_exits, capacity)`` for the caller to
    continue into the entry phase.
    """
    intents: list[TradeIntent] = [intent for intent, _rule in exit_details]
    remaining_after_exits = set(norm.open_positions) - {
        intent.symbol for intent in intents if intent.side == OrderSide.SELL
    }
    capacity = max(0, parameters["max_concurrent_positions"] - len(remaining_after_exits))

    if exit_details:
        first_intent, first_rule = exit_details[0]
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule=first_rule,
                narrative=exit_narrative(first_rule, first_intent.symbol, parameters),
                triggering_values={"symbol": first_intent.symbol},
                threshold=exit_threshold(first_rule, parameters),
            ),
        )

    if capacity <= 0:
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=(
                    f"at capacity: {len(remaining_after_exits)} open positions "
                    f">= max {parameters['max_concurrent_positions']}"
                ),
                triggering_values={"open_positions": len(remaining_after_exits)},
                threshold={"max_concurrent_positions": parameters["max_concurrent_positions"]},
            ),
        )

    if regime_filter_enabled and not market_regime_is_bullish(norm.bars_by_symbol, parameters):
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=(
                    f"market regime {parameters['market_regime_symbol']} bearish — "
                    f"below {parameters['market_regime_ma_length']}-DMA; entries suppressed"
                ),
                triggering_values={
                    "market_regime_symbol": parameters["market_regime_symbol"],
                },
                threshold={
                    "market_regime_ma_length": parameters["market_regime_ma_length"],
                },
            ),
        )

    return intents, remaining_after_exits, capacity


def assemble_entry_decision(
    *,
    intents: list[TradeIntent],
    candidates: list[tuple[str, float, float]],
    capacity: int,
    context: StrategyContext,
    parameters: Mapping[str, Any],
    rejected_alternatives: list[dict[str, Any]],
    signal_label: str,
    signal_format: str,
    ranking_enabled: bool,
    entry_rule: str,
    entry_threshold_keys: tuple[str, ...],
    entry_narrative_fn: Callable[[tuple[str, float, float], list[TradeIntent]], str],
) -> StrategyDecision:
    """Build the post-gate StrategyDecision (entry-success OR no-entry-qualified).

    Owns the ranking payload, overflow-rejection loop, sizing/affordability
    loop, and the two terminal ``StrategyDecision`` shapes. The caller
    supplies ranked candidates (highest-priority first) and the entry
    narrative as a closure over its own ``parameters``.
    """
    ranking_payload: list[dict[str, Any]] | None = None
    if ranking_enabled:
        ranking_payload = [
            {"symbol": sym, signal_label: value, "latest_close": close}
            for sym, close, value in candidates
        ]

    selected = candidates[:capacity]
    for sym, _close, value in candidates[capacity:]:
        rejected_alternatives.append(
            {
                "symbol": sym,
                "reason": (
                    f"capacity {capacity} full; ranked below selection "
                    f"({signal_label}={value:{signal_format}})"
                ),
            }
        )

    for symbol, latest_close, _value in selected:
        shares = shares_for_notional_pct(
            equity=context.equity,
            notional_pct=parameters["per_position_notional_pct"],
            unit_price=latest_close,
        )
        if shares <= 0:
            rejected_alternatives.append(
                {
                    "symbol": symbol,
                    "reason": (
                        f"equity {context.equity:.2f} cannot afford one share "
                        f"at {latest_close:.2f} for "
                        f"{parameters['per_position_notional_pct']:.2%} allocation"
                    ),
                }
            )
            continue
        intents.append(
            TradeIntent(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=float(shares),
                order_type=OrderType.MARKET,
            )
        )

    entry_intents = [intent for intent in intents if intent.side == OrderSide.BUY]
    threshold_payload = {key: parameters[key] for key in entry_threshold_keys}
    if entry_intents:
        primary = selected[0]
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule=entry_rule,
                narrative=entry_narrative_fn(primary, entry_intents),
                triggering_values={
                    "selected_symbol": primary[0],
                    f"selected_{signal_label}": primary[2],
                    "selected_close": primary[1],
                },
                threshold=threshold_payload,
                ranking=ranking_payload,
                rejected_alternatives=rejected_alternatives,
            ),
        )

    return StrategyDecision(
        intents=intents,
        reasoning=DecisionReasoning(
            rule="no_signal",
            narrative=(
                "no entry candidates qualified — "
                f"{len(rejected_alternatives)} universe member(s) rejected"
            ),
            triggering_values={"universe_size": len(context.universe)},
            threshold=threshold_payload,
            ranking=ranking_payload,
            rejected_alternatives=rejected_alternatives,
        ),
    )
