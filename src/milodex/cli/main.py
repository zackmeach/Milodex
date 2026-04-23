"""CLI entrypoint. Builds the root parser from command modules and dispatches."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

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
    promote,
    promotion,
    reconcile,
    report,
    status,
    strategy,
    trade,
)
from milodex.cli.commands import (
    config as config_cmd,
)
from milodex.cli.commands.status import _build_status_result  # noqa: F401  (test monkeypatch seam)
from milodex.cli.formatter import get_formatter
from milodex.config import get_data_dir, get_locks_dir, get_logs_dir, get_trading_mode
from milodex.core.advisory_lock import AdvisoryLockError
from milodex.core.event_store import EventStore
from milodex.data.alpaca_provider import AlpacaDataProvider
from milodex.execution import ExecutionService
from milodex.execution.state import KillSwitchStateStore
from milodex.strategies.loader import StrategyLoader
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
}


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
    config_dir: Path = Path("configs"),
    locks_dir: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return a process exit code."""
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    _locks_dir = locks_dir if locks_dir is not None else get_locks_dir()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    as_json = bool(getattr(args, "json_output", False))
    formatter = get_formatter(as_json=as_json)
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
            config_dir=Path("configs"),
            broker_client=broker,
            data_provider=data_provider,
            execution_service=execution_service,
            event_store=event_store,
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
    except (BrokerError, ValueError) as exc:
        result = error_result(command_name, str(exc), code="error")
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
