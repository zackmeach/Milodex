"""Table test for the pure BENCH archetype classifier (roadmap M2).

``classify_archetype`` is a pure, Qt-free function in
``milodex.gui.strategy_row``. Its priority order is load-bearing (first match
wins) because the archetypes overlap — these cases pin every branch and the
two deliberate overlaps: a benchmark-family canary, and the regime
lifecycle-proof strategy that must NOT read as a canary.
"""

from __future__ import annotations

import pytest

from milodex.gui.strategy_row import classify_archetype


@pytest.mark.parametrize(
    ("family", "stage", "promotion_type", "gate_failures", "expected"),
    [
        # --- one clean case per archetype ---------------------------------
        # canary: non-regime lifecycle_exempt promotion (intraday harness).
        ("breakout", "paper", "lifecycle_exempt", [], "canary"),
        # baseline: benchmark family (null templates).
        ("benchmark", "backtest", "statistical", ["S"], "baseline"),
        # paper: promoted edge, statistical.
        ("meanrev", "paper", "statistical", [], "paper"),
        # blocked: backtest with failing gate, rule family.
        ("momentum", "backtest", "", ["S", "N"], "blocked"),
        # research: idle/backtest rule family, gate passing (fallthrough).
        ("momentum", "backtest", "", [], "research"),
        # --- canary-vs-baseline overlap (rule 1 beats rule 2) -------------
        # A benchmark-family strategy promoted lifecycle_exempt is the intraday
        # benchmark canary — must read as canary, not baseline.
        ("benchmark", "paper", "lifecycle_exempt", [], "canary"),
        # --- regime lifecycle_exempt is NOT a canary (rule 1 excludes it) --
        # The SPY/SHY lifecycle-proof strategy is lifecycle_exempt but family
        # == regime → falls through rule 1, lands at paper via rule 3.
        ("regime", "paper", "lifecycle_exempt", [], "paper"),
        # --- deciders are research, NEVER blocked (rule 4 beats rule 5) ----
        # scored/tree seam-proof deciders sit at backtest with failing/NULL-WF
        # gates but must not read as blocked.
        ("scored", "backtest", "", ["S", "D", "N"], "research"),
        ("tree", "backtest", "", ["S", "N"], "research"),
        # --- idle unpromoted rule family → research -----------------------
        ("momentum", "idle", "", [], "research"),
        # regime at backtest with no promotion is gate-exempt upstream
        # (gate_failures empty), so it falls through to research.
        ("regime", "backtest", "", [], "research"),
    ],
)
def test_classify_archetype(
    family: str,
    stage: str,
    promotion_type: str,
    gate_failures: list[str],
    expected: str,
) -> None:
    assert classify_archetype(family, stage, promotion_type, gate_failures) == expected


def test_classify_archetype_returns_only_known_archetypes() -> None:
    """Whatever the inputs, the return is one of exactly five strings."""
    known = {"canary", "baseline", "paper", "blocked", "research"}
    for family in ("meanrev", "benchmark", "regime", "scored", "tree", "breakout"):
        for stage in ("idle", "backtest", "paper", "micro_live", "live"):
            for ptype in ("", "statistical", "lifecycle_exempt", "operator_override", None):
                for gf in ([], ["S"]):
                    assert classify_archetype(family, stage, ptype, gf) in known
