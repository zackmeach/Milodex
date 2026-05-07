# ADR 0030 — Backtest is Exploratory; Manifest Discipline Binds at Paper+

**Status:** Accepted · 2026-05-06
**Related:** [ADR 0015](0015-strategy-identifier-and-frozen-manifest.md) (clarified scope, not superseded), [ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md) (authorizes this work), [ADR 0009](0009-promotion-pipeline-stage-model.md) (stage definitions), [ADR 0011](0011-sqlite-event-store.md) (run-id audit trail), [VISION.md](../VISION.md) §Research Discipline

## Context

[ADR 0015](0015-strategy-identifier-and-frozen-manifest.md) established the frozen instance manifest: at run start, the loaded strategy config is hashed and the hash is recorded alongside every run, fill, and explanation row. At promoted stages (`paper`, `micro_live`, `live`), `_check_manifest_drift` in `src/milodex/risk/evaluator.py` refuses execution if the on-disk YAML's hash differs from the frozen manifest hash. This discipline is correct and load-bearing — it prevents the "operator edits YAML after promotion" silent escape that R-STR-011 through R-STR-014 exist to catch.

The implementation already scopes `_check_manifest_drift` to promoted stages. The `_FROZEN_STAGES` sentinel in `src/milodex/promotion/manifest.py` limits freezing to `{"paper", "micro_live", "live"}`; a `backtest`-stage strategy cannot be frozen because there is no promoted state to snapshot. The evaluator exempts `backtest`-stage strategies from the drift check by reading the effective stage and returning a passing result when that stage is not in the promoted set. This part of the design is correct.

The problem that remains is operator friction at a different stage boundary: a strategy that has **already been promoted to `paper`** does have a frozen manifest, and the drift check runs correctly when the runner is live. But if the operator wants to explore a parameter change — "what if I tighten the RSI threshold from 10 to 8?" — they currently must run five CLI commands to do it legally:

1. `milodex promotion demote <id> --to backtest --reason "parameter exploration"`
2. Edit the YAML.
3. Run `milodex backtest <id> ...`.
4. Evaluate results and decide.
5. `milodex promotion freeze <id>` then `milodex promotion promote <id> --to paper`.

Five commands for what is fundamentally a read operation against a simulation. This friction discourages exploratory backtesting precisely when the operator should be running it freely. FOUNDER_INTENT's §Research Discipline states that the system must make correct research easy; making exploratory backtesting expensive makes the platform lie to the operator about how safe it is to experiment.

The architectural question this ADR resolves: **does manifest discipline apply to `BacktestEngine` invocations?**

[ADR 0028](0028-phase-4-scope-closes-as-cleanup-and-attribution.md) §(g) authorizes this ADR under the "backtest sandbox semantics" cleanup-only work: *"Allow backtest-stage queries against frozen-stage strategies without manifest comparison."* This ADR articulates the concrete decisions; PR #9 implements.

## Decision

### Decision 1 — Stage boundary for manifest discipline

Manifest-drift enforcement applies exclusively to the promoted stages: `paper`, `micro_live`, and `live`. The `backtest` stage has no frozen manifest by design (`_FROZEN_STAGES` in `src/milodex/promotion/manifest.py`), and no frozen manifest means no drift to check. This is the existing behavior, and it is correct. ADR 0015 is clarified — not superseded — to make this scope explicit: the frozen-manifest discipline is a **production-evidence guarantee**, not a research constraint.

### Decision 2 — Backtesting a paper-stage strategy without demotion ceremony

When the operator invokes `milodex backtest <strategy_id> ...` against a strategy that is currently at `paper` stage, the engine reads the YAML on disk **at invocation time** and runs the backtest against that config. No manifest comparison occurs. The operator does not need to demote the strategy before running the backtest.

This is a deliberate scope relaxation for the backtest path only. The alternative — refusing to backtest a paper-stage strategy unless it has been demoted first — would preserve the existing five-command ceremony without adding any safety property. Backtesting is a read-only simulation; it cannot place a real or paper order (the engine uses `NullRiskEvaluator` and `SimulatedBroker` throughout). There is no risk to production evidence: the frozen manifest for the paper-stage strategy is untouched; no run appended by the backtest engine changes the strategy's stage or replaces the frozen hash. The operator's explore-and-decide loop is protected by audit, not by ceremony.

### Decision 3 — Implementation hook: `is_backtest` flag on `EvaluationContext`

The backtest engine must signal to the risk evaluator that the manifest-drift check is not applicable for this evaluation context. The mechanism: a boolean field on `EvaluationContext` (e.g., `is_backtest: bool = False`) that `_check_manifest_drift` reads before any other logic.

When `is_backtest=True`, `_check_manifest_drift` returns immediately with a passing `RiskCheckResult` whose message is `"backtest mode — manifest drift not enforced"`. It does not inspect `runtime_config_hash`, `frozen_manifest_hash`, or the effective stage.

