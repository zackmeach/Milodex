"""Tests for Bench paper-runner control helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from milodex.strategies.loader import load_strategy_config
from milodex.strategies.paper_runner_control import (
    PaperRunnerControl,
    PaperRunnerLaunchError,
    consume_controlled_stop_request,
    controlled_stop_request_path,
    evaluation_symbol_for_config,
    live_runner_eval_symbols,
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


def test_controlled_stop_request_accepts_utf8_bom(tmp_path: Path) -> None:
    """A BOM-prefixed UTF-8 stop request (e.g. written by a Windows tool that
    emits utf-8-sig) is consumed normally."""
    path = controlled_stop_request_path(tmp_path, "strategy-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"strategy_id": "strategy-a", "mode": "controlled"}),
        encoding="utf-8-sig",
    )

    payload = consume_controlled_stop_request(path, strategy_id="strategy-a")

    assert payload is not None
    assert payload["mode"] == "controlled"
    assert not path.exists()


def test_invalid_json_stop_request_is_preserved_not_deleted(tmp_path: Path, caplog) -> None:
    path = controlled_stop_request_path(tmp_path, "strategy-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"strategy_id": "strategy-a", "mode": "contro', encoding="utf-8")
    invalid_path = path.with_name(path.name + ".invalid")

    with caplog.at_level("WARNING"):
        payload = consume_controlled_stop_request(path, strategy_id="strategy-a")

    assert payload is None
    assert not path.exists()
    assert invalid_path.exists()
    assert invalid_path.read_text(encoding="utf-8").startswith('{"strategy_id"')
    assert any("Invalid controlled-stop request" in r.message for r in caplog.records)


def test_utf16_stop_request_is_preserved_not_deleted(tmp_path: Path, caplog) -> None:
    """PowerShell 5.1 writes UTF-16 LE by default — the soak showed stop-request
    encoding matters. A UTF-16 file is not consumable but must stay diagnosable."""
    path = controlled_stop_request_path(tmp_path, "strategy-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"strategy_id": "strategy-a", "mode": "controlled"}),
        encoding="utf-16",
    )
    invalid_path = path.with_name(path.name + ".invalid")

    with caplog.at_level("WARNING"):
        payload = consume_controlled_stop_request(path, strategy_id="strategy-a")

    assert payload is None
    assert not path.exists()
    assert invalid_path.exists()
    assert any("Invalid controlled-stop request" in r.message for r in caplog.records)


def test_non_object_json_stop_request_is_preserved(tmp_path: Path, caplog) -> None:
    path = controlled_stop_request_path(tmp_path, "strategy-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('["not", "a", "dict"]', encoding="utf-8")
    invalid_path = path.with_name(path.name + ".invalid")

    with caplog.at_level("WARNING"):
        payload = consume_controlled_stop_request(path, strategy_id="strategy-a")

    assert payload is None
    assert invalid_path.exists()
    assert any("non-object JSON payload" in r.message for r in caplog.records)


def test_second_invalid_stop_request_replaces_preserved_copy(tmp_path: Path) -> None:
    path = controlled_stop_request_path(tmp_path, "strategy-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    invalid_path = path.with_name(path.name + ".invalid")

    path.write_text("first-garbage", encoding="utf-8")
    assert consume_controlled_stop_request(path, strategy_id="strategy-a") is None
    path.write_text("second-garbage", encoding="utf-8")
    assert consume_controlled_stop_request(path, strategy_id="strategy-a") is None

    assert invalid_path.read_text(encoding="utf-8") == "second-garbage"


def test_invalid_stop_request_does_not_stop_runner(tmp_path: Path) -> None:
    """A malformed stop request must not be consumed as a stop: the runner's
    check leaves no shutdown requested and the runner keeps going."""
    from milodex.strategies.runner import StrategyRunner

    path = controlled_stop_request_path(tmp_path, "strategy-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json at all", encoding="utf-8")

    runner = StrategyRunner.__new__(StrategyRunner)
    runner._controlled_stop_request_path = path
    runner._strategy_id = "strategy-a"
    runner._requested_shutdown = None

    runner._check_controlled_stop_request()

    assert runner._requested_shutdown is None


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


def test_interpreter_probe_imports_the_runner_entrypoint(tmp_path: Path) -> None:
    # Must probe the *real* runner entrypoint, not the shallow package:
    # a half-broken interpreter can `import milodex` yet fail to
    # `import milodex.cli.main` (the actual `-m` target the runner uses).
    control = PaperRunnerControl(locks_dir=tmp_path)

    command = control._interpreter_probe_command()  # noqa: SLF001

    assert command[1] == "-c"
    assert command[2] == "import milodex.cli.main"


def test_verify_interpreter_passes_for_current_interpreter(tmp_path: Path) -> None:
    # The test interpreter is the project venv: the runner entrypoint imports.
    control = PaperRunnerControl(locks_dir=tmp_path)

    assert control._verify_interpreter() is None  # noqa: SLF001


def test_verify_interpreter_fails_for_missing_interpreter(tmp_path: Path) -> None:
    control = PaperRunnerControl(
        locks_dir=tmp_path,
        python_executable="C:/definitely/not/a/python.exe",
    )

    reason = control._verify_interpreter()  # noqa: SLF001

    assert reason is not None
    assert "C:/definitely/not/a/python.exe" in reason


def test_start_refuses_when_interpreter_cannot_import_milodex(tmp_path: Path) -> None:
    control = PaperRunnerControl(
        locks_dir=tmp_path,
        python_executable="C:/definitely/not/a/python.exe",
    )

    with pytest.raises(PaperRunnerLaunchError) as excinfo:
        control.start("sample.daily.example.curated.v1")

    assert "C:/definitely/not/a/python.exe" in str(excinfo.value)


def test_existing_live_runner_is_none_when_no_lock(tmp_path: Path) -> None:
    control = PaperRunnerControl(locks_dir=tmp_path)

    assert (
        control._existing_live_runner("sample.daily.example.curated.v1")  # noqa: SLF001
        is None
    )


def test_existing_live_runner_detects_held_lock(tmp_path: Path) -> None:
    import os

    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.strategies.paper_runner_control import runner_lock_name

    strategy_id = "sample.daily.example.curated.v1"
    lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=tmp_path)
    lock.acquire()
    try:
        control = PaperRunnerControl(locks_dir=tmp_path)

        holder = control._existing_live_runner(strategy_id)  # noqa: SLF001

        assert holder is not None
        assert holder.pid == os.getpid()
    finally:
        lock.release()


def test_existing_live_runner_is_none_for_recycled_pid(tmp_path: Path) -> None:
    """A stale lock whose PID resolves to a live process but whose started_at
    predates that process (recycled-PID signature, e.g. post-reboot) is not a
    live runner — duplicate-start must not refuse a legitimate relaunch."""
    import os
    from datetime import UTC, datetime

    from milodex.strategies.paper_runner_control import runner_lock_name

    strategy_id = "sample.daily.example.curated.v1"
    lock_path = tmp_path / f"{runner_lock_name(strategy_id)}.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": "ghost",
                "holder_name": "milodex",
                "started_at": datetime.fromtimestamp(0, tz=UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    control = PaperRunnerControl(locks_dir=tmp_path)

    assert control._existing_live_runner(strategy_id) is None  # noqa: SLF001


def test_start_refuses_when_live_runner_already_holds_lock(tmp_path: Path) -> None:
    from milodex.core.advisory_lock import AdvisoryLock
    from milodex.strategies.paper_runner_control import runner_lock_name

    strategy_id = "sample.daily.example.curated.v1"
    lock = AdvisoryLock(runner_lock_name(strategy_id), locks_dir=tmp_path)
    lock.acquire()
    try:
        control = PaperRunnerControl(locks_dir=tmp_path)

        with pytest.raises(PaperRunnerLaunchError) as excinfo:
            control.start(strategy_id)

        assert "already running" in str(excinfo.value)
    finally:
        lock.release()


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


def _write_minimal_strategy_config(
    config_dir: Path,
    *,
    strategy_id: str,
    variant: str = "test",
    universe: list[str] | None = None,
    universe_ref: str | None = None,
) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    universe_lines = ""
    if universe is not None:
        universe_lines = "  universe:\n" + "".join(f'    - "{sym}"\n' for sym in universe)
    universe_ref_line = ""
    if universe_ref is not None:
        universe_ref_line = f'  universe_ref: "{universe_ref}"\n'
    path = config_dir / f"{strategy_id.replace('.', '_')}.yaml"
    path.write_text(
        f"""
