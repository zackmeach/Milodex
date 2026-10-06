# Milodex Architecture Deepening Audit — 2026-10-05

**Date:** 2026-10-05
**Baseline:** master @ `e323309`, 303 commits after the [2026-06-12 audit](2026-06-12-architecture-deepening-audit.md).
**Scope:** The whole codebase, using the `improve-codebase-architecture` method: find seams worth deepening for testability and AI-navigability. Weight goes to code that changed since June.

**Method**
- **Exploration.** Eight cluster explorers covered: runner/drain, execution↔risk, promotion governance, research lane, event store/operations, market data, GUI/commands, and strategies/engine.
- **Verification.** Eight independent adversarial verifiers followed, one per theme, each told to refute. Every claim kept below was re-grounded at file:line, and most were reproduced with read-only scripts:
  - live `data/milodex.db` opened with `mode=ro`;
  - scratch EventStores;
  - stubbed HTTP;
  - no broker or network calls.
- **Prior adjudications honoured.** Nothing on a refuted or skipped list is re-raised. The lists come from the 2026-06-12 audit and its second opinion, the 2026-07-17 ponytail addendum, and the 2026-07-11 test-gap sweep.

Vocabulary as before: *module / interface / implementation / depth / seam / adapter / leverage / locality*, with the deletion test as the spine.

---

## Bottom line

**June's pattern is mostly contained.** June's pattern was *leaked invariants*: one rule copied N times, kept correct by byte-identity. Since then:
- the hash recipe was collapsed;
- the staleness predicate is shared;
- the config resolver was consolidated;
- the parity tests are pinned.

**The pattern now is more consequential.** A rule is owned by the surface that was built first. The surface built later re-derives it with different semantics: it reinterprets the rule rather than copying it. Three seams carry this:

1. **Backtest → paper.** The backtest kernel and engine own position and session semantics: held days, same-session flatten, RTH bounds and lookback. The runner re-derives them differently, or not at all. **Paper does not run what was promoted.**
2. **Preview → authority.** The promotion policy answers "do these metrics pass tier X". It never answers "may strategy S advance to stage T on run R". So the GUI, the CLI, the engine and the research screen each choose the tier, the trade floor, the lifecycle identity and the evidence run themselves.
3. **Adapter → consumers.** Two adapters each make a load-bearing classification:
   - the broker adapter decides whether an order came into existence, by message substring;
   - the cache adapter decides whether a cached bar is on the current adjustment epoch, inside one branch.

   Four consumers trust each answer.

**Result: 17 verified defects** (6 LIVE, 10 LIVE-reachable, 1 record-only).
- **None bypasses the risk veto.**
- **Several are fail-open on the operator's or the evidence's side:**
  - a landed order is recorded as rejected and becomes invisible to the strategy ledger;
  - a synthetic row passes a freshness gate;
  - promotion evidence can come from another strategy's run.

---

## Verified defects

**How to read these entries**
- **Severity scale:**
  - **LIVE**: happening now.
  - **LIVE-reachable**: the trigger path is verified but has not been hit.
  - **record-only**: wrong durable data that feeds no decision.
- **→ Cn:** names the deepening candidate that retires the defect's class.
- **Tiny fixes:** they do not need to wait for the deepening.

**V1. Bench hides governance verbs because it judges with the capital tier.** LIVE, fail-closed.
- **What happens.** `gui/strategy_bank_state.py:74,242-250` `_compute_gate_failures` applies:
  - the `capital_gate` thresholds (aliases at `promotion/state_machine.py:58-60`);
  - a fixed trade floor of 30;
  - `family=="regime"` as the lifecycle identity.

  That verdict sets `GateResult` (`gui/bench_actions.py:104-109`), and `can_promote_to_next` reads it (`gui/bench_v1.py:~258-277`). The authority, by contrast, uses the paper tier for backtest→paper (`promotion/policy.py:108-112,169`).
- **Reproduced on the live DB:**
  - `breakout.daily.nr7_inside.liquid_largecap.v1` (S 0.192 / DD 13.4% / N 930) and `seasonality.daily.turn_of_month.spy.v1` (S 0.04 / DD 9.6% / N 47, floor 0) pass `check_gate(to_stage="paper")` but get no "Promote to Paper".
  - The idle rows `52w_high_proximity` and `xsec_rotation` lose "Return to Backtest".
- **Tests pin opposite answers:** `test_strategy_bank_state.py:32-37` vs `test_bench_facade.py:~2825`.
- **CLI:** unaffected. → C2.

**V2. Evidence-run admissibility is unchecked on the statistical path.** LIVE-reachable.
- **The gap.** `promotion/run_evidence.py:48-63` resolves `--run-id` by id alone (`core/event_store.py:2579-2585`). It checks neither the run's owner, nor its status, nor that it is walk-forward.
- **Reproduced through the real orchestrator up to the durable write:** `meanrev.daily.ibs_lowclose.index_etfs.v1`, whose own run has Sharpe −0.12, is promoted to paper as `statistical` on `nr7_inside`'s run.
- **Non-walk-forward runs compound it.** `list_trades_for_backtest_run` (`event_store.py:2658-2664`) has no status filter, and `analytics/metrics.py:111-113` counts every buy/sell row. So skipped rows inflate the gate's trade count. Run 123: metadata says 198 trades, there are 640 rows, 442 of them skipped.
- **Lifecycle promotions disagree with themselves.** They record `request.run_id` (`orchestrator.py:479,502`), while the criteria select their own run (`lifecycle_criteria.py:109`).
- **Exposure today:** all 18 existing ledger rows are consistent. Paper stage only. → C2.

