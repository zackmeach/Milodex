"""Tests for the Bench v1 read-model schema and menu-rule pure functions.

Covers:

- Schema construction (enums, EvidenceRecord, BenchStrategyState, MenuItem).
- Each pure menu rule (``can_promote_to_next``, ``can_return_to``,
  ``re_run_verb``, ``can_demote``) walked across the relevant axis grids.
- The composer (``compute_menu_items``) for the canonical row states
  documented in ``docs/mockups/bench-brief.md`` §7.3.
- The empty-menu floor invariant (ADR 0047 Decision 5): the result is
  never empty; ``Open Evidence`` is the floor item.
- The two ADR-0004 / ADR-0043 capital-stage policy filters.

Per ADR 0049 (v1 visual-prototype scope), this PR adds schema and pure
functions only — no fixtures, no QML changes, no event-store wiring.
"""

from __future__ import annotations

import pytest

from milodex.gui.bench_v1 import (
    ADR_0004_HIDDEN_PROMOTION_TARGETS,
    ADR_0043_LIVE_LOCKED_DEMOTIONS_FROM,
    LABEL_DEMOTE_TO_BACKTEST,
    LABEL_FREEZE_MANIFEST,
    LABEL_INITIATE_BACKTEST,
    LABEL_OPEN_EVIDENCE,
    LABEL_REFRESH_BACKTEST,
    LABEL_RETURN_TO_IDLE,
    LABEL_START_TRADING,
    LABEL_STOP_TRADING,
    BenchStrategyState,
    EvidenceRecord,
    Freshness,
    GateResult,
    MenuItem,
    Stage,
    can_demote,
    can_promote_to_next,
    can_return_to,
    compute_menu_items,
    label_promote_to,
    label_return_to,
    re_run_verb,
)

# ---------------------------------------------------------------------------
# Schema construction
# ---------------------------------------------------------------------------


class TestEnumValues:
    """Enum string values must match the canonical lowercase form used in
    read_models.py and SQLite. Locked vocabulary."""

    def test_stage_string_values(self) -> None:
        assert Stage.IDLE == "idle"
        assert Stage.BACKTEST == "backtest"
        assert Stage.PAPER == "paper"
        assert Stage.MICRO_LIVE == "micro_live"
        assert Stage.LIVE == "live"

    def test_stage_enumeration_is_complete(self) -> None:
        assert {s.value for s in Stage} == {
            "idle",
            "backtest",
            "paper",
            "micro_live",
            "live",
        }

    def test_freshness_enumeration_is_complete(self) -> None:
        # ADR 0050 Decision 3: exactly five values.
        assert {f.value for f in Freshness} == {
            "missing",
            "fresh",
            "aging",
            "stale",
            "invalidated",
        }

    def test_gate_result_enumeration_is_complete(self) -> None:
        # ADR 0050 Decision 4: exactly four values.
        assert {g.value for g in GateResult} == {
            "pass",
            "fail",
            "pending",
            "not_applicable",
        }


class TestEvidenceRecordConstruction:
    def test_evidence_record_holds_two_axes(self) -> None:
        record = EvidenceRecord(freshness=Freshness.FRESH, gate_result=GateResult.PASS)
        assert record.freshness == Freshness.FRESH
        assert record.gate_result == GateResult.PASS

    def test_evidence_record_is_frozen(self) -> None:
        record = EvidenceRecord(freshness=Freshness.FRESH, gate_result=GateResult.PASS)
        with pytest.raises(Exception):
            record.freshness = Freshness.STALE  # type: ignore[misc]

    def test_evidence_record_does_not_carry_run_in_flight(self) -> None:
        # ADR 0050 Decision 3: runs_in_flight is operational state,
        # never inside EvidenceRecord. Asserting the field is absent
        # guards against reintroducing the bug PR A's quality review
        # caught.
        record = EvidenceRecord(freshness=Freshness.MISSING, gate_result=GateResult.PENDING)
        assert not hasattr(record, "is_run_in_flight")
        assert not hasattr(record, "runs_in_flight")


