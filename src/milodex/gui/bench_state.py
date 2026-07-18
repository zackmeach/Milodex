"""``BenchState`` read model for the Phase 5 view-only strategy bench.

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
from milodex.gui.snapshot_builders import build_bench_snapshot


class BenchState(PollingReadModel):
    """Read model for the Phase 5 view-only strategy bench."""

    sectionsChanged = Signal()  # noqa: N815

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
        self._sections: list[dict[str, Any]] = []
        super().__init__(
            builder=lambda: build_bench_snapshot(db_path, configs_dir, locks_dir),
            refresh_interval_ms=refresh_interval_ms,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        sections = list(result.get("sections") or [])
        if sections != self._sections:
            self._sections = sections
            self.sectionsChanged.emit()

    def _get_sections(self) -> list:
        return self._sections

    sections = Property("QVariantList", _get_sections, notify=sectionsChanged)
