# Founder Intent: Milodex

## Purpose of This Document

This document captures the founder's personal intent for Milodex so that future documentation, product decisions, UI choices, architectural tradeoffs, and implementation details remain aligned with the deeper reason the project exists.

Milodex should not be shaped only by what is technically possible or financially conventional. It should also reflect the founder's goals, values, and the kind of product experience he wants others to have.

---

## Core Founder Intent

Milodex exists as an AI-assisted systems-building project designed to prove that a complex, real-world software product can be built from the ground up with frontier models, strong engineering judgment, clear documentation, a polished user experience, and functional end-to-end behavior.

At its core, this project is not primarily about becoming a finance expert. It is about building something real, difficult, and credible in a domain that is not already the founder's native area of expertise. The project should demonstrate the ability to use frontier and agentic models effectively to architect, implement, document, and deliver a serious software system that works.

Milodex should ultimately be something the founder can stand behind and say:

> I built this. It works. It is thoughtfully designed. You can use it too.

---

## Milodex Is an Automation Harness, Not an AI Trading Bot

Milodex is sometimes easy to misread as "an AI trading bot." That framing is wrong, and the misreading is corrosive enough that it has to be corrected explicitly in the founder-intent layer rather than left to be inferred later.

**Milodex is an operator-governed automation harness for investment strategies and automated investing techniques.**

It is not the operator handing money to a frontier model and saying "go trade for me." A frontier model, a machine-learning model, a ruleset, or a config-driven strategy may eventually decide what it wants to trade — but only from inside the harness Milodex provides.

The distinction matters because it determines who owns what:

- the **operator** controls promotion
- the **architecture** controls lifecycle
- the **risk layer** has veto power
- the **model or strategy** does not own the system
- every technique must be testable, observable, promotable, and risk-gated before it is allowed to affect real capital

Milodex should support many possible automated investing techniques over time:

- simple rule-based strategies
- academic, config-driven strategies
- machine-learning models
- frontier-model or agentic decision systems
- equities and ETFs in the near term
- additional asset classes such as crypto over time
- multiple broker and exchange APIs as the harness matures

The harness framing is the load-bearing one. "AI trading bot" is rejected. "Automated investing technique inside a governed harness" is the correct mental model.

---

## The Harness Model — What Owns the Decision vs. What Owns the System

The harness model splits responsibility cleanly between the decision-making technique and the system around it.

**The technique owns the decision.** A rule, a config, an ML model, or an agent can decide *what* it wants to do — what to buy, what to sell, when to enter, when to exit, when to wait.

**The harness owns the system.** Milodex decides *whether* that decision is allowed to take effect, *under what conditions*, *with what evidence*, *with what risk constraints*, and *with what operator approval*. The harness owns lifecycle, promotion, evidence reconstruction, risk veto, kill switch, audit trail, and the human-approval gates that stand between a technique's intent and the broker.

This means a strategy is never "live" by virtue of existing. It is live only because:

1. it survived backtest with statistically credible results,
2. it survived paper trading,
3. it survived micro-live with real but tightly bounded capital,
4. the operator explicitly promoted it at every stage,
5. the risk layer has not vetoed it, and
6. no kill switch is engaged.

A technique that cannot be evaluated, promoted, paused, vetoed, or rolled back is not a technique the harness can host. The harness's job is to make the decision-making layer **substitutable** — rules today, ML tomorrow, an agentic system after that — without letting any of them bypass the controls.

This is the core architectural promise. The decision layer is plural and evolving; the harness is singular and disciplined.

---

## Core Promise

Milodex helps one technical operator move automated investing techniques through a disciplined lifecycle:

> **idea → research → backtest → evidence review → paper trading → controlled live exposure**

without skipping safety gates.

The deliberate word here is **techniques**, not only "strategies." The long-term product must support different automation forms: rules, configs, ML models, LLM agents, and other decision engines. The lifecycle is the constant. The technique inside it is allowed to vary.

A useful one-line framing:

