# Investigation: manifest_drift TOCTOU race, 2026-05-06

**Status:** read-only investigation. No state mutations. No code changes applied. No broker actions reverted.

**Severity:** real bug in the risk layer. Architectural governance contract violated. Single-trade operational damage today was zero (the unauthorized SELL was profitable and on a position the operator wanted exited anyway), but the failure mode is reproducible and silent — a parallel agent or test run that happens to flip a YAML's `stage` field to `backtest` between two runner cycles can fire a paper order through a gate that was supposed to refuse it.

**Operator question:** *"how did the 16:37 submission get through despite the drift gate?"* — after a meanrev session showed 145 cycles blocked on `manifest_drift` plus exactly one cycle submitting SELL NVDA x50 to the broker.

**One-paragraph answer:** The runner re-reads the YAML's `stage` field from disk on every decision cycle, and the drift-exemption policy flaps with whatever state the file happens to be in at that microsecond. Today another Claude instance (Opus 4.7) was editing the meanrev YAML in parallel — toggling `stage` between `paper` and `backtest` as part of demotion / backtest-validation work. At 16:37:07 UTC, the YAML was transiently in a `stage: backtest` state for one read; the runner's risk evaluator hit the explicit exemption *"Stage 'backtest' is exempt from manifest drift"* and let the SELL through. The cycle before AND the cycle after both saw `stage: paper` and were blocked. The strategy's effective stage should be **bound at runner startup** and immutable for the life of the runner; instead it's being recomputed from disk per-cycle, creating a TOCTOU window.

---

## Timeline (UTC)

| Time | Event | Source |
|---|---|---|
| 14:22 (10:22 ET) | PR #27 lands (`PR 5.1 — Re-baseline strategy bank under fixed engine`). Demotes meanrev YAML from `stage: paper` to `stage: backtest` with comment block citing universe migration. | git log `dc9c2cb` |
| 15:02:57 | Regime runner starts. Session `dda47b79`. | `strategy_runs` |
| 15:03:08 | Meanrev runner starts. Session `b055016e-4350-438f-a58d-04d09c510136`. **Reads `stage: paper` from YAML at startup.** | `strategy_runs` |
| 15:03:28 | First meanrev SELL NVDA proposal → blocked on `manifest_drift`. Runtime hash `baf2341f0647`, frozen manifest at paper stage `f531a076d8a7` (2026-04-24, operator). | `explanations` |
| 15:03–16:35 | 144 consecutive blocks on `manifest_drift`. `strategy_stage` field consistently `paper` per cycle. Hash consistently `baf2341f0647`. | `explanations` |
| 16:18 (12:18 ET) | PR #28 + #29 land back-to-back (dividend-adjusted bars, survivorship-bias disclosure). Touch universe configs but not the meanrev YAML directly. | git log `27ea915`, `2c42b8b` |
| 16:36:28 | Cycle blocks on `manifest_drift`. Hash now `748ef559b526` (changed from `baf2341f0647`). `strategy_stage` still `paper`. **YAML on disk has been modified externally** — the new hash proves it. | `explanations` |
| 16:37:07 | Cycle **submits** SELL NVDA x50. Risk summary `Allowed`. Reason codes `[]`. `strategy_stage` field reads `backtest`. Hash `600bd9a09efd`. **All 12 risk checks pass; manifest_drift check returns true with message "Stage 'backtest' is exempt from manifest drift."** | `explanations` |
| 16:37:08 | Broker accepts SELL NVDA x50 → fills at market. Order `86f38832-447`. ~$10,255 cash inflow, ~$481.50 realized gain. | `milodex orders` |
| 16:37:44 | Next meanrev cycle. `strategy_stage` reads `paper` again. Blocks on `manifest_drift`. The race window has closed. | `explanations` |
| 17:11 (13:11 ET) | PR #30 lands (`ex-ante curated_largecap.v2 (survivorship-corrected)`). Updates the meanrev YAML's `universe_ref` from `universe.phase1.curated.v1` to `universe.curated_largecap.v2`. | git log `e118220` |
| 17:11–21:02 | Subsequent meanrev cycles continue blocking on `manifest_drift` (hash drift extends). Total 145 blocks over the session before `controlled_stop`. | `explanations` |

The single-cycle `stage: backtest` flip at 16:37:07 was the only one in the session. Three different config_hashes were observed across the session (`baf2341f0647`, `748ef559b526`, `600bd9a09efd`), confirming the YAML was being rewritten externally at multiple points — Opus 4.7 working in parallel.

---

## Root-Cause Analysis

### The exemption is correct in principle

The risk evaluator has a deliberate exemption: strategies at `stage: backtest` are not bound by frozen-manifest drift. This makes sense — backtest-stage strategies haven't been promoted to anything yet, there is no frozen manifest to drift against, and the operator is iterating freely on the YAML during validation. Locking the YAML against itself at backtest stage would block the entire research loop.

### The exemption check uses the wrong binding

The bug is that the exemption asks **"what does the YAML on disk currently say?"** instead of **"what stage was this runner started against?"**

