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
    """Apply the REAL (fully-migrated) schema via EventStore."""
    from milodex.core.event_store import EventStore

    EventStore(path)


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


def _seed_stage_return(db: Path, strategy_id: str) -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO promotions
            (recorded_at, strategy_id, from_stage, to_stage, promotion_type, approved_by,
             backtest_run_id, notes)
        VALUES ('2026-05-08T12:30:00+00:00', ?, 'idle', 'backtest', 'stage_return',
                'bench_gui', 'run-2', 'Initiate Backtest via Bench GUI')
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
    # Task 33: metaEvidenceAt is now raw ISO (formatting moved to QML).
    assert row["metaEvidenceAt"]
    assert row["visualPriority"] == 1
    # PR G: actions are now produced by compute_menu_items via
    # _compute_bench_action_menu.  The floor item is always Open Evidence last.
    assert row["actions"][-1]["label"] == "Open Evidence"
    assert row["actions"][-1]["verbClass"] == "informational"


def test_bench_actions_no_forbidden_labels_and_open_evidence_is_floor(tmp_path: Path) -> None:
    """compute_menu_items path: no forbidden verbs; Open Evidence is always last."""
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
    labels = [a["label"] for a in row["actions"]]

    # Open Evidence is always the last item (ADR 0047 Decision 5).
    assert labels[-1] == "Open Evidence"

    # Forbidden verbs must not appear (ADR 0050 Decision 7).
    forbidden = {"Send to Idle", "Demote to Paper", "Demote to Micro Live"}
    assert not forbidden.intersection(labels), f"Forbidden label found in: {labels}"

    # verbClass keys must be present on each action dict.
    assert all("verbClass" in a for a in row["actions"])


def test_bench_actions_paper_row_has_correct_menu_structure(tmp_path: Path) -> None:
    """PAPER row: ADR 0004 hides Promote to Micro Live; directional verbs precede invocation."""
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
    labels = [a["label"] for a in row["actions"]]
    verb_classes = [a["verbClass"] for a in row["actions"]]

    # ADR 0004 forward lock: Promote to Micro Live must not appear.
    assert "Promote to Micro Live" not in labels

    # Open Evidence floor is always last (ADR 0047 Decision 5).
    assert labels[-1] == "Open Evidence"
    assert verb_classes[-1] == "informational"

    # Ordering: all directional verbs precede all invocation verbs.
    saw_invocation = False
    for vc in verb_classes:
        if vc == "invocation":
            saw_invocation = True
        if vc == "directional":
            assert not saw_invocation, "directional verb appeared after an invocation verb"

    # Start Trading or Stop Trading must appear (paper is a trading-eligible stage).
    trading_labels = {"Start Trading", "Stop Trading"}
    assert any(lbl in trading_labels for lbl in labels)


def test_bench_backtest_row_without_evidence_does_not_offer_promotion(
    tmp_path: Path,
) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)

    snapshot = build_bench_snapshot(db, configs)
    backtest = next(section for section in snapshot["sections"] if section["stage"] == "backtest")
    row = backtest["strategies"][0]
    labels = [a["label"] for a in row["actions"]]

    assert "Promote to Paper" not in labels
    assert "Initiate Backtest" in labels


def test_bench_backtest_row_with_failing_evidence_does_not_offer_promotion(
    tmp_path: Path,
) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id, sharpe=0.2, max_drawdown_pct=20.0, trade_count=10)

    snapshot = build_bench_snapshot(db, configs)
    backtest = next(section for section in snapshot["sections"] if section["stage"] == "backtest")
    row = backtest["strategies"][0]
    labels = [a["label"] for a in row["actions"]]

    assert "Promote to Paper" not in labels
    assert "Initiate Backtest" in labels


def test_bench_backtest_row_with_passing_evidence_offers_promotion(
    tmp_path: Path,
) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id, sharpe=0.8, max_drawdown_pct=6.0, trade_count=45)

    snapshot = build_bench_snapshot(db, configs)
    backtest = next(section for section in snapshot["sections"] if section["stage"] == "backtest")
    row = backtest["strategies"][0]
    labels = [a["label"] for a in row["actions"]]

    assert "Promote to Paper" in labels


def test_bench_idle_row_with_passing_backtest_evidence_can_initiate_backtest(
    tmp_path: Path,
) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="idle")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id, sharpe=0.8, max_drawdown_pct=6.0, trade_count=45)

    snapshot = build_bench_snapshot(db, configs)
    idle = next(section for section in snapshot["sections"] if section["stage"] == "idle")
    row = idle["strategies"][0]
    labels = [a["label"] for a in row["actions"]]

    assert "Initiate Backtest" in labels
    assert "Open Evidence" in labels


