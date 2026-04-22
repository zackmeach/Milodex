# ADR 0018 — Durable State Lives Under `data/`, Not `state/`

**Status:** Accepted
**Date:** 2026-04-21
**Supersedes:** `SRS.md` R-XC-006 (the `state/` directory layout)

## Context

`SRS.md` R-XC-006 specifies a top-level `state/` directory holding all durable Milodex-authoritative state: `state/milodex.db`, `state/kill_switch.json`, `state/strategies/<name>.json`, and `state/locks/`. That layout was written before the SQLite event store (ADR 0011) and the hybrid source-of-truth model (ADR 0010) were finalized.

Since then, the implementation has converged on a different layout:

- The event store lives at `data/milodex.db`, resolved by `milodex.config.get_data_dir()`.
- `KillSwitchStateStore` persists kill-switch state inside the event store itself. The legacy `logs/kill_switch_state.json` file is migrated into the event store on first access and is no longer authoritative.
- Per-strategy JSON files (`state/strategies/<name>.json`) do not exist. Strategy state that needs to survive a controlled stop (rolling features, last-trade timestamps, streak counters — R-STR-010) is not yet landed; when it lands it will live under `data/strategies/<strategy_id>.json`.
- Single-process advisory locks live at `data/locks/`, resolved by `milodex.config.get_locks_dir()` (overridable via `MILODEX_LOCKS_DIR`).

The SRS, the code, and ADR 0011 (which still references `state/milodex.db`) have drifted. This ADR supersedes R-XC-006 and pins the convergent layout.

## Decision

All durable Milodex-authoritative state lives under `data/`:

- `data/milodex.db` — SQLite event store (trade log, promotion log, kill-switch events, strategy runs, backtest runs, explanation records). Authoritative per ADR 0011.
- `data/locks/` — single-process advisory lock files (e.g. `data/locks/milodex.runtime.lock`). Each lock file is a JSON PID record created atomically; stale locks are reclaimed after a cross-platform liveness check.
- `data/strategies/<strategy_id>.json` — per-strategy state (reserved; no file yet). When R-STR-010 state flush lands, it will live here.
- The legacy `logs/kill_switch_state.json` is **not** authoritative. It exists only as a one-way migration source: `KillSwitchStateStore` reads it on first access, imports it into the event store, and never writes to it again.

Directory paths are resolved through `milodex.config`:

- `get_data_dir()` — `MILODEX_DATA_DIR` override, else `<project_root>/data`.
- `get_locks_dir()` — `MILODEX_LOCKS_DIR` override, else `get_data_dir() / "locks"`.
- `get_logs_dir()` — `MILODEX_LOG_DIR` override, else `<project_root>/logs`. Logs only; no durable state.

The `data/` directory is gitignored except for `.gitkeep`. The same applies to `logs/`.

## Rationale

- **Alignment with what the code actually does.** Every module that persists durable state already uses `get_data_dir()`. Renaming to `state/` now would churn migrations, tests, environment overrides, and operator muscle memory for no gain.
- **The event store subsumes most of what `state/` was meant to hold.** Kill-switch state, strategy runs, trade log, and explanation records are all SQLite rows — a single file (`data/milodex.db`) backed up by a single copy. The per-file JSON layout envisioned by R-XC-006 was appropriate when the event store did not exist; it is redundant now.
- **Advisory locks remain genuine state.** Locks are tiny, short-lived, and benefit from being file-based (they are visible with `ls`, reclaimable by liveness check, and do not require a live database connection to inspect). `data/locks/` keeps them colocated with the rest of durable state without dragging them into SQLite.
- **One override axis per concept.** `MILODEX_DATA_DIR`, `MILODEX_LOCKS_DIR`, `MILODEX_LOG_DIR`, and `MILODEX_CACHE_DIR` each name exactly one purpose. There is no compound override and no implicit precedence between them.
- **The `state/` name was aspirational, not contractual.** No external system — no backup script, no third-party tool, no operator runbook outside the Milodex docs — references `state/`. The cost of superseding it is entirely in the docs.

## Consequences

- `SRS.md` R-XC-006 is updated to point at `data/` and to reference this ADR.
- `ADR 0011` (`state/milodex.db`) has a directory-layout note added referring to this ADR; the decision about using SQLite is unchanged.
- `ADR 0012` (`state/strategies/<name>.json` on controlled stop) is unaffected in substance; the future path is `data/strategies/<strategy_id>.json`. A directory-layout note will be added when R-STR-010 lands.
- `KillSwitchStateStore`'s migration path stays: read legacy `logs/kill_switch_state.json` on first access, write forward into `data/milodex.db`, never write back.

No code changes are required by this ADR. It is documentation converging on the implementation.
