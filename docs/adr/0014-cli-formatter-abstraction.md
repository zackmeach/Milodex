# ADR 0014 — CLI Formatter Abstraction for Dual Human / JSON Output

**Status:** Accepted
**Date:** 2026-04-16

## Context

The CLI is the primary operator surface today, and a GUI or Web UI is contemplated for Phase 2+. A future GUI will want to drive the same commands as the CLI and consume their output programmatically. Tests, scripts, and any scheduled automation on top of Milodex (e.g., a daily "email me the analytics report" cron) also benefit from machine-readable output.

Retrofitting a `--json` flag across N commands later is tedious and often inconsistent — each command accretes its own format logic, structured output ends up close-but-not-identical to the human format, and consumers have to guess fields. Getting this right at the start costs very little; getting it wrong later is a protracted cleanup.

## Decision

Every CLI command emits its result through a **formatter abstraction**: the command produces a single structured result object (a plain Python dict or dataclass), and a pluggable formatter renders that object as either human-readable text or JSON. Adding a `--json` flag to a command is a zero-line change in the command itself; the dispatcher reads the flag and picks the formatter.

Commands MUST NOT call `print()` directly for result output. Log lines (INFO/DEBUG/WARN) go to the log stream independently of the formatter and are not affected by `--json`.

The human and JSON formatters are implemented as two concrete classes behind a common `Formatter` interface. Either can be removed in isolation without changing command code.

## Rationale

- **Zero-cost machine readability at day one.** Because the structured-result-then-format pattern is the only way commands produce output, `--json` support is a consequence of the architecture rather than a per-command chore. R-CLI-009 is satisfied by construction.
- **Output consistency across formats.** Human and JSON output are rendered from the same source object, so they cannot drift: if a field shows up in one, it shows up in the other. Consumers writing against the JSON output don't need to reverse-engineer the human output.
- **GUI-ready.** A future GUI can shell out to `milodex X --json` and parse results directly, no scraping required. The CLI becomes the GUI's back-end API with zero extra work.
- **Testable.** Command tests assert against the structured result object, not against rendered text. Formatter tests independently verify rendering. Neither test touches the other's concern, and both are small.
- **Reversible if unused.** The user's stated constraint is that this must be removable if JSON support never gets adopted. Because commands don't depend on the JSON formatter specifically — only on the abstract `Formatter` interface — removing JSON is deleting one class and its tests. Commands keep working. This is exactly the modularity the requirement calls for.
- **Logs stay separate.** Log lines (DEBUG/INFO/WARN from the Python logger) are unaffected by formatter selection. The structured-output concept applies only to a command's *result* — the thing the operator or a script is looking at, not the running commentary. This preserves R-CLI-011 (verbosity flags) as an orthogonal concern.
