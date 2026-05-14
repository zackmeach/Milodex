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
from pathlib import Path

import pytest

from milodex.commands.bench import (
    ACTION_FAMILY_DEMOTE,
    ACTION_FAMILY_FREEZE_MANIFEST,
    BenchCommandFacade,
)
from milodex.core.event_store import EventStore
from milodex.gui import bench_command_bridge as bridge_module
from milodex.gui.bench_command_bridge import BenchCommandBridge

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
    )


class _FakeBenchState:
    """Records refresh kicks so tests can assert the bridge requests one."""

    def __init__(self) -> None:
        self.refresh_kicks = 0

    def _kick_refresh(self) -> None:  # noqa: N802 — mirrors _PollingReadModel
        self.refresh_kicks += 1


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

    # The proposal is now cached for the matching submit call.
    assert payload["proposal_id"] in bridge._proposals  # noqa: SLF001


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
        cfg.read_text(encoding="utf-8").replace(
            'stage: "paper"', 'stage: "backtest"'
        ),
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


def test_bridge_exposes_only_demote_and_freeze_manifest_action_families() -> None:
    """ADR 0051 Phase D1: the bridge wires demote (C2) and freeze_manifest
    (D1). Every other action family must remain absent from the QML-callable
    surface."""
    members = {
        name
        for name, _ in inspect.getmembers(BenchCommandBridge, predicate=callable)
    }
    # Submit-capable slots present.
    assert "proposeDemote" in members
    assert "submitDemote" in members
    assert "proposeFreezeManifest" in members
    assert "submitFreezeManifest" in members
    # No other action-family slot. Adding one without the corresponding ADR
    # amendment + facade wiring + boundary doc update is exactly the failure
    # mode the perimeter exists to prevent.
    for forbidden in (
        "proposeBacktest",
        "submitBacktest",
        "proposePromoteToPaper",
        "submitPromoteToPaper",
        "proposeStartPaperRunner",
        "submitStartPaperRunner",
        "proposeStopPaperRunner",
        "submitStopPaperRunner",
    ):
        assert forbidden not in members, (
            f"BenchCommandBridge must not expose {forbidden} at Phase D1 "
            "(ADR 0051 §10). Phase D1 is demote + freeze_manifest only."
        )


def test_submit_capable_action_families_returns_demote_and_freeze_manifest(
    facade: BenchCommandFacade,
) -> None:
    bridge = BenchCommandBridge(facade)
    assert bridge.submitCapableActionFamilies() == [
        ACTION_FAMILY_DEMOTE,
        ACTION_FAMILY_FREEZE_MANIFEST,
    ]


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
                f"bench_command_bridge.py must not import {forbidden!r} "
                "(ADR 0051 §4)."
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
    # Proposal cached for the matching submit call.
    assert payload["proposal_id"] in bridge._proposals  # noqa: SLF001


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
        config_path.read_text(encoding="utf-8").replace(
            'stage: "paper"', 'stage: "backtest"'
        ),
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
