"""Direct unit tests for the shared daily cross-sectional helper module.

Per-strategy tests exercise the helper indirectly. This module pins the
helper's own contract — the surface the next migration slice will rely
on — so a refactor that changes the contract surfaces here first.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC
from typing import Any

import pandas as pd

from milodex.broker.models import OrderSide, OrderType
from milodex.data.models import BarSet
from milodex.execution.models import TradeIntent
from milodex.strategies.base import StrategyContext, StrategyDecision
from milodex.strategies.daily_cross_sectional import (
    NormalizedInputs,
    PreEntryContinue,
    assemble_entry_decision,
    evaluate_pre_entry_gates,
    normalize_universe_and_positions,
    rank_candidates,
)

# ---------------------------------------------------------------------------
# normalize_universe_and_positions
# ---------------------------------------------------------------------------


def test_normalize_upper_cases_symbol_keys() -> None:
    context = _context(
        universe=("XLK",),
        positions={"xlk": 5.0},
        bars_by_symbol={"xlk": _flat_barset("XLK"), "spy": _flat_barset("SPY")},
    )

    norm = normalize_universe_and_positions(context)

    assert "XLK" in norm.open_positions
    assert "XLK" in norm.bars_by_symbol
    assert "SPY" in norm.bars_by_symbol


def test_normalize_drops_positions_outside_declared_universe() -> None:
    context = _context(
        universe=("XLK",),
        positions={"XLK": 5.0, "TLT": 3.0},
        bars_by_symbol={"XLK": _flat_barset("XLK")},
    )

    norm = normalize_universe_and_positions(context)

    assert "XLK" in norm.open_positions
    assert "TLT" not in norm.open_positions


def test_normalize_drops_non_positive_position_quantities() -> None:
    context = _context(
        universe=("XLK", "XLF"),
        positions={"XLK": 5.0, "XLF": 0.0},
        bars_by_symbol={"XLK": _flat_barset("XLK")},
    )

    norm = normalize_universe_and_positions(context)

    assert "XLK" in norm.open_positions
    assert "XLF" not in norm.open_positions


# ---------------------------------------------------------------------------
# evaluate_pre_entry_gates — gate ordering and PreEntryContinue contract
# ---------------------------------------------------------------------------


def test_gates_happy_path_returns_pre_entry_continue() -> None:
    """No exits, capacity available, regime bullish — return PreEntryContinue."""
    norm = NormalizedInputs(open_positions={}, bars_by_symbol={"SPY": _bullish_spy()})
    parameters = _base_parameters()

    result = evaluate_pre_entry_gates(
        norm=norm,
        parameters=parameters,
        exit_details=[],
        exit_narrative=_noop_narrative,
        exit_threshold=_noop_threshold,
        regime_filter_enabled=True,
    )

    assert isinstance(result, PreEntryContinue)
    assert result.intents == []
    assert result.remaining_after_exits == set()
    assert result.capacity == parameters["max_concurrent_positions"]


def test_gates_exit_first_short_circuits_with_exit_decision() -> None:
    """One exit intent — return a StrategyDecision carrying the exit, no entry phase."""
    norm = NormalizedInputs(open_positions={"XLK": 5.0}, bars_by_symbol={"SPY": _bullish_spy()})
    parameters = _base_parameters()
    exit_intent = TradeIntent(
        symbol="XLK", side=OrderSide.SELL, quantity=5.0, order_type=OrderType.MARKET
    )

    result = evaluate_pre_entry_gates(
        norm=norm,
        parameters=parameters,
        exit_details=[(exit_intent, "test.exit_rule")],
        exit_narrative=lambda rule, sym, params: f"exit {sym} via {rule}",
        exit_threshold=lambda rule, params: {"exit_param": 42},
        regime_filter_enabled=True,
    )

    assert isinstance(result, StrategyDecision)
    assert result.intents == [exit_intent]
    assert result.reasoning.rule == "test.exit_rule"
    assert result.reasoning.narrative == "exit XLK via test.exit_rule"
    assert result.reasoning.threshold == {"exit_param": 42}


def test_gates_exit_ordering_is_deterministic_across_exit_details_order() -> None:
    """Exit ordering must not depend on the order ``_exit_intents`` produced.

    Every cross-sectional ``_exit_intents`` iterates ``open_positions`` in
    mapping order (broker-response order live, kernel insertion order in
    backtest). The helper sorts ``exit_details`` by symbol so both the emitted
    SELL order and the reported primary exit (``exit_details[0]``) are
    reproducible regardless of the input order. Without the sort this fails:
    the forward and reversed inputs would yield different primaries and orders.
    """
    norm = NormalizedInputs(
        open_positions={"XLK": 5.0, "XLE": 3.0, "XLF": 7.0},
        bars_by_symbol={"SPY": _bullish_spy()},
    )

    def _exit(symbol: str, qty: float) -> tuple[TradeIntent, str]:
        intent = TradeIntent(
            symbol=symbol, side=OrderSide.SELL, quantity=qty, order_type=OrderType.MARKET
        )
        return (intent, f"test.exit_{symbol.lower()}")

    forward = [_exit("XLK", 5.0), _exit("XLE", 3.0), _exit("XLF", 7.0)]

    def _run(exit_details: list[tuple[TradeIntent, str]]) -> StrategyDecision:
        result = evaluate_pre_entry_gates(
            norm=norm,
            parameters=_base_parameters(),
            exit_details=exit_details,
            exit_narrative=lambda rule, sym, params: f"exit {sym} via {rule}",
            exit_threshold=lambda rule, params: {"rule": rule},
            regime_filter_enabled=True,
        )
        assert isinstance(result, StrategyDecision)
        return result

    a = _run(forward)
    b = _run(list(reversed(forward)))

    # Same SELLs, in the same symbol-sorted order, regardless of input order.
    assert [intent.symbol for intent in a.intents] == ["XLE", "XLF", "XLK"]
    assert [intent.symbol for intent in a.intents] == [intent.symbol for intent in b.intents]
    # Same reported primary exit (alphabetically first), regardless of input order.
    assert a.reasoning.rule == b.reasoning.rule == "test.exit_xle"
    assert a.reasoning.narrative == b.reasoning.narrative
    assert a.reasoning.triggering_values == b.reasoning.triggering_values == {"symbol": "XLE"}


def test_gates_capacity_zero_short_circuits_when_no_exits() -> None:
    """Positions full, no exits — return at-capacity decision before regime check."""
    norm = NormalizedInputs(
        open_positions={"XLK": 5.0, "XLF": 5.0},
        bars_by_symbol={"SPY": _bearish_spy()},  # regime also bearish, but capacity fires first
    )
    parameters = _base_parameters(max_concurrent_positions=2)

    result = evaluate_pre_entry_gates(
        norm=norm,
        parameters=parameters,
        exit_details=[],
        exit_narrative=_noop_narrative,
        exit_threshold=_noop_threshold,
        regime_filter_enabled=True,
    )

    assert isinstance(result, StrategyDecision)
    assert result.intents == []
    assert result.reasoning.rule == "no_signal"
    assert "capacity" in result.reasoning.narrative
    assert "bearish" not in result.reasoning.narrative


def test_gates_regime_bearish_short_circuits_when_enabled() -> None:
    """Capacity available, regime bearish — return regime-suppressed decision."""
    norm = NormalizedInputs(open_positions={}, bars_by_symbol={"SPY": _bearish_spy()})
    parameters = _base_parameters()

    result = evaluate_pre_entry_gates(
        norm=norm,
        parameters=parameters,
        exit_details=[],
        exit_narrative=_noop_narrative,
        exit_threshold=_noop_threshold,
        regime_filter_enabled=True,
    )

    assert isinstance(result, StrategyDecision)
    assert result.reasoning.rule == "no_signal"
    assert "bearish" in result.reasoning.narrative


def test_gates_regime_filter_disabled_skips_regime_check_even_when_bearish() -> None:
    """regime_filter_enabled=False — bearish regime is ignored."""
    norm = NormalizedInputs(open_positions={}, bars_by_symbol={"SPY": _bearish_spy()})
    parameters = _base_parameters()

    result = evaluate_pre_entry_gates(
        norm=norm,
        parameters=parameters,
        exit_details=[],
        exit_narrative=_noop_narrative,
        exit_threshold=_noop_threshold,
        regime_filter_enabled=False,
    )

    assert isinstance(result, PreEntryContinue)


# ---------------------------------------------------------------------------
# assemble_entry_decision — stable audit-key contract
# ---------------------------------------------------------------------------


def test_assemble_entry_writes_stable_triggering_keys() -> None:
    """selected_signal_label + selected_signal_value are present; dynamic key is absent.

    Old contract wrote a dynamic key like selected_breakout_strength. New contract
    writes the label as a value on a stable key so the audit schema is uniform
    across all cross-sectional strategies.
    """
    context = _strategy_context(equity=10_000.0)
    decision = assemble_entry_decision(
        intents=[],
        candidates=[("XLK", 100.0, 0.42)],
        capacity=2,
        context=context,
        parameters={"per_position_notional_pct": 0.10, "atr_entry_multiplier": 1.0},
        rejected_alternatives=[],
        signal_label="breakout_strength",
        signal_format=".4f",
        ranking_enabled=True,
        entry_rule="breakout.test_entry",
        entry_threshold_keys=("atr_entry_multiplier",),
        entry_narrative_fn=lambda primary, ents: "narrative",
    )

    tv = decision.reasoning.triggering_values
    assert tv["selected_symbol"] == "XLK"
    assert tv["selected_signal_label"] == "breakout_strength"
    assert tv["selected_signal_value"] == 0.42
    assert tv["selected_close"] == 100.0
    # The dynamic-key contract is GONE — no `selected_<label>` key.
    assert "selected_breakout_strength" not in tv


def test_assemble_entry_ranking_payload_uses_stable_keys() -> None:
    """Ranking entries use signal_label + signal_value, not dynamic <label> keys."""
    context = _strategy_context(equity=10_000.0)
    decision = assemble_entry_decision(
        intents=[],
        candidates=[("XLK", 100.0, 0.42), ("XLF", 50.0, 0.31)],
        capacity=2,
        context=context,
        parameters={"per_position_notional_pct": 0.10, "atr_entry_multiplier": 1.0},
        rejected_alternatives=[],
        signal_label="breakout_strength",
        signal_format=".4f",
        ranking_enabled=True,
        entry_rule="breakout.test_entry",
        entry_threshold_keys=("atr_entry_multiplier",),
        entry_narrative_fn=lambda primary, ents: "narrative",
    )

    ranking = decision.reasoning.ranking
    assert ranking is not None
    assert len(ranking) == 2
    first = ranking[0]
    assert first["symbol"] == "XLK"
    assert first["signal_label"] == "breakout_strength"
    assert first["signal_value"] == 0.42
    assert first["latest_close"] == 100.0
    # The dynamic-key contract is GONE — no `breakout_strength` literal key.
    assert "breakout_strength" not in first


def test_assemble_entry_extras_merge_after_standard_keys() -> None:
    """extra_triggering_values_fn additions land in triggering_values, alongside standards."""
    context = _strategy_context(equity=10_000.0)
    decision = assemble_entry_decision(
        intents=[],
        candidates=[("XLK", 100.0, 0.42)],
        capacity=2,
        context=context,
        parameters={"per_position_notional_pct": 0.10, "entry_channel_length": 20},
        rejected_alternatives=[],
        signal_label="breakout_strength",
        signal_format=".4f",
        ranking_enabled=False,
        entry_rule="breakout.donchian_entry",
        entry_threshold_keys=("entry_channel_length",),
        entry_narrative_fn=lambda primary, ents: "narrative",
        extra_triggering_values_fn=lambda primary: {"selected_channel_high": 99.5},
    )

    tv = decision.reasoning.triggering_values
    # Both standard and extra keys are present.
    assert tv["selected_signal_label"] == "breakout_strength"
    assert tv["selected_signal_value"] == 0.42
    assert tv["selected_channel_high"] == 99.5


def test_assemble_entry_overflow_rejection_includes_formatted_signal_value() -> None:
    """When candidates exceed capacity, the overflow reason string formats the signal value."""
    context = _strategy_context(equity=10_000.0)
    rejected: list[dict[str, Any]] = []
    assemble_entry_decision(
        intents=[],
        candidates=[("XLK", 100.0, 0.42), ("XLF", 50.0, 0.31), ("XLE", 80.0, 0.25)],
        capacity=1,
        context=context,
        parameters={"per_position_notional_pct": 0.10, "atr_entry_multiplier": 1.0},
        rejected_alternatives=rejected,
        signal_label="breakout_strength",
        signal_format=".4f",
        ranking_enabled=True,
        entry_rule="breakout.test_entry",
        entry_threshold_keys=("atr_entry_multiplier",),
        entry_narrative_fn=lambda primary, ents: "narrative",
    )

    # XLK fits in capacity-1; XLF and XLE are rejected.
    overflow_reasons = [r["reason"] for r in rejected if r["symbol"] in ("XLF", "XLE")]
    assert len(overflow_reasons) == 2
    for reason in overflow_reasons:
        assert "breakout_strength=" in reason
        assert "capacity 1 full" in reason


def test_assemble_entry_sizing_affordability_zero_records_rejection() -> None:
    """A candidate whose share-size is zero (price too high for equity) is rejected, not bought."""
    context = _strategy_context(equity=10.0)  # tiny equity
    rejected: list[dict[str, Any]] = []
    decision = assemble_entry_decision(
        intents=[],
        candidates=[("XLK", 100.0, 0.42)],
        capacity=2,
        context=context,
        parameters={"per_position_notional_pct": 0.10, "atr_entry_multiplier": 1.0},
        rejected_alternatives=rejected,
        signal_label="breakout_strength",
        signal_format=".4f",
        ranking_enabled=False,
        entry_rule="breakout.test_entry",
        entry_threshold_keys=("atr_entry_multiplier",),
        entry_narrative_fn=lambda primary, ents: "narrative",
    )

    # No buy intent emitted.
    assert all(intent.side != OrderSide.BUY for intent in decision.intents)
    # Rejection recorded with sizing-affordability narrative.
    assert any("cannot afford" in r["reason"] for r in rejected)


def test_assemble_entry_no_qualifying_candidates_returns_no_signal() -> None:
    """Empty candidates list → no_signal decision with rejected-count narrative."""
    context = _strategy_context(equity=10_000.0)
    rejected: list[dict[str, Any]] = [
        {"symbol": "XLK", "reason": "no signal"},
        {"symbol": "XLF", "reason": "no signal"},
    ]
    decision = assemble_entry_decision(
        intents=[],
        candidates=[],
        capacity=2,
        context=context,
        parameters={"per_position_notional_pct": 0.10, "atr_entry_multiplier": 1.0},
        rejected_alternatives=rejected,
        signal_label="breakout_strength",
        signal_format=".4f",
        ranking_enabled=False,
        entry_rule="breakout.test_entry",
        entry_threshold_keys=("atr_entry_multiplier",),
        entry_narrative_fn=lambda primary, ents: "narrative",
    )

    assert decision.intents == []
    assert decision.reasoning.rule == "no_signal"
    assert "no entry candidates qualified" in decision.reasoning.narrative
    assert "2 universe member(s) rejected" in decision.reasoning.narrative


# ---------------------------------------------------------------------------
# rank_candidates — canonical sort helper
# ---------------------------------------------------------------------------


def test_rank_candidates_ascending_orders_by_signal_value() -> None:
    """Ascending key_fn puts the smallest signal value first."""
    candidates = [("XLK", 100.0, 0.42), ("XLF", 50.0, 0.18), ("XLE", 80.0, 0.31)]

    ranked = rank_candidates(candidates, key_fn=lambda c: (c[2], c[0]))

    assert [c[0] for c in ranked] == ["XLF", "XLE", "XLK"]


def test_rank_candidates_descending_orders_by_negated_signal_value() -> None:
    """Descending key_fn (negate value) puts the largest signal value first."""
    candidates = [("XLK", 100.0, 0.42), ("XLF", 50.0, 0.18), ("XLE", 80.0, 0.31)]

    ranked = rank_candidates(candidates, key_fn=lambda c: (-c[2], c[0]))

    assert [c[0] for c in ranked] == ["XLK", "XLE", "XLF"]


def test_rank_candidates_symbol_tiebreak_ascending_on_value_ties() -> None:
    """When signal values are equal, symbol breaks the tie alphabetically."""
    candidates = [("XLK", 100.0, 0.30), ("XLF", 50.0, 0.30), ("XLE", 80.0, 0.30)]

    ranked = rank_candidates(candidates, key_fn=lambda c: (c[2], c[0]))

    assert [c[0] for c in ranked] == ["XLE", "XLF", "XLK"]


def test_rank_candidates_is_stable_and_returns_new_list() -> None:
    """Helper returns a freshly-sorted list without mutating the input."""
    candidates = [("XLK", 100.0, 0.42), ("XLF", 50.0, 0.18)]
    snapshot = list(candidates)

    ranked = rank_candidates(candidates, key_fn=lambda c: (c[2], c[0]))

    assert ranked is not candidates
    assert candidates == snapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _base_parameters(*, max_concurrent_positions: int = 3) -> dict[str, Any]:
    return {
        "max_concurrent_positions": max_concurrent_positions,
        "market_regime_symbol": "SPY",
        "market_regime_ma_length": 5,
    }


def _noop_narrative(rule: str, symbol: str, params: Mapping[str, Any]) -> str:
    return f"{rule} for {symbol}"


def _noop_threshold(rule: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {}


def _flat_barset(symbol: str) -> BarSet:
    bars = [(100.0, 101.0, 99.0, 100.0)] * 20
    return _barset(bars)


def _bullish_spy() -> BarSet:
    bars = [(100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i) for i in range(20)]
    return _barset(bars)


def _bearish_spy() -> BarSet:
    bars = [(120.0 - i, 121.0 - i, 119.0 - i, 120.0 - i) for i in range(20)]
    return _barset(bars)


def _barset(bars: list[tuple[float, float, float, float]]) -> BarSet:
    timestamps = pd.date_range("2025-01-01", periods=len(bars), freq="D", tz=UTC)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": [b[0] for b in bars],
                "high": [b[1] for b in bars],
                "low": [b[2] for b in bars],
                "close": [b[3] for b in bars],
                "volume": [1_000_000] * len(bars),
                "vwap": [b[3] for b in bars],
            }
        )
    )


def _context(
    *,
    universe: tuple[str, ...],
    positions: Mapping[str, float],
    bars_by_symbol: Mapping[str, BarSet],
) -> StrategyContext:
    return StrategyContext(
        strategy_id="test.daily.helper",
        family="test",
        template="daily.helper",
        variant="test",
        version=1,
        config_hash="hash",
        parameters={},
        universe=universe,
        universe_ref=None,
        disable_conditions=(),
        config_path="",
        manifest={},
        positions=positions,
        equity=10_000.0,
        bars_by_symbol=bars_by_symbol,
        entry_state={},
    )


def _strategy_context(*, equity: float) -> StrategyContext:
    return StrategyContext(
        strategy_id="test.daily.helper",
        family="test",
        template="daily.helper",
        variant="test",
        version=1,
        config_hash="hash",
        parameters={},
        universe=("XLK", "XLF", "XLE"),
        universe_ref=None,
        disable_conditions=(),
        config_path="",
        manifest={},
        positions={},
        equity=equity,
        bars_by_symbol={},
        entry_state={},
    )
