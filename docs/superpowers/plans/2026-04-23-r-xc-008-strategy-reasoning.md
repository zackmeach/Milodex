# R-XC-008 — Strategy Reasoning on `Strategy.evaluate()`

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the single surviving Phase 1.3 deferral — R-XC-008's "triggering event / rule threshold / alternatives rejected" fields — by extending `Strategy.evaluate()` to return reasoning alongside `TradeIntent`s, then threading that reasoning through `StrategyRunner`, `BacktestEngine`, and `ExecutionService` into the existing `ExplanationEvent.context` JSON blob.

**Architecture:** Interface-level, not infrastructural. No new tables, no migrations. One sacred-surface change (the `Strategy.evaluate()` return type), two strategy updates (regime + meanrev), two call-site updates (runner + engine), one persistence-path update (service). The event store already accepts arbitrary `context: dict[str, Any]` — R-XC-008 is about *filling that dict honestly*, not about reshaping storage.

**Tech Stack:** Python 3.11+, pytest, existing `milodex.strategies.base`, `milodex.execution.service`, SQLite event store (unchanged).

---

## 1. Context

### The deferral in one sentence
Engine-side R-XC-008 enrichment (rule name, config hash, bar timestamp) landed in commit `04ba89d` as a hardcoded `"fill_simulation"` stub. The actual reasoning — *why did the strategy propose this trade, what triggered it, what else did it consider* — never left `evaluate()`.

### What the SRS says R-XC-008 needs
From `docs/SRS.md:342`:
> Every meaningful decision … **shall** persist an *explanation record* capturing: the strategy instance, **the triggering data or event, the rule or threshold evaluated**, the current risk state, the resulting action, **any alternatives rejected or not taken**, and whether human approval was required or bypassed.

From `docs/REPORTING.md:45` "Mandatory Trade-Level Reasoning Fields":
- signal values that triggered the decision
- entry or exit rule satisfied
- ranking result (if applicable)
- blockers or warnings present at decision time
- human-readable explanation of why the trade was proposed or blocked

Fields already on `ExplanationEvent`: strategy instance, config hash, symbol, timestamp, risk checks, market/account state, submitted action, broker outcome.

Fields **still missing**: triggering signal values, rule name + threshold, ranking result, alternatives rejected, human-readable explanation. These are all *strategy-internal* — only `Strategy.evaluate()` knows them.

### What's already on master
- `Strategy.evaluate(bars, context) -> list[TradeIntent]` — declared in [base.py:62](../../../src/milodex/strategies/base.py).
- Two implementations: [regime_spy_shy_200dma.py](../../../src/milodex/strategies/regime_spy_shy_200dma.py), [meanrev_rsi2_pullback.py](../../../src/milodex/strategies/meanrev_rsi2_pullback.py). Both have rich internal state (MA value, latest close, RSI, rank, rejection reasons) that's computed and discarded.
- Two call sites: [runner.py:105](../../../src/milodex/strategies/runner.py), [engine.py:272](../../../src/milodex/backtesting/engine.py).
- `ExplanationEvent.context: dict[str, Any]` already exists and is the natural landing place. No schema migration needed.
- `ExecutionService._record_explanation` at [service.py:372](../../../src/milodex/execution/service.py) builds `context` and hardcodes a `"fill_simulation"` rule for backtest rows. That block is the exact seam this plan replaces.

### Why this was deferred from §5.1.2/§5.1.3
The return-type of `Strategy.evaluate()` is a *sacred surface* (CLAUDE.md). Getting it wrong means a second migration later. Keeping it out of the analytics gap-closure plan was correct — that plan was purely additive data-over-event-store work; this one is interface design.

---

## 2. Architectural Decisions

### AD-1 — Return a `StrategyDecision` object, not a sidecar on `TradeIntent`
`Strategy.evaluate()` returns a new frozen `StrategyDecision(intents: list[TradeIntent], reasoning: DecisionReasoning)`, not `list[TradeIntent]` with reasoning bolted on.

