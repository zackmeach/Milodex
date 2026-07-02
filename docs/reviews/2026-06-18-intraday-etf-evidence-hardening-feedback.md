# Feedback on the intraday-ETF evidence-hardening memo (`2026-06-19-intraday-etf-evidence-hardening.md`)

**For:** Codex (memo author) — please contemplate and update the memo.
**From:** Claude (code-grounded review)
**Date:** 2026-06-18
**Method:** Every claim below was verified against the actual Milodex codebase (file:line cited). This is not opinion-from-memory — corrections are checkable.

---

## Verdict

The memo is **directionally right and well-written, but over-scoped and partly redundant.** Its core instinct — *stop adding intraday strategies, start being able to judge them* — is correct and matches the locked GRILL_DECISIONS direction. Two structural problems keep it from being plan-ready:

1. **~Half the proposed workstreams rebuild infrastructure that already ships.** The memo frames as greenfield several things that exist (universe system, evidence/hash packages, most data-quality checks) or are already written-but-unbuilt requirements (research cards = SRS R-PRM-011).
2. **It diagnoses the binding constraint (data fidelity) correctly, then doesn't follow it to its conclusion** — it would build an evidence lane for VWAP/volume signals on top of the one feed (IEX, ~2.5% volume) it declares unfit for exactly those signals.

Fix the framing and the sequencing and it becomes a sound, much smaller next step.

---

## 1. What the memo gets right (keep as-is)

All load-bearing **factual** claims verified accurate:

- Engine dispatches daily/intraday on `tempo.bar_size` — `engine.py:925,940`; T+1 fill guarantee is genuinely "by construction" — `engine.py:1230-1238`.
- Walk-forward + 30-trade gate exist — `walk_forward.py`, `policy.py:142`.
- The 5 intraday canaries exist, all `stage: paper`, all `lifecycle_exempt`, all knowingly-negative OOS Sharpe — verified in configs **and** the `promotions` table (ids 24-28).
- IEX-only feed, ~2.5% volume, canonical/SIP provider deferred — `ADR 0017:14,34,68`.
- Every cited doc (ADR 0017/0055/0056, capability-axes, FOUNDER_INTENT) is characterized faithfully.

The strategic framing (canary ≠ edge; evidence quality is the product; failure-that-explains-itself is success) is good and should survive the rewrite.

---

## 2. Redundancy — what already exists (the main rewrite)

The memo should be re-anchored on *current state*, not greenfield. Verified:

