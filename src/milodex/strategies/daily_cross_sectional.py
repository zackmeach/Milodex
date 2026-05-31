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


@dataclass(frozen=True)
class PreEntryContinue:
    """Continuation payload from ``evaluate_pre_entry_gates`` when no gate fired.

    Returned in the happy-path case (no exits, capacity available, regime
    bullish or filter disabled) so the caller can proceed into the entry
    phase. Named fields replace the older ``(list, set, int)`` tuple shape
    that required positional unpacking at every call site.
    """

    intents: list[TradeIntent]
    remaining_after_exits: set[str]
    capacity: int


# An early-return ``StrategyDecision`` OR a continuation ``PreEntryContinue``.
PreEntryOutcome = StrategyDecision | PreEntryContinue


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
    returns a ``PreEntryContinue`` carrying the intents-so-far, the
    set of symbols still open after exits, and the remaining capacity.
    """
    # Deterministic exit ordering. Each strategy's ``_exit_intents`` iterates
    # ``open_positions`` in mapping order — broker-response order live, kernel
    # insertion order in backtest — so without this sort the emitted SELL order
    # and the reported primary exit (``exit_details[0]`` below) would depend on
    # that nondeterministic order. Sorting by symbol makes both reproducible;
    # the set of exits is unchanged, only their order and the primary label.
    exit_details = sorted(exit_details, key=lambda pair: pair[0].symbol)
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

    return PreEntryContinue(
        intents=intents,
        remaining_after_exits=remaining_after_exits,
        capacity=capacity,
    )


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
    extra_triggering_values_fn: Callable[[tuple[str, float, float]], dict[str, Any]] | None = None,
) -> StrategyDecision:
    """Build the post-gate StrategyDecision (entry-success OR no-entry-qualified).

    Owns the ranking payload, overflow-rejection loop, sizing/affordability
    loop, and the two terminal ``StrategyDecision`` shapes. The caller
    supplies ranked candidates (highest-priority first) and the entry
    narrative as a closure over its own ``parameters``.

    Audit-key contract (stable across all cross-sectional strategies):

    - ``triggering_values`` on entry-success carries ``selected_symbol``,
      ``selected_signal_label`` (the strategy's signal name as a *value*,
      e.g. ``"breakout_strength"``), ``selected_signal_value`` (the
      numeric signal for the chosen primary), and ``selected_close``.
    - ``ranking`` entries carry ``symbol``, ``signal_label``,
      ``signal_value``, and ``latest_close``.

    The label is stored as a value on stable keys rather than embedded in
    the key name so the audit schema is uniform across strategies — a
    downstream consumer can read "what was the signal value?" without
    knowing which strategy produced the row. This is a deliberate
    forward-only contract change from the older dynamic
    ``selected_<signal_label>`` shape; historical ``explanations`` rows
    on the event store are immutable per ADR 0011 and retain their
    pre-change keys.

    ``extra_triggering_values_fn`` — when provided, is called with the
    primary candidate 3-tuple ``(symbol, latest_close, signal_value)`` in
    the entry-success branch only. The returned dict is merged into
    ``triggering_values`` *after* the standard keys are set, so it can
    add new keys (e.g. ``selected_channel_high`` for Donchian) but must
    not rely on overriding the standard ones. This is the approved (and
    only approved) extension point for per-strategy entry fields that do
    not fit the standard ``selected_signal_*`` shape; mirrors how
    ``entry_narrative_fn`` is supplied as a closure over strategy-specific
    state (e.g. a ``channel_highs_by_symbol`` map).
    """
    ranking_payload: list[dict[str, Any]] | None = None
    if ranking_enabled:
        ranking_payload = [
            {
                "symbol": sym,
                "signal_label": signal_label,
                "signal_value": value,
                "latest_close": close,
            }
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
        triggering_values: dict[str, Any] = {
            "selected_symbol": primary[0],
            "selected_signal_label": signal_label,
            "selected_signal_value": primary[2],
            "selected_close": primary[1],
        }
        if extra_triggering_values_fn is not None:
            triggering_values.update(extra_triggering_values_fn(primary))
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule=entry_rule,
                narrative=entry_narrative_fn(primary, entry_intents),
                triggering_values=triggering_values,
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


def rank_candidates(
    candidates: list[tuple[str, float, float]],
    *,
    key_fn: Callable[[tuple[str, float, float]], tuple[Any, ...]],
) -> list[tuple[str, float, float]]:
    """Stable sort of cross-sectional entry candidates.

    Canonical helper consumed by every cross-sectional strategy in place of
    a per-strategy ``_rank_candidates`` shim or inline ``sorted(...)`` call.
    The helper is intentionally minimal: each strategy supplies a ``key_fn``
    lambda that encodes (a) which tuple element is the signal value and
    (b) whether to rank ascending (``(c[2], c[0])``) or descending
    (``(-c[2], c[0])``). Symbol-ascending is the canonical tiebreak.

    Returns a new list; the input list is not mutated. ``sorted`` is stable,
    so callers that hand in a partially-ordered list keep that order on ties
    inside the ``key_fn`` output.
    """
    return sorted(candidates, key=key_fn)