**Why.** Two reasons:
1. **Non-emission is a decision.** An evaluation cycle that produces *zero* intents still has reasoning ("RSI 42 > entry threshold 5", "MA filter rejected — latest close below 200-DMA"). That story can't live on a `TradeIntent` that doesn't exist.
2. **Rejected alternatives aren't 1-per-intent.** Meanrev may evaluate 20 candidates and emit 3 intents; the 17 rejections are per-*cycle*, not per-intent. A cycle-level wrapper is the right home.

Sidecar-on-intent would force awkward duplication and still not answer the zero-intents case.

### AD-2 — `DecisionReasoning` is a flat dataclass with one free-form `extras: dict`
Fields on `DecisionReasoning`:
- `rule: str` — canonical rule ID (`"regime.ma_filter_cross"`, `"meanrev.rsi_entry"`, `"meanrev.rsi_exit"`, `"meanrev.stop_loss"`, `"meanrev.max_hold"`, `"no_signal"`).
- `triggering_values: dict[str, float | int | str | None]` — the inputs the rule consumed (`{"latest_close": 450.12, "ma_200": 432.05}` or `{"rsi": 1.8, "entry_threshold": 5.0}`). Kept as a typed-enough dict rather than a nested dataclass because shape varies per strategy family.
- `threshold: dict[str, float | int | str | None]` — the threshold-side of the comparison (`{"ma_200": 432.05}`); may overlap `triggering_values` when a rule is a crossover.
- `ranking: list[dict] | None` — for cross-sectional families (meanrev), the ordered list of candidates considered with their scores. `None` when not applicable.
- `rejected_alternatives: list[dict]` — `[{"symbol": "AAPL", "reason": "rsi=12 above entry threshold 5"}, ...]`. Empty list when no candidates were rejected.
- `narrative: str` — one-sentence human-readable summary. Required — this is the field an operator reads first.
- `extras: dict[str, Any]` — escape hatch for strategy-specific debugging fields that shouldn't bloat the main shape.

**Why.** The SRS/REPORTING list is concrete enough to typify but open-ended enough that a fully-nested schema would be brittle. Flat dataclass + `extras` keeps the 90% case clean while allowing one-off fields without a schema change.

### AD-3 — Persist reasoning into the existing `ExplanationEvent.context` dict, not a new column
`ExecutionService._record_explanation` currently writes `{message, latest_price, estimated_unit_price, estimated_order_value}` into `context`. Plan: add a `reasoning` key holding `DecisionReasoning.asdict()`. The `"fill_simulation"` hardcoded block goes away.

**Why.** `context` is already the designated free-form slot on `ExplanationEvent`. Promoting reasoning to first-class columns is premature — the typed Python surface is on `DecisionReasoning`, the persistence surface is documented-JSON in `context`. If a future query path needs indexed access to (say) `reasoning.rule`, promote that single field then. YAGNI for now.

No schema migration. JSON contract is additive (new `reasoning` key under an existing JSON blob; old rows still parse).

### AD-4 — Emit a no-trade `ExplanationEvent` when evaluate() returns zero intents
Today, zero intents means zero rows. Under R-XC-008 ("every meaningful decision"), an evaluation cycle *is* a decision even when it proposes nothing. Plan: `StrategyRunner` and `BacktestEngine` write a single `ExplanationEvent` with `decision_type="no_trade"`, `status="no_signal"`, the strategy context, and the `DecisionReasoning` payload, when `decision.intents` is empty.

**Why.** Without this, a backtest that fires once in 250 days has 249 invisible decisions. The SRS's "every meaningful decision" language specifically includes the cases where the answer was *no*. Cheap: one row per trading day per strategy.

**Risk:** row-count bloat. Mitigation — one row per cycle is bounded by cycles (≤1/day for daily strategies; this plan does not re-enter intraday territory). A 10-year daily backtest = ~2,500 extra rows, trivially small.

### AD-5 — Update both strategies in the same commit as the base class
`Strategy.evaluate`'s abstract signature change breaks both concrete strategies at compile time. They must land together. No "shim that wraps a `list[TradeIntent]` return into `StrategyDecision`" — that's exactly the kind of backwards-compat vestige CLAUDE.md's "avoid backwards-compatibility hacks" rule rejects.

**Why.** There are two strategies. The blast radius is finite and already inside the current plan's TDD sequence. A shim would invite future strategies to return the old shape and rot.

