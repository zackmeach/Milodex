# ADR 0007 — `argparse` for the CLI

**Status:** Accepted
**Date:** 2026-04-16

## Context

Milodex's primary interaction surface is a CLI (`milodex status`, `milodex trade submit`, etc.). Python offers multiple CLI frameworks: the stdlib `argparse`, and third-party options like `click` and `typer`. The CLI is expected to grow — nested subcommands (`trade submit`, `trade preview`, `trade kill-switch status`, `data bars`, `config validate`) are already in place.

## Decision

The CLI is built on the standard library's `argparse` module, using subparsers for command hierarchies. No third-party CLI framework is added.

## Rationale

- **Zero new runtime dependencies.** The project's code-style rules explicitly discourage adding dependencies without justification. `argparse` ships with Python; `click`/`typer` would each be a transitive-dependency expansion for ergonomic sugar.
- **CLI complexity is bounded.** The operator is the developer — a technical user — and the command surface fits a documented subparser tree. The things `click`/`typer` excel at (rich help rendering, decorator-based type coercion, ergonomic color output) are not required for a developer-facing local tool.
- **Debuggability.** `argparse` behavior is explicit and trivially traceable. Decorator-driven CLI frameworks introduce indirection that's faster to write and slower to debug when something unexpected happens.
- **Stable API.** `argparse` has been in the standard library since Python 3.2 and is not going anywhere. CLI frameworks, by contrast, occasionally have ecosystem churn (e.g., Click 7 → 8 breaking changes).
- **If this decision needs to be revisited,** it will be because command ergonomics — not technical capability — are the bottleneck. That would be a good problem to have, and the swap cost is finite (the command graph is centralized).
