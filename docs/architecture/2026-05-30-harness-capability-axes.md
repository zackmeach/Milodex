# Harness Capability Axes — Architecture Archetype Map

**Date:** 2026-05-30
**Type:** Capability map — *not* a build plan, *not* a roadmap.
**Companions:** [`FOUNDER_INTENT.md`](../FOUNDER_INTENT.md) (the thesis this map serves), [`STRATEGY_BANK.md`](../STRATEGY_BANK.md) (what is actually built), the deepening roadmap under [`roadmaps/`](roadmaps/) (execution order).

---

## What this document is

This is the finite set of **independent axes** along which an automated investing
technique can vary, and — for each axis — *which stage of the harness it stresses*,
*where Milodex is today*, *the next proof-slice*, and *the aspirational ring the
architecture should be designed not to preclude but may never ship*.

It exists to replace a tempting but wrong expansion rule —
*"implement at least two of each strategy type"* — which produces combinatorial
sprawl and lets a backtest pass masquerade as architectural progress. The correct
rule is stated once, below, and the rest of the document is the map it operates on.

**This is a capability map, not a build plan.** The presence of an axis or an
outer-ring entry here is a statement that *the abstractions should not hard-code
their way out of it* — not a commitment to build it. Execution order lives in the
roadmap; evidence of what is built lives in the strategy bank.

---

## The load-bearing rule

> **For each major harness axis, implement one minimal proof-slice that forces a
> real abstraction boundary — then stop, unless a second slice is needed to prove
> the first was not hard-coded.**

Corollaries:

- **One axis at a time.** Vary a single axis; hold every other axis at its already-proven
  setting. A slice that moves two axes at once cannot tell you which one broke.
- **The first slice carries ~all the architectural signal.** The *second* strategy
  inside an already-proven archetype adds statistical-robustness value (a promotion
  concern) but near-zero architecture-validation value. Do not pay twice for it.
- **Edge-family variety is architecturally free and uninformative.** Momentum vs.
  mean-reversion vs. breakout are the *same integration shape* — config-driven
  long-only daily equity rules. Adding more proves nothing new about the harness.
  Integration-archetype variety (a new axis) is where the unanswered "does it
  generalize" questions live.
- **A backtest pass is not architectural progress.** See *Promotion intent*, below.

---

## Inclusion bar

Layered. Each axis lists a **core** next-slice (plausibly on Milodex's path and
worth proof-slicing) and an **outer ring** (designed-not-to-preclude; may never
ship). The separation is deliberate: "designed to allow" must never be read as
"going to build."

---

## The axes

The test for "is this a real axis": does varying it force a change in a *distinct*
harness stage — data acquisition → decision → intent → risk evaluation → execution →
broker → audit/event-store → promotion? Eight axes pass. One capability is
cross-cutting and is noted separately.

