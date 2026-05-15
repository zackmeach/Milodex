# tests/milodex/docs/test_claude_md_policy_pointer.py
"""CLAUDE.md must point at the promotion-policy SoT, not restate thresholds.

Deliberately narrow (operator-confirmed scope): it checks the promotion
rule does not present threshold numbers as authoritative policy and that it
points at ADR 0052 / policy.py. It must NOT forbid numbers elsewhere in the
file.
"""

from pathlib import Path

import pytest

CLAUDE_MD = Path(__file__).resolve().parents[3] / "CLAUDE.md"


def _promotion_rule_block() -> str:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    lower = text.lower()
    idx = lower.find("promotion pipeline")
    assert idx != -1, "Could not locate the promotion pipeline rule in CLAUDE.md"
    return text[idx : idx + 600]


def test_claude_md_points_at_policy_sot() -> None:
    block = _promotion_rule_block()
    assert "policy.py" in block or "ADR 0052" in block, (
        "CLAUDE.md promotion rule must point at the policy SoT (policy.py / ADR 0052)"
    )


@pytest.mark.parametrize("forbidden", ["Sharpe > 0.5", "drawdown < 15", "Sharpe > 0.0"])
def test_claude_md_promotion_rule_states_no_threshold_numbers(forbidden: str) -> None:
    block = _promotion_rule_block()
    assert forbidden not in block, (
        f"CLAUDE.md promotion rule must not restate threshold '{forbidden}' "
        "as authoritative policy — point at policy.py / ADR 0052 instead"
    )
