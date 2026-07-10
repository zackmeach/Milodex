"""Snapshot builders for the Phase 5/6 GUI observability surfaces.

The four ``build_*_snapshot`` entry points the State read models poll, plus the
shared ``_strategy_rows`` query-master and the kanban/front-page projector
utilities.  These compose the leaf helpers (query_helpers, row_formatters,
bench_actions, ledger_builders) into the QML payloads.  Read-only throughout —
no class here runs backtests, promotes, edits configs, or resets risk state.

Extracted verbatim from ``read_models.py`` (PR12 decompose). No behavior
changed — definitions were moved, not rewritten.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from milodex.gui import _event_queries
from milodex.gui.bench_actions import _bench_evidence_by_stage, _bench_runs_in_flight
from milodex.gui.ledger_builders import _ledger_entries
from milodex.gui.query_helpers import (
    _latest_orchestration_jobs,
    _latest_pnl,
    _latest_promotions,
    _latest_session_states,
    _load_strategy_configs,
    _open_ro_conn,
)
from milodex.gui.row_formatters import (
    _VISIBLE_STAGES,
    _card_activity,
    _display_name,
    _empty_front_summary,
    _float_or_none,
    _int_or_none,
    _market_placeholder,
    _meta_evidence,
    _meta_line,
    _now_iso,
    _paper_evidence,
    _status_copy,
    _today_label,
)
from milodex.gui.strategy_bank_state import _compute_gate_failures
from milodex.gui.strategy_row import _StrategyRow


def build_front_page_snapshot(
    db_path: Path, configs_dir: Path, locks_dir: Path | None = None
) -> dict[str, Any]:
    _pnl_default = {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]}
    if not db_path.exists():
        rows = _strategy_rows(None, configs_dir, locks_dir)
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
                "pnl": _pnl_default,
                "lastRefreshedAt": _now_iso(),
            }
        )
        return {"summary": summary, "lastRefreshedAt": summary["lastRefreshedAt"]}
    conn = _open_ro_conn(db_path)
    try:
        rows = _strategy_rows(conn, configs_dir, locks_dir)
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
                "pnl": _latest_pnl(conn),
                "lastRefreshedAt": _now_iso(),
            }
        )
    finally:
        conn.close()
    return {"summary": summary, "lastRefreshedAt": summary["lastRefreshedAt"]}


def build_bench_snapshot(
    db_path: Path, configs_dir: Path, locks_dir: Path | None = None
) -> dict[str, Any]:
    labels = {
        "idle": ("i.", "Idle", "configured, not yet run"),
        "backtest": ("ii.", "Backtest", "historical evidence and gate verdicts"),
        "paper": ("iii.", "Paper", "live feed, no capital"),
        "micro_live": ("iv.", "Micro live", "locked in Phase 5"),
        "live": ("v.", "Live", "locked in Phase 5"),
    }
    conn: sqlite3.Connection | None = _open_ro_conn(db_path) if db_path.exists() else None
    try:
        rows = _strategy_rows(conn, configs_dir, locks_dir)
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
    finally:
        if conn is not None:
            conn.close()
    return {"sections": sections, "lastRefreshedAt": _now_iso()}


def build_kanban_snapshot(
    db_path: Path, configs_dir: Path, locks_dir: Path | None = None
) -> dict[str, Any]:
    labels = {
        "idle": ("i.", "Idle", "configured, no action queued"),
        "backtest": ("ii.", "Backtest", "historical evidence review"),
        "paper": ("iii.", "Paper", "live feed, no capital"),
        "micro_live": ("iv.", "Micro live", "locked by ADR 0004"),
        "live": ("v.", "Live", "locked by ADR 0004"),
    }
    conn: sqlite3.Connection | None = _open_ro_conn(db_path) if db_path.exists() else None
    try:
        rows = _strategy_rows(conn, configs_dir, locks_dir)
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
    finally:
        if conn is not None:
            conn.close()
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
    if not db_path.exists():
        return {"entries": [], "lastRefreshedAt": _now_iso()}
    conn = _open_ro_conn(db_path)
    try:
        entries = _ledger_entries(conn, _configs)
    finally:
        conn.close()
    return {"entries": entries, "lastRefreshedAt": _now_iso()}


def _strategy_rows(
    conn: sqlite3.Connection | None,
    configs_dir: Path,
    locks_dir: Path | None = None,
) -> list[_StrategyRow]:
    configs = _load_strategy_configs(configs_dir)
    if conn is not None:
        latest_runs = _event_queries.latest_backtest_metrics(conn)
        promotions = _latest_promotions(conn)
        sessions = _latest_session_states(conn, locks_dir)
        jobs = _latest_orchestration_jobs(conn)
    else:
        latest_runs = {}
        promotions = {}
        sessions = {}
        jobs = {}
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
        failures = tuple(_compute_gate_failures(sharpe, max_dd, trade_count, config.family))
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
                backtest_run_started_at=str(metrics.get("started_at") or ""),
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


def _kanban_lane(row: _StrategyRow) -> str:
    if row.job_status in {"queued", "starting", "running"}:
        if row.job_action_type == "backtest_walk_forward":
            return "backtest"
        if row.job_action_type in {"paper_session_start", "micro_live_session_start"}:
            return row.stage
    if row.stage == "backtest" and not row.evidence_run_id:
        return "idle"
    return row.stage if row.stage in _VISIBLE_STAGES else "idle"


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