### AD-6 — `no_signal` is a legal rule name; it's what regime/meanrev return when nothing fires
Rather than invent a special "no rule fired" marker, both strategies return `DecisionReasoning(rule="no_signal", ...)` with the threshold comparison that failed. This keeps the rule-name axis unambiguous — every row has a rule.

**Why.** The alternative (`rule: str | None`) forces every downstream consumer to handle `None`. A sentinel string keeps the Python API total.

### AD-7 — No new CLI commands; surface reasoning via existing `analytics` + `explain` paths
The trust report (`reports.py` from the prior plan) will read `reasoning.narrative` and `reasoning.rule` off recent explanation rows. The existing `milodex analytics trades --json` surface already returns the full `context` dict. No new subcommand.

**Why.** Consumption surface is already adequate. Adding commands before operators ask for them is speculative.

---

## 3. File Inventory

Grouped by commit (§4). Paths relative to `C:\Users\zdm80\Milodex`.

### Phase A — Data types (AD-1, AD-2)

**Modify:**
- `src/milodex/strategies/base.py` — add `DecisionReasoning` and `StrategyDecision` frozen dataclasses. Change `Strategy.evaluate()` abstractmethod signature from `-> list[TradeIntent]` to `-> StrategyDecision`. Update the docstring to describe the reasoning contract.

**Create:**
- `tests/milodex/strategies/test_base_reasoning.py` — dataclass-level tests (asdict round-trip, required-field defaults, `extras` hole-punching).

### Phase B — Strategy implementations (AD-5, AD-6)

**Modify:**
- `src/milodex/strategies/regime_spy_shy_200dma.py` — return `StrategyDecision`. Reasoning: `rule="regime.ma_filter_cross"` when target flips, `rule="regime.hold"` when already in target, `rule="no_signal"` with the MA-length-insufficient case. `triggering_values={"latest_close": ..., "ma_200": ...}`. `narrative` like `"latest close 450.12 above 200-DMA 432.05 → rotate to SPY"`.
- `src/milodex/strategies/meanrev_rsi2_pullback.py` — return `StrategyDecision`. Exits: `rule="meanrev.rsi_exit"` / `"meanrev.stop_loss"` / `"meanrev.max_hold"` per branch. Entries: `rule="meanrev.rsi_entry"`. `ranking` populated with the candidate list when `ranking_enabled`. `rejected_alternatives` populated with candidates that failed MA, RSI, or capacity cuts. `narrative` summarizing the cycle outcome.

**Modify:**
- `tests/milodex/strategies/test_regime_spy_shy_200dma.py` — update existing assertions to `result.intents`; add new assertions on `result.reasoning.rule`, `.narrative`, `.triggering_values`.
- `tests/milodex/strategies/test_meanrev_rsi2_pullback.py` — same updates, plus assertions on `ranking` and `rejected_alternatives` for a multi-candidate cycle.

### Phase C — Call-site propagation (AD-3, AD-4)

**Modify:**
- `src/milodex/strategies/runner.py` — unpack `decision = evaluate(...)`, pass `decision.reasoning` into each submitted execution request's audit trail, emit a `decision_type="no_trade"` explanation when `decision.intents` is empty.
- `src/milodex/backtesting/engine.py` — same pattern.
- `src/milodex/execution/service.py` — `_record_explanation` accepts an optional `reasoning: DecisionReasoning | None` arg; when present, merges `reasoning.asdict()` into `context["reasoning"]`. Delete the hardcoded `"fill_simulation"` block (replaced by real data from the strategy).
- `src/milodex/execution/models.py` — extend `TradeIntent` or `ExecutionRequest` with a `reasoning: DecisionReasoning | None` field so the service can retrieve it without a parallel out-of-band channel. **Decision during implementation:** whichever is less surgical — likely `ExecutionRequest`, since that's the object already crossing the service boundary.

### Phase D — No-trade explanation rows (AD-4)

