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

from PySide6.QtCore import Property, QObject, Qt, QThreadPool, QTimer, Signal, Slot

from milodex.broker.client import BrokerClient
from milodex.broker.models import AccountInfo, Position
from milodex.execution.state import KillSwitchStateStore
from milodex.gui.polling_lifecycle import PollingReadModel

logger = logging.getLogger(__name__)

# The confirmation token QML must pass to reset_kill_switch().  A module-level
# constant makes the value testable and avoids hardcoding the string in two
# places (Python + QML read it via the resetKillSwitchToken Q_PROPERTY).
RESET_KILL_SWITCH_TOKEN = "CONFIRM"


# ---------------------------------------------------------------------------
# Broker poll — composed PollingReadModel
# ---------------------------------------------------------------------------


def _account_to_snapshot(
    *,
    account: AccountInfo,
    market_open: bool,
    positions: list[Position],
) -> dict[str, Any]:
    """Convert broker call results into the dict shape the main thread expects.

    Pulled out as a module-level helper so it's testable without Qt. The
    ``lastRefreshedAt`` key is the PollingReadModel base's contract; the
    rest are domain fields ``OperationalState._on_broker_snapshot_ready``
    reads to update its Q_PROPERTYs.
    """
    return {
        "market_open": bool(market_open),
        "equity": float(account.equity),
        "cash": float(account.cash),
        "buying_power": float(account.buying_power),
        "daily_pnl": float(account.daily_pnl),
        "open_positions_count": len(positions),
        "lastRefreshedAt": datetime.now(tz=UTC).isoformat(),
    }


def _build_broker_snapshot(broker_factory: Callable[[], BrokerClient]) -> dict[str, Any]:
    """PollingReadModel builder — sequential broker calls + snapshot construction.

    Used by the composed ``_BrokerPoller`` (private subclass below). Errors
    bubble up naturally; ``PollingReadModel.RefreshRunnable`` catches them
    and routes to ``dataStatus = 'error'``.
    """
    broker = broker_factory()
    account = broker.get_account()
    market_open = broker.is_market_open()
    positions = broker.get_positions()
    return _account_to_snapshot(account=account, market_open=market_open, positions=positions)