**V3. `BacktestEngine.min_trades_required()` crashes on the regime config.** LIVE-reachable, research only.
- `backtesting/engine.py:356-358` does `int(cfg.get(..., 30))`, and `configs/spy_shy_200dma_v1.yaml` sets `min_trades_required: null`. The result is a `TypeError`.
- `research screen` catches it as an error row (`walk_forward_batch.py:454-461`), but only after the run has been persisted as completed.
- *Tiny fix.* → C2.

**V4. Paper counts `held_days` in calendar days; the evidence counts trading days.** LIVE-reachable.
- **Runner:** `(now_utc.date() - opened_date).days` (`strategies/runner.py:1791-1798`).
- **Kernel:** one tick per trading day (`backtesting/simulation_kernel.py:325-344,467`).
- **Documented contract:** "trading days since entry" (`docs/strategy-families.md`).
- **Effect.** Both start at 0 on the fill day, and every consumer uses `>=`. So a Tuesday–Friday fill exits 1–2 trading days earlier in paper than in the backtest it was promoted on. A lock-in after 20:00 ET adds one more day.
- **Exposed:** 5 paper daily configs — `pullback_rsi2.curated_largecap`, `bbands_lowerband`, `atr_channel` and `donchian_20_10` (max_hold 5), and `tsmom` (max_hold 10).
- **Not yet observed under current code:** every post-June max-hold lot was a Monday fill.
- **Tests pin the calendar formula:** `test_runner.py:984,1149`. → C1.

**V5. `same_session` is not enforced in paper.** LIVE-reachable.
- **Only the engine reads `position_lifecycle`** (`engine.py:1200-1231`, flatten at `:1392-1437`). Paper relies on each strategy's 15:55 time-stop bar instead.
- **The time-stop exit is vetoed.**
  - The completed-bar cutoff (`runner.py:1772-1776`) first evaluates that bar at 16:00:00 ET.
  - The resulting SELL goes straight to `submit_paper` (`runner.py:639`).
  - The risk layer vetoes it as `market_closed` (`risk/evaluator.py:437-447`).
- **DB evidence:**
  - All 15 of 15 evaluations of the 19:55Z bar between 9/28 and 10/2 were recorded at 20:00:0x with `market_open=0`.
  - In 2 of 84 historical cases, Alpaca's clock still reported open. That would let a post-close market order through.
- **Nothing else flattens.** The controlled stop, the kill switch and the risk layer all leave positions open. As a result, `benchmark.unconditional_intraday_long.spy.v1` becomes buy-and-hold in paper.
- **Running now:** `rsi2.iwm`, `rsi2.qqq` and `vwap_trend.spy`, all with frozen paper manifests. The defect is masked only because every entry since 9/29 has been vetoed as `max_total_exposure_exceeded`.
- **Related parity gap:** paper intraday runners evaluate pre-market and post-market bars (observed 12:20Z–20:55Z) that the backtest masks out. → C1.

**V6. The half-day calendar expires after 2026-12-24.** LIVE-reachable, dated.
- `strategies/_session_intraday.py:43-56` hardcodes `US_MARKET_HALF_DAYS`; its last entry is 2026-12-24.
- Every 2027 half-day is unknown to the session strategies' defensive close and to readiness.
- The engine's RTH mask has no half-days at all (`backtesting/intraday_simulation.py:39-53`). → C1.

**V7. A queued EXIT whose pre-submit step raises is stranded silently.** LIVE-reachable whenever the daily fleet runs.
- **The swallowing `except`.** The generic `except` in `_drain_queued_intents` (`runner.py:1546-1555`) logs the error and leaves the row queued.
- **Nothing surfaces the stranded row:**
  - the stranded-exit alerter only sees rows that are not drainable;
  - the sweep alerts only at the 7-day TTL;
  - the next lock-in supersedes the row to `obsolete` with an info-level log (`runner.py:1060-1089`).
- **Triggers:**
  - a `DataConnectivityError` at the open (the 2026-07-23 TLS-EOF class);
  - a SQLite lock during ledger reads;
  - `evaluate` raising on truncated bars.
- **Outcome:** the exit is not executed, and no alert or explanation is written. That contradicts ADR 0057: "every outcome … records an explanation row".
- **Connectivity blind spot.** The same `except` swallows connectivity errors. So the runner's outage budget and its `broker_connectivity_degraded` alert never fire during a data outage at the open.
- **Reproduced** on a temp store. → C4.

**V8. Drain dispositions have observability gaps.** LIVE-reachable, fail-safe. All behave safely, but each loses information:
- A config-drift EXIT is alerted as `no_clean_handoff` (`runner.py:1302`).
- A config-drift ENTRY, and an unclean-handoff ENTRY, write no explanation.
- A flat-ledger EXIT retire writes nothing.
- A transient `tradability_read_error` retires the row on its first miss. #374 fixed the same class for `no_fresh_price`.
- #381's terminal-on-`stale_market_data` rule contradicts the unamended ADR 0057 addendum ("no terminal-veto taxonomy").
- #382 had to split a risk reason code so the runner's string match (`runner.py:1616`) stayed correct. → C4.