> **Milodex is a harness for automated trading and investing techniques.**

---

## Trust Through Lifecycle, Evidence, Preview, and Veto

Milodex's safety posture is captured in four short rules. They are not slogans; they are the architectural posture the rest of the product flows from:

- **Preview before action.** Every operator-driven change is rendered as a reviewable preview before it can dispatch. The operator sees what would happen before it happens.
- **Evidence before promotion.** No technique advances a stage without evidence the harness can show, reconstruct, and explain. Promotion is gated, not granted.
- **Risk veto before execution.** The risk layer sits between intent and broker, with the authority to refuse any trade. Strategies propose; risk disposes.
- **Manual gates before capital.** The transitions that put real money at risk — promotion to live, capital allocation, kill-switch reset, broker live-trade permission — require explicit human approval, every time.

These four rules exist because Milodex can eventually automate actions involving real money. The product must make automation feel **trustworthy, reviewable, bounded, and professionally controlled**.

Important nuance, stated plainly so it cannot be flattened by a later edit:

**Milodex is not equivalent to handing one's finances to a licensed financial professional.** It does not have fiduciary status. It does not give financial advice. It does not guarantee returns. It is not a magic-money-machine. It is not a blind-delegation interface to an opaque model.

Instead, the intent is this:

> Milodex should make automated investing feel legible, reviewable, bounded, and controlled enough that the operator can develop *justified trust* in the system before allowing it to affect real capital.

Justified trust is the goal. Blind trust is the failure mode the harness exists to prevent.

---

## The Risk Layer — Operator Preferences, System Enforcement

The risk layer is sacred. It is not, however, arbitrary.

Milodex should not treat risk as a single universal setting that applies identically to every operator, every account, every strategy, every asset class, and every market condition forever. Risk tolerance is personal, contextual, and sometimes changes over time. A system that claims to be trustworthy cannot impose one opaque risk posture and ask the operator to accept it on faith.

But that does **not** mean strategies, models, agents, or features get to control risk. The right distinction — the one the rest of the product must reflect — is:

> **The operator owns risk preferences. The risk layer owns enforcement.**

A strategy may propose an action. A model may generate an intent. An agent may identify an opportunity. None of them may rewrite the rules that judge whether their own action is safe. The risk layer is the **independent boundary** between automation and capital.

### What this lets the operator do

The operator should eventually be able to express personal risk tolerance — but only inside a governed risk framework. The framework must have these properties:

- **Safe defaults.** A fresh installation runs at conservative posture. Higher-risk settings are an explicit choice, not a quiet drift.
- **Deliberate opt-in for higher risk.** Every step away from the safe default requires an explicit operator action, with the change rendered as a reviewable preview before it takes effect.
- **Explicit human approval for live-risk changes.** Modifying capital-affecting risk policy is on the human-approval list, every time. There is no "approve once, applies forever" path.
- **Logged and reviewable.** Every risk-policy change is written to the audit trail. The operator can reconstruct what the posture was on any given day, who changed it, and why.
- **Visibly active.** The product makes clear at all times what risk posture is currently in force. A risk setting that the operator cannot see is a risk setting Milodex should not have.
- **Legible.** The operator should be able to read what a risk setting allows, blocks, or changes — in plain product language, not buried in YAML.
- **Bounded.** Some boundaries are not negotiable. Account-level guardrails (kill switch, broker live-trade permission, fat-finger protection) stay above strategy-specific preferences. The operator cannot disable the floor; they can only choose where they sit above it.
- **Enforceable.** A risk control is configurable only in ways the system can still enforce. A setting whose value the risk layer cannot honor — for performance, scope, or architectural reasons — is not a setting; it is a lie.

### What this denies — to strategies, models, agents, and features

The risk layer never bends to the thing it is evaluating. In particular:

