"""Live operational-state model exposed to QML.

Aggregates four pieces of operational signal that the GUI's anchor surface
must render reactively:

1. **Kill-switch state** — read from :class:`~milodex.execution.state.KillSwitchStateStore`
   (local SQLite event store; fast).
2. **Market-open status** — broker round-trip; slow.
3. **Account snapshot** — equity / cash / buying-power; broker round-trip.
4. **Open-positions count** — broker round-trip.

Refresh strategy
----------------

- Kill-switch is polled every 1 s on the main thread (the read is local
  SQLite + tiny payload, fast enough to share the GUI thread).
- Broker state is polled every 15 s on a worker thread via a
  :class:`QThreadPool` runnable.  When the worker finishes, results are
  funneled back to the main thread via a signal connected with
  :class:`Qt.ConnectionType.QueuedConnection` so property updates and
  ``notify`` signals run on the same thread that owns the GUI bindings.

This is the *load-bearing* pattern for every observability surface that
follows in Phase 5.  The threading model trades a 15 s ceiling on
broker-state freshness for guaranteed non-blocking GUI render — which is
the right tradeoff for daily-swing tempo (R-CLI requires status to be
"current within ~30s," and 15 s leaves headroom).

Tolerance
---------

- Broker exceptions never crash the GUI.  On failure the state object
  sets ``broker_status = "error"`` and ``broker_error_message``,
  preserves the previous account snapshot (so the operator still sees
  the last-known equity), and continues polling.  On the next successful
  fetch the error state is cleared.
- A construction-time broker-factory failure is also non-fatal: the
  state object boots in ``broker_status = "error"`` with sensible
  defaults so the GUI can still render the kill-switch surface even
  when the broker is unreachable (no API keys, offline, etc.).

The constructor accepts a ``broker_client_factory`` callable (the same
shape the CLI uses) so tests inject mocks; the production wire-up in
:func:`milodex.gui.app.run_app` builds the factory the same way the CLI
does.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import (
    Property,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)

from milodex.broker.client import BrokerClient
from milodex.broker.models import AccountInfo, Position
from milodex.execution.state import KillSwitchStateStore

logger = logging.getLogger(__name__)

# The confirmation token QML must pass to reset_kill_switch().  A module-level
# constant makes the value testable and avoids hardcoding the string in two
# places (Python + QML read it via the resetKillSwitchToken Q_PROPERTY).
RESET_KILL_SWITCH_TOKEN = "CONFIRM"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class _BrokerPollSignals(QObject):
    """Signal carrier for the worker — QRunnable cannot emit signals itself.

    A separate QObject owns the signals; the runnable holds a reference and
    emits on completion.  The carrier is parented to the OperationalState so
    it lives as long as the polling lifecycle.
    """

    completed = Signal(dict)  # snapshot dict on success
    failed = Signal(str)  # error message on failure


class _BrokerPollRunnable(QRunnable):
    """One-shot broker poll executed on a QThreadPool worker thread.

    Calls ``broker.get_account()``, ``broker.is_market_open()``, and
    ``broker.get_positions()`` sequentially.  On success emits
    :attr:`_BrokerPollSignals.completed` with a snapshot dict; on any
    exception emits :attr:`_BrokerPollSignals.failed` with the error
    message.  The runnable does not access any QObject state directly —
    everything flows back to the main thread via the signal carrier.
    """

    def __init__(
        self,
        broker_client_factory: Callable[[], BrokerClient],
        signals: _BrokerPollSignals,
    ) -> None:
        super().__init__()
        self._factory = broker_client_factory
        self._signals = signals
        # Workers are short-lived; the pool's auto-delete path is safe.
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover — exercised via tests with stubs
        try:
            broker = self._factory()
            account = broker.get_account()
            market_open = broker.is_market_open()
            positions = broker.get_positions()
            snapshot = _account_to_snapshot(
                account=account,
                market_open=market_open,
                positions=positions,
            )
            self._signals.completed.emit(snapshot)
        except Exception as exc:  # noqa: BLE001 — broker exceptions vary by provider
            logger.warning("OperationalState: broker poll failed: %s", exc)
            self._signals.failed.emit(str(exc))


def _account_to_snapshot(
    *,
    account: AccountInfo,
    market_open: bool,
    positions: list[Position],
) -> dict[str, Any]:
    """Convert broker call results into the dict shape the main thread expects.

    Pulled out as a module-level helper so it's testable without Qt.  The
    dict shape is what :meth:`OperationalState._on_broker_complete` reads
    to update its Q_PROPERTYs.
    """
    return {
        "market_open": bool(market_open),
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "daily_pnl": float(account.daily_pnl),
        "open_positions_count": len(positions),
        "refreshed_at": datetime.now(tz=UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# OperationalState
# ---------------------------------------------------------------------------


class OperationalState(QObject):
    """Aggregated operational signal exposed to QML as Q_PROPERTYs.

    See module docstring for the threading model and refresh strategy.
    """

    # Signals — Qt naming convention (camelCase).  Each Q_PROPERTY's
    # ``notify=`` argument names one of these.
    killSwitchChanged = Signal()  # noqa: N815
    marketStateChanged = Signal()  # noqa: N815
    accountChanged = Signal()  # noqa: N815
    brokerStatusChanged = Signal()  # noqa: N815
    refreshedAtChanged = Signal()  # noqa: N815

    def __init__(
        self,
        *,
        broker_client_factory: Callable[[], BrokerClient],
        kill_switch_store: KillSwitchStateStore,
        trading_mode: str,
        kill_switch_poll_seconds: float = 1.0,
        broker_poll_seconds: float = 15.0,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._broker_factory = broker_client_factory
        self._kill_switch_store = kill_switch_store
        self._trading_mode = trading_mode
        self._kill_switch_poll_ms = max(1, int(kill_switch_poll_seconds * 1000))
        self._broker_poll_ms = max(1, int(broker_poll_seconds * 1000))

        # Dedicated pool (max 1 thread — broker polls are sequential by design).
        # A per-instance pool means waitForDone() in stop() drains ONLY our
        # workers; using globalInstance() would block on unrelated pool users
        # added by future surfaces (Strategy Bank, Attribution, etc.).
        if thread_pool is not None:
            self._thread_pool = thread_pool
            self._owns_thread_pool = False
        else:
            self._thread_pool = QThreadPool()
            self._thread_pool.setMaxThreadCount(1)
            self._owns_thread_pool = True

        # State backing fields — see Q_PROPERTY definitions below.
        self._kill_switch_active: bool = False
        self._kill_switch_reason: str = ""
        self._kill_switch_triggered_at: str = ""

        self._market_open: bool = False
        self._equity: float = 0.0
        self._cash: float = 0.0
        self._buying_power: float = 0.0
        self._daily_pnl: float = 0.0
        self._currency: str = "USD"
        self._open_positions_count: int = 0
        self._last_refreshed_at: str = ""

        self._broker_status: str = "stale"
        self._broker_error_message: str = ""

        # KILL-SWITCH POLL THREAD: runs on the GUI thread (1s timer). SQLite
        # reads are fast — current store size keeps _poll_kill_switch well
        # under 1ms. If the event store grows large enough that this exceeds
        # ~8ms, move it onto _broker_pool to avoid frame drops.
        # Round-2 reviewer flag; not fix-now.
        self._kill_switch_timer = QTimer(self)
        self._kill_switch_timer.setInterval(self._kill_switch_poll_ms)
        self._kill_switch_timer.timeout.connect(self._poll_kill_switch)

        self._broker_timer = QTimer(self)
        self._broker_timer.setInterval(self._broker_poll_ms)
        self._broker_timer.timeout.connect(self._kick_broker_poll)

        # Signal carrier for worker -> main-thread bridging.  Parented to
        # this OperationalState so its lifetime is tied to ours.  The
        # connections use QueuedConnection so the receiver runs on the
        # main thread (the thread that owns this QObject).
        self._poll_signals = _BrokerPollSignals(self)
        self._poll_signals.completed.connect(
            self._on_broker_complete, Qt.ConnectionType.QueuedConnection
        )
        self._poll_signals.failed.connect(
            self._on_broker_failed, Qt.ConnectionType.QueuedConnection
        )

        # Whether a broker poll is currently in flight — guards against
        # piling up workers if the broker is slower than the poll interval.
        self._broker_poll_in_flight: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin polling.

        Not strictly idempotent: calling twice enqueues a second immediate
        kill-switch poll and a second broker poll attempt (the in-flight guard
        drops the duplicate broker worker, so no pile-up occurs, but the
        kill-switch poll does re-run).  The QTimer guards (``isActive()``)
        prevent the timers from being restarted.  Callers should call start()
        exactly once per lifecycle.

        The kill-switch poll runs immediately so the UI has correct state
        from the very first frame.  The broker poll fires on the next
        timer tick (15 s) to avoid a race with QML root-object creation;
        ``broker_status`` reads as ``"stale"`` until then.
        """
        # Read kill-switch state synchronously once so the GUI doesn't
        # render a stale "OPERATIONAL" first frame if the kill switch is
        # actually active.
        self._poll_kill_switch()
        # Kick a broker poll right away too — better to show real numbers
        # ASAP than wait the full interval for the first paint.
        self._kick_broker_poll()
        if not self._kill_switch_timer.isActive():
            self._kill_switch_timer.start()
        if not self._broker_timer.isActive():
            self._broker_timer.start()

    def stop(self) -> None:
        """Halt polling and drain any in-flight broker worker.

        Idempotent: safe to call from a Qt-aware shell after start(); also
        safe to call before start() (does nothing).

        Drains in-flight broker work so a queued signal can never fire on a
        torn-down receiver during interpreter shutdown — the load-bearing
        failure mode otherwise on Windows.  Then disconnects the signals so
        any event that slipped through the race window becomes a no-op.

        This pattern is inherited by Strategy Bank, Attribution, and
        Paper-Session Status — the drain + disconnect contract must stay here.
        """
        self._kill_switch_timer.stop()
        self._broker_timer.stop()
        # Drain any in-flight worker.  Broker calls finish in well under 1 s
        # normally; 2 000 ms gives a generous margin for slow networks.
        self._thread_pool.waitForDone(2000)
        # Defensive disconnect: any event queued before waitForDone() returned
        # (e.g. a signal the worker emitted right before the pool drained)
        # would otherwise fire on a potentially torn-down receiver.
        try:
            self._poll_signals.completed.disconnect(self._on_broker_complete)
            self._poll_signals.failed.disconnect(self._on_broker_failed)
        except (RuntimeError, TypeError):
            pass  # already disconnected — idempotent stop

    # ------------------------------------------------------------------
    # Kill-switch poll (main thread)
    # ------------------------------------------------------------------

    def _poll_kill_switch(self) -> None:
        try:
            state = self._kill_switch_store.get_state()
        except Exception as exc:  # noqa: BLE001 — store may go transiently bad
            logger.warning("OperationalState: kill-switch read failed: %s", exc)
            return

        new_active = bool(state.active)
        new_reason = state.reason or ""
        new_triggered_at = state.last_triggered_at or ""

        changed = (
            new_active != self._kill_switch_active
            or new_reason != self._kill_switch_reason
            or new_triggered_at != self._kill_switch_triggered_at
        )
        if changed:
            self._kill_switch_active = new_active
            self._kill_switch_reason = new_reason
            self._kill_switch_triggered_at = new_triggered_at
            self.killSwitchChanged.emit()

    # ------------------------------------------------------------------
    # Broker poll (worker thread -> main thread via QueuedConnection)
    # ------------------------------------------------------------------

    def _kick_broker_poll(self) -> None:
        """Schedule a broker poll on the QThreadPool, dropping if one is in flight."""
        if self._broker_poll_in_flight:
            # Don't pile up runnables if the broker is slow.  We'll catch
            # the next tick.
            return
        self._broker_poll_in_flight = True
        runnable = _BrokerPollRunnable(self._broker_factory, self._poll_signals)
        self._thread_pool.start(runnable)

    @Slot(dict)
    def _on_broker_complete(self, snapshot: dict[str, Any]) -> None:
        """Apply a successful broker snapshot on the main thread."""
        self._broker_poll_in_flight = False
        market_changed = snapshot["market_open"] != self._market_open
        account_changed = (
            snapshot["equity"] != self._equity
            or snapshot["cash"] != self._cash
            or snapshot["buying_power"] != self._buying_power
            or snapshot["daily_pnl"] != self._daily_pnl
            or snapshot["open_positions_count"] != self._open_positions_count
        )

        self._market_open = snapshot["market_open"]
        self._equity = snapshot["equity"]
        self._cash = snapshot["cash"]
        self._buying_power = snapshot["buying_power"]
        self._daily_pnl = snapshot["daily_pnl"]
        self._open_positions_count = snapshot["open_positions_count"]
        self._last_refreshed_at = snapshot["refreshed_at"]
        self.refreshedAtChanged.emit()

        if market_changed:
            self.marketStateChanged.emit()
        if account_changed:
            self.accountChanged.emit()

        # Clear any prior error.
        if self._broker_status != "connected" or self._broker_error_message:
            self._broker_status = "connected"
            self._broker_error_message = ""
            self.brokerStatusChanged.emit()

    @Slot(str)
    def _on_broker_failed(self, message: str) -> None:
        """Record a broker failure on the main thread without losing state.

        Per module docstring: keep last-known account values so the
        operator still sees the most recent equity/cash even when the
        broker is unreachable; the GUI shows a stale/error indicator.
        """
        self._broker_poll_in_flight = False
        if self._broker_status != "error" or self._broker_error_message != message:
            self._broker_status = "error"
            self._broker_error_message = message
            self.brokerStatusChanged.emit()

    # ------------------------------------------------------------------
    # Q_PROPERTY accessors
    # ------------------------------------------------------------------

    def _get_kill_switch_active(self) -> bool:
        return self._kill_switch_active

    def _get_kill_switch_reason(self) -> str:
        return self._kill_switch_reason

    def _get_kill_switch_triggered_at(self) -> str:
        return self._kill_switch_triggered_at

    def _get_market_open(self) -> bool:
        return self._market_open

    def _get_trading_mode(self) -> str:
        return self._trading_mode

    def _get_equity(self) -> float:
        return self._equity

    def _get_cash(self) -> float:
        return self._cash

    def _get_buying_power(self) -> float:
        return self._buying_power

    def _get_daily_pnl(self) -> float:
        return self._daily_pnl

    def _get_currency(self) -> str:
        return self._currency

    def _get_open_positions_count(self) -> int:
        return self._open_positions_count

    def _get_last_refreshed_at(self) -> str:
        return self._last_refreshed_at

    def _get_broker_status(self) -> str:
        return self._broker_status

    def _get_broker_error_message(self) -> str:
        return self._broker_error_message

    def _get_reset_kill_switch_token(self) -> str:
        return RESET_KILL_SWITCH_TOKEN

    # Q_PROPERTY declarations.  ``Property`` here is PySide6's
    # ``QtCore.Property`` shim that builds Q_PROPERTYs from Python.
    killSwitchActive = Property(  # noqa: N815
        bool, _get_kill_switch_active, notify=killSwitchChanged
    )
    killSwitchReason = Property(  # noqa: N815
        str, _get_kill_switch_reason, notify=killSwitchChanged
    )
    killSwitchTriggeredAt = Property(  # noqa: N815
        str, _get_kill_switch_triggered_at, notify=killSwitchChanged
    )
    marketOpen = Property(bool, _get_market_open, notify=marketStateChanged)  # noqa: N815
    tradingMode = Property(str, _get_trading_mode, notify=marketStateChanged)  # noqa: N815
    equity = Property(float, _get_equity, notify=accountChanged)
    cash = Property(float, _get_cash, notify=accountChanged)
    buyingPower = Property(float, _get_buying_power, notify=accountChanged)  # noqa: N815
    dailyPnl = Property(float, _get_daily_pnl, notify=accountChanged)  # noqa: N815
    currency = Property(str, _get_currency, notify=accountChanged)
    openPositionsCount = Property(  # noqa: N815
        int, _get_open_positions_count, notify=accountChanged
    )
    lastRefreshedAt = Property(  # noqa: N815
        str, _get_last_refreshed_at, notify=refreshedAtChanged
    )
    brokerStatus = Property(str, _get_broker_status, notify=brokerStatusChanged)  # noqa: N815
    brokerErrorMessage = Property(  # noqa: N815
        str, _get_broker_error_message, notify=brokerStatusChanged
    )
    resetKillSwitchToken = Property(  # noqa: N815
        str, _get_reset_kill_switch_token, constant=True
    )

    # ------------------------------------------------------------------
    # Q_INVOKABLE actions
    # ------------------------------------------------------------------

    @Slot(str, result=bool)
    def reset_kill_switch(self, confirmation_token: str) -> bool:
        """Reset the kill switch — manual confirmation required.

        Per ADR 0005, the kill switch only resets on a deliberate manual
        operator action.  The QML layer must pass the value of
        :data:`RESET_KILL_SWITCH_TOKEN` (exposed to QML via the
        ``resetKillSwitchToken`` Q_PROPERTY) as ``confirmation_token`` —
        any other value is rejected.  This is belt-and-braces over the
        confirmation dialog: even if a UI bug ever fired this slot without
        a dialog, a stray click could not silently reset.

        Returns
        -------
        bool
            ``True`` on accepted reset, ``False`` on rejected token.

        **On per-call nonce hardening.** A standard pattern for irreversible
        actions is a per-call UUID with TTL — dialog requests a token,
        slot validates and burns it, replays fail. We deliberately do not
        implement this: the threat model is single-operator, and the
        combination of (a) the constant-token check above, (b) the dialog
        type-to-confirm gate, and (c) the manual-reset requirement from
        ADR 0005 covers the realistic failure modes. Revisit if/when QML
        contributors are added.
        """
        if confirmation_token != RESET_KILL_SWITCH_TOKEN:
            logger.warning(
                "OperationalState.reset_kill_switch: rejected token %r (must be %r — see ADR 0005)",
                confirmation_token,
                RESET_KILL_SWITCH_TOKEN,
            )
            return False
        try:
            self._kill_switch_store.reset()
        except Exception as exc:  # noqa: BLE001 — durable-state ops can fail
            logger.warning("OperationalState.reset_kill_switch: store.reset() failed: %s", exc)
            return False
        # Refresh state synchronously so the UI flips to "OPERATIONAL"
        # without waiting for the next 1s tick.
        self._poll_kill_switch()
        return True
