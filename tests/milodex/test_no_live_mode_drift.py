"""ADR 0023 §7 / R-XC-016 — no live-mode drift.

Structural invariants ensuring no code path can reach the broker outside
the existing paper-only safeguards. Each test asserts a property whose
removal would silently re-open a live-trading pathway.

The underlying behaviors are also exercised by tests in
``tests/milodex/risk/``, ``tests/milodex/promotion/``,
``tests/milodex/execution/``, and ``tests/milodex/cli/`` — but those
tests cover *behavior under given conditions*. This module locks the
*existence* of the conditions, so a refactor that removes (for example)
the trading-mode check from ``RiskEvaluator.evaluate`` fails this test
even if no behavioral test happens to exercise the missing path.

Three defensive layers are locked:

1. **Risk evaluator paper-mode check.** ``RiskEvaluator.evaluate``
   invokes ``_check_trading_mode``, and the check refuses non-paper
   modes with reason code ``paper_mode_required``.

2. **Promotion state machine block.**
   ``promotion.state_machine.PHASE_ONE_BLOCKED_STAGES`` contains both
   ``live`` and ``micro_live``, and ``validate_stage_transition`` raises
   when either is the target.

3. **Chokepoint architecture.** ``BrokerClient.submit_order`` is called
   from at most a fixed set of files: the broker module itself
   (abstract definition + concrete implementations) and
   ``execution/service.py`` (the single legitimate caller per
   CLAUDE.md "execution is the chokepoint from intent → trade").
"""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path
from unittest.mock import Mock

import pytest

from milodex.promotion.state_machine import (
    PHASE_ONE_BLOCKED_STAGES,
    validate_stage_transition,
)
from milodex.risk.evaluator import RiskEvaluator

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "milodex"

# Files allowed to contain ``.submit_order(`` calls. The broker module
# defines the abstract method and concrete implementations; execution
# service is the single chokepoint that invokes them. Anything else
# would be a bypass of the risk layer.
_SUBMIT_ORDER_ALLOWED_RELPATHS: frozenset[str] = frozenset(
    {
        "broker/client.py",
        "broker/alpaca_client.py",
        "broker/simulated.py",
        "execution/service.py",
    }
)