class TestBenchStrategyStateDefaults:
    def test_minimum_construction_only_requires_current_stage(self) -> None:
        state = BenchStrategyState(current_stage=Stage.IDLE)
        assert state.current_stage == Stage.IDLE
        assert state.evidence_by_stage == {}
        assert state.runs_in_flight == {}
        assert state.is_session_running is False

    def test_runs_in_flight_lives_on_state_not_inside_evidence(self) -> None:
        # ADR 0050 Decision 3 invariant.
        state = BenchStrategyState(current_stage=Stage.BACKTEST)
        assert isinstance(state.runs_in_flight, dict)
        assert "evidence_by_stage" in state.__dataclass_fields__
        assert "runs_in_flight" in state.__dataclass_fields__


class TestMenuItemConstruction:
    def test_menu_item_with_target_stage(self) -> None:
        item = MenuItem(
            label=label_promote_to(Stage.PAPER),
            verb_class="directional",
            target_stage=Stage.PAPER,
        )
        assert item.label == "Promote to Paper"
        assert item.verb_class == "directional"
        assert item.target_stage == Stage.PAPER

    def test_menu_item_without_target_stage(self) -> None:
        item = MenuItem(label=LABEL_OPEN_EVIDENCE, verb_class="informational")
        assert item.target_stage is None


# ---------------------------------------------------------------------------
# Locked label constants
# ---------------------------------------------------------------------------


class TestLockedLabels:
    """ADR 0050 Decision 7 verb grammar — exact strings."""

    def test_invocation_labels(self) -> None:
        assert LABEL_INITIATE_BACKTEST == "Initiate Backtest"
        assert LABEL_REFRESH_BACKTEST == "Refresh Backtest"
        assert LABEL_START_TRADING == "Start Trading"
        assert LABEL_STOP_TRADING == "Stop Trading"

    def test_directional_labels(self) -> None:
        assert LABEL_DEMOTE_TO_BACKTEST == "Demote to Backtest"
        assert LABEL_RETURN_TO_IDLE == "Return to Idle"
        assert label_promote_to(Stage.PAPER) == "Promote to Paper"
        assert label_promote_to(Stage.MICRO_LIVE) == "Promote to Micro Live"
        assert label_promote_to(Stage.LIVE) == "Promote to Live"
        assert label_return_to(Stage.PAPER) == "Return to Paper"
        assert label_return_to(Stage.MICRO_LIVE) == "Return to Micro Live"
        assert label_return_to(Stage.LIVE) == "Return to Live"

    def test_informational_floor_label(self) -> None:
        assert LABEL_OPEN_EVIDENCE == "Open Evidence"


# ---------------------------------------------------------------------------
# can_promote_to_next
# ---------------------------------------------------------------------------


class TestCanPromoteToNext:
    """Per ADR 0050 Decision 5: requires Fresh|Aging current-stage
    evidence with Pass gate. IDLE and LIVE never promote (no verb)."""

    def test_idle_never_promotes(self) -> None:
        # No `Promote to Backtest` verb exists per ADR 0050 Decision 7;
        # IDLE → BACKTEST is system-driven on backtest job acceptance.
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.IDLE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        assert can_promote_to_next(state) is False

    def test_live_never_promotes(self) -> None:
        state = BenchStrategyState(current_stage=Stage.LIVE)
        assert can_promote_to_next(state) is False

    def test_backtest_fresh_pass_promotes(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.BACKTEST,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        assert can_promote_to_next(state) is True

    def test_backtest_aging_pass_promotes(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.BACKTEST,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.AGING, GateResult.PASS),
            },
        )
        assert can_promote_to_next(state) is True

    @pytest.mark.parametrize(
        ("freshness", "gate_result"),
        [
            (Freshness.STALE, GateResult.PASS),  # stale fails
            (Freshness.FRESH, GateResult.FAIL),  # fail fails
            (Freshness.FRESH, GateResult.PENDING),  # pending fails
            (Freshness.MISSING, GateResult.PENDING),  # no completed evidence
            (Freshness.INVALIDATED, GateResult.PASS),  # invalidated fails
        ],
    )
    def test_backtest_non_pass_does_not_promote(
        self, freshness: Freshness, gate_result: GateResult
    ) -> None:
        state = BenchStrategyState(
            current_stage=Stage.BACKTEST,
            evidence_by_stage={Stage.BACKTEST: EvidenceRecord(freshness, gate_result)},
        )
        assert can_promote_to_next(state) is False

    def test_active_stage_with_no_evidence_record_does_not_promote(self) -> None:
        state = BenchStrategyState(current_stage=Stage.PAPER)
        assert can_promote_to_next(state) is False