**Modify:**
- `src/milodex/execution/service.py` — add a new entrypoint `record_no_trade_decision(*, strategy_name, strategy_stage, strategy_config_path, reasoning, account, market_open, latest_bar, session_id, source, backtest_run_id)` that writes a single `ExplanationEvent` with no associated `TradeEvent`.
- `src/milodex/strategies/runner.py` + `src/milodex/backtesting/engine.py` — call it when `decision.intents` is empty.
- `tests/milodex/execution/test_service_reasoning.py` — NEW. Verifies reasoning lands in `context["reasoning"]` for submit + backtest paths; verifies no-trade decisions produce one explanation row with no trade row; verifies `rule="no_signal"` survives the round-trip.

### Phase E — Downstream surfacing

**Modify:**
- `src/milodex/analytics/reports.py` — the trust report already lists `open_questions`. Extend it with a `recent_decisions: list[{recorded_at, rule, narrative}]` summary drawn from the latest N `ExplanationEvent` rows for the strategy. Keeps the "what has this strategy been *thinking*" surface honest.
- `tests/milodex/analytics/test_reports.py` — extend the existing seeded-run test to assert `recent_decisions` is populated when reasoning is present.

### Phase F — Docs

**Modify:**
- `docs/ROADMAP_PHASE1.md` — flip the "surviving deferral" note on R-XC-008 to closed; link this plan's tail commit.
- `docs/OPERATIONS.md` — short paragraph in the audit-record section describing the `context.reasoning` shape operators will see in exports.

---

## 4. Commit Sequence

Small commits, each self-contained and test-green. TDD pattern per §5.1.1. Every commit must run `pytest` (full suite) + `ruff check` + `ruff format --check` green before proceeding.

### Phase A — Data types (1 commit)

1. **`feat(strategies): introduce StrategyDecision + DecisionReasoning types`**
   - New dataclasses in `strategies/base.py`. `Strategy.evaluate` signature updated. No strategy implementations updated yet — **suite will go red on both concrete strategies at type-check but green at runtime**; this commit is *allowed to leave the two strategies uncompiled against the new abstract signature only if no test imports them directly*. If any test does, either include the strategy update in this commit or feature-flag. **Preferred path:** bundle with Phase B (see below).
   - Green: `pytest tests/milodex/strategies/test_base_reasoning.py`.

> **Note.** If bundling A+B in a single commit keeps the tree green with fewer moving pieces, do that. The separation above is aspirational, not dogmatic — the rule from CLAUDE.md ("don't commit half-finished implementations") wins.

### Phase B — Strategy implementations (1 commit)

2. **`feat(strategies): return StrategyDecision with reasoning from regime and meanrev`**
   - Both strategies updated to return `StrategyDecision`. Existing strategy tests updated to assert on `result.intents` + `result.reasoning`.
   - Green: `pytest tests/milodex/strategies/`.

### Phase C — Call-site propagation (1 commit)

3. **`feat(execution): thread strategy reasoning into ExplanationEvent.context`**
   - `runner.py`, `engine.py` unpack `StrategyDecision`. `service.py` accepts `reasoning` and merges into `context`. `ExecutionRequest` grows a `reasoning` field. Hardcoded `"fill_simulation"` block deleted.
   - Green: full suite.

### Phase D — No-trade rows (1 commit)

4. **`feat(execution): persist no-trade decisions with reasoning`**
   - `record_no_trade_decision` method on `ExecutionService`. Runner + engine call it on empty-intent cycles.
   - New test file `tests/milodex/execution/test_service_reasoning.py`.
   - Green: full suite.

### Phase E — Trust report surfacing (1 commit)

5. **`feat(analytics): surface recent decision narratives in trust report`**
   - `reports.py` reads recent explanation rows and exposes `recent_decisions`.
   - `test_reports.py` extended.
   - Green: full suite.

### Phase F — Docs (1 commit)

6. **`docs(roadmap,ops): close R-XC-008 deferral; document reasoning payload`**
   - ROADMAP_PHASE1.md flag flipped.
   - OPERATIONS.md paragraph added.

---

## 5. Manual Integration Verification

Reproduce the baseline regime backtest and inspect one explanation row's `context.reasoning` field end-to-end:

