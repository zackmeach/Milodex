# Promotion Policy Source-of-Truth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate promotion gate-decision policy into a typed source of truth (`promotion/policy.py` + ADR 0052) with zero change to promotion behavior.

**Architecture:** A new frozen typed `PromotionPolicy` owns the research-target statistical tiers and a definition-only lifecycle operational-gate concept. `state_machine.py` keeps structural invariants and transition mechanics and delegates the statistical verdict to the active policy. All currently-public symbols (`PromotionCheckResult`, the five threshold constants) are preserved as policy-derived re-exports so no caller changes — this is a consolidation PR, not an API-shape PR.

**Tech Stack:** Python 3.11+, dataclasses (frozen), pytest, ruff. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-05-15-promotion-policy-source-of-truth-design.md`

---

## Behavior-preservation contract (read before starting)

The proof of "no behavior change" is: **`tests/milodex/promotion/test_state_machine.py` and `tests/milodex/promotion/test_transition.py` pass with ZERO edits after the refactor.** Those suites already exercise `check_gate` across the tier/boundary/None/lifecycle-exempt/custom-floor matrix. If a step requires editing them, the refactor is wrong — stop and reconsider.

Public symbols that MUST keep resolving with identical values (verified importers):
- `from milodex.promotion.state_machine import PromotionCheckResult, check_gate, validate_stage_transition, STAGE_ORDER`
- `from milodex.promotion.state_machine import MAX_DRAWDOWN_PCT, MIN_SHARPE, MIN_TRADES` — used by `gui/read_models.py:35`, `gui/strategy_bank_state.py:85`
- `from milodex.promotion import MIN_SHARPE, PAPER_MIN_SHARPE, ...` — `promotion/__init__.py.__all__`

Current values (the invariants the characterization locks): `MIN_SHARPE=0.5`, `MAX_DRAWDOWN_PCT=15.0`, `MIN_TRADES=30`, `PAPER_MIN_SHARPE=0.0`, `PAPER_MAX_DRAWDOWN_PCT=25.0`.

## File Structure

- **Create** `src/milodex/promotion/policy.py` — typed SoT: `GateThresholds`, `PromotionCheckResult` (moved here), `LifecycleGateDefinition`, `PromotionPolicy`, `PHASE1_GOVERNANCE_V1`, `ACTIVE_PROMOTION_POLICY`, `_fmt_or_none`.
- **Modify** `src/milodex/promotion/state_machine.py` — delete the 5 literal constants + `_thresholds_for_stage`; re-export `PromotionCheckResult` from policy; constants become policy-derived aliases; `check_gate` delegates to the policy; fix docstring SRS citations. `validate_stage_transition`, `transition`, `demote`, `STAGE_ORDER`, `PHASE_ONE_BLOCKED_STAGES` untouched.
- **Modify** `src/milodex/promotion/__init__.py` — repoint imports to surviving names (values identical); `__all__` unchanged.
- **Create** `tests/milodex/promotion/test_policy.py` — unit tests for the policy object.
- **Create** `docs/adr/0052-promotion-policy-is-a-typed-governance-source-of-truth.md`.
- **Modify** `CLAUDE.md`, `docs/SRS.md`, `docs/STRATEGY_BANK.md`, `docs/PROMOTION_GOVERNANCE.md` — point at the SoT, do not duplicate numbers.
- **Create** `tests/milodex/docs/test_claude_md_policy_pointer.py` — targeted regression lock.

Work happens on branch `codex/promotion-policy-sot` (already created, spec committed there).

---

### Task 1: Typed policy module

**Files:**
- Create: `src/milodex/promotion/policy.py`
- Test: `tests/milodex/promotion/test_policy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/milodex/promotion/test_policy.py
"""Unit tests for the typed promotion-policy source of truth."""

from milodex.promotion.policy import (
    ACTIVE_PROMOTION_POLICY,
    PHASE1_GOVERNANCE_V1,
    LifecycleGateDefinition,
    PromotionCheckResult,
    PromotionPolicy,
)


