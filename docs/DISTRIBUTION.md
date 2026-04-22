# Distribution and Shareability

Companion to `docs/SRS.md`, `docs/VISION.md` (Priority Rank "Shareability"), and the SRS Phase 2+ "Distributable Installer" appendix. This document defines the product stance Milodex takes when another person — a friend, a curious peer, a potential employer, a technically interested non-expert — installs and runs it: **what the software takes responsibility for, what it clearly refuses, what must appear in onboarding, what the safe-default shipping profile is, how secrets flow, the install-ergonomics ambition, and what must remain openly opinionated** rather than pretend to be universal truth.

The founder's intent (see `docs/FOUNDER_INTENT.md`) is that Milodex feel trustworthy, understandable, and accessible — even to a user with limited financial literacy — and that its credibility come from honesty, not from hype or false universality. Every rule below serves that.

This stance applies even before the Phase 2 installer exists, because **shareability is a product posture, not only a packaging milestone**. Phase 1 should already feel intentionally shareable rather than personal and fragile.

---

## Software Responsibilities — What Milodex Owns

When a friend runs Milodex, the software takes responsibility for being:

- **clear** — the user can tell what is active and what it is doing
- **conservative** — defaults favor safety over performance
- **transparent** — assumptions, warnings, and blockers are visible
- **safety-conscious** — consequential actions require explicit approval

Concretely, Milodex helps the user understand:

- which strategy is active
- what the system is currently doing
- what assumptions it is using
- what risks or blockers exist

And it enforces, as software behavior:

- safe defaults (see "Safe-Default Shipping Profile" below)
- explicit approvals for consequential actions (per `docs/CLI_UX.md` "Confirmation Prompts vs Non-Interactive")
- strong auditability (per `docs/OPERATIONS.md` audit-record contracts and R-XC-008)

---

## What Milodex Clearly Refuses

The software must not, and any surface (CLI, docs, marketing text, future GUI) must not cause it to appear to:

- **present itself as financial advice**
- **imply guaranteed returns or safety**
- **hide uncertainty or weak evidence** (per `docs/REPORTING.md` "How Milodex Presents Uncertainty")
- **allow risky modes by default**
- **pretend the founder's preferences are universal best practices** (see "Openly Opinionated" below)
- **silently take live-capital actions without explicit user consent and setup**

Milodex acts like a **disciplined tool, not an all-knowing investing authority**. When a surface is tempted to speak with more confidence than the evidence justifies, it defers to the uncertainty vocabulary in `docs/REPORTING.md`.

---

## Onboarding Warnings

Before a new user can run Milodex, onboarding must clearly warn that:

- Milodex is a personal, opinionated system, **not licensed financial advice**.
- Past backtest or paper results **do not guarantee** future performance.
- The system may contain bugs, assumptions, or model errors.
- Paper behavior and live behavior can diverge.
- The user is responsible for understanding and approving any live-capital use.
- Defaults are conservative, but **not risk-free**.
- Users should begin with paper trading and review outputs before trusting the system further.

**Tone:** serious and plainspoken. The warnings build trust by being honest — not by sounding legalistic or alarmist. A wall of legal boilerplate is as bad as no warning at all; both fail the honesty test.

The onboarding warning surface is a normative text asset (lives in docs and in the first-run onboarding flow). Changes to its meaning require an ADR under the standard applied in `docs/ENGINEERING_STANDARDS.md` "ADR Threshold" — it is part of the operator contract.

---

## Safe-Default Shipping Profile

When Milodex ships to another person (Phase 2+), the defaults must be the **most conservative, beginner-friendly set possible**:

- **paper trading by default**
- **no live trading enabled by default**
- **fixed curated universe** (see `configs/universe_phase1_v1.yaml` as the Phase 1 reference)
- **conservative position sizing** — matches `sizing.per_position_target_pct` in `configs/risk_defaults.yaml` (10%)
- **capped portfolio exposure** — `portfolio.max_total_exposure_pct` at 50%
- **explicit kill switches enabled** — both strategy-level and account-level per R-EXE-014
- **preview-before-commit workflow** — every consequential command per R-CLI-018
- **strong logging and auditability enabled** — SQLite event store, explanation records
- **clear status and warning surfaces** — per `docs/CLI_UX.md` and `docs/REPORTING.md`
- **no advanced customization required for first use**

