# Engineering Standards

Companion to `docs/SRS.md` Cross-Cutting Requirements. This document defines the engineering discipline Milodex holds itself to: **authoritative vs orchestration modules**, the **service layer**, the **execution vs risk boundary**, **what state goes where**, **versioning of configs/schemas/migrations**, **backwards-compatibility policy**, **highest-risk code paths**, **mandatory tests before "done"**, **backtest reproducibility**, **how docs stay current**, **normative vs descriptive documents**, the **ADR threshold**, and the **scaffolded vs implemented distinction**.

The founder's intent (see `docs/FOUNDER_INTENT.md`) is that Milodex be credibly real, not a polished shell. Every rule below trades short-term convenience for long-term trust.

---

## Authoritative vs Orchestration Modules

Business logic — the rules that define *what Milodex means and how it behaves* — lives in **authoritative domain modules**:

- strategy evaluation (`src/milodex/strategies/`)
- promotion policy (lifecycle, governance — backed by `docs/PROMOTION_GOVERNANCE.md`)
- risk policy (`src/milodex/risk/`; `execution/risk.py` is a thin compatibility shim)
- portfolio policy (sizing, exposure — backed by `docs/RISK_POLICY.md`)
- execution eligibility
- audit / governance rules

**Thin orchestration modules** coordinate; they do not invent policy:

- CLI command handlers (`src/milodex/cli/`)
- scheduled workflow runners
- broker / data adapters (`src/milodex/broker/`, `src/milodex/data/`)
- reporting and export wiring (`src/milodex/analytics/`)
- startup / shutdown coordination

**The rule:** domain modules decide; orchestration modules coordinate. Orchestration must not quietly invent policy. If an adapter or CLI handler contains logic that changes *what is allowed*, that logic belongs in a domain module.

---

## Application / Service Layer

A dedicated **application/service layer** sits between the CLI (and any future GUI) and the domain modules for every meaningful workflow. The CLI must not reach directly into raw business logic or infrastructure in an ad hoc way.

The service layer coordinates use cases including:

- preview
- submit
- reconcile
- promote / demote
- report generation
- incident handling

Benefits: the system is easier to test, easier to evolve into a GUI later, and consistent across interfaces. A future GUI should consume the same service-layer calls the CLI does — not re-implement the logic.

---

## `execution/` vs `risk/` Boundary

The risk evaluator (`RiskEvaluator` and `EvaluationContext`) lives in `src/milodex/risk/evaluator.py`. The `src/milodex/execution/risk.py` module is a thin backwards-compatibility re-export and is not the home of risk logic. The logical boundary:

**`risk/` — "Is Milodex allowed to proceed at all?"**

- exposure checks
- kill-switch logic
- drawdown and daily loss checks
- sector / concentration caps
- hard-stop vs warning classification (per `docs/RISK_POLICY.md`)
- strategy / account disablement policy

**`execution/` — "How does this happen safely if allowed?"**

- order intent construction
- duplicate-order protection
- broker request shaping
- submission workflow
- execution-state tracking
- reconciliation hooks tied to order lifecycle

The distinction: **risk decides whether; execution decides how-safely.** Any code that answers "whether" must be reviewable as a risk rule, not buried inside a submission routine.

---

## Strategy Position Provenance

Strategies read their own open positions from the trade ledger, filtered by originating `strategy_name` — **not** from `BrokerClient.get_positions()`. The paper account is shared across every Milodex strategy, so the broker's position list is account-wide and cannot answer "which strategy opened this." The `trades` table can, because every row carries the `strategy_name` that produced it.

Concretely: `StrategyContext.positions` is populated by `compute_ledger_positions(event_store, strategy_id)` (see `src/milodex/strategies/positions.py`), which nets signed quantities over submitted paper trades for that strategy. The broker is consulted only for `avg_entry_price` on symbols the ledger already attributes to the strategy — authoritative for actual fill price, not for ownership.

See ADR 0021 for the incident that forced the rule and the full rationale. The invariant: **a strategy's world is what its own ledger says it is.** Any code path that hands a strategy positions belonging to another strategy (or to the operator) is a bug, regardless of whether the risk layer happens to catch it downstream.

---

## State: SQLite vs Files vs Never-Persisted

### SQLite holds durable operational state