- **No strategy, ML model, frontier agent, or feature may weaken its own guardrails.** A strategy that finds itself blocked does not get to propose a looser risk policy from inside the harness. The operator may relax the policy from outside the harness, under the governance described above. The strategy may not.
- **No "the user controls the risk layer" framing.** That sentence is too loose. It implies the operator can disable the safety structure itself rather than choose a posture inside it.
- **No "strategies configure their own risk."** That is dangerous because it lets the thing being evaluated influence the rules evaluating it.
- **No "risk gates can be weakened when they block a useful feature."** That is the most common path to compromise on a system like this, and it is the one the harness exists to refuse.

### The strongest version

Captured as one load-bearing sentence so it cannot be flattened by a later edit:

> **Milodex lets the operator define risk preferences within explicit, bounded, auditable policy. The risk layer enforces those preferences and retains veto power. No strategy, model, agent, or feature may modify, weaken, or bypass the risk policy that evaluates it.**

This keeps both halves of the intent intact:

1. The operator is not trapped inside a one-size-fits-all risk posture.
2. The risk layer remains independent, enforceable, and sacred.

### How this relates to product trust

The point is not to let the operator "turn off safety." The point is to refuse the pretense that one fixed risk profile is right for everyone. A trustworthy system should let the operator say, in a controlled and visible way:

> *"This is my risk tolerance. These are the limits I am willing to allow. Now enforce them consistently — even against strategies I like."*

That is different from letting automation choose its own boundaries. The shortest form of the rule is the one downstream docs should quote:

> **The operator may choose the risk posture. The system must enforce it. The automation must submit to it.**

---

## Product Tone

Milodex should feel:

- serious
- sober
- audit-heavy
- market-desk and editorial in register
- clear
- empowering
- approachable without being simplistic

Milodex should not feel:

- like flashy consumer fintech
- magical
- hype-driven
- reliant on unnecessary jargon to seem sophisticated
- opaque or intimidating

The product should help the operator understand what is happening and why. It should make complex automation feel **legible and controlled**, not opaque or impressive-for-its-own-sake. The editorial register — quiet typography, restrained color, evidence rendered as document rather than dashboard — is a deliberate choice that follows from the harness framing. A harness that hides its own workings cannot earn justified trust.

---

## Primary Meaning of the Project

Milodex is meant to demonstrate all of the following at once:

- the ability to use frontier AI models as a serious development partner
- the ability to build a new software architecture from scratch
- the ability to combine backend systems, documentation, UI, workflow design, and product thinking into one coherent system
- the ability to execute in an unfamiliar domain and still deliver something credible
- the ability to ship a polished, functional, shareable project that reflects real engineering capability

This means the project is both a product and a statement of technical capability.

---

## Relationship to Profitability

Profitability matters, but it is not the only purpose of Milodex.

The founder does care whether the system is effective. If the system can operate successfully and produce credible results, that validates the quality of what was built. A profitable or at least defensibly effective system provides evidence that the project is not merely attractive or architecturally interesting, but actually useful.

However, Milodex should not be framed as a reckless attempt to build a magic money machine. Its purpose is to build a trustworthy, well-structured, explainable, and usable system whose outcomes are credible enough that the founder can confidently stand behind it.

In short:

- profitability is meaningful
- credibility is required
- trustworthiness matters more than hype

---

## Accessibility and Financial Literacy

A major part of the founder's intent is that Milodex should make the prospect of investing — especially regular, automated investing behavior — feel more accessible to people who are not deeply financially literate.

This includes the founder himself.

The founder is not approaching this project as someone with deep investing expertise. Part of the value of Milodex is that it should help bridge that gap by creating a system that feels trustworthy, understandable, and oriented even for someone who is not highly sophisticated in financial markets.

Milodex should therefore aim to be:

- approachable without being simplistic
- powerful without being intimidating
- informative without being overwhelming
- structured enough to build trust
- legible enough that a less financially literate operator can still feel oriented, supported, and in control

