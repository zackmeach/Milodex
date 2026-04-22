# Risk Policy

Companion to `docs/SRS.md` Domain 2 (Execution / Risk) and Domain 4 (Strategy Engine). SRS encodes *what risk checks must exist and how they behave*; this document fixes the **default numeric values**, the **scope of kill-switch behavior**, and the **policy distinctions** (hard stop vs warning, increasing vs reducing exposure) that the checks implement. The authoritative machine-readable source is `configs/risk_defaults.yaml` — this document is the human-readable rationale it should match.

The founder's intent (see `docs/FOUNDER_INTENT.md`) is that Milodex feel disciplined rather than aggressive, and understandable to a less financially literate user. Every default below is chosen for clarity and restraint over sophistication.

---

## Phase 1 Paper Evaluation Baseline

- **Starting paper capital:** **$100,000**. This is the default reference baseline for Phase 1 evaluation. It is large enough to make position sizing, exposure, and portfolio behavior easy to reason about, and it matches a common paper-trading convention. It is deliberately not tied to the founder's personal account size — the goal in Phase 1 is a clean, understandable evaluation environment. Alternate paper-capital profiles may be supported later.

---

## Sizing and Exposure Defaults

All percentages are of **current account equity** unless explicitly stated as start-of-day equity.

| Parameter | Phase 1 default | Rationale |
|---|---|---|
| Per-position target size | **10% of equity** | Simple fixed-percent sizing. One-sentence explainable. |
| Max single-position exposure | **10% of equity** | Matches sizing target; prevents any one name from dominating. |
| Max total portfolio exposure | **50% of equity** | Milodex never deploys more than half the portfolio into active positions under default rules. The remainder stays unallocated. |
| Max single-sector exposure | **20% of equity** | Prevents obvious over-concentration in one sector. |
| Max correlated positions per trade idea | **2** | Blocks stacking near-identical exposures (e.g., multiple semis expressing the same view) even when the sector cap is not yet breached. |

**Sizing is simple fixed-percent** for Phase 1, not volatility-aware. Clarity, explainability, and low implementation ambiguity win over sophistication. Volatility-aware sizing may be explored later; it is not a Phase 1 requirement.

Sizing and exposure rules apply to **exposure-increasing** orders. Orders that reduce or close existing exposure are governed by the more permissive policy in "Reducing vs Increasing Exposure" below.

---

## Daily Loss Logic

Daily loss is computed as **realized + unrealized P&L** measured against the **start-of-day equity snapshot**. Using only realized P&L would ignore open-position risk; using only point-in-time equity without a defined reference becomes ambiguous. The explicit rule is:

> `daily_loss = (current_realized_pnl + current_unrealized_pnl) - start_of_day_equity_snapshot`

Compared against the configured daily loss cap (default: 3% of start-of-day equity, see `configs/risk_defaults.yaml`). Breach is a kill-switch trigger, not a warning.

---

## Kill-Switch Triggers

The kill switch trips on **any** of the following conditions. The list is deliberately broader than drawdown alone — the switch exists to stop the system whenever **trust in execution or state becomes questionable**, not only when P&L deteriorates.

- daily loss cap breach (per the formula above)
- portfolio drawdown threshold breach
- repeated order submission failures
- repeated unexplained mismatches between broker state and local state
- repeated data-quality failures that affect tradability decisions
- stale or unverifiable market data at submit time
- duplicate-order detection failure (the system cannot confidently determine duplicate status)
- execution behavior materially diverging from allowed assumptions
- account-level exposure limit breached or attempted
- strategy config fingerprint mismatch at runtime (per R-STR-012)
- repeated runtime exceptions in critical trading paths
- broker connectivity unavailable after configured retry count
- manual operator-triggered emergency stop (via the SIGINT shutdown dialog, per R-STR-008)

---

## Kill-Switch Scope: Strategy-Level vs Account-Level

Milodex supports **both** strategy-level and account-level kill switches.

- **Strategy-level kill switch** isolates problems to a single strategy when the issue is bounded to that strategy's signals, execution, or state. One failing strategy should not unnecessarily shut down everything.
- **Account-level kill switch** halts all trading when the condition threatens the integrity or safety of the entire system (e.g., broker-state mismatch, connectivity loss, account-exposure breach, operator emergency stop).

