"""Legacy ``milodex promote`` shortcut — refused.

The legacy ``promote`` command recorded a ``PromotionEvent`` directly via
``event_store.append_promotion`` with no accompanying
``StrategyManifestEvent``. Per ADR 0015 every promotion must carry a frozen
manifest that the runtime can match by ``config_hash``; the legacy path
bypassed that guarantee. A strategy could reach ``micro_live`` with
``manifest_id=null`` in the governance ledger, leaving the risk layer as the
only thing blocking unsealed live orders (which is what happened on
2026-04-24 at 13:42 UTC — nine ``no_frozen_manifest`` refusals on a SPY
exit).

The authoritative path is ``milodex promotion promote``, which routes through
``milodex.promotion.state_machine.transition`` and writes manifest +
promotion atomically.

This module still exposes :func:`resolve_strategy_config` because other
commands (``milodex.cli.main``) import it; the CLI subcommand itself now
refuses with a pointer to the replacement.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from milodex.cli._shared import CommandContext, add_global_flags
from milodex.cli.formatter import CommandResult


def register(subparsers: argparse._SubParsersAction) -> None:
    promote_parser = subparsers.add_parser(
        "promote",
        help=(
            "REFUSED. Use 'milodex promotion promote' — the legacy shortcut "
            "skipped the manifest freeze required by ADR 0015."
        ),
    )
    add_global_flags(promote_parser)
    # Accept (and ignore) any positional/optional args so the refusal fires
    # from run(), not argparse. Users copying old commands off of notes get a
    # helpful error instead of a cryptic "unrecognized arguments" failure.
    promote_parser.add_argument("args", nargs=argparse.REMAINDER)


def run(args: argparse.Namespace, ctx: CommandContext) -> CommandResult:
    _ = args, ctx
    raise ValueError(
        "'milodex promote' was removed — it recorded promotions without "
        "freezing the strategy manifest, violating ADR 0015. Use "
        "'milodex promotion promote' instead; it freezes the manifest and "
        "records the promotion atomically."
    )


def resolve_strategy_config(strategy_id: str, config_dir: Path = Path("configs")) -> Path:
    """Locate the YAML file whose ``strategy.id`` matches ``strategy_id``.

    Kept here (and re-exported via :mod:`milodex.cli.main`) because several
    commands resolve a strategy_id → config path the same way. Not coupled
    to the legacy promote flow.
    """
    from milodex.strategies.loader import load_strategy_config

    for path in sorted(config_dir.glob("*.yaml")):
        try:
            config = load_strategy_config(path)
        except ValueError:
            continue
        if config.strategy_id == strategy_id:
            return path
    msg = f"Strategy config not found for strategy id: {strategy_id}"
    raise ValueError(msg)
