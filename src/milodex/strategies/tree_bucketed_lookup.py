"""Decider B — daily **decision-tree / bucketed-lookup** decider.

A second deterministic, *non-rule* paradigm behind the unchanged ``Strategy``
contract (decision-layer seam proof, axis 3) — deliberately **structurally
different** from the linear scored decider (Decider A). Where A computes a
continuous weighted score and ranks, B discretizes the same two features into
buckets and traverses a fixed depth-2 binary tree to a leaf action:

    root:  momentum >= momentum_split ?
      yes (high-momentum node):  rsi <= rsi_split_strong ?
            yes -> leaf "strong_buy"   (enter)
            no  -> leaf "trend_follow"  (enter)
      no  (low/mid-momentum node): rsi <= rsi_split_dip ?
            yes -> leaf "dip_buy"       (enter)
            no  -> leaf "neutral_skip"  (skip)

The level-2 RSI edge **differs by the first branch** (``rsi_split_strong`` vs.
``rsi_split_dip``) — i.e. the second decision boundary depends on the path, the
hallmark of a tree rather than two ANDed thresholds. Every classification
traverses two split levels and the tree exposes four distinct leaves (≥3 are
reached over any real window), so by the brief's own definition this is not a
rule (a depth-1 / 2-leaf tree would be).

Split edges and the leaf→action table live in the YAML, so ``config_hash``
covers reproducibility — no model artifact. Features come from the shared
:mod:`milodex.strategies._decider_features` kit (same inputs as Decider A).

**Capability-proof only.** ``stage: backtest``, lifecycle-exempt, "mechanics
not alpha". Negative / random performance is the expected, correct result.
"""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies._decider_features import trailing_return, wilder_rsi
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
)
from milodex.strategies.daily_cross_sectional import normalize_universe_and_positions

_DECIDER_KIND = "tree"
_LEAVES = ("strong_buy", "trend_follow", "dip_buy", "neutral_skip")