def test_bench_in_flight_backtest_job_suppresses_duplicate_backtest_action(
    tmp_path: Path,
) -> None:
    from milodex.gui.read_models import build_bench_snapshot

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
            queued_at, progress_label, metadata_json
        )
        VALUES ('job-1', 'batch-1', ?, 'backtest_walk_forward', 'backtest', 'running',
                '2026-05-10T12:00:00+00:00', 'walk-forward running', '{}')
        """,
        (strategy_id,),
    )
    conn.commit()
    conn.close()

    snapshot = build_bench_snapshot(db, configs)
    backtest = next(section for section in snapshot["sections"] if section["stage"] == "backtest")
    row = backtest["strategies"][0]
    labels = [a["label"] for a in row["actions"]]

    assert "Initiate Backtest" not in labels
    assert "Refresh Backtest" not in labels


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
    # Task 33: displayTimestamp is now raw ISO — formatting moved to QML.
    # Assert it's present and non-empty; the "T" separator is expected.
    assert all(entry["displayTimestamp"] for entry in entries)


def test_ledger_snapshot_labels_idle_to_backtest_stage_return(tmp_path: Path) -> None:
    from milodex.gui.read_models import build_ledger_snapshot

    strategy_id = "meanrev.daily.rsi2pullback.v1"
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_stage_return(db, strategy_id)

    entries = build_ledger_snapshot(db)["entries"]

    assert entries[0]["transition"] == "idle -> backtest"
    assert entries[0]["outcome"] == "RETURNED"
    assert entries[0]["outcomeKind"] == "returned"
    assert entries[0]["reason"] == "Initiate Backtest via Bench GUI"


# NOTE: the mock ``build_desk_snapshot`` and its two tests
# (test_desk_snapshot_exposes_stage_ladder_rows /
# test_desk_events_expose_structured_event_fields) were removed in PR 8 of
# the Trading Desk redesign. The DESK surface is now driven by the six
# dedicated read-models (PerformanceState, RiskThroughputState,
# ActiveOpsState, AttentionState, MarketTapeState, ActivityFeedState),
# each covered by its own test module.


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
    # Insert minimal explanations first (trades.explanation_id is NOT NULL).
    conn.execute(
        "INSERT INTO explanations (recorded_at, decision_type, status, symbol, side, quantity,"
        " order_type, time_in_force, submitted_by, market_open,"
        " account_equity, account_cash, account_portfolio_value, account_daily_pnl,"
        " risk_allowed, risk_summary, reason_codes_json, risk_checks_json, context_json,"
        " session_id)"
        " VALUES ('2026-05-09T12:00:00+00:00','submit','submitted','SPY','buy',1.0,"
        " 'market','day','test',1, 10000.0,10000.0,10000.0,0.0,"
        " 1,'ok','[]','{}','{}','session-1')"
    )
    eid1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO explanations (recorded_at, decision_type, status, symbol, side, quantity,"
        " order_type, time_in_force, submitted_by, market_open,"
        " account_equity, account_cash, account_portfolio_value, account_daily_pnl,"
        " risk_allowed, risk_summary, reason_codes_json, risk_checks_json, context_json,"
        " session_id)"
        " VALUES ('2026-05-09T12:01:00+00:00','submit','submitted','SPY','buy',1.0,"
        " 'market','day','test',1, 10000.0,10000.0,10000.0,0.0,"
        " 1,'ok','[]','{}','{}','session-1')"
    )
    eid2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO trades"
        " (explanation_id, recorded_at, status, source, symbol, side, quantity,"
        "  order_type, time_in_force, estimated_unit_price, estimated_order_value, submitted_by,"
        "  session_id)"
        " VALUES (?, '2026-05-09T12:00:00+00:00','submitted','paper','SPY','buy',1.0,"
        "  'market','day',100.0,100.0,'test', 'session-1')",
        (eid1,),
    )
    conn.execute(
        "INSERT INTO trades"
        " (explanation_id, recorded_at, status, source, symbol, side, quantity,"
        "  order_type, time_in_force, estimated_unit_price, estimated_order_value, submitted_by,"
        "  session_id)"
        " VALUES (?, '2026-05-09T12:01:00+00:00','submitted','paper','SPY','buy',1.0,"
        "  'market','day',100.0,100.0,'test', 'session-1')",
        (eid2,),
    )
    conn.commit()
    conn.close()

    card = build_kanban_snapshot(db, configs)["lanes"][2]["cards"][0]

    assert card["sessionState"] == "running"
    assert card["sessionId"] == "session-1"
    assert card["sessionDetail"] == "session active"
    assert card["paperEvidence"]["status"] == "running"
    assert card["paperEvidence"]["tradeCount"] == 2
    assert card["evidencePacket"]["paperEvidence"]["sessionId"] == "session-1"


def test_bench_snapshot_marks_controlled_stop_paper_evidence_completed(
    tmp_path: Path,
) -> None:
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        INSERT INTO strategy_runs (
            session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json
        )
        VALUES ('session-2', ?, '2026-05-09T12:00:00+00:00',
                '2026-05-09T12:30:00+00:00', 'controlled_stop', '{}')
        """,
        (strategy_id,),
    )
    conn.execute(
        "INSERT INTO explanations (recorded_at, decision_type, status, symbol, side, quantity,"
        " order_type, time_in_force, submitted_by, market_open,"
        " account_equity, account_cash, account_portfolio_value, account_daily_pnl,"
        " risk_allowed, risk_summary, reason_codes_json, risk_checks_json, context_json,"
        " session_id)"
        " VALUES ('2026-05-09T12:00:00+00:00','submit','submitted','SPY','buy',1.0,"
        " 'market','day','test',1, 10000.0,10000.0,10000.0,0.0,"
        " 1,'ok','[]','{}','{}','session-2')"
    )
    eid3 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO trades"
        " (explanation_id, recorded_at, status, source, symbol, side, quantity,"
        "  order_type, time_in_force, estimated_unit_price, estimated_order_value, submitted_by,"
        "  session_id)"
        " VALUES (?, '2026-05-09T12:00:00+00:00','submitted','paper','SPY','buy',1.0,"
        "  'market','day',100.0,100.0,'test', 'session-2')",
        (eid3,),
    )
    conn.commit()
    conn.close()

    card = build_bench_snapshot(db, configs)["sections"][2]["strategies"][0]

    assert card["paperEvidence"]["status"] == "completed"
    assert card["paperEvidence"]["exitReason"] == "controlled_stop"
    assert card["paperEvidence"]["tradeCount"] == 1


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