- At 15:03:08 UTC the runner started reading `stage: paper`. Its session record reflects that.
- For 144 consecutive cycles, the runner read `stage: paper` from disk and the drift gate refused (correctly).
- For 1 cycle at 16:37:07, the runner read `stage: backtest` from disk and the drift gate exempted (incorrectly given the runner is supposed to be running paper-mode for the entirety of its session).
- For all subsequent cycles, the runner read `stage: paper` again and the drift gate refused.

Per `ADR 0015` (frozen-manifest contract): once a runner is started at a stage, it operates at that stage until it stops. The YAML is not the source of truth for "what stage am I running at right now" — the runner's bound state at startup is. The implementation diverges from the contract by treating the YAML as a per-cycle authority.

### Why each cycle re-reads from disk

The runner re-runs `compute_config_hash(path)` every cycle to feed the drift comparison. That function reads the YAML, canonicalizes it, and SHA-256s the result. It also exposes the `stage` field as part of the canonicalized data the risk evaluator's drift-exempt check consults. The hash and the stage flag therefore travel together: any external write to the YAML changes both atomically from the evaluator's perspective.

This is a TOCTOU race with three actors:
1. **The runner** — long-lived, reads the YAML on each cycle (~30–40s cadence for meanrev).
2. **A parallel writer** — Opus 4.7 today, but could be any of: a `git checkout` of a feature branch, an editor save, a backtest-runner shelling the file, a typo, an `Edit` tool call.
3. **The risk evaluator's policy** — branches on `stage`, with materially different behavior between `paper` and `backtest`.

The gate fires for almost every cycle because the YAML is in a stable state most of the time. It fails open the instant the YAML transits a `stage: backtest` state, then resumes blocking the next cycle.

### Why it produced a "successful" trade today

Three conditions had to align at the same microsecond for the SELL to clear:

1. The YAML had to be in `stage: backtest` at the moment `compute_config_hash` opened the file. Confirmed.
2. The runner's strategy logic had to be proposing a non-trivial decision on that cycle. Confirmed — it had been proposing SELL NVDA every cycle since 15:03 (the strategy's RSI2 exit rule had fired against NVDA).
3. All other 11 risk checks had to pass. Confirmed — kill switch inactive, paper mode confirmed, market open, data fresh, daily loss within limits, order value reasonable, position-size projection clean, total exposure clean, concurrent positions clean, no duplicate order. The only check that varies based on the YAML state is `manifest_drift`.

The result was a profitable exit on a position the operator wanted out of. The fact that today's outcome was favorable should not obscure the fact that **the architecture allowed it for the wrong reason**. Tomorrow the same race could fire an entry on a position the operator does not want.

---

## Why This Matters (Governance Angle)

The frozen-manifest principle (`ADR 0015`) exists to give the operator a hard guarantee: *once a strategy reaches a promoted stage, the YAML cannot change behavior without an explicit re-promotion + re-freeze.* The drift gate is the runtime enforcement of that guarantee.

A bypass-shaped hole in the drift gate breaks two contracts:

1. **The promotion-gate contract.** Today the meanrev strategy was *intentionally demoted* (per the YAML comment block) precisely because the universe migration to `curated_largecap.v2` invalidated the prior paper-stage validation evidence. The risk gate was supposed to refuse paper orders against an unsealed config until re-validation. It refused 145 times and let through 1.

2. **The runner-stage-isolation contract.** A runner started at paper-mode is supposed to *be* running paper-mode for its session. Today's runner submitted one order under backtest-mode semantics (which exempt drift) while the broker treated it as a paper-mode submission. The internal consistency of "what stage am I" was violated mid-session.

The vulnerability surface is broad. Any of the following mechanisms can fire the race:

- A parallel Claude / Opus / Sonnet / Codex instance editing the YAML
- A `git checkout` to a branch where the YAML's stage differs
- A `git rebase` mid-session (which writes intermediate states to the working tree)
- A backtest run that toggles `stage: backtest` for the run and reverts
- An editor's autosave of an in-progress edit
- A shell script that uses `sed -i` or similar in-place edits

Today's specific instance (Opus 4.7 working in parallel) is unusual but **not the bug** — it is one of many ways the underlying TOCTOU race can fire.

---

## Recommended Fix

### One-line bug, structural cause

The runner should snapshot `effective_stage` once at startup and the drift-exemption check should consult that snapshot, not the YAML's current stage. The runner already binds other state at startup (config hash for frozen-manifest comparison, session_id, strategy_id) — `effective_stage` should join that binding.

Concretely (sketch — not implemented):