# ---------------------------------------------------------------------------
# can_return_to
# ---------------------------------------------------------------------------


class TestCanReturnToIdle:
    """Return to Idle is the to-shelf affordance — available from any
    active stage, no freshness check. Not available from IDLE itself."""

    @pytest.mark.parametrize(
        "stage", [Stage.BACKTEST, Stage.PAPER, Stage.MICRO_LIVE, Stage.LIVE]
    )
    def test_return_to_idle_from_active_stage_is_allowed(self, stage: Stage) -> None:
        state = BenchStrategyState(current_stage=stage)
        assert can_return_to(state, Stage.IDLE) is True

    def test_return_to_idle_from_idle_is_not_allowed(self) -> None:
        state = BenchStrategyState(current_stage=Stage.IDLE)
        assert can_return_to(state, Stage.IDLE) is False


class TestCanReturnToActiveStage:
    """Return to <active stage> is the leave-IDLE affordance — only
    fires from IDLE; requires target evidence Fresh|Aging + Pass (or
    NotApplicable for LIVE specifically)."""

    @pytest.mark.parametrize(
        "active_origin", [Stage.BACKTEST, Stage.PAPER, Stage.MICRO_LIVE, Stage.LIVE]
    )
    def test_active_to_active_is_not_a_return_verb(self, active_origin: Stage) -> None:
        # Active-to-active is Promote/Demote territory, not Return.
        state = BenchStrategyState(
            current_stage=active_origin,
            evidence_by_stage={
                Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        for target in (Stage.PAPER, Stage.MICRO_LIVE, Stage.LIVE):
            if target == active_origin:
                continue
            assert can_return_to(state, target) is False

    @pytest.mark.parametrize("target", [Stage.PAPER, Stage.MICRO_LIVE])
    def test_idle_with_fresh_pass_target_evidence_returns_to_target(
        self, target: Stage
    ) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                target: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        assert can_return_to(state, target) is True

    @pytest.mark.parametrize("target", [Stage.PAPER, Stage.MICRO_LIVE])
    def test_idle_with_aging_pass_target_evidence_returns_to_target(
        self, target: Stage
    ) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                target: EvidenceRecord(Freshness.AGING, GateResult.PASS),
            },
        )
        assert can_return_to(state, target) is True

    @pytest.mark.parametrize(
        ("freshness", "gate_result"),
        [
            (Freshness.STALE, GateResult.PASS),
            (Freshness.FRESH, GateResult.FAIL),
            (Freshness.AGING, GateResult.FAIL),
            (Freshness.INVALIDATED, GateResult.PASS),
            (Freshness.MISSING, GateResult.PENDING),
        ],
    )
    def test_idle_with_unusable_target_evidence_does_not_return(
        self, freshness: Freshness, gate_result: GateResult
    ) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.PAPER: EvidenceRecord(freshness, gate_result),
            },
        )
        assert can_return_to(state, Stage.PAPER) is False

    def test_idle_with_no_target_evidence_does_not_return(self) -> None:
        state = BenchStrategyState(current_stage=Stage.IDLE)
        assert can_return_to(state, Stage.PAPER) is False

    def test_return_to_live_accepts_not_applicable(self) -> None:
        # ADR 0050 Decision 5: NotApplicable is a valid gate_result for
        # LIVE-stage Return because LIVE has no further promotion gate.
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.LIVE: EvidenceRecord(Freshness.FRESH, GateResult.NOT_APPLICABLE),
            },
        )
        assert can_return_to(state, Stage.LIVE) is True

    def test_return_to_live_accepts_pass(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        assert can_return_to(state, Stage.LIVE) is True

    @pytest.mark.parametrize("non_live_target", [Stage.PAPER, Stage.MICRO_LIVE])
    def test_not_applicable_does_not_unlock_non_live_returns(
        self, non_live_target: Stage
    ) -> None:
        # NotApplicable is a LIVE-only wildcard. For non-LIVE targets,
        # only Pass satisfies the gate check.
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                non_live_target: EvidenceRecord(
                    Freshness.FRESH, GateResult.NOT_APPLICABLE
                ),
            },
        )
        assert can_return_to(state, non_live_target) is False


