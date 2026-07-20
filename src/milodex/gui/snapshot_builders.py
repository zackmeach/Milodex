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
from milodex.gui.bench_grouping import build_group_rollups
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
from milodex.gui.strategy_row import _StrategyRow, classify_archetype


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
        # as_qml() computed once per row; the flat per-stage lists and the
        # group rosters share the same payload dicts.
        qml_by_id = {row.strategy_id: row.as_qml() for row in rows}
        groups = build_group_rollups(rows, qml_by_id)
        sections = []
        for stage in _VISIBLE_STAGES:
            roman, name, caption = labels[stage]
            strategies = [qml_by_id[row.strategy_id] for row in rows if row.stage == stage]
            sections.append(
                {
                    "stage": stage,
                    "stageRoman": roman,
                    "stageName": name,
                    "stageCaption": caption,
                    # Flat per-instance rows, unchanged shape — kept for every
                    # existing consumer (capture scripts, tests, tooling).
                    "strategies": strategies,
                    # Template-group rollup (read side): a group lands in the
                    # section of its GROUP stage, so instances waiting below
                    # render inside their group's roster here, not in their
                    # own stage's section.
                    "groups": [group for group in groups if group["stage"] == stage],
                }
            )
    finally:
        if conn is not None:
            conn.close()
    return {"sections": sections, "lastRefreshedAt": _now_iso()}


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
        effective_stage = config.stage if config.stage in _VISIBLE_STAGES else "backtest"
        # Promotion records — not YAML stage — are the binding source for a
        # promoted stage (docs/STRATEGY_BANK.md "How to refresh"). A config
        # claiming paper/micro_live/live with NO promotion ledger row was never
        # promoted (and would be no_frozen_manifest-vetoed by the risk layer),
        # so it is at-backtest in reality — clamp it, same spirit as the
        # non-visible-stage clamp above. This moves the row's SECTION and its
        # archetype together, so the chip can never contradict the ladder.
        if effective_stage in {"paper", "micro_live", "live"} and not promotion:
            effective_stage = "backtest"
        promotion_type = str(promotion.get("promotion_type") or "")
        # Feed the classifier only evidence-grounded gate failures: when all
        # three metrics are None the strategy was never evaluated, and the
        # surface's own status copy reads "Config valid — awaiting evidence"
        # (_status_copy checks all-None BEFORE failures). "blocked" must mean
        # "evaluated and failed the gate", so a never-evaluated row falls
        # through to "research" instead of wearing a BLOCKED chip beside an
        # awaiting-evidence status word.
        evaluated = not (sharpe is None and max_dd is None and trade_count is None)
        archetype = classify_archetype(
            config.family, effective_stage, promotion_type, list(failures) if evaluated else []
        )
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
                stage=effective_stage,
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
                promotion_type=promotion_type,
                gate_failures=failures,
                archetype=archetype,
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
