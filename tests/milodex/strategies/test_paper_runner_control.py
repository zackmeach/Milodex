"""Tests for Bench paper-runner control helpers."""

from __future__ import annotations

import json
from pathlib import Path

from milodex.strategies.paper_runner_control import (
    PaperRunnerControl,
    consume_controlled_stop_request,
    controlled_stop_request_path,
)
from milodex.strategies.runner import StrategyRunner


def test_controlled_stop_request_round_trips_and_is_consumed(tmp_path: Path) -> None:
    control = PaperRunnerControl(locks_dir=tmp_path)

    result = control.request_controlled_stop(
        "sample.daily.example.curated.v1",
        holder={"pid": 123, "hostname": "test-host"},
    )
    payload = consume_controlled_stop_request(
        result.request_path,
        strategy_id="sample.daily.example.curated.v1",
    )

    assert payload is not None
    assert payload["mode"] == "controlled"
    assert payload["holder"]["pid"] == 123
    assert not result.request_path.exists()


def test_controlled_stop_request_ignores_other_strategy(tmp_path: Path) -> None:
    path = controlled_stop_request_path(tmp_path, "strategy-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"strategy_id": "strategy-a", "mode": "controlled"}),
        encoding="utf-8",
    )

    payload = consume_controlled_stop_request(path, strategy_id="strategy-b")

    assert payload is None
    assert path.exists()


def test_runner_log_path_is_none_when_no_log_dir(tmp_path: Path) -> None:
    control = PaperRunnerControl(locks_dir=tmp_path)

    assert control._runner_log_path("sample.daily.example.curated.v1") is None  # noqa: SLF001


def test_runner_log_path_is_under_log_dir(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    control = PaperRunnerControl(locks_dir=tmp_path, log_dir=log_dir)

    path = control._runner_log_path("sample.daily.example.curated.v1")  # noqa: SLF001

    assert path is not None
    assert path.parent == log_dir
    assert path.name.startswith("runner.sample.daily.example.curated.v1.")
    assert path.suffix == ".log"


def test_open_runner_stdio_discards_when_no_path() -> None:
    import subprocess

    stdout, stderr, handle = PaperRunnerControl._open_runner_stdio(None)  # noqa: SLF001

    assert stdout == subprocess.DEVNULL
    assert stderr == subprocess.DEVNULL
    assert handle is None


def test_open_runner_stdio_writes_to_log_path(tmp_path: Path) -> None:
    import subprocess

    log_path = tmp_path / "logs" / "runner.sample.v1.20260518T150000.log"

    stdout, stderr, handle = PaperRunnerControl._open_runner_stdio(log_path)  # noqa: SLF001

    try:
        assert handle is not None
        assert stdout is handle
        assert stderr == subprocess.STDOUT
        handle.write("child-output\n")
        handle.flush()
    finally:
        if handle is not None:
            handle.close()

    assert log_path.exists()
    assert "child-output" in log_path.read_text(encoding="utf-8")


def test_strategy_runner_check_sets_controlled_shutdown(tmp_path: Path) -> None:
    strategy_id = "sample.daily.example.curated.v1"
    path = controlled_stop_request_path(tmp_path, strategy_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"strategy_id": strategy_id, "mode": "controlled"}),
        encoding="utf-8",
    )
    runner = object.__new__(StrategyRunner)
    runner._strategy_id = strategy_id  # noqa: SLF001
    runner._requested_shutdown = None  # noqa: SLF001
    runner._controlled_stop_request_path = path  # noqa: SLF001

    runner._check_controlled_stop_request()  # noqa: SLF001

    assert runner._requested_shutdown == "controlled"  # noqa: SLF001
    assert not path.exists()
