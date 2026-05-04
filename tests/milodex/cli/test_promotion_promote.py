"""CLI tests for ``milodex promotion promote`` (slice 2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path

from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import BacktestRunEvent, EventStore

_STRATEGY_ID = "test.daily.promote_slice2.spy.v1"

_YAML = """\
strategy:
  id: "{strategy_id}"
  family: "test"
  template: "daily.promote_slice2"
  variant: "spy"
  version: 1
  description: "slice-2 promote CLI tests"
  enabled: true
  universe:
    - "SPY"
  parameters: {{}}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.20
    max_positions: 3
    daily_loss_cap_pct: 0.03
    stop_loss_pct: null
  stage: "{stage}"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
  disable_conditions_additional: []
"""


def _write_config(config_dir: Path, stage: str) -> Path:
    path = config_dir / "test_strategy.yaml"
    path.write_text(_YAML.format(strategy_id=_STRATEGY_ID, stage=stage), encoding="utf-8")
    return path


def _run(argv: list[str], tmp_path: Path, *, stdout=None, stderr=None):
    out = stdout or StringIO()
    err = stderr or StringIO()
    event_store_path = tmp_path / "data" / "milodex.db"
    exit_code = cli_entrypoint(
        argv,
        event_store_factory=lambda: EventStore(event_store_path),
        config_dir=tmp_path / "configs",
        broker_factory=lambda: _raise("no broker"),
        data_provider_factory=lambda: _raise("no data provider"),
        stdout=out,
        stderr=err,
    )
    return exit_code, out, err


def _raise(msg: str):
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Required-evidence refusal (AD-8 / R-PRM-008)
# ---------------------------------------------------------------------------


def test_promotion_promote_refuses_without_recommendation(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "paper",
            "--risk",
            "nothing bad",
            "--lifecycle-exempt",
        ],
        tmp_path,
    )

    assert exit_code != 0
    assert "--recommendation" in err.getvalue()


def test_promotion_promote_refuses_without_risk(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "paper",
            "--recommendation",
            "ready for paper",
            "--lifecycle-exempt",
        ],
        tmp_path,
    )

    assert exit_code != 0
    assert "--risk" in err.getvalue()


def test_promotion_promote_refuses_on_blank_recommendation(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "paper",
            "--recommendation",
            "   ",
            "--risk",
            "a real risk",
            "--lifecycle-exempt",
        ],
        tmp_path,
    )

    assert exit_code != 0
    assert "--recommendation" in err.getvalue()


# ---------------------------------------------------------------------------
# Happy path: lifecycle_exempt
# ---------------------------------------------------------------------------


def test_promotion_promote_lifecycle_exempt_writes_manifest_and_promotion(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="backtest")

    exit_code, out, _ = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "paper",
            "--recommendation",
            "regime strategy — lifecycle-exempt, ready for paper",
            "--risk",
            "statistical thresholds not applied",
            "--risk",
            "drift-check relies on correct freeze",
            "--lifecycle-exempt",
        ],
        tmp_path,
    )

    assert exit_code == 0
    assert "paper" in out.getvalue()

    store = EventStore(tmp_path / "data" / "milodex.db")
    promotions = store.list_promotions()
    assert len(promotions) == 1
    p = promotions[0]
    assert p.to_stage == "paper"
    assert p.promotion_type == "lifecycle_exempt"
    assert p.manifest_id is not None
    assert p.evidence_json is not None
    assert p.evidence_json["recommendation"].startswith("regime strategy")
    assert len(p.evidence_json["known_risks"]) == 2

    manifest = store.get_active_manifest_for_strategy(_STRATEGY_ID, "paper")
    assert manifest is not None
    assert p.manifest_id == manifest.id

    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Gate-failure path (statistical with no --run-id)
# ---------------------------------------------------------------------------


def test_promotion_promote_gate_failure_writes_nothing(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="backtest")

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "paper",
            "--recommendation",
            "statistical promote attempt",
            "--risk",
            "we know metrics are insufficient",
        ],
        tmp_path,
    )

    assert exit_code != 0
    assert "BLOCKED" in err.getvalue() or "Sharpe" in err.getvalue()

    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions() == []
    assert store.list_strategy_manifests() == []
    assert 'stage: "backtest"' in config_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_promotion_promote_json_output(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")
    out = StringIO()

    _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "paper",
            "--recommendation",
            "ready",
            "--risk",
            "a risk",
            "--lifecycle-exempt",
            "--json",
        ],
        tmp_path,
        stdout=out,
    )

    payload = json.loads(out.getvalue())
    assert payload["data"]["promoted"] is True
    assert payload["data"]["from_stage"] == "backtest"
    assert payload["data"]["to_stage"] == "paper"
    assert payload["data"]["evidence"]["schema_version"] == 1


# ---------------------------------------------------------------------------
# Phase 1 live-stage refusal (R-PRM-006, ADR 0004)
# ---------------------------------------------------------------------------


def test_promotion_promote_refuses_live_stage_in_phase_one(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="micro_live")

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "live",
            "--recommendation",
            "ready for live",
            "--risk",
            "a risk",
            "--lifecycle-exempt",
            "--confirm",
        ],
        tmp_path,
    )

    assert exit_code != 0
    stderr = err.getvalue()
    assert "blocked during Phase 1" in stderr
    assert "ADR 0004" in stderr
    assert "R-PRM-006" in stderr

    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions() == []
    assert store.list_strategy_manifests() == []
    assert 'stage: "micro_live"' in config_path.read_text(encoding="utf-8")


def test_promotion_promote_refuses_micro_live_in_phase_one(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="paper")

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "micro_live",
            "--recommendation",
            "ready for micro_live",
            "--risk",
            "a risk",
            "--lifecycle-exempt",
        ],
        tmp_path,
    )

    assert exit_code != 0
    assert "blocked during Phase 1" in err.getvalue()
    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions() == []
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# C-2 (PHASE2_PLANNING.md): honest-signal regression — the canonical Phase 1
# truthful-failure scenario travels through the actual operator pathway.
# ---------------------------------------------------------------------------


def _seed_meanrev_walk_forward_run(
    event_store_path: Path,
    *,
    run_id: str,
    strategy_id: str,
) -> None:
    """Seed a walk-forward backtest run with meanrev's actual Phase 1 numbers.

    The OOS-aggregate metadata mirrors session `54e71b30-...` from 2026-04-26:
    Sharpe 0.327, max DD 6.41%, 752 trades over 2015→2024. The promotion
    gate must refuse this evidence specifically because Sharpe < 0.50.
    """
    event_store_path.parent.mkdir(parents=True, exist_ok=True)
    store = EventStore(event_store_path)
    start = datetime(2015, 1, 1, tzinfo=UTC)
    end = datetime(2024, 12, 31, tzinfo=UTC)
    store.append_backtest_run(
        BacktestRunEvent(
            run_id=run_id,
            strategy_id=strategy_id,
            config_path="configs/test.yaml",
            config_hash="fp-meanrev-truthful",
            start_date=start,
            end_date=end,
            started_at=start,
            status="running",
            slippage_pct=0.001,
            commission_per_trade=0.0,
            metadata={
                "initial_equity": 100_000.0,
                "walk_forward": True,
                "oos_aggregate": {
                    "total_return_pct": 4.34,
                    "sharpe": 0.327,
                    "max_drawdown_pct": 6.41,
                    "trading_days": 752,
                    "trade_count": 752,
                },
            },
        )
    )
    store.update_backtest_run_status(run_id, status="completed", ended_at=end + timedelta(days=1))


def test_promotion_promote_refuses_meanrev_shape_evidence_through_cli(tmp_path):
    """End-to-end honest-signal regression — through `milodex promotion promote`.

    Mirrors meanrev's 2026-04-26 reality: walk-forward Sharpe 0.327 (<0.50),
    max DD 6.41% (<15.0), 752 trades (>=30). The CLI must refuse with a
    Sharpe-specific reason, write nothing to the event store, and leave the
    YAML stage unchanged. ADR 0023's "the platform refused to lie about
    meanrev" thesis lives on this test.
    """
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="backtest")
    _seed_meanrev_walk_forward_run(
        tmp_path / "data" / "milodex.db",
        run_id="bt-meanrev-truthful",
        strategy_id=_STRATEGY_ID,
    )

    exit_code, _, err = _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "paper",
            "--run-id",
            "bt-meanrev-truthful",
            "--recommendation",
            "sub-threshold Sharpe; this should refuse",
            "--risk",
            "promotion attempted on insufficient evidence",
        ],
        tmp_path,
    )

    assert exit_code != 0, (
        "honest-signal regression: a walk-forward run with Sharpe 0.327 must "
        "be refused at the promotion gate; passing here means the platform "
        "lost the property ADR 0023 stands on"
    )
    err_text = err.getvalue()
    assert "BLOCKED" in err_text, "human-facing refusal must surface as BLOCKED"
    assert "Sharpe" in err_text, (
        f"refusal must name Sharpe specifically (max DD and trade count both pass); "
        f"got: {err_text!r}"
    )
    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions() == [], (
        "refused promotion must write no PromotionEvent; the audit trail is "
        "preserved as 'no row' rather than 'rejected row' for now"
    )
    assert store.list_strategy_manifests() == [], (
        "refused promotion must write no StrategyManifestEvent either; "
        "manifest freezing is gated on a passing gate result"
    )
    assert 'stage: "backtest"' in config_path.read_text(encoding="utf-8"), (
        "YAML stage must remain at backtest when the gate refuses"
    )
