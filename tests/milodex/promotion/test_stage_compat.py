"""Unit tests for milodex.promotion.stage_compat.

Covers ``ALLOWED_STAGES_BY_MODE`` and ``RECOGNIZED_MODES`` at their new public
home in the promotion layer (refactor/bench-facade-layering). This table was
previously private to ``milodex.cli.commands.strategy`` as ``_ALLOWED_STAGES_BY_MODE``;
moving it here eliminates the layering inversion where the bench command facade
was reaching into CLI internals to enforce stage-compatibility.
"""

from __future__ import annotations

from milodex.promotion.stage_compat import ALLOWED_STAGES_BY_MODE, RECOGNIZED_MODES


def test_allowed_stages_by_mode_contains_paper():
    """Phase 1 requires paper mode to be present and to allow paper-stage strategies."""
    assert "paper" in ALLOWED_STAGES_BY_MODE
    assert "paper" in ALLOWED_STAGES_BY_MODE["paper"]


def test_recognized_modes_matches_allowed_stages_keys():
    """RECOGNIZED_MODES must be the exact key-set of ALLOWED_STAGES_BY_MODE."""
    assert RECOGNIZED_MODES == frozenset(ALLOWED_STAGES_BY_MODE.keys())


def test_allowed_stages_values_are_frozensets():
    """Each value in the table must be a frozenset (immutable, usable in 'in' tests)."""
    for mode, stages in ALLOWED_STAGES_BY_MODE.items():
        assert isinstance(stages, frozenset), (
            f"ALLOWED_STAGES_BY_MODE[{mode!r}] must be a frozenset, got {type(stages)}"
        )


def test_live_and_micro_live_not_in_phase_1_table():
    """Phase 1 explicitly does not open live or micro_live trading modes."""
    assert "live" not in ALLOWED_STAGES_BY_MODE
    assert "micro_live" not in ALLOWED_STAGES_BY_MODE


def test_backtest_stage_not_allowed_under_paper_mode():
    """A backtest-stage strategy must not be eligible to run in paper mode."""
    assert "backtest" not in ALLOWED_STAGES_BY_MODE["paper"]
