"""``FrontPageState`` read model for the calm FRONT digest.

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
from milodex.gui.row_formatters import _empty_front_summary
from milodex.gui.snapshot_builders import build_front_page_snapshot


class FrontPageState(PollingReadModel):
    """Read model for the calm FRONT digest."""

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
        self._summary: dict[str, Any] = _empty_front_summary()
        super().__init__(
            builder=lambda: build_front_page_snapshot(db_path, configs_dir, locks_dir),
            refresh_interval_ms=refresh_interval_ms,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        summary = dict(result.get("summary") or {})
        if summary != self._summary:
            self._summary = summary
            self.summaryChanged.emit()

    def _get_summary(self) -> dict:
        return self._summary

    summary = Property("QVariantMap", _get_summary, notify=summaryChanged)
