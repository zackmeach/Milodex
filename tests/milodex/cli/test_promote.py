"""CLI integration tests for ``milodex promote``.

Each test injects:
  - a temporary ``config_dir`` containing a minimal valid strategy YAML
  - an ``event_store_factory`` pointing to a tmp SQLite DB

This avoids touching the real ``configs/`` directory or ``data/milodex.db``.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import EventStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STRATEGY_ID = "test.daily.gate_test.spy.v1"

_STRATEGY_YAML_TEMPLATE = """\
strategy:
  id: "{strategy_id}"
  family: "test"
  template: "daily.gate_test"
  variant: "spy"
  version: 1
  description: "Minimal strategy for promote CLI tests."
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
    path.write_text(
        _STRATEGY_YAML_TEMPLATE.format(strategy_id=_STRATEGY_ID, stage=stage),
        encoding="utf-8",
    )
    return path


def _run_cli(
    argv: list[str],
    tmp_path: Path,
    *,
    stdout: StringIO | None = None,
    stderr: StringIO | None = None,
) -> tuple[int, StringIO, StringIO]:
    """Run cli_entrypoint with injected tmp event store and config dir."""
    out = stdout or StringIO()
    err = stderr or StringIO()
    event_store_path = tmp_path / "data" / "milodex.db"

    exit_code = cli_entrypoint(
        argv,
        event_store_factory=lambda: EventStore(event_store_path),
        config_dir=tmp_path / "configs",
        broker_factory=lambda: _raise("broker not needed"),
        data_provider_factory=lambda: _raise("data_provider not needed"),
        stdout=out,
        stderr=err,
    )
    return exit_code, out, err


def _raise(msg: str):
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Invalid stage transition errors
# ---------------------------------------------------------------------------


def test_promote_invalid_stage_downgrade_exits_nonzero(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="paper")

    exit_code, _, err = _run_cli(
        ["promote", _STRATEGY_ID, "--to", "paper", "--lifecycle-exempt"],
        tmp_path,
    )

    assert exit_code != 0
    assert "already at stage" in err.getvalue()