# ---------------------------------------------------------------------------
# PR M (ADR 0049): normalized read-only Evidence Packet contract
# ---------------------------------------------------------------------------


def test_bench_pr_m_evidence_packet_shape(tmp_path: Path) -> None:
    """Each Bench row exposes a normalized read-only evidencePacket."""
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
    paper = next(s for s in snapshot["sections"] if s["stage"] == "paper")
    row = paper["strategies"][0]

    assert "evidencePacket" in row, "every Bench row must carry evidencePacket"
    packet = row["evidencePacket"]

    # Top-level identity
    assert packet["schemaVersion"] == 1
    assert packet["strategyId"] == strategy_id
    assert packet["strategyName"] == row["name"]
    assert packet["currentStage"] == "paper"

    # Source — explicit non-authoritative framing
    source = packet["source"]
    assert source["kind"] == "gui_read_model_snapshot"
    assert source["authoritative"] is False
    assert "deferred" in source["note"].lower()

    # Metrics mirror the existing flat fields
    metrics = packet["metrics"]
    assert metrics["sharpe"] == row["sharpe"]
    assert metrics["maxDrawdownPct"] == row["maxDrawdownPct"]
    # Note: as_qml() coerces trade_count NULL→0; the packet preserves None semantics
    # via the underlying _StrategyRow field, but for the seeded row both are 120.
    assert metrics["tradeCount"] == 120

    # Evidence sub-section
    evidence = packet["evidence"]
    assert evidence["runId"] == row["evidenceRunId"]
    assert evidence["label"] == row["metaEvidenceLabel"]
    assert evidence["observedAt"] == row["metaEvidenceAt"]
    assert evidence["promotedAt"] == row["promotedAt"]
    assert evidence["promotionType"] == row["promotionType"]

    # Gate — failures mirror flat, freshness/gateResult are explicit deferral
    gate = packet["gate"]
    assert gate["failures"] == row["gateFailures"]
    assert gate["freshness"] == "not_reconstructed_v1"
    assert gate["gateResult"] == "not_reconstructed_v1"
    assert gate["reconstructionDeferred"] is True

    # Status / session / job mirrors
    status = packet["status"]
    assert status["kind"] == row["statusKind"]
    assert status["word"] == row["statusWord"]
    assert status["tail"] == row["statusTail"]
    assert status["metaLine"] == row["metaLine"]

    assert packet["session"]["state"] == row["sessionState"]
    assert packet["session"]["id"] == row["sessionId"]
    assert packet["session"]["detail"] == row["sessionDetail"]

    assert packet["job"]["id"] == row["jobId"]
    assert packet["job"]["status"] == row["jobStatus"]
    assert packet["job"]["actionType"] == row["jobActionType"]
    assert packet["job"]["detail"] == row["jobDetail"]


