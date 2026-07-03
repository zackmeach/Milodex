"""Tests for the Bench command facade in ``milodex.commands.bench``.

These tests pin the ADR 0051 proposal/validation/submit contract. They cover:

- shape of ``Blocker``, ``Precondition``, ``CommandProposal``, ``CommandResult``
- admissibility on the happy path for each action family
- structured-blocker shape on the unhappy paths the GUI must surface
- micro-live / live remain absent from the facade surface (no methods, no
  routes, no accepted ``to_stage``)
- the module does not import PySide6, broker, runner, or execution-write paths
- the GUI's existing forbidden-token contract (ADR 0049 / BENCH_BOUNDARY) is
  not weakened by Bench command wiring
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import re
import sys
import textwrap
from dataclasses import FrozenInstanceError, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from milodex.backtesting.engine import BacktestResult
from milodex.backtesting.walk_forward_runner import (
    WalkForwardResult,
    WalkForwardStability,
)
from milodex.commands import bench as facade_module
from milodex.commands.bench import (
    ACTION_FAMILIES,
    ACTION_FAMILY_BACKTEST,
    ACTION_FAMILY_DEMOTE,
    ACTION_FAMILY_FREEZE_MANIFEST,
    ACTION_FAMILY_PROMOTE_TO_PAPER,
    ACTION_FAMILY_START_PAPER_RUNNER,
    ACTION_FAMILY_STOP_PAPER_RUNNER,
    READINESS_KILL_SWITCH,
    BenchCommandFacade,
    Blocker,
    CommandProposal,
    CommandResult,
    Precondition,
    WorkflowReadinessIssue,
    WorkflowReadinessReport,
)
from milodex.core.event_store import (
    BacktestRunEvent,
    EventStore,
    ExplanationEvent,
    ReconciliationRunEvent,
    StrategyRunEvent,
)
from milodex.data.models import BarSet
from milodex.risk.policy import RiskPolicy
from milodex.strategies.paper_runner_control import (
    ControlledStopRequestResult,
    PaperRunnerStartResult,
)

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
      description: "Bench facade test strategy."
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


def _write_strategy(config_dir: Path, *, stage: str, min_trades_required: int = 30) -> Path:
    path = config_dir / "strategy.yaml"
    content = _STRATEGY_YAML_TEMPLATE.format(stage=stage).replace(
        "min_trades_required: 30",
        f"min_trades_required: {min_trades_required}",
    )
    path.write_text(content, encoding="utf-8")
    return path


# The policy-listed lifecycle-proof id (ADR 0058). The lifecycle-exempt bypass
# is scoped to this id; the sample STRATEGY_ID above is NOT lifecycle-proof, so
# the two no-backtest-run happy-path submit tests use this regime config.
_REGIME_STRATEGY_ID = "regime.daily.sma200_rotation.spy_shy.v1"

_REGIME_YAML_TEMPLATE = textwrap.dedent(
    """\
    strategy:
      id: "regime.daily.sma200_rotation.spy_shy.v1"
      family: "regime"
      template: "daily.sma200_rotation"
      variant: "spy_shy"
      version: 1
      description: "Lifecycle-proof regime strategy (bench facade test)."
      enabled: true
      universe: ["SPY", "SHY"]
      parameters:
        ma_filter_length: 200
      tempo:
        bar_size: "1D"
        min_hold_days: 1
        max_hold_days: 5
      risk:
        max_position_pct: 0.10
        max_positions: 1
        daily_loss_cap_pct: 0.02
        stop_loss_pct: null
      stage: "{stage}"
      backtest:
        commission_per_trade: 0.00
        min_trades_required: 30
      disable_conditions_additional: []
    """
)


def _write_regime_strategy(config_dir: Path, *, stage: str) -> Path:
    path = config_dir / "regime_strategy.yaml"
    path.write_text(_REGIME_YAML_TEMPLATE.format(stage=stage), encoding="utf-8")
    return path


def _make_regime_promote_proposal(
    *,
    recommendation: str | None = "Regime strategy; lifecycle-exempt per R-PRM-004.",
    known_risks: list[str] | None = None,
    approved_by: str = "operator",
) -> CommandProposal:
    """Lifecycle-exempt promote proposal for the policy-listed regime id
    (ADR 0058) — the honest lifecycle-exempt happy path (no backtest run)."""
    risks = ["whipsaw"] if known_risks is None else list(known_risks)
    return CommandProposal(
        action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
        strategy_id=_REGIME_STRATEGY_ID,
        inputs={
            "to_stage": "paper",
            "recommendation": recommendation,
            "known_risks": risks,
            "run_id": None,
            "approved_by": approved_by,
            "lifecycle_exempt": True,
        },
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="phase-d2-regime-proposal",
    )


def _seed_runner_lock(locks_dir: Path) -> Path:
    """Write a holder file mimicking a *live* active runner.

    Records this test process's own PID with a ``started_at`` of "now" so the
    shared identity-verified liveness helper (``advisory_lock.holder_is_live``,
    which ``_peek_runner_lock`` now routes through) classifies it as genuinely
    live: the process exists and its start time precedes the lock. A fixed
    arbitrary PID (the old ``pid=1``) is no longer sufficient — identity-
    verified liveness rejects a PID it cannot confirm is the original holder.
    """
    holder_path = locks_dir / f"milodex.runtime.strategy.{STRATEGY_ID}.lock"
    holder_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": "test-host",
                "holder_name": f"milodex strategy run {STRATEGY_ID}",
                "started_at": datetime.now(tz=UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return holder_path


def _seed_dead_runner_lock(locks_dir: Path) -> Path:
    """Write a stale holder file whose recorded PID is not a live process.

    ``pid=0`` short-circuits ``_process_exists`` to False, so identity-verified
    liveness classifies it dead — the hard-killed-runner-left-a-stale-lock
    signature. Routing ``_peek_runner_lock`` through ``holder_is_live`` must
    report this as no active runner, not a phantom live/stoppable one.
    """
    holder_path = locks_dir / f"milodex.runtime.strategy.{STRATEGY_ID}.lock"
    holder_path.write_text(
        json.dumps(
            {
                "pid": 0,
                "hostname": "ghost",
                "holder_name": f"milodex strategy run {STRATEGY_ID}",
                "started_at": "2026-05-14T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return holder_path


class _FakePaperRunnerControl:
    def __init__(self, locks_dir: Path) -> None:
        self.starts: list[str] = []
        self.stops: list[tuple[str, dict]] = []
        self.locks_dir = locks_dir

    def start(self, strategy_id: str):
        self.starts.append(strategy_id)
        return PaperRunnerStartResult(
            strategy_id=strategy_id,
            pid=4242,
            command=("python", "-m", "milodex.cli.main", "strategy", "run", strategy_id),
            stop_request_path=self.locks_dir / f"{strategy_id}.controlled_stop.json",
            launched_at=datetime(2026, 5, 15, 12, 0, 0),
        )

    def request_controlled_stop(self, strategy_id: str, *, holder: dict):
        self.stops.append((strategy_id, holder))
        return ControlledStopRequestResult(
            strategy_id=strategy_id,
            request_path=self.locks_dir / f"{strategy_id}.controlled_stop.json",
            requested_at=datetime(2026, 5, 15, 12, 1, 0),
            holder=holder,
        )


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


def _append_open_strategy_run(event_store: EventStore, *, session_id: str) -> None:
    event_store.append_strategy_run(
        StrategyRunEvent(
            session_id=session_id,
            strategy_id=STRATEGY_ID,
            started_at=datetime(2026, 5, 15, 11, 0, 0),
            ended_at=None,
            exit_reason=None,
            metadata={},
        )
    )


class _FakeWorkflowReadiness:
    def __init__(self, *reports: WorkflowReadinessReport) -> None:
        self._reports = list(reports) or [WorkflowReadinessReport()]
        self.calls: list[dict[str, object]] = []

    def evaluate(
        self,
        *,
        action_family: str,
        strategy_id: str,
        required_checks: frozenset[str],
        inspected_checks: frozenset[str],
    ) -> WorkflowReadinessReport:
        self.calls.append(
            {
                "action_family": action_family,
                "strategy_id": strategy_id,
                "required_checks": required_checks,
                "inspected_checks": inspected_checks,
            }
        )
        if len(self._reports) > 1:
            return self._reports.pop(0)
        return self._reports[0]


def _healthy_readiness() -> _FakeWorkflowReadiness:
    return _FakeWorkflowReadiness(WorkflowReadinessReport())


def _readiness_issue(reason_code: str, *, blocking: bool = True) -> WorkflowReadinessIssue:
    dimension_by_code = {
        "reconciliation_drift": "reconciliation",
        "reconciliation_required": "reconciliation",
        "reconciliation_stale": "reconciliation",
        "reconciliation_incomplete": "reconciliation",
        "kill_switch_open": "kill_switch",
        "data_stale": "data_freshness",
        "broker_unreachable": "broker_reachability",
    }
    return WorkflowReadinessIssue(
        dimension=dimension_by_code[reason_code],
        reason_code=reason_code,
        message=f"{reason_code} test issue",
        context={"source": "test"},
        blocking=blocking,
    )


@pytest.fixture
def make_facade(config_dir: Path, locks_dir: Path, event_store: EventStore):
    def _make(
        *,
        trading_mode: str = "paper",
        with_event_store: bool = True,
        backtest_engine_factory=None,
        paper_runner_control=None,
        workflow_readiness=None,
        now=None,
        sleep=None,
    ) -> BenchCommandFacade:
        return BenchCommandFacade(
            config_dir=config_dir,
            locks_dir=locks_dir,
            get_trading_mode=lambda: trading_mode,
            event_store_factory=(lambda: event_store) if with_event_store else None,
            backtest_engine_factory=backtest_engine_factory,
            paper_runner_control=paper_runner_control,
            workflow_readiness=workflow_readiness or _healthy_readiness(),
            now=now,
            sleep=sleep,
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
    assert d["data"] == {}
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


def test_propose_backtest_admissible_for_known_strategy(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_backtest(STRATEGY_ID, start=date(2025, 1, 1), end=date(2025, 6, 30))
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
    assert "walk_forward_runner.run_walk_forward" in proposal.projected_outcome["eventual_callee"]


def test_propose_backtest_blocks_when_dates_inverted(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_backtest(STRATEGY_ID, start=date(2025, 6, 30), end=date(2025, 1, 1))
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


def test_propose_freeze_manifest_admissible_for_paper_stage(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_freeze_manifest(STRATEGY_ID)
    assert proposal.admissible, proposal.blockers
    assert proposal.projected_outcome["eventual_callee"] == (
        "milodex.promotion.manifest.freeze_manifest"
    )


def test_propose_freeze_manifest_blocks_backtest_stage(make_facade, config_dir: Path) -> None:
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


def test_propose_promote_to_paper_requires_evidence_inputs(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="backtest", min_trades_required=20)
    facade = make_facade()
    proposal = facade.propose_promote_to_paper(STRATEGY_ID)
    assert not proposal.admissible
    codes = {b.reason_code for b in proposal.blockers}
    assert "missing_recommendation" in codes
    assert "missing_known_risks" in codes
    assert "missing_run_id" in codes
    run_id_blocker = next(b for b in proposal.blockers if b.reason_code == "missing_run_id")
    assert "Sharpe > 0.0" in run_id_blocker.message
    assert "max drawdown < 25.0%" in run_id_blocker.message
    assert "trades >= 20" in run_id_blocker.message
    assert "Sharpe > 0.5" not in run_id_blocker.message
    assert "max drawdown < 15.0%" not in run_id_blocker.message
    assert run_id_blocker.context == {
        "min_sharpe": 0.0,
        "max_drawdown_pct": 25.0,
        "min_trades": 20,
    }


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


def test_propose_promote_to_paper_blocks_wrong_source_stage(make_facade, config_dir: Path) -> None:
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


@pytest.mark.parametrize(
    "reason_code",
    [
        "reconciliation_drift",
        "kill_switch_open",
        "data_stale",
        "broker_unreachable",
    ],
)
def test_propose_promote_to_paper_blocks_required_workflow_readiness(
    make_facade, config_dir: Path, reason_code: str
) -> None:
    _write_strategy(config_dir, stage="backtest")
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(issues=(_readiness_issue(reason_code),))
    )
    facade = make_facade(workflow_readiness=readiness)

    proposal = facade.propose_promote_to_paper(
        STRATEGY_ID,
        recommendation="Paper slot is justified.",
        known_risks=["Paper-only operational risk."],
        lifecycle_exempt=True,
    )

    assert reason_code in {b.reason_code for b in proposal.blockers}
    first_issue = proposal.projected_outcome["workflow_readiness"]["issues"][0]
    assert first_issue["reason_code"] == reason_code
    assert readiness.calls[0]["required_checks"] == frozenset(
        {"reconciliation", "kill_switch", "data_freshness", "broker_reachability"}
    )


def test_default_workflow_readiness_reads_durable_reconciliation(event_store: EventStore) -> None:
    readiness = facade_module._DefaultWorkflowReadiness(lambda: event_store)

    missing = readiness.evaluate(
        action_family=ACTION_FAMILY_START_PAPER_RUNNER,
        strategy_id=STRATEGY_ID,
        required_checks=frozenset({"reconciliation"}),
        inspected_checks=frozenset(),
    )
    assert [issue.reason_code for issue in missing.issues] == ["reconciliation_required"]

    event_store.append_reconciliation_run(
        ReconciliationRunEvent(
            run_id="bench-clean",
            recorded_at=datetime.now(tz=UTC),
            as_of=datetime.now(tz=UTC),
            local_trading_day=datetime.now(tz=UTC)
            .astimezone(ZoneInfo("America/New_York"))
            .date()
            .isoformat(),
            status="clean",
            broker_connected=True,
            market_open=True,
            checked_dimensions_version="R-OPS-004.v1.1",
            checked_dimensions=["positions"],
            deferred_checks=[],
            incident_hash=None,
            incident_recorded=False,
            incident_deduplicated=False,
            reason_codes=[],
            summary={},
        )
    )
    clean = readiness.evaluate(
        action_family=ACTION_FAMILY_START_PAPER_RUNNER,
        strategy_id=STRATEGY_ID,
        required_checks=frozenset({"broker_reachability", "reconciliation"}),
        inspected_checks=frozenset(),
    )
    assert clean.issues == ()


# --------------------------------------------------------------------------- #
# _DefaultWorkflowReadiness — data_freshness dimension (HR-9)
# --------------------------------------------------------------------------- #

_EXPLANATION_DEFAULTS = {
    "decision_type": "preview",
    "status": "preview",
    "strategy_name": "test_strategy",
    "strategy_stage": "paper",
    "strategy_config_path": "configs/test.yaml",
    "config_hash": None,
    "symbol": "SPY",
    "side": "buy",
    "quantity": 1.0,
    "order_type": "market",
    "time_in_force": "day",
    "submitted_by": "strategy_runner",
    "market_open": True,
    "latest_bar_close": 450.0,
    "account_equity": 10_000.0,
    "account_cash": 9_000.0,
    "account_portfolio_value": 10_000.0,
    "account_daily_pnl": 0.0,
    "risk_allowed": True,
    "risk_summary": "Allowed",
    "reason_codes": [],
    "risk_checks": [],
    "context": {},
    "session_id": "sess-freshness-test",
}


def _append_fresh_explanation(event_store: EventStore) -> None:
    """Append an explanation row with a bar timestamp of *now* (fresh data)."""
    event_store.append_explanation(
        ExplanationEvent(
            recorded_at=datetime.now(tz=UTC),
            latest_bar_timestamp=datetime.now(tz=UTC),
            **_EXPLANATION_DEFAULTS,
        )
    )


def _append_stale_explanation(event_store: EventStore) -> None:
    """Append an explanation row with a bar timestamp > 24 h ago (stale data)."""
    from datetime import timedelta

    event_store.append_explanation(
        ExplanationEvent(
            recorded_at=datetime.now(tz=UTC),
            latest_bar_timestamp=datetime.now(tz=UTC) - timedelta(hours=48),
            **_EXPLANATION_DEFAULTS,
        )
    )


def _append_backtest_explanation(event_store: EventStore) -> None:
    """Append a backtest-engine explanation with an ANCIENT bar timestamp.

    Mirrors the simulation kernel's rows: ``backtest_run_id`` ancestor,
    historical ``latest_bar_timestamp``. Such rows land with higher ids than
    live rows whenever a backtest runs after the fleet's last evaluation —
    they must never drive the freshness signal (same contamination family
    as R-P0-1).
    """
    from datetime import timedelta

    from milodex.core.event_store import BacktestRunEvent

    now = datetime.now(tz=UTC)
    run_row_id = event_store.append_backtest_run(
        BacktestRunEvent(
            run_id="bt-freshness-test",
            strategy_id="test.strategy.v1",
            config_path=None,
            config_hash=None,
            start_date=now,
            end_date=now,
            started_at=now,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={},
        )
    )
    overrides = dict(_EXPLANATION_DEFAULTS)
    overrides["submitted_by"] = "backtest_engine"
    event_store.append_explanation(
        ExplanationEvent(
            recorded_at=now,
            latest_bar_timestamp=now - timedelta(days=365),
            backtest_run_id=run_row_id,
            **overrides,
        )
    )


def test_default_workflow_readiness_data_freshness_ignores_backtest_rows(
    event_store: EventStore,
) -> None:
    # Review F-1: a backtest run AFTER the last live evaluation writes
    # explanations with historical bar timestamps at higher row ids. The
    # freshness signal must come from live rows only — otherwise running a
    # backtest before a promote false-blocks the GUI gate with a years-old
    # "latest" bar.
    _append_fresh_explanation(event_store)
    _append_backtest_explanation(event_store)
    readiness = facade_module._DefaultWorkflowReadiness(lambda: event_store)

    report = readiness.evaluate(
        action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
        strategy_id=STRATEGY_ID,
        required_checks=frozenset({facade_module.READINESS_DATA_FRESHNESS}),
        inspected_checks=frozenset(),
    )

    assert report.issues == (), f"Unexpected issues: {report.issues}"


def test_default_workflow_readiness_data_freshness_fresh_data_no_issue(
    event_store: EventStore,
) -> None:
    # Fresh bar → no data_stale issue; promote proposal becomes admissible
    # (this is the "always blocked" behavior HR-9 replaces).
    _append_fresh_explanation(event_store)
    readiness = facade_module._DefaultWorkflowReadiness(lambda: event_store)

    report = readiness.evaluate(
        action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
        strategy_id=STRATEGY_ID,
        required_checks=frozenset({facade_module.READINESS_DATA_FRESHNESS}),
        inspected_checks=frozenset(),
    )

    assert report.issues == (), f"Unexpected issues: {report.issues}"


def test_default_workflow_readiness_data_freshness_stale_data_blocks(
    event_store: EventStore,
) -> None:
    # Stale bar (> 24 h old) → blocking data_stale issue.
    _append_stale_explanation(event_store)
    readiness = facade_module._DefaultWorkflowReadiness(lambda: event_store)

    report = readiness.evaluate(
        action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
        strategy_id=STRATEGY_ID,
        required_checks=frozenset({facade_module.READINESS_DATA_FRESHNESS}),
        inspected_checks=frozenset(),
    )

    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.dimension == facade_module.READINESS_DATA_FRESHNESS
    assert issue.reason_code == "data_stale"
    assert issue.blocking is True
    assert "age_hours" in issue.context
    assert issue.context["threshold_hours"] == facade_module._DATA_FRESHNESS_STALE_HOURS


def test_default_workflow_readiness_data_freshness_empty_store_blocks(
    event_store: EventStore,
) -> None:
    # Empty store (no bar timestamps) → fail closed: blocking data_stale issue.
    readiness = facade_module._DefaultWorkflowReadiness(lambda: event_store)

    report = readiness.evaluate(
        action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
        strategy_id=STRATEGY_ID,
        required_checks=frozenset({facade_module.READINESS_DATA_FRESHNESS}),
        inspected_checks=frozenset(),
    )

    assert len(report.issues) == 1
    issue = report.issues[0]
    assert issue.reason_code == "data_stale"
    assert issue.blocking is True


def test_default_workflow_readiness_data_freshness_no_store_blocks() -> None:
    # No factory configured → fail closed: unreadable store blocks.
    readiness = facade_module._DefaultWorkflowReadiness(event_store_factory=None)

    report = readiness.evaluate(
        action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
        strategy_id=STRATEGY_ID,
        required_checks=frozenset({facade_module.READINESS_DATA_FRESHNESS}),
        inspected_checks=frozenset(),
    )

    assert len(report.issues) == 1
    assert report.issues[0].reason_code == "data_stale"
    assert report.issues[0].blocking is True


def test_default_workflow_readiness_single_event_store_per_evaluate(
    event_store: EventStore,
) -> None:
    # G-P3-5: one EventStore construction per evaluate() call, regardless of how
    # many dimensions are checked. The factory records each call; we assert exactly
    # one call for an evaluate over all four dimensions.
    calls: list[EventStore] = []

    def _factory() -> EventStore:
        calls.append(event_store)
        return event_store

    readiness = facade_module._DefaultWorkflowReadiness(_factory)
    _append_fresh_explanation(event_store)

    readiness.evaluate(
        action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
        strategy_id=STRATEGY_ID,
        required_checks=frozenset(
            {
                facade_module.READINESS_DATA_FRESHNESS,
                facade_module.READINESS_KILL_SWITCH,
                facade_module.READINESS_RECONCILIATION,
                facade_module.READINESS_BROKER_REACHABILITY,
            }
        ),
        inspected_checks=frozenset(),
    )

    assert len(calls) == 1, (
        f"Expected 1 EventStore construction for all four dimensions, got {len(calls)}"
    )


def test_propose_promote_to_paper_admissible_when_data_is_fresh(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    # End-to-end: fresh bar in the event store → promote proposal admissible
    # (data_freshness dimension satisfied; no data_stale blocker).
    _write_strategy(config_dir, stage="backtest")
    _append_fresh_explanation(event_store)
    # Use the real _DefaultWorkflowReadiness wired through the facade.
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=config_dir.parent / "locks",
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
    )
    (config_dir.parent / "locks").mkdir(exist_ok=True)

    proposal = facade.propose_promote_to_paper(
        STRATEGY_ID,
        recommendation="Fresh data confirmed.",
        known_risks=["Paper risk."],
        lifecycle_exempt=True,
    )

    # data_stale must NOT be a blocker.
    data_stale_blockers = [b for b in proposal.blockers if b.reason_code == "data_stale"]
    assert data_stale_blockers == [], (
        f"Unexpected data_stale blocker with fresh data: {data_stale_blockers}"
    )


# --------------------------------------------------------------------------- #
# Demote proposals
# --------------------------------------------------------------------------- #


def test_propose_demote_admissible_for_paper_to_backtest(make_facade, config_dir: Path) -> None:
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


def test_propose_demote_admissible_for_backtest_to_idle(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_demote(
        STRATEGY_ID,
        to_stage="idle",
        reason="Shelf while evidence is rebuilt.",
        gui_submit=True,
    )
    assert proposal.admissible, proposal.blockers
    assert proposal.projected_outcome["from_stage"] == "backtest"
    assert proposal.projected_outcome["to_stage"] == "idle"
    assert proposal.projected_outcome["yaml_updated"] is True


def test_propose_demote_requires_reason(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_demote(STRATEGY_ID, to_stage="backtest")
    assert not proposal.admissible
    assert any(b.reason_code == "missing_reason" for b in proposal.blockers)


def test_propose_demote_rejects_invalid_target(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_demote(STRATEGY_ID, to_stage="live", reason="test")
    assert not proposal.admissible
    assert any(b.reason_code == "invalid_demotion_target" for b in proposal.blockers)


def test_propose_demote_rejects_noop_same_stage(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_demote(STRATEGY_ID, to_stage="backtest", reason="test")
    assert not proposal.admissible
    assert any(b.reason_code == "demotion_is_noop" for b in proposal.blockers)


def test_propose_demote_with_active_runner_blocks_required_readiness(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    _seed_runner_lock(locks_dir)
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(issues=(_readiness_issue("reconciliation_drift"),))
    )
    facade = make_facade(workflow_readiness=readiness)

    proposal = facade.propose_demote(
        STRATEGY_ID,
        to_stage="backtest",
        reason="Walk back active runner safely.",
        gui_submit=True,
    )

    assert "reconciliation_drift" in {b.reason_code for b in proposal.blockers}
    assert readiness.calls[0]["required_checks"] == frozenset({"reconciliation", "kill_switch"})
    assert readiness.calls[0]["inspected_checks"] == frozenset(
        {"data_freshness", "broker_reachability"}
    )


def test_propose_demote_without_active_runner_does_not_require_workflow_readiness(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(issues=(_readiness_issue("reconciliation_drift"),))
    )
    facade = make_facade(workflow_readiness=readiness)

    proposal = facade.propose_demote(
        STRATEGY_ID,
        to_stage="backtest",
        reason="Walk back inactive strategy.",
        gui_submit=True,
    )

    assert proposal.blockers == []
    assert readiness.calls == []


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


def test_propose_start_paper_runner_blocks_backtest_stage(make_facade, config_dir: Path) -> None:
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
    holder_blocker = next(b for b in proposal.blockers if b.reason_code == "advisory_lock_held")
    assert holder_blocker.context["holder"]["pid"] == os.getpid()


_OTHER_STRATEGY_ID = "sample.daily.example.other.v1"


def _write_other_strategy(config_dir: Path, *, universe: str) -> Path:
    """Second strategy config whose evaluation symbol is ``universe``'s first entry."""
    content = (
        _STRATEGY_YAML_TEMPLATE.format(stage="paper")
        .replace(f'id: "{STRATEGY_ID}"', f'id: "{_OTHER_STRATEGY_ID}"')
        .replace('variant: "curated"', 'variant: "other"')
        .replace('universe: ["AAPL", "MSFT"]', f"universe: {universe}")
    )
    path = config_dir / "other_strategy.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _seed_other_runner_lock(locks_dir: Path) -> Path:
    holder_path = locks_dir / f"milodex.runtime.strategy.{_OTHER_STRATEGY_ID}.lock"
    holder_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": "test-host",
                "holder_name": f"milodex strategy run {_OTHER_STRATEGY_ID}",
                "started_at": datetime.now(tz=UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    return holder_path


def test_propose_start_paper_runner_admits_same_eval_symbol_co_run(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    """A live runner on the SAME evaluation symbol no longer blocks the proposal
    (concurrent-intraday PR4 removed the launch guard / its bench mirror). The
    three invariants the guard stood in for are closed (PR1 paper submit
    serialization, PR2 opposite-side veto, PR3 per-strategy cap reads the
    strategy ledger), so same-symbol co-run is admitted. The per-strategy runner
    lock (``advisory_lock_held``) still blocks the *same* strategy."""
    _write_strategy(config_dir, stage="paper")  # STRATEGY_ID, eval symbol AAPL
    _write_other_strategy(config_dir, universe='["AAPL", "SPY"]')  # same eval symbol
    _seed_other_runner_lock(locks_dir)
    facade = make_facade()

    proposal = facade.propose_start_paper_runner(STRATEGY_ID)

    assert proposal.admissible, proposal.blockers
    codes = {b.reason_code for b in proposal.blockers}
    assert "evaluation_symbol_in_use" not in codes
    # The retired guard's precondition is gone entirely.
    assert not any(p.name == "evaluation_symbol_free" for p in proposal.preconditions)


@pytest.mark.parametrize(
    "reason_code",
    [
        "reconciliation_drift",
        "kill_switch_open",
        # data_freshness is NOT in _WORKFLOW_REQUIRED_START_RUNNER — omitted intentionally.
        # The real evaluator never generates a data_stale issue for start_paper_runner.
        "broker_unreachable",
    ],
)
def test_propose_start_paper_runner_blocks_required_workflow_readiness(
    make_facade, config_dir: Path, reason_code: str
) -> None:
    _write_strategy(config_dir, stage="paper")
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(issues=(_readiness_issue(reason_code),))
    )
    facade = make_facade(workflow_readiness=readiness)

    proposal = facade.propose_start_paper_runner(STRATEGY_ID)

    assert reason_code in {b.reason_code for b in proposal.blockers}
    first_issue = proposal.projected_outcome["workflow_readiness"]["issues"][0]
    assert first_issue["reason_code"] == reason_code
    assert readiness.calls[0]["required_checks"] == frozenset(
        {"reconciliation", "kill_switch", "broker_reachability"}
    )


def test_propose_stop_paper_runner_blocks_when_no_runner(make_facade, config_dir: Path) -> None:
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


def test_propose_stop_paper_runner_has_no_required_readiness_checks(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    # READINESS_KILL_SWITCH was moved to inspected_checks for the stop family (HR-5).
    # A controlled stop submits no trades; the risk layer blocks anything a still-running
    # runner attempts. Gating a de-risking action on the kill switch inverts the asymmetry
    # principle and wedges the GUI during a safety event.
    _write_strategy(config_dir, stage="paper")
    _seed_runner_lock(locks_dir)
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(
            issues=(
                _readiness_issue("kill_switch_open"),
                _readiness_issue("reconciliation_drift", blocking=False),
                _readiness_issue("data_stale", blocking=False),
                _readiness_issue("broker_unreachable", blocking=False),
            )
        )
    )
    facade = make_facade(workflow_readiness=readiness)

    proposal = facade.propose_stop_paper_runner(STRATEGY_ID)

    # Kill-switch is now inspected, not required — no blockers from readiness.
    assert proposal.blockers == []
    readiness_issues = {
        issue["reason_code"] for issue in proposal.projected_outcome["workflow_readiness"]["issues"]
    }
    assert readiness_issues == {
        "kill_switch_open",
        "reconciliation_drift",
        "data_stale",
        "broker_unreachable",
    }
    assert readiness.calls[0]["required_checks"] == frozenset()
    assert READINESS_KILL_SWITCH in readiness.calls[0]["inspected_checks"]


def test_propose_stop_paper_runner_admissible_when_kill_switch_active(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    # HR-5: with the kill switch ACTIVE the stop proposal must be admissible, and the
    # kill-switch state must appear as an inspected/warning entry, not a blocker.
    _write_strategy(config_dir, stage="paper")
    _seed_runner_lock(locks_dir)
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(issues=(_readiness_issue("kill_switch_open"),))
    )
    facade = make_facade(workflow_readiness=readiness)

    proposal = facade.propose_stop_paper_runner(STRATEGY_ID)

    assert proposal.admissible, proposal.blockers
    assert proposal.blockers == []
    # The kill-switch issue is present in the readiness payload as a warning.
    readiness_issues = [
        issue
        for issue in proposal.projected_outcome["workflow_readiness"]["issues"]
        if issue["reason_code"] == "kill_switch_open"
    ]
    assert len(readiness_issues) == 1
    # The FAKE evaluator hardcodes blocking=True here, which is deliberately
    # adversarial: it proves the helper ignores blocking flags on dimensions
    # outside required_checks. The REAL evaluator (_DefaultWorkflowReadiness)
    # would set blocking=False for an inspected-only dimension.
    assert readiness_issues[0]["blocking"] is True


# --------------------------------------------------------------------------- #
# Micro-live / live remain absent
# --------------------------------------------------------------------------- #


def test_facade_exposes_no_micro_live_or_live_route() -> None:
    """ADR 0051 §6, §7: no GUI path to micro_live or live at launch."""
    methods = {name for name, _ in inspect.getmembers(BenchCommandFacade, predicate=callable)}
    for forbidden in (
        "propose_promote_to_micro_live",
        "submit_promote_to_micro_live",
        "propose_promote_to_live",
        "submit_promote_to_live",
    ):
        assert forbidden not in methods, (
            f"BenchCommandFacade must not expose {forbidden} at launch (ADR 0051)."
        )


def test_propose_demote_refuses_micro_live_and_live_targets(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    for bad in ("micro_live", "live"):
        proposal = facade.propose_demote(STRATEGY_ID, to_stage=bad, reason="trying")
        assert not proposal.admissible
        assert any(b.reason_code == "invalid_demotion_target" for b in proposal.blockers)


def test_backtest_proposal_does_not_consult_workflow_readiness(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(issues=(_readiness_issue("broker_unreachable"),))
    )
    facade = make_facade(workflow_readiness=readiness)

    proposal = facade.propose_backtest(
        STRATEGY_ID,
        start=date(2025, 1, 1),
        end=date(2025, 1, 31),
    )

    assert proposal.admissible, proposal.blockers
    assert readiness.calls == []


def test_submit_promote_to_paper_revalidates_workflow_readiness_drift(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="backtest")
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(),
        WorkflowReadinessReport(issues=(_readiness_issue("data_stale"),)),
    )
    facade = make_facade(workflow_readiness=readiness)
    proposal = facade.propose_promote_to_paper(
        STRATEGY_ID,
        recommendation="Ready for paper.",
        known_risks=["Paper risk."],
        lifecycle_exempt=True,
    )
    assert proposal.admissible, proposal.blockers

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "blocked"
    assert [b.reason_code for b in result.blockers] == ["data_stale"]
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


# --------------------------------------------------------------------------- #
# PR 15 - paper runner submit
# --------------------------------------------------------------------------- #


def test_submit_start_paper_runner_launches_control_and_returns_refs(
    make_facade, config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    _append_open_strategy_run(event_store, session_id="active-session")
    control = _FakePaperRunnerControl(locks_dir)
    facade = make_facade(paper_runner_control=control)
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)

    result = facade.submit_start_paper_runner(proposal)

    assert result.status == "submitted", result.blockers
    assert control.starts == [STRATEGY_ID]
    assert result.durable_refs["strategy_id"] == STRATEGY_ID
    assert result.durable_refs["runner_pid"] == "4242"
    assert result.durable_refs["session_id"] == "active-session"
    assert result.audit_event_id == "active-session"
    assert result.data["command"][-1] == STRATEGY_ID


def test_submit_start_paper_runner_errors_without_audit_linkage(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    """Row never appears → runner_audit_link_missing after the budget.

    Uses a fast-expiring fake clock so the retry loop exits after one probe
    without sleeping 15 s in CI (injection seam: ``now`` + ``sleep``).
    """
    from datetime import UTC, datetime, timedelta

    _write_strategy(config_dir, stage="paper")
    control = _FakePaperRunnerControl(locks_dir)
    # Fake clock: each call advances 20 s. Calls 0-1 are consumed by the
    # proposal timestamp and the orchestration-job row; the deadline anchors
    # at call index 2 and the while condition first evaluates at index 3 —
    # 20 s later, already past the 15 s budget, so the loop body never
    # executes. (The 20 s step is wide enough that exact indices don't
    # matter.)
    t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    calls: list[int] = [0]

    def _fast_now() -> datetime:
        n = calls[0]
        calls[0] += 1
        return t0 + timedelta(seconds=n * 20)

    facade = make_facade(paper_runner_control=control, now=_fast_now, sleep=lambda _: None)
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)

    result = facade.submit_start_paper_runner(proposal)

    assert result.status == "error"
    assert control.starts == [STRATEGY_ID]
    assert result.audit_event_id is None
    assert result.durable_refs["runner_pid"] == "4242"
    assert result.blockers[0].reason_code == "runner_audit_link_missing"


def test_submit_start_paper_runner_succeeds_when_session_row_appears_on_third_poll(
    make_facade, config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    """Row appears on the 3rd probe → status submitted, no error, elapsed < budget.

    The first two _latest_open_session_id calls return None (child hasn't
    written the row yet); the third returns the session id. A monotonically
    advancing fake clock tracks elapsed polls; we assert the result is
    submitted and the fake deadline was not exhausted.
    """
    from datetime import UTC, datetime, timedelta

    _write_strategy(config_dir, stage="paper")
    control = _FakePaperRunnerControl(locks_dir)

    # Fake clock: advances 1 s per call so the full 15 s budget is not
    # exhausted by 3 probe rounds (propose uses call 0; deadline uses call 1;
    # the while-condition calls are 2, 4, 6; the session-lookup calls 3, 5
    # return None then "active-session" via the event-store).
    t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    calls: list[int] = [0]

    def _slow_now() -> datetime:
        n = calls[0]
        calls[0] += 1
        return t0 + timedelta(seconds=n)

    # Simulate the child writing its row on the 3rd _latest_open_session_id call.
    probe_count: list[int] = [0]
    original_store = event_store

    def _counting_factory():
        probe_count[0] += 1
        if probe_count[0] >= 3:
            _append_open_strategy_run(original_store, session_id="active-session")
        return original_store

    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=_counting_factory,
        paper_runner_control=control,
        workflow_readiness=_healthy_readiness(),
        now=_slow_now,
        sleep=lambda _: None,
    )
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)

    result = facade.submit_start_paper_runner(proposal)

    assert result.status == "submitted", result.blockers
    assert control.starts == [STRATEGY_ID]
    assert result.durable_refs["session_id"] == "active-session"
    assert result.audit_event_id == "active-session"
    # Budget was not exhausted: fake clock advanced only a few seconds.
    assert calls[0] < 20  # well under the 15 s budget boundary


