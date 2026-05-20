"""GUI read models for the Phase 5 observability surfaces.

The classes in this module expose *read-only* state to QML.  They follow the
same lifecycle contract as ``OperationalState`` and ``StrategyBankState``:
periodic refresh on a per-instance worker pool, main-thread Q_PROPERTY
updates, and graceful degradation that preserves last-known data after a
successful refresh.

No class here runs backtests, promotes, demotes, edits configs, or resets risk
state.  The GUI surfaces that bind to these models remain observability-first.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import Property, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot

from milodex.gui.bench_v1 import (
    BenchStrategyState,
    EvidenceRecord,
    Freshness,
    GateResult,
    Stage,
    compute_menu_items,
)
from milodex.promotion.state_machine import MAX_DRAWDOWN_PCT, MIN_SHARPE, MIN_TRADES
from milodex.strategies.loader import StrategyConfig, load_strategy_config

logger = logging.getLogger(__name__)

_STAGES = ("backtest", "paper", "micro_live", "live")
_VISIBLE_STAGES = ("idle", "backtest", "paper", "micro_live", "live")
_CONFIG_SKIP_PREFIXES = ("universe_",)
_CONFIG_SKIP_NAMES = {"risk_defaults.yaml", "sample_strategy.yaml"}


@dataclass(frozen=True)
class _StrategyRow:
    strategy_id: str
    name: str
    display_name_source: str
    stage: str
    description: str
    config_path: str
    family: str
    template: str
    enabled: bool
    sharpe: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None
    evidence_run_id: str = ""
    promoted_at: str = ""
    promotion_type: str = ""
    gate_failures: tuple[str, ...] = ()
    status_kind: str = "info"
    status_word: str = "Configured"
    status_tail: str = "ready for evidence review."
    meta_line: str = ""
    meta_config_key: str = ""
    meta_stage: str = ""
    meta_evidence_label: str = ""
    meta_evidence_at: str = ""
    session_state: str = "not_running"
    session_id: str = ""
    session_detail: str = ""
    paper_evidence: dict[str, Any] = field(default_factory=dict)
    job_id: str = ""
    job_status: str = ""
    job_action_type: str = ""
    job_detail: str = ""
    visual_priority: int = 0
    # Bench v1 read-model schema (ADR 0050). Populated empty by default;
    # PR G will wire the menu computation and PR E will populate fixtures.
    # Not exposed in `as_qml()` yet — that wiring is PR G's scope.
    evidence_by_stage: dict[Stage, EvidenceRecord] = field(default_factory=dict)
    runs_in_flight: dict[Stage, bool] = field(default_factory=dict)

    def as_qml(self) -> dict[str, Any]:
        return {
            "strategyId": self.strategy_id,
            "name": self.name,
            "displayName": self.name,
            "displayNameSource": self.display_name_source,
            "stage": self.stage,
            "description": self.description,
            "configPath": self.config_path,
            "family": self.family,
            "template": self.template,
            "enabled": self.enabled,
            "sharpe": self.sharpe,
            "maxDrawdownPct": self.max_drawdown_pct,
            "tradeCount": self.trade_count or 0,
            "evidenceRunId": self.evidence_run_id,
            "promotedAt": self.promoted_at,
            "promotionType": self.promotion_type,
            "gateFailures": list(self.gate_failures),
            "statusKind": self.status_kind,
            "statusWord": self.status_word,
            "statusTail": self.status_tail,
            "metaLine": self.meta_line,
            "metaConfigKey": self.meta_config_key,
            "metaStage": self.meta_stage,
            "metaEvidenceLabel": self.meta_evidence_label,
            "metaEvidenceAt": self.meta_evidence_at,
            "sessionState": self.session_state,
            "sessionId": self.session_id,
            "sessionDetail": self.session_detail,
            "paperEvidence": dict(self.paper_evidence),
            "jobId": self.job_id,
            "jobStatus": self.job_status,
            "jobActionType": self.job_action_type,
            "jobDetail": self.job_detail,
            "visualPriority": self.visual_priority,
            "actions": _compute_bench_action_menu(self),
            "evidencePacket": _evidence_packet(self),
        }


class _RefreshSignals(QObject):
    completed = Signal(dict)
    failed = Signal(str)


class _RefreshRunnable(QRunnable):
    def __init__(self, builder: Callable[[], dict[str, Any]], signals: _RefreshSignals) -> None:
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


class _PollingReadModel(QObject):
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

        self._signals = _RefreshSignals(self)
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
        self._thread_pool.start(_RefreshRunnable(self._builder, self._signals))

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


class FrontPageState(_PollingReadModel):
    """Read model for the calm FRONT digest."""

    summaryChanged = Signal()  # noqa: N815

    def __init__(self, db_path: Path, configs_dir: Path, refresh_interval_ms: int = 30_000) -> None:
        self._summary: dict[str, Any] = _empty_front_summary()
        super().__init__(
            builder=lambda: build_front_page_snapshot(db_path, configs_dir),
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


class BenchState(_PollingReadModel):
    """Read model for the Phase 5 view-only strategy bench."""

    sectionsChanged = Signal()  # noqa: N815
    selectedStrategyChanged = Signal()  # noqa: N815

    def __init__(self, db_path: Path, configs_dir: Path, refresh_interval_ms: int = 30_000) -> None:
        self._sections: list[dict[str, Any]] = []
        self._selected_strategy_id = ""
        super().__init__(
            builder=lambda: build_bench_snapshot(db_path, configs_dir),
            refresh_interval_ms=refresh_interval_ms,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        sections = list(result.get("sections") or [])
        if sections != self._sections:
            self._sections = sections
            self.sectionsChanged.emit()

    @Slot(str)
    def selectStrategy(self, strategy_id: str) -> None:  # noqa: N802
        if strategy_id != self._selected_strategy_id:
            self._selected_strategy_id = strategy_id
            self.selectedStrategyChanged.emit()

    def _get_sections(self) -> list:
        return self._sections

    def _get_selected_strategy_id(self) -> str:
        return self._selected_strategy_id

    sections = Property("QVariantList", _get_sections, notify=sectionsChanged)
    selectedStrategyId = Property(str, _get_selected_strategy_id, notify=selectedStrategyChanged)  # noqa: N815


class KanbanState(_PollingReadModel):
    """Read model for the Phase 6 read-only operator Kanban."""

    lanesChanged = Signal()  # noqa: N815
    summaryChanged = Signal()  # noqa: N815

    def __init__(self, db_path: Path, configs_dir: Path, refresh_interval_ms: int = 30_000) -> None:
        self._lanes: list[dict[str, Any]] = []
        self._summary: dict[str, Any] = {}
        super().__init__(
            builder=lambda: build_kanban_snapshot(db_path, configs_dir),
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


class LedgerState(_PollingReadModel):
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
        "backtest": frozenset({"backtested", "backtested_strong", "backtested_paper", "backtested_weak"}),
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


def build_front_page_snapshot(db_path: Path, configs_dir: Path) -> dict[str, Any]:
    rows = _strategy_rows(db_path, configs_dir)
    stage_counts = _stage_counts(rows)
    running_count = sum(1 for row in rows if row.stage in {"paper", "micro_live", "live"})
    feature = _feature_row(rows)
    summary = _empty_front_summary()
    summary.update(
        {
            "asOf": _today_label(),
            "totalConfigs": len(rows),
            "runningCount": running_count,
            "liveCount": stage_counts["micro_live"] + stage_counts["live"],
            "stageTally": stage_counts,
            "feature": feature.as_qml() if feature is not None else {},
            "market": _market_placeholder(),
            "pnl": _latest_pnl(db_path),
            "lastRefreshedAt": _now_iso(),
        }
    )
    return {"summary": summary, "lastRefreshedAt": summary["lastRefreshedAt"]}


def build_bench_snapshot(db_path: Path, configs_dir: Path) -> dict[str, Any]:
    rows = _strategy_rows(db_path, configs_dir)
    labels = {
        "idle": ("i.", "Idle", "configured, not yet run"),
        "backtest": ("ii.", "Backtest", "historical evidence and gate verdicts"),
        "paper": ("iii.", "Paper", "live feed, no capital"),
        "micro_live": ("iv.", "Micro live", "locked in Phase 5"),
        "live": ("v.", "Live", "locked in Phase 5"),
    }
    sections = []
    for stage in _VISIBLE_STAGES:
        roman, name, caption = labels[stage]
        strategies = [row.as_qml() for row in rows if row.stage == stage]
        sections.append(
            {
                "stage": stage,
                "stageRoman": roman,
                "stageName": name,
                "stageCaption": caption,
                "strategies": strategies,
            }
        )
    return {"sections": sections, "lastRefreshedAt": _now_iso()}


def build_kanban_snapshot(db_path: Path, configs_dir: Path) -> dict[str, Any]:
    rows = _strategy_rows(db_path, configs_dir)
    labels = {
        "idle": ("i.", "Idle", "configured, no action queued"),
        "backtest": ("ii.", "Backtest", "historical evidence review"),
        "paper": ("iii.", "Paper", "live feed, no capital"),
        "micro_live": ("iv.", "Micro live", "locked by ADR 0004"),
        "live": ("v.", "Live", "locked by ADR 0004"),
    }
    lanes: list[dict[str, Any]] = []
    for lane in _VISIBLE_STAGES:
        roman, name, caption = labels[lane]
        cards = []
        for row in rows:
            kanban_lane = _kanban_lane(row)
            if kanban_lane != lane:
                continue
            card = row.as_qml()
            card.update(
                {
                    "promotionStage": row.stage,
                    "kanbanLane": kanban_lane,
                    "eligibilityVerdict": _eligibility_verdict(row),
                    "eligibilityCopy": _eligibility_copy(row),
                }
            )
            cards.append(card)
        lanes.append(
            {
                "lane": lane,
                "laneRoman": roman,
                "laneName": name,
                "laneCaption": caption,
                "cards": cards,
            }
        )
    return {
        "lanes": lanes,
        "summary": {
            "totalConfigs": len(rows),
            "lockedStages": ["micro_live", "live"],
            "lastRefreshedAt": _now_iso(),
        },
        "lastRefreshedAt": _now_iso(),
    }


def build_ledger_snapshot(db_path: Path, configs_dir: Path | None = None) -> dict[str, Any]:
    # When configs_dir is None, pass a non-existent sentinel so _new_strategy_entries
    # produces only event-store-backed rows (no YAML mtime fallback).
    _configs = configs_dir if configs_dir is not None else Path("__no_configs__")
    return {"entries": _ledger_entries(db_path, _configs), "lastRefreshedAt": _now_iso()}


def _strategy_rows(db_path: Path, configs_dir: Path) -> list[_StrategyRow]:
    configs = _load_strategy_configs(configs_dir)
    latest_runs = _latest_backtest_metrics(db_path)
    promotions = _latest_promotions(db_path)
    sessions = _latest_session_states(db_path)
    jobs = _latest_orchestration_jobs(db_path)
    rows: list[_StrategyRow] = []
    for config in configs:
        metrics = latest_runs.get(config.strategy_id, {})
        promotion = promotions.get(config.strategy_id, {})
        session = sessions.get(config.strategy_id, {})
        job = jobs.get(config.strategy_id, {})
        activity = _card_activity(session, job)
        sharpe = _float_or_none(promotion.get("sharpe_ratio"), metrics.get("sharpe"))
        max_dd = _float_or_none(promotion.get("max_drawdown_pct"), metrics.get("max_drawdown_pct"))
        trade_count = _int_or_none(promotion.get("trade_count"), metrics.get("trade_count"))
        failures = tuple(_gate_failures(sharpe, max_dd, trade_count, config.family))
        evidence_by_stage = _bench_evidence_by_stage(metrics, config.family)
        runs_in_flight = _bench_runs_in_flight(job)
        status_kind, status_word, status_tail = _status_copy(
            config.stage, failures, sharpe, max_dd, trade_count
        )
        meta_config_key = f"{config.family}.{config.template}"
        meta_evidence_label, meta_evidence_at = _meta_evidence(promotion, metrics)
        display_name, display_name_source = _display_name(config)
        rows.append(
            _StrategyRow(
                strategy_id=config.strategy_id,
                name=display_name,
                display_name_source=display_name_source,
                stage=config.stage if config.stage in _VISIBLE_STAGES else "backtest",
                description=config.description,
                config_path=str(config.path),
                family=config.family,
                template=config.template,
                enabled=config.enabled,
                sharpe=sharpe,
                max_drawdown_pct=max_dd,
                trade_count=trade_count,
                evidence_run_id=str(
                    promotion.get("backtest_run_id") or metrics.get("run_id") or ""
                ),
                promoted_at=str(promotion.get("recorded_at") or ""),
                promotion_type=str(promotion.get("promotion_type") or ""),
                gate_failures=failures,
                status_kind=status_kind,
                status_word=status_word,
                status_tail=status_tail,
                meta_line=_meta_line(config, promotion, metrics),
                meta_config_key=meta_config_key,
                meta_stage=config.stage,
                meta_evidence_label=meta_evidence_label,
                meta_evidence_at=meta_evidence_at,
                session_state=activity["state"],
                session_id=activity["session_id"],
                session_detail=activity["detail"],
                paper_evidence=_paper_evidence(session),
                job_id=str(job.get("job_id") or ""),
                job_status=str(job.get("status") or ""),
                job_action_type=str(job.get("action_type") or ""),
                job_detail=str(job.get("detail") or ""),
                evidence_by_stage=evidence_by_stage,
                runs_in_flight=runs_in_flight,
            )
        )
    sorted_rows = sorted(rows, key=lambda row: (_VISIBLE_STAGES.index(row.stage), row.name.lower()))
    return [
        _StrategyRow(
            **{
                **row.__dict__,
                "visual_priority": index + 1,
            }
        )
        for index, row in enumerate(sorted_rows)
    ]


def _compute_bench_action_menu(row: _StrategyRow) -> list[dict[str, Any]]:
    """Compute the Action menu item list for a bench row via compute_menu_items.

    Constructs a BenchStrategyState from the row fields and delegates to the
    pure-function composer in bench_v1.  The legacy _bench_actions() path is
    removed: this is the only code path that produces the ``actions`` key
    exposed to QML.

    Evidence is derived upstream from durable state: completed backtest metrics
    drive BACKTEST Fresh+Pass/Fresh+Fail, missing backtests render as
    Missing+Pending, and non-terminal orchestration jobs populate
    ``runs_in_flight``. Operational paper controls still use session state.

    The returned list is a list of plain dicts so it serialises cleanly to
    QML's QVariantList.  Each dict carries:

      - ``label``: the operator-facing verb string (from bench_v1 locked labels)
      - ``verbClass``: ``"directional"``, ``"invocation"``, or ``"informational"``
      - ``targetStage``: the target stage string for directional verbs,
        or ``""`` for invocation / informational verbs
    """
    # Prefer the row's evidence_by_stage if it was populated upstream.
    evidence = row.evidence_by_stage

    if not evidence and row.stage in {"idle", "backtest"}:
        evidence = {Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING)}

    try:
        current_stage = Stage(row.stage)
    except ValueError:
        current_stage = Stage.IDLE

    state = BenchStrategyState(
        current_stage=current_stage,
        evidence_by_stage=evidence,
        runs_in_flight=row.runs_in_flight,
        is_session_running=row.session_state == "running",
    )

    items = compute_menu_items(state)
    return [
        {
            "label": item.label,
            "verbClass": item.verb_class,
            "targetStage": item.target_stage or "",
            "actionIntentPreview": _action_intent_preview(row, item),
        }
        for item in items
    ]


def _bench_evidence_by_stage(
    metrics: dict[str, Any],
    family: str,
) -> dict[Stage, EvidenceRecord]:
    sharpe = _float_or_none(metrics.get("sharpe"))
    max_dd = _float_or_none(metrics.get("max_drawdown_pct"))
    trade_count = _int_or_none(metrics.get("trade_count"))
    has_completed_backtest = bool(metrics.get("run_id")) or any(
        value is not None for value in (sharpe, max_dd, trade_count)
    )
    if not has_completed_backtest:
        return {Stage.BACKTEST: EvidenceRecord(Freshness.MISSING, GateResult.PENDING)}

    failures = _gate_failures(sharpe, max_dd, trade_count, family)
    return {
        Stage.BACKTEST: EvidenceRecord(
            Freshness.FRESH,
            GateResult.FAIL if failures else GateResult.PASS,
        )
    }


def _bench_runs_in_flight(job: dict[str, Any]) -> dict[Stage, bool]:
    if not job:
        return {}
    if str(job.get("status") or "") not in {"queued", "starting", "running"}:
        return {}
    if str(job.get("action_type") or "") in {
        "backtest",
        "backtest_single",
        "backtest_walk_forward",
    }:
        return {Stage.BACKTEST: True}
    return {}


# ---------------------------------------------------------------------------
# PR N: Action Intent Preview contract
#
# Stable, read-only preview metadata attached to every Bench action item. The
# QML confirmation modal renders from this object instead of recomputing the
# same classifications inline. The preview is descriptive only — `executable`
# and `wired` are both False in v1; ADR 0049 Decision 2 holds.
# ---------------------------------------------------------------------------

# Plain-language intent copy, keyed by action_kind. The exact wording matches
# the PR L QML _intentCopy() helper so the confirmation modal's prose remains
# identical whether read from the preview or the QML fallback.
_ACTION_INTENT_COPY: dict[str, str] = {
    "promote": (
        "Move this strategy forward from its current stage to the next stage "
        "after evidence and policy gates are satisfied."
    ),
    "demote": (
        "Move this strategy backward to an earlier stage and remove it from "
        "its current operating stage."
    ),
    "return": (
        "Restore this strategy to a previously eligible stage or return it to the idle shelf."
    ),
    "start_trading": (
        "Start a paper trading session for this strategy through the controlled "
        "runner boundary."
    ),
    "stop_trading": (
        "Request a controlled stop for the current paper session. This is not the kill switch."
    ),
    "initiate_backtest": (
        "Run canonical walk-forward backtest evidence for this strategy."
    ),
    "refresh_backtest": (
        "Refresh aging or stale evidence with the canonical walk-forward backtest."
    ),
    "open_evidence": (
        "Open the read-only Evidence snapshot for this strategy. "
        "Informational only — no state changes."
    ),
    "unknown": "Action not recognised by the intent preview.",
}

# Static enumeration of what a future Milodex would validate before this
# action could proceed. Copy-only — no real check is performed by including
# the action item in this list.
_ACTION_REQUIREMENTS: tuple[str, ...] = (
    "Evidence gate check",
    "Freshness check",
    "Operator confirmation",
    "Policy lock check",
    "Risk guard check",
    "Event write after confirmation",
)

# Display string identifying the kind of record a future event-store would
# write. NOT a class name, NOT a function, NOT a payload — purely a label
# for operator orientation.
_ACTION_FUTURE_RECORD: dict[str, str] = {
    "promote": "promotion_event",
    "demote": "demotion_event",
    "return": "stage_return_event",
    "start_trading": "session_start_event",
    "stop_trading": "session_stop_event",
    "initiate_backtest": "backtest_run",
    "refresh_backtest": "backtest_run",
    "open_evidence": "evidence_view",
    "unknown": "—",
}


# Verbatim source-note string for every actionIntentPreview. Single-line
# literal so static grep-based safety tests can match substring-exactly.
_ACTION_PREVIEW_SOURCE_NOTE: str = (
    "Bench action intent previews are display metadata. Submit-capable actions "
    "must still validate through the Bench command bridge before state changes."
)

# Verbatim safety copy strings. These match the PR L QML _COPY_* constants
# so the confirmation modal renders the same prose whether sourced from the
# preview or the QML fallback. The strings MUST remain single-line literals
# so static grep-based safety tests continue to match substring-exactly.
_COPY_SAFETY_BOUNDARY: str = (
    "Bench renders this intent packet for review before any submit-capable action "
    "is validated through the command bridge."
)
_COPY_CAPITAL_LOCK_SHORT: str = (
    "Capital-bearing transitions remain locked while ADR 0004 is in force."
)
_COPY_PAPER_START: str = (
    "Paper-stage sessions use live feed with no capital exposure. "
    "Capital-bearing stages remain locked while ADR 0004 is in force."
)


def _action_kind(label: str) -> str:
    """Coarse classification from the action label.

    Promote/Demote/Return are prefix-matched (multiple target-stage suffixes
    exist); the invocation labels are fixed strings; Open Evidence is the
    informational floor.
    """
    if label.startswith("Promote to "):
        return "promote"
    if label.startswith("Demote to "):
        return "demote"
    if label.startswith("Return to "):
        return "return"
    if label == "Start Trading":
        return "start_trading"
    if label == "Stop Trading":
        return "stop_trading"
    if label == "Initiate Backtest":
        return "initiate_backtest"
    if label == "Refresh Backtest":
        return "refresh_backtest"
    if label == "Freeze Manifest":
        return "freeze_manifest"
    if label == "Open Evidence":
        return "open_evidence"
    return "unknown"


def _is_capital_bearing(label: str, target_stage: str, current_stage: str) -> bool:
    """Classify whether an action crosses ADR 0004 capital-bearing territory.

    Mirrors the PR L QML `_isCapitalBoundary` helper, including the paper-
    stage Start Trading refinement: paper sessions use live feed with no
    capital exposure and are NOT capital-bearing.
    """
    if target_stage in {"micro_live", "live"}:
        return True
    if "Micro Live" in label or "Live" in label:
        return True
    if label == "Start Trading":
        return current_stage in {"micro_live", "live"}
    return False


def _safety_copy(label: str, current_stage: str, capital_bearing: bool) -> str:
    base = _COPY_SAFETY_BOUNDARY
    if label == "Start Trading" and current_stage == "paper":
        return base + "\n\n" + _COPY_PAPER_START
    if capital_bearing:
        return base + "\n\n" + _COPY_CAPITAL_LOCK_SHORT
    return base


def _is_submit_capable_action(kind: str, target_stage: str, current_stage: str) -> bool:
    if kind in {
        "demote",
        "freeze_manifest",
        "initiate_backtest",
        "refresh_backtest",
    }:
        return True
    if kind == "promote":
        return target_stage == "paper"
    if kind == "return":
        return target_stage == "idle"
    if kind in {"start_trading", "stop_trading"}:
        return current_stage == "paper"
    return False


def _action_intent_preview(row: _StrategyRow, item: Any) -> dict[str, Any]:
    """Normalized read-only Action Intent Preview (PR N, ADR 0049).

    Carries the descriptive metadata a confirmation modal needs to render
    an Intent Packet without recomputing classifications in QML. This is a
    *preview*, never an executable command:

      - ``executable`` MUST stay False
      - ``wired`` MUST stay False

    until command infrastructure lands behind a separate ADR. Downstream UI
    reads these flags to keep the v1 framing explicit.

    The preview never carries a command payload, a proposal object, or any
    field whose name implies execution (submit, dispatch, broker, event).
    """
    label = item.label
    target_stage = item.target_stage or ""
    kind = _action_kind(label)
    capital_bearing = _is_capital_bearing(label, target_stage, row.stage)
    submit_capable = _is_submit_capable_action(kind, target_stage, row.stage)
    return {
        "schemaVersion": 1,
        "source": {
            "kind": "gui_read_model_preview",
            "authoritative": False,
            "note": _ACTION_PREVIEW_SOURCE_NOTE,
        },
        "strategyId": row.strategy_id,
        "strategyName": row.name,
        "actionKind": kind,
        "actionLabel": label,
        "verbClass": item.verb_class,
        "currentStage": row.stage,
        "targetStage": target_stage,
        "intentCopy": _ACTION_INTENT_COPY.get(kind, _ACTION_INTENT_COPY["unknown"]),
        "requirements": list(_ACTION_REQUIREMENTS),
        "futureRecord": _ACTION_FUTURE_RECORD.get(kind, "—"),
        "capitalBearing": capital_bearing,
        "safetyCopy": _safety_copy(label, row.stage, capital_bearing),
        "executable": submit_capable,
        "wired": submit_capable,
    }


def _evidence_packet(row: _StrategyRow) -> dict[str, Any]:
    """Normalized read-only Evidence Packet for a Bench row (PR M, ADR 0049).

    Consolidates the scattered evidence-related fields already carried on
    _StrategyRow into a single, stable contract for the Evidence modal and
    the Intent Packet preview.  This is a *shape*, not a reconstruction:
    freshness and gate verdicts are not authoritative in v1 — the packet
    only carries what the GUI read-model already exposes.

    Real event-derived freshness and gate reconstruction are deferred
    (ADR 0049 Decision 5). The ``source.authoritative`` flag MUST stay
    False and the ``gate.reconstructionDeferred`` flag MUST stay True
    until real event-derived evidence reconstruction lands; downstream
    UI uses them to keep the v1 framing explicit.
    """
    return {
        "schemaVersion": 1,
        "strategyId": row.strategy_id,
        "strategyName": row.name,
        "currentStage": row.stage,
        "source": {
            "kind": "gui_read_model_snapshot",
            "authoritative": False,
            "note": (
                "Bench v1 evidence is normalized from the current GUI read-model "
                "snapshot. Real event-derived freshness and gate reconstruction "
                "are deferred."
            ),
        },
        "metrics": {
            "sharpe": row.sharpe,
            "maxDrawdownPct": row.max_drawdown_pct,
            "tradeCount": row.trade_count,
        },
        "evidence": {
            "runId": row.evidence_run_id,
            "label": row.meta_evidence_label,
            "observedAt": row.meta_evidence_at,
            "promotedAt": row.promoted_at,
            "promotionType": row.promotion_type,
        },
        "gate": {
            "failures": list(row.gate_failures),
            "freshness": "not_reconstructed_v1",
            "gateResult": "not_reconstructed_v1",
            "reconstructionDeferred": True,
        },
        "status": {
            "kind": row.status_kind,
            "word": row.status_word,
            "tail": row.status_tail,
            "metaLine": row.meta_line,
        },
        "session": {
            "state": row.session_state,
            "id": row.session_id,
            "detail": row.session_detail,
        },
        "paperEvidence": dict(row.paper_evidence),
        "job": {
            "id": row.job_id,
            "status": row.job_status,
            "actionType": row.job_action_type,
            "detail": row.job_detail,
        },
    }


def _load_strategy_configs(configs_dir: Path) -> list[StrategyConfig]:
    result: list[StrategyConfig] = []
    for path in sorted(configs_dir.glob("*.yaml")):
        if path.name in _CONFIG_SKIP_NAMES or path.name.startswith(_CONFIG_SKIP_PREFIXES):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict) or "strategy" not in data:
            continue
        try:
            result.append(load_strategy_config(path))
        except ValueError as exc:
            logger.warning("Skipping invalid strategy config %s: %s", path, exc)
    return result


def _latest_backtest_metrics(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT br.strategy_id, br.run_id, br.started_at, br.metadata_json
            FROM backtest_runs br
            INNER JOIN (
                SELECT strategy_id, MAX(id) AS max_id
                FROM backtest_runs
                WHERE status = 'completed'
                GROUP BY strategy_id
            ) latest ON latest.strategy_id = br.strategy_id AND latest.max_id = br.id
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = _json(row["metadata_json"])
        aggregate = metadata.get("oos_aggregate", {}) if isinstance(metadata, dict) else {}
        result[row["strategy_id"]] = {
            "run_id": row["run_id"],
            "started_at": row["started_at"],
            "sharpe": aggregate.get("sharpe"),
            "max_drawdown_pct": aggregate.get("max_drawdown_pct"),
            "trade_count": aggregate.get("trade_count"),
        }
    return result


def _latest_promotions(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT p.*
            FROM promotions p
            INNER JOIN (
                SELECT strategy_id, MAX(id) AS max_id
                FROM promotions
                WHERE promotion_type NOT IN ('demotion', 'stage_return')
                GROUP BY strategy_id
            ) latest ON latest.strategy_id = p.strategy_id AND latest.max_id = p.id
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {row["strategy_id"]: dict(row) for row in rows}


def _promotion_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ledger rows from the promotions table (promoted/demoted/returned)."""
    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute("SELECT * FROM promotions ORDER BY id DESC LIMIT 200").fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        promotion_type = str(row["promotion_type"])
        if promotion_type == "demotion":
            outcome_kind = "demoted"
        elif promotion_type == "stage_return":
            outcome_kind = "returned"
        else:
            outcome_kind = "promoted"
        entries.append(
            {
                "timestamp": row["recorded_at"],
                "displayTimestamp": _compact_timestamp(str(row["recorded_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": row["to_stage"],
                "transition": f"{row['from_stage']} -> {row['to_stage']}",
                "outcome": _ledger_outcome_label(outcome_kind),
                "outcomeKind": outcome_kind,
                "reason": row["notes"] or promotion_type,
                "recent": True,
            }
        )
    return entries


def _kill_switch_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ledger rows from kill_switch_events."""
    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            "SELECT * FROM kill_switch_events ORDER BY id DESC LIMIT 100"
        ).fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        entries.append(
            {
                "timestamp": row["recorded_at"],
                "displayTimestamp": _compact_timestamp(str(row["recorded_at"])),
                "strategyId": "",
                "subject": "kill switch",
                "stage": "system",
                "transition": "session",
                "outcome": str(row["event_type"]).upper(),
                "outcomeKind": "fired" if row["event_type"] == "triggered" else "info",
                "reason": row["reason"] or "",
                "recent": True,
            }
        )
    return entries


def _session_start_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ledger rows for strategy session starts."""
    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            """
            SELECT strategy_id, started_at, session_id
            FROM strategy_runs
            WHERE started_at IS NOT NULL
            ORDER BY started_at DESC LIMIT 200
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        entries.append(
            {
                "timestamp": row["started_at"],
                "displayTimestamp": _compact_timestamp(str(row["started_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": "lifecycle",
                "transition": "session",
                "outcome": "STARTED",
                "outcomeKind": "started",
                "reason": "trading session began",
                "recent": True,
            }
        )
    return entries


def _session_stop_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Sessions ended for operator-initiated reasons. EXCLUDES kill-switch
    and orphan-recovered closures (those emit their own ledger rows from
    kill_switch_events or are synthetic reconciliation rows)."""
    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            """
            SELECT strategy_id, ended_at, exit_reason, session_id
            FROM strategy_runs
            WHERE ended_at IS NOT NULL
              AND exit_reason NOT IN ('kill_switch', 'orphan_recovered')
            ORDER BY ended_at DESC LIMIT 200
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        entries.append(
            {
                "timestamp": row["ended_at"],
                "displayTimestamp": _compact_timestamp(str(row["ended_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": "lifecycle",
                "transition": "session",
                "outcome": "STOPPED",
                "outcomeKind": "stopped",
                "reason": row["exit_reason"] or "stopped",
                "recent": True,
            }
        )
    return entries


def _backtest_complete_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Ledger rows for completed backtest runs.

    Actual columns (confirmed via PRAGMA): ended_at, status, metadata_json.
    Sharpe lives at metadata_json.oos_aggregate.sharpe.
    Three-tone color binding to promotion policy thresholds.
    """
    from milodex.promotion.policy import ACTIVE_PROMOTION_POLICY  # noqa: PLC0415

    paper_gate = ACTIVE_PROMOTION_POLICY.paper_gate.min_sharpe
    capital_gate = ACTIVE_PROMOTION_POLICY.capital_gate.min_sharpe

    entries: list[dict[str, Any]] = []
    try:
        rows = conn.execute(
            """
            SELECT id, strategy_id, ended_at, status,
                   json_extract(metadata_json, '$.oos_aggregate.sharpe') AS sharpe,
                   json_extract(metadata_json, '$.oos_aggregate.max_drawdown_pct') AS max_dd,
                   json_extract(metadata_json, '$.oos_aggregate.trade_count') AS n
            FROM backtest_runs
            WHERE status = 'completed' AND ended_at IS NOT NULL
            ORDER BY ended_at DESC LIMIT 200
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    for row in rows:
        sharpe = row["sharpe"]
        if sharpe is None:
            kind = "backtested"
        elif sharpe >= capital_gate:
            kind = "backtested_strong"
        elif sharpe >= paper_gate:
            kind = "backtested_paper"
        else:
            kind = "backtested_weak"

        reason_parts = []
        if sharpe is not None:
            reason_parts.append(f"Sharpe {sharpe:.2f}")
        if row["max_dd"] is not None:
            reason_parts.append(f"max-dd {abs(row['max_dd']) * 100:.1f}%")
        if row["n"] is not None:
            reason_parts.append(f"n={row['n']}")
        reason = " · ".join(reason_parts) or "completed"

        entries.append(
            {
                "timestamp": row["ended_at"],
                "displayTimestamp": _compact_timestamp(str(row["ended_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": "backtest",
                "transition": "backtest",
                "outcome": "COMPLETED",
                "outcomeKind": kind,
                "reason": reason,
                "recent": True,
            }
        )
    return entries


def _new_strategy_entries(conn: sqlite3.Connection, configs_dir: Path) -> list[dict[str, Any]]:
    """First appearance per strategy_id across event tables.

    YAML mtime fallback only for strategies with no event-store history.
    """
    try:
        rows = conn.execute(
            """
            WITH first_seen AS (
                SELECT strategy_id, recorded_at AS first_at FROM promotions
                UNION ALL
                SELECT strategy_id, started_at FROM strategy_runs WHERE started_at IS NOT NULL
                UNION ALL
                SELECT strategy_id, started_at FROM backtest_runs WHERE started_at IS NOT NULL
            )
            SELECT strategy_id, MIN(first_at) AS first_at FROM first_seen GROUP BY strategy_id
            """
        ).fetchall()
    except sqlite3.Error:
        rows = []

    seen_with_history: dict[str, str] = {row["strategy_id"]: row["first_at"] for row in rows}

    entries: list[dict[str, Any]] = []
    for sid, first_at in seen_with_history.items():
        entries.append(
            {
                "timestamp": first_at,
                "displayTimestamp": _compact_timestamp(str(first_at)),
                "strategyId": sid,
                "subject": _short_strategy_name(sid),
                "stage": "system",
                "transition": "registration",
                "outcome": "ADDED",
                "outcomeKind": "added",
                "reason": "strategy first appeared in event store",
                "recent": True,
            }
        )

    # Fallback: strategies present in configs/ with no event-store ancestry.
    for yaml_path in Path(configs_dir).glob("*.yaml"):
        sid = _strategy_id_from_yaml(yaml_path)
        if sid and sid not in seen_with_history:
            mtime_iso = datetime.fromtimestamp(
                yaml_path.stat().st_mtime, tz=UTC
            ).isoformat()
            entries.append(
                {
                    "timestamp": mtime_iso,
                    "displayTimestamp": _compact_timestamp(mtime_iso),
                    "strategyId": sid,
                    "subject": _short_strategy_name(sid),
                    "stage": "system",
                    "transition": "registration",
                    "outcome": "ADDED",
                    "outcomeKind": "added",
                    "reason": "config file mtime (no event-store history)",
                    "recent": True,
                }
            )
    return entries


_LEDGER_SOURCE_PRIORITY = {
    "promoted": 0,
    "demoted": 0,
    "returned": 0,
    "fired": 1,
    "info": 1,
    "started": 2,
    "stopped": 2,
    "backtested_strong": 3,
    "backtested_paper": 3,
    "backtested_weak": 3,
    "backtested": 3,
    "added": 4,
}


def _ledger_entries(db_path: Path, configs_dir: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        entries: list[dict[str, Any]] = []
        entries += _promotion_entries(conn)
        entries += _kill_switch_entries(conn)
        entries += _session_start_entries(conn)
        entries += _session_stop_entries(conn)
        entries += _backtest_complete_entries(conn)
        entries += _new_strategy_entries(conn, configs_dir)
    finally:
        conn.close()
    return sorted(
        entries,
        key=lambda e: (
            # Primary: newest first
            str(e.get("timestamp") or ""),
            # Secondary: lower priority number = higher position when timestamps equal
            -_LEDGER_SOURCE_PRIORITY.get(str(e.get("outcomeKind") or ""), 5),
        ),
        reverse=True,
    )


def _latest_session_states(db_path: Path) -> dict[str, dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT sr.*,
                   (
                       SELECT COUNT(*)
                       FROM trades t
                       WHERE t.session_id = sr.session_id
                         AND t.source = 'paper'
                   ) AS trade_count
            FROM strategy_runs sr
            INNER JOIN (
                SELECT strategy_id, MAX(id) AS max_id
                FROM strategy_runs
                GROUP BY strategy_id
            ) latest ON latest.strategy_id = sr.strategy_id AND latest.max_id = sr.id
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    result: dict[str, dict[str, Any]] = {}
    failure_reasons = {"crashed", "failed", "kill_switch", "orphan_recovered", "error"}
    for row in rows:
        exit_reason = str(row["exit_reason"] or "")
        if row["ended_at"] in (None, ""):
            state = "running"
            detail = "session active"
        elif exit_reason in failure_reasons:
            state = "failed"
            detail = exit_reason
        else:
            state = "stopped"
            detail = exit_reason or "session ended"
        result[row["strategy_id"]] = {
            "state": state,
            "session_id": str(row["session_id"]),
            "started_at": str(row["started_at"] or ""),
            "ended_at": str(row["ended_at"] or ""),
            "exit_reason": exit_reason,
            "trade_count": int(row["trade_count"] or 0),
            "detail": detail,
        }
    return result


def _ledger_outcome_label(outcome_kind: str) -> str:
    if outcome_kind == "demoted":
        return "DEMOTED"
    if outcome_kind == "returned":
        return "RETURNED"
    return "PROMOTED"


def _latest_orchestration_jobs(db_path: Path) -> dict[str, dict[str, str]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT oj.*
            FROM orchestration_jobs oj
            INNER JOIN (
                SELECT strategy_id, MAX(id) AS max_id
                FROM orchestration_jobs
                WHERE status IN ('queued', 'starting', 'running')
                GROUP BY strategy_id
            ) latest ON latest.strategy_id = oj.strategy_id AND latest.max_id = oj.id
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {
        row["strategy_id"]: {
            "job_id": str(row["job_id"]),
            "status": str(row["status"]),
            "action_type": str(row["action_type"]),
            "detail": str(row["progress_label"] or row["error_message"] or ""),
            "cancel_requested_at": str(row["cancel_requested_at"] or ""),
        }
        for row in rows
    }


def _latest_pnl(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT recorded_at, daily_pnl, portfolio_value
            FROM portfolio_snapshots
            ORDER BY recorded_at DESC
            LIMIT 20
            """
        ).fetchall()
    except sqlite3.Error:
        return {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]}
    finally:
        conn.close()
    if not rows:
        return {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]}
    latest = rows[0]
    portfolio_value = float(latest["portfolio_value"] or 0.0)
    daily_pnl = float(latest["daily_pnl"] or 0.0)
    pct = 0.0 if portfolio_value == 0 else (daily_pnl / portfolio_value) * 100
    return {
        "today": daily_pnl,
        "todayPct": pct,
        "sparkline": [float(row["daily_pnl"] or 0.0) for row in reversed(rows)],
    }


def _market_placeholder() -> dict[str, Any]:
    return {
        "regime": "UNKNOWN",
        "regimeNote": "Market tape not wired yet.",
        "spyPct": 0.0,
        "qqqPct": 0.0,
        "iwmPct": 0.0,
        "weatherLine": "Market summary awaits a data-feed read model.",
        "tape": [],
    }


def _feature_row(rows: list[_StrategyRow]) -> _StrategyRow | None:
    candidates = [row for row in rows if row.status_kind in {"positive", "warning"}]
    if not candidates:
        candidates = rows
    return sorted(candidates, key=_queue_rank)[0] if candidates else None


def _queue_rank(row: _StrategyRow) -> tuple[int, float]:
    status_rank = {"positive": 0, "warning": 1, "info": 2, "negative": 3}.get(row.status_kind, 4)
    sharpe = row.sharpe if row.sharpe is not None else -999.0
    return status_rank, -sharpe


def _stage_counts(rows: list[_StrategyRow]) -> dict[str, int]:
    return {stage: sum(1 for row in rows if row.stage == stage) for stage in _VISIBLE_STAGES}


def _stage_label(stage: str) -> str:
    return {
        "idle": "Idle",
        "backtest": "Backtest",
        "paper": "Paper",
        "micro_live": "Micro Live",
        "live": "Live",
    }.get(stage, stage.replace("_", " ").title())


def _status_copy(
    stage: str,
    failures: tuple[str, ...],
    sharpe: float | None,
    max_dd: float | None,
    trade_count: int | None,
) -> tuple[str, str, str]:
    if sharpe is None and max_dd is None and trade_count is None:
        return "info", "Config valid", "awaiting evidence."
    if failures:
        labels = {"S": "Sharpe", "D": "Max-dd", "N": "Trades"}
        failed = ", ".join(labels[code] for code in failures)
        return "warning", f"{failed} gate failing", "view evidence before any move."
    if stage == "backtest":
        return "positive", "Gates pass", "paper-eligible through the CLI path."
    if stage == "paper":
        return "positive", "Paper evidence passing", "monitor live-feed behavior."
    return "info", "Evidence recorded", "stage remains governed by existing policy."


def _meta_line(config: StrategyConfig, promotion: dict[str, Any], metrics: dict[str, Any]) -> str:
    parts = [f"{config.family}.{config.template}", config.stage]
    if promotion.get("recorded_at"):
        parts.append(f"promoted {promotion['recorded_at']}")
    elif metrics.get("started_at"):
        parts.append(f"backtest {metrics['started_at']}")
    return " | ".join(parts)


def _meta_evidence(promotion: dict[str, Any], metrics: dict[str, Any]) -> tuple[str, str]:
    if promotion.get("recorded_at"):
        return "promoted", _compact_timestamp(str(promotion["recorded_at"]))
    if metrics.get("started_at"):
        return "backtest", _compact_timestamp(str(metrics["started_at"]))
    return "awaiting", "first run"


def _gate_failures(
    sharpe: float | None,
    max_dd: float | None,
    trade_count: int | None,
    family: str = "",
) -> list[str]:
    # Regime strategies are lifecycle-proof and exempt from statistical gate thresholds
    # (CLAUDE.md "Strategy bank, two roles"; SRS R-PRM-004).
    if family == "regime":
        return []
    failures: list[str] = []
    if sharpe is None or sharpe <= MIN_SHARPE:
        failures.append("S")
    if max_dd is None or max_dd >= MAX_DRAWDOWN_PCT:
        failures.append("D")
    if trade_count is None or trade_count < MIN_TRADES:
        failures.append("N")
    return failures


def _kanban_lane(row: _StrategyRow) -> str:
    if row.job_status in {"queued", "starting", "running"}:
        if row.job_action_type == "backtest_walk_forward":
            return "backtest"
        if row.job_action_type in {"paper_session_start", "micro_live_session_start"}:
            return row.stage
    if row.stage == "backtest" and not row.evidence_run_id:
        return "idle"
    return row.stage if row.stage in _VISIBLE_STAGES else "idle"


def _card_activity(session: dict[str, Any], job: dict[str, Any]) -> dict[str, str]:
    if job:
        state = (
            "canceling" if job.get("cancel_requested_at") else str(job.get("status") or "queued")
        )
        detail = str(job.get("detail") or job.get("action_type") or "orchestration job")
        if state == "canceling":
            detail = f"cancel requested | {detail}"
        return {
            "state": state,
            "session_id": "",
            "detail": detail,
        }
    return {
        "state": str(session.get("state") or "not_running"),
        "session_id": str(session.get("session_id") or ""),
        "detail": str(session.get("detail") or ""),
    }


def _paper_evidence(session: dict[str, Any]) -> dict[str, Any]:
    if not session:
        return {
            "available": False,
            "status": "missing",
            "tradeCount": 0,
        }
    state = str(session.get("state") or "not_running")
    exit_reason = str(session.get("exit_reason") or "")
    if state == "running":
        status = "running"
    elif exit_reason in {"controlled_stop", "interrupted"}:
        status = "completed"
    elif exit_reason in {"kill_switch", "orphan_recovered"} or exit_reason.startswith("crashed:"):
        status = "warning"
    else:
        status = "completed" if state == "stopped" else state
    return {
        "available": True,
        "status": status,
        "sessionId": str(session.get("session_id") or ""),
        "startedAt": str(session.get("started_at") or ""),
        "endedAt": str(session.get("ended_at") or ""),
        "exitReason": exit_reason,
        "tradeCount": int(session.get("trade_count") or 0),
        "detail": str(session.get("detail") or ""),
    }


def _eligibility_verdict(row: _StrategyRow) -> str:
    if row.stage in {"micro_live", "live"}:
        return "locked"
    if row.sharpe is None and row.max_drawdown_pct is None and row.trade_count is None:
        return "not_evaluated"
    if row.gate_failures:
        return "blocked"
    return "gate_passing"


def _eligibility_copy(row: _StrategyRow) -> str:
    if row.stage in {"micro_live", "live"}:
        return "Capital-bearing stages remain locked by ADR 0004; evidence is review-only."
    if row.sharpe is None and row.max_drawdown_pct is None and row.trade_count is None:
        return "Awaiting walk-forward evidence; no promotion action is available here."
    if row.gate_failures:
        labels = {"S": "Sharpe", "D": "max drawdown", "N": "trade count"}
        failed = ", ".join(labels[code] for code in row.gate_failures)
        return f"{failed} gate failing; review evidence before any move."
    if row.stage == "paper":
        return "Paper evidence passing; micro-live and live remain locked by ADR 0004."
    return "Backtest gates passing; promotion remains an explicit CLI/governance action."


def _display_name(config: StrategyConfig) -> tuple[str, str]:
    display_name = getattr(config, "display_name", None)
    if display_name:
        return str(display_name), "config"
    return _short_strategy_name(config.strategy_id), "derived"


def _short_strategy_name(strategy_id: str) -> str:
    parts = strategy_id.split(".")
    if len(parts) >= 3:
        raw = parts[2]
    else:
        raw = strategy_id
    return raw.replace("_", " ").replace("-", " ").title()


def _strategy_id_from_yaml(yaml_path: Path) -> str | None:
    """Extract strategy.id from a YAML config file.  Returns None on any error."""
    try:
        with yaml_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if isinstance(data, dict):
            return str(data["strategy"]["id"])
    except Exception:  # noqa: BLE001
        return None
    return None


def _float_or_none(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _int_or_none(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _today_label() -> str:
    now = datetime.now()
    if _supports_dash_day():
        return now.strftime("%A, %B %-d")
    return now.strftime("%A, %B %d").replace(" 0", " ")


def _compact_timestamp(timestamp: str) -> str:
    if not timestamp:
        return ""
    normalized = timestamp.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        if "T" in timestamp and len(timestamp) >= 16:
            return timestamp[:16].replace("T", " ")
        return timestamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def _supports_dash_day() -> bool:
    try:
        datetime.now().strftime("%-d")
    except ValueError:
        return False
    return True


def _empty_front_summary() -> dict[str, Any]:
    return {
        "asOf": "",
        "totalConfigs": 0,
        "runningCount": 0,
        "liveCount": 0,
        "stageTally": {stage: 0 for stage in _VISIBLE_STAGES},
        "feature": {},
        "market": _market_placeholder(),
        "pnl": {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]},
    }