Accessibility here means **legibility**, not **simplification**. The product should not hide complexity to seem friendlier; it should *render* complexity well so that a non-expert operator can read what the system is doing, why, and what would happen next. The audit-heavy, market-desk register is the same register that earns trust from a sophisticated reader — it should also be the register that orients a careful but inexperienced one. Lowering the emotional barrier is a goal; lowering the safety posture is not.

---

## Product Experience the Founder Wants

The founder wants Milodex to create a strong first impression for a wide range of users, especially:

- peers
- potential employers
- curious friends
- technically interested non-experts
- users with limited financial literacy who still want a trustworthy system

The desired reaction is something close to:

> Wow, this almost seems too easy for what we're doing.

That reaction should come from thoughtful product design, not from hiding complexity irresponsibly. The system should make something serious feel approachable.

The "almost seems too easy" reaction is specifically about **legibility**: complex automation made readable. It is not about Milodex being a lightweight or casual product. The harness is serious, sober, and audit-heavy underneath. The polish of the surface is what makes the seriousness approachable — not a substitute for it.

Milodex should feel:

- polished
- smooth
- surprisingly easy to start
- visually clear
- already active and useful shortly after setup
- impressive without feeling cluttered or confusing
- serious without feeling intimidating

---

## First-Launch Experience

One of the most important product goals is that, after minimal configuration, a user should be able to launch Milodex and very quickly see that it is working in some meaningful capacity.

That does **not** mean the product needs to generate financial return immediately.

It **does** mean the user should quickly be able to see:

- what strategy or system is active
- what Milodex is doing on their behalf
- what data it is using
- what the current state is
- what actions it may take next
- what safeguards or limits are in place

The product should create a sense of immediate functional reality:

> I just set this up, and it is already doing something meaningful for me.

This is especially important because the founder wants the product to feel usable and valuable to people who may not be deeply technical or financially sophisticated.

---

## Audience and Shareability

Milodex is intended to be shareable and portfolio-worthy.

The founder wants it to be something that a person can discover, install, try, and quickly understand. It should not feel like a personal lab experiment that only works on the creator's machine. It should feel like a real product with care behind it.

This matters because Milodex is one of the first things people may see when evaluating the founder as a developer. The project should therefore communicate:

- seriousness
- craftsmanship
- architectural thoughtfulness
- usability
- completeness
- clarity
- execution quality

A hiring manager, peer, or curious friend should be able to explore Milodex and come away thinking that the founder can build and ship substantial systems.

---

## What Milodex Should Prove About the Founder

Milodex should help demonstrate that the founder can:

- use frontier AI tools effectively and responsibly
- architect nontrivial systems rather than just generate snippets
- carry an idea from concept through implementation and documentation
- build credible software in a domain outside his natural expertise
- make difficult systems feel approachable through good product design
- deliver a project that is not only interesting, but functional and shareable

This project should show not just creativity, but delivery.

---

## UX and Documentation Implications

The founder's intent should directly shape product and documentation choices.

### UX implications
Milodex should prioritize:

- low-friction onboarding
- minimal but necessary setup
- quick visible payoff after launch
- clear explanation of current system behavior
- trust-building through transparency
- restraint in information density
- interfaces that feel approachable to less financially literate users

### Documentation implications
Documentation should prioritize:

- clear quick-start guidance
- explanation of what the system is doing and why
- visible architectural intent
- confidence-building language rather than hype
- accessibility for non-experts without dumbing the system down
- a strong sense that this is a real, thoughtfully built product

---

## Product Guardrails

Milodex should **not** become:

- a hype-driven "AI trader" with black-box behavior
- a product framed as handing one's finances to an opaque model
- a product that implies fiduciary status, financial advice, or guaranteed returns
- an overcomplicated system that overwhelms operators
- a product that assumes high financial literacy as a prerequisite
- a technically impressive shell with weak real behavior
- a project that values polish over truth
- a system that makes investing look magical or risk-free
- a harness whose safety gates can be quietly weakened to ship a feature

Milodex **should** become:

