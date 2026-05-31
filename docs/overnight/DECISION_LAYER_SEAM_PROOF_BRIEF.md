# Overnight Run Brief — Decision-Layer Substitutability Proof

**Type:** Launch brief (input). The run produces a separate after-action report (AAR) in this directory.
**Scope class:** backtest-only · additive · no network · no sacred-layer mutation · branch-isolated · committed-not-pushed.
**Date authored:** 2026-05-30. **Revised** 2026-05-30 after an adversarial code-grounded review (three blockers fixed; see the AAR-author note at the end).

> **You (the overnight agent) have none of the authoring conversation's context.**
> Everything you need is in this brief and the files it cites. Re-ground against the
> cited files before acting — line numbers may have drifted. Do not infer scope
> beyond what is written here. When in doubt, STOP and leave a question for the
> morning rather than guessing. This brief was hardened against an adversarial review;
> the specific values below (the backtest window, the `asdict()` rule, the risk-veto
> unit test) are load-bearing — follow them exactly, do not "improve" them.

---

## Mission (one sentence)

Prove that Milodex's existing `Strategy` decision contract honestly hosts
**non-rule** decision techniques — by generalizing `DecisionReasoning` once and
adding **two paradigm-distinct deterministic non-rule deciders** behind the
*unchanged* contract — backtest-only, capability-proof only, with **exhaustive
adversarial verification** that nothing downstream regresses and the seam is not
secretly hard-coded to rules.

## Why this matters (thesis grounding — read, do not skip)

Per [`docs/architecture/2026-05-30-harness-capability-axes.md`](../architecture/2026-05-30-harness-capability-axes.md),
**axis 3 (decision-layer type) is the project's central claim**: the decision
layer is substitutable (rules today, ML and frontier models later) while the
harness — risk veto, promotion lifecycle, evidence, audit — stays singular and
governs every technique identically. Today **only config-driven rules are proven.**
This run is the first non-rule proof. The deliverable is *the proof that the seam
generalizes*, not a profitable strategy.

## The governing discipline (honor it exactly)

- **One axis at a time.** This run varies only the *decision paradigm*. Hold every
  other axis at its proven setting: US-equity asset class, daily tempo, long-only
  intent shape, market-order execution, simulated broker, cached OHLCV, stateless.
- **The value is the seam, not the deciders.** The deciders are deliberately simple.
  **Negative or random backtest performance is expected and fine.** An agent that
  tunes a decider for Sharpe has misunderstood the task.
- **Two deciders, no more.** They prove the seam is not fitted to one shape. A third
  adds nothing. Do not add one.
- **Capability-proven, not alpha.** These are `lifecycle_exempt`, `stage: backtest`,
  "mechanics not alpha." **Never promote to paper.**

---

## Grounding — verified facts (an adversarial review confirmed these against the code)

The decision contract is **already** abstract. Do not invent a new interface.

- [`src/milodex/strategies/base.py`](../../src/milodex/strategies/base.py) —
  `class Strategy(ABC)`, single abstract method
  `evaluate(bars, context) -> StrategyDecision`. Already decoupled from risk/execution.
- `StrategyDecision = (intents, reasoning)`. `DecisionReasoning` (base.py:54) is
  **rule-shaped** (`rule`, `triggering_values`, `threshold`, `ranking`,
  `rejected_alternatives`, `narrative`, `extras`); its docstring says the shape
  freezes "when a **second consumer** emerges" — your non-rule deciders are it.
- **All 14 strategies construct `DecisionReasoning(...)` with keyword args only**
  (verified) — adding *trailing optional* fields cannot break any constructor.
  `rule` and `narrative` are always passed, so they stay **required**; do **not**
  make them optional (gratuitous, non-surgical).
- **Consumers of the serialized reasoning** (verified — there is no GUI consumer;
  do not spend verification budget looking for one): `execution/service.py:246,535`
  call `reasoning.asdict()` only; `analytics/reports.py:180` reads defensively
  (`.get("rule","")`, `.get("narrative","")`); `runner.py`/`simulation_kernel.py`
  pass the object by reference. The only thing that pins the exact serialized shape
  is the golden test `tests/milodex/strategies/test_base_reasoning.py:36-44`
  (see also its forward-looking warning at `:47-65`).
