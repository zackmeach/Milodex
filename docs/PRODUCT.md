# Milodex — Product Compass

> **Read first:** [`docs/FOUNDER_INTENT.md`](FOUNDER_INTENT.md) is the upstream document. PRODUCT.md is a short, operational compass derived from it. When this document and FOUNDER_INTENT disagree, FOUNDER_INTENT wins and PRODUCT.md is wrong.

This document is the product compass for Milodex. It is shorter than [`docs/FOUNDER_INTENT.md`](FOUNDER_INTENT.md), less detailed than [`docs/SRS.md`](SRS.md), less Bench-specific than [`docs/BENCH_BOUNDARY.md`](BENCH_BOUNDARY.md), and broader than [`docs/VISION.md`](VISION.md)'s phase-one scope. It exists so future contributors — human or agent — can locate the product's identity quickly without reading the full archive.

---

## 1. Product Thesis

**Milodex is an operator-governed automation harness for investment strategies and automated investing techniques.**

It runs locally, on the operator's hardware, against the operator's brokerage. It moves techniques — rule-based strategies, config-driven academic strategies, machine-learning models, and eventually frontier-model or agentic decision systems — through a disciplined lifecycle. The technique decides *what* to trade; the harness decides *whether* the decision is allowed to take effect, *under what conditions*, and *with what safeguards in front of it*.

Milodex is **not** an "AI trading bot." It is **not** a magic-money-machine. It is **not** a black-box agent handed real capital. The framing of "give your money to a model and let it trade" is the failure mode the harness exists to prevent.

---

## 2. Who It Is For

Milodex is built first for **one technical operator** — a developer with engineering judgment, a brokerage account, and a willingness to take responsibility for the decisions a harness like this makes possible.

The operator wants disciplined automation without blind delegation. They want the leverage of a system that can run, monitor, and propose; they want the safety of a system that cannot promote, allocate, or execute without their authority.

Milodex should also be **approachable to less financially literate operators** — friends, peers, technically curious non-experts — through *legibility*, not *oversimplification*. The audit-heavy, market-desk register that earns trust from a sophisticated reader is the same register that orients a careful but inexperienced one. Lowering the emotional barrier is a goal; lowering the safety posture is not.

It is not a SaaS, not a managed fund, not a product for sale, not a substitute for a licensed financial professional.

---

## 3. Core Product Promise

Milodex helps one technical operator move automated investing techniques through a disciplined lifecycle:

> **idea → research → backtest → evidence review → paper trading → controlled live exposure**

— without skipping safety gates.

The deliberate word here is **techniques**, not only "strategies." The long-term product must host different automation forms: rules, configs, ML models, LLM agents, and other decision engines. The lifecycle is the constant. The technique inside it is allowed to vary.

A useful one-line framing: **Milodex is a harness for automated trading and investing techniques.**

---

## 4. The Harness Model

The harness model splits responsibility cleanly:

- **The technique owns the decision.** A rule, a config, an ML model, or an agent can decide *what* it wants to do — what to buy, what to sell, when to enter, when to exit, when to wait.
- **The harness owns the system.** Milodex owns lifecycle, promotion, evidence reconstruction, risk veto, kill switch, audit trail, and the human-approval gates that stand between a technique's intent and the broker.

This means decision engines are **substitutable** and the harness is **stable**. A strategy is never "live" by virtue of existing; it is live only because it survived backtest with statistically credible results, survived paper trading, survived micro-live with tightly bounded capital, was explicitly promoted by the operator at every stage, has not been vetoed by the risk layer, and is not blocked by a kill switch.

A technique that cannot be evaluated, promoted, paused, vetoed, or rolled back is not a technique the harness can host.

In short: **the model or strategy proposes; the harness constrains; the risk layer can veto; the operator approves promotion.**

---

## 5. Safety Posture

Four rules. Verbatim. They are the architectural posture the rest of the product flows from.

- **Preview before action.** Every operator-driven change is rendered as a reviewable preview before it can dispatch. The operator sees what would happen before it happens.
- **Evidence before promotion.** No technique advances a stage without evidence the harness can show, reconstruct, and explain. Promotion is gated, not granted.
- **Risk veto before execution.** The risk layer sits between intent and broker, with the authority to refuse any trade. Strategies propose; risk disposes.
- **Manual gates before capital.** The transitions that put real money at risk — promotion to live, capital allocation, kill-switch reset, broker live-trade permission — require explicit human approval, every time.

The list of actions that always require explicit human approval is the authoritative one in [`docs/VISION.md`](VISION.md) "Autonomy Boundary" and [`CLAUDE.md`](../CLAUDE.md). PRODUCT.md does not duplicate it.

The goal these four rules serve is **justified trust** — the trust an operator earns by watching a system render its work legibly, gate it correctly, refuse the wrong action confidently, and ask for permission at the right moments. Blind trust is the named failure mode the harness exists to prevent.

---

## 6. Current Product Surfaces

Milodex's primary navigation is a four-surface narrative — `FRONT · BENCH · LEDGER · DESK` — chosen as a publication-and-workshop metaphor rather than a software-tab metaphor. Detailed visual contracts live in [`docs/DESIGN.md`](DESIGN.md); the product-level role of each surface is summarized here.