- strategy instances and config fingerprints (per R-STR-011)
- lifecycle stage and promotion records (per R-ANA-001a, R-PRM-007)
- approvals and governance events
- orders, fills, positions, reconciliation history
- incidents, kill-switch events, audit logs
- experiment registry entries (per R-PRM-011)
- references to reports and evidence artifacts

### Files hold larger or more portable artifacts

- frozen config files and manifests (`configs/*.yaml`)
- report exports (CSV, JSON, markdown under `reports/` or similar)
- backtest outputs
- markdown review artifacts
- optional cached datasets where appropriate
- ADRs and documentation (`docs/`)

### Never persisted (unless explicitly needed and secured)

- raw secrets or API keys in logs or exports
- ephemeral in-memory intermediate calculations with no audit value
- unredacted sensitive provider payloads (unless required as incident evidence, and then secured)
- duplicate transient runtime state that creates confusion rather than clarity

**The principle:** persist what is needed for truth, replay, audit, and user trust — not every transient detail.

---

## Versioning: Configs, Schemas, Migrations

Configs, schemas, and state migrations are versioned **explicitly and independently**. No silent upgrades.

- **Configs** carry a `version` field and an immutable fingerprint (per R-STR-011). The fingerprint is the primary key for promotion logs, explanation records, and CLI surfaces (per R-STR-012).
- **Schemas** (YAML config schemas, SQLite table schemas, JSON output schemas) carry named versions with clear compatibility expectations. Schema changes that affect consumers require an ADR.
- **Database / state migrations** are sequential, named, and applied through an explicit migration system. Migrations are never applied implicitly at import time.
- Every run and every promotion artifact records which config version, schema version, and migration state it depended on.

If a config or state format changes materially, Milodex either migrates it explicitly or **rejects it clearly** — never silently upgrades.

---

## Backwards-Compatibility Policy

Phase 1 aims for **limited, intentional backwards compatibility, not indefinite compatibility**.

- Minor additive config changes may remain backwards compatible when safe.
- Breaking semantic changes require a new config version or an explicit migration path.
- Milodex prefers being explicit and clean over carrying hidden legacy behavior.
- If an old config is no longer valid, the system fails clearly and explains how it must be updated.

This policy lets the system evolve without accumulating legacy debt that erodes trust.

---

## Highest-Risk Code Paths

These paths must receive the strongest test coverage first. They are where Milodex most easily loses trust if it behaves incorrectly:

- exposure-increasing submit logic (R-EXE-002, R-EXE-016)
- duplicate-order prevention (R-EXE-009)
- kill-switch activation and enforcement (R-EXE-005, R-EXE-010, R-EXE-014, R-EXE-015)
- local-vs-broker state reconciliation (R-OPS-004)
- strategy promotion and demotion gates (R-PRM-004, R-PRM-007–R-PRM-011)
- config fingerprinting and drift prevention (R-STR-011, R-STR-013)
- risk hard-stop enforcement (R-EXE-017)
- startup safety checks (R-OPS-002)
- data freshness and stale-data blocking (R-OPS-007)
- trade reasoning persistence and audit record generation (R-XC-008, R-ANA-007, R-OPS-011)

Test coverage ratio (per R-XC-004) already requires `risk/evaluator.py` to exceed project-average coverage by 10 points. The list above extends that spirit to the full set of trust-critical paths.

---

## Mandatory Tests Before "Done"

A feature is not done because it worked once on the happy path. Before calling a feature done, Milodex requires, as applicable:

- **unit tests** for the core business rules
- **integration tests** for interactions between services, persistence, and adapters
- **workflow tests** for end-to-end use cases (preview, submit, reconcile, promote)
- **failure-path tests** for degraded conditions, stale data, broker outages, and kill-switch activation
- **idempotency tests** for commands that must be safe when repeated (per R-OPS-010)
- **regression tests** for any bug that previously occurred

A PR that adds a feature without adding the applicable test categories is not complete.

---

## Backtest Reproducibility

Milodex aims for **strong local reproducibility**. Given the same frozen config, same input data snapshot, same code version, and same assumptions, a local rerun must reproduce **materially the same** results. Small formatting differences may be acceptable; core metrics, trades, and conclusions must not drift.

Reproducibility protects both the founder and a future user: an experiment can be rerun later and trusted that the system is not inventing inconsistent history. The config fingerprint (R-STR-011) and the frozen-instance manifest requirement (ADR 0015) are the mechanisms that make this enforceable — not an aspiration.

