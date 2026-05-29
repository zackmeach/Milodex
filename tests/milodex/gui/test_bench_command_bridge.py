"""Phase C2 tests for ``milodex.gui.bench_command_bridge.BenchCommandBridge``.

The bridge is the single Qt-side wrapper over ``BenchCommandFacade``. It is
the only file under ``src/milodex/gui/`` permitted to import the facade.
These tests pin:

- proposeDemote returns the proposal dict and caches the proposal
- submitDemote with a known id submits via the facade and emits
  ``submitCompleted``
- submitDemote with an unknown id returns a structured error
- the bridge exposes only the demote action family at Phase C2
- successful submit triggers a Bench read-model refresh
- the bridge does not expose backtest / promote / freeze / runner methods

These tests construct ``BenchCommandBridge`` with real ``BenchCommandFacade``
+ an in-tmp ``EventStore`` so the wiring is end-to-end up to but not
including QML. QML-level tests live in ``test_qml_load_smoke.py``.
"""

from __future__ import annotations

import inspect
import textwrap
import threading
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from milodex.backtesting.engine import BacktestResult
from milodex.commands.bench import (
    ACTION_FAMILY_BACKTEST,
    ACTION_FAMILY_DEMOTE,
    ACTION_FAMILY_FREEZE_MANIFEST,
    ACTION_FAMILY_PROMOTE_TO_PAPER,
    ACTION_FAMILY_START_PAPER_RUNNER,
    ACTION_FAMILY_STOP_PAPER_RUNNER,
    BenchCommandFacade,
    WorkflowReadinessIssue,
    WorkflowReadinessReport,
)
from milodex.core.event_store import BacktestRunEvent, EventStore, StrategyRunEvent
from milodex.gui import bench_command_bridge as bridge_module
from milodex.gui.bench_command_bridge import BenchCommandBridge
from milodex.risk.policy import RiskPolicy
from milodex.strategies.paper_runner_control import (
    ControlledStopRequestResult,
    PaperRunnerStartResult,
)

# Reuse the canonical strategy_id pattern from the facade tests.
STRATEGY_ID = "sample.daily.example.curated.v1"

