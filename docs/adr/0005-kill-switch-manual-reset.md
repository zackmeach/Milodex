# ADR 0005 — Kill Switch Requires Manual Reset

**Status:** Accepted
**Date:** 2026-04-16

## Context

When the system's configurable loss thresholds are breached, trading halts. The question is what happens next: the kill switch could auto-reset after a cooldown, auto-reset on market-session boundaries, or require an explicit operator action.

## Decision

A tripped kill switch persists to `KillSwitchStateStore` with `active: true` and is cleared **only** by an explicit operator action (a CLI `reset` command). No timer, no session boundary, no automatic condition ever clears the flag. While active, `ExecutionService` refuses all new orders and `BrokerClient.cancel_all_orders()` has already run.

## Rationale

- **The trigger condition is by definition a symptom, not the disease.** When the kill switch fires, something upstream has gone wrong — bad data, a strategy bug, an unexpected market event, a risk-parameter miscalibration. Auto-resuming assumes the problem resolved itself, which it rarely does; manual reset forces the operator to diagnose before unblocking.
- **Deliberate friction is the point.** Easy-to-reset safety controls decay into rituals ("just clear it and keep going"). Reset being a discrete, auditable action makes the incident visible in the operator's workflow and the state file.
- **Consistency with the "Autonomy Boundary" principle** in VISION.md: re-enabling after a kill switch event is one of the three actions that always require human review. This ADR is the mechanical enforcement of that principle.
- **Persistent state survives restarts.** A crash or reboot does not silently reset the flag. State is durable across process lifetimes so the halt outlives the failure that caused it.
- **Simple mental model.** Either the kill switch is on and nothing trades, or the operator explicitly cleared it. No hidden third state, no timing edge cases.
