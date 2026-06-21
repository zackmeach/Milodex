# Intraday ETF Evidence Hardening - Proposed Next Step

**Status:** Proposed for founder review  
**Date:** 2026-06-19  
**Type:** Strategy / research-lane memo, not an implementation plan  
**Revision note:** Updated after code-grounded review in
`docs/INTRADAY_ETF_EVIDENCE_HARDENING_FEEDBACK.md`.

## Purpose

This document explains the recommended next step for Milodex's intraday lane.

The earlier framing was directionally right but too greenfield. Milodex already
has several pieces this lane needs: universe manifests, universe hashing, run
manifests, promotion evidence packages, backtest data-quality checks, and
intraday harness canaries. The next step should build on those pieces instead of
rebuilding them.

The corrected recommendation is:

> Build a small, price-action-first intraday ETF evidence lane centered on
> baselines, intraday-aware data-readiness reporting, experiment registry entries,
> and clear candidate/canary separation.

This is not a promise that the first candidates will be profitable. Successful
completion means Milodex becomes better at judging intraday strategies and
better at preserving rejected evidence.

## Executive Summary

Milodex should still make intraday a major product lane, but the next step should
be smaller and sharper than "build a full intraday research platform."

Recommended next step:

> **Liquid ETF Price-Action Evidence v1**

This v1 deliberately avoids volume-sensitive strategy families such as VWAP
reversion because the current data provider path is Alpaca IEX only. IEX is
useful for harness work and exploratory price-action tests, but it is not enough
to make strong claims about consolidated volume, session VWAP, relative volume,
or volume-confirmed signals.

This memo chooses the practical v1 path:

> Use the existing IEX-backed intraday path for price-action candidates only, and
> make SIP/consolidated or canonical research data a hard prerequisite before
> judging VWAP or other volume-sensitive intraday families.

The genuinely new work is concentrated in four areas:

1. **Baseline framework**
   Build reusable intraday null comparisons: no-trade, generalized
   unconditional same-window long, random matched exposure, time-of-day null, and
   family-specific nulls.

2. **Intraday-aware data readiness**
   Reuse `src/milodex/data/bar_quality.py`, but make the coverage/gap logic
   intraday-aware, expose it as a standalone per-universe/timeframe report, add a
   data-content hash and feed-quality label, and add session-edge/stale-bar
   checks.

3. **Experiment registry / R-PRM-011**
   Implement the existing SRS and governance requirement for an experiment
   registry instead of inventing a parallel "research card" concept.

4. **Small universe hardening**
   Use the existing universe manifest system. Add one exact liquid-ETF manifest
   if needed, plus ETF-type exclusion validation for leveraged, inverse,
   volatility, OTC, or exotic products.

If every v1 candidate fails, but Milodex can explain the failure against clean
baselines and durable experiment records, the step succeeded.

## Where Milodex Is Today

### Product Direction

The founder direction captured in `docs/GRILL_DECISIONS_2026-06-18.md` is:

- Milodex should optimize the next phase around harness trustworthiness.
- Intraday should become a major product lane.
- The operator should not have to manually pick tickers.
- Strategy evidence and runtime risk posture are separate concepts.
- Canaries are useful for harness validation, but should not be confused with
  edge candidates.
- Milodex needs clearer scoring and normalized performance metrics.

This memo follows that direction.

### Current Intraday Capability

Milodex already has meaningful intraday infrastructure:

- the backtest engine dispatches daily versus intraday based on `tempo.bar_size`;
- minute-bar simulation exists;
- intraday strategies can be represented in configs;
- paper runners can run intraday strategies;
- runner control, locks, event-store records, and explanations exist;
- same-symbol concurrent operation has been hardened in later ADR addenda through
  per-strategy ledgers and non-backtest submit serialization.

The core question is no longer:

> Can Milodex run an intraday strategy?

The next question is:

> Can Milodex decide whether an intraday strategy deserves trust?

### Current Intraday Strategy Bank

