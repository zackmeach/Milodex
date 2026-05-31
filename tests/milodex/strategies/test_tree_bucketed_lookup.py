"""Tests for Decider B — the decision-tree / bucketed-lookup decider.

Covers the seam proof's genuineness bar for a *tree* decider (verification #6:
the tree traverses >=2 split levels to >=3 reachable distinct leaves — a
depth-1 / 2-leaf tree would be a rule and fail) and determinism (#3), plus the
entry / leaf-exit / stop / validation behavior and the non-rule reasoning
serialization (the ``decision_path``, never a ``score``).
"""

from __future__ import annotations

import math
from datetime import UTC
from pathlib import Path

import pandas as pd
import pytest

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.strategies import StrategyLoader
from milodex.strategies.base import StrategyContext
from milodex.strategies.tree_bucketed_lookup import TreeBucketedLookupStrategy

_CONFIG = Path("configs/tree_daily_bucketed_lookup_sector_etfs_v1.yaml")

_PARAMS: dict[str, object] = {
    "momentum_lookback": 63,
    "rsi_lookback": 14,
    "momentum_split": 0.0,
    "rsi_split_strong": 70.0,
    "rsi_split_dip": 30.0,
    "leaf_actions": {
        "strong_buy": {"action": "enter", "priority": 3},
        "trend_follow": {"action": "enter", "priority": 2},
        "dip_buy": {"action": "enter", "priority": 1},
        "neutral_skip": {"action": "skip", "priority": 0},
    },
    "target_positions": 2,
    "max_concurrent_positions": 3,
    "per_position_notional_pct": 0.45,
    "stop_loss_pct": 0.10,
    "max_hold_days": 10,
}


def test_loader_resolves_tree_decider() -> None:
    loaded = StrategyLoader().load(_CONFIG)
    assert isinstance(loaded.strategy, TreeBucketedLookupStrategy)
    assert loaded.context.strategy_id == "tree.daily.bucketed_lookup.sector_etfs.v1"
    assert loaded.context.family == "tree"
    assert loaded.context.universe_ref == "universe.sector_etfs_spdr.v1"


def test_tree_entry_routes_to_enter_leaf() -> None:
    bars = _diverse_universe()
    decision = TreeBucketedLookupStrategy().evaluate(
        _first(bars), _context(tuple(bars), bars, equity=100_000.0)
    )
    buys = [i for i in decision.intents if i.side is OrderSide.BUY]
    assert buys, "tree decider should enter at least one enter-leaf name"
    assert all(i.order_type is OrderType.MARKET for i in buys)
    assert decision.reasoning.rule == "tree.bucketed_lookup.entry"
    assert decision.reasoning.triggering_values["selected_leaf"] in {
        "strong_buy",
        "trend_follow",
        "dip_buy",
    }


def test_tree_traverses_two_levels_to_three_leaves() -> None:
    """Genuineness (verification #6, Decider B): the decision path has two
    split steps (>=2 levels), and the cross section reaches >=3 distinct
    leaves. A depth-1 / 2-leaf tree would be a rule and fail this."""
    bars = _diverse_universe()
    decision = TreeBucketedLookupStrategy().evaluate(
        _first(bars), _context(tuple(bars), bars, equity=100_000.0)
    )

    # The selected symbol's path traverses exactly two split levels.
    path = decision.reasoning.decision_path
    assert path is not None
    assert len(path) == 2
    assert [step["node"] for step in path] == ["momentum", "rsi"]
    assert path[-1]["leaf"] in {"strong_buy", "trend_follow", "dip_buy", "neutral_skip"}

    # The cross section reaches >=3 distinct leaves.
    ranking = decision.reasoning.ranking
    assert ranking is not None
    distinct_leaves = {entry["leaf"] for entry in ranking}
    assert len(distinct_leaves) >= 3, f"tree not multi-leaf enough: {distinct_leaves}"

    assert decision.reasoning.kind == "tree"
    # A tree decider never carries a continuous score — that is Decider A's field.
    assert decision.reasoning.score is None


def test_tree_reasoning_serialization_carries_decision_path_not_score() -> None:
    bars = _diverse_universe()
    decision = TreeBucketedLookupStrategy().evaluate(
        _first(bars), _context(tuple(bars), bars, equity=100_000.0)
    )
    payload = decision.reasoning.asdict()
    assert payload["kind"] == "tree"
    assert "decision_path" in payload
    assert len(payload["decision_path"]) == 2
    # The scored-only fields stay omitted for a tree decider.
    assert "score" not in payload
    assert "feature_contributions" not in payload


def test_tree_is_deterministic_across_input_ordering() -> None:
    bars = _diverse_universe()
    symbols = list(bars)

    first = TreeBucketedLookupStrategy().evaluate(
        _first(bars), _context(tuple(symbols), bars, equity=100_000.0)
    )
    reordered_symbols = tuple(reversed(symbols))
    reordered_bars = {sym: bars[sym] for sym in reordered_symbols}
    second = TreeBucketedLookupStrategy().evaluate(
        _first(reordered_bars), _context(reordered_symbols, reordered_bars, equity=100_000.0)
    )

    assert _intent_signature(first) == _intent_signature(second)
    assert first.reasoning.asdict() == second.reasoning.asdict()


