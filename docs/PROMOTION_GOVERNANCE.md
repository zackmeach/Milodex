# Promotion Governance

Companion to `docs/SRS.md` Domain 8 (Promotion Pipeline). SRS encodes *what must be true* for a stage transition; this document defines *what artifacts must exist, what they must contain, who approves them, and how reversals and rejections are preserved*. Every evidence package described here is scoped to **one frozen strategy instance** (one `strategy.id` + config fingerprint, per R-STR-012) — never to a template, family, or informal bundle.

Promotion in Milodex is governed, not ceremonial. The purpose of each package is to make it possible, months later, for the operator (or a reviewer) to ask "why was this strategy in this stage?" and get an answer from durable state alone — not from memory, not from the CLI session that approved it.

---

## Evidence Package: backtest → paper

A strategy may advance from `backtest` to `paper` only when a complete promotion evidence package exists and is attached to the governance artifact (below). The package must include, at minimum:

- the exact frozen strategy instance manifest and config fingerprint
- a reproducible backtest run with stated assumptions
- in-sample and out-of-sample results produced by the approved walk-forward method
- the required backtest report metrics and benchmark comparisons (per R-BKT-005 and R-ANA- series)
- confirmation that the strategy cleared the minimum evidence thresholds **for its strategy class** (research-target thresholds per R-PRM-004, or the operational gate for the lifecycle-proof strategy)
- sensitivity or robustness checks across a small approved parameter range
- confirmation that data-quality checks passed, or an explicit record of any exclusions
- a short human-readable explanation of what the strategy is doing and why the result should or should not be trusted
- a written promotion recommendation that enumerates known risks, weaknesses, and unresolved concerns

Promotion to `paper` means: **this strategy is credible enough to be tested in live market conditions without real capital** — it does not mean the strategy is trusted with money.

---

## Evidence Package: remaining in paper

`paper` is not a terminal state; it is a state that must continue to earn itself. At any promotion review (scheduled or event-triggered), the paper evidence package must include, at minimum:

- the original approved frozen strategy instance and the backtest→paper promotion artifact
- paper-trading history over the minimum required observation window
- expected-vs-actual signal generation comparison
- expected-vs-actual execution behavior comparison
- paper fill logs, rejected orders, skipped orders, and any runtime anomalies
- proof that risk checks, blocking rules, and kill-switch logic are functioning correctly
- divergence analysis between backtest expectations and paper behavior
- a summary of operator burden (is the system still understandable and manageable?)
- a current recommendation: remain in paper, demote, revise, or prepare for future micro-live consideration

Remaining in paper must require that the strategy is still **operationally trustworthy**, not merely theoretically attractive.

---

## Evidence Package: paper → micro_live (future, Phase 2+)

Out of scope for Phase 1 (R-PRM-006), but specified here so future work inherits the intent rather than re-deriving it. The future micro-live package is strictly a superset of the paper package and must add:

- the full backtest→paper promotion package, preserved
- sustained paper performance over a defined minimum duration
- evidence that actual paper behavior closely matches expected behavior
- zero unresolved critical execution, risk, or data-quality issues
- proof that all approval gates, kill switches, and demotion paths were exercised or otherwise verified
- confirmation that the strategy remained understandable and operable under real workflow conditions
- a capital-allocation proposal stating exact starting capital, sizing, and risk limits
- a written micro-live risk memo stating why the strategy has earned limited real-money trust
- explicit human approval recorded in durable state

Micro-live means: **this strategy has earned a very small amount of real-money trust** — not that it is production-proven at scale.

---

## Approval Authority and Governance Artifacts

Promotion decisions are approved manually by the operator. The CLI is an acceptable *interface* for approval, but the authoritative record must live in durable state (the SQLite `promotion_log`, per R-ANA-001a), never only in a shell session or informal notes.

Every promotion decision — approved, rejected, or waived — must write a **promotion review artifact** containing, at minimum:

- strategy instance name and config fingerprint
- current stage and proposed next stage
- date and time of review
- approver identity
- links or references to the relevant backtest and/or paper runs
- required-evidence summary
- threshold checks, with each marked pass / fail / waived
- known risks and unresolved concerns
- human-readable explanation of why promotion is or is not justified
- final decision
- any conditions attached to the decision (e.g., "demote if divergence > X within Y days")
- immutable audit metadata (append-only, cryptographically linked to the strategy instance and prior events per R-XC-008)

The guiding test for the artifact: *can a future reader answer "why was this promoted?" from this record alone?* If not, the artifact is incomplete.

---

## Demotion and Disablement

Milodex may **automatically disable** a strategy or **automatically mark it for review** when safety conditions are breached — that is the purpose of the risk layer and kill switch. But **formal lifecycle-stage demotion** (e.g., `paper` → `backtest`) must require explicit human confirmation and a governance artifact, the same way promotion does. This preserves safety (auto-stop is instant) without allowing silent lifecycle changes that are hard to audit.

The following events must force disablement, review, or likely demotion:

- breach of hard risk limits
- repeated unexplained divergence between expected and actual behavior
- repeated execution anomalies or order-state inconsistencies
- failed data-quality checks that undermine trust in signals
- triggered kill switch or circuit breaker
- material config drift or evidence mismatch
- prolonged paper underperformance relative to the strategy's own acceptable evidence range
- discovery of invalid backtest assumptions or methodological flaws
- operator loss of confidence due to unresolved system behavior

Not every event must immediately demote a strategy, but any of them must at minimum force a **stop-and-review** state that cannot be cleared without explicit operator action.

---

## Reversibility of Promotion Decisions

Promotion decisions are reversible, but never silently. A reversal is a **new governance event**, never an edit to prior history. A reversal record must include:

- reference to the original promotion decision
- the strategy instance and its current stage
- the stage being reversed *from* and *to*
- the operator identity
- timestamp
- explicit reason for reversal
- supporting evidence or linked incident records
- any follow-up conditions (e.g., required retesting before re-promotion)

Milodex must preserve the full promotion-and-demotion chain so that lifecycle history remains reconstructable end-to-end for any strategy instance.

---

## Experiment Registry

Milodex maintains a formal experiment registry covering **rejected, failed, inconclusive, abandoned, and promoted** strategies — not only the ones that made it to paper. Each registry entry must capture:

- the frozen strategy instance or idea identifier
- the hypothesis under test
- the stage reached
- why it was rejected, demoted, or abandoned
- the evidence supporting that decision
- lessons or cautions learned
- whether the idea is permanently retired or potentially worth revisiting later

The registry exists for research memory, auditability, and avoiding repeated dead ends. Rejected work still contributes to the system's intelligence and discipline — throwing it away throws away the lesson with it.

---

## Relationship to SRS Requirements

- `R-PRM-001` — `R-PRM-006` define the stage machine, stage-gated risk enforcement, and the statistical vs operational gates for research-target vs lifecycle-proof strategies. This document is the companion that defines *what the attached evidence must look like* at each gate.
- `R-ANA-001a` defines the `promotion_log` storage. Every artifact described here is persisted there (or linked from there).
- `R-XC-008` (explanation records) is the authoritative requirement that every governance decision be reconstructable from durable state. This document is one of its concrete applications.
