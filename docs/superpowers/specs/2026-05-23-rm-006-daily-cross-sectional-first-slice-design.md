# RM-006 first-slice: daily cross-sectional evaluation flow

Source: roadmap item RM-006 (`docs/architecture/roadmaps/2026-05-21-deepening-roadmap.md`)
Audit: AUDIT-004 (`docs/architecture/audits/2026-05-21-deepening-audit.md`)
Date: 2026-05-23
Status: ready for implementation

## Scope of this slice

Authorized scope is **minimal first slice**:
- Create one shared module owning the repeated cross-sectional evaluation flow.
- Migrate two strategies onto it: `meanrev_rsi2_pullback` and `momentum_daily_tsmom`.
- Collapse three verbatim copies of `_market_regime_is_bullish` into one.

**Out of scope** for this PR (deferred to RM-006 expansion follow-ups):
- Migrating the other 7 daily cross-sectional strategies (`breakout_donchian`, `breakout_atr_channel`, `breakout_nr7_inside`, `meanrev_ibs_lowclose`, `meanrev_bbands_lowerband`, `momentum_52w_high_proximity`, `momentum_xsec_rotation`).
- Test consolidation. Existing per-strategy tests stay where they are. They will be migrated to interface-level tests in a later PR once the shared module has proven stable across more strategies.
- Changing `_validated_parameters` shape. Each strategy keeps its own.

## Verified facts (re-checked against live code 2026-05-23)

- `_market_regime_is_bullish` is duplicated verbatim in `meanrev_rsi2_pullback.py:324-346`, `momentum_daily_tsmom.py:323-344`, and `breakout_donchian.py` — same docstring, same fail-open semantics, same body.
- `meanrev_rsi2_pullback.evaluate` (lines 62-242) and `momentum_daily_tsmom.evaluate` (lines 64-238) share ~30 lines of character-for-character identical skeleton: universe/position normalization (lines 72-80 vs 73-81), `rejected_alternatives = []`, exit-intent extraction and intent extend (lines 84-86 vs 85-87), `remaining_after_exits` set difference (lines 88-90 vs 89-91), `capacity = max(0, ...)` computation, capacity-zero early return (lines 110-122 vs 106-118), regime-bearish early return (lines 124-140 vs 120-136), overflow-rejection loop, sizing/affordability loop with TradeIntent build, and the StrategyDecision/DecisionReasoning assembly shape.
- The strategy-specific parts are: signal computation (Wilder RSI vs trailing return), entry-threshold comparison direction (`<` vs `>=`), ranking metric semantics, exit-rule string names, `triggering_values` key names (`selected_rsi` vs `selected_momentum`), `threshold` key names (`rsi_entry_threshold` vs `momentum_entry_threshold`), and entry/exit narrative format strings.

## Design decision (the load-bearing question)

The investigator flagged the `DecisionReasoning` payload shape as the load-bearing question. Two candidate designs were considered:

**Rejected: single mega-function with callback registry.** `run_cross_sectional_evaluation(context, *, exit_fn, entry_candidates_fn, rank_fn, entry_reasoning_fn, no_signal_reasoning_fn)` — too many callbacks, awkward to type, makes each strategy's `evaluate` a thin shell that obscures intent.

**Chosen: two phase helpers with explicit data-in / decision-out boundaries.** The shared module exposes:

1. `normalize_universe_and_positions(context) -> NormalizedInputs` — returns a small dataclass with `open_positions: dict[str, float]`, `bars_by_symbol: dict[str, BarSet]`.

2. `market_regime_is_bullish(bars_by_symbol: dict[str, BarSet], parameters: Mapping[str, Any]) -> bool` — the verbatim function moves here, deleted from all three strategy files.

3. `evaluate_pre_entry_gates(*, norm: NormalizedInputs, parameters: Mapping[str, Any], exit_details: list[tuple[TradeIntent, str]], exit_narrative: Callable[[str, str, Mapping], str], exit_threshold: Callable[[str, Mapping], Mapping]) -> PreEntryOutcome` — returns one of two shapes via a discriminated result:

   - `PreEntryDecision(StrategyDecision)` — the strategy should return this decision immediately (exit-first / capacity-zero / regime-bearish branches).
   - `PreEntryContinue(intents: list[TradeIntent], remaining_after_exits: set[str], capacity: int)` — proceed to entry phase.

   Implemented as `PreEntryOutcome = StrategyDecision | tuple[list[TradeIntent], set[str], int]` — simple, no new dataclass needed for the union (the caller checks `isinstance(result, StrategyDecision)`).