`docs/STRATEGY_BANK.md` records five intraday paper-stage strategies that are
deliberate harness-validation canaries:

- `breakout.orb.intraday.spy.v1`
- `benchmark.unconditional_intraday_long.spy.v1`
- `meanrev.rsi2.intraday.spy.v1`
- `meanrev.vwap_reversion.intraday.spy.v1`
- `momentum.vwap_trend.intraday.spy.v1`

They were promoted via `lifecycle_exempt` to exercise the paper-runner harness,
not because their evidence showed durable edge. Their negative results are
acceptable because their job is mechanical validation.

This boundary should stay explicit:

- **Current intraday canaries:** prove Milodex can operate intraday.
- **Recommended next step:** prove Milodex can evaluate price-action intraday
  candidates.

### Current Universe Infrastructure

The memo should not treat universes as greenfield.

Milodex already has:

- 7 `configs/universe_*.yaml` manifests;
- `resolve_universe_ref` in the strategy loader;
- validation that strategy configs use either inline symbols or a universe ref,
  not both;
- universe manifest hashing in backtest run manifests;
- data warmup through `data fetch-universe`;
- many strategy configs already using system-owned symbol selection.

The proposed ETF symbols are already present across existing manifests:

- Index ETFs: `SPY`, `QQQ`, `IWM`, `DIA`
- Sector ETFs: `XLB`, `XLC`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLRE`,
  `XLU`, `XLV`, `XLY`
- Macro/risk ETFs: `TLT`, `GLD`

That is 17 candidate symbols.

What is actually new:

- one exact liquid-ETF manifest if the current manifests do not express the v1
  research universe cleanly;
- ETF-type exclusion validation so leveraged, inverse, volatility, OTC, and
  exotic products cannot accidentally enter the lane.

### Current Evidence And Reproducibility Infrastructure

Milodex already has two important evidence surfaces:

- promotion-time `EvidencePackage` in `src/milodex/promotion/evidence.py`;
- per-backtest run manifest generation in
  `src/milodex/backtesting/run_manifest.py`.

The run manifest already captures or derives several things this lane needs:

- strategy id and config hash;
- universe ref, symbol list, and manifest hash;
- provider class and cache version;
- execution assumptions;
- requested date window;
- data-quality summary;
- git commit and dirty-state metadata where available.

What is actually new:

- exporting or composing these existing surfaces into an intraday evidence report;
- adding baseline results to that report;
- adding an experiment-registry id/status to the report;
- adding data-content hash and feed-quality label where missing.

### Current Data-Quality Infrastructure

Milodex already has `src/milodex/data/bar_quality.py`.

It scans for:

- duplicate timestamps;
- non-monotonic timestamps;
- invalid OHLC relationships;
- invalid volume;
- requested-window coverage;
- requested-window gaps;
- requested-window edge warnings.

The limitation is shape, not existence. The current scanner is backtest-focused
and daily-shaped. It collapses timestamps to dates for coverage/gap checks, which
is correct for daily bars but insufficient for intraday bars.

What is actually new:

- make coverage/gap checks intraday-aware;
- expose the scan as a standalone per-universe/timeframe data-readiness report;
- add suspicious zero-volume warnings;
- add stale-final-bar detection;
- add session-open/session-close coverage checks;
- add data-content hash;
- add provider/feed quality labels.

### Current Data Provider Reality

This is the binding constraint.

The current Alpaca data provider uses stock historical data and requests IEX
feed data. There is no implemented `MassiveDataProvider`, no Alpaca SIP runtime
path, and no canonical consolidated intraday provider in code today.

ADR 0017 already defines the intended future:

- canonical research data provider;
- execution-adjacent market data;
- broker state of record;
- IEX as fallback/local-development quality.

The intraday lane must respect that distinction.

IEX data can support a limited price-action v1, but it should not be treated as
proof of consolidated market volume behavior. Any VWAP, relative-volume,
volume-confirmation, or volume-weighted signal should be deferred until SIP-grade
or canonical consolidated data exists.

### Current Experiment Registry Reality

This should not be called a new "research card" feature.

SRS R-PRM-011 already requires an experiment registry covering promoted,
rejected, failed, inconclusive, and abandoned strategy instances.
`docs/PROMOTION_GOVERNANCE.md` already describes an Experiment Registry that
captures the hypothesis under test and terminal status.

What is actually new:

- implement R-PRM-011;
- use the registry entry as the pre-registration artifact for intraday
  candidates;
- ensure rejected and abandoned intraday candidates remain queryable.

### Current Baseline Reality

Baselines are the most important missing piece.

Existing partial baselines:

- daily SPY buy-and-hold benchmark in analytics;
- SPY-only unconditional intraday long as a strategy canary.

Missing baseline framework:

- no reusable no-trade baseline artifact;
- no generalized unconditional same-window long across ETF symbols/universes;
- no random matched-exposure baseline;
- no time-of-day null;
- no family-specific null framework;
- no randomized/shuffle/permutation baseline machinery.

This is the centerpiece of v1 because it directly attacks self-deception.

## Data-Provider Decision

The feedback raised the key contradiction:

> You cannot build a lane to judge VWAP/volume strategies on a feed that is not
> fit to judge consolidated volume.

This memo resolves that by choosing the smaller v1:

### Chosen v1 path

Use current IEX-backed intraday data for **price-action** candidates only.

This allows Milodex to build the evidence lane without waiting for a new provider
while being honest about what it can and cannot judge.

### Deferred until data upgrade

Defer all volume-sensitive families:

- VWAP reversion;
- VWAP trend;
- relative-volume filters;
- volume-confirmed breakouts;
- volume-weighted indicators;
- any claim that depends on consolidated volume distribution.

These require an explicit data-provider upgrade: Alpaca SIP or a canonical
research provider such as the ADR 0017 research-data role.

### Alternative path

If the founder wants VWAP/volume signals to be the first serious intraday lane,
then the next step changes:

> Add SIP/canonical consolidated intraday data first, then build the evidence
> lane.

That path is valid, but larger. This memo recommends price-action v1 first.

## Recommended Destination

### Name

**Liquid ETF Price-Action Evidence v1**

### One-Sentence Goal

Milodex can evaluate pre-registered, price-action intraday ETF candidates across
a frozen liquid-ETF universe using intraday-aware data-readiness checks,
required baselines, existing reproducibility manifests, and experiment-registry
records.

### Intended User-Facing Meaning

For a non-expert operator, this should eventually support Bench language like:

> Research candidate: liquid-ETF opening range failure. Tested across 17 ETFs.
> Beat time-of-day and matched-exposure baselines. Paper soak not started. Data
> label: IEX exploratory.

Or:

> Blocked: liquid-ETF gap fade. Failed random matched-exposure baseline across
> most ETFs. Preserved as rejected evidence.

The operator should not need to understand Sharpe, walk-forward methodology, or
ETF selection to understand whether the system trusts the strategy.

## Scope

### In Scope

- U.S.-listed, plain-vanilla, liquid ETFs.
- Long-only intraday strategies.
- Price-action-only candidates.
- 5-minute bars for v1 if coverage is adequate; 15-minute bars if 5-minute data
  quality is too weak.
- Regular U.S. equity market session.
- Existing universe manifest machinery.
- One exact liquid-ETF v1 manifest if useful.
- ETF-type exclusion validation.
- Intraday-aware data-readiness reporting.
- Experiment registry entries per R-PRM-011.
- Baseline/null comparisons.
- Evidence export/composition using existing run manifests and evidence package
  surfaces.
- Paper-soak process for survivors.
- Bench/read-model language that separates canaries from edge candidates.

### Out Of Scope

- VWAP and other volume-sensitive candidates.
- Crypto.
- Options.
- Futures.
- Forex.
- Short selling.
- Margin-dependent strategies.
- Tick-level or sub-minute trading.
- Multi-leg orders.
- Broker #2.
- LLM decisioning.
- Live or micro-live promotion.
- User-selected symbols.
- Profit promises or "AI trader" framing.

Keeping these out of scope is important. The point is to harden one lane without
moving every other axis at once.

## Candidate Universe

The v1 research universe should be system-owned and frozen.

Recommended symbols:

- Broad index ETFs: `SPY`, `QQQ`, `IWM`, `DIA`
- Sector SPDRs: `XLB`, `XLC`, `XLE`, `XLF`, `XLI`, `XLK`, `XLP`, `XLRE`,
  `XLU`, `XLV`, `XLY`
- Macro/risk ETFs: `TLT`, `GLD`

That is 17 ETFs.

This exact list can be expressed as a new `liquid_etf_core.v1` manifest if that
keeps the research lane clean. But the important work is not building the
universe system. That system exists. The important work is:

- exact v1 membership;
- hash/version;
- inclusion/exclusion rationale;
- validation that disallowed ETF types cannot enter.

The operator should not have to pick from this list.

## Candidate Families For v1

Because v1 uses IEX-backed data, candidate families should avoid volume claims.

Recommended v1 families:

1. **Opening range continuation / failure**
   - Thesis: early range breaks may continue or fail in liquid ETFs.
   - Uses price bars, not volume-derived VWAP.
   - Must beat unconditional same-window long and time-of-day baselines.

2. **Gap fade / gap continuation**
   - Thesis: overnight gaps may show short-horizon continuation or reversal.
   - Requires clean prior close and session open.
   - Must beat simple gap-conditioned nulls.

3. **Late-session price continuation / reversal**
   - Thesis: late-day price path may carry continuation or reversal information.
   - Requires careful close-window rules.
   - Must beat time-of-day nulls.

4. **Price-extension mean reversion**
   - Thesis: extreme price extension from a price-only anchor may mean-revert.
   - The anchor must not be VWAP unless the data provider is upgraded.

Deferred families:

- VWAP reversion;
- VWAP trend;
- relative-volume strategies;
- volume-confirmed breakouts.

## Experiment Registry Entries

The earlier memo used "research cards." The corrected framing is:

> Build and use the R-PRM-011 Experiment Registry.

Each intraday candidate should have a registry entry before evidence is run.

Suggested required fields:

- experiment id;
- strategy id;
- terminal status;
- hypothesis under test;
- universe id and hash;
- timeframe;
- data source and feed-quality label;
- eligible session window;
- entry rule;
- exit rule;
- stop/time-stop rule;
- sizing rule;
- max positions;
- slippage/spread assumptions;
- forbidden trade windows;
- required baselines;
- evaluation window;
- pass/fail criteria;
- known caveats;
- date registered;
- date closed, when terminal;
- reason for terminal status.

Terminal statuses should include at least:

- `promoted`;
- `rejected`;
- `failed`;
- `inconclusive`;
- `abandoned`;
- `superseded`.

Rejected and abandoned candidates should remain durable. They are evidence.

## Required Baselines

Every candidate should be compared against simple alternatives.

Recommended v1 baseline set:

1. **No-trade baseline**
   - Records the cost of doing nothing.
   - Important for showing that activity itself is justified.

2. **Generalized unconditional same-window long**
   - Extends the current SPY-only unconditional intraday long concept across the
     ETF universe and candidate time window.
   - Tests whether the signal beats simply being long during that window.

3. **Random matched exposure**
   - Same number of trades and similar holding durations, randomized across
     eligible bars.
   - Must be deterministic under a recorded seed.
   - Tests whether the candidate beats random timing with the same exposure.

4. **Time-of-day null**
   - Tests whether the candidate is just capturing generic session behavior.

5. **Family-specific null**
   - Example: a gap strategy must beat a simpler gap-conditioned rule.
   - Example: opening-range failure must beat a simple post-open reversal null.

The baseline framework should store:

- baseline id;
- baseline type;
- seed, if randomized;
- universe/hash;
- data manifest/hash;
- candidate run id;
- comparable metrics;
- per-symbol contribution;
- pass/fail result.

The baseline requirement matters because intraday strategies can look useful when
they are really:

- long exposure during a bull period;
- time-of-day exposure;
- one ETF carrying the result;
- lower turnover than a bad comparison;
- a lucky parameter choice.

## Intraday Data Readiness

The v1 lane should reuse the existing `bar_quality.py` concepts and make them
fit intraday evidence.

### Required Report Fields

For every ETF in the v1 universe, produce a data-readiness report covering:

- provider/feed label;
- timeframe;
- requested date range;
- observed date range;
- expected regular-session bar count;
- observed regular-session bar count;
- missing bar windows;
- duplicate timestamps;
- non-monotonic timestamps;
- invalid OHLC;
- invalid volume;
- suspicious zero-volume bars;
- stale final bar;
- session-open coverage;
- session-close coverage;
- cache version;
- data-content hash;
- pass/fail/warn verdict.

### Feed Quality Labels

Evidence should carry a feed-quality label:

- `research_grade`: canonical/consolidated research data, suitable for durable
  evidence;
- `execution_adjacent`: broker/SIP data suitable for runtime comparison and
  submit-adjacent views;
- `fallback`: IEX/local-development quality, useful for harness and exploratory
  price-action evidence, limited for promotion claims.

For v1, IEX-backed data should be labeled `fallback` or equivalent. The label
should be visible in evidence, not hidden in implementation detail.

## Evidence Report

The v1 lane should compose existing evidence surfaces rather than invent a new
package from nothing.

Every candidate report should include:

- experiment-registry id;
- strategy id;
- strategy config hash;
- universe id and hash;
- data-readiness report id/hash;
- feed-quality label;
- backtest run manifest;
- git commit/dirty state where available;
- timeframe;
- evaluation window;
- slippage/spread assumptions;
- trade list reference;
- aggregate metrics;
- per-symbol metrics;
- per-window metrics;
- baseline results;
- data-quality incidents;
- caveats;
- terminal verdict.

The report should make evidence limitations obvious. For v1, a price-action
candidate can be evaluated under IEX caveat. A VWAP candidate should not receive
a durable verdict until the data provider is upgraded.

## Backtest Methodology

The exact windows should be selected during implementation, but the evidence lane
should follow this structure:

- use a fixed historical window selected before candidate results are known;
- split into walk-forward or other out-of-sample segments;
- report aggregate OOS results and per-window results;
- report per-symbol contribution;
- require candidate performance to survive friction;
- record parameter sensitivity;
- refuse to promote strategies that depend on one ETF, one month, or one
  parameter setting;
- preserve rejected results in the experiment registry.

The 30-trade rule belongs to promotion policy, not the backtest engine, and
`lifecycle_exempt` promotions can waive statistical gates. This lane should not
use `lifecycle_exempt` to make a weak edge candidate look deserving. If a
candidate is promoted only for mechanics, it should be labeled as a canary.

## Execution And Fill Assumptions

Intraday evidence is highly sensitive to fill assumptions. v1 should document:

- when decisions are made;
- whether decisions use only completed bars;
- when orders are assumed to fill;
- whether fills occur at next open, next close, midpoint, or a conservative price;
- slippage assumption;
- spread assumption;
- whether entries are forbidden near the open or close;
- whether partial fills are modeled or deferred;
- whether order rejections are simulated;
- whether stale bars block decisions.

Recommended default posture:

- no partial-bar decisions;
- no catch-up trades from missed morning signals;
- no new entries during fragile open/close windows unless the experiment is
  explicitly about those windows;
- conservative friction;
- explicit caveats when the backtest does not model a real-world behavior.

The goal is not perfect market simulation. The goal is honest market simulation.

## Paper Soak

Backtests should not be the end of intraday evidence. Survivors should enter a
fixed paper-soak process.

### Paper Soak Purpose

Paper soak answers:

- does the strategy run at the expected cadence?
- do paper fills resemble backtest fill assumptions?
- do data gaps occur?
- do risk vetoes happen as expected?
- do broker rejections occur?
- does slippage stay within expected bounds?
- does the strategy's behavior resemble the research evidence?
- does Milodex explain the activity clearly?

### Suggested Paper Soak Standard

For v1, a candidate that passes backtest evidence should collect a fixed forward
window such as:

- 20 regular trading sessions, or
- a minimum number of eligible signal days, whichever is more appropriate for the
  family.

The soak should record:

- proposed trades;
- submitted trades;
- risk vetoes;
- broker rejects;
- fills;
- expected versus observed slippage;
- data incidents;
- P/L versus backtest expectation;
- P/L versus baseline;
- operator interventions;
- runner lifecycle events;
- stop/close/reopen continuity events.

Passing paper soak should mean behavior matched evidence well enough to keep
collecting trust. It should not mean "made money for 20 days."

## Product Surface Requirements

### Bench

Bench should make these categories obvious:

- **Harness canary:** runs to prove mechanics, not edge.
- **Research candidate:** has a hypothesis and evidence package, but has not
  earned paper trust.
- **Paper evidence collector:** passed enough research evidence to collect
  forward paper data.
- **Blocked:** failed evidence gate, with the reason preserved.
- **Deserving paper strategy:** earned paper status on merit.

Example language:

> Harness canary: exercises intraday runner mechanics. Not an edge candidate.

> Research candidate: price-action evidence passed across liquid ETFs. Paper soak
> not complete. Data label: IEX exploratory.

> Blocked: failed random matched-exposure baseline across 12 of 17 ETFs.

Paper stage alone should not imply "trusted alpha."

### Front

The Front page should stay simple. It should not become a research dashboard.

Acceptable high-yield examples:

> 2 intraday candidates are collecting paper evidence today.

> Intraday research is quiet today. No candidates are running.

The Front should not show ETF lists, technical metrics, or hypothesis details
above the fold.

### Ledger

Ledger should preserve:

- what strategy proposed;
- what risk allowed or blocked;
- what broker accepted or rejected;
- what filled;
- which experiment/evidence package the event belongs to;
- whether the event was candidate evidence, harness validation, or ordinary paper
  operation.

### Desk

Desk can show dense runtime detail:

- active intraday runners;
- data feed state;
- stale/missing bar warnings;
- open orders;
- fills;
- risk vetoes;
- strategy lifecycle state;
- broker reconciliation state.

## Definition Of Done

This recommended step is complete when Milodex can do all of the following:

1. **Current-state audit**
   - Documents which pieces already exist and which are genuinely new.
   - Confirms intraday caches, provider/feed path, existing baselines, and current
     evidence surfaces.

2. **Data-provider decision**
   - v1 explicitly scopes to price-action candidates on IEX-backed data.
   - VWAP/volume-sensitive candidates are blocked until SIP/canonical data exists.

3. **Universe hardening**
   - A v1 liquid ETF universe is expressed exactly.
   - Existing universe infrastructure is reused.
   - ETF-type exclusion validation exists.

4. **Intraday data readiness**
   - Existing bar-quality checks are lifted into an intraday-aware report.
   - Reports include feed-quality label and data-content hash.
   - Reports catch missing/stale/duplicate/out-of-session issues at bar level.

5. **Experiment registry**
   - R-PRM-011 is implemented or sufficiently scaffolded for this lane.
   - Intraday candidates are registered before evidence runs.
   - Rejected/inconclusive/abandoned candidates remain queryable.

6. **Baseline framework**
   - No-trade baseline exists.
   - Generalized unconditional same-window long exists.
   - Random matched-exposure baseline exists and is deterministic under seed.
   - Time-of-day null exists.
   - Family-specific nulls can be attached to candidates.

7. **Evidence report**
   - Existing run manifests and evidence surfaces are composed into a report.
   - Baseline results, data-readiness results, and experiment-registry status are
     included.

8. **Candidate evaluation**
   - At least three price-action intraday hypotheses are evaluated.
   - Each receives a pass/fail/inconclusive verdict.
   - Failure is preserved and explainable.

9. **Product language**
   - Bench distinguishes canaries, research candidates, paper evidence collectors,
     blocked strategies, and deserving paper strategies.
   - The operator is not asked to pick ETF symbols.

## What Success Looks Like - Concrete Example

Suppose Milodex evaluates:

`breakout.opening_range_failure.intraday.liquid_etf_5m.v1`

The successful end state might look like this:

1. `liquid_etf_core.v1` is frozen with 17 ETFs.
2. 5-minute IEX-backed bars are cached for the selected historical window.
3. The data-readiness report says 15 ETFs pass, 2 have missing session-edge
   coverage and are excluded or warned.
4. The experiment registry entry is created before the run and records the
   hypothesis, universe hash, timeframe, caveats, and required baselines.
5. Milodex runs the candidate and required baselines:
   - no trade;
   - unconditional same-window long;
   - random matched exposure;
   - time-of-day null;
   - opening-range-specific null.
6. The evidence report shows:
   - performance across multiple ETFs;
   - per-symbol contribution;
   - per-window stability;
   - whether the candidate beats matched exposure after friction;
   - whether one symbol or one month carries the result;
   - the explicit data label: IEX exploratory.
7. Bench labels it:
   - "Research candidate";
   - "price-action evidence: passed" or "blocked";
   - "paper soak: not started";
   - "data label: IEX exploratory."
8. If it passes, the operator can start a paper soak only after the evidence is
   visible.
9. If it fails, the experiment registry records the terminal status and reason.

That is a successful hardening result. It is successful because the verdict is
durable, explainable, and baseline-aware.

## Suggested Workstreams

This is not a build plan, but these are the natural workstreams a later plan
would likely contain.

### Workstream A - Current-State Audit

Confirm current code and data reality:

- universe manifests and symbol coverage;
- intraday caches by timeframe and symbol;
- provider/feed path;
- existing bar-quality checks;
- existing run manifest/evidence package fields;
- existing benchmark/baseline surfaces;
- current gaps in R-PRM-011 experiment registry implementation;
- Strategy Bank notes that may be stale versus current ADR addenda.

Output:

- short audit note;
- exact net-new build list;
- implementation risks.

### Workstream B - Data Decision And Candidate Scope

Codify the v1 data decision:

- IEX-backed data can support exploratory price-action evidence.
- VWAP/volume-sensitive evidence is blocked until better data exists.

Output:

- memo/ADR note or section in this doc;
- explicit blocked list for volume-sensitive candidates.

### Workstream C - Universe Hardening

Reuse existing universe infrastructure.

Output:

- optional `liquid_etf_core.v1` manifest;
- inclusion/exclusion rationale;
- ETF-type exclusion validation;
- tests for disallowed ETF types.

### Workstream D - Intraday Data Readiness

Lift and extend `bar_quality.py` for intraday use.

Output:

- standalone per-universe/timeframe data-readiness report;
- intraday-aware coverage/gap checks;
- session-edge checks;
- stale-final-bar check;
- suspicious zero-volume warnings;
- feed-quality label;
- data-content hash.

### Workstream E - Baseline Framework

Build reusable intraday null comparisons.

Output:

- no-trade baseline;
- generalized unconditional same-window long baseline;
- random matched-exposure baseline with recorded seed;
- time-of-day null;
- family-specific null attachment point;
- baseline result storage/reporting.

### Workstream F - Experiment Registry

Implement the R-PRM-011 / Promotion Governance registry path.

Output:

- registry data model;
- create/list/update or equivalent CLI;
- terminal statuses;
- no-delete behavior;
- link from registry entry to backtest/evidence outputs.

### Workstream G - Evidence Report Composition

Compose existing artifacts into a candidate report.

Output:

- report schema;
- links to run manifest, data-readiness report, baselines, and registry entry;
- pass/fail/inconclusive verdict.

### Workstream H - Candidate Runs And Product Presentation

Run a small set of price-action candidates and present them honestly.

Output:

- at least three registered price-action candidates;
- evidence verdicts;
- Bench category language;
- optional Front summary line only if useful.

## Open Decisions For Founder Review

These should be decided before turning this memo into an implementation plan:

1. **Data path**
   - Recommendation: accept IEX for price-action v1 and defer VWAP/volume
     strategies until SIP/canonical data exists.

2. **Universe membership**
   - Recommendation: start with the 17 ETFs listed above.

3. **Timeframe**
   - Recommendation: start with 5-minute bars if readiness reports pass;
     otherwise use 15-minute bars for v1.

4. **Candidate family count**
   - Recommendation: three price-action families only for v1.

5. **Baseline requirement**
   - Recommendation: no candidate receives a favorable verdict without matched
     exposure and time-of-day baseline comparisons.

6. **Paper soak duration**
   - Recommendation: 20 trading sessions for v1, with family-specific minimum
     signal-count adjustments if needed.

7. **Bench category labels**
   - Recommendation: add first-class labels for harness canary, research
     candidate, paper evidence collector, deserving paper, and blocked.

8. **Success standard**
   - Recommendation: treat "all candidates failed, but failures are well
     explained" as successful completion of the hardening step.

## Recommended Locked Positions

If the founder agrees with this direction, these are the positions worth locking:

- The next intraday step is evidence hardening, not merely adding strategy
  variants.
- The first governed intraday universe is liquid ETFs, not broad single-name
  stocks.
- Symbol selection remains system-owned.
- Existing universe/evidence infrastructure should be reused.
- Baselines are the centerpiece of v1.
- R-PRM-011 Experiment Registry is the pre-registration mechanism.
- IEX-backed v1 is price-action only.
- VWAP/volume-sensitive candidates wait for SIP/canonical data.
- Harness canaries and edge candidates must be visibly distinct.
- Passing paper soak means behavior matched evidence, not simply short-term
  profit.
- Live or micro-live is out of scope for this step.

## Adjacent Cleanups Not In This Memo

These were found during review but are not part of the evidence-lane proposal:

- Some canary config headers may still say "backtest-only" while the strategy is
  now paper-stage in config/event-store. Those headers should be corrected in a
  small separate cleanup.
- `TIMEFRAME_CHOICES` currently omits `30m` even though the provider supports
  `MINUTE_30`. This does not block a 5-minute v1 lane, but it should be fixed if
  a 30-minute lane is planned.
- Some Strategy Bank concurrency text may describe older guardrails superseded by
  later ADR addenda. Refresh before using it as implementation source.

## Source Documents

Local:

- `CLAUDE.md`
- `docs/FOUNDER_INTENT.md`
- `docs/GRILL_DECISIONS_2026-06-18.md`
- `docs/STRATEGY_BANK.md`
- `docs/PROMOTION_GOVERNANCE.md`
- `docs/RISK_POLICY.md`
- `docs/SRS.md`
- `docs/architecture/2026-05-30-harness-capability-axes.md`
- `docs/adr/0017-data-source-hierarchy.md`
- `docs/adr/0055-event-store-per-strategy-position-ledger.md`
- `docs/adr/0056-cross-process-submit-serialization-per-account-advisory-lock.md`

External context:

- FINRA Regulatory Notice 26-10:
  `https://www.finra.org/rules-guidance/notices/26-10`
- Alpaca Trading API:
  `https://docs.alpaca.markets/us/docs/trading-api`
- Alpaca Market Data documentation:
  `https://docs.alpaca.markets/us/docs/about-market-data-api`

