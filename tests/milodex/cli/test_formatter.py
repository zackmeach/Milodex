"""Tests for the CLI formatter — JSON contract + rich rendering toggles.

The rich layer is a *display* concern only and must:
- Stay completely off when ``--json`` is requested.
- Stay completely off when stdout is not a TTY (pipes / tests / redirects).
- Activate when stdout is a TTY and ``CommandResult.renderable`` is set.
- Never affect ``CommandResult.data`` (the JSON contract).
"""

from __future__ import annotations

import io
import json

from rich.panel import Panel
from rich.text import Text

from milodex.cli.formatter import (
    CommandResult,
    HumanFormatter,
    JsonFormatter,
    get_formatter,
)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:  # pragma: no cover — overridden per-instance
        return True


class _FakePipe(io.StringIO):
    def isatty(self) -> bool:  # pragma: no cover — overridden per-instance
        return False


def _result_with_renderable() -> CommandResult:
    return CommandResult(
        command="status",
        data={"foo": 1},
        human_lines=["Plain status line"],
        renderable=Panel(Text("RICHLY-RENDERED-MARKER")),
    )


def test_human_formatter_falls_back_to_plain_text_when_stdout_is_not_tty():
    formatter = HumanFormatter(stdout=_FakePipe())
    out = formatter.render(_result_with_renderable())
    assert out == "Plain status line"
    # No ANSI / no box drawing — pipes get plain text.
    assert "RICHLY-RENDERED-MARKER" not in out


def test_human_formatter_uses_rich_when_stdout_is_tty():
    formatter = HumanFormatter(stdout=_FakeTTY())
    out = formatter.render(_result_with_renderable())
    # The renderable's text content must appear in the output.
    assert "RICHLY-RENDERED-MARKER" in out
    # A Panel emits at least one box-drawing character on TTY.
    assert any(ch in out for ch in "─│╭╮╰╯┌┐└┘"), (
        f"Expected box-drawing characters in TTY output, got: {out!r}"
    )


def test_human_formatter_with_no_renderable_uses_human_lines_either_way():
    result = CommandResult(
        command="x",
        data={},
        human_lines=["only line"],
    )
    assert HumanFormatter(stdout=_FakePipe()).render(result) == "only line"
    assert HumanFormatter(stdout=_FakeTTY()).render(result) == "only line"


def test_json_formatter_ignores_renderable_completely():
    formatter = JsonFormatter()
    out = formatter.render(_result_with_renderable())
    payload = json.loads(out)
    # The renderable must NOT round-trip into the JSON contract.
    assert "renderable" not in payload
    assert "RICHLY-RENDERED-MARKER" not in out
    # human_lines still populates the summary field — that's the R-CLI-009 contract.
    assert payload["summary"] == ["Plain status line"]
    assert payload["data"] == {"foo": 1}


def test_get_formatter_passes_stdout_through_to_human_formatter():
    fake = _FakeTTY()
    formatter = get_formatter(as_json=False, stdout=fake)
    assert isinstance(formatter, HumanFormatter)
    # Use the formatter to confirm the stdout reference was retained — a
    # renderable that requires TTY mode should produce rich output.
    out = formatter.render(_result_with_renderable())
    assert "RICHLY-RENDERED-MARKER" in out


def test_get_formatter_returns_json_formatter_when_as_json():
    formatter = get_formatter(as_json=True, stdout=_FakeTTY())
    assert isinstance(formatter, JsonFormatter)


def test_command_result_to_json_payload_omits_renderable_field():
    """JSON schema contract — `renderable` is a display field, never serialized."""
    payload = _result_with_renderable().to_json_payload()
    assert "renderable" not in payload
    # The expected R-CLI-009 fields are all present.
    assert {
        "schema_version",
        "command",
        "timestamp",
        "status",
        "data",
        "warnings",
        "errors",
        "summary",
    } <= set(payload.keys())