4. `assemble_entry_decision(*, intents, candidates, capacity, context, parameters, rejected_alternatives, signal_label, signal_format, ranking_enabled, entry_rule, entry_threshold_keys, entry_narrative_fn) -> StrategyDecision` — owns the ranking-payload construction, overflow-rejection loop, sizing/affordability loop, and final StrategyDecision build (both entry-success and no-entry-qualified branches).

   Parameters explained:
   - `signal_label: str` — `"rsi"` or `"momentum"`, used as the dict key in `ranking_payload` entries (`{"symbol": ..., "rsi": ...}` vs `{"symbol": ..., "momentum": ...}`), as the `selected_*` key in `triggering_values` (`selected_rsi` vs `selected_momentum`), and in the overflow-rejection reason string.
   - `signal_format: str` — a format spec like `".2f"` (meanrev) or `".4f"` (momentum), used to render the signal value inside the overflow-rejection reason string. Implementation must use `format(value, signal_format)` (or equivalent `f"{value:{signal_format}}"`) so the rejected_alternatives entry reads `"capacity N full; ranked below selection ({signal_label}={value:{signal_format}})"`. This reproduces meanrev's `(rsi={rsi:.2f})` and momentum's `(momentum={mom:.4f})` byte-for-byte. See `meanrev_rsi2_pullback.py:165` and `momentum_daily_tsmom.py:160-165` for the pre-refactor strings being preserved.
   - `ranking_enabled: bool` — drives whether `ranking_payload` is built (passed straight from `parameters["ranking_enabled"]`). The function constructs `ranking_payload` internally when `True`, leaves it `None` when `False`. The caller must NOT pre-build `ranking_payload`; ownership lives inside this function so the `signal_label` discipline is enforced in one place.
   - `entry_rule: str` — `"meanrev.rsi_entry"` or `"momentum.tsmom_entry"`.
   - `entry_threshold_keys: tuple[str, ...]` — which parameter keys to expose in the `threshold` dict (`("rsi_entry_threshold", "ma_filter_length")` vs `("momentum_entry_threshold", "ma_filter_length")`).
   - `entry_narrative_fn: Callable[[tuple[str, float, float], list[TradeIntent]], str]` — given `(primary_candidate, entry_intents)`, returns the entry narrative string. The strategy's `evaluate` defines this as a lambda that closes over `parameters` from the enclosing scope (both pre-refactor narratives reference `parameters['rsi_entry_threshold']` / `parameters['momentum_entry_threshold']`). The shared module does NOT pass `parameters` into the lambda — the closure handles it. Implementer note: define the lambda inside `evaluate` after `_validated_parameters(context)` returns, so `parameters` is in scope.

   The function also handles the no-entry-candidates-qualified fallback decision (the path where all candidates fail sizing or none qualified). That fallback uses `signal_label`-driven `selected_*` keys when applicable, the `entry_threshold_keys` for the `threshold` dict, and a generic narrative `"no entry candidates qualified — {N} universe member(s) rejected"` (which is character-identical across both strategies pre-refactor, so no callback needed).

### Why this shape passes the deletion test

If `assemble_entry_decision` were deleted, the overflow loop + sizing loop + ranking payload + StrategyDecision shape would reappear identically across both strategies (and across the 7 deferred strategies). The deletion would concentrate ~50 lines per strategy back into each strategy file. That is real depth.

If `evaluate_pre_entry_gates` were deleted, the exit-first + capacity-zero + regime-bearish early-return triple would reappear in each strategy. That is ~35 lines per strategy of identical control flow.

Together, the shared module collapses ~85 lines of per-strategy boilerplate into one module. With 9 strategies in the family, that is roughly 9 × 85 = 765 lines today, going to one shared module (~150 lines) + ~9 strategy-specific signal sections (~30 lines each). Even after this first slice migrates only 2 of 9, the third strategy migration onward is mechanical.

### What stays per-strategy

- `_validated_parameters` (different parameter sets and ranges)
- `_VALID_SIZING_RULES` and `_VALID_RANKING_METRICS` (different allowed sets)
- `_entry_candidates` (different signal computation)
- `_exit_intents` (different signal computation, different exit rule names)
- `_rank_candidates` (different ranking metric semantics)
- `_exit_narrative` (different rule-name to narrative mapping)
- `_exit_threshold` (different threshold-key mapping)
- The signal computation functions (`_wilder_rsi`, `_momentum_signal`)

The strategy's `evaluate` becomes ~30 lines of glue calling the shared helpers in the right order.

## Files to create / modify

**New file**: `src/milodex/strategies/daily_cross_sectional.py`
- Exports: `NormalizedInputs`, `normalize_universe_and_positions`, `market_regime_is_bullish`, `PreEntryOutcome`, `evaluate_pre_entry_gates`, `assemble_entry_decision`.
- Import `Strategy`, `StrategyContext`, `StrategyDecision`, `DecisionReasoning` from `milodex.strategies.base`.
- Import `OrderSide`, `OrderType` from `milodex.broker.models`.
- Import `TradeIntent` from `milodex.execution.models`.
- Import `shares_for_notional_pct` from `milodex.execution.sizing`.
- Import `BarSet` from `milodex.data.models`.

**Modify**: `src/milodex/strategies/meanrev_rsi2_pullback.py`
- Replace `evaluate` body with calls to the new module.
- Delete `_market_regime_is_bullish` (lines 324-346).
- Keep `_validated_parameters`, `_entry_candidates`, `_exit_intents`, `_exit_narrative`, `_exit_threshold`, `_rank_candidates`, `_wilder_rsi` as-is.

