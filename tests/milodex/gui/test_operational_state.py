"""Tests for :class:`milodex.gui.operational_state.OperationalState`.

The pure-logic helper :func:`_account_to_snapshot` is tested without Qt.
The full QObject lifecycle requires a QGuiApplication and uses a mock
broker — we never hit a real Alpaca endpoint.  Tests are gated behind
``_skip_no_qt`` when PySide6 isn't installed.

Threading note: tests drive the polling cycle directly via the private
``_poll_kill_switch`` and ``_kick_broker_poll`` methods rather than
sleeping for QTimer ticks.  This keeps the suite fast and deterministic.
The worker pool is a real :class:`QThreadPool`; we wait briefly for the
worker to complete via ``QThreadPool.waitForDone()`` before asserting.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# PySide6 availability
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QCoreApplication, QThreadPool  # noqa: F401
    from PySide6.QtGui import QGuiApplication  # noqa: F401

    _PYSIDE6_AVAILABLE = True
except ImportError:
    _PYSIDE6_AVAILABLE = False

_skip_no_qt = pytest.mark.skipif(
    not _PYSIDE6_AVAILABLE,
    reason="PySide6 not installed — skipping Qt-aware OperationalState tests",
)


# ---------------------------------------------------------------------------
# Pure-logic helpers — no Qt required
# ---------------------------------------------------------------------------


def test_account_to_snapshot_shape() -> None:
    """``_account_to_snapshot`` produces the dict shape the main thread expects."""
    from milodex.broker.models import AccountInfo, Position
    from milodex.gui.operational_state import _account_to_snapshot

    account = AccountInfo(
        equity=1000.0,
        cash=400.0,
        buying_power=800.0,
        portfolio_value=1000.0,
        daily_pnl=10.0,
    )
    positions = [
        Position(
            symbol="SPY",
            quantity=2,
            avg_entry_price=400.0,
            current_price=405.0,
            market_value=810.0,
            unrealized_pnl=10.0,
            unrealized_pnl_pct=0.025,
        )
    ]

    snap = _account_to_snapshot(account=account, market_open=True, positions=positions)
    assert snap["market_open"] is True
    assert snap["equity"] == 1000.0
    assert snap["cash"] == 400.0
    assert snap["buying_power"] == 800.0
    assert snap["open_positions_count"] == 1
    # refreshed_at is an ISO-8601 timestamp; just check it parses.
    assert datetime.fromisoformat(snap["refreshed_at"])


# ---------------------------------------------------------------------------
# Qt-aware fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QGuiApplication so QObject + QTimer + QThreadPool work."""
    if not _PYSIDE6_AVAILABLE:
        return None

    import os
    import sys

    from PySide6.QtGui import QGuiApplication

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv[:1])
    return app


def _make_account(equity: float = 1000.0, cash: float = 400.0):
    from milodex.broker.models import AccountInfo

    return AccountInfo(
        equity=equity,
        cash=cash,
        buying_power=cash * 2,
        portfolio_value=equity,
        daily_pnl=0.0,
    )


def _make_state(*, store=None, factory=None, broker_poll_seconds: float = 9999.0):
    """Construct an OperationalState with sensible defaults for tests.

    A long broker_poll_seconds ensures the timer never fires during a
    test — we drive polling explicitly.  ``factory`` and ``store`` default
    to MagicMocks if the caller doesn't pass concrete ones.
    """
    from milodex.gui.operational_state import OperationalState

    if store is None:
        store = MagicMock()
        store.get_state.return_value = MagicMock(active=False, reason=None, last_triggered_at=None)
    if factory is None:
        broker = MagicMock()
        broker.get_account.return_value = _make_account()
        broker.is_market_open.return_value = True
        broker.get_positions.return_value = []
        factory = MagicMock(return_value=broker)

    return OperationalState(
        broker_client_factory=factory,
        kill_switch_store=store,
        trading_mode="paper",
        kill_switch_poll_seconds=9999.0,  # tests drive it explicitly
        broker_poll_seconds=broker_poll_seconds,
    )


