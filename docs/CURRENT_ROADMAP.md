# Milodex — Current Roadmap (Control Tower)

> **What this is.** One canonical, living control-tower roadmap for the path from
> *current state* through **trust closure**. It is a control tower, not an issue
> tracker or an implementation plan: it sequences outcomes, keeps exactly one
> critical-path milestone active, uses observable evidence gates, and updates only
> at gate completion or formal gate invalidation. Day-to-day progress lives in PRs,
> ADRs, execution plans, and decision briefs — not here.
>
> **Authority.** This file is descriptive/planning prose (authority rank 4 per
> [`adr/README.md`](adr/README.md) Document Authority Order: ADRs → SRS/specs →
> config schemas → planning prose → brainstorm/history). It **adjudicates and links**
> sources; it does not override [`FOUNDER_INTENT.md`](FOUNDER_INTENT.md),
> [`VISION.md`](VISION.md), the SRS, or any ADR. Where it records a policy conflict,
> the conflict is routed to a decision gate — it is never silently resolved here.

---

## 1. Charter and non-goals

**Charter.** Milodex's mechanics work but its deployed-fleet execution paths are
currently blocked (see §2). The harness can *evaluate, monitor, and explain* — but on
2026-06-22 a full-day live fire proved the **deployed cohort could not submit a single
order**: the daily fleet is structurally blocked, and the deployed non-SPY intraday
replicas were unfrozen. *(A frozen-SPY-intraday-from-open path is structurally open but
was not exercised — mid-session launch.)* This roadmap drives the system from that
state to **trust closure**: the point at which a
deliberately authorized paper cohort transacts and explains itself end-to-end, the
lifecycle is correct and unambiguous across launch/close/reopen/recovery, research
evidence is current and honest, operator-visible trust is truthful, recovery is
drilled, governance boundaries are intact, and **every completion claim is backed by
observable verification** — with the repository and runtime left clean.

The founder bar (from [`FOUNDER_INTENT.md`](FOUNDER_INTENT.md) and
[`GRILL_DECISIONS_2026-06-18.md`](GRILL_DECISIONS_2026-06-18.md)): **trust over
profit.** Finding a profitable strategy is **not** a trust-closure requirement.
Priority rank (VISION): research-OS > trading-tool > showcase. Excellence order:
*evaluate > monitor > execute > discover*. Bias: **mechanics before UI**.

**Milestone labels are `M0`, `M1`, …** — deliberately *not* the historical
product-phase numbers (Phases 1–5 closed, Phase 6 open). Do not conflate them.

**Non-goals (explicitly excluded from this roadmap):**

- **Real-capital expansion.** Paper-only (ADR 0004); micro_live/live stay locked
  (ADR 0042). No milestone here moves real money.
- **Post-trust product expansion** — crypto universe, ML/LLM decision layer,
  Milodex Score / durable-P/L formula, auto-discovered universes, capital allocator.
  Recorded in §10, not sequenced.
- **Unrelated speculative architecture** — god-file splits, frontend-framework
  switch (PyWebView), broad refactors. §10.
- **Cleanup that does not serve the active milestone** (§6 touch-it rule).
- **A PR-by-PR implementation plan.** Only the active milestone is detailed enough
  to seed a later plan; downstream milestones stay intentionally coarse.

---

## 2. Current-state banner

*Factual-currency correction applied 2026-07-02 (D-1 resolution + live-fire state);
M1 gate remains open — this is not a gate update.*