---

## Keeping Docs From Drifting Behind Implementation

Documentation updates are **part of feature completion**, not an optional cleanup step. At minimum:

- Normative docs are updated when behavior or policy changes.
- New ADR-worthy decisions are recorded when architecture or governance shifts (see "ADR Threshold" below).
- Implemented behavior is checked against the relevant doc during review.
- Scaffolded-only work is labeled honestly in both docs and code (see "Scaffolded vs Implemented" below).
- Major commands and workflows have a clear authoritative doc home.

Milodex prefers **honest current docs over polished outdated docs**. An outdated polished doc is worse than a plain accurate one, because it actively misleads.

---

## Normative vs Descriptive Documents

### Normative — define how the system must behave

- vision / product-intent documents (`docs/VISION.md`, `docs/FOUNDER_INTENT.md`)
- architecture decisions recorded in ADRs (`docs/adr/`)
- strategy lifecycle and promotion policy (`docs/PROMOTION_GOVERNANCE.md`)
- risk policy (`docs/RISK_POLICY.md`)
- runtime / operations policy (`docs/OPERATIONS.md`)
- reporting and CLI UX policy (`docs/REPORTING.md`, `docs/CLI_UX.md`)
- SRS / requirements (`docs/SRS.md`)
- this engineering standards document

### Descriptive — unless explicitly marked otherwise

- brainstorm notes
- exploratory design notes
- progress updates
- implementation sketches
- old planning documents
- personal reflections that do not define behavior

**If normative and descriptive docs conflict, the normative doc wins** until intentionally revised. A PR that changes system behavior must also update the normative doc; updating only a descriptive note is not enough.

---

## ADR Threshold

A decision deserves an **Architecture Decision Record** (a new file under `docs/adr/`) when it changes:

- long-term architecture
- system boundaries
- authority model
- lifecycle semantics
- risk model
- data model
- operator contract

In short: **use an ADR for decisions future work will need to treat as precedent.**

A plain markdown note (or a section in an existing doc) is enough for:

- local exploration
- temporary thinking
- minor implementation observations
- ideas not yet adopted
- descriptive summaries that do not define system behavior

If the decision changes how the system is supposed to behave or be reasoned about, it probably deserves an ADR.

---

## Scaffolded vs Implemented

Milodex is conservative in calling things "implemented." **"Looks present" is not the same as "can be trusted."**

A feature is **scaffolded** when its structure, interface, or placeholder flow exists but any of the following are incomplete:

- real business behavior
- safety checks
- persistence and audit records
- test coverage

A feature is **implemented** only when **all** of the following are true:

- the intended behavior actually works
- core failure paths are handled
- required persistence and audit records exist
- tests exist at the appropriate level (per "Mandatory Tests" above)
- docs are updated
- the feature can be honestly relied on within its claimed scope

Scaffolded features must be labeled as such — in code (e.g., `# scaffolded — see docs/X.md`), in commit messages, in the relevant doc, and in any CLI help string. Claiming implemented-ness prematurely is itself a trust violation, not merely an estimation error.

---

## GUI Readiness Gate (Phase 2+)

The desktop GUI (Phase 2+ per the SRS appendix) must not be started until **all** of the following are true:

- the core CLI workflow is stable and understandable
- preview, submit, reconcile, and reporting flows are implemented (per above) and trustworthy
- risk checks and kill switches behave correctly in integration tests and in live paper use
- durable state and audit logging are in place (SQLite event store, explanation records, governance log)
- config and promotion workflows are frozen enough to present visually without material change
- the JSON / service-layer contracts are stable enough for another interface to rely on (per R-CLI-017)
- the founder can already stand behind the system as a real working product **even without the GUI**

**The GUI is not a tool to compensate for unclear core behavior.** It is a view layer built on top of a system that already deserves trust. Starting the GUI early to make the product feel "real" is the exact anti-pattern `docs/FOUNDER_INTENT.md` warns against — polish as a substitute for truth.

---

## Relationship to SRS

- `R-XC-002` through `R-XC-008` already encode code-style, secrets handling, state directories, timestamps, and explanation records.
- New requirements `R-XC-011` through `R-XC-016` (below) encode the authoritative/orchestration split, service layer, versioning policy, mandatory test categories, and scaffolded-vs-implemented labeling.
