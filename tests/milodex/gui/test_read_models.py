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
        assert preview["executable"] is False, "PR N must keep executable=False"
        assert preview["wired"] is False, "PR N must keep wired=False"


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
        assert "No command is submitted" in note
        assert "no event is written" in note
        assert "no state is changed" in note


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
    assert "Bench v1 renders this intent packet for review only." in preview["safetyCopy"]


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
        "initiate_backtest": "backtest_request_event",
        "refresh_backtest": "backtest_refresh_event",
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