| Field | Value |
|---|---|
| **As-of** | 2026-07-02 (factual-currency correction; see note below) |
| **Commit examined** | `11e444b` on `master` (**= `origin/master`**, working tree clean) |
| **master vs origin** | in sync — `11e444b` on both. *(Superseded: the prior banner's `4a6798e` / 6-ahead-unpushed state no longer exists.)* |
| **Second worktree** | none active for this roadmap surface. *(Superseded: `C:/Users/zdm80/milodex-reqs-wt` was on an already-merged branch — stale, cleanup-only.)* |
| **Last verified gate (this roadmap)** | M0 **CLOSED 2026-06-22** (retrospective §11). Most recent *product-phase* closure: Phase 5 ([ADR 0038](adr/0038-phase-5-is-closed-and-phase-6-may-open.md)); Phase 6 (Bench/operator surfaces) open. |
| **Active milestone** | **M1 — Executable paper-fleet truth** (the keystone milestone) — **ACTIVE, gate not yet met.** D-1 (daily-execution fork) **RESOLVED**: Option A queue-at-open, shipped [ADR 0057](adr/0057-daily-execution-queue-at-open.md) (#289/#290/#291). Close-side half-proof observed 2026-07-02 (6/6 daily paper runners, clean full session, 8 queued intents locked in at close, `market_closed` veto intact). Open-side (drain → submit → observed fill) **unproven** — next-open relaunch 2026-07-06 is the planned M1 gate event. |

**Current blockers (code-confirmed at HEAD):**

1. **[SUPERSEDED for daily; intraday unchanged]** As tested 2026-06-22, the deployed
   cohort had no working submit path. **Daily is no longer blocked this way**: D-1
   resolved as queue-at-open ([ADR 0057](adr/0057-daily-execution-queue-at-open.md),
   #289/#290/#291) — a daily runner persists a queued intent at close
   ([`strategies/runner.py:415`](../src/milodex/strategies/runner.py)), drains it
   during market hours from the live loop
   ([`runner.py:283-289`](../src/milodex/strategies/runner.py) →
   `_drain_queued_intents` `runner.py:981`), and submits through the execution
   chokepoint with the full risk battery + an atomic CAS
   (`event_store.consume_queued_intent_and_append_attempt`). The `market_closed` veto
   ([`risk/evaluator.py:437-447`](../src/milodex/risk/evaluator.py)) is **not**
   relaxed — doctrine intact. Close-side proof observed 2026-07-02 (see §2 banner);
   open-side (drain → submit → fill) is the M1 gate event, not yet proven. The
   deployed intraday Phase-2 candidates remain vetoed `no_frozen_manifest`
   ([`risk/evaluator.py:321`](../src/milodex/risk/evaluator.py)) — 16 non-SPY ETF
   replicas carry `stage: paper` but were never `promotion freeze`d. The 5 **frozen**
   SPY intraday canaries are a structurally-open path that produced no fill only
   because the session launched mid-day (open-anchored entries dead; rsi2 locked out
   of already-dipped symbols) — **unexercised, not severed**.
2. **[SUPERSEDED]** As of 2026-07-02, `master` is in sync with `origin/master`
   (`11e444b`), the working tree is clean, and the 4 previously-untracked files are
   committed; the second worktree (`C:/Users/zdm80/milodex-reqs-wt`) is on an
   already-merged branch — stale, cleanup-only. Residual non-blocking debt: 4
   unmerged local branches with real work (`chore/dep-upper-bounds`,
   `chore/dep-upper-bounds-batch2`, `docs/doc-debt-closeout`,
   `fix/ruff-i001-gap-continuation`), ~13 stale merged-but-undeleted local branches,
   ~13 `worktree-wf_*` scratch branches, and one untracked file
   (`docs/RESUME_EXTRACT.md`).

**Pending high-impact / uncertain decisions (await the §8 decision-pause protocol):**

- **D-1 Daily-execution fork — RESOLVED.** Decided: Option A, queue-at-open
  ([ADR 0057](adr/0057-daily-execution-queue-at-open.md), #289/#290/#291). Close-side
  half-proof observed 2026-07-02 (clean full session, 8 intents queued at close,
  `market_closed` veto intact); open-side (drain → submit → fill) unproven — the
  2026-07-06 pre-open relaunch is the planned M1 gate event. *Keystone.*
- **D-2 Intraday freeze governance** — whether/which of the 16 unfrozen non-SPY ETF
  replicas to `promotion freeze` (prerequisite for any intraday fill).
- **D-3 Auto-launch vs ADR 0012** — a from-open launch is required for intraday to
  behave as designed, but none exists and [ADR 0012](adr/0012-runtime-and-dual-stop.md)
  forbids a daemon/scheduler in Phase 1. Manual pre-open deploy is the interim.
- **D-4 Lifecycle-proof operational gate — DECIDED (ADR 0058).** "Split now,
  enforce at M4": `--lifecycle-exempt` is scoped to policy-listed lifecycle-proof
  ids (`policy.py` `applies_to`); a separate `--operator-override` (paper-only,
  reasoned, `promotion_type='operator_override'`) is the honest general bypass;
  the three R-PRM-004 criteria are recorded-as-unenforced with enforcement
  deferred to M4. `check_gate` and the risk layer untouched.
  ([ADR 0058](adr/0058-lifecycle-exemption-is-scoped-and-operator-override-is-split.md))
- **D-5 Evidence-durability labeling stance** — every intraday verdict is
  structurally non-durable on IEX by policy; "closeable" must mean *honestly-labeled
  exploratory verdicts exist*, not *promotion-grade edge*. Lock the definition.
- **D-6 Coverage floor for closure — CLOSED (decided at M0).** Founder chose a
  targeted critical-obligation assurance gate with a versioned allowlist (see §11);
  not a bare coverage percentage.
- **D-7 `docs/troubleshooting-fault-modes` branch adjudication — CLOSED.** Branch
  merged as #281 (+134-line additive doc). The "~447 unrelated test deletions" framing
  was stale/unreproducible — corrected here; content consumed in M4.
- **D-8 Evidence-reconstruction sufficiency** — is *honest non-authoritative
  labeling* enough for closure, or does closure require authoritative event-derived
  evidence reconstruction ([ADR 0050](adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md)
  Decision 8)? Product-defining for a research-OS-first system; the GUI audit's #1
  trust gap. Decided via the protocol — not by roadmap prose.
- **D-9 Manual emergency-halt governance** — a production manual kill-switch **TRIP**
  affordance is **explicitly not authorized** by
  [ADR 0051](adr/0051-bench-command-infrastructure-v1.md) §Non-goals (line 286) or
  [ADR 0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md)
  Decision 4 (the kill switch's home, the Anchor view, was deleted). Requires
  independent review + an ADR amendment before any implementation — it is **not**
  "just reachability."

**Known limitations in this status assessment (read honestly):**

- **[SUPERSEDED]** The original "no `git fetch` performed" caveat applied to the
  2026-06-22 assessment at `51e470f`/`4a6798e`. This correction (2026-07-02) did
  `git fetch` and confirmed local `master` (`11e444b`) = `origin/master`; the
  underlying content clusters were not re-audited file-by-file beyond the specific
  claims corrected in this pass.
- **Reliability evidence is idle-stability only.** The clean ~5h soak (2026-06-22)
  was a **mid-session launch that produced zero fills**. From-open launch and the
  concurrent same-symbol submit path (#270/#262) are **un-exercised** — do not let
  "5h clean" accrue as evidence of execution truth.
- **Some 2026-06-10 GUI-wiring-audit items (P1-2 promote-born-`data_stale`, P2-1
  stop-during-kill-switch, P2-2 GUI reconcile) were flagged by one reader as
  unresolved, but the 2026-06-10 hardening execution log records HR-5/HR-9/HR-10 as
  merged.** Treated here as **needs re-confirmation on entry** (M2), not as
  confirmed-broken.

---

## 3. Source-adjudication ledger

Adjudicated against code/configs at `51e470f` by six independent code-grounded
auditors plus an independent sequencing review. Disposition vocabulary: *adopted /
partially-adopted / superseded / rejected / deferred*. This ledger **links and
adjudicates** — it does not restate source content.

### 3.1 Normative anchors (upstream of everything)

| Document | Type | Disposition | Rationale → absorbed by |
|---|---|---|---|
| [FOUNDER_INTENT.md](FOUNDER_INTENT.md) | canonical | adopted | The "why": harness-not-bot, justified trust, operator-owns-preferences/risk-owns-enforcement. Frames the whole charter. |
| [VISION.md](VISION.md) | canonical | adopted | What/in-what-order; Autonomy Boundary; "evaluate>monitor>execute>discover"; risk layer = highest-stakes assumption. |
| [PRODUCT.md](PRODUCT.md) · [README.md](README.md) · [adr/README.md](adr/README.md) | canonical | adopted | Compass + doc map + Document Authority Order. No drift found. *(Limitation: this roadmap is not yet registered in the README map — see M0.)* |
| [SRS.md](SRS.md) | canonical | adopted | Agrees with code (R-EXE-015(f)/016 carry DC-1; R-OPS-001 does **not** claim daily executes — SRS is *clean* on the daily gap). |
| [GRILL_DECISIONS_2026-06-18.md](GRILL_DECISIONS_2026-06-18.md) | conversation record (authority ~5) | partially-adopted | High-signal founder *direction* (intraday lane, continuity, close/quit, runner lifecycle policy, Bench-as-control-surface) but **not binding until folded into ADR/SRS**. Direction the roadmap sequences; Score/durable-P/L formulas DEFERRED (§10). |

### 3.2 Governance (ADRs) — sacred-path & promotion

| Document | Disposition | Rationale → absorbed by |
|---|---|---|
| ADR [0008](adr/0008-risk-layer-veto-architecture.md)/[0009](adr/0009-promotion-pipeline-stage-model.md)/[0005](adr/0005-kill-switch-manual-reset.md)/[0012](adr/0012-runtime-and-dual-stop.md)/[0030](adr/0030-backtest-is-exploratory-manifest-binds-at-paper-plus.md)/[0056](adr/0056-cross-process-submit-serialization-per-account-advisory-lock.md) | adopted | Sacred path **verified intact**: single chokepoint (`execution/service.py:357`, reached only after risk eval), manual-reset kill switch (no auto-resume), stage enforced at risk layer, per-account submit serialization (fail-closed) for paper/micro_live/live. → governance integrity (M1/M6). |
| ADR [0026](adr/0026-concurrent-multi-strategy-uses-per-process-supervisor.md) | adopted | Same-symbol co-run now allowed; three invariants close it (ADR 0056 lock / opposite-side veto / ADR 0055 ledger cap). Internally current via 2026-06-15 addendum. |
| ADR [0054](adr/0054-risk-profiles-bounded-operator-preferences.md) | adopted | Risk profiles are runtime overlays; absolute ceilings immutable; do **not** touch evidence/promotion gates (grill-locked separation holds in code). |
| ADR [0052](adr/0052-promotion-policy-is-a-typed-governance-source-of-truth.md) | partially-adopted | Typed two-tier gate is SoT; **lifecycle-proof operational gate is define-only (`enforced=False`)** → **D-4** decision. |
| ADR [0055](adr/0055-event-store-per-strategy-position-ledger.md) | partially-adopted | Per-strategy ledger live, **but ADR body line 285 still says "do not co-run same symbol / code-enforced"** — contradicts current code (guard removed `211d983`). → doc-truth touch-it (M0/M1). |
| ADR [0049](adr/0049-phase-6-bench-v1-is-a-visual-prototype-with-no-backend-mutation.md) | partially-adopted | **Amended in part** by [ADR 0051](adr/0051-bench-command-infrastructure-v1.md) (§1; status "Accepted — amended in part"). The six named action families mutate, but **for every path ADR 0051 did not open, ADR 0049's no-mutation perimeter remains binding.** A planning doc cannot demote it — amended, *not* superseded. |
| ADR [0036](adr/0036-operator-kanban-surface-for-promotion-pipeline.md)/[0044](adr/0044-kanban-uses-cached-hover-validation-and-authoritative-drop-validation.md)/[0045](adr/0045-kanban-responsive-layout-uses-horizontal-board-scroll.md) | superseded | Self-marked superseded; live spec is bench-brief + ADR 0047/0048. |
| ADR [0042](adr/0042-live-and-micro-live-eligibility-is-locked-and-evidence-based.md)/[0043](adr/0043-bench-demotion-actions-open-a-governance-flow.md)/[0047](adr/0047-bench-action-availability-is-the-validation-surface.md)/[0016](adr/0016-phase1-instrument-whitelist.md) | adopted | Capital-stage lock, governance-flow demotion, hide-don't-disable availability, instrument denylist — all match code. |

### 3.3 Live evidence & current-state

| Document | Type | Disposition | Rationale → absorbed by |
|---|---|---|---|
| [architecture/roadmaps/2026-06-22-livefire-findings.md](architecture/roadmaps/2026-06-22-livefire-findings.md) | live-evidence | adopted | **Authoritative live read.** Findings 2/3 (no submit path) line-cites verified; Finding 4 (clean soak) confirmed. → M1 (execution truth) + §2 banner. |
| [architecture/roadmaps/2026-06-10-hardening-roadmap.md](architecture/roadmaps/2026-06-10-hardening-roadmap.md) | historical-plan | partially-adopted | 12/12 executable PRs merged; **DC-2/HR-8 queue-at-open** was the leading **D-1** option — since built and merged ([ADR 0057](adr/0057-daily-execution-queue-at-open.md), #289-#291). HR-4 manual kill-switch trip+reset still un-walked → M4. |
| [architecture/roadmaps/2026-06-21-usage-burn-backlog.md](architecture/roadmaps/2026-06-21-usage-burn-backlog.md) | backlog | partially-adopted | Internal-refactor tiers (A/B/C/D) = maintainability, **deferred** (§10) unless touch-it. **Tier F product-validation (F1 clean-room, F2 lifecycle rehearsal, F3 fault drills) = trust-closure-relevant** → M4. *Note: "now-tier done as 8 PRs" reflects unmerged branches, not master — the file's own item states (UNVERIFIED) govern.* |
| [REQUIREMENTS_COVERAGE.md](REQUIREMENTS_COVERAGE.md) | generated | partially-adopted | Content current; stamp lags one commit; 29% coverage is the live backdrop for the in-flight reqs-traceability work. → **D-6** + parallel verification. |
| [LAUNCH_READINESS.md](LAUNCH_READINESS.md) | frozen snapshot (2026-05-14) | superseded | PASS verdicts pinned to `fee27fe`; §7 outcome record blank/never-tagged; 5 MANUAL-REQUIRED items genuinely unverified. → M4 (drills) + relabel touch-it. |
| [KNOWN_FLAKY_TESTS.md](KNOWN_FLAKY_TESTS.md) | living reference | adopted | Current; one quarantined showcase test (the lone expected SKIP). Keep current. |

### 3.4 Intraday research-evidence lane

| Document | Disposition | Rationale → absorbed by |
|---|---|---|
| [INTRADAY_ETF_EVIDENCE_PHASE2_COMPLETE.md](INTRADAY_ETF_EVIDENCE_PHASE2_COMPLETE.md) | adopted | Honest, self-correcting; Phase 2 merged; **row-2/2.47 evidence SUPERSEDED, fresh 68-cell rerun PENDING** (experiment_registry has **0 rows**). → M3. |
| [INTRADAY_ETF_EVIDENCE_HARDENING.md](INTRADAY_ETF_EVIDENCE_HARDENING.md) · [_FEEDBACK.md](INTRADAY_ETF_EVIDENCE_HARDENING_FEEDBACK.md) · [_LEAN_SLICE_BUILD.md](INTRADAY_ETF_EVIDENCE_LEAN_SLICE_BUILD.md) · [_PHASE2_ORCHESTRATION_BRIEF.md](INTRADAY_ETF_EVIDENCE_PHASE2_ORCHESTRATION_BRIEF.md) | adopted | Strategy memo + reviews + lean-slice + plan — all realized in merged code; consumed by the completion record. → M3 (lane mechanically complete). |
| [INTRADAY_ETF_EVIDENCE_PHASE2_TIER1_GATE.md](INTRADAY_ETF_EVIDENCE_PHASE2_TIER1_GATE.md) · [reviews/working_lane_evidence_phase2.md](reviews/working_lane_evidence_phase2.md) | superseded | Pre-RTH-lifecycle evidence; explicitly superseded; not persisted. Audit history only. |
| [reviews/2026-06-20-intraday-etf-evidence-phase2-external-review-handoff.md](reviews/2026-06-20-intraday-etf-evidence-phase2-external-review-handoff.md) | adopted | Caught the overnight-null BLOCKER + 4 MAJOR; all fixes in HEAD. Methodology-correctness precedent. |
| [STRATEGY_BANK.md](STRATEGY_BANK.md) | partially-adopted | Roster accurate (11 frozen), **but the "at most 3 run at once" concurrency section is STALE** (guard removed `211d983`). → strategy-bank-truth touch-it (M1/M2). |

### 3.5 GUI / operator-visible trust

| Document | Disposition | Rationale → absorbed by |
|---|---|---|
| [GUI_AUDIT_2026-06-22.md](GUI_AUDIT_2026-06-22.md) | adopted | Findings ground out in code: evidence non-authoritative, no veto-reason, phantom-vs-count headline, no manual halt, no archetype distinction. → M2. |
| [BENCH_BOUNDARY.md](BENCH_BOUNDARY.md) · [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) · [DESIGN.md](DESIGN.md) | adopted | Current Bench-boundary + design canon; stable foundation. |
| [PHASE6_BENCH_PREP.md](PHASE6_BENCH_PREP.md) | partially-superseded | Its "no backend mutation" plan is overtaken **only** for the 6 action families [ADR 0051](adr/0051-bench-command-infrastructure-v1.md) opened; ADR 0049's perimeter still binds the rest. Add a forward-pointer; do not treat as fully dead. → status touch-it (M2). |
| ADR [0050](adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md) | partially-adopted | Schema axes adopted; **real event-derived reconstruction deferred (Decision 8)** = the #1 operator-trust gap. Honest *labeling* → M2; authoritative *reconstruction* → §10. |
| [FRONTEND_FRAMEWORK_AUDIT_2026-06-20.md](FRONTEND_FRAMEWORK_AUDIT_2026-06-20.md) | deferred | Mild-lean-to-stay on QML; switch-trigger (view-layer AI bottleneck) unmet. → §10. |
| [reviews/2026-06-10-gui-wiring-audit.md](reviews/2026-06-10-gui-wiring-audit.md) | partially-adopted | Kill-switch reset reachability FIXED; P1-2/P2-1/P2-2 status = re-confirm on M2 entry. |

### 3.6 Operations / recovery

| Document | Disposition | Rationale → absorbed by |
|---|---|---|
| [OPERATIONS.md](OPERATIONS.md) | partially-adopted | **Governance-integrity gap:** `:135` presents post-close daily evaluation as a working path, silent on the `market_closed` submit veto → a reader concludes daily executes. → holding correction in M0, authoritative fix in M1. |
| [PAPER_WORKFLOW.md](PAPER_WORKFLOW.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md) · [RISK_POLICY.md](RISK_POLICY.md) · [PROMOTION_GOVERNANCE.md](PROMOTION_GOVERNANCE.md) | adopted | Match code. *RISK_POLICY **honestly labels** sector/correlation caps + strategy-level kill switch as "planned — not yet enforced" (`RISK_POLICY.md:26–29, 276–277`) — **not** a doc lie. Genuine open contract: the **strategy-level kill switch is an unimplemented SRS requirement** → requirements adjudication (D-6 area), not casual prose-trim.* |
| [reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md](reviews/2026-05-19-orphan-reconcile-pid-reuse-defect.md) | adopted | Fully remediated (identity-verified reaping). Phantom-reaping closed. |
| [reviews/2026-06-10-runner-process-audit.md](reviews/2026-06-10-runner-process-audit.md) | partially-adopted | Infra solid; **P0-2 daily-cannot-execute STILL OPEN** (re-confirmed live). → M1. |
| [reviews/2026-04-29-runner-startup-investigation.md](reviews/2026-04-29-runner-startup-investigation.md) | superseded | Overtaken by ADR 0055 strategy-scoped ledger. Banner touch-it. |

### 3.7 Historical plans & audits (retired)

| Document | Disposition | Rationale → absorbed by |
|---|---|---|
| [ROADMAP_PHASE1.md](ROADMAP_PHASE1.md), [PHASE2–5_PLANNING.md](PHASE2_PLANNING.md) | superseded | Closed via ADR 0023/0025/0027/0031/0038. Cite-and-retire. → §10 phase-history. |
| [architecture/audits/2026-05-21-deepening-audit.md](architecture/audits/2026-05-21-deepening-audit.md) · [roadmaps/2026-05-21-deepening-roadmap.md](architecture/roadmaps/2026-05-21-deepening-roadmap.md) | adopted (retired) | 11/13 RM done; residuals RM-007/008. → §10 maintainability. |
| [reviews/2026-06-12-architecture-deepening-audit.md](reviews/2026-06-12-architecture-deepening-audit.md) · [reviews/2026-06-13-architecture-audit-second-opinion.md](reviews/2026-06-13-architecture-audit-second-opinion.md) | partially-adopted | Capital-gate concern (submit serialization) **now CLOSED (ADR 0056)**. Resolver dup #7, parity-test #1-4, workflow-readiness #8 **still open** → §10. |
| [reviews/2026-05-29-milodex-truth-and-direction-audit.md](reviews/2026-05-29-milodex-truth-and-direction-audit.md) · [reviews/2026-06-13-documentation-audit.md](reviews/2026-06-13-documentation-audit.md) | partially-adopted | Direction sound. Its "enforcement-overstatement" finding is **partly stale** — RISK_POLICY now labels sector/correlation + strategy-kill-switch as planned/not-enforced. **Genuine residual drift**: `_CHECKS` count (code = 17 at `evaluator.py:99-117`; docs understate 14/16) + `risk_defaults.yaml` "informational only" comment (binding per ADR 0029) → M0 doc-truth. |
| [architecture/2026-05-30-harness-capability-axes.md](architecture/2026-05-30-harness-capability-axes.md) | adopted (living lens) | Capability map, not a plan; axis-3 decision-layer-LLM = the thesis. → §10 deferred index framing. |
| [reviews/2026-05-30-crypto-archetype-proof-slice.md](reviews/2026-05-30-crypto-archetype-proof-slice.md) (+ adversarial) | adopted | Backtest-only proof; crypto data-ingestion prerequisite **still open** → §10. |
| [TEST_EFFICACY_AUDIT.md](TEST_EFFICACY_AUDIT.md) · [reviews/2026-06-12-thermo-nuclear-code-quality-review.md](reviews/2026-06-12-thermo-nuclear-code-quality-review.md) | superseded / adopted (retired) | Frozen mutation snapshot; thermo-nuclear 30 findings adjudicated/resolved (PRs #231–245) with two carries (P2-11, P2-17 crypto cache). |

---

## 4. Trust-closure definition (the target state — observable)

Trust closure is reached when **all** of the following are observably true. *Finding
a profitable strategy is not required.*

**Operational truth.** A deliberately authorized paper cohort completes clean
full-session operation; decisions, vetoes, submissions, **fills**, stops, and
reconciliation are explainable end-to-end; multi-session reliability is demonstrated
(without profitability). *Observable: a named fill event in the event store +
reconciliation diff = 0 — uptime is not a fill.*

**Lifecycle correctness.** Intraday launch/session behavior is explicit and proven
(from-open; clean-evaluation-boundary on late start); the daily post-close execution
contradiction is **resolved safely, or daily is explicitly reclassified as
decision-only** rather than falsely appearing executable; close/quit/reopen/recovery
behavior is unambiguous.

**Current evidence.** Superseded results are replaced by evidence from the corrected
lifecycle/provenance implementation; **at least three price-action hypotheses receive
current verdicts**; rejected and inconclusive outcomes remain successful research
results when well-supported. *Honesty bound: intraday verdicts are structurally
non-durable on IEX — "current verdicts exist and are honestly labeled exploratory"
is closure; "promotion-grade edge" is not, and is not required.*

**Operator-visible trust.** Evidence authority and freshness are truthful; veto
reasons and genuine runner liveness are visible; manual emergency halt is reachable;
canaries, baselines, research candidates, blocked, and evidence-earned-paper
strategies are visibly distinct.

**Recovery proof.** First-launch, controlled-stop, stale/phantom-runner, broker
reconciliation, and representative failure/recovery drills have **observable
acceptance evidence** (an injected fault + "operator sees X, does Y" + durable logs)
— not unit-test coverage alone.

**Governance integrity.** Risk, promotion, broker, and human-approval boundaries
remain intact; source documents and roadmap status agree with current behavior;
verification supports every completion claim.

---

## 5. Milestone spine

Determined **after** source adjudication and reconciled with the independent review.
Only the active milestone (**M0**) is detailed enough to seed an implementation plan;
later milestones are intentionally coarse and firm up only at the prior gate's
retrospective.

```
M0 ─► M1 ─► M2 ──────────► M4 ─► M5 ─► M6
 │   (critical path)
 └─► M3  (parallel; lifecycle/provenance already merged in `ea12cc1` —
          needs only M0 + an isolated evidence/worktree state, NOT M1)
```

**Keystone dependency — RESOLVED.** The **D-1 daily-execution fork** shaped
everything downstream; it is now decided: **queue-at-open**
([ADR 0057](adr/0057-daily-execution-queue-at-open.md), #289/#290/#291). The
rejected alternative, "reclassify daily → decision-only," would have been cheap but
loaded the entire real-fill burden onto intraday (IEX-non-durable, freeze-gated,
from-open-launch-dependent). Close-side half-proof observed 2026-07-02; open-side
proof is the remaining M1 gate event (§2).

---

### M0 — Ground-truth & in-flight reconciliation  · **CLOSED 2026-06-22** (retrospective: §11)

- **Intended outcome.** An honest baseline: every *safety/execution-relevant*
  canonical doc matches HEAD behavior (or is labeled historical); the mid-flight
  repo is triaged so later work merges cleanly; this roadmap is canonical; and the
  pending execution decisions (D-1..D-7) are framed as decision briefs ready for the
  §8 protocol. **No sacred-path code changes.**
- **Why here.** The control-tower premise is "no completion claim trusted without
  verification." Several canonical docs currently *drift from or omit* current
  behavior in safety-relevant ways: OPERATIONS implies daily executes (omits the
  `market_closed` veto); STRATEGY_BANK documents a removed 3-runner co-run guard as
  live; ADR 0055's body still says "do not co-run same symbol"; the `_CHECKS` count is
  understated; a `risk_defaults.yaml` comment is now false. *(Note: sector/correlation
  caps + the strategy-level kill switch are **not** doc lies — RISK_POLICY honestly
  labels them planned/not-enforced; the strategy-level kill switch is a separate
  **unimplemented requirement**, handled as requirements adjudication, not a prose
  fix.)* You cannot decide D-1 on drifted docs, nor reach the closure gate over an
  un-triaged repo. Cheapest, highest-leverage de-risking; precedes the keystone
  decision.
- **Entry evidence.** §2 banner (this roadmap exists; HEAD identified; mid-flight
  state enumerated).
- **In scope.**
  - Triage every in-flight branch + the second worktree: each is *merge-bound*,
    *write-off (decision record)*, or *explicitly parked* — nothing left implicit.
  - Frame **D-7** (the `troubleshooting-fault-modes` branch: separate the +134 doc
    lines from the ~447 test deletions) as a decision brief — **do not merge it here**.
  - Correct **genuine** safety/execution-relevant doc-vs-code drift only:
    OPERATIONS:135 daily-execution disclosure (holding correction); STRATEGY_BANK
    stale concurrency section; ADR 0055 co-run addendum/pointer; ADR 0049 status →
    "amended in part by 0051" (**not** "superseded"); `_CHECKS` count → point-at-code
    (code = 17); CLAUDE.md `--lifecycle-exempt` file:line correction;
    `risk_defaults.yaml` "informational only" comment (now binding per ADR 0029).
  - Do **not** "trim" the sector/correlation or strategy-level-kill-switch text —
    RISK_POLICY already labels them planned/not-enforced. The strategy-level kill
    switch is an **unimplemented SRS requirement**; record it for requirements
    adjudication (D-6 area), do not silently delete the contract.
  - Register this roadmap in the [README.md](README.md) documentation map.
  - Draft + independently review the D-1 and D-2 decision briefs (no founder decision yet).
- **Explicitly out of scope.** Any change to `risk/`, `execution/`, `promotion/`, or
  runner code. Cosmetic/area-wide doc rewrites. Building the queue-at-open feature.
  Freezing any strategy. Closing coverage gaps (parallel verification, not M0).
- **Source claims absorbed.** OPERATIONS doctrine-silence; STRATEGY_BANK + ADR 0055
  + ADR 0049 + PHASE6_BENCH_PREP drift; truth-audit/doc-audit enforcement-overstatement;
  README map gap; usage-burn D2 doc-debt; in-flight repo state.
- **Risk seams.** None to sacred code (doc-only + git triage). The *only* risk is
  scope creep into area-wide doc rewriting — bounded by §6.
- **Expected decision gates.** D-7 brief produced (adjudication, not merge). D-1/D-2
  briefs produced (handed to §8, not resolved in M0).
- **Touch-it cleanup (§6).** All the safety-relevant doc corrections above are
  *prerequisite* correctness fixes (they prevent later mis-decisions). Cosmetic
  copy/banner sweeps are deferred to the milestone that enters that surface.
- **Allowed parallel work.** Requirements-coverage backfill (verification-class,
  independent); historical-doc banners; the cosmetic doc-debt sub-items not in scope above.
- **Exit criteria (observable).**
  1. Every local branch + the second worktree has a recorded disposition (merge /
     write-off / park) in a decision record; **D-7** (the +134-doc / ~447-deletion
     branch) is adjudicated, not merged blindly.
  2. The listed *genuine* safety/execution-relevant doc-vs-code drifts are corrected
     and re-grepped clean against HEAD.
  3. `CURRENT_ROADMAP.md` is linked from the README map.
  4. **D-1** and **D-2** decision briefs exist and have been independently reviewed;
     **D-6** (closure coverage-floor scope) is decided so the parallel verification
     track knows its target. Remaining decisions are owned per the §8 decision map.
- **Required verification.** `git` branch/worktree inventory matches the decision
  record; targeted re-grep of each corrected doc against the cited code line; full
  `python -m pytest` + `ruff` green (the "1 skipped" showcase test is expected, not a
  failure) to confirm doc-only changes regressed nothing.
- **Invalidation / reopen conditions.** A new in-flight branch or worktree appears
  un-triaged; a corrected doc is found to still contradict code; `origin/master`
  turns out to have advanced (re-baseline on next sync).

---

### M1 — Executable paper-fleet truth  · **ACTIVE** *(keystone milestone; opened 2026-06-22)*

- **Intended outcome.** A deliberately authorized cohort completes one **clean
  full-session-from-open** run in which an order actually **fills**, and every step
  (decision → veto → submit → fill → stop → reconcile) is explainable.
- **Why here.** This is the floor of "operational truth." Until something fills, a
  trust roadmap is closing around an idle corpse, and the OPERATIONS doctrine-silence
  proves the non-executing state actively misleads.
- **Coarse scope.** Resolve **D-1** (daily fork) and **D-2** (intraday freeze) via
  §8; implement the chosen path; if intraday is the fill path, **D-3** (from-open
  launch vs ADR 0012) and the freeze land here. **D-4** (lifecycle gate) is decided
  here because freezing/promoting is the act it governs.
- **Risk seams.** The `market_closed` veto, the manifest gate, submit serialization,
  the runner cadence — **the sacred layer**. Every diff here goes through
  `risk-invariant-reviewer`. **Any PR touching `_check_market_open` without a
  preceding D-1 ADR is a doctrine violation.**
- **Exit criteria (observable, refined by independent review).** A **named fill event
  in the event store** for an authorized cohort over one clean full session, with
  reconciliation diff = 0 and the full decision→fill→stop chain reconstructable from
  `explanations`. *Uptime is explicitly not sufficient.* **Plus D-1 branch-specific
  proof** (a single intraday fill must not mask an untested daily policy): *queue-at-open*
  → prove lock-in-at-close + next-open submission + the mandatory morning re-validation
  pass; *decision-only* → prove every execution claim/action for 1D is removed or
  relabeled (incl. OPERATIONS + the GUI archetype); *relax-market-hours* → prove the
  accepted post-close-submit safety behavior under risk-invariant review.
- **Conditions that would invalidate.** "Clean soak, zero fills" being offered as the
  gate; an inline `market_closed` carve-out shipped without the D-1 ADR.

### M2 — Operator-visible execution truth  *(coarse)*

- **Outcome.** On a fleet that now transacts: veto-reason visible; aggregate liveness
  counts only PID-verified runners; evidence authority/freshness shown truthfully
  (honest *labeling*); canaries/baselines/research/blocked/paper visibly distinct.
  **Strictly after M1** (mechanics before UI; the archetype taxonomy cannot be
  finalized until the D-1 fork decides whether "decision-only" is an archetype).
  Re-confirm GUI-wiring P1-2/P2-1/P2-2 on entry.
- **Decision-gated within M2.** (a) **Manual emergency-halt (D-9):** a production
  kill-switch TRIP is **not** authorized by ADR 0051 §Non-goals / ADR 0049 Decision 4
  (and the Anchor view that hosted it was deleted), so it needs an ADR amendment
  before build — *not* "just reachability." (b) **Evidence reconstruction (D-8):**
  whether honest non-authoritative *labeling* satisfies closure, or authoritative
  event-derived reconstruction (ADR 0050 v2) is required, is decided before M2's
  operator-trust gate can close.

### M3 — Current research verdicts  *(parallel-eligible after M0; not gated by M1)*

- **Outcome.** ≥3 price-action hypotheses get **current** verdicts from the corrected
  lifecycle/provenance path, persisted to `experiment_registry` (currently 0 rows),
  each honestly stamped IEX-exploratory / non-durable. Rejected/inconclusive count as
  success. **Parallel lane** (backtest/evidence, not live-fleet/GUI). The lifecycle/
  provenance correction (final-bar RTH flatten + `position_lifecycle`) **already
  merged in `ea12cc1`**, so M3 depends only on **M0** (repo/evidence-state
  reconciliation) + an isolated worktree/scratch evidence state — **not on M1**.
  **D-5** (durability labeling) decided here.

### M4 — Recovery & failure-mode proof  *(coarse)*

- **Outcome.** A drill matrix with PASS evidence for: stale market data, locked/
  corrupt SQLite DB, broker outage/API error, dead/wedged runner; plus first-launch/
  clean-room, controlled-stop-on-wedged, and kill-switch trip→reset — each an injected
  fault + observable operator-facing message + durable log (unit-test coverage is not
  sufficient). Absorbs usage-burn F1/F2/F3 and the D-7-adjudicated fault-mode docs.

### M5 — Continuity, shutdown & multi-session reliability  *(coarse)*

- **Outcome.** Continuity-check-on-reopen panel + quit/close confirmation with active
  runners (founder-locked, currently unbuilt); **and** multi-session/from-open
  reliability demonstrated with the concurrent same-symbol submit path (#270/#262)
  exercised (elapsed-time evidence, parallel-eligible). Closes the lifecycle-across-
  sessions half of "operational truth."

### M6 — Final repository & operational closure  *(coarse; see §12)*

- **Outcome.** Nothing in flight, nothing orphaned, nothing dependent on an agent's
  memory. Full criteria in §12.

---

## 6. Risk-based touch-it rule

When a milestone enters an area with known cleanup:

- **Prerequisite** when the cleanup is correctness / safety / evidence-validity /
  likely-rework (e.g. M0's safety-relevant doc-vs-code drift; M1 re-grounding the
  risk-layer claims it is about to change).
- **Separate task within the same milestone** when it is maintainability cleanup in
  code already being changed (e.g. consolidating a duplicated helper you are editing).
- **Deferred** when merely adjacent or disproportionately large (e.g. god-file splits,
  area-wide refactors — §10).

This rule is **not** a license for area-wide refactoring. Test: if a reviewer asks
"why did this line change," the answer must be the active milestone's outcome.

---

## 7. Parallel-work policy

Only **one** critical-path milestone is active. Parallel work is allowed **only** for
independently verifiable cleanup, verification, documentation, or paper-soak /
elapsed-time evidence — and must be independent at **file, semantic, runtime-state,
and evidence** levels, not merely titled differently.

Currently sanctioned parallel tracks: requirements-coverage backfill (verification);
historical-doc banners (documentation); **M3 research verdicts** once M1's lifecycle
fix merges (separate lane); multi-session paper soak (elapsed-time). The maintainability
deferred themes (§10) are parallel-eligible **only** if they do not touch the risk/
execution seam an active milestone is changing — otherwise they collapse into a
same-milestone touch-it task.

---

## 8. Decision-pause protocol

Any high-impact or materially uncertain decision pauses progress: risk policy,
promotion governance, broker/execution behavior, evidence methodology, major
architectural boundaries, irreversible state, material scope change, or operational
cleanup that may discard valuable state.

**Flow:** (1) primary agent defines the decision, current evidence, options,
trade-offs, risks, recommendation → (2) independent subagent reviews a *neutral*
framing and is explicitly asked to dissent → (3) primary reconciles both → (4) founder
receives a concise decision brief with a recommended choice → (5) founder decides →
(6) decision recorded in the appropriate ADR / decision record → (7) roadmap
incorporates it at gate completion or formal gate invalidation. **Do not dump raw
complexity on the founder before the independent review.**

**Currently paused (require the protocol): D-2, D-3, D-5, D-8, D-9** (§2). *(D-1,
D-4, D-6, D-7 resolved/closed as of 2026-07-02 — see §2.)*

**Not high-impact — just do it (surgical, no pause):** veto-reason surfacing
(read-only of an existing field); aggregate-liveness correctness fix; evidence
authority/freshness *labeling* honesty — **showing** `authoritative=False` truthfully
(distinct from D-8, *whether that suffices for closure*, and from building
reconstruction, which is large → §10); quit/close + continuity panels (founder
already *locked* the decision); M0 genuine-drift doc corrections (documenting reality
is not a decision). *Trap to avoid: pausing on cheap reversible UI while failing to
pause on genuinely high-impact decisions like the manual-halt TRIP (**D-9** — it
reads "just reachability" but is ADR-forbidden today). (D-1 and D-7 were this trap's
original examples; both are now resolved — see §2.)*

**Decision-ownership map** (every queued decision has exactly one owning gate):

| Decision | Framed at | Decided at |
|---|---|---|
| D-1 daily-execution fork | M0 | M1 |
| D-2 intraday freeze | M0 | M1 |
| D-3 auto-launch vs ADR 0012 | M1 | M1 (or M5 if reliability defers it) |
| D-4 lifecycle-proof gate enforce-vs-document | M1 | M1 — decided 2026-07-02 ([ADR 0058](adr/0058-lifecycle-exemption-is-scoped-and-operator-override-is-split.md)) |
| D-5 evidence-durability labeling | M3 | M3 |
| D-6 closure coverage-floor scope | M0 | M0 |
| D-7 fault-modes branch adjudication | M0 | M0 (content consumed in M4) |
| D-8 evidence-reconstruction sufficiency | M1/M2 | M2 |
| D-9 manual emergency-halt governance (ADR amendment) | M2 | M2 |

---

## 9. Roadmap update policy

This roadmap is **not** a live task board. Update its state **only** when a milestone
gate completes, or when new evidence formally invalidates/reopens a gate. At gate
completion, review the whole milestone retrospectively (§11) and update from total
evidence. Gate retrospectives are **append-only**. Day-to-day progress, commits, task
status, and mid-gate decisions belong in execution plans, PRs, issues, ADRs, or
decision briefs — not here.

---

## 10. Deferred source index (outside this roadmap — deliberately, not secretly)

Important work beyond trust closure. **Indexed, not scheduled.** Do not pull onto the
critical path unless a milestone proves it genuinely required for trust closure.

| Theme | Source | Note |
|---|---|---|
| Crypto universe + data ingestion | crypto-slice reviews; thermo P2-17; capability-axes axis-1 | `/`-symbol-safe cache key (`data/cache.py:96`) + a crypto provider. Backtest/fixture-proven only. |
| ML / LLM decision layer (axis-3 thesis) | [capability-axes](architecture/2026-05-30-harness-capability-axes.md) | Backtestable non-rule decider done; LLM decider (forward-only, shadow-first) unscoped. |
| Milodex Score / durable-P/L formula | [GRILL_DECISIONS](GRILL_DECISIONS_2026-06-18.md) | Founder wants it; **formula deferred**; unbuilt. Off the critical path. |
| Governed / auto-discovered universes | GRILL_DECISIONS vs VISION research discipline | Founder direction vs "curated-universe-first." Reconcile in a future ADR. |
| Authoritative evidence reconstruction | [ADR 0050](adr/0050-strategy-evidence-has-a-freshness-axis-distinct-from-promotion-stage.md) Decision 8 | **Decision-gated (D-8)**, not deferred-by-prose. M2 ships *honest labeling*; whether *authoritative* reconstruction is required for closure (research-OS is priority-rank-1) or is a post-closure item is decided via §8 before M2's gate closes. |
| SIP / consolidated data feed | [ADR 0017](adr/0017-data-source-hierarchy.md); intraday-evidence lane | The binding constraint on intraday *durability*. Buy only when a promotion case demands it — **do not** pull forward to "close" M3. |
| God-file / leaked-invariant cleanup | [2026-06-12 deepening audit](reviews/2026-06-12-architecture-deepening-audit.md) | Resolver dup (#7), sacred-layer parity tests (#1-4), workflow-readiness lift (#8). Maintainability. |
| Capital / portfolio allocator | capability-axes | Off-thesis; design-not-to-preclude. |
| Frontend-framework switch (PyWebView) | [framework audit](FRONTEND_FRAMEWORK_AUDIT_2026-06-20.md) | Switch-trigger = measured view-layer AI bottleneck only. |
| Product-phase history (Phases 1–5) | ADR 0023/0025/0027/0031/0038 | Closed; cite-and-retire. |

---

## 11. Gate retrospective template (append-only)

At each gate completion, append one block. Never edit a prior block.

```
### [Mn] — <title> — RETROSPECTIVE (closed YYYY-MM-DD, commit <sha>)
- Planned outcome:
- What actually shipped:
- PRs / commits merged:
- Verification performed (commands + results actually observed):
- Live evidence (named events / drill PASS rows / fills / reconciliation diffs):
- Cleanup absorbed (touch-it items, with classification):
- Decisions made (D-x → ADR / decision record links):
- Deviations from the plan:
- Newly discovered work (→ which milestone or §10):
- May the next gate open? (yes/no + why)
- Must any prior gate be invalidated/reopened? (yes/no + why)
```

### [M0] — Ground-truth & in-flight reconciliation — RETROSPECTIVE (closed 2026-06-22, branch `docs/m0-ground-truth`, decision-records commit `f53e181`)

- **Planned outcome:** an honest baseline — safety/execution-relevant canonical
  docs match HEAD; the mid-flight repo triaged; this roadmap canonical + linked;
  D-1..D-7 framed/decided per the §8 ownership map. No sacred-path code.
- **What actually shipped:**
  - **Doc-truth:** corrected the genuine safety/execution drifts —
    `OPERATIONS.md` `market_closed` holding note; `STRATEGY_BANK.md` stale
    3-runner guard section (guard removed `211d983`); `ADR 0049` status → amended
    by `ADR 0051`; `ADR 0055` co-run addendum; `SRS` R-EXE-004 enumeration
    completeness; `README.md` roadmap registration. Re-grounding found **3 of the
    handoff's "remaining" items already-correct at HEAD** (`_CHECKS` in SRS +
    ADR 0008 already point at code; `risk_defaults.yaml` already ADR-0029-binding;
    `CLAUDE.md` lifecycle-exempt already fixed) — no no-op edits were made.
  - **CLAUDE.md** launch-manual drift (carried from the prior session) committed.
  - **Branch/worktree triage record** — all 16 refs + `master` + the second
    worktree dispositioned; **zero write-offs**.
  - **D-1 + D-2 briefs** framed, each independently Opus-reviewed (reviewers asked
    to dissent), and reconciled.
  - **D-6 decided** by the founder.
  - **D-7 adjudicated.**
  - **phase-audit allowlist fix** (`scripts/audit_phase_state.py`).
- **PRs / commits:** branch `docs/m0-ground-truth` — `7a61ea7` (roadmap +
  artifacts + README), `0acb218` (CLAUDE.md), `fa1f744` (doc-truth drift),
  `f53e181` (decision records + audit fix), + this retrospective commit. **Not yet
  merged to `master`** — lands as the M0 PR.
- **Verification performed (observed):** `.venv\Scripts\python -m pytest -q` →
  **3294 passed, 1 skipped, 4 xfailed, 0 failed** (73.67s); the 1 skip = the
  expected design-system-showcase quarantine. `ruff check src/ tests/ scripts/` →
  clean **except** 1 pre-existing `I001` in
  `tests/milodex/strategies/test_gap_continuation_intraday.py:3` (owned by the
  merge-bound `fix/ruff-i001-gap-continuation` branch; this branch touched **0**
  src/test files). Targeted re-greps confirmed each corrected doc's cited code line
  (`evaluator.py:428` `market_closed`; `:321` `no_frozen_manifest`; `211d983` =
  the guard-removal commit; ADR 0051/0056 exist). Authoritative freeze state
  queried from `strategy_manifests` (5 intraday SPY + 6 daily frozen).
- **Live evidence:** none required at M0 (doc/triage milestone; no fills).
- **Cleanup absorbed (touch-it):** phase-audit allowlist
  (prerequisite-correctness — the roadmap is an M0 deliverable *and* the fix heals
  a pre-existing `master` red); SRS R-EXE-004 enumeration completeness
  (prerequisite-correctness). No area-wide sweeps.
- **Decisions made:** **D-6** → [`reviews/2026-06-22-D6-closure-coverage-floor-decision.md`](reviews/2026-06-22-D6-closure-coverage-floor-decision.md)
  (founder chose a **targeted critical-obligation assurance gate** — versioned
  allowlist of individual reqs, clause decomposition, contract-appropriate
  independently-reviewed evidence; **code references alone don't satisfy it** —
  stronger than the primary's recommendation). **D-7** →
  [`reviews/2026-06-22-m0-branch-worktree-triage.md`](reviews/2026-06-22-m0-branch-worktree-triage.md)
  (merge-bound; content consumed in M4). **D-1 / D-2** framed + reviewed →
  [`reviews/2026-06-22-D1-daily-execution-fork-brief.md`](reviews/2026-06-22-D1-daily-execution-fork-brief.md),
  [`reviews/2026-06-22-D2-intraday-freeze-governance-brief.md`](reviews/2026-06-22-D2-intraday-freeze-governance-brief.md)
  (decided at M1).
- **Deviations from the plan:** (1) The handoff's doc-truth "remaining" list was
  ~half already-done at HEAD; re-grep caught it (no no-op edits). (2) **D-7's
  "~447 test deletions" framing was stale/unreproducible** — the branch is a clean
  +134-line single-file additive doc against every current base; adjudication
  simplified. (3) The M0 reconciliation **surfaced a pre-existing red test**
  (`test_repo_audits_clean`) caused by merged reqs work (`a2998c9`), not by this
  session — fixed via the allowlist. (4) The independent reviews materially
  improved both briefs (D-1 missed Option D / the `preview_only` path; D-2 carried
  a factual error — the SPY benchmark is a one-shot ~10:00-ET entry, not
  always-long). (5) D-6 came back **stronger** than recommended.
- **Newly discovered work (→ milestone):** (a) **Versioned critical-requirement
  allowlist + clause decomposition + contract-appropriate evidence** — the D-6
  assurance gate redefines the **parallel verification track's** target (coverage
  % is now an outcome, not the goal). (b) **D-2 Option E** — make
  `stage: paper`-without-frozen-manifest a load-time error → **M1**. (c) `master`
  ahead 6 unpushed + the 1 pre-existing ruff `I001` → **M6** mechanical / owning
  branch.
- **May the next gate open?** **Yes — M1 may open**, and **M3** is parallel-eligible
  (it depends only on M0 + isolated evidence state). All M0 exit criteria are met:
  every ref dispositioned; genuine drifts corrected + re-grepped clean; roadmap
  linked from the README; D-1/D-2 reviewed; D-6 decided. **Caveat:** the
  `docs/m0-ground-truth` PR should merge to `master` before M1 *execution* begins
  so M1 branches off the corrected docs.
- **Must any prior gate be invalidated/reopened?** No prior roadmap gate exists
  (M0 was first). The **D-6 decision strengthens the §12 closure gate** (assurance,
  not coverage-%) — folded into §12 at this close.

---

## 12. Final closure gate (M6)

The roadmap is **not** complete until all of the following are true:

- Every in-scope roadmap item is completed, explicitly rejected, or deliberately
  deferred beyond this roadmap.
- Every implementation has been reviewed through a PR and merged successfully.
- Required CI, tests, lint, and live verification pass on final `master` (the lone
  quarantined showcase test is an expected SKIP, not a failure).
- **The D-6 targeted critical-obligation assurance gate is satisfied** (decided
  2026-06-22, [`reviews/2026-06-22-D6-closure-coverage-floor-decision.md`](reviews/2026-06-22-D6-closure-coverage-floor-decision.md)):
  a **versioned allowlist of individual trust-critical SRS requirements** is 100%
  adjudicated; each is decomposed into testable clauses with **contract-appropriate,
  independently-reviewed evidence** (positive / refusal / boundary / fail-closed /
  durable-state integration / operational-drill as applicable — **code references
  alone do not satisfy it**); **zero unresolved implementation or spec gaps**;
  final evidence passes on final `master`. Non-critical traceability is valuable
  but non-blocking.
- No roadmap PRs, unfinished worktrees, unpushed commits, forgotten stashes,
  temporary artifacts, or orphaned execution plans remain.
- Git working tree is clean; local `master` and `origin/master` resolve to the same
  commit.
- Merged/dead branches are pruned locally and remotely; **unmerged branches are
  reviewed before deletion** — nothing valuable discarded silently (M0 front-loads
  this adjudication so M6 is mechanical, not archaeological).
- Untracked audits, probes, evidence, and planning artifacts are committed,
  deliberately archived, or intentionally removed.
- **Runtime precondition (safety):** the fleet is stopped, controlled-stop drained,
  no pending stop-requests, locks cleared — **before** the gate reconciles. No active
  or phantom runners, stale locks, orphan jobs, or unexplained runtime state remain.
  Reconcile only a quiesced broker/event-store.
- Broker/event-store state is understood and reconciled; no unexplained positions or
  orders remain (flattening/cancelling stays an explicit operator decision).
- Canonical documentation matches final behavior; historical documents are clearly
  marked.
- The roadmap closes with a final retrospective (§11) and an evidence package.
- A fresh clone (or equivalent clean-room) confirms reproducibility.

**Desired final state:** *nothing in flight, nothing orphaned, nothing quietly
dependent on an agent's memory.*

> This future closure gate is **not** permission to clean, delete, merge, freeze,
> promote, or mutate any state now. It is the destination, executed only at M6 under
> the protocol above.