def test_submit_start_paper_runner_retries_then_errors_when_budget_exhausted(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    """Row never appears → runner_audit_link_missing after budget is exhausted.

    Distinct from the pin test above: this one asserts the retry loop actually
    runs multiple probes before giving up, rather than the loop body never
    executing (the pin test uses a large time-step to skip the loop entirely;
    this test uses a small step and counts probes).
    """
    from datetime import UTC, datetime, timedelta

    _write_strategy(config_dir, stage="paper")
    control = _FakePaperRunnerControl(locks_dir)

    # Fake clock: advances 6 s per call; deadline = t0+6+15 = t0+21.
    # While-condition checks at t0+12, t0+18 (both < t0+21 → loop runs twice),
    # then t0+24 (> t0+21 → exits).
    t0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    calls: list[int] = [0]

    def _stepped_now() -> datetime:
        n = calls[0]
        calls[0] += 1
        return t0 + timedelta(seconds=n * 6)

    probe_count: list[int] = [0]

    def _counting_factory_no_row():
        probe_count[0] += 1
        return EventStore(":memory:")

    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=_counting_factory_no_row,
        paper_runner_control=control,
        workflow_readiness=_healthy_readiness(),
        now=_stepped_now,
        sleep=lambda _: None,
    )
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)

    result = facade.submit_start_paper_runner(proposal)

    assert result.status == "error"
    assert result.blockers[0].reason_code == "runner_audit_link_missing"
    # The loop ran at least twice (not a single immediate failure).
    assert probe_count[0] >= 2


