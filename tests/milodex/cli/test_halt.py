"""Tests for the ``milodex halt`` operator manual kill-switch trip (ADR 0005 Addendum / D-9)."""

from __future__ import annotations

from io import StringIO

from milodex.cli.commands import halt as halt_module
from milodex.cli.main import main as cli_entrypoint
from milodex.execution.models import HaltOutcome
from milodex.execution.state import KillSwitchState


class StubHaltService:
    """Execution-service stub recording halt_trading calls."""

    def __init__(
        self,
        *,
        halt_outcome: HaltOutcome | None = None,
        state_after: KillSwitchState | None = None,
    ) -> None:
        self.halt_outcome = halt_outcome or HaltOutcome(orders_cancelled=True, cancel_error=None)
        self.state_after = state_after or KillSwitchState(
            active=True,
            reason="operator manual trip",
            last_triggered_at="2026-07-09T14:00:00+00:00",
        )
        self.halt_calls: list[str] = []

    def halt_trading(self, reason: str) -> HaltOutcome:
        self.halt_calls.append(reason)
        return self.halt_outcome

    def get_kill_switch_state(self) -> KillSwitchState:
        return self.state_after


class _RecordingControl:
    """PaperRunnerControl double: records stop requests, optionally fails per runner."""

    def __init__(self, *, fail_for: set[str] | None = None, **_kwargs) -> None:
        self._fail_for = fail_for or set()
        self.stop_calls: list[str] = []

    def request_controlled_stop(self, strategy_id: str, *, holder):
        self.stop_calls.append(strategy_id)
        if strategy_id in self._fail_for:
            raise RuntimeError(f"stop write failed for {strategy_id}")

        class _Result:
            request_path = f"/locks/{strategy_id}.controlled_stop.json"

        return _Result()


def test_halt_requires_confirm():
    service = StubHaltService()
    stderr = StringIO()

    exit_code = cli_entrypoint(
        ["halt"],
        execution_service_factory=lambda: service,
        stdout=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 1
    assert "--confirm" in stderr.getvalue()
    assert service.halt_calls == []


def test_halt_default_reason_trips_and_reports(monkeypatch):
    service = StubHaltService()
    monkeypatch.setattr(halt_module, "live_runner_holders", lambda config_dir, locks_dir: {})
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["halt", "--confirm"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert service.halt_calls == ["operator manual trip"]
    assert "Operator Manual Halt" in output
    assert "Kill switch: active" in output
    assert "resting orders cancelled" in output
    assert "none found" in output


def test_halt_custom_reason_forwarded(monkeypatch):
    service = StubHaltService()
    monkeypatch.setattr(halt_module, "live_runner_holders", lambda config_dir, locks_dir: {})
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["halt", "--confirm", "--reason", "market circuit breaker"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    assert exit_code == 0
    assert service.halt_calls == ["market circuit breaker"]


def test_halt_reports_cancel_failure_but_still_succeeds(monkeypatch):
    service = StubHaltService(
        halt_outcome=HaltOutcome(orders_cancelled=False, cancel_error="RuntimeError: lost"),
    )
    monkeypatch.setattr(halt_module, "live_runner_holders", lambda config_dir, locks_dir: {})
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["halt", "--confirm"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "cancel_all_orders FAILED" in output
    assert "engaged anyway" in output
    # The switch still activates despite the cancel failure.
    assert "Kill switch: active" in output


def test_halt_stops_every_live_runner_and_fails_soft(monkeypatch):
    service = StubHaltService()
    holders = {
        "regime.daily.sma200_rotation.spy_shy.v1": {"pid": 111},
        "meanrev.daily.rsi2.spy.v1": {"pid": 222},
    }
    monkeypatch.setattr(halt_module, "live_runner_holders", lambda config_dir, locks_dir: holders)
    # The second runner is "wedged": its stop write raises — must not block the halt.
    control = _RecordingControl(fail_for={"meanrev.daily.rsi2.spy.v1"})
    monkeypatch.setattr(halt_module, "PaperRunnerControl", lambda **kwargs: control)
    stdout = StringIO()

    exit_code = cli_entrypoint(
        ["halt", "--confirm"],
        execution_service_factory=lambda: service,
        stdout=stdout,
        stderr=StringIO(),
    )

    output = stdout.getvalue()
    assert exit_code == 0
    # A stop request was issued for BOTH live runners.
    assert set(control.stop_calls) == set(holders)
    # The healthy runner reports OK; the wedged one reports FAIL — but the halt still succeeds.
    assert "[OK] regime.daily.sma200_rotation.spy_shy.v1" in output
    assert "[FAIL] meanrev.daily.rsi2.spy.v1" in output
    assert service.halt_calls == ["operator manual trip"]
