"""CLI integration tests for ``milodex experiment`` (F-PR2).

Tests invoke cli_entrypoint directly with a tmp-path event store, matching
the harness pattern in test_promotion.py.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from milodex.cli.main import main as cli_entrypoint
from milodex.core.event_store import EventStore


def _run_cli(argv: list[str], tmp_path: Path) -> tuple[int, StringIO, StringIO, EventStore]:
    out, err = StringIO(), StringIO()
    db_path = tmp_path / "data" / "milodex.db"
    store = EventStore(db_path)

    def _no_broker():
        raise AssertionError("broker not needed")

    def _no_data():
        raise AssertionError("data_provider not needed")

    exit_code = cli_entrypoint(
        argv,
        event_store_factory=lambda: store,
        config_dir=tmp_path / "configs",
        broker_factory=_no_broker,
        data_provider_factory=_no_data,
        stdout=out,
        stderr=err,
    )
    return exit_code, out, err, store


# ── helpers ───────────────────────────────────────────────────────────────────


def _create_argv(
    experiment_id: str = "exp-001",
    hypothesis: str = "VWAP reversion has edge",
    stage_reached: str = "backtest",
    terminal_status: str = "rejected",
    rationale: str = "Sharpe < 0.5 OOS",
    **extra,
) -> list[str]:
    argv = [
        "experiment",
        "create",
        "--experiment-id",
        experiment_id,
        "--hypothesis",
        hypothesis,
        "--stage-reached",
        stage_reached,
        "--terminal-status",
        terminal_status,
        "--rationale",
        rationale,
    ]
    for k, v in extra.items():
        argv.extend([f"--{k.replace('_', '-')}", v])
    return argv


# ── tests ─────────────────────────────────────────────────────────────────────


def test_create_then_list_returns_entry(tmp_path: Path) -> None:
    """create then list shows the entry; filter keeps only matching status."""
    exit_code, out, err, store = _run_cli(_create_argv(), tmp_path)
    assert exit_code == 0, err.getvalue()

    # list all
    exit_code2, out2, err2, _ = _run_cli(["experiment", "list"], tmp_path)
    assert exit_code2 == 0, err2.getvalue()
    body = out2.getvalue()
    assert "exp-001" in body

    # filter match
    exit_code3, out3, err3, _ = _run_cli(
        ["experiment", "list", "--terminal-status", "rejected"], tmp_path
    )
    assert exit_code3 == 0
    assert "exp-001" in out3.getvalue()

    # filter no-match
    exit_code4, out4, err4, _ = _run_cli(
        ["experiment", "list", "--terminal-status", "promoted"], tmp_path
    )
    assert exit_code4 == 0
    assert "exp-001" not in out4.getvalue()


def test_update_appended_and_show_reflects_change(tmp_path: Path) -> None:
    """update writes a new row; show/get_experiment returns the updated value."""
    _run_cli(_create_argv(terminal_status="active"), tmp_path)

    exit_code, out, err, store = _run_cli(
        [
            "experiment",
            "update",
            "--experiment-id",
            "exp-001",
            "--terminal-status",
            "rejected",
            "--rationale",
            "Updated: OOS confirmed poor",
        ],
        tmp_path,
    )
    assert exit_code == 0, err.getvalue()

    # verify via store directly
    evt = store.get_experiment("exp-001")
    assert evt is not None
    assert evt.terminal_status == "rejected"
    assert "Updated" in evt.rationale

    # verify via show subcommand
    exit_code2, out2, err2, _ = _run_cli(
        ["experiment", "show", "--experiment-id", "exp-001"], tmp_path
    )
    assert exit_code2 == 0
    assert "rejected" in out2.getvalue()
    assert "Updated" in out2.getvalue()


def test_update_missing_experiment_is_clean_error(tmp_path: Path) -> None:
    """update on a non-existent experiment_id returns a non-zero exit, not a traceback."""
    exit_code, out, err, _ = _run_cli(
        [
            "experiment",
            "update",
            "--experiment-id",
            "does-not-exist",
            "--terminal-status",
            "abandoned",
        ],
        tmp_path,
    )
    assert exit_code != 0
    # No unhandled KeyError traceback in stderr
    assert "Traceback" not in err.getvalue()
    assert "Traceback" not in out.getvalue()


def test_create_invalid_terminal_status_rejected_by_argparse(tmp_path: Path) -> None:
    """argparse choices= rejects an unknown terminal_status before store is touched."""
    with pytest.raises(SystemExit) as exc_info:
        cli_entrypoint(
            [
                "experiment",
                "create",
                "--experiment-id",
                "e",
                "--hypothesis",
                "h",
                "--stage-reached",
                "backtest",
                "--terminal-status",
                "BOGUS",
                "--rationale",
                "r",
            ],
            event_store_factory=lambda: EventStore(tmp_path / "milodex.db"),
            config_dir=tmp_path,
            broker_factory=lambda: None,
            data_provider_factory=lambda: None,
            stdout=StringIO(),
            stderr=StringIO(),
        )
    assert exc_info.value.code != 0


def test_evidence_json_inline_string_parsed_into_dict(tmp_path: Path) -> None:
    """--evidence-json as inline JSON string is stored as a dict."""
    evidence = {"sharpe": 0.42, "trades": 17}
    argv = _create_argv(terminal_status="rejected", evidence_json=json.dumps(evidence))
    exit_code, out, err, store = _run_cli(argv, tmp_path)
    assert exit_code == 0, err.getvalue()

    evt = store.get_experiment("exp-001")
    assert evt is not None
    assert evt.evidence_json == evidence
