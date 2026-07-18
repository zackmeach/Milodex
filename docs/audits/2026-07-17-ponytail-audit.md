# Ponytail Audit — 2026-07-17

Repo-wide complexity / dead-code cut hunt. **Lists findings only; applies nothing.**

## Method

Eight parallel Cursor Grok 4.5 explore auditors, scoped by package:

| Scope | Agent |
|-------|-------|
| risk / execution / broker | [risk/exec/broker](868368b8-9392-445a-83be-2337efc9ca33) |
| strategies / configs | [strategies](3e296f88-3c09-4e96-a68f-15a816037d2e) |
| promotion / research | [promotion](63e08246-478a-407e-a1fd-428f2abdca0b) |
| backtesting / analytics | [backtesting](226b61d6-c5ad-4192-9598-13c969103b7f) |
| data / core / operations / runner | [data/core](07d51c3f-3d05-4908-a4ae-dc904ca119b0) |
| cli / commands | [cli](65d00aa5-1b0b-483c-a418-cd7715be8657) |
| gui (Python + QML) | [gui](ece8eb93-6690-4a76-9177-7e634fe29995) |
| scripts / deps / test fixtures | [deps](fdb5c449-4e03-4360-a10a-b30556011940) |

Tags: `delete` · `stdlib` · `native` · `yagni` · `shrink`. Correctness, security, and performance are out of scope.

**Caveats before cutting:** DesignSystemShowcase is currently pinned by ADR 0035 smoke tests — delete only with a smoke-test rewrite. Disable-condition catalog removal touches risk; run `risk-invariant-reviewer` if that PR lands. Line estimates are conservative and may overlap slightly across themes.

---

## Ranked findings (biggest cut first)

`delete` DesignSystemShowcase + showcase-only StrategyRow/StatusPill + unreferenced GateTable (~1.3k QML). Keep Button/Surface; rewrite ADR 0035 smoke to pin live surfaces. [src/milodex/gui/qml/Milodex/surfaces/DesignSystemShowcase.qml, …/StrategyRow.qml, …/StatusPill.qml, …/GateTable.qml]

`delete` Orphan scripts cluster: `eod_review.py` + completed `counterfactual_gate.py` (+ parity tests) + spent `reconcile_yesterday_backtest_orphans.py` (~1.1k). [scripts/eod_review.py, scripts/counterfactual_gate.py, scripts/reconcile_yesterday_backtest_orphans.py, tests/milodex/scripts/…]

