"""Tests for the PR G action-menu wiring path.

Verifies that ``_compute_bench_action_menu`` (the replacement for the deleted
``_bench_actions``) correctly delegates to ``compute_menu_items`` and that:

- The returned list is produced by the pure-function source of truth.
- ``Open Evidence`` is always the last item (ADR 0047 Decision 5).
- Forbidden labels from the legacy ``_bench_actions`` path are unreachable.
- Ordering is preserved: directional → invocation → informational.
- The ``verbClass`` and ``targetStage`` keys are present on every item.
- The ``_bench_actions`` and ``_bench_action`` names no longer exist in
  ``read_models`` (they have been deleted; no code path should route through
  them).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from milodex.gui.bench_v1 import (
    LABEL_OPEN_EVIDENCE,
    BenchStrategyState,
    EvidenceRecord,
    Freshness,
    GateResult,
    Stage,
    compute_menu_items,
)

# ---------------------------------------------------------------------------
# Helper — build a minimal _StrategyRow-like object
# ---------------------------------------------------------------------------


@dataclass
class _FakeRow:
    """Minimal stand-in for _StrategyRow to drive _compute_bench_action_menu."""

    strategy_id: str = "test.strategy.v1"
    name: str = "Test Strategy"
    stage: str = "backtest"
    session_state: str = "not_running"
    gate_failures: tuple = ()
    evidence_by_stage: dict = field(default_factory=dict)
    runs_in_flight: dict = field(default_factory=dict)
    job_status: str = ""


def _menu(row: _FakeRow) -> list[dict]:
    from milodex.gui.read_models import _compute_bench_action_menu

    return _compute_bench_action_menu(row)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Structural invariants
# ---------------------------------------------------------------------------


class TestOpenEvidenceFloor:
    """ADR 0047 Decision 5: Open Evidence is always the last item."""

    @pytest.mark.parametrize(
        "stage",
        ["idle", "backtest", "paper", "micro_live", "live"],
    )
    def test_open_evidence_is_last_for_every_stage(self, stage: str) -> None:
        row = _FakeRow(stage=stage)
        items = _menu(row)
        assert items[-1]["label"] == LABEL_OPEN_EVIDENCE
        assert items[-1]["verbClass"] == "informational"

    def test_menu_is_never_empty(self) -> None:
        for stage in ["idle", "backtest", "paper", "micro_live", "live"]:
            row = _FakeRow(stage=stage)
            assert len(_menu(row)) >= 1


class TestVerbOrdering:
    """directional verbs must precede invocation verbs; informational always last."""

    def test_directional_before_invocation(self) -> None:
        # PAPER row with synthetic Fresh+Pass evidence → has both directional
        # (Demote to Backtest, Return to Idle) and invocation (Start Trading).
        row = _FakeRow(stage="paper")
        items = _menu(row)
        verb_classes = [item["verbClass"] for item in items]
        saw_invocation = False
        for vc in verb_classes:
            if vc == "invocation":
                saw_invocation = True
            if vc == "directional":
                assert not saw_invocation, (
                    f"directional verb appeared after invocation in: {verb_classes}"
                )

    def test_informational_is_always_last(self) -> None:
        for stage in ["idle", "backtest", "paper", "micro_live", "live"]:
            items = _menu(_FakeRow(stage=stage))
            informational_indices = [
                i for i, item in enumerate(items) if item["verbClass"] == "informational"
            ]
            assert len(informational_indices) >= 1
            assert informational_indices[-1] == len(items) - 1, (
                f"informational item not last for stage={stage}"
            )


class TestRequiredKeys:
    """Every action dict must carry label, verbClass, and targetStage."""

    def test_all_items_have_required_keys(self) -> None:
        for stage in ["idle", "backtest", "paper", "micro_live", "live"]:
            for item in _menu(_FakeRow(stage=stage)):
                assert "label" in item, f"missing 'label' for stage={stage}"
                assert "verbClass" in item, f"missing 'verbClass' for stage={stage}"
                assert "targetStage" in item, f"missing 'targetStage' for stage={stage}"

    def test_directional_items_have_non_empty_target_stage(self) -> None:
        # BACKTEST row with synthetic Fresh+Pass → has "Promote to Paper" (directional).
        items = _menu(_FakeRow(stage="backtest"))
        for item in items:
            if item["verbClass"] == "directional":
                assert item["targetStage"] != "", (
                    f"directional verb '{item['label']}' has empty targetStage"
                )

    def test_informational_item_has_empty_target_stage(self) -> None:
        items = _menu(_FakeRow(stage="idle"))
        floor = next(i for i in items if i["verbClass"] == "informational")
        assert floor["targetStage"] == ""


# ---------------------------------------------------------------------------
# Forbidden labels (the old _bench_actions vocabulary)
# ---------------------------------------------------------------------------


FORBIDDEN_LABELS = {
    "Send to Idle",
    "Demote to Paper",
    "Demote to Micro Live",
    "Promote to Backtest",
}


class TestForbiddenLabels:
    """No row in the rendered UI should ever show forbidden labels."""

    @pytest.mark.parametrize(
        "stage",
        ["idle", "backtest", "paper", "micro_live", "live"],
    )
    def test_no_forbidden_labels_at_any_stage(self, stage: str) -> None:
        items = _menu(_FakeRow(stage=stage))
        labels = {item["label"] for item in items}
        found = FORBIDDEN_LABELS.intersection(labels)
        assert not found, f"Forbidden label(s) {found} found at stage={stage}"

    def test_no_send_to_idle_with_rich_evidence(self) -> None:
        # Construct a row with evidence_by_stage populated so the synthetic
        # fallback is bypassed — confirms forbidden labels are absent even
        # when real evidence is provided.
        row = _FakeRow(
            stage="backtest",
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        labels = {item["label"] for item in _menu(row)}
        assert "Send to Idle" not in labels


# ---------------------------------------------------------------------------
# ADR 0004 forward-promotion lock
# ---------------------------------------------------------------------------


class TestADR0004Lock:
    """Promote to Micro Live and Promote to Live must never appear in v1."""

    def test_promote_to_micro_live_never_appears(self) -> None:
        # PAPER with Fresh+Pass evidence: would promote to Micro Live without ADR 0004.
        row = _FakeRow(
            stage="paper",
            evidence_by_stage={Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS)},
        )
        labels = [item["label"] for item in _menu(row)]
        assert "Promote to Micro Live" not in labels

    def test_promote_to_live_never_appears(self) -> None:
        row = _FakeRow(
            stage="micro_live",
            evidence_by_stage={Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS)},
        )
        labels = [item["label"] for item in _menu(row)]
        assert "Promote to Live" not in labels


# ---------------------------------------------------------------------------
# Legacy symbol deletion guard
# ---------------------------------------------------------------------------


class TestLegacyCodePathDeleted:
    """_bench_actions and _bench_action no longer exist in read_models."""

    def test_bench_actions_function_not_exported(self) -> None:
        import milodex.gui.read_models as rm

        assert not hasattr(rm, "_bench_actions"), (
            "_bench_actions was not deleted; the legacy code path is still reachable"
        )

    def test_bench_action_function_not_exported(self) -> None:
        import milodex.gui.read_models as rm

        assert not hasattr(rm, "_bench_action"), (
            "_bench_action was not deleted; the legacy code path is still reachable"
        )


# ---------------------------------------------------------------------------
# Evidence_by_stage supplied vs. synthetic fallback
# ---------------------------------------------------------------------------


class TestEvidenceRouting:
    """When evidence_by_stage is provided, it overrides the synthetic fallback."""

    def test_real_evidence_missing_pending_suppresses_initiate_for_in_flight(self) -> None:
        # IDLE row, BACKTEST run in flight → only Open Evidence (in-flight suppression).
        row = _FakeRow(
            stage="idle",
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
            },
            runs_in_flight={Stage.BACKTEST: True},
        )
        items = _menu(row)
        labels = [item["label"] for item in items]
        assert "Initiate Backtest" not in labels
        assert labels == [LABEL_OPEN_EVIDENCE]

    def test_real_evidence_fresh_pass_at_backtest_promotes(self) -> None:
        row = _FakeRow(
            stage="backtest",
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        labels = [item["label"] for item in _menu(row)]
        assert "Promote to Paper" in labels

    def test_synthetic_fallback_backtest_surfaces_initiate(self) -> None:
        # No evidence_by_stage → synthetic fallback for idle → Initiate Backtest surfaces.
        row = _FakeRow(stage="idle")
        labels = [item["label"] for item in _menu(row)]
        assert "Initiate Backtest" in labels

    def test_synthetic_fallback_paper_surfaces_start_trading(self) -> None:
        # No evidence_by_stage → synthetic Fresh+Pass at PAPER stage → Start Trading surfaces.
        row = _FakeRow(stage="paper")
        labels = [item["label"] for item in _menu(row)]
        assert "Start Trading" in labels

    def test_compute_menu_items_equivalence(self) -> None:
        """_compute_bench_action_menu with real evidence must match compute_menu_items directly."""
        state = BenchStrategyState(
            current_stage=Stage.PAPER,
            evidence_by_stage={Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS)},
            is_session_running=True,
        )
        expected_labels = [item.label for item in compute_menu_items(state)]

        row = _FakeRow(
            stage="paper",
            session_state="running",
            evidence_by_stage={Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS)},
        )
        actual_labels = [item["label"] for item in _menu(row)]

        assert actual_labels == expected_labels