| # | Axis | Harness stage it stresses | Where Milodex is today | Core next-slice | Outer ring (design-not-to-preclude) |
|---|---|---|---|---|---|
| 1 | **Asset class** | Data provider, symbol representation, sizing, calendar, instrument metadata | Equity/ETF proven. Crypto-spot proven at *fixture* level only — sizing, 24/7 calendar, and `/`-symbol cache seams surfaced; real-data ingestion deferred. | Close crypto-spot real-data ingestion (the loop already opened) *or* explicitly mark it exploratory. | Options (expiry/strike/Greeks/exercise — a new instrument model), futures (roll/margin/multiplier), FX (pip sizing, 24/5). |
| 2 | **Tempo / cadence** | Runner cadence + market-hours gate, data fidelity, backtest-engine dispatch, concurrency surface | Daily EOD proven. Intraday minute-bar proven (canary fleet exercised the runner end-to-end). Weekly handled inside the daily runner (Friday rebalance). | None new needed — both proven. Harden the concurrency the intraday fleet exposed. | Tick / sub-minute / event-driven streaming — requires an always-on daemon, which breaks the scheduled-workflow operating model. |
| 3 | **Decision-layer type** | The decision *interface* itself; backtestability/evidence; promotion gating; manifest reproducibility; per-call determinism | **Config-driven rules only.** The harness's central thesis — a substitutable decision layer — is entirely untested beyond rules. | A backtestable non-rules decider first (proves the `DecisionProvider` seam cheaply), then an **LLM decider** (forces a new evidence track). | Multi-step / tool-using agentic decision systems. |
| 4 | **Decision shape / output** | The `TradeIntent` model, risk-layer exposure logic, sizing, position management | Long-only, single-asset, discrete entry/exit. **Short is unsupported** — attribution clamps net-short to zero ("the system can't go short"). Cross-sectional rank exists; portfolio target-weight does not. | *Deferred — not on the critical path.* Pull only when a technique demands it. | Multi-leg / spreads (coupled to the options asset class). |
| 5 | **Execution semantics / order type** | Broker abstraction, execution service, parent/child order lineage, reconciliation, in-flight-order accounting | Market-order, DAY, one order per intent. The durable model re-queries the broker rather than mirroring orders — child-order graphs break that assumption. | Limit / bracket orders; TWAP/VWAP as *preview-only* first. | Passive/maker quoting, market-making (needs event-driven runtime + queue logic). |
| 6 | **Broker / venue** | The broker interface's true abstractness, credentials/config, per-venue reconciliation, kill-switch scope | Alpaca only. `BrokerClient` is abstract but has **one implementation** — the abstraction has never been proven by a second. | A second broker (cheapest way to prove the seam isn't Alpaca-shaped). | Multi-venue routing, crypto CEX/DEX, on-chain settlement. |
| 7 | **Data source / fidelity** | Data-provider abstraction, derived-feature plumbing, bar-quality checks, golden-data contract tests | Alpaca + Yahoo, OHLCV bars, simple cache. No alt-data, no feature store, no fundamentals. | An alt-data adapter used as a *filter* (news sentiment, options-IV); a minimal feature store. | Tick / depth / full LOB pipeline. |
| 8 | **Statefulness of the technique** | Snapshot/restore, backtest↔live replay equivalence, audit reconstruction, **kill-switch-reset behavior of learned state** | All current techniques are stateless-recompute (state derives entirely from bars each cycle). No technique carries memory between decisions. | *Hold at the stateless setting* while proving axis 3. A stateful technique is its own deliberate slice, later. | RL learners; an LLM holding a persistent running thesis across decisions. |

### Why axes 4 and 8 are distinct (orthogonality)

- **Axis 8 is not a sub-point of axis 3.** You can hold decision-type fixed and vary
  statefulness (a rules strategy can be stateless-recompute or carry rolling memory),
  *and* hold statefulness fixed and vary decision-type (an ML classifier and an LLM
  can both be stateless). Two independently-movable dimensions = two axes. Axis 3
  stresses *per-call determinism and backtestability*; axis 8 stresses *snapshot,
  replay-equivalence, and what happens to learned state on a kill-switch reset* —
  different failure modes. The dangerous intersection is a *stateful, non-deterministic*
  technique (an LLM with thesis memory): worst case for the audit trail. Build the
  first LLM slice **stateless** to keep axis 8 at its cheap setting.

---

## Cross-cutting capability (not a clean proof-slice axis)