**V9. Broker 5xx errors and 504 retries are recorded as definitive rejections.** LIVE-reachable, never observed.
- **SDK behaviour.** alpaca-py 0.43.2 retries POST `/v2/orders` on 504, three times (`alpaca/common/rest.py:129-135,201-207`). It raises `APIError` for every other status.
- **The classifier** (`broker/alpaca_client.py:246-257`) matches on message text and never reads `status_code`.
- **Stubbed-HTTP repro.** These all become `OrderRejectedError`:
  - a 500, 502 or 503;
  - four 504s in a row;
  - a 504 followed by a 422 duplicate `client_order_id`, which proves the order exists.
- **Why that matters.** `rejected` is the one status excluded from both the duplicate-order veto (`event_store.py:1458-1476`) and the strategy ledger (`core/trade_status.py:19`). So a landed order is invisible to the strategy:
  - an entry can be re-entered (bounded by the account single-position cap, which reads broker positions);
  - an exit can leave the ledger long, so a later SELL opens a short.

  The only backstop is reconciliation at the next startup or rollover.
- **Two related gaps:**
  - SDK requests carry no timeout, so a hung POST holds the ADR 0056 account lock indefinitely.
  - Read-path 5xx errors re-raise raw from `_read_call` (`alpaca_client.py:259-289`) and crash the runner, instead of counting against the connectivity budget.
- **The correct branch already exists:** unknown delivery maps to status `error` (`execution/service.py:624-638`). → C5.

**V10. Exits can deadlock on the concurrent-position caps.** LIVE-reachable. The fix changes veto behaviour, so it is an **owner decision**.
- **The missing exemption.** `_check_concurrent_positions` and `_check_strategy_concurrent_positions` (`risk/evaluator.py:732-782,869-887`) have no exemption for exposure-reducing orders; the four sibling checks do.
- **When exits block:**
  - a full exit when held ≥ cap+2;
  - a partial exit, or any exit with a resting BUY, when held ≥ cap+1.
- **Current exposure.** Seven symbols are held: CVX, DIA, QQQ, SMH, SPY, XLE and XOM. Under the conservative profile (cap 5), every exit would be blocked.
- **The silent fallback.** The active profile is aggressive (cap 15). But a missing, empty, unknown or unreadable `data/risk_profile.txt`, or a malformed overlay, silently resolves to conservative (`risk/config.py:184-191,318-345`). That path also skips the runners-active guard.
- **No escape hatch.** The kill switch also blocks reducing orders, so nothing in the system can flatten. This contradicts the DC-1 principle that a held position must always be exitable.
- **Constraint on the fix.** The strategy-cap exemption must key on the ledger, not on broker net (ADR 0055). → C3.

**V11. A synthetic fault-check passes the Bench data-freshness gate for every strategy.** LIVE-reachable, low impact.
- **The chain:**
  1. `promotion/fault_injection.py:248` writes `latest_bar_timestamp=now` with `backtest_run_id=None`.
  2. `get_latest_bar_timestamp` (`event_store.py:1138-1143`) is global and excludes only backtest rows.
  3. So `_DefaultWorkflowReadiness` (`commands/bench.py:485-520`) reports the data as fresh until the next live evaluation, up to 24h.
- **Reproduced:** one fault-check turns `data_stale` into `None`.
- **Governance is untouched.** `check_gate` and the criteria are unaffected, and the CLI has no freshness gate.
- **The root fix is one line.** The fault-check fetched no bar, so it must not record a bar time; criterion (c) reads `recorded_at` (`lifecycle_criteria.py:242`). *Tiny fix.* → C7.

**V12. Operator surfaces count non-operator explanation rows.** LIVE for display, LIVE-reachable for records.
- **Five sites, five different exclusion subsets:** `event_store.py:1138-1143` and `:1230-1233`, `gui/_dashboard_scope.py:15-20`, and `cli/commands/report.py:161-172` and `:370-374`.
- **Effects:**
  - **Trust report:** for 32 of 56 strategies today, the last action and config fingerprint come from a backtest row.
  - **`report daily`:** counts a synthetic veto as a rejection.
  - **`count_paper_rejections`:** would count enforce-mode backtest blocks into the paper→micro_live evidence package, because backtest rows carry `config.stage`. Record-only: no gate reads it.
- **ADR premise is false.** ADR 0058 calls the synthetic exclusions belt-and-suspenders, assuming synthetic rows carry the backtest stage. That is false for the regime strategy, which is at `stage: paper`. → C7.

**V13. Runners read bars from mixed adjustment epochs.** LIVE.
- **Why runners never heal.** The adjustment-epoch probe and heal run only on a full cache hit with `end < today` (`data/alpaca_provider.py:146-152`). Runners always request `end = today` (`runner.py:1749`).
- **What runners do instead:** a tail refetch plus a 60-day weekday gap scan. Each holiday "gap" refetch overwrites the next session's bar with fresh-epoch data.
- **A concrete splice in TLT.** `1Day` and `5Min` opens differ by ×1.0036–1.0038 before 2026-06-01. The exception is 2026-05-26, the day after Memorial Day, at ×1.000000.
- **Effect on signals.** Dividend ex-dates show up as one-day price steps in runner indicators; RSI2 is sensitive at 0.1–0.4%. Meanwhile, the promotion evidence was computed on total-return-adjusted bars. A split on a runner symbol would go undetected on this path.
- **Healing is incidental:** it happens only when some read of the symbol uses `end < today`. → C6.

