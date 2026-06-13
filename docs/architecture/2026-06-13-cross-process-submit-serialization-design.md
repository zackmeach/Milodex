# Cross-process submit serialization — design proposal (the micro-live capital gate)

**Status:** Resolved — Option A accepted; see [ADR 0056](../adr/0056-cross-process-submit-serialization-per-account-advisory-lock.md). This note remains the option analysis / background.
**Date:** 2026-06-13
**Related:** [ADR 0008](../adr/0008-risk-layer-veto-architecture.md) (risk veto / execution chokepoint), [ADR 0024](../adr/0024-account-scoped-position-caps-are-authoritative.md) (account-scoped caps authoritative), [ADR 0026](../adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) (per-process supervisor; 2026-05-30 addendum names this race), [ADR 0029](../adr/0029-per-strategy-position-attribution-at-risk-layer.md) (per-strategy attribution), [ADR 0055](../adr/0055-event-store-per-strategy-position-ledger.md) (per-strategy ledger), [`docs/RISK_POLICY.md`](../RISK_POLICY.md) "Known limitations" #3, [`src/milodex/execution/service.py`](../../src/milodex/execution/service.py), [`src/milodex/core/advisory_lock.py`](../../src/milodex/core/advisory_lock.py)

> **What this is, and why it is not an ADR (yet).** This is a pre-decision design surface, not a decision. The Milodex ADR conventions ([`docs/adr/README.md`](../adr/README.md)) keep ADRs forward-facing and decision-only — they record the chosen path and do not enumerate alternatives. The mechanism here is *not yet chosen*; the choice is a sacred-layer decision reserved for the operator. So the option space lives in this design note. **When a mechanism is selected, it is recorded as a normal Accepted ADR** (chosen-path-only) and this note becomes its background.
>
> **Why it exists.** The 2026-06-12 architecture deepening audit ([`docs/reviews/2026-06-12-architecture-deepening-audit.md`](../reviews/2026-06-12-architecture-deepening-audit.md)) surfaced many leaked-invariant seams but did **not** rank the cross-process submit race — the one not-yet-built seam that actually gates real capital. The 2026-06-13 second opinion ([`docs/reviews/2026-06-13-architecture-audit-second-opinion.md`](../reviews/2026-06-13-architecture-audit-second-opinion.md)) flagged the omission and recommended capturing it as a design surface rather than a refactor PR. This is that surface.

## Context

The risk veto enforces account-scoped position and exposure caps ([ADR 0024](../adr/0024-account-scoped-position-caps-are-authoritative.md)) by reading the current account snapshot, evaluating the intent against the caps, and then submitting through the single execution chokepoint ([ADR 0008](../adr/0008-risk-layer-veto-architecture.md)). That read → evaluate → submit sequence is **not atomic across processes.**

**The race.** Under the per-process supervisor model ([ADR 0026](../adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md)), each strategy runs in its own process. Two runners can:

1. both read the same pre-fire account snapshot (e.g. account holds N positions, cap is N+1),
2. both pass `_check_concurrent_positions` / `_check_total_exposure` against that stale snapshot,
3. both submit, and both fill —

briefly pushing the account to N+2, one position past the cap. The same shape applies to total-exposure notional.

**Why the existing fix is partial.** The 2026-05-30 same-process tightening ([ADR 0024](../adr/0024-account-scoped-position-caps-are-authoritative.md)) makes `_check_total_exposure` / `_check_concurrent_positions` count in-flight (unfilled) BUY orders from `context.recent_orders` toward the caps. That closes a burst-before-fill overshoot **within one runner**. It explicitly does not span processes ([ADR 0026](../adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) addendum, 2026-05-30, line 118): two separate processes do not see each other's in-flight orders in their own `recent_orders` view at evaluation time.