When in doubt, escalate to the account-level switch. Any hard stop listed below that applies globally (e.g., broker connectivity) trips the account-level switch; any hard stop tied to one strategy's behavior (e.g., that strategy's config fingerprint mismatch, that strategy's repeated rejections) trips the strategy-level switch for that instance.

---

## What Happens When a Kill Switch Triggers

A kill switch creates a **reviewable incident state**, not just a temporary pause flag. When triggered, the system must:

1. Immediately block any new exposure-increasing orders.
2. Preserve and log the exact triggering condition.
3. Snapshot relevant local state, broker state, and strategy state.
4. Mark affected strategies (or the full account) as halted in durable state.
5. Surface a clear operator-facing incident summary.
6. Require explicit review before any re-enable action (no auto-resume — per R-EXE-006).
7. Create a governance event linking the halt to any later reversal or restart (per the promotion-log append-only pattern in `docs/PROMOTION_GOVERNANCE.md`).
8. Continue allowing safe read-only inspection and reporting.
9. Continue allowing **exposure-reducing** actions if policy permits (see next section).

---

## Reducing vs Increasing Exposure

Milodex treats order direction asymmetrically by its **effect on risk**, not by its side alone.

- **Exposure-increasing orders** (new longs in Phase 1, adds to existing longs) pass the **full** set of risk, data, approval, and kill-switch checks. Any hard stop blocks them.
- **Exposure-reducing orders** (sells that close or shrink an existing long, in Phase 1's long-only model) pass a **more permissive** policy, because they generally move the portfolio toward safety.

**Sell orders during an active kill switch:** ordinary exposure-reducing sells are still allowed, because they make the system safer. Any sell whose net effect would increase risk via more complex side effects (e.g., hypothetical short-side expansion in a future non-long-only phase) is blocked.

The guiding principle: a kill switch stops new risk from being added. It does not trap the system in existing risk.

---

## Duplicate-Order Policy

Milodex enforces a **strict no-duplicate-order** policy. An order is a duplicate and **blocked** if it would create materially the same exposure change for the same strategy while an equivalent order is already pending, recently submitted, or not yet reconciled.

Duplicate detection keys off, at minimum:

- strategy instance
- symbol
- side
- intended action type
- target quantity or target exposure
- execution window / submission cycle

If the system cannot confidently determine whether an order is a duplicate, it must **block and require review** rather than submit twice. Uncertainty is itself a hard stop (see below).

---

## Hard Stops vs Warnings

The rule is simple: **if the condition makes execution untrustworthy or policy-invalid, it is a hard stop. If it is informative but still safe to proceed, it is a warning.**

**Absolute hard stops** (block submission, no override without explicit operator action):

- kill switch active
- stale or unverifiable submit-time data
- broker connectivity unavailable at submit time
- config fingerprint mismatch
- duplicate-order uncertainty
- max single-position exposure breach
- max portfolio exposure breach
- sector / correlation cap breach
- missing required approval for a gated action
- critical reconciliation mismatch between local and broker state

**Warnings** (logged in the explanation record per R-XC-008, do not block):

- lower-confidence data anomalies that do not invalidate the run
- unusual but not forbidden volatility conditions
- strategy underperformance that has not yet crossed demotion thresholds
- elevated slippage relative to expectations
- non-critical paper-vs-live divergence
- increased operator-review burden

Warnings accumulate and may themselves become a kill-switch trigger if they repeat (e.g., repeated data-quality warnings → data-quality hard stop).

---

## Relationship to SRS and Config

- `configs/risk_defaults.yaml` is the machine-readable source of truth for every numeric default above. If this document and the config disagree, **the config wins** and this document should be updated.
- `R-EXE-004` enumerates the risk-check set that enforces these rules.
- `R-EXE-008` requires all thresholds to be sourced from `risk_defaults.yaml` (no hardcoded values in code).
- `R-EXE-009` defines duplicate detection; this document fixes its key set and "block on uncertainty" policy.
- `R-EXE-010` defines the kill-switch trigger set; this document is the authoritative enumeration.
- `R-EXE-014` through `R-EXE-017` (new) encode the scope split, the post-trip requirements, the reducing-vs-increasing asymmetry, and the hard-stop vs warning classification.