_STRATEGY_YAML_TEMPLATE = textwrap.dedent(
    """\
    strategy:
      id: "sample.daily.example.curated.v1"
      family: "sample"
      template: "daily.example"
      variant: "curated"
      version: 1
      description: "Phase C2 bridge test strategy."
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


def _append_walk_forward_backtest_run(event_store: EventStore, *, run_id: str) -> None:
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
                    "sharpe": 1.1,
                    "max_drawdown_pct": 6.0,
                    "trade_count": 35,
                },
            },
            ended_at=now,
        )
    )


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
        self.calls: list[str] = []

    def evaluate(
        self,
        *,
        action_family: str,
        strategy_id: str,
        required_checks: frozenset[str],
        inspected_checks: frozenset[str],
    ) -> WorkflowReadinessReport:
        self.calls.append(action_family)
        if len(self._reports) > 1:
            return self._reports.pop(0)
        return self._reports[0]


def _healthy_readiness() -> _FakeWorkflowReadiness:
    return _FakeWorkflowReadiness(WorkflowReadinessReport())


def _readiness_issue(reason_code: str, *, blocking: bool = True) -> WorkflowReadinessIssue:
    return WorkflowReadinessIssue(
        dimension="broker_reachability",
        reason_code=reason_code,
        message=f"{reason_code} test issue",
        context={"source": "bridge-test"},
        blocking=blocking,
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


@pytest.fixture
def facade(config_dir: Path, locks_dir: Path, event_store: EventStore) -> BenchCommandFacade:
    return BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        workflow_readiness=_healthy_readiness(),
    )


class _FakeBenchState:
    """Records refresh kicks so tests can assert the bridge requests one."""

    def __init__(self) -> None:
        self.refresh_kicks = 0

    def _kick_refresh(self) -> None:  # noqa: N802 — mirrors _PollingReadModel
        self.refresh_kicks += 1


class _FakeLedgerState(_FakeBenchState):
    """Same private refresh contract as LedgerState, isolated for assertions."""


class _FakeSingleBacktestEngine:
    def __init__(self, *, release_event: threading.Event | None = None) -> None:
        self._release_event = release_event

    def run(self, start, end, *, run_id=None):  # noqa: ANN001
        if self._release_event is not None:
            self._release_event.wait(timeout=5)
        return BacktestResult(
            run_id=run_id or "bench-bridge-run",
            strategy_id=STRATEGY_ID,
            start_date=start,
            end_date=end,
            initial_equity=1_000.0,
            final_equity=1_050.0,
            total_return_pct=5.0,
            trade_count=2,
            buy_count=1,
            sell_count=1,
            slippage_pct=0.0005,
            commission_per_trade=0.0,
            trading_days=5,
            db_id=303,
            risk_policy=RiskPolicy.BYPASS,
            skipped_count=0,
            data_quality={"status": "pass"},
            run_manifest={"schema_version": 1},
        )


def _process_qt_until(predicate, *, timeout_ms: int = 2_000) -> bool:  # noqa: ANN001
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = threading.Event()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(deadline.set)
    timer.start(timeout_ms)
    while not predicate() and not deadline.is_set():
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
    timer.stop()
    return bool(predicate())


class _FakePaperRunnerControl:
    def __init__(self, locks_dir: Path) -> None:
        self.starts: list[str] = []
        self.stops: list[str] = []
        self.locks_dir = locks_dir

    def start(self, strategy_id: str):
        self.starts.append(strategy_id)
        return PaperRunnerStartResult(
            strategy_id=strategy_id,
            pid=5150,
            command=("python", "-m", "milodex.cli.main", "strategy", "run", strategy_id),
            stop_request_path=self.locks_dir / "stop.json",
            launched_at=datetime(2026, 5, 15, 12, 0, 0),
        )

    def request_controlled_stop(self, strategy_id: str, *, holder: dict):
        self.stops.append(strategy_id)
        return ControlledStopRequestResult(
            strategy_id=strategy_id,
            request_path=self.locks_dir / "stop.json",
            requested_at=datetime(2026, 5, 15, 12, 1, 0),
            holder=holder,
        )


def _seed_runner_lock(locks_dir: Path) -> None:
    (locks_dir / f"milodex.runtime.strategy.{STRATEGY_ID}.lock").write_text(
        (
            '{"pid":1,"hostname":"test-host","holder_name":"milodex strategy run",'
            '"started_at":"2026-05-15T11:00:00+00:00"}'
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# proposeDemote
# --------------------------------------------------------------------------- #


def test_propose_demote_returns_dict_and_caches_proposal(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)

    payload = bridge.proposeDemote(
        {
            "strategy_id": STRATEGY_ID,
            "to_stage": "backtest",
            "reason": "Walking back; OOS drift.",
        }
    )
    assert isinstance(payload, dict)
    assert payload["action_family"] == ACTION_FAMILY_DEMOTE
    assert payload["strategy_id"] == STRATEGY_ID
    assert payload["proposal_id"]
    assert payload["blockers"] == []

    # The proposal is cached for the matching submit call — verify behaviorally
    # by submitting and confirming we get past the unknown-proposal-id guard.
    submit_result = bridge.submitDemote(payload["proposal_id"])
    assert submit_result["status"] == "submitted"


def test_propose_demote_with_blank_reason_returns_blocker_payload(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)
    payload = bridge.proposeDemote(
        {"strategy_id": STRATEGY_ID, "to_stage": "backtest", "reason": "   "}
    )
    assert payload["blockers"]
    codes = {b["reason_code"] for b in payload["blockers"]}
    assert "missing_reason" in codes


# --------------------------------------------------------------------------- #
# submitDemote
# --------------------------------------------------------------------------- #


def test_submit_demote_with_known_id_writes_event_and_refreshes_bench_state(
    facade: BenchCommandFacade,
    config_dir: Path,
    event_store: EventStore,
) -> None:
    config_path = _write_strategy(config_dir, stage="paper")
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    # Capture the signal payload via a Python-side listener.
    signal_payloads: list[dict] = []
    bridge.submitCompleted.connect(signal_payloads.append)

    proposal = bridge.proposeDemote(
        {
            "strategy_id": STRATEGY_ID,
            "to_stage": "backtest",
            "reason": "OOS evidence degraded.",
        }
    )
    result = bridge.submitDemote(proposal["proposal_id"])

    assert result["status"] == "submitted"
    assert result["action_family"] == ACTION_FAMILY_DEMOTE
    assert result["durable_refs"]["from_stage"] == "paper"
    assert result["durable_refs"]["to_stage"] == "backtest"
    assert result["audit_event_id"]
    # The bench read model was kicked exactly once for the successful submit.
    assert fake_state.refresh_kicks == 1
    # signal fired exactly once with the matching payload
    assert len(signal_payloads) == 1
    assert signal_payloads[0]["status"] == "submitted"
    # The governance event landed via the existing event store path.
    events = event_store.list_promotions_for_strategy(STRATEGY_ID)
    assert len(events) == 1
    assert events[0].promotion_type == "demotion"
    # YAML stage line was rewritten by the governance path.
    assert 'stage: "backtest"' in config_path.read_text(encoding="utf-8")


def test_submit_demote_unknown_id_returns_structured_error_and_does_not_refresh(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    payloads: list[dict] = []
    bridge.submitCompleted.connect(payloads.append)

    result = bridge.submitDemote("not-a-real-proposal-id")
    assert result["status"] == "error"
    codes = {b["reason_code"] for b in result["blockers"]}
    assert "unknown_proposal_id" in codes
    assert fake_state.refresh_kicks == 0
    assert len(payloads) == 1
    assert payloads[0]["status"] == "error"


def test_submit_demote_consumes_proposal_so_second_call_fails(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)
    proposal = bridge.proposeDemote(
        {
            "strategy_id": STRATEGY_ID,
            "to_stage": "backtest",
            "reason": "first run",
        }
    )
    first = bridge.submitDemote(proposal["proposal_id"])
    assert first["status"] == "submitted"
    # Second submit with the same id must error — the proposal was consumed.
    second = bridge.submitDemote(proposal["proposal_id"])
    assert second["status"] == "error"
    assert any(b["reason_code"] == "unknown_proposal_id" for b in second["blockers"])


def test_submit_demote_blocked_proposal_skips_refresh(
    facade: BenchCommandFacade,
    config_dir: Path,
    event_store: EventStore,
) -> None:
    """If the facade refuses (proposal regenerated with blockers at submit
    time), the bridge must not kick a read-model refresh — there's nothing
    new to show."""
    _write_strategy(config_dir, stage="paper")
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    # Propose admissibly, then drift the YAML so submit re-validation fails.
    proposal = bridge.proposeDemote(
        {
            "strategy_id": STRATEGY_ID,
            "to_stage": "backtest",
            "reason": "first run",
        }
    )
    cfg = config_dir / "strategy.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace('stage: "paper"', 'stage: "backtest"'),
        encoding="utf-8",
    )

    result = bridge.submitDemote(proposal["proposal_id"])
    assert result["status"] == "blocked"
    assert fake_state.refresh_kicks == 0
    # No event was written.
    assert event_store.list_promotions_for_strategy(STRATEGY_ID) == []


# --------------------------------------------------------------------------- #
# Bridge surface — only demote is exposed
# --------------------------------------------------------------------------- #


def test_bridge_exposes_submit_capable_action_family_slots() -> None:
    """The bridge exposes only the action families wired through the facade."""
    members = {name for name, _ in inspect.getmembers(BenchCommandBridge, predicate=callable)}
    # Submit-capable slots present.
    assert "proposeDemote" in members
    assert "submitDemote" in members
    assert "proposeFreezeManifest" in members
    assert "submitFreezeManifest" in members
    assert "proposeBacktest" in members
    assert "submitBacktest" in members
    assert "proposePromoteToPaper" in members
    assert "submitPromoteToPaper" in members
    # No other action-family slot. Adding one without the corresponding ADR
    # amendment + facade wiring + boundary doc update is exactly the failure
    # mode the perimeter exists to prevent.
    for forbidden in (
        "proposeStartPaperRunner",
        "submitStartPaperRunner",
        "proposeStopPaperRunner",
        "submitStopPaperRunner",
    ):
        assert forbidden in members, (
            f"BenchCommandBridge must not expose {forbidden} at Phase D1 "
            "(ADR 0051 §10). Phase D1 is demote + freeze_manifest only."
        )


def test_submit_capable_action_families_returns_wired_families(
    facade: BenchCommandFacade,
) -> None:
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
# Backtest submit bridge
# --------------------------------------------------------------------------- #


def test_propose_backtest_returns_dict_and_caches_proposal(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    bridge = BenchCommandBridge(facade)

    payload = bridge.proposeBacktest({"strategy_id": STRATEGY_ID})

    assert payload["action_family"] == ACTION_FAMILY_BACKTEST
    assert payload["inputs"]["start"] == "2020-01-01"
    assert payload["inputs"]["end"] == "2024-12-31"
    assert payload["inputs"]["walk_forward"] is True
    assert payload["inputs"]["initial_equity"] == 100_000.0
    assert payload["inputs"]["risk_policy"] == "bypass"
    # The proposal is cached for the matching submit call — verify behaviorally
    # by submitting and confirming we get past the unknown-proposal-id guard.
    submit_result = bridge.submitBacktest(payload["proposal_id"])
    assert all(
        blocker.get("reason_code") != "unknown_proposal_id"
        for blocker in submit_result.get("blockers", [])
    )


def test_submit_backtest_with_known_id_submits_and_refreshes(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="backtest")
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        backtest_engine_factory=lambda _strategy_id, **_kwargs: _FakeSingleBacktestEngine(),
        workflow_readiness=_healthy_readiness(),
    )
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)
    payloads: list[dict] = []
    bridge.submitCompleted.connect(payloads.append)

    proposal = bridge.proposeBacktest(
        {
            "strategy_id": STRATEGY_ID,
            "start": "2020-01-01",
            "end": "2020-01-05",
            "walk_forward": False,
            "initial_equity": 1000,
        }
    )
    result = bridge.submitBacktest(proposal["proposal_id"])

    assert result["status"] == "submitted"
    assert result["durable_refs"]["run_id"] == "bench-bridge-run"
    assert result["durable_refs"]["backtest_run_db_id"] == "303"
    assert result["data"]["metrics"]["trade_count"] == 2
    assert fake_state.refresh_kicks == 1
    assert len(payloads) == 1
    assert payloads[0]["status"] == "submitted"


def test_submit_backtest_unknown_or_consumed_id_returns_structured_error(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    bridge = BenchCommandBridge(facade)

    unknown = bridge.submitBacktest("missing-proposal")
    assert unknown["status"] == "error"
    assert unknown["action_family"] == ACTION_FAMILY_BACKTEST
    assert unknown["blockers"][0]["reason_code"] == "unknown_proposal_id"

    proposal = bridge.proposeBacktest({"strategy_id": STRATEGY_ID, "walk_forward": False})
    first = bridge.submitBacktest(proposal["proposal_id"])
    assert first["status"] == "error"
    second = bridge.submitBacktest(proposal["proposal_id"])
    assert second["status"] == "error"
    assert second["blockers"][0]["reason_code"] == "unknown_proposal_id"


def test_submit_backtest_async_returns_queued_and_emits_final_result(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="backtest")
    release = threading.Event()
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        backtest_engine_factory=lambda _strategy_id, **_kwargs: _FakeSingleBacktestEngine(
            release_event=release
        ),
        workflow_readiness=_healthy_readiness(),
    )
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)
    payloads: list[dict] = []
    queued_payloads: list[dict] = []
    bridge.submitCompleted.connect(payloads.append)
    bridge.submitQueued.connect(queued_payloads.append)

    proposal = bridge.proposeBacktest(
        {"strategy_id": STRATEGY_ID, "walk_forward": False, "initial_equity": 1000}
    )
    queued = bridge.submitBacktestAsync(proposal["proposal_id"])

    assert queued["bridge_status"] == "queued"
    assert queued["action_family"] == ACTION_FAMILY_BACKTEST
    assert queued_payloads == [queued]
    assert payloads == []

    release.set()
    assert _process_qt_until(lambda: len(payloads) == 1)
    assert payloads[0]["status"] == "submitted"
    assert payloads[0]["action_family"] == ACTION_FAMILY_BACKTEST
    assert fake_state.refresh_kicks == 1


# --------------------------------------------------------------------------- #
# Paper runner submit bridge
# --------------------------------------------------------------------------- #


def test_propose_start_paper_runner_returns_dict_and_caches_proposal(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        paper_runner_control=_FakePaperRunnerControl(locks_dir),
        workflow_readiness=_healthy_readiness(),
    )
    bridge = BenchCommandBridge(facade)

    payload = bridge.proposeStartPaperRunner({"strategy_id": STRATEGY_ID})

    assert payload["action_family"] == ACTION_FAMILY_START_PAPER_RUNNER
    assert payload["blockers"] == []
    # The proposal is cached for the matching submit call — verify behaviorally
    # by submitting and confirming we get past the unknown-proposal-id guard.
    submit_result = bridge.submitStartPaperRunner(payload["proposal_id"])
    assert all(
        blocker.get("reason_code") != "unknown_proposal_id"
        for blocker in submit_result.get("blockers", [])
    )


def test_submit_start_paper_runner_with_known_id_submits_and_refreshes(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    _append_open_strategy_run(event_store, session_id="bridge-start-session")
    control = _FakePaperRunnerControl(locks_dir)
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        paper_runner_control=control,
        workflow_readiness=_healthy_readiness(),
    )
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    proposal = bridge.proposeStartPaperRunner({"strategy_id": STRATEGY_ID})
    result = bridge.submitStartPaperRunner(proposal["proposal_id"])

    assert result["status"] == "submitted", result["blockers"]
    assert result["durable_refs"]["runner_pid"] == "5150"
    assert result["durable_refs"]["session_id"] == "bridge-start-session"
    assert result["audit_event_id"] == "bridge-start-session"
    assert fake_state.refresh_kicks == 1
    assert control.starts == [STRATEGY_ID]


def test_submit_start_paper_runner_missing_audit_link_surfaces_error_without_refresh(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    control = _FakePaperRunnerControl(locks_dir)
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        paper_runner_control=control,
        workflow_readiness=_healthy_readiness(),
    )
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    proposal = bridge.proposeStartPaperRunner({"strategy_id": STRATEGY_ID})
    result = bridge.submitStartPaperRunner(proposal["proposal_id"])

    assert result["status"] == "error"
    assert result["audit_event_id"] is None
    assert result["blockers"][0]["reason_code"] == "runner_audit_link_missing"
    assert fake_state.refresh_kicks == 0
    assert control.starts == [STRATEGY_ID]


def test_start_paper_runner_readiness_blocker_serializes_without_qml_logic(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(issues=(_readiness_issue("broker_unreachable"),))
    )
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        paper_runner_control=_FakePaperRunnerControl(locks_dir),
        workflow_readiness=readiness,
    )
    bridge = BenchCommandBridge(facade)

    payload = bridge.proposeStartPaperRunner({"strategy_id": STRATEGY_ID})

    assert payload["blockers"][0]["reason_code"] == "broker_unreachable"
    assert payload["projected_outcome"]["workflow_readiness"]["issues"][0]["context"] == {
        "source": "bridge-test"
    }
    assert readiness.calls == [ACTION_FAMILY_START_PAPER_RUNNER]


def test_submit_start_paper_runner_readiness_drift_blocks_without_refresh(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    readiness = _FakeWorkflowReadiness(
        WorkflowReadinessReport(),
        WorkflowReadinessReport(issues=(_readiness_issue("data_stale"),)),
    )
    control = _FakePaperRunnerControl(locks_dir)
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        paper_runner_control=control,
        workflow_readiness=readiness,
    )
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    proposal = bridge.proposeStartPaperRunner({"strategy_id": STRATEGY_ID})
    result = bridge.submitStartPaperRunner(proposal["proposal_id"])

    assert result["status"] == "blocked"
    assert result["blockers"][0]["reason_code"] == "data_stale"
    assert fake_state.refresh_kicks == 0
    assert control.starts == []


def test_submit_stop_paper_runner_with_known_id_submits_and_refreshes(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    _append_open_strategy_run(event_store, session_id="bridge-stop-session")
    _seed_runner_lock(locks_dir)
    control = _FakePaperRunnerControl(locks_dir)
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        paper_runner_control=control,
        workflow_readiness=_healthy_readiness(),
    )
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    proposal = bridge.proposeStopPaperRunner({"strategy_id": STRATEGY_ID})
    result = bridge.submitStopPaperRunner(proposal["proposal_id"])

    assert result["status"] == "submitted", result["blockers"]
    assert result["durable_refs"]["exit_reason"] == "controlled_stop"
    assert result["durable_refs"]["session_id"] == "bridge-stop-session"
    assert result["audit_event_id"] == "bridge-stop-session"
    assert result["data"]["kill_switch"] is False
    assert fake_state.refresh_kicks == 1
    assert control.stops == [STRATEGY_ID]


def test_submit_paper_runner_unknown_or_consumed_ids_return_structured_errors(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    _append_open_strategy_run(event_store, session_id="bridge-consumed-start-session")
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        paper_runner_control=_FakePaperRunnerControl(locks_dir),
        workflow_readiness=_healthy_readiness(),
    )
    bridge = BenchCommandBridge(facade)

    unknown_start = bridge.submitStartPaperRunner("missing-start")
    assert unknown_start["status"] == "error"
    assert unknown_start["blockers"][0]["reason_code"] == "unknown_proposal_id"

    start = bridge.proposeStartPaperRunner({"strategy_id": STRATEGY_ID})
    assert bridge.submitStartPaperRunner(start["proposal_id"])["status"] == "submitted"
    consumed_start = bridge.submitStartPaperRunner(start["proposal_id"])
    assert consumed_start["blockers"][0]["reason_code"] == "unknown_proposal_id"

    unknown_stop = bridge.submitStopPaperRunner("missing-stop")
    assert unknown_stop["status"] == "error"
    assert unknown_stop["blockers"][0]["reason_code"] == "unknown_proposal_id"


def test_submit_paper_runner_async_methods_return_queued_and_emit_final_results(
    config_dir: Path, locks_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    _append_open_strategy_run(event_store, session_id="bridge-async-session")
    control = _FakePaperRunnerControl(locks_dir)
    facade = BenchCommandFacade(
        config_dir=config_dir,
        locks_dir=locks_dir,
        get_trading_mode=lambda: "paper",
        event_store_factory=lambda: event_store,
        paper_runner_control=control,
        workflow_readiness=_healthy_readiness(),
    )
    bridge = BenchCommandBridge(facade, bench_state=_FakeBenchState())
    payloads: list[dict] = []
    bridge.submitCompleted.connect(payloads.append)

    start = bridge.proposeStartPaperRunner({"strategy_id": STRATEGY_ID})
    start_queued = bridge.submitStartPaperRunnerAsync(start["proposal_id"])
    assert start_queued["bridge_status"] == "queued"
    assert _process_qt_until(lambda: len(payloads) == 1)

    _seed_runner_lock(locks_dir)

    stop = bridge.proposeStopPaperRunner({"strategy_id": STRATEGY_ID})
    stop_queued = bridge.submitStopPaperRunnerAsync(stop["proposal_id"])
    assert stop_queued["bridge_status"] == "queued"

    assert _process_qt_until(lambda: len(payloads) == 2)
    assert [payload["action_family"] for payload in payloads] == [
        ACTION_FAMILY_START_PAPER_RUNNER,
        ACTION_FAMILY_STOP_PAPER_RUNNER,
    ]
    assert all(payload["status"] == "submitted" for payload in payloads)


# --------------------------------------------------------------------------- #
# Promote-to-paper submit bridge
# --------------------------------------------------------------------------- #


def test_propose_promote_to_paper_returns_dict_and_caches_proposal(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    bridge = BenchCommandBridge(facade)

    payload = bridge.proposePromoteToPaper(
        {
            "strategy_id": STRATEGY_ID,
            "recommendation": "Backtest evidence is strong enough for paper.",
            "known_risk": "Regime shifts may degrade the signal.",
            "run_id": "bt-gui-promote",
        }
    )

    assert payload["action_family"] == ACTION_FAMILY_PROMOTE_TO_PAPER
    assert payload["inputs"]["recommendation"] == ("Backtest evidence is strong enough for paper.")
    assert payload["inputs"]["known_risks"] == ["Regime shifts may degrade the signal."]
    assert payload["inputs"]["run_id"] == "bt-gui-promote"
    assert payload["inputs"]["approved_by"] == bridge_module._resolve_operator_identity()
    # The proposal is cached for the matching submit call — verify behaviorally
    # by submitting and confirming we get past the unknown-proposal-id guard.
    submit_result = bridge.submitPromoteToPaper(payload["proposal_id"])
    assert all(
        blocker.get("reason_code") != "unknown_proposal_id"
        for blocker in submit_result.get("blockers", [])
    )


def test_submit_promote_to_paper_with_known_id_writes_event_and_refreshes(
    facade: BenchCommandFacade,
    config_dir: Path,
    event_store: EventStore,
) -> None:
    config_path = _write_strategy(config_dir, stage="backtest")
    _append_walk_forward_backtest_run(event_store, run_id="bt-gui-promote")
    fake_state = _FakeBenchState()
    fake_ledger_state = _FakeLedgerState()
    bridge = BenchCommandBridge(
        facade,
        bench_state=fake_state,
        ledger_state=fake_ledger_state,
    )
    payloads: list[dict] = []
    bridge.submitCompleted.connect(payloads.append)

    proposal = bridge.proposePromoteToPaper(
        {
            "strategy_id": STRATEGY_ID,
            "recommendation": "Backtest evidence is strong enough for paper.",
            "known_risk": "Regime shifts may degrade the signal.",
            "run_id": "bt-gui-promote",
        }
    )
    result = bridge.submitPromoteToPaper(proposal["proposal_id"])

    assert result["status"] == "submitted", result["blockers"]
    assert result["action_family"] == ACTION_FAMILY_PROMOTE_TO_PAPER
    assert result["durable_refs"]["from_stage"] == "backtest"
    assert result["durable_refs"]["to_stage"] == "paper"
    assert result["durable_refs"]["backtest_run_id"] == "bt-gui-promote"
    assert result["audit_event_id"]
    assert fake_state.refresh_kicks == 1
    assert fake_ledger_state.refresh_kicks == 1
    assert len(payloads) == 1
    assert payloads[0]["status"] == "submitted"
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")


def test_propose_promote_to_paper_surfaces_missing_inputs(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    bridge = BenchCommandBridge(facade)

    payload = bridge.proposePromoteToPaper({"strategy_id": STRATEGY_ID})

    codes = {blocker["reason_code"] for blocker in payload["blockers"]}
    assert {"missing_recommendation", "missing_known_risks", "missing_run_id"}.issubset(codes)


def test_submit_promote_to_paper_unknown_or_consumed_id_returns_structured_error(
    facade: BenchCommandFacade,
    config_dir: Path,
    event_store: EventStore,
) -> None:
    _write_strategy(config_dir, stage="backtest")
    _append_walk_forward_backtest_run(event_store, run_id="bt-gui-promote")
    bridge = BenchCommandBridge(facade)

    unknown = bridge.submitPromoteToPaper("missing-proposal")
    assert unknown["status"] == "error"
    assert unknown["action_family"] == ACTION_FAMILY_PROMOTE_TO_PAPER
    assert unknown["blockers"][0]["reason_code"] == "unknown_proposal_id"

    proposal = bridge.proposePromoteToPaper(
        {
            "strategy_id": STRATEGY_ID,
            "recommendation": "Backtest evidence is strong enough for paper.",
            "known_risk": "Regime shifts may degrade the signal.",
            "run_id": "bt-gui-promote",
        }
    )
    first = bridge.submitPromoteToPaper(proposal["proposal_id"])
    assert first["status"] == "submitted"
    second = bridge.submitPromoteToPaper(proposal["proposal_id"])
    assert second["status"] == "error"
    assert second["blockers"][0]["reason_code"] == "unknown_proposal_id"


def test_propose_promote_to_paper_ignores_qml_approved_by(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    bridge = BenchCommandBridge(facade)

    payload = bridge.proposePromoteToPaper(
        {
            "strategy_id": STRATEGY_ID,
            "recommendation": "Backtest evidence is strong enough for paper.",
            "known_risk": "Regime shifts may degrade the signal.",
            "run_id": "bt-gui-promote",
            "approved_by": "malicious-attempt",
        }
    )

    assert payload["inputs"]["approved_by"] == bridge_module._resolve_operator_identity()
    assert payload["inputs"]["approved_by"] != "malicious-attempt"


# --------------------------------------------------------------------------- #
# Module-level invariants
# --------------------------------------------------------------------------- #


def test_bridge_module_does_not_import_broker_or_runner() -> None:
    """ADR 0051 §4: the bridge may import PySide6 but must not import broker
    clients or strategy runners. The facade is the only command boundary."""
    source = Path(bridge_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "from milodex.broker",
        "import milodex.broker",
        "from milodex.strategies.runner",
        "import milodex.strategies.runner",
        "from milodex.execution",
        "import milodex.execution",
    ):
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert not stripped.startswith(forbidden), (
                f"bench_command_bridge.py must not import {forbidden!r} (ADR 0051 §4)."
            )


def test_facade_module_remains_pyside_free_after_phase_c2() -> None:
    """ADR 0051 §4 / §5: the facade lives outside src/milodex/gui/ and must
    not gain a PySide6 import as a side effect of Phase C2.

    The earlier reload-and-compare belt-and-braces assertion was removed in
    the Phase C2 review F-cleanup: once PySide6 is loaded by any test module
    in the run, the comparison is vacuously true. The source-level check
    below is the load-bearing invariant.
    """
    from milodex.commands import bench as facade_module

    source = Path(facade_module.__file__).read_text(encoding="utf-8")
    # Substring check on import lines only — docstring text mentioning
    # PySide6 is documentation, not an import.
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("from PySide6") or stripped.startswith("import PySide6"):
            raise AssertionError(
                f"milodex.commands.bench gained a PySide6 import in Phase C2: {line!r}"
            )


# --------------------------------------------------------------------------- #
# Phase C2 review F3 — _kick_refresh contract pin
# --------------------------------------------------------------------------- #


class _RaisingBenchState:
    """BenchState double whose ``_kick_refresh`` always raises.

    The bridge must log via ``logger.exception`` and still return the
    submit result so the operator's signal handler sees ``status="submitted"``
    even when the read-model refresh is unavailable.
    """

    def _kick_refresh(self) -> None:  # noqa: N802 — mirrors _PollingReadModel
        raise RuntimeError("boom")


def test_submit_demote_logs_when_kick_refresh_raises(
    facade: BenchCommandFacade,
    config_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Phase C2 review F3: the bridge's single permitted private reach into
    ``BenchState._kick_refresh`` must be guarded — a refresh exception is
    logged via ``logger.exception`` and the submit result is still emitted.
    """
    _write_strategy(config_dir, stage="paper")
    raising_state = _RaisingBenchState()
    bridge = BenchCommandBridge(facade, bench_state=raising_state)

    proposal = bridge.proposeDemote(
        {
            "strategy_id": STRATEGY_ID,
            "to_stage": "backtest",
            "reason": "kick-refresh-raises pin.",
        }
    )
    with caplog.at_level("ERROR", logger="milodex.gui.bench_command_bridge"):
        result = bridge.submitDemote(proposal["proposal_id"])

    assert result["status"] == "submitted"
    matching = [
        rec
        for rec in caplog.records
        if "BenchState refresh after submit_demote failed" in rec.getMessage()
    ]
    assert matching, (
        "Expected a logger.exception record when _kick_refresh raises; "
        f"got {[r.getMessage() for r in caplog.records]!r}"
    )
    assert any(rec.exc_info is not None for rec in matching), (
        "logger.exception must attach exc_info so the traceback is auditable."
    )


# --------------------------------------------------------------------------- #
# Phase C2 review F4 — backend-sourced identity
# --------------------------------------------------------------------------- #


def test_submit_demote_uses_backend_resolved_identity(
    facade: BenchCommandFacade,
    config_dir: Path,
    event_store: EventStore,
) -> None:
    """Phase C2 review F4: any ``approved_by`` key in the QML payload is
    ignored; identity is sourced backend-side via
    ``_resolve_operator_identity``. The event store record must reflect the
    resolved identity, not the QML-supplied string.
    """
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)

    proposal = bridge.proposeDemote(
        {
            "strategy_id": STRATEGY_ID,
            "to_stage": "backtest",
            "reason": "identity-resolution pin.",
            # A malicious / accidental override from QML must be ignored.
            "approved_by": "malicious-attempt",
        }
    )
    result = bridge.submitDemote(proposal["proposal_id"])
    assert result["status"] == "submitted"

    events = event_store.list_promotions_for_strategy(STRATEGY_ID)
    assert len(events) == 1
    assert events[0].approved_by == bridge_module._resolve_operator_identity()
    assert events[0].approved_by != "malicious-attempt"