**Why it is deferred for paper.** Paper is intentionally lock-free. The accepted bound is "transiently one extra concurrent position / one extra order's notional per simultaneous fire" — recoverable, visible in the audit trail, and acceptable for paper capital ([ADR 0026](../adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) addendum, line 119; [`docs/RISK_POLICY.md`](../RISK_POLICY.md) "Known limitations" #3).

**Why it blocks real capital.** With real money, an account-cap overshoot is a real risk-limit breach, not a recoverable nuisance. Per [`docs/RISK_POLICY.md`](../RISK_POLICY.md) "Known limitations" #3 and the [ADR 0026](../adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) addendum (line 119), per-account read → submit serialization is a **blocking requirement before any `micro_live` or `live` capital**, alongside the per-strategy P&L attribution gap and the `recent_orders` truncation gap.

This proposal does **not** change paper behavior. It defines the gate that must close before the `micro_live` stage is reachable with real capital.

## Proposed shape

Close the cross-process cap race by making the **read account snapshot → evaluate account-scoped caps → submit** sequence mutually exclusive per account, engaged only at `micro_live` and `live` stages. The mechanism is one of the options below.

Non-negotiable invariants any chosen mechanism must preserve:

- It sits **at or above the execution chokepoint** ([ADR 0008](../adr/0008-risk-layer-veto-architecture.md)). No code path reaches the broker outside the serialized section.
- It **never weakens or bypasses** the risk veto, kill switch, or promotion gate. Serialization is added *around* enforcement, not in place of it.
- It is **account-scoped**, matching the kill switch ([ADR 0005](../adr/0005-kill-switch-manual-reset.md)) and account caps ([ADR 0024](../adr/0024-account-scoped-position-caps-are-authoritative.md)). Contention may be narrowed to account+symbol if analysis shows that is sufficient for the cap semantics.
- **Paper stays lock-free.** The serialized section engages only for stages with real-capital effect.
- Lock acquisition is **bounded** (timeout + fail-closed): a runner that cannot acquire within the timeout declines the submit (no trade) rather than proceeding unserialized. A timeout must never fall through to an unserialized submit.

### Options

**Option A — per-account cross-process advisory lock (recommended).**
Extend the existing file-based advisory-lock infrastructure ([`src/milodex/core/advisory_lock.py`](../../src/milodex/core/advisory_lock.py)) with an account-scoped (or account+symbol) lock held across the read → evaluate → submit critical section inside the execution chokepoint. Lowest new surface, reuses proven infrastructure, aligns with the single-chokepoint invariant. Cost: serializes submits per account at micro_live (acceptable — submit volume is low at daily/intraday tempo with sub-$1k capital), and adds a cross-process lock and its timeout/stale-holder semantics to reason about (the advisory-lock module already handles stale-holder reclamation).

**Option B — broker-reservation protocol.**
Reserve capacity at the broker before submit so two processes cannot both evaluate-then-submit against a stale snapshot. Strongest correctness (the broker is the arbiter), but depends on broker support Alpaca does not currently expose for this, and couples the gate to broker capabilities. Likely infeasible in Phase 1; recorded for completeness.

**Option C — optimistic submit + compensating reconciliation.**
Allow the unserialized submit but detect an overshoot immediately post-fill (via the reconciliation surface, [ADR 0055](../adr/0055-event-store-per-strategy-position-ledger.md)) and emit a compensating cancel/flatten. Avoids a hot-path lock but accepts a transient real-capital breach and adds an unwinding path that can itself fail — weaker for real capital, and "transient breach then unwind" is a poor posture for the sacred layer. Not recommended for the capital gate.

**Recommendation:** Option A. It is the smallest change that fully closes the race within existing invariants and infrastructure. Options B and C are recorded so the decision is made against the full space, not by default.

## Consequences (of the recommended path)

- **Closes the documented cap race** for real capital, converting [`docs/RISK_POLICY.md`](../RISK_POLICY.md) "Known limitations" #3 from open to closed for `micro_live`/`live`.
- Adds **submit-path latency** at `micro_live`/`live` only (lock acquisition); paper is unchanged.
- Introduces a **cross-process lock** on the sacred path with timeout/stale-holder semantics to test and reason about (Option A reuses [`advisory_lock.py`](../../src/milodex/core/advisory_lock.py), which already implements stale-holder reclamation).
- Unblocks the `micro_live` stage as far as this gate is concerned; the other two real-capital-gate items (per-strategy P&L attribution, `recent_orders` truncation) remain independently required.

## Next step

No code lands under this proposal. The operator selects an option; the choice is recorded as an Accepted ADR (forward-facing, chosen-path-only), and implementation follows as a separate PR (estimated decent-sized for Option A: lock plumbing at the chokepoint + stage gating + bounded-acquire/fail-closed + cross-process race tests). The risk layer is sacred; this is the kind of change that warrants the grilling-loop design pass before any edit to `execution/`.
