"""Phase B tests for ``milodex.commands.bench``.

These tests pin the proposal/validation contract from ADR 0051 §3–§9 before
any submit wiring lands. They cover:

- shape of ``Blocker``, ``Precondition``, ``CommandProposal``, ``CommandResult``
- admissibility on the happy path for each action family
- structured-blocker shape on the unhappy paths the GUI must surface
- micro-live / live remain absent from the facade surface (no methods, no
  routes, no accepted ``to_stage``)
- the module does not import PySide6, broker, runner, or execution-write paths
- the GUI's existing forbidden-token contract (ADR 0049 / BENCH_BOUNDARY) is
  not weakened by Phase B
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
import sys
import textwrap
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import date, datetime
from pathlib import Path

import pytest

from milodex.commands import bench as facade_module
from milodex.commands.bench import (
    ACTION_FAMILIES,
    ACTION_FAMILY_BACKTEST,
    ACTION_FAMILY_DEMOTE,
    ACTION_FAMILY_FREEZE_MANIFEST,
    ACTION_FAMILY_PROMOTE_TO_PAPER,
    ACTION_FAMILY_START_PAPER_RUNNER,
    ACTION_FAMILY_STOP_PAPER_RUNNER,
    BenchCommandFacade,
    Blocker,
    CommandProposal,
    CommandResult,
    Precondition,
)
from milodex.core.event_store import EventStore

# Canonical id used by every test config. The loader cross-validates
# strategy.id against family/template/variant/version, so we pin one shape
# and give each test its own tmp config_dir.
STRATEGY_ID = "sample.daily.example.curated.v1"

_STRATEGY_YAML_TEMPLATE = textwrap.dedent(
    """\
    strategy:
      id: "sample.daily.example.curated.v1"
      family: "sample"
      template: "daily.example"
      variant: "curated"
      version: 1
      description: "Phase B facade test strategy."
      enabled: true
      universe: ["AAPL", "MSFT"]
      parameters:
        lookback_days: 20
      tempo:
        bar_size: "1D"
        min_hold_days: 1
        max_hold_days: 5
      risk:
        max_position_pct: 0.10
        max_positions: 3
        daily_loss_cap_pct: 0.02
        stop_loss_pct: 0.05
      stage: "{stage}"
      backtest:
        commission_per_trade: 0.00
        min_trades_required: 30
      disable_conditions_additional: []
    """
)


def _write_strategy(config_dir: Path, *, stage: str) -> Path:
    path = config_dir / "strategy.yaml"
    path.write_text(_STRATEGY_YAML_TEMPLATE.format(stage=stage), encoding="utf-8")
    return path


def _seed_runner_lock(locks_dir: Path) -> Path:
    """Write a holder file mimicking an active runner.

    PID=1 exists on every test platform (Windows ``System Idle Process``,
    Linux ``init``/``systemd``), so the holder is treated as live by
    ``AdvisoryLock._read_holder`` + ``_process_exists``.
    """
    holder_path = locks_dir / f"milodex.runtime.strategy.{STRATEGY_ID}.lock"
    holder_path.write_text(
        json.dumps(
            {
                "pid": 1,
                "hostname": "test-host",
                "holder_name": f"milodex strategy run {STRATEGY_ID}",
                "started_at": "2026-05-14T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return holder_path


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    d = tmp_path / "configs"
    d.mkdir()
    return d


@pytest.fixture
def locks_dir(tmp_path: Path) -> Path:
    d = tmp_path / "locks"
    d.mkdir()
    return d


@pytest.fixture
def event_store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events" / "milodex.db")


@pytest.fixture
def make_facade(config_dir: Path, locks_dir: Path, event_store: EventStore):
    def _make(
        *,
        trading_mode: str = "paper",
        with_event_store: bool = True,
    ) -> BenchCommandFacade:
        return BenchCommandFacade(
            config_dir=config_dir,
            locks_dir=locks_dir,
            get_trading_mode=lambda: trading_mode,
            event_store_factory=(lambda: event_store) if with_event_store else None,
        )

    return _make


# --------------------------------------------------------------------------- #
# Dataclass shape
# --------------------------------------------------------------------------- #


def test_blocker_is_frozen_dataclass_with_named_fields() -> None:
    b = Blocker(reason_code="x", message="y", context={"a": 1})
    assert is_dataclass(b)
    with pytest.raises(FrozenInstanceError):
        b.reason_code = "z"  # type: ignore[misc]
    assert b.to_dict() == {"reason_code": "x", "message": "y", "context": {"a": 1}}


def test_precondition_is_frozen_dataclass_with_named_fields() -> None:
    p = Precondition(name="stage_ok", passed=True, detail="hello")
    assert is_dataclass(p)
    with pytest.raises(FrozenInstanceError):
        p.passed = False  # type: ignore[misc]
    assert p.to_dict() == {"name": "stage_ok", "passed": True, "detail": "hello"}


def test_command_proposal_has_required_fields() -> None:
    proposal = CommandProposal(
        action_family=ACTION_FAMILY_DEMOTE,
        strategy_id="x.y.z",
        inputs={"to_stage": "backtest"},
        state_snapshot={"stage": "paper"},
        preconditions=[Precondition("p", True)],
        projected_outcome={"summary": "demote"},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="abc",
    )
    assert proposal.admissible is True
    d = proposal.to_dict()
    assert d["action_family"] == ACTION_FAMILY_DEMOTE
    assert d["strategy_id"] == "x.y.z"
    assert d["proposal_id"] == "abc"
    assert d["blockers"] == []
    assert d["preconditions"][0]["name"] == "p"


def test_command_proposal_admissibility_flips_with_blockers() -> None:
    proposal = CommandProposal(
        action_family=ACTION_FAMILY_DEMOTE,
        strategy_id="x.y.z",
        inputs={},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[Blocker(reason_code="r", message="m")],
        proposed_at=datetime.now(),
        proposal_id="abc",
    )
    assert proposal.admissible is False


def test_command_result_shape() -> None:
    result = CommandResult(
        proposal_id="abc",
        action_family=ACTION_FAMILY_DEMOTE,
        status="blocked",
        blockers=[Blocker(reason_code="r", message="m")],
    )
    d = result.to_dict()
    assert d["proposal_id"] == "abc"
    assert d["status"] == "blocked"
    assert d["durable_refs"] == {}
    assert d["audit_event_id"] is None
    assert d["submitted_at"] is None


def test_action_families_tuple_matches_constants() -> None:
    assert set(ACTION_FAMILIES) == {
        ACTION_FAMILY_BACKTEST,
        ACTION_FAMILY_FREEZE_MANIFEST,
        ACTION_FAMILY_PROMOTE_TO_PAPER,
        ACTION_FAMILY_DEMOTE,
        ACTION_FAMILY_START_PAPER_RUNNER,
        ACTION_FAMILY_STOP_PAPER_RUNNER,
    }


# --------------------------------------------------------------------------- #
# Backtest proposals
# --------------------------------------------------------------------------- #


def test_propose_backtest_admissible_for_known_strategy(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_backtest(
        STRATEGY_ID, start=date(2025, 1, 1), end=date(2025, 6, 30)
    )
    assert proposal.action_family == ACTION_FAMILY_BACKTEST
    assert proposal.admissible, proposal.blockers
    assert proposal.state_snapshot["stage"] == "backtest"
    assert (
        proposal.projected_outcome["eventual_callee"]
        == "milodex.backtesting.engine.BacktestEngine.run"
    )


def test_propose_backtest_walk_forward_routes_to_walk_forward_callee(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_backtest(
        STRATEGY_ID,
        start=date(2025, 1, 1),
        end=date(2025, 6, 30),
        walk_forward=True,
    )
    assert (
        "walk_forward_runner.run_walk_forward"
        in proposal.projected_outcome["eventual_callee"]
    )


def test_propose_backtest_blocks_when_dates_inverted(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_backtest(
        STRATEGY_ID, start=date(2025, 6, 30), end=date(2025, 1, 1)
    )
    assert not proposal.admissible
    codes = [b.reason_code for b in proposal.blockers]
    assert "invalid_date_range" in codes


def test_propose_backtest_blocks_unknown_strategy(make_facade) -> None:
    facade = make_facade()
    proposal = facade.propose_backtest(
        "nope.nope.nope.v1", start=date(2025, 1, 1), end=date(2025, 6, 30)
    )
    assert not proposal.admissible
    assert proposal.blockers[0].reason_code == "strategy_not_found"


# --------------------------------------------------------------------------- #
# Freeze manifest proposals
# --------------------------------------------------------------------------- #


def test_propose_freeze_manifest_admissible_for_paper_stage(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_freeze_manifest(STRATEGY_ID)
    assert proposal.admissible, proposal.blockers
    assert proposal.projected_outcome["eventual_callee"] == (
        "milodex.promotion.manifest.freeze_manifest"
    )


def test_propose_freeze_manifest_blocks_backtest_stage(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_freeze_manifest(STRATEGY_ID)
    assert not proposal.admissible
    assert proposal.blockers[0].reason_code == "stage_not_freezable"
    pre = {p.name: p.passed for p in proposal.preconditions}
    assert pre["stage_is_freezable"] is False


# --------------------------------------------------------------------------- #
# Promote to paper proposals
# --------------------------------------------------------------------------- #


def test_propose_promote_to_paper_requires_evidence_inputs(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_promote_to_paper(STRATEGY_ID)
    assert not proposal.admissible
    codes = {b.reason_code for b in proposal.blockers}
    assert "missing_recommendation" in codes
    assert "missing_known_risks" in codes
    assert "missing_run_id" in codes


def test_propose_promote_to_paper_admissible_with_evidence_and_run_id(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_promote_to_paper(
        STRATEGY_ID,
        recommendation="OOS Sharpe stable across windows; promote.",
        known_risks=["regime-dependent edge"],
        run_id="bt-uuid-1234",
    )
    assert proposal.admissible, proposal.blockers
    assert proposal.projected_outcome["to_stage"] == "paper"
    assert proposal.projected_outcome["promotion_type"] == "statistical"


def test_propose_promote_to_paper_lifecycle_exempt_skips_run_id_requirement(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_promote_to_paper(
        STRATEGY_ID,
        recommendation="Regime strategy; lifecycle-exempt per R-PRM-004.",
        known_risks=["whipsaw"],
        lifecycle_exempt=True,
    )
    assert proposal.admissible, proposal.blockers
    assert proposal.projected_outcome["promotion_type"] == "lifecycle_exempt"


def test_propose_promote_to_paper_blocks_wrong_source_stage(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_promote_to_paper(
        STRATEGY_ID,
        recommendation="x",
        known_risks=["y"],
        run_id="z",
    )
    assert not proposal.admissible
    assert any(b.reason_code == "wrong_source_stage" for b in proposal.blockers)


# --------------------------------------------------------------------------- #
# Demote proposals
# --------------------------------------------------------------------------- #


def test_propose_demote_admissible_for_paper_to_backtest(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_demote(
        STRATEGY_ID,
        to_stage="backtest",
        reason="OOS evidence degraded over last 30 days.",
    )
    assert proposal.admissible, proposal.blockers
    assert proposal.projected_outcome["from_stage"] == "paper"
    assert proposal.projected_outcome["to_stage"] == "backtest"
    assert proposal.projected_outcome["yaml_updated"] is True


def test_propose_demote_requires_reason(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_demote(STRATEGY_ID, to_stage="backtest")
    assert not proposal.admissible
    assert any(b.reason_code == "missing_reason" for b in proposal.blockers)


def test_propose_demote_rejects_invalid_target(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_demote(STRATEGY_ID, to_stage="live", reason="test")
    assert not proposal.admissible
    assert any(b.reason_code == "invalid_demotion_target" for b in proposal.blockers)


def test_propose_demote_rejects_noop_same_stage(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_demote(
        STRATEGY_ID, to_stage="backtest", reason="test"
    )
    assert not proposal.admissible
    assert any(b.reason_code == "demotion_is_noop" for b in proposal.blockers)


def test_propose_demote_to_disabled_refused_when_gui_submit_true(
    make_facade, config_dir: Path
) -> None:
    """Phase C2 review F2: the Bench GUI submit surface must refuse
    ``to_stage='disabled'`` with a structured ``disabled_demote_not_gui_ready``
    blocker until runtime refusal of disabled strategies lands
    (``promotion.state_machine`` slice 3). The bridge passes
    ``gui_submit=True``; CLI defaults to False and is unaffected.
    """
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_demote(
        STRATEGY_ID,
        to_stage="disabled",
        reason="Strategy retired pending replacement.",
        gui_submit=True,
    )
    assert not proposal.admissible
    codes = {b.reason_code for b in proposal.blockers}
    assert "disabled_demote_not_gui_ready" in codes
    # Backtest target stays admissible under the same flag.
    backtest_proposal = facade.propose_demote(
        STRATEGY_ID,
        to_stage="backtest",
        reason="Walk back.",
        gui_submit=True,
    )
    assert backtest_proposal.admissible, backtest_proposal.blockers


def test_propose_demote_to_disabled_admissible_when_gui_submit_false(
    make_facade, config_dir: Path
) -> None:
    """Phase C2 review F2: the CLI default path (``gui_submit=False``) must
    keep admitting ``to_stage='disabled'`` for ledger-only demotion. This
    pins the regression that the existing
    ``test_submit_demote_to_disabled_is_ledger_only`` test exercises end to
    end."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_demote(
        STRATEGY_ID,
        to_stage="disabled",
        reason="Strategy retired pending replacement.",
        # gui_submit defaults to False — explicit here for documentation.
        gui_submit=False,
    )
    assert proposal.admissible, proposal.blockers
    codes = {b.reason_code for b in proposal.blockers}
    assert "disabled_demote_not_gui_ready" not in codes


