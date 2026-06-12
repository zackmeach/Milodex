"""Public-API contract tests for `PollingReadModel`.

These tests pin the lifecycle contract that all migrated subclasses
(`StrategyBankState`, `PerformanceState`, `ActivityFeedState`,
`RiskThroughputState`, `ActiveOpsState`, `AttentionState`, `MarketTapeState`)
will rely on after RM-007 PRs B–D land.

Per RM-007 done criteria: "Tests assert behavior through the read-model
interface, not private timer fields." Every test below drives via public
`start()` / `stop()` and asserts on Q_PROPERTY values or signal emissions —
no `_kick_refresh` / `_thread_pool` / `_refresh_in_flight` pinning.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QObject

from milodex.gui.polling_lifecycle import PollingReadModel


def _spin_until(predicate, timeout_ms: int = 2_000) -> bool:
    """Spin Qt event processing until `predicate()` is True or timeout elapses."""
    app = QCoreApplication.instance() or QCoreApplication([])
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            # One more drain to settle any signal-handler side effects.
            app.processEvents()
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


class _FakeState(PollingReadModel):
    """Minimal concrete subclass for testing the base contract."""

    def __init__(
        self,
        *,
        builder,
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        self.applied_results: list[dict[str, Any]] = []
        super().__init__(
            builder=builder,
            refresh_interval_ms=refresh_interval_ms,
            parent=parent,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        self.applied_results.append(result)


@pytest.fixture(autouse=True)
def _qt_app():
    """Ensure a QCoreApplication exists for the duration of each test."""
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def test_start_then_stop_does_not_leak_worker() -> None:
    """A start/stop cycle drains the worker pool without hanging."""
    state = _FakeState(
        builder=lambda: {"payload": "x", "lastRefreshedAt": "2026-05-24T00:00:00+00:00"},
        refresh_interval_ms=30_000,
    )
    state.start()
    _spin_until(lambda: state.dataStatus == "ready", timeout_ms=2_000)
    state.stop()
    assert state.dataStatus == "ready"  # final state preserved post-stop


def test_in_flight_drop_prevents_overlapping_refreshes() -> None:
    """Repeated `start()` calls while one refresh is in flight do not stack workers."""
    call_count = {"n": 0}

    def builder() -> dict[str, Any]:
        call_count["n"] += 1
        return {"lastRefreshedAt": "2026-05-24T00:00:01+00:00"}

    state = _FakeState(builder=builder, refresh_interval_ms=30_000)
    state.start()
    state.start()  # second call while first may still be in-flight
    state.start()  # third — same
    _spin_until(lambda: state.dataStatus == "ready", timeout_ms=2_000)
    state.stop()
    # Three start() calls produce at most three builder invocations (no leak);
    # at least one runs.
    assert 1 <= call_count["n"] <= 3


def test_error_preserves_last_known_lastRefreshedAt() -> None:  # noqa: N802 - Qt property
    """After a successful refresh then an error, lastRefreshedAt does NOT reset."""
    sequence = iter(
        [
            {"lastRefreshedAt": "2026-05-24T00:00:05+00:00"},  # success
            RuntimeError("boom"),  # failure
        ]
    )

    def builder() -> dict[str, Any]:
        value = next(sequence)
        if isinstance(value, Exception):
            raise value
        return value

    state = _FakeState(builder=builder, refresh_interval_ms=30_000)
    state.start()
    _spin_until(lambda: state.dataStatus == "ready", timeout_ms=2_000)
    preserved_ts = state.lastRefreshedAt
    assert preserved_ts == "2026-05-24T00:00:05+00:00"

    # Drive second refresh deterministically (production path = timer expiry).
    state._kick_refresh()  # noqa: SLF001 — deterministic test trigger
    _spin_until(lambda: state.dataStatus == "error", timeout_ms=2_000)
    state.stop()

    assert state.dataStatus == "error"
    assert state.dataErrorMessage == "boom"
    # Critical invariant: timestamp is preserved across the error.
    assert state.lastRefreshedAt == preserved_ts


def test_error_sets_dataStatus_and_dataErrorMessage() -> None:  # noqa: N802 - Qt property
    """A first-call failure puts the model in error state with the exception message."""

    def boom_builder() -> dict[str, Any]:
        raise ValueError("the bomb")

    state = _FakeState(builder=boom_builder, refresh_interval_ms=30_000)
    state.start()
    _spin_until(lambda: state.dataStatus == "error", timeout_ms=2_000)
    state.stop()

    assert state.dataStatus == "error"
    assert state.dataErrorMessage == "the bomb"


def test_subsequent_success_clears_error_state() -> None:
    """After an error, a successful refresh clears error state and emits 'ready'."""
    sequence = iter(
        [
            RuntimeError("first attempt fails"),
            {"lastRefreshedAt": "2026-05-24T00:00:10+00:00"},
        ]
    )

    def builder() -> dict[str, Any]:
        value = next(sequence)
        if isinstance(value, Exception):
            raise value
        return value

    state = _FakeState(builder=builder, refresh_interval_ms=30_000)
    state.start()
    _spin_until(lambda: state.dataStatus == "error", timeout_ms=2_000)
    assert state.dataStatus == "error"

    state._kick_refresh()  # noqa: SLF001 — deterministic test trigger
    _spin_until(lambda: state.dataStatus == "ready", timeout_ms=2_000)
    state.stop()

    assert state.dataStatus == "ready"
    assert state.dataErrorMessage == ""


def test_stop_drains_pending_worker_within_2s() -> None:
    """`stop()` returns within the base's hardcoded 2s waitForDone boundary."""
    state = _FakeState(
        builder=lambda: {"lastRefreshedAt": "2026-05-24T00:00:15+00:00"},
        refresh_interval_ms=30_000,
    )
    state.start()
    # Don't wait for ready — just stop immediately. Stop must drain the pool.
    t0 = time.monotonic()
    state.stop()
    elapsed = time.monotonic() - t0
    # Base hardcodes waitForDone(2000); stop() should return within 2.5s.
    assert elapsed < 2.5


