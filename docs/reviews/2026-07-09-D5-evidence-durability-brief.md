# D-5 Decision Brief — Evidence-Durability Labeling Stance

*2026-07-09. Prepared per the CURRENT_ROADMAP §8 decision-pause protocol: primary
framing + independent dissent review (reviewer explicitly asked to dissent),
reconciled below. Founder decides; the decision lands in a decision record /
ADR-adjacent note, and the roadmap incorporates it at the M3 gate.*

## The decision

Lock what "current research verdicts exist" (M3 / trust-closure §4) requires with
respect to evidence durability, given the data feed. The roadmap's honesty bound:
"closeable" must mean *honestly-labeled exploratory verdicts exist*, not
*promotion-grade edge*.

## Facts (code-grounded, verified 2026-07-09)

1. **Every bar in the system is IEX-sourced.** `feed=DataFeed.IEX` is hardcoded
   for all fetches (`data/alpaca_provider.py:315`, `:503`) — daily and intraday
   alike. The honest constraint is therefore **"IEX-sourced verdicts are
   non-durable" — a property of the feed, not the tempo**. The roadmap's current
   wording ("every *intraday* verdict is structurally non-durable on IEX") is a
   category error the dissent caught: a daily verdict on today's feed is equally
   non-durable; a SIP-sourced verdict at any tempo would be durable.
2. **What a registry row records:** `iex_exploratory=True`, `durable=False`,
   `feed="iex"` — always serialized, but inside the `evidence_json` TEXT blob
   (`research/evidence_assembler.py:132-135`, `:157-159`). First-class columns
   carry only `revisitable` (forced `True` for IEX rows), `terminal_status`, and
   a prose `rationale` prefixed "IEX-exploratory / non-durable (ADR 0017)".
3. **The writer coherence guard covers only `rejected` rows**
   (`evidence_assembler.py:696-710`). Because IEX can never overstate-proof a
   win, decisive wins terminal as `inconclusive` (`:616-643`) — i.e. **most
   closure rows will bypass the guard**. In normal operation the markers are
   coherent by construction; the guard defends only against in-process
   tampering. It is not the honesty mechanism it first appears to be.
4. **The promotion firewall is real but accidental.** No `promotion/` code reads
   `experiment_registry` (verified by grep: the table is touched only by
   `research/evidence_assembler.py`, `core/event_store.py`, and the two CLI
   command modules). Nothing *enforces* this — no policy line, no test. A future
   PR wiring promotion to registry rows would breach it silently.

## Options

| Option | Substance | Assessment |
|---|---|---|
| **A — Labeling-only (status quo)** | The existing markers + rationale prefix are the stance; closure = ≥3 honestly-labeled exploratory rows. No code change. | Founder-aligned (trust over profit; edge not required). Weakness: the machine-readable markers live in a JSON blob; the guard misses the `inconclusive` majority. |
| **C+ — Enforced promotion firewall** | One-line policy statement (*no promotion codepath may read `experiment_registry`*) **plus an AST/import-level test** asserting it — the same pattern as the existing chokepoint-invariant tests. | The dissent's central upgrade, adopted: this test, not the writer guard, is the real guarantee that exploratory rows can never leak into a capital decision. Small PR. |
| **B — Column promotion** | Migration promoting `durable`/`feed` to first-class columns. | Not a closure prerequisite. **D-8-gated**: if D-8 decides "labeling suffices" for operator trust, B becomes the mechanical substrate for M2's GUI durability chip (json_extract/prose-regex is too fragile a base for an operator-facing truth surface). Defer, but record the coupling. |
| **D (maximal) — SIP before any verdict counts** | All closure verdicts must be durable-feed. | **Reject** — contradicts §4 ("promotion-grade edge is not required"), §10 ("do not pull SIP forward to close M3"), and FOUNDER_INTENT (trust over profit). |
| **D (narrow) / E — ≥1 durable-feed verdict among the three** | Buy the $99/mo SIP toggle for one durable verdict (tempo is irrelevant — see fact 1; a "daily sidestep" without SIP is illusory). | Coherent but unnecessary: closure is defined by honesty, not durability. Available later if a promotion case ever demands durable evidence (§10 trigger unchanged). |

## Recommendation (reconciled)

**Adopt A + mandatory C+, with the wording correction; defer B (D-8-gated);
reject maximal-D; leave narrow-D/E un-exercised.**

Concretely, the decision record would lock:

1. **Closure definition:** M3 closes when ≥3 price-action hypotheses have current
   verdicts persisted to `experiment_registry`, each carrying the full
   non-durable marker set. Rejected/inconclusive are successful research
   outcomes. All three may be IEX-exploratory — §4 as written never required
   otherwise.
2. **Wording correction** (roadmap §4 / M3 / D-5 text): non-durability is a
   property of the **feed** ("IEX/non-consolidated-sourced"), not the tempo
   ("intraday"). On the current hardcoded feed this covers every verdict at any
   tempo.
3. **C+ firewall PR** (small): policy line + a test asserting no `promotion/`
   module imports/reads `experiment_registry`. Rides with the M3 work.
4. **B recorded as D-8-gated**, not shelved: if D-8 lands "labeling suffices,"
   B (or a dedicated durability column) becomes an M2 prerequisite for the
   operator-visible durability label.

## What the founder is being asked to decide

1. Adopt the stance above (A + mandatory C+, wording fix, B gated on D-8)? —
   or direct otherwise.
2. Confirm the ≥3 closure verdicts may all be IEX-exploratory (no SIP purchase
   for M3).
3. Approve the official evidence-run window **2022-01-01 → 2026-06-13** (inside
   every symbol's 5Min cache; wider = more walk-forward folds) — the window the
   scratch rehearsal uses, and the free parameter the official registry rows
   will permanently record.

## Dissent findings incorporated (attribution)

The independent review materially changed this brief: the tempo-vs-provider
category error (its core finding — confirmed against `alpaca_provider.py`), the
`rejected`-only scope of the writer guard, the unenforced-firewall upgrade of
Option C from "optional" to mandatory-with-test, the D-8/B coupling, and the
fair (narrow) restatement of Option D. The primary framing's original
recommendation ("A, optionally + C") stands corrected to the above.