def test_bench_pr_m_evidence_packet_keys_are_stable(tmp_path: Path) -> None:
    """Lock the top-level packet key set so future PRs can't silently drop fields."""
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
    row = next(s for s in snapshot["sections"] if s["stage"] == "paper")["strategies"][0]
    packet = row["evidencePacket"]

    assert set(packet.keys()) == {
        "schemaVersion",
        "strategyId",
        "strategyName",
        "currentStage",
        "source",
        "metrics",
        "evidence",
        "gate",
        "status",
        "session",
        "paperEvidence",
        "job",
    }
    assert set(packet["source"].keys()) == {"kind", "authoritative", "note"}
    assert set(packet["metrics"].keys()) == {"sharpe", "maxDrawdownPct", "tradeCount"}
    assert set(packet["evidence"].keys()) == {
        "runId",
        "label",
        "observedAt",
        "promotedAt",
        "promotionType",
    }
    assert set(packet["gate"].keys()) == {
        "failures",
        "freshness",
        "gateResult",
        "reconstructionDeferred",
    }
    assert set(packet["status"].keys()) == {"kind", "word", "tail", "metaLine"}
    assert set(packet["session"].keys()) == {"state", "id", "detail"}
    assert set(packet["job"].keys()) == {"id", "status", "actionType", "detail"}


def test_bench_pr_m_packet_is_independent_of_flat_fields(tmp_path: Path) -> None:
    """The packet is a copy: mutating it must not leak back into flat row keys."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id)
    _seed_promotion(db, strategy_id)

    row = build_bench_snapshot(db, configs)["sections"][2]["strategies"][0]
    packet = row["evidencePacket"]
    original_failures = list(packet["gate"]["failures"])

    # Mutate the packet's nested list — the flat gateFailures list must be
    # unaffected because _evidence_packet() returns a fresh list().
    packet["gate"]["failures"].append("X")
    assert row["gateFailures"] == original_failures


def test_bench_pr_m_packet_handles_backtest_row_without_evidence(tmp_path: Path) -> None:
    """Backtest rows with no seeded backtest/promotion still get a well-formed packet."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="backtest")
    db = tmp_path / "milodex.db"
    _create_db(db)
    # No seed: no backtest, no promotion.

    snapshot = build_bench_snapshot(db, configs)
    backtest = next(s for s in snapshot["sections"] if s["stage"] == "backtest")
    assert len(backtest["strategies"]) == 1
    packet = backtest["strategies"][0]["evidencePacket"]

    assert packet["schemaVersion"] == 1
    assert packet["currentStage"] == "backtest"
    assert packet["source"]["authoritative"] is False
    # Metrics are absent → None values; packet still has the keys.
    assert packet["metrics"]["sharpe"] is None
    assert packet["metrics"]["maxDrawdownPct"] is None
    assert packet["metrics"]["tradeCount"] is None
    # Gate failures may be populated by the empty-metrics path, but
    # freshness/gateResult must remain explicit non-reconstruction sentinels.
    assert packet["gate"]["freshness"] == "not_reconstructed_v1"
    assert packet["gate"]["gateResult"] == "not_reconstructed_v1"
    assert packet["gate"]["reconstructionDeferred"] is True


def test_bench_pr_m_no_command_proposal_keys_in_packet(tmp_path: Path) -> None:
    """ADR 0049 Decision 2: packet must not introduce command/proposal shapes."""
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
    forbidden = {
        "commandProposal",
        "CommandProposal",
        "submitCommand",
        "dispatchCommand",
        "command",
        "proposal",
    }

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in forbidden, (
                    f"forbidden key '{key}' found in evidencePacket — "
                    "ADR 0049 Decision 2: Bench v1 is read-only"
                )
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for section in snapshot["sections"]:
        for row in section["strategies"]:
            _walk(row.get("evidencePacket"))


# ---------------------------------------------------------------------------
# PR N (ADR 0049): normalized read-only Action Intent Preview contract
# ---------------------------------------------------------------------------


def _all_actions(snapshot: dict) -> list[dict]:
    actions = []
    for section in snapshot["sections"]:
        for row in section["strategies"]:
            actions.extend(row["actions"])
    return actions


def test_bench_pr_n_action_preview_present_on_every_action(tmp_path: Path) -> None:
    """Every Bench action carries an actionIntentPreview object."""
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
    actions = _all_actions(snapshot)
    assert actions, "expected at least one action in the snapshot"
    for action in actions:
        assert "actionIntentPreview" in action, (
            f"missing actionIntentPreview on action {action.get('label')!r}"
        )
        preview = action["actionIntentPreview"]
        assert preview["schemaVersion"] == 1
        submit_capable = (
            preview["actionKind"]
            in {
                "demote",
                "freeze_manifest",
                "initiate_backtest",
                "refresh_backtest",
                "start_trading",
                "stop_trading",
            }
            or (preview["actionKind"] == "promote" and preview["targetStage"] == "paper")
            or (preview["actionKind"] == "return" and preview["targetStage"] == "idle")
        )
        if submit_capable:
            assert preview["executable"] is True
            assert preview["wired"] is True
        else:
            assert preview["executable"] is False
            assert preview["wired"] is False