**V14. The Ledger and activity feed render drawdown ×100.** LIVE, display.
- `gui/ledger_builders.py:205` and `gui/activity_feed_state.py:208` multiply `max_drawdown_pct` by 100, but it is already a percent: the writer is `walk_forward_runner.py:488`, and the gate compares it to 25.0.
- **Live:** "max-dd 401.1%" for `gap.gap_continuation.intraday.xle.v1`, whose real drawdown is 4.01%.
- **Rendered correctly elsewhere:** Bench and Front.
- **Test masks it:** `test_activity_feed_state.py:1019` writes the wrong value in a comment and asserts only the substring.
- *Tiny fix.*

**V15. Backtest risk pricing and audit rows use a future bar.** LIVE for audit rows; latent for metrics.
- **The mismatch.** `data/simulated.py:34-36,73-81` resolves "latest" as the last bar of the fill *date*, while the kernel marks the broker at the fill price (`simulation_kernel.py:183-184`).
- **Effect on the audit trail.** Every backtest submit explanation records a bar up to the session close beyond the decision; for example, row 2127729 has its decision bar at 14:45Z but records 19:55Z. It also prices `estimated_order_value` on that bar.
- **Metrics are unaffected under BYPASS.** Fills, P&L and every promotion metric come from the kernel. BYPASS is the default, and ENFORCE has never been used in any of the three stores.
- **Under ENFORCE it would matter.** With `--risk-policy enforce`, the single-position and total-exposure checks would decide on future prices.
- **Fix home:** `sync_broker_state` as the single "now". (Adjacent to C1.)

**V16. Lifecycle position is read five ways.** LIVE for display, LIVE-reachable otherwise.
- **Desk count wrong:** Desk "Backtest Only" shows 20, including two idle-shelf strategies. Bench has 18 backtest rows with evidence.
- **Stale evidence on re-promotion.** A Bench row's metrics and `evidenceRunId` come from the superseded promotion, while the menu verdict uses the latest backtest. So Demote → Initiate Backtest → Promote submits the *old* run id, and the gate evaluates stale evidence: `metrics_from_run` has no freshness check.
- **Demote never retires the paper manifest.** Reverting the YAML stage line therefore re-arms paper trading with no promotion row.
- **Mixed stage stamps.** Demote-while-running stamps explanations `backtest` and same-session no-action rows `paper`.
- **Ledger-only disable.** `milodex promotion demote --to disabled` touches only the ledger, and the strategy keeps trading. This is a documented "slice 3" deferral; the CLI prints "ledger-only".

→ C8.

**V17. Evidence-row provenance is synthesized rather than carried.** Record-only.
- `research/evidence_assembler.py:539-563` builds a run manifest that describes no actual run:
  - `risk_policy="research_screen"`, which is not a `RiskPolicy` value; real runs record `bypass`;
  - `warmup_start=start`, where real runs use start minus at least 30 days.
- **Where it shows up:** all 3 existing registry rows (M3 `data-run1` store) and every future row. It feeds no verdict or display.
- **Coverage is lost.** Preflight's frozen-calendar coverage is dropped; only `snapshot_id` is kept. → C9.

**Owner question (single-source; intent unclear).** `milodex experiment update` appends a row, and the latest id wins (`event_store.py:2059-2094`). So it supersedes an official registry verdict and bypasses the evidence coherence guard. Is a manual terminal-status override intended?

---

## Deepening candidates (ranked by leverage)

Sizes follow house convention: tiny / small / decent / large.

### C1. Only the backtest kernel owns strategy-position and session semantics (backtest ↔ paper parity)

- **Cluster:**
  - `backtesting/simulation_kernel.py` (`tick_held_days`, entry state).
  - `backtesting/engine.py` (same-session flatten, RTH mask, lookback resolver) and `backtesting/intraday_simulation.py`.
  - `strategies/runner.py` (`_build_entry_state`, `_history_window_days`, the intraday `run_cycle` path).
  - `strategies/_session_intraday.py` (half-day list, `is_time_stop_bar`, private ET-offset helper).
  - The 10 session strategies.
- **Why coupled:** a strategy's promotion evidence is produced under kernel semantics, but paper re-derives each rule:
  - **held days:** calendar vs trading days;
  - **lookback:** ignores the override, has no cap, counts ints only;
  - **session bounds:** no RTH filter and no same-session flatten;
  - **half-day calendar:** lives in strategy code with a hardcoded end date.
- **Retires:** V4, V5, V6 and the extended-hours parity gap. V15 is its backtest-side twin.
- **Deletion test:** holds.
  - Delete the engine flatten and backtests silently carry positions overnight; that is the `acd96a4` overnight-null BLOCKER. Paper already behaves as if that deletion had happened.
  - There are two held-days producers and two lookback resolvers, with no parity test between them.
- **Includes prior #17, which is now worse.** Duplicated helpers: `_no_signal` ×11, `_exit_decision` ×9 (plus 3 bench copies), `_entry_price` ×8. Half-day and time-stop blocks: ×10 each. The 3 newest strategies have no half-day tests.
  - The leaf helpers belong in `base.py`, because crypto must not import `_session_intraday`.
  - The half-day/time-stop rule belongs with the session owner.