class TestNoLiveModeDrift:
    # ------------------------------------------------------------------
    # Layer 1: Risk evaluator paper-mode check
    # ------------------------------------------------------------------

    def test_evaluate_invokes_trading_mode_check(self):
        """``RiskEvaluator.evaluate`` must invoke ``_check_trading_mode``.

        Removing the check would silently bypass the paper-only fence at
        runtime. The behavioral consequence is tested in
        ``tests/milodex/execution/test_service.py::test_paper_submit_requires_paper_mode``;
        this test pins the *existence* of the wiring so a refactor that
        deletes it fails here even before any execution-service test runs.

        ``evaluate`` dispatches the check suite by name through
        ``_run_check`` over the ``_CHECKS`` registry (fail-closed
        wrapper). We AST-parse the ``_CHECKS`` tuple rather than
        regex-match the source because a commented-out entry disappears
        from the AST — and a commented-out check IS the regression we
        most want to catch ("just disable the check temporarily" is the
        most plausible bad refactor). We additionally assert ``evaluate``
        actually dispatches every ``_CHECKS`` entry through
        ``_run_check`` so the registry cannot be made inert.
        """
        # 1. ``_check_trading_mode`` is present in the AST of the
        #    ``_CHECKS`` registry literal (commenting it out removes it).
        class_source = textwrap.dedent(inspect.getsource(RiskEvaluator))
        class_tree = ast.parse(class_source)
        checks_literals: list[str] = []
        for node in ast.walk(class_tree):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_CHECKS" for t in node.targets)
                and isinstance(node.value, ast.Tuple)
            ):
                checks_literals = [
                    elt.value
                    for elt in node.value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
        assert "_check_trading_mode" in checks_literals, (
            "RiskEvaluator._CHECKS must include '_check_trading_mode' "
            "(found no such entry in the AST of the registry tuple). "
            "Removing or commenting it out violates ADR 0004 "
            "(paper-only) and R-EXE-007 runtime defense-in-depth."
        )
        # The registry must match the live tuple (guards against the AST
        # parse silently going stale relative to the runtime attribute).
        assert "_check_trading_mode" in RiskEvaluator._CHECKS

        # 2. ``evaluate`` actually dispatches the registry through
        #    ``_run_check`` — the registry must not be made inert.
        evaluate_source = textwrap.dedent(inspect.getsource(RiskEvaluator.evaluate))
        evaluate_tree = ast.parse(evaluate_source)
        dispatches = [
            node
            for node in ast.walk(evaluate_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_run_check"
        ]
        references_checks = any(
            isinstance(node, ast.Attribute) and node.attr == "_CHECKS"
            for node in ast.walk(evaluate_tree)
        )
        assert dispatches and references_checks, (
            "RiskEvaluator.evaluate must dispatch the _CHECKS registry "
            "through _run_check. Bypassing the registry or the "
            "fail-closed wrapper violates R-EXE-007 defense-in-depth."
        )

    def test_check_trading_mode_rejects_live_mode(self):
        """``_check_trading_mode`` must reject ``trading_mode='live'``.

        Reason code must remain ``paper_mode_required`` — the
        explanation event store and the trust dashboard both key off
        that string.
        """
        context = Mock()
        context.trading_mode = "live"
        context.preview_only = False

        result = RiskEvaluator()._check_trading_mode(context)

        assert result.passed is False
        assert result.reason_code == "paper_mode_required"

    def test_check_trading_mode_rejects_micro_live_mode(self):
        """``_check_trading_mode`` must also reject any non-paper mode.

        Phase 1 has only two valid trading modes (paper/live per
        ``config.get_trading_mode``) but we assert the check is
        defensive against any non-``paper`` string — the check is
        whitelist-shaped (``!= "paper"``), not blacklist-shaped.
        """
        context = Mock()
        context.trading_mode = "micro_live"
        context.preview_only = False

        result = RiskEvaluator()._check_trading_mode(context)

        assert result.passed is False
        assert result.reason_code == "paper_mode_required"

    def test_check_trading_mode_allows_paper_mode(self):
        """``_check_trading_mode`` must pass ``trading_mode='paper'``.

        Sanity check on the positive case so a future bug that breaks
        the paper path itself is caught alongside the live-refusal
        invariant.
        """
        context = Mock()
        context.trading_mode = "paper"
        context.preview_only = False

        result = RiskEvaluator()._check_trading_mode(context)

        assert result.passed is True
        assert result.reason_code is None

    # ------------------------------------------------------------------
    # Layer 2: Promotion state machine block
    # ------------------------------------------------------------------

    def test_phase_one_blocked_stages_contains_live(self):
        """``live`` must be in ``PHASE_ONE_BLOCKED_STAGES``.

        Per [ADR 0004](docs/adr/0004-paper-only-phase-one.md) the
        live-stage lock lifts via a future ADR, not a config edit nor
        a constant-edit. Removing ``live`` from this frozenset is the
        kind of one-line change a refactor could plausibly make
        without ill intent — this test catches it.
        """
        assert "live" in PHASE_ONE_BLOCKED_STAGES

    def test_phase_one_blocked_stages_contains_micro_live(self):
        """``micro_live`` must also be in ``PHASE_ONE_BLOCKED_STAGES``.

        ``micro_live`` is the real-money pre-live stage; promoting to
        it is a live-mode-equivalent risk during Phase 1. The lock
        treats both stages identically per ADR 0004.
        """
        assert "micro_live" in PHASE_ONE_BLOCKED_STAGES

    def test_phase_one_blocked_stages_is_exactly_micro_live_and_live(self):
        """The blocked set must be *exactly* ``{"micro_live", "live"}``.

        The membership tests above catch *removing* a stage; this
        exact-set pin also catches *adding* one. A mutation that put
        ``paper`` (or ``backtest``) into the set would silently pass the
        membership tests yet halt Phase-1 paper trading at promotion.
        Pinning the whole set closes that gap.
        """
        assert PHASE_ONE_BLOCKED_STAGES == frozenset({"micro_live", "live"})

    def test_validate_stage_transition_blocks_micro_live_promotion(self):
        """``paper → micro_live`` must raise during Phase 1."""
        with pytest.raises(ValueError, match=r"Phase 1"):
            validate_stage_transition("paper", "micro_live")

    def test_validate_stage_transition_blocks_live_promotion(self):
        """``micro_live → live`` must raise during Phase 1.

        This transition is otherwise valid stage-order-wise (live is
        the next stage after micro_live), so the only thing stopping
        it is the Phase-1 block. Verifying the block fires for the
        legal-but-Phase-1-blocked transition is the load-bearing
        assertion.
        """
        with pytest.raises(ValueError, match=r"Phase 1"):
            validate_stage_transition("micro_live", "live")

    # ------------------------------------------------------------------
    # Layer 3: Chokepoint architecture
    # ------------------------------------------------------------------

    def test_submit_order_only_called_from_chokepoint_files(self):
        """``.submit_order(`` calls must only appear in allowed files.

        Per [CLAUDE.md](CLAUDE.md) "execution/" is the "single
        chokepoint from intent → trade: invokes the risk layer, records
        explanations, submits to broker. No code path reaches the
        broker without passing through here." This test enforces that
        property structurally.

        Allowed call sites:

        - ``broker/client.py`` — abstract method definition.
        - ``broker/alpaca_client.py`` / ``broker/simulated.py`` —
          concrete broker implementations (they implement
          ``submit_order``; the alpaca client also calls the SDK's
          ``submit_order`` in its body).
        - ``execution/service.py`` — the single legitimate caller.

        Anywhere else introducing a ``.submit_order(`` call would
        bypass the risk evaluator's paper-mode check.
        """
        pattern = re.compile(r"\.submit_order\s*\(")

        violations: list[str] = []
        for path in _SRC_ROOT.rglob("*.py"):
            rel = path.relative_to(_SRC_ROOT).as_posix()
            if rel in _SUBMIT_ORDER_ALLOWED_RELPATHS:
                continue
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                violations.append(rel)

        assert not violations, (
            f"`.submit_order(` calls found outside the chokepoint files: "
            f"{violations}. Per ADR 0004 / CLAUDE.md, broker submission "
            f"must route through ExecutionService.submit_paper, which "
            f"invokes the risk evaluator's paper-mode check. If a new "
            f"call site is intentional, add it to "
            f"_SUBMIT_ORDER_ALLOWED_RELPATHS in this test and explain "
            f"in the commit message why the new caller does not bypass "
            f"the risk layer."
        )

    def test_allowed_relpaths_resolve_to_real_files(self):
        """Sanity: every entry in the allow-list points at a real file.

        Catches refactors that rename or move a chokepoint file
        without updating the allow-list — otherwise a missing file
        would silently widen the test's gap.
        """
        for rel in _SUBMIT_ORDER_ALLOWED_RELPATHS:
            assert (_SRC_ROOT / rel).is_file(), (
                f"_SUBMIT_ORDER_ALLOWED_RELPATHS references a missing "
                f"file: {rel}. If the chokepoint file moved, update the "
                f"allow-list. If it was deleted, the chokepoint itself "
                f"may be broken — investigate before re-running."
            )