- an operator-governed automation harness for investment techniques
- a trustworthy system
- a clearly explained system
- a usable system
- a shareable system
- a polished system
- a system that makes disciplined automated investing feel legible, reviewable, bounded, and controlled
- a system that demonstrates the founder's ability to build something real and substantial

---

## Founder Priority Order

When tradeoffs are necessary, the founder's intended priority order is:

1. **Build something real, functional, and trustworthy**
2. **Demonstrate strong AI-assisted engineering capability**
3. **Make the system accessible and easy to use**
4. **Make it shareable and portfolio-worthy**
5. **Pursue profitability as validation of effectiveness**

This order matters. Milodex should not chase profit at the cost of trust, usability, or credibility.

---

## Canonical Intent Statement

Milodex is an operator-governed automation harness for investment strategies and automated investing techniques. It is an AI-assisted, safety-conscious, shareable software product meant to demonstrate the founder's ability to architect and ship a real, complex, working system in an unfamiliar domain. The harness moves techniques — rules, configs, ML models, agentic systems — through a disciplined lifecycle of research, backtest, evidence review, paper trading, and controlled live exposure, with preview before action, evidence before promotion, risk veto before execution, and manual gates before capital. Risk preferences belong to the operator; risk enforcement belongs to the risk layer; no strategy, model, agent, or feature may modify, weaken, or bypass the risk policy that evaluates it. It should be trustworthy, polished, easy to start, sober and audit-heavy in register, and legible even to operators with limited financial literacy. While financial effectiveness matters as evidence that the system genuinely works, the deeper goal is to build a credible, well-designed platform that makes disciplined automated investing feel legible, reviewable, bounded, and controlled enough that the operator can develop justified trust in the system before allowing it to affect real capital — and that clearly reflects the founder's technical capability, product judgment, and ability to deliver.

---

## Instructions for Future Documentation

Any future documentation for Milodex should remain consistent with this founder intent. In particular:

- Frame Milodex as an **operator-governed automation harness for investment techniques**, not as an "AI trading bot" and not as a product that hands the operator's money to a model.
- Use **"automated investing techniques"** (or "techniques") where the discussion spans rules, configs, ML models, and agentic systems. Reserve **"strategies"** for the specific case.
- Preserve the four-rule safety posture in every layer that touches it: *preview before action, evidence before promotion, risk veto before execution, manual gates before capital.*
- Preserve the operator-vs-enforcement split for the risk layer: **the operator owns risk preferences; the risk layer owns enforcement.** Do not write that "the user controls the risk layer" or that "strategies configure their own risk" — both framings invert the relationship the harness depends on.
- Do **not** imply fiduciary status, financial advice, guaranteed returns, magic-money-machine behavior, or blind delegation of personal finances to an opaque model. Use the "justified trust" framing instead.
- Do not frame Milodex primarily as a finance-learning project.
- Do not frame it primarily as a hype-driven autonomous trading system.
- Hold the editorial / market-desk / audit-heavy tone. Approachability is achieved through legibility, not through softening the safety posture or hiding complexity.
- Emphasize trust, usability, accessibility, clarity, and real functionality.
- Keep the product legible for operators with limited financial literacy without lowering the safety posture.
- Preserve the idea that the system should feel surprisingly easy to start and understand — where "easy" means legible, not lightweight.
- Ensure the project continues to present as portfolio-worthy, polished, and shareable.
- Treat profitability as validation of effectiveness, not as the only measure of success.
- Do not weaken [ADR 0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md), [`docs/BENCH_BOUNDARY.md`](BENCH_BOUNDARY.md), or the human-approval list in [`CLAUDE.md`](../CLAUDE.md) and [`docs/VISION.md`](VISION.md) when writing downstream product or design docs. Those documents are downstream instances of the harness model captured here; this document is upstream of all of them.

When in doubt, future documentation and product decisions should ask:

> Does this help Milodex feel more like a trustworthy, operator-governed harness for automated investing — legible, reviewable, bounded, and controlled — while remaining usable, accessible, and clearly reflective of the founder's ability to build and deliver something real?