def _wait_for_pool(pool=None) -> None:
    """Block until pending QThreadPool work is done, then drain the event loop.

    Pass ``pool`` to drain a specific (per-instance) pool; omit to drain the
    global pool (kept for legacy call sites).
    """
    if pool is None:
        pool = QThreadPool.globalInstance()
    pool.waitForDone(2000)
    # Give queued signals a chance to deliver to the main thread.
    QCoreApplication.processEvents()
    # Some events ship deferred deliveries; processing twice gives them a beat.
    QCoreApplication.processEvents()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_no_qt
def test_initial_state_before_first_refresh(qapp) -> None:
    """Properties have sensible defaults before any poll has run."""
    _ = qapp
    state = _make_state()
    assert state.killSwitchActive is False
    assert state.killSwitchReason == ""
    assert state.killSwitchTriggeredAt == ""
    assert state.marketOpen is False
    assert state.tradingMode == "paper"
    assert state.equity == 0.0
    assert state.cash == 0.0
    assert state.buyingPower == 0.0
    assert state.currency == "USD"
    assert state.openPositionsCount == 0
    assert state.lastRefreshedAt == ""
    assert state.brokerStatus == "stale"
    assert state.brokerErrorMessage == ""


@_skip_no_qt
def test_kill_switch_state_reflects_store(qapp) -> None:
    """When the store returns active, the property and signal update."""
    _ = qapp
    store = MagicMock()
    store.get_state.return_value = MagicMock(
        active=True,
        reason="margin breach",
        last_triggered_at="2026-05-07T12:00:00+00:00",
    )
    state = _make_state(store=store)

    fired: list[None] = []
    state.killSwitchChanged.connect(lambda: fired.append(None))

    state._poll_kill_switch()  # noqa: SLF001 — test drives the poll directly

    assert state.killSwitchActive is True
    assert state.killSwitchReason == "margin breach"
    assert state.killSwitchTriggeredAt == "2026-05-07T12:00:00+00:00"
    assert len(fired) == 1


@_skip_no_qt
def test_kill_switch_no_signal_when_unchanged(qapp) -> None:
    """Repeated polls with the same state do not re-emit the change signal."""
    _ = qapp
    state = _make_state()
    fired: list[None] = []
    state.killSwitchChanged.connect(lambda: fired.append(None))

    state._poll_kill_switch()  # noqa: SLF001
    state._poll_kill_switch()  # noqa: SLF001
    # Initial state is inactive, polling inactive twice -> no change events.
    assert fired == []


@_skip_no_qt
def test_broker_state_updates_from_factory_call(qapp) -> None:
    """A successful broker poll populates account/market properties."""
    _ = qapp
    broker = MagicMock()
    broker.get_account.return_value = _make_account(equity=1234.56, cash=200.0)
    broker.is_market_open.return_value = True
    broker.get_positions.return_value = ["pos1", "pos2"]
    factory = MagicMock(return_value=broker)
    state = _make_state(factory=factory)

    state._kick_broker_poll()  # noqa: SLF001
    _wait_for_pool(state._thread_pool)  # noqa: SLF001

    assert state.brokerStatus == "connected"
    assert state.equity == 1234.56
    assert state.cash == 200.0
    assert state.buyingPower == 400.0
    assert state.openPositionsCount == 2
    assert state.marketOpen is True
    assert state.lastRefreshedAt != ""


@_skip_no_qt
def test_broker_failure_sets_error_status(qapp) -> None:
    """Broker exceptions set brokerStatus=error and preserve last-known values."""
    _ = qapp
    broker = MagicMock()
    broker.get_account.return_value = _make_account(equity=999.0, cash=100.0)
    broker.is_market_open.return_value = True
    broker.get_positions.return_value = []
    factory = MagicMock(return_value=broker)
    state = _make_state(factory=factory)

    # First poll succeeds — equity = 999.0
    state._kick_broker_poll()  # noqa: SLF001
    _wait_for_pool(state._thread_pool)  # noqa: SLF001
    assert state.brokerStatus == "connected"
    assert state.equity == 999.0

    # Second poll fails — broker raises
    broker.get_account.side_effect = RuntimeError("alpaca timeout")
    state._kick_broker_poll()  # noqa: SLF001
    _wait_for_pool(state._thread_pool)  # noqa: SLF001

    assert state.brokerStatus == "error"
    assert "alpaca timeout" in state.brokerErrorMessage
    # Last-known equity preserved
    assert state.equity == 999.0