Shipped defaults help someone get started safely and understand the system quickly. They are not tuned to maximize returns or flexibility on day one. A user who wants more risk or more customization must deliberately opt in — that deliberate act is itself part of the trust contract.

---

## Secrets and Config Flow for a Shared Installer

### Acceptable patterns

- **environment variables** (the current `.env` + `.env.example` pattern)
- **local config files** excluded from version control (`.env`, user-specific overrides)
- **setup wizards or prompts** that write to local secure config locations
- **explicit test / validation steps** for broker and data credentials before first use (a "health check before run" step)

### Unacceptable patterns

- shipping real secrets (in code, in examples, in templates, anywhere)
- storing secrets in logs, exports, or example files
- requiring users to hand-edit obscure internal files without guidance
- mixing demo defaults and real credentials in confusing ways (e.g., an example file that ambiguously looks like the real one)

### Principle

The setup flow must make it **obvious what is required, what is optional, and what is sensitive**. A user should never be uncertain about whether a field they are filling in will be transmitted, cached, or exposed. The secrets-handling discipline in R-XC-001 extends to any future installer or wizard — secrets never appear in example files, logs, error messages, or export payloads.

---

## Install Ergonomics — Ambition vs Phase 1

The eventual goal is a **near one-click or very low-friction install experience**, because shareability and first impressions matter to the project's goals (see `docs/FOUNDER_INTENT.md` "Audience and Shareability").

For Phase 1, however, a **developer-oriented but very well-documented setup is sufficient**, provided it is:

- clean
- fast
- realistic for another person to follow without guesswork

Phase 1 install polish is not a goal; Phase 1 install *honesty* is. A README that tells the truth about what is needed and how long it takes beats a polished wizard that obscures actual requirements.

The key: even before a full installer exists, the project should already feel **intentionally shareable rather than personal and fragile**. A friend cloning the repo should not encounter hand-rolled path hacks, undocumented environment assumptions, or broken first-run flows.

---

## Openly Opinionated — What Not to Universalize

Milodex remains **openly personal and opinionated** in the areas below. These are the founder's deliberate choices, not claimed universal truth. Presenting them honestly as one reasonable worldview makes the product more credible, not less.

- strategy-family selection and ordering (mean reversion first, then momentum, then breakout — per `docs/strategy-families.md`)
- promotion thresholds and evidence standards (Sharpe ≥ 0.5, max drawdown ≤ 15%, ≥ 30 trades — per R-PRM-004)
- risk tolerance defaults (10% / 50% / 20% sector — per `docs/RISK_POLICY.md`)
- curated universe choices (Phase 1 universe in `configs/universe_phase1_v1.yaml`)
- reporting preferences and trust-summary design
- UX emphasis on clarity over density
- governance style and approval philosophy (per `docs/PROMOTION_GOVERNANCE.md`)

**Anti-rule:** no surface — CLI help, README, docs, future GUI — may claim or imply that these choices are the objectively correct way to operate a trading system. Each may be framed as "Milodex's default" or "the founder's chosen discipline," never as "the right answer." A user is entitled to disagree and override — but must do so deliberately.

---

## Relationship to Other Docs and SRS

- `docs/FOUNDER_INTENT.md` — the product posture that motivates every rule here.
- `docs/RISK_POLICY.md`, `docs/PROMOTION_GOVERNANCE.md`, `docs/OPERATIONS.md`, `docs/CLI_UX.md`, `docs/REPORTING.md` — own the concrete defaults and surfaces referenced by the safe-default profile and the "what Milodex refuses" section.
- `docs/ENGINEERING_STANDARDS.md` — the ADR threshold applied to onboarding warnings and the scaffolded-vs-implemented discipline that keeps install honesty enforceable.
- SRS Phase 2+ "Distributable Installer" appendix — carries the packaging milestone. Requirements R-XC-017 and R-XC-018 below pull the shareability posture into Phase 1 so it isn't deferred until the installer lands.
