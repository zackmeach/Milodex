"""Tests for Phase 5 GUI read models."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _write_strategy_config(configs_dir: Path, strategy_id: str, stage: str = "backtest") -> Path:
    path = configs_dir / f"{strategy_id.replace('.', '_')}.yaml"
    path.write_text(
        f"""
strategy:
  id: {strategy_id}
  family: meanrev
  template: daily
  variant: rsi2pullback
  version: 1
  description: RSI-2 Pullback
  enabled: true
  universe: [SPY]
  parameters:
    rsi_period: 2
    entry_rsi_max: 10
    exit_rsi_min: 70
  tempo:
    bar_size: 1D
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.1
    max_positions: 1
    daily_loss_cap_pct: 0.03
    stop_loss_pct: 0.05
  stage: {stage}
  backtest:
    commission_per_trade: 0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )
    return path


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            from_stage TEXT NOT NULL,
            to_stage TEXT NOT NULL,
            promotion_type TEXT NOT NULL,
            approved_by TEXT NOT NULL,
            backtest_run_id TEXT,
            sharpe_ratio REAL,
            max_drawdown_pct REAL,
            trade_count INTEGER,
            notes TEXT
        );
        CREATE TABLE backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            strategy_id TEXT NOT NULL,
            config_path TEXT,
            config_hash TEXT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL,
            slippage_pct REAL,
            commission_per_trade REAL,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE kill_switch_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            reason TEXT
        );
        CREATE TABLE strategy_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            exit_reason TEXT,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            session_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            equity REAL NOT NULL,
            cash REAL NOT NULL,
            portfolio_value REAL NOT NULL,
            daily_pnl REAL NOT NULL,
            positions_json TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _seed_backtest(db: Path, strategy_id: str, sharpe: float = 0.72) -> None:
    metadata = {
        "oos_aggregate": {
            "sharpe": sharpe,
            "max_drawdown_pct": 8.5,
            "trade_count": 120,
        }
    }
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO backtest_runs
            (run_id, strategy_id, start_date, end_date, started_at, status, metadata_json)
        VALUES ('run-1', ?, '2020-01-01', '2024-12-31', '2026-05-01T00:00:00+00:00',
                'completed', ?)
        """,
        (strategy_id, json.dumps(metadata)),
    )
    conn.commit()
    conn.close()


def _seed_promotion(db: Path, strategy_id: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO promotions
            (recorded_at, strategy_id, from_stage, to_stage, promotion_type, approved_by,
             backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count, notes)
        VALUES ('2026-05-08T12:00:00+00:00', ?, 'backtest', 'paper', 'statistical',
                'test', 'run-1', 0.72, 8.5, 120, 'gate pass')
        """,
        (strategy_id,),
    )
    conn.commit()
    conn.close()


def test_bench_snapshot_groups_config_and_evidence(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id)
    _seed_promotion(db, strategy_id)

    snapshot = build_bench_snapshot(db, configs)
    paper = next(section for section in snapshot["sections"] if section["stage"] == "paper")

    assert len(paper["strategies"]) == 1
    row = paper["strategies"][0]
    assert row["strategyId"] == strategy_id
    assert row["statusKind"] == "positive"
    assert row["tradeCount"] == 120
    assert row["gateFailures"] == []
    assert row["metaConfigKey"] == "meanrev.daily"
    assert row["metaStage"] == "paper"
    assert row["metaEvidenceLabel"] == "promoted"
    assert row["metaEvidenceAt"]
    assert "T" not in row["metaEvidenceAt"]


def test_ledger_snapshot_combines_promotions_and_kill_events(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_ledger_snapshot

    strategy_id = "meanrev.daily.rsi2pullback.v1"
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_promotion(db, strategy_id)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO kill_switch_events (event_type, recorded_at, reason) VALUES (?, ?, ?)",
        ("triggered", "2026-05-08T13:00:00+00:00", "daily loss cap"),
    )
    conn.commit()
    conn.close()

    entries = build_ledger_snapshot(db)["entries"]

    assert {entry["outcomeKind"] for entry in entries} >= {"promoted", "fired"}
    assert any(entry["subject"] == "kill switch" for entry in entries)
    assert all(entry["displayTimestamp"] for entry in entries)
    assert all("T" not in entry["displayTimestamp"] for entry in entries)


def test_desk_snapshot_exposes_stage_ladder_rows(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_desk_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    _write_strategy_config(configs, "meanrev.daily.rsi2pullback.v1", stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)

    snapshot = build_desk_snapshot(db, configs)["snapshot"]

    assert snapshot["strategyTotal"] == 1
    rows = {row["stage"]: row for row in snapshot["stageRows"]}
    assert rows["paper"]["strategyCount"] == 1
    assert rows["paper"]["fillPct"] == 1.0


def test_desk_events_expose_structured_event_fields(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_desk_snapshot

    strategy_id = "meanrev.daily.rsi2pullback.v1"
    configs = tmp_path / "configs"
    configs.mkdir()
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_promotion(db, strategy_id)

    event = build_desk_snapshot(db, configs)["snapshot"]["events"][0]

    assert event["subject"] == "Rsi2Pullback"
    assert event["transition"] == "backtest -> paper"
    assert event["reason"] == "gate pass"
