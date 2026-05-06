# ADR 0028 — Phase 4 scope closes as cleanup and per-strategy attribution

**Status:** Accepted · 2026-05-06
**Related:** [PHASE4_PLANNING.md](../PHASE4_PLANNING.md) §4.1, §4.2; [ADR 0027](0027-phase-3-is-closed-and-phase-4-may-open.md) (authorizes Phase 4); [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) (extended by §4.1 (f) work); [ADR 0004](0004-paper-only-phase-one.md) (preserved by §4.2 = (a)); [VISION.md](../VISION.md), [FOUNDER_INTENT.md](../FOUNDER_INTENT.md)

## Context

Prior phases resolved their scope questions inline — Phase 2's §4.1 = (iv) and Phase 3's §4.1 = (i)+(iii) were struck through directly in their planning documents without a separate scope ADR. This ADR departs from that precedent. The departure is deliberate: Phase 4's §4.1 = (f) authorizes per-strategy position attribution work that extends [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)'s "account-scoped is binding" semantics. ADR-discipline holds that architectural extensions deserve their own ADRs. A scope decision that authorizes an architectural extension is itself architectural — inline strikethrough is insufficient.

[PHASE4_PLANNING.md §4.1](../PHASE4_PLANNING.md) presents seven candidates: (a) micro_live promotion, (b) Desktop GUI, (c) distributable installer, (d) third research-target strategy, (e) disciplined re-tune, (f) per-strategy position attribution at the risk layer, (g) cleanup-only. The operator resolved both §4.1 and §4.2 on 2026-05-06.

The resolution arose from a concrete operational pain the operator named: *"I can't quickly tell what strategies exist, where they are, how much they've made, what's been approved, how much money I have, how much I've gained or lost."* The natural next pull from FOUNDER_INTENT's priority order is #3 (accessibility) — which points toward option (b) GUI. The operator declined that path on grounds of mechanics-before-UI: *"I don't jump into a UI design, try to use it, and then have to juggle when things go wrong, what is the UI and what are the underlying mechanics that are causing problems."* A UI built on unclear mechanics roughly doubles debugging cost: every anomaly is ambiguous between display logic and data layer. Phase 4's job is therefore to firm the mechanics so that when a UI sits on them, the surface it exposes is trustworthy.

## Decision

1. **Phase 4 §4.1 closes as (f) + (g) — per-strategy attribution and cleanup-only.**
2. **Phase 4 §4.2 closes as (a) — live remains locked. [ADR 0004](0004-paper-only-phase-one.md) is unchanged.**
3. **Options (a) micro_live, (b) GUI, (c) installer, (d) third research-target, (e) re-tune are deferred to the Phase 5+ candidate menu.**

## Rationale

**Mechanics before UI.** The operator's concern is not that a GUI is a bad idea — it is that building a UI on top of mechanics that have known ambiguities creates a debugging surface that conflates display problems with data problems. Per FOUNDER_INTENT priority #1 (trustworthy), the platform must not create confusion about the source of anomalies. Firming the mechanics first keeps the trustworthiness property intact through the UI layer when it eventually lands.

**(f) Per-strategy position attribution** is the path [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) held in reserve. CS-1 was closed in Phase 2 via option (c) — document the schema overload and accept account-scoped enforcement as authoritative — but ADR 0024's Consequences explicitly noted that *"if/when the operator opens that goal, option (a) becomes the right next move and this ADR's semantics extend rather than reverse."* Phase 4 §4.1 (f) opens that goal. The mechanics-firming intent and the concrete operational pain the operator named (can't tell what strategies have made or lost) both point here: attribution at the risk layer is a prerequisite for any future surface — CLI, GUI, or otherwise — to report per-strategy P&L accurately. ADR 0029 will articulate the new semantics in detail; this ADR only authorizes the work.

**(g) Cleanup-only** absorbs four sub-categories of mechanics-firming work that do not add new system goals:

- **Doc drift cleanup.** The planning and roadmap documents have accumulated minor inaccuracies (e.g., `ROADMAP_PHASE1.md §7` checkboxes show closed items as open; `PHASE2_PLANNING.md §3` has the actual close-out evidence). Drift of this kind undermines the documents' value as durable records; correcting it costs little.

- **Test audit and refresh.** A requirement-to-test traceability matrix (which requirements have test coverage, which do not), plus test-efficacy analysis via mutation testing on critical paths (risk layer, promotion pipeline, honest-signal property). Operates on a discovery-then-decide model: the audit surfaces gaps in a report; the operator authorizes which gaps to fix. No gap is assumed to require a fix in advance of the audit.

- **Concurrent backtest UX.** Parallelize `research screen` or ship a `--parallel` flag; verify SQLite WAL mode handles concurrent invocations cleanly. Phase 3 surfaced no carry item here, but concurrent strategy execution (ADR 0026) makes concurrent backtesting a plausible operator pattern. Resolving it before the GUI lands removes one source of mechanics ambiguity.

- **Backtest sandbox semantics.** Allow backtest-stage queries against frozen-stage strategies without manifest comparison. The current manifest-comparison gate correctly enforces promotion discipline at execution time; but a `research` invocation on a paper-stage strategy that hasn't changed is unnecessarily blocked by it. ADR 0030 will articulate the semantics; same authorization shape as ADR 0029.

**Deferred options and their reasoning.** Options (a) micro_live and (b) GUI are the two highest-stakes deferred items:

- *(a) micro_live* requires a strategy that has earned the gate, a new ADR superseding ADR 0004, and live capital allocation — all human-gated per VISION's autonomy boundary. None of (f) or (g) advances the gate-earning question; deferring is structurally correct.
- *(b) GUI* is deliberately deferred, not deprioritized. The mechanics-before-UI principle means: phase 4 (f)+(g) is the prerequisite for (b), not competition with it. Phase 5+ is the right home.
- *(c) installer* — no urgency without a working GUI first.
- *(d) third research-target* — the platform already has two truthful gate refusals; a third failure would be informative but not more so per incremental unit. The harness-scalability claim is already well-evidenced.
- *(e) re-tune* — legitimate per VISION's research discipline, but no mechanics urgency. Phase 5+ menu.

**§4.2 = (a) follows directly.** None of (f) or (g) requires the live boundary to move. ADR 0004 stays in force.

## Consequences

- Phase 4 work begins immediately on the (f)+(g) bundle.
- ADR 0029 will articulate per-strategy attribution semantics in detail; ADR 0028 only authorizes the work.
- ADR 0030 will articulate backtest sandbox semantics; same authorization shape.
- Live trading remains structurally locked. Phase 5+ may revisit per the existing ADR-supersession mechanism.
- The deferred Phase 5+ menu inherits options (a), (b), (c), (d), (e) plus whatever surfaces during Phase 4 execution.
- Test audit operates on a discovery-then-decide model: the audit produces reports (traceability matrix, test-efficacy report) — operator authorizes which gaps to fix. No gap is pre-authorized for remediation.
- `PHASE4_PLANNING.md` §4.1 and §4.2 are struck through with decision callouts and reference this ADR.

## Non-goals

- Does not relax [ADR 0004](0004-paper-only-phase-one.md) (paper-only).
- Does not pre-commit ADR 0029 or ADR 0030's specific semantics.
- Does not authorize live capital allocation.
- Does not promote any strategy beyond `paper`.
- Does not commit to a specific PR sequence — sequencing is operator-owned within the (f)+(g) scope.
- Does not retro-edit [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) — ADR 0029 is the extension point.
