"""Tests for :class:`DecisionReasoning` and :class:`StrategyDecision`."""

from __future__ import annotations

import json

from milodex.strategies.base import DecisionReasoning


def test_decision_reasoning_defaults_are_empty_containers() -> None:
    reasoning = DecisionReasoning(
        rule="no_signal",
        narrative="No rule fired this bar.",
    )

    assert reasoning.triggering_values == {}
    assert reasoning.threshold == {}
    assert reasoning.ranking is None
    assert reasoning.rejected_alternatives == []
    assert reasoning.extras == {}


def test_decision_reasoning_asdict_round_trips_all_fields() -> None:
    reasoning = DecisionReasoning(
        rule="regime.ma_filter_cross",
        narrative="latest close 12.00 above 3-DMA 11.00 → rotate to SPY",
        triggering_values={"latest_close": 12.0, "ma_3": 11.0},
        threshold={"ma_3": 11.0},
        ranking=[{"symbol": "SPY", "score": 1.0}],
        rejected_alternatives=[{"symbol": "SHY", "reason": "not in target regime"}],
        extras={"debug_note": "crossover detected"},
    )

    payload = reasoning.asdict()

    assert payload == {
        "rule": "regime.ma_filter_cross",
        "narrative": "latest close 12.00 above 3-DMA 11.00 → rotate to SPY",
        "triggering_values": {"latest_close": 12.0, "ma_3": 11.0},
        "threshold": {"ma_3": 11.0},
        "ranking": [{"symbol": "SPY", "score": 1.0}],
        "rejected_alternatives": [{"symbol": "SHY", "reason": "not in target regime"}],
        "extras": {"debug_note": "crossover detected"},
    }


def test_decision_reasoning_asdict_is_json_serializable() -> None:
    """The actual storage path runs ``asdict()`` through ``json.dumps`` before
    persisting to the event store. Lock the JSON-roundtrip contract so a
    future field added to ``DecisionReasoning`` cannot regress the
    serialization invariant silently."""
    reasoning = DecisionReasoning(
        rule="meanrev.rsi_entry",
        narrative="RSI(2)=4.5 below threshold 10.0 \u2192 enter long SPY",
        triggering_values={"rsi_2": 4.5, "close": 100.0},
        threshold={"rsi_2": 10.0},
        ranking=[{"symbol": "SPY", "score": 1.0}],
        rejected_alternatives=[{"symbol": "QQQ", "reason": "above MA filter"}],
        extras={"bars_in_window": 14},
    )

    encoded = json.dumps(reasoning.asdict())
    decoded = json.loads(encoded)

    assert decoded == reasoning.asdict()


def test_decision_reasoning_omits_unset_non_rule_fields() -> None:
    """A rule-shaped reasoning (no non-rule fields set) serializes the legacy
    seven-key payload — the new optional decision-layer fields are omitted, so
    every existing rule strategy's blob stays byte-identical to the
    pre-generalization shape (decision-layer-seam-proof verification #2)."""
    import dataclasses

    reasoning = DecisionReasoning(rule="no_signal", narrative="No rule fired this bar.")
    payload = reasoning.asdict()

    legacy_keys = {
        "rule",
        "narrative",
        "triggering_values",
        "threshold",
        "ranking",
        "rejected_alternatives",
        "extras",
    }
    assert set(payload) == legacy_keys
    # The override emits exactly the legacy projection of the full dataclass —
    # the non-rule fields are dropped, nothing else changes.
    full = dataclasses.asdict(reasoning)
    assert payload == {key: full[key] for key in legacy_keys}


def test_decision_reasoning_serializes_populated_non_rule_fields() -> None:
    """A non-rule decider populates the new fields, which then appear in the
    serialized blob alongside the legacy keys (and only the populated ones)."""
    reasoning = DecisionReasoning(
        rule="scored.linear_features.entry",
        narrative="weighted score 1.83 ranked XLK first",
        ranking=[{"symbol": "XLK", "score": 1.83}, {"symbol": "XLE", "score": -0.40}],
        kind="scored",
        score=1.83,
        feature_contributions={"momentum": 0.90, "rsi": 0.50, "ma_distance": 0.43},
    )

    payload = reasoning.asdict()

    assert payload["kind"] == "scored"
    assert payload["score"] == 1.83
    assert payload["feature_contributions"]["momentum"] == 0.90
    # decision_path was left unset → omitted, not serialized as None.
    assert "decision_path" not in payload
    # Still JSON round-trippable with the new fields present.
    assert json.loads(json.dumps(payload)) == payload
