"""Decider A — daily cross-sectional **linear scored / ranking** decider.

A deterministic, *non-rule* decision paradigm built behind the unchanged
``Strategy`` contract (decision-layer seam proof, axis 3). Where every rule
family reaches an action through a chain of boolean threshold comparisons,
this decider computes a **continuous score** for each universe member —

    score(symbol) = Σ  weight_f · zscore_f(symbol)
                    f ∈ {momentum, rsi, ma_distance, realized_vol}

— ranks the cross section by that score, and rotates into the top ``N``. The
score is a real number that *orders* candidates; it is emphatically not a
single threshold (see the genuineness assertion in the tests: the score takes
≥3 distinct values across the cross section and that ordering drives the
selection).

Coefficients (``feature_weights``) live in the YAML, so ``config_hash`` covers
reproducibility — there is **no model artifact**. Features come from the
shared :mod:`milodex.strategies._decider_features` kit, which re-expresses
quantities the rule families already compute from cached OHLCV. No new data
source, no new axis: only the decision paradigm changes.

**Capability-proof only.** ``stage: backtest``, lifecycle-exempt, "mechanics
not alpha". Negative / random backtest performance is the expected, correct
result; this is not tuned for return.
"""

from __future__ import annotations

from typing import Any

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.execution.sizing import shares_for_notional_pct
from milodex.strategies._decider_features import (
    cross_sectional_zscore,
    ma_distance,
    realized_vol,
    trailing_return,
    wilder_rsi,
)
from milodex.strategies.base import (
    DecisionReasoning,
    Strategy,
    StrategyContext,
    StrategyDecision,
    StrategyParameterSpec,
)
from milodex.strategies.daily_cross_sectional import normalize_universe_and_positions

_FEATURE_KEYS = ("momentum", "rsi", "ma_distance", "realized_vol")
_DECIDER_KIND = "scored"


