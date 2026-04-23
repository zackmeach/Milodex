"""CLI tests for ``milodex promotion history`` (slice 2 / AD-9)."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import EventStore

_STRATEGY_ID = "test.daily.history_slice2.spy.v1"

_YAML = """\
strategy:
  id: "{strategy_id}"
  family: "test"
  template: "daily.history_slice2"
  variant: "spy"
  version: 1
  description: "slice-2 history CLI tests"
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


def _raise(msg: str):
    raise AssertionError(msg)


def _run(argv: list[str], tmp_path: Path, *, stdout=None, stderr=None):
    out = stdout or StringIO()
    err = stderr or StringIO()
    exit_code = cli_entrypoint(
        argv,
        event_store_factory=lambda: EventStore(tmp_path / "data" / "milodex.db"),
        config_dir=tmp_path / "configs",
        broker_factory=lambda: _raise("no broker"),
        data_provider_factory=lambda: _raise("no data provider"),
        stdout=out,
        stderr=err,
    )
    return exit_code, out, err


def _promote_and_demote(tmp_path: Path) -> None:
    _run(
        [
            "promotion",
            "promote",
            _STRATEGY_ID,
            "--to",
            "paper",
            "--recommendation",
            "ready for paper",
            "--risk",
            "lifecycle-exempt",
            "--lifecycle-exempt",
        ],
        tmp_path,
    )
    _run(
        [
            "promotion",
            "demote",
            _STRATEGY_ID,
            "--to",
            "backtest",
            "--reason",
            "restaging for verification",
        ],
        tmp_path,
    )


def test_history_renders_reversal_glyph_for_demotion(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")
    _promote_and_demote(tmp_path)

    exit_code, out, _ = _run(
        ["promotion", "history", _STRATEGY_ID],
        tmp_path,
    )
    assert exit_code == 0
    body = out.getvalue()
    # newest-first: demotion row appears before the promotion row it reverses
    demote_idx = body.find("demotion")
    promote_idx = body.find("lifecycle_exempt")
    assert demote_idx != -1 and promote_idx != -1
    assert demote_idx < promote_idx
    # reversal glyph with referenced id
    assert "\u21a9" in body  # ↩


def test_history_empty_strategy(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    exit_code, out, _ = _run(
        ["promotion", "history", _STRATEGY_ID],
        tmp_path,
    )
    assert exit_code == 0
    assert "No promotion history" in out.getvalue()


def test_history_limit_truncates(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")
    _promote_and_demote(tmp_path)

    exit_code, out, _ = _run(
        ["promotion", "history", _STRATEGY_ID, "--limit", "1"],
        tmp_path,
    )
    assert exit_code == 0
    body = out.getvalue()
    assert "demotion" in body
    # the older lifecycle_exempt row is beyond the limit
    assert "lifecycle_exempt" not in body


def test_history_json_output(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")
    _promote_and_demote(tmp_path)
    out = StringIO()

    _run(
        ["promotion", "history", _STRATEGY_ID, "--json"],
        tmp_path,
        stdout=out,
    )
    payload = json.loads(out.getvalue())
    events = payload["data"]["events"]
    assert len(events) == 2
    # newest first
    assert events[0]["promotion_type"] == "demotion"
    assert events[0]["reverses_event_id"] == events[1]["id"]
    assert events[1]["promotion_type"] == "lifecycle_exempt"
    assert events[1]["reverses_event_id"] is None
