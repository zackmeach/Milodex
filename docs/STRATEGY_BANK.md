# Strategy Bank — Canonical Status

## What can I run today?

Six statistically- or lifecycle-justified strategies are at paper stage and are the deserving runnable list:

- `regime.daily.sma200_rotation.spy_shy.v1`
- `breakout.daily.atr_channel.sector_etfs.v1`
- `meanrev.daily.bbands_lowerband.curated_largecap.v1`
- `meanrev.daily.pullback_rsi2.curated_largecap.v1`
- `momentum.daily.tsmom.curated_largecap.v1`
- `breakout.daily.donchian_20_10.sector_etfs.v1`

> ⚠️ **Two additional strategies are at paper stage in the event store but are FLAGGED for operator review — they should arguably not be running:**
> - `breakout.orb.intraday.spy.v1` — promoted to paper 2026-05-28 via `lifecycle_exempt`, OOS Sharpe **−1.06**.
> - `benchmark.unconditional_intraday_long.spy.v1` — promoted to paper 2026-05-28 via `lifecycle_exempt`, OOS Sharpe **−1.69**.
>
> Both promotions contradict this bank's prior verdict ("Do not promote") and the `lifecycle_exempt` mechanism is documented as being for the *regime* strategy, not negative-Sharpe edge/benchmark candidates. The benchmark is explicitly *not a promotion candidate*. See the **Flagged paper promotions** callout below. This document reports the DB truth; the disposition (likely demotion) is an operator decision.

Three strategies are at the **idle** stage (demoted out of active rotation 2026-05-19). Three remain genuinely at **backtest** stage (never promoted) and are blocked — see the blocked table and the new Idle section below.

---

## As of date and source of truth

This document reflects the event-store state as of 2026-05-28 (branch `fix/promotion-ordering-and-bank-refresh`).