- **Front.** The front page. Answers *"how is the system, in plain language?"* Conversational, warm-but-factual prose. Reports state; never recommends. The surface a non-expert can read.
- **Bench.** The strategy bench. Answers *"what's the state of each strategy and what does it need next?"* A governed pipeline ledger of five vertical stage sections — idle, backtest, paper, micro-live, live — with per-row evidence and per-action previews. Operational management of the lifecycle.
- **Ledger.** The paper of record. Answers *"what has the system actually done, and why?"* Chronological, monospaced, sparse. Reads like a printout. The surface that earns auditability.
- **Desk.** The trading desk. Answers *"what's everything I might need on one fold?"* Dense cockpit view in newspaper-front-page register: hero band, columnar body, lettered sections. The surface a power user drives from once they trust the system.

**Future execution tooling.** Real command submission — the path that turns a Bench Action Menu selection into a broker-affecting event — does not yet exist. It is a deliberate non-feature in Bench v1 and will require its own ADR (amending or superseding [ADR 0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md)) and a separate PR before any path becomes live. See [§8 Current Boundaries](#8-current-boundaries).

The arc is **approachable → operational → auditable → dense**. A new surface idea should fit one of these four roles, or it should argue for a fifth metaphor in the same family. New tabs named `Settings` or `Analytics` break the metaphor.

---

## 7. What Milodex Is Not

- **Not financial advice.** Milodex does not advise, recommend, or guide an investment decision.
- **Not fiduciary.** Milodex has no fiduciary status, duty, or claim to one.
- **Not a guarantor of returns.** Outcomes are not promised. Past performance, simulated or otherwise, is not predictive.
- **Not blind delegation to an opaque model.** A technique runs only inside the harness, only after passing the lifecycle, and only under risk-veto and operator-approval supervision.
- **Not a hype-driven AI trader.** The product optimizes for trust, legibility, and discipline — not excitement, opacity, or rapid automation.
- **Not a system where safety gates can be weakened to ship features faster.** A feature that requires loosening lifecycle, evidence, risk, or approval boundaries is the wrong feature.

This list is product-shaped. The legal-shaped version of these disclaimers, when it is needed, will live separately.

---

## 8. Current Boundaries

These are the boundaries Milodex enforces *today*. They are real; they are not aspirational.

- **Bench is currently a read-only visual prototype.** It renders strategy state from the GUI read-models, exposes a per-row Action menu, and opens Evidence and Confirmation modals — but no menu item submits a command, mutates state, writes an event, or contacts the broker. See [`docs/bench/README.md`](bench/README.md) for the PR-by-PR scope summary.
- **The Command Draft Preview is not submittable.** It exists only inside the Bench confirmation modal as a local UI composition. Its `submissionState` is the literal `"not_submittable_v1"`; its primary button has no `MouseArea`. The banner says verbatim: *"Milodex can render this draft for review, but Bench v1 cannot submit it."*
- **Real command execution requires ADR work and separate PRs.** The escalation rail is: a new ADR amending or superseding [ADR 0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) Decision 2, followed by an isolated PR that introduces command infrastructure under the new ADR's constraints. Forbidden-token guards are widened explicitly as part of that PR, never silently.
- **The full architectural walkthrough lives in [`docs/BENCH_BOUNDARY.md`](BENCH_BOUNDARY.md).** That document is the load-bearing technical contract; PRODUCT.md only points at it.

Outside Bench, the same posture applies. The risk layer's veto is enforced in code, not promised in prose. The kill switch requires manual reset; auto-resume is not a feature. The promotion pipeline does not allow stage-skipping.

---

## 9. Product Decision Principles for Future Agents

When a future contributor — human or agent — is deciding what to build, change, or refuse, the following five questions are the compass:

1. **Does this make automation more legible?** Can the operator read what the system did, why, and what it would do next?
2. **Does this preserve justified trust?** Does the change earn trust through evidence, gates, and review — or does it ask the operator to trust the model, the codebase, or the contributor on faith?
3. **Does this keep lifecycle, evidence, risk, and approval boundaries intact?** A change that quietly relaxes a gate, hides a veto, or widens an allowlist without an ADR is a regression even if it ships a feature.
4. **Does this make the product more usable without making it misleading?** Polish that hides complexity is not polish; it is a trap. Polish that *renders* complexity well is the goal.
5. **Does this avoid implying execution capability before the system earns it?** A surface that looks like it can submit a command should be able to, or it should be unambiguously labelled as preview-only.

If a proposed change fails any of these, the change is wrong and the question is what to do *instead*.

---

## See Also

- [`docs/FOUNDER_INTENT.md`](FOUNDER_INTENT.md) — the upstream "why" this document derives from.
- [`docs/VISION.md`](VISION.md) — the long-term shape, phase scope, and operating principles.
- [`docs/SRS.md`](SRS.md) — the requirements specification (the *shall*-statement layer).
- [`docs/DESIGN.md`](DESIGN.md) — the four-surface narrative, tokens, and visual contracts.
- [`docs/BENCH_BOUNDARY.md`](BENCH_BOUNDARY.md) — the three-layer read-only architecture inside Bench v1.
- [`docs/bench/README.md`](bench/README.md) — the Bench v1 checkpoint and ordered future-PR plan.
- [ADR 0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) — the binding policy that makes Bench v1 a read-only visual prototype.