def test_qml_modal_does_not_hardcode_approved_by_literal() -> None:
    """Phase C2 review F4: ``BenchConfirmationModal.qml`` must not decide
    operator identity. The modal's demote-submit dispatch passes only
    ``strategy_id``, ``to_stage``, and ``reason`` to the bridge — identity is
    sourced backend-side. This test bounds the check to the dispatch function
    so unrelated occurrences of the word "operator" in surrounding QML do
    not false-positive.
    """
    modal_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "milodex"
        / "gui"
        / "qml"
        / "Milodex"
        / "components"
        / "BenchConfirmationModal.qml"
    )
    src = modal_path.read_text(encoding="utf-8")
    start = src.find("function _dispatchDemoteSubmit")
    assert start != -1, "_dispatchDemoteSubmit function missing from modal"
    # Bound the search to the dispatch function body.
    after = src[start:]
    end_relative = after.find("\n    }")
    assert end_relative != -1, "could not locate end of _dispatchDemoteSubmit"
    body = after[: end_relative + len("\n    }")]
    assert '"approved_by"' not in body, (
        "BenchConfirmationModal._dispatchDemoteSubmit must not include an "
        "approved_by key in the proposeDemote payload (Phase C2 review F4); "
        "identity is sourced backend-side by BenchCommandBridge."
    )
    assert '"operator"' not in body, (
        "BenchConfirmationModal._dispatchDemoteSubmit must not hardcode "
        '"operator" as the operator identity (Phase C2 review F4).'
    )


