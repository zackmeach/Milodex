"""CLI integration tests for ``milodex promotion`` (freeze + manifest show)."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import EventStore

_STRATEGY_ID = "regime.daily.sma200_rotation.cli_test.v1"

_STRATEGY_YAML_TEMPLATE = """\
strategy:
  id: "{strategy_id}"
  family: "regime"
  template: "daily.sma200_rotation"
  variant: "cli_test"
  version: 1
  description: "Minimal strategy for promotion CLI tests."
  enabled: true
  universe:
    - "SPY"
    - "SHY"
  parameters:
    ma_period: 200
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
    path = config_dir / "regime_cli.yaml"
    path.write_text(
        _STRATEGY_YAML_TEMPLATE.format(strategy_id=_STRATEGY_ID, stage=stage),
        encoding="utf-8",
    )
    return path


def _run_cli(
    argv: list[str],
    tmp_path: Path,
) -> tuple[int, StringIO, StringIO, Path]:
    out, err = StringIO(), StringIO()
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
    return exit_code, out, err, event_store_path


def _raise(msg: str):
    raise AssertionError(msg)


def test_freeze_happy_path(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="paper")

    exit_code, out, err, db_path = _run_cli(
        ["promotion", "freeze", _STRATEGY_ID],
        tmp_path,
    )

    assert exit_code == 0, err.getvalue()
    output = out.getvalue()
    assert "Frozen manifest" in output
    assert "paper" in output
    assert _STRATEGY_ID in output

    store = EventStore(db_path)
    event = store.get_active_manifest_for_strategy(_STRATEGY_ID, "paper")
    assert event is not None
    assert event.stage == "paper"
    assert event.frozen_by == "operator"


def test_freeze_backtest_stage_errors(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    exit_code, _, err, _ = _run_cli(
        ["promotion", "freeze", _STRATEGY_ID],
        tmp_path,
    )
    assert exit_code != 0
    assert "backtest" in err.getvalue().lower()


def test_manifest_show_when_unfrozen(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="paper")

    exit_code, out, err, _ = _run_cli(
        ["promotion", "manifest", _STRATEGY_ID],
        tmp_path,
    )
    assert exit_code == 0, err.getvalue()
    assert "No active manifest" in out.getvalue()


def test_manifest_show_after_freeze(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="paper")

    exit_code, _, err, _ = _run_cli(
        ["promotion", "freeze", _STRATEGY_ID],
        tmp_path,
    )
    assert exit_code == 0, err.getvalue()

    exit_code, out, err, _ = _run_cli(
        ["promotion", "manifest", _STRATEGY_ID],
        tmp_path,
    )
    assert exit_code == 0, err.getvalue()
    output = out.getvalue()
    assert "Active manifest" in output
    assert _STRATEGY_ID in output
    assert "config_hash:" in output


def test_freeze_json_output(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="paper")

    exit_code, out, err, _ = _run_cli(
        ["--json", "promotion", "freeze", _STRATEGY_ID],
        tmp_path,
    )
    assert exit_code == 0, err.getvalue()
    payload = json.loads(out.getvalue())
    assert payload["command"] == "promotion.freeze"
    assert payload["data"]["strategy_id"] == _STRATEGY_ID
    assert payload["data"]["stage"] == "paper"
    assert len(payload["data"]["config_hash"]) == 64


def test_manifest_show_strategy_not_found(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="paper")

    exit_code, _, err, _ = _run_cli(
        ["promotion", "manifest", "nonexistent.daily.foo.bar.v1"],
        tmp_path,
    )
    assert exit_code != 0
    assert "not found" in err.getvalue().lower()
