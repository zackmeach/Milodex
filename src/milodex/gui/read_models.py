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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import Property, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot

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
    job_id: str = ""
    job_status: str = ""
    job_action_type: str = ""
    job_detail: str = ""
    visual_priority: int = 0

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
            "jobId": self.job_id,
            "jobStatus": self.job_status,
            "jobActionType": self.job_action_type,
            "jobDetail": self.job_detail,
            "visualPriority": self.visual_priority,
            "actions": _bench_actions(self),
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

    def __init__(self, db_path: Path, refresh_interval_ms: int = 30_000) -> None:
        self._all_entries: list[dict[str, Any]] = []
        self._entries: list[dict[str, Any]] = []
        self._stage_filter = "all"
        self._strategy_filter = "all"
        self._outcome_filter = "all"
        self._time_filter = "all"
        super().__init__(
            builder=lambda: build_ledger_snapshot(db_path),
            refresh_interval_ms=refresh_interval_ms,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        entries = list(result.get("entries") or [])
        if entries != self._all_entries:
            self._all_entries = entries
            self._refilter()

    @Slot(str, str, str, str)
    def setLedgerFilter(self, stage: str, strategy_id: str, outcome: str, time_range: str) -> None:  # noqa: N802
        self._stage_filter = stage or "all"
        self._strategy_filter = strategy_id or "all"
        self._outcome_filter = outcome or "all"
        self._time_filter = time_range or "all"
        self.filtersChanged.emit()
        self._refilter()

    @Slot()
    def clearLedgerFilters(self) -> None:  # noqa: N802
        self.setLedgerFilter("all", "all", "all", "all")

    def _refilter(self) -> None:
        filtered: list[dict[str, Any]] = []
        for entry in self._all_entries:
            if self._stage_filter != "all" and entry.get("stage") != self._stage_filter:
                continue
            if self._strategy_filter != "all" and entry.get("strategyId") != self._strategy_filter:
                continue
            if self._outcome_filter != "all" and entry.get("outcomeKind") != self._outcome_filter:
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

    entries = Property("QVariantList", _get_entries, notify=entriesChanged)
    stageFilter = Property(str, _get_stage_filter, notify=filtersChanged)  # noqa: N815
    strategyFilter = Property(str, _get_strategy_filter, notify=filtersChanged)  # noqa: N815
    outcomeFilter = Property(str, _get_outcome_filter, notify=filtersChanged)  # noqa: N815
    timeFilter = Property(str, _get_time_filter, notify=filtersChanged)  # noqa: N815


class DeskState(_PollingReadModel):
    """Read model for the dense DESK cockpit."""

    snapshotChanged = Signal()  # noqa: N815

    def __init__(self, db_path: Path, configs_dir: Path, refresh_interval_ms: int = 30_000) -> None:
        self._snapshot: dict[str, Any] = _empty_desk_snapshot()
        super().__init__(
            builder=lambda: build_desk_snapshot(db_path, configs_dir),
            refresh_interval_ms=refresh_interval_ms,
        )

    def _apply_result(self, result: dict[str, Any]) -> None:
        snapshot = dict(result.get("snapshot") or {})
        if snapshot != self._snapshot:
            self._snapshot = snapshot
            self.snapshotChanged.emit()

    def _get_snapshot(self) -> dict:
        return self._snapshot

    snapshot = Property("QVariantMap", _get_snapshot, notify=snapshotChanged)


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


def build_ledger_snapshot(db_path: Path) -> dict[str, Any]:
    return {"entries": _ledger_entries(db_path), "lastRefreshedAt": _now_iso()}


def build_desk_snapshot(db_path: Path, configs_dir: Path) -> dict[str, Any]:
    rows = _strategy_rows(db_path, configs_dir)
    stage_counts = _stage_counts(rows)
    queue = []
    for row in sorted(rows, key=lambda r: _queue_rank(r)):
        if row.status_kind not in {"positive", "warning"}:
            continue
        item = row.as_qml()
        item.update(
            {
                "from": row.stage,
                "to": _next_stage(row.stage),
                "days": "view",
                "note": f"{row.status_word} {row.status_tail}".strip(),
            }
        )
        queue.append(item)
        if len(queue) >= 6:
            break
    snapshot = _empty_desk_snapshot()
    snapshot.update(
        {
            "stageCounts": stage_counts,
            "stageRows": _desk_stage_rows(stage_counts, len(rows)),
            "strategyTotal": len(rows),
            "promotionQueue": queue,
            "runners": _runner_rows(db_path),
            "events": _event_rows(db_path),
            "system": _system_snapshot(db_path),
            "pnl": _latest_pnl(db_path),
            "market": _market_placeholder(),
            "lastRefreshedAt": _now_iso(),
        }
    )
    return {"snapshot": snapshot, "lastRefreshedAt": snapshot["lastRefreshedAt"]}


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
                job_id=str(job.get("job_id") or ""),
                job_status=str(job.get("status") or ""),
                job_action_type=str(job.get("action_type") or ""),
                job_detail=str(job.get("detail") or ""),
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


def _bench_action(
    action_id: str,
    label: str,
    kind: str,
    *,
    target_stage: str = "",
    requires_confirmation: bool = False,
    is_prototype_only: bool = True,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "id": action_id,
        "label": label,
        "kind": kind,
        "requiresConfirmation": requires_confirmation,
        "isPrototypeOnly": is_prototype_only,
    }
    if target_stage:
        action["targetStage"] = target_stage
    return action


def _bench_actions(row: _StrategyRow) -> list[dict[str, Any]]:
    actions = [
        _bench_action(
            "open_evidence",
            "Open Evidence",
            "evidence",
            is_prototype_only=False,
        )
    ]
    stage = row.stage
    has_open_job = row.job_status in {"queued", "starting", "running"}
    is_running = row.session_state == "running"

    if stage == "backtest" and not has_open_job:
        actions.append(_bench_action("initiate_backtest", "Initiate Backtest", "backtest"))

    if stage in {"backtest", "paper", "micro_live"} and not row.gate_failures:
        target = _next_stage(stage)
        if target != stage:
            actions.append(
                _bench_action(
                    f"promote_{target}",
                    f"Promote to {_stage_label(target)}",
                    "promote",
                    target_stage=target,
                    requires_confirmation=target in {"micro_live", "live"},
                )
            )

    if stage == "paper":
        actions.append(
            _bench_action(
                "demote_backtest",
                "Demote to Backtest",
                "demote",
                target_stage="backtest",
            )
        )
    elif stage == "micro_live":
        actions.append(
            _bench_action("demote_paper", "Demote to Paper", "demote", target_stage="paper")
        )
    elif stage == "live":
        actions.append(
            _bench_action(
                "demote_micro_live",
                "Demote to Micro Live",
                "demote",
                target_stage="micro_live",
            )
        )

    if stage in {"paper", "micro_live", "live"}:
        actions.append(
            _bench_action(
                "stop_trading" if is_running else "start_trading",
                "Stop Trading" if is_running else "Start Trading",
                "trading",
            )
        )

    if stage != "idle":
        actions.append(_bench_action("send_idle", "Send to Idle", "idle", target_stage="idle"))

    return actions


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
                WHERE promotion_type != 'demotion'
                GROUP BY strategy_id
            ) latest ON latest.strategy_id = p.strategy_id AND latest.max_id = p.id
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {row["strategy_id"]: dict(row) for row in rows}