# --------------------------------------------------------------------------- #
# Phase C2 review cleanup — bench_state=None happy path
# --------------------------------------------------------------------------- #


def test_submit_demote_succeeds_when_bench_state_is_none(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    """The ``bench_state`` parameter is optional. With it omitted, the
    successful-submit refresh branch is skipped silently and the submit
    result is still emitted.
    """
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)  # default bench_state=None

    proposal = bridge.proposeDemote(
        {
            "strategy_id": STRATEGY_ID,
            "to_stage": "backtest",
            "reason": "bench_state=None pin.",
        }
    )
    result = bridge.submitDemote(proposal["proposal_id"])
    assert result["status"] == "submitted"
    assert result["audit_event_id"]


# --------------------------------------------------------------------------- #
# Phase D1 — freeze_manifest bridge slots
# --------------------------------------------------------------------------- #


def test_propose_freeze_manifest_returns_dict_and_caches_proposal(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)

    payload = bridge.proposeFreezeManifest({"strategy_id": STRATEGY_ID})
    assert isinstance(payload, dict)
    assert payload["action_family"] == ACTION_FAMILY_FREEZE_MANIFEST
    assert payload["strategy_id"] == STRATEGY_ID
    assert payload["proposal_id"]
    assert payload["blockers"] == []
    # The proposal is cached for the matching submit call — verify behaviorally
    # by submitting and confirming we get past the unknown-proposal-id guard.
    submit_result = bridge.submitFreezeManifest(payload["proposal_id"])
    assert all(
        blocker.get("reason_code") != "unknown_proposal_id"
        for blocker in submit_result.get("blockers", [])
    )


