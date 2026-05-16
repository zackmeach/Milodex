"""Stage-compatibility table: which promotion stages may run under each trading mode.

The table lives here (in the promotion layer) so that both the CLI strategy
command and the Bench command facade can import it from the same authoritative
location without either reaching across the CLI/facade boundary.

ADR 0051 §6 requires the facade to route through the same callees as the CLI.
Previously the facade reached into ``milodex.cli.commands.strategy`` to import
the private ``_ALLOWED_STAGES_BY_MODE`` dict — a layering inversion. This
module is the shared, public home for that table.
"""

from __future__ import annotations

# Map of trading mode -> set of strategy stages that are eligible to run.
# Phase 1 supports paper mode only; the dict is extended when live/micro_live
# modes open (requires an ADR amendment per the CLAUDE.md autonomy boundary).
#
# TODO(phase-2): when live mode opens, extend this table AND relax the
# ``trading_mode != "paper"`` guard in cli/commands/strategy.py.
ALLOWED_STAGES_BY_MODE: dict[str, frozenset[str]] = {
    "paper": frozenset({"paper"}),
}

RECOGNIZED_MODES: frozenset[str] = frozenset(ALLOWED_STAGES_BY_MODE)