class ScoredLinearFeaturesStrategy(Strategy):
    """Cross-sectional linear-scored rotation decider (non-rule, deterministic)."""

    family = "scored"
    template = "daily.linear_features"
    parameter_specs = (
        StrategyParameterSpec("momentum_lookback", expected_types=(int,)),
        StrategyParameterSpec("rsi_lookback", expected_types=(int,)),
        StrategyParameterSpec("ma_length", expected_types=(int,)),
        StrategyParameterSpec("vol_lookback", expected_types=(int,)),
        StrategyParameterSpec("feature_weights", expected_types=(dict,)),
        StrategyParameterSpec("target_positions", expected_types=(int,)),
        StrategyParameterSpec("exit_rank_buffer", expected_types=(int,)),
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

        # Mechanical risk exits fire daily, exactly as in the rule families —
        # the *scoring* paradigm governs selection, not the stop. These stay
        # rule-shaped (no ``kind``): a stop is a threshold, not a score.
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

        ranking = _score_universe(context.universe, bars_by_symbol, params)
        ranking_payload = [
            {
                "symbol": entry["symbol"],
                "rank": entry["rank"],
                "score": entry["score"],
                "features": entry["features"],
            }
            for entry in ranking
        ]

        if not ranking:
            return StrategyDecision(
                intents=[],
                reasoning=DecisionReasoning(
                    rule="no_signal",
                    narrative="no universe member had sufficient history to score",
                    triggering_values={"universe_size": len(context.universe)},
                ),
            )

        rank_by_symbol = {entry["symbol"]: entry["rank"] for entry in ranking}
        exit_rank_limit = params["target_positions"] + params["exit_rank_buffer"]

        intents: list[TradeIntent] = []
        rejected_alternatives: list[dict[str, Any]] = []

        # Rank exits: held names that fell outside the top (target + buffer).
        # Iterate in sorted-symbol order so the emitted SELL ordering is
        # deterministic regardless of the positions-mapping iteration order
        # (live broker response order vs. backtest insertion order).
        rank_exit_symbols: list[str] = []
        for symbol in sorted(open_positions):
            rank = rank_by_symbol.get(symbol)
            if rank is None or rank > exit_rank_limit:
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
        capacity = max(0, params["max_concurrent_positions"] - len(remaining_after_exits))

        # Entries: highest-scored names not already held, subject to capacity.
        entries: list[dict[str, Any]] = []
        for entry in ranking[: params["target_positions"]]:
            symbol = entry["symbol"]
            if symbol in remaining_after_exits:
                rejected_alternatives.append({"symbol": symbol, "reason": "already held"})
                continue
            if len(entries) >= capacity:
                rejected_alternatives.append({"symbol": symbol, "reason": "capacity full"})
                continue
            shares = shares_for_notional_pct(
                equity=context.equity,
                notional_pct=params["per_position_notional_pct"],
                unit_price=entry["latest_close"],
            )
            if shares <= 0:
                rejected_alternatives.append(
                    {
                        "symbol": symbol,
                        "reason": (
                            f"equity {context.equity:.2f} cannot afford one share at "
                            f"{entry['latest_close']:.2f}"
                        ),
                    }
                )
                continue
            entries.append(entry)
            intents.append(
                TradeIntent(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    quantity=float(shares),
                    order_type=OrderType.MARKET,
                )
            )

        top = ranking[0]
        if entries:
            primary = entries[0]
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="scored.linear_features.entry",
                    narrative=(
                        f"linear score selected {primary['symbol']} "
                        f"(score {primary['score']:+.3f}, rank {primary['rank']}) "
                        f"from {len(ranking)} scored candidates; "
                        f"buy {len([i for i in intents if i.side == OrderSide.BUY])}"
                    ),
                    triggering_values={
                        "selected_symbol": primary["symbol"],
                        "selected_score": primary["score"],
                        "selected_close": primary["latest_close"],
                    },
                    threshold={
                        "target_positions": params["target_positions"],
                        "exit_rank_limit": exit_rank_limit,
                    },
                    ranking=ranking_payload,
                    rejected_alternatives=rejected_alternatives,
                    kind=_DECIDER_KIND,
                    score=primary["score"],
                    feature_contributions=primary["contributions"],
                ),
            )

        if rank_exit_symbols:
            return StrategyDecision(
                intents=intents,
                reasoning=DecisionReasoning(
                    rule="scored.linear_features.rank_exit",
                    narrative=(
                        f"rotating out {len(rank_exit_symbols)} holding(s) ranked below "
                        f"the top {exit_rank_limit}: {', '.join(rank_exit_symbols)}"
                    ),
                    triggering_values={"exited_count": len(rank_exit_symbols)},
                    threshold={"exit_rank_limit": exit_rank_limit},
                    ranking=ranking_payload,
                    kind=_DECIDER_KIND,
                    score=top["score"],
                    feature_contributions=top["contributions"],
                ),
            )

        return StrategyDecision(
            intents=intents,
            reasoning=DecisionReasoning(
                rule="no_signal",
                narrative=(
                    f"top score {top['symbol']} (score {top['score']:+.3f}) produced no "
                    f"actionable entry — {len(rejected_alternatives)} candidate(s) rejected"
                ),
                triggering_values={"universe_size": len(context.universe)},
                threshold={"target_positions": params["target_positions"]},
                ranking=ranking_payload,
                rejected_alternatives=rejected_alternatives,
                kind=_DECIDER_KIND,
                score=top["score"],
                feature_contributions=top["contributions"],
            ),
        )


