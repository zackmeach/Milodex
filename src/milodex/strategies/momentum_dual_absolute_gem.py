"""Daily Dual Momentum (GEM) — single-asset weekly rotation.

Implements the ``momentum`` family's ``daily.dual_absolute`` template per
``docs/strategy-families.md``. Single-asset rotation across a small
multi-asset universe with two stacked momentum filters:

1. **Relative momentum:** rank the risk-on members of the universe by
   trailing ``momentum_lookback`` return; pick the top.
2. **Absolute momentum:** only hold the relative winner if its trailing
   return also exceeds the ``risk_off_symbol``'s trailing return;
   otherwise hold the risk-off asset.

Single-asset by hardcoded invariant — like the ``regime`` family. Phase 1
daily-swing fit: weekly Friday rebalance (the tightest faithful adaptation
of Antonacci's monthly GEM). No intra-period stops by published design;
holds naturally cap at five trading days via the rebalance cadence.

Evidence: Antonacci 2014 *Dual Momentum Investing*; Antonacci 2017
"Risk Premia Harvesting Through Dual Momentum" (JPM).

Daily-swing fit caveat: the edge degradation from monthly→weekly is
expected to be modest — GEM's edge is largely about avoiding sustained
equity drawdowns, and the rebalance frequency change affects that signal
less than it would affect a momentum-continuation signal.
"""

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

_VALID_SIZING_RULES = {"single_asset_full_allocation"}


