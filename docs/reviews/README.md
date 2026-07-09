# Reviews — point-in-time evidence & forensic write-ups

Everything here is **dated/frozen**: a snapshot of what was true when it was
written, kept for the record. Nothing in this directory is current canon. When a
review's finding is still live, it has been folded into an ADR, the SRS, or
`docs/CURRENT_ROADMAP.md` — trust those, not the review.

This index is intentionally lean: individual dated files are self-describing by
filename. It only calls out the multi-file **series** (which need reading order)
and the non-`.md` evidence, so the directory doesn't read as a flat dump.

## Conventions

- New files: `YYYY-MM-DD-topic.md` (date-prefixed kebab-case).
- Legacy names predate the convention and are left as-is (topic-only kebab like
  `strategy-bank-*`, and `SCREAMING_SNAKE` like `PHASE_1.3_EVIDENCE_*` /
  `PROJECT_STATE_ASSESSMENT_*`). Don't rename in isolation — some are cited by
  code/scripts (e.g. `backtest-rejection-analysis.md` is referenced from
  `src/milodex/backtesting/engine.py` and `scripts/counterfactual_gate.py`).

## Strategy-bank research series (2026-04 → 05)

Frozen research closeout. **Current bank truth is `docs/STRATEGY_BANK.md` /
`data/milodex.db`, not these files.** Reading order:

1. `strategy-bank-tier1-results.md` → `strategy-bank-tier2-results.md` → `strategy-bank-tier3-results.md` / `strategy-bank-tier3-52w-screen.md` — the tiered screens.
2. `backtest-rejection-analysis.md` — why ~92% of candidates were rejected (engine bugs vs gate).
3. `strategy-bank-post-fixes.md` — re-baseline after the engine fixes (run IDs in the `.json` sibling).
4. `strategy-bank-survivorship-corrected.md` · `strategy-bank-final-comparison.md` — the corrected final comparison (`counterfactual_gate.py` reads the latter).
5. `screen_2026-05-07.md` — the 2026-05-07 walk-forward screen batch.

`*.json` siblings are the regenerable raw generator output for the matching
`.md` — evidence, not authored prose.

## Intraday-ETF evidence set (2026-06)

The `2026-06-1x/2x-intraday-etf-evidence-*.md` files are the Phase-2 intraday
lane's memo → feedback → lean-slice → orchestration → completion → gate chain
(moved here from `docs/` root 2026-07-02). The Tier-1 gate report is
**superseded** (see its in-doc banner). Sequenced by `CURRENT_ROADMAP.md` §3.4.

## Raw backtest evidence

`phase1.3-backtest-evidence/` holds the raw trade/equity CSVs cited by
`PHASE_1.3_EVIDENCE_2026-04-22.md`. Engine CSV *exports* (dir names ending
`_backtest_trades.csv/`) are gitignored — relocate real evidence into a named
folder like this one, don't commit the export dir.
