# After-Action Report — Decision-Layer Substitutability Proof

**Branch:** `overnight/decision-layer-seam-proof` (off `master` HEAD `7fb52dc`). **Committed, not pushed. No PR. No merge.**
**Date:** 2026-05-31 (overnight run; brief authored 2026-05-30).
**Companion input:** [`DECISION_LAYER_SEAM_PROOF_BRIEF.md`](DECISION_LAYER_SEAM_PROOF_BRIEF.md).

---

## HONEST STATUS (read first)

- **The seam proof itself is complete and green.** `DecisionReasoning` was generalized once; two paradigm-distinct deterministic non-rule deciders (a linear scored/ranking decider and a decision-tree/bucketed-lookup decider) run behind the **unchanged** `Strategy` contract, backtest only. All seam-specific tests pass; ruff is clean on `src/ tests/`.
- **The adversarial fan-out caught one real, latent bug; it is fixed.** Verifier #3 found the deciders inherited the *positions-mapping* iteration order into their SELL-emission order and stop-reasoning primary selection. Fixed (commit `61377e0`, sorted positions iteration) and re-verified results-invariant; the determinism tests were strengthened so the class is now guarded. 7 of 8 invariants held on first pass.
- **The full suite is NOT 100% green — by one pre-existing, environmental failure that this work did not cause.** `tests/milodex/gui/test_app.py::test_main_qml_loads_without_errors_via_subprocess` fails because PySide6 in this `.venv` emits `QFontDatabase: Cannot find font directory …/PySide6/lib/fonts` ("Qt no longer ships fonts") and the test asserts the QML subprocess produces empty stderr. **Verified pre-existing: it fails identically on `master` HEAD with none of this branch's code present.** My diff touches only `strategies/`, `configs/`, `tests/milodex/strategies/` — there is no path from it to QML font loading.
  - **Full-suite result (clean run, this branch):** `2211 passed, 2 skipped, 4 xfailed, 1 failed` in 273.93s. The 1 failure is the font issue above.
- **Risk pass-through is demonstrated ONLY by the verification-#5 unit test, NOT by the backtest.** The backtest path injects `NullRiskEvaluator` (BYPASS) by design, so it cannot show a veto and I do not claim it does. The unit test against the real `RiskEvaluator` shows both deciders' emitted intents are vetoed above the order-value cap and allowed below it.
- **Returns are irrelevant and were not tuned.** Both deciders happen to post positive returns on this window; that is noise for a capability proof. No parameter was chosen for performance.

---

## What was built (surgical change surface)

Diff vs pre-branch master HEAD `7fb52dc` — only these areas changed:

| File | Change |
|---|---|
| `src/milodex/strategies/base.py` | Generalized `DecisionReasoning`: added 4 optional non-rule fields (`kind`, `score`, `decision_path`, `feature_contributions`), each flagged `omit_if_default` in field metadata; rewrote `asdict()` to drop metadata-flagged fields when at default. |
| `src/milodex/strategies/_decider_features.py` | **New.** Pure cross-sectional feature kit (trailing momentum return, Wilder RSI, MA-distance, realized vol, cross-sectional z-score) over cached OHLCV. No new data source. |
| `src/milodex/strategies/scored_linear_features.py` | **New.** Decider A, family `scored`: continuous weighted z-score → cross-sectional rank → top-N. |
| `src/milodex/strategies/tree_bucketed_lookup.py` | **New.** Decider B, family `tree`: depth-2 binary tree, level-2 edge varies by first branch. |
| `src/milodex/strategies/loader.py` | Registered both deciders in `build_default_registry()` (+2 imports, +2 `register`). |
| `configs/scored_daily_linear_features_sector_etfs_v1.yaml` | **New.** Decider A config; `stage: backtest`; lifecycle-exempt noted in comment. |
| `configs/tree_daily_bucketed_lookup_sector_etfs_v1.yaml` | **New.** Decider B config; `stage: backtest`; lifecycle-exempt noted in comment. |
| `tests/milodex/strategies/test_base_reasoning.py` | **Added** 2 test cases (legacy 7-key omission; populated non-rule fields). The 3 pre-existing tests are untouched. |
| `tests/milodex/strategies/test_scored_linear_features.py` | **New.** Decider A unit + genuineness (#6) + determinism (#3). |
| `tests/milodex/strategies/test_tree_bucketed_lookup.py` | **New.** Decider B unit + genuineness (#6) + determinism (#3). |
| `tests/milodex/strategies/test_decision_layer_seam.py` | **New.** #1 no-special-casing, #2 byte-identity (mechanical+behavioral), #4 config-hash reproducibility. |
| `tests/milodex/strategies/test_decision_layer_seam_risk_veto.py` | **New.** #5 real-`RiskEvaluator` veto for both deciders. |

No file under `risk/`, `execution/service.py`, `promotion/`, or any migration/schema changed. No schema migration. No promotion-manifest / event-store promotion write.

### The two deciders (paradigm-distinct, same everything-else)

Same universe (`universe.sector_etfs_spdr.v1`, 11 SPDR sector ETFs), same daily tempo, long-only, market orders, simulated broker, cached OHLCV, stateless. Only the **decision paradigm** differs:

- **Decider A — linear scored / ranking.** `score(symbol) = Σ weight_f · zscore_f` over {momentum, rsi, ma_distance, realized_vol}; rank the cross section; rotate into top-N. Populates `kind="scored"`, `score`, `feature_contributions` — **never** `decision_path`.
- **Decider B — decision-tree / bucketed-lookup.** Depth-2 binary tree: `momentum >= split?` then `rsi <= edge?` where the level-2 RSI edge **differs by the first branch** (`rsi_split_strong` vs `rsi_split_dip`) — a genuine tree, not two ANDed thresholds. Four leaves (`strong_buy`/`trend_follow`/`dip_buy`/`neutral_skip`). Populates `kind="tree"`, `decision_path` (2 steps) — **never** `score`.

The divergence in *which* reasoning fields each populates is the structural-distinctness proof.

---

## The load-bearing decision (the headline surprise)

The brief mandated overriding `asdict()` to "OMIT any field whose value equals its default," and asserted this yields the "identical legacy 7-key dict." **Grounding against the code showed those two clauses are in tension.** `meanrev_crypto_rsi2.py` (and many `no_signal` paths) construct `DecisionReasoning(rule=..., narrative=...)` leaving **all** legacy optional fields at their defaults. A literal "omit any field at default" rule would shrink such a blob from the legacy **7 keys to 2** — which *fails* verification #2's own wording ("the legacy 7 keys … unchanged") and could break existing strategy tests.

**Resolution (toward the brief's stated GOAL, byte-identity — a hard deliverable):** I implemented the override so that **only the four new non-rule fields are subject to omit-if-default** (via per-field `metadata={"omit_if_default": True}`), while the legacy seven are always serialized. This:
- keeps every rule strategy's serialized blob byte-identical, including sparse `no_signal` reasonings (verified live: the 70 `scored.max_hold` / 88 `tree.max_hold` mechanical-stop explanations serialized with `kind` omitted — legacy shape);
- keeps the golden test `test_base_reasoning.py` green **unmodified**;
- introduces **no** type/`kind`/template/class dispatch — the omission is driven purely by field metadata + an equals-own-default test, so rule strategies and deciders traverse the identical `asdict()` path (verification #1).

This is the one place I deviated from the brief's literal *mechanism* to honor its literal *goal* and its own verification #2. **If the morning reviewer prefers the literal "omit any field" reading, that is the conversation to have — but it cannot coexist with verification #2 as written.** Pure omit-defaults was rejected for that reason.

---

## The 8 verifications

| # | Invariant | How proven | Result |
|---|---|---|---|
| 1 | No special-casing | `test_decision_layer_seam.py::test_reasoning_module_has_no_type_dispatch` (mechanical scan of `base.py` for dispatch forms) + adversarial agent | **PASS** — omission is metadata-driven; no `kind ==` / `isinstance(decider` / `template ==` / decider-name comparison. |
| 2 | 14-strategy byte-identical reasoning | `test_base_reasoning.py` (golden test unmodified + 2 added cases); `test_decision_layer_seam.py` (mechanical: non-rule kwargs appear only in the 2 decider files; behavioral: rule blob has no new keys); live event-store evidence | **PASS** — rule blobs unchanged; new keys only on deciders. |
| 3 | Determinism | `test_*::*deterministic*` (identical **ordered** intents + reasoning across reversed universe/bars **and positions** ordering, incl. a multi-position stop) | **PASS after fix `61377e0`** — adversarial #3 caught a positions-order leak in the exit/stop path (the original test never varied positions and compared a *sorted* signature). Fixed via sorted positions iteration; re-verified results-invariant. |
| 4 | Reproducibility | `test_decision_layer_seam.py::test_decider_config_hash_is_reproducible`; backtests are config-hash keyed | **PASS** (config_hash stable, sha-256, no collision) |
| 5 | Risk veto (real evaluator, NOT backtest) | `test_decision_layer_seam_risk_veto.py`: real `RiskEvaluator` vetoes a **decider-emitted** intent above the order-value cap (`max_order_value_exceeded`, `allowed is False`) and allows it below — for **both** deciders | **PASS** |
| 6 | Genuineness | Decider A: score takes ≥3 distinct values driving the rank; Decider B: path traverses 2 split levels, ≥3 distinct leaves reached. Asserted in the decider tests | **PASS** |
| 7 | No sacred mutation | `git diff --stat 7fb52dc..branch`: only `strategies/`, `configs/`, `tests/milodex/strategies/`, `docs/` | **PASS** |
| 8 | Full suite + ruff | `./.venv/Scripts/python.exe -m pytest -p no:cacheprovider`; `ruff check src/ tests/` | **2211 passed, 2 skipped, 4 xfailed, 1 failed** (the 1 = pre-existing PySide6 font env failure, reproduced on `master`); **ruff: All checks passed**. |

### Adversarial fan-out (8 independent cold-agent refuters; workflow `wz3w0pn21`)

Each agent was charged with **refuting** one invariant against the committed code, opening the real files and running the relevant commands, defaulting to "refuted" under uncertainty.

| Verifier | Verdict | Conf | Key evidence |
|---|---|---|---|
| #1 no special-casing | **HELD** | 98 | `asdict()` has only a metadata gate + equals-own-default; grep for `kind ==` / `isinstance(decider` / decider-name comparison / `template ==` → none. Empirical: a tree blob keeps `decision_path` and drops `score` — the *opposite* of any `if kind=="tree"` dispatch. |
| #2 byte-identical | **HELD** | 95 | Golden test byte-unchanged (`git diff HEAD~1`: only 2 appended funcs). `no_signal` → exactly the 7 legacy keys (not a shrunken 2-key dict). `score=0.0` not omitted (0.0≠None trap avoided). New-field kwargs appear only in the 2 decider files. |
| #3 determinism | **REFUTED → fixed** | 72 | universe/bars reorder fully deterministic, but positions-dict order leaked into SELL emission order + stop-reasoning primary. **Fixed (`61377e0`)**, re-verified. |
| #4 reproducibility | **HELD** | 96 | Re-ran scored twice offline → identical 256 trades / $139,444.36; `config_hash` stable sha-256; live `urlopen` blocked (`WinError 10061`) confirming zero network egress. |
| #5 risk veto | **HELD** | 95 | Real `RiskEvaluator` (not `Null`); decider-emitted BUY (S7, qty 193); `allowed False` + `max_order_value_exceeded` above cap, `allowed True` below — both deciders. |
| #6 genuineness | **HELD** | 96 | scored: 8 distinct scores, real 4-feature linear combo summing to the score; tree: path-dependent depth-2 (same RSI → different leaf by first branch), ≥3 leaves reached. |
| #7 no sacred mutation | **HELD** | 99 | `git diff --name-status`: 14 files, all under `strategies/`/`configs/`/`tests/.../strategies/`/`docs/`; none under `risk/`/`execution/service.py`/`promotion/`/migrations. |
| critic — completeness | **HELD** | 88 | Cross-sectional population is real (`ranking` length 11; engine builds the multi-symbol map); no other `asdict()` consumers; warmup safe (max int param 63 → 365-day warmup, well above the 2020-07-27 cache floor); no overstated risk claim. |

**Net: 7/8 held on first pass.** The single refutation (#3) was a genuine latent determinism gap in the exit/stop path — caught, fixed, re-verified, and the test class hardened. The two notable non-blocking caveats the agents surfaced (#2 explicit-`None` foot-gun; #4 cache-horizon truncation) are recorded under *Deferrals & surprises*.

---

## Backtests (pinned window, fully offline)

Both run with the dead-proxy hard guard (`HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 NO_PROXY=`) so any network attempt would fail loudly. Neither emitted `data.alpaca.markets` / `ProxyError`; both reported `Data quality: pass` — **fully cache-resident, no network**.

| Decider | Run ID | Trading days | Trades | Skipped | Final equity ($100k start) | Total return |
|---|---|---|---|---|---|---|
| `scored.daily.linear_features.sector_etfs.v1` | `6d4878e6-a742-4870-8651-13217a6148c5` | 551 | 256 (129 b / 127 s) | 117 | $139,444.36 | +39.44% |
| `tree.daily.bucketed_lookup.sector_etfs.v1` | `26ba9d56-f17e-41e9-b114-bf8838bcab65` | 551 | 198 (99 b / 99 s) | 442 | $135,804.68 | +35.80% |

Command shape (per the brief, plain backtest, NOT `--walk-forward`):
```
$env:HTTPS_PROXY="http://127.0.0.1:9"; $env:HTTP_PROXY="http://127.0.0.1:9"; $env:NO_PROXY=""
./.venv/Scripts/python.exe -m milodex.cli.main backtest <strategy_id> --start 2023-01-03 --end 2026-05-16
```

Both deciders were **re-run offline after the determinism fix** and reproduced identical aggregates (scored 256 / $139,444.36 → run `ca7dd592`; tree 198 / $135,804.68 → run `659cbec3`), confirming the fix is results-invariant. Verification #4 also independently re-ran scored twice (same numbers). **Returns are noise** for a capability proof and were not tuned. The high tree "skipped" count (442) is benign — over-capacity / already-held entry candidates the engine declines; not a defect.

### New `data/milodex.db` rows added (expected, benign — `stage: backtest`, no promotion)

Every backtest appends one `backtest_runs` row (keyed by `run_id`; metrics in `metadata_json`) plus per-decision `explanations` rows (keyed by `session_id` = the run UUID). Across the evidence run + offline reproducibility re-runs (verification #4 and the post-fix check), the verified totals for the two decider `strategy_id`s are:

| Decider | `backtest_runs` rows | `explanations` rows | run_ids |
|---|---|---|---|
| scored | 4 | 2,568 | `6d4878e6` (evidence) · `05c2953d`, `2cece26b` (verifier #4 reproductions) · `ca7dd592` (post-fix) |
| tree | 2 | 1,300 | `26ba9d56` (evidence) · `659cbec3` (post-fix) |
| **total** | **6** | **3,868** | |

The reasoning blob serialized end-to-end through the **real `ExecutionService` → event store**, exactly as designed (sampled from the evidence runs `6d4878e6` / `26ba9d56`, 642 / 650 explanations respectively):
- scored: blobs carry `score` + `feature_contributions`, **never** `decision_path`; `scored.max_hold` mechanical stops are legacy-shaped (`kind` omitted) — 455 scored-kind vs 70 legacy-stop in the evidence run.
- tree: blobs carry `decision_path`, **never** `score`; `tree.max_hold` stops legacy-shaped — 107 path-bearing entries vs 88 legacy-stop.

This is the strongest single piece of evidence that the seam works through the audit path, not just in unit tests. No promotion / manifest / `lifecycle_exempt` event-store writes were made. (The reviewer will also see a pre-existing `running` row `id 111` for `breakout.orb.intraday.spy.v1` — an unrelated intraday phantom, **not** from this branch.)

---

## Deferrals & surprises

- **Surprise / brief tension:** the `asdict()` omit rule vs. verification #2 byte-identity (see "The load-bearing decision" above). Resolved toward byte-identity; flagged for review.
- **Determinism gap (found by adversarial #3, fixed):** the deciders' exit/stop path inherited the positions-mapping iteration order. Fixed in `61377e0` (sorted positions iteration) and re-verified results-invariant; the determinism tests now reverse positions order and compare full ordered intent lists. Note: the existing rule strategy `momentum_xsec_rotation` has the **same** latent pattern (`for symbol in list(open_positions)`); I did **not** touch it (out of scope, surgical) — worth a follow-up if positions-order determinism is wanted system-wide.
- **Latent foot-gun (verifier #2, not a current violation — documented, not fixed):** because omission is "field equals its own default (`None`)", a *future* decider that passes an **explicit** `score=None` (meaning "serialize null") would instead have the field omitted. The two current deciders only ever pass computed non-`None` values, so the contract holds today; `None`-means-omit is the intended semantics. Flagged so a future ML/LLM decider author knows.
- **Cache-horizon truncation (verifier #4 — data nuance, not a guardrail breach):** the sector-ETF 1Day cache ends ~2026-05-05 (XLC even 2024-12-31), earlier than the brief's stated tail (2026-05-18). For `--end 2026-05-16` the engine served data **through the cache horizon** (551 trading days, `Data quality: pass`) **without erroring or fetching** — verified offline (network blocked). So the effective window is slightly shorter than requested, and XLC likely drops out of the cross-section after 2024-12-31 (`ranking` occasionally 10 not 11 symbols). Immaterial to the capability proof; noted for honesty and as a possible cache-warmup follow-up.
- **Pre-existing red:** the PySide6 font test. Not fixed (out of scope; environmental). A fix would be deploying DejaVu fonts into the venv or switching the test to tolerate the font warning — neither is this slice's job.
- **Deferred (correctly, per one-axis discipline):** no regime/market filter was added to the deciders (it would inflate the warmup window toward the cache floor and move a second axis). LLM/non-deterministic decider — the actual axis-3 evidence hard-problem — remains untouched, as intended.
- **`lifecycle_exempt`** is expressed as a config **comment**, not a parsed YAML key (the loader has no such field; it lives in promotion/event-store). Per the brief, the label is documentation here, not a promotion write.

---

## Exact meaningful commands

```
# branch + pin docs
git switch -c overnight/decision-layer-seam-proof   # off master 7fb52dc
git add docs/architecture/2026-05-30-harness-capability-axes.md docs/overnight/DECISION_LAYER_SEAM_PROOF_BRIEF.md && git commit

# build committed as 29370a8; positions-order determinism fix as 61377e0

# targeted gate (dev)
./.venv/Scripts/python.exe -m pytest tests/milodex/strategies/test_base_reasoning.py -p no:cacheprovider -q

# adversarial verification fan-out (8 cold agents, each refuting one invariant)
#   → 7/8 held; #3 determinism refuted → fixed in 61377e0, re-verified

# backtests (offline, dead-proxy guard) — see table above

# full suite + lint (verification #8)
./.venv/Scripts/python.exe -m pytest -p no:cacheprovider
./.venv/Scripts/python.exe -m ruff check src/ tests/

# pre-existing-failure proof
git switch master && ./.venv/Scripts/python.exe -m pytest tests/milodex/gui/test_app.py::test_main_qml_loads_without_errors_via_subprocess -p no:cacheprovider -q  # fails identically
```

---

## PROPOSED edit to the capability map (NOT applied — for review)

`docs/architecture/2026-05-30-harness-capability-axes.md`, axis 3 row. **Current** "Where Milodex is today" cell reads:

> **Config-driven rules only.** The harness's central thesis — a substitutable decision layer — is entirely untested beyond rules.

**Proposed** replacement (reflecting this proof slice; the LLM tier remains the open thesis test):

> **Config-driven rules + two backtestable non-rule deciders** (capability-proof on branch `overnight/decision-layer-seam-proof`, not merged): a linear scored/ranking decider and a decision-tree/bucketed-lookup decider run behind the *unchanged* `Strategy` contract. The same `DecisionReasoning` hosts both paradigms with **byte-identical** rule serialization, and the **real `RiskEvaluator` vetoes their intents** (unit-proven; the backtest path is BYPASS by design). The backtestable seam generalizes beyond rules. **Still untested: the LLM / non-deterministic tier** — the actual evidence hard-problem.

And in the axis-3 "Core next-slice" cell, the "backtestable non-rules decider **first**" item is now **done** (two slices — the second proves the first was not hard-coded, per the map's own load-bearing rule); the remaining next-slice is the **LLM decider** (forces a new, forward-only evidence track).

Rationale for two deciders (not over-building): the map's load-bearing rule permits a second slice "to prove the first was not hard-coded." Two structurally-distinct paradigms (continuous-linear vs discrete-tree) behind one unchanged contract is exactly that proof; a third would add nothing and was not built.