class TreeBucketedLookupStrategy(Strategy):
    """Decision-tree / bucketed-lookup decider (non-rule, deterministic)."""

    family = "tree"
    template = "daily.bucketed_lookup"
    parameter_specs = (
        StrategyParameterSpec("momentum_lookback", expected_types=(int,)),
        StrategyParameterSpec("rsi_lookback", expected_types=(int,)),
        StrategyParameterSpec("momentum_split", expected_types=(int, float)),
        StrategyParameterSpec("rsi_split_strong", expected_types=(int, float)),
        StrategyParameterSpec("rsi_split_dip", expected_types=(int, float)),
        StrategyParameterSpec("leaf_actions", expected_types=(dict,)),
        StrategyParameterSpec("target_positions", expected_types=(int,)),
        StrategyParameterSpec("max_concurrent_positions", expected_types=(int,)),
        StrategyParameterSpec("per_position_notional_pct", expected_types=(int, float)),
        StrategyParameterSpec("stop_loss_pct", expected_types=(int, float)),
        StrategyParameterSpec("max_hold_days", expected_types=(int,)),
    )

    def evaluate(self, bars: BarSet, context: StrategyContext) -> StrategyDecision:
        _ = bars
        params = _validated_parameters(context)
        norm = normalize_universe_and_positions(context)
        open_positions = norm.open_positions
        bars_by_symbol = norm.bars_by_symbol

        # Mechanical risk exits fire daily (rule-shaped; a stop is a threshold).
        stop_details = _stop_intents(open_positions, bars_by_symbol, context, params)
        if stop_details:
            first_intent, first_rule = stop_details[0]
            return StrategyDecision(
                intents=[intent for intent, _ in stop_details],
                reasoning=DecisionReasoning(
                    rule=first_rule,
                    narrative=_exit_narrative(first_rule, first_intent.symbol, params),
                    triggering_values={"symbol": first_intent.symbol},
                    threshold=_exit_threshold(first_rule, params),
                ),
            )

        classified = _classify_universe(context.universe, bars_by_symbol, params)
        ranking_payload = [
            {
                "symbol": row["symbol"],
                "leaf": row["leaf"],
                "action": row["action"],
                "momentum": row["momentum"],
                "rsi": row["rsi"],
            }
            for row in classified
        ]
        leaf_by_symbol = {row["symbol"]: row for row in classified}

        intents: list[TradeIntent] = []
        rejected_alternatives: list[dict[str, Any]] = []

        # Leaf exits: held names whose current leaf is a "skip" action (or which
        # can no longer be classified) leave the book. Sorted-symbol iteration
        # keeps the emitted SELL ordering deterministic regardless of the
        # positions-mapping iteration order.
        leaf_exit_symbols: list[str] = []
        for symbol in sorted(open_positions):
            row = leaf_by_symbol.get(symbol)
            if row is None or row["action"] != "enter":
                leaf_exit_symbols.append(symbol)
                intents.append(
                    TradeIntent(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=float(open_positions[symbol]),
                        order_type=OrderType.MARKET,
                    )
                )
        remaining_after_exits = set(open_positions) - set(leaf_exit_symbols)
        capacity = max(0, params["max_concurrent_positions"] - len(remaining_after_exits))

        # Entry candidates: "enter"-leaf symbols, ranked by leaf priority (desc)
        # then momentum (desc) then symbol (asc) for a deterministic order.
        enter_rows = [row for row in classified if row["action"] == "enter"]
        enter_rows.sort(key=lambda row: (-row["priority"], -row["momentum"], row["symbol"]))

        entries: list[dict[str, Any]] = []
        for row in enter_rows:
            symbol = row["symbol"]
            if symbol in remaining_after_exits:
                rejected_alternatives.append({"symbol": symbol, "reason": "already held"})
                continue
            if len(entries) >= min(params["target_positions"], capacity):
                rejected_alternatives.append({"symbol": symbol, "reason": "capacity full"})
                continue
            shares = shares_for_notional_pct(
                equity=context.equity,
                notional_pct=params["per_position_notional_pct"],
                unit_price=row["latest_close"],
            )
            if shares <= 0:
                rejected_alternatives.append(
                    {
                        "symbol": symbol,
                        "reason": (
                            f"equity {context.equity:.2f} cannot afford one share at "
                            f"{row['latest_close']:.2f}"
                        ),
                    }
                )
                continue
            entries.append(row)
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=float(shares),
                    order_type=OrderType.MARKET,
                )
            )

        if entries:
            primary = entries[0]
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="tree.bucketed_lookup.entry",
                    narrative=(
                        f"tree routed {primary['symbol']} to leaf '{primary['leaf']}' "
                        f"(enter); buy {len([i for i in intents if i.side == OrderSide.BUY])} "
                        f"of {len(enter_rows)} enter-leaf candidate(s)"
                    ),
                    triggering_values={
                        "selected_symbol": primary["symbol"],
                        "selected_leaf": primary["leaf"],
                        "selected_close": primary["latest_close"],
                    },
                    threshold={
                        "momentum_split": params["momentum_split"],
                        "rsi_split_strong": params["rsi_split_strong"],
                        "rsi_split_dip": params["rsi_split_dip"],
                    },
                    ranking=ranking_payload,
                    rejected_alternatives=rejected_alternatives,
                    kind=_DECIDER_KIND,
                    decision_path=primary["path"],
                ),
            )

        if leaf_exit_symbols:
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="tree.bucketed_lookup.leaf_exit",
                    narrative=(
                        f"tree routed {len(leaf_exit_symbols)} holding(s) to a non-enter "
                        f"leaf: {', '.join(leaf_exit_symbols)}"
                    ),
                    triggering_values={"exited_count": len(leaf_exit_symbols)},
                    ranking=ranking_payload,
                    kind=_DECIDER_KIND,
                ),
            )

        narrative = (
            "no enter-leaf candidate available"
            if not classified
            else f"all {len(classified)} classified symbol(s) routed to skip/held leaves"
        )
        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=narrative,
                triggering_values={"universe_size": len(context.universe)},
                ranking=ranking_payload,
                kind=_DECIDER_KIND,
            ),
        )


