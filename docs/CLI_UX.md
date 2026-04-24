# CLI UX and Operator Workflow

Companion to `docs/SRS.md` Domain 7 (CLI). This document defines the operator-facing CLI experience: the **five most-used commands**, the **ideal daily workflow**, the **output-priority order** (readability → auditability → speed), the **JSON contract** a future GUI or script can rely on, which commands need **preview-before-commit**, which need **confirmation prompts**, how **errors are phrased under stress**, what a good **status** command contains, and the full **decision context that must be visible before any submit**.

The founder's intent (see `docs/FOUNDER_INTENT.md`) is that Milodex feel polished, deliberate, and understandable — something the operator **checks, trusts, previews, reviews, and operates deliberately** rather than a black box turned loose. Every rule below serves that.

---

## The Five Most-Used Commands

These are the CLI surfaces the operator should reach for daily. They shape the product identity as much as the code does.

1. **`status`** — current system health, strategy state, warnings, and next expected actions.
2. **`preview`** — what Milodex would do before it commits.
3. **`report`** — the most recent strategy, portfolio, or trust report.
4. **`reconcile`** — compare and sync local state with broker state.
5. **Daily workflow command** (e.g., `run daily` or equivalent) — execute the normal daily operating cycle.

These five must be first-class, discoverable, documented, and reliable. If any of them feels awkward, clever, or brittle, the product feels wrong regardless of how good the internals are.

---

## Ideal Daily Operator Workflow

From opening the terminal to closing it:

1. Open terminal and run `status`.
2. Confirm system health, broker/data connectivity, and whether any incidents or halted states exist.
3. Review a concise summary of active strategies, open positions, and expected next actions.
4. Run `reconcile` if needed to confirm broker and local state match.
5. Run `preview` for the day's eligible actions or strategy decisions.
6. Review the reasoning, risk checks, and any warnings.
7. If appropriate, approve and run the next workflow step or submit-capable action.
8. Review the resulting state, fills, or incidents.
9. Generate or inspect a summary report.
10. Close terminal knowing the system has either completed safely or is clearly halted and reviewable.

The workflow must feel smooth, deliberate, and understandable — not like spelunking through raw logs. If any step routinely requires the operator to grep a logfile or open the database, that step has failed its UX purpose.

---

## Output Priorities

Default human-readable output optimizes in this fixed order:

1. **Readability** — a human can quickly understand what is happening, why, and whether anything needs attention.
2. **Auditability** — the output is precise enough that later review can reconstruct what happened.
3. **Speed** — fast enough to use comfortably.

Milodex must be fast enough to be pleasant, but not at the expense of clarity. Output must feel clear under normal conditions and **especially clear under stress** (kill-switch trip, reconciliation mismatch, broker outage). Under stress, condensed or stylized layouts give way to direct, explicit ones.

---

## JSON Output Contract

JSON output (`--json` on every command per R-CLI-009) is not a dump of terminal text. It is a **stable contract** for future GUIs and automations. Every JSON payload must include, at minimum:

- command name
- timestamp (UTC, ISO-8601 per R-XC-007)
- success-or-failure status
- machine-readable error code (when applicable, per R-CLI-010)
- strategy instance IDs and config fingerprints (where relevant)
- stage or lifecycle state
- data freshness state
- broker connectivity state
- warnings and blockers as structured arrays (not prose)
- decision summaries
- audit record references or IDs
- human-readable summary text (in addition to structured fields)

The schema is versioned. Breaking changes to the JSON contract require an ADR — GUIs, scripts, and external reviewers depend on it.

---

## Preview-Before-Commit

Any command that can change exposure, strategy state, or governance state supports **preview-before-commit**. A preview shows what Milodex intends to do, what checks were applied, what assumptions it is using, and what will happen on commit.

Commands that must support preview:

- trade submission commands
- promotion and demotion commands
- strategy enable / disable commands
- re-enable-after-kill-switch commands
- configuration changes that affect active strategy behavior
- capital or sizing policy changes
- any batch action that could affect multiple strategies or orders

A commit path that cannot be previewed is a design bug. Preview output is itself an audit record (per `docs/OPERATIONS.md` "Preview audit record").

---

## Confirmation Prompts vs Non-Interactive

Read-only and diagnostic commands stay smooth and scriptable. Consequential commands require deliberate acknowledgement.

### Require a confirmation prompt

- any submit-capable exposure-increasing action
- promotion to a higher lifecycle stage
- re-enabling after a kill switch or major incident
- disabling or retiring an active strategy
- changing live or paper risk policy
- destructive actions (clearing state, deleting artifacts, forcing overrides)

### Stay non-interactive (scriptable, no prompt)

- `status`
- report generation
- health checks
- reconciliation checks
- backtests
- read-only previews
- export commands
- audit inspection

Confirmation prompts must be bypassable only by an explicit `--yes` flag, never by environment default. `--yes` itself is an auditable fact and appears in the command's explanation record.