def _score_universe(
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return universe members scored and ranked by the linear feature score.

    Each entry: ``{symbol, rank, score, latest_close, features, contributions}``.
    Only symbols with all features computable are scored; the rest are dropped
    (no score = no claim). Ranks are 1-indexed; ties break alphabetically by
    symbol for determinism.
    """
    weights = params["feature_weights"]
    raw: dict[str, dict[str, float]] = {}
    closes_latest: dict[str, float] = {}

    for symbol in sorted({sym.upper() for sym in universe}):
        barset = bars_by_symbol.get(symbol)
        if barset is None:
            continue
        frame = barset.to_dataframe()
        if frame.empty:
            continue
        closes = frame["close"].astype(float)
        features = {
            "momentum": trailing_return(closes, params["momentum_lookback"]),
            "rsi": wilder_rsi(closes, params["rsi_lookback"]),
            "ma_distance": ma_distance(closes, params["ma_length"]),
            "realized_vol": realized_vol(closes, params["vol_lookback"]),
        }
        if any(value is None for value in features.values()):
            continue
        raw[symbol] = {key: float(value) for key, value in features.items()}  # type: ignore[arg-type]
        closes_latest[symbol] = float(closes.iloc[-1])

    if not raw:
        return []

    # Cross-sectional z-score per feature, then a weighted linear combination.
    zscores: dict[str, dict[str, float]] = {}
    for feature in _FEATURE_KEYS:
        column = {symbol: raw[symbol][feature] for symbol in raw}
        normalized = cross_sectional_zscore(column)
        for symbol, value in normalized.items():
            zscores.setdefault(symbol, {})[feature] = value

    scored: list[dict[str, Any]] = []
    for symbol in raw:
        contributions = {
            feature: float(weights.get(feature, 0.0)) * zscores[symbol][feature]
            for feature in _FEATURE_KEYS
        }
        score = sum(contributions.values())
        scored.append(
            {
                "symbol": symbol,
                "score": score,
                "latest_close": closes_latest[symbol],
                "features": raw[symbol],
                "contributions": contributions,
            }
        )

    scored.sort(key=lambda row: (-row["score"], row["symbol"]))
    for index, row in enumerate(scored):
        row["rank"] = index + 1
    return scored


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
                exit_rule = "scored.stop_loss"
            elif isinstance(held_days, int | float) and int(held_days) >= params["max_hold_days"]:
                exit_rule = "scored.max_hold"

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
    if rule == "scored.stop_loss":
        return f"close breached stop_loss {params['stop_loss_pct']:.2%} from entry → sell {symbol}"
    if rule == "scored.max_hold":
        return f"held >= max_hold_days {params['max_hold_days']} → sell {symbol}"
    return f"exit triggered → sell {symbol}"


def _exit_threshold(rule: str, params: dict[str, Any]) -> dict[str, float | int | str | None]:
    if rule == "scored.stop_loss":
        return {"stop_loss_pct": params["stop_loss_pct"]}
    if rule == "scored.max_hold":
        return {"max_hold_days": params["max_hold_days"]}
    return {}


def _validated_parameters(context: StrategyContext) -> dict[str, Any]:
    def required(name: str) -> Any:
        if name not in context.parameters:
            msg = f"Missing required strategy parameter: {name}"
            raise ValueError(msg)
        return context.parameters[name]

    momentum_lookback = int(required("momentum_lookback"))
    rsi_lookback = int(required("rsi_lookback"))
    ma_length = int(required("ma_length"))
    vol_lookback = int(required("vol_lookback"))
    for name, value in (
        ("momentum_lookback", momentum_lookback),
        ("ma_length", ma_length),
    ):
        if value < 1:
            msg = f"{name} must be >= 1"
            raise ValueError(msg)
    if rsi_lookback < 2:
        msg = "rsi_lookback must be >= 2"
        raise ValueError(msg)
    if vol_lookback < 2:
        msg = "vol_lookback must be >= 2"
        raise ValueError(msg)

    target_positions = int(required("target_positions"))
    if target_positions < 1:
        msg = "target_positions must be >= 1"
        raise ValueError(msg)

    exit_rank_buffer = int(required("exit_rank_buffer"))
    if exit_rank_buffer < 0:
        msg = "exit_rank_buffer must be >= 0"
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

    feature_weights = required("feature_weights")
    if not isinstance(feature_weights, dict) or not feature_weights:
        msg = "feature_weights must be a non-empty mapping"
        raise ValueError(msg)
    unknown = set(feature_weights) - set(_FEATURE_KEYS)
    if unknown:
        msg = f"feature_weights has unknown feature(s): {sorted(unknown)}; allowed {_FEATURE_KEYS}"
        raise ValueError(msg)
    normalized_weights: dict[str, float] = {}
    for key, value in feature_weights.items():
        if not isinstance(value, int | float) or isinstance(value, bool):
            msg = f"feature_weights[{key!r}] must be numeric, got {value!r}"
            raise ValueError(msg)
        normalized_weights[key] = float(value)
    if all(weight == 0.0 for weight in normalized_weights.values()):
        msg = "feature_weights must have at least one non-zero weight"
        raise ValueError(msg)

    return {
        "momentum_lookback": momentum_lookback,
        "rsi_lookback": rsi_lookback,
        "ma_length": ma_length,
        "vol_lookback": vol_lookback,
        "feature_weights": normalized_weights,
        "target_positions": target_positions,
        "exit_rank_buffer": exit_rank_buffer,
        "max_concurrent_positions": max_concurrent_positions,
        "per_position_notional_pct": per_position_notional_pct,
        "stop_loss_pct": stop_loss_pct,
        "max_hold_days": max_hold_days,
    }
