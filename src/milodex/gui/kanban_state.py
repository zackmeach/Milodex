"""``KanbanState`` read model for the Phase 6 read-only operator Kanban.

Thin ``PollingReadModel`` subclass: periodic refresh on a per-instance worker
pool, main-thread Q_PROPERTY updates, last-known-data degradation. No backtests,
promotions, config edits, or risk-state resets — observability-first.

Extracted verbatim from ``read_models.py`` (PR12 decompose). No behavior
changed — the class was moved, not rewritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, Signal

from milodex.gui.polling_lifecycle import PollingReadModel
from milodex.gui.snapshot_builders import build_kanban_snapshot


class KanbanState(PollingReadModel):
    """Read model for the Phase 6 read-only operator Kanban."""

    lanesChanged = Signal()  # noqa: N815
    summaryChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path,
        configs_dir: Path,
        refresh_interval_ms: int = 30_000,
        locks_dir: Path | None = None,
    ) -> None:
        if locks_dir is None:
            from milodex.config import get_locks_dir

            locks_dir = get_locks_dir()
        self._lanes: list[dict[str, Any]] = []
        self._summary: dict[str, Any] = {}
        super().__init__(
            builder=lambda: build_kanban_snapshot(db_path, configs_dir, locks_dir),
            refresh_interval_ms=refresh_interval_ms,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        lanes = list(result.get("lanes") or [])
        summary = dict(result.get("summary") or {})
        if lanes != self._lanes:
            self._lanes = lanes
            self.lanesChanged.emit()
        if summary != self._summary:
            self._summary = summary
            self.summaryChanged.emit()

    def _get_lanes(self) -> list:
        return self._lanes

    def _get_summary(self) -> dict:
        return self._summary

    lanes = Property("QVariantList", _get_lanes, notify=lanesChanged)
    summary = Property("QVariantMap", _get_summary, notify=summaryChanged)