def _classify_universe(
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Classify each universe member through the tree to a leaf.

    Each row: ``{symbol, leaf, action, priority, momentum, rsi, latest_close,
    path}``. Symbols without enough history to compute both features are
    dropped. Returned in deterministic symbol order.
    """
    rows: list[dict[str, Any]] = []
    for symbol in sorted({sym.upper() for sym in universe}):
        barset = bars_by_symbol.get(symbol)
        if barset is None:
            continue
        frame = barset.to_dataframe()
        if frame.empty:
            continue
        closes = frame["close"].astype(float)
        momentum = trailing_return(closes, params["momentum_lookback"])
        rsi = wilder_rsi(closes, params["rsi_lookback"])
        if momentum is None or rsi is None:
            continue
        leaf, path = _traverse_tree(momentum, rsi, params)
        action_spec = params["leaf_actions"][leaf]
        rows.append(
            {
                "symbol": symbol,
                "leaf": leaf,
                "action": action_spec["action"],
                "priority": action_spec["priority"],
                "momentum": float(momentum),
                "rsi": float(rsi),
                "latest_close": float(closes.iloc[-1]),
                "path": path,
            }
        )
    return rows


def _traverse_tree(
    momentum: float, rsi: float, params: dict[str, Any]
) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Traverse the fixed depth-2 tree to a leaf, recording the path.

    Returns ``(leaf_name, path_steps)`` where ``path_steps`` is an ordered
    tuple of the two split decisions taken — exactly the ``decision_path`` the
    reasoning records for the selected symbol.
    """
    if momentum >= params["momentum_split"]:
        root_branch = "high_momentum"
        rsi_edge = params["rsi_split_strong"]
        leaf = "strong_buy" if rsi <= rsi_edge else "trend_follow"
    else:
        root_branch = "low_momentum"
        rsi_edge = params["rsi_split_dip"]
        leaf = "dip_buy" if rsi <= rsi_edge else "neutral_skip"

    path = (
        {
            "node": "momentum",
            "feature": "momentum",
            "value": float(momentum),
            "edge": float(params["momentum_split"]),
            "branch": root_branch,
        },
        {
            "node": "rsi",
            "feature": "rsi",
            "value": float(rsi),
            "edge": float(rsi_edge),
            "branch": "oversold" if rsi <= rsi_edge else "not_oversold",
            "leaf": leaf,
        },
    )
    return leaf, path


def _stop_intents(
    open_positions: dict[str, float],
    bars_by_symbol: dict[str, BarSet],
    context: StrategyContext,
    params: dict[str, Any],
) -> list[tuple[TradeIntent, str]]:
    """Daily mechanical exits (stop_loss > max_hold), mirroring the rule families."""
    results: list[tuple[TradeIntent, str]] = []
    # Sorted iteration keeps the stop ordering (and thus the reported primary
    # stop in the reasoning) deterministic across positions-mapping orderings.
    for symbol in sorted(open_positions):
        quantity = open_positions[symbol]
        barset = bars_by_symbol.get(symbol)
        if barset is None:
            continue
        frame = barset.to_dataframe()
        if frame.empty:
            continue
        latest_close = float(frame["close"].astype(float).iloc[-1])

        exit_rule: str | None = None
        state = context.entry_state.get(symbol) if context.entry_state else None
        if state is not None:
            entry_price = state.get("entry_price")
            held_days = state.get("held_days")
            if (
                isinstance(entry_price, int | float)
                and entry_price > 0
                and latest_close <= float(entry_price) * (1 - params["stop_loss_pct"])
            ):
                exit_rule = "tree.stop_loss"
            elif isinstance(held_days, int | float) and int(held_days) >= params["max_hold_days"]:
                exit_rule = "tree.max_hold"

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


def _exit_narrative(rule: str, symbol: str, params: dict[str, Any]) -> str:
    if rule == "tree.stop_loss":
        return f"close breached stop_loss {params['stop_loss_pct']:.2%} from entry → sell {symbol}"
    if rule == "tree.max_hold":
        return f"held >= max_hold_days {params['max_hold_days']} → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, params: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "tree.stop_loss":
        return {"stop_loss_pct": params["stop_loss_pct"]}
    if rule == "tree.max_hold":
        return {"max_hold_days": params["max_hold_days"]}
    return {}


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    momentum_lookback = int(required("momentum_lookback"))
    if momentum_lookback < 1:
        msg = "momentum_lookback must be >= 1"
        raise ValueError(msg)
    rsi_lookback = int(required("rsi_lookback"))
    if rsi_lookback < 2:
        msg = "rsi_lookback must be >= 2"
        raise ValueError(msg)

    momentum_split = float(required("momentum_split"))
    rsi_split_strong = float(required("rsi_split_strong"))
    rsi_split_dip = float(required("rsi_split_dip"))
    for name, value in (
        ("rsi_split_strong", rsi_split_strong),
        ("rsi_split_dip", rsi_split_dip),
    ):
        if not 0 < value < 100:
            msg = f"{name} must be in (0, 100), got {value!r}"
            raise ValueError(msg)

    target_positions = int(required("target_positions"))
    if target_positions < 1:
        msg = "target_positions must be >= 1"
        raise ValueError(msg)
    max_concurrent_positions = int(required("max_concurrent_positions"))
    if max_concurrent_positions < target_positions:
        msg = "max_concurrent_positions must be >= target_positions"
        raise ValueError(msg)

    per_position_notional_pct = float(required("per_position_notional_pct"))
    if not 0 < per_position_notional_pct <= 1:
        msg = f"per_position_notional_pct must be in (0, 1], got {per_position_notional_pct!r}"
        raise ValueError(msg)

    stop_loss_pct = float(required("stop_loss_pct"))
    if stop_loss_pct <= 0:
        msg = f"stop_loss_pct must be > 0, got {stop_loss_pct!r}"
        raise ValueError(msg)

    max_hold_days = int(required("max_hold_days"))
    if max_hold_days < 1:
        msg = "max_hold_days must be >= 1"
        raise ValueError(msg)

    leaf_actions = required("leaf_actions")
    if not isinstance(leaf_actions, dict):
        msg = "leaf_actions must be a mapping of leaf-name -> {action, priority}"
        raise ValueError(msg)
    missing = set(_LEAVES) - set(leaf_actions)
    if missing:
        msg = f"leaf_actions must define every leaf {_LEAVES}; missing {sorted(missing)}"
        raise ValueError(msg)
    normalized_leaves: dict[str, dict[str, Any]] = {}
    for leaf in _LEAVES:
        spec = leaf_actions[leaf]
        if not isinstance(spec, dict) or "action" not in spec or "priority" not in spec:
            msg = f"leaf_actions[{leaf!r}] must be a mapping with 'action' and 'priority'"
            raise ValueError(msg)
        action = str(spec["action"])
        if action not in {"enter", "skip"}:
            msg = f"leaf_actions[{leaf!r}].action must be 'enter' or 'skip', got {action!r}"
            raise ValueError(msg)
        normalized_leaves[leaf] = {"action": action, "priority": int(spec["priority"])}
    if not any(spec["action"] == "enter" for spec in normalized_leaves.values()):
        msg = "leaf_actions must mark at least one leaf as 'enter'"
        raise ValueError(msg)

    return {
        "momentum_lookback": momentum_lookback,
        "rsi_lookback": rsi_lookback,
        "momentum_split": momentum_split,
        "rsi_split_strong": rsi_split_strong,
        "rsi_split_dip": rsi_split_dip,
        "leaf_actions": normalized_leaves,
        "target_positions": target_positions,
        "max_concurrent_positions": max_concurrent_positions,
        "per_position_notional_pct": per_position_notional_pct,
        "stop_loss_pct": stop_loss_pct,
        "max_hold_days": max_hold_days,
    }
