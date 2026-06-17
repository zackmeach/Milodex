"""CLI entrypoint. Builds the root parser from command modules and dispatches."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TextIO

from milodex._logging import install_file_handler
from milodex.backtesting.engine import BacktestEngine
from milodex.broker import BrokerError
from milodex.broker.alpaca_client import AlpacaBrokerClient
from milodex.cli._shared import (
    CommandContext,
    add_global_flags,
    command_name_from_args,
    error_result,
)
from milodex.cli.commands import (
    analytics,
    backtest,
    data,
    maintenance,
    promote,
    promotion,
    reconcile,
    report,
    research,
    status,
    strategy,
    trade,
)
from milodex.cli.commands import (
    config as config_cmd,
)
from milodex.cli.commands import gui as gui_cmd
from milodex.cli.commands.status import _build_status_result  # noqa: F401  (test monkeypatch seam)
from milodex.cli.formatter import get_formatter
from milodex.config import (
    get_bundled_resource_dir,
    get_data_dir,
    get_locks_dir,
    get_logs_dir,
    get_trading_mode,
)
from milodex.core.advisory_lock import AdvisoryLockError
from milodex.core.event_store import EventStore
from milodex.data.alpaca_provider import AlpacaDataProvider
from milodex.data.bar_quality import DataQualityError
from milodex.execution import ExecutionService, UnsupportedOrderTypeError
from milodex.execution.state import KillSwitchStateStore
from milodex.strategies.loader import StrategyLoader
from milodex.strategies.paper_runner_control import controlled_stop_request_path
from milodex.strategies.runner import StrategyRunner

_COMMAND_MODULES = (
    status,
    data,
    config_cmd,
    trade,
    strategy,
    backtest,
    analytics,
    promote,
    promotion,
    report,
    reconcile,
    research,
    maintenance,
    gui_cmd,
)

_DISPATCH = {
    "status": status,
    "positions": status,
    "orders": status,
    "data": data,
    "config": config_cmd,
    "trade": trade,
    "strategy": strategy,
    "backtest": backtest,
    "analytics": analytics,
    "promote": promote,
    "promotion": promotion,
    "report": report,
    "reconcile": reconcile,
    "research": research,
    "maintenance": maintenance,
}


def _force_utf8_streams() -> None:
    """Force process stdout/stderr to UTF-8 so non-Latin1 glyphs (e.g. the
    promotion-history reversal '↩', '≥', '∞') neither crash nor
    mojibake on a cp1252 Windows console.

    Mutates the process-global ``sys.stdout``/``sys.stderr`` (including pytest's
    capture stream) for the life of the process; idempotent and ASCII-safe.
    No-op for streams that cannot reconfigure (already-UTF-8 pipes, detached or
    closed streams, non-reconfigurable capture objects).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            # Detached/closed/non-reconfigurable stream — leave as-is.
            pass