- **Dependency category:**
  - in-process for "bar dates → sessions held" and "bar → in RTH / last decidable bar";
  - local-substitutable for the calendar: the frozen Alpaca `calendar.json` replaces the hardcoded half-day list.
- **Test impact:**
  - The calendar-day pins (`test_runner.py:984,1149`) become one kernel↔runner parity test.
  - About 25 per-strategy half-day, time-stop and one-entry tests become session-module tests.
  - Paper-side flat-by-close becomes testable; today there is no seam for it.
- **Constraints:**
  - A paper pre-close flatten must not exempt exits from `market_hours`.
  - That flatten fills before 16:00, while the backtest flattens at the 16:00 close. This needs an ADR note.
  - `multi_session` (crypto) must never call it.
- **Sacred-adjacent:** yes; it touches the submit path and the market-hours check.
- **Size:** large, about 4 PRs:
  - held-days parity (small);
  - session owner plus paper flatten (decent);
  - skeleton consolidation (decent);
  - calendar source (small).

### C2. Promotion has no "may S advance to T on run R" verdict

- **Cluster:**
  - `promotion/` — `policy.py`, `orchestrator.py`, `run_evidence.py`, `lifecycle_criteria.py`, `state_machine.py`.
  - GUI — `gui/strategy_bank_state._compute_gate_failures` and its 4 consumers.
  - `commands/bench.py` — propose, and the idle→backtest transition.
  - `cli/commands/backtest.py`.
  - `backtesting/engine.min_trades_required` and `walk_forward_batch`.
- **Why coupled:**
  - **The policy holds thresholds but makes no decisions.** It is a typed source of thresholds, not of decisions. Callers each decide:
    - tier selection by target stage;
    - trade-floor resolution, spelled 5 ways including two literal 30s;
    - lifecycle identity (`applies_to` vs `family=="regime"`);
    - evidence-run admissibility: owner, status, walk-forward, and which run counts as "latest".
  - **Propose drifts from the authority.** It has no read-only assess path, so the preview restates some checks and omits the gate, scope and criteria.
  - **The non-blank rule is scattered.** Evidence-input failures escape the orchestrator's result union as `ValueError`, so that rule lives in about 6 places, including QML.
  - **The idle→backtest transition is misplaced and misordered.** It lives in the Bench facade, imports a private state-machine helper, and writes the YAML before the ledger. That is a latent partial write, and an append failure also leaves the orchestration job `running` forever.
- **Retires:** V1, V2, V3, plus the preview drift, the `ValueError` escape and the write ordering.
- **Deletion test:** holds. The tier/floor/identity logic reappears in 7 callers today.
- **Dependency category:** in-process (the verdict is pure), plus local-substitutable (tmp EventStore + YAML) for run admissibility.
- **Test impact:** the S/D/N pins, walk-forward-batch gate tests and propose-restatement tests become:
  - one policy verdict table;
  - one menu↔submit parity test;
  - orchestrator boundary tests for a foreign run, a non-completed run, and non-walk-forward skipped rows.
- **Constraints:**
  - It tightens and never loosens.
  - Thresholds stay in `policy.py`.
  - `STRATEGY_BANK.md`'s capital-tier blocked label can survive as an explicitly requested tier.
- **Sacred-adjacent:** yes (governance).
- **Size:** decent, 2–3 PRs.

### C3. Risk-check policy is encoded by code position, not declared

