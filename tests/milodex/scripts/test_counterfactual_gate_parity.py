"""Parity tests for scripts/counterfactual_gate.py vs the production promotion policy.

The counterfactual script inlines the production capital-gate constants
(``PROD_MIN_SHARPE`` / ``PROD_MAX_DD`` / ``PROD_MIN_TRADES``) because importing
the promotion package pulls in the broker/execution stack. Nothing else enforces
that those copies track ``ACTIVE_PROMOTION_POLICY`` — these tests do, so any
future threshold change (ADR-gated, per ADR 0052) breaks loudly here.

Covers:
- Constants parity: the script's inlined thresholds equal the active policy's
  capital gate and default trade floor.
- Outcome parity: the script's production-shaped gate and
  ``ACTIVE_PROMOTION_POLICY.evaluate_research_target`` agree on pass,
  fail-on-sharpe, and fail-on-trade-count fixtures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from milodex.promotion.policy import ACTIVE_PROMOTION_POLICY

# Make the scripts/ directory importable regardless of CWD
# (mirrors test_backfill_pullback_rsi2_audit_gap).
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if not (_SCRIPTS_DIR / "counterfactual_gate.py").exists():
    pytest.skip(
        "scripts/counterfactual_gate.py not found (moved or archived)",
        allow_module_level=True,
    )
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from counterfactual_gate import (  # noqa: E402
    PROD_MAX_DD,
    PROD_MIN_SHARPE,
    PROD_MIN_TRADES,
    StrategyEvidence,
    production_gate,
)

_CAPITAL_GATE = ACTIVE_PROMOTION_POLICY.capital_gate


class TestConstantsParity:
    def test_min_sharpe_matches_capital_gate(self) -> None:
        assert PROD_MIN_SHARPE == _CAPITAL_GATE.min_sharpe

    def test_max_drawdown_matches_capital_gate(self) -> None:
        assert PROD_MAX_DD == _CAPITAL_GATE.max_drawdown_pct

    def test_min_trades_matches_default_trade_floor(self) -> None:
        assert PROD_MIN_TRADES == ACTIVE_PROMOTION_POLICY.default_trade_floor


def _evidence(*, sharpe: float, max_dd: float, trades: int) -> StrategyEvidence:
    # Non-"regime" family so the script's lifecycle exemption does not trigger.
    return StrategyEvidence(
        strategy_id="momentum.daily.parity_fixture.test.v1",
        family="momentum",
        cadence="daily",
        trades=trades,
        oos_sharpe=sharpe,
        oos_max_dd_pct=max_dd,
        oos_total_return_pct=None,
        source="db",
    )


class TestOutcomeParity:
    @pytest.mark.parametrize(
        ("sharpe", "max_dd", "trades", "expected_allowed"),
        [
            pytest.param(1.0, 10.0, 100, True, id="pass"),
            pytest.param(0.3, 10.0, 100, False, id="fail-sharpe"),
            pytest.param(1.0, 10.0, 10, False, id="fail-trades"),
        ],
    )
    def test_script_gate_matches_production_policy(
        self, sharpe: float, max_dd: float, trades: int, expected_allowed: bool
    ) -> None:
        script_outcome = production_gate(_evidence(sharpe=sharpe, max_dd=max_dd, trades=trades))

        policy_result = ACTIVE_PROMOTION_POLICY.evaluate_research_target(
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd,
            trade_count=trades,
            target_stage="micro_live",
            min_trade_count=ACTIVE_PROMOTION_POLICY.default_trade_floor,
        )

        assert script_outcome.allowed == policy_result.allowed == expected_allowed
        # Both sides must fail the same number of criteria, not just both fail.
        assert len(script_outcome.failures) == len(policy_result.failures)