def test_bench_pr_n_action_preview_keys_are_stable(tmp_path: Path) -> None:
    """Lock the action preview key set so future PRs can't silently drift."""
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
    actions = _all_actions(snapshot)
    preview = actions[0]["actionIntentPreview"]
    assert set(preview.keys()) == {
        "schemaVersion",
        "source",
        "strategyId",
        "strategyName",
        "actionKind",
        "actionLabel",
        "verbClass",
        "currentStage",
        "targetStage",
        "intentCopy",
        "requirements",
        "futureRecord",
        "capitalBearing",
        "safetyCopy",
        "executable",
        "wired",
    }
    assert set(preview["source"].keys()) == {"kind", "authoritative", "note"}

    # Row identity flows into every action's preview.
    paper_row = next(s for s in snapshot["sections"] if s["stage"] == "paper")["strategies"][0]
    for action in actions:
        p = action["actionIntentPreview"]
        assert p["strategyId"] == paper_row["strategyId"]
        assert p["strategyName"] == paper_row["name"]


def test_bench_pr_n_action_preview_source_contract(tmp_path: Path) -> None:
    """Every preview carries the explicit non-authoritative source object."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id)
    _seed_promotion(db, strategy_id)

    for action in _all_actions(build_bench_snapshot(db, configs)):
        source = action["actionIntentPreview"]["source"]
        assert source["kind"] == "gui_read_model_preview"
        assert source["authoritative"] is False
        note = source["note"]
        assert "display metadata" in note
        assert "Bench command bridge" in note
        assert "before state changes" in note


def test_bench_pr_n_action_preview_kind_classification(tmp_path: Path) -> None:
    """actionKind classifies Promote/Demote/Return prefixes and fixed labels."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id)
    _seed_promotion(db, strategy_id)

    actions = _all_actions(build_bench_snapshot(db, configs))
    by_label = {a["label"]: a["actionIntentPreview"] for a in actions}

    # Every action's kind must be the canonical classification.
    expected_by_prefix = (
        ("Promote to ", "promote"),
        ("Demote to ", "demote"),
        ("Return to ", "return"),
    )
    fixed_labels = {
        "Start Trading": "start_trading",
        "Stop Trading": "stop_trading",
        "Initiate Backtest": "initiate_backtest",
        "Refresh Backtest": "refresh_backtest",
        "Freeze Manifest": "freeze_manifest",
        "Open Evidence": "open_evidence",
    }
    for label, preview in by_label.items():
        kind = preview["actionKind"]
        if label in fixed_labels:
            assert kind == fixed_labels[label], (
                f"{label!r} → kind {kind!r}, expected {fixed_labels[label]!r}"
            )
            continue
        matched = False
        for prefix, expected_kind in expected_by_prefix:
            if label.startswith(prefix):
                assert kind == expected_kind, (
                    f"{label!r} prefix {prefix!r} → kind {kind!r}, expected {expected_kind!r}"
                )
                matched = True
                break
        assert matched or kind == "unknown", f"{label!r} unclassified — kind {kind!r}"


def test_bench_pr_n_action_preview_capital_bearing_paper_start(tmp_path: Path) -> None:
    """Paper-stage Start Trading is NOT capital-bearing (PR L refinement)."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id)
    _seed_promotion(db, strategy_id)

    actions = _all_actions(build_bench_snapshot(db, configs))
    start = next((a for a in actions if a["label"] == "Start Trading"), None)
    assert start is not None, "paper row should expose Start Trading"
    preview = start["actionIntentPreview"]
    assert preview["capitalBearing"] is False, (
        "paper-stage Start Trading must not be classified as capital-bearing"
    )
    # The pre-rendered safetyCopy must include the paper-start clarification.
    assert "no capital exposure" in preview["safetyCopy"]
    assert "validated through the command bridge" in preview["safetyCopy"]


def test_bench_pr_n_action_preview_future_record_strings(tmp_path: Path) -> None:
    """Each actionKind maps to the canonical futureRecord string."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id)
    _seed_promotion(db, strategy_id)

    expected = {
        "promote": "promotion_event",
        "demote": "demotion_event",
        "return": "stage_return_event",
        "start_trading": "session_start_event",
        "stop_trading": "session_stop_event",
        "initiate_backtest": "backtest_run",
        "refresh_backtest": "backtest_run",
        "open_evidence": "evidence_view",
    }
    for action in _all_actions(build_bench_snapshot(db, configs)):
        preview = action["actionIntentPreview"]
        kind = preview["actionKind"]
        if kind in expected:
            assert preview["futureRecord"] == expected[kind], (
                f"{kind!r} → futureRecord {preview['futureRecord']!r}, expected {expected[kind]!r}"
            )


