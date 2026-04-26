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

The human formatter has two modes:

- **Plain text** (default, also used in tests / pipes / non-TTY stdout):
  ``human_lines`` are joined with ``\\n``.
- **Rich** (when stdout is a TTY and a ``CommandResult.renderable`` is
  set): the renderable (``rich.panel.Panel``, ``rich.table.Table``, etc.)
  is printed via ``rich.console.Console``. ``human_lines`` remain as the
  fallback and continue to populate the JSON ``summary`` field, so the
  display layer is a strict superset and machine consumers see no change.
"""

from __future__ import annotations

import io
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TextIO

from rich.console import Console

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
        human formatter prints these when no ``renderable`` is set, and
        always uses them as the JSON ``summary`` field. Keep them
        substring-searchable — tests assert against this content.
    renderable:
        Optional ``rich`` renderable (``Panel``, ``Table``, ``Group``, …)
        used by the human formatter when stdout is a TTY. Defaults to
        ``None`` for backwards compatibility — commands opt in surface by
        surface. Never affects JSON output.
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
    renderable: Any = None
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""

    def to_json_payload(self) -> dict[str, Any]:
        """Return the canonical JSON payload for this result.

        Fields are the subset of the R-CLI-009 contract that apply to every
        command. Command-specific fields live under ``data``. The
        ``renderable`` field is intentionally **not** included — it's a
        display-layer concern only.
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
    """Render a :class:`CommandResult` as human-readable text.

    When ``stdout`` is a TTY and ``result.renderable`` is set, the rich
    layer kicks in: the renderable is printed through a ``rich.Console``
    that emits ANSI escapes for colors / box drawing. When ``stdout`` is
    a non-TTY (tests, pipes, redirects) the formatter ignores the
    renderable and falls back to ``human_lines`` joined with ``\\n``.

    This keeps:
    - tests and pipe consumers seeing plain text exactly as before,
    - terminal users seeing the upgraded display,
    - the JSON path completely untouched.
    """

    def __init__(self, *, stdout: TextIO | None = None) -> None:
        self._stdout = stdout

    def render(self, result: CommandResult) -> str:
        if result.status == "error" and not result.human_lines:
            parts = [error.get("message", "Unknown error") for error in result.errors]
            return "\n".join(parts)

        if result.renderable is not None and self._is_tty():
            buffer = io.StringIO()
            console = Console(file=buffer, force_terminal=True, soft_wrap=False)
            console.print(result.renderable)
            return buffer.getvalue().rstrip("\n")
        return "\n".join(result.human_lines)

    def _is_tty(self) -> bool:
        if self._stdout is None:
            return False
        is_tty = getattr(self._stdout, "isatty", None)
        if is_tty is None:
            return False
        try:
            return bool(is_tty())
        except (ValueError, OSError):
            return False


class JsonFormatter(Formatter):
    """Render a :class:`CommandResult` as a single JSON object."""

    def __init__(self, *, indent: int | None = 2) -> None:
        self._indent = indent

    def render(self, result: CommandResult) -> str:
        return json.dumps(result.to_json_payload(), indent=self._indent, default=str)


def get_formatter(*, as_json: bool, stdout: TextIO | None = None) -> Formatter:
    """Return the formatter selected by the ``--json`` flag.

    The optional ``stdout`` is forwarded to :class:`HumanFormatter` for
    TTY detection (which gates rich rendering). Pass ``sys.stdout`` in
    production; tests typically pass a ``StringIO`` and get plain text.
    """
    if as_json:
        return JsonFormatter()
    return HumanFormatter(stdout=stdout)