def build_parser() -> argparse.ArgumentParser:
    """Build the root CLI parser by delegating subparser registration to command modules."""
    parser = argparse.ArgumentParser(prog="milodex", description="Milodex operator CLI.")
    add_global_flags(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for module in _COMMAND_MODULES:
        module.register(subparsers)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    broker_factory=AlpacaBrokerClient,
    data_provider_factory=AlpacaDataProvider,
    execution_service_factory=None,
    strategy_runner_factory=None,
    backtest_engine_factory=None,
    event_store_factory=None,
    config_dir: Path | None = None,
    locks_dir: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a process exit code."""
    _force_utf8_streams()
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    # Resolve config_dir lazily so frozen-bundle detection in
    # get_bundled_resource_dir() runs at call time, not at import time.
    # Callers (e.g. tests) can still pass an explicit Path to override.
    if config_dir is None:
        config_dir = get_bundled_resource_dir() / "configs"
    install_file_handler(get_logs_dir())
    _locks_dir = locks_dir if locks_dir is not None else get_locks_dir()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    as_json = bool(getattr(args, "json_output", False))
    formatter = get_formatter(as_json=as_json, stdout=stdout)
    command_name = command_name_from_args(args)

    def get_event_store() -> EventStore:
        if event_store_factory is not None:
            return event_store_factory()
        return EventStore(get_data_dir() / "milodex.db")

    def get_execution_service() -> ExecutionService:
        if execution_service_factory is not None:
            return execution_service_factory()
        return ExecutionService(
            broker_client=broker_factory(),
            data_provider=data_provider_factory(),
        )

    def get_strategy_runner(strategy_id: str) -> StrategyRunner:
        if strategy_runner_factory is not None:
            return strategy_runner_factory(strategy_id)
        broker = broker_factory()
        data_provider = data_provider_factory()
        event_store = get_event_store()
        kill_switch_store = KillSwitchStateStore(
            event_store=event_store,
            legacy_path=get_logs_dir() / "kill_switch_state.json",
        )
        execution_service = ExecutionService(
            broker_client=broker,
            data_provider=data_provider,
            kill_switch_store=kill_switch_store,
            event_store=event_store,
        )
        return StrategyRunner(
            strategy_id=strategy_id,
            config_dir=config_dir,
            broker_client=broker,
            data_provider=data_provider,
            execution_service=execution_service,
            event_store=event_store,
            controlled_stop_request_path=controlled_stop_request_path(
                _locks_dir,
                strategy_id,
            ),
        )

    def get_backtest_engine(strategy_id: str, **kwargs) -> BacktestEngine:
        if backtest_engine_factory is not None:
            return backtest_engine_factory(strategy_id, **kwargs)
        loader = StrategyLoader()
        config_path = _resolve_strategy_config(strategy_id, config_dir)
        loaded = loader.load(config_path)
        event_store = get_event_store()
        data_provider = data_provider_factory()
        return BacktestEngine(
            loaded=loaded,
            data_provider=data_provider,
            event_store=event_store,
            **kwargs,
        )

    ctx = CommandContext(
        get_execution_service=get_execution_service,
        get_strategy_runner=get_strategy_runner,
        get_backtest_engine=get_backtest_engine,
        get_event_store=get_event_store,
        broker_factory=broker_factory,
        data_provider_factory=data_provider_factory,
        get_trading_mode=lambda: get_trading_mode(),
        config_dir=config_dir,
        locks_dir=_locks_dir,
        stdout=stdout,
    )

    # gui bypasses the standard CommandResult dispatch — it owns the event loop.
    if args.command == "gui":
        return gui_cmd.run(args, ctx)

    module = _DISPATCH.get(args.command)
    if module is None:
        result = error_result(command_name, f"Unsupported command: {args.command}")
        print(formatter.render(result), file=stderr)
        return 1

    try:
        result = module.run(args, ctx)
    except AdvisoryLockError as exc:
        result = error_result(command_name, str(exc), code="advisory_lock_held")
        print(formatter.render(result), file=stderr)
        return 1
    except UnsupportedOrderTypeError as exc:
        result = error_result(command_name, str(exc), code="unsupported_order_type")
        print(formatter.render(result), file=stderr)
        return 1
    except DataQualityError as exc:
        result = error_result(
            command_name,
            str(exc),
            code="data_quality_failed",
            data={"data_quality": exc.report.to_dict()},
        )
        print(formatter.render(result), file=stderr)
        return 1
    except (BrokerError, ValueError) as exc:
        result = error_result(command_name, str(exc), code="error")
        print(formatter.render(result), file=stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — final operator-facing safety net
        logging.getLogger(__name__).exception("Unexpected CLI error in %s", command_name)
        result = error_result(
            command_name,
            f"Unexpected error ({type(exc).__name__}): {exc}. "
            "Full traceback written to the log directory.",
            code="unexpected_error",
        )
        print(formatter.render(result), file=stderr)
        return 1

    rendered = formatter.render(result)
    if rendered:
        stream = stdout if result.status == "success" else stderr
        print(rendered, file=stream)
    return 0 if result.status == "success" else 1


def _resolve_strategy_config(strategy_id: str, config_dir: Path = Path("configs")) -> Path:
    from milodex.cli.commands.promote import resolve_strategy_config

    return resolve_strategy_config(strategy_id, config_dir)


if __name__ == "__main__":
    raise SystemExit(main())
