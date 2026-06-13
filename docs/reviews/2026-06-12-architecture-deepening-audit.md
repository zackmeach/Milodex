# Milodex Architecture Deepening Audit

**Date:** 2026-06-12
**Scope:** Whole-codebase deepening audit — find seams worth deepening for testability and AI-navigability, not bugs.
**Method:** 17-area fan-out exploration (one explorer per module/cross-cut) → per-candidate adversarial verification (verified / overstated / dismissed) → synthesis. Every kept candidate had its citations re-opened and its deletion test re-run by a verifier before landing here. Two candidates were refuted and dropped (appendix).

Vocabulary is strict: *module / interface / implementation / depth / seam / adapter / leverage / locality*. The deletion test is the spine: delete the module — if complexity **reappears across N callers**, it earned its keep (deep); if it **vanishes**, it was a shallow pass-through. A pure-function extraction that improves testability while the real bug hides in *how it's called* is an anti-pattern, flagged where present.

---

## Executive summary — highest-leverage deepenings, ranked

The headline hypotheses going in (bench.py is a god-facade, EventStore is a god-object, the docs are sprawl) were **refuted**. The codebase is healthier than expected; prior reviews (#231–#245 thermo-nuclear, runner-process, gui-wiring) already cut the worst seams. What remains is concentrated in two shapes: **leaked invariants** (one rule, computed in N places, correctness resting on byte-identity by convention) and **shallow-by-duplication seams** (a scan/verdict loop copy-pasted instead of owned by a module).

1. **`effective_stage` resolution triplicated across the execution↔risk seam** (risk/execution). A TOCTOU-critical stage rule — which governs the manifest-drift veto on paper+ — is spelled three times, with a `service.py` comment admitting the copies *must* stay byte-identical or it's a race. Leaked invariant guarding a sacred surface. Highest locality-per-line win. *Sacred-layer-adjacent.*

2. **Canonical-config hash recipe copied 4× behind the risk veto** (promotion). The SHA-256-over-canonical-JSON recipe that the manifest-drift veto compares is re-spelled at four sites sharing input but not the digest step. Change separators on one path → every paper+ strategy fails the veto and the fleet halts. Fail-closed, so latent — but the invariant deserves one owner. *Sacred-layer-adjacent.*

3. **Daily-loss & data-staleness math duplicated between veto checks and disable-condition evaluators** (risk). `disable_conditions.py` reimplements `_check_daily_loss` + `_check_data_staleness` line-for-line, with docstrings that admit the duplication exists "so verdicts can never diverge." The R-STR-014 halt and the veto it co-fires with rest on two hand-maintained copies. *Sacred-layer-adjacent.*

4. **Universe-manifest scan reimplemented 4× with divergent fail-vs-silent semantics** (backtesting/strategies/xcut-config). One glob-load-match-extract loop, copy-pasted four times across three modules, each extracting one field. The manifest *shape* has no owner; a glob change is a four-file edit. The "silent-None poisons the reproducibility manifest" framing was refuted (the loader pre-validates the ref), so this is a clean DRY/locality move, not a bug.

5. **Workflow-readiness verdict trapped as a private class inside the command facade** (commands). `_DefaultWorkflowReadiness` (~230 LOC) is the one place in `commands/` that owns real decision logic (the "is the harness ready to act" verdict). Already behind an injected seam (the test fake is the second adapter) — but it lives in the wrong package and half-duplicates the CLI trust report. Mechanical lift to `operations/`.

6. **GUI event-store read layer is a scattered query repository with no owning module** (gui). Seven read models each open their own `mode=ro` connection and re-author the latest-row-per-strategy invariant by hand (three divergent encodings), one duplicating the risk-layer's `get_latest_promotion_for_strategy` ordering. The gui-hardening design proposed a shared read layer; only two functions landed.

7. **strategy-id→config-path resolver duplicated 7× across 5 layers** (xcut-layering). The same glob-and-match loop is private in 7 files; `gui/app.py` imports a *CLI command module* for a pure domain lookup. Two competing canonical resolvers already exist (`promote.py`, `manifest.py`) — pick one home and re-export.

8. **Reachability tests grep QML source tokens instead of driving the runtime tree** (xcut-tests). The two suites born from the kill-switch-reset-unreachable bug (G-P1-1) assert that QML *strings* survive a refactor — the exact failure class (declared-but-unconnected signal) still passes. The project already built the QQuickView harness that fixes this; finish migrating onto it.

Everything below #5 is locality/navigability hygiene; #1–4 are leaked invariants on or near the sacred layer and should go first with extra care.

---

## Severity-ranked table (all kept candidates)

| # | Title | Area | Severity | Conf | Verdict | ADR status | Prior-review overlap |
|---|-------|------|----------|------|---------|------------|----------------------|
| 1 | `effective_stage` triplicated (evaluator + builder) | risk | medium | 88 | verified→low | aligns 0015/0030 | adjacent 2026-05-06 TOCTOU review |
| 2 | `effective_stage` manifest-drift rule copied 3× (exec seam) | execution | medium | 80 | verified | aligns 0015/0008 | adjacent 2026-05-06 TOCTOU review |
| 3 | Canonical-config hash recipe copied 4× | promotion | medium | 80 | overstated→low | aligns 0015/0030 | none (NEW) |
| 4 | Daily-loss & staleness math duplicated (veto ↔ disable-cond) | risk | medium | 85 | verified | aligns 0019/R-STR-014 | none (post-#242 cleanup) |
| 5 | Universe-manifest scan reimplemented 4× (divergent semantics) | backtesting | medium | 86 | overstated→low | aligns 0015/0021/0030 | none (NEW) |
| 6 | Universe-manifest resolution leaked shape (xcut copy) | xcut-config | medium | 90 | verified→low | aligns 0003/0015 | none (NEW) |
| 7 | strategy-id→config-path resolver duplicated 7× | xcut-layering | medium | 92 | verified | reinforces 0003/0051 | partial ADR-0051 |
| 8 | Workflow-readiness verdict trapped in command facade | commands | medium | 82 | overstated→low | restores 0051 intent | partial AUDIT-002 |
| 9 | Data-freshness verdict computed 2× (bench ↔ CLI report) | commands | low | 88 | overstated→low | none | partial #234 (constant only) |
| 10 | Shallow freshness constant vs deep verdict | operations | medium | 80 | verified | none | partial #234 (constant only) |
| 11 | oos_aggregate decode has no single owner (gate ≠ report) | analytics | medium | 80 | overstated→low | aligns 0021/0052 | none (NEW) |
| 12 | Daily-return derivation computed 3× (divergent edge cases) | backtesting | medium | 70 | verified | aligns 0021 | none (NEW) |
| 13 | Gate verdict semantics reimplemented in GUI read models | promotion | medium | 80 | overstated→low | aligns 0052/0020 | distinct from #235/AUDIT-001 |
| 14 | GUI event-store read layer scattered, no owning module | gui | medium | 80 | overstated | aligns 0010/0049 | partial AUDIT-006/RM-007 |
| 15 | Cache-fetch planning welded into AlpacaDataProvider.get_bars | data | medium | 82 | overstated | aligns 0017/0002 | none (NEW) |
| 16 | Active cache version has two definitions of truth | data | medium | 70 | verified | aligns 0002 | none (NEW) |
| 17 | Intraday session flow + Wilder-RSI have no shared seam | strategies | medium | 88 | overstated | aligns 0003 | NEW intraday analog of AUDIT-004 |
| 18 | Daily/intraday replay is a branch, not two adapters | backtesting | low | 64 | verified | none | partial #241/AUDIT-005 |
| 19 | "Begin parent run" choreography duplicated 2× | backtesting | low | 68 | verified | none | adjacent #233 (close-out) |
| 20 | BacktestStructuralRiskEvaluator hand-lists check subset | risk | medium | 80 | overstated→low | aligns 0030/0008 | none (NEW) |
| 21 | Per-strategy `_validated_parameters` re-checks loader ranges | strategies | low | 80 | verified | aligns 0003/P2-04 | follow-on of #245 |
| 22 | `get_orders` status filter stringly-typed at broker seam | broker | low | 72 | overstated→low | aligns 0006 | none (NEW) |
| 23 | Broker exception classification by substring-sniffing | broker | low | 60 | overstated→low | aligns 0006/0001 | none (NEW) |
| 24 | DataProvider docs an `is_market_open()` it doesn't own | data | low | 88 | overstated→low | none | none (NEW) |
| 25 | SimulatedDataProvider.get_bars silently ignores timeframe | data | low | 72 | overstated→low | aligns 0006 | none (NEW) |
| 26 | KillSwitchStateStore misplaced in execution → import cycle | xcut-layering | medium | 88 | overstated→low | reopens 0019 | none (NEW) |
| 27 | test_qml_load_smoke conflates deep load-smoke + 38 token pins | xcut-tests | medium | 82 | verified→80 | aligns 0049/0051 | partial #243 |
| 28 | Decision-layer-seam tests grep src instead of asserting blob | xcut-tests | low | 75 | overstated→low | aligns 0015/0030 | none (NEW) |
| 29 | Absent CONTEXT.md domain-vocabulary anchor | xcut-navigability | medium | 88 | verified→82 | none | none (NEW) |
| 30 | CLAUDE.md census stale: 12 modules claimed, 13 exist | xcut-navigability | medium | 95 | overstated→low | doc-accuracy (outside ADR) | none (NEW) |
| 31 | ADR index stale: stops at 0052, three ADRs unreachable | xcut-navigability | medium | 96 | overstated→low | upholds authority order | none (NEW) |

"verified→low" / "overstated→low" = verifier's corrected severity after refutation.

---

## Detailed findings by theme

### Theme A — Leaked seams (one invariant, N copies, correctness by convention)

This is the dominant pattern in the kept set, and it clusters on the sacred layer. In each case a rule that *must* stay identical across call sites is restated rather than owned by a module. None weaken enforcement; all concentrate a today-correct invariant into one owner so it stays correct under change and becomes testable at the interface.

---

#### 1. `effective_stage` resolution triplicated across the evaluator and its builder
**Files:** `risk/evaluator.py:231`, `risk/evaluator.py:304`, `execution/service.py:445`

**Problem.** The rule `expected_stage or strategy_config.stage` — which resolves which promotion stage governs a cycle — is implemented three times. `_check_strategy_stage` (evaluator.py:231) and `_check_manifest_drift` (evaluator.py:304) each recompute it; `execution/service.py:445-449` recomputes it again to key the frozen-manifest-hash lookup. service.py's own comment (437-444) states this third copy must mirror the evaluator or it is a TOCTOU race: keying the hash lookup off `strategy_config.stage` while the evaluator keys the exemption off `intent.expected_stage` desyncs the manifest-drift gate. The interface (`EvaluationContext`) exposes both `expected_stage` and `strategy_config.stage` as raw fields and trusts every reader to combine them identically.

**Solution.** Make effective-stage a single computed property of `EvaluationContext` (or a tiny `effective_stage(intent, config)` helper in `risk/`). All three sites read that one definition. The risk module owns the rule; the builder consumes it instead of re-deriving.

**Benefits.** *Locality:* the TOCTOU-critical rule lives once, enforced by construction not reviewer vigilance. *Leverage:* callers stop needing to know the evaluator's internal `or` semantics. *Tests:* stage resolution becomes unit-testable in isolation (empty-string/whitespace edge cases) instead of only reachable through a full `evaluate()` or a full execution cycle.

**Deletion test.** Delete the helper → `expected_stage or stage` reappears in all three callers (it does today). Concentrates real duplicated complexity → earns its keep.

**Severity/Confidence.** medium → **low** / 88. ADR: aligns 0015/0030; strengthens the 2026-05-06 TOCTOU defense without reopening it.

**Verifier's note.** Triplication is real and grounded. The deletion-test value is genuine but small (a one-line rule). The adversarial wrinkle *strengthens* the finding: the three derivations are not identical today — evaluator uses `or` (empty-string `expected_stage` falls through to YAML); service.py uses `is not None` (empty-string kept). For all real inputs they agree, but the candidate's own cited edge case (empty stage) already produces divergent behavior on the two sides of the very comparison the comment swears must mirror. No live defect (no YAML emits an empty stage) — exposure is future-regression risk. Corrected to low.

---

#### 2. `effective_stage` manifest-drift rule copied 3× across the execution-risk seam
**Files:** `execution/service.py:445`, `risk/evaluator.py:231`, `risk/evaluator.py:304`

**Problem.** The same triplication as #1, framed from the execution side: service.py:445 keys the manifest-hash lookup (the *frozen* side of the drift compare); evaluator.py:304 keys the exemption (the *runtime* side); evaluator.py:231 keys eligibility. The correctness coupling between the two sides of the drift comparison is enforced only by the prose comment at service.py:437-444 — not by a single resolution point. That is a leaked invariant spanning two modules with no compile-time link.

**Solution.** Resolve `effective_stage` once on the shared `EvaluationContext` (all three readers already receive the same frozen context) and have the frozen-hash lookup read it too.

**Benefits.** *Locality:* one home for the drift-compare invariant. *Leverage:* eliminates the `is not None` vs `or` form discrepancy by construction. *Tests:* one test surface for the prefer-rule.

**Deletion test.** Delete the service-side copy and the frozen-side hash lookup breaks; the runtime-side reads remain at evaluator.py:231/304. The literal "logic reappears at the evaluator" phrasing is loose (those are runtime-side reads), but the real defect — one small prefer-rule guarding a sacred invariant, spelled in 3 places with a form discrepancy — holds.

**Severity/Confidence.** medium / 80. ADR: aligns 0015/0008.

**Verifier's note.** Verified. One imprecise phrase to discard: the candidate calls this "fail-open on the manifest gate" — `_check_manifest_drift` actually fails **closed** (raises on missing runtime hash at evaluator.py:314, blocks on missing frozen manifest at :320). The risk is a wrong-stage-satisfies race, not a blanket fail-open. (Findings #1 and #2 are the same triplication seen from both modules; fix them as one PR.)

---

#### 3. Daily-loss and data-staleness math duplicated between the veto checks and the disable-condition evaluators
**Files:** `risk/evaluator.py:466`, `risk/disable_conditions.py:146`, `risk/evaluator.py:439`, `risk/disable_conditions.py:122`

**Problem.** `_evaluate_drawdown_breach` (disable_conditions.py:146-180) reimplements the equity-base, current-loss-pct, kill-switch-threshold, and effective-cap math — including the `expected_daily_loss_cap_pct` runner-bound preference — line-for-line from `_check_daily_loss` (evaluator.py:466-493) + `_effective_daily_loss_pct` (evaluator.py:870-882). `_evaluate_data_quality` (disable_conditions.py:122-143) re-derives the naive-timestamp-is-UTC normalization and staleness comparison from `_check_data_staleness` (evaluator.py:439-464). Both disable_conditions docstrings explicitly state the duplication exists so verdicts "can never diverge" (125-128, 150-152) — which is precisely the smell: correctness depends on two copies staying byte-identical by hand. All three checks sit in the same `_CHECKS` tuple and co-fire in one pass, so a desync silently splits the R-STR-014 halt from the veto it shadows.

**Solution.** Extract the two scalar predicates the risk layer already computes — `daily_loss_status(account, defaults, expected_cap)` and `bar_is_stale(bar, defaults)` — into named pure functions in `risk/`. Both the `_check_*` veto and the disable-condition evaluators call them. The numeric policy lives once; the two surfaces co-fire over the same computation by construction.

**Benefits.** *Locality:* the equity-base/staleness math has one owner; "never diverge" becomes structural not aspirational. *Leverage:* future tuning (a different loss denominator) updates both atomically. *Tests:* the predicates are directly unit-testable (cap-vs-threshold boundary ordering) without constructing a full `EvaluationContext` twice. This is a genuine locality win — the duplicated *call* is the bug surface, not a testability-only extraction.

**Deletion test.** Delete the shared predicate → the loss/staleness arithmetic reappears verbatim in both the veto check and the disable-condition evaluator (it does today). Concentrates → deep.

**Severity/Confidence.** medium / 85. ADR: aligns 0019; deepens R-STR-014/#242 rather than reopening it.

**Verifier's note.** Verified, kept at medium. There is no shared helper today (grep for `daily_loss_status`/`bar_is_stale` returned only the four duplicating sites), and `test_disable_conditions.py` (380 lines) contains **no** parity test pinning the two implementations together — so the invariant is genuinely aspirational. Blast radius is bounded: both surfaces fail-closed, and a desync makes one looser than the other while each still vetoes on its own math — no silent bypass of risk. A single parity regression test could also pin it without the extraction; that's why medium, not high.

---

#### 4. Canonical-config hash recipe copied across four sites; risk-veto drift relies on byte-identity by convention
**Files:** `promotion/state_machine.py:353-355`, `promotion/manifest.py:30-33`, `promotion/run_evidence.py:83-87`, `strategies/loader.py:385-390`

**Problem.** `_check_manifest_drift` vetoes paper+ execution when runtime hash ≠ frozen hash. Four functions each re-spell `sha256(json.dumps(canonical, sort_keys=True, separators=(",",":")))`: `manifest._hash_canonical`, `state_machine._hash_canonical`, `run_evidence.compute_post_update_hash`, and `loader.compute_config_hash` (the runtime side). All share `canonicalize_config_data` (loader.py:527) but not the digest step. Two of the four docstrings admit the fragility — but it's a comment, not a type. Change separators or sort on one path only and every paper+ strategy fails the veto and the fleet halts.

**Solution.** Extract one shared `hash_canonical_config(canonical)` in the promotion layer; all four sites call it. The invariant becomes structural.

**Benefits.** *Locality:* the recipe lives once. *Tests:* one property test pins freeze-hash == runtime-hash, today only implicit.

**Deletion test.** Collapse the four → SHA-256-over-canonical reappears at every freeze/transition/evidence/runtime site needing the same digest. Earned depth, inlined four times.

**Severity/Confidence.** medium → **low** / 80. ADR: aligns 0015/0030; #239 touched manifest atomicity, not hashing — genuinely new.

**Verifier's note.** Kernel real and grounded; severity dialed down because the candidate over-claims a four-way symmetric blast radius. Two of the four (state_machine and run_evidence) are already guarded by a same-process equality check at state_machine.py:189 that raises loudly before any durable write. The genuinely silent, veto-relevant drift is the **2-way** pair runtime(loader) vs frozen(manifest), which has no assembly-time guard. And a drift produces a fail-closed loud veto (halts paper+) — safety-preserving, no capital loss, no bypass. Worth doing to make the runtime-vs-frozen invariant structural; not medium-grade.

---

### Theme B — Shallow-by-duplication seams (a scan/verdict loop with no owning module)

A scan-and-match or compute-a-verdict loop copy-pasted across callers. The interface is barely smaller than the implementation behind each copy — shallow multiplied by N. Concentrating it into one module is the shallow→deep move.

---

#### 5 & 6. Universe-manifest lookup reimplemented 4× with divergent fail-vs-silent semantics
**Files:** `strategies/loader.py:124`, `strategies/loader.py:171`, `backtesting/engine.py:742`, `backtesting/run_manifest.py:88`

**Problem.** The pattern "glob `universe_*.yaml`, `yaml.safe_load` each, skip non-dict, match `str(universe.get("id","")) == universe_ref`, extract field X" is implemented verbatim four times: `resolve_universe_ref` (symbols), `resolve_universe_survivorship_corrected` (survivorship flag), `engine._resolve_universe_slippage` (slippage_pct), `run_manifest._resolve_universe_manifest` (path+hash). Two live in `backtesting/`. There is no module owning "find the manifest for this ref." The copies diverge on error mode: loader variants **raise** ValueError on an unknown ref; both backtesting variants **silently** return None / `{path:None,hash:None}`. The manifest *shape* (glob pattern, id-key, dict-guards, YAMLError swallow) is smeared across three modules; a new field grows a fifth loop, a glob change edits four files; a reader of `engine.py:742` cannot tell it's the same lookup as `loader.py:124`.

**Solution.** One resolver in `strategies/` (where `resolve_universe_ref` already lives): given `universe_ref` and `config_path`, return the matched manifest dict **and path** (run_manifest needs the path, so dict-only is insufficient), or None per a single, deliberate unknown-ref policy. The four functions become thin field extractors over it. Keep each caller's not-found *policy* caller-side (loader raises, backtest paths return None) — concentrate the *loop*, distribute the *policy*.

**Benefits.** *Locality:* YAML schema, id-match rule, guard ladder live once; a field rename is a one-file edit. *Leverage:* callers ask "give me field X for this ref" behind a small interface. *Tests:* four near-identical fixture suites collapse to one.

**Deletion test.** Delete the resolver → the identical loop reappears at all four call sites (it exists four times today). Concentrates → earns its keep. The current state is the failed deletion test made permanent.

**Severity/Confidence.** medium → **low** / 86–90. ADR: aligns 0003/0015/0021/0030; distinct from the R-DAT-016 policy contradiction RESOLVED #232 — that was the SRS amendment, this is the duplicated *read* loop.

**Verifier's note.** Structural kernel verified across both candidates (two explorers found this independently — strong signal). The **bug framing was refuted**: the candidate's headline ("a typo'd `universe_ref` silently falls back to global-default slippage and stamps a manifest claiming `manifest_path=None` — a reproducibility lie") is unreachable. `_load_universe` (loader.py:471-473) makes `universe` and `universe_ref` mutually exclusive, so a non-None ref always forces `resolve_universe_ref` at loader.py:96-97, which **raises** on an unknown ref. A backtest cannot begin with a typo'd ref — it fails to load before the engine/slippage/manifest ever run. The silent-None branches fire only on the legitimate inline-universe path or a real manifest genuinely lacking the field, where None is correct. So this is a clean DRY/locality refactor shorn of the correctness/reproducibility narrative. Low.

---

#### 7. strategy-id→config-path resolution duplicated 7× across 5 layers, no single module
**Files:** `strategies/loader.py:192` (leaf loader, no resolver), `promotion/manifest.py:97`, `gui/app.py:265`

**Problem.** The resolver loop (glob `configs/*.yaml` → `load_strategy_config` → `except ValueError: continue` → `if config.strategy_id == sid: return path`) is duplicated near-verbatim across **seven** call sites: `cli/commands/promote.py:56`, `promotion/manifest.py:97`, `cli/commands/strategy.py:52`, `backtesting/walk_forward_batch.py:355` (closure), `strategies/runner.py:581` (method), `strategies/runner_status.py:295` (StrategyConfig variant), `strategies/paper_runner_control.py:78` (loop-all variant). Delete any one and the identical loop reappears at the next caller. Worse, `gui/app.py:265` imports `resolve_strategy_config` from `cli.commands.promote` — a CLI command module — for a pure domain lookup, a layering inversion.

**Solution.** One resolver in `loader.py` (the leaf loader's module, which today has no resolver); `manifest.py` re-exports it; GUI imports `loader` instead of a CLI module. The glob, id-match, and guard ladder live once.

**Benefits.** *Locality:* manifest-shape and id-match in one place. *Leverage:* GUI off the CLI module. *Tests:* one well-covered surface; the five private copies are currently untested.

**Deletion test.** Delete the resolver → the loop reappears across 5 layers → one deep module. Confirmed concentrating.

**Severity/Confidence.** medium / 92 (highest-confidence kept candidate). ADR: reinforces 0003/0051; not contradicted.

**Verifier's note.** Verified; the candidate **undercounted** (claimed 4+, actually 7). Two corrections: (a) the codebase is half-migrated — `commands/bench.py:66` already imports `manifest.py:resolve_strategy_config_path` as the de-facto canonical resolver, so there are *two* competing canonical resolvers plus 5 private copies; pick one home and re-export. (b) **Do not** fold in the nearby `universe_*.yaml` scans (findings #5/#6) — different concern, an over-eager implementer could wrongly merge them.

---

#### 8. Workflow-readiness verdict logic is a deep module trapped inside the command facade as a private class
**Files:** `commands/bench.py:285-514`, `commands/bench.py:546-548`, `operations/reconciliation.py`, `cli/commands/report.py:255-268`

**Problem.** `_DefaultWorkflowReadiness` (~230 LOC, bench.py:285-514) is the only place in the `commands/` package that owns real decision logic rather than pass-through orchestration. It reads the event store directly (`latest_readiness`, `get_latest_kill_switch_event`, `get_latest_bar_timestamp`) and turns each into a fail-closed `WorkflowReadinessIssue` across four readiness dimensions. This is a coherent domain module — the "is the harness ready to act" verdict — that should sit at a named seam in `operations/`, beside the `reconciliation.py`/`freshness.py` sources it consumes. It lives in the facade only by accident of who first needed it, and ADR-0051 declares these command modules own no business rules of their own.

**Solution.** Lift `_DefaultWorkflowReadiness` plus the four `WorkflowReadiness*` dataclasses into a dedicated `operations/workflow_readiness.py`. The facade keeps injecting it through the existing `workflow_readiness` constructor seam (the test fake is already the second adapter — a real seam). `_evaluate_workflow_readiness` shrinks to a pure caller.

**Benefits.** *Locality:* the readiness verdict lives in one operations module instead of a private facade class. *Leverage:* a small `evaluate()` interface fronts four dimensions; CLI and Bench can share it. *Tests:* the module gets its own focused test surface (today exercised only indirectly through facade propose tests).

**Deletion test.** Already-concentrated: all five propose call sites go through one method (`_evaluate_workflow_readiness`), so the proposal **moves** an already-deep module rather than concentrating scattered complexity. Restores ADR-0051 intent.

**Severity/Confidence.** medium → **low** / 70. ADR: restores 0051 thin-facade intent (does not reopen it). Partial overlap with AUDIT-002 ("Bench workflow-readiness").

**Verifier's note.** Citations all hold; the class is a self-contained ~230-LOC module behind a real injected seam (`_FakeWorkflowReadiness` at test_bench_facade.py:221-233 is the second adapter). But the deletion test is framed to overstate: the complexity is *already* concentrated in one class behind one interface inside bench.py — this is the "moves not concentrates" pattern. The one surviving real kernel is cross-file duplication with the CLI trust report (see #9), and even that is **partial**: only 2 of the 4 dimensions overlap (reconciliation/broker read `latest_readiness`; the CLI does a live `broker.is_market_open()`). A pure testability/locality move with no bug behind it — low.

---

#### 9. Data-freshness staleness verdict computed in two places with drift risk
**Files:** `commands/bench.py:447-514`, `cli/commands/report.py:260-268`, `operations/freshness.py`

**Problem.** The staleness verdict — `get_latest_bar_timestamp()` → `age_hours` → compare to `DATA_FRESHNESS_STALE_HOURS` — exists twice: `_DefaultWorkflowReadiness._data_freshness_issue` and the CLI trust report's `_data_freshness`. #234 deduplicated the *constant* (both import it from `operations/freshness.py`) but left the *verdict computation* duplicated. Behavioral divergence: report.py returns `stale=None` for the no-bars case (informational) while bench.py treats no-bars as a blocking issue.

**Solution.** Add `data_freshness_verdict(event_store, now) -> FreshnessVerdict` to `operations/freshness.py` (which already owns the constant). Both callers map it. The differing no-bars *policy* stays caller-side; the *computation* unifies.

**Benefits.** *Locality:* one owner for "how stale is the data." *Leverage:* the freshness module deepens (owns threshold + verdict). *Tests:* one focused age/tz/no-bars test instead of asserting it twice through two unrelated surfaces.

**Deletion test.** Delete the verdict fn → age/tz/threshold computation reappears in both callers (it does today). Concentrates → deep.

**Severity/Confidence.** low / 80. ADR: none. NEW relative to #234 (constant-only).

**Verifier's note.** Real kernel, exaggerated "drift risk." Only one of the two flagged divergences is behavioral (the no-bars policy, which the fix keeps caller-side). The other — report's `_aware()` helper vs bench's inline tzinfo branch — is behaviorally **identical** (both treat naive as UTC); calling it "already-divergent tz behavior" inflates a cosmetic difference. The concentrated complexity is tiny (a query + a divide + a compare), so the interface is nearly as complex as the implementation — sits near the shallow end. Minor but legitimate.

---

#### 10. Shallow freshness constant vs deep freshness verdict (operations framing)
**Files:** `operations/freshness.py:8`, `cli/commands/report.py:260-268`, `commands/bench.py:479-514`, `operations/reconciliation.py:537`

**Problem.** Same duplication as #9, with the deep contrast made explicit: `freshness.py` is *only* the 24.0 constant — a shallow pass-through — while `latest_readiness` (reconciliation.py:537) is the deep model to imitate (a verdict consumed identically by three callers). The five-step staleness protocol (fetch-bar, None-guard, tz-normalize, age-hours, threshold) is reimplemented at both report.py and bench.py; the `_aware` helper is duplicated verbatim at reconciliation.py:1158 and report.py:641.

**Solution.** Add `data_freshness(event_store, now) -> FreshnessVerdict` to `freshness.py`, mirroring `latest_readiness`; both callers map it instead of recomputing; fold in the duplicated `_aware`.

**Deletion test.** Delete `freshness.py` → only 24.0 reappears twice (shallow). Delete a deep `data_freshness` → the five-step protocol reappears in both report.py and bench.py (deep). Clean shallow-vs-deep demonstration.

**Severity/Confidence.** medium / 80. ADR: none.

**Verifier's note.** Verified on all three fronts. The reuse target is real — the gui-wiring audit (P1-2) explicitly wants a GUI freshness badge, and this verdict is its natural source. Capped at medium because there are only two callers and the two None-semantics genuinely differ by design, so the computation core concentrates cleanly but "lives once" is slightly diluted at the verdict edges. (#9 and #10 are the same duplication from the commands vs operations vantage; fix as one PR that lands the verdict in `operations/freshness.py`.)

---

#### 11. oos_aggregate decode has no single owner; gate and trust report decode it differently
**Files:** `analytics/metrics.py:255-274`, `promotion/run_evidence.py:56-64`, `gui/_event_queries.py:65-95`

**Problem.** `oos_aggregate` is the gate's canonical evidence (ADR 0021), decoded at three non-equivalent sites. `metrics_for_run` overrides sharpe only if the key is present and keeps the equity-curve drawdown as fallback. `run_evidence.metrics_from_run` returns the raw oos values unconditionally, None on missing. `_event_queries.oos_aggregate_metrics` parses the JSON a third way. For a partial aggregate the gate (via `metrics_from_run`) and the CLI trust report (via `metrics_for_run`) can read different Sharpe and drawdown — one of them feeds the risk-read-back gate.

**Solution.** Give analytics one decoder for the gate-canonical triple, on the **None-on-missing** rule (the safe, fail-closed one). The CLI trust report adopts *that*, and the GUI helper becomes a thin caller.

**Benefits.** *Locality:* the fallback rule lives once. *Leverage:* gate and report cannot disagree on a run's metrics — a safety property for the gate the risk layer reads back. *Tests:* two per-path surfaces collapse to one.

**Deletion test.** Route the duplicate decodes through one decoder → walk-forward decode reappears as the single copy. Earned depth.

**Severity/Confidence.** medium → **low** / 75. ADR: aligns 0021/0052.

**Verifier's note.** Divergence is real and grounded; downgraded for two reasons. (1) Latent only — the writer emits all keys today, so it bites solely on malformed/legacy metadata. (2) **The candidate's proposed fix direction is unsound as written.** It suggested routing the gate through `metrics_for_run`, but that decoder deliberately falls back to the equity-curve sharpe/drawdown its own docstring calls "not meaningful for walk-forward" — that would make the gate read fragmented numbers on a partial aggregate, the opposite of fail-closed. The gate's None-on-missing rule is the safe one; canonicalize on *that* and have the trust report adopt it. Corrected above.

---

#### 12. Daily-return derivation — the audit-critical OOS number the gate reads — computed in three places with divergent edge cases
**Files:** `backtesting/walk_forward_runner.py:261`, `backtesting/walk_forward_runner.py:418`, `backtesting/walk_forward_batch.py:541`, `analytics/metrics.py:166`

**Problem.** A shared helper exists (`analytics.daily_returns_from_equity`) and `run_walk_forward` uses it for per-window Sharpe. But the **OOS-aggregate** returns — the stream the promotion gate's Sharpe/drawdown is computed from — are re-derived inline in `_aggregate_oos` (walk_forward_runner.py:418-422) with a deliberately different rule: it **emits 0.0** for a step when prev≤0 to keep returns index-aligned, where the shared helper **drops** the step. Then `walk_forward_batch._daily_return_series` derives daily returns a **third** way (`continue` on prev==0, keyed by date) for the cross-strategy correlation matrix. "What counts as a daily return" has three answers living within feet of the most audit-sensitive numbers in the system, and the divergences are silent.

**Solution.** Give analytics one daily-returns interface that names its edge-case contract explicitly (a `DROP` mode vs an `EMIT_ZERO` index-alignment mode). `_aggregate_oos` and `_daily_return_series` call it with the mode they need. The intentional alignment behavior becomes a named, tested option rather than an inline reimplementation a future edit can silently diverge from.

**Benefits.** *Locality:* the definition of a daily return, and every zero/negative-prior edge case, lives in one module feeding window Sharpe, OOS-aggregate Sharpe, and correlation alike. *Leverage:* callers pick a named mode. *Tests:* the alignment invariant (`_aggregate_oos` already raises on misalignment) becomes a property of the shared helper, tested once.

**Deletion test.** Route the two inline derivations through the shared helper → the per-step pct-change does not vanish; it becomes the helper's body, and the alignment-mode branch is real distinct behavior the gate depends on. Concentrates → earns its keep.

**Severity/Confidence.** medium / 72. ADR: aligns 0021.

**Verifier's note.** Verified — three genuinely distinct edge-case contracts (`prev > 0` drop, `prev > 0 else 0.0` emit, `prev == 0 continue` — note the last lets a *negative* prior equity through where the other two guard `> 0`). Severity held at medium but **impact framing corrected down**: only the `_aggregate_oos` copy feeds the gate; the correlation derivation is informational display. In the common case ($100k start, long-only US equities, equity staying positive) the gate copy and the shared helper produce numerically identical results, so the gate is not silently wrong today. Durable value is auditability/AI-navigability and future-edit safety — squarely on the legibility axis.

---

#### 13. Gate verdict semantics reimplemented outside PromotionPolicy in GUI read models
**Files:** `promotion/policy.py:112-126`, `gui/strategy_bank_state.py:238-243`, `gui/attention_state.py:276`, `gui/ledger_builders.py:184-188`

**Problem.** `evaluate_research_target` owns the gate comparison (sharpe≤t, max_dd≥t, count<t). `_compute_gate_failures` (strategy_bank_state.py:238-243) hand-writes the same three comparisons against state_machine aliases, emitting S/D/N codes. If a future ADR changed an *operator* (e.g. Sharpe strict `<`), the two silently diverge.

**Solution.** Add a read-only `PromotionPolicy.gate_failure_codes()` predicate (or map `evaluate_research_target.failures` to S/D/N at a thin GUI seam); keep scalar aliases for display only. The comparison operators live once.

**Deletion test.** Delete `_compute_gate_failures` → the boundary/None/regime-skip logic reappears across the GUI consumers. Earned-depth predicate not yet built.

**Severity/Confidence.** medium → **low** / 62. ADR: aligns 0052/0020.

**Verifier's note.** Real kernel, inflated framing — three sub-claims refuted. (1) "ADR editing policy.py leaves GUI verdicts stale" is **false for threshold values**: the GUI imports policy-derived aliases (`MIN_SHARPE = ACTIVE_PROMOTION_POLICY.capital_gate.min_sharpe`, etc.), so changing a number propagates automatically — only an *operator/None-handling* change desyncs. (2) "Three answers" is false: `attention_state.py:276` **consumes** the same `_compute_gate_failures`, it does not reimplement it. (3) "policy strict vs ledger gte" is a category error: `ledger_builders.py:184` `>=` is a display-band classifier, not a gate verdict. The fix worth doing is the thin GUI-seam adapter so the operators live once — and S/D/N are ADR-0009 *display* codes, so pushing them into the governance source of truth is arguably the wrong direction. Low.

---

#### 14. GUI event-store read layer is a scattered query repository with no owning module
**Files:** `gui/strategy_bank_state.py:143`, `gui/active_ops_state.py:120`, `gui/query_helpers.py:57`, `gui/_event_queries.py:98`, `core/event_store.py:1776`, `core/event_store.py:2122`

**Problem.** Seven read models each open their own `mode=ro` connection and re-author the latest-row-per-strategy invariant by hand — MAX-id self-join (`_event_queries.py:98`, `query_helpers.py:57`), `ROW_NUMBER` (`active_ops_state.py:120`), NOT-EXISTS `recorded_at` ordering (`strategy_bank_state.py:143`). The last duplicates `EventStore.get_latest_promotion_for_strategy` (event_store.py:1776) — a load-bearing risk-layer ordering invariant. The gui-hardening design proposed a shared read layer; only two functions landed.

**Solution.** Promote `_event_queries.py` into one owned read-projection module exposing typed named projections; each query function collapses to one call plus QML-shape mapping. Keep `mode=ro` at this seam (`EventStore._connect` is read-write). Express the ordering once, cross-referenced to `get_latest_promotion_for_strategy` so GUI and risk layer cannot drift.

**Benefits.** *Locality:* ordering and schemas concentrate in one file. *Leverage:* read models become thin adapters. *Tests:* projection semantics tested once. *AI-navigability:* one answer to where the GUI decides which promotion is current.

**Deletion test.** Delete it → the latest-row join and `mode=ro` boilerplate reappear across 7 connect sites / 11 latest-row constructs over 6 files. Concentrates → earned depth spread thin.

**Severity/Confidence.** medium / 70. ADR: aligns 0010/0049. Partial overlap with AUDIT-006/RM-007 (lifecycle half landed; query-ownership gap is new).

**Verifier's note.** Verdict "overstated" but kept at medium — the canonical `get_latest_promotion_for_strategy` is used by only state_machine and CLI report, and three divergent "latest" encodings exist in the GUI. Risk layer not weakened; no ADR re-litigated. (The verifier's reasoning field was terse here; the grounding confirms the three-encoding divergence and the canonical method's narrow callers.)

---

### Theme C — Hypothetical vs real seams (forks that should be adapters)

---

#### 15. Cache-fetch planning logic is welded into AlpacaDataProvider.get_bars (shallow seam over deep behavior)
**Files:** `data/alpaca_provider.py:92-270`, `data/alpaca_provider.py:301-312`, `tests/milodex/data/test_alpaca_provider.py:395-446`

**Problem.** The hard, bug-prone logic in this module is the cache-fetch *planning*: which date sub-ranges are missing (alpaca_provider.py:124-172), healing a stale daily cache whose tail fell behind today (the 2026-05-28 silent-staleness bug, :138-149), the always-refetch-today rule, mid-range gap scanning with weekend skipping (:150-170 + `_range_has_weekday`), the symbols-by-range batching plan (:182-189). This is interleaved line-by-line with Alpaca-specific request construction (`StockBarsRequest`, `DataFeed.IEX`, `Adjustment.ALL`) and response unpacking. The result is a shallow seam: the `DataProvider` interface is small, but the most regression-prone behaviour behind it has **no independent test surface** — every planning test must stand up the full provider with a mocked SDK and assert against Alpaca request objects.

**Solution.** Extract the cache-read-plan as a pure module: given (cached date set, cached range, requested start/end, today) → the list of `(fetch_start, fetch_end)` ranges and the per-range symbol grouping. The adapter calls the planner, turns each range into a `StockBarsRequest`, and merges. **Do not** extract request construction or response parsing — those are legitimately Alpaca-specific.

**Benefits.** *Locality:* the staleness-bug class and gap-detection edge cases land in one pure module. *Leverage:* a future ADR-0017 research provider that caches reuses the planner. *Tests:* date-in/range-out assertions with no MagicMock SDK scaffolding — the AI-navigable, testable-at-the-interface shape.

**Deletion test.** Delete the planner concept → the gap/staleness complexity reappears in any future provider that caches.

**Severity/Confidence.** medium / 72. ADR: aligns 0017/0002.

**Verifier's note.** Testability/locality kernel survived; the **cross-caller leverage claim did not**. There is exactly **one** caller of this planning logic today — `yahoo_provider.py` is a deliberately minimal non-caching VIX fetch, `SimulatedDataProvider` bypasses the cache. The "concentrates across N callers" deletion signal is therefore single-caller-within-method today. The ADR-0017 future provider is real but **deferred** and would *not* reuse the "identical plan": ADR 0017 mandates a raw-bars-canonical + computed-adjusted-views model for the research provider, which differs materially from Alpaca's cached `Adjustment.ALL` bars. Real testability win behind a small interface; leverage story is future and softer than claimed. Confidence trimmed 82→72.

---

#### 16. Active cache version has two independent definitions of truth (writer constant vs GUI disk-scan)
**Files:** `data/alpaca_provider.py:39`, `data/cache.py:59-68`, `gui/_market_cache.py:14-34`, `gui/market_tape_state.py:82-87`, `gui/performance_state.py:278-282`

**Problem.** No single answer to "which cache version is active." The writer pins it to a compile-time constant `CACHE_VERSION = "v3"`; the GUI read path scans disk for the highest `vN` directory (`_latest_cache_version`) and constructs `ParquetCache(version=that)`. These agree today only by coincidence. On disk right now there is a stale `v2` and a legacy unversioned `1Day` alongside `v3`. On a bump to `v4` before a fetch populates it, the GUI's "highest vN" scan still points at `v3` and silently reads stale prices — or a leftover `v4` scratch dir makes the GUI read a version the writer isn't maintaining. `ParquetCache` owns the `{version}/{timeframe}/{SYMBOL}` layout but does not own "which version is current."

**Solution.** Give `ParquetCache` (or a thin sibling in `data/`) the single authority for resolving the active version — `ParquetCache.open_active(cache_dir)` that knows whether "active" means the writer constant or newest-on-disk, and reconciles the two. The GUI read models call that factory instead of importing a GUI-private regex scanner.

**Benefits.** *Locality:* "what version is live" answerable in the module that owns the version layout. *Leverage:* readers (current GUI, future tooling) get correct resolution for free. *Tests:* `cache_dir` in, version out, with stale-dir/missing-dir cases — no Qt.

**Deletion test.** Delete `_latest_cache_version` → the tape and performance read models both still must answer "which vN" — the logic reappears in two GUI callers, divorced from the writer's constant.

**Severity/Confidence.** medium / 78. ADR: aligns 0002.

**Verifier's note.** Verified; the candidate **undersold** it. There are three writer-side consumers of `CACHE_VERSION` (provider write, `tape_cache_warmup.py`, `run_manifest.py` manifest record) vs two disk-scan readers. The warmup path *writes* the tape's VIX data to `CACHE_VERSION` while the GUI *reads* it via the disagreeing disk-scan — producer and consumer of one dataset resolve "active version" by different rules. Blast radius is the market-tape/benchmark **display** surface only (risk layer/runners/backtests use the provider's `CACHE_VERSION` path, never the GUI scan), so no trade-decision surface is touched. Correctly medium.

---

#### 17. Intraday single-name session flow + Wilder-RSI have no shared seam
**Files:** `strategies/meanrev_rsi2_intraday.py:90`, `strategies/breakout_orb_intraday.py:80`, `strategies/_session_intraday.py:306`, `strategies/_indicators.py:30`

**Problem.** Four equity single-name intraday strategies repeat the evaluate skeleton (guards, half-day defensive close, indicator, exit ladder, entry-window + one-entry guard, sizing, BUY); `_no_signal`/`_exit_decision` are byte-identical across the files, `_entry_price` across five; the one-entry guards re-derive the ET offset inline at four sites, duplicating `_session_intraday._et_time_offset_minutes`; Wilder-RSI exists in copies across the intraday/pullback/decider modules and the public `_indicators.wilder_rsi_series`.

**Solution.** Add an intraday counterpart to `daily_cross_sectional.py` (the AUDIT-004-built daily precedent) owning the session skeleton; strategies supply indicator + entry/exit predicates as closures. Fold in the leaf helpers. Point the **series**-returning RSI site at public `_indicators.wilder_rsi_series` and delete that copy; the two **scalar**-returning sites (`meanrev_rsi2_pullback`, `_decider_features`) get a `.iloc[-1]` adapter, not a straight swap.

**Benefits.** Half-day rule, exit ordering, one-entry semantics, audit shape, RSI formula each change in one place; a new strategy = indicator + predicates; tested once at the interface.

**Deletion test.** Delete the seam → the skeleton + helpers + RSI reappear across callers; complexity concentrates. Calendar primitives already pass.

**Severity/Confidence.** medium / 82. ADR: aligns 0003 + daily-cross-sectional design spec. NEW intraday analog of AUDIT-004 (which named the *daily* flow, now built); RSI dup was un-flagged by any prior review.

**Verifier's note.** Overstated on **scope** and one solution mechanic. The seam is genuinely the **four equity-session** strategies, not "seven single-name intraday/crypto." The two crypto canaries (`meanrev_crypto_rsi2`, `momentum_crypto_ema_cross`) **deliberately** skip the session spine (no half-day, no entry window, no one-entry guard, no time-stop; 2-deep exit ladder; `max_hold_days`) — folding them in would force always-False session predicates for half the callers, a leaky abstraction. They share only leaf helpers + indicators. And the RSI "delete 3 copies" mechanic is wrong for the two scalar callers (they return `float|None`, not a series). Not the pure-function anti-pattern — the seam concentrates the load-bearing invariants (half-day defensive close, exit ordering, one-entry audit shape) where the real maintenance bugs would live.

---

#### 18. Daily vs intraday replay is a branch inside BacktestEngine, not two adapters behind a Simulator seam
**Files:** `backtesting/engine.py:922`, `:937`, `:978`, `:1137`, `backtesting/intraday_simulation.py:1`

**Problem.** `_simulate` dispatches on `if timeframe == Timeframe.DAY_1` to two ~150-line private methods (`_simulate_daily`, `_simulate_intraday`) with genuinely distinct replay structures (date-index bisect slicing vs event-timeline + per-symbol cursors). The shared *mechanics* were correctly pushed into the kernel and `_finalize_simulation` (#241), but the two replay drivers remain private methods selected by an if-branch, not adapters at a seam. Consequences: neither path can be exercised without a full engine; the per-method scaffolding (empty-universe guard, empty-days early return, kernel construction, `tick_held_days`, finalize tail) is duplicated; the unused `timeframe` parameter threaded into `_simulate_daily` "for signature symmetry" is a tell that the two share a contract no type expresses.

**Solution.** Express replay as a small `Simulator` protocol (iterate-the-window producing per-step decision/drain events) with `DailySimulator` and `IntradaySimulator` adapters, each owning only its bar-replay structure and both consuming the existing kernel + `_finalize_simulation`. The shared guards and kernel construction move up to one place. **Do not** pull kernel logic back out — the kernel stays the deep core.

**Deletion test.** Partial. Collapse the dispatch → the date-index path and the cursor/timeline path do **not** merge (distinct complexity reappears → the split is earned). But the *scaffolding* around each vanishes into one place → that duplication is the shallow part worth concentrating. Replay logic deep, surrounding fork shallow.

**Severity/Confidence.** low / 68. ADR: none. Partial overlap #241/AUDIT-005 (mechanics dedup done; replay-driver-seam is the residual).

**Verifier's note.** Verified, modest. The "tell" is verbatim in the source (`timeframe: Timeframe,  # accepted for signature symmetry; unused in body`). No test calls the replay methods directly. Two honest deductions: the "third replay mode for crypto becomes a new adapter" leverage is thin (the intraday timeline is already asset-class-agnostic and replays 24/7 crypto through the existing path), and #241's commit explicitly left the loop cores untouched, so the head scaffolding it left duplicated is a genuine new delta, not a re-report.

---

#### 19. Durable "begin parent run" choreography duplicated between single-run and walk-forward paths
**Files:** `backtesting/engine.py:354`, `:499`, `:524`, `:530`

**Problem.** Starting a durable backtest run is the same two-step choreography in both paths: `reconcile_orphan_backtest_runs(...)` then `append_backtest_run(BacktestRunEvent(... status='running' ...))`. `run()` inlines it; `start_walk_forward_parent_run()` inlines it again. The two event constructions are identical except the metadata blob. The orphan-reconcile-must-precede-append ordering is load-bearing (the WHERE clause would otherwise sweep the freshly-inserted row, documented at engine.py:519-523) and now lives in two places — with the rationale comment on only one of the two copies.

**Solution.** Extract one private `begin_parent_run(metadata)` helper that owns reconcile-then-append and the event assembly; both callers pass their metadata blob. The ordering invariant and its rationale live once.

**Deletion test.** Delete the helper → reconcile + append + event-construction reappears in both callers (today's state). Concentrates across 2 callers → deep enough to extract; the load-bearing ordering invariant raises the value of single ownership.

**Severity/Confidence.** low / 82. ADR: none; #233 made the *close-out* atomic but did not consolidate the *begin* half — new.

**Verifier's note.** Verified, low. Character-for-character identical except metadata; the walk-forward copy carries **no** ordering-trap comment, and the path missing the rationale is the one most likely to be forgotten. Verified via `git show` that #233 consolidated only `finalize_backtest_run`. Small surface, paper-only blast radius — correctly low.

---

### Theme D — Testability via depth (and one structural-drift guard)

---

#### 20. BacktestStructuralRiskEvaluator hand-lists its check subset, drifting from _CHECKS with no guard
**Files:** `risk/policy.py:66`, `risk/evaluator.py:99`, `tests/milodex/risk/test_risk_rules.py:2121`

**Problem.** `_CHECKS` is the canonical ordered tuple of all 16 checks, length-pinned by a test. But `BacktestStructuralRiskEvaluator.evaluate` (policy.py:66-85) re-encodes "which checks are structural" as a hand-written call list of 5 methods, with no link to `_CHECKS`. If a new structural check is added to `_CHECKS`, it silently does **not** apply during backtest ENFORCE replay, and nothing fails — the only ENFORCE test asserts one oversized buy is blocked, which a stale list still passes. "A structural check" has no seam: it's implicit in which method names someone remembered to copy.

**Solution.** Classify each check once at its definition — a `_STRUCTURAL_CHECKS` tag/table adjacent to `_CHECKS` — and have the backtest evaluator iterate the tag instead of a hand-copied list. Use an explicit **tag**, not a derived "wall-clock-free" predicate (see note).

**Deletion test.** Delete the structural list and derive from a tag → the classification concentrates onto the check definitions; the duplicated method-name list vanishes (pure restatement) while the real distinction is preserved as data. Shallow→deep.

**Severity/Confidence.** medium → **low** / 82. ADR: aligns 0030/0008.

**Verifier's note.** Core claim real (the evaluator is live — `engine.py:1351` constructs it for `RiskPolicy.ENFORCE`), but overstated. The hazard is **latent** (fires only when someone adds a new *structural* check, rare; the live set has been stable at 16). The structural-5 are the contiguous tail block (checks 11-15), trivially visible to a reviewer. The candidate's own stated win is the membership test, and a one-line `structural ⊆ _CHECKS` test captures most of the value without restructuring. And "structural == wall-clock-free" is imprecise: `_check_reconciliation_readiness` is also wall-clock-free but self-exempts via `is_backtest` — so the fix must be an explicit tag, not a derived predicate. Low.

---

#### 21. Per-strategy `_validated_parameters` re-checks ranges the loader now enforces (post-P2-04 dead validation)
**Files:** `strategies/meanrev_rsi2_pullback.py:136`, `strategies/loader.py:318`, `strategies/base.py:17`

**Problem.** 17 strategies carry `_validated_parameters` re-validating ranges via if-raise; the same constraints are on `parameter_specs`/`parameter_relations` and enforced at load by `validate_strategy_parameters` (loader.py:318) on every path before evaluate. The range half is dead — but the method also coerces and builds the typed dict, so it's not fully deletable. The duplicate can drift from the authoritative spec.

**Solution.** Reduce the per-strategy step to coercion + typed-dict assembly; strip the range/enum/cross-field re-checks. **Do not** add a half-coercing `coerce_parameters` to the loader (that risks a worse seam) — the clean cut is removing the redundant validators only, leaving coercion in place.

**Deletion test.** Partial: range checks vanish (loader concentrates) → shallow; coercion/typed-dict reappears per caller → stays. Half shallow.

**Severity/Confidence.** low / 82. ADR: aligns 0003/P2-04. Follow-on of #245 (which added declarative constraints across 23 classes but left the old validators in place).

**Verifier's note.** Verified on three fronts — all ten range/enum/cross-field checks in the sampled strategy are mirrored by a loader-enforced constraint; the loader runs on every evaluate path; #245's resolution log confirms it added constraints without stripping validators. Dead code is harmless (loader fires first); payoff is drift-prevention + agent-navigability across 17 files. Hygiene, not friction with teeth.

---

### Theme E — Broker seam polish (stringly-typed surface, brittle signal)

Both are low-severity; the broker module is otherwise one of the deepest, healthiest in the codebase (real two-adapter ABC seam, no Alpaca type leakage, isolated retry).

---

#### 22. get_orders status filter is stringly-typed at the broker seam
**Files:** `broker/client.py:58`, `broker/alpaca_client.py:240-250`, `broker/simulated.py:169-172`, `operations/reconciliation.py:613`, `cli/commands/status.py:84`

**Problem.** `get_orders(status: str = "all")` is a magic string (`{open,closed,all}`) on an otherwise fully-Enum interface. The ABC docstring names the three values only in prose, so the valid set is invisible to type checkers and to an agent reading the signature. Both adapters independently re-implement the string→filter mapping.

**Solution.** Introduce an `OrderQueryStatus` enum (OPEN/CLOSED/ALL) in `models.py` alongside the existing enums and type the parameter as that. Concentrates the queryable-status vocabulary in one declaration the interface advertises.

**Severity/Confidence.** low / 66. ADR: aligns 0006.

**Verifier's note.** Overstated and the correctness story was **refuted**. The headline ("a typo `status='opened'` silently widens reconciliation's open-order snapshot via the ALL fallback") is unreachable: both real callers are typo-immune — `reconciliation.py:613` passes a hardcoded literal `"open"`, and `status.py` is argparse-constrained to `choices=("all","open","closed")` before it reaches the broker. Also the two adapters don't even share the claimed failure mode (`simulated.py` falls back to `[]`, not ALL). The enum doesn't delete the per-adapter SDK-enum mapping either — it converts `str→X` into `Enum→X`, same two homes. Real kernel: the lone stringly-typed enum concept on an all-Enum interface is a legitimate legibility/AI-navigability nit. Keep as low consistency cleanup; drop the hazard framing.

---

#### 23. Broker exception classification is by substring-sniffing of vendor message text
**Files:** `broker/alpaca_client.py:202-212`, `broker/exceptions.py:8-33`, `execution/service.py:192`

**Problem.** The Alpaca adapter decides which broker-agnostic exception to raise by lowercasing the `APIError` message and substring-matching (`forbidden`/`auth` → BrokerAuthError, `insufficient`/`buying power` → InsufficientFundsError, else OrderRejectedError; `connect`/`timeout` → BrokerConnectionError). The exception **type** is load-bearing: `execution/service.py:192` branches on `(OrderRejectedError, InsufficientFundsError)` to record a clean REJECTED outcome vs the fail-loud re-raise. So a wording change in Alpaca's strings — or a new rejection reason / connection error lacking the magic tokens — can silently reclassify, and the audit/finalize path the execution outbox depends on hinges on undocumented vendor copy. `exceptions.py` (which says "broker-agnostic") does not document that classification is heuristic.

**Solution.** Where Alpaca exposes a structured code (`APIError` carries `status_code`/`code`, already used for 429 detection), classify on the code first, fall back to substring-sniffing only when no code is present. At minimum, document at the seam the exact contract execution relies on: `OrderRejectedError | InsufficientFundsError = clean rejection, everything else = fail-loud`.

**Deletion test.** Delete the types + classifier → the rejection/insufficient/auth/connection distinction reappears at the execution callsite and anywhere telling a clean rejection from a fatal error. The taxonomy concentrates real cross-caller behaviour. The deepening is in the classification **signal**, not the types — a "deepen the implementation behind a good interface" opportunity.

**Severity/Confidence.** low / 62. ADR: aligns 0006/0001. NEW (thermo-nuclear flagged missing `client_order_id`, RESOLVED #236, but not the classification signal).

**Verifier's note.** Structural kernel survives; two sub-claims softened. (1) A *new rejection reason* with no tokens still falls through to `OrderRejectedError` — the **safe** default — so a wording change to a genuine rejection does **not** break the audit path. (2) The raw-re-raise connection branch lands on the fail-loud path, not misclassified as a clean rejection. The genuinely dangerous misroute is narrower: an auth- or connection-class `APIError` whose message lacks the tokens gets recorded as REJECTED instead of fail-loud. The classifier (202-212) has **zero** test coverage. Paper-only, single broker today — low, but real and new.

---

### Theme F — Interface-honesty / contract-conformance (cheap, doc-shaped)

---

#### 24. DataProvider interface documents an is_market_open() contract it does not own
**Files:** `data/provider.py:39-47`, `broker/client.py:74`, `execution/service.py:355`

**Problem.** `DataProvider.get_latest_bar`'s docstring tells callers to "check `is_market_open()` if they need to distinguish latest from live." But `is_market_open()` is not on `DataProvider` — it lives on the **broker** interface. The data interface points the caller at a capability that does not exist on this seam; an agent navigating by interface is sent on a false trail.

**Solution.** Reword the docstring to name the actual owner ("the broker's `is_market_open()`; the data provider does not expose market session state"). **Do not** add a method just to satisfy the doc.

**Severity/Confidence.** low / 90. ADR: none (option (a) reinforces ADR 0006 seam separation).

**Verifier's note.** Overstated framing of a one-line docstring fix. The reference is informational, not a contract `get_latest_bar` must satisfy, and the line directly above already states the market-closed staleness behavior in plain English — so a reader is not actually stranded. `is_market_open()` is unique and unambiguously on the broker; the "false trail" costs one grep. True, correctly-grounded, near-zero-risk; marginal legibility value. Low.

---

#### 25. SimulatedDataProvider.get_bars silently ignores the timeframe argument
**Files:** `data/simulated.py:38-58`, `data/provider.py:19-37`, `backtesting/simulation_kernel.py:148`

**Problem.** `DataProvider.get_bars` declares `timeframe` as a required, meaning-bearing parameter; `AlpacaDataProvider` honors it. `SimulatedDataProvider` takes the same parameter and discards it (`# noqa: ARG002`), returning whatever bars were prefetched regardless of timeframe. The two adapters at the same seam honor the interface differently — a caller passing `MINUTE_5` to a simulated provider holding daily bars gets daily bars with no error.

**Solution.** Either store the timeframe the `all_bars` represent and raise on a mismatched request (the safer choice, consistent with `ParquetCache.merge`'s fail-loud posture), or document on the ABC that the simulated provider is single-timeframe-by-construction.

**Severity/Confidence.** low / 68. ADR: aligns 0006.

**Verifier's note.** Overstated — the method has **zero production callers**. The sim provider is wired only into `ExecutionService`, which calls `get_latest_bar`/`set_simulation_day`, never `get_bars`; the engine's `get_bars` runs on the real injected provider. The only `get_bars` callers are tests. The "latent intraday-backtest trap" requires both future multi-timeframe wiring (doesn't exist) and an inconsistent timeframe (impossible today). The recommended **assert** gold-plates a dead-in-production path; the salvageable nub is a one-line docstring ("single-timeframe by construction; timeframe accepted-but-fixed"). Low, doc-only.

---

### Theme G — Cross-cutting layering

---

#### 26. KillSwitchStateStore misplaced in execution causes a runtime risk→execution import and a cycle
**Files:** `execution/state.py:22`, `risk/profile_activation.py:13`, `operations/reconciliation.py:934`

**Problem.** The kill switch is risk-owned, but its store lives in `execution/state.py:22`. `risk/profile_activation.py:13` **runtime-imports** it from execution (used at :139), contradicting ADR 0019 §18 ("risk/ imports nothing from execution/. Verified by import audit"). The resulting cycle (profile_activation → execution.state → execution/__init__ pulls ExecutionService → reconciliation → back to profile_activation) is papered over by a lazy import at reconciliation.py:934.

**Solution.** Move the store to **`core/`** (shared infra) — not `risk/` — and amend ADR 0019. The store depends only on `core.event_store` and is shared by cli/gui/backtest/execution/risk; relocating to `risk/` would manufacture new risk→cli/gui/backtest consumer edges, arguably worse than the single cycle today.

**Deletion test.** N/A as depth — this is a **relocation**, not a concentration. The "loop reappears in 6 callers" is really the 6 import sites; moving the module deletes one bad edge.

**Severity/Confidence.** medium → **low** / 72. ADR: **reopens 0019** (the ADR became internally inconsistent the moment a risk-module service needed the store).

**Verifier's note.** Kernel real (the cycle is self-documented at reconciliation.py:934; `profile_activation.py:13` is the **only** runtime risk→execution edge — evaluator's execution imports are all `TYPE_CHECKING`-only; ADR 0019 §18's verification note is now stale/false). But severity overshoots — one documented lazy import on a paper-stage project, a legibility wart, not correctness. And the fix framing is half-wrong: ADR 0019 *Decision item 2* deliberately placed `KillSwitchState` (the type) in execution/, read back by risk by reference (blessed as "correct"). The store co-habits that file. The real finding is that ADR 0019 is now internally inconsistent (invariant: risk⊥execution; placement: kill-switch state in execution, read by risk) — both cannot hold once a risk-module *service* needs the store. Cleanest resolution: store → `core/`, plus an explicit ADR 0019 amendment. *Sacred-layer-adjacent (risk module touch — extra care).*

---

### Theme H — Test-surface depth (assert behavior, not source shape)

The recurring anti-pattern across the GUI/strategy tests: a brittle layer of `read_text()`-then-`in src` assertions that pin **implementation tokens** rather than reaching the runtime interface. The project already built the deep harness (QQuickView + offscreen + event-pump + tree-walk + `invokeMethod`) that fixes this; the deepening is to finish migrating onto it.

---

#### 27. Reachability tests assert QML source tokens, not runtime reachability
**Files:** `tests/milodex/gui/test_kill_switch_reset_reachability.py:59/174/259`, `tests/milodex/gui/test_reconcile_affordance_reachability.py:37`, `tests/milodex/gui/test_bench_confirmation_modal_behavior.py:88`, `src/milodex/gui/operational_state.py:426`

**Problem.** These two suites exist **because** of G-P1-1: the kill-switch reset became unreachable in the running GUI after a nav rework even though every Python seam was intact. The regression net then encodes ~24 assertions that grep QML source strings (`signal killSwitchResetClicked()`, `root.killSwitchActive`, `var ok = OperationalState.reset_kill_switch(`, `killSwitchResetModal.open = true`). The file's own docstring concedes the gap ("without simulating mouse events … deferred to manual operator verification"). So the exact failure class that motivated the file — a declared-but-unconnected signal, a handler wired to the wrong id, a posture MouseArea that no longer reaches the modal — passes every assertion as long as the literal strings survive. The interface under test is the runtime QML object tree; the test surface is the `.qml` file's bytes.

**Solution.** Port the reachability assertions onto a shared version of the existing `test_bench_confirmation_modal_behavior.py` harness: instantiate `Main.qml` (or the `RiskStrip`/`RiskOfficeDrawer` component) with a record-only `OperationalState` fake, emit the entry signal, assert the modal's `open` flips, and that typing the token then clicking reset records exactly one `reset_kill_switch(token)` call and a false return keeps the modal open. Keep a 1-2 line static guard for genuinely static facts (file deleted, qmldir registration); delete the token-shape pins. Lift the harness builder into `tests/milodex/gui/_qml_harness.py` so all three suites share one seam.

**Deletion test.** Delete the ~24 grep methods → nothing is lost in real coverage (the slot is already behaviorally tested in `test_operational_state.py`; the QML compiles under the load-smoke test). The string-literal complexity **vanishes** rather than reappearing → shallow pass-through assertions, not earned depth.

**Severity/Confidence.** high → **medium** / 82. ADR: aligns 0005/0033.

**Verifier's note.** Kernel verified — the two suites are 100% source greps with zero runtime drive (`findChild`/`invokeMethod`/`QQuickView` appear 0 times outside docstrings), and the harness they should port onto genuinely exists (two adapters: the bench-modal behavior test and a second tree-walk variant in `test_desk_layout_regression.py`). Downgraded from high because the actual safety mechanism (the reset slot's token gate, ADR-0005 enforcement) **is** fully behavior-tested at `test_operational_state.py:317-443` — what's untested is only the QML→Python connection, and that gap is already backstopped by the parked "HR-4 manual trip+reset" operator verification. Medium.

---

#### 28. test_qml_load_smoke.py conflates a deep load-smoke seam with ~38 shallow QML-source token pins
**Files:** `tests/milodex/gui/test_qml_load_smoke.py:236/367/614/1289/1551`, `src/milodex/gui/bench_actions.py:178`

**Problem.** The file's headline value is genuinely deep: `test_surface_qml_loads_clean` and the singleton probes spawn a real engine and catch the load-time "Type X unavailable" cascade pytest otherwise never sees. But 28 of its 45 test functions then `read_text` the `.qml` and assert substrings: anchor-chain ids (`anchors.right: statusCol.left`), brace-depth parsing of a Flickable to prove a footer is a sibling, `Math.min(root._modalIntrinsicHeight, root._modalMaxHeight)`, exact bridge call spellings (`BenchCommandBridge.submitBacktestAsync(`). Many duplicate copy/spec facts whose true owner is `bench_actions.py:178` (`ACTION_KIND_SPECS`) and are **also** pinned behaviorally in `test_bench_confirmation_modal_behavior.py` — the file's own comments admit this. A layout refactor that preserves on-screen result still breaks them; a wiring bug that preserves the string still passes.

**Solution.** Split along the seam: keep the engine-load smoke tests + singleton probes (the deep part); move the behavior-shaped pins (modal copy, submit affordances, section labels, draft-preview state) into `test_bench_confirmation_modal_behavior.py` where the harness drives them through the rendered tree; demote the pure layout/anchor-geometry pins to a small explicitly-labelled "static structural guards" module (or delete the ones already covered behaviorally). The ADR-0051 allowlist guard is legitimately static — keep it. **Do not** extract a pure helper just to grep more cheaply — move the assertion to the runtime interface.

**Deletion test.** Delete the anchor-geometry and modal-copy grep tests → the copy/affordance facts reappear as failures in the behavioral test (already covered) → grep version is shallow. Delete the load-smoke tests → the "Type X unavailable" cascade becomes invisible again → load-smoke half is deep and must stay. The file mixes one deep module with many shallow ones.

**Severity/Confidence.** medium / 80. ADR: aligns 0049/0051. Partial overlap #243 (added the behavioral harness, converted a handful of pins; did not split the file or migrate the bulk).

**Verifier's note.** Verified — counts match exactly (45 functions, 28 read_text greps, ~6 engine spawns in a 1736-line file), and the file's own comments admit copy facts are Python-owned and re-covered behaviorally. xcut-test hygiene on a paper-bounded surface; the net deepening is locality concentration (one Python owner, one behavioral test) plus shrinking the load-smoke seam to its deep core. Correctly avoids the named anti-pattern. Medium ceiling, not high.

---

#### 29 (test variant). Two decision-layer-seam invariants grep src/ for forbidden tokens instead of asserting the seam's runtime contract
**Files:** `tests/milodex/strategies/test_decision_layer_seam.py:35`, `:66`, `src/milodex/strategies/base.py:1`

**Problem.** `test_reasoning_module_has_no_type_dispatch` reads `base.py` source and asserts literal substrings (`'kind =='`, `'isinstance(decider'`) are absent; `test_non_rule_kwargs_used_only_by_the_two_deciders` globs `strategies/*.py` and greps for `kind=`, `score=`. The actual invariant — "both deciders and all rule strategies traverse one type-free `asdict()` and rule blobs gain no new keys" — is already proven behaviorally in the same file. A type-dispatch reintroduced via a dict lookup or a `match` statement that doesn't use the literal `kind ==` would pass; a benign rename trips them.

**Solution.** Keep the two behavioral tests (the deep seam test). For the file-wide kwarg scan, prefer a behavioral generalization over `build_default_registry`; if a static no-dispatch guard is still wanted, narrow it to a single AST check (no `isinstance`/`match` on a reasoning variable) and label it explicitly as a static guard.

**Severity/Confidence.** low → **dropped to 55 confidence**. ADR: aligns 0015/0030.

**Verifier's note.** Overstated — the **deletion test is false as stated**. The behavioral siblings hand-construct exactly one rule blob and one decider blob; they do **not** iterate strategy files, so the file-wide grep at :66 (scanning ~25 modules for accidental non-rule-kwarg population) provides real, non-overlapping coverage. Delete it and a future rule strategy that accidentally wrote `score=...` goes uncaught. The proposed registry-iteration replacement is under-specified (a Strategy exposes no canonical reasoning blob without running `evaluate()` against fabricated context). The file docstring explicitly labels these the "mechanical" companions to the "behavioral" tests for adversarial verifiers — i.e. deliberate defense-in-depth, not a coverage lie. Real-but-minor brittleness; kept as a low note, not a coverage gap.

---

### Theme I — AI-navigability (the docs that an agent reads first)

These are documentation-shaped, but on this project AI-navigability is founder priority #3 and the codebase's stated thesis is agent-driven development — so a stale map an agent reads first has real cost.

---

#### 30. Absent domain-vocabulary anchor (CONTEXT.md)
**Files:** `docs/SRS.md:25-32`, `docs/README.md:1-95`, `docs/FOUNDER_INTENT.md`, `CLAUDE.md:15-40`, `docs/strategy-families.md`

**Problem.** The single highest-leverage navigability artifact — a domain-vocabulary anchor — does not exist. The closest thing, SRS "Key Terms," defines exactly **five** terms and omits the most load-bearing framings. *Harness* spans 10 docs but is anchored as a concept in none; *frozen manifest* spans 8 docs + 2 ADRs; *attribution* 6 docs; *reconciliation* 10. A reader must read FOUNDER_INTENT for the Harness thesis, SRS for 5 terms, CLAUDE.md for the module list, and triangulate across 55 ADRs to assemble the domain vocab. **That triangulation is the navigability cost** — and the `improve-codebase-architecture` skill running this very audit expects a `CONTEXT.md` it cannot find ("Create the file lazily if it doesn't exist").

**Solution.** Add a single `docs/CONTEXT.md` — a flat glossary, one sentence + owning module dir + owning ADR + 1-2 key type/function names per term (~30-40 entries). A map, not a spec — link to owners, restate no thresholds (the authority order forbids it).

**Deletion test.** Deleting the (hypothetical) CONTEXT.md makes per-concept triangulation reappear in every reader/agent — the grounding agent for this audit demonstrably had to perform exactly that, hand-assembling a 19-entry "DOMAIN VOCAB" section. Complexity concentrates → earned, deep artifact.

**Severity/Confidence.** medium / 82. ADR: none (aligns with the Document Authority Order — link to owners, restate nothing).

**Verifier's note.** Kernel verified: no CONTEXT.md exists; the skill (SKILL.md:3/56/68, INTERFACE-DESIGN.md:30) genuinely expects it and is working around its absence; SRS Key Terms is exactly 5; 55 ADRs confirmed. One scope caution: the candidate invents its own "concept → module → ADR → file:line crosswalk + CI test" format, but the skill's actual `CONTEXT-FORMAT.md` specifies a *vocabulary glossary* (term + definition + Avoid-aliases + Relationships + Example dialogue), not a code-navigability index. Both are useful; the CI-test benefit is the candidate's addition. Build the glossary the skill consumes; the crosswalk is a nice-to-have layered on top.

---

#### 31. CLAUDE.md architecture census is stale: claims 12 modules, the tree has 13; operations/ is invisible
**Files:** `CLAUDE.md:17-30`, `src/milodex/operations/__init__.py`, `src/milodex/operations/reconciliation.py`

**Problem.** CLAUDE.md:17 declares "Twelve modules" and lists 12; the tree has 13 top-level packages. The missing one is `operations/` — not a leaf: it holds `reconciliation.py` (44KB), `freshness.py`, `maintenance.py`, and it sits on the trust spine. `execution/service.py` imports `latest_readiness`, calls it, and injects the verdict into the `EvaluationContext` the risk evaluator consumes. AGENTS.md routes all agents to CLAUDE.md as the single canonical guide — so the map an AI reads first omits a module directly on the risk-gate input path.

**Solution.** Add an `operations/` bullet and correct "Twelve" → "Thirteen." Two lines.

**Severity/Confidence.** medium → **low** / 90. ADR: doc-accuracy (CLAUDE.md sits outside the authority hierarchy per adr/README.md:24).

**Verifier's note.** Core finding verified (12 claimed, 13 exist; `operations/` on the execution→risk readiness path). Two citation errors deflate scope: the candidate lists `risk/attribution.py` and `core/trade_status.py` as `operations/` importers — **both false** (attribution imports only `core.trade_status`; trade_status imports nothing from operations — the real edge runs reconciliation→attribution). Actual src importers are 6, and the genuinely load-bearing path is just execution→risk. A two-line doc-census typo fix — low.

---

#### 32. ADR index (authority rank-1 entry point) is stale: stops at 0052; 0053/0054/0055 unreachable
**Files:** `docs/adr/README.md:28-84`, `docs/README.md:82`, `docs/adr/0053…md`, `0054…md`, `0055…md`

**Problem.** `docs/adr/README.md` establishes ADRs as authority rank 1, and its Index table is the discovery surface. The table ends at 0052 — it does not list 0053 (backtest equity snapshots — the +9865% trust-bug fix), 0054 (risk profiles — a sacred-layer concept), or 0055 (per-strategy position ledger — the concurrency-attribution contract). All three exist and are Accepted. Compounding it, `docs/README.md:82` still calls the corpus "54 ADRs" (actual: 55). An agent that trusts the index — as the authority order instructs — concludes these decisions don't exist.

**Solution.** Append rows for 0053/0054/0055 to the index, correct the count to 55. Optionally a CI assertion that index-row-count == `NNNN-*.md` file count, so this drift class fails loudly.

**Deletion test.** The index concentrates "what decisions exist and their status" for every reader; deleting it forces `ls` + open 55 files. A deep artifact whose value is proportional to its completeness — a 3-row gap in a 55-row authority index is high-impact precisely because it's trusted.

**Severity/Confidence.** medium → **low** / 95. ADR: upholds the Document Authority Order.

**Verifier's note.** Verified (index ends at 0052; the three ADRs appear nowhere in it; no row-count test exists). One citation error: the "54" string is at `docs/README.md:82`, not :91 (line 91 is prose that says "Do not list ADRs individually here"). Severity is doc-drift, not architecture friction — 3 table rows + one number — but the discoverability cost on an agent-driven project is real. Low.

---

## Recommended sequencing (by PR size, leverage, risk)

**Wave 1 — leaked sacred-layer invariants (do first; extra care; small, surgical).**
- **PR A (tiny, sacred-adjacent):** `effective_stage` single resolution on `EvaluationContext`, read by both evaluator checks and the service hash-lookup. Fixes #1 + #2 together. Highest locality-per-line; eliminates the `or` vs `is not None` discrepancy by construction.
- **PR B (small, sacred-adjacent):** `hash_canonical_config(canonical)` in promotion, called by all four hash sites (#4). Add the freeze-hash == runtime-hash property test.
- **PR C (small, sacred-adjacent):** `daily_loss_status` + `bar_is_stale` predicates in `risk/`, shared by veto checks and disable-condition evaluators (#3). Add the parity test the suite currently lacks.

**Wave 2 — shallow-by-duplication consolidations (decent, unblock others).**
- **PR D (small):** one `strategy-id→config-path` resolver in `loader.py`, `manifest.py` re-exports, GUI off the CLI module (#7). Highest confidence; unblocks the GUI layering cleanup. Do **not** fold in universe scans.
- **PR E (small):** one universe-manifest resolver in `strategies/` (returns dict + path), four callers become field extractors, each keeps its not-found policy (#5/#6).
- **PR F (small):** `data_freshness_verdict` in `operations/freshness.py`, consumed by bench + CLI report; fold in `_aware` (#9/#10). Lands the GUI-freshness-badge reuse point.
- **PR G (decent):** lift `_DefaultWorkflowReadiness` to `operations/workflow_readiness.py` (#8); shrinks bench.py ~250 LOC, gives the readiness verdict a test surface. Depends on PR F (shares the freshness verdict).

**Wave 3 — analytics/promotion verdict ownership (small–decent, audit-sensitive).**
- **PR H (small, audit-sensitive):** one OOS-aggregate decoder on the None-on-missing rule; gate + CLI report + GUI helper consume it (#11). Correct the fix direction — do **not** route the gate through the equity-curve fallback.
- **PR I (small, audit-sensitive):** named-mode daily-return interface; `_aggregate_oos` and correlation call it (#12).
- **PR J (small):** thin GUI gate-verdict adapter mapping `evaluate_research_target.failures` → S/D/N (#13).

**Wave 4 — GUI read layer + test-surface depth (decent–large).**
- **PR K (decent):** promote `_event_queries.py` into the owned read-projection module; migrate the 7 read models (#14).
- **PR L (small):** lift the QQuickView harness into `tests/milodex/gui/_qml_harness.py` and migrate the two reachability suites onto it (#27).
- **PR M (decent):** split `test_qml_load_smoke.py` along the load-smoke vs token-pin seam (#28).

**Wave 5 — structural / data / layering polish (small, low-risk).**
- **PR N (small):** `_STRUCTURAL_CHECKS` tag adjacent to `_CHECKS` + membership test (#20).
- **PR O (small):** extract the cache-read-plan as a pure module (#15); active-cache-version factory on `ParquetCache` (#16).
- **PR P (small):** strip redundant per-strategy validators, keep coercion (#21).
- **PR Q (small):** intraday session-flow seam for the four equity strategies + RSI consolidation (#17).
- **PR R (tiny):** `begin_parent_run` helper (#19); `Simulator` adapter split (#18) only if PR R touches the area anyway.
- **PR S (small, sacred-adjacent, reopens ADR 0019):** move `KillSwitchStateStore` → `core/`, amend ADR 0019 (#26). Needs an explicit ADR amendment, not a silent move.

**Wave 6 — broker + interface-honesty + docs (tiny, near-zero risk).**
- **PR T (tiny):** `OrderQueryStatus` enum (#22); broker exception classify-on-code-first + seam docstring (#23); `is_market_open()` docstring fix (#24); simulated-provider timeframe docstring (#25).
- **PR U (tiny):** `docs/CONTEXT.md` glossary (#30); CLAUDE.md census fix (#31); ADR index + count fix, optional CI guard (#32). Low cost, real AI-navigability payoff; PR U could go first as a warm-up.

Sacred-layer-adjacent PRs (A, B, C, S): risk/promotion/execution touch — surgical diffs only, no adjacent improvement, keep the veto behavior byte-identical, full risk suite must read clean before claiming green.

---

## Per-area health summary

- **commands** — Healthy adapter over governance seams; bench.py is NOT a god-facade (governance genuinely delegates to `promotion/`). One mis-placed decision module (`_DefaultWorkflowReadiness`) + two structural-repetition opportunities.
- **core** — Deep and healthy. The EventStore "god-object" hypothesis was dismissed (writers already atomic where it matters).
- **backtesting** — Deep and healthy; two prior reviews already cut the worst seams. Residual friction at the edges (universe scans, replay-driver fork, daily-return derivation, begin-run dup) — all locality/testability, none touch risk/promotion/audit invariants.
- **risk** — Deepest, healthiest sacred surface. `evaluate()` is a small interface over 16 fail-closed checks with a fail-closed wrapper. All friction is leaked coordination seams (effective_stage, daily-loss/staleness math, structural-check subset) — never the veto itself.
- **execution** — Deep. The one finding is the shared effective_stage leak with risk (#2).
- **strategies** — Healthy; config-driven discovery works. Two opportunities: intraday session seam (#17), dead per-strategy validators (#21).
- **promotion** — Healthy and largely deep. Hash-recipe duplication (#4) and the read-side gate-verdict leak (#13).
- **data** — One of the healthier modules; `DataProvider` ABC is a real two-adapter seam, `ParquetCache.merge` earns its depth. Friction: cache-plan welded into the adapter (#15), two cache-version truths (#16), two interface-honesty nits (#24/#25).
- **analytics** — Healthy; one verdict-ownership leak (oos_aggregate decode, #11).
- **operations** — Healthy; `latest_readiness` is the deep model. One shallow constant that should grow a verdict (#10).
- **broker** — One of the deepest, healthiest modules; real two-adapter ABC, no Alpaca type leakage, isolated retry. Only stringly-typed surface polish (#22) and brittle exception signal (#23).
- **cli** — Healthy; the duplications it participates in are owned elsewhere (freshness verdict, resolver).
- **gui** — Mechanically healthy post gui-hardening; the scattered read layer (#14) is the one structural gap.
- **xcut-layering** — Mostly healthy; lowest layers import nothing upward, chokepoint intact. Two frictions: resolver dup (#7), kill-switch-store cycle (#26).
- **xcut-tests** — High-stakes Python seams ARE tested at their real interfaces. One cross-cutting anti-pattern: source-substring assertions where a runtime harness already exists (#27/#28); one false-positive dropped to a note (#29).
- **xcut-config** — Universe-manifest read loop is the one duplicated config-read shape (#6).
- **xcut-navigability** — Healthier than the "30 overlapping reviews = sprawl" hypothesis; docs/README is a well-maintained map. The gaps are meta: no CONTEXT.md anchor (#30), stale CLAUDE.md census (#31), stale ADR index (#32).

---

## Appendix — dismissed candidates (refuted, left out of the body)

- **core / "EventStore writes lack a transaction seam"** — Dismissed. (1) Citation false: `append_manifest_and_promotion`'s docstring explicitly states the shared-connection rollback contract the candidate called "unstated." (2) Deletion test fails — a `_transaction()` helper would consolidate commit/rollback boilerplate, but the load-bearing decision (which statements group atomically) is inherently per-method and stays there; only boilerplate moves. The named anti-pattern. (3) The genuine risk (explanation+trade atomicity) is already solved with an explicit `try/except BaseException: rollback` (#236); "implicit rollback" is the idiomatic, correct `with connection` pattern. Self-rated low/55; corrected to 15.

- **operations / "Per-strategy ledger fold duplicated as raw SQL"** — Dismissed. The fold genuinely exists at **two** sites (not three — the third cited site is a shallow id-enumeration query with no paper-predicate/order-id logic, which inflated the "triplicated" count). The "NEW" claim is false: thermo-nuclear P2-10 cited the exact callsite (line-drifted 948→992), its written remedy is verbatim the candidate's proposal, and #237 deliberately scoped down to constant+parity-test while the adjudication **consciously deferred** the fuller TradeLedger extraction behind P1-02 (which reshapes the same tables). Already adjudicated and parked behind a sequencing decision — not a new target. Also overlaps open P2-11.

---

## Note on CONTEXT.md (the skill's domain anchor)

`CONTEXT.md` is **absent** (verified: no file at root or `docs/`). It matters here more than on a typical project: the `improve-codebase-architecture` skill that ran this audit expects it as its domain-vocabulary source and is structurally working around its absence (the grounding agent hand-assembled a 19-entry vocab section to do the job). On a codebase whose stated thesis is agent-driven development and whose #3 priority is AI-navigability, the missing anchor is a recurring tax on every future agent. It is captured as finding #30; building the glossary the skill consumes (term + definition + owning module + ADR, a map not a spec) is the cheapest high-leverage navigability win in this report, and a reasonable PR-U warm-up before the structural work.