`yagni` Collapse per-strategy `_validated_parameters` that re-check `parameter_specs` already enforced at load; keep thin casts only (~650 across strategies). [src/milodex/strategies/*]

`delete` Orphaned `bench_v1_fixtures.py` + sole consumer test (~750). Bench uses live `BenchState`. [src/milodex/gui/bench_v1_fixtures.py, tests/milodex/gui/test_bench_v1_fixtures.py]

`yagni` Disable-condition layer: 6 declared-only catalog entries never veto; 3 auto-evals duplicate existing checks. Drop module + `_check_disable_conditions` (~400). [src/milodex/risk/disable_conditions.py, evaluator.py, tests/milodex/risk/test_disable_conditions.py]

`delete` Dead `analytics/reports.py` TrustReport stack — zero prod callers; CLI owns trust report (~430). [src/milodex/analytics/reports.py, tests/milodex/analytics/test_reports.py]

`yagni` Delete IEX Yahoo daily cross-check (uncalibrated, opt-in only) + readiness hook + tests (~320). [src/milodex/data/consolidated_reference.py, intraday_readiness.py, tests/…]

`yagni` Drop deferred Editorial Light + Bronze themes (Dark-only launch) (~theme files + Theme.qml tokens). [src/milodex/gui/qml/Milodex/themes/*, theme_manager.py, Theme.qml]

`delete` Kanban leftovers: `KanbanState`, snapshot builders, eligibility helpers, `Theme.column.kanban*` (~250+tests). Already unregistered from QML. [src/milodex/gui/kanban_state.py, snapshot_builders.py, read_models.py]

`delete` Orphan `research/candidate_rates` + tests (zero prod/CLI callers) (~210). [src/milodex/research/candidate_rates.py, tests/milodex/research/test_candidate_rates.py]

`delete` `StrategyBankState` + `_build_bank_snapshot` + Qt lifecycle tests (~200). Keep `_query_bank` / `_compute_gate_failures`. [src/milodex/gui/strategy_bank_state.py]

`yagni` Unread strategy knobs: `sizing_rule` / `fixed_notional`, `ranking_metric`, always-true `ranking_enabled`, seasonality locked params (~200 across specs/YAML). [src/milodex/strategies/*, configs/*]

`delete` EventStore orchestration list/get/cancel APIs with no prod callers (~170). [src/milodex/core/event_store.py]

`delete` Legacy kill-switch JSON migration (~145). Event store is sole store. [src/milodex/execution/state.py, service.py, tests/milodex/execution/test_kill_switch_migration.py]

`delete` Prod-unused attribution helpers `count_positions_by_strategy` + `strategy_position_quantity` (~130). [src/milodex/risk/attribution.py, tests/…]

`yagni` Second strategy YAML parser `load_strategy_execution_config` vs `StrategyConfig` (~120). [src/milodex/execution/config.py, service.py, strategies/runner.py]

`delete` Phase-1-unreachable LIMIT/STOP submit branches + intent plumbing (~100). Keep read-side order-type map. [src/milodex/broker/alpaca_client.py, execution/models.py, execution/service.py]

`delete` `BrokerClient.get_position` + both impls — never called in `src/` (~90). [src/milodex/broker/*]

`delete` `DataProvider.get_tradeable_assets` + Alpaca `TradingClient` that exists only for it (~75). [src/milodex/data/provider.py, alpaca_provider.py, simulated.py]

`delete` Dead root conftest fixtures `sample_bar`/`sample_barset`/`sample_order`/`sample_position`/`sample_account` (~75). [tests/conftest.py]

`delete` Tests for archived one-shot audit-gap backfill (~150). Keep inert forensic script per ADR 0032. [tests/milodex/scripts/test_backfill_pullback_rsi2_audit_gap.py]

`delete` Dead `Main.qml.formatTimestamp` + `test_time_format_helper.py` (~180). Desk uses `Formatters.shortTime`. [Main.qml, tests/milodex/gui/test_time_format_helper.py]

`delete` Unused `_session_intraday` helpers (`is_session_start_bar`, `in_opening_range`, `latest_session_date_et`) (~tests-only). [src/milodex/strategies/_session_intraday.py]

`delete` Sync-only bench bridge slots (`submitBacktest` / `submitStartPaperRunner` / `submitStopPaperRunner`); QML only calls `*Async` (~80). [src/milodex/gui/bench_command_bridge.py]

`shrink` Twin screenshot-capture bootstraps → shared module (~80). [scripts/capture_gui_screenshots.py, capture_bench_interactive.py]

`shrink` Triplicate SHA-256 recipe across promotion → one `hash_canonical` (~60). [src/milodex/promotion/manifest.py, state_machine.py, run_evidence.py]

`shrink` Twin analytics snapshot writers + unused return DTOs (~55). [src/milodex/analytics/snapshots.py]

`delete` Legacy no-`ts_index` branches in engine day-slice helpers (~55). [src/milodex/backtesting/engine.py]

`delete` `BenchState.selectStrategy` / `selectedStrategyId` (~55). QML-dead. [src/milodex/gui/bench_state.py]

`delete` Prod-unused EventStore APIs: `mark_queued_intent_consumed`, `get_last_paper_buy_date_by_symbol`, test-only queued/attempt readers (~100). [src/milodex/core/event_store.py]

`shrink` Route bench submit helpers through existing mismatch/stale helpers (~45). [src/milodex/commands/bench.py]

`delete` Triple-thin `resolve_*_config_path` wrappers; call `loader.resolve_config_path` (~45). [cli/commands/promote.py, strategy.py, promotion/manifest.py, …]

`shrink` Deduplicate rich metrics row/color logic (~40). [src/milodex/cli/rich_views.py]

`yagni` `WalkForwardSplitter` class (one method) → function; fold into runner (~40). [src/milodex/backtesting/walk_forward.py]

`yagni` Fanout `param_overrides=` never passed from CLI (~tests-only). [src/milodex/research/fanout.py]

`yagni` Unused Theme tokens (`ease.*`, showcase display sizes, `column.bench*`/`kanban*`) (~40). [Theme.qml]

`yagni` move `mutmut` out of default `[dev]`; move `pyinstaller` to `[installer]` extra (−2 deps). [pyproject.toml]

`shrink` Duplicate risk/execution config helpers (`_KNOWN_PROFILES`, twin YAML loaders, twin RiskDefaults builders, twin sizing validators) (~50). [risk/config.py, profile_activation.py, execution/config.py, sizing.py]

`yagni` Pure-delegate backtest engine façades + dual PendingOrder types (~55). [backtesting/engine.py, simulation_kernel.py]

`stdlib` Hand-rolled asdict / pearson / mean-variance / compound → `dataclasses.asdict`, `statistics.correlation`/`fmean`/`stdev`, `math.prod`. [cli/_shared.py, analytics/metrics.py, walk_forward_*.py, strategies/_decider_features.py, research/evidence_assembler.py, promotion/lifecycle_criteria.py]

`yagni` Fold `cli/config_validation.py`; drop Formatter ABC; stub trust rows; market-only ORDER_TYPE_CHOICES; dead CLI/`commands` aliases (~60). [cli/*, commands/bench.py]

`yagni` Lifecycle `enforced` flag (always True); always-`iex` `feed_label`; `stage_compat.py` one-mode table; one-liner `get_active_manifest_hash`. [promotion/*, research/evidence_assembler.py]

`yagni` `register_qml_types` kwargs wrapper; `read_models` re-export shim; `risk_profile_bridge.record_startup_default` re-export; QML-unused `list_themes`. [gui/*]

`shrink` Deduplicate scored/tree exit helpers; gem `_trailing_returns` → shared feature; universe-manifest YAML scan twice. [strategies/*]

`delete` Empty TYPE_CHECKING / unused enums / unread config crumbs (`ExecutionStatus.CANCELLED`, `per_position_target_pct`, `Order.notional`, `require_manual_reset` field, `oneshot_launch_fleet.cmd`, dead `_cols`). [various]

`native` Hand-rolled Qt event-pump loops → `QTest.qWait`. [scripts/capture_*.py]

---

## By area (agent nets)

| Area | Est. lines | Deps |
|------|------------|------|
| gui | −2800 | 0 |
| scripts / deps / fixtures | −1465 | −2 |
| risk / execution / broker | −750 | 0 |
| strategies / configs | −650 | 0 |
| backtesting / analytics | −620 | 0 |
| data / core / ops / runner | −520 | 0 |
| promotion / research | −360 | 0 |
| cli / commands | −239 | 0 |

Overlaps mean the raw sum overstates unique cuts; the merged ceiling below discounts that.

---

## Suggested cut waves (optional)

1. **Zero-risk deletes** — orphan scripts, dead fixtures, unused GUI leftovers (Kanban/StrategyBankState/bench fixtures), dead analytics reports, orphan candidate_rates.
2. **Dep hygiene** — `mutmut` / `pyinstaller` extras.
3. **Surface shrink** — CLI wrappers, Theme tokens, sync bridge slots (update smoke pins with showcase if deleted).
4. **Risk-adjacent** — disable-conditions, execution config re-parse, Phase-1 limit/stop plumbing — behind risk-invariant review.
5. **Strategy YAGNI** — unread knobs + collapse dual validators (large diff, behavior-preserving if specs stay SoT).

net: -~6500 lines unique (conservative after overlap), -2 deps possible.

---

## Verification addendum — 2026-07-17 (post-audit adjudication)

Every finding was independently re-verified against real code by six scoped verification
agents plus orchestrator-side checks (Windows Task Scheduler, CI shape). Outcome: four PRs
merged on green (#367–#370), net **−4,943 lines, −2 default deps**. Everything else was
refuted, spec-pinned, or judged not worth the churn. Findings below are adjudicated —
do not re-raise the refuted/skipped items without new evidence.

### Shipped (merged on green + review)

- **#367 deps extras** — `mutmut` → `[mutation]`, `pyinstaller` → `[installer]`; uv.lock regen; INSTALL/TEST_EFFICACY/script-header updates.
- **#368 pure deletes** — `reconcile_yesterday_backtest_orphans.py`, 5 dead root-conftest fixtures, `bench_v1_fixtures` pair, `analytics/reports` pair, `candidate_rates` pair, 3 orphan `_session_intraday` helpers.
- **#369 risk/core trim** (risk-invariant-reviewer APPROVE) — 2 test-only attribution helpers, `BrokerClient.get_position`, `ExecutionStatus.CANCELLED`, `Order.notional`, 8 dead EventStore APIs (6 orchestration read/cancel, `mark_queued_intent_consumed`, `get_last_paper_buy_date_by_symbol`), `get_tradeable_assets` + data-layer `TradingClient`, triplicate hash recipe collapsed to `promotion/manifest.hash_canonical` (byte-identical; parity guard unchanged).
- **#370 GUI purge** (adversarial review APPROVE) — showcase cluster (DesignSystemShowcase/StrategyRow/StatusPill/GateTable), kanban leftovers, `StrategyBankState` wrapper (pure fns kept), `BenchState.selectStrategy`, `Main.qml.formatTimestamp`, `list_themes`, dead Theme tokens. ADR 0035 addendum; subprocess smoke retargeted to `BenchSurface.qml`.

### Refuted findings (audit was wrong — keep the code)

- `oneshot_launch_fleet.cmd` — live Task Scheduler target (MilodexLaunch-20260717/20260720); grep can't see the external binding.
- `counterfactual_gate.py` — live parity guard asserting its inlined thresholds track `ACTIVE_PROMOTION_POLICY`, plus frozen-evidence citations (`docs/reviews/`).
- Disable-condition layer — redundant in veto outcome, but SRS R-STR-014 `shall` + 10 mapped tests + `disable_conditions_additional` is a loader-required key on every strategy YAML. Removal is an SRS amendment, not a dead-code delete.
- Kill-switch JSON migration — live one-way absorber, ADR 0018 pins it ("migration path stays"); deleting risks dropping an active halt on a pre-cutover machine.
- Lifecycle `enforced` flag — read in a live governance branch (`orchestrator.py:394`); deliberate ADR 0058 M4 switch.
- `stage_compat.py` — consumed by CLI + bench facade + risk evaluator; the ADR 0051 §6 layering seam.
- `get_active_manifest_hash` — backs the execution manifest-drift gate (`service.py`).
- Formatter ABC — two implementations (Human/Json, ADR 0014); the "one impl" premise was false.
- "Dual PendingOrder types" — one definition each (daily vs intraday), no duplication.
- `cli/_shared` "hand-rolled asdict" — hand-picked field coercion (`isoformat`/`.value`) that `dataclasses.asdict` cannot do.
- Most stdlib-math swaps — already stdlib (`fmean`/`median`/`pstdev`) or the file has no such code; the Sortino swap would *change the formula* (semi-variance from 0, not pvariance) and Sharpe/pearson swaps risk fsum-vs-sum epsilon drift into a promotion `<=` gate.
- `per_position_target_pct` — unread in code but R-STR-016 `shall` names it (see Surfaced).
- fanout `param_overrides` — generated the 16 committed matched-exposure baseline configs (E-PR2); only regeneration/audit mechanism for those rates.
- `column.bench*` wildcard — half the set (`benchMetric`/`benchAction`/`benchStatus`) is live in BenchSurface/BenchRow; only the other three were dead (removed in #370).

### Verified-dead-or-true but skipped deliberately (churn > win)

Sync bench bridge slots (docstring records intentional test-harness retention); Editorial
Light/Bronze theme removal (ADR 0035 locks three themes — owner call); LIMIT/STOP submit
plumbing + `ORDER_TYPE_CHOICES` narrowing (sacred-layer churn, already unreachable-by-guard);
`load_strategy_execution_config` collapse (would invert risk/execution→strategies layering);
`_validated_parameters` collapse (~650 lines: loader specs+relations cover prod, mirrors are
deliberate test isolation, two strategies need nested coercion the spec system can't express);
unread strategy knobs (loader-required YAML keys fleet-wide; `ranking_enabled` genuinely
branches); no-`ts_index` engine branches (differential oracle for the perf-equivalence tests);
`WalkForwardSplitter` fold; engine façades (documented RM-005a API seam); snapshot-writer
shrink; `resolve_*_config_path` wrappers; `config_validation` fold; `rich_views` dedup;
screenshot-script dedup / QTest.qWait; stub trust rows; dead `_cols` (forensic probe script);
`feed_label` inline; `get_queued_intent`/`list_execution_attempts` (D6 drain-proof test infra).

### Surfaced for the owner (not fixed here)

1. **Latent `_sharpe` bug** (`analytics/metrics.py`): `risk_free_daily` is subtracted from the mean but not from the squared deviations — variance is wrong whenever risk-free ≠ 0. Dormant today (always called with 0.0) and it feeds the promotion `<=` gate, so fix deliberately, not as a drive-by.
2. **R-STR-016 spec≠code**: sizing "shall default to `sizing.per_position_target_pct` (0.10)" but nothing reads the key (runner sizes from `intent.notional_pct`). Amend the SRS or wire it.
3. **Theme switching is orphaned**: the deleted showcase was the only `set_theme` UI entry point; the three themes + ThemeManager machinery remain with no operator-facing switch. Decide: dark-only launch (drop Light/Bronze via ADR amendment) or add a real theme control.
4. **Operator-tool disposition**: `eod_review.py` (its own retirement condition — a `milodex analytics live` command — hasn't fired) and the IEX cross-check (`milodex data --cross-check-reference`) are reachable operator tools referenced by no runbook. Keep or retire — owner call.
