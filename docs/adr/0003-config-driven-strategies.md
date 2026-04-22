# ADR 0003 — Strategy Parameters in YAML, Not Code

**Status:** Accepted
**Date:** 2026-04-16

## Context

Strategies have two kinds of definition: the *behavior* (how signals are computed from bars, how intents are constructed) and the *parameters* (universe, lookback windows, thresholds, tempo, risk overrides, stage). Parameters change frequently during research; behavior changes slowly.

## Decision

Strategy behavior lives in Python under `src/milodex/strategies/`. Strategy parameters live exclusively in versioned YAML files under `configs/`. A strategy is instantiated from `(class, config file)`; the same class can be reused by many configs with different parameters.

## Rationale

- **Tuning without code edits.** Adjusting a lookback window or a universe symbol should not require a code change or a restart of whatever test harness is running. YAML edits are safer, more reviewable in diff form, and don't risk accidentally changing behavior.
- **Versioning.** `configs/*.yaml` is checked into git. Every parameter value is attributable to a specific commit, which matters when evaluating whether a backtest result came from today's parameters or last month's.
- **Clear separation of concerns.** The code answers "how does this strategy work?" The config answers "what's it tuned to right now?" Reviewers and future-you can look at either independently.
- **Validatable.** A schema (`milodex config validate`) catches typos and missing fields before a strategy runs, which a Python-literal parameter dict would not.
- **Reusability across stages.** The same YAML file declares stage (`backtest` → `paper` → ...), so promotion is a one-line edit with a clear audit trail.