def test_bench_pr_n_action_preview_requirements_are_independent(tmp_path: Path) -> None:
    """requirements is a fresh list per preview — mutation must not leak."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id)
    _seed_promotion(db, strategy_id)

    actions = _all_actions(build_bench_snapshot(db, configs))
    first, second = actions[0], actions[1]
    first["actionIntentPreview"]["requirements"].append("LEAKED")
    assert "LEAKED" not in second["actionIntentPreview"]["requirements"]


def test_bench_pr_n_no_command_keys_in_action_preview(tmp_path: Path) -> None:
    """ADR 0049 Decision 2: preview must never introduce command/proposal keys."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    strategy_id = "meanrev.daily.rsi2pullback.v1"
    _write_strategy_config(configs, strategy_id, stage="paper")
    db = tmp_path / "milodex.db"
    _create_db(db)
    _seed_backtest(db, strategy_id)
    _seed_promotion(db, strategy_id)

    forbidden = {
        "commandProposal",
        "CommandProposal",
        "submitCommand",
        "dispatchCommand",
        "command",
        "proposal",
        "payload",
        "broker",
        "eventStore",
    }

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in forbidden, (
                    f"forbidden key '{key}' found in actionIntentPreview — "
                    "ADR 0049 Decision 2: Bench v1 is read-only"
                )
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for action in _all_actions(build_bench_snapshot(db, configs)):
        _walk(action["actionIntentPreview"])


def test_bench_pr_n_action_preview_micro_live_capital_bearing(tmp_path: Path) -> None:
    """Promote-target of micro_live/live or label containing 'Live' is capital-bearing."""
    from milodex.gui.bench_v1 import MenuItem
    from milodex.gui.read_models import _action_intent_preview, _StrategyRow

    row = _StrategyRow(
        strategy_id="x.y.z.v1",
        name="X",
        display_name_source="derived",
        stage="paper",
        description="",
        config_path="",
        family="meanrev",
        template="daily",
        enabled=True,
    )
    item = MenuItem(
        label="Promote to Micro Live",
        verb_class="directional",
        target_stage="micro_live",
    )
    preview = _action_intent_preview(row, item)
    assert preview["capitalBearing"] is True
    assert _COPY_CAPITAL_LOCK_SHORT_TEST in preview["safetyCopy"]


_COPY_CAPITAL_LOCK_SHORT_TEST = (
    "Capital-bearing transitions remain locked while ADR 0004 is in force."
)


# ---------------------------------------------------------------------------
# Task 21 (PR-6): 6-source ledger taxonomy
# ---------------------------------------------------------------------------


def _create_full_ledger_db(path: Path) -> None:
    """Create a DB with all tables needed for 6-source ledger testing."""
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
        """
    )
    conn.commit()
    conn.close()


def test_ledger_entries_includes_all_six_event_types(tmp_path: Path) -> None:
    """Ledger sources: promotions, kill_switch, session start, session stop,
    backtest completion, new strategy."""
    import json as _json

    from milodex.gui.read_models import build_ledger_snapshot

    db = tmp_path / "milodex.db"
    _create_full_ledger_db(db)
    configs = tmp_path / "configs"
    configs.mkdir()

    sid = "meanrev.daily.rsi2pullback.v1"

    # 1. Promotion row → outcomeKind='promoted'
    conn = sqlite3.connect(str(db))
    conn.execute(
        """INSERT INTO promotions
           (recorded_at, strategy_id, from_stage, to_stage, promotion_type,
            approved_by, notes)
           VALUES ('2026-05-01T10:00:00+00:00', ?, 'backtest', 'paper',
                   'statistical', 'test', 'gate pass')""",
        (sid,),
    )

    # 2. Kill-switch row → outcomeKind='fired'
    conn.execute(
        "INSERT INTO kill_switch_events (event_type, recorded_at, reason) VALUES (?, ?, ?)",
        ("triggered", "2026-05-02T10:00:00+00:00", "daily loss cap"),
    )

    # 3. Session start → outcomeKind='started'
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-1', ?, '2026-05-03T10:00:00+00:00', NULL, NULL, '{}')""",
        (sid,),
    )

    # 4. Session stop (non-kill-switch) → outcomeKind='stopped'
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-2', ?, '2026-05-04T09:00:00+00:00',
                   '2026-05-04T16:00:00+00:00', 'controlled_stop', '{}')""",
        (sid,),
    )

    # 5. Completed backtest → outcomeKind='backtested_strong' (Sharpe ≥ 0.5)
    metadata = _json.dumps(
        {"oos_aggregate": {"sharpe": 0.72, "max_drawdown_pct": 8.5, "trade_count": 120}}
    )
    conn.execute(
        """INSERT INTO backtest_runs
           (run_id, strategy_id, start_date, end_date, started_at, ended_at,
            status, metadata_json)
           VALUES ('run-bt-1', ?, '2020-01-01', '2024-12-31',
                   '2026-05-05T08:00:00+00:00', '2026-05-05T08:30:00+00:00',
                   'completed', ?)""",
        (sid, metadata),
    )

    conn.commit()
    conn.close()

    # 6. New strategy via YAML mtime fallback (no event-store history for this sid)
    yaml_sid = "momentum.daily.test_new.v1"
    yaml_path = configs / "momentum_daily_test_new_v1.yaml"
    yaml_path.write_text(
        f"""
