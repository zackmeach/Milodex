"""Canonical Qt polling lifecycle for read-only GUI models.

Owns timer, per-instance ``QThreadPool(max=1)``, in-flight drop,
``QueuedConnection`` signals between worker and main thread, graceful
``start``/``stop`` with ``waitForDone(2000)``, and the error-state
contract that preserves last-known data after a failed refresh.

Subclasses pass a ``builder`` callable (typically a ``lambda`` wrapping
a query function) and implement ``_apply_result``. ``_apply_result`` is
a plain method that raises ``NotImplementedError`` rather than an
``@abstractmethod`` because PySide6's Shiboken metaclass conflicts with
``ABCMeta`` — do not attempt to make it formally abstract.

Public surface:
- ``PollingReadModel`` (QObject) — base class with ``dataStatus``,
  ``dataErrorMessage``, ``lastRefreshedAt`` Q_PROPERTYs; ``start()`` /
  ``stop()`` lifecycle methods.
- ``RefreshSignals`` (QObject) — ``completed: Signal(dict)``,
  ``failed: Signal(str)``. Exposed for callers who need to construct
  custom runnables.
- ``RefreshRunnable`` (QRunnable) — wraps a ``builder`` callable; emits
  ``completed`` on success or ``failed`` on exception via the bound
  ``RefreshSignals`` instance.

Workers are expected to return a ``dict`` with an optional
``"lastRefreshedAt"`` ISO-string key. If absent, the base falls back
to ``_now_iso()``. Any additional keys flow into the subclass's
``_apply_result``.

See ``docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md`` —
RM-007 for the extraction rationale.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from PySide6.QtCore import Property, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class RefreshSignals(QObject):
    completed = Signal(dict)
    failed = Signal(str)


class RefreshRunnable(QRunnable):
    def __init__(self, builder: Callable[[], dict[str, Any]], signals: RefreshSignals) -> None:
        super().__init__()
        self._builder = builder
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - exercised through QObject lifecycle tests
        try:
            self._signals.completed.emit(self._builder())
        except Exception as exc:  # noqa: BLE001 - read model sources can fail in varied ways
            logger.warning("GUI read-model refresh failed: %s", exc)
            self._signals.failed.emit(str(exc))


class PollingReadModel(QObject):
    """Shared Q_PROPERTY lifecycle for read-only GUI models."""

    dataStatusChanged = Signal()  # noqa: N815
    refreshedAtChanged = Signal()  # noqa: N815

    def __init__(
        self,
        *,
        builder: Callable[[], dict[str, Any]],
        refresh_interval_ms: int = 30_000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._builder = builder
        self._refresh_interval_ms = max(1, refresh_interval_ms)
        self._data_status = "loading"
        self._data_error_message = ""
        self._last_refreshed_at = ""
        self._refresh_in_flight = False

        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)

        self._timer = QTimer(self)
        self._timer.setInterval(self._refresh_interval_ms)
        self._timer.timeout.connect(self._kick_refresh)

        self._signals = RefreshSignals(self)
        self._signals.completed.connect(
            self._on_refresh_complete, Qt.ConnectionType.QueuedConnection
        )
        self._signals.failed.connect(self._on_refresh_failed, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        self._kick_refresh()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._thread_pool.waitForDone(2000)
        try:
            self._signals.completed.disconnect(self._on_refresh_complete)
            self._signals.failed.disconnect(self._on_refresh_failed)
        except (RuntimeError, TypeError):
            pass

    def _kick_refresh(self) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        self._thread_pool.start(RefreshRunnable(self._builder, self._signals))

    @Slot(dict)
    def _on_refresh_complete(self, result: dict[str, Any]) -> None:
        self._refresh_in_flight = False
        self._last_refreshed_at = str(result.get("lastRefreshedAt") or _now_iso())
        self.refreshedAtChanged.emit()
        self._apply_result(result)
        if self._data_status != "ready" or self._data_error_message:
            self._data_status = "ready"
            self._data_error_message = ""
            self.dataStatusChanged.emit()

    @Slot(str)
    def _on_refresh_failed(self, message: str) -> None:
        self._refresh_in_flight = False
        if self._data_status != "error" or self._data_error_message != message:
            self._data_status = "error"
            self._data_error_message = message
            self.dataStatusChanged.emit()

    def _apply_result(self, result: dict[str, Any]) -> None:
        raise NotImplementedError

    def _get_data_status(self) -> str:
        return self._data_status

    def _get_data_error_message(self) -> str:
        return self._data_error_message

    def _get_last_refreshed_at(self) -> str:
        return self._last_refreshed_at

    dataStatus = Property(str, _get_data_status, notify=dataStatusChanged)  # noqa: N815
    dataErrorMessage = Property(str, _get_data_error_message, notify=dataStatusChanged)  # noqa: N815
    lastRefreshedAt = Property(str, _get_last_refreshed_at, notify=refreshedAtChanged)  # noqa: N815
