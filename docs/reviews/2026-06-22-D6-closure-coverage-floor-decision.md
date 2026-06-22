# D-6 — Closure coverage-floor scope (decision, decided at M0)

**Date:** 2026-06-22 · **Status:** framed (M0); **decided at M0** per
[`CURRENT_ROADMAP.md`](../CURRENT_ROADMAP.md) §8 map. **Founder decides.**

> Decided at M0 so the **parallel requirements-coverage verification track** knows
> its closure target (roadmap M0 exit criterion 4).

## 1. The decision

Is a **requirements-coverage floor** in scope as a *trust-closure gate*, and if so,
what shape — a blanket numeric % across all SRS requirements, a targeted floor on
the safety/execution-critical requirement classes only, or no gate (continue
backfill as non-blocking parallel work)?

## 2. Current evidence

- **Coverage = 31.2% of 138 SRS requirements** (`REQUIREMENTS_COVERAGE.md`, as of
  `5060328`, 2026-06-22) — ~95 requirements untested. The figure moved 29% → 31.2%
  via the in-flight reqs-traceability batches now merged into local `master`.
- The matrix is **generator-produced** (`scripts/audit_requirements_coverage.py`)
  — coverage is measured, not hand-asserted.
- Closure §4 *Governance integrity* already requires *"verification supports every
  completion claim."* That is a per-claim bar, **not** a blanket coverage %.
- The founder bar is **trust over profit**; the risk layer is the highest-stakes
  assumption. Safety/execution-critical requirement classes (R-EXE, R-RISK/risk
  checks, R-PRM promotion, R-BRK broker, kill-switch/reconcile) carry the trust
  weight; the long tail (reporting, CLI-UX, cosmetic) carries far less.

## 3. Options

- **Option A — Blanket numeric floor** (e.g. "≥ X% of all 138 reqs traced to
  tests" as a closure gate). *Pro:* one number. *Con:* arbitrary; spends effort
  proving low-stakes reqs to hit a % rather than de-risking the sacred path; a 31%
  → 70% sprint is large and mostly off-thesis.
- **Option B — No floor; backfill stays non-blocking parallel work.** *Pro:*
  honest to "trust over profit"; verification effort goes where it matters by
  judgment. *Con:* "decided" = "no gate," which risks the sacred-path reqs never
  getting a hard line.
- **Option C — Targeted floor on safety/execution-critical classes only**
  (every R-EXE / risk-check / R-PRM / R-BRK / kill-switch / reconcile requirement
  traced to a pass+fail test as a closure gate); the long tail continues as
  non-blocking parallel backfill. *Pro:* principled — puts the hard line exactly on
  the trust-bearing surface, matching the risk doctrine and §4 governance
  integrity; bounded, achievable. *Con:* needs a one-time tagging of which classes
  are "critical" (small task; the SRS domains already cluster this way).

## 4. Recommendation (primary)

**Option C.** A blanket % is the wrong shape for a trust-over-profit system; "no
gate at all" under-protects the sacred path. Gate closure on **full
test-traceability of the safety/execution-critical requirement classes**, and keep
the remaining backfill as sanctioned parallel verification (valuable, not a closure
blocker). This gives the parallel track a concrete, bounded target without a
make-work % sprint.

## 5. Founder decision (2026-06-22) — DECIDED

**A targeted critical-obligation *assurance* gate — stronger than the primary's
Option C.** The founder chose the targeted shape but raised the bar from
"trace critical classes to a pass+fail test" to a genuine assurance gate:

1. **Versioned allowlist of individual trust-critical SRS requirements — NOT entire
   prefixes.** The critical set is a curated, frozen, versioned list of specific
   requirements, not "all of R-EXE/R-RISK/…". Picking by prefix is too coarse.
2. **Decompose each critical requirement into testable clauses.** A requirement is
   not one checkbox; each obligation within it is adjudicated.
3. **Contract-appropriate, independently-reviewed evidence per clause** — as
   applicable: positive, refusal/failure, boundary, fail-closed, durable-state
   integration, or operational-drill evidence. **Code references alone do NOT
   satisfy the gate** (this explicitly rejects the current coverage matrix's
   count-the-reference model as sufficient for the critical set).
4. **Closure requires:** 100% adjudication of the critical set; **zero unresolved
   implementation or spec gaps**; and final evidence **passing on final `master`**.
5. **Non-critical traceability remains valuable, non-blocking parallel work.**

### Consequences for the roadmap (folded at M0 close)

- The **parallel verification track's target is redefined**: it is no longer
  "raise the coverage %." It is (a) author + freeze the **versioned critical-
  requirement allowlist**, (b) decompose each into clauses, (c) gather
  contract-appropriate, independently-reviewed evidence per clause. Coverage % is
  an outcome, not the target.
- This **supersedes** the §4 *Governance integrity* phrasing only by
  *strengthening* it: "verification supports every completion claim" now has a
  concrete, evidence-typed gate for the critical set. The CURRENT_ROADMAP closure
  gate (§12) should reference this assurance gate at the next gate update.
- **A new artifact is owed** (parallel track, not M0): the versioned
  critical-requirement allowlist + clause decomposition. Tracked as newly-
  discovered work in the M0 retrospective.
- **D-6 is now closed.**