strategy:
  id: {yaml_sid}
  family: momentum
  template: daily
  variant: test_new
  version: 1
  description: Test New Strategy
  enabled: true
  universe: [SPY]
  parameters: {{}}
  tempo:
    bar_size: 1D
  risk:
    max_position_pct: 0.1
    max_positions: 1
    daily_loss_cap_pct: 0.03
    stop_loss_pct: 0.05
  stage: backtest
  backtest:
    commission_per_trade: 0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )

    entries = build_ledger_snapshot(db, configs)["entries"]

    # Must contain at least one of each required kind
    kinds = {e["outcomeKind"] for e in entries}
    assert "promoted" in kinds, f"Missing 'promoted' in {kinds}"
    assert "fired" in kinds, f"Missing 'fired' in {kinds}"
    assert "started" in kinds, f"Missing 'started' in {kinds}"
    assert "stopped" in kinds, f"Missing 'stopped' in {kinds}"
    assert any(k.startswith("backtested") for k in kinds), f"Missing 'backtested_*' in {kinds}"
    assert "added" in kinds, f"Missing 'added' in {kinds}"

    # Sort: DESC by timestamp
    timestamps = [e["timestamp"] for e in entries if e.get("timestamp")]
    assert timestamps == sorted(timestamps, reverse=True), "Entries are not sorted DESC"


def test_kill_switch_does_not_emit_stop_row(tmp_path: Path) -> None:
    """A session ended via kill_switch must NOT emit a 'STOPPED' row;
    the kill_switch_events row stands alone."""
    from milodex.gui.read_models import build_ledger_snapshot

    db = tmp_path / "milodex.db"
    _create_full_ledger_db(db)
    configs = tmp_path / "configs"
    configs.mkdir()

    sid = "meanrev.daily.rsi2pullback.v1"
    ts_ks = "2026-05-10T14:00:00+00:00"
    ts_stop = "2026-05-10T14:00:00.005+00:00"  # 5ms later

    conn = sqlite3.connect(str(db))
    # Kill-switch fired at ts_ks
    conn.execute(
        "INSERT INTO kill_switch_events (event_type, recorded_at, reason) VALUES (?, ?, ?)",
        ("triggered", ts_ks, "daily loss cap"),
    )
    # Session closed at ts_stop with exit_reason='kill_switch' (should be excluded)
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-ks', ?, '2026-05-10T09:00:00+00:00', ?, 'kill_switch', '{}')""",
        (sid, ts_stop),
    )
    conn.commit()
    conn.close()

    entries = build_ledger_snapshot(db, configs)["entries"]

    # Only the kill-switch row for this session's timestamp range
    assert not any(e["outcomeKind"] == "stopped" for e in entries), (
        "A 'stopped' row was emitted for a kill_switch session — it should be filtered out"
    )
    assert any(e["outcomeKind"] == "fired" for e in entries), (
        "Expected a 'fired' row from kill_switch_events"
    )


# ---------------------------------------------------------------------------
# Task 24 (PR-6): cross-source timestamp ordering verification
# ---------------------------------------------------------------------------


