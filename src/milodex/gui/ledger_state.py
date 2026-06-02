"""``LedgerState`` read model for the filterable paper-of-record ledger.

Thin ``PollingReadModel`` subclass: periodic refresh on a per-instance worker
pool, main-thread Q_PROPERTY updates, last-known-data degradation. No backtests,
promotions, config edits, or risk-state resets — observability-first.

Extracted verbatim from ``read_models.py`` (PR12 decompose). No behavior
changed — the class was moved, not rewritten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, Signal, Slot

from milodex.gui.polling_lifecycle import PollingReadModel
from milodex.gui.snapshot_builders import build_ledger_snapshot


class LedgerState(PollingReadModel):
    """Read model for the filterable paper-of-record ledger."""

    entriesChanged = Signal()  # noqa: N815
    filtersChanged = Signal()  # noqa: N815

    def __init__(
        self,
        db_path: Path,
        configs_dir: Path | None = None,
        refresh_interval_ms: int = 30_000,
    ) -> None:
        self._all_entries: list[dict[str, Any]] = []
        self._entries: list[dict[str, Any]] = []
        self._stage_filter = "all"
        self._strategy_filter = "all"
        self._outcome_filter = "all"
        self._time_filter = "all"
        self._group_filter = "all"
        _configs = configs_dir if configs_dir is not None else Path("configs")
        super().__init__(
            builder=lambda: build_ledger_snapshot(db_path, _configs),
            refresh_interval_ms=refresh_interval_ms,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        entries = list(result.get("entries") or [])
        if entries != self._all_entries:
            self._all_entries = entries
            self._refilter()

    # Outcome-group → outcomeKind membership map (Task 22 / issue 09).
    _GROUP_KINDS: dict[str, frozenset[str]] = {
        "promotion": frozenset({"promoted", "demoted", "returned"}),
        "lifecycle": frozenset({"started", "stopped"}),
        "backtest": frozenset(
            {"backtested", "backtested_strong", "backtested_paper", "backtested_weak"}
        ),
        "system": frozenset({"fired", "info", "added"}),
    }

    @Slot(str, str, str, str)
    def setLedgerFilter(self, stage: str, strategy_id: str, outcome: str, time_range: str) -> None:  # noqa: N802
        self._stage_filter = stage or "all"
        self._strategy_filter = strategy_id or "all"
        self._outcome_filter = outcome or "all"
        self._time_filter = time_range or "all"
        self._group_filter = "all"
        self.filtersChanged.emit()
        self._refilter()

    @Slot(str)
    def setGroupFilter(self, group: str) -> None:  # noqa: N802
        """Filter by outcome group. Pass 'all' to clear. Resets outcome/stage filters."""
        self._group_filter = group or "all"
        self._outcome_filter = "all"
        self._stage_filter = "all"
        self.filtersChanged.emit()
        self._refilter()

    @Slot()
    def clearLedgerFilters(self) -> None:  # noqa: N802
        self.setLedgerFilter("all", "all", "all", "all")

    def _refilter(self) -> None:
        filtered: list[dict[str, Any]] = []
        group_kinds = self._GROUP_KINDS.get(self._group_filter)
        for entry in self._all_entries:
            if self._stage_filter != "all" and entry.get("stage") != self._stage_filter:
                continue
            if self._strategy_filter != "all" and entry.get("strategyId") != self._strategy_filter:
                continue
            if self._outcome_filter != "all" and entry.get("outcomeKind") != self._outcome_filter:
                continue
            if group_kinds is not None and entry.get("outcomeKind") not in group_kinds:
                continue
            # Time ranges are intentionally simple for Phase 5: "all" and "recent".
            if self._time_filter == "recent" and not bool(entry.get("recent")):
                continue
            filtered.append(entry)
        if filtered != self._entries:
            self._entries = filtered
            self.entriesChanged.emit()

    def _get_entries(self) -> list:
        return self._entries

    def _get_stage_filter(self) -> str:
        return self._stage_filter

    def _get_strategy_filter(self) -> str:
        return self._strategy_filter

    def _get_outcome_filter(self) -> str:
        return self._outcome_filter

    def _get_time_filter(self) -> str:
        return self._time_filter

    def _get_group_filter(self) -> str:
        return self._group_filter

    entries = Property("QVariantList", _get_entries, notify=entriesChanged)
    stageFilter = Property(str, _get_stage_filter, notify=filtersChanged)  # noqa: N815
    strategyFilter = Property(str, _get_strategy_filter, notify=filtersChanged)  # noqa: N815
    outcomeFilter = Property(str, _get_outcome_filter, notify=filtersChanged)  # noqa: N815
    timeFilter = Property(str, _get_time_filter, notify=filtersChanged)  # noqa: N815
    groupFilter = Property(str, _get_group_filter, notify=filtersChanged)  # noqa: N815