strategy:
  id: "{strategy_id}"
  family: "meanrev"
  template: "daily.pullback_rsi2"
  variant: "{variant}"
  version: 1
  description: "Co-run helper test"
  enabled: true
{universe_lines}{universe_ref_line}  parameters:
    rsi_lookback: 2
    rsi_entry_threshold: 10
    rsi_exit_threshold: 50
    ma_filter_length: 200
    stop_loss_pct: 0.05
    max_hold_days: 5
    max_concurrent_positions: 1
    sizing_rule: "equal_notional"
    per_position_notional_pct: 0.10
    ranking_enabled: false
    ranking_metric: "rsi_ascending"
    market_regime_symbol: ""
    market_regime_ma_length: 200
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 0.10
    max_positions: 1
    daily_loss_cap_pct: 0.02
    stop_loss_pct: 0.05
  stage: "paper"
  backtest:
    commission_per_trade: 0.00
    min_trades_required: 30
  disable_conditions_additional: []
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return path


def test_evaluation_symbol_for_config_uses_first_resolved_universe_symbol(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "configs"
    _write_minimal_strategy_config(
        config_dir,
        strategy_id="meanrev.daily.pullback_rsi2.test.v1",
        universe=["SPY", "QQQ"],
    )
    (config_dir / "universe_test.yaml").write_text(
        """
universe:
  id: "test_ref.v1"
  etfs: ["IWM"]
  stocks: ["AAPL"]
""".strip(),
        encoding="utf-8",
    )
    _write_minimal_strategy_config(
        config_dir,
        strategy_id="meanrev.daily.pullback_rsi2.ref_test.v1",
        variant="ref_test",
        universe_ref="test_ref.v1",
    )

    inline = load_strategy_config(config_dir / "meanrev_daily_pullback_rsi2_test_v1.yaml")
    ref = load_strategy_config(config_dir / "meanrev_daily_pullback_rsi2_ref_test_v1.yaml")

    assert evaluation_symbol_for_config(inline) == "SPY"
    assert evaluation_symbol_for_config(ref) == "AAPL"


def test_live_runner_eval_symbols_maps_live_runners_by_eval_symbol(tmp_path: Path) -> None:
    from milodex.core.advisory_lock import AdvisoryLock

    config_dir = tmp_path / "configs"
    locks_dir = tmp_path / "locks"
    strategy_id = "meanrev.daily.pullback_rsi2.test.v1"
    _write_minimal_strategy_config(
        config_dir,
        strategy_id=strategy_id,
        universe=["MSFT"],
    )
    lock = AdvisoryLock(
        f"milodex.runtime.strategy.{strategy_id}",
        locks_dir=locks_dir,
        holder_name=f"milodex strategy run {strategy_id}",
    )
    lock.acquire()
    try:
        mapping = live_runner_eval_symbols(config_dir, locks_dir)

        assert mapping == {"MSFT": strategy_id}
    finally:
        lock.release()


def test_live_runner_eval_symbols_excludes_requested_strategy(tmp_path: Path) -> None:
    from milodex.core.advisory_lock import AdvisoryLock

    config_dir = tmp_path / "configs"
    locks_dir = tmp_path / "locks"
    strategy_id = "meanrev.daily.pullback_rsi2.test.v1"
    _write_minimal_strategy_config(
        config_dir,
        strategy_id=strategy_id,
        universe=["MSFT"],
    )
    lock = AdvisoryLock(
        f"milodex.runtime.strategy.{strategy_id}",
        locks_dir=locks_dir,
        holder_name=f"milodex strategy run {strategy_id}",
    )
    lock.acquire()
    try:
        mapping = live_runner_eval_symbols(
            config_dir,
            locks_dir,
            exclude_strategy_id=strategy_id,
        )

        assert mapping == {}
    finally:
        lock.release()


def test_live_runner_eval_symbols_ignores_stale_lock(tmp_path: Path) -> None:
    import os
    from datetime import UTC, datetime

    config_dir = tmp_path / "configs"
    locks_dir = tmp_path / "locks"
    strategy_id = "meanrev.daily.pullback_rsi2.test.v1"
    _write_minimal_strategy_config(
        config_dir,
        strategy_id=strategy_id,
        universe=["MSFT"],
    )
    lock_path = locks_dir / f"milodex.runtime.strategy.{strategy_id}.lock"
    locks_dir.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "hostname": "ghost",
                "holder_name": "milodex",
                "started_at": datetime.fromtimestamp(0, tz=UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    assert live_runner_eval_symbols(config_dir, locks_dir) == {}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only flag combination")
def test_creationflags_uses_create_no_window_not_detached_process():
    """The subprocess launch flags must include CREATE_NO_WINDOW (the actual
    Windows console suppressor) and must NOT include DETACHED_PROCESS, which
    is mutually exclusive with CREATE_NO_WINDOW per MSDN and paradoxically
    creates a console for a console-subsystem child .exe."""
    from milodex.strategies import paper_runner_control as prc

    flags = prc._compute_creation_flags()

    assert flags & subprocess.CREATE_NO_WINDOW, "CREATE_NO_WINDOW must be set"
    assert not (flags & subprocess.DETACHED_PROCESS), "DETACHED_PROCESS must NOT be set"
    assert flags & subprocess.CREATE_NEW_PROCESS_GROUP, "CREATE_NEW_PROCESS_GROUP preserved"