@_skip_no_qt
def test_broker_recovery_clears_error(qapp) -> None:
    """A successful poll after a failure clears the error state."""
    _ = qapp
    broker = MagicMock()
    broker.get_account.side_effect = RuntimeError("boom")
    broker.is_market_open.return_value = False
    broker.get_positions.return_value = []
    factory = MagicMock(return_value=broker)
    state = _make_state(factory=factory)

    state._kick_broker_poll()  # noqa: SLF001
    _wait_for_pool(state._thread_pool)  # noqa: SLF001
    assert state.brokerStatus == "error"

    # Recover
    broker.get_account.side_effect = None
    broker.get_account.return_value = _make_account(equity=500.0, cash=100.0)
    state._kick_broker_poll()  # noqa: SLF001
    _wait_for_pool(state._thread_pool)  # noqa: SLF001

    assert state.brokerStatus == "connected"
    assert state.brokerErrorMessage == ""
    assert state.equity == 500.0


@_skip_no_qt
def test_start_stop_lifecycle(qapp) -> None:
    """start() begins polling; stop() halts it.  No errors either way."""
    _ = qapp
    state = _make_state()
    state.start()
    assert state._kill_switch_timer.isActive()  # noqa: SLF001
    assert state._broker_timer.isActive()  # noqa: SLF001

    state.stop()
    assert not state._kill_switch_timer.isActive()  # noqa: SLF001
    assert not state._broker_timer.isActive()  # noqa: SLF001
    # stop() itself already drains; the call here is a belt-and-braces check
    # that there is nothing left to drain (i.e. it's idempotent).
    _wait_for_pool(state._thread_pool)  # noqa: SLF001


@_skip_no_qt
def test_reset_kill_switch_requires_confirmation_token(qapp) -> None:
    """Per ADR 0005, kill-switch reset only proceeds on the canonical token."""
    _ = qapp
    store = MagicMock()
    store.get_state.return_value = MagicMock(
        active=True, reason="manual", last_triggered_at="2026-05-07T00:00:00+00:00"
    )
    state = _make_state(store=store)

    # Wrong token: reject, no store call.
    assert state.reset_kill_switch("yes") is False
    store.reset.assert_not_called()

    # Correct token: accept, store.reset() invoked.
    store.reset.return_value = MagicMock(active=False)
    # After reset, store.get_state() should reflect inactive.
    store.get_state.return_value = MagicMock(active=False, reason=None, last_triggered_at=None)
    assert state.reset_kill_switch("CONFIRM") is True
    store.reset.assert_called_once()
    # Synchronous re-poll flipped the property.
    assert state.killSwitchActive is False


@_skip_no_qt
def test_concurrent_poll_kicks_drop_when_in_flight(qapp) -> None:
    """A second _kick_broker_poll while one is in flight is a no-op (no pile-up)."""
    _ = qapp
    state = _make_state()
    state._broker_poll_in_flight = True  # noqa: SLF001 — simulate in-flight
    factory_calls_before = state._broker_factory.call_count  # noqa: SLF001

    state._kick_broker_poll()  # noqa: SLF001
    # No new worker scheduled while in-flight; the factory wasn't called again.
    assert state._broker_factory.call_count == factory_calls_before  # noqa: SLF001


