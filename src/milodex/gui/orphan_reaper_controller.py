"""Main-thread periodic driver for the orphan-run reaper (GUI).

Runs ``reconcile_orphaned_runs_on_bootstrap`` on a ``QTimer``. The reaper is
liveness-gated and re-checks the lock holder before mutating (the residual-1
guard), so periodic firing is safe against the GUI's worker-thread async spawn
(which is *not* serialized against the reaper). Reuses one ``EventStore``
(open-op-close connections; no per-tick migration scan).

``reaped(list)`` is informational (logged), not wired to a read-model refresh —
the active-ops 30s poll clears the phantom badge.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from milodex.core.event_store import EventStore
from milodex.strategies.orphan_reconciliation import reconcile_orphaned_runs_on_bootstrap

_logger = logging.getLogger(__name__)

_MIN_INTERVAL_SECONDS = 5
_MAX_INTERVAL_SECONDS = 3600


class OrphanReaperController(QObject):
    """Periodically reaps orphaned strategy_runs rows on the Qt main thread."""

    reaped = Signal(list)

    def __init__(
        self,
        *,
        event_store: EventStore,
        locks_dir: Path,
        interval_seconds: int = 60,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = event_store
        self._locks_dir = locks_dir
        self._interval = self._clamp(interval_seconds)
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval * 1000)
        self._timer.timeout.connect(self._reap)

    @staticmethod
    def _clamp(seconds: int) -> int:
        return max(_MIN_INTERVAL_SECONDS, min(_MAX_INTERVAL_SECONDS, int(seconds)))

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _get_interval(self) -> int:
        return self._interval

    def _set_interval(self, seconds: int) -> None:
        self._interval = self._clamp(seconds)
        self._timer.setInterval(self._interval * 1000)

    intervalSeconds = Property(int, _get_interval, _set_interval)  # noqa: N815  QML-facing

    @Slot(int)
    def persistInterval(self, seconds: int) -> None:  # noqa: N802  QML-facing
        """Update the live interval and persist it durably (called from QML)."""
        from milodex.gui.runner_health_settings import write_reap_interval_seconds

        self._set_interval(seconds)
        write_reap_interval_seconds(self._interval)

    @Slot()
    def _reap(self) -> None:
        try:
            reaped = reconcile_orphaned_runs_on_bootstrap(
                self._store, self._locks_dir, now=datetime.now(tz=UTC)
            )
        except Exception:
            _logger.exception("Periodic orphan reaper failed; will retry next tick")
            return
        if reaped:
            _logger.warning(
                "Periodic reaper closed %d orphan run(s): %s", len(reaped), ", ".join(reaped)
            )
        self.reaped.emit(reaped)
