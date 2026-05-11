"""Tests for Phase 5 GUI read models."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _write_strategy_config(
    configs_dir: Path,
    strategy_id: str,
    stage: str = "backtest",
    display_name: str | None = None,
) -> Path:
    path = configs_dir / f"{strategy_id.replace('.', '_')}.yaml"
    display_name_line = f"  display_name: {display_name}\n" if display_name is not None else ""
    path.write_text(
        f"""
strategy:
  id: {strategy_id}
{display_name_line.rstrip()}
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
        CREATE TABLE orchestration_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL UNIQUE,
            action_type TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            status TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE orchestration_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            batch_id TEXT NOT NULL,
            strategy_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            requested_stage TEXT NOT NULL,
            status TEXT NOT NULL,
            queued_at TEXT NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            cancel_requested_at TEXT,
            execution_ref_type TEXT,
            execution_ref TEXT,
            progress_current INTEGER,
            progress_total INTEGER,
            progress_label TEXT,
            error_code TEXT,
            error_message TEXT,
            metadata_json TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _seed_backtest(
    db: Path,
    strategy_id: str,
    sharpe: float = 0.72,
    max_drawdown_pct: float = 8.5,
    trade_count: int = 120,
) -> None:
    metadata = {
        "oos_aggregate": {
            "sharpe": sharpe,
            "max_drawdown_pct": max_drawdown_pct,
            "trade_count": trade_count,
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
    assert row["visualPriority"] == 1
    assert row["actions"][0] == {
        "id": "open_evidence",
        "label": "Open Evidence",
        "kind": "evidence",
        "requiresConfirmation": False,
        "isPrototypeOnly": False,
    }


def test_bench_actions_hide_unavailable_actions_and_mark_prototypes(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id, sharpe=0.25, max_drawdown_pct=18.0, trade_count=20)

    snapshot = build_bench_snapshot(db, configs)
    backtest = next(section for section in snapshot["sections"] if section["stage"] == "backtest")
    row = backtest["strategies"][0]
    action_ids = [action["id"] for action in row["actions"]]

    assert action_ids[0] == "open_evidence"
    assert "promote_paper" not in action_ids
    assert "initiate_backtest" in action_ids
    assert all(
        action["isPrototypeOnly"]
        for action in row["actions"]
        if action["id"] != "open_evidence"
    )


def test_bench_actions_confirm_capital_stage_targets(tmp_path: Path) -> None:
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
    row = paper["strategies"][0]
    promote_micro = next(
        action for action in row["actions"] if action["id"] == "promote_micro_live"
    )
    start_trading = next(action for action in row["actions"] if action["id"] == "start_trading")

    assert promote_micro["targetStage"] == "micro_live"
    assert promote_micro["requiresConfirmation"] is True
    assert start_trading["requiresConfirmation"] is False


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


def _write_regime_config(configs_dir: Path, strategy_id: str, stage: str = "paper") -> Path:
    path = configs_dir / f"{strategy_id.replace('.', '_')}.yaml"
    path.write_text(
        f"""
strategy:
  id: {strategy_id}
  family: regime
  template: daily.sma200_rotation
  variant: spy_shy
  version: 1
  description: SPY/SHY 200-DMA Regime
  enabled: true
  universe: [SPY, SHY]
  parameters:
    ma_filter_length: 200
    risk_on_symbol: SPY
    risk_off_symbol: SHY
    allocation_pct: 0.09
  tempo:
    bar_size: 1D
    min_hold_days: 1
    max_hold_days: null
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.05
    stop_loss_pct: null
  stage: {stage}
  backtest:
    commission_per_trade: 0
    min_trades_required: null
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )
    return path


def test_regime_strategy_has_empty_gate_failures(tmp_path: Path) -> None:
    """Regime strategies are exempt from statistical gate thresholds (CLAUDE.md, SRS R-PRM-004)."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "regime.daily.sma200_rotation.spy_shy.v1"
    _write_regime_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    # No backtest or promotion records — metrics are all None, which would normally
    # trigger all three gate failures (S, D, N) for a non-regime strategy.

    snapshot = build_bench_snapshot(db, configs)
    paper = next(section for section in snapshot["sections"] if section["stage"] == "paper")
    assert len(paper["strategies"]) == 1
    row = paper["strategies"][0]

    assert row["gateFailures"] == [], "Regime strategy must be exempt from gate thresholds"
    assert row["statusKind"] == "info", "Regime strategy with no evidence should be info-kind"


def test_demotion_records_do_not_pollute_latest_promotions(tmp_path: Path) -> None:
    """_latest_promotions must ignore demotion rows so NULL metrics don't create false failures."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)

    conn = sqlite3.connect(str(db))
    # First record: a valid statistical promotion with passing metrics.
    conn.execute(
        """
        INSERT INTO promotions
            (recorded_at, strategy_id, from_stage, to_stage, promotion_type, approved_by,
             backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count, notes)
        VALUES ('2026-05-01T10:00:00+00:00', ?, 'backtest', 'paper', 'statistical',
                'test', 'run-1', 0.80, 7.0, 150, 'gate pass')
        """,
        (strategy_id,),
    )
    # Second record: a demotion with NULL metrics (higher id → previously selected by MAX(id)).
    conn.execute(
        """
        INSERT INTO promotions
            (recorded_at, strategy_id, from_stage, to_stage, promotion_type, approved_by,
             backtest_run_id, sharpe_ratio, max_drawdown_pct, trade_count, notes)
        VALUES ('2026-05-05T09:00:00+00:00', ?, 'paper', 'backtest', 'demotion',
                'test', NULL, NULL, NULL, NULL, 'demoted')
        """,
        (strategy_id,),
    )
    conn.commit()
    conn.close()

    snapshot = build_bench_snapshot(db, configs)
    backtest_section = next(
        section for section in snapshot["sections"] if section["stage"] == "backtest"
    )
    assert len(backtest_section["strategies"]) == 1
    row = backtest_section["strategies"][0]

    # The promotion row (sharpe=0.80, dd=7.0, trades=150) should win, not the demotion NULL row.
    assert row["sharpe"] == 0.80, "Promotion metrics must not be masked by demotion NULL row"
    assert row["gateFailures"] == [], "Valid promotion metrics must pass all gates"


def test_kanban_snapshot_exposes_five_lanes_and_card_axes(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_kanban_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    named_id = "meanrev.daily.rsi2pullback.v1"
    derived_id = "regime.daily.sma200_rotation.spy_shy.v1"
    _write_strategy_config(configs, named_id, stage="paper", display_name='"RSI-2 Pullback"')
    _write_regime_config(configs, derived_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, named_id)

    snapshot = build_kanban_snapshot(db, configs)

    assert [lane["lane"] for lane in snapshot["lanes"]] == [
        "idle",
        "backtest",
        "paper",
        "micro_live",
        "live",
    ]
    cards = {card["strategyId"]: card for lane in snapshot["lanes"] for card in lane["cards"]}
    assert cards[named_id]["displayName"] == "RSI-2 Pullback"
    assert cards[named_id]["displayNameSource"] == "config"
    assert cards[named_id]["promotionStage"] == "paper"
    assert cards[named_id]["kanbanLane"] == "paper"
    assert cards[named_id]["sessionState"] == "not_running"
    assert cards[named_id]["eligibilityVerdict"] == "gate_passing"
    assert "ADR 0004" in cards[named_id]["eligibilityCopy"]
    assert "Capital-bearing stages remain locked" not in cards[named_id]["eligibilityCopy"]
    assert cards[named_id]["tradeCount"] == 120
    assert cards[derived_id]["displayName"] == "Sma200 Rotation"
    assert cards[derived_id]["displayNameSource"] == "derived"


def test_kanban_snapshot_keeps_idle_lane_separate_from_promotion_stage(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_kanban_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)

    card = build_kanban_snapshot(db, configs)["lanes"][0]["cards"][0]

    assert card["promotionStage"] == "backtest"
    assert card["kanbanLane"] == "idle"
    assert card["eligibilityVerdict"] == "not_evaluated"


def test_kanban_snapshot_derives_session_state_from_strategy_runs(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_kanban_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper", display_name='"RSI-2 Pullback"')
    db = tmp_path / "milodex.db"
    _create_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO strategy_runs (
            session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json
        )
        VALUES ('session-1', ?, '2026-05-09T12:00:00+00:00', NULL, NULL, '{}')
        """,
        (strategy_id,),
    )
    conn.commit()
    conn.close()

    card = build_kanban_snapshot(db, configs)["lanes"][2]["cards"][0]

    assert card["sessionState"] == "running"
    assert card["sessionId"] == "session-1"
    assert card["sessionDetail"] == "session active"


def test_kanban_snapshot_surfaces_queued_orchestration_job_activity(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_kanban_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO orchestration_batches (
            batch_id, action_type, requested_by, requested_at, status, metadata_json
        )
        VALUES ('batch-1', 'backtest_walk_forward', 'operator',
                '2026-05-10T12:00:00+00:00', 'queued', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO orchestration_jobs (
            job_id, batch_id, strategy_id, action_type, requested_stage, status,
            queued_at, progress_current, progress_total, progress_label, metadata_json
        )
        VALUES ('job-1', 'batch-1', ?, 'backtest_walk_forward', 'backtest', 'queued',
                '2026-05-10T12:00:00+00:00', 0, 4, 'queued for walk-forward', '{}')
        """,
        (strategy_id,),
    )
    conn.commit()
    conn.close()

    card = build_kanban_snapshot(db, configs)["lanes"][1]["cards"][0]

    assert card["kanbanLane"] == "backtest"
    assert card["sessionState"] == "queued"
    assert card["sessionDetail"] == "queued for walk-forward"
    assert card["jobStatus"] == "queued"


def test_kanban_snapshot_surfaces_cancel_requested_job_as_canceling(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_kanban_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO orchestration_batches (
            batch_id, action_type, requested_by, requested_at, status, metadata_json
        )
        VALUES ('batch-1', 'backtest_walk_forward', 'operator',
                '2026-05-10T12:00:00+00:00', 'running', '{}')
        """
    )
    conn.execute(
        """
        INSERT INTO orchestration_jobs (
            job_id, batch_id, strategy_id, action_type, requested_stage, status,
            queued_at, cancel_requested_at, progress_current, progress_total,
            progress_label, metadata_json
        )
        VALUES ('job-1', 'batch-1', ?, 'backtest_walk_forward', 'backtest', 'running',
                '2026-05-10T12:00:00+00:00', '2026-05-10T12:05:00+00:00',
                2, 4, '2/4 windows complete', '{}')
        """,
        (strategy_id,),
    )
    conn.commit()
    conn.close()

    card = build_kanban_snapshot(db, configs)["lanes"][1]["cards"][0]

    assert card["sessionState"] == "canceling"
    assert card["sessionDetail"] == "cancel requested | 2/4 windows complete"