class _BrokerPoller(PollingReadModel):
    """Private composition target — feeds broker snapshots up to OperationalState.

    Inherits ``PollingReadModel``'s canonical lifecycle (timer, per-instance
    ``QThreadPool(max=1)``, in-flight drop, ``waitForDone(2000)`` shutdown,
    last-known data preservation on error). Each successful refresh emits
    :attr:`snapshotReady` with the broker snapshot dict.

    OperationalState composes one of these for its broker half and forwards
    ``dataStatusChanged`` / ``refreshedAtChanged`` / ``snapshotReady`` into
    its own QML-facing signals. The kill-switch half stays on the main
    thread inside OperationalState itself (1s cadence, local SQLite — no
    worker pool needed).
    """

    snapshotReady = Signal(dict)  # noqa: N815

    def _apply_result(self, result: dict[str, Any]) -> None:
        self.snapshotReady.emit(result)


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
        thread_pool: QThreadPool | None = None,  # noqa: ARG002 — accepted for API stability
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._broker_factory = broker_client_factory
        self._kill_switch_store = kill_switch_store
        self._trading_mode = trading_mode
        self._kill_switch_poll_ms = max(1, int(kill_switch_poll_seconds * 1000))
        self._broker_poll_ms = max(1, int(broker_poll_seconds * 1000))

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

        self._broker_status: str = "stale"
        self._broker_error_message: str = ""

        # KILL-SWITCH POLL: stays on the GUI thread (1s timer). SQLite reads
        # are fast — well under 1ms with the current store size. If it ever
        # exceeds ~8ms, move it onto a worker pool to avoid frame drops.
        # Round-2 reviewer flag; not fix-now.
        self._kill_switch_timer = QTimer(self)
        self._kill_switch_timer.setInterval(self._kill_switch_poll_ms)
        self._kill_switch_timer.timeout.connect(self._poll_kill_switch)

        # BROKER POLL: composed PollingReadModel (RM-007 PR D). Owns its own
        # QTimer + per-instance QThreadPool(max=1) + in-flight drop +
        # waitForDone(2000) shutdown + error-state preservation. Snapshots
        # bubble up via snapshotReady; dataStatus / refreshedAtChanged are
        # bridged into OperationalState's brokerStatus / refreshedAt below.
        self._broker_poller = _BrokerPoller(
            builder=lambda: _build_broker_snapshot(broker_client_factory),
            refresh_interval_ms=self._broker_poll_ms,
            parent=self,
        )
        self._broker_poller.snapshotReady.connect(
            self._on_broker_snapshot_ready, Qt.ConnectionType.QueuedConnection
        )
        # Bridge base lifecycle signals -> OperationalState's QML-facing signals.
        self._broker_poller.refreshedAtChanged.connect(self.refreshedAtChanged)
        self._broker_poller.dataStatusChanged.connect(self._on_broker_data_status_changed)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Begin polling.

        Kill-switch is read synchronously once so the GUI doesn't render a
        stale "OPERATIONAL" first frame if the kill switch is actually active.
        ``_broker_poller.start()`` fires an immediate broker poll AND starts
        the 15s timer (inherited from PollingReadModel).

        Not strictly idempotent — calling twice triggers a second immediate
        kill-switch poll. The QTimer ``isActive()`` guards prevent timer
        double-start; PollingReadModel's in-flight guard prevents broker-
        worker pile-up. Callers should call start() exactly once per
        lifecycle anyway.
        """
        self._poll_kill_switch()
        self._broker_poller.start()
        if not self._kill_switch_timer.isActive():
            self._kill_switch_timer.start()

    def stop(self) -> None:
        """Halt polling and drain any in-flight broker worker.

        Idempotent: safe to call from a Qt-aware shell after start(); also
        safe to call before start() (no-op).

        ``_broker_poller.stop()`` drains the worker via ``waitForDone(2000)``
        and defensively disconnects its internal signals — the
        Windows-shutdown contract is preserved by the composed
        PollingReadModel base.
        """
        self._kill_switch_timer.stop()
        self._broker_poller.stop()

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
    # Broker poll handlers (bridged from composed _BrokerPoller)
    # ------------------------------------------------------------------

    @Slot(dict)
    def _on_broker_snapshot_ready(self, snapshot: dict[str, Any]) -> None:
        """Apply a successful broker snapshot on the main thread.

        Called via QueuedConnection from ``_broker_poller.snapshotReady``,
        which itself is emitted from the base's ``_apply_result`` after the
        worker successfully built the snapshot dict. The base has already
        cleared its in-flight flag, updated lastRefreshedAt, and emitted
        refreshedAtChanged (which we forward) by the time this slot runs.
        """
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

        if market_changed:
            self.marketStateChanged.emit()
        if account_changed:
            self.accountChanged.emit()

    @Slot()
    def _on_broker_data_status_changed(self) -> None:
        """Translate the base's data_status vocabulary to OperationalState's brokerStatus.

        Base PollingReadModel uses ``loading`` / ``ready`` / ``error``;
        OperationalState's QML surface uses ``stale`` / ``connected`` /
        ``error``. Per module docstring: keep last-known account values
        through errors so the operator still sees the most recent
        equity/cash — the base preserves last-known data on error by
        not calling _apply_result on failure.
        """
        base_status = self._broker_poller.dataStatus
        if base_status == "ready":
            new_status = "connected"
            new_msg = ""
        elif base_status == "error":
            new_status = "error"
            new_msg = self._broker_poller.dataErrorMessage
        else:  # "loading" — before any refresh has resolved
            new_status = "stale"
            new_msg = ""

        if new_status != self._broker_status or new_msg != self._broker_error_message:
            self._broker_status = new_status
            self._broker_error_message = new_msg
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
        # Delegate to composed poller — base manages the ISO timestamp
        # (with _now_iso fallback if the snapshot omits lastRefreshedAt).
        return self._broker_poller.lastRefreshedAt

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