- `StrategyContext.effective_stage` becomes immutable, bound at `StrategyLoader.load(path)` time from `config.stage`.
- The risk evaluator's drift-exempt check switches from `if config.stage == "backtest"` to `if context.effective_stage == "backtest"`.
- `compute_config_hash` continues to read from disk per cycle (that's the point — the drift comparison must catch a YAML change), but the *exemption policy* is no longer keyed off the per-cycle stage value.

### Test specification (red-green TDD)

Write an integration test where:

1. A YAML is at `stage: paper` with a frozen manifest at paper.
2. A runner is started against that YAML.
3. Mid-session, an external write changes the YAML to `stage: backtest` (and any minor parameter, to ensure config_hash drifts).
4. The runner's next cycle is invoked.
5. **Assert: the cycle is blocked on `manifest_drift`, not exempted.**

The test will be red against current code. The fix turns it green. Add a second test that confirms a runner *started* at backtest stage continues to be exempt mid-session — that's the legitimate use case the exemption exists for, and we don't want to regress it.

### Audit pass

After fixing the immediate bug, sweep the risk evaluator and runner for any other state that:

- Is read from disk per cycle, AND
- Branches policy decisions, AND
- Should logically be runner-bound.

Candidates worth checking:
- `disable_conditions_additional` — if the YAML is edited mid-session to add or remove disable conditions, does behavior flap?
- `risk.max_position_pct`, `risk.max_positions`, `risk.daily_loss_cap_pct`, `risk.stop_loss_pct` — same question.
- `tempo` — should not vary mid-session under any circumstance.
- `universe_ref` resolution — if the universe manifest is hot-edited, does the runner pick up the new symbol set mid-session?

The general principle: anything that the operator agreed to at promotion time should be **frozen by reference** at runner startup, not consulted live from disk. The disk read should be limited to the drift detector itself, whose entire job is to *notice* a disk change and refuse.

---

## Severity Assessment

| Dimension | Today | Tomorrow if unfixed |
|---|---|---|
| Operational damage | $0 (profitable trade) | unbounded |
| Governance damage | one unauthorized SELL through a gate that was supposed to refuse it | every time a parallel agent / editor / git op flips a YAML, repeat |
| Detection difficulty | obvious only because user noticed `1 submitted` line in EOD report and asked | invisible unless every submit is cross-checked against governance state |
| Reproducibility | guaranteed if the conditions co-occur | guaranteed |
| Blast radius | one strategy, one cycle | every strategy that runs concurrent with any YAML-touching activity |

This is not a "panic, fix tonight" finding. The user's session is over, NVDA is exited, no further runs are scheduled until the meanrev strategy has been properly re-promoted (which it cannot be until the curated_largecap.v2 validation evidence is generated). The hole is not actively bleeding.

It is a "fix before the next concurrent-runner session" finding. Specifically: **do not start meanrev paper-mode runs in parallel with any code or YAML editing activity until this is fixed.** The regime runner is uncovered by today's incident only because regime did not propose any orders today; running regime concurrently with editing carries the same architectural risk.

---

## Open Questions

1. **Does the runner's startup logging capture `effective_stage` already?** If yes, the fix may be simply pointing the drift-exempt check at an already-bound value. If no, a small struct addition is needed.

2. **Is the risk evaluator pure-function, or does it carry state?** A pure-function evaluator that takes `(intent, runtime_config, context)` and returns a decision needs `context.effective_stage` available; a stateful evaluator with a constructor could bind once at runner startup. The fix shape depends on this.

3. **Should the runner refuse to start if `config.stage == "backtest"` and the operator passed `--mode paper` (or vice versa)?** Currently the runner appears to start happily and let the risk evaluator sort it out per-cycle. A startup-time mode-vs-stage consistency check would catch a class of operator errors orthogonal to this bug.

4. **Are there other gates that read from disk per cycle?** Listed in the audit-pass section above; the answer determines whether this is a one-off fix or a structural refactor.

5. **What's the right way to handle the meanrev strategy operationally going forward?** The strategy is intentionally demoted to backtest pending curated_largecap.v2 re-validation. The right operational state is: meanrev does not run paper-mode until re-promoted with fresh evidence. Today's runner started against a paper-stage YAML state that no longer holds — the operator should clarify the intended workflow (e.g., a `--require-stage` startup flag that asserts the YAML stage matches an explicit operator expectation before the runner even begins).

---

## Action Items

| # | Item | Priority |
|---|---|---|
| 1 | Implement `StrategyContext.effective_stage` snapshot-at-startup; route the drift-exempt check through it | high — fixes the bypass |
| 2 | Add integration test asserting paper-bound runner blocks drift even when YAML transiently flips to backtest | high — pins the contract |
| 3 | Add second integration test asserting backtest-bound runner stays exempt across YAML edits | medium — protects the legitimate use case |
| 4 | Audit `risk/evaluator.py` and `strategies/runner.py` for other per-cycle YAML reads that branch policy | medium — finds adjacent vulnerabilities |
| 5 | Operational guardrail: don't start paper-mode meanrev runner in parallel with YAML/code editing until #1 lands | low — process workaround, not a code fix |
| 6 | Consider a `--require-stage` startup flag on `milodex strategy run` that asserts YAML stage matches an explicit operator expectation | low — orthogonal class of error, but discovered alongside this one |

The single trade today (`SELL NVDA x50 @ 16:37:08 UTC`, broker order `86f38832-447`) is **not being reverted**. It cleared the position the operator wanted exited; reverting it would inflict actual damage to back out an architecturally-incorrect-but-operationally-favorable outcome. The reasoning is recorded here so the audit trail is complete; the position is closed and the cash is in the paper account.
