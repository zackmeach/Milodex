# Reporting, Analytics, and Uncertainty

Companion to `docs/SRS.md` Domain 6 (Analytics & Reporting) and Domain 7 (CLI). This document defines the **primary trust report** the operator opens most often, the **minimum analytics set** that supports trust-or-distrust judgment, **mandatory trade-level reasoning fields**, **daily and weekly summary contents**, the rule for **paper/live divergences worth surfacing**, **attribution slices**, **essential vs nice-to-have charts**, **Phase 1 export formats**, the **CLI-direct vs export split**, and — most importantly — **how uncertainty is presented**.

The founder's intent (see `docs/FOUNDER_INTENT.md`) is that Milodex feel alive, understandable, and under control — and that its trust come from **honesty, not from certainty theater**. The rules below enforce that on every surface that communicates with the operator.

---

## The Primary Trust Report

The single most important report is a **current strategy status and trust report**. It must answer, in one view:

- which strategies are active, halted, in paper, or under review
- what each strategy is doing right now or expects to do next
- whether anything has drifted from expectation
- whether any warnings, incidents, or trust issues exist
- whether the operator can leave the system alone or needs to intervene

This is the report that makes Milodex feel alive, understandable, and under control. It is the default CLI surface for "how is my system?" — not a metric dashboard, not a trade log, not a chart gallery. Everything else is secondary.

---

## Minimum Analytics Set for Trust / Distrust

A strategy should not be trusted because one number looks good. Trust is justified only when behavior, evidence quality, and risk profile make sense **together**. The minimum set required to form that judgment:

- total return
- maximum drawdown
- trade count
- out-of-sample performance
- benchmark-relative performance (vs SPY per R-ANA-003)
- win/loss structure
- profit factor
- average holding period
- exposure level
- slippage assumptions used
- paper-vs-backtest behavior comparison
- explanation of what the strategy is trying to exploit
- known weaknesses or fragile conditions

Any surface presenting strategy-level analytics must include all of these at parity — not a cherry-picked subset.

---

## Mandatory Trade-Level Reasoning Fields

Every trade (preview or submit) must persist a reasoning record that is reconstructable later as both a machine event and a human-understandable decision. Required fields:

- strategy instance ID (per R-STR-012)
- config fingerprint (per R-STR-011)
- symbol
- timestamp of decision
- signal values that triggered the decision
- entry or exit rule satisfied
- ranking result (if applicable)
- risk checks evaluated (per-check pass/fail)
- blockers or warnings present at decision time
- data freshness state
- broker connectivity state
- whether the action increased or reduced exposure (per R-EXE-016)
- expected action
- submitted action
- final broker outcome
- approval reference (if the action was gated)
- human-readable explanation of why the trade was proposed or blocked

This is a stricter, trade-scoped version of the general explanation-record requirement in R-XC-008. The preview and submit audit-record field lists in `docs/OPERATIONS.md` are the same data viewed from the runtime-event angle.

---

## Daily and Weekly Summaries

Two distinct cadences serve two distinct purposes: **daily = operational awareness; weekly = judgment and reflection.**

### Daily summary — required contents

- portfolio status
- active strategies and their current stage
- positions opened, closed, or changed
- key signals generated
- risk warnings or incidents
- paper/live divergence notes
- what Milodex expects next

### Weekly summary — required contents

- strategy-by-strategy performance
- major divergences, incidents, or disablements
- trend in trustworthiness or stability
- whether each strategy is improving, degrading, or unchanged
- notable lessons from the week
- actions needed from the operator

The weekly summary is not a rollup of seven daily summaries — it carries judgment and trend analysis that the daily view deliberately does not.

---

## Paper / Live Divergence — What to Surface

The rule is not "report every mismatch." It is: **surface any mismatch that makes the operator less confident that paper evidence translates into real operation.** That includes:

- a trade that should have happened but did not
- a trade that happened when it should not have
- materially different fill timing or fill price
- repeated order rejections in one environment but not the other
- different risk-check outcomes under the same expected conditions
- strategy state drift between paper assumptions and runtime reality
- unexplained difference in exposure, turnover, or holding-period behavior
- repeated small mismatches that accumulate into meaningfully different outcomes

Small, explained, one-off differences (documented slippage, known broker-hour edges) do not need to be surfaced individually — but their *accumulation* does, which is why the last bullet matters.

---

## Attribution Slices

Milodex must support attribution by:

- **strategy** — which strategy is actually driving results?
- **symbol** — which names help or hurt most?
- **regime** — what market conditions does this strategy handle well or poorly?
- **holding period** — does the strategy work better on short holds vs longer holds?

Phase 1 does not need the most advanced attribution system possible, but these four slices must be clearly supported. "Clearly supported" means a CLI user can pull each slice without writing SQL and without opening a notebook.

---

## Charts: Essential vs Nice-to-Have

**Essential charts** (Phase 1 must produce these): answer *is this working, how risky is it, should I trust it?*

- equity curve
- drawdown curve
- cumulative return vs benchmark
- distribution of trade outcomes
- rolling performance over time
- exposure over time
- paper vs expected behavior comparison (when relevant)

**Nice-to-have charts** (Phase 1+):

- monthly heatmap
- holding-period distribution
- symbol contribution chart
- regime attribution chart
- rolling Sharpe or similar secondary diagnostic
- slippage drift over time
- sector exposure over time

The essential set is non-negotiable; the nice-to-have set is deferred without blocking Phase 1 completion.

---

## Export Formats

Phase 1 supports, at minimum:

- human-readable text or markdown summaries
- JSON for structured machine-readable output
- CSV for tabular exports (trades, metrics, incident logs)

This is enough for CLI use, future GUI work, automation, and sharing results without overbuilding the export layer. Per R-CLI-009, every command also supports `--json`, so most structured-output needs are already satisfied by the CLI itself; dedicated exports exist for bulk or archival use.

---

## CLI-Direct vs Export Split

The CLI optimizes for **fast understanding**; exports carry **full evidence and detail**. They are not competitors — they are different resolutions.

### Show directly in the CLI

- current system status
- active strategies and lifecycle stage
- current trust / warning / incident state
- key summary metrics
- concise trade reasoning
- promotion and review outcomes
- what action Milodex recommends next

### Use exports for

- full backtest reports
- complete trade ledgers
- longer incident histories
- deep attribution breakdowns
- archival evidence packages (per `docs/PROMOTION_GOVERNANCE.md`)
- machine-ingestible structured records

If a piece of information is something the operator checks daily, it belongs in the CLI. If it is something a reviewer pulls once per audit cycle, it belongs in an export.

---

## How Milodex Presents Uncertainty

This is the rule the system must not compromise on: **uncertainty is presented explicitly, plainly, and without pretending to confidence Milodex does not have.**

Low-confidence conclusions must be labeled in direct language, for example:

- *insufficient evidence*
- *low confidence*
- *review required*
- *behavior diverged from expectation*
- *result is suggestive, not trustworthy yet*
- *paper evidence not yet strong enough for promotion*

Whenever possible, Milodex must also say **why** confidence is low — low trade count, fragile parameter sensitivity, stale data, runtime anomalies, paper-vs-expected mismatch, unresolved incidents.

The system must never hide uncertainty behind polished output. Trust should come from **honesty, not from certainty theater**. This applies equally to the CLI, the trust report, summary views, and export documents.

---

## Relationship to SRS

- Domain 6 (R-ANA-*) owns the metric computations, event store, and export plumbing.
- Domain 7 (R-CLI-*) owns the surface rendering and `--json` parity.
- New requirements R-ANA-006 through R-ANA-010 and R-CLI-012 through R-CLI-014 encode the rules above. They reference this document for their detailed field lists and label vocabulary.