# --------------------------------------------------------------------------- #
# Start / stop paper runner proposals
# --------------------------------------------------------------------------- #


def test_propose_start_paper_runner_admissible_for_paper_stage(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)
    assert proposal.admissible, proposal.blockers
    assert proposal.projected_outcome["trading_mode"] == "paper"


def test_propose_start_paper_runner_blocks_backtest_stage(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)
    assert not proposal.admissible
    codes = {b.reason_code for b in proposal.blockers}
    assert "stage_incompatible_with_mode" in codes


def test_propose_start_paper_runner_blocks_non_paper_trading_mode(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade(trading_mode="live")
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)
    assert not proposal.admissible
    codes = {b.reason_code for b in proposal.blockers}
    assert "trading_mode_not_paper" in codes


def test_propose_start_paper_runner_blocks_when_advisory_lock_held(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    _seed_runner_lock(locks_dir)
    facade = make_facade()
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)
    assert not proposal.admissible
    codes = {b.reason_code for b in proposal.blockers}
    assert "advisory_lock_held" in codes
    holder_blocker = next(
        b for b in proposal.blockers if b.reason_code == "advisory_lock_held"
    )
    assert holder_blocker.context["holder"]["pid"] == 1


def test_propose_stop_paper_runner_blocks_when_no_runner(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_stop_paper_runner(STRATEGY_ID)
    assert not proposal.admissible
    assert proposal.blockers[0].reason_code == "no_active_runner"


def test_propose_stop_paper_runner_admissible_with_active_runner(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    _seed_runner_lock(locks_dir)
    facade = make_facade()
    proposal = facade.propose_stop_paper_runner(STRATEGY_ID)
    assert proposal.admissible, proposal.blockers
    assert proposal.projected_outcome["exit_reason"] == "controlled_stop"
    assert proposal.projected_outcome["kill_switch"] is False


# --------------------------------------------------------------------------- #
# Micro-live / live remain absent
# --------------------------------------------------------------------------- #


def test_facade_exposes_no_micro_live_or_live_route() -> None:
    """ADR 0051 §6, §7: no GUI path to micro_live or live at launch."""
    methods = {
        name for name, _ in inspect.getmembers(BenchCommandFacade, predicate=callable)
    }
    for forbidden in (
        "propose_promote_to_micro_live",
        "submit_promote_to_micro_live",
        "propose_promote_to_live",
        "submit_promote_to_live",
    ):
        assert forbidden not in methods, (
            f"BenchCommandFacade must not expose {forbidden} at launch (ADR 0051)."
        )


def test_propose_demote_refuses_micro_live_and_live_targets(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    for bad in ("micro_live", "live"):
        proposal = facade.propose_demote(STRATEGY_ID, to_stage=bad, reason="trying")
        assert not proposal.admissible
        assert any(
            b.reason_code == "invalid_demotion_target" for b in proposal.blockers
        )


# --------------------------------------------------------------------------- #
# Submit methods: Phase B is uniformly blocked
# --------------------------------------------------------------------------- #


# Every submit method except submit_demote is still a Phase B stub. Phase C1
# wires submit_demote first; the rest land in Phases C2 / D / E / F.
@pytest.mark.parametrize(
    "method_name,family",
    [
        ("submit_backtest", ACTION_FAMILY_BACKTEST),
        ("submit_freeze_manifest", ACTION_FAMILY_FREEZE_MANIFEST),
        ("submit_promote_to_paper", ACTION_FAMILY_PROMOTE_TO_PAPER),
        ("submit_start_paper_runner", ACTION_FAMILY_START_PAPER_RUNNER),
        ("submit_stop_paper_runner", ACTION_FAMILY_STOP_PAPER_RUNNER),
    ],
)
def test_submit_returns_phase_b_blocker(
    method_name: str, family: str, make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    dummy = CommandProposal(
        action_family=family,
        strategy_id=STRATEGY_ID,
        inputs={},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="dummy-proposal",
    )
    result: CommandResult = getattr(facade, method_name)(dummy)
    assert result.status == "blocked"
    assert result.action_family == family
    assert result.proposal_id == "dummy-proposal"
    assert any(b.reason_code == "not_submit_capable_phase_b" for b in result.blockers)
    assert result.submitted_at is None
    assert result.audit_event_id is None


# --------------------------------------------------------------------------- #
# Phase C1 — submit_demote
# --------------------------------------------------------------------------- #


def _make_demote_proposal(
    *,
    to_stage: str,
    reason: str | None,
    approved_by: str = "operator",
    evidence_ref: str | None = None,
) -> CommandProposal:
    """Construct a proposal the way QML/the bridge eventually will: by
    serializing the inputs the user passed into propose_demote. submit_demote
    re-validates these via propose_demote before dispatching."""
    return CommandProposal(
        action_family=ACTION_FAMILY_DEMOTE,
        strategy_id=STRATEGY_ID,
        inputs={
            "to_stage": to_stage,
            "reason": reason,
            "approved_by": approved_by,
            "evidence_ref": evidence_ref,
        },
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="phase-c1-test-proposal",
    )


def test_submit_demote_to_backtest_updates_yaml_and_returns_durable_refs(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = _make_demote_proposal(
        to_stage="backtest",
        reason="OOS evidence degraded; walk back for re-tuning.",
    )

    result = facade.submit_demote(proposal)

    assert result.status == "submitted", result.blockers
    assert result.action_family == ACTION_FAMILY_DEMOTE
    assert result.proposal_id == "phase-c1-test-proposal"
    assert result.audit_event_id is not None
    assert result.submitted_at is not None
    assert result.durable_refs["from_stage"] == "paper"
    assert result.durable_refs["to_stage"] == "backtest"
    assert result.durable_refs["promotion_type"] == "demotion"
    assert result.durable_refs["strategy_id"] == STRATEGY_ID
    assert "promotion_id" in result.durable_refs

    # YAML stage line was rewritten to 'backtest' by the governance path.
    assert 'stage: "backtest"' in config_path.read_text(encoding="utf-8")

    # Append-only governance event is reachable via the canonical query.
    events = event_store.list_promotions_for_strategy(STRATEGY_ID)
    assert len(events) == 1
    assert events[0].promotion_type == "demotion"
    assert events[0].to_stage == "backtest"


def test_submit_demote_to_disabled_is_ledger_only(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = _make_demote_proposal(
        to_stage="disabled",
        reason="Strategy retired pending replacement.",
    )

    result = facade.submit_demote(proposal)

    assert result.status == "submitted", result.blockers
    assert result.durable_refs["to_stage"] == "disabled"
    # YAML stage MUST remain 'paper' — disabled is ledger-only per the
    # governance contract (state_machine.demote slice 2 / state_machine.py).
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")
    # The facade must warn the operator about ledger-only semantics so the
    # confirmation-modal copy and the audit reader stay aligned.
    assert any("ledger-only" in w for w in result.warnings)

    events = event_store.list_promotions_for_strategy(STRATEGY_ID)
    assert len(events) == 1
    assert events[0].to_stage == "disabled"


def test_submit_demote_records_reverses_event_id_when_prior_promotion_exists(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """If a prior non-demotion promotion exists, the demotion's
    reverses_event_id must point at it. The facade reflects this back to the
    operator via durable_refs so audit readers can walk the reversal chain
    without re-querying the event store."""
    from datetime import UTC
    from datetime import datetime as _dt

    from milodex.core.event_store import PromotionEvent

    _write_strategy(config_dir, stage="paper")
    # Seed a real prior promotion event (backtest -> paper).
    prior_id = event_store.append_promotion(
        PromotionEvent(
            strategy_id=STRATEGY_ID,
            from_stage="backtest",
            to_stage="paper",
            promotion_type="statistical",
            approved_by="operator",
            recorded_at=_dt.now(tz=UTC),
            notes="seed",
            reverses_event_id=None,
        )
    )

    facade = make_facade()
    result = facade.submit_demote(
        _make_demote_proposal(to_stage="backtest", reason="walk back")
    )

    assert result.status == "submitted", result.blockers
    assert result.durable_refs.get("reverses_event_id") == str(prior_id)


def test_submit_demote_blank_reason_refused_and_no_state_change(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="paper")
    yaml_before = config_path.read_text(encoding="utf-8")
    facade = make_facade()

    result = facade.submit_demote(
        _make_demote_proposal(to_stage="backtest", reason="   ")
    )

    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert "missing_reason" in codes
    # No mutation: YAML untouched, no governance event.
    assert config_path.read_text(encoding="utf-8") == yaml_before
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


@pytest.mark.parametrize("bad_target", ["live", "micro_live", "paper", "nonsense"])
def test_submit_demote_invalid_target_refused(
    bad_target: str,
    make_facade,
    config_dir: Path,
    event_store: EventStore,
) -> None:
    config_path = _write_strategy(config_dir, stage="paper")
    yaml_before = config_path.read_text(encoding="utf-8")
    facade = make_facade()

    result = facade.submit_demote(
        _make_demote_proposal(to_stage=bad_target, reason="trying")
    )

    assert result.status == "blocked"
    # Either invalid_demotion_target (live/micro_live/nonsense) or
    # demotion_is_noop (paper at paper stage) — both are rejections that
    # mean "the facade refused before dispatch."
    codes = {b.reason_code for b in result.blockers}
    assert codes.intersection({"invalid_demotion_target", "demotion_is_noop"}), codes

    assert config_path.read_text(encoding="utf-8") == yaml_before
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_demote_rejects_proposal_for_different_action_family(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    wrong = CommandProposal(
        action_family=ACTION_FAMILY_BACKTEST,
        strategy_id=STRATEGY_ID,
        inputs={"to_stage": "backtest", "reason": "x"},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="wrong-family",
    )
    result = facade.submit_demote(wrong)
    assert result.status == "error"
    assert any(
        b.reason_code == "proposal_action_family_mismatch" for b in result.blockers
    )
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_demote_revalidates_stale_proposal(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """A proposal admissible at propose-time goes stale when the underlying
    stage moves. submit must re-validate and refuse."""
    config_path = _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    # Propose against the live config (paper stage); this is admissible.
    proposal = facade.propose_demote(
        STRATEGY_ID, to_stage="backtest", reason="real reason"
    )
    assert proposal.admissible

    # Simulate drift: someone (operator, another process) already walked the
    # strategy back to backtest. The proposal is now stale.
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'stage: "paper"', 'stage: "backtest"'
        ),
        encoding="utf-8",
    )

    result = facade.submit_demote(proposal)
    assert result.status == "blocked"
    # The drift surfaces as 'demotion_is_noop' (target==current_stage).
    assert any(b.reason_code == "demotion_is_noop" for b in result.blockers)
    # Still no real demotion event recorded.
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_demote_refuses_when_facade_lacks_event_store(
    config_dir: Path, locks_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=None,
    )
    result = facade.submit_demote(
        _make_demote_proposal(to_stage="backtest", reason="x")
    )
    assert result.status == "error"
    assert any(b.reason_code == "event_store_unavailable" for b in result.blockers)


# --------------------------------------------------------------------------- #
# Module-level invariants
# --------------------------------------------------------------------------- #


_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z0-9_.]+)", re.MULTILINE)


def _imported_modules(path: Path) -> set[str]:
    """Return the top-level module names this Python file actually imports.

    Substring-grepping for "PySide6" or "broker" picks up docstring text
    and comments — that's why we parse the import lines instead.
    """
    source = path.read_text(encoding="utf-8")
    return {match.group(1).split(".")[0] for match in _IMPORT_RE.finditer(source)}


def test_facade_module_does_not_import_pyside6() -> None:
    """ADR 0051 §4: the facade lives outside src/milodex/gui/ and must not
    import PySide6. The bridge module (Phase C+) is the only allowed caller
    from the GUI side."""
    modules = _imported_modules(Path(facade_module.__file__))
    assert "PySide6" not in modules, (
        "milodex.commands.bench must not import PySide6 (ADR 0051 §4 / §5). "
        f"Imports found: {sorted(modules)}"
    )
    # Importing the facade must not pull PySide6 in as a side effect.
    pyside_before = "PySide6" in sys.modules
    importlib.reload(facade_module)
    pyside_after = "PySide6" in sys.modules
    assert pyside_before == pyside_after, (
        "Reloading milodex.commands.bench changed sys.modules['PySide6'] — the "
        "facade is leaking a Qt dependency."
    )


def test_facade_module_does_not_import_broker_runner_or_execution_writes() -> None:
    """Forbidden dependencies per ADR 0051 §4 / §5."""
    source = Path(facade_module.__file__).read_text(encoding="utf-8")
    forbidden_import_lines = (
        "from milodex.broker",
        "import milodex.broker",
        "from milodex.strategies.runner",
        "import milodex.strategies.runner",
        "from milodex.execution",
        "import milodex.execution",
    )
    for forbidden in forbidden_import_lines:
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert not stripped.startswith(forbidden), (
                f"milodex.commands.bench must not import {forbidden!r} in Phase B "
                f"(ADR 0051 §4 / §5). Offending line: {line!r}"
            )


def test_gui_qml_files_still_forbid_submit_broker_eventstore() -> None:
    """ADR 0049 perimeter survives Phase B.

    Phase B introduces no QML changes, so the existing forbidden-token
    contract on Bench QML must still hold.
    """
    qml_dir = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "milodex"
        / "gui"
        / "qml"
        / "Milodex"
    )
    forbidden_tokens = (
        "BenchState.promote",
        "BenchState.demote",
        "BenchState.start",
        "BenchState.stop",
        "BenchState.backtest",
        "BenchState.return",
        "broker.",
        "eventStore.",
        "eventstore.",
        "executeOrder",
        "config.write",
        "submitCommand",
        "dispatchCommand",
    )
    for qml_path in qml_dir.rglob("*.qml"):
        src = qml_path.read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in src, (
                f"{qml_path.name} contains forbidden token {token!r} "
                "(ADR 0049 perimeter; Phase B may not weaken this)."
            )
