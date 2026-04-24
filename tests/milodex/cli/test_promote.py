"""Tests for the refused legacy ``milodex promote`` shortcut.

The legacy command recorded promotions without freezing the strategy
manifest, violating ADR 0015. It now refuses at runtime and points the
operator at ``milodex promotion promote``.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from milodex.cli.commands.promote import resolve_strategy_config
from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import EventStore


def _run_cli(argv: list[str], tmp_path: Path) -> tuple[int, str, str]:
    out = StringIO()
    err = StringIO()
    exit_code = cli_entrypoint(
        argv,
        event_store_factory=lambda: EventStore(tmp_path / "data" / "milodex.db"),
        config_dir=tmp_path / "configs",
        broker_factory=lambda: _unused("broker"),
        data_provider_factory=lambda: _unused("data_provider"),
        stdout=out,
        stderr=err,
    )
    return exit_code, out.getvalue(), err.getvalue()


def _unused(msg: str):
    raise AssertionError(f"{msg} should not be constructed for 'promote' refusal")


def test_legacy_promote_refuses_and_writes_no_events(tmp_path: Path) -> None:
    """The refusal must fire before any event-store write occurs."""
    (tmp_path / "configs").mkdir()
    db_path = tmp_path / "data" / "milodex.db"

    exit_code, _, err = _run_cli(
        ["promote", "some.strategy.id", "--to", "paper", "--lifecycle-exempt"],
        tmp_path,
    )

    assert exit_code != 0
    assert "'milodex promotion promote'" in err
    assert "ADR 0015" in err
    # No side effects: event store file should not have been created.
    assert not db_path.exists(), "refusal must fire before any DB write"


def test_legacy_promote_refuses_with_no_args(tmp_path: Path) -> None:
    """Even a bare `milodex promote` must be refused, not crash on argparse."""
    (tmp_path / "configs").mkdir()

    exit_code, _, err = _run_cli(["promote"], tmp_path)

    assert exit_code != 0
    assert "'milodex promotion promote'" in err


def test_resolve_strategy_config_still_works(tmp_path: Path) -> None:
    """`resolve_strategy_config` is a general helper used outside the legacy
    command; the refusal of the subcommand must not regress it."""
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    path = config_dir / "resolvable.yaml"
    path.write_text(
        """
strategy:
  id: "resolve.daily.test.v.v1"
  family: "resolve"
  template: "daily.test"
  variant: "v"
  version: 1
  description: "helper test"
  enabled: true
  universe: ["SPY"]
  parameters: {}
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.20
    max_positions: 3
    daily_loss_cap_pct: 0.03
    stop_loss_pct: null
  stage: "backtest"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip(),
        encoding="utf-8",
    )

    resolved = resolve_strategy_config("resolve.daily.test.v.v1", config_dir)
    assert resolved == path