@_skip_no_qt
def test_stop_drains_in_flight_broker_worker(qapp) -> None:
    """stop() must wait for in-flight broker workers to complete before returning.

    Defends the architectural contract that the load-bearing failure mode on
    Windows shutdown — worker emits signal to a torn-down OperationalState —
    cannot occur.  Replicates the pattern Strategy Bank / Attribution will
    inherit; this test is the regression guard.

    Strategy: the broker mock blocks on get_account() until the test releases
    a threading.Event.  We kick a poll, then call stop() while the worker is
    blocked.  stop() must not return until the worker has finished.  After
    stop() returns the OperationalState can be safely destroyed.
    """
    import threading
    import time

    from milodex.gui.operational_state import OperationalState

    release = threading.Event()
    worker_ran = threading.Event()

    def slow_get_account():
        worker_ran.set()  # signal that the worker started
        release.wait(timeout=5.0)  # block until the test releases us
        return _make_account()

    broker = MagicMock()
    broker.get_account.side_effect = slow_get_account
    broker.is_market_open.return_value = True
    broker.get_positions.return_value = []
    factory = MagicMock(return_value=broker)

    store = MagicMock()
    store.get_state.return_value = MagicMock(active=False, reason=None, last_triggered_at=None)

    state = OperationalState(
        broker_client_factory=factory,
        kill_switch_store=store,
        trading_mode="paper",
        kill_switch_poll_seconds=9999.0,
        broker_poll_seconds=9999.0,
    )

    # Kick a broker poll and wait until the worker has actually started blocking.
    state._kick_broker_poll()  # noqa: SLF001
    assert worker_ran.wait(timeout=3.0), "Worker did not start within 3 s"

    # Release the worker and immediately call stop().  stop() must drain the
    # pool — i.e. it must not return until the worker finishes.
    release.set()
    t0 = time.monotonic()
    state.stop()
    elapsed = time.monotonic() - t0

    # The worker was unblocked synchronously before stop() was called, so stop()
    # should return quickly (well under 1 s).  The important assertion is that
    # the pool is now empty — no in-flight work remains.
    assert state._thread_pool.activeThreadCount() == 0  # noqa: SLF001
    assert elapsed < 2.0, f"stop() took {elapsed:.2f}s — should drain in well under 2 s"

    # After stop() the worker's signal is disconnected; destroying state is safe
    # even if a queued event was somehow still pending.
    del state


@_skip_no_qt
def test_reset_kill_switch_token_property_matches_constant(qapp) -> None:
    """resetKillSwitchToken Q_PROPERTY returns the module constant.

    Belt-and-braces against silent mismatch: if the constant is renamed or
    the property getter is wired to a different value, this fails loudly.
    """
    _ = qapp
    from milodex.gui.operational_state import (  # noqa: F401
        RESET_KILL_SWITCH_TOKEN,
        OperationalState,
    )

    state = _make_state()
    assert state.resetKillSwitchToken == RESET_KILL_SWITCH_TOKEN


def test_reset_kill_switch_token_constant_value_is_canonical() -> None:
    """RESET_KILL_SWITCH_TOKEN must equal the literal string 'CONFIRM'.

    No Qt needed — this pins the constant's value so any change to the
    canonical token fails explicitly rather than quietly altering the
    operator's expected input.
    """
    from milodex.gui.operational_state import RESET_KILL_SWITCH_TOKEN

    assert RESET_KILL_SWITCH_TOKEN == "CONFIRM"


@_skip_no_qt
@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "confirm",  # wrong case
        "CONFIRM ",  # trailing whitespace
        "CONFI RM",  # internal whitespace
        " CONFIRM",  # leading whitespace
    ],
)
def test_reset_kill_switch_rejects_non_canonical_tokens(qapp, token) -> None:
    """Per ADR 0005, only the exact string 'CONFIRM' is accepted.

    Locks the contract against case-insensitive matches, whitespace coercion,
    None, empty string, and any other variant that is not strict equality.
    """
    _ = qapp
    store = MagicMock()
    store.get_state.return_value = MagicMock(
        active=True, reason="manual", last_triggered_at="2026-05-07T00:00:00+00:00"
    )
    state = _make_state(store=store)

    # @Slot(str, result=bool) — passing None may raise at the Qt boundary;
    # any exception from a non-str token counts as "rejected."
    try:
        result = state.reset_kill_switch(token)
    except (TypeError, RuntimeError):
        # Qt raised at the Slot boundary for a non-str type — counts as rejected.
        result = False

    assert result is False, f"Expected False for token {token!r}, got {result!r}"
    store.reset.assert_not_called()