# ---------------------------------------------------------------------------
# re_run_verb
# ---------------------------------------------------------------------------


class TestReRunVerb:
    """Walk the freshness × gate_result × is_run_in_flight grid."""

    @pytest.mark.parametrize(
        ("freshness", "gate_result"),
        [
            (Freshness.MISSING, GateResult.PENDING),
            (Freshness.FRESH, GateResult.PASS),
            (Freshness.FRESH, GateResult.FAIL),
            (Freshness.AGING, GateResult.PASS),
            (Freshness.AGING, GateResult.FAIL),
            (Freshness.STALE, GateResult.PASS),
            (Freshness.STALE, GateResult.FAIL),
            (Freshness.INVALIDATED, GateResult.PASS),
            (Freshness.INVALIDATED, GateResult.FAIL),
        ],
    )
    def test_in_flight_suppresses_any_re_run_verb(
        self, freshness: Freshness, gate_result: GateResult
    ) -> None:
        evidence = EvidenceRecord(freshness=freshness, gate_result=gate_result)
        assert re_run_verb(evidence, is_run_in_flight=True) is None

    @pytest.mark.parametrize("freshness", [Freshness.AGING, Freshness.STALE])
    def test_aging_or_stale_pass_yields_refresh(self, freshness: Freshness) -> None:
        evidence = EvidenceRecord(freshness=freshness, gate_result=GateResult.PASS)
        assert re_run_verb(evidence, is_run_in_flight=False) == LABEL_REFRESH_BACKTEST

    @pytest.mark.parametrize("freshness", [Freshness.AGING, Freshness.STALE])
    def test_aging_or_stale_fail_yields_initiate(self, freshness: Freshness) -> None:
        evidence = EvidenceRecord(freshness=freshness, gate_result=GateResult.FAIL)
        assert re_run_verb(evidence, is_run_in_flight=False) == LABEL_INITIATE_BACKTEST

    def test_missing_with_no_in_flight_run_yields_initiate(self) -> None:
        evidence = EvidenceRecord(freshness=Freshness.MISSING, gate_result=GateResult.PENDING)
        assert re_run_verb(evidence, is_run_in_flight=False) == LABEL_INITIATE_BACKTEST

    @pytest.mark.parametrize(
        "gate_result", [GateResult.PASS, GateResult.FAIL, GateResult.PENDING]
    )
    def test_invalidated_yields_initiate_regardless_of_gate(
        self, gate_result: GateResult
    ) -> None:
        evidence = EvidenceRecord(freshness=Freshness.INVALIDATED, gate_result=gate_result)
        assert re_run_verb(evidence, is_run_in_flight=False) == LABEL_INITIATE_BACKTEST

    @pytest.mark.parametrize("gate_result", [GateResult.PASS, GateResult.FAIL])
    def test_fresh_yields_no_re_run_verb_workflow_discipline(
        self, gate_result: GateResult
    ) -> None:
        # Workflow discipline per ADR 0050 Decision 5: Fresh+Pass and
        # Fresh+Fail produce no re-run verb. The operator must change
        # something (config, parameters) which transitions evidence to
        # Invalidated; only then does Initiate Backtest appear.
        evidence = EvidenceRecord(freshness=Freshness.FRESH, gate_result=gate_result)
        assert re_run_verb(evidence, is_run_in_flight=False) is None

    def test_live_evidence_not_applicable_yields_no_re_run_verb(self) -> None:
        # Per the comment in re_run_verb: LIVE-stage evidence has no
        # backtest re-run concept.
        evidence = EvidenceRecord(freshness=Freshness.FRESH, gate_result=GateResult.NOT_APPLICABLE)
        assert re_run_verb(evidence, is_run_in_flight=False) is None

    @pytest.mark.parametrize("freshness", [Freshness.AGING, Freshness.STALE])
    def test_pending_on_aging_or_stale_evidence_yields_no_verb(
        self, freshness: Freshness
    ) -> None:
        # Aging+Pending and Stale+Pending mean a run is producing new
        # evidence on top of prior aged evidence (a refresh-in-flight
        # case). Open Evidence carries the monitoring affordance; no
        # second re-run verb appears even when is_run_in_flight=False
        # for symmetry with the in_flight=True case.
        evidence = EvidenceRecord(freshness=freshness, gate_result=GateResult.PENDING)
        assert re_run_verb(evidence, is_run_in_flight=False) is None