- **`asdict()` (base.py:99-101) is plain `dataclasses.asdict`** — every field,
  including defaulted ones, is serialized. This is the crux of the byte-identical
  constraint below.
- Reasoning is persisted **only** as a free-form JSON blob
  (`explanations.context_json TEXT`, migration 001:27). **No typed column, no
  schema migration needed, ever, for this work.**
- **Registration is in-scope:** add both deciders to `build_default_registry()` at
  [`strategies/loader.py:322-370`](../../src/milodex/strategies/loader.py) + a
  `configs/*.yaml` each. `loader.py` is **not** sacred and **not** one of the 14
  strategy files — editing it is expected. There is **no allowed-family list**; a
  new family string (`scored`, `tree`) is accepted. The only id check is
  `id == "{family}.{template}.{variant}.v{version}"` (loader.py:400). **If you think
  you must edit anything under `risk/`, `execution/service.py`, `promotion/`, or a
  migration to register or run these — you've taken a wrong turn. STOP.**
- **Backtest risk reality (critical):** the backtest path runs **`NullRiskEvaluator`
  (always-allow) by design** — `engine.py:1305-1311`, default `RiskPolicy.BYPASS`;
  CLAUDE.md: "backtesting is intentionally below the risk layer — risk is enforced
  at promotion, not simulation." **The real `RiskEvaluator` with veto power is NOT
  on the backtest path.** Consequences are baked into §"What to build" and
  "Verification" below — read them; do not claim the backtest demonstrates a risk
  veto (it cannot, by design).
- **Data/cache reality (critical for the no-network rule):** the CLI backtest wires
  `AlpacaDataProvider`, which serves from cache **only** when the requested range
  (including a hidden 365–600-day warmup the engine subtracts before fetching) is
  fully within the on-disk cache. Cache lives at `market_cache/v3/1Day/`. For
  `sector_etfs` (11 syms) and `curated_largecap` (42 syms): floor **2020-07-27**,
  tail **2026-05-18**. Any range that underflows the floor or exceeds the tail
  triggers a **live network fetch** — a guardrail violation. The safe window is
  pinned in §"What to build" §4. There is **no offline flag**; correct window choice
  is the only defense.

---

## What to build

### 1. Generalize `DecisionReasoning` (load-bearing — do this first, verify, then build on it)

Extend `DecisionReasoning` so it can honestly represent a non-rule decision (a
continuous score + feature contributions; a discrete decision path) **without
changing the serialized blob for the 14 rule strategies**.

- **Mandated shape (do not deviate):** keep `rule` and `narrative` required; add the
  new non-rule fields as **optional with neutral defaults** (e.g. `kind: str | None
  = None`, `score: float | None = None`, `decision_path: tuple[...] | None = None`,
  `feature_contributions: Mapping | None = None`), **and override `asdict()` to OMIT
  any field whose value equals its default.** This makes rule strategies serialize
  the *identical* legacy 7-key dict (so `test_base_reasoning.py:36-44` stays green
  **untouched** — do NOT edit that test to make it pass; you may ADD a new test case
  for the non-rule shape). Non-rule fields appear in the blob only when populated.