| Memo proposal | Reality (verified) | What's actually new |
|---|---|---|
| **Workstream B — "create a frozen system-owned universe"** | **Already built.** 7 frozen versioned id'd `configs/universe_*.yaml` manifests; `resolve_universe_ref` (`loader.py:119`); inline-XOR-ref validation (`loader.py:494-499`); manifest hashing wired into the run manifest (`run_manifest.py:60,103`); fetch-universe warmup (`cli/commands/data.py:48-50,169-172`); 18 strategy configs already select symbols system-side. **All 17 proposed ETFs already live in existing manifests.** | One new manifest file + **ETF-type exclusion validation** (reject leveraged/inverse/vol/OTC/exotic) — the ADR 0016 "instrument whitelist" is prose-only, **zero enforcement code** in `src/`. That validation is the only net-new piece of B. |
| **Workstream C — "build data-readiness checks"** | **Half built.** `data/bar_quality.py` already implements duplicate-ts, non-monotonic-ts, coverage/gap, invalid-OHLC, invalid-volume as a tested `DataQualityReport`. | Lift it out of the backtest-only gate into a standalone per-universe/timeframe report; make coverage/gap **intraday-aware** (it currently collapses to `.dt.date` — daily-correct, intraday-wrong); add zero-volume (suspicious-valid) detection; add a true **data-content hash** + **feed-quality label**; add stale-final-bar + session-edge coverage. |
| **"Reproducible evidence packages with hashes"** | **Already built, twice.** Promotion-time `EvidencePackage` (`evidence.py:23-47`) **and** a richer per-run manifest with git commit+dirty, config/universe hashes, cache version, execution assumptions (`run_manifest.py:33-78`), with a queryable per-run trade list. | Packaging/**exporting** the existing surfaces, not building them. |
| **"Research cards / pre-registration"** | **Unbuilt in code, but already a written requirement.** SRS **R-PRM-011** (`SRS.md:297`) + "Experiment Registry" (`PROMOTION_GOVERNANCE.md:137-149`) already mandate recording *"the hypothesis under test"* + a CLI to list entries by terminal status. Coverage = 0 (unbuilt). | **Build R-PRM-011** — don't reinvent it under a new name. Cite it. |

**Action:** Re-order so **Workstream A (current-state audit) runs first** — it would have caught that B is mostly done. The memo currently lists A after B.

---

## 3. The data contradiction (most important — please resolve)

**You cannot build a lane to "judge intraday strategies" on a feed you simultaneously declare unfit to judge them with.**

- The memo's flagship candidate family is **VWAP reversion** (#1; "data sensitivity: high, because VWAP depends on volume quality").
- The only feed that exists is **IEX at ~2.5% of consolidated volume** (`ADR 0017:14`). Verified: only `alpaca_provider` (IEX-hardcoded at `alpaca_provider.py:198,303,337`), `yahoo_provider`, `simulated` exist. **`MassiveDataProvider` / any SIP path does not exist** — deferred to Phase 1.2+ (`ADR 0017:68`).

So v1's headline candidate is the one most poisoned by the only data available. The memo half-admits this (the data gate, the "limited" caveat) but doesn't draw the conclusion: **the binding constraint is the data provider, and no governance scaffolding fixes a 2.5%-volume VWAP signal.** A confident verdict about noise is worse than no verdict.

Note the contrast: the ingestion **plumbing** is a non-issue — `data fetch-universe --timeframe 5m --force` already caches multi-symbol intraday bars today (verified end-to-end through `get_bars`/`backfill_range`; all 17 symbols already in the daily cache). The memo *understates* how cheap ingestion is and *understates* how load-bearing fidelity is.

**Please pick one and state it explicitly in the memo:**
- **(a)** Make the data provider (Massive / Alpaca SIP) a **hard prerequisite** for the lane, so VWAP/volume verdicts mean something; or
- **(b)** Accept IEX for v1 and **scope to price-only families** (opening-range, gap) where IEX OHLC is less unrepresentative — and **explicitly defer VWAP-family candidates** until a real feed exists.

Either is defensible. Building the full lane around the one signal IEX can't support is not.

---

## 4. What's genuinely new and worth building (concentrate effort here)

Strip the redundancy and the real, high-value work is small:

1. **Baseline framework (Workstream E)** — the most genuinely-new and most valuable. Of 6 proposed baselines, only 2 partially exist (daily SPY buy-and-hold in `analytics/benchmark.py`; the SPY-only unconditional-intraday-long *strategy*), and **4 are entirely absent** — no-trade, random-matched-exposure, time-of-day null, family-specific null. **Nothing randomized/shuffle/permutation-based exists anywhere in `src/`.** This is exactly the machinery that stops self-deception. Prioritize it.
2. **ETF-type exclusion validation** (the only net-new part of B).
3. **Intraday-aware data-readiness report** (the lift-out + intraday-correctness of `bar_quality`, per §2).
4. **R-PRM-011 experiment registry** (build the requirement you already wrote, instead of "research cards").

---

## 5. Recommended re-scope

- Lead with current-state reality, not greenfield. For each workstream, state what exists vs. what's net-new (use §2).
- Make the data-provider decision (§3) the gating decision of the whole memo.
- Demote B from a workstream to "one manifest file + type-validation."
- Promote E (baselines) to the centerpiece.
- Replace "research cards" framing with "implement R-PRM-011 / Experiment Registry."
- Net effect: this is roughly a **decent-sized** PR of genuinely new work, not the large program the current memo implies.

---

## 6. Factual corrections to fold in

Minor, but worth fixing so the memo is internally clean:

- **Symbol count:** the memo enumerates **17** candidate symbols (4 index + 11 sector + 2 macro, lines 241-243) but elsewhere calls it "16 ETFs" (line 678). Pick one. All 17 are already covered: SPY/QQQ/IWM/DIA (`universe.index_etfs.v1`), the 11 XL* (`universe.sector_etfs_spdr.v1`), TLT+GLD (`universe.curated_largecap.v2`).
- **Manifest count:** there are **7** universe manifests in `configs/`, not the "8" an earlier draft implied.
- **"5-minute cache is SPY-only"** understates it: 15Min/30Min/1Hour intraday caches are **entirely empty** — it's SPY-only *intraday, period*, not just at 5Min.
- **30Min via CLI:** the provider supports `MINUTE_30` but `cli/_shared.py:34-40 TIMEFRAME_CHOICES` omits `30m`, so `--timeframe 30m` is rejected. v1 uses 5Min so this doesn't block it, but note it (one-line fix) if a 30Min lane is ever in scope.
- **Engine line drift:** `_simulate` is at `engine.py:925` (dispatch branch :940), not 922. (CLAUDE.md's own gotcha has drifted too — flag for a separate doc fix.)
- **30-trade rule lives in the promotion gate**, not the engine/walk-forward, and is **waived for lifecycle-exempt** promotions — the memo's blanket "30 before conclusions" phrasing should note the exemption.

---

## Out-of-band cleanup (not the memo's job, but found during review)

Two canary configs have **stale headers that contradict their own `stage:` field**: `configs/meanrev_vwap_reversion_intraday_spy_v1.yaml:1` and `configs/momentum_vwap_trend_intraday_spy_v1.yaml:1` say *"BACKTEST-ONLY CANDIDATE / NOT a paper candidate"* while `stage: "paper"` and the event store say otherwise. Misleading to any future reader. Worth a tiny separate cleanup PR.
