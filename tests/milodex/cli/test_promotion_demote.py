"""CLI tests for ``milodex promotion demote`` (slice 2 / AD-6)."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import EventStore

_STRATEGY_ID = "test.daily.demote_slice2.spy.v1"

_YAML = """\
strategy:
  id: "{strategy_id}"
  family: "test"
  template: "daily.demote_slice2"
  variant: "spy"
  version: 1
  description: "slice-2 demote CLI tests"
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


def _run(argv: list[str], tmp_path: Path):
    out = StringIO()
    err = StringIO()
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


def _raise(msg: str):
    raise AssertionError(msg)


def _promote_first(tmp_path: Path) -> None:
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
            "manifest drift risk",
            "--lifecycle-exempt",
        ],
        tmp_path,
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_demote_to_backtest_updates_yaml_and_links_reversal(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="backtest")
    _promote_first(tmp_path)

    exit_code, out, _ = _run(
        [
            "promotion",
            "demote",
            _STRATEGY_ID,
            "--to",
            "backtest",
            "--reason",
            "restaging for slice-2 verification",
        ],
        tmp_path,
    )

    assert exit_code == 0
    assert "backtest" in out.getvalue()

    store = EventStore(tmp_path / "data" / "milodex.db")
    promotions = store.list_promotions()
    assert len(promotions) == 2
    promote_row, demote_row = promotions[0], promotions[1]
    assert promote_row.promotion_type == "lifecycle_exempt"
    assert demote_row.promotion_type == "demotion"
    assert demote_row.from_stage == "paper"
    assert demote_row.to_stage == "backtest"
    assert demote_row.reverses_event_id == promote_row.id
    assert "restaging" in demote_row.notes

    assert 'stage: "backtest"' in config_path.read_text(encoding="utf-8")


def test_demote_to_disabled_leaves_yaml_alone(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = _write_config(config_dir, stage="backtest")
    _promote_first(tmp_path)

    exit_code, _, _ = _run(
        [
            "promotion",
            "demote",
            _STRATEGY_ID,
            "--to",
            "disabled",
            "--reason",
            "retiring pending rewrite",
        ],
        tmp_path,
    )

    assert exit_code == 0
    store = EventStore(tmp_path / "data" / "milodex.db")
    demote_row = store.list_promotions()[-1]
    assert demote_row.to_stage == "disabled"
    assert demote_row.promotion_type == "demotion"
    # YAML untouched — stage still reads 'paper' from the prior promote.
    assert 'stage: "paper"' in config_path.read_text(encoding="utf-8")


def test_demote_carries_evidence_ref_in_notes(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")
    _promote_first(tmp_path)

    _run(
        [
            "promotion",
            "demote",
            _STRATEGY_ID,
            "--to",
            "backtest",
            "--reason",
            "post-incident demotion",
            "--evidence-ref",
            "INC-1234",
        ],
        tmp_path,
    )

    store = EventStore(tmp_path / "data" / "milodex.db")
    demote_row = store.list_promotions()[-1]
    assert "INC-1234" in demote_row.notes


# ---------------------------------------------------------------------------
# Refusal path
# ---------------------------------------------------------------------------


def test_demote_without_prior_promotion_records_null_reversal(tmp_path):
    """Legitimate edge: strategy is at 'paper' pre-slice-2 without a promote row."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="paper")

    exit_code, _, _ = _run(
        [
            "promotion",
            "demote",
            _STRATEGY_ID,
            "--to",
            "backtest",
            "--reason",
            "pre-existing paper stage with no promotion row",
        ],
        tmp_path,
    )

    assert exit_code == 0
    store = EventStore(tmp_path / "data" / "milodex.db")
    demote_row = store.list_promotions()[0]
    assert demote_row.reverses_event_id is None


def test_demote_same_stage_refused(tmp_path):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    _write_config(config_dir, stage="backtest")

    exit_code, _, err = _run(
        [
            "promotion",
            "demote",
            _STRATEGY_ID,
            "--to",
            "backtest",
            "--reason",
            "nothing to demote",
        ],
        tmp_path,
    )

    assert exit_code != 0
    assert "already at stage" in err.getvalue()
