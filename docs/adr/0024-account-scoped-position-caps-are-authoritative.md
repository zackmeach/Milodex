# ADR 0024 — Account-Scoped Position Caps Are Authoritative

**Status:** Accepted
**Date:** 2026-05-04
**Relates to:** [ADR 0008](0008-risk-layer-veto-architecture.md) (risk layer veto), [ADR 0022](0022-strategy-rotation-scope-is-the-declared-universe.md) (universe-as-strategy-scope, the intent-side companion), [CLAUDE.md](../../CLAUDE.md) "Risk layer is sacred", CS-1 in [docs/PHASE2_PLANNING.md](../PHASE2_PLANNING.md)

## Context

The risk evaluator's `concurrent_positions` check counts every open broker position regardless of which strategy proposed it. Strategy YAML configs declare a `risk.max_positions` value (e.g., regime's `max_positions: 1`, meanrev's `max_positions: 3`) that reads naturally as a per-strategy ceiling.

On 2026-05-04, regime session `a140da6c-a50d-4bdb-98e9-fc2b20e2ed1f` started against a paper account that still held meanrev's three leftover positions (AVGO, GLD, SLV). [ADR 0022](0022-strategy-rotation-scope-is-the-declared-universe.md) had already restricted regime's *intent generation* to its declared universe (SPY, SHY) — and it held: regime proposed exactly one BUY SPY intent with zero rogue SELL legs against the foreign positions. But every cycle was rejected with reason code `max_concurrent_positions_exceeded`: the risk layer's account-scoped count was `1 + 3 = 4 > 1`.

Both behaviors are individually correct:

- **Risk layer veto** ([CLAUDE.md](../../CLAUDE.md), [ADR 0008](0008-risk-layer-veto-architecture.md)) requires account-level enforcement. The risk layer's job is to refuse trades that would compromise the account regardless of which strategy proposed them.
- **Strategy YAML's `max_positions: 1`** in [configs/spy_shy_200dma_v1.yaml](../../configs/spy_shy_200dma_v1.yaml) is correct in isolation: regime should hold *either* SPY or SHY at any time, never both.

The conflict is schema-level — `max_positions` is overloaded between "strategy-internal invariant" and "account-wide brake fed to the risk evaluator." The 2026-05-04 incident exposed the overload by colliding the two interpretations against the same field.

[Phase 2 §3.2 CS-1](../PHASE2_PLANNING.md) framed three resolution options. This ADR adopts option (c).

## Decision

**Account-level enforcement is the binding semantics for position caps.** The risk evaluator's `concurrent_positions` check counts every open broker position and refuses an intent that would push the projected open count above `max_concurrent_positions` from `configs/risk_defaults.yaml`. This is unchanged from current behavior — this ADR codifies it as the authoritative model.

**Strategy YAML `risk.max_positions` is informational metadata about what the strategy expects to hold.** It does not constrain the risk evaluator. It documents the strategy's internal invariant and is available for future per-strategy attribution work, but does not bind the risk layer today.

**Operators running multiple strategies in the same paper account must size `max_concurrent_positions` to the sum of strategies' expected concurrent positions.** Underprovisioning produces the exact symptom CS-1 surfaced: legitimate intents blocked by `max_concurrent_positions_exceeded`. The runtime makes no automatic adjustment for this.

## Rationale

- **The risk layer's principle requires account-scoped enforcement.** [CLAUDE.md](../../CLAUDE.md)'s "strategy proposes, risk disposes" frames the risk layer as the account's last line of defense before the broker. Per-strategy enforcement at the risk layer would require strategy-attribution at the position level, but the broker (Alpaca) returns positions without strategy attribution — reconciling broker positions to our trade history is nontrivial and reliability-sensitive. Until that reconciliation is built, account-scoped is the only enforceable layer.
- **Per-strategy position accounting is the right long-term answer if and only if concurrent multi-strategy execution is opened.** [Phase 2 §4.1.iii](../PHASE2_PLANNING.md) is deferred. Building per-strategy attribution at the risk layer for a single-strategy-at-a-time use case is overhead without a use case.
- **Phase 1's "two strategies, two purposes" was operational symmetry, not concurrent execution.** The 2026-05-04 incident occurred when regime started against a paper account that still held meanrev's leftover positions — a transient scenario, not the steady-state design. The pre-flight checklist in [ROADMAP_PHASE1.md §8 item 7](../ROADMAP_PHASE1.md) already encoded the operator-side discipline for this case ("decide before starting whether to liquidate prior positions or let meanrev manage them").
- **Documenting the constraint matches FOUNDER_INTENT priority #1 (trustworthy).** A platform that pretends to support concurrent multi-strategy operation when its risk layer cannot disambiguate strategy ownership of positions is the kind of "technically impressive shell with weak real behavior" [FOUNDER_INTENT.md](../FOUNDER_INTENT.md) explicitly warns against. Naming the limitation honestly is the platform telling the truth about itself.
- **Option (c) preserves Phase 2 §4.1.iii optionality cleanly.** When concurrent multi-strategy execution is taken up, this ADR's "account-scoped is binding" semantics stand alongside per-strategy attribution rather than being replaced. The future addition adds expressiveness; it does not invalidate the account-scoped layer.

## Alternatives considered

**(a) Per-strategy position accounting.** Risk's `concurrent_positions` check counts only positions whose `submitted_by` (or strategy-attribution chain) matches the proposing strategy. Cleanest semantics, largest code surface. Rejected because (i) it requires position-attribution at write time and reconciliation against broker positions, both nontrivial; (ii) it pre-commits Phase 2 to concurrent multi-strategy work, which is a §4.1.iii decision the operator has not yet made.

**(b) Split the schema.** Strategy YAML declares informational `expected_concurrent_positions` while `risk_defaults.yaml` retains sole binding `max_concurrent_positions`. Rejected as awkward — it permanently splits a schema concern across two surfaces while solving nothing the simple "documented and accepted" path doesn't already solve.

## Consequences

- **No code changes to the risk evaluator.** The `concurrent_positions` check continues to count every open broker position. Behavior is unchanged; the rule is now named.
- **`configs/risk_defaults.yaml` `max_concurrent_positions` comment is updated** to call out the multi-strategy sizing requirement and reference this ADR.
- **`docs/RISK_POLICY.md` adds a "Position Cap Scope" section** mirroring the existing "Kill-Switch Scope" section — same shape, different concern.
- **Strategy YAML `risk.max_positions`** is documented as advisory (informational metadata), not binding at the risk layer. Existing strategy YAMLs are unchanged.
- **The 2026-05-04 incident is the canonical operational example.** When operators encounter `max_concurrent_positions_exceeded` against a multi-strategy paper account, the resolution is to raise `max_concurrent_positions` or liquidate stale positions — not to reduce strategy `max_positions` (which doesn't bind the risk layer anyway).
- **Phase 2 §4.1.iii (concurrent multi-strategy) carries a known upgrade path:** if/when the operator opens that goal, option (a) becomes the right next move and this ADR's semantics extend rather than reverse.

## Non-goals

- This ADR does **not** change the risk evaluator's `concurrent_positions` check.
- This ADR does **not** introduce per-strategy position attribution.
- This ADR does **not** pre-decide how option (a) would be implemented if §4.1.iii is taken up.
- This ADR does **not** alter `configs/sample_strategy.yaml`'s schema — `risk.max_positions` remains a documented field, but its binding-vs-advisory status is now clear.
