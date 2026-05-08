"""gui command -- launch the Milodex graphical interface."""

from __future__ import annotations

import argparse
import logging

from milodex.cli._shared import CommandContext

logger = logging.getLogger(__name__)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``gui`` subcommand on *subparsers*."""
    subparsers.add_parser(
        "gui",
        help="Launch the Milodex GUI.",
    )


def run(args: argparse.Namespace, ctx: CommandContext) -> int:  # noqa: ARG001
    """Handle the ``milodex gui`` command.

    Imports :func:`milodex.gui.app.run_app` and delegates to it.  The import
    is deferred to this function so that PySide6 absence only surfaces when
    the operator actually requests the GUI, not at CLI startup.

    Parameters
    ----------
    args
        Parsed CLI arguments (unused -- the gui command has no options yet).
    ctx
        Shared command context (unused at this stage; available for future
        options such as ``--theme``).

    Returns
    -------
    int
        Exit code returned by :func:`~milodex.gui.app.run_app`, or ``1`` if
        PySide6 is not installed.
    """
    try:
        from milodex.gui.app import run_app
    except ImportError as exc:
        logger.error(
            "gui: cannot import milodex.gui.app -- PySide6 may not be installed. "
            "Install it with: pip install PySide6  (%s)",
            exc,
        )
        return 1

    return run_app()
