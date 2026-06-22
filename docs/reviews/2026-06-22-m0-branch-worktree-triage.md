# M0 — Branch & Worktree Triage (decision record)

**Date:** 2026-06-22
**Milestone:** M0 — Ground-truth & in-flight reconciliation
**Author:** primary agent (Opus)
**Purpose:** Satisfy M0 exit criterion 1 — *every local branch + the second
worktree has a recorded disposition (merge / write-off / park)* — and adjudicate
**D-7** (the `docs/troubleshooting-fault-modes` branch). Per
[`CURRENT_ROADMAP.md`](../CURRENT_ROADMAP.md) §12, M0 front-loads this
adjudication so M6 cleanup is mechanical, not archaeological.

**Scope boundary.** This record *adjudicates and assigns disposition*. It does
**not** execute merges, deletions, pushes, or worktree removal — those are
deferred to normal PR flow (sanctioned parallel tracks per §7) or M6 mechanical
closure. Nothing is discarded by this document.

## Disposition vocabulary

- **captured** — content already present in local `master`; the branch ref is
  redundant and is retired at M6 after `master` is pushed.
- **merge-bound** — valuable, ready to land via normal PR review; not on M0's
  critical path. Landing is deferred (parallel track or M6), not done here.
- **park** — valuable but deliberately held pending a milestone, a decision, or a
  required review (e.g. sacred-layer). Do not land until the gate clears.
- **write-off** — discard. *(None assigned. Nothing here is being discarded.)*

## Git ground truth (re-grepped at HEAD `51e470f` family; `master` `4a6798e`)

- Local `master` is **ahead 6 / behind 0** of `origin/master` (unpushed).
- All 14 pushed branches are **0 ahead / 0 behind** their `origin/*` upstream
  (synced; each has a remote PR surface).
- Two branches are **local-only** and **already merged into `master`**.
- No stashes.

## Branch dispositions

### Local-only, already merged into `master`

| Branch | Disposition | Rationale |
|---|---|---|
| `chore/reqs-traceability-batch1` *(was checked out at handoff)* | **captured** | Its 2 commits (`51e470f`, `35f1b94`) are in `master`'s merge `4a6798e`. Branch ref redundant; retire at M6 after push. |
| `reqs-coverage-backfill-batch1` *(in worktree `milodex-reqs-wt`)* | **captured** | Head `a2998c9` is in `master`. Requirements-coverage backfill is a sanctioned parallel track (§7); this batch's content is captured. |

### `master`

| Ref | Disposition | Rationale |
|---|---|---|
| `master` (`4a6798e`, ahead 6) | **merge-bound (push)** | The 6 unpushed commits are the reqs-traceability backfill (R-BRK/R-DAT/R-EXE) + review-response corrections, landed via the merge commit. Push is an explicit outward action, deferred (not an M0 edit). |

### Pushed branches (synced 0/0 with `origin/*`) — the usage-burn cleanup set

