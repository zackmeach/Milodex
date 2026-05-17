# Strategy Bank — Canonical Status

## What can I run today?

Six strategies have graduated to paper testing and are authorized to run:

- `regime.daily.sma200_rotation.spy_shy.v1`
- `breakout.daily.atr_channel.sector_etfs.v1`
- `meanrev.daily.bbands_lowerband.curated_largecap.v1`
- `meanrev.daily.pullback_rsi2.curated_largecap.v1`
- `momentum.daily.tsmom.curated_largecap.v1`
- `breakout.daily.donchian_20_10.sector_etfs.v1`

Six strategies remain at backtest stage and are blocked from promotion. See the blocked table below for the reason each one failed and what would need to change.

---

## As of date and source of truth

This document reflects master at commit `7d5fd0c` (2026-05-16).

Stages and metrics are unchanged from the 2026-05-07 baseline (commit `8fe357c`): six strategies at paper, six blocked at backtest. The only event-store change since is a fresh `lifecycle_exempt` paper record for `regime.daily.sma200_rotation.spy_shy.v1` (`recorded_at = 2026-05-15T19:06:35Z`, from the phase-one paper-lifecycle work in PR #146). It re-affirms the existing regime paper status under policy R-PRM-004 — it does not change the strategy's stage, evidence run, or walk-forward metrics, which remain as listed in the paper-stage table below.

The authoritative data source is `data/milodex.db`. The tables that drive this document are `promotions` and `backtest_runs`. The promotion records are the binding source for stage; backtest run metadata is the source for all Sharpe, drawdown, and trade-count figures.

Paper-stage entry reflects the **paper-readiness tier** (permissive gate: Sharpe > 0.0, max DD < 25%, configured trade floor), not the stricter capital-readiness tier required to advance beyond paper; authoritative gate definitions are in `src/milodex/promotion/policy.py` / ADR 0052.

### How to refresh

Run both queries against `data/milodex.db` to regenerate the tables in this document.

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

Note: the regime strategy row returns `evidence_run_id = NULL` because it was promoted under a lifecycle exemption before the re-baseline run `f7e0730c-fbdb-4c05-919d-622f8b61185d` was recorded. The walk-forward metrics for regime are sourced from that run in `backtest_runs`, not from the promotion record.

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

To verify any metric from a run ID directly:

```bash
python -m milodex.cli.main analytics metrics <run_id>
```

---

## Paper-stage strategies — the deserving list

Walk-forward evidence sourced from `docs/reviews/screen_2026-05-07.md`. Metrics are OOS-aggregate across 4 walk-forward windows, 2020-01-01 to 2024-12-31 canonical range. All promotion records are in `data/milodex.db` / `promotions` table.

| strategy_id | promoted_at | evidence run_id | WF Sharpe | WF MaxDD% | WF Trades | promotion_type |
|---|---|---|---|---|---|---|
| `regime.daily.sma200_rotation.spy_shy.v1` | 2026-04-23 | `f7e0730c-fbdb-4c05-919d-622f8b61185d` * | 1.19 | 0.95 | 27 | lifecycle_exempt |
| `breakout.daily.atr_channel.sector_etfs.v1` | 2026-05-07 | `294d404a-43b5-4d01-9af2-321cea66366f` | 0.64 | 4.30 | 433 | statistical |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | 2026-05-07 | `4c91eada-34fc-4ffe-9347-ddb48b6568ea` | 0.52 | 3.38 | 361 | statistical |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | 2026-05-07 | `5210be26-5d60-4ad5-8834-7efc162cb391` | 0.73 | 3.98 | 776 | statistical |
| `momentum.daily.tsmom.curated_largecap.v1` | 2026-05-07 | `16636a03-509c-4816-a2cc-1e2214dffd7e` | 0.88 | 6.25 | 458 | statistical |
| `breakout.daily.donchian_20_10.sector_etfs.v1` | 2026-05-07 | `a6f59a53-0e5a-4811-8b78-1cf4bc82b787` | 0.87 | 7.59 | 435 | statistical |

\* `f7e0730c` is the walk-forward re-baseline run recorded 2026-05-07. The promotion event (id=4 in `promotions`) predates this run and carries no `backtest_run_id` because the exemption was granted before the backtest infra was in place. The re-baseline is the authoritative walk-forward evidence for this strategy.

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

## Backtest-stage strategies — blocked

Gate codes: `[S]` = Sharpe below the capital-readiness floor, `[D]` = MaxDD above the capital-readiness ceiling, `[N]` = trade count below the strategy's configured `backtest.min_trades_required` floor. Authoritative threshold values are in `src/milodex/promotion/policy.py` / ADR 0052.

Walk-forward methodology and canonical window 2020-01-01 to 2024-12-31 per ADR 0021 and ADR 0030.

| strategy_id | latest run_id | WF Sharpe | WF MaxDD% | WF Trades | gate verdict | what would need to change |
|---|---|---|---|---|---|---|
| `breakout.daily.nr7_inside.liquid_largecap.v1` | `01d22eda-a624-4456-92f9-3de20a9af892` | 0.19 | 13.43 | 930 | BLOCK `[S]` | Sharpe needs to reach > 0.5 OOS-aggregate. The signal is showing consistent per-window decay (window 0 Sharpe was -0.83). Would require signal redesign or universe change, not parameter tuning. |
| `meanrev.daily.ibs_lowclose.index_etfs.v1` | `ef7f6831-97d3-47bb-9c05-4df1b4b5ca89` | -0.12 | 4.89 | 404 | BLOCK `[S]` | Anti-edge: 3 of 4 windows produced negative Sharpe. This is not a marginal fail. Drawdown is low but the signal is working against the strategy OOS. Retire or fundamentally rework the entry logic. |
| `momentum.daily.52w_high_proximity.largecap.v1` | `92e18152-25b5-4d6e-bccf-f4bd6a4ef825` | 0.16 | 16.44 | 769 | BLOCK `[S][D]` | Sharpe needs > 0.5 and MaxDD needs to come under 15%. Window 1 produced Sharpe -1.51 and nearly all of the drawdown was concentrated there. Both gates fail independently; fixing one would not clear the other at current parameter settings. |
| `momentum.daily.xsec_rotation.sector_etfs.v1` | `afe46162-d3ad-4737-9420-ce3b69674c11` | 0.22 | 18.54 | 390 | BLOCK `[S][D]` | Sharpe 0.22 and MaxDD 18.54% both fail. 2 of 4 windows were negative (single-window-dependency flag set). Both gates fail independently. |
| `seasonality.daily.turn_of_month.spy.v1` | `89cbb47e-2eb7-4199-9ce4-681bbd224eb3` | -0.27 | 11.59 | 40 | BLOCK `[S]` | OOS Sharpe is negative (-0.27). Whole-period in-sample showed +0.33, making this a textbook overfitting case — the effect disappears OOS. Per-window Sharpe std is 1.40 (extreme regime sensitivity: 2 positive windows, 2 negative). No path to graduation without a structurally different signal. |
| `momentum.daily.dual_absolute.gem_weekly.v1` | `41777d12-bc1d-46aa-a256-ce9abc1a31dd` | 0.83 | 17.88 | 20 | BLOCK `[D][N]` | See callout below. |

### Callout: `momentum.daily.dual_absolute.gem_weekly.v1` — structural gate tension

This strategy is different from the others. Its walk-forward Sharpe (0.83, run `41777d12`) would pass the Sharpe gate comfortably. It fails on two gates:

- **`[D]` MaxDD 17.88%** — fails the < 15% threshold.
- **`[N]` trade count 20** — fails this strategy's configured minimum of 30.

The trade-count failure is not a tuning problem. The strategy trades weekly. With 4 walk-forward windows of ~223 test days each, a weekly strategy can only accumulate roughly 4–8 trades per window, yielding approximately 16–32 OOS trades total. The current run produced 20. No parameter change resolves this without changing the strategy's fundamental frequency.

The MaxDD failure may be addressable with a tighter position-sizing or stop rule, but that risks changing the strategy's character.

**Current status: flagged, not retired.** The walk-forward Sharpe is real signal and this tension is a methodology question, not a strategy failure. The appropriate resolution is a governance discussion in Phase 5+ about how the 30-trade gate applies to sub-daily-frequency strategies — specifically whether a frequency-adjusted minimum (e.g., 30 * weekly/daily ratio) is the right standard. Until that question is resolved, the strategy stays at backtest stage.

Do not promote. Do not retire. Keep the run record in place.

---

## Methodology notes

Walk-forward validation splits the canonical evaluation window (2020-01-01 to 2024-12-31) into 4 approximately equal out-of-sample test segments, each preceded by a training segment of similar length. The "OOS aggregate" Sharpe and drawdown figures reported in this document are computed across all test segments concatenated — not from any single window. This is the correct figure for gate evaluation per ADR 0021.

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

- A strategy is promoted to paper (add a row to the paper table, remove from blocked if applicable)
- A strategy is demoted from paper (move to blocked table, record the reason)
- A walk-forward re-baseline changes the evidence run_id or metrics for a paper-stage strategy
- A blocked strategy receives a new backtest run with a materially different result
- A gate verdict changes for any reason
- The dual_absolute governance question is resolved

Recommended workflow: run both SQL queries from the "How to refresh" section against `data/milodex.db`, regenerate both tables from the output, update any notes that depend on metrics, then commit. Do not update the table numbers by hand — re-run the queries.

The walk-forward screen artifact for the 2026-05-07 run batch is at `docs/reviews/screen_2026-05-07.md`.
