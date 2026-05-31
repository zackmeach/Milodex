"""Cross-cutting verifications for the decision-layer substitutability proof.

These lock the seam invariants as permanent suite assertions (the adversarial
overnight verifiers refute the same invariants independently):

- #1 No special-casing: the generalized reasoning module (and its ``asdict``
  override) has no branch on decider type, ``kind`` value, template, or a
  decider-name literal. Both deciders hit the same dataclass and the same
  ``asdict``.
- #2 Byte-identical rule serialization: the non-rule field kwargs are
  populated only by the two deciders; a rule-shaped reasoning serializes the
  legacy key set with no new keys.
- #4 Reproducibility: ``config_hash`` is stable across repeated computation for
  both decider configs (same config -> same hash -> same backtest result).
"""

from __future__ import annotations

from pathlib import Path

from milodex.strategies.base import DecisionReasoning
from milodex.strategies.loader import compute_config_hash

_STRATEGIES_DIR = Path("src/milodex/strategies")
_REASONING_MODULE = _STRATEGIES_DIR / "base.py"
_DECIDER_FILES = {"scored_linear_features.py", "tree_bucketed_lookup.py"}
_NON_RULE_KWARGS = ("kind=", "score=", "decision_path=", "feature_contributions=")

_DECIDER_CONFIGS = (
    Path("configs/scored_daily_linear_features_sector_etfs_v1.yaml"),
    Path("configs/tree_daily_bucketed_lookup_sector_etfs_v1.yaml"),
)


def test_reasoning_module_has_no_type_dispatch() -> None:
    """Verification #1 (mechanical): the reasoning module routes by neither
    decider type nor ``kind`` value nor template — the omission is purely
    metadata-driven."""
    source = _REASONING_MODULE.read_text(encoding="utf-8")

    # Comparison / dispatch forms only — a docstring that *names* the decider
    # kinds as field examples is documentation, not dispatch.
    forbidden = (
        "kind ==",
        "kind==",
        "self.kind",
        "isinstance(decider",
        "isinstance(reasoning",
        "template ==",
        "template==",
        ".family ==",
        '== "scored"',
        '== "tree"',
        '"scored" ==',
        '"tree" ==',
    )
    for needle in forbidden:
        assert needle not in source, f"reasoning module must not type-dispatch on {needle!r}"

    # Positive: the omission is driven by field metadata + equals-own-default,
    # the one code path both rule strategies and deciders traverse.
    assert "omit_if_default" in source
    assert "fields(self)" in source


def test_non_rule_kwargs_used_only_by_the_two_deciders() -> None:
    """Verification #2 (mechanical): no rule strategy populates the non-rule
    decision fields, so every rule strategy's serialized blob is unchanged."""
    offenders: dict[str, list[str]] = {}
    for path in _STRATEGIES_DIR.glob("*.py"):
        if path.name in _DECIDER_FILES:
            continue
        source = path.read_text(encoding="utf-8")
        hits = [kwarg for kwarg in _NON_RULE_KWARGS if kwarg in source]
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"rule modules must not set non-rule reasoning fields: {offenders}"


def test_rule_reasoning_blob_has_no_new_keys_decider_blob_does() -> None:
    """Verification #2 (behavioral): a rule-shaped reasoning serializes the
    legacy keys only; a decider-shaped one adds exactly the populated fields."""
    rule_blob = DecisionReasoning(
        rule="meanrev.rsi_entry",
        narrative="RSI below entry threshold",
        triggering_values={"rsi": 4.5},
        threshold={"rsi_entry_threshold": 10.0},
        ranking=[{"symbol": "XLK", "signal_value": 4.5}],
    ).asdict()
    assert _NON_RULE_KEYS.isdisjoint(rule_blob)

    decider_blob = DecisionReasoning(
        rule="scored.linear_features.entry",
        narrative="weighted score selected XLK",
        ranking=[{"symbol": "XLK", "score": 1.8}],
        kind="scored",
        score=1.8,
        feature_contributions={"momentum": 1.0, "rsi": 0.8},
    ).asdict()
    assert {"kind", "score", "feature_contributions"} <= set(decider_blob)
    assert "decision_path" not in decider_blob  # unset -> omitted


def test_decider_config_hash_is_reproducible() -> None:
    """Verification #4: each decider config hashes to a stable value across
    repeated computation — the reproducibility backbone of a backtest."""
    for config in _DECIDER_CONFIGS:
        first = compute_config_hash(config)
        second = compute_config_hash(config)
        assert first == second
        assert len(first) == 64  # sha-256 hex
    # The two distinct configs do not collide.
    hashes = {compute_config_hash(config) for config in _DECIDER_CONFIGS}
    assert len(hashes) == len(_DECIDER_CONFIGS)


_NON_RULE_KEYS = frozenset({"kind", "score", "decision_path", "feature_contributions"})
