"""config validate command."""

from __future__ import annotations

import argparse
from pathlib import Path

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.config_validation import validate_config_file
from milodex.cli.formatter import CommandResult


def register(subparsers: argparse._SubParsersAction) -> None:
    config_parser = subparsers.add_parser("config", help="Validate Milodex config files.")
    add_global_flags(config_parser)
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    validate_parser = config_subparsers.add_parser("validate", help="Validate a YAML config file.")
    add_global_flags(validate_parser)
    validate_parser.add_argument("path", help="Path to the YAML config file.")
    validate_parser.add_argument(
        "--kind",
        choices=("strategy", "risk"),
        help="Optional config kind override.",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    if args.config_command != "validate":
        raise ValueError(f"Unsupported config command: {args.config_command}")
    return _build_validate_result(Path(args.path), args.kind)


def _build_validate_result(path: Path, kind: str | None) -> CommandResult:
    lines = validate_config_file(path, kind=kind)
    detected_kind: str | None = None
    for line in lines:
        if line.startswith("Detected kind:"):
            detected_kind = line.split(":", 1)[1].strip()
            break
    return CommandResult(
        command="config.validate",
        data={"path": str(path), "kind": detected_kind or kind, "messages": list(lines)},
        human_lines=lines,
    )
