# Risk Policy

Companion to `docs/SRS.md` Domain 2 (Execution / Risk) and Domain 4 (Strategy Engine). SRS encodes *what risk checks must exist and how they behave*; this document fixes the **default numeric values**, the **scope of kill-switch behavior**, and the **policy distinctions** (hard stop vs warning, increasing vs reducing exposure) that the checks implement. The authoritative machine-readable source is `configs/risk_defaults.yaml` — this document is the human-readable rationale it should match.

The founder's intent (see `docs/FOUNDER_INTENT.md`) is that Milodex feel disciplined rather than aggressive, and understandable to a less financially literate user. Every default below is chosen for clarity and restraint over sophistication.

---

## Phase 1 Paper Evaluation Baseline

- **Starting paper capital:** **$100,000**. This is the default reference baseline for Phase 1 evaluation. It is large enough to make position sizing, exposure, and portfolio behavior easy to reason about, and it matches a common paper-trading convention. It is deliberately not tied to the founder's personal account size — the goal in Phase 1 is a clean, understandable evaluation environment. Alternate paper-capital profiles may be supported later.

---

## Sizing and Exposure Defaults

All percentages are of **current account equity** unless explicitly stated as start-of-day equity.

| Parameter | Phase 1 default | Rationale |
|---|---|---|
| Per-position target size | **10% of equity** | Simple fixed-percent sizing. One-sentence explainable. |
| Max single-position exposure | **10% of equity** | Matches sizing target; prevents any one name from dominating. |
| Max total portfolio exposure | **50% of equity** | Milodex never deploys more than half the portfolio into active positions under default rules. The remainder stays unallocated. |
| Max single-sector exposure | **20% of equity** | Prevents obvious over-concentration in one sector. |
| Max correlated positions per trade idea | **2** | Blocks stacking near-identical exposures (e.g., multiple semis expressing the same view) even when the sector cap is not yet breached. |

**Sizing is simple fixed-percent** for Phase 1, not volatility-aware. Clarity, explainability, and low implementation ambiguity win over sophistication. Volatility-aware sizing may be explored later; it is not a Phase 1 requirement.

Sizing and exposure rules apply to **exposure-increasing** orders. Orders that reduce or close existing exposure are governed by the more permissive policy in "Reducing vs Increasing Exposure" below.

---

## Daily Loss Logic

Daily loss is computed as **realized + unrealized P&L** measured against the **start-of-day equity snapshot**. Using only realized P&L would ignore open-position risk; using only point-in-time equity without a defined reference becomes ambiguous. The explicit rule is:

> `daily_loss = (current_realized_pnl + current_unrealized_pnl) - start_of_day_equity_snapshot`

Compared against the configured daily loss cap (default: 3% of start-of-day equity, see `configs/risk_defaults.yaml`). Breach is a kill-switch trigger, not a warning.

---

## Kill-Switch Triggers

The kill switch trips on **any** of the following conditions. The list is deliberately broader than drawdown alone — the switch exists to stop the system whenever **trust in execution or state becomes questionable**, not only when P&L deteriorates.

- daily loss cap breach (per the formula above)
- portfolio drawdown threshold breach
- repeated order submission failures
- repeated unexplained mismatches between broker state and local state
- repeated data-quality failures that affect tradability decisions
- stale or unverifiable market data at submit time
- duplicate-order detection failure (the system cannot confidently determine duplicate status)
- execution behavior materially diverging from allowed assumptions
- account-level exposure limit breached or attempted
- strategy config fingerprint mismatch at runtime (per R-STR-012)
- repeated runtime exceptions in critical trading paths
- broker connectivity unavailable after configured retry count
- manual operator-triggered emergency stop (via the SIGINT shutdown dialog, per R-STR-008)

---

## Kill-Switch Scope: Strategy-Level vs Account-Level

Milodex supports **both** strategy-level and account-level kill switches.

- **Strategy-level kill switch** isolates problems to a single strategy when the issue is bounded to that strategy's signals, execution, or state. One failing strategy should not unnecessarily shut down everything.
- **Account-level kill switch** halts all trading when the condition threatens the integrity or safety of the entire system (e.g., broker-state mismatch, connectivity loss, account-exposure breach, operator emergency stop).

When in doubt, escalate to the account-level switch. Any hard stop listed below that applies globally (e.g., broker connectivity) trips the account-level switch; any hard stop tied to one strategy's behavior (e.g., that strategy's config fingerprint mismatch, that strategy's repeated rejections) trips the strategy-level switch for that instance.