def test_tree_leaf_exit_when_holding_routes_to_skip() -> None:
    bars = _diverse_universe()
    probe = TreeBucketedLookupStrategy().evaluate(
        _first(bars), _context(tuple(bars), bars, equity=100_000.0)
    )
    skip_symbols = [e["symbol"] for e in probe.reasoning.ranking if e["action"] == "skip"]
    assert skip_symbols, "fixture should route at least one symbol to a skip leaf"
    held = skip_symbols[0]

    decision = TreeBucketedLookupStrategy().evaluate(
        _first(bars),
        _context(
            tuple(bars),
            bars,
            equity=100_000.0,
            positions={held: 7.0},
            entry_state={held: {"entry_price": 100.0, "held_days": 1}},
        ),
    )
    sells = [i for i in decision.intents if i.side is OrderSide.SELL]
    assert held in {i.symbol for i in sells}


def test_tree_stop_loss_fires_before_classification() -> None:
    bars = _diverse_universe()
    held = sorted(bars)[0]
    entry_price = float(bars[held].to_dataframe()["close"].iloc[-1]) * 2.0
    decision = TreeBucketedLookupStrategy().evaluate(
        _first(bars),
        _context(
            tuple(bars),
            bars,
            equity=100_000.0,
            positions={held: 5.0},
            entry_state={held: {"entry_price": entry_price, "held_days": 1}},
        ),
    )
    assert decision.reasoning.rule == "tree.stop_loss"
    assert "kind" not in decision.reasoning.asdict()


def test_tree_rejects_missing_leaf_action() -> None:
    bars = _diverse_universe()
    context = _context(
        tuple(bars),
        bars,
        equity=100_000.0,
        override_parameters={
            "leaf_actions": {
                "strong_buy": {"action": "enter", "priority": 3},
                # trend_follow / dip_buy / neutral_skip omitted
            }
        },
    )
    with pytest.raises(ValueError, match="must define every leaf"):
        TreeBucketedLookupStrategy().evaluate(_first(bars), context)


def test_tree_rejects_bad_action_value() -> None:
    bars = _diverse_universe()
    context = _context(
        tuple(bars),
        bars,
        equity=100_000.0,
        override_parameters={
            "leaf_actions": {
                "strong_buy": {"action": "buy_hard", "priority": 3},
                "trend_follow": {"action": "enter", "priority": 2},
                "dip_buy": {"action": "enter", "priority": 1},
                "neutral_skip": {"action": "skip", "priority": 0},
            }
        },
    )
    with pytest.raises(ValueError, match="must be 'enter' or 'skip'"):
        TreeBucketedLookupStrategy().evaluate(_first(bars), context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _series(k: int, n: int = 90) -> list[float]:
    drift = (k - 4) * 0.003
    amp = 0.02 + 0.01 * (k % 4)
    phase = 0.6 * k
    return [100.0 * (1.0 + drift) ** i * (1.0 + amp * math.sin(0.25 * i + phase)) for i in range(n)]


def _diverse_universe() -> dict[str, BarSet]:
    return {f"S{k}": _make_bars(_series(k)) for k in range(8)}


def _make_bars(closes: list[float]) -> BarSet:
    n = len(closes)
    timestamps = pd.date_range(pd.Timestamp("2023-01-02", tz=UTC), periods=n, freq="D")
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [c * 1.005 for c in closes],
            "low": [c * 0.995 for c in closes],
            "close": closes,
            "volume": [1_000_000] * n,
            "vwap": closes,
        }
    )
    return BarSet(frame)


def _first(bars_by_symbol: dict[str, BarSet]) -> BarSet:
    return next(iter(bars_by_symbol.values()))


def _context(
    universe: tuple[str, ...],
    bars_by_symbol: dict[str, BarSet],
    *,
    equity: float,
    positions: dict[str, float] | None = None,
    entry_state: dict[str, dict[str, object]] | None = None,
    override_parameters: dict[str, object] | None = None,
) -> StrategyContext:
    parameters = dict(_PARAMS)
    if override_parameters is not None:
        parameters.update(override_parameters)
    return StrategyContext(
        strategy_id="tree.daily.bucketed_lookup.sector_etfs.v1",
        family="tree",
        template="daily.bucketed_lookup",
        variant="sector_etfs",
        version=1,
        config_hash="hash",
        parameters=parameters,
        universe=universe,
        universe_ref="universe.sector_etfs_spdr.v1",
        disable_conditions=(),
        config_path=str(_CONFIG),
        manifest={},
        positions=positions or {},
        equity=equity,
        bars_by_symbol=bars_by_symbol,
        entry_state=entry_state or {},
    )


def _intent_signature(decision) -> list[tuple[str, str, float]]:
    return sorted((i.symbol, i.side.value, i.quantity) for i in decision.intents)