def test_submit_start_paper_runner_blocks_when_revalidation_fails(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    path = _write_strategy(config_dir, stage="paper")
    control = _FakePaperRunnerControl(locks_dir)
    facade = make_facade(paper_runner_control=control)
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)
    path.write_text(
        path.read_text(encoding="utf-8").replace('stage: "paper"', 'stage: "backtest"'),
        encoding="utf-8",
    )

    result = facade.submit_start_paper_runner(proposal)

    assert result.status == "blocked"
    assert control.starts == []
    assert any(b.reason_code == "stage_incompatible_with_mode" for b in result.blockers)


def test_submit_start_paper_runner_requires_runner_control(make_facade, config_dir: Path) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_start_paper_runner(STRATEGY_ID)

    result = facade.submit_start_paper_runner(proposal)

    assert result.status == "error"
    assert result.blockers[0].reason_code == "paper_runner_control_unavailable"


def test_submit_stop_paper_runner_writes_controlled_stop_request(
    make_facade, config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    _seed_runner_lock(locks_dir)
    _append_open_strategy_run(event_store, session_id="active-session")
    control = _FakePaperRunnerControl(locks_dir)
    facade = make_facade(paper_runner_control=control)
    proposal = facade.propose_stop_paper_runner(STRATEGY_ID)

    result = facade.submit_stop_paper_runner(proposal)

    assert result.status == "submitted", result.blockers
    assert control.stops[0][0] == STRATEGY_ID
    assert result.durable_refs["exit_reason"] == "controlled_stop"
    assert result.durable_refs["kill_switch"] == "false"
    assert result.durable_refs["session_id"] == "active-session"
    assert result.audit_event_id == "active-session"
    assert result.data["kill_switch"] is False


def test_submit_stop_paper_runner_blocks_without_audit_linkage(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    _seed_runner_lock(locks_dir)
    control = _FakePaperRunnerControl(locks_dir)
    facade = make_facade(paper_runner_control=control)
    proposal = facade.propose_stop_paper_runner(STRATEGY_ID)

    result = facade.submit_stop_paper_runner(proposal)

    assert result.status == "blocked"
    assert control.stops == []
    assert result.audit_event_id is None
    assert result.blockers[0].reason_code == "runner_audit_link_missing"


def test_propose_stop_paper_runner_blocks_dead_but_lock_present_runner(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    """A hard-killed runner that left a stale lock on disk must not look
    stoppable: identity-verified liveness (hardening-2) reports it absent."""
    _write_strategy(config_dir, stage="paper")
    _seed_dead_runner_lock(locks_dir)
    facade = make_facade()

    proposal = facade.propose_stop_paper_runner(STRATEGY_ID)

    assert not proposal.admissible
    assert proposal.blockers[0].reason_code == "no_active_runner"


def test_submit_stop_paper_runner_blocks_dead_but_lock_present_runner(
    make_facade, config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    """Controlled stop against a dead-but-lock-present runner is honestly
    blocked, never a false 'submitted'. A phantom open strategy_runs row is
    present (the hard-kill signature) and must not change the verdict; no
    controlled-stop request reaches the runner control."""
    _write_strategy(config_dir, stage="paper")
    _seed_dead_runner_lock(locks_dir)
    _append_open_strategy_run(event_store, session_id="sess-phantom-1")
    control = _FakePaperRunnerControl(locks_dir)
    facade = make_facade(paper_runner_control=control)

    proposal = facade.propose_stop_paper_runner(STRATEGY_ID)
    result = facade.submit_stop_paper_runner(proposal)

    assert result.status == "blocked", result
    assert any(b.reason_code == "no_active_runner" for b in result.blockers)
    assert control.stops == [], "no controlled-stop should be requested for a dead runner"


def test_submit_stop_paper_runner_blocks_without_active_runner(
    make_facade, config_dir: Path, locks_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    control = _FakePaperRunnerControl(locks_dir)
    facade = make_facade(paper_runner_control=control)
    dummy = CommandProposal(
        action_family=ACTION_FAMILY_STOP_PAPER_RUNNER,
        strategy_id=STRATEGY_ID,
        inputs={},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="dummy-proposal",
    )

    result = facade.submit_stop_paper_runner(dummy)

    assert result.status == "blocked"
    assert control.stops == []
    assert result.blockers[0].reason_code == "no_active_runner"


# --------------------------------------------------------------------------- #
# Phase C1 — submit_demote
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# PR 13 - submit_backtest
# --------------------------------------------------------------------------- #


class _FakeSingleBacktestEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[date, date, str | None]] = []

    def run(self, start: date, end: date, *, run_id: str | None = None) -> BacktestResult:
        self.calls.append((start, end, run_id))
        return BacktestResult(
            run_id=run_id or "bt-single",
            strategy_id=STRATEGY_ID,
            start_date=start,
            end_date=end,
            initial_equity=1_000.0,
            final_equity=1_125.0,
            total_return_pct=12.5,
            trade_count=4,
            buy_count=2,
            sell_count=2,
            slippage_pct=0.0005,
            commission_per_trade=0.0,
            trading_days=8,
            db_id=101,
            round_trip_count=2,
            risk_policy=RiskPolicy.BYPASS,
            skipped_count=1,
            data_quality={"status": "pass", "blocker_count": 0, "warning_count": 0},
            run_manifest={"schema_version": 1, "data": {"quality": {"status": "pass"}}},
        )


class _FakeWalkForwardEngine:
    def __init__(self) -> None:
        self.prefetched: tuple[date, date] | None = None
        # ``walk_forward_windows`` and ``bar_size`` are both exposed as public
        # attributes on ``BacktestEngine`` (the latter became a public property
        # in the RM-005a backtest-run-lifecycle surface refactor); tests mirror
        # them directly rather than reaching into ``_loaded``.
        self.walk_forward_windows: int = 2
        self.bar_size: str = "1D"
        # Kept ``_loaded`` stub for any legacy access path that still reads
        # ``tempo["bar_size"]`` via the loaded config; harmless if unused.
        self._loaded = type("_L", (), {"config": type("_C", (), {"tempo": {"bar_size": "1D"}})()})()

    def prefetch_bars(self, start: date, end: date, *, timeframe=None) -> dict[str, BarSet]:  # noqa: ARG002
        self.prefetched = (start, end)
        return {"AAPL": _barset(start, 8)}


def _barset(start: date, days: int) -> BarSet:
    timestamps = pd.date_range(start=start.isoformat(), periods=days, freq="D", tz="UTC")
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": [100.0] * days,
                "high": [101.0] * days,
                "low": [99.0] * days,
                "close": [100.5] * days,
                "volume": [1000] * days,
            }
        )
    )


def _make_backtest_proposal(*, walk_forward: bool) -> CommandProposal:
    return CommandProposal(
        action_family=ACTION_FAMILY_BACKTEST,
        strategy_id=STRATEGY_ID,
        inputs={
            "start": "2020-01-01",
            "end": "2020-01-08",
            "walk_forward": walk_forward,
            "initial_equity": 1000.0,
            "slippage": None,
            "run_id": "bench-run",
            "risk_policy": "bypass",
        },
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="bench-backtest-proposal",
    )


def test_submit_backtest_runs_single_period_engine_and_returns_payload(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="backtest")
    engine = _FakeSingleBacktestEngine()
    facade = make_facade(backtest_engine_factory=lambda _strategy_id, **_kwargs: engine)

    result = facade.submit_backtest(_make_backtest_proposal(walk_forward=False))

    assert result.status == "submitted", result.blockers
    assert engine.calls == [(date(2020, 1, 1), date(2020, 1, 8), "bench-run")]
    assert (
        result.durable_refs.items()
        >= {
            "run_id": "bench-run",
            "strategy_id": STRATEGY_ID,
            "start": "2020-01-01",
            "end": "2020-01-08",
            "walk_forward": "false",
            "risk_policy": "bypass",
            "backtest_run_db_id": "101",
        }.items()
    )
    assert result.durable_refs["orchestration_job_id"]
    assert result.durable_refs["orchestration_batch_id"]
    job = event_store.get_orchestration_job(result.durable_refs["orchestration_job_id"])
    assert job is not None
    assert job.status == "completed"
    assert job.action_type == "backtest_single"
    assert job.execution_ref == "bench-run"
    assert result.data["metrics"]["trade_count"] == 4
    assert result.data["skipped_count"] == 1
    assert result.data["data_quality_status"] == "pass"
    assert result.data["run_manifest"]["schema_version"] == 1


def test_submit_backtest_from_idle_returns_strategy_to_backtest_stage(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="idle")
    engine = _FakeSingleBacktestEngine()
    facade = make_facade(backtest_engine_factory=lambda _strategy_id, **_kwargs: engine)

    result = facade.submit_backtest(_make_backtest_proposal(walk_forward=False))

    assert result.status == "submitted", result.blockers
    assert result.durable_refs["from_stage"] == "idle"
    assert result.durable_refs["to_stage"] == "backtest"
    assert result.durable_refs["stage_return_promotion_id"]
    assert 'stage: "backtest"' in config_path.read_text(encoding="utf-8")

    events = event_store.list_promotions_for_strategy(STRATEGY_ID)
    assert len(events) == 1
    assert events[0].from_stage == "idle"
    assert events[0].to_stage == "backtest"
    assert events[0].promotion_type == "stage_return"
    assert events[0].backtest_run_id == "bench-run"


def test_submit_backtest_from_idle_writes_stage_return_before_engine_runs(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """ADR 0050 Decision 6: the idle->backtest governance write happens at job
    acceptance — the YAML stage and the stage_return promotion event are
    already durable when the engine starts running."""
    config_path = _write_strategy(config_dir, stage="idle")
    observed: dict[str, object] = {}
    inner = _FakeSingleBacktestEngine()

    class _ObservingEngine:
        def run(self, start: date, end: date, *, run_id: str | None = None) -> BacktestResult:
            observed["yaml"] = config_path.read_text(encoding="utf-8")
            observed["promotions"] = event_store.list_promotions_for_strategy(STRATEGY_ID)
            return inner.run(start, end, run_id=run_id)

    facade = make_facade(backtest_engine_factory=lambda _sid, **_kw: _ObservingEngine())

    result = facade.submit_backtest(_make_backtest_proposal(walk_forward=False))

    assert result.status == "submitted", result.blockers
    assert 'stage: "backtest"' in observed["yaml"]
    promotions = observed["promotions"]
    assert len(promotions) == 1
    assert promotions[0].promotion_type == "stage_return"
    assert promotions[0].from_stage == "idle"
    assert promotions[0].to_stage == "backtest"
    assert promotions[0].backtest_run_id == "bench-run"


class _ExplodingBacktestEngine:
    def run(self, start: date, end: date, *, run_id: str | None = None) -> BacktestResult:
        raise RuntimeError("engine blew up")


def test_submit_backtest_failure_does_not_roll_back_stage_return(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """A failed run leaves the strategy at BACKTEST with no fresh evidence —
    a legitimate ADR 0050 state. No YAML revert, no compensating event."""
    config_path = _write_strategy(config_dir, stage="idle")
    facade = make_facade(backtest_engine_factory=lambda _sid, **_kw: _ExplodingBacktestEngine())

    result = facade.submit_backtest(_make_backtest_proposal(walk_forward=False))

    assert result.status == "error"
    assert result.blockers[0].reason_code == "backtest_failed"
    # Acceptance-time governance state survives the failure.
    assert 'stage: "backtest"' in config_path.read_text(encoding="utf-8")
    events = event_store.list_promotions_for_strategy(STRATEGY_ID)
    assert len(events) == 1
    assert events[0].promotion_type == "stage_return"
    assert events[0].from_stage == "idle"
    assert events[0].to_stage == "backtest"
    # The error result still carries the durable stage-return refs.
    assert result.durable_refs["from_stage"] == "idle"
    assert result.durable_refs["to_stage"] == "backtest"
    assert result.durable_refs["stage_return_promotion_id"]


def test_submit_backtest_non_idle_stage_writes_no_stage_return(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="paper")
    engine = _FakeSingleBacktestEngine()
    facade = make_facade(backtest_engine_factory=lambda _sid, **_kw: engine)

    result = facade.submit_backtest(_make_backtest_proposal(walk_forward=False))

    assert result.status == "submitted", result.blockers
    assert "from_stage" not in result.durable_refs
    assert "stage_return_promotion_id" not in result.durable_refs
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_backtest_runs_walk_forward_with_prefetched_bars(
    make_facade, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_strategy(config_dir, stage="backtest")
    engine = _FakeWalkForwardEngine()
    facade = make_facade(backtest_engine_factory=lambda _strategy_id, **_kwargs: engine)
    captured: dict[str, object] = {}

    def fake_run_walk_forward(**kwargs):
        captured.update(kwargs)
        return WalkForwardResult(
            run_id="bench-run",
            strategy_id=STRATEGY_ID,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 1, 8),
            initial_equity=1_000.0,
            train_days=4,
            test_days=2,
            step_days=2,
            windows=[],
            oos_trade_count=3,
            oos_skipped_count=2,
            oos_trading_days=4,
            oos_total_return_pct=7.5,
            oos_sharpe=1.2,
            oos_max_drawdown_pct=3.4,
            oos_equity_curve=[],
            stability=WalkForwardStability(
                sharpe_min=1.0,
                sharpe_max=1.4,
                sharpe_std=0.2,
                windows_positive=2,
                windows_negative=0,
                single_window_dependency=False,
            ),
            db_id=202,
            risk_policy=RiskPolicy.BYPASS,
            data_quality={"status": "pass_with_warnings", "warning_count": 1},
            run_manifest={"schema_version": 1, "mode": "walk_forward"},
        )

    monkeypatch.setattr(
        facade_module,
        "run_walk_forward",
        lambda engine_arg, **kwargs: fake_run_walk_forward(engine=engine_arg, **kwargs),
    )

    result = facade.submit_backtest(_make_backtest_proposal(walk_forward=True))

    assert result.status == "submitted", result.blockers
    assert engine.prefetched == (date(2020, 1, 1), date(2020, 1, 8))
    assert captured["all_bars"] is not None
    assert result.durable_refs["walk_forward"] == "true"
    assert result.durable_refs["backtest_run_db_id"] == "202"
    assert result.data["oos_aggregate"]["trade_count"] == 3
    assert result.data["oos_aggregate"]["skipped_count"] == 2
    assert result.data["data_quality_status"] == "pass_with_warnings"


def test_submit_backtest_rejects_wrong_action_family(make_facade) -> None:
    facade = make_facade(backtest_engine_factory=lambda *_args, **_kwargs: object())
    wrong = CommandProposal(
        action_family=ACTION_FAMILY_DEMOTE,
        strategy_id=STRATEGY_ID,
        inputs={},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="wrong-family",
    )

    result = facade.submit_backtest(wrong)

    assert result.status == "error"
    assert any(
        blocker.reason_code == "proposal_action_family_mismatch" for blocker in result.blockers
    )


def test_submit_backtest_requires_event_store_and_engine_factory(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    proposal = _make_backtest_proposal(walk_forward=False)

    no_store = make_facade(
        with_event_store=False,
        backtest_engine_factory=lambda *_args, **_kwargs: object(),
    )
    assert no_store.submit_backtest(proposal).blockers[0].reason_code == ("event_store_unavailable")

    no_engine = make_facade()
    assert no_engine.submit_backtest(proposal).blockers[0].reason_code == (
        "backtest_engine_unavailable"
    )


def test_submit_backtest_revalidates_stale_deleted_config(make_facade, config_dir: Path) -> None:
    config_path = _write_strategy(config_dir, stage="backtest")
    proposal = _make_backtest_proposal(walk_forward=False)
    facade = make_facade(
        backtest_engine_factory=lambda *_args, **_kwargs: _FakeSingleBacktestEngine()
    )
    config_path.unlink()

    result = facade.submit_backtest(proposal)

    assert result.status == "blocked"
    assert any(blocker.reason_code == "strategy_not_found" for blocker in result.blockers)


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


def test_submit_demote_to_idle_updates_yaml_and_returns_durable_refs(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = _make_demote_proposal(
        to_stage="idle",
        reason="Shelf while evidence is rebuilt.",
    )

    result = facade.submit_demote(proposal, gui_submit=True)

    assert result.status == "submitted", result.blockers
    assert result.durable_refs["from_stage"] == "backtest"
    assert result.durable_refs["to_stage"] == "idle"
    assert result.durable_refs["promotion_type"] == "demotion"
    assert 'stage: "idle"' in config_path.read_text(encoding="utf-8")

    events = event_store.list_promotions_for_strategy(STRATEGY_ID)
    assert len(events) == 1
    assert events[0].promotion_type == "demotion"
    assert events[0].to_stage == "idle"


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
    result = facade.submit_demote(_make_demote_proposal(to_stage="backtest", reason="walk back"))

    assert result.status == "submitted", result.blockers
    assert result.durable_refs.get("reverses_event_id") == str(prior_id)


def test_submit_demote_blank_reason_refused_and_no_state_change(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="paper")
    yaml_before = config_path.read_text(encoding="utf-8")
    facade = make_facade()

    result = facade.submit_demote(_make_demote_proposal(to_stage="backtest", reason="   "))

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

    result = facade.submit_demote(_make_demote_proposal(to_stage=bad_target, reason="trying"))

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
    assert any(b.reason_code == "proposal_action_family_mismatch" for b in result.blockers)
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_demote_revalidates_stale_proposal(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """A proposal admissible at propose-time goes stale when the underlying
    stage moves. submit must re-validate and refuse."""
    config_path = _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    # Propose against the live config (paper stage); this is admissible.
    proposal = facade.propose_demote(STRATEGY_ID, to_stage="backtest", reason="real reason")
    assert proposal.admissible

    # Simulate drift: someone (operator, another process) already walked the
    # strategy back to backtest. The proposal is now stale.
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('stage: "paper"', 'stage: "backtest"'),
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
    result = facade.submit_demote(_make_demote_proposal(to_stage="backtest", reason="x"))
    assert result.status == "error"
    assert any(b.reason_code == "event_store_unavailable" for b in result.blockers)


# --------------------------------------------------------------------------- #
# Phase D1 — submit_freeze_manifest
# --------------------------------------------------------------------------- #


def _make_freeze_manifest_proposal(
    *,
    frozen_by: str = "operator",
) -> CommandProposal:
    """Construct a proposal the way the bridge eventually will: serialize the
    inputs `propose_freeze_manifest` exposes. `submit_freeze_manifest`
    re-validates these via `propose_freeze_manifest` before dispatching."""
    return CommandProposal(
        action_family=ACTION_FAMILY_FREEZE_MANIFEST,
        strategy_id=STRATEGY_ID,
        inputs={"frozen_by": frozen_by},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="phase-d1-test-proposal",
    )


def test_submit_freeze_manifest_success_on_paper_stage(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """Phase D1: a paper-stage strategy freezes successfully via the GUI
    submit path — same governance callee the CLI uses."""
    config_path = _write_strategy(config_dir, stage="paper")
    facade = make_facade()

    result = facade.submit_freeze_manifest(_make_freeze_manifest_proposal(frozen_by="operator"))

    assert result.status == "submitted", result.blockers
    assert result.action_family == ACTION_FAMILY_FREEZE_MANIFEST
    # Durable refs carry the strategy_id, stage, config_hash, config_path,
    # frozen_by, and frozen_at + the manifest_event_id once persisted.
    assert result.durable_refs["strategy_id"] == STRATEGY_ID
    assert result.durable_refs["stage"] == "paper"
    assert result.durable_refs["frozen_by"] == "operator"
    assert result.durable_refs["config_path"] == str(config_path)
    assert result.durable_refs["config_hash"]
    assert result.durable_refs["frozen_at"]
    assert result.durable_refs.get("manifest_event_id")
    assert result.submitted_at is not None
    assert result.audit_event_id == result.durable_refs["manifest_event_id"]
    # The governance event landed via the existing event store path.
    manifest = event_store.get_active_manifest_for_strategy(STRATEGY_ID, "paper")
    assert manifest is not None
    assert manifest.config_hash == result.durable_refs["config_hash"]


def test_submit_freeze_manifest_refuses_backtest_stage(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """Phase D1: a backtest-stage strategy must be refused with a structured
    blocker — backtest has nothing to snapshot yet."""
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()

    result = facade.submit_freeze_manifest(_make_freeze_manifest_proposal())

    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    # The proposal-time stage check fires during re-validation before the
    # governance call.
    assert "stage_not_freezable" in codes
    # No event written on refusal.
    assert event_store.get_active_manifest_for_strategy(STRATEGY_ID, "backtest") is None


def test_submit_freeze_manifest_revalidates_stale_proposal(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """Phase D1: a proposal admissible at propose-time but stale at
    submit-time (stage drifted to backtest) must be refused without
    dispatching to the governance callee."""
    config_path = _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = facade.propose_freeze_manifest(STRATEGY_ID)
    assert proposal.admissible, proposal.blockers

    # Drift the YAML so the strategy is no longer freezable.
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('stage: "paper"', 'stage: "backtest"'),
        encoding="utf-8",
    )

    result = facade.submit_freeze_manifest(proposal)
    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert "stage_not_freezable" in codes
    assert event_store.get_active_manifest_for_strategy(STRATEGY_ID, "paper") is None
    assert event_store.get_active_manifest_for_strategy(STRATEGY_ID, "backtest") is None


def test_submit_freeze_manifest_refuses_when_facade_lacks_event_store(
    config_dir: Path, locks_dir: Path
) -> None:
    """A facade constructed without an event_store_factory cannot freeze."""
    _write_strategy(config_dir, stage="paper")
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=None,
    )
    result = facade.submit_freeze_manifest(_make_freeze_manifest_proposal())
    assert result.status == "error"
    assert any(b.reason_code == "event_store_unavailable" for b in result.blockers)


def test_submit_freeze_manifest_rejects_proposal_for_different_action_family(
    make_facade, config_dir: Path
) -> None:
    """Phase D1: a proposal whose action_family is not freeze_manifest is
    refused with `proposal_action_family_mismatch`."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    wrong = CommandProposal(
        action_family=ACTION_FAMILY_DEMOTE,  # mismatched
        strategy_id=STRATEGY_ID,
        inputs={"frozen_by": "operator"},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="wrong-family",
    )
    result = facade.submit_freeze_manifest(wrong)
    assert result.status == "error"
    assert any(b.reason_code == "proposal_action_family_mismatch" for b in result.blockers)


def test_submit_freeze_manifest_threads_frozen_by_from_inputs(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """Phase D1: `frozen_by` is carried on the proposal inputs and threaded
    through to the governance callee — same pattern as `approved_by` on
    demote. Mirrors the Phase C2 F4 identity-sourcing pattern."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    result = facade.submit_freeze_manifest(
        _make_freeze_manifest_proposal(frozen_by="operator-cli-override")
    )
    assert result.status == "submitted", result.blockers
    assert result.durable_refs["frozen_by"] == "operator-cli-override"
    manifest = event_store.get_active_manifest_for_strategy(STRATEGY_ID, "paper")
    assert manifest is not None
    assert manifest.frozen_by == "operator-cli-override"


# --------------------------------------------------------------------------- #
# Phase D2 — submit_promote_to_paper (backend only)
# --------------------------------------------------------------------------- #


_PROMOTE_DEFAULT_RISKS: list[str] = ["regime-dependent edge"]


def _make_promote_to_paper_proposal(
    *,
    recommendation: str | None = "OOS Sharpe stable across windows; promote.",
    known_risks: list[str] | None = None,
    run_id: str | None = "bt-d2-test",
    approved_by: str = "operator",
    lifecycle_exempt: bool = False,
) -> CommandProposal:
    """Construct a proposal the way the (future) bridge will: serialize the
    inputs ``propose_promote_to_paper`` exposes. ``submit_promote_to_paper``
    re-validates these via ``propose_promote_to_paper`` before dispatching.

    ``known_risks=None`` means "use the default sample risks". Pass an empty
    list explicitly to exercise the missing-risks refusal path.
    """
    risks = list(_PROMOTE_DEFAULT_RISKS) if known_risks is None else list(known_risks)
    return CommandProposal(
        action_family=ACTION_FAMILY_PROMOTE_TO_PAPER,
        strategy_id=STRATEGY_ID,
        inputs={
            "to_stage": "paper",
            "recommendation": recommendation,
            "known_risks": risks,
            "run_id": run_id,
            "approved_by": approved_by,
            "lifecycle_exempt": lifecycle_exempt,
        },
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="phase-d2-test-proposal",
    )


def _append_walk_forward_backtest_run(
    event_store: EventStore,
    *,
    run_id: str,
    sharpe: float,
    max_drawdown_pct: float,
    trade_count: int,
) -> None:
    """Insert a synthetic walk-forward backtest run whose OOS aggregate
    metrics can be read directly by ``metrics_from_run`` (milodex.promotion.run_evidence)
    — no need to synthesize real trades. Mirrors the metadata shape the orchestrator
    writes per ADR 0021."""
    now = datetime.now()
    event_store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=STRATEGY_ID,
            config_path=None,
            config_hash=None,
            start_date=now,
            end_date=now,
            started_at=now,
            status="completed",
            slippage_pct=None,
            commission_per_trade=None,
            metadata={
                "walk_forward": True,
                "oos_aggregate": {
                    "sharpe": sharpe,
                    "max_drawdown_pct": max_drawdown_pct,
                    "trade_count": trade_count,
                },
            },
            ended_at=now,
        )
    )


def test_submit_promote_to_paper_lifecycle_exempt_success(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """Phase D2 happy path via the lifecycle-exempt branch (no backtest run
    needed), using the policy-listed lifecycle-proof regime id (ADR 0058 —
    the exemption is scoped to that id). Asserts atomic manifest + promotion
    landing and YAML stage rewrite via the same governance callee the CLI uses."""
    config_path = _write_regime_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = _make_regime_promote_proposal()

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "submitted", result.blockers
    assert result.action_family == ACTION_FAMILY_PROMOTE_TO_PAPER
    # Durable refs carry the full promotion + manifest identification.
    assert result.durable_refs["strategy_id"] == _REGIME_STRATEGY_ID
    assert result.durable_refs["from_stage"] == "backtest"
    assert result.durable_refs["to_stage"] == "paper"
    assert result.durable_refs["promotion_type"] == "lifecycle_exempt"
    assert result.durable_refs["approved_by"] == "operator"
    assert result.durable_refs.get("promotion_id")
    assert result.durable_refs.get("manifest_id")
    assert result.durable_refs.get("manifest_hash")
    assert result.audit_event_id == result.durable_refs["promotion_id"]
    assert result.submitted_at is not None
    # YAML stage line rewritten.
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")
    # Auto-frozen manifest at the paper stage.
    manifest = event_store.get_active_manifest_for_strategy(_REGIME_STRATEGY_ID, "paper")
    assert manifest is not None
    assert manifest.stage == "paper"
    assert manifest.config_hash == result.durable_refs["manifest_hash"]


def test_submit_promote_to_paper_statistical_success(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """Phase D2: statistical promotion path with a real backtest run whose
    OOS metrics clear the gate (Sharpe > 0.5, drawdown < 15%, trades >= 30).
    Asserts metrics propagate into ``durable_refs``."""
    config_path = _write_strategy(config_dir, stage="backtest")
    _append_walk_forward_backtest_run(
        event_store,
        run_id="bt-d2-pass",
        sharpe=1.25,
        max_drawdown_pct=8.5,
        trade_count=42,
    )
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(run_id="bt-d2-pass")

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "submitted", result.blockers
    assert result.durable_refs["promotion_type"] == "statistical"
    assert result.durable_refs.get("backtest_run_id") == "bt-d2-pass"
    assert result.durable_refs.get("sharpe_ratio")
    assert result.durable_refs.get("max_drawdown_pct")
    assert result.durable_refs.get("trade_count") == "42"
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")


def test_submit_promote_to_paper_accepts_paper_tier_below_capital_thresholds(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="backtest")
    _append_walk_forward_backtest_run(
        event_store,
        run_id="bt-paper-tier-pass",
        sharpe=0.327,
        max_drawdown_pct=18.0,
        trade_count=42,
    )
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(run_id="bt-paper-tier-pass")

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "submitted", result.blockers
    assert result.durable_refs["promotion_type"] == "statistical"
    assert result.durable_refs.get("backtest_run_id") == "bt-paper-tier-pass"
    assert float(result.durable_refs["sharpe_ratio"]) == 0.327
    assert float(result.durable_refs["max_drawdown_pct"]) == 18.0
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")


def test_submit_promote_to_paper_honors_configured_trade_floor(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="backtest", min_trades_required=20)
    _append_walk_forward_backtest_run(
        event_store,
        run_id="bt-configured-floor-pass",
        sharpe=0.66,
        max_drawdown_pct=18.0,
        trade_count=20,
    )
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(run_id="bt-configured-floor-pass")

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "submitted", result.blockers
    assert result.durable_refs["promotion_type"] == "statistical"
    assert result.durable_refs.get("trade_count") == "20"
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")


def test_submit_promote_to_paper_missing_recommendation_refused(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """A blank recommendation must be refused at revalidation, before any
    governance call. No mutation."""
    config_path = _write_strategy(config_dir, stage="backtest")
    yaml_before = config_path.read_text(encoding="utf-8")
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(
        recommendation="   ", lifecycle_exempt=True, run_id=None
    )

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert "missing_recommendation" in codes
    # No state mutation.
    assert config_path.read_text(encoding="utf-8") == yaml_before
    assert event_store.get_active_manifest_for_strategy(STRATEGY_ID, "paper") is None
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_promote_to_paper_missing_known_risks_refused(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    config_path = _write_strategy(config_dir, stage="backtest")
    yaml_before = config_path.read_text(encoding="utf-8")
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(known_risks=[], lifecycle_exempt=True, run_id=None)

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert "missing_known_risks" in codes
    assert config_path.read_text(encoding="utf-8") == yaml_before
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_promote_to_paper_wrong_source_stage_refused(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """A paper-stage (or other non-backtest) strategy cannot be promoted to
    paper. Mirrors ``validate_stage_transition`` behavior."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(lifecycle_exempt=True, run_id=None)

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert "wrong_source_stage" in codes


def test_submit_promote_to_paper_missing_run_id_for_statistical_refused(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """Non-lifecycle-exempt promotion requires a backtest run id."""
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(run_id=None, lifecycle_exempt=False)

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert "missing_run_id" in codes


def test_submit_promote_to_paper_backtest_run_not_found(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """A run_id that does not exist in the event store surfaces as a
    structured ``backtest_run_not_found`` blocker, not as a raised exception."""
    config_path = _write_strategy(config_dir, stage="backtest")
    yaml_before = config_path.read_text(encoding="utf-8")
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(run_id="bt-does-not-exist")

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert "backtest_run_not_found" in codes
    assert config_path.read_text(encoding="utf-8") == yaml_before
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_promote_to_paper_gate_failure_blocks_and_does_not_mutate(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """A backtest whose OOS metrics fail the gate must produce
    ``gate_check_failed`` blockers per failure reason, with no mutation to
    YAML, manifest history, or promotion history."""
    config_path = _write_strategy(config_dir, stage="backtest")
    yaml_before = config_path.read_text(encoding="utf-8")
    _append_walk_forward_backtest_run(
        event_store,
        run_id="bt-d2-fail",
        sharpe=-0.2,
        max_drawdown_pct=30.0,
        trade_count=10,
    )
    facade = make_facade()
    proposal = _make_promote_to_paper_proposal(run_id="bt-d2-fail")

    result = facade.submit_promote_to_paper(proposal)

    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert codes == {"gate_check_failed"}
    # Each gate failure is a separate blocker.
    assert len(result.blockers) >= 3
    assert config_path.read_text(encoding="utf-8") == yaml_before
    assert event_store.get_active_manifest_for_strategy(STRATEGY_ID, "paper") is None
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_promote_to_paper_revalidates_stale_proposal(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """A proposal admissible at propose-time but stale at submit-time (stage
    drifted away from backtest) must be refused without dispatching."""
    config_path = _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = facade.propose_promote_to_paper(
        STRATEGY_ID,
        recommendation="OK to promote.",
        known_risks=["regime-dependent edge"],
        lifecycle_exempt=True,
    )
    assert proposal.admissible, proposal.blockers

    # Drift the YAML so the strategy is no longer at backtest.
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('stage: "backtest"', 'stage: "paper"'),
        encoding="utf-8",
    )

    result = facade.submit_promote_to_paper(proposal)
    assert result.status == "blocked"
    codes = {b.reason_code for b in result.blockers}
    assert "wrong_source_stage" in codes
    # No promotion event written.
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


def test_submit_promote_to_paper_refuses_when_facade_lacks_event_store(
    config_dir: Path, locks_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=None,
    )
    result = facade.submit_promote_to_paper(_make_promote_to_paper_proposal())
    assert result.status == "error"
    assert any(b.reason_code == "event_store_unavailable" for b in result.blockers)


def test_submit_promote_to_paper_rejects_proposal_for_different_action_family(
    make_facade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = make_facade()
    wrong = CommandProposal(
        action_family=ACTION_FAMILY_DEMOTE,  # mismatched
        strategy_id=STRATEGY_ID,
        inputs={"to_stage": "paper"},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime.now(),
        proposal_id="wrong-family",
    )
    result = facade.submit_promote_to_paper(wrong)
    assert result.status == "error"
    assert any(b.reason_code == "proposal_action_family_mismatch" for b in result.blockers)


def test_submit_promote_to_paper_threads_approved_by_from_inputs(
    make_facade, config_dir: Path, event_store: EventStore
) -> None:
    """The CLI default of ``approved_by="operator"`` is overrideable per
    request. The Bench bridge (Phase D3) will source it via the same
    ``_resolve_operator_identity()`` helper used for demote/freeze; the
    backend just threads the value through to the governance event.

    Uses the lifecycle-proof regime id so the lifecycle-exempt no-run path is
    admissible under the ADR 0058 scoping."""
    _write_regime_strategy(config_dir, stage="backtest")
    facade = make_facade()
    proposal = _make_regime_promote_proposal(
        approved_by="operator-cli-override",
        recommendation="lifecycle-exempt",
        known_risks=["w"],
    )

    result = facade.submit_promote_to_paper(proposal)
    assert result.status == "submitted", result.blockers
    assert result.durable_refs["approved_by"] == "operator-cli-override"
    promotions = event_store.list_promotions_for_strategy(_REGIME_STRATEGY_ID)
    assert len(promotions) == 1
    assert promotions[0].approved_by == "operator-cli-override"


def test_submit_promote_to_paper_is_exposed_by_bench_bridge(
    tmp_path: Path,
) -> None:
    """The Bench bridge exposes promote-to-paper slots and reports the full
    wired GUI submit set."""
    from milodex.gui.bench_command_bridge import BenchCommandBridge

    members = {name for name, _ in inspect.getmembers(BenchCommandBridge, predicate=callable)}
    assert "proposePromoteToPaper" in members
    assert "submitPromoteToPaper" in members

    # Minimal facade against tmpdir for the introspection check.
    cfg = tmp_path / "configs"
    locks = tmp_path / "locks"
    cfg.mkdir()
    locks.mkdir()
    facade = BenchCommandFacade(config_dir=cfg, locks_dir=locks, get_trading_mode=lambda: "paper")
    bridge = BenchCommandBridge(facade)
    assert bridge.submitCapableActionFamilies() == [
        ACTION_FAMILY_DEMOTE,
        ACTION_FAMILY_FREEZE_MANIFEST,
        ACTION_FAMILY_BACKTEST,
        ACTION_FAMILY_PROMOTE_TO_PAPER,
        ACTION_FAMILY_START_PAPER_RUNNER,
        ACTION_FAMILY_STOP_PAPER_RUNNER,
    ]


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
                f"milodex.commands.bench must not import {forbidden!r} "
                f"(ADR 0051 §4 / §5). Offending line: {line!r}"
            )


def test_facade_module_does_not_import_cli_internals() -> None:
    """ADR 0051 §6: the facade must route through domain/governance callees, not CLI
    internals. Any ``milodex.cli.*`` import in bench.py is a layering inversion.
    This test pins the refactor that graduated ``_metrics_from_run``,
    ``_compute_post_update_hash``, and ``_ALLOWED_STAGES_BY_MODE`` to public
    ``milodex.promotion`` surfaces (PR refactor/bench-facade-layering).
    """
    source = Path(facade_module.__file__).read_text(encoding="utf-8")
    forbidden_cli_imports = (
        "from milodex.cli",
        "import milodex.cli",
    )
    for forbidden in forbidden_cli_imports:
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert not stripped.startswith(forbidden), (
                f"milodex.commands.bench must not import from milodex.cli.* "
                f"(ADR 0051 §6 layering rule). Offending line: {line!r}"
            )


# --------------------------------------------------------------------------- #
# `_submit_with_config` shell — shared spine for the four config-resolving
# submits (freeze, demote, promote_to_paper, backtest). These tests pin the
# shell's contract directly so any future migration sees regressions
# immediately rather than only through the public-API tests above.
# --------------------------------------------------------------------------- #


def _admissible_proposal(action_family: str) -> CommandProposal:
    """Build a minimal admissible proposal for shell tests.

    Admissibility is implicit on the dataclass (no blockers, all preconditions
    pass). The shell only checks `revalidation.admissible` — it doesn't inspect
    inputs/state_snapshot — so empty values are fine here.
    """
    return CommandProposal(
        action_family=action_family,
        strategy_id=STRATEGY_ID,
        inputs={},
        state_snapshot={},
        preconditions=[Precondition("ok", passed=True)],
        projected_outcome={},
        blockers=[],
        proposed_at=datetime(2026, 5, 24, 12, 0, 0),
        proposal_id="shell-test-revalidation",
    )


def _shell_dispatch_success(proposal, revalidation, config, event_store):  # noqa: ARG001
    """Stub dispatch that returns a generic submitted CommandResult."""
    return CommandResult(
        proposal_id=proposal.proposal_id,
        action_family=proposal.action_family,
        status="submitted",
        durable_refs={"shell": "dispatched"},
        blockers=[],
        warnings=[],
        submitted_at=datetime(2026, 5, 24, 12, 0, 1),
        audit_event_id="shell-test-audit",
    )


def test__submit_with_config_returns_action_family_mismatch_when_proposal_wrong_family(
    make_facade, config_dir: Path
) -> None:
    """Shell rejects a proposal whose action_family does not match `expected`
    with `proposal_action_family_mismatch`, BEFORE invoking revalidate or
    dispatch (so neither callable runs)."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = _admissible_proposal(ACTION_FAMILY_DEMOTE)  # wrong family

    calls = {"revalidate": 0, "dispatch": 0}

    def _record_revalidate():
        calls["revalidate"] += 1
        return _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST)

    def _record_dispatch(*args, **kwargs):  # noqa: ARG001
        calls["dispatch"] += 1
        return _shell_dispatch_success(proposal, None, None, None)

    result = facade._submit_with_config(
        proposal,
        expected_action_family=ACTION_FAMILY_FREEZE_MANIFEST,
        caller_method="submit_freeze_manifest",
        revalidate=_record_revalidate,
        dispatch=_record_dispatch,
    )

    assert result.status == "error"
    assert any(b.reason_code == "proposal_action_family_mismatch" for b in result.blockers)
    assert calls == {"revalidate": 0, "dispatch": 0}


def test__action_family_mismatch_message_names_the_calling_submit(
    make_facade, config_dir: Path
) -> None:
    """Per-method specificity in the mismatch error message — `caller_method`
    appears in the blocker text so an operator reading raw logs can tell
    which submit refused the proposal without cross-referencing context.

    Opus reviewer 2026-05-24 flagged a regression where PR #185 collapsed
    all three submits into a shared ``_submit_with_config`` shell and the
    message lost its per-method prefix (became ``"submit received..."``
    instead of ``"submit_freeze_manifest received..."`` etc).
    """
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = _admissible_proposal(ACTION_FAMILY_DEMOTE)  # wrong family

    result = facade._submit_with_config(
        proposal,
        expected_action_family=ACTION_FAMILY_FREEZE_MANIFEST,
        caller_method="submit_freeze_manifest",
        revalidate=lambda: _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST),
        dispatch=lambda *args, **kwargs: _shell_dispatch_success(proposal, None, None, None),
    )

    mismatch_blockers = [
        b for b in result.blockers if b.reason_code == "proposal_action_family_mismatch"
    ]
    assert len(mismatch_blockers) == 1
    assert "submit_freeze_manifest received" in mismatch_blockers[0].message, (
        f"caller_method must appear in message; got {mismatch_blockers[0].message!r}"
    )


def test__submit_with_config_requires_event_store_before_dispatch(
    make_facade, config_dir: Path
) -> None:
    """Shell short-circuits with `event_store_unavailable` when the facade was
    constructed without an event_store_factory, BEFORE invoking revalidate or
    dispatch."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade(with_event_store=False)
    proposal = _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST)

    calls = {"revalidate": 0, "dispatch": 0}

    def _record_revalidate():
        calls["revalidate"] += 1
        return _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST)

    def _record_dispatch(*args, **kwargs):  # noqa: ARG001
        calls["dispatch"] += 1
        return _shell_dispatch_success(proposal, None, None, None)

    result = facade._submit_with_config(
        proposal,
        expected_action_family=ACTION_FAMILY_FREEZE_MANIFEST,
        caller_method="submit_freeze_manifest",
        revalidate=_record_revalidate,
        dispatch=_record_dispatch,
    )

    assert result.status == "error"
    assert any(b.reason_code == "event_store_unavailable" for b in result.blockers)
    assert calls == {"revalidate": 0, "dispatch": 0}


def test__submit_with_config_returns_blocked_when_revalidation_is_inadmissible(
    make_facade, config_dir: Path
) -> None:
    """Shell returns a `blocked` CommandResult propagating the revalidation's
    blockers when revalidate() returns an inadmissible proposal. Dispatch is
    NOT called."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST)

    stale = CommandProposal(
        action_family=ACTION_FAMILY_FREEZE_MANIFEST,
        strategy_id=STRATEGY_ID,
        inputs={},
        state_snapshot={},
        preconditions=[],
        projected_outcome={},
        blockers=[Blocker(reason_code="stage_drift", message="stage changed", context={})],
        proposed_at=datetime(2026, 5, 24, 12, 0, 0),
        proposal_id="stale-rev",
    )

    dispatch_calls = []

    def _record_dispatch(*args, **kwargs):
        dispatch_calls.append((args, kwargs))
        return _shell_dispatch_success(proposal, None, None, None)

    result = facade._submit_with_config(
        proposal,
        expected_action_family=ACTION_FAMILY_FREEZE_MANIFEST,
        caller_method="submit_freeze_manifest",
        revalidate=lambda: stale,
        dispatch=_record_dispatch,
    )

    assert result.status == "blocked"
    assert any(b.reason_code == "stage_drift" for b in result.blockers)
    assert dispatch_calls == []


def test__submit_with_config_returns_blocked_when_resolve_config_fails(
    make_facade,
    config_dir: Path,  # noqa: ARG001  config_dir intentionally empty
) -> None:
    """Shell returns a `blocked` CommandResult with `strategy_not_found` when
    `_resolve_config` cannot locate the strategy's YAML. Dispatch is NOT
    called."""
    # Note: config_dir fixture is empty — no _write_strategy call.
    facade = make_facade()
    proposal = _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST)

    dispatch_calls = []

    def _record_dispatch(*args, **kwargs):
        dispatch_calls.append((args, kwargs))
        return _shell_dispatch_success(proposal, None, None, None)

    result = facade._submit_with_config(
        proposal,
        expected_action_family=ACTION_FAMILY_FREEZE_MANIFEST,
        caller_method="submit_freeze_manifest",
        revalidate=lambda: _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST),
        dispatch=_record_dispatch,
    )

    assert result.status == "blocked"
    assert any(b.reason_code == "strategy_not_found" for b in result.blockers)
    assert dispatch_calls == []


def test__submit_with_config_forwards_revalidation_to_dispatch(
    make_facade, config_dir: Path
) -> None:
    """Shell calls `dispatch(proposal, revalidation, config, event_store)` with
    the EXACT revalidation CommandProposal returned by revalidate() — not a
    fresh re-revalidation, not the original proposal. This is what lets demote
    derive post-dispatch warnings from revalidation.blockers without re-running
    propose_demote inside dispatch."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST)
    revalidation_result = _admissible_proposal(ACTION_FAMILY_FREEZE_MANIFEST)

    captured: dict[str, object] = {}

    def _record_dispatch(received_proposal, received_revalidation, received_config, received_store):
        captured["proposal"] = received_proposal
        captured["revalidation"] = received_revalidation
        captured["config"] = received_config
        captured["event_store"] = received_store
        return _shell_dispatch_success(
            received_proposal, received_revalidation, received_config, received_store
        )

    result = facade._submit_with_config(
        proposal,
        expected_action_family=ACTION_FAMILY_FREEZE_MANIFEST,
        caller_method="submit_freeze_manifest",
        revalidate=lambda: revalidation_result,
        dispatch=_record_dispatch,
    )

    assert result.status == "submitted"
    assert captured["proposal"] is proposal
    assert captured["revalidation"] is revalidation_result
    # config is the loaded StrategyConfig (non-None) and event_store is the live store
    assert captured["config"] is not None
    assert captured["event_store"] is not None


def test_submit_demote_revalidation_captures_gui_submit_kwarg(
    make_facade, config_dir: Path
) -> None:
    """H2 mitigation: the migrated `submit_demote` MUST capture `gui_submit`
    in its revalidation closure. The existing pre-migration code at
    bench.py:1635 does this; this test pins the convention so the migration
    cannot accidentally drop it.

    Verification strategy: with to_stage='disabled' the propose-side guard
    blocks ONLY when gui_submit=True (see propose_demote `gui_disabled_ok`
    check). If the migrated closure forgets to forward gui_submit, revalidation
    would default to False and the guard would not fire — letting the demote
    proceed. So gui_submit=True with to_stage='disabled' MUST end up blocked
    via revalidation's `disabled_demote_gui_ready` precondition.
    """
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    proposal = _make_demote_proposal(
        to_stage="disabled",
        reason="Stop trading and shelve via GUI.",
    )

    result = facade.submit_demote(proposal, gui_submit=True)

    # Revalidation must have run with gui_submit=True and the guard must have
    # produced a blocker — proves the closure forwarded the kwarg.
    assert result.status == "blocked"
    assert any(
        b.reason_code in {"disabled_demote_gui_not_ready", "stage_not_demotable"}
        or "disabled" in b.message.lower()
        for b in result.blockers
    )


# --------------------------------------------------------------------------- #
# `_resolve_config` contract — these tests pin the resolver's three exit
# shapes so PR C's swap to canonical `resolve_strategy_config_path` cannot
# silently regress: blank-string short-circuit, malformed-YAML guard on the
# matched file (defense-in-depth via monkeypatched helper), and unknown
# strategy_id fall-through.
# --------------------------------------------------------------------------- #


def test__resolve_config_blank_strategy_id_returns_strategy_id_blank(
    make_facade,
) -> None:
    """B2: blank or whitespace-only strategy_id short-circuits before any
    glob/load, returning a distinct `strategy_id_blank` reason code.

    The canonical `resolve_strategy_config_path` has no blank check; PR C's
    wrapper must preserve this precondition explicitly. Pins the distinct
    reason code so the wrapper cannot silently collapse it into
    `strategy_not_found`.
    """
    facade = make_facade()
    config, blocker = facade._resolve_config("")
    assert config is None
    assert blocker is not None
    assert blocker.reason_code == "strategy_id_blank"

    config, blocker = facade._resolve_config("   ")
    assert config is None
    assert blocker is not None
    assert blocker.reason_code == "strategy_id_blank"


def test__resolve_config_unknown_strategy_id_returns_strategy_not_found(
    make_facade, config_dir: Path
) -> None:
    """Regression cover for the PR C swap: unknown strategy_id (no matching
    YAML in config_dir) returns the `strategy_not_found` reason code,
    matching today's behavior."""
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()
    config, blocker = facade._resolve_config("nonexistent.strategy.v1")
    assert config is None
    assert blocker is not None
    assert blocker.reason_code == "strategy_not_found"


def test__resolve_config_malformed_matched_yaml_returns_strategy_config_invalid(
    make_facade, config_dir: Path, monkeypatch
) -> None:
    """B3 defense-in-depth: if `resolve_strategy_config_path` returns a path
    but `load_strategy_config(path)` raises on the matched file (race
    condition or future canonical-helper change), the wrapper MUST surface a
    structured `strategy_config_invalid` Blocker rather than letting the
    exception cross the facade boundary.

    This guards a failure mode that doesn't occur in normal operation today
    (the canonical helper already calls `load_strategy_config` successfully
    before returning a path), but the explicit guard protects against future
    canonical-helper changes and file-system races.

    Test strategy: monkeypatch the canonical helper to return a
    deliberately-nonexistent path; `load_strategy_config` then raises
    ValueError("Config file does not exist"), and the wrapper must catch
    and return `strategy_config_invalid`.
    """
    _write_strategy(config_dir, stage="paper")
    facade = make_facade()

    nonexistent_path = config_dir / "ghost-config.yaml"
    monkeypatch.setattr(
        facade_module,
        "resolve_strategy_config_path",
        lambda strategy_id, config_dir: nonexistent_path,  # noqa: ARG005
    )

    config, blocker = facade._resolve_config(STRATEGY_ID)
    assert config is None
    assert blocker is not None
    assert blocker.reason_code == "strategy_config_invalid"
    assert "ghost-config" in str(blocker.context.get("config_path", ""))


# ---------------------------------------------------------------------------
# HR-10: run_reconciliation_now — facade tests
# ---------------------------------------------------------------------------


class _FakeBroker:
    """Minimal broker stub whose snapshot returns zero positions/orders.

    HR-13 item 13: includes get_orders and is_market_open so
    run_reconciliation_now can reach a status="clean" result without raising
    AttributeError in the broker snapshot path.
    """

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def get_orders(self, status: str = "all", limit: int = 100):
        return []

    def is_market_open(self) -> bool:
        return False

    def get_account(self):
        return None

    @property
    def connected(self):
        return True


class _ErrorBroker:
    """Broker stub that raises on any call (simulates broker unreachable)."""

    def get_positions(self):
        raise RuntimeError("broker unreachable")

    def get_open_orders(self):
        raise RuntimeError("broker unreachable")

    def get_account(self):
        raise RuntimeError("broker unreachable")


class TestRunReconciliationNow:
    """BenchCommandFacade.run_reconciliation_now returns structured results (HR-10)."""

    def _facade_with_broker(
        self,
        config_dir: Path,
        locks_dir: Path,
        event_store: EventStore,
        broker_factory=None,
    ) -> BenchCommandFacade:
        return BenchCommandFacade(
            config_dir=config_dir,
            locks_dir=locks_dir,
            get_trading_mode=lambda: "paper",
            event_store_factory=lambda: event_store,
            broker_factory=broker_factory,
        )

    def test_returns_structured_result_keys(
        self, config_dir: Path, locks_dir: Path, event_store: EventStore
    ) -> None:
        """run_reconciliation_now always returns all required payload keys."""
        facade = self._facade_with_broker(
            config_dir, locks_dir, event_store, broker_factory=_FakeBroker
        )
        result = facade.run_reconciliation_now()
        for key in (
            "status",
            "clean",
            "mismatch_count",
            "trading_day",
            "run_id",
            "run_db_id",
            "recorded_at",
            "error",
        ):
            assert key in result, f"Expected key {key!r} in run_reconciliation_now result"

    def test_persists_reconciliation_run_row(
        self, config_dir: Path, locks_dir: Path, event_store: EventStore
    ) -> None:
        """run_reconciliation_now with persist=True writes a run row to the event store.

        HR-13 item 13: _FakeBroker now supplies get_orders + is_market_open so
        the broker snapshot succeeds and run_reconciliation can reach
        status='clean' (zero broker positions, zero local positions).
        """
        facade = self._facade_with_broker(
            config_dir, locks_dir, event_store, broker_factory=_FakeBroker
        )
        result = facade.run_reconciliation_now()
        assert result["status"] != "error", (
            f"Expected a non-error result; got status={result['status']!r}, "
            f"error={result['error']!r}"
        )
        # With an empty broker and an empty event store, reconciliation must be
        # clean (no mismatches).
        assert result["status"] == "clean", (
            f"Expected status='clean' with empty broker and empty store; got {result['status']!r}"
        )
        # The run row must be retrievable via the event store.
        latest = event_store.get_latest_reconciliation_run()
        assert latest is not None, "run_reconciliation_now must persist a reconciliation run row"
        assert result["run_id"] == latest.run_id

    def test_broker_factory_none_returns_structured_error(
        self, config_dir: Path, locks_dir: Path, event_store: EventStore
    ) -> None:
        """Without broker_factory, run_reconciliation_now returns status='error', never raises."""
        facade = self._facade_with_broker(config_dir, locks_dir, event_store, broker_factory=None)
        result = facade.run_reconciliation_now()
        assert result["status"] == "error"
        assert result["clean"] is False
        assert result["error"] != ""

    def test_event_store_factory_none_returns_structured_error(
        self, config_dir: Path, locks_dir: Path
    ) -> None:
        """Without event_store_factory, run_reconciliation_now returns status='error'."""
        facade = BenchCommandFacade(
            config_dir=config_dir,
            locks_dir=locks_dir,
            get_trading_mode=lambda: "paper",
            event_store_factory=None,
            broker_factory=_FakeBroker,
        )
        result = facade.run_reconciliation_now()
        assert result["status"] == "error"
        assert result["clean"] is False
        assert result["error"] != ""

    def test_broker_raises_returns_structured_error(
        self, config_dir: Path, locks_dir: Path, event_store: EventStore
    ) -> None:
        """When the broker factory raises, run_reconciliation_now returns status='error'."""

        def _raise():
            raise RuntimeError("network timeout")

        facade = self._facade_with_broker(config_dir, locks_dir, event_store, broker_factory=_raise)
        result = facade.run_reconciliation_now()
        assert result["status"] == "error"
        assert result["clean"] is False
        assert "network timeout" in result["error"] or result["error"] != ""


def test_gui_qml_files_still_forbid_submit_broker_eventstore() -> None:
    """ADR 0049 perimeter survives Bench command wiring.

    QML may use the command bridge, but the existing forbidden-token contract
    on direct Bench QML mutation paths must still hold.
    """
    qml_dir = Path(__file__).resolve().parents[3] / "src" / "milodex" / "gui" / "qml" / "Milodex"
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
                "(ADR 0049 perimeter; command wiring may not weaken this)."
            )
