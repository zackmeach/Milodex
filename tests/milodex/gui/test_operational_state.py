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

import time
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
    # lastRefreshedAt is an ISO-8601 timestamp; just check it parses.
    # (Pre-PR D this key was "refreshed_at"; renamed to match the
    # PollingReadModel base contract — see RM-007 PR D.)
    assert datetime.fromisoformat(snap["lastRefreshedAt"])


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


def _wait_for_pool(poller) -> None:
    """Poll until the poller's background refresh settles (``dataStatus`` leaves "loading").

    Condition-based, not a fixed budget — a plain ``waitForDone(2000)`` can return
    before the xdist-delayed worker runs, flaking the caller (root-caused 2026-07-06,
    same fix as test_attention_state.py). A pool-idle check can't help here: the pool
    reads idle both before the worker starts and after it finishes, so only the
    poller's own terminal ``dataStatus`` distinguishes the two. A terminal "error"
    outcome is not masked — the caller's assertion still fails honestly.
    """
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        poller._thread_pool.waitForDone(50)  # noqa: SLF001
        QCoreApplication.processEvents()
        if poller.dataStatus != "loading":
            break
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

    state._broker_poller._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state._broker_poller)  # noqa: SLF001

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
    state._broker_poller._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state._broker_poller)  # noqa: SLF001
    assert state.brokerStatus == "connected"
    assert state.equity == 999.0

    # Second poll fails — broker raises
    broker.get_account.side_effect = RuntimeError("alpaca timeout")
    state._broker_poller._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state._broker_poller)  # noqa: SLF001

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

    state._broker_poller._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state._broker_poller)  # noqa: SLF001
    assert state.brokerStatus == "error"

    # Recover
    broker.get_account.side_effect = None
    broker.get_account.return_value = _make_account(equity=500.0, cash=100.0)
    state._broker_poller._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state._broker_poller)  # noqa: SLF001

    assert state.brokerStatus == "connected"
    assert state.brokerErrorMessage == ""
    assert state.equity == 500.0


@_skip_no_qt
def test_start_stop_lifecycle(qapp) -> None:
    """start() begins polling; stop() halts it.  No errors either way.

    Broker-half lifecycle (timer active / pool drain) is now owned by the
    composed _BrokerPoller (RM-007 PR D) and covered ONCE in
    test_polling_lifecycle.py. This test just asserts the kill-switch timer
    bridges through start()/stop() correctly.
    """
    _ = qapp
    state = _make_state()
    state.start()
    assert state._kill_switch_timer.isActive()  # noqa: SLF001

    state.stop()
    assert not state._kill_switch_timer.isActive()  # noqa: SLF001


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


# Lifecycle scaffold tests (in-flight drop, stop-drains-worker) were removed
# in RM-007 PR D — those contracts are now covered ONCE in
# tests/milodex/gui/test_polling_lifecycle.py via the composed _BrokerPoller's
# inherited PollingReadModel base.


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


# ---------------------------------------------------------------------------
# daily_pnl — pure-logic and Qt tests
# ---------------------------------------------------------------------------


def test_account_snapshot_includes_daily_pnl() -> None:
    from types import SimpleNamespace

    from milodex.gui.operational_state import _account_to_snapshot

    acct = SimpleNamespace(equity=1000.0, cash=500.0, buying_power=500.0, daily_pnl=12.34)
    snap = _account_to_snapshot(account=acct, market_open=True, positions=[])
    assert snap["daily_pnl"] == 12.34


@_skip_no_qt
def test_daily_pnl_updates_from_broker_poll(qapp) -> None:
    """A successful broker poll populates dailyPnl from AccountInfo.daily_pnl."""
    _ = qapp
    from milodex.broker.models import AccountInfo

    broker = MagicMock()
    broker.get_account.return_value = AccountInfo(
        equity=1000.0,
        cash=400.0,
        buying_power=800.0,
        portfolio_value=1000.0,
        daily_pnl=42.50,
    )
    broker.is_market_open.return_value = True
    broker.get_positions.return_value = []
    factory = MagicMock(return_value=broker)
    state = _make_state(factory=factory)

    state._broker_poller._kick_refresh()  # noqa: SLF001
    _wait_for_pool(state._broker_poller)  # noqa: SLF001

    assert state.dailyPnl == 42.50  # noqa: N815


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
