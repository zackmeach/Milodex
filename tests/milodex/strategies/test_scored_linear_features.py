"""Tests for Decider A — the linear scored / ranking decider.

Covers the decision-layer seam proof's genuineness bar for a *scored* decider
(verification #6: the score takes >=3 distinct values across candidates and
drives the rank ordering — a single threshold would fail) and determinism
(verification #3), plus the standard entry / rank-exit / stop / validation
behavior and the non-rule reasoning serialization.
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
from milodex.strategies.scored_linear_features import ScoredLinearFeaturesStrategy

_CONFIG = Path("configs/scored_daily_linear_features_sector_etfs_v1.yaml")

_PARAMS: dict[str, object] = {
    "momentum_lookback": 63,
    "rsi_lookback": 14,
    "ma_length": 50,
    "vol_lookback": 20,
    "feature_weights": {"momentum": 1.0, "ma_distance": 0.5, "rsi": -0.2, "realized_vol": -0.5},
    "target_positions": 2,
    "exit_rank_buffer": 1,
    "max_concurrent_positions": 3,
    "per_position_notional_pct": 0.45,
    "stop_loss_pct": 0.10,
    "max_hold_days": 10,
}


def test_loader_resolves_scored_decider() -> None:
    loaded = StrategyLoader().load(_CONFIG)
    assert isinstance(loaded.strategy, ScoredLinearFeaturesStrategy)
    assert loaded.context.strategy_id == "scored.daily.linear_features.sector_etfs.v1"
    assert loaded.context.family == "scored"
    assert loaded.context.universe_ref == "universe.sector_etfs_spdr.v1"


def test_scored_entry_selects_top_by_score() -> None:
    bars = _diverse_universe()
    decision = ScoredLinearFeaturesStrategy().evaluate(
        _first(bars), _context(tuple(bars), bars, equity=100_000.0)
    )
    buys = [i for i in decision.intents if i.side is OrderSide.BUY]
    assert buys, "scored decider should enter the top-scored names"
    assert all(i.order_type is OrderType.MARKET for i in buys)
    assert len(buys) == 2  # target_positions, capacity allows
    assert decision.reasoning.rule == "scored.linear_features.entry"
    ranking = decision.reasoning.ranking
    assert ranking is not None
    # rank 1 carries the max score; selected symbol is the rank-1 symbol.
    top = min(ranking, key=lambda e: e["rank"])
    assert top["score"] == max(e["score"] for e in ranking)
    assert decision.reasoning.triggering_values["selected_symbol"] == top["symbol"]


def test_score_is_continuous_and_drives_rank() -> None:
    """Genuineness (verification #6, Decider A): the score takes >=3 distinct
    values across the cross section, and the ranking is exactly the score
    ordering. A lone boolean threshold could not produce this."""
    bars = _diverse_universe()
    decision = ScoredLinearFeaturesStrategy().evaluate(
        _first(bars), _context(tuple(bars), bars, equity=100_000.0)
    )
    ranking = decision.reasoning.ranking
    assert ranking is not None

    distinct_scores = {round(entry["score"], 9) for entry in ranking}
    assert len(distinct_scores) >= 3, f"score not continuous enough: {distinct_scores}"

    # The ranking IS the score ordering (descending), 1-indexed and contiguous.
    by_rank = sorted(ranking, key=lambda e: e["rank"])
    scores_in_rank_order = [e["score"] for e in by_rank]
    assert scores_in_rank_order == sorted(scores_in_rank_order, reverse=True)
    assert [e["rank"] for e in by_rank] == list(range(1, len(by_rank) + 1))

    # The non-rule fields are populated and consistent.
    assert decision.reasoning.kind == "scored"
    assert decision.reasoning.score == by_rank[0]["score"]
    contributions = decision.reasoning.feature_contributions
    assert contributions is not None
    assert math.isclose(sum(contributions.values()), decision.reasoning.score, abs_tol=1e-9)


def test_scored_reasoning_serialization_carries_only_populated_non_rule_fields() -> None:
    bars = _diverse_universe()
    decision = ScoredLinearFeaturesStrategy().evaluate(
        _first(bars), _context(tuple(bars), bars, equity=100_000.0)
    )
    payload = decision.reasoning.asdict()
    assert payload["kind"] == "scored"
    assert "score" in payload
    assert "feature_contributions" in payload
    # The tree-only field stays omitted for a scored decider.
    assert "decision_path" not in payload


def test_scored_is_deterministic_across_input_ordering() -> None:
    """Verification #3: identical intents (in the same ORDER) and reasoning
    regardless of how the universe tuple, bars map, AND positions mapping are
    ordered. The positions axis is the one the original test missed — it leaks
    into SELL emission order and stop-reasoning primary selection unless the
    decider sorts its positions iteration."""
    bars = _diverse_universe()
    symbols = list(bars)
    rev = tuple(reversed(symbols))
    rev_bars = {sym: bars[sym] for sym in rev}

    def run(universe, bars_map, positions, entry_state):
        return ScoredLinearFeaturesStrategy().evaluate(
            _first(bars_map),
            _context(
                universe, bars_map, equity=100_000.0, positions=positions, entry_state=entry_state
            ),
        )

    # Entry path (no positions).
    entry_a = run(tuple(symbols), bars, {}, {})
    entry_b = run(rev, rev_bars, {}, {})
    assert _ordered_intents(entry_a) == _ordered_intents(entry_b)
    assert entry_a.reasoning.asdict() == entry_b.reasoning.asdict()

    # Stop path: hold three names deep underwater so all stop. The reported
    # primary stop and the SELL ordering must not depend on positions order.
    held = [symbols[0], symbols[2], symbols[4]]
    positions = {sym: 5.0 for sym in held}
    entry_state = {
        sym: {
            "entry_price": float(bars[sym].to_dataframe()["close"].iloc[-1]) * 2.0,
            "held_days": 1,
        }
        for sym in held
    }
    rev_positions = {sym: positions[sym] for sym in reversed(held)}
    stop_a = run(tuple(symbols), bars, positions, entry_state)
    stop_b = run(rev, rev_bars, rev_positions, entry_state)
    assert stop_a.reasoning.rule == "scored.stop_loss"
    assert _ordered_intents(stop_a) == _ordered_intents(stop_b)
    assert stop_a.reasoning.asdict() == stop_b.reasoning.asdict()


def test_scored_rank_exit_when_holding_falls_out_of_buffer() -> None:
    bars = _diverse_universe()
    # Score everything once to find the worst-ranked symbol, then hold it.
    probe = ScoredLinearFeaturesStrategy().evaluate(
        _first(bars), _context(tuple(bars), bars, equity=100_000.0)
    )
    worst = max(probe.reasoning.ranking, key=lambda e: e["rank"])["symbol"]

    decision = ScoredLinearFeaturesStrategy().evaluate(
        _first(bars),
        _context(
            tuple(bars),
            bars,
            equity=100_000.0,
            positions={worst: 10.0},
            entry_state={worst: {"entry_price": 100.0, "held_days": 1}},
        ),
    )
    sells = [i for i in decision.intents if i.side is OrderSide.SELL]
    assert worst in {i.symbol for i in sells}


def test_scored_stop_loss_fires_before_scoring() -> None:
    bars = _diverse_universe()
    held = sorted(bars)[0]
    entry_price = float(bars[held].to_dataframe()["close"].iloc[-1]) * 2.0  # huge unrealized loss
    decision = ScoredLinearFeaturesStrategy().evaluate(
        _first(bars),
        _context(
            tuple(bars),
            bars,
            equity=100_000.0,
            positions={held: 5.0},
            entry_state={held: {"entry_price": entry_price, "held_days": 1}},
        ),
    )
    assert decision.reasoning.rule == "scored.stop_loss"
    # A mechanical stop stays rule-shaped — no non-rule fields on the blob.
    assert "kind" not in decision.reasoning.asdict()
    assert [i.symbol for i in decision.intents if i.side is OrderSide.SELL] == [held]


def test_scored_rejects_unknown_feature_weight() -> None:
    bars = _diverse_universe()
    context = _context(
        tuple(bars),
        bars,
        equity=100_000.0,
        override_parameters={"feature_weights": {"momentum": 1.0, "bogus": 0.5}},
    )
    with pytest.raises(ValueError, match="unknown feature"):
        ScoredLinearFeaturesStrategy().evaluate(_first(bars), context)


def test_scored_rejects_all_zero_weights() -> None:
    bars = _diverse_universe()
    context = _context(
        tuple(bars),
        bars,
        equity=100_000.0,
        override_parameters={"feature_weights": {"momentum": 0.0, "rsi": 0.0}},
    )
    with pytest.raises(ValueError, match="non-zero"):
        ScoredLinearFeaturesStrategy().evaluate(_first(bars), context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _series(k: int, n: int = 90) -> list[float]:
    """Deterministic, positive price path with a per-symbol drift and wiggle.

    ``k`` spans a range of drifts (some up, some down, one near-flat) and
    wiggle amplitudes, so the cross section produces diverse momentum, RSI,
    MA-distance, and realized-vol — hence diverse scores.
    """
    drift = (k - 4) * 0.003  # -1.2% .. +0.9% per bar
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
        strategy_id="scored.daily.linear_features.sector_etfs.v1",
        family="scored",
        template="daily.linear_features",
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


def _ordered_intents(decision) -> list[tuple[str, str, float]]:
    """Intents in emission order (order-sensitive) — guards SELL ordering."""
    return [(intent.symbol, intent.side.value, intent.quantity) for intent in decision.intents]