Changes since the 2026-05-20 (`feat/intraday-orb-spy-v1`) update:
- **ORB and the intraday benchmark were promoted to paper on 2026-05-28 via `lifecycle_exempt`** (promotions ids 25 and 24) despite negative OOS Sharpe. Flagged for operator review — see callout. Both were re-run on 2026-05-27 (ORB evid `6a556eec` Sharpe −1.06; benchmark evid `ab6b88d7` Sharpe −1.69), figures that differ from the 2026-05-20 blocked-table numbers because those were earlier runs.
- **Three strategies were demoted to the new `idle` stage on 2026-05-19** ("Return to Idle via Bench GUI"): `momentum.daily.52w_high_proximity.largecap.v1`, `momentum.daily.xsec_rotation.sector_etfs.v1`, `seasonality.daily.turn_of_month.spy.v1`. They are no longer in the blocked-at-backtest table.
- `breakout.daily.atr_channel.sector_etfs.v1` and `breakout.daily.donchian_20_10.sector_etfs.v1` went through idle→backtest→paper recycles on 2026-05-19 (promotions ids 17–21); both are back at paper with their original evidence runs and metrics unchanged.
- `regime.daily.sma200_rotation.spy_shy.v1` was re-promoted to paper on 2026-05-15 (lifecycle_exempt, promotion id 12); its promotion-record evidence run is now `0733d4d1` (which carries no WF stats — a lifecycle-exempt regime can't accumulate gate-able trades). The last full WF re-baseline remains `f7e0730c` (Sharpe 1.19 / MaxDD 0.95 / 27 trades).
- `momentum.daily.dual_absolute.gem_weekly.v1` was re-run (run `f8588224`): MaxDD 17.88→**15.80**, trade count still 20 — gate verdict unchanged (`[D][N]`).

Roster now: **6 deserving paper + 2 flagged paper = 8 at paper stage**, 3 at idle, 3 genuinely blocked at backtest.

The authoritative data source is `data/milodex.db`. The tables that drive this document are `promotions` and `backtest_runs`. The promotion records are the binding source for stage; backtest run metadata is the source for all Sharpe, drawdown, and trade-count figures.

Statistical paper-stage entry reflects the **paper-readiness tier** (permissive gate: Sharpe > 0.0, max DD < 25%, configured trade floor), not the stricter capital-readiness tier required to advance beyond paper; authoritative gate definitions are in `src/milodex/promotion/policy.py` / ADR 0052. Note that a `lifecycle_exempt` promotion **bypasses this gate entirely** — which is how the two negative-Sharpe flagged strategies reached paper (see Flagged paper promotions).

### How to refresh

Run the three queries below against `data/milodex.db` to regenerate the tables in this document (paper, blocked-at-backtest, idle).

**Paper-stage strategies (the runnable list):**

```sql
SELECT p.strategy_id,
       p.recorded_at            AS promoted_at,
       p.backtest_run_id        AS evidence_run_id,
       p.promotion_type,
       p.sharpe_ratio,
       p.max_drawdown_pct,
       p.trade_count
FROM promotions p
INNER JOIN (
    SELECT strategy_id, MAX(id) AS max_id
    FROM promotions
    WHERE to_stage = 'paper'
    GROUP BY strategy_id
) latest ON p.strategy_id = latest.strategy_id AND p.id = latest.max_id
WHERE p.to_stage = 'paper'
ORDER BY p.recorded_at;
```

Note: the regime strategy row now returns `evidence_run_id = 0733d4d1-...` (the 2026-05-15 re-promotion id=12 carries this `backtest_run_id`), but that run holds no WF stats — a lifecycle-exempt regime can't accumulate gate-able trades. The walk-forward metrics for regime (Sharpe 1.19 / MaxDD 0.95 / 27 trades) are sourced from the last full re-baseline run `f7e0730c-fbdb-4c05-919d-622f8b61185d` in `backtest_runs`, not from the promotion record.

**Backtest-stage strategies (blocked):**

```sql
SELECT br.strategy_id,
       br.run_id,
       br.started_at,
       json_extract(br.metadata_json, '$.oos_aggregate.sharpe')         AS wf_sharpe,
       json_extract(br.metadata_json, '$.oos_aggregate.max_drawdown_pct') AS wf_max_dd,
       json_extract(br.metadata_json, '$.oos_aggregate.trade_count')    AS wf_trades
FROM backtest_runs br
INNER JOIN (
    SELECT strategy_id, MAX(id) AS max_id
    FROM backtest_runs
    WHERE status = 'completed'
    GROUP BY strategy_id
) latest ON br.strategy_id = latest.strategy_id AND br.id = latest.max_id
WHERE br.strategy_id NOT IN (
    SELECT strategy_id FROM promotions WHERE to_stage = 'paper'
)
AND br.status = 'completed'
ORDER BY br.strategy_id;
```

⚠️ **This blocked-stage query over-includes idle strategies** — it excludes only paper-promoted strategies, so any strategy whose latest event is `idle` still appears here. Cross-reference the idle query below and move idle strategies to the Idle section; only strategies with **no promotion event at all** are genuinely blocked-at-backtest.

**Idle-stage strategies (demoted from rotation):**

```sql
SELECT p.strategy_id, p.recorded_at, p.notes
FROM promotions p
INNER JOIN (
    SELECT strategy_id, MAX(recorded_at) AS mx
    FROM promotions
    GROUP BY strategy_id
) latest ON p.strategy_id = latest.strategy_id AND p.recorded_at = latest.mx
WHERE p.to_stage = 'idle'
ORDER BY p.strategy_id;
```

To verify any metric from a run ID directly:

```bash
python -m milodex.cli.main analytics metrics <run_id>
```

---

## Paper-stage strategies — the deserving list

Walk-forward evidence sourced from `docs/reviews/screen_2026-05-07.md`. Metrics are OOS-aggregate across 4 walk-forward windows, 2020-01-01 to 2024-12-31 canonical range. All promotion records are in `data/milodex.db` / `promotions` table.

**Deserving paper roster (statistical + lifecycle-proof regime):**

| strategy_id | promoted_at | evidence run_id | WF Sharpe | WF MaxDD% | WF Trades | promotion_type |
|---|---|---|---|---|---|---|
| `regime.daily.sma200_rotation.spy_shy.v1` | 2026-05-15 | `f7e0730c-fbdb-4c05-919d-622f8b61185d` * | 1.19 | 0.95 | 27 | lifecycle_exempt |
| `breakout.daily.atr_channel.sector_etfs.v1` | 2026-05-19 | `294d404a-43b5-4d01-9af2-321cea66366f` | 0.64 | 4.30 | 433 | statistical |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | 2026-05-07 | `4c91eada-34fc-4ffe-9347-ddb48b6568ea` | 0.52 | 3.38 | 361 | statistical |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | 2026-05-07 | `5210be26-5d60-4ad5-8834-7efc162cb391` | 0.73 | 3.98 | 776 | statistical |
| `momentum.daily.tsmom.curated_largecap.v1` | 2026-05-07 | `16636a03-509c-4816-a2cc-1e2214dffd7e` | 0.88 | 6.25 | 458 | statistical |
| `breakout.daily.donchian_20_10.sector_etfs.v1` | 2026-05-19 | `a6f59a53-0e5a-4811-8b78-1cf4bc82b787` | 0.87 | 7.59 | 435 | statistical |

\* The regime row's *promotion-record* evidence run is now `0733d4d1` (re-promotion 2026-05-15, id=12), which carries no WF stats — a lifecycle-exempt regime trades too infrequently to produce gate-able metrics. The metrics shown (1.19 / 0.95 / 27) are sourced from the last full walk-forward re-baseline `f7e0730c` (2026-05-07), which remains the authoritative WF evidence for this strategy. The original 2026-04-23 exemption (id=4) predated the backtest infra and carried no `backtest_run_id`.

**Flagged paper roster (lifecycle_exempt, negative Sharpe — pending operator review, see callout):**

| strategy_id | promoted_at | evidence run_id | WF Sharpe | WF MaxDD% | WF Trades | promotion_type |
|---|---|---|---|---|---|---|
| `breakout.orb.intraday.spy.v1` | 2026-05-28 | `6a556eec-1ed2-4cc7-a808-3473b8380e00` | **−1.06** | 2.58 | 856 | lifecycle_exempt |
| `benchmark.unconditional_intraday_long.spy.v1` | 2026-05-28 | `ab6b88d7-0e49-4482-847a-71865f542472` | **−1.69** | 7.59 | 1769 | lifecycle_exempt |

### What to watch during paper validation

**`regime.daily.sma200_rotation.spy_shy.v1`**
Lifecycle-proof strategy. Its job is to validate the pipeline end-to-end, not to generate alpha. The SMA-200 signal is binary and regime-dependent — extended sideways markets will produce few or no trades. Do not interpret low activity as a problem. Watch for execution errors, fill confirmation, and P&L attribution.

**`breakout.daily.atr_channel.sector_etfs.v1`**
Lowest Sharpe of the statistical promotions (0.64, run `294d404a`). Paper will reveal whether the edge persists in live market microstructure. Watch for slippage sensitivity — the ATR channel breakout fires on daily closes, so fill quality matters. No per-window instability flag. Runtime sizing is capped at 10% notional per position to match the global single-position and order-value guardrails.

**`meanrev.daily.bbands_lowerband.curated_largecap.v1`**
Sharpe 0.52 (run `4c91eada`) — narrowest margin above the 0.5 gate. The low drawdown (3.38%) is a positive sign for capital efficiency, but the margin above gate means the edge is fragile. Watch OOS Sharpe trajectory carefully before any promotion discussion.

**`meanrev.daily.pullback_rsi2.curated_largecap.v1`**
This strategy has a complex audit trail. Original promotion on 2026-04-22 was made on run `2ccea042` (Sharpe 1.02, pre-correction universe). A stage divergence followed. The 2026-05-07 paper promotion is on the corrected universe (run `5210be26`, Sharpe 0.73 — a 28% deflation from the original figure). The corrected figure is the honest baseline. See ADR 0032 and the audit section below. Paper performance should be benchmarked against the 0.73 figure, not 1.02.

**`momentum.daily.tsmom.curated_largecap.v1`**
Strongest statistical promotion (Sharpe 0.88, run `16636a03`). Clean drawdown (6.25%). No stability flags. Standard paper monitoring applies.

**`breakout.daily.donchian_20_10.sector_etfs.v1`**
Per-window Sharpe instability is a real concern here. The four windows produced: `[0.97, -0.17, 0.62, 2.22]` with std=0.99. One window was materially negative (2022 drawdown period). The OOS aggregate of 0.87 (run `a6f59a53`) passes the gate, but the variance across windows is the highest of any promoted strategy. The aggregate Sharpe is being lifted by the strong final window (2.22). This does not disqualify it — walk-forward methodology accounts for this by requiring the aggregate to gate — but it means paper validation should be watched for regime sensitivity more carefully than the headline Sharpe suggests. Additionally, the strategy's prior whole-period run suggested Sharpe 1.11 / 516 trades, which was in-sample noise. The walk-forward figure is the one that counts. Runtime sizing is capped at 10% notional per position to match the global single-position and order-value guardrails.

---

## ⚠️ Flagged paper promotions — pending operator review

On 2026-05-28 two intraday strategies were promoted to paper via `lifecycle_exempt` (promotions ids 24, 25):

| strategy_id | promo id | OOS Sharpe | promotion_type | why this is flagged |
|---|---|---|---|---|
| `breakout.orb.intraday.spy.v1` | 25 | −1.06 | lifecycle_exempt | Negative Sharpe; this bank's standing verdict is "Do not promote." Not a lifecycle-proof regime strategy. |
| `benchmark.unconditional_intraday_long.spy.v1` | 24 | −1.69 | lifecycle_exempt | Negative Sharpe; **explicitly not a promotion candidate** — it exists only as a comparison floor. |

**Why this is a concern:**
- The `lifecycle_exempt` mechanism is documented (ADR 0009/0020/0052, policy R-PRM-004) as being for the **regime strategy** — one that trades too infrequently by design to accumulate gate-able statistics. Neither ORB (856 trades) nor the benchmark (1769 trades) fits that justification; both have ample trades and simply fail the Sharpe gate.
- The benchmark is, by design, not a strategy to run — promoting it to paper has no defensible rationale.
- This is exactly the operator-override surface noted in CLAUDE.md: `--lifecycle-exempt` bypasses the statistical gate for *any* promotion. Used here, it placed two negative-Sharpe candidates at paper.

**Disposition:** This document reports the event-store truth (both are at paper). It does **not** rewrite the promotion decision. The likely correct action is `milodex promotion demote` for both back to backtest (ORB) / idle (benchmark), but that is an operator call. Until then, treat both as **not authorized to run** despite their paper stage.

---

## Idle-stage strategies — demoted from active rotation

Three strategies were demoted to the `idle` stage on 2026-05-19 ("Return to Idle via Bench GUI", promotions ids 14–16). `idle` is distinct from `backtest`: these strategies *were* in rotation and were deliberately parked, not blocked pre-promotion. Their latest completed backtest runs (below) remain on record.

| strategy_id | demoted_at | latest run WF Sharpe | WF MaxDD% | WF Trades |
|---|---|---|---|---|
| `momentum.daily.52w_high_proximity.largecap.v1` | 2026-05-19 | 0.16 | 16.44 | 769 |
| `momentum.daily.xsec_rotation.sector_etfs.v1` | 2026-05-19 | 0.22 | 17.83 | 390 |
| `seasonality.daily.turn_of_month.spy.v1` | 2026-05-19 | -0.27 | 11.59 | 40 |

All three would also fail the capital-readiness gate on their latest runs (see the prior blocked-table rationale, preserved in git history) — being parked at idle is consistent with that. Re-running or reworking any of them is a future decision.

---

## Backtest-stage strategies — blocked

Gate codes: `[S]` = Sharpe below the capital-readiness floor, `[D]` = MaxDD above the capital-readiness ceiling, `[N]` = trade count below the strategy's configured `backtest.min_trades_required` floor. Authoritative threshold values are in `src/milodex/promotion/policy.py` / ADR 0052.

Walk-forward methodology and canonical window 2020-01-01 to 2024-12-31 per ADR 0021 and ADR 0030.

Strategies with no promotion event — genuinely pre-promotion. (The three previously-listed `52w_high_proximity`, `xsec_rotation`, and `turn_of_month` rows moved to the Idle section above; ORB moved to the Flagged paper section above.)

| strategy_id | latest run_id | WF Sharpe | WF MaxDD% | WF Trades | gate verdict | what would need to change |
|---|---|---|---|---|---|---|
| `breakout.daily.nr7_inside.liquid_largecap.v1` | `01d22eda-a624-4456-92f9-3de20a9af892` | 0.19 | 13.43 | 930 | BLOCK `[S]` | Sharpe needs to reach > 0.5 OOS-aggregate. The signal is showing consistent per-window decay (window 0 Sharpe was -0.83). Would require signal redesign or universe change, not parameter tuning. |
| `meanrev.daily.ibs_lowclose.index_etfs.v1` | `ef7f6831-97d3-47bb-9c05-4df1b4b5ca89` | -0.12 | 4.89 | 404 | BLOCK `[S]` | Anti-edge: 3 of 4 windows produced negative Sharpe. This is not a marginal fail. Drawdown is low but the signal is working against the strategy OOS. Retire or fundamentally rework the entry logic. |
| `momentum.daily.dual_absolute.gem_weekly.v1` | `f8588224-624d-47a3-b440-5cf1734cf43b` | 0.74 | 15.80 | 20 | BLOCK `[D][N]` | See callout below. |

### Callout: `momentum.daily.dual_absolute.gem_weekly.v1` — structural gate tension

This strategy is different from the others. Its walk-forward Sharpe (0.74, run `f8588224`) would pass the Sharpe gate comfortably. It fails on two gates:

- **`[D]` MaxDD 15.80%** — fails the < 15% threshold (narrowly; the prior run `41777d12` was 17.88%, so a re-run tightened it but did not clear the gate).
- **`[N]` trade count 20** — fails this strategy's configured minimum of 30.

The trade-count failure is not a tuning problem. The strategy trades weekly. With 4 walk-forward windows of ~223 test days each, a weekly strategy can only accumulate roughly 4–8 trades per window, yielding approximately 16–32 OOS trades total. The current run produced 20. No parameter change resolves this without changing the strategy's fundamental frequency.

The MaxDD failure may be addressable with a tighter position-sizing or stop rule, but that risks changing the strategy's character.

**Current status: flagged, not retired.** The walk-forward Sharpe is real signal and this tension is a methodology question, not a strategy failure. The appropriate resolution is a governance discussion in Phase 5+ about how the 30-trade gate applies to sub-daily-frequency strategies — specifically whether a frequency-adjusted minimum (e.g., 30 * weekly/daily ratio) is the right standard. Until that question is resolved, the strategy stays at backtest stage.

Do not promote. Do not retire. Keep the run record in place.

### Callout: `breakout.orb.intraday.spy.v1` — first intraday candidate, null result (now flagged at paper)

> **Status correction (2026-05-28):** ORB is no longer at backtest — it was promoted to paper via `lifecycle_exempt` on 2026-05-28 alongside the benchmark. See the **Flagged paper promotions** section above. The analysis below remains the correct read of the signal; the "stays at backtest" verdict it originally drew has been overridden by an operator action this document flags as questionable.

This is the first strategy to use the intraday backtest engine ([Milodex#164](https://github.com/zackmeach/Milodex/pull/164)). The walk-forward result is a clean negative-Sharpe null exactly as the plan predicted. The harness is now proven to honestly evaluate intraday signals.

The figures below are the original 2026-05-20 run (ORB `1dc31aa7`, benchmark sibling). Both strategies were **re-run on 2026-05-27** (ORB `6a556eec` → Sharpe **−1.06**; benchmark `ab6b88d7` → Sharpe **−1.69**), the figures carried on their 2026-05-28 promotion records. Notably the re-run *reverses* the head-to-head — ORB now edges the benchmark on Sharpe (−1.06 vs −1.69) where the 05-20 run had it losing — but both remain deeply negative and neither clears any gate.

| metric (2026-05-20 run) | ORB | benchmark (unconditional intraday long SPY) |
|---|---|---|
| Trades | 790 | 1581 |
| Total return | -2.56% | -4.86% |
| Sharpe | -1.53 | -1.27 |
| Max drawdown | 2.61% | 5.14% |
| Positive windows | 0 / 4 | 0 / 4 |

ORB has *better* total return than the benchmark because lower trade frequency means less cumulative friction (5 bps slippage × ~790 fills vs ~1581 fills). But ORB's risk-adjusted Sharpe is *worse* than the benchmark in this run. The strategy is filtering OK in nominal terms (avoiding some bad trades) but the volatility cost of the breakout filter exceeds the return benefit. Both fail the capital-readiness Sharpe floor.

Promotion verdict (analytical): neither candidate meets the capital-readiness gate; on signal merit both belong at backtest. This verdict was **overridden operationally** on 2026-05-28 by a `lifecycle_exempt` paper promotion of both — an action this document flags for review (see Flagged paper promotions).

This is also the first place in the bank where the canonical walk-forward window diverges from the bank standard (2020–2024). The 2022–2025 window was chosen because Alpaca's free-tier intraday data depth past 2022 is unverified — the 2020–2021 portion would either fail to fetch or fail with partial-history data quality issues. The 4-year window still samples three regimes (2022 rate-shock bear, 2023 AI rally, 2024–2025 bull tape) and produces 800 OOS sessions worth of trades, well above the statistical minimum.

The benchmark sibling (`benchmark.unconditional_intraday_long.spy.v1`) is the comparison floor. It is intentionally trivial: buy at the post-opening-range bar, sell at the time-stop bar, every full session. It is not a promotion candidate; it exists so that any intraday signal can be measured against unconditional intraday long on the same universe and friction. ORB's losing this comparison settles the question for v1 of this signal.

**Honest framing matches expectations.** ORB on SPY is one of the most heavily competed-away intraday patterns (Crabel 1990 → 30+ years of public discussion), and post-2022 the 0DTE options boom has materially changed SPY intraday microstructure (dealer gamma hedging frequently fades opening-range breakouts). Finding a positive edge here was always implausible. The value of this PR is the harness — it lets the next intraday hypothesis ride on infrastructure that has been validated against a known-null signal.

On merit: do not promote, do not retire. The negative result is valuable evidence — future intraday candidates need to beat both the benchmark AND clear the Sharpe floor before any paper-stage discussion. (As of 2026-05-28 the event store nonetheless places ORB at paper via lifecycle exemption; see the flag above.)

---

## Methodology notes

Walk-forward validation splits the canonical evaluation window (2020-01-01 to 2024-12-31) into 4 approximately equal out-of-sample test segments, each preceded by a training segment of similar length. The "OOS aggregate" Sharpe and drawdown figures reported in this document are computed across all test segments concatenated — not from any single window. This is the correct figure for gate evaluation per ADR 0021.

Intraday strategies use a shifted window (2022-01-01 to 2025-12-31) because Alpaca's free-tier intraday data depth past 2022 is unverified. The methodology is otherwise identical: 4 OOS test segments, aggregate Sharpe across concatenated segments, same capital-readiness gates. The window shift is documented per-strategy in the blocked-table notes.

The walk-forward approach is required per ADR 0030, which establishes that backtest runs are exploratory and that whole-period (in-sample) results are inadmissible for promotion gating. The seasonality strategy in this bank is a concrete illustration of why: its whole-period Sharpe was +0.33 while its OOS aggregate was -0.27.

Gates (ADR 0009 / ADR 0020 / ADR 0052): authoritative capital-readiness threshold values (Sharpe, MaxDD, trade-count floor) are defined in `src/milodex/promotion/policy.py`. All three gates must pass simultaneously for a statistical promotion. The regime strategy (`sma200_rotation`) is exempt from these gates under policy R-PRM-004 (lifecycle-exempt promotion type), because a regime strategy that trades infrequently by design cannot accumulate enough OOS trades in a 5-year window for ordinary statistical gates. The exemption is explicit in the `promotions` table (`promotion_type = 'lifecycle_exempt'`).

---

## Audit and integrity notes

**pullback_rsi2 stage divergence and backfill (ADR 0032)**

`meanrev.daily.pullback_rsi2.curated_largecap.v1` was promoted to paper on 2026-04-22 (run `2ccea042`, Sharpe 1.02) and then to micro_live on the same date. Between 2026-04-22 and 2026-05-07 the strategy's YAML `stage:` field was direct-edited to `backtest` without invoking `milodex promotion demote`, leaving no demotion event in the `promotions` table. This gap was closed on 2026-05-07 via a synthetic `audit_backfill` event (promotions id=9, `approved_by = 'audit_backfill'`, `recorded_at = '2026-05-06T20:00:00+00:00'`). The original promotion used the pre-Phase-4 universe (pre-survivorship-correction, pre-dividend-adjustment); the corrected re-baseline on 2026-05-07 produced a walk-forward Sharpe of 0.73 — a 28% deflation from the original 1.02. The 2026-05-07 paper promotion supersedes the original. See ADR 0032 for full policy.

**Orphan backtest_runs rows (PR #47)**

Three `backtest_runs` rows from 2026-05-06 were left in status `running` after the backtest engine crashed mid-session. PR #47 added recovery logic at engine startup (`fix(backtest): orphan backtest_runs recovery at engine startup`) that resets orphaned `running` rows to `failed` on restart. The three orphan rows were reconciled as part of that PR's deployment. They do not appear in the latest-run queries above.

**Phase-1 stale artifact cleanup (PR #46)**

PR #46 (`chore: remove stale paper-runtime artifacts and harden .gitignore`) deleted leftover paper-runtime state files that predated the Phase-4 cleanup and hardened `.gitignore` to prevent future recurrence. No data loss — these were ephemeral runtime caches, not event-store records.

---

## Doc maintenance

Update this document whenever any of the following events occur:

- A strategy is promoted to paper (add a row to the deserving or flagged paper table; remove from blocked/idle if applicable)
- A strategy is demoted to `idle` (move to the Idle section) or to `backtest` (move to the blocked table), recording the reason
- A strategy is promoted via `lifecycle_exempt` without a lifecycle-proof justification (add to / clear from the Flagged paper promotions section)
- A walk-forward re-baseline changes the evidence run_id or metrics for a paper-stage strategy
- A blocked strategy receives a new backtest run with a materially different result
- A gate verdict changes for any reason
- The dual_absolute governance question, or a flagged paper promotion, is resolved

Note the three stage categories this document now tracks: **paper** (deserving + flagged), **idle** (demoted from rotation), and **backtest** (genuinely pre-promotion, no promotion event). `idle` and `backtest` are distinct — do not merge them.

Recommended workflow: run the SQL queries from the "How to refresh" section against `data/milodex.db` (plus the `to_stage='idle'` query for the Idle section), regenerate the tables from the output, update any notes that depend on metrics, then commit. Do not update the table numbers by hand — re-run the queries. When resolving "latest stage per strategy" outside the canned `to_stage`-filtered queries, order by `recorded_at` (not `id`): a backdated `audit_backfill` event sorts ahead of a later real promotion under `id` order (the `pullback_rsi2` case; see `EventStore.get_latest_promotion_for_strategy`).

The walk-forward screen artifact for the 2026-05-07 run batch is at `docs/reviews/screen_2026-05-07.md`.
