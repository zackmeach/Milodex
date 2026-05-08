"""Tests for the ``milodex gui`` CLI command.

Tests:
- test_gui_command_registers: argparse smoke test confirming ``milodex gui``
  is a valid subcommand (does not require Qt).
- test_gui_command_help_text: help text mentions the GUI.
- test_gui_handler_calls_run_app: handler calls run_app and propagates the
  return value.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from milodex.cli.main import build_parser

# ---------------------------------------------------------------------------
# Test: subcommand is registered
# ---------------------------------------------------------------------------


def test_gui_command_registers():
    """``milodex gui`` is a valid subcommand in the root parser."""
    parser = build_parser()
    # parse_args raises SystemExit on unknown commands -- if this doesn't
    # raise, the subcommand is registered.
    args = parser.parse_args(["gui"])
    assert args.command == "gui"


# ---------------------------------------------------------------------------
# Test: help text mentions GUI
# ---------------------------------------------------------------------------


def test_gui_command_help_text():
    """The gui subcommand help text references graphical interface / GUI."""
    parser = build_parser()
    try:
        parser.parse_args(["gui", "--help"])
    except SystemExit:
        pass

    # Capture help via format_help on the subparser action
    subparsers_action = None
    for action in parser._actions:
        if hasattr(action, "_name_parser_map"):
            subparsers_action = action
            break

    assert subparsers_action is not None, "Could not find subparsers action"
    gui_parser = subparsers_action._name_parser_map.get("gui")
    assert gui_parser is not None, "gui subparser not found"

    help_text = gui_parser.format_help().lower()
    assert "gui" in help_text or "graphical" in help_text, (
        f"Expected 'GUI' or 'graphical' in help text, got: {help_text!r}"
    )


# ---------------------------------------------------------------------------
# Test: handler calls run_app and propagates exit code
# ---------------------------------------------------------------------------


def test_gui_handler_calls_run_app():
    """gui.run() calls run_app and returns its exit code.

    run_app is imported inside gui.run() so we patch it at its definition
    site (milodex.gui.app.run_app).
    """
    from milodex.cli.commands import gui as gui_cmd

    mock_ctx = MagicMock()
    mock_args = MagicMock()
    mock_args.command = "gui"

    with patch("milodex.gui.app.run_app", return_value=42) as mock_run_app:
        result = gui_cmd.run(mock_args, mock_ctx)

    mock_run_app.assert_called_once()
    assert result == 42


def test_gui_handler_returns_1_when_import_fails():
    """gui.run() returns 1 and logs an error when milodex.gui.app is unavailable."""
    from milodex.cli.commands import gui as gui_cmd

    mock_ctx = MagicMock()
    mock_args = MagicMock()
    mock_args.command = "gui"

    # Patch the import inside run() by raising ImportError for milodex.gui.app
    import builtins

    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "milodex.gui.app":
            raise ImportError("No module named 'PySide6'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        result = gui_cmd.run(mock_args, mock_ctx)

    assert result == 1
