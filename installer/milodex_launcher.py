"""Frozen-bundle entry point for the Milodex desktop application.

PyInstaller targets this script as the Analysis entry point rather than
cli/main.py directly.  Installed users running Milodex.exe land in the
GUI by default.  Power users can still pass CLI sub-commands and flags:

    Milodex.exe backtest run breakout.daily.atr_channel.sector_etfs.v1
    Milodex.exe --help

The launcher forwards sys.argv[1:] unchanged when arguments are present;
it injects ["gui"] when none are given so the default launched surface is
the GUI, not a bare CLI prompt.
"""

from __future__ import annotations

import sys


def main() -> int:
    from milodex.cli.main import main as _cli_main

    # sys.argv[0] is the executable path; the real args start at index 1.
    args = sys.argv[1:] if len(sys.argv) > 1 else ["gui"]
    return _cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