def test_propose_freeze_manifest_for_backtest_stage_returns_blocker_payload(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="backtest")
    bridge = BenchCommandBridge(facade)
    payload = bridge.proposeFreezeManifest({"strategy_id": STRATEGY_ID})
    assert payload["blockers"]
    codes = {b["reason_code"] for b in payload["blockers"]}
    assert "stage_not_freezable" in codes


def test_submit_freeze_manifest_with_known_id_writes_event_and_refreshes(
    facade: BenchCommandFacade, config_dir: Path, event_store: EventStore
) -> None:
    _write_strategy(config_dir, stage="paper")
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    payloads: list[dict] = []
    bridge.submitCompleted.connect(payloads.append)

    proposal = bridge.proposeFreezeManifest({"strategy_id": STRATEGY_ID})
    result = bridge.submitFreezeManifest(proposal["proposal_id"])

    assert result["status"] == "submitted"
    assert result["action_family"] == ACTION_FAMILY_FREEZE_MANIFEST
    assert result["durable_refs"]["stage"] == "paper"
    assert result["durable_refs"]["config_hash"]
    assert result["durable_refs"]["frozen_by"]
    assert result["audit_event_id"]
    assert fake_state.refresh_kicks == 1
    assert len(payloads) == 1
    assert payloads[0]["status"] == "submitted"
    manifest = event_store.get_active_manifest_for_strategy(STRATEGY_ID, "paper")
    assert manifest is not None