# ---------------------------------------------------------------------------
# can_demote
# ---------------------------------------------------------------------------


class TestCanDemote:
    @pytest.mark.parametrize("stage", [Stage.IDLE, Stage.BACKTEST])
    def test_demote_unavailable_at_idle_or_backtest(self, stage: Stage) -> None:
        state = BenchStrategyState(current_stage=stage)
        assert can_demote(state) is False

    @pytest.mark.parametrize("stage", [Stage.PAPER, Stage.MICRO_LIVE, Stage.LIVE])
    def test_demote_available_at_paper_and_higher(self, stage: Stage) -> None:
        state = BenchStrategyState(current_stage=stage)
        assert can_demote(state) is True


# ---------------------------------------------------------------------------
# Composer — canonical row states from bench-brief §7.3
# ---------------------------------------------------------------------------


def _labels(items: list[MenuItem]) -> list[str]:
    return [item.label for item in items]


class TestComposerIdleRows:
    """IDLE rows from bench-brief §7.3."""

    def test_idle_no_history(self) -> None:
        # Missing+Pending evidence at every stage; no run in flight.
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
            },
        )
        assert _labels(compute_menu_items(state)) == [
            LABEL_INITIATE_BACKTEST,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_idle_no_history_backtest_run_in_flight(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
            },
            runs_in_flight={Stage.BACKTEST: True},
        )
        assert _labels(compute_menu_items(state)) == [LABEL_OPEN_EVIDENCE]

    def test_idle_with_prior_paper_fresh_pass_and_no_backtest_history(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        # Directional first (Return to Paper), then invocation (Initiate
        # Backtest), then floor (Open Evidence).
        assert _labels(compute_menu_items(state)) == [
            "Return to Paper",
            LABEL_INITIATE_BACKTEST,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_idle_with_prior_micro_live_fresh_pass(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        labels = _labels(compute_menu_items(state))
        assert "Return to Micro Live" in labels
        assert LABEL_INITIATE_BACKTEST in labels
        assert LABEL_OPEN_EVIDENCE == labels[-1]

    def test_idle_with_prior_live_fresh_not_applicable(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
                Stage.LIVE: EvidenceRecord(Freshness.FRESH, GateResult.NOT_APPLICABLE),
            },
        )
        labels = _labels(compute_menu_items(state))
        assert "Return to Live" in labels
        assert LABEL_INITIATE_BACKTEST in labels
        assert labels[-1] == LABEL_OPEN_EVIDENCE

    def test_idle_with_micro_live_stale_pass_and_backtest_stale_pass(self) -> None:
        # Per bench-brief §7.3: when prior MICRO LIVE evidence is
        # Stale+Pass and BACKTEST evidence is also Stale+Pass, the menu
        # surfaces Refresh Backtest (driven by BACKTEST evidence) plus
        # the Open Evidence floor.
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.STALE, GateResult.PASS),
                Stage.MICRO_LIVE: EvidenceRecord(Freshness.STALE, GateResult.PASS),
            },
        )
        assert _labels(compute_menu_items(state)) == [
            LABEL_REFRESH_BACKTEST,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_idle_with_micro_live_stale_fail_and_backtest_stale_fail(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.IDLE,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.STALE, GateResult.FAIL),
                Stage.MICRO_LIVE: EvidenceRecord(Freshness.STALE, GateResult.FAIL),
            },
        )
        assert _labels(compute_menu_items(state)) == [
            LABEL_INITIATE_BACKTEST,
            LABEL_OPEN_EVIDENCE,
        ]


