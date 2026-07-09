# ADR 0005 — Kill Switch Requires Manual Reset

**Status:** Accepted — amended 2026-07-09 (Addendum: operator-initiated manual trip)
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

---

## Addendum (2026-07-09) — Operator-initiated manual trip (D-9)

*Decision record: [`docs/reviews/2026-07-09-D9-manual-halt-brief.md`](../reviews/2026-07-09-D9-manual-halt-brief.md)
(framed and dissent-reviewed per the CURRENT_ROADMAP §8 protocol; founder decided
2026-07-09). Roadmap trust-closure §4 requires a reachable manual emergency halt;
none existed on any surface.*

### What this addendum changes

The original Rationale frames every activation as **automatic fault detection**
("the trigger condition is by definition a symptom, not the disease"). This
addendum deliberately **widens the activation model**: the operator may trip the
kill switch on judgment, with no upstream fault detected by the system. Nothing
about *reset* changes — the manual-reset-only invariant, the Autonomy Boundary
("re-enabling after any kill switch" requires human review), and durable state
semantics are untouched.

### Decision

1. **A manual trip exists as a CLI action** (`milodex halt`, a discoverable
   top-level command; the emergency affordance must be fast to invoke).
   `--confirm` is required; `--reason` is **optional** (default:
   `"operator manual trip"`) — reset's investigate-first friction is deliberate
   in the *dangerous* direction; a fail-safe halt must not inherit it.
2. **Trip semantics are the breach-trip semantics** — one halt, one meaning:
   best-effort `BrokerClient.cancel_all_orders()` (a cancel failure never blocks
   the halt) **then** the durable state flip, via a **shared method** used by
   both the breach path and the manual path. A bare state flip that leaves
   resting orders live is not a halt and must not exist as a callable seam.
3. **The manual halt also issues a controlled stop to all live runners**
   (founder-selected variant A2): after the trip, runners are wound down via the
   existing controlled-stop mechanism, **failing soft** — a wedged or
   uncooperative runner does not block or delay the trip itself, which has
   already completed; such runners remain alive-but-vetoed (every intent the
   evaluator sees is refused while the switch is active) until handled per
   `TROUBLESHOOTING.md`.
4. **Provenance rides the `reason` string** (e.g. the operator's text or the
   default). No new `event_type` value — downstream consumers hard-code the
   `activated`/`reset` vocabulary and a new value would silently undercount
   activations. A structured `origin` column is deferred until something needs
   to query it.
5. **Post-trip position reality is documented, not hidden:** an active kill
   switch vetoes *all* trades including exits, and the trip has cancelled any
   resting protective orders — so open positions are stranded from automation
   until the operator flattens manually at the broker or investigates and runs
   `reset --confirm`. This is accepted halt semantics, identical to a breach
   trip.

### Explicitly out of scope

- **No GUI trip surface.** ADR 0049 Decision 4 and ADR 0051 §Non-goals stand
  unmodified — this addendum authorizes a CLI path only. The D-9 decision
  record notes honestly that the GUI prohibition is a v1-prototype-era
  boundary rather than a risk-based line (the GUI already performs the more
  dangerous reset), and that a GUI trip is the risk-coherent *later* state —
  any such surface requires its own amendment of 0049/0051.
- **No change to reset**, its `--confirm` ceremony, or the Autonomy Boundary.
- **No auto-trip conditions** are added or altered.