def _ledger_entries(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    entries: list[dict[str, Any]] = []
    try:
        promotion_rows = conn.execute(
            "SELECT * FROM promotions ORDER BY id DESC LIMIT 200"
        ).fetchall()
        kill_rows = conn.execute(
            "SELECT * FROM kill_switch_events ORDER BY id DESC LIMIT 100"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    for row in promotion_rows:
        promotion_type = str(row["promotion_type"])
        outcome_kind = "demoted" if promotion_type == "demotion" else "promoted"
        entries.append(
            {
                "timestamp": row["recorded_at"],
                "displayTimestamp": _compact_timestamp(str(row["recorded_at"])),
                "strategyId": row["strategy_id"],
                "subject": _short_strategy_name(row["strategy_id"]),
                "stage": row["to_stage"],
                "transition": f"{row['from_stage']} -> {row['to_stage']}",
                "outcome": "DEMOTED" if outcome_kind == "demoted" else "PROMOTED",
                "outcomeKind": outcome_kind,
                "reason": row["notes"] or promotion_type,
                "recent": True,
            }
        )
    for row in kill_rows:
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
    return sorted(entries, key=lambda entry: str(entry.get("timestamp") or ""), reverse=True)


def _runner_rows(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, session_id, strategy_id, started_at, ended_at, exit_reason
            FROM strategy_runs
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [
        {
            "pid": row["session_id"],  # session_id is more meaningful than autoincrement rowid
            "sessionId": row["session_id"],
            "strategyId": row["strategy_id"],
            "name": _short_strategy_name(row["strategy_id"]),
            "startedAt": row["started_at"],
            "state": "running" if row["ended_at"] in (None, "") else "stopped",
            "detail": row["exit_reason"] or "session active",
        }
        for row in rows
    ]


def _latest_session_states(db_path: Path) -> dict[str, dict[str, str]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT sr.*
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
    result: dict[str, dict[str, str]] = {}
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
            "detail": detail,
        }
    return result


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


def _event_rows(db_path: Path) -> list[dict[str, Any]]:
    entries = _ledger_entries(db_path)[:12]
    return [
        {
            "ts": _short_time(str(entry["timestamp"])),
            "timestamp": entry["timestamp"],
            "kind": entry["outcome"],
            "subject": entry["subject"],
            "transition": entry["transition"],
            "reason": entry["reason"],
            "body": f"{entry['subject']} {entry['transition']}: {entry['reason']}",
            "kindType": entry["outcomeKind"],
        }
        for entry in entries
    ]


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


def _system_snapshot(db_path: Path) -> dict[str, Any]:
    return {
        "dbPresent": db_path.exists(),
        "riskMode": "paper locked",
        "feedLatency": "n/a",
        "drawdown": "n/a",
        "capitalDeployed": "n/a",
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


def _desk_stage_rows(stage_counts: dict[str, int], total: int) -> list[dict[str, Any]]:
    stages = [
        ("i.", "IDLE", "idle", "awaiting first run"),
        ("ii.", "BACKTEST", "backtest", "historical evidence"),
        ("iii.", "PAPER", "paper", "live feed, no capital"),
        ("iv.", "MICRO LIVE", "micro_live", "live capital, capped"),
        ("v.", "LIVE", "live", "full attribution"),
    ]
    return [
        {
            "tick": tick,
            "name": name,
            "stage": stage,
            "deck": deck,
            "strategyCount": stage_counts.get(stage, 0),
            "running": 0,
            "fillPct": 0.0 if total <= 0 else stage_counts.get(stage, 0) / total,
        }
        for tick, name, stage, deck in stages
    ]


def _next_stage(stage: str) -> str:
    if stage == "backtest":
        return "paper"
    if stage == "paper":
        return "micro_live"
    if stage == "micro_live":
        return "live"
    return stage


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


def _meta_evidence(
    promotion: dict[str, Any], metrics: dict[str, Any]
) -> tuple[str, str]:
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


def _card_activity(session: dict[str, str], job: dict[str, str]) -> dict[str, str]:
    if job:
        state = (
            "canceling"
            if job.get("cancel_requested_at")
            else str(job.get("status") or "queued")
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


def _short_time(timestamp: str) -> str:
    return timestamp[11:19] if len(timestamp) >= 19 else timestamp


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


def _empty_desk_snapshot() -> dict[str, Any]:
    return {
        "stageCounts": {stage: 0 for stage in _VISIBLE_STAGES},
        "stageRows": [],
        "strategyTotal": 0,
        "promotionQueue": [],
        "runners": [],
        "events": [],
        "system": {},
        "pnl": {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]},
        "market": _market_placeholder(),
    }