**Capital / portfolio allocation across techniques.** Strategies are siloed and
per-strategy-capped today; there is no meta-layer allocating capital across them.
This is flagged *off-thesis for the near term*: an ensemble allocator / risk-parity /
optimizer layer is alpha-sophistication that optimizes profitability (priority #5)
and proves nothing new about the harness. The architecture should not preclude it;
it should not be built next.

**The one exception that *is* needed now:** concurrent techniques must not
double-spend capital or breach global exposure. That is **not** a new "portfolio
ledger" component — it is the existing hardening item: count in-flight (pending)
orders into the exposure/position checks (the data is already in the evaluation
context) and add a per-account lock on the capital-bearing path only, with the
paper path staying lock-free. Owed as hardening, not built as a new subsystem.

---

## Axis 3 is the thesis

Axes 1, 2, 4, 5, 6, 7 each amount to *"the harness handles a new shape of the same
kind of thing."* Useful, but skippable — Milodex is a credible harness without any
of them advancing. **Axis 3 — decision-layer type — is the only axis that tests the
project's actual central claim:** that strategy logic is substitutable (rules today,
ML and frontier-model deciders later), while the risk layer, promotion lifecycle,
evidence, and human gates remain singular and govern every technique identically.
`FOUNDER_INTENT.md` names this directly ("the decision layer is plural and evolving;
the harness is singular and disciplined"). Proving a non-rules technique inside the
same evaluate → risk → execute → audit envelope — *with the risk layer retaining
veto and the technique holding no special status* — is the most on-thesis, most
differentiating expansion available.

The honest hard problem on axis 3 is **evidence**. The promotion pipeline rests on
backtest → walk-forward → gate. A supervised ML decider stays *inside* that paradigm
(leakage controllable via purged cross-validation; deterministic given a frozen model
artifact). An LLM decider *breaks* it: training-cutoff lookahead cannot be retrained
away, output is non-deterministic, and every decision is a metered call. The LLM
therefore cannot earn promotion via a historical backtest honestly; it must be proven
**forward-only, shadow-first** (propose intents and reasoning, log them as
explanations, let the risk layer evaluate, *do not submit*) and needs a new evidence
track analogous to the regime strategy's lifecycle exemption. *How a non-backtestable
technique earns promotion in a backtest-driven harness* is the real architectural
question — answering it is a genuine contribution, not merely wiring an API.

Sequencing within axis 3: the backtestable non-rules decider **first** (it proves the
`DecisionProvider` interface and the model-artifact-in-manifest pattern at low risk;
the value is the seam, not the model — it could be deliberately dumb), the LLM
**second** (the thesis test). Do not parallelize them as equals.

---

## Promotion intent — separate three labels

A backtest pass can mean three different things, and conflating them is how
expansion masquerades as progress. Make the *intent* of a promotion explicit so a
capability probe is never misread as alpha:

| Label | Meaning | Existing Milodex mechanism |
|---|---|---|
| **Capability-proven** | The harness *can host* this shape. Mechanics, not alpha. | `lifecycle_exempt` promotion (the crypto/intraday canary status). |
| **Candidate strategy** | Maybe worth paper-testing on merit. | Paper-readiness gate tier. |
| **Production candidate** | Earns capital only after stronger OOS / robustness / risk / operational checks. | Capital-readiness gate tier (+ the manual human-approval gates). |

The mechanisms already exist; the gap is that nothing labels a promotion with *why*
it happened. The intraday canaries rely on a prose note in the strategy bank to
carry "promoted to prove mechanics, not because it has edge." Making promotion-intent
a first-class field is a small, on-thesis governance improvement that stops the
misread structurally.

---

## How to use this map

1. Pick **one axis** with an unproven core next-slice.
2. Build the **smallest serious slice** that forces that axis's abstraction boundary,
   holding every other axis at its proven setting.
3. Label the result by **promotion intent** (capability / candidate / production).
   A capability-proof stays inert or `lifecycle_exempt`; it does not become a
   running strategy by virtue of passing a backtest.
4. Stop. Add a second slice on the same axis *only* to prove the first was not
   hard-coded.
5. Record what the slice actually proved (and what it deferred) — see the crypto
   archetype proof slice review under [`reviews/`](../reviews/) as the template.

---

## Maintenance

Update when an axis's "today" status changes (a slice proves a core next-slice, or a
deferred integration closes), when a new genuinely-independent axis is identified, or
when the promotion-intent labeling becomes a first-class field. Do not add edge-family
entries — they are not axes. Do not promote outer-ring entries to core without a
stated reason on Milodex's actual path.
