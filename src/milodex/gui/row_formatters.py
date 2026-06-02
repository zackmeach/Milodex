"""Pure formatter and projector utilities for GUI read models.

Leaf module in the read-model DAG: no intra-package GUI imports beyond the
strategy loader's ``StrategyConfig`` type.  These helpers turn raw config /
event-store values into the display-ready primitives the snapshot builders and
ledger builders assemble into QML payloads.

This module was extracted verbatim from ``read_models.py`` (PR12 decompose).
No behavior changed — definitions were moved, not rewritten.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from milodex.strategies.loader import StrategyConfig

_STAGES = ("backtest", "paper", "micro_live", "live")
_VISIBLE_STAGES = ("idle", "backtest", "paper", "micro_live", "live")
_CONFIG_SKIP_PREFIXES = ("universe_",)
_CONFIG_SKIP_NAMES = {"risk_defaults.yaml", "sample_strategy.yaml"}


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


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _today_label() -> str:
    now = datetime.now()
    if _supports_dash_day():
        return now.strftime("%A, %B %-d")
    return now.strftime("%A, %B %d").replace(" 0", " ")


def _compact_timestamp(timestamp: str) -> str:
    """Return the raw ISO timestamp unchanged.

    Time formatting was moved to QML (Task 33 / PR-7c) — ``shortTime()`` /
    ``formatTs()`` in each surface now converts the raw ISO string to the
    operator's chosen 24h or 12h format.  This function is kept as a
    pass-through so call sites remain explicit; callers keep using
    ``displayTimestamp`` as the dict key for backward compatibility with
    any QML fallback paths still reading it.
    """
    return timestamp


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