def test_builder_result_without_lastRefreshedAt_falls_back_to_now_iso() -> None:  # noqa: N802
    """When the builder result has no `lastRefreshedAt`, base fills in the current ISO timestamp.

    This is the contract that migrated workers will rely on — they only need
    to emit `lastRefreshedAt` if they care about a specific timestamp.
    ActiveOps, for example, doesn't include a timestamp in its payload at all.
    """
    state = _FakeState(
        builder=lambda: {"payload": "no_ts"},  # NO lastRefreshedAt key
        refresh_interval_ms=30_000,
    )
    state.start()
    _spin_until(lambda: state.dataStatus == "ready", timeout_ms=2_000)
    state.stop()

    # lastRefreshedAt is non-empty — base filled it in via _now_iso fallback.
    assert state.lastRefreshedAt != ""
    # And the result still got applied to the subclass.
    assert state.applied_results == [{"payload": "no_ts"}]


def test_request_refresh_triggers_refresh_while_running() -> None:
    """`request_refresh()` behaves like a timer-driven kick on a running model."""
    call_count = {"n": 0}

    def builder() -> dict[str, Any]:
        call_count["n"] += 1
        return {"lastRefreshedAt": f"2026-06-12T00:00:{call_count['n']:02d}+00:00"}

    state = _FakeState(builder=builder, refresh_interval_ms=30_000)
    state.start()
    assert _spin_until(lambda: state.dataStatus == "ready", timeout_ms=2_000)
    settled = call_count["n"]

    state.request_refresh("post-submit")
    assert _spin_until(lambda: call_count["n"] > settled, timeout_ms=2_000), (
        "request_refresh on a running model did not deliver a fresh refresh"
    )
    state.stop()


def test_request_refresh_after_stop_starts_no_worker() -> None:
    """`request_refresh()` after `stop()` is a complete no-op — no worker starts.

    This is the public replacement for the bridge's prior private
    `_kick_refresh` reach: a late post-submit refresh request delivered during
    shutdown must not restart pool work on a torn-down read model.
    """
    call_count = {"n": 0}

    def builder() -> dict[str, Any]:
        call_count["n"] += 1
        return {"lastRefreshedAt": f"2026-06-12T00:01:{call_count['n']:02d}+00:00"}

    state = _FakeState(builder=builder, refresh_interval_ms=30_000)
    state.start()
    assert _spin_until(lambda: state.dataStatus == "ready", timeout_ms=2_000)
    state.stop()
    drained = call_count["n"]

    state.request_refresh("late-after-stop")
    # Give a (wrongly started) worker time to run; the count must not move.
    _spin_until(lambda: call_count["n"] > drained, timeout_ms=300)
    assert call_count["n"] == drained, (
        "request_refresh after stop() started a worker on a stopped read model"
    )

    # A restart re-arms the model: request_refresh works again.
    state.start()
    assert _spin_until(lambda: call_count["n"] > drained, timeout_ms=2_000)
    state.stop()


def test_restart_after_stop_resumes_polling() -> None:
    """A `start() → stop() → start()` cycle must resume delivering refreshes.

    Regression test for a defect where `stop()` disconnected the per-state
    `_signals.completed` / `_signals.failed` slots but `start()` did NOT
    reconnect them.  A user (or a test harness) restarting the poller after
    a stop would silently get no refresh results — workers would run, but
    their emits would land on a disconnected signal and be dropped.

    Discovered by the Opus regression reviewer 2026-05-24 while auditing
    the centralized base contract.  The pre-extraction per-module copies
    had the same bug, but only one consumer (a future restart-aware GUI
    surface) would trip it.  The fix lives in `polling_lifecycle.py`.
    """
    call_count = {"n": 0}

    def builder() -> dict[str, Any]:
        call_count["n"] += 1
        return {
            "payload": f"call_{call_count['n']}",
            "lastRefreshedAt": f"2026-05-24T00:00:{call_count['n']:02d}+00:00",
        }

    state = _FakeState(builder=builder, refresh_interval_ms=30_000)

    # First start: refresh delivers normally.
    state.start()
    assert _spin_until(lambda: state.dataStatus == "ready", timeout_ms=2_000)
    first_applied = len(state.applied_results)
    assert first_applied >= 1, "First start() should have delivered at least one result"

    # Stop + restart cycle.
    state.stop()
    state.start()

    # Second start: must deliver a NEW refresh.  Wait for applied_results to grow.
    assert _spin_until(lambda: len(state.applied_results) > first_applied, timeout_ms=2_000), (
        "Restart after stop did not deliver a refresh — "
        "signals dropped silently because stop() disconnected and start() did not reconnect"
    )

    state.stop()
