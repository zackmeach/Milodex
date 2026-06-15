# ADR 0056 — Cross-process submit serialization uses a per-account advisory lock

**Status:** Accepted
**Date:** 2026-06-13
**Related:** [design proposal](../architecture/2026-06-13-cross-process-submit-serialization-design.md) (option analysis + background), [ADR 0008](0008-risk-layer-veto-architecture.md) (execution chokepoint), [ADR 0024](0024-account-scoped-position-caps-are-authoritative.md) (account-scoped caps), [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) (per-process supervisor; 2026-05-30 addendum names the race), [ADR 0005](0005-kill-switch-manual-reset.md) (account-scoped kill switch), [`src/milodex/execution/service.py`](../../src/milodex/execution/service.py), [`src/milodex/core/advisory_lock.py`](../../src/milodex/core/advisory_lock.py)

## Context

The account-scoped position and exposure caps ([ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)) are enforced by reading the account snapshot, evaluating the intent, and submitting through the single execution chokepoint ([ADR 0008](0008-risk-layer-veto-architecture.md)). That read → evaluate → submit sequence is not atomic across processes: under the per-process supervisor ([ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md)) two runners can both evaluate against the same pre-fire snapshot, both pass the caps, and both fill — overshooting an account cap. The 2026-05-30 same-process tightening ([ADR 0024](0024-account-scoped-position-caps-are-authoritative.md)) closes this only *within* one process. Per the [ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) addendum and [`docs/RISK_POLICY.md`](../RISK_POLICY.md) "Known limitations" #3, closing the cross-process race is a blocking requirement before any `micro_live` or `live` capital; paper accepts the bounded overshoot and stays lock-free.

The option space (per-account advisory lock vs broker-reservation vs optimistic reconciliation) was analysed in the [design proposal](../architecture/2026-06-13-cross-process-submit-serialization-design.md). The operator selected the per-account advisory lock.

## Decision

The submit critical section is serialized per account by a file-based advisory lock:

- A bounded, blocking acquire (`AdvisoryLock.acquire_blocking`) wraps the read-snapshot → evaluate-caps → submit span inside `ExecutionService._submit`. The existing single-holder advisory lock ([`core/advisory_lock.py`](../../src/milodex/core/advisory_lock.py)) — including its stale / recycled-PID reclamation — is reused; the only addition is wait-your-turn-then-fail-closed semantics.
- The lock is **account-scoped**, keyed by trading mode (`submit.{trading_mode}`); one Alpaca account per mode in Phase 1. It is account-wide, not per-symbol, because the caps it protects (concurrent positions, total exposure) are account-wide.
- It engages **only for non-backtest submits at `micro_live` and `live`**. Paper stays lock-free (the accepted overshoot bound); backtests run a simulated broker in one process and never serialize. *(Amended 2026-06-15 — paper now serializes too; see [Amendment](#amendment-2026-06-15--paper-now-serializes) below.)*
- Acquisition is **bounded** (default 30 s) and **fail-closed**: on timeout the submit is declined — recorded as a blocked decision with reason code `submit_serialization_unavailable`, no order sent, the runner continues to the next cycle. A timeout never falls through to an unserialized submit.

## Rationale

It is the smallest change that fully closes the race within existing invariants: it sits at the execution chokepoint, adds serialization *around* enforcement (the risk veto is untouched and never weakened), reuses proven lock infrastructure rather than inventing a protocol, and keeps paper lock-free as decided. Broker-reservation depends on broker support Alpaca does not expose; optimistic post-fill reconciliation accepts a transient real-capital breach — both rejected (see the design proposal).

## Consequences

- Closes the cross-process cap race for real capital, converting [`docs/RISK_POLICY.md`](../RISK_POLICY.md) "Known limitations" #3 from open to closed for `micro_live`/`live`.
- Adds submit-path latency only at `micro_live`/`live` (a single lock acquire over a seconds-long critical section); paper is unchanged.
- `micro_live`/`live` are not reachable in Phase 1, so the lock path is dormant today — forward-provisioned so the gate exists before those stages open, and verified directly by unit and contention tests rather than in production.
- The other two real-capital-gate items remain independently required: per-strategy P&L attribution, and the `recent_orders` truncation gap ([`docs/RISK_POLICY.md`](../RISK_POLICY.md) "Known limitations").

## Amendment (2026-06-15) — paper now serializes

The original decision kept paper lock-free because the accepted overshoot bound — "at most one extra concurrent position / one extra order's notional per simultaneous fire" — was tolerable for paper capital, and at the time the per-symbol launch guard ([ADR 0026](0026-concurrent-multi-strategy-uses-per-process-supervisor.md) addendum) capped practical concurrency at one runner per evaluation symbol.

The concurrent-intraday-runners work (2026-06-15) removes that guard so every deployable paper strategy can run at once, **including several on the same symbol sharing one Alpaca account**. With many writers on one account, two simultaneous fires can both clear an account-scoped cap on the same stale snapshot — the exact race this ADR closes for `micro_live`/`live`. The lock-free rationale no longer holds.

`_should_serialize_submit` therefore now returns true for **every non-backtest stage** (`paper`/`micro_live`/`live`); only backtests remain lock-free (simulated broker, single process). All other decisions above are unchanged: account-scoped key, bounded blocking acquire, fail-closed on timeout with reason `submit_serialization_unavailable`.

**Honest scope.** The lock makes Milodex's own submit critical sections mutually exclusive per account. It does **not** make caps atomic against *asynchronous broker fills* (a just-submitted order may not appear in the next snapshot). It reduces paper to the same residual the live path already carries — economically nil at 5Min cadence, ~11 strategies, sub-$1k intents, a $101k account.