| Branch | Δ vs master | Disposition | Rationale |
|---|---|---|---|
| `chore/dep-upper-bounds` | 1f, +10/-2 | **merge-bound** | Cap numpy/pandas/pyarrow at next major. Low-risk build hygiene. |
| `chore/dep-upper-bounds-batch2` | 1f, +33/-11 | **merge-bound** | Cap 8 more unbounded-major deps. Build hygiene. |
| `chore/unquarantine-gui-showcase` | 3f, +32/-10 | **park** | Un-quarantines the design-system-showcase subprocess test (C3). The roadmap treats that lone skip as *expected* (Qt/QML process-global pollution, `KNOWN_FLAKY_TESTS.md`). Do not land until the flake is proven fixed — else it re-introduces a real `1 failed`. |
| `docs/doc-debt-closeout` | 5f, +1108/-36 | **merge-bound** | Cosmetic doc-debt closeout — sanctioned parallel doc track (§7). Large but additive. |
| `docs/troubleshooting-fault-modes` | 1f, +134/-0 | **merge-bound** | **D-7 — see adjudication below.** Clean additive operator fault-mode guidance. |
| `feat/indicator-consolidation` | 8f, +402/-141 | **park** | RSI/ATR/EMA consolidation onto `_indicators` + decider/parity tests (A1/A4). This is the §10 maintainability theme (resolver dedup / sacred-layer parity tests). Touches strategy indicator paths — hold until a milestone enters that seam (touch-it) or §10 is scheduled. |
| `fix/broker-401-read-path-translation` | 2f, +164/-4 | **park (sacred-layer review required)** | Translates 401/connect errors on broker **read** paths to `BrokerAuthError`. Touches `broker/` (sacred path). Valuable, but must pass `risk-invariant-reviewer` before landing. Park pending that review. |
| `fix/cli-sqlite-error-message` | 2f, +106/-0 | **merge-bound** | Actionable message for corrupt/locked event store (F3 finding). Operator-facing; content feeds M4 fault-mode drills. |
| `fix/gui-capture-scripts-register-types` | 2f, +0/-10 | **merge-bound** | Repairs GUI capture scripts broken by the `register_qml_types` refactor. Tooling fix. |
| `fix/gui-sqlite-swallow-logging` | 5f, +133/-11 | **merge-bound** | Logs swallowed sqlite read errors instead of silent-empty (D1). Observability fix; relevant to M2 operator trust. |
| `fix/ruff-i001-gap-continuation` | 1f, +1/-1 | **merge-bound** | Pre-existing ruff I001 import sort. Trivial. |
| `test/cli-shared-contract-reconcile-dedup` | 1f, +283/-0 | **merge-bound** | Pins the `cli/_shared` JSON serializer/formatter contract (D4). Verification-class, sanctioned parallel (§7). |
| `test/gui-debrittle-batch1` | 5f, +2149/-255 | **merge-bound** | Replaces retained click-path pins with real `QTest.mouseClick` tests (C2). Test-quality / de-brittling — sanctioned parallel; aligns with the test-bloat strategy (targeted de-brittling, not deletion). |
| `test/gui-debrittle-pilots` | 2f, +461/-0 | **merge-bound** | Behavioral trigger-and-observe pilots for C1/C2. Verification-class, parallel. |

### This milestone's branch

| Branch | Disposition | Rationale |
|---|---|---|
| `docs/m0-ground-truth` *(current)* | **merge-bound** | The M0 doc-truth + decision-record PR. Lands at M0 close. |

## D-7 adjudication — `docs/troubleshooting-fault-modes`

**The roadmap's D-7 framing is stale and is corrected here.** §2/§3.6 describe
this branch as carrying *"+134 lines of operator fault-mode guidance **and ~447
unrelated test deletions**"* — flagged as operational cleanup that may discard
valuable state, hence a decision-pause.

**Re-grounded at HEAD, that is not true.** The branch is **one commit** (`5dcbf8a`)
diffing **`1 file changed, 134 insertions(+), 0 deletions(-)`** against *both* the
`master` merge-base **and** `origin/master`. The file is `docs/TROUBLESHOOTING.md`.
There are **no test deletions on this branch** at any current base. The "~447
deletions" were almost certainly measured against an earlier base before those
test changes landed in `master` via another path; they are not attributable to
this branch now.

**Adjudication: the decision-pause is dissolved — this is a clean, additive doc
branch.** D-7 is therefore not "operational cleanup that may discard valuable
state"; it discards nothing.

- **Disposition: merge-bound.** Land via normal doc-track PR. Content is consumed
  by **M4** (recovery & failure-mode proof — it documents operator recovery for 3
  undocumented fault modes, the F3 item).
- **Residual check before landing (cheap):** confirm at merge time that the diff
  is still additive-only (re-run `git diff --shortstat origin/master
  docs/troubleshooting-fault-modes`). If a future rebase ever reintroduces test
  deletions, D-7 reverts to a decision-pause.

## Second worktree

| Worktree | Branch | Disposition |
|---|---|---|
| `C:/Users/zdm80/milodex-reqs-wt` | `reqs-coverage-backfill-batch1` (captured) | **park until M6.** Its branch content is in `master`; the worktree is safe to remove at M6 once `master` is pushed. Not removed here (M0 records, M6 executes). The requirements-coverage backfill track continues as sanctioned parallel work (§7) — new batches get their own dispositions on the next sync. |

## Summary

- **16 refs triaged** (14 pushed + 2 local-only) + `master` + the M0 branch + the
  worktree. Every one has a recorded disposition. **No write-offs** — nothing is
  discarded by this record.
- **D-7 adjudicated:** stale "447 deletions" framing dissolved; clean +134 doc
  branch, merge-bound, content → M4.
- **Sacred-layer gate noted:** `fix/broker-401-read-path-translation` parked
  pending `risk-invariant-reviewer`.
- **Execution deferred:** merges/pushes/worktree-removal are not M0 actions.