class TestComposerBacktestRows:
    """BACKTEST rows from bench-brief §7.3."""

    def test_backtest_fresh_pass_no_run_in_flight(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.BACKTEST,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        assert _labels(compute_menu_items(state)) == [
            "Promote to Paper",
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_backtest_fresh_fail(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.BACKTEST,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.FAIL),
            },
        )
        # No Promote (fail), no re-run (Fresh+Fail = workflow discipline).
        assert _labels(compute_menu_items(state)) == [
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_backtest_aging_pass(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.BACKTEST,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.AGING, GateResult.PASS),
            },
        )
        # Directional first (Promote, Return to Idle), then invocation
        # (Refresh Backtest), then floor.
        assert _labels(compute_menu_items(state)) == [
            "Promote to Paper",
            LABEL_RETURN_TO_IDLE,
            LABEL_REFRESH_BACKTEST,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_backtest_run_in_flight(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.BACKTEST,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING),
            },
            runs_in_flight={Stage.BACKTEST: True},
        )
        assert _labels(compute_menu_items(state)) == [
            LABEL_RETURN_TO_IDLE,
            LABEL_OPEN_EVIDENCE,
        ]


class TestComposerPaperRows:
    def test_paper_fresh_pass_session_idle(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.PAPER,
            evidence_by_stage={
                Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        # Promote to Micro Live hidden (ADR 0004); no re-run verb (no
        # BACKTEST evidence record). Directional verbs (Demote, Return
        # to Idle) precede invocation (Freeze Manifest, Start Trading)
        # per the ordering rule. Freeze Manifest (ADR 0051 Phase D1)
        # surfaces at every _FREEZE_MANIFEST_STAGES row.
        assert _labels(compute_menu_items(state)) == [
            LABEL_DEMOTE_TO_BACKTEST,
            LABEL_RETURN_TO_IDLE,
            LABEL_FREEZE_MANIFEST,
            LABEL_START_TRADING,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_paper_session_running(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.PAPER,
            evidence_by_stage={
                Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
            is_session_running=True,
        )
        assert _labels(compute_menu_items(state)) == [
            LABEL_DEMOTE_TO_BACKTEST,
            LABEL_RETURN_TO_IDLE,
            LABEL_FREEZE_MANIFEST,
            LABEL_STOP_TRADING,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_paper_aging_pass_with_backtest_aging_pass(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.PAPER,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.AGING, GateResult.PASS),
                Stage.PAPER: EvidenceRecord(Freshness.AGING, GateResult.PASS),
            },
        )
        # Promote to Micro Live still hidden (ADR 0004); Refresh Backtest
        # now surfaces because BACKTEST evidence is Aging+Pass. Directional
        # verbs (Demote, Return to Idle) come before invocation (Freeze
        # Manifest, Start Trading, Refresh Backtest).
        assert _labels(compute_menu_items(state)) == [
            LABEL_DEMOTE_TO_BACKTEST,
            LABEL_RETURN_TO_IDLE,
            LABEL_FREEZE_MANIFEST,
            LABEL_START_TRADING,
            LABEL_REFRESH_BACKTEST,
            LABEL_OPEN_EVIDENCE,
        ]


class TestComposerCapitalStageRows:
    """MICRO LIVE and LIVE rows under ADR 0004 lock per bench-brief §7.3."""

    def test_micro_live_session_idle(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.MICRO_LIVE,
            evidence_by_stage={
                Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        # Promote to Live hidden by ADR 0004 forward lock.
        # Demote to Backtest hidden by ADR 0043 Decision 3 + ADR 0004.
        # Directional Return to Idle precedes invocation Freeze Manifest
        # then Start Trading.
        assert _labels(compute_menu_items(state)) == [
            LABEL_RETURN_TO_IDLE,
            LABEL_FREEZE_MANIFEST,
            LABEL_START_TRADING,
            LABEL_OPEN_EVIDENCE,
        ]

    def test_live_session_idle(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.LIVE,
            evidence_by_stage={
                Stage.LIVE: EvidenceRecord(Freshness.FRESH, GateResult.NOT_APPLICABLE),
            },
        )
        # No further promotion (LIVE is terminal).
        # Demote and Return to Micro Live both capital-affecting →
        # hidden by ADR 0043 Decision 3 + ADR 0004.
        # Directional Return to Idle precedes invocation Freeze Manifest
        # then Start Trading.
        assert _labels(compute_menu_items(state)) == [
            LABEL_RETURN_TO_IDLE,
            LABEL_FREEZE_MANIFEST,
            LABEL_START_TRADING,
            LABEL_OPEN_EVIDENCE,
        ]


# ---------------------------------------------------------------------------
# Empty-menu floor invariant (ADR 0047 Decision 5)
# ---------------------------------------------------------------------------


class TestEmptyMenuFloor:
    """The Action menu is never empty. Open Evidence is always present
    as the floor item."""

    @pytest.mark.parametrize(
        "stage", [Stage.IDLE, Stage.BACKTEST, Stage.PAPER, Stage.MICRO_LIVE, Stage.LIVE]
    )
    def test_open_evidence_present_at_every_stage_with_no_evidence(
        self, stage: Stage
    ) -> None:
        state = BenchStrategyState(current_stage=stage)
        items = compute_menu_items(state)
        assert items[-1].label == LABEL_OPEN_EVIDENCE
        assert items[-1].verb_class == "informational"

    def test_open_evidence_present_when_every_state_changing_verb_hidden(self) -> None:
        # Construct the worst case: BACKTEST with Fresh+Fail evidence
        # (no promote per gate-fail; no re-run verb per workflow
        # discipline; no Demote at BACKTEST).
        state = BenchStrategyState(
            current_stage=Stage.BACKTEST,
            evidence_by_stage={
                Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.FAIL),
            },
        )
        items = compute_menu_items(state)
        # Return to Idle is always available from active stages, so the
        # absolute-zero-state-changing-verbs case requires IDLE plus no
        # evidence:
        idle_state = BenchStrategyState(current_stage=Stage.IDLE)
        idle_items = compute_menu_items(idle_state)
        assert _labels(idle_items) == [LABEL_OPEN_EVIDENCE]
        # And the BACKTEST+Fresh+Fail row is also non-empty:
        assert items[-1].label == LABEL_OPEN_EVIDENCE
        assert len(items) >= 1

    def test_compute_menu_items_never_returns_empty(self) -> None:
        # Brute-force: walk every stage with empty everything and assert
        # the result is at least one item.
        for stage in Stage:
            state = BenchStrategyState(current_stage=stage)
            items = compute_menu_items(state)
            assert len(items) >= 1
            assert items[-1].label == LABEL_OPEN_EVIDENCE


# ---------------------------------------------------------------------------
# Capital-stage policy filters (ADR 0004 + ADR 0043 Decision 3)
# ---------------------------------------------------------------------------


class TestCapitalStagePolicy:
    def test_adr_0004_hides_promote_to_micro_live_and_live(self) -> None:
        assert Stage.MICRO_LIVE in ADR_0004_HIDDEN_PROMOTION_TARGETS
        assert Stage.LIVE in ADR_0004_HIDDEN_PROMOTION_TARGETS

    def test_adr_0004_does_not_hide_promote_to_paper(self) -> None:
        assert Stage.PAPER not in ADR_0004_HIDDEN_PROMOTION_TARGETS

    def test_adr_0043_locks_demotions_from_micro_live_and_live(self) -> None:
        assert Stage.MICRO_LIVE in ADR_0043_LIVE_LOCKED_DEMOTIONS_FROM
        assert Stage.LIVE in ADR_0043_LIVE_LOCKED_DEMOTIONS_FROM

    def test_adr_0043_does_not_lock_demote_from_paper(self) -> None:
        assert Stage.PAPER not in ADR_0043_LIVE_LOCKED_DEMOTIONS_FROM

    def test_promote_to_micro_live_never_appears_in_v1(self) -> None:
        # Even with PAPER evidence Fresh+Pass, the menu hides Promote
        # to Micro Live in v1.
        state = BenchStrategyState(
            current_stage=Stage.PAPER,
            evidence_by_stage={
                Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        labels = _labels(compute_menu_items(state))
        assert "Promote to Micro Live" not in labels

    def test_promote_to_live_never_appears_in_v1(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.MICRO_LIVE,
            evidence_by_stage={
                Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        labels = _labels(compute_menu_items(state))
        assert "Promote to Live" not in labels

    def test_demote_from_micro_live_never_appears_in_v1(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.MICRO_LIVE,
            evidence_by_stage={
                Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
            },
        )
        labels = _labels(compute_menu_items(state))
        assert LABEL_DEMOTE_TO_BACKTEST not in labels

    def test_demote_from_live_never_appears_in_v1(self) -> None:
        state = BenchStrategyState(
            current_stage=Stage.LIVE,
            evidence_by_stage={
                Stage.LIVE: EvidenceRecord(Freshness.FRESH, GateResult.NOT_APPLICABLE),
            },
        )
        labels = _labels(compute_menu_items(state))
        assert LABEL_DEMOTE_TO_BACKTEST not in labels


# ---------------------------------------------------------------------------
# Forbidden vocabulary
# ---------------------------------------------------------------------------


class TestForbiddenVocabulary:
    """ADR 0050 Decision 7 explicitly forbids `Send to Idle` and
    `Promote to Backtest`. These checks lock the absence."""

    def test_no_send_to_idle_label_constant(self) -> None:
        # Module exposes Return to Idle, never Send to Idle.
        from milodex.gui import bench_v1

        public_attrs = [name for name in dir(bench_v1) if not name.startswith("_")]
        for name in public_attrs:
            value = getattr(bench_v1, name)
            if isinstance(value, str):
                assert "Send to Idle" not in value, name

    def test_no_promote_to_backtest_label_helper(self) -> None:
        # label_promote_to(BACKTEST) would produce "Promote to Backtest"
        # if naively called — we verify the composer never produces it.
        # The guard is structural: can_promote_to_next returns False at
        # IDLE (the only stage whose next is BACKTEST), so the composer
        # never reaches label_promote_to(BACKTEST).
        for current in Stage:
            state = BenchStrategyState(
                current_stage=current,
                evidence_by_stage={
                    Stage.IDLE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                    Stage.BACKTEST: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                    Stage.PAPER: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                    Stage.MICRO_LIVE: EvidenceRecord(Freshness.FRESH, GateResult.PASS),
                    Stage.LIVE: EvidenceRecord(Freshness.FRESH, GateResult.NOT_APPLICABLE),
                },
            )
            labels = _labels(compute_menu_items(state))
            assert "Promote to Backtest" not in labels, current
