# M4 Autonomy-Boundary Review (DRAFT for founder signature)

> **Status: DRAFT.** Prepared 2026-07-22 as the boundary review the M4 gate
> requires. Walks the [`docs/VISION.md`](../../VISION.md) "Autonomy Boundary"
> list item-by-item and states where each boundary stands after the 2026-07-13
> → 07-22 live-fire window and the M4 waves (#352–#378). Every event-store
> claim was re-verified read-only against `data/milodex.db` on 2026-07-22.
> Companion: [`2026-07-24-m4-closure-retrospective.md`](2026-07-24-m4-closure-retrospective.md).

The boundary under review (VISION, "Autonomy Boundary" — actions that always
require explicit human approval):

1. Promoting any strategy from paper to live trading
2. Allocating or increasing real capital to a live strategy
3. Re-enabling any bot after a kill switch, circuit breaker, or major risk event
4. Changing core risk limits for live deployment
5. Granting a new broker connection permission to place live trades
6. Overriding a blocked or rejected execution decision
7. Retiring or replacing a live strategy with a materially different version

---

## Item 1 — Promoting to live: **INTACT, structurally unreachable**

No promotion to `micro_live` or `live` occurred — or could have. ADR 0004
(paper-only) and ADR 0042 (capital-stage eligibility locked and evidence-based)
remain in force; ADR 0058's `--operator-override` bypass is **paper-stage
only** by construction (capital stages were explicitly carved out as belonging
to this boundary). The window's only promotion event of any kind is a Bench
`stage_return` (`turn_of_month` idle→backtest, 2026-07-19, promotion id 29) —
a research-lane move, not a capital move.

**Verified:** `promotions` table — zero `to_stage` ∈ {`micro_live`,`live`}
rows in this window or since the ADR 0042 lock (the ledger's one capital-stage
row ever is historical: id 2, 2026-04-22, rsi2 paper→micro_live,
`approved_by='owner'`, predating ADR 0042 and long since walked back — the
strategy runs at paper today); zero `promotion_type='operator_override'` rows
in the main store **or** the isolated M3 evidence store (both queried
2026-07-22). The override mechanism ADR 0058 built has, to date, never been
exercised anywhere — the honest general bypass exists, reviewed and scoped,
and remains unused.

## Item 2 — Real capital: **INTACT, nothing to review**

Paper account only ($101k paper equity throughout; `TRADING_MODE=paper`). No
capital allocation surface exists to misuse. The week's ~$50k of paper
notional moved entirely through the risk-gated chokepoint.

## Item 3 — Re-enabling after kill switch / risk event: **INTACT, untouched**

The kill switch did not fire and was not tripped in the real store for the
entire window — `kill_switch_events` has **zero rows since 2026-05-04**
(verified 2026-07-22). The M4 `kill_switch_trip_reset` drill exercised the
full trip→reset cycle **in a scratch event store only**, and the drill's own
assertions re-proved the boundary: reset without `--confirm` is refused with
a message naming the requirement; reset with `--confirm` is an explicit
operator act; no auto-resume path exists (ADR 0005). The D-9 `milodex halt`
lever (#341) exists and was not needed this week. The HR-4/HR-5 founder GUI
walk of the tripped-state render + reset modal remains OPEN (retrospective
§3) — that is a *visibility* walk, not a boundary gap: the mechanism's
manual-reset property is enforced in code and drill-proven.

## Item 4 — Changing core risk limits: **INTACT — the week's risk-layer changes were governed, reviewed, and safety-directional**

This is the item the week genuinely exercised, so it gets the long form.

Three PRs changed risk-evaluator semantics (#363 single-position DC-1
exemption, #364 concurrent-cap account-scoping, #378 total-exposure DC-1
exemption) and one changed drain behavior feeding it (#374 bounded
no-fresh-price exit retry). For each, the boundary-relevant facts:

- **All four were human-reviewed pre-merge.** Each went through a PR; #378's
  body explicitly gates itself ("Sacred layer — request `risk-invariant-reviewer`
  before any merge") and the session records log risk-invariant review passes
  for #363, #374, and #378 before merge; #364 was the spec≠code correction
  re-aligning code to ADR 0024/0029. None was an inline runtime change; none
  shipped without the founder-visible review path. *(Provenance nit for the
  founder: the APPROVE verdicts live in session records, not as durable PR
  comments — confirm from your side, and consider requiring the verdict be
  pasted into the PR thread for future sacred-layer merges.)*
- **Direction of change: every one narrows a false veto on exposure-REDUCING
  orders or corrects scope to spec.** The DC-1 doctrine (a held position must
  always be exitable) was already the reviewed rule for the sibling caps;
  #363/#378 extend it to the caps that had missed it. Exposure-*increasing*
  orders face every cap exactly as before — `is_exposure_increasing` still
  classifies a naked short or an over-held sell as increasing (only the
  covered portion is exempt, `risk/evaluator.py:639`). #374 changes *when* a
  drain retries, never *whether* risk evaluates: nothing submits without a
  confirmed fresh price, and the retry window only extends the attempt
  horizon before the same fail-closed drop.
- **No limit value changed.** Cap percentages, the kill-switch config, and
  `configs/risk_defaults.yaml` guardrails are untouched this window. What
  changed is which orders a cap correctly *applies to* — spec-alignment, not
  posture relaxation.
- **"For live deployment" is currently vacuous but the discipline held
  anyway:** nothing is live, yet every change was treated as if the boundary
  applied — which is the posture FOUNDER_INTENT requires ("no strategy, model,
  agent, or feature may modify, weaken, or bypass the risk policy that
  evaluates it"). Notably, the *strategies being vetoed* never got a looser
  policy from inside the harness: on 07-21 the fleet ate 1,072 false vetoes
  for ~3h45m while the fix went through review (see item 6).

**Judgment offered for founder ratification:** these changes are boundary-
*conforming* corrections (false-veto removal on the exit side, spec≠code
repair), not boundary-*relevant* limit changes. If the founder disagrees with
that classification for any of the four, that PR should be re-opened for
explicit approval here.

## Item 5 — New broker live-trade permission: **INTACT, nothing granted**

Alpaca paper keys only, unchanged. No new broker connection, no live-trade
permission, no credential scope change. (The 2026-07-06 broker-alternatives
review's stay-on-Alpaca verdict stands; nothing this week touched it.)

## Item 6 — Overriding a blocked/rejected execution decision: **INTACT — the hard case happened and no override occurred**

The week produced the strongest evidence this boundary has ever had. On
2026-07-21 a false veto (`max_total_exposure_exceeded`, missing DC-1
exemption) blocked every covered exit in the fleet — 1,072 blocked submit
explanations over ~3h45m, positions locked in place. The tempting move — relax
the cap at runtime, force the orders through, edit state — was available to a
frustrated operator and was not taken. Instead: root-cause → 7-line reviewed
fix (#378) → redeploy → the *risk layer itself* re-evaluated and allowed the
exits → 5 fills → reconcile CLEAN. Every one of the 1,072 vetoes stands in the
ledger as a correct record of what the policy-of-record said at that moment.

Also verified: zero `reconciliation_adjustments` this window (no
`resolve-position` overrides); zero `operator_override` promotions (item 1);
the three stale 07-15 entry intents vetoed at Monday's drain were left vetoed,
not forced.

## Item 7 — Retiring/replacing a live strategy: **N/A, intact by vacancy**

Nothing is live. Paper-fleet composition changed only by which runners the
operator launched; no strategy version was replaced. (The frozen-manifest
gate continues to pin what each paper runner may trade: all six daily
strategies ran under their existing frozen manifests.)

---

## Residual risks named for the record (not boundary breaches)

1. **The scheduling substrate is outside the governance perimeter.** One-shot
   launch/stop tasks die with the driving session (twice this window). The
   *safety* consequence was nil — runners orphan-reap, state survives,
   exits re-lock — but a stop that silently fails to fire is an availability
   defect adjacent to the boundary (a runner running longer than the operator
   intended). Owned by D-3/M5; flagged here so the founder decides with eyes
   open.
2. **`--operator-override` is enforced but unexercised.** Its refusal paths
   are tested (#356 territory) but no real invocation exists in any ledger.
   First real use should be treated as a small event: read the recorded
   reason, confirm `promotion_type='operator_override'` landed.
3. **HR-4/HR-5 GUI renders un-walked** (visibility of the tripped state, not
   enforcement — see item 3 and the walk script §3).

## Verdict offered for signature

All seven boundary items stand intact after the window. No human-approval
gate was bypassed, no risk policy was weakened at runtime, no override
mechanism was exercised, and the one live stress on the boundary (item 6)
resolved through review rather than around it.

```
Boundary review:  RATIFIED / RATIFIED-WITH-NOTES / REJECTED (circle one)
Item-4 classification (four risk-layer PRs as boundary-conforming): AGREE / RE-OPEN: ______
Signature: ____________________     Date: ____________
Notes:
```
