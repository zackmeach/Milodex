"""Daily cross-sectional rank-rotation momentum strategy.

Implements the ``momentum`` family's ``daily.xsec_rotation`` template per
``docs/strategy-families.md``. Where ``daily.tsmom`` evaluates each symbol
on its own absolute return, ``daily.xsec_rotation`` ranks symbols against
each other on each weekly rebalance and holds the top-N.

Cadence: ranking + turnover decisions only fire on bars whose weekday
equals ``rebalance_weekday`` (default Friday → next Monday open). On
non-rebalance bars the strategy returns ``no_signal`` for entries and rank
exits, but **stops are evaluated daily** so a hard loss escapes mid-week
without waiting for the next rebalance. The runner is unchanged — the
cadence is enforced inside ``evaluate()``.

Evidence: Jegadeesh & Titman 1993 *JoF*; Asness/Moskowitz/Pedersen 2013
*JoF*; Faber 2010 SSRN; Antonacci 2014 *Dual Momentum Investing*.

Daily-swing fit caveat: the published research uses monthly rebalances
with 1–12 month holds. Weekly rebalance is the tightest faithful daily-
swing adaptation; expect ~30–50% of the published edge to survive the
hold compression.
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
from milodex.strategies.daily_cross_sectional import (
    market_regime_is_bullish as _market_regime_is_bullish,
)

_VALID_SIZING_RULES = {"equal_notional", "fixed_notional"}
_VALID_RANKING_METRICS = {"xsec_return_descending"}


class MomentumXsecRotationStrategy(Strategy):
    """Cross-sectional rank-rotation momentum strategy."""

    family = "momentum"
    template = "daily.xsec_rotation"
    parameter_specs = (
        StrategyParameterSpec("ranking_lookback", expected_types=(int,)),
        StrategyParameterSpec("target_positions", expected_types=(int,)),
        StrategyParameterSpec("exit_outside_top_n", expected_types=(int,)),
        StrategyParameterSpec("rebalance_weekday", expected_types=(int,)),
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

        universe_symbols = {symbol.upper() for symbol in context.universe}
        open_positions = {
            symbol.upper(): float(quantity)
            for symbol, quantity in context.positions.items()
            if float(quantity) > 0 and symbol.upper() in universe_symbols
        }
        bars_by_symbol = {
            symbol.upper(): barset for symbol, barset in context.bars_by_symbol.items()
        }

        # Stops fire daily. Rank-based entries / rank exits fire weekly.
        stop_intents = _stop_intents(open_positions, bars_by_symbol, context, parameters)
        intents: list[TradeIntent] = list(intent for intent, _ in stop_intents)

        # Determine rebalance cadence from the latest bar's weekday.
        latest_weekday = _latest_weekday(bars_by_symbol)
        is_rebalance = (
            latest_weekday is not None and latest_weekday == parameters["rebalance_weekday"]
        )

        if stop_intents:
            first_intent, first_rule = stop_intents[0]
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule=first_rule,
                    narrative=_exit_narrative(first_rule, first_intent.symbol, parameters),
                    triggering_values={"symbol": first_intent.symbol},
                    threshold=_exit_threshold(first_rule, parameters),
                ),
            )

        if not is_rebalance:
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative=(
                        f"non-rebalance bar (weekday={latest_weekday}); "
                        f"ranking only fires on weekday={parameters['rebalance_weekday']}"
                    ),
                    triggering_values={"latest_weekday": latest_weekday},
                    threshold={"rebalance_weekday": parameters["rebalance_weekday"]},
                ),
            )

        ranking = _rank_universe(
            universe=context.universe,
            bars_by_symbol=bars_by_symbol,
            parameters=parameters,
        )
        rank_by_symbol = {entry["symbol"]: entry["rank"] for entry in ranking}

        # Rank exits: held symbols whose rank > exit_outside_top_n leave.
        rank_exit_symbols: list[str] = []
        # Sorted-symbol iteration so the emitted rank-exit SELL order is
        # deterministic regardless of the positions-mapping iteration order.
        for symbol in sorted(open_positions):
            rank = rank_by_symbol.get(symbol)
            if rank is None or rank > parameters["exit_outside_top_n"]:
                rank_exit_symbols.append(symbol)
                intents.append(
                    TradeIntent(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=float(open_positions[symbol]),
                        order_type=OrderType.MARKET,
                    )
                )

        remaining_after_exits = set(open_positions) - set(rank_exit_symbols)

        rejected_alternatives: list[dict[str, Any]] = []
        regime_bullish = _market_regime_is_bullish(bars_by_symbol, parameters)

        # Entries: top `target_positions` symbols not already held, subject to
        # capacity and per-symbol MA filter (when configured).
        entries: list[tuple[str, float, float]] = []  # (symbol, latest_close, ranking_return)
        if regime_bullish:
            target = ranking[: parameters["target_positions"]]
            capacity = max(
                0,
                parameters["max_concurrent_positions"] - len(remaining_after_exits),
            )
            for entry in target:
                if len(entries) >= capacity:
                    rejected_alternatives.append(
                        {"symbol": entry["symbol"], "reason": "capacity full"}
                    )
                    continue
                symbol = entry["symbol"]
                if symbol in remaining_after_exits:
                    rejected_alternatives.append({"symbol": symbol, "reason": "already held"})
                    continue
                if parameters["ma_filter_length"] > 0:
                    barset = bars_by_symbol[symbol]
                    closes = barset.to_dataframe()["close"].astype(float)
                    sma = float(closes.tail(parameters["ma_filter_length"]).mean())
                    if float(closes.iloc[-1]) <= sma:
                        rejected_alternatives.append(
                            {
                                "symbol": symbol,
                                "reason": (
                                    f"close {float(closes.iloc[-1]):.2f} not above "
                                    f"{parameters['ma_filter_length']}-SMA {sma:.2f}"
                                ),
                            }
                        )
                        continue
                entries.append((symbol, entry["latest_close"], entry["ranking_return"]))

        for symbol, latest_close, _ranking_return in entries:
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

        ranking_payload = [
            {
                "symbol": entry["symbol"],
                "rank": entry["rank"],
                "ranking_return": entry["ranking_return"],
            }
            for entry in ranking
        ]

        if rank_exit_symbols and not entries:
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="momentum.xsec_exit",
                    narrative=(
                        f"weekly rebalance: dropping {len(rank_exit_symbols)} holding(s) "
                        f"out of top {parameters['exit_outside_top_n']}: "
                        f"{', '.join(rank_exit_symbols)}"
                    ),
                    triggering_values={"exited_count": len(rank_exit_symbols)},
                    threshold={"exit_outside_top_n": parameters["exit_outside_top_n"]},
                    ranking=ranking_payload,
                    rejected_alternatives=rejected_alternatives,
                ),
            )

        entry_intents = [intent for intent in intents if intent.side == OrderSide.BUY]
        if entry_intents:
            primary = entries[0]
            narrative = (
                f"weekly rebalance: top-{parameters['target_positions']} entries "
                f"by {parameters['ranking_lookback']}-day return; "
                f"primary {primary[0]} ranking_return {primary[2]:.2%}; "
                f"buy {len(entry_intents)} candidate(s): "
                f"{', '.join(intent.symbol for intent in entry_intents)}"
            )
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="momentum.xsec_entry",
                    narrative=narrative,
                    triggering_values={
                        "selected_symbol": primary[0],
                        "selected_ranking_return": primary[2],
                        "selected_close": primary[1],
                    },
                    threshold={
                        "ranking_lookback": parameters["ranking_lookback"],
                        "target_positions": parameters["target_positions"],
                    },
                    ranking=ranking_payload,
                    rejected_alternatives=rejected_alternatives,
                ),
            )

        narrative = "weekly rebalance: no entries — " + (
            "regime filter bearish"
            if not regime_bullish
            else f"{len(rejected_alternatives)} top-ranked candidate(s) rejected"
        )
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=narrative,
                triggering_values={"universe_size": len(context.universe)},
                threshold={
                    "ranking_lookback": parameters["ranking_lookback"],
                    "target_positions": parameters["target_positions"],
                },
                ranking=ranking_payload,
                rejected_alternatives=rejected_alternatives,
            ),
        )


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    ranking_lookback = int(required("ranking_lookback"))
    if ranking_lookback < 2:
        msg = "ranking_lookback must be >= 2"
        raise ValueError(msg)

    target_positions = int(required("target_positions"))
    if target_positions < 1:
        msg = "target_positions must be >= 1"
        raise ValueError(msg)

    exit_outside_top_n = int(required("exit_outside_top_n"))
    if exit_outside_top_n < target_positions:
        msg = (
            "exit_outside_top_n must be >= target_positions "
            "(holding outside one's own target rank with no buffer is unstable)"
        )
        raise ValueError(msg)

    rebalance_weekday = int(required("rebalance_weekday"))
    if not 0 <= rebalance_weekday <= 4:
        msg = f"rebalance_weekday must be 0..4 (Mon=0, Fri=4), got {rebalance_weekday!r}"
        raise ValueError(msg)

    ma_filter_length = int(required("ma_filter_length"))
    if ma_filter_length < 0:
        msg = "ma_filter_length must be >= 0 (0 disables the filter)"
        raise ValueError(msg)

    max_hold_days = int(required("max_hold_days"))
    if max_hold_days < 1:
        msg = "max_hold_days must be >= 1"
        raise ValueError(msg)

    max_concurrent_positions = int(required("max_concurrent_positions"))
    if max_concurrent_positions < target_positions:
        msg = (
            "max_concurrent_positions must be >= target_positions "
            "(otherwise the strategy cannot hold its declared target set)"
        )
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
        "ranking_lookback": ranking_lookback,
        "target_positions": target_positions,
        "exit_outside_top_n": exit_outside_top_n,
        "rebalance_weekday": rebalance_weekday,
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


def _latest_weekday(bars_by_symbol: dict[str, BarSet]) -> int | None:
    """Return the latest bar's weekday across the universe (0=Mon, 4=Fri).

    Cross-sectional strategies need a single rebalance bar across the basket.
    Picking the max-of-latest-timestamps across the universe gives the
    "most recent calendar bar anyone in the universe has seen" — which under
    Phase 1's daily-bar guarantee is the same for every symbol on a given
    day. Returns None if the universe has no bars at all.
    """
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


def _rank_universe(
    *,
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return universe members ranked by trailing return, descending.

    Each entry: ``{symbol, rank, ranking_return, latest_close}``. Symbols
    with insufficient history are ranked at the bottom (no return = no
    momentum claim). Ranks are 1-indexed.
    """
    rows: list[tuple[str, float | None, float]] = []
    for symbol in universe:
        normalized = symbol.upper()
        barset = bars_by_symbol.get(normalized)
        if barset is None:
            continue
        df = barset.to_dataframe()
        if df.empty:
            continue
        closes = df["close"].astype(float)
        latest_close = float(closes.iloc[-1])
        if len(closes) < parameters["ranking_lookback"] + 1:
            rows.append((normalized, None, latest_close))
            continue
        reference = float(closes.iloc[-1 - parameters["ranking_lookback"]])
        if reference <= 0:
            rows.append((normalized, None, latest_close))
            continue
        ranking_return = latest_close / reference - 1.0
        rows.append((normalized, ranking_return, latest_close))

    # Symbols with None return rank last; tie-break alphabetically.
    rows.sort(
        key=lambda row: (
            0 if row[1] is not None else 1,
            -(row[1] if row[1] is not None else 0.0),
            row[0],
        )
    )
    return [
        {
            "symbol": sym,
            "rank": idx + 1,
            "ranking_return": ret if ret is not None else float("nan"),
            "latest_close": close,
        }
        for idx, (sym, ret, close) in enumerate(rows)
    ]


def _stop_intents(
    open_positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    parameters: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    """Daily stops — fire on every bar (rebalance or not).

    Precedence: stop_loss > max_hold. Rank-based exits are handled in the
    main evaluate() flow on rebalance bars.
    """
    results: list[tuple[TradeIntent, str]] = []
    # Sorted iteration keeps the stop ordering (and thus stop_intents[0], the
    # reported primary stop) deterministic across positions-mapping orderings.
    for symbol in sorted(open_positions):
        quantity = open_positions[symbol]
        barset = bars_by_symbol.get(symbol)
        if barset is None:
            continue
        df = barset.to_dataframe()
        if df.empty:
            continue
        latest_close = float(df["close"].astype(float).iloc[-1])

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
    if rule == "momentum.stop_loss":
        return (
            f"close breached stop_loss {parameters['stop_loss_pct']:.2%} from entry → sell {symbol}"
        )
    if rule == "momentum.max_hold":
        return f"held >= max_hold_days {parameters['max_hold_days']} → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, parameters: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "momentum.stop_loss":
        return {"stop_loss_pct": parameters["stop_loss_pct"]}
    if rule == "momentum.max_hold":
        return {"max_hold_days": parameters["max_hold_days"]}
    return {}
