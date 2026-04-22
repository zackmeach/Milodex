"""CLI output formatter abstraction (ADR 0014, R-CLI-009).

Every command produces a single :class:`CommandResult`. A :class:`Formatter`
renders that result as either human-readable text or machine-readable JSON.
Commands never call ``print`` for result output directly; they build a
``CommandResult`` and hand it to the dispatcher, which picks the formatter
based on the ``--json`` flag.

The JSON contract is the stable interface documented in
``docs/CLI_UX.md`` "JSON Output Contract" (R-CLI-009). Breaking changes
require an ADR; bump :data:`JSON_SCHEMA_VERSION` for every incompatible
payload change.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JSON_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CommandResult:
    """Structured result of a single CLI command invocation.

    Attributes
    ----------
    command:
        Dotted command name, e.g. ``"status"`` or ``"trade.submit"``.
    status:
        ``"success"`` or ``"error"``.
    data:
        Command-specific structured payload (dict of native Python types).
    human_lines:
        Pre-rendered human-readable output lines for this command. The
        human formatter prints these as-is; JSON formatter emits them as
        a ``summary`` array so operators can correlate the two.
    warnings:
        Non-fatal concerns, as short human-readable strings.
    errors:
        Fatal errors with a ``code`` and ``message``. Empty on success.
    timestamp:
        UTC ISO-8601 timestamp. Auto-populated if omitted.
    """

    command: str
    status: str = "success"
    data: dict[str, Any] = field(default_factory=dict)
    human_lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""

    def to_json_payload(self) -> dict[str, Any]:
        """Return the canonical JSON payload for this result.

        Fields are the subset of the R-CLI-009 contract that apply to every
        command. Command-specific fields live under ``data``.
        """
        timestamp = self.timestamp or datetime.now(tz=UTC).isoformat()
        return {
            "schema_version": JSON_SCHEMA_VERSION,
            "command": self.command,
            "timestamp": timestamp,
            "status": self.status,
            "data": self.data,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "summary": list(self.human_lines),
        }


class Formatter(ABC):
    """Pluggable renderer for :class:`CommandResult`."""

    @abstractmethod
    def render(self, result: CommandResult) -> str:
        """Return the string to write to stdout for ``result``."""


class HumanFormatter(Formatter):
    """Render a :class:`CommandResult` as human-readable text."""

    def render(self, result: CommandResult) -> str:
        if result.status == "error" and not result.human_lines:
            parts = [error.get("message", "Unknown error") for error in result.errors]
            return "\n".join(parts)
        return "\n".join(result.human_lines)


class JsonFormatter(Formatter):
    """Render a :class:`CommandResult` as a single JSON object."""

    def __init__(self, *, indent: int | None = 2) -> None:
        self._indent = indent

    def render(self, result: CommandResult) -> str:
        return json.dumps(result.to_json_payload(), indent=self._indent, default=str)


def get_formatter(*, as_json: bool) -> Formatter:
    """Return the formatter selected by the ``--json`` flag."""
    if as_json:
        return JsonFormatter()
    return HumanFormatter()