When `is_backtest=False` (the default), `_check_manifest_drift` behaves exactly as today: exempt `backtest`-stage strategies via effective-stage check, require hash match for `paper`/`micro_live`/`live`.

**Wiring scope for PR #9:** The existing `BacktestEngine` already injects `NullRiskEvaluator` (not `RiskEvaluator`) as the evaluator, so `_check_manifest_drift` is never reached during a backtest simulation today — `NullRiskEvaluator` short-circuits the entire risk evaluation before any check method is called. As a result, no existing production call site sets `is_backtest=True`; none is needed to fix the current behavior.

PR #9 should add the field and the fast-path bypass as a clean architectural hook for future research-mode paths (e.g., a preview runner that uses the full evaluator for all checks except manifest enforcement). The production call site is a placeholder for that future path; it must not be force-wired to any existing call site today.

PR #9 must also add a unit test that constructs an `EvaluationContext` with `is_backtest=True` and calls `_check_manifest_drift` directly, verifying it returns a passing `RiskCheckResult` without inspecting `runtime_config_hash`, `frozen_manifest_hash`, or the effective stage. No production wiring beyond the field definition and fast-path is needed in this PR.

### Decision 4 — Audit trail preservation

The `BacktestRunEvent` written to the event store at the start of every backtest run already carries a `config_hash` field (visible in `src/milodex/backtesting/engine.py:185`). That hash is derived from `self._loaded.context.config_hash`, which reflects the YAML that was actually loaded at invocation time.

This is the guarantee: if the operator runs a backtest against a YAML that differs from the strategy's frozen manifest, the run-id's metadata shows the YAML-at-invocation hash, **not** the frozen manifest hash. The audit trail captures what was actually tested. PR #9 must verify this property is preserved after any changes to the backtest path.

The corollary: a run-id whose `config_hash` differs from the strategy's frozen manifest hash is not a violation; it is useful information. The operator can compare the two hashes to understand how far the tested config diverged from the promoted config.

### Decision 5 — Documentation surface

