# ADR 0011 — SQLite as the Event-Shaped Store

**Status:** Accepted
**Date:** 2026-04-16

## Context

Milodex needs a durable store for event-shaped data: trade intents, fills, cancellations, promotion transitions, and (eventually) paper-vs-live divergence records. The access patterns are transactional — single-row appends, single-row updates (a submitted intent gains its fill record later) — and queries are selective and aggregation-heavy ("Sharpe for strategy X since date Y", "all AAPL buys in the last 30 days").

The project already uses Parquet for the market-data cache (ADR 0002), and small JSON files are used for ephemeral state (`state/kill_switch.json`, `state/strategies/<name>.json`). Neither format fits the event-log access pattern.

## Decision

Trade log, fill log, and promotion log live in a single SQLite database at `data/milodex.db` (per ADR 0018, which supersedes the original `state/milodex.db` path). WAL mode is enabled (`PRAGMA journal_mode=WAL`). Structured fields get typed columns; freeform inspectable fields (reasoning blobs, per-check risk verdicts, evidence snapshots) are stored as JSON text within rows. Schema evolution is handled by a `schema_version` table and a `migrations/` directory of numbered SQL files.

Initial schema (summary; full schema in code):

- `trades` — one row per intent. Columns include `client_order_id`, `strategy_name`, `strategy_version`, `stage`, `symbol`, `side`, `quantity` (notional + shares), `order_type`, `submitted_at` (UTC ISO-8601), `alpaca_order_id`, `filled_at`, `fill_price`, `filled_quantity`, `status`, `reasoning` (JSON), `risk_checks` (JSON). Indexed on `(strategy_name, submitted_at)`, `symbol`, `status`.
- `promotion_log` — one row per stage transition, with `from_stage`, `to_stage`, `evidence_snapshot` (JSON), `note`.

SQLite does **not** hold current-state data (positions, open orders, account balance). Those are queried live from Alpaca per ADR 0010.

## Rationale

- **Transactional writes with later updates** match SQLite's model exactly. An intent row is inserted at submission and updated at fill; JSONL requires a destructive rewrite, Parquet requires a file rewrite or a compaction pipeline.
- **Selective indexed queries** are the primary analytics pattern. SQL with indexes beats "load N files into pandas, filter in memory" at every scale above toy. A year of paper trading at realistic rates produces thousands of rows — trivial for SQLite, already sluggish for JSONL scanning.
- **Schema evolution** is supported natively (`ALTER TABLE`); Parquet files are schema-rigid within a file, requiring rewrites when a new column is added.
- **Single file, no server, stdlib.** Matches Milodex's "no services, no daemons" posture. Backup is `cp state/milodex.db state/milodex.db.bak`; sharing is zipping the file. The operator can open the same file in any SQL GUI (DB Browser for SQLite, DBeaver) or drive it from `sqlite3` on the command line without running the app.
- **JSON-in-SQLite hybrid** lets freeform fields (reasoning, risk verdicts) stay inspectable and evolvable without a rigid sub-schema, while structured fields remain typed and queryable. SQLite's JSON functions are available if deeper querying is ever needed.
- **Scales past any plausible Phase 1 or Phase 2 volume.** SQLite's real-world ceiling is far above what a personal trading system will generate; no architectural change is needed until it stops being a personal trading system.
- **Format responsibility stays clean.** Parquet continues to own immutable bulk bar data. Small JSON files continue to own tiny mutable state (kill switch, per-strategy state). SQLite owns the event logs. Three formats, each for the job that fits it.