```bash
TMPDIR=$(mktemp -d)
MILODEX_DATA_DIR="$TMPDIR/data" MILODEX_LOG_DIR="$TMPDIR/logs" MILODEX_LOCKS_DIR="$TMPDIR/data/locks" \
  ./.venv/Scripts/python.exe -m milodex.cli.main backtest \
  regime.daily.sma200_rotation.spy_shy.v1 \
  --start 2024-01-02 --end 2024-06-28
# Expected: unchanged baseline — 124 trading days, $101,348.57, +1.35%, 1 BUY

# Inspect the one explanation row for the BUY
sqlite3 "$TMPDIR/data/milodex.db" \
  "SELECT json_extract(context_json, '$.reasoning') FROM explanations WHERE status != 'no_signal' LIMIT 1;"
# Expected: a JSON object with rule, triggering_values, threshold, narrative fields populated

# Count no-trade decisions for the same run
sqlite3 "$TMPDIR/data/milodex.db" \
  "SELECT COUNT(*) FROM explanations WHERE decision_type = 'no_trade';"
# Expected: ~123 (one per non-firing trading day)
```

### Lint + format

```bash
ruff check src/milodex/ tests/milodex/
ruff format --check src/milodex/ tests/milodex/
```

### Production-DB guard

`_guard_real_event_store_untouched` in `tests/conftest.py` remains authoritative — it will fail if any refactor accidentally writes to `data/milodex.db`.

---

## 6. Open Questions

1. **Bundling Phase A+B.** Do you want the base-class change and both strategy updates in one commit (simpler, keeps tree green), or separate commits as drafted (smaller diffs, brief intermediate state)? I lean bundled.

2. **No-trade row cadence.** `StrategyRunner` evaluates once per paper-trading cycle (daily). `BacktestEngine` evaluates once per bar. Both equal one no-trade row per non-firing cycle. Confirm this cadence is acceptable — a 10-year daily backtest would add ~2,500 rows per strategy. If you want to suppress no-trade rows for backtest, say so and I'll gate it behind a `source != "backtest"` check.

3. **`reasoning` home on the execution models.** Plan puts it on `ExecutionRequest`. Alternative: `TradeIntent` (higher upstream, but reasoning is cycle-level not intent-level — awkward). Alternative: out-of-band argument to `submit_paper`/`submit_backtest`. Confirm `ExecutionRequest` is the right seam.

4. **Narrative formatting.** Free-form sentence, or a lightweight structured format (`"<rule> fired: <triggering_values> vs <threshold>"`)? Free-form is more readable, structured is more greppable. I lean free-form with a suggested template in the docstring.

5. **Schema visibility.** Should `context.reasoning` be documented as a stable JSON contract (i.e., a schema frozen under ADR 0014 semantics), or left as internal-only? R-XC-008 says explanation records must be reconstructable from the event store alone, which implies stable-enough; but locking the schema now before real consumers exist feels premature.

---

## 7. Non-Goals

- No new CLI commands. Reasoning surfaces through existing `analytics trades --json` and `analytics metrics` paths (via the trust report extension).
- No new SQL tables or migrations. `context` JSON blob absorbs the new payload.
- No changes to `RiskEvaluator`. Risk reasoning already lives on `risk_checks` and `reason_codes`; this plan is purely the strategy-side story.
- No retroactive backfill of reasoning on historical `ExplanationEvent` rows. Old rows keep an empty `context.reasoning` — the reasoning contract is forward-only.
- No human-approval-bypass field. R-XC-008 also mentions "whether human approval was required or bypassed" — that's governance metadata (R-CLI-019 `--yes` flag) and lives on a separate track, not on `DecisionReasoning`.
- No Phase 1.4 promotion-pipeline work.

---

## 8. Verification Before Completion

Task is done when:
- `pytest tests/milodex/` green (current: 362; this plan adds ~15–25 new tests).
- `ruff check` + `ruff format --check` clean.
- Manual §5 integration block reproduces baseline regime numbers *and* shows a populated `reasoning` field on the BUY row and ~123 no-trade rows.
- `git log --oneline -6` shows the 6-commit sequence from §4.
- Production `data/milodex.db` mtime unchanged across development.
- `docs/ROADMAP_PHASE1.md` §5.1.3 R-XC-008 deferral note removed; the roadmap has no surviving Phase 1.3 deferrals.