`docs/PROMOTION_GOVERNANCE.md` (or the equivalent stage-discipline document that covers the promotion pipeline's manifest rules) must be updated to document the sandbox semantic: backtest invocations run against the YAML at invocation time; manifest comparison is a production-evidence guarantee scoped to `paper`/`micro_live`/`live` execution. PR #9 owns this.

### Decision 6 — Scope of the relaxation

The relaxation is **narrowly scoped**:

- It applies to `_check_manifest_drift` in the backtest execution path.
- It applies to `BacktestEngine` invocations (both `run` and `simulate_window`).

The relaxation does **not** apply to:

- The live runner (`src/milodex/strategies/runner.py`). `_check_manifest_drift` remains fully enforced for every runner cycle.
- The promotion gate. Promoting a strategy from `backtest` to `paper` still requires `milodex promotion freeze` to snapshot the current YAML. A backtest run against a modified config does not constitute the freeze.
- Universe manifest discipline (ADR 0022). Universe-level config is a separate manifest surface; this ADR does not touch it.
- The risk veto (`RiskEvaluator.evaluate`). The risk layer is sacred. The `is_backtest` flag relaxes one check on one field; it does not reduce the evaluator's authority over any other dimension.

### Decision 7 — Compatibility with ADR 0015

ADR 0015 is **in force and unchanged** for the stages that need it. This ADR clarifies that ADR 0015's frozen-manifest discipline is a **paper+** guarantee — it ensures that the evidence package used for promotion reviews captures exactly what was actually paper-traded. Backtesting is not part of that evidence package at the paper-evaluation stage; it is research that happens before or alongside it. Clarifying scope does not weaken the guarantee.

Concretely:
- The frozen manifest for a paper-stage strategy is still required before `_check_manifest_drift` passes in the runner.
- Editing the YAML after freeze still triggers a drift refusal in the runner.
- The promotion gate still requires hash-match between on-disk YAML and frozen manifest.
- This ADR adds nothing to the freeze path; it adds only the sandbox bypass to the backtest path.

### Decision 8 — Open questions deferred to PR #9

These questions are genuine design choices that belong to implementation; the ADR does not pre-commit on them.

- Exact field name on `EvaluationContext`: `is_backtest`, `evaluation_mode`, or another name.
- Whether the backtest report surfaces a visible "running against modified config" annotation when the invocation hash differs from any frozen manifest that exists for the strategy. The audit trail preserves this information; whether the CLI output surfaces it is a UX decision PR #9 owns.
- Whether a `--strict-manifest` flag should exist for operators who want the original behavior on the backtest path. The current assessment is no: ADR 0015's discipline already fires correctly in the runner; an opt-in backtest strictness mode adds surface area without a safety property that isn't already covered.

## Rationale

**Why Decision 2(a) — bypass — over 2(b) — require demotion.** The purpose of the manifest-drift check is to prevent silent config drift from corrupting a production evidence package. A backtest invocation cannot corrupt that package: it writes to `backtest_runs`, not to the promotion log, and uses `NullRiskEvaluator` + `SimulatedBroker` — it cannot touch the broker. The five-command demotion ceremony imposes real operator cost (and a promotion re-ceremony on the other side) in exchange for zero safety improvement. Every unit of friction applied to a zero-risk operation is friction that could discourage a useful one. FOUNDER_INTENT's research-discipline principle treats exploration as a first-class value; gating it behind ceremony inverts that priority.

**Why a flag on `EvaluationContext` rather than a separate code path.** The evaluator's `_check_manifest_drift` already reads `EvaluationContext` fields to make its decision. Adding one more field keeps the decision logic in one place: if you want to understand how manifest drift is handled, you read `_check_manifest_drift`. A separate code path (e.g., a different evaluator subclass for backtest-adjacent previews) would scatter the logic and create two surfaces to keep consistent. The `NullRiskEvaluator` is already the primary backtest bypass; `is_backtest` is the precision instrument for paths that use the full evaluator but need to communicate their evaluation context.

**Why the audit trail is sufficient for integrity.** The alternative — restricting what the operator can backtest — treats exploration itself as a risk. The actual risk is undetected divergence between what was tested and what was promoted. That risk is addressed by the audit trail: the `config_hash` on every run-id is the immutable record of what was tested. An operator who promotes a strategy after running backtests on a modified config will see, at promotion time, that the on-disk YAML's hash no longer matches the frozen manifest — the promotion gate still fires. The audit trail and the promotion gate together provide all the integrity the production evidence package requires.

## Consequences

- **PR #9 implements:**
  - An `is_backtest` field (or equivalent) on `EvaluationContext` in `src/milodex/risk/evaluator.py`, defaulting to `False`.
  - A fast-path bypass in `_check_manifest_drift` that returns a passing `RiskCheckResult` when `is_backtest=True`, before any stage or hash logic.
  - Verification that `BacktestEngine.run` records `config_hash` from the YAML at invocation time (Decision 4 audit trail guarantee — existing behavior to be explicitly tested).
  - A documentation update to `docs/PROMOTION_GOVERNANCE.md` (or equivalent) capturing the sandbox semantic and the paper+ scope of ADR 0015.
  - Amend ADR 0015's Status header to note "scope clarified by ADR 0030" — the concrete change is appending `"Scope clarified at ADR 0030 (manifest discipline binds at paper+, backtest is exploratory)."` to the existing Status line. This is a small, additive note; it does not change ADR 0015's Decision or Rationale.
- ADR 0015's frozen-manifest discipline is unchanged at `paper`/`micro_live`/`live` stages. Its scope is clarified in its Status header (PR #9's responsibility per bullet 5 above) and in `docs/PROMOTION_GOVERNANCE.md`.
- Operator can invoke `milodex backtest <id>` against a paper-stage strategy without demotion. The strategy's paper-stage frozen manifest is not disturbed.
- Backtest run-id metadata carries the actual invocation `config_hash`, not the frozen manifest hash. When the two differ, the operator can inspect both.
- The live runner's manifest enforcement is unchanged. The promotion gate's hash-match requirement is unchanged.
- Phase 4 close-out (PR #10) will reference this ADR as a resolved cleanup item.

## Non-goals

- Does **not** relax manifest discipline at `paper`, `micro_live`, or `live` stages for any non-backtest execution path.
- Does **not** change the promotion gate behavior. `milodex promotion promote` still refuses when the on-disk YAML hash differs from the frozen manifest.
- Does **not** relax the risk veto in any form. `_check_manifest_drift` is one check within `RiskEvaluator.evaluate`; the evaluator's authority over all other dimensions is unaffected.
- Does **not** change universe manifest discipline (ADR 0022). Universe-level config constraints are a separate surface.
- Does **not** pre-commit the exact field name, query shape, or CLI presentation decisions — those are owned by PR #9.
- Does **not** supersede ADR 0015. ADR 0015 is in force; this ADR clarifies its scope.

## Open questions for PR #9

- **Exact field name on `EvaluationContext`:** `is_backtest`, `evaluation_mode: Literal["live", "backtest"]`, or another scheme. `is_backtest: bool = False` is the simplest and sufficient for Decision 3's needs; PR #9 may choose a richer discriminant if the evaluation-mode surface is expected to grow.
- **Modified-config annotation in CLI output:** When the operator runs a backtest and the invocation hash differs from any frozen manifest for the strategy, should the CLI print a visible notice (e.g., "Note: backtest ran against config hash `abc123` — differs from frozen manifest `def456`")? The information is already in the event store; surfacing it may help the operator's research loop. PR #9 decides.
- **`--strict-manifest` flag:** Whether to offer an opt-in flag that restores the old behavior (refuse to backtest a paper-stage strategy without demotion). Current assessment: no. The promotion gate already enforces discipline at the only moment that matters (promotion time). Opt-in strictness on the backtest path would add operator surface area for a safety property already covered elsewhere.