---

## Position Cap Scope: Two Orthogonal Layers

Milodex enforces position caps at **two independent layers** that run together. Both must pass for an intent to proceed; either failure blocks the trade with its own reason code.

**Account-scoped (the floor — [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md), unchanged).** `max_concurrent_positions` in `configs/risk_defaults.yaml` is the account-wide ceiling. The risk evaluator counts every open broker position regardless of which strategy proposed it, and refuses any intent that would push the projected open count above this value. Reason code: `max_concurrent_positions_exceeded`.

**Per-strategy (the strategy's own ceiling — [ADR 0029](adr/0029-per-strategy-position-attribution-at-risk-layer.md), as of 2026-05-06).** `risk.max_positions` in a strategy's YAML, when set, is **binding** for that strategy's own positions. The risk evaluator reconstructs attribution from the durable `trades` history (filtering to `status="submitted"` rows only), counts positions attributed to the proposing strategy, and refuses an intent that would push that strategy's projected count above its declared cap. Reason code: `max_strategy_positions_exceeded`. Absence of `risk.max_positions` leaves the per-strategy check skipped — the account-scoped floor still applies. Operator-placed positions (whose recorded `strategy_name` is NULL) are attributed to the reserved pseudo-strategy `"operator"` and count toward the account-scoped cap but not toward any runner-strategy's per-strategy cap.

Practical consequences for operators:

- **Single-strategy operation:** size `max_concurrent_positions` to that strategy's expected ceiling. The default `10` accommodates either Phase 1 strategy with headroom. Setting `risk.max_positions` in the strategy YAML adds a per-strategy ceiling on top.
- **Multi-strategy operation in one paper account:** size `max_concurrent_positions` ≥ the **sum** of strategies' expected concurrent positions (the account-scoped floor still has to permit the combined load). Each strategy's `risk.max_positions` then constrains only its own attributed positions. Under ADR 0029, regime can submit BUY SPY against an account that already holds meanrev's three positions because the per-strategy cap counts only regime-attributed positions (zero) — the account-scoped cap remains the only check capable of blocking that specific intent. Underprovisioning the account-scoped cap still produces `max_concurrent_positions_exceeded` (the 2026-05-04 incident — see [ADR 0024](adr/0024-account-scoped-position-caps-are-authoritative.md)).
- **Strategy YAML `risk.max_positions`** is now binding for runner-attributed positions of that strategy (ADR 0029 supersedes ADR 0024's "informational only" interpretation of this specific field). The account-scoped floor in `configs/risk_defaults.yaml` is unchanged.

Attribution is reconstructed on demand per evaluation; no parallel `position_attribution` table is maintained. The `trades` table's `strategy_name` column on the most recent zero → non-zero submitted opening fill is the source of truth.

---

## What Happens When a Kill Switch Triggers

A kill switch creates a **reviewable incident state**, not just a temporary pause flag. When triggered, the system must:

1. Immediately block any new exposure-increasing orders.
2. Preserve and log the exact triggering condition.
3. Snapshot relevant local state, broker state, and strategy state.
4. Mark affected strategies (or the full account) as halted in durable state.
5. Surface a clear operator-facing incident summary.
6. Require explicit review before any re-enable action (no auto-resume — per R-EXE-006).
7. Create a governance event linking the halt to any later reversal or restart (per the promotion-log append-only pattern in `docs/PROMOTION_GOVERNANCE.md`).
8. Continue allowing safe read-only inspection and reporting.
9. Continue allowing **exposure-reducing** actions if policy permits (see next section).

---

## Reducing vs Increasing Exposure

Milodex treats order direction asymmetrically by its **effect on risk**, not by its side alone.

- **Exposure-increasing orders** (new longs in Phase 1, adds to existing longs) pass the **full** set of risk, data, approval, and kill-switch checks. Any hard stop blocks them.
- **Exposure-reducing orders** (sells that close or shrink an existing long, in Phase 1's long-only model) pass a **more permissive** policy, because they generally move the portfolio toward safety.

**Sell orders during an active kill switch:** ordinary exposure-reducing sells are still allowed, because they make the system safer. Any sell whose net effect would increase risk via more complex side effects (e.g., hypothetical short-side expansion in a future non-long-only phase) is blocked.

The guiding principle: a kill switch stops new risk from being added. It does not trap the system in existing risk.

---

## Duplicate-Order Policy

Milodex enforces a **strict no-duplicate-order** policy. An order is a duplicate and **blocked** if it would create materially the same exposure change for the same strategy while an equivalent order is already pending, recently submitted, or not yet reconciled.

Duplicate detection keys off, at minimum:

- strategy instance
- symbol
- side
- intended action type
- target quantity or target exposure
- execution window / submission cycle

If the system cannot confidently determine whether an order is a duplicate, it must **block and require review** rather than submit twice. Uncertainty is itself a hard stop (see below).

---

## Hard Stops vs Warnings

The rule is simple: **if the condition makes execution untrustworthy or policy-invalid, it is a hard stop. If it is informative but still safe to proceed, it is a warning.**

**Absolute hard stops** (block submission, no override without explicit operator action):

- kill switch active
- stale or unverifiable submit-time data
- broker connectivity unavailable at submit time
- config fingerprint mismatch
- duplicate-order uncertainty
- max single-position exposure breach
- max portfolio exposure breach
- sector / correlation cap breach
- missing required approval for a gated action
- critical reconciliation mismatch between local and broker state

**Warnings** (logged in the explanation record per R-XC-008, do not block):

- lower-confidence data anomalies that do not invalidate the run
- unusual but not forbidden volatility conditions
- strategy underperformance that has not yet crossed demotion thresholds
- elevated slippage relative to expectations
- non-critical paper-vs-live divergence
- increased operator-review burden

Warnings accumulate and may themselves become a kill-switch trigger if they repeat (e.g., repeated data-quality warnings → data-quality hard stop).

---

## Known Backtest Limitations and Biases

Backtest results in Milodex are produced by an engine that is itself well-tested (T+1 fill timing, universe coverage assertion, tiered slippage, split- and dividend-adjusted bars, walk-forward OOS aggregation). The engine is not the credibility-limiting factor. **The data is.** Two known biases distort backtest numbers in ways the engine cannot correct on its own:

### Survivorship bias (universe selection)

Several universe manifests in `configs/` declare their members as a present-day ticker list and apply that list retroactively to historical evaluation windows. This silently:

- **Excludes** names that were in the universe in 2020 but are no longer there in 2026 (delistings, demotions, corporate restructurings).
- **Includes** names that were not in the universe in 2020 but are now (recent additions, IPOs that grew into large-cap status).

The bias is asymmetric: the names that disappear are disproportionately the ones whose price declined catastrophically; the names that appear are disproportionately the ones whose price appreciated. Backtests on hindsight-selected stock universes are therefore **systematically optimistic** — Sharpe ratios are inflated by an unknown but typically meaningful amount (rule of thumb: 0.2–0.4 for a 5-year curated-large-cap window).

Each universe manifest carries a `survivorship_corrected: bool` field declaring its status. ETF-only universes with stable constituents are immune (`survivorship_corrected: true`); stock universes built from current ticker lists are not (`survivorship_corrected: false`). The `milodex research screen` output reports this per-strategy as the `surv_corr` column so the operator knows which Sharpes are credibility-corrected and which are not.

**Affected universes (Phase 1):**

| Universe | Status | Reason |
| --- | --- | --- |
| `universe.spy_only.v1` | corrected | Single ETF (SPY 1993–) |
| `universe.index_etfs.v1` | corrected | 4 broad-market ETFs, all stable since pre-2020 |
| `universe.gem_quartet.v1` | corrected | 4 GEM ETFs, all stable since pre-2020 |
| `universe.sector_etfs_spdr.v1` | corrected | 11 SPDR sector ETFs, family stable since 1998 (XLRE 2015, XLC 2018) |
| `universe.curated_largecap.v2` | **corrected** | 20 ETFs (immune) + 22 large-cap single-names selected ex-ante from S&P 100 at 2019-12-31, market cap ≥ $100B |
| `universe.phase1.curated.v1` | not corrected (deprecated) | Replaced by `universe.curated_largecap.v2`. Retained for historical-backtest reproducibility of frozen v1 strategy manifests; do not use for new strategies. |
| `universe.sp100_liquid.v1` | **not corrected** | 99 single-name stocks; ~20–30 constituent changes 2020–2024 |

**Affected research-target strategies after migration:**

| Strategy | Universe | Survivorship status |
| --- | --- | --- |
| `momentum.daily.tsmom.curated_largecap.v1` | `curated_largecap.v2` | **corrected** |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | `curated_largecap.v2` | **corrected** (demoted to backtest pending re-promotion on v2 evidence) |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | `curated_largecap.v2` | **corrected** |
| `breakout.daily.nr7_inside.liquid_largecap.v1` | `sp100_liquid.v1` | not corrected |
| `momentum.daily.52w_high_proximity.largecap.v1` | `sp100_liquid.v1` | not corrected |

**Lifecycle-proof strategy is not materially affected.** `regime.daily.sma200_rotation.spy_shy.v1` rotates between SPY (1993–) and SHY (2002–), both of which have traded continuously throughout every Phase 1 evaluation window. The screen output reports `surv_corr=no` for regime because the strategy declares its universe inline rather than via a manifest — the disclosure mechanism only reads from manifest YAMLs, and an inline universe has no place to declare its status. This is a known cosmetic gap; the underlying universe is survivorship-immune.

**Strategies with inline universes default to `surv_corr=no`.** The `survivorship_corrected` flag lives on universe manifests, not strategies. A strategy that inlines its universe (rather than declaring `universe_ref:` and pointing at a manifest) cannot declare survivorship-correction status. The default false is the correct conservative answer in absence of an authoritative declaration. Migrating an inline universe to a manifest is the path to opt in.

**Methodology for the curated_largecap.v2 fix:** ex-ante selection rather than point-in-time membership tracking. The 2019-12-31 S&P 100 constituent list is treated as the membership truth: any name in the index on that date with market cap ≥ $100B is eligible; nothing else is. The cutoff date pre-dates every Phase 1 evaluation window, so 2020-2024 information cannot have influenced the selection. Names whose ticker changed mid-window (META = FB until 2022-06-09) are excluded to avoid requiring ticker-aliasing infrastructure. This is the right shape of fix for hand-curated universes; **point-in-time membership tracking** (with `(symbol, valid_from, valid_to)` tuples) is the right shape for index-derived universes like `sp100_liquid` where constituents legitimately change over time.

**Remaining planned fix:** point-in-time membership reconstruction for `sp100_liquid` (99 stocks, ~20-30 constituent changes 2020-2024). This requires both the per-date membership data and ticker-aliasing infrastructure for mid-window ticker changes. Out of scope for the current PR; tracked as future Phase 1.5 hardening.

**Operational mitigation:** promotion is stage-aware. The paper-readiness gate is intentionally lighter because it only spends a paper-trading slot, while capital-stage gates remain strict (Sharpe > 0.5, max DD < 15%, and the strategy's configured trade-count floor). Strategies that barely clear a strict gate on biased data are likely below 0.0 in real expectation, so paper validation remains the buffer before capital is exposed. The bias hurts research velocity (false-positive strategies waste paper-trading slots) more than it hurts capital safety.

### Date-range truncation (provider history limit)

A subtler bias: Alpaca's free IEX feed can silently truncate requests for dates beyond the rolling history window. A request for `2020-01-01` may return bars starting months later with no provider error. `milodex data fetch-universe` now reports both symbol coverage and date-range coverage so a cache cannot look complete merely because every requested *symbol* returned some data.

Date-range diagnostics record first/last returned bar dates per symbol and warn when a symbol starts more than 7 calendar days after the requested `--start` or ends more than 7 calendar days before the requested `--end`. JSON output includes `requested_start`, `requested_end`, `date_range_warnings`, `symbols_with_full_date_range`, and `date_range_coverage_pct`; human output prints a compact warning list capped at 10 symbols.

This is warning-only. It diagnoses fetch completeness without changing provider cache semantics or backtest execution. A future hard-error mode can be added if the warning signal proves useful and not noisy.

---

## Relationship to SRS and Config

- `configs/risk_defaults.yaml` is the machine-readable source of truth for every numeric default above. If this document and the config disagree, **the config wins** and this document should be updated.
- `R-EXE-004` enumerates the risk-check set that enforces these rules.
- `R-EXE-008` requires all thresholds to be sourced from `risk_defaults.yaml` (no hardcoded values in code).
- `R-EXE-009` defines duplicate detection; this document fixes its key set and "block on uncertainty" policy.
- `R-EXE-010` defines the kill-switch trigger set; this document is the authoritative enumeration.
- `R-EXE-014` through `R-EXE-017` (new) encode the scope split, the post-trip requirements, the reducing-vs-increasing asymmetry, and the hard-stop vs warning classification.