def test_active_policy_is_phase1_governance_v1() -> None:
    assert ACTIVE_PROMOTION_POLICY is PHASE1_GOVERNANCE_V1
    assert isinstance(ACTIVE_PROMOTION_POLICY, PromotionPolicy)


def test_phase1_values_match_legacy_constants() -> None:
    p = PHASE1_GOVERNANCE_V1
    assert p.paper_tier.min_sharpe == 0.0
    assert p.paper_tier.max_drawdown_pct == 25.0
    assert p.capital_tier.min_sharpe == 0.5
    assert p.capital_tier.max_drawdown_pct == 15.0
    assert p.default_trade_floor == 30


def test_lifecycle_gate_is_defined_but_not_enforced() -> None:
    gate = PHASE1_GOVERNANCE_V1.lifecycle_gate
    assert isinstance(gate, LifecycleGateDefinition)
    assert gate.enforced is False
    assert len(gate.criteria) == 3


def test_evaluate_paper_tier_passing() -> None:
    r = PHASE1_GOVERNANCE_V1.evaluate_research_target(
        sharpe_ratio=0.1, max_drawdown_pct=20.0, trade_count=30,
        target_stage="paper", min_trade_count=30,
    )
    assert isinstance(r, PromotionCheckResult)
    assert r.allowed is True
    assert r.promotion_type == "statistical"
    assert r.failures == []


def test_evaluate_paper_tier_sharpe_boundary_is_exclusive() -> None:
    # Sharpe must be > min; exactly 0.0 fails on the paper tier.
    r = PHASE1_GOVERNANCE_V1.evaluate_research_target(
        sharpe_ratio=0.0, max_drawdown_pct=20.0, trade_count=30,
        target_stage="paper", min_trade_count=30,
    )
    assert r.allowed is False
    assert r.failures == ["Sharpe 0.0 must be > 0.0 (got 0.0)"]


def test_evaluate_capital_tier_thresholds() -> None:
    r = PHASE1_GOVERNANCE_V1.evaluate_research_target(
        sharpe_ratio=0.4, max_drawdown_pct=10.0, trade_count=30,
        target_stage="micro_live", min_trade_count=30,
    )
    assert r.allowed is False
    assert r.failures == ["Sharpe 0.4 must be > 0.5 (got 0.4)"]


def test_evaluate_none_metrics_all_fail() -> None:
    r = PHASE1_GOVERNANCE_V1.evaluate_research_target(
        sharpe_ratio=None, max_drawdown_pct=None, trade_count=None,
        target_stage="paper", min_trade_count=30,
    )
    assert r.allowed is False
    assert r.failures == [
        "Sharpe None must be > 0.0 (got None)",
        "Max drawdown None% must be < 25.0% (got None)",
        "Trade count must be >= 30 (got None)",
    ]