- **Cluster:**
  - `risk/evaluator.py`: `_CHECKS`, 17 checks.
  - `risk/policy.py`: the hand list in `BacktestStructuralRiskEvaluator` (prior #20).
  - The G1 guard at `execution/service.py:~127`.
  - `docs/RISK_POLICY.md`.
- **Why coupled:** two policies are implicit in where code sits.
  - **Exposure-reducing exemptions:** which checks exempt exposure-reducing orders is held in 4 hand copies. Two of them were added after the production incidents of 07-14 and 07-21.
  - **Backtest replay:** which checks run during backtest replay is a separate hand list.
  - **The ADR trigger has fired.** ADR 0019 said to revisit a registry once the rules passed about 15; there are now 17.
  - **The doc is behind.** RISK_POLICY.md lists two exemptions; the code has four.
- **Retires:**
  - V10, which is an owner decision.
  - Price validity, a latent defect: a NaN, zero or negative price passes every notional cap. Drain entries raise before submit, and exits are exempt.
- **Deletion test:** holds. The exemption has drifted copy by copy, as the incidents show.
- **Dependency category:** in-process.
- **Test impact:** the per-check exemption tests become one membership table test. That also gives prior #20 its missing guard.
- **Constraints:**
  - Veto behaviour stays byte-identical, except where the owner decides otherwise (V10).
  - Passing messages persist in explanations, so they must be kept verbatim.
  - G1 must also refuse a `_CHECKS` override (`type(x)._CHECKS is not RiskEvaluator._CHECKS`). Otherwise the structural-subset fix becomes a G1 bypass.
- **Sacred:** core.
- **Size:** small–decent, plus the owner decision.

### C4. The queue-at-open lifecycle is welded into the runner

- **Cluster:**
  - `strategies/runner.py`:
    - `_drain_queued_intents`, about 330 LOC;
    - persist (~965-1090);
    - sweep (~801-862);
    - stranded alert (~1288-1303).
  - `runner/drain_policy.py`, which covers tradability only.
  - The queued-intent section of `core/event_store.py`. The drainability fence and the config-hash check are each written twice, once for enumerate and once for CAS.
- **Why coupled:**
  - **Dispositions are inline.** Each outcome × intent class maps to a status, an alert, an explanation and a retry. That mapping is decided inline at 9 sites.
  - **A risk reason code is string-matched.** The permanent-veto rule matches the code as a string.
  - **Exclusion reasons are lost.** Enumeration returns only the surviving rows, so the runner reconstructs the exclusions by set-difference and loses the reason.
- **Retires:** V7, V8, and the connectivity masking.
- **Deletion test:** holds.
  - Today `drain_policy.py` fails it: deleting it moves 20 lines.
  - A disposition table would concentrate the 9 branch sites and their fix history (4c36ca0, ba35846, 666e4df, #346, #374, #381).
- **Dependency category:** local-substitutable. Use a tmp SQLite EventStore, `SimulatedBroker`, and a fake submit that returns `ExecutionResult`. `test_drain_policy_tradable.py` already shows the pattern.
- **Test impact:**
  - About 1,500 LOC of `test_runner_drain_*.py` goes away. Those tests need a full runner build, 5 private monkeypatches and a hand-rebuilt persist codec. They become disposition-table tests.
  - Keep `test_persist_relaunch_drain_drill.py` as the boundary test.
- **Constraints:**
  - The ADR 0057 addendum forbids a terminal-veto taxonomy. So #381's rule must be one named exception plus an ADR amendment.
  - The CAS re-assertion (the TOCTOU guard) stays. Only its definition is single-sourced.
- **Sacred-adjacent:** yes.
- **Size:** decent.

### C5. The broker boundary does not own delivery certainty

- **Cluster:**
  - Broker side:
    - `broker/alpaca_client.py` (the submit classifier and `_read_call`);
    - alpaca-py's retry defaults;
    - `core/_alpaca_retry.py`;
    - `broker/client.py` (the interface).
  - Consumers:
    - `execution/service.py` (outcome mapping);
    - `core/event_store.py` (duplicate veto);
    - `core/trade_status.py`;
    - `strategies/runner.py` (strand detection).
- **Why coupled:**
  - Four modules read `OrderRejectedError` as "no order exists".
  - The adapter decides that from message text, while the SDK retries writes underneath it.
  - The interface declares no outcome contract.
- **Retires:** V9, the missing request timeout, and the read-path 5xx crash.
- **Deletion test:** holds. Four files consume the classifier's verdict; the classifier should be deep, but it is shallow and wrong. Prior #23 rated it low because the wording was stable. The defect is a whole status class, not wording drift.
- **Dependency category:** true-external (Alpaca HTTP). Stub at `requests.Session` inside a real `TradingClient`.
- **Test impact:**
  - Today the message-text tests mock `TradingClient`, so the SDK never runs.
  - They become status-code tests: 500, 503, 504×4, and 504→422.
  - Add one composed test: an unknown-delivery submit blocks the next duplicate.
- **Constraints:**
  - Unknown delivery maps to the existing fail-loud `error` path.
  - Better still, resolve the outcome via `get_order_by_client_id`.
  - `error` alone shields only for `duplicate_order_window_seconds` and writes no trades row.
- **Sacred:** yes.
- **Size:** small.

### C6. Cache-correctness policy is welded into the Alpaca adapter, contradicting ADR 0002/0017

- **Cluster:**
  - **`data/alpaca_provider.py` `get_bars`**, which holds:
    - the range plan;
    - the today-refetch;
    - the 60-day weekday gap scan;
    - the epoch probe/heal;
    - batching;
    - fail-soft persist;
    - the contention alert;
    - `backfill_range`.
  - **`data/cache.py`.**
  - **Completeness, re-derived 5 ways:** `data/intraday_readiness.py:172`, `research/snapshot.py`, `data/bar_quality.py`, `cli/commands/data.py`, and the provider heuristic.
  - **Cache identity, resolved three ways:** `CACHE_VERSION`, the GUI disk scan, and the snapshot's hand-built path.
- **Why coupled:**
  - One invariant lives inside one branch of one adapter method: every served cached bar is on the current adjustment epoch and the interior is complete.
  - The CLI context is typed to the concrete adapter, so `backfill_range` isn't on the interface.
- **Retires:** V13. It also closes the still-open G2 gap: no test touches the gap scan or `_range_has_weekday`.
- **Deletion test:** holds for testability and invariant locality. There is a single production caller, so the leverage is not reuse.
- **Dependency category:** ports & adapters, with an in-memory range fetcher plus a tmp-dir `ParquetCache`.
- **Test impact:** the patched-SDK tests (the caching, heal and fail-soft blocks in `test_alpaca_provider.py`) become date-in/frames-out invariant tests.
- **Decide first.** The verifier's verdict is that extending the probe to the runner path entrenches a design ADR 0017 already rejected: raw bars canonical, adjusted views derived, never rewritten. Choose one:
  - **(a) Interim:** make the epoch check an invariant of every cached serve.
  - **(b) Full ADR 0017:** raw bars plus a derived adjusted view.
- **Sacred-adjacent:** yes. It feeds runner signals and the staleness gate.
- **Size:** decent for (a); large for (b).

### C7. Nothing owns "which explanation rows are operator evidence"

- **Cluster:**
  - `core/event_store.py`: `get_latest_bar_timestamp` and `count_paper_rejections`
  - `gui/_dashboard_scope.py`
  - `cli/commands/report.py`
  - `promotion/fault_injection.py`
- **Why coupled:**
  - **Three non-operator row classes:** backtest-linked rows, `backtest_fill`, and synthetic fault-injection rows.
  - **Five reads, five subsets:** each read excludes a different subset of those classes.
  - **Scattered literal:** the synthetic literal lives in 3+ places, because core can't import promotion.
- **Retires:** V11 and V12. It also fixes the report's whole-table loads: each `milodex report` loads 1.19M explanation rows.
- **Deletion test:** holds. 3 of the 5 variants already miss a class.
- **Dependency category:** local-substitutable (temp SQLite).
- **Test impact:** one boundary test seeds all four row classes and asserts that every operator read sees only the live rows. It replaces the per-surface tests; none of them test the synthetic exclusion today.
- **Precedent:** `core/trade_status.py` (P2-10).
- **Sacred-adjacent:** yes, as a readiness-gate input.
- **Size:** small, plus the tiny root fix at `fault_injection.py:248`.

### C8. "What stage is this strategy at" has no read-side owner

- **Cluster:**
  - **Bench clamp:** `gui/snapshot_builders.py:187-207` and `gui/query_helpers.py:58-75`.
  - **Desk ever-paper SQL:** `gui/strategy_bank_state.py:100-129` and `attention_state.py:365`.
  - **CLI:** `report.py:597-626`, whose docstring about runtime is false.
  - **Orchestrator:** the catch-up predicate.
  - **Execution:** 4 stage resolutions and 3–4 YAML reads per submit. The audit row records the YAML stage, not the evaluated one; this is prior #1/#2, now worse.
- **Why coupled:**
  - The runtime authority is the YAML stage plus the active manifest at that stage.
  - The ledger is never binding at runtime.
  - Each surface claims a different source, and none says which question it answers: claimed, recorded, or runnable.
- **Retires:** V16, the audit-stage mismatch, and the 7/22 QQQ/IWM confusion class.
- **Deletion test:** holds.
  - A pure resolver over (YAML stage, promotion rows, manifest rows) collapses ≥5 encodings.
  - It must take already-fetched rows so that GUI `mode=ro` isolation survives.
- **Dependency category:** local-substitutable (temp SQLite + temp YAML).
- **Test impact:** the hand-INSERT read-model tests become one owner matrix test (yaml × ledger × manifest) plus thin projection tests.
- **Constraints:**
  - First, name the runtime authority honestly.
  - Making the ledger binding at runtime is a risk-layer change that needs founder review, not a refactor.
  - Execution side:
    - read the file once per submit (parse and hash the same bytes);
    - resolve the governing stage once;
    - record the evaluated stage.
- **Sacred-adjacent:** yes.
- **Size:** decent.

### C9. The official research-evidence protocol lives in the CLI

- **Cluster:**
  - `cli/commands/research.py`: `_screen`, `_evidence` and `_match_random`.
  - `research/snapshot.py` and `research/evidence_assembler.py`.
  - Walk-forward run metadata, decoded by 7 readers with divergent missing-key semantics (prior #11, now worse).
  - The cell roster, encoded in two modules plus a test.
- **Why coupled:**
  - The D-5 sequence is hand-sequenced three times: preflight → offline context → scratch store → roster → common OOS boundaries → stamp → verify.
  - The writer checks only the hash, and accepts a `snapshot_id` on its own.
  - `_screen` and `_match_random` stamp runs before the post-run verify, with no rollback.
- **Retires:**
  - V17.
  - The stamp-before-verify ordering.
  - Override promotion on a non-completed walk-forward run, which records a fabricated DD of 0.0. This is shared with C2.
- **Deletion test:** holds. Delete the three handlers and the protocol vanishes, because the `research/` module exposes only primitives.
- **Dependency category:** local-substitutable: `SnapshotDataProvider` over a tmp snapshot, a tmp EventStore, and an injected `today`.
- **Test impact:**
  - No CLI test mentions "snapshot" today, and the guard strings are untested. These become boundary tests at the research owner.
  - A typed run-metadata decoder beside the writer, with strict and lenient modes, replaces the 14-field JSON↔DB mirror check.
- **Open question:** under #391 the official ledger is a scratch DB, while the roadmap counts M3 against `data/milodex.db`. The two are unreconciled.
- **Sacred:** no. It is gate-adjacent through the shared decoder.
- **Size:** decent.

### C10. The GUI duplicates runner-status assembly

- **Cluster:**
  - Domain: `strategies/runner_status.py` (`collect_runner_statuses`).
  - GUI copies: `gui/active_ops_state.py`, `gui/query_helpers.py` and `gui/attention_state.py`.
- **Why coupled:** the domain collector can't be used from the GUI read models.
  - It takes an `EventStore`, whose constructor migrates.
  - It YAML-loads every config for each strategy.
  - So the GUI re-assembles status itself, with three different latest-session semantics and a copied cadence table.
- **Retires:**
  - The GUI cannot tell a wedged stop from a moot one. LIVE.
  - `milodex strategy status` says daily runners are "idle while the market is open by design". That has been false since ADR 0057. LIVE, and pinned by `test_runner_status.py:305-317`.
- **Dependency category:** local-substitutable.
- **Test impact:** the GUI heartbeat and phantom tests fold into the runner_status tests.
- **Sacred-adjacent:** yes (liveness and stop semantics).
- **Size:** small.

---

## Status of the 2026-06-12 findings

| # | Status | Note |
|---|---|---|
| 1/2 `effective_stage` | worse | 4 code copies: evaluator ×2 (`or`), service ×2 (`is not None`, one added 2026-06-13). The audit row records a fifth value. The divergence is still unreachable because the loader validates stage. → C8 |
| 3 daily-loss / staleness | partial | Staleness landed (shared `risk/staleness.py`). Daily-loss math is unchanged and parity-pinned. |
| 4 hash recipe | better | The promotion side collapsed onto `manifest.hash_canonical`. The loader's runtime recipe is parity-pinned. |
| 5/6 universe scan | same | 4 loops: loader ×2, engine, run_manifest. |
| 7 config resolver | landed | #248 |
| 8 workflow readiness | same | `bench.py:291-520` |
| 9/10 freshness verdict | downgraded → none | The no-data policy difference is correct per surface: the gate fails closed and the display shows unknown. `_aware` is a general idiom. |
| 11 run-metadata decode | worse | 7 decoders (snapshot and lifecycle added). → C9 |
| 12 daily returns | same | New: per-window metrics exclude day 0 (display only). The stitched OOS curve repeats its first date. |
| 13 GUI gate verdict | worse | Now live (V1). → C2 |
| 14 GUI read layer | same | |
| 15 cache plan welded | worse | 5 more policies welded in since June. → C6 |
| 16 cache version | same | |
| 17 intraday seam | partial | RSI consolidated (ec44a86). The session skeleton grew from 4 to 7 strategies plus 3 benches. → C1 |
| 18 replay branch / 19 begin-run | same | |
| 20 structural subset | same | Any fix must extend G1. → C3 |
| 23 broker classification | worse | Adds the 5xx class and a second classifier (`_read_call`). → C5 |
| 24/25 interface docs | same | |
| 26 kill-switch store placement | same | `risk/profile_activation.py:13` still runtime-imports execution (ADR 0019 breach). Profile activation is operator workflow, not enforcement, so it belongs in `commands/` and should use runner liveness rather than raw `ended_at IS NULL`. |
| 27/28 QML token tests | better (partial) | A behavioural harness exists, but `test_qml_load_smoke.py` still holds 39 source pins. |

## Dismissed this round

Each of these was explored and survived the deletion test or the self-refutation pass:

- **Splitting `execution/service.py`.** It only relocates code and deletes nothing.
- **Three RiskDecisions built in execution.** They can only block, so they cannot weaken the veto.
- **Per-intent re-evaluation at drain.** No outcome change was found; performance impact is minor.
- **Readiness blind spot in the research lane.** The official snapshot path already blocks it.
- **Exit_reason vocabulary read by 4 classifiers.** They answer different questions.
- **Engine-factory recipe ×3.** It belongs to the CommandContext cluster.
- **`feed_label` dead knob.**
- **Walk-forward framing split.**
- **`_peek_runner_lock`.** It is a twin of the liveness helper, not a divergent copy.
- **Kill-switch "active" predicate.** Bench matches the owner. Only `report.py` diverges, which is a display issue and latent.
- **`paper_runner_control.py:45-95`.** These helpers have no src callers, and ADR 0026:144's claim about them is false. That is janitor work, not deepening.

## Navigability

- **CONTEXT.md predates the research lane.**
  - **Missing terms:**
    - snapshot, preflight, cell roster, null-baseline kinds;
    - screen / BatchRow / screen JSON;
    - experiment registry and `terminal_status`;
    - advisory verdict, decisive-loss predicate;
    - IEX-exploratory vs durable;
    - frozen session schedule, scratch DB, fan-out;
    - queued intent, lifecycle criteria, operator override, synthetic fault injection.
  - **Terms now overloaded:**
    - **"manifest":** the promotion manifest, the snapshot's `manifest.json`, the run manifest, and universe manifests;
    - **"coverage":** the preflight fraction, the readiness percent, and prefetch coverage;
    - **"snapshot":** a research snapshot vs ADR 0053 equity snapshots;
    - **"evidence":** promotion evidence vs research evidence.
- **CLAUDE.md** says "Fourteen modules", but `src/milodex/` has 15 packages; `runner/` is the extra one.
- **ADR 0051 Phase D3** says the GUI omits lifecycle-exempt promotion. That is stale: the bridge passes `lifecycle_exempt_eligible`.
- **RISK_POLICY.md** has a stale exemption list (see C3).

## Operational observation (not architecture)

**What is happening.** Since 2026-09-28, three intraday paper runners have been running: `rsi2.iwm`, `rsi2.qqq` and `vwap_trend.spy`. Every entry since 9/29 has been vetoed with `max_total_exposure_exceeded`. **The soak is producing no intraday trades.**

**Why** (per a read-only DB query): the exposure comes from July positions held by daily strategies. Their queued exits expired unexecuted: queued_intents 67–69 (DIA/QQQ) expired on 2026-08-03. The daily fleet last ran on 2026-07-27.