**Modify**: `src/milodex/strategies/momentum_daily_tsmom.py`
- Replace `evaluate` body with calls to the new module.
- Delete `_market_regime_is_bullish` (lines 323-344).
- Keep `_validated_parameters`, `_entry_candidates`, `_exit_intents`, `_exit_narrative`, `_exit_threshold`, `_rank_candidates`, `_momentum_signal` as-is.

**Tests**: no test files modified in this slice. Existing per-strategy tests must continue to pass unchanged. That is the behavioral preservation proof.

## Constraints

- Preserve ADR 0008 (risk-layer veto): strategies still emit TradeIntents through the same path. The shared module does not bypass `ExecutionService` because strategies don't reach `ExecutionService` directly — they emit intents that callers route. Unchanged.
- Preserve ADR 0003 (config-driven): no parameter changes; YAML schema untouched.
- Preserve ADR 0022 (universe-scoped rotation): universe scope handling stays the same.
- Preserve manifest hashes: strategy class definitions are not part of the manifest hash (which derives from config + identifier per ADR 0015), so the refactor cannot affect manifest hashes. Verify by running manifest tests.
- Preserve `DecisionReasoning` payload byte-for-byte for both strategies. The `triggering_values` key names, `threshold` key names, `ranking` entry shape, `rule` strings, and narrative strings must all match the pre-refactor output exactly. This is the golden-test invariant.

## Validation

The proof that the refactor is behavior-preserving is that **all of these pass without modification**:

```
python -m pytest tests/milodex/strategies/test_meanrev_rsi2_pullback.py
python -m pytest tests/milodex/strategies/test_momentum_daily_tsmom.py
python -m pytest tests/milodex/strategies
python -m pytest tests/milodex/backtesting/test_engine.py
python -m pytest tests/milodex/promotion
python -m ruff check src/ tests/
python -m ruff format --check src/ tests/
```

If any DecisionReasoning payload assertion fails, the refactor has drifted — stop and diagnose; do not "fix" the test.

## Done criteria

1. `src/milodex/strategies/daily_cross_sectional.py` exists with the API described above.
2. `_market_regime_is_bullish` exists exactly once in the codebase (in the new module). Verify with `grep -rn "_market_regime_is_bullish\|def market_regime_is_bullish" src/`.
3. `meanrev_rsi2_pullback.evaluate` and `momentum_daily_tsmom.evaluate` are ~30 lines each (orchestration only).
4. All listed validation commands pass on first run with no test file modifications.
5. The third copy in `breakout_donchian.py` is also removed (single-line change importing from the new module) — even though `breakout_donchian` itself is not migrated to the new flow in this slice, the verbatim regime helper duplication should be eliminated. If this turns out to require migrating `breakout_donchian` (e.g. because its regime helper is subtly different on close inspection), defer the third-copy cleanup to the first expansion PR and note the deferral in the PR description.

## Open question for the implementer

The investigator's note about strategies with no regime filter (`meanrev_ibs_lowclose`, `breakout_nr7_inside`) is relevant only when they get migrated in expansion PRs. In this first slice, both target strategies use the regime filter, so `evaluate_pre_entry_gates` should always perform the regime check. The function should accept a `regime_filter_enabled: bool = True` keyword argument so future strategies can skip it cleanly without forking the helper — this is a small forward-compatibility provision, not a speculative abstraction (we have known consumers).

## Revision notes

2026-05-23 reviewer pass surfaced two non-blocker concerns; both fixed in the design above before dispatching the implementer:

1. **Overflow rejection reason string** was not parameterized in the initial draft. Pre-refactor meanrev uses `(rsi={rsi:.2f})` and momentum uses `(momentum={mom:.4f})`. Fix: added `signal_format: str` parameter to `assemble_entry_decision`. Combined with `signal_label`, this reproduces the rejection reason byte-for-byte.
2. **`ranking_payload` ownership** was ambiguous in the initial draft. Fix: `assemble_entry_decision` owns `ranking_payload` construction internally (driven by `ranking_enabled` and `signal_label`). Caller must NOT pre-build it.

The reviewer also clarified that `entry_narrative_fn` closes over `parameters` from the enclosing `evaluate` scope; documented explicitly above. `breakout_donchian` uses a 4-tuple candidate shape — not a problem for this slice (donchian's flow migration is deferred), but noted for the expansion PR.

## Reviewer's expectations

The reviewer for this slice must:
- Open both migrated strategy files and verify `evaluate` is glue-only.
- Verify `_market_regime_is_bullish` is gone from both files (and ideally from `breakout_donchian.py` too).
- Verify no test file was modified.
- Confirm the new shared module is imported by both strategies and by `breakout_donchian.py` (regime helper only).
- Spot-check that the DecisionReasoning narrative format strings still produce byte-identical output by reading the entry-narrative lambda passed to `assemble_entry_decision` against the pre-refactor narrative.