- "Byte-identical" (verification #2) means: **the serialized payload of every rule
  strategy is unchanged.** It does NOT mean the dataclass is unchanged. The
  `asdict()`-omit-defaults override is what makes both true at once.

### 2. Decider A — linear scored / ranking (deterministic, genuinely non-rule)

New `Strategy` subclass + YAML. Score = weighted sum of **normalized features
already computed by existing families** (momentum, RSI, MA-distance, realized vol —
**no new data source**). Coefficients in config (so `config_hash` covers
reproducibility — **no model artifact**). Cross-sectional rank → top-N entry.
- **Genuineness bar (this is the proof, not a formality):** the reasoning must carry
  a **continuous `score`** that takes **≥3 distinct values across candidates and
  drives a rank ordering** — not a single boolean threshold. *A lone comparison
  against one threshold is a rule, not a score, and fails the proof.*

### 3. Decider B — decision-tree / bucketed-lookup (deterministic, genuinely non-rule)

New `Strategy` subclass + YAML. Discretize the same features into buckets, traverse
a fixed depth-limited tree / frozen lookup table to a leaf → action. Tree/table in
config. Output: discrete leaf; reasoning: the decision path.
- **Genuineness bar:** the tree must traverse **≥2 split levels to ≥3 reachable
  distinct leaves** over the backtest. *A depth-1 / 2-leaf tree IS a rule and fails
  the proof.*

> A and B must be **structurally different** (continuous-linear vs discrete-tree) —
> that difference is the whole proof. Same universe, window, everything-else.

### 4. Backtest both through the real path — PINNED window, offline

Register both; run **plain single-period** backtests (NOT `--walk-forward` — it
re-underflows the cache floor) through the real engine:

- **Window (do not change):** `--start 2023-01-03 --end 2026-05-16`.
- **Universe:** `universe.sector_etfs_spdr.v1` (or `universe.curated_largecap.v2`).
- These dates keep `run_start − warmup` above the cache floor (2020-07-27) and `end`
  at/below the cache tail (2026-05-18), so the run is **fully offline**.
- Both decisions flow through the real `ExecutionService` / engine, which **injects
  `NullRiskEvaluator` (BYPASS) by design** — so the backtest proves the *intent
  plumbing* is identical to a rule strategy (evaluate → intents → execution →
  audit), **not** a risk veto. The veto is proven separately (verification #5).
- The run **will append `backtest_run_id`-keyed `explanations` + `backtest_runs`
  rows to the live `data/milodex.db`** — expected and benign (`stage: backtest`, no
  promotion). Note it in the AAR so the morning reviewer isn't surprised by new rows.
- **Evidence = `backtest_runs` rows only** (metrics in `metadata_json`, existing
  schema). Do **not** create a promotion manifest or write to `promotion/`.
  `lifecycle_exempt` / `stage: backtest` are documentation labels (AAR + config),
  **not** event-store/promotion writes, for this run.

---

## Hard constraints / non-goals (unattended-safety boundary — non-negotiable)

- **No network.** If the engine emits any `data.alpaca.markets` request, or you see a
  `ProxyError` / connection error, the window is wrong → **STOP**, leave a morning
  question; do not let it fetch. On any **cache miss**, STOP — never let the provider
  fall back to a live fetch. Confirm the universe+window is fully cache-resident
  before running. (Optional hard guard: run backtests under a dead proxy —
  `HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 NO_PROXY=` — so any
  network attempt fails loudly instead of silently succeeding.)
- **No broker/credential access. No live/micro_live/paper path. Backtest-only.**
- **Do not mutate any sacred-layer file** — `risk/`, `execution/service.py`,
  `promotion/` state machine, or the event-store **schema** — except to *read*.
  **No schema migration.** (The build path does NOT require any of these — verified.
  Registration is `loader.py` + `configs/` + new `strategies/*.py` only.)
- **Never promote to paper.** Both deciders stay `stage: backtest`, `lifecycle_exempt`.
- **Do not rename `Strategy`** or churn the 14 existing strategies beyond the
  `asdict()`/field-add change in §1. **Do not edit `test_base_reasoning.py` to make
  it pass** — the `asdict()` override must keep it green as-is.
- **Do not add model-artifact / manifest plumbing** (that's the future ML slice).
- **Do not tune for returns** — negative/random is the expected, correct result.
- **Surgical diffs only.** Every changed line traces to this slice.

---

## Verification bar (the overnight depth — fan out, adversarially; each verifier REFUTES)

1. **No special-casing (mechanical — verifier must OPEN the files, not just run a
   test).** `grep` the generalized reasoning module and the `asdict()` override for
   any `kind ==`, `isinstance(decider`, a decider-name string literal, or
   `template ==` / type-dispatch branch → assert **zero**. Both deciders construct
   the **same** dataclass and hit the **same** `asdict()` with no type dispatch.
2. **14-strategy byte-identical serialized reasoning.** Assert every rule strategy's
   serialized `context["reasoning"]` blob is unchanged before/after §1 (the legacy
   7 keys, no new keys for rule strategies). `test_base_reasoning.py:36-44` must pass
   **unmodified**. New keys appear only for the non-rule deciders.
3. **Determinism** — each decider yields identical intents across repeated runs and
   input orderings.
4. **Reproducibility** — `config_hash` stable; same config → same backtest result.
5. **Risk veto (UNIT test against the REAL evaluator — NOT the backtest).** The
   backtest uses `NullRiskEvaluator` and cannot demonstrate a veto. Write a
   standalone unit test: construct the real `RiskEvaluator()` + an
   `EvaluationContext(intent=<a decider-emitted TradeIntent that violates a cap,
   e.g. order-value or single-position>)` and assert `decision.allowed is False`.
   Pattern: `tests/milodex/risk/test_risk_rules.py:156-189`. This proves the harness
   governs a non-rule technique's intent identically to a rule's.
6. **Genuineness** — Decider A's score takes ≥3 distinct values driving a rank;
   Decider B traverses ≥2 levels to ≥3 leaves. (A single threshold is a rule → fail.)
7. **No sacred mutation** — `git diff` audit confirms no `risk/` / `execution/service.py`
   / `promotion/` / migration / schema file changed.
8. **Full suite green** — the **entire** suite, not subsets, via the venv interpreter:
   `./.venv/Scripts/python.exe -m pytest -p no:cacheprovider`. ruff clean:
   `./.venv/Scripts/python.exe -m ruff check src/ tests/`.

---

## Deliverables (for morning review)

- Code + 2 configs + tests on branch `overnight/decision-layer-seam-proof` (off
  `master` HEAD). **Committed, NOT pushed. No PR.**
- After-action report `docs/overnight/DECISION_LAYER_SEAM_PROOF_AAR.md`: what was
  built, what each of the 8 verifications proved (with the full-suite pass count read
  off a clean run), the new `data/milodex.db` rows added, what was deferred,
  surprises, exact meaningful commands.
- A **proposed** edit to the capability map's axis-3 "today" status, called out in
  the AAR — **do not silently rewrite** the map.
- **Honest status at the top.** If any verification failed or any constraint forced a
  compromise, say so loudly. A red branch with a precise failure writeup beats a
  false green. In particular: do not claim "risk pass-through demonstrated" from the
  backtest — only verification #5's unit test demonstrates it.

## Stop / escalation conditions

- Any network/`data.alpaca.markets`/`ProxyError` during a backtest, or any cache
  miss → **STOP**, document, morning question. Do not fetch.
- Reasoning generalization seems to need a schema migration or to break a consumer
  other than the expected `test_base_reasoning.py` (which must stay green) → **STOP**.
- Proving risk veto seems to require touching `risk/` (it should only require
  *constructing* `RiskEvaluator()` in a test and reading) → **STOP**.
- Full suite can't be made green → leave the branch red with an exact failure
  writeup. **Do not merge. Do not claim green. Never push to master. Never open a PR.**

## Suggested workflow shape (phases)

1. **Generalize `DecisionReasoning` + `asdict()` override** (sequential, careful) +
   the byte-identical sweep (verifier #2). Gate: green before proceeding.
2. **Build Decider A and Decider B** + configs + unit tests, incl. the genuineness
   tests (#6) and the risk-veto unit test (#5) (parallel — independent files).
3. **Backtest each** on the pinned window, offline (parallel).
4. **Adversarial verification fan-out** — verifiers #1–#7, each refuting one invariant.
5. **Synthesis** — AAR + proposed capability-map edit + final full-suite run (#8).