---

## Error Phrasing Under Stress

Every important error answers four questions, in plain language:

1. **What failed?**
2. **Why did it fail?**
3. **What did Milodex do in response?** (blocked, halted, degraded, rolled back, etc.)
4. **What should the operator do next?**

Errors avoid vague wording ("something went wrong") and avoid overly technical language unless the technical detail is actually useful. Under stress the operator must be able to tell quickly whether the system is **safe, halted, degraded, or awaiting review**.

The per-check pass/fail table from R-CLI-007 is the concrete embodiment of this rule for risk-rejected trades; the same spirit applies to every error surface.

---

## What a Good `status` Command Looks Like

`status` is the home view of the system. It is compact and readable, and it shows:

- overall system state: **healthy, degraded, halted, or review required**
- broker connectivity state
- data freshness state
- current kill-switch state (with scope — strategy-level or account-level, per R-EXE-014)
- active strategies and their lifecycle stage
- whether any trades are pending, blocked, or recently filled
- any unresolved incidents or warnings
- the next expected system action
- whether operator attention is needed right now

The guiding test for `status`: **the operator answers, in under a minute: "is Milodex okay, what is it doing, and do I need to act?"** If it takes longer, the command has failed.

---

## Decision Context Required Before Any Submit

A submit must never feel like pressing a mysterious button. Before any submit-capable command is allowed to proceed, the operator must see the full decision context:

- strategy instance name and config fingerprint
- symbol, side, and intended quantity or target exposure
- whether the order increases or reduces exposure (per R-EXE-016)
- the rule or signal that triggered the action
- key signal values
- relevant risk checks and whether they passed
- current data freshness state
- current broker connectivity and reconciliation state
- duplicate-order check result (per R-EXE-009)
- any warnings, blockers, or degraded conditions
- whether human approval is required and whether it has been granted

If any of these cannot be displayed (e.g., reconciliation state is unknown), the submit is refused until it can. Absence of context is itself a hard stop — consistent with the "block on uncertainty" rule in `docs/RISK_POLICY.md`.

---

## `research screen` — batch walk-forward evaluator

Not one of the five daily commands. `research screen` is a **research-time** tool for working through the strategy bank: point it at a glob of configs and it runs the same walk-forward harness the single-strategy `backtest --walk-forward` uses — but across every matching config in one invocation, with a ranked comparison table at the end.

**Purpose.** Compare candidates side-by-side. The operator's daily workflow doesn't use this; a weekend research session does. It surfaces which configs clear the promotion gate on OOS evidence and which lean on a single lucky window so the operator can decide what to promote next.

**Explicit scope.** Evaluation only. `research screen` never freezes a manifest, never promotes, never advances a stage. The `gate` column is advisory — the actual promotion is still a separate operator decision via `milodex promotion promote`. This keeps the governance line clean: screening is cheap and repeatable; promotion is deliberate and logged.

**Invocation.**

```
milodex research screen --configs "meanrev_*.yaml" --start 2022-01-01 --end 2024-12-31
milodex research screen --strategy-id meanrev.daily.rsi2pullback.v1 \
                        --strategy-id regime.daily.sma200_rotation.spy_shy.v1 \
                        --start 2022-01-01 --end 2024-12-31
milodex research screen --configs "*.yaml" --start ... --end ... --report-out
```

`--configs` and `--strategy-id` are mutually exclusive. `--report-out` with no value writes to `docs/reviews/screen_<today>.md` (plus a JSON sibling at the same stem); pass an explicit path to override. `--fail-fast` aborts on the first error instead of recording it as an `error` row — default is to continue so one malformed config does not mask results for the other candidates.

**Output.** Human-readable ranking table sorted by (gate_allowed desc, oos_sharpe desc). Columns: `strategy_id | family | trades | oos_sharpe | oos_max_dd | fragile | gate`. `fragile=yes` means dropping the best-returning window flips aggregate return negative (ADR 0021 single-window-dependency flag). `gate=pass (statistical)` / `pass (lifecycle_exempt)` / `block` / `error`.

**JSON contract.** `research.screen` payload: `start_date`, `end_date`, `row_count`, `rows[]` (each row includes the metrics and gate failure list), `report_path`. Stable across invocations so downstream tooling can ingest screening snapshots.

---

## Relationship to SRS

- `R-CLI-001` through `R-CLI-011` define the existing CLI surface (status, preview, submit, JSON parity, exit codes, verbosity).
- `R-CLI-012` through `R-CLI-014` (added in the Reporting pass) define the primary trust report, the CLI-vs-export split, and explicit uncertainty labeling.
- `R-CLI-015` through `R-CLI-021` (new in this pass) encode the five-most-used commands, the output-priority order, the JSON contract, the preview-before-commit surface, the confirmation-prompt policy, the error-phrasing contract, and the submit decision-context gate.