def test_kill_switch_orders_above_session_stop_when_simultaneous(tmp_path: Path) -> None:
    """When kill-switch fires and strategy_runs.ended_at write happen within ms
    of each other, the kill_switch_events entry must sort above the (excluded)
    stop entry — but the stop entry doesn't appear (excluded by filter), so the
    assertion here is that the kill_switch row appears and no STOPPED row
    appears for the same session.

    Timestamp precision across sources verified consistent (ISO 8601 UTC
    throughout). If a future writer emits a different precision, the sort may
    need re-verification.
    """
    from milodex.gui.read_models import build_ledger_snapshot

    db = tmp_path / "milodex.db"
    _create_full_ledger_db(db)
    configs = tmp_path / "configs"
    configs.mkdir()

    sid = "meanrev.daily.rsi2pullback.v1"
    # Simulated kill-switch fire at T; session ended at T + 5ms with kill_switch reason.
    ts_fire = "2026-05-15T14:30:00.000+00:00"
    ts_end = "2026-05-15T14:30:00.005+00:00"

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO kill_switch_events (event_type, recorded_at, reason) VALUES (?, ?, ?)",
        ("triggered", ts_fire, "max drawdown exceeded"),
    )
    conn.execute(
        """INSERT INTO strategy_runs
           (session_id, strategy_id, started_at, ended_at, exit_reason, metadata_json)
           VALUES ('sess-ks-24', ?, '2026-05-15T09:00:00+00:00', ?, 'kill_switch', '{}')""",
        (sid, ts_end),
    )
    conn.commit()
    conn.close()

    entries = build_ledger_snapshot(db, configs)["entries"]

    # The session-stop row with exit_reason='kill_switch' must be excluded.
    stopped_entries = [e for e in entries if e.get("outcomeKind") == "stopped"]
    assert stopped_entries == [], (
        f"No 'stopped' row expected when exit_reason='kill_switch'; got: {stopped_entries}"
    )

    # The kill-switch fired row must be present.
    fired_entries = [e for e in entries if e.get("outcomeKind") == "fired"]
    assert len(fired_entries) == 1, (
        f"Expected exactly one 'fired' entry; got {len(fired_entries)}: {fired_entries}"
    )
    assert fired_entries[0]["timestamp"] == ts_fire

    # The kill-switch row sorts before (or at the same position as) the session-start row.
    fired_idx = next(i for i, e in enumerate(entries) if e.get("outcomeKind") == "fired")
    started_entries = [
        e for e in entries if e.get("outcomeKind") == "started" and e.get("strategyId") == sid
    ]
    if started_entries:
        started_idx = next(
            i
            for i, e in enumerate(entries)
            if e.get("outcomeKind") == "started" and e.get("strategyId") == sid
        )
        assert fired_idx < started_idx, (
            f"Kill-switch 'fired' row (idx={fired_idx}) must sort before session 'started' row "
            f"(idx={started_idx}) because the kill-switch timestamp is later"
        )


# ---------------------------------------------------------------------------
# PR4: read-only connections + single-connection-per-refresh
# ---------------------------------------------------------------------------


def test_open_ro_conn_rejects_writes(tmp_path: Path) -> None:
    """Connections opened via _open_ro_conn must be read-only.

    mode=ro surfaces any write attempt as sqlite3.OperationalError — that is
    the whole point of this PR.
    """
    import pytest as _pytest

    from milodex.gui.read_models import _open_ro_conn

    db = tmp_path / "test.db"
    # Create the file with a simple table via a normal rw connection first.
    setup = sqlite3.connect(str(db))
    setup.execute("CREATE TABLE t (x INTEGER)")
    setup.commit()
    setup.close()

    conn = _open_ro_conn(db)
    try:
        with _pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO t VALUES (1)")
    finally:
        conn.close()


def test_build_front_page_snapshot_nonexistent_db(tmp_path: Path) -> None:
    """build_front_page_snapshot returns a valid empty snapshot for a missing DB."""
    from milodex.gui.read_models import build_front_page_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    missing = tmp_path / "does_not_exist.db"

    snapshot = build_front_page_snapshot(missing, configs)

    assert "summary" in snapshot
    assert "lastRefreshedAt" in snapshot
    summary = snapshot["summary"]
    assert summary["totalConfigs"] == 0
    assert summary["runningCount"] == 0
    assert summary["pnl"] == {"today": 0.0, "todayPct": 0.0, "sparkline": [0.0]}


def test_build_bench_snapshot_nonexistent_db(tmp_path: Path) -> None:
    """build_bench_snapshot returns a valid empty snapshot for a missing DB."""
    from milodex.gui.read_models import build_bench_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    missing = tmp_path / "does_not_exist.db"

    snapshot = build_bench_snapshot(missing, configs)

    assert "sections" in snapshot
    assert "lastRefreshedAt" in snapshot
    assert len(snapshot["sections"]) == 5  # one per _VISIBLE_STAGES
    for section in snapshot["sections"]:
        assert section["strategies"] == []


def test_build_kanban_snapshot_nonexistent_db(tmp_path: Path) -> None:
    """build_kanban_snapshot returns a valid empty snapshot for a missing DB."""
    from milodex.gui.read_models import build_kanban_snapshot

    configs = tmp_path / "configs"
    configs.mkdir()
    missing = tmp_path / "does_not_exist.db"

    snapshot = build_kanban_snapshot(missing, configs)

    assert "lanes" in snapshot
    assert "summary" in snapshot
    assert "lastRefreshedAt" in snapshot
    assert len(snapshot["lanes"]) == 5
    for lane in snapshot["lanes"]:
        assert lane["cards"] == []


def test_build_ledger_snapshot_nonexistent_db(tmp_path: Path) -> None:
    """build_ledger_snapshot returns an empty entries list for a missing DB."""
    from milodex.gui.read_models import build_ledger_snapshot

    missing = tmp_path / "does_not_exist.db"

    snapshot = build_ledger_snapshot(missing)

    assert snapshot["entries"] == []
    assert "lastRefreshedAt" in snapshot