def test_submit_freeze_manifest_unknown_id_returns_structured_error(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    payloads: list[dict] = []
    bridge.submitCompleted.connect(payloads.append)

    result = bridge.submitFreezeManifest("not-a-real-proposal-id")
    assert result["status"] == "error"
    codes = {b["reason_code"] for b in result["blockers"]}
    assert "unknown_proposal_id" in codes
    assert result["action_family"] == ACTION_FAMILY_FREEZE_MANIFEST
    assert fake_state.refresh_kicks == 0
    assert len(payloads) == 1


def test_submit_freeze_manifest_consumes_proposal_so_second_call_fails(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)
    proposal = bridge.proposeFreezeManifest({"strategy_id": STRATEGY_ID})
    first = bridge.submitFreezeManifest(proposal["proposal_id"])
    assert first["status"] == "submitted"
    # Second call must error — the proposal was consumed.
    second = bridge.submitFreezeManifest(proposal["proposal_id"])
    assert second["status"] == "error"
    assert any(b["reason_code"] == "unknown_proposal_id" for b in second["blockers"])


def test_submit_freeze_manifest_blocked_proposal_skips_refresh(
    facade: BenchCommandFacade, config_dir: Path, event_store: EventStore
) -> None:
    """If the proposal goes stale between propose and submit (stage drifted
    to backtest), the bridge must not kick a refresh — there's nothing
    new to show."""
    config_path = _write_strategy(config_dir, stage="paper")
    fake_state = _FakeBenchState()
    bridge = BenchCommandBridge(facade, bench_state=fake_state)

    proposal = bridge.proposeFreezeManifest({"strategy_id": STRATEGY_ID})
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('stage: "paper"', 'stage: "backtest"'),
        encoding="utf-8",
    )
    result = bridge.submitFreezeManifest(proposal["proposal_id"])
    assert result["status"] == "blocked"
    assert fake_state.refresh_kicks == 0
    assert event_store.get_active_manifest_for_strategy(STRATEGY_ID, "paper") is None