def test_promote_unknown_strategy_exits_nonzero(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()

    exit_code, _, err = _run_cli(
        ["promote", "nonexistent.strategy.id", "--to", "paper", "--lifecycle-exempt"],
        tmp_path,
    )

    assert exit_code != 0


# ---------------------------------------------------------------------------
# Gate failures
# ---------------------------------------------------------------------------


def test_promote_statistical_no_run_id_blocked(tmp_path: Path) -> None:
    """No --run-id and no --lifecycle-exempt: all metrics are None → gate fails."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    exit_code, _, err = _run_cli(
        ["promote", _STRATEGY_ID, "--to", "paper"],
        tmp_path,
    )

    assert exit_code != 0
    output = err.getvalue()
    assert "BLOCKED" in output or "failed" in output.lower() or "Sharpe" in output


def test_promote_gate_failures_not_recorded_in_store(tmp_path: Path) -> None:
    """Gate failure must not write a PromotionEvent to the event store."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    _run_cli(["promote", _STRATEGY_ID, "--to", "paper"], tmp_path)

    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions() == []


def test_promote_gate_failure_config_file_unchanged(tmp_path: Path) -> None:
    """Config file must not be modified when the gate blocks the promotion."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="backtest")
    original_content = config_path.read_text(encoding="utf-8")

    _run_cli(["promote", _STRATEGY_ID, "--to", "paper"], tmp_path)

    assert config_path.read_text(encoding="utf-8") == original_content


# ---------------------------------------------------------------------------
# Lifecycle-exempt success path
# ---------------------------------------------------------------------------


def test_promote_lifecycle_exempt_exits_zero(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    exit_code, out, _ = _run_cli(
        ["promote", _STRATEGY_ID, "--to", "paper", "--lifecycle-exempt"],
        tmp_path,
    )

    assert exit_code == 0
    assert "backtest" in out.getvalue()
    assert "paper" in out.getvalue()


def test_promote_lifecycle_exempt_records_event(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    _run_cli(
        ["promote", _STRATEGY_ID, "--to", "paper", "--lifecycle-exempt"],
        tmp_path,
    )

    store = EventStore(tmp_path / "data" / "milodex.db")
    promotions = store.list_promotions()
    assert len(promotions) == 1
    p = promotions[0]
    assert p.strategy_id == _STRATEGY_ID
    assert p.from_stage == "backtest"
    assert p.to_stage == "paper"
    assert p.promotion_type == "lifecycle_exempt"


def test_promote_lifecycle_exempt_updates_config_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="backtest")

    _run_cli(
        ["promote", _STRATEGY_ID, "--to", "paper", "--lifecycle-exempt"],
        tmp_path,
    )

    content = config_path.read_text(encoding="utf-8")
    assert 'stage: "paper"' in content
    assert 'stage: "backtest"' not in content


def test_promote_approved_by_recorded(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    _run_cli(
        [
            "promote", _STRATEGY_ID, "--to", "paper",
            "--lifecycle-exempt", "--approved-by", "alice",
        ],
        tmp_path,
    )

    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions()[0].approved_by == "alice"


def test_promote_notes_recorded(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    _run_cli(
        [
            "promote", _STRATEGY_ID, "--to", "paper",
            "--lifecycle-exempt", "--notes", "Phase 1 paper evidence filed.",
        ],
        tmp_path,
    )

    store = EventStore(tmp_path / "data" / "milodex.db")
    assert store.list_promotions()[0].notes == "Phase 1 paper evidence filed."


def test_promote_sequential_promotions(tmp_path: Path) -> None:
    """Promote backtest→paper then paper→micro_live; two events recorded."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    _run_cli(["promote", _STRATEGY_ID, "--to", "paper", "--lifecycle-exempt"], tmp_path)
    _run_cli(["promote", _STRATEGY_ID, "--to", "micro_live", "--lifecycle-exempt"], tmp_path)

    store = EventStore(tmp_path / "data" / "milodex.db")
    promotions = store.list_promotions()
    assert len(promotions) == 2
    assert promotions[0].to_stage == "paper"
    assert promotions[1].to_stage == "micro_live"
    latest = store.get_latest_promotion_for_strategy(_STRATEGY_ID)
    assert latest is not None
    assert latest.to_stage == "micro_live"


# ---------------------------------------------------------------------------
# Promoting to 'live'
# ---------------------------------------------------------------------------


def test_promote_to_live_requires_confirm(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="micro_live")

    exit_code, _, err = _run_cli(
        ["promote", _STRATEGY_ID, "--to", "live", "--lifecycle-exempt"],
        tmp_path,
    )

    assert exit_code != 0
    assert "--confirm" in err.getvalue()


def test_promote_to_live_with_confirm_succeeds(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="micro_live")

    exit_code, out, _ = _run_cli(
        [
            "promote", _STRATEGY_ID, "--to", "live",
            "--lifecycle-exempt", "--confirm",
        ],
        tmp_path,
    )

    assert exit_code == 0
    store = EventStore(tmp_path / "data" / "milodex.db")
    p = store.list_promotions()[0]
    assert p.to_stage == "live"


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def test_promote_json_output_success(tmp_path: Path) -> None:
    import json

    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")
    out = StringIO()

    _run_cli(
        ["promote", _STRATEGY_ID, "--to", "paper", "--lifecycle-exempt", "--json"],
        tmp_path,
        stdout=out,
    )

    payload = json.loads(out.getvalue())
    assert payload["data"]["promoted"] is True
    assert payload["data"]["from_stage"] == "backtest"
    assert payload["data"]["to_stage"] == "paper"


def test_promote_json_output_failure(tmp_path: Path) -> None:
    import json

    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")
    err = StringIO()

    exit_code, _, _ = _run_cli(
        ["promote", _STRATEGY_ID, "--to", "paper", "--json"],
        tmp_path,
        stderr=err,
    )

    assert exit_code != 0
    payload = json.loads(err.getvalue())
    assert payload["data"]["promoted"] is False
    assert len(payload["data"]["gate_failures"]) > 0
