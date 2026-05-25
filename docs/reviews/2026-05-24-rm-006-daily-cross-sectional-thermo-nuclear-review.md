# Thermo-nuclear code quality review: RM-006 daily cross-sectional flow

**Date:** 2026-05-24  
**Scope:** `daily_cross_sectional.py`, trio migrations (`breakout_atr_channel.py`, `breakout_nr7_inside.py`, `meanrev_bbands_lowerband.py`), comparison to `refactor/rm-006-ibs-donchian-shared-flow` lineage on `master`  
**Branch reviewed:** `master` @ `9d50e0c` (includes #178 first slice, #179 ibs+donchian, #180 trio, #181 defer-52w)  
**Verdict:** **Behaviorally sound; abstraction is net-positive but near its complexity budget.** Do not add more migrations without consolidating glue. Several code-judo opportunities remain.

---

## Executive summary

RM-006 set out to delete duplicated cross-sectional orchestration (normalize → exit-first → capacity → regime → rank → size → `StrategyDecision`). On that narrow metric, **the helper succeeds**: the sizing loop, ranking payload construction, overflow rejections, and pre-entry gate ordering live once in `daily_cross_sectional.py` (~274 lines), and eight strategies now share them.

The cost is a **multi-callback, string-keyed, tuple-shaped API** that every migrated `evaluate` must wire by hand. That is *better* than the design doc’s rejected “single mega-function with callback registry,” but it is **not dramatically simpler at the call site** than the doc’s ~30-line glue target. Typical `evaluate` bodies are **~48–55 lines** (trio + reference migrations), mostly identical boilerplate plus three nested closures worth of configuration literals.

**Answer to the central question:** The helper **did delete real complexity** (mechanical loops and decision assembly). It **also created indirection** (union return type, `exit_*` callables, dynamic `selected_{signal_label}`, optional `extra_triggering_values_fn`, caller-owned ranking). That indirection is **manageable for 8 strategies** but will get awkward unless the next RM-006 slice **consolidates the repeated glue** before migrating `momentum_xsec_rotation` / `momentum_52w_high_proximity`.

---

## Methodology and branch lineage

| Artifact | Role |
|----------|------|
| `docs/superpowers/specs/2026-05-23-rm-006-daily-cross-sectional-first-slice-design.md` | Intended API and rejection of mega-callback design |
| `8d4a70a` / PR #179 (`refactor/rm-006-ibs-donchian-shared-flow`) | Added `extra_triggering_values_fn`; migrated IBS + Donchian |
| `e122bca` / PR #180 | Migrated atr / nr7 / bbands; deliberate payload + gate-order upgrades |
| `4ca461a` | Post-review cleanup (dead `_regime_bullish`, narrative consistency) |

**Note on “sibling not an ancestor”:** On current `master`, `8d4a70a` **is** an ancestor of `HEAD` (`git merge-base HEAD 8d4a70a` → `8d4a70a`). There is no parallel unmerged ibs-donchian stack to reconcile—only **layered commits** (ibs/donchian → trio → defer-52w). Integration risk is **semantic** (gate ordering, payload enrichment), not merge conflict.

**Tests run for this review:** 53 tests across atr / nr7 / bbands / ibs / donchian strategy modules — all passed.

There are **no unit tests targeting `daily_cross_sectional.py` directly**; correctness is proven only indirectly through per-strategy tests.

---

## What the shared module deleted (real wins)

These blocks would otherwise be copy-pasted in every cross-sectional strategy:

1. **Universe-scoped normalization** — `normalize_universe_and_positions` + `NormalizedInputs` (lines 35–58).
2. **Single `market_regime_is_bullish`** — replaces three verbatim copies; fail-open semantics preserved (lines 61–84).
3. **Fixed pre-entry ordering** — exit-first → capacity-zero → regime-bearish (`evaluate_pre_entry_gates`, lines 91–157). Trio migration **intentionally** changed atr_channel ordering (capacity before regime when both bind); pinned by `test_atr_channel_at_capacity_takes_precedence_over_regime_bearish`.
4. **Entry-phase mechanical work** — ranking payload, capacity overflow rejections with formatted signal strings, `shares_for_notional_pct` loop, rich vs empty entry `DecisionReasoning` (`assemble_entry_decision`, lines 160–274).

**Deletion test (from design spec):** Removing `assemble_entry_decision` would restore ~50 lines/strategy of identical loops; removing `evaluate_pre_entry_gates` would restore ~35 lines/strategy of identical early returns. **That test passes.**

---

## Structural concerns (ordered by severity)

### 1. Missed code-judo: eight copies of the same `evaluate` skeleton

Every migrated strategy repeats the same sequence:

```text
normalize → rejected=[] → exit_details → evaluate_pre_entry_gates
→ isinstance(gated, StrategyDecision) → unpack tuple
→ _entry_candidates → (optional sort/_rank_candidates)
→ def entry_narrative(...) → assemble_entry_decision(..., 10+ kwargs)
```

Reference: `breakout_atr_channel.evaluate` lines 45–94 (~50 lines), nearly byte-identical to `meanrev_bbands_lowerband.evaluate` 43–93 and `breakout_nr7_inside.evaluate` 42–93.

The design doc explicitly **rejected** `run_cross_sectional_evaluation(..., exit_fn, entry_fn, rank_fn, ...)`, but the codebase now has **eight manual orchestrations** feeding the same two phase helpers. That is the worst of both worlds: no single orchestrator, yet every strategy pays the orchestration tax.

**Recommendation (high conviction):** Add one thin orchestrator, e.g. `run_daily_cross_sectional_phase1(...)` + `run_daily_cross_sectional_phase2(...)`, **or** a frozen `CrossSectionalGlue` dataclass holding the `assemble_entry_decision` string parameters (`signal_label`, `signal_format`, `entry_rule`, `entry_threshold_keys`, `regime_filter_enabled`). Goal: **`evaluate` drops to ~25 lines** without reintroducing a callback registry for signal math (keep `_entry_candidates` / `_exit_intents` per strategy).

---

### 2. `PreEntryOutcome` union + `isinstance` gate is the wrong discriminant

```86:88:src/milodex/strategies/daily_cross_sectional.py
# An early-return ``StrategyDecision`` OR a continuation tuple of
# (intents-so-far, remaining-after-exits, capacity).
PreEntryOutcome = StrategyDecision | tuple[list[TradeIntent], set[str], int]
```

Every consumer must write:

```python
if isinstance(gated, StrategyDecision):
    return gated
intents, remaining_after_exits, capacity = gated
```

Problems:

- **Not self-documenting** — tuple positions are unnamed at the type level (design doc considered `PreEntryContinue` dataclass and deferred it).
- **Easy to misuse** — swapping tuple elements fails at runtime only.
- **No exhaustiveness** — Python won’t catch a third branch.

**Recommendation:** Replace with `@dataclass(frozen=True) class PreEntryContinue` and `def evaluate_pre_entry_gates(...) -> StrategyDecision | PreEntryContinue`, or a small `Literal`-tagged result. Same behavior, deletes `isinstance` + magic tuple unpacking across eight files.

---

### 3. `assemble_entry_decision` parameter surface is a “soft mega-function”

Callers pass **12+ arguments**, many stringly-typed:

| Parameter | Role |
|-----------|------|
| `signal_label` | Dict keys in ranking + `selected_{label}` + rejection strings |
| `signal_format` | Format spec for rejection reason only |
| `entry_rule` | `DecisionReasoning.rule` |
| `entry_threshold_keys` | Subset of `parameters` for threshold dict |
| `entry_narrative_fn` | Closure over `parameters` / side maps |
| `extra_triggering_values_fn` | Donchian escape hatch |

This avoids one giant function with signal callbacks, but **shifts complexity to every call site**. Typos in `signal_label` (`"breakout_strength"` vs `"range_value"`) produce wrong audit keys silently.

**Recommendation:** `EntryDecisionConfig` frozen dataclass (strategy constructs once in `evaluate` from validated parameters). Optional: `Candidate = tuple[str, float, float]` type alias documented as `(symbol, close, signal_value)`.

---

### 4. Ranking is split across caller and helper (inconsistent)

- `assemble_entry_decision` **builds** `ranking_payload` from pre-sorted candidates but **never sorts**.
- **Trio** uses inline `sorted(..., key=lambda ...)` (atr descending, nr7/bbands ascending).
- **IBS / RSI2 / tsmom / Donchian** use per-file `_rank_candidates` with metric string switches (duplicated pattern, 4 implementations).

**Risk:** A new strategy copies the wrong sort direction or forgets pre-sort when `ranking_enabled=True` — helper will happily rank the wrong order in the payload vs selection.

**Recommendation:** One canonical `rank_cross_sectional_candidates(candidates, metric: str) -> list[...]` in `daily_cross_sectional.py` (or shared `strategies/ranking.py`) with metric constants colocated with `_VALID_RANKING_METRICS` per strategy. Delete four `_rank_candidates` clones and three inline sorts.

---

### 5. Donchian 4-tuple narrowing + side map is the smelliest migration pattern

```97:113:src/milodex/strategies/breakout_donchian.py
        raw_candidates = _entry_candidates(...)  # 4-tuple
        channel_highs_by_symbol: dict[str, float] = {
            sym: ch_high for sym, _close, _strength, ch_high in raw_candidates
        }
        candidates: list[tuple[str, float, float]] = [
            (sym, close, strength) for sym, close, strength, _ch_high in raw_candidates
        ]
```

Plus `extra_triggering_values_fn=lambda primary: {"selected_channel_high": channel_highs_by_symbol[primary[0]]}`.

This works and is documented in commit `8d4a70a`, but it is **exactly the awkwardness** the review was worried about: tuple shape mismatch forces parallel data structures and closure lookups.

**Code-judo options (pick one before more channel-style strategies):**

- **A.** Extend candidate type to `tuple[str, float, float, Mapping[str, Any] | None]` with optional metadata bag (helper ignores 4th element except via callback).
- **B.** Keep 3-tuple but let `_entry_candidates` return `list[EntryCandidate]` `@dataclass` with `channel_high: float | None`.
- **C.** Accept that `extra_triggering_values_fn` is the permanent extension point and document it as the **only** approved pattern for “extra per-symbol entry fields.”

**Do not** add a second side-map pattern for the next strategy without choosing A/B/C explicitly.

---

### 6. Exit callbacks (`exit_narrative`, `exit_threshold`) are necessary but repetitive

Each strategy implements nearly identical rule-string switches:

- `_exit_narrative(rule, symbol, params)` — 3–4 branches + fallback
- `_exit_threshold(rule, params)` — 1–2 branches + `{}`

These **cannot** move into the shared module without a rule registry per family. Acceptable duplication **today**. If a ninth strategy adds a fifth copy, extract `ExitReasoningHooks` protocol or family-level helpers (`breakout_exit_narrative`, `meanrev_exit_narrative`).

**Observation:** `evaluate_pre_entry_gates` only surfaces **the first** exit’s narrative/threshold on exit-first return (lines 113–123). That matches pre-refactor behavior but means multi-exit days lose per-symbol reasoning in the primary `DecisionReasoning` — pre-existing, not introduced by RM-006.

---

## Per-file findings (trio + helper)

### `daily_cross_sectional.py` (274 lines)

| Check | Result |
|-------|--------|
| Under 1k lines | Yes |
| Cohesive module | Yes — clear phase split |
| Magic / hidden behavior | `signal_format` in f-string `f"{value:{signal_format}}"` — fine but unusual |
| Regime fail-open | Documented; risk layer remains authoritative — aligned with founder intent |

**Minor:** Regime-bearish branch indexes `parameters['market_regime_symbol']` without `.get` (lines 145–152). Safe when `regime_filter_enabled=True` only for strategies that populate those keys; fragile if a future caller sets `regime_filter_enabled=True` with empty regime keys (would narrate `"market regime  bearish"`). Prefer `.get` with defaults in narrative only.

---

### `breakout_atr_channel.py` (232 lines)

**Strengths:** Regime enabled correctly; rich exit narratives restored post-migration; validation for ranking/sizing metrics.

**Issues:**

- **`evaluate` ~50 lines** — mostly glue (see §1).
- **`_validated` is weaker than nr7** — no range checks on `ema_length`, `max_hold_days`, etc. (pre-existing style, but trio inconsistency).
- **`_entry_candidates` silently `continue`s on already-open** (line 131–132) while nr7 **records** `"already open"` in `rejected_alternatives` — inconsistent audit trail across breakout family.
- **Inline sort** instead of `_rank_candidates` — metric string validated but sort logic not tied to metric name in one function.

**Deliberate semantic change (OK, tested):** Capacity-before-regime when both full and bearish.

---

### `breakout_nr7_inside.py` (230 lines)

**Strengths:** Thorough `_validated_parameters`; clearest comments on `regime_filter_enabled=False`; records `"already open"` rejections.

**Issues:**

- Same glue weight as atr/bbands.
- **`_exit_threshold` returns empty dict** for stop rules — acceptable if stops are self-explanatory, but asymmetric vs meanrev/breakout peers.

---

### `meanrev_bbands_lowerband.py` (213 lines)

**Strengths:** Clean signal helpers (`_bands`); ascending z-score rank documented.

**Issues:**

- **`_validated` is the thinnest of the trio** — casts only, no range validation (contrast nr7). Raises risk of obscure runtime failures deep in pandas paths.
- **Behavioral upgrade (good):** At-capacity now returns verbose `no_signal` via shared helper; pinned by `test_bbands_at_capacity_returns_verbose_decision`.
- Silent skip on already-open (like atr, unlike nr7).

---

## Comparison to `refactor/rm-006-ibs-donchian-shared-flow`

| Dimension | IBS + Donchian (#179) | Trio (#180) | Assessment |
|-----------|----------------------|-------------|------------|
| Shared API | Introduced `extra_triggering_values_fn` | Consumed; trio does not need it | API extension justified by Donchian |
| Regime | IBS `False`, Donchian `True` | atr `True`, nr7/bbands `False` | Consistent pattern |
| Payload | Full rich payload | Upgraded from thin `selected_symbol` only | **Deliberate enrichment**, not regression |
| Tests | Existing tests unchanged | New payload/ranking/order tests | Trio tests do the pinning first slice deferred |
| Glue lines | ~same as trio | ~same | Neither slice hit ~30-line `evaluate` goal |

**Stack risk:** Low for git merge; **medium for behavioral audit** if operators compared old thin narratives to new rich ones. Document in runbooks if needed.

**Reference migrations to emulate:** `meanrev_ibs_lowclose` uses `_rank_candidates` + `_VALID_*` sets — **better template for the next strategy** than the trio’s inline `sorted`.

---

## Spaghetti / branching audit

| Pattern | Verdict |
|---------|---------|
| New `if` in unrelated modules | No — changes are localized |
| `regime_filter_enabled` flag | **Good** forward-compatible provision (spec § open question) |
| Feature logic in shared path | Regime + sizing are genuinely shared — OK |
| `momentum_xsec_rotation` | Still **unmigrated** (~525 lines, manual normalize + regime import only) — largest remaining duplication |

---

## File size

No file crossed 1k lines. Largest migrated file: `breakout_donchian.py` at 473 lines (pre-existing size; migration **reduced** evaluate bulk).

---

## Test / boundary gaps

1. **No direct tests** for `daily_cross_sectional` — consider a small `test_daily_cross_sectional.py` with table-driven cases for gate ordering, overflow rejection format strings, and `ranking_payload` key shapes (would have caught `signal_label` typos).
2. **Golden byte-for-byte** was first-slice requirement; trio **changed** narratives/payloads by design — correct, but means RM-006 is no longer one homogeneous “preservation refactor” across the bank.
3. **`sizing_rule` validated but unused** in shared path (only `per_position_notional_pct` drives sizing) — pre-existing; don’t pretend shared helper enforces sizing policy.

---

## Approval bar (thermo-nuclear)

| Criterion | Status |
|-----------|--------|
| No clear structural regression | **Pass** — duplication reduced |
| No missed dramatic simplification | **Fail (soft)** — eightfold glue copy remains; union tuple; ranking split |
| No unjustified file-size explosion | **Pass** |
| No spaghetti special-cases in shared paths | **Pass** |
| No hacky abstraction | **Borderline** — string keys + tuple union + Donchian side map |
| Canonical helper reuse | **Partial** — regime centralized; ranking not |
| Ready for more migrations without cleanup | **No** |

**Overall:** **Approve the merged work for master** (tests green, deletion test passes, payloads improved). **Block a third expansion slice** until:

1. `PreEntryContinue` (or equivalent) replaces tuple union.  
2. `EntryDecisionConfig` (or orchestrator) collapses `assemble_entry_decision` call-site kwargs.  
3. Canonical ranking helper replaces inline sorts + four `_rank_candidates`.  
4. Document Donchian extension pattern (§5 option C minimum).

---

## Recommended next actions (priority order)

1. **Glue consolidation PR** — dataclass config + optional single orchestrator; target ≤30-line `evaluate` in one reference strategy, migrate others mechanically.  
2. **`rank_cross_sectional_candidates` in shared module** — delete duplicated sorts.  
3. **`test_daily_cross_sectional.py`** — gate order, rejection string format, ranking key discipline.  
4. **Align trio validation** — bring bbands/atr toward nr7/ibs strictness (or extract shared `validate_common_cross_sectional_params`).  
5. **Align `rejected_alternatives` for already-open** — pick silent skip vs explicit reject across breakout/meanrev families.  
6. **Before xsec / 52w migration** — decide Donchian metadata pattern (§5); xsec likely **does not fit** this helper without a separate cadence module.

---

## Appendix: migrated consumer inventory

| Strategy | Uses shared flow | Regime filter | Ranking style | Notes |
|----------|------------------|---------------|---------------|-------|
| `meanrev_rsi2_pullback` | Full | Default True | `_rank_candidates` | First slice reference |
| `momentum_daily_tsmom` | Full | Default True | `_rank_candidates` | First slice reference |
| `meanrev_ibs_lowclose` | Full | False | `_rank_candidates` | #179 |
| `breakout_donchian` | Full + extra_triggering | True | `_rank_candidates` | 4-tuple narrow |
| `breakout_atr_channel` | Full | True | inline sort | Trio |
| `breakout_nr7_inside` | Full | False | inline sort | Trio |
| `meanrev_bbands_lowerband` | Full | False | inline sort | Trio |
| `momentum_xsec_rotation` | **Partial** (regime only) | Manual | N/A | Not in scope but largest remaining debt |

---

*Review performed read-only against source; no production code modified except this document.*
