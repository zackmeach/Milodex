# D-9 Decision Brief — Manual Emergency-Halt Governance

*2026-07-09. Framed at M2 entry per the CURRENT_ROADMAP §8 decision-pause
protocol (primary framing; independent dissent review pending; founder decides).
Status: **DRAFT — not decided.***

## The decision

Whether (and where) the operator gets a **manual kill-switch TRIP** affordance,
and what governance change that requires. The roadmap flags this as the trap
decision that "reads as just reachability" but is ADR-forbidden today.

## Facts (code- and ADR-grounded, verified 2026-07-09 at `ebe643c`)

1. **No manual trip exists anywhere.** The CLI has `trade kill-switch status`
   and `trade kill-switch reset --confirm` only ([trade.py:58-124](../../src/milodex/cli/commands/trade.py)).
   The kill switch activates solely from the automatic daily-loss breach path.
2. **Trust closure requires a reachable manual halt.** CURRENT_ROADMAP §4
   "Operator-visible trust": *"manual emergency halt is reachable."* Today it
   is not — by any surface.
3. **A GUI trip is expressly forbidden.** ADR 0051 §Non-goals: *"Does not
   authorise any GUI surface to trigger the kill switch. ADR 0005 and ADR 0049
   Decision 4 stay in force."* ADR 0049: Bench paths must not interact with
   kill-switch state; the kill-switch surface was the Anchor view's concern —
   and the Anchor view was deleted (HR-4, #220). `submit_stop_paper_runner` is
   controlled-stop only, explicitly not a kill-switch trigger.
4. **ADR 0005 (manual-reset-only) is about reset, not trip.** A manual trip
   does not touch the reset invariant; auto-resume stays impossible.
5. **Directionality:** a trip is fail-safe — it can only halt trading. The
   risk asymmetry is the opposite of reset (which re-enables trading and is
   already confirm-gated).

## Options

| Option | Substance | Governance footprint | Assessment |
|---|---|---|---|
| **A — CLI manual trip only** *(recommended)* | `milodex trade kill-switch trip --confirm --reason <required>` invoking the same kill-switch store activation the risk layer uses; durable `kill_switch_events` row records operator-initiated origin + reason. GUI continues to *show* state (already does) but cannot trip. | A small ADR (or ADR 0005 addendum): operator-initiated activation is a new *invocation* of the existing mechanism, not a new mechanism. ADR 0049/0051 untouched — the CLI is not a GUI surface, so their prohibitions stand unmodified. | Satisfies the closure bar ("reachable") with the smallest governed change, in the fail-safe direction. Reset discipline unchanged. |
| **B — CLI trip + Bench trip affordance** | Option A plus a Bench action family (proposal → typed confirmation → facade → same store), a new home for the affordance (Anchor is gone), forbidden-token test extension. | Requires amending **both** ADR 0051 §Non-goals and ADR 0049 Decision 4, plus the ADR-0051-style narrow token allowlist expansion. | Better ergonomics in a real emergency (the GUI is what's open), but a materially larger governance + test surface. Can layer on A later without rework. |
| **C — No manual trip; reinterpret the closure bar** | Read "manual emergency halt is reachable" as controlled-stop + manual broker flattening. | Roadmap §4 edit. | Rejected by framing: controlled-stop needs a live cooperative runner (CLAUDE.md gotcha — it hangs on a wedged one), and flattening is not a halt. Weakens a closure bar specifically written for the wedged/emergency case. |

## Reconciled recommendation (post-dissent, 2026-07-09)

The independent dissent review materially corrected the framing. Reconciled
position: **A first — corrected — with the fleet-stop sub-question put to the
founder; B deferred but honestly, not as "ergonomics."**

**Dissent findings adopted:**

1. **A's original spelling was broken (dissent F1, HIGH).**
   `trigger_kill_switch()` / `KillSwitchStateStore.activate()` only flip
   durable state — **order cancellation lives in the breach-path wrapper**
   (`execution/service.py:1128-1136`: `cancel_all_orders()` *then* trigger)
   and the runner SIGINT path. ADR 0005 and VISION:185 define the halt as
   including cancellation. Corrected A: the CLI trip calls a **shared method**
   (best-effort `cancel_all_orders()` — failure never blocks the halt — then
   the state flip) used by both the breach path and the CLI. Never bare
   `trigger_kill_switch`.
2. **`--reason` optional, `--confirm` kept (F4).** Reset's investigate-first
   friction points at the dangerous direction; importing it onto the fail-safe
   trip is backwards. Default reason: "operator manual trip".
3. **Post-trip position story must be documented (F2).** The kill-switch veto
   blocks ALL trades including exits, and the trip cancels resting protective
   orders — a trip strands open positions with no automated exit. The ADR
   addendum documents the operator path: flatten manually at the broker, or
   investigate → `reset --confirm`. (Controlled stop still works during an
   active switch — HR-5 — but its exit orders would be vetoed too.)
4. **Origin via the `reason` string, zero schema change (F5).** A new
   `event_type` value would silently break two hard-coded counters
   (`promotion/evidence.py:94`, `cli/commands/report.py:240`). Structured
   `origin` column deferred.
5. **Instrument: ADR 0005 addendum — for the stronger reason (F6).** Not
   because trip is "out of 0005's scope," but because 0005's rationale frames
   every activation as automatic fault-detection ("symptom, not disease"); a
   deliberate operator halt widens that activation model and the addendum
   must say so explicitly.
6. **B reframed (F3).** The GUI already performs the *more dangerous* half of
   the pair — `Main.qml` wires a live kill-switch **reset** modal — while
   0049/0051 forbid the fail-safe half. The prohibition is a
   prototype-no-mutation-era artifact, not a risk-based line. B remains
   deferred (the CLI path is also the *more reliable* emergency path — it
   does not depend on a possibly-wedged QML event loop), but the deferral is
   recorded as "later, risk-coherent end state," not "luxury."
7. **CLI trip is operator-visible in the GUI within ~1s** (OperationalState's
   1s poll on the shared store) — corroborated; strengthens CLI-only A.

**The one genuinely open fork (dissent F7) — founder decides:**

- **A1 — trip = muzzle:** cancel orders + flip state; runners keep looping and
  get every intent vetoed until separately stopped. Smallest; matches breach
  semantics exactly; the addendum documents that runners stay alive.
- **A2 — trip = muzzle + fleet stop:** a composite (e.g. top-level
  `milodex halt`) that trips AND issues controlled-stop to all live runners.
  Closer to what "emergency halt" colloquially means; more moving parts
  (controlled stop needs live cooperative runners — it no-ops/hangs on wedged
  ones, so A2 must fail soft to A1 semantics).

Discoverability (a top-level `milodex halt` alias vs the nested
`trade kill-switch trip`) rides with whichever is chosen — for an emergency
affordance, speed of invocation is functional, not polish.

**Latent item noted in passing (not D-9 scope):** `gui/ledger_builders.py:80`
maps `event_type == "triggered"` → "fired" but the store only writes
`activated`/`reset` — kill-switch ledger rows render as bland "info". Fold
into M2 GUI-truth work.