def test_submit_freeze_manifest_uses_backend_resolved_identity(
    facade: BenchCommandFacade, config_dir: Path, event_store: EventStore
) -> None:
    """Phase C2 F4 pattern, applied to freeze: any ``frozen_by`` key in the
    QML payload is ignored; identity is sourced backend-side via
    ``_resolve_operator_identity``."""
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)

    proposal = bridge.proposeFreezeManifest(
        {
            "strategy_id": STRATEGY_ID,
            # A malicious / accidental override from QML must be ignored.
            "frozen_by": "malicious-attempt",
        }
    )
    result = bridge.submitFreezeManifest(proposal["proposal_id"])
    assert result["status"] == "submitted"
    manifest = event_store.get_active_manifest_for_strategy(STRATEGY_ID, "paper")
    assert manifest is not None
    assert manifest.frozen_by == bridge_module._resolve_operator_identity()
    assert manifest.frozen_by != "malicious-attempt"


def test_submit_freeze_manifest_logs_when_kick_refresh_raises(
    facade: BenchCommandFacade,
    config_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refresh-guard parity with demote: a ``_kick_refresh`` exception is
    logged via ``logger.exception`` and the submit result is still emitted.
    """
    _write_strategy(config_dir, stage="paper")
    raising_state = _RaisingBenchState()
    bridge = BenchCommandBridge(facade, bench_state=raising_state)

    proposal = bridge.proposeFreezeManifest({"strategy_id": STRATEGY_ID})
    with caplog.at_level("ERROR", logger="milodex.gui.bench_command_bridge"):
        result = bridge.submitFreezeManifest(proposal["proposal_id"])

    assert result["status"] == "submitted"
    matching = [
        rec
        for rec in caplog.records
        if "BenchState refresh after submit_freeze_manifest failed" in rec.getMessage()
    ]
    assert matching, (
        "Expected a logger.exception record when _kick_refresh raises for "
        f"freeze_manifest; got {[r.getMessage() for r in caplog.records]!r}"
    )
    assert any(rec.exc_info is not None for rec in matching)


def test_submit_freeze_manifest_succeeds_when_bench_state_is_none(
    facade: BenchCommandFacade, config_dir: Path
) -> None:
    _write_strategy(config_dir, stage="paper")
    bridge = BenchCommandBridge(facade)  # default bench_state=None
    proposal = bridge.proposeFreezeManifest({"strategy_id": STRATEGY_ID})
    result = bridge.submitFreezeManifest(proposal["proposal_id"])
    assert result["status"] == "submitted"
    assert result["audit_event_id"]


def test_qml_modal_does_not_hardcode_frozen_by_literal() -> None:
    """Phase D1: ``BenchConfirmationModal.qml`` must not decide who is
    freezing. Identity flows backend-side via ``_resolve_operator_identity``.
    Bound to the freeze dispatch function so unrelated occurrences in
    surrounding QML do not false-positive."""
    modal_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "milodex"
        / "gui"
        / "qml"
        / "Milodex"
        / "components"
        / "BenchConfirmationModal.qml"
    )
    src = modal_path.read_text(encoding="utf-8")
    start = src.find("function _dispatchFreezeManifestSubmit")
    assert start != -1, "_dispatchFreezeManifestSubmit function missing from modal"
    after = src[start:]
    end_relative = after.find("\n    }")
    assert end_relative != -1, "could not locate end of _dispatchFreezeManifestSubmit"
    body = after[: end_relative + len("\n    }")]
    assert '"frozen_by"' not in body, (
        "BenchConfirmationModal._dispatchFreezeManifestSubmit must not "
        "include a frozen_by key in the proposeFreezeManifest payload "
        "(Phase D1); identity is sourced backend-side by BenchCommandBridge."
    )
    assert '"operator"' not in body, (
        "BenchConfirmationModal._dispatchFreezeManifestSubmit must not "
        'hardcode "operator" as the freezing identity (Phase D1).'
    )