def test_evaluate_unknown_stage_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unknown to_stage"):
        PHASE1_GOVERNANCE_V1.evaluate_research_target(
            sharpe_ratio=1.0, max_drawdown_pct=1.0, trade_count=99,
            target_stage="banana", min_trade_count=30,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/milodex/promotion/test_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'milodex.promotion.policy'`

- [ ] **Step 3: Write `src/milodex/promotion/policy.py`**

```python
"""Typed promotion-policy source of truth.

This module is the single authority for promotion *gate-decision* policy:
the research-target statistical tiers and the lifecycle-proof operational
gate *definition*. Structural transition legality (stage order, no-skip,
Phase-1 live-lock) and transition mechanics remain in
``milodex.promotion.state_machine`` and are deliberately NOT owned here.

Governance, not configuration. These values change only by a deliberate ADR
(see ADR 0052), never by config edit, strategy, model, or agent.
``ACTIVE_PROMOTION_POLICY`` is the single named current policy; it is NOT
runtime-selectable. Profile selection is deferred future work.

Reference: SRS R-PRM-004; ADR 0052.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STAGE_PAPER = "paper"
CAPITAL_STAGES: frozenset[str] = frozenset({"micro_live", "live"})


def _fmt_or_none(value: float | int | None) -> str:
    return "None" if value is None else str(value)


@dataclass(frozen=True)
class PromotionCheckResult:
    """Gate check outcome for a single promotion request.

    Public shape preserved verbatim from the pre-consolidation
    ``state_machine.PromotionCheckResult``; re-exported there for import
    stability.
    """

    allowed: bool
    promotion_type: str
    failures: list[str] = field(default_factory=list)
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None


@dataclass(frozen=True)
class GateThresholds:
    """One statistical tier's numeric thresholds."""

    min_sharpe: float
    max_drawdown_pct: float


@dataclass(frozen=True)
class LifecycleGateDefinition:
    """Definition-only model of the SRS R-PRM-004 lifecycle-proof gate.

    ``enforced`` is False by deliberate decision: the criteria are modeled
    so the policy is complete and inspectable, but ``check_gate`` still
    short-circuits lifecycle-exempt promotions to ``allowed=True``. Closing
    this gap is tracked future work (ADR 0052, "Known gap").
    """

    criteria: tuple[str, ...]
    description: str
    enforced: bool = False


@dataclass(frozen=True)
class PromotionPolicy:
    """The typed gate-decision policy. Frozen; one named instance per ADR."""

    name: str
    paper_tier: GateThresholds
    capital_tier: GateThresholds
    default_trade_floor: int
    lifecycle_gate: LifecycleGateDefinition

    def _thresholds_for_stage(self, target_stage: str) -> GateThresholds:
        if target_stage == STAGE_PAPER:
            return self.paper_tier
        if target_stage in CAPITAL_STAGES:
            return self.capital_tier
        msg = (
            f"Unknown to_stage '{target_stage}'. "
            "Valid stages: ['backtest', 'paper', 'micro_live', 'live']."
        )
        raise ValueError(msg)

    def evaluate_research_target(
        self,
        *,
        sharpe_ratio: float | None,
        max_drawdown_pct: float | None,
        trade_count: int | None,
        target_stage: str,
        min_trade_count: int,
    ) -> PromotionCheckResult:
        """Statistical verdict for a research-target strategy.

        Comparison operators, failure-message strings, and ``promotion_type``
        are byte-for-byte identical to the pre-consolidation ``check_gate``.
        """
        tier = self._thresholds_for_stage(target_stage)
        failures: list[str] = []

        if sharpe_ratio is None or sharpe_ratio <= tier.min_sharpe:
            failures.append(
                f"Sharpe {_fmt_or_none(sharpe_ratio)} must be > {tier.min_sharpe} "
                f"(got {_fmt_or_none(sharpe_ratio)})"
            )
        if max_drawdown_pct is None or max_drawdown_pct >= tier.max_drawdown_pct:
            failures.append(
                f"Max drawdown {_fmt_or_none(max_drawdown_pct)}% must be < "
                f"{tier.max_drawdown_pct}% "
                f"(got {_fmt_or_none(max_drawdown_pct)})"
            )
        if trade_count is None or trade_count < min_trade_count:
            failures.append(
                f"Trade count must be >= {min_trade_count} "
                f"(got {_fmt_or_none(trade_count)})"
            )

        return PromotionCheckResult(
            allowed=len(failures) == 0,
            promotion_type="statistical",
            failures=failures,
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
        )


PHASE1_GOVERNANCE_V1 = PromotionPolicy(
    name="phase1_governance_v1",
    paper_tier=GateThresholds(min_sharpe=0.0, max_drawdown_pct=25.0),
    capital_tier=GateThresholds(min_sharpe=0.5, max_drawdown_pct=15.0),
    default_trade_floor=30,
    lifecycle_gate=LifecycleGateDefinition(
        criteria=(
            "a successful deterministic backtest run",
            "explanation records (R-XC-008) generated for every simulated signal",
            "the risk layer having rejected at least one synthetic fault-injection trade",
        ),
        description=(
            "SRS R-PRM-004 lifecycle-proof paper gate for the regime strategy. "
            "Defined for completeness; NOT enforced in code (check_gate still "
            "returns allowed=True for lifecycle-exempt promotions). Tracked gap."
        ),
        enforced=False,
    ),
)

# The single named current governance policy. NOT runtime-selectable (ADR 0052).
ACTIVE_PROMOTION_POLICY = PHASE1_GOVERNANCE_V1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/milodex/promotion/test_policy.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Lint**

Run: `python -m ruff check src/milodex/promotion/policy.py tests/milodex/promotion/test_policy.py && python -m ruff format --check src/milodex/promotion/policy.py tests/milodex/promotion/test_policy.py`
Expected: no errors (run `ruff format` without `--check` to fix, if needed)

- [ ] **Step 6: Commit**

```bash
git add src/milodex/promotion/policy.py tests/milodex/promotion/test_policy.py
git commit -m "feat(promotion): typed promotion-policy source of truth"
```

---

### Task 2: Repoint state_machine.py to the policy (behavior-preserving)

**Files:**
- Modify: `src/milodex/promotion/state_machine.py` (constants 49–53, `PromotionCheckResult` 56–65, `check_gate` 105–157, `_thresholds_for_stage` 160–166, `_fmt_or_none` 169–170, docstring 8–23)
- Modify: `src/milodex/promotion/__init__.py` (imports 18–28)
- Regression guard (DO NOT EDIT): `tests/milodex/promotion/test_state_machine.py`, `tests/milodex/promotion/test_transition.py`

- [ ] **Step 1: Confirm the regression baseline is green pre-change**

Run: `python -m pytest tests/milodex/promotion/ -q`
Expected: PASS. Record the count; it must be identical after this task.

- [ ] **Step 2: Edit `state_machine.py` — replace constants block (lines 49–53) with policy-derived aliases**

Replace:

```python
MIN_SHARPE: float = 0.5
MAX_DRAWDOWN_PCT: float = 15.0
MIN_TRADES: int = 30
PAPER_MIN_SHARPE: float = 0.0
PAPER_MAX_DRAWDOWN_PCT: float = 25.0
```

with:

```python
# Backward-compatible aliases. The source of truth is
# milodex.promotion.policy.ACTIVE_PROMOTION_POLICY (ADR 0052). These names are
# public (re-exported by promotion/__init__.py; imported by gui/read_models.py
# and gui/strategy_bank_state.py) so they are retained as policy-derived
# aliases, not deleted — values are identical.
MIN_SHARPE: float = ACTIVE_PROMOTION_POLICY.capital_tier.min_sharpe
MAX_DRAWDOWN_PCT: float = ACTIVE_PROMOTION_POLICY.capital_tier.max_drawdown_pct
MIN_TRADES: int = ACTIVE_PROMOTION_POLICY.default_trade_floor
PAPER_MIN_SHARPE: float = ACTIVE_PROMOTION_POLICY.paper_tier.min_sharpe
PAPER_MAX_DRAWDOWN_PCT: float = ACTIVE_PROMOTION_POLICY.paper_tier.max_drawdown_pct
```

- [ ] **Step 3: Edit `state_machine.py` imports — add policy import, drop now-unused `field`**

At the import block (around line 29), ensure these exist:

```python
from milodex.promotion.policy import (
    ACTIVE_PROMOTION_POLICY,
    PromotionCheckResult,
)
```

Remove the local `PromotionCheckResult` dataclass (lines 56–65) entirely — it now lives in `policy.py` and is imported above (re-export: keeping it importable from `state_machine`). Remove `_fmt_or_none` (169–170) if no longer referenced after Step 4. Keep `from dataclasses import dataclass, field` only if still used elsewhere in the file (it is not after removal — change to no dataclass import if nothing else needs it; `ruff` will flag unused).

- [ ] **Step 4: Edit `check_gate` (105–157) to delegate; delete `_thresholds_for_stage` (160–166)**

Replace the body of `check_gate` after the `lifecycle_exempt` branch with delegation. Final `check_gate`:

```python
def check_gate(
    *,
    lifecycle_exempt: bool,
    to_stage: str = "micro_live",
    sharpe_ratio: float | None,
    max_drawdown_pct: float | None,
    trade_count: int | None,
    min_trade_count: int = MIN_TRADES,
) -> PromotionCheckResult:
    """Evaluate statistical promotion thresholds.

    Thin adapter over ``ACTIVE_PROMOTION_POLICY`` (ADR 0052). When
    ``lifecycle_exempt=True`` the thresholds are bypassed and the check
    always passes (promotion_type='lifecycle_exempt') — define-only; the
    lifecycle operational gate is intentionally NOT enforced here.
    """
    if lifecycle_exempt:
        return PromotionCheckResult(
            allowed=True,
            promotion_type="lifecycle_exempt",
            failures=[],
            sharpe_ratio=sharpe_ratio,
            max_drawdown_pct=max_drawdown_pct,
            trade_count=trade_count,
        )
    return ACTIVE_PROMOTION_POLICY.evaluate_research_target(
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
        target_stage=to_stage,
        min_trade_count=min_trade_count,
    )
```

Delete `_thresholds_for_stage` and `_fmt_or_none` (now in `policy.py`).

Note: `min_trade_count: int = MIN_TRADES` keeps the identical default value (30) because `MIN_TRADES` is now the policy-derived alias from Step 2. Signature and default behavior unchanged.

- [ ] **Step 5: Fix the docstring SRS citations (lines 8–23)**

In the module docstring, change the two wrong citations: `(SRS R-PRM-001)` and `(SRS R-PRM-002)` on the capital-stage Sharpe/drawdown lines both become `(SRS R-PRM-004)`. Add one line under the header: `Gate-decision policy lives in milodex.promotion.policy (ADR 0052); this module owns structural transition legality and mechanics.`

- [ ] **Step 6: Edit `src/milodex/promotion/__init__.py`**

The `from milodex.promotion.state_machine import (...)` block still resolves all names (constants are aliases, `PromotionCheckResult`/`check_gate` re-exported). No change needed to names or `__all__`. Verify by import only — do not restructure. If `ruff` reports the state_machine import of `PromotionCheckResult` as a re-export needing `__all__`, add `# noqa: F401` is NOT allowed — instead ensure `state_machine.py` lists re-exported names in its own `__all__` if one exists, or leave as direct import (it is used in annotations, so it is "used").

- [ ] **Step 7: Run the regression guard — MUST be identical to Step 1**

Run: `python -m pytest tests/milodex/promotion/ -q`
Expected: PASS, same count as Step 1, zero edits to `test_state_machine.py` / `test_transition.py`. If anything fails, the refactor changed behavior — fix the refactor, never the tests.

- [ ] **Step 8: Verify the public import surface still resolves**

Run:
```bash
python -c "from milodex.promotion.state_machine import PromotionCheckResult, check_gate, MIN_SHARPE, MAX_DRAWDOWN_PCT, MIN_TRADES, PAPER_MIN_SHARPE, PAPER_MAX_DRAWDOWN_PCT; from milodex.promotion import MIN_SHARPE as A, PAPER_MIN_SHARPE as B; print(MIN_SHARPE, MAX_DRAWDOWN_PCT, MIN_TRADES, PAPER_MIN_SHARPE, PAPER_MAX_DRAWDOWN_PCT)"
```
Expected output exactly: `0.5 15.0 30 0.0 25.0`

- [ ] **Step 9: Lint**

Run: `python -m ruff check src/milodex/promotion/ && python -m ruff format --check src/milodex/promotion/`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add src/milodex/promotion/state_machine.py src/milodex/promotion/__init__.py
git commit -m "refactor(promotion): state_machine delegates gate decision to policy SoT"
```

---

### Task 3: ADR 0052

**Files:**
- Create: `docs/adr/0052-promotion-policy-is-a-typed-governance-source-of-truth.md`
- Modify: `docs/adr/README.md` (append the index row)

- [ ] **Step 1: Read an existing ADR for house format**

Run: `sed -n '1,40p' docs/adr/0051-bench-command-infrastructure-v1.md` and `grep -n "0051" docs/adr/README.md`
Note the front-matter/section structure and the README index row format.

- [ ] **Step 2: Write ADR 0052** following that exact structure. Required content:
  - **Status:** Accepted
  - **Context:** policy truth duplicated across 8 surfaces; SRS R-PRM-004 and `state_machine.py` already agree; `CLAUDE.md` is the wrong artifact (conflates capital gate as the gate); no SoT for promotion policy the way `risk_defaults.yaml` is for execution risk.
  - **Decision:** the three-tier model (system invariants / promotion governance policy / operator risk preferences); `promotion/policy.py` is the typed SoT; **enumerate and name the system invariants** as non-negotiable (stage-order, no-skip, no-downgrade, Phase-1 live-lock, risk-layer veto) and state they remain code-side in `state_machine.py`/risk layer; `ACTIVE_PROMOTION_POLICY` is the single named current policy and **not runtime-selectable**; promotion policy changes only by future ADR.
  - **Known gap (named, tracked):** lifecycle-proof operational gate (R-PRM-004 a/b/c) is defined (`LifecycleGateDefinition`, `enforced=False`) but not enforced; closing it is deferred future work with its own spec/test surface.
  - **Consequences / Out of scope:** no YAML promotion config, no profile selection, no CLI/Bench surfacing, no operational-gate enforcement — each a clean later extension.
  - **References:** SRS R-PRM-004; spec `docs/superpowers/specs/2026-05-15-promotion-policy-source-of-truth-design.md`.

- [ ] **Step 3: Append the index row to `docs/adr/README.md`** matching the existing column format, status "Accepted".

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0052-promotion-policy-is-a-typed-governance-source-of-truth.md docs/adr/README.md
git commit -m "docs(adr): 0052 promotion policy is a typed governance source of truth"
```

---

### Task 4: Documentation reconciliation + targeted regression lock

**Files:**
- Modify: `CLAUDE.md`, `docs/SRS.md`, `docs/STRATEGY_BANK.md`, `docs/PROMOTION_GOVERNANCE.md`
- Create: `tests/milodex/docs/test_claude_md_policy_pointer.py`

- [ ] **Step 1: `CLAUDE.md` — rewrite the promotion rule, carry no numbers**

Find the "Promotion pipeline is mandatory" bullet under "Key Design Rules". Replace its threshold sentence with: stages remain `backtest → paper → micro_live → live`, no skipping; the **two-tier** gate (a permissive paper-readiness tier and a stricter capital-readiness tier, plus a lifecycle-proof exemption) is defined authoritatively in `src/milodex/promotion/policy.py` and ADR 0052 — **state no Sharpe/drawdown/trade numbers here**. Keep the existing "Kill switch" / "Actions that always require explicit human approval" bullets unchanged.

- [ ] **Step 2: `docs/SRS.md` R-PRM-004 (line ~290) — add a pointer only**

Append to the R-PRM-004 row a sentence (do not alter the requirement text): `Implementation source of truth: src/milodex/promotion/policy.py (ADR 0052).`

- [ ] **Step 3: `docs/STRATEGY_BANK.md` — one-line tier clarification**

In the "As of date and source of truth" section (near the existing as-of note added earlier), add one line: paper-stage entry reflects the **paper-readiness tier** (Sharpe > 0.0, max DD < 25%, configured trade floor), not the stricter capital tier; authoritative definition in `promotion/policy.py` / ADR 0052. No metric or stage table changes.

- [ ] **Step 4: `docs/PROMOTION_GOVERNANCE.md` — add SoT pointer, de-duplicate**

Add a pointer to `promotion/policy.py` + ADR 0052 as the threshold SoT. Grep the file for hardcoded `0.5` / `15` / `0.0` / `25` / `Sharpe` threshold restatements; if any present a numeric gate as authoritative, replace with a pointer. If none present, add the pointer near the promotion-gate discussion and make no numeric edits.

Run first: `grep -n "Sharpe\|drawdown\|0\.5\|15%\|25%\|0\.0" docs/PROMOTION_GOVERNANCE.md`

- [ ] **Step 5: Write the targeted regression test**

```python
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
```

- [ ] **Step 6: Create the test package init if missing**

Run: `test -f tests/milodex/docs/__init__.py || (mkdir -p tests/milodex/docs && touch tests/milodex/docs/__init__.py)`

- [ ] **Step 7: Run the regression test**

Run: `python -m pytest tests/milodex/docs/test_claude_md_policy_pointer.py -q`
Expected: PASS (4 passed). If it fails, fix `CLAUDE.md` (Step 1), not the test.

- [ ] **Step 8: Lint + commit**

Run: `python -m ruff check tests/milodex/docs/ && python -m ruff format --check tests/milodex/docs/`

```bash
git add CLAUDE.md docs/SRS.md docs/STRATEGY_BANK.md docs/PROMOTION_GOVERNANCE.md tests/milodex/docs/
git commit -m "docs(promotion): point doctrine at the policy SoT; add CLAUDE.md regression lock"
```

---

### Task 5: Full-suite verification & PR

- [ ] **Step 1: Full test suite**

Run: `python -m pytest -q`
Expected: all pass + the pre-existing 4 xfailed; total = prior baseline (1427) + new policy/doc tests, 0 failures, 0 unexpected xpass.

- [ ] **Step 2: Full lint**

Run: `python -m ruff check src/ tests/ && python -m ruff format --check src/ tests/`
Expected: clean.

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin codex/promotion-policy-sot
gh pr create --title "Promotion policy source-of-truth consolidation (ADR 0052)" --body "$(cat <<'EOF'
## Summary
Behavior-preserving consolidation of promotion gate-decision policy into a typed
source of truth (`promotion/policy.py` + ADR 0052). SRS R-PRM-004 and the code
already agreed; this removes the duplicated threshold truth and repoints the
doctrine docs at the SoT.

- New `promotion/policy.py`: `PromotionPolicy` + `PHASE1_GOVERNANCE_V1` + `ACTIVE_PROMOTION_POLICY`; `PromotionCheckResult` moved here and re-exported.
- `state_machine.py` delegates the statistical verdict; structural invariants, `transition`, `demote` unchanged; public constants retained as policy-derived aliases.
- Lifecycle operational gate defined (`enforced=False`) — not enforced; recorded as a named tracked gap in ADR 0052.
- Doctrine reconciliation: CLAUDE.md / SRS / STRATEGY_BANK / PROMOTION_GOVERNANCE point at the SoT (no duplicated numbers).

## Behavior change
None. Proven by `tests/milodex/promotion/test_state_machine.py` + `test_transition.py` passing unedited.

## Test plan
- [ ] `pytest tests/milodex/promotion/ -q` — green, unedited regression suite
- [ ] `pytest -q` — full suite green
- [ ] `ruff check src/ tests/` — clean
- [ ] Public import surface resolves with identical values

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Report the PR URL.**

---

## Out of scope (do not implement — operator-confirmed)

YAML promotion config · profile selection/loader · CLI/Bench active-policy surfacing · lifecycle operational-gate enforcement · unrelated `commands/bench.py` docstring sweep. Each is a clean later extension off this foundation.
