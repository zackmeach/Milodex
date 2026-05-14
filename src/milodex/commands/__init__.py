"""Backend command facades that GUI surfaces (and future tooling) reach.

The modules in this package own no business rules of their own. Each facade is
a thin orchestrator over existing CLI / governance / runtime callees. See
``docs/adr/0051-bench-command-infrastructure-v1.md`` for the contract.
"""

from milodex.commands.bench import (
    ACTION_FAMILIES,
    BenchCommandFacade,
    Blocker,
    CommandProposal,
    CommandResult,
    Precondition,
)

__all__ = [
    "ACTION_FAMILIES",
    "BenchCommandFacade",
    "Blocker",
    "CommandProposal",
    "CommandResult",
    "Precondition",
]
