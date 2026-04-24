# ADR 0021 — Walk-forward metrics are OOS-aggregate, not whole-period

**Status:** Accepted · 2026-04-24
**Supersedes:** (implicit prior behaviour of `backtest --walk-forward`)
**Related:** ADR 0020 (promotion thresholds as code invariants), SRS R-BKT-002 / R-BKT-003, `docs/VISION.md` §"Optimization has a line"

## Context

Before this ADR the `milodex backtest --walk-forward` command ran a single
backtest across the full `[start, end]` window and then appended the window
boundaries that *would have been used* as metadata. The reported Sharpe, max
drawdown, total return — the numbers the promotion gate reads — were
whole-period metrics. Walk-forward validation existed in name but not in
substance.

This was the exact failure mode `docs/VISION.md` warns against: a strategy
could look great because the full-period metric was dominated by a single
lucky stretch, and the walk-forward label gave false comfort that the
performance had been tested out-of-sample.

## Decision

When `--walk-forward` is set, the engine now runs an independent simulation
for each OOS test window. Equity resets to the caller's `initial_equity` at
the start of every window; per-window trades, equity curves, and metrics are
collected separately and then aggregated into a single OOS stream.

OOS-aggregate metrics are the authoritative evidence:

- **Total return:** geometric compound of per-window returns.
- **Sharpe:** computed on the concatenation of per-window daily returns
  (not the mean of per-window Sharpes — that double-counts volatility).
- **Max drawdown:** computed on the stitched cumulative equity curve, where
  each window's return stream compounds on top of the running equity.

The promotion gate — `milodex promotion promote` → `_metrics_from_run` —
reads these OOS-aggregate numbers from the walk-forward run's metadata.
Whole-period runs (no `--walk-forward`) continue to report whole-period
metrics; only the walk-forward path changes.

A single `BacktestRunEvent` is written per walk-forward invocation so the
gate can be keyed off one `run_id`. Per-window breakdowns live in the run's
metadata alongside the aggregate.

## Stability diagnostics

Three robustness checks are surfaced next to the aggregate:

- **`sharpe_min` / `sharpe_max` / `sharpe_std`** across windows.
- **`windows_positive` / `windows_negative`** counts.
- **`single_window_dependency` flag:** true when the aggregate return is
  positive but goes ≤ 0 after removing the best-returning window. This
  catches the "one lucky window carrying the whole result" failure mode —
  the fragility signal `VISION.md` calls out as the difference between edge
  and overfit.

The flag is surfaced in the CLI human output and stored in metadata. It
does not currently block promotion on its own; it's evidence for the
operator reviewing the trust report. Blocking on stability is a separate
decision that needs thought about how to set the threshold.

## Consequences

**Breaking behaviour change.** `backtest --walk-forward` now reports
different numbers than it did before for the same `[start, end]`. Historical
runs in the evidence log (`docs/reviews/PHASE_1.3_EVIDENCE_2026-04-22.md`)
should be re-run and their numbers interpreted as pre-fix baseline, not
OOS-aggregate evidence.

**Promotion gate becomes honest.** Strategies that previously passed the
`backtest → paper` gate on whole-period Sharpe may now fail under OOS
evaluation. This is correct — the gate was never supposed to approve
strategies that only work in-sample.

**Parameter search remains disallowed.** This ADR changes how we *evaluate*
fixed parameters, not how we search them. Walk-forward parameter
optimization (grid-searching per window, picking the best train-window
params for each test window) is explicitly out of scope — Milodex strategies
use published parameters and walk-forward serves as OOS validation, not as
a fitting procedure.

## Alternatives considered

**A. Keep whole-period metrics, add OOS as a secondary view.**
Rejected. The promotion gate must read the honest number; having a primary
misleading number and a buried correct one is worse than changing the
primary.

**B. Store each window as its own `BacktestRunEvent`.**
Rejected. The gate would need to aggregate across runs on every evaluation,
which couples the gate to walk-forward semantics. Single parent run with
per-window metadata keeps the gate simple and lets the walk-forward runner
own the aggregation logic.

**C. Report per-window arithmetic mean return instead of geometric compound.**
Rejected. Arithmetic averaging under-states the effect of compounding
capital across windows and over-states stability. Geometric compounding is
the only answer that matches what the strategy actually experiences under
continuous deployment.