class MomentumDualAbsoluteGemStrategy(Strategy):
    """Antonacci-style dual-momentum single-asset rotation."""

    family = "momentum"
    template = "daily.dual_absolute"
    parameter_specs = (
        StrategyParameterSpec("risk_off_symbol", expected_types=(str,)),
        StrategyParameterSpec("momentum_lookback", expected_types=(int,)),
        StrategyParameterSpec("rebalance_weekday", expected_types=(int,)),
        StrategyParameterSpec("allocation_pct", expected_types=(int, float)),
        StrategyParameterSpec("sizing_rule", expected_types=(str,)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars

        parameters = _validated_parameters(context)
        risk_off = parameters["risk_off_symbol"]
        if risk_off not in {symbol.upper() for symbol in context.universe}:
            msg = (
                f"risk_off_symbol {risk_off!r} must be a member of the strategy universe "
                f"(got universe={list(context.universe)!r})"
            )
            raise ValueError(msg)

        universe_symbols = {symbol.upper() for symbol in context.universe}
        open_positions = {
            symbol.upper(): float(quantity)
            for symbol, quantity in context.positions.items()
            if float(quantity) > 0 and symbol.upper() in universe_symbols
        }
        bars_by_symbol = {
            symbol.upper(): barset for symbol, barset in context.bars_by_symbol.items()
        }

        latest_weekday = _latest_weekday(bars_by_symbol)
        if latest_weekday is None or latest_weekday != parameters["rebalance_weekday"]:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative=(
                        f"non-rebalance bar (weekday={latest_weekday}); "
                        f"GEM rotation only fires on weekday={parameters['rebalance_weekday']}"
                    ),
                    triggering_values={"latest_weekday": latest_weekday},
                    threshold={"rebalance_weekday": parameters["rebalance_weekday"]},
                ),
            )

        returns_by_symbol = _trailing_returns(
            universe=context.universe,
            bars_by_symbol=bars_by_symbol,
            momentum_lookback=parameters["momentum_lookback"],
        )

        # Pick the top risk-on candidate by trailing return.
        risk_on_candidates = {
            sym: ret
            for sym, ret in returns_by_symbol.items()
            if sym != risk_off and ret is not None
        }
        risk_off_return = returns_by_symbol.get(risk_off)

        if not risk_on_candidates or risk_off_return is None:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative=(
                        "insufficient history to evaluate dual_absolute on this bar — "
                        f"risk_on candidates with returns: {len(risk_on_candidates)}, "
                        f"risk_off return: {'None' if risk_off_return is None else 'ok'}"
                    ),
                    triggering_values={"momentum_lookback": parameters["momentum_lookback"]},
                ),
            )

        top_risk_on = max(risk_on_candidates.items(), key=lambda kv: kv[1])
        target = top_risk_on[0] if top_risk_on[1] > risk_off_return else risk_off

        # Single-asset invariant: at most one open position. If we hold
        # something other than the target, sell it; then buy the target.
        intents: list[TradeIntent] = []
        ranking_payload = [
            {"symbol": sym, "trailing_return": ret}
            for sym, ret in sorted(
                returns_by_symbol.items(),
                key=lambda kv: (kv[1] is None, -(kv[1] if kv[1] is not None else 0.0)),
            )
        ]

        currently_held = next(iter(open_positions), None)
        if currently_held == target:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="momentum.dual_absolute_hold",
                    narrative=(
                        f"holding {target} unchanged this rebalance "
                        f"(top risk-on={top_risk_on[0]} ret={top_risk_on[1]:.2%}; "
                        f"risk-off ret={risk_off_return:.2%})"
                    ),
                    triggering_values={
                        "target": target,
                        "top_risk_on": top_risk_on[0],
                        "top_risk_on_return": top_risk_on[1],
                        "risk_off_return": risk_off_return,
                    },
                    ranking=ranking_payload,
                ),
            )

        if currently_held is not None:
            intents.append(
                TradeIntent(
                    symbol=currently_held,
                    side=OrderSide.SELL,
                    quantity=float(open_positions[currently_held]),
                    order_type=OrderType.MARKET,
                )
            )

        target_barset = bars_by_symbol.get(target)
        if target_barset is None:
            # Cannot enter without target bars — emit only the exit (if any).
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative=(f"target {target} resolved but no bars available to size entry"),
                    triggering_values={"target": target},
                ),
            )
        target_close = float(target_barset.to_dataframe()["close"].astype(float).iloc[-1])
        shares = shares_for_notional_pct(
            equity=context.equity,
            notional_pct=parameters["allocation_pct"],
            unit_price=target_close,
        )
        if shares > 0:
            intents.append(
                TradeIntent(
                    symbol=target,
                    side=OrderSide.BUY,
                    quantity=float(shares),
                    order_type=OrderType.MARKET,
                )
            )

        narrative = (
            f"GEM rotation: target {target} "
            f"(top risk-on={top_risk_on[0]} ret={top_risk_on[1]:.2%}; "
            f"risk-off ret={risk_off_return:.2%}); "
            f"prior position={currently_held or 'none'}"
        )
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule="momentum.dual_absolute_rotation",
                narrative=narrative,
                triggering_values={
                    "target": target,
                    "top_risk_on": top_risk_on[0],
                    "top_risk_on_return": top_risk_on[1],
                    "risk_off_return": risk_off_return,
                    "prior_position": currently_held or "",
                },
                threshold={"momentum_lookback": parameters["momentum_lookback"]},
                ranking=ranking_payload,
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    risk_off_symbol = str(required("risk_off_symbol")).upper()
    if not risk_off_symbol:
        msg = "risk_off_symbol must be a non-empty string"
        raise ValueError(msg)

    momentum_lookback = int(required("momentum_lookback"))
    if momentum_lookback < 2:
        msg = "momentum_lookback must be >= 2"
        raise ValueError(msg)

    rebalance_weekday = int(required("rebalance_weekday"))
    if not 0 <= rebalance_weekday <= 4:
        msg = f"rebalance_weekday must be 0..4 (Mon=0, Fri=4), got {rebalance_weekday!r}"
        raise ValueError(msg)

    allocation_pct = float(required("allocation_pct"))
    if not 0 < allocation_pct <= 1:
        msg = f"allocation_pct must be in (0, 1], got {allocation_pct!r}"
        raise ValueError(msg)

    sizing_rule = str(required("sizing_rule"))
    if sizing_rule not in _VALID_SIZING_RULES:
        msg = f"sizing_rule must be one of {sorted(_VALID_SIZING_RULES)}, got {sizing_rule!r}"
        raise ValueError(msg)

    return {
        "risk_off_symbol": risk_off_symbol,
        "momentum_lookback": momentum_lookback,
        "rebalance_weekday": rebalance_weekday,
        "allocation_pct": allocation_pct,
        "sizing_rule": sizing_rule,
    }


def _latest_weekday(bars_by_symbol: dict[str, BarSet]) -> int | None:
    latest: pd.Timestamp | None = None
    for barset in bars_by_symbol.values():
        df = barset.to_dataframe()
        if df.empty:
            continue
        candidate = df["timestamp"].iloc[-1]
        if latest is None or candidate > latest:
            latest = candidate
    if latest is None:
        return None
    return int(pd.Timestamp(latest).weekday())


def _trailing_returns(
    *,
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    momentum_lookback: int,
) -> dict[str, float | None]:
    """Return ``{symbol: trailing_return_or_None}`` over the lookback window.

    A symbol with too short a history yields None — the strategy treats
    that as "no claim about momentum" and excludes the symbol from
    ranking. The risk-off symbol getting None aborts the cycle (no signal),
    since the absolute-momentum floor cannot be evaluated.
    """
    out: dict[str, float | None] = {}
    for symbol in universe:
        normalized = symbol.upper()
        barset = bars_by_symbol.get(normalized)
        if barset is None:
            out[normalized] = None
            continue
        closes = barset.to_dataframe()["close"].astype(float)
        if len(closes) < momentum_lookback + 1:
            out[normalized] = None
            continue
        reference = float(closes.iloc[-1 - momentum_lookback])
        if reference <= 0:
            out[normalized] = None
            continue
        out[normalized] = float(closes.iloc[-1]) / reference - 1.0
    return out
