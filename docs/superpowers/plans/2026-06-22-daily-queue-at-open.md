# Daily Queue-at-Open (D-1 Option A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Tasks are phase-scoped** (Phase N / Task M); execute phases in order, tasks within a phase in order.

**Goal:** Let daily (1D) strategies execute by locking in a signal at close, persisting an inert/expiring intent, and resubmitting it at the next open through the full 17-check risk battery.

**Architecture:** Split the single post-close decide-and-submit phase into two phases bridged by a durable, append-only-then-lifecycle `queued_intents` table. A queued intent is a proposal, never a pre-approved order: the at-open drain re-runs `evaluate()` for fresh sizing+signal and re-enters `submit_paper` so all 17 checks re-run. Manual-launch only (ADR-0012-clean; distinct from D-3). Conservative fail-closed drop for halt/async-fill ambiguity; dropped exits raise an operator alert.

**Tech Stack:** Python 3.11, SQLite event store (ADR 0011), pytest (`.venv/Scripts/python.exe -m pytest`), ruff. Governance: ADR 0057 (write FIRST), per-PR `risk-invariant-reviewer`.

**Authoritative design:** `docs/superpowers/specs/2026-06-22-daily-queue-at-open-design.md` (nine invariants I-1..I-9). **Decision record:** `docs/adr/0057-daily-execution-queue-at-open.md`.

**Green baseline to regress against (M0 close, `625d46e`):** `3294 passed, 1 skipped, 4 xfailed`. The lone skip is the design-system-showcase quarantine.

---

## Plan Contracts & Reconciliation (AUTHORITATIVE — read before any task)

The phase sections below were drafted in parallel; where any section's text disagrees
with a contract below, **this section wins**. A cross-section consistency pass found these
and they are resolved here.

1. **Broker tradability DROP helper — canonical API.** The helper lives at
   `milodex.runner.drain_policy.tradable_drop_decision(broker: BrokerClient, symbol: str) -> TradableDecision`
   (frozen dataclass `TradableDecision(drop: bool, reason: str, detail: str)`) in the **new**
   file `src/milodex/runner/drain_policy.py` (create `src/milodex/runner/__init__.py` and
   `tests/milodex/runner/__init__.py`). The Phase 6 runner drain imports
   `from milodex.runner.drain_policy import tradable_drop_decision`, calls it with
   `intent.symbol`, and branches on `decision.drop`. **There is NO
   `milodex.broker.tradability.intent_is_tradable`** — delete any such reference; that module
   does not exist and must not be created. `tradable_drop_decision` returns `drop=True` when
   the symbol is not clearly tradable, status is unknown, OR the broker read raises (wrapped so
   it never propagates into the runner loop).

2. **Schema-version pins — TWO migrations, 8 sites each.** This plan ships **two** additive
   migrations: `016_queued_intents.sql` (Phase 1) and `017_operator_alerts.sql` (Phase 7).
   Both keep `MIN_COMPATIBLE_SCHEMA_VERSION` at **12** (`event_store.py:401`;
   `test_event_store.py:47` `== 12` pin unchanged). There are **8** hardcoded
   `schema_version == 15` assertions, not 1 — Phase 1 (016) bumps ALL of them `15 → 16`;
   Phase 7 (017) bumps the SAME sites `16 → 17`:
   `tests/milodex/core/test_event_store.py:56,83,1349,1466`,
   `tests/milodex/core/test_experiment_registry.py:195`,
   `tests/milodex/core/test_migrations.py:167,247,313`. **Re-grep `schema_version ==` at
   implementation time** in case more were added; `test_concurrency.py:109` is dynamic (no
   edit). Each migration phase owns an explicit, enumerated pin-update step.

3. **`EventStore.get_queued_intent(self, intent_id: int) -> QueuedIntentEvent | None`** is
   defined in **Phase 1** alongside the quad (single `SELECT * FROM queued_intents WHERE id=?`
   → `_queued_intent_from_row`). It is a test/diagnostic single-row read, NOT on the
   drain-authority path, so it does not weaken the `get_active_queued_intents` sole-authority
   contract. Phases 6/7 consume it; they do not define it.

4. **`_append_queued_intent(store, *, idempotency_key, **overrides) -> int`** shared test
   helper is defined in **Phase 1** in `tests/milodex/core/conftest.py`, constructing a
   `QueuedIntentEvent` with the exact shared-contract field names. Phases 3/5/6 **import** it;
   they do not redefine it.

5. **Idempotency-CAS suppressed result.** Do **not** add an `ExecutionStatus.SUPPRESSED`
   member. A CAS race-loss (rowcount 0) reuses `status=BLOCKED` + `reason_code='idempotency_suppressed'`
   (mirrors the existing `_declined_for_serialization` precedent). Two acceptance criteria are
   mandatory: (a) the suppressed branch **returns BEFORE `_maybe_activate_kill_switch`**
   (`service.py:315`) and records an explanation row with that reason_code — a benign race-loss
   must never trip the kill switch; (b) an explicit test asserts a suppressed result does not
   flip the kill switch. The Phase 3 exactly-one-submit test asserts via the **broker
   submit-count** (and optionally the durable trail `event_store.list_execution_attempts()`,
   `event_store.py:830`); it does **NOT** call `recent_execution_attempts()` — no such method
   exists.

6. **config_hash guard (I-7) plumbing.** The guard lives inside `get_active_queued_intents`
   (no signature change): it reads `row.strategy_config_path` **per-row**, recomputes the hash
   via a **lazy import** of the existing `compute_config_hash` in `strategies/loader.py` (~429)
   wrapped as `compute_config_hash_or_none` (avoids the event_store↔strategies import cycle),
   and **drops** on None-or-mismatch, AFTER the base status/expires filter and BEFORE
   returning, on BOTH the running-session and `controlled_stop` arms. The hash is computed over
   **normalized** content so CRLF/format-only churn does not false-drop.

7. **`get_active_queued_intents` is built in layers (sequential, same method).** Phase 1 ships
   the base `status='queued' AND datetime(expires_at) > :now` filter. **Phase 5** layers the
   clean-exit fence (`session_id == running_session_id` OR originating
   `strategy_runs.exit_reason == 'controlled_stop'` — literal equality, not `IS NOT NULL`) and
   the config_hash guard onto the same method. The two phases touch this method **sequentially**,
   never concurrently. It remains **THE SOLE drain authority**.

8. **Required parity / authority tests (close the consistency-flagged coverage gaps):**
   (a) **Phase 6** ships a contract test pinning `idempotency_key == f"{strategy_id}|{trading_session}|{side}|{symbol}"` byte-for-byte — a mismatch between the runner-produced key and the
   CAS/UNIQUE constraint silently defeats dedup; (b) **Phase 5** ships the fence/guard test
   whose id the Phase 7 D-6 Boundary row references (proves `get_active_queued_intents` is the
   sole authority after layering); (c) **Phase 1** either asserts the presence of
   `idx_queued_intents_status_expires` / `idx_queued_intents_strategy_status` or notes them as
   deliberately untested.

---

## Phase 0: Governance gate (before any code)

### Task 0.1 — Place and review ADR 0057

- [ ] Confirm `docs/adr/0057-daily-execution-queue-at-open.md` exists and records D-1 = Option A (decision, the six founder criteria, the conservative fail-closed-drop scope, the manual-launch / ADR-0012-clean assumption, and pointers to the spec's nine invariants).
- [ ] **Doctrine gate:** no PR touching `_check_market_open` or `_check_data_staleness` may land before this ADR. Every sacred-path diff (risk/, execution/, promotion/, runner) gets a `risk-invariant-reviewer` (Opus) pass.



---

# Phase 1: Data layer — queued_intents durable state (core/)

## Section: Data layer — `queued_intents` durable state (core/)

Persistence substrate for queue-at-open. A daily runner persists a lock-in-confirmed intent at the close cycle (Phase-1, separate section); at the next session open a runner drains it through the risk battery and submits (Phase-3, separate section). This section builds ONLY the durable-state floor: migration 016, the `QueuedIntentEvent` dataclass, and the five-method store API. No runner/risk/execution wiring here.

**Sacred-adjacent (core/ durable state).** `get_active_queued_intents` is the SOLE drain authority and bakes the expiry + clean-handoff fences into SQL; `mark_queued_intent_consumed` is the single-statement CAS that gates broker submit. Both get a risk-invariant-reviewer pass.

**Shared-contract anchors (do not invent variants):**
- 016 is next (015 is highest shipped). Additive table, no existing reader → `MIN_COMPATIBLE_SCHEMA_VERSION` stays **12** (event_store.py:401 — do NOT touch).
- `idempotency_key = f"{strategy_id}|{trading_session}|{side}|{symbol}"`, UNIQUE.
- Clean-exit fence (I-4): drainable iff `session_id == running_session_id` OR originating `strategy_runs.exit_reason == 'controlled_stop'` (literal string equality — NOT `IS NOT NULL`). `interrupted`/`crashed`/`kill_switch`/`orphan_recovered`/NULL → DROP.
- CAS contract: `UPDATE queued_intents SET status='consumed', consumed_at=?, consumed_by=? WHERE idempotency_key=? AND status='queued'`; caller proceeds to submit only if `rowcount == 1`.

**Grounding (read before starting):** migration style `src/milodex/core/migrations/015_experiment_registry.sql`; the experiment_registry quad in `event_store.py` (`append_experiment` :1957, `_experiment_from_row` :2622, helpers `_dt`/`_parse_datetime`/`_dump_json`/`_load_json` :2769-2782); migration discovery `_load_migrations` :2389-2395 (glob-sorted by numeric `*.sql` prefix) and `_apply_migrations` :2320; `strategy_runs(session_id NOT NULL, exit_reason)` DDL at `migrations/001_initial.sql:59-67`; test conventions in `tests/milodex/core/test_experiment_registry.py`.

**One divergence from the 015 template — do NOT copy the append-only guard.** experiment_registry's `test_no_delete_or_in_place_mutate_path_in_source` (test_experiment_registry.py:198-213) asserts the source contains NO `UPDATE`/`DELETE` against its table. `queued_intents` is a *lifecycle* table — `mark_*` methods legitimately `UPDATE queued_intents`. Do not write an analogous "no UPDATE" grep guard for it; the status-transition methods are the design.

Commands: `.venv/Scripts/python.exe -m pytest -q <path>` ; `.venv/Scripts/python.exe -m ruff check src/ tests/`. Green baseline: 3294 passed, 1 skipped, 4 xfailed.

---

### Task 1 — Migration `016_queued_intents.sql` (DDL + schema bump to 16)

**1a. Write failing test** — `tests/milodex/core/test_queued_intents.py` (new file). Pin schema version 16 and table existence:

```python
"""Tests for the queued-intents lifecycle store (queue-at-open, migration 016)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from milodex.core.event_store import EventStore, QueuedIntentEvent


def test_schema_version_is_16_after_construction(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.schema_version == 16


def test_queued_intents_table_exists(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert "queued_intents" in store.list_table_names()
```

**1b. Run-fail** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intents.py` → ImportError (`QueuedIntentEvent` absent) / version 15.

**1c. Implement** — create `src/milodex/core/migrations/016_queued_intents.sql`:

```sql
-- Queued intents (queue-at-open). A daily runner that confirms a close-bar
-- lock-in while the next session's open is in the future persists the intended
-- order here instead of submitting immediately; at the next session open a
-- runner drains it through the full risk battery and submits.
--
-- Unlike experiment_registry / promotions this table is NOT append-only — it is
-- a lifecycle table. A row moves queued -> consumed | expired | obsolete via the
-- mark_* methods. The drain CAS (mark_queued_intent_consumed) is the single
-- statement that gates a broker submit: exactly one process can flip a 'queued'
-- row to 'consumed', so a duplicate drain is impossible by construction.
--
-- idempotency_key = "{strategy_id}|{trading_session}|{side}|{symbol}" (UNIQUE):
-- one queued intent per strategy per session per side per symbol. A re-persist
-- of the same logical intent collides on the UNIQUE constraint rather than
-- double-queuing.
--
-- Additive only: creates one new table no existing code reads, so the minimum
-- compatible schema version is unchanged (per migration 007's
-- append-never-rewrite principle).

CREATE TABLE queued_intents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    strategy_id TEXT,
    strategy_config_path TEXT,
    config_hash TEXT,
    session_id TEXT,
    trading_session TEXT,
    locked_in_bar_timestamp TEXT,
    symbol TEXT,
    side TEXT,
    intent_class TEXT,
    notional_pct REAL,
    expected_stage TEXT,
    expected_max_positions INTEGER,
    expected_max_position_pct REAL,
    expected_daily_loss_cap_pct REAL,
    intent_payload_json TEXT,
    reasoning_json TEXT,
    created_at TEXT,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    consumed_at TEXT,
    consumed_by TEXT
);

CREATE INDEX idx_queued_intents_strategy_status
    ON queued_intents(strategy_id, status);

CREATE INDEX idx_queued_intents_status_expires
    ON queued_intents(status, expires_at);
```

(No code change needed for discovery — `_load_migrations` (event_store.py:2389) globs `*.sql` and parses the `016` prefix; the schema bump to 16 is automatic via the version write in `_apply_migrations`.)

**1d. Run-pass** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intents.py::test_schema_version_is_16_after_construction tests/milodex/core/test_queued_intents.py::test_queued_intents_table_exists` → 2 passed.

**1e. Update the 015 sibling assertion + commit.** experiment_registry's `test_schema_version_is_15_after_construction` (test_experiment_registry.py:193-195) now sees 16. Change its assertion to `== 16`. (Grep the suite for any other `schema_version == 15` literal and bump.) Run `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_experiment_registry.py` → green. Commit: `feat(core): add queued_intents migration 016 (queue-at-open durable state)`.

---

### Task 2 — `QueuedIntentEvent` frozen dataclass + `_queued_intent_from_row`

**2a. Write failing test** — append to `test_queued_intents.py` a builder + roundtrip (mirrors `_event` / `test_append_then_get_roundtrips_all_fields`):

```python
def _intent(idempotency_key: str = "rsi2.v1|2026-06-23|buy|SPY", **overrides) -> QueuedIntentEvent:
    fields: dict[str, object] = {
        "idempotency_key": idempotency_key,
        "strategy_id": "rsi2.v1",
        "strategy_config_path": "configs/rsi2.yaml",
        "config_hash": "c" * 64,
        "session_id": "sess-A",
        "trading_session": "2026-06-23",
        "locked_in_bar_timestamp": "2026-06-22T20:00:00+00:00",
        "symbol": "SPY",
        "side": "buy",
        "intent_class": "entry",
        "notional_pct": 0.1,
        "expected_stage": "paper",
        "expected_max_positions": 3,
        "expected_max_position_pct": 0.2,
        "expected_daily_loss_cap_pct": 0.05,
        "intent_payload_json": {"qty": 4, "limit_price": 500.0},
        "reasoning_json": {"signal": "rsi<10"},
        "created_at": datetime(2026, 6, 22, 20, 0, tzinfo=UTC),
        "expires_at": datetime(2026, 6, 23, 19, 0, tzinfo=UTC),
    }
    fields.update(overrides)
    return QueuedIntentEvent(**fields)  # type: ignore[arg-type]


def test_append_then_active_roundtrips_all_fields(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    new_id = store.append_queued_intent(_intent())
    assert isinstance(new_id, int)

    active = store.get_active_queued_intents(
        "rsi2.v1",
        now=datetime(2026, 6, 23, 14, 0, tzinfo=UTC),
        running_session_id="sess-A",
    )
    assert len(active) == 1
    e = active[0]
    assert e.id == new_id
    assert e.idempotency_key == "rsi2.v1|2026-06-23|buy|SPY"
    assert e.symbol == "SPY"
    assert e.side == "buy"
    assert e.intent_class == "entry"
    assert e.notional_pct == 0.1
    assert e.expected_max_positions == 3
    assert e.expected_daily_loss_cap_pct == 0.05
    assert e.intent_payload_json == {"qty": 4, "limit_price": 500.0}
    assert e.reasoning_json == {"signal": "rsi<10"}
    assert e.status == "queued"
    assert e.consumed_at is None and e.consumed_by is None


def test_unique_idempotency_key_rejects_duplicate(tmp_path):
    import sqlite3

    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    with pytest.raises(sqlite3.IntegrityError):
        store.append_queued_intent(_intent())
```

**2b. Run-fail** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intents.py::test_append_then_active_roundtrips_all_fields` → ImportError / AttributeError.

**2c. Implement** — in `event_store.py`, add the dataclass next to `ExperimentEvent` (after line ~199). JSON columns carried as `dict | None` (de/serialized via `_dump_json`/`_load_json`, mirroring `ExperimentEvent.evidence_json`); timestamps as `datetime | None`; the four `expected_*` governance-snapshot fields plus the lifecycle trio default so a fresh intent is built positionally-light:

```python
@dataclass(frozen=True)
class QueuedIntentEvent:
    """A daily intent locked in at close, awaiting drain at the next session open.

    queue-at-open durable state. Persisted by the runner at the lock-in-confirmed
    cycle when the next session's open is still in the future; drained — through
    the full risk battery — at that open. NOT append-only: a row's ``status``
    transitions ``queued`` -> ``consumed`` | ``expired`` | ``obsolete`` via the
    ``mark_*`` store methods.

    ``idempotency_key`` (UNIQUE) = ``f"{strategy_id}|{trading_session}|{side}|{symbol}"``.
    The ``expected_*`` fields snapshot the governance posture at lock-in time so a
    drift between persist and drain (stage change, cap edit) is detectable.
    ``intent_payload_json`` carries the order intent; ``reasoning_json`` the
    decision reasoning. ``session_id`` is the originating run's session — the
    clean-handoff fence in :meth:`EventStore.get_active_queued_intents` compares
    it against the draining run and falls back to the originating run's
    ``strategy_runs.exit_reason``.
    """

    idempotency_key: str
    strategy_id: str | None = None
    strategy_config_path: str | None = None
    config_hash: str | None = None
    session_id: str | None = None
    trading_session: str | None = None
    locked_in_bar_timestamp: str | None = None
    symbol: str | None = None
    side: str | None = None
    intent_class: str | None = None
    notional_pct: float | None = None
    expected_stage: str | None = None
    expected_max_positions: int | None = None
    expected_max_position_pct: float | None = None
    expected_daily_loss_cap_pct: float | None = None
    intent_payload_json: dict[str, Any] | None = None
    reasoning_json: dict[str, Any] | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    status: str = "queued"
    consumed_at: datetime | None = None
    consumed_by: str | None = None
    id: int | None = None
```

Add `_queued_intent_from_row` next to `_experiment_from_row` (after line ~2636):

```python
def _queued_intent_from_row(row: sqlite3.Row) -> QueuedIntentEvent:
    return QueuedIntentEvent(
        id=int(row["id"]),
        idempotency_key=str(row["idempotency_key"]),
        strategy_id=row["strategy_id"],
        strategy_config_path=row["strategy_config_path"],
        config_hash=row["config_hash"],
        session_id=row["session_id"],
        trading_session=row["trading_session"],
        locked_in_bar_timestamp=row["locked_in_bar_timestamp"],
        symbol=row["symbol"],
        side=row["side"],
        intent_class=row["intent_class"],
        notional_pct=(None if row["notional_pct"] is None else float(row["notional_pct"])),
        expected_stage=row["expected_stage"],
        expected_max_positions=(
            None if row["expected_max_positions"] is None else int(row["expected_max_positions"])
        ),
        expected_max_position_pct=(
            None
            if row["expected_max_position_pct"] is None
            else float(row["expected_max_position_pct"])
        ),
        expected_daily_loss_cap_pct=(
            None
            if row["expected_daily_loss_cap_pct"] is None
            else float(row["expected_daily_loss_cap_pct"])
        ),
        intent_payload_json=(
            None if row["intent_payload_json"] is None else _load_json(row["intent_payload_json"])
        ),
        reasoning_json=(
            None if row["reasoning_json"] is None else _load_json(row["reasoning_json"])
        ),
        created_at=_parse_datetime(row["created_at"]),
        expires_at=_parse_datetime(row["expires_at"]),
        status=str(row["status"]),
        consumed_at=_parse_datetime(row["consumed_at"]),
        consumed_by=row["consumed_by"],
    )
```

(`append_queued_intent` and `get_active_queued_intents` are implemented in Tasks 3–4; 2c only adds the dataclass + row-mapper, so 2b's test stays red until Task 3. Implement `append_queued_intent` here too so this task closes — see Task 3's INSERT; move it to 2c if executing strictly TDD, but keep the active/CAS/fence logic in 3–4.)

**2d. Run-pass** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intents.py -k "roundtrip or duplicate"` → passed.

**2e. Lint + commit** — `.venv/Scripts/python.exe -m ruff check src/ tests/`; commit `feat(core): QueuedIntentEvent dataclass + row mapper`.

---

### Task 3 — `append_queued_intent` (INSERT, returns id)

**3a. Write failing test** — covered by 2a's `test_append_then_active_roundtrips_all_fields` and `test_unique_idempotency_key_rejects_duplicate`; add a JSON-null roundtrip:

```python
def test_json_and_nullable_fields_roundtrip(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(
        _intent("k|s|buy|QQQ", symbol="QQQ", intent_payload_json=None, reasoning_json=None)
    )
    active = store.get_active_queued_intents(
        "rsi2.v1", now=datetime(2026, 6, 23, 14, 0, tzinfo=UTC), running_session_id="sess-A"
    )
    match = [e for e in active if e.symbol == "QQQ"][0]
    assert match.intent_payload_json is None
    assert match.reasoning_json is None
```

**3b. Run-fail** → AttributeError (`append_queued_intent` absent).

**3c. Implement** — add to `EventStore` immediately after `update_experiment` (event_store.py:~2073), mirroring `append_experiment`'s shape (`_connect`, `_dt` for timestamps, `_dump_json` for JSON, `connection.commit()`, `int(cursor.lastrowid)`):

```python
def append_queued_intent(self, event: QueuedIntentEvent) -> int:
    """Persist a queued intent and return its autoincrement id.

    queue-at-open: a daily runner that confirms a close-bar lock-in while the
    next session's open is in the future calls this instead of submitting.
    The ``idempotency_key`` UNIQUE constraint makes a re-persist of the same
    logical intent raise ``sqlite3.IntegrityError`` rather than double-queue.
    """
    with self._connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO queued_intents (
                idempotency_key, strategy_id, strategy_config_path, config_hash,
                session_id, trading_session, locked_in_bar_timestamp, symbol, side,
                intent_class, notional_pct, expected_stage, expected_max_positions,
                expected_max_position_pct, expected_daily_loss_cap_pct,
                intent_payload_json, reasoning_json, created_at, expires_at, status,
                consumed_at, consumed_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.idempotency_key,
                event.strategy_id,
                event.strategy_config_path,
                event.config_hash,
                event.session_id,
                event.trading_session,
                event.locked_in_bar_timestamp,
                event.symbol,
                event.side,
                event.intent_class,
                event.notional_pct,
                event.expected_stage,
                event.expected_max_positions,
                event.expected_max_position_pct,
                event.expected_daily_loss_cap_pct,
                None if event.intent_payload_json is None else _dump_json(event.intent_payload_json),
                None if event.reasoning_json is None else _dump_json(event.reasoning_json),
                _dt(event.created_at),
                _dt(event.expires_at),
                event.status,
                _dt(event.consumed_at),
                event.consumed_by,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
```

**3d. Run-pass** → `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intents.py` (active-path tests still red pending Task 4; the append + IntegrityError + JSON-null tests pass).

**3e. Commit** — `feat(core): append_queued_intent`.

---

### Task 4 — `get_active_queued_intents` (SOLE drain authority: expiry + clean-handoff in SQL)

This is the load-bearing read. It MUST return ONLY rows where, in a single query: `status='queued'` AND `datetime(expires_at) > now` AND clean-handoff holds (`session_id == running_session_id` OR the originating run's `strategy_runs.exit_reason == 'controlled_stop'`). The clean-handoff check JOINs/sub-queries `strategy_runs.exit_reason` keyed on `queued_intents.session_id = strategy_runs.session_id`.

**4a. Write failing tests** — the fence matrix:

```python
def _seed_run(db_path, session_id: str, exit_reason):
    import sqlite3

    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT INTO strategy_runs (session_id, strategy_id, started_at, ended_at, "
            "exit_reason, metadata_json) VALUES (?, 'rsi2.v1', ?, ?, ?, '{}')",
            (session_id, "2026-06-22T20:00:00+00:00", "2026-06-22T20:05:00+00:00", exit_reason),
        )
        con.commit()


_NOW = datetime(2026, 6, 23, 14, 0, tzinfo=UTC)


def test_expired_intent_is_not_active(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(expires_at=datetime(2026, 6, 23, 13, 0, tzinfo=UTC)))
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A") == []


def test_same_session_intent_is_active(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(session_id="sess-A"))
    active = store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A")
    assert len(active) == 1


def test_cross_session_controlled_stop_is_active(tmp_path):
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    store.append_queued_intent(_intent(session_id="sess-OLD"))
    _seed_run(db, "sess-OLD", "controlled_stop")
    active = store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-NEW")
    assert len(active) == 1


@pytest.mark.parametrize("exit_reason", ["interrupted", "crashed", "kill_switch", "orphan_recovered", None])
def test_cross_session_dirty_exit_is_dropped(tmp_path, exit_reason):
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    store.append_queued_intent(_intent(session_id="sess-OLD"))
    _seed_run(db, "sess-OLD", exit_reason)
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-NEW") == []


def test_cross_session_no_run_row_is_dropped(tmp_path):
    """No strategy_runs row for the originating session => not controlled_stop => DROP."""
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(session_id="sess-GHOST"))
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-NEW") == []


def test_consumed_intent_is_not_active(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    store.mark_queued_intent_consumed(
        "rsi2.v1|2026-06-23|buy|SPY", consumed_by="sess-A", consumed_at=_NOW
    )
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A") == []


def test_scoped_to_strategy_id(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent(strategy_id="other.v1", idempotency_key="other.v1|s|buy|SPY"))
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A") == []
```

**4b. Run-fail** → AttributeError. (`test_consumed_intent_is_not_active` also needs Task 5's CAS — run it in Task 5; here run the fence subset with `-k`.)

**4c. Implement** — add after `append_queued_intent`. The clean-handoff fence is a correlated `EXISTS` sub-query on `strategy_runs`; `now` is bound as an ISO string and compared with SQLite's `datetime()` so the lexical comparison is calendar-correct. `'controlled_stop'` is a **literal string equality** (NOT `exit_reason IS NOT NULL`):

```python
def get_active_queued_intents(
    self,
    strategy_id: str,
    *,
    now: datetime,
    running_session_id: str,
) -> list[QueuedIntentEvent]:
    """Return the drainable queued intents for ``strategy_id`` — the SOLE drain
    authority for queue-at-open.

    A row is drainable iff ALL of:
      * ``status = 'queued'`` (not already consumed / expired / obsolete),
      * not expired: ``datetime(expires_at) > datetime(now)``,
      * clean-handoff holds: the intent was queued by the currently running
        session (``session_id = running_session_id``) OR its originating run
        shut down cleanly (``strategy_runs.exit_reason = 'controlled_stop'``).

    The clean-handoff fence (I-4) is enforced HERE, in SQL, so no caller can
    drain across an unclean process boundary: an ``interrupted`` / ``crashed`` /
    ``kill_switch`` / ``orphan_recovered`` / NULL exit_reason, or a session with
    NO ``strategy_runs`` row at all, is dropped. ``controlled_stop`` is matched
    by literal string equality — NOT ``IS NOT NULL`` — so only a deliberate,
    cooperative stop hands its queued intent to a successor.
    """
    sql = """
        SELECT qi.* FROM queued_intents AS qi
        WHERE qi.strategy_id = ?
          AND qi.status = 'queued'
          AND datetime(qi.expires_at) > datetime(?)
          AND (
                qi.session_id = ?
                OR EXISTS (
                    SELECT 1 FROM strategy_runs AS sr
                    WHERE sr.session_id = qi.session_id
                      AND sr.exit_reason = 'controlled_stop'
                )
              )
        ORDER BY qi.id ASC
    """
    with self._connect() as connection:
        rows = connection.execute(
            sql, (strategy_id, _dt(now), running_session_id)
        ).fetchall()
    return [_queued_intent_from_row(row) for row in rows]
```

Note: `_dt(now)` requires `now` to be a `datetime` (raises if a naive/None slips in — desirable; the runner always passes an aware UTC `now`). Document at the call site (Phase-3 section) that `now` and `expires_at` must share tz convention (both UTC ISO) so the `datetime()` lexical compare is sound.

**4d. Run-pass** → `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intents.py -k "active or expired or session or dirty or run_row or scoped"` → all green (except `test_consumed_intent_is_not_active`, deferred to Task 5).

**4e. Commit** — `feat(core): get_active_queued_intents drain authority (expiry + clean-handoff fence)`.

**4f. Dispatch risk-invariant-reviewer (Opus) on this diff.** Attack surface to hand the reviewer: (1) can a dirty-exit or no-run-row intent ever pass the fence (probe the `EXISTS` vs a naive `LEFT JOIN ... exit_reason IS NOT NULL`); (2) tz/format skew between `expires_at` and `now` defeating `datetime()` (e.g. one offset-aware, one naive); (3) is `'controlled_stop'` the only clean reason, or should `orphan_recovered` be admitted (contract says DROP — confirm); (4) does `status='queued'` plus the Task-5 CAS fully exclude double-drain across two concurrently-launched successor runners. Reviewer must open `event_store.py`, `migrations/016`, and `migrations/001_initial.sql` and spend half its effort trying to break the fence.

---

### Task 5 — `mark_queued_intent_consumed` (atomic CAS, returns rowcount) + `mark_*_expired` / `mark_*_obsolete`

**5a. Write failing tests** — CAS rowcount semantics + the two simple status setters:

```python
def test_consume_cas_returns_one_then_zero(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    key = "rsi2.v1|2026-06-23|buy|SPY"

    first = store.mark_queued_intent_consumed(key, consumed_by="sess-A", consumed_at=_NOW)
    assert first == 1
    # Second CAS on the same (now non-'queued') row loses: rowcount 0.
    second = store.mark_queued_intent_consumed(key, consumed_by="sess-B", consumed_at=_NOW)
    assert second == 0


def test_consume_sets_audit_columns(tmp_path):
    import sqlite3

    store = EventStore(tmp_path / "milodex.db")
    store.append_queued_intent(_intent())
    store.mark_queued_intent_consumed(
        "rsi2.v1|2026-06-23|buy|SPY", consumed_by="sess-A", consumed_at=_NOW
    )
    with sqlite3.connect(tmp_path / "milodex.db") as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM queued_intents").fetchone()
    assert row["status"] == "consumed"
    assert row["consumed_by"] == "sess-A"
    assert row["consumed_at"] == _NOW.isoformat()


def test_consume_unknown_key_returns_zero(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    assert store.mark_queued_intent_consumed("nope", consumed_by="x", consumed_at=_NOW) == 0


def test_mark_expired_and_obsolete(tmp_path):
    db = tmp_path / "milodex.db"
    store = EventStore(db)
    eid = store.append_queued_intent(_intent())
    store.mark_queued_intent_expired(eid)
    assert store.get_active_queued_intents("rsi2.v1", now=_NOW, running_session_id="sess-A") == []

    eid2 = store.append_queued_intent(_intent("rsi2.v1|2026-06-23|buy|IWM", symbol="IWM"))
    store.mark_queued_intent_obsolete(eid2)
    import sqlite3

    with sqlite3.connect(db) as con:
        rows = dict(con.execute("SELECT status, COUNT(*) FROM queued_intents GROUP BY status"))
    assert rows == {"expired": 1, "obsolete": 1}
```

Plus re-enable `test_consumed_intent_is_not_active` from Task 4.

**5b. Run-fail** → AttributeError.

**5c. Implement** — the CAS is a SINGLE `UPDATE ... WHERE idempotency_key=? AND status='queued'`; `cursor.rowcount` is the gate (1 = this caller won the drain and may submit; 0 = lost / already non-queued). `mark_*_expired` / `mark_*_obsolete` key on `id` and are unconditional status setters (no CAS — they're operator/maintenance transitions, not the submit gate):

```python
def mark_queued_intent_consumed(
    self, idempotency_key: str, *, consumed_by: str, consumed_at: datetime
) -> int:
    """Atomically claim a queued intent for submit. Returns rows updated (0 or 1).

    THE drain gate. A single-statement compare-and-swap: flip ``status`` to
    ``'consumed'`` only if it is still ``'queued'``. The caller proceeds to the
    broker submit ONLY when this returns 1 — a return of 0 means another process
    already consumed (or expired/obsoleted) the row, so this caller must NOT
    submit. Because it is one UPDATE guarded by ``status = 'queued'``, two
    concurrent successor runners cannot both win: SQLite serializes the writes
    and exactly one sees rowcount 1.
    """
    with self._connect() as connection:
        cursor = connection.execute(
            """
            UPDATE queued_intents
            SET status = 'consumed', consumed_at = ?, consumed_by = ?
            WHERE idempotency_key = ? AND status = 'queued'
            """,
            (_dt(consumed_at), consumed_by, idempotency_key),
        )
        connection.commit()
        return cursor.rowcount


def mark_queued_intent_expired(self, intent_id: int) -> None:
    """Mark a queued intent expired (its open window passed undrained)."""
    with self._connect() as connection:
        connection.execute(
            "UPDATE queued_intents SET status = 'expired' WHERE id = ?",
            (intent_id,),
        )
        connection.commit()


def mark_queued_intent_obsolete(self, intent_id: int) -> None:
    """Mark a queued intent obsolete (superseded before drain — e.g. a newer
    lock-in for the same strategy/session/side/symbol, or operator override)."""
    with self._connect() as connection:
        connection.execute(
            "UPDATE queued_intents SET status = 'obsolete' WHERE id = ?",
            (intent_id,),
        )
        connection.commit()
```

**5d. Run-pass** → `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intents.py` → all green.

**5e. Lint + full-suite regression** — `.venv/Scripts/python.exe -m ruff check src/ tests/`; then `.venv/Scripts/python.exe -m pytest -q` and confirm the count reads off the actual output (expected: baseline + new tests, still `1 skipped`, `4 xfailed`, 0 failed). Commit `feat(core): queued-intent consume CAS + expire/obsolete transitions`.

**5f. Dispatch risk-invariant-reviewer (Opus) on this diff.** Probes: (1) is the CAS truly single-statement and `rowcount`-gated — could any refactor read-then-write and reintroduce a TOCTOU window; (2) does `mark_queued_intent_consumed` ever return >1 (it can't — UNIQUE key, but confirm the reviewer reasons it through); (3) is `commit()` inside the same connection as the UPDATE so the claim is durable before the caller acts on rowcount; (4) confirm `mark_*_expired`/`mark_*_obsolete` are deliberately NOT CAS-guarded and that's acceptable (they are not submit gates). Reviewer opens `event_store.py` and `migrations/016`, half effort on breaking the CAS exclusivity.



---

# Phase 2: CONFIG + TEMPO-AWARE STALENESS

## CONFIG + TEMPO-AWARE STALENESS

Two surgical changes, sequenced so the field exists before the risk check reads it:

1. **`StrategyExecutionConfig` gains `bar_size: str = ""`** and the loader reads `strategy.tempo.bar_size` (None-safe — legacy YAML omits `tempo`).
2. **`_check_data_staleness` becomes tempo-aware**: a one-session budget (`DAILY_STALENESS_BUDGET_SECONDS`) only when `context.strategy_config.bar_size == '1D'`, else the global `max_data_staleness_seconds` (300s). Read via `getattr(..., 'bar_size', None)` so `strategy_config is None` or any non-`1D` resolves to 300s.

This is a SACRED risk-layer change. The widened budget MUST apply ONLY to the daily (`1D`) path; the intraday path keeps the unmodified 300s. Every existing intraday/None-config staleness test must stay green unchanged — that is the regression fence that proves the loosening is daily-only. Each task that touches `src/milodex/execution/config.py` (feeds the risk evaluator) or `src/milodex/risk/evaluator.py` ends with a risk-invariant-reviewer dispatch.

Grounding (re-read at implementation time):
- `StrategyExecutionConfig` frozen dataclass: `src/milodex/execution/config.py:17-37`. Trailing default-valued fields today are `family: str = ""` (`config.py:36`) and `disable_conditions_additional: tuple[str, ...] = ()` (`config.py:37`).
- Loader: `src/milodex/execution/config.py:40-63`. The `strategy` mapping is fetched at `config.py:43`; `tempo` is a sibling sub-mapping of `risk` (`config.py:44`).
- `_check_data_staleness`: `src/milodex/risk/evaluator.py:440-465`; `max_age` is built at `evaluator.py:457` from `context.risk_defaults.max_data_staleness_seconds`.
- `EvaluationContext.strategy_config: StrategyExecutionConfig | None`: `src/milodex/risk/evaluator.py:51` (it is `None` for operator manual trades and legacy callers).
- Global default: `max_data_staleness_seconds: 300` (`configs/risk_defaults.yaml:63`), surfaced as `RiskDefaults.max_data_staleness_seconds` (`src/milodex/risk/config.py:82`).
- Loader is directly tested in `tests/milodex/risk/test_disable_conditions.py` (`load_strategy_execution_config` import at line 22; round-trip tests at 331, 356).
- Staleness rule is directly tested in `tests/milodex/risk/test_risk_rules.py`: `make_context(...)` builder (lines 86-190, accepts `strategy_config=`, threads it to `EvaluationContext` at `test_risk_rules.py:181`); boundary tests `test_data_staleness_passes_exactly_at_max_age` (1438) and `test_data_staleness_fails_just_over_max_age` (1479), both monkeypatching `milodex.risk.evaluator.datetime` to a `_FrozenDateTime` to engineer exact bar age.

Commands (each task): test `.venv/Scripts/python.exe -m pytest -q <path>`; lint `.venv/Scripts/python.exe -m ruff check src/ tests/`. Green baseline before this section: **3294 passed, 1 skipped, 4 xfailed**.

---

### Task 1 — `StrategyExecutionConfig.bar_size` field + loader reads `strategy.tempo.bar_size`

**Step 1 — Write the failing test.** Append two loader tests to `tests/milodex/risk/test_disable_conditions.py` (it already imports `load_strategy_execution_config` and `StrategyExecutionConfig` at line 22). Place after `test_execution_config_defaults_stay_lenient` (ends line 376):

```python
def test_loader_reads_bar_size_from_tempo(tmp_path):
    """Loader surfaces strategy.tempo.bar_size onto the execution config."""
    path = tmp_path / "daily.yaml"
    path.write_text(
        """
strategy:
  name: "daily_demo"
  enabled: true
  stage: "paper"
  family: "momentum"
  tempo:
    bar_size: "1D"
  risk:
    max_position_pct: 0.10
    max_positions: 2
    daily_loss_cap_pct: 0.02
""".strip(),
        encoding="utf-8",
    )
    config = load_strategy_execution_config(path)
    assert config.bar_size == "1D"


def test_loader_bar_size_defaults_empty_when_tempo_absent(tmp_path):
    """Legacy YAML with no tempo block yields bar_size == '' (None-safe)."""
    path = tmp_path / "legacy.yaml"
    path.write_text(
        """
strategy:
  name: "legacy"
  enabled: true
  stage: "paper"
  risk:
    max_position_pct: 0.10
    max_positions: 2
    daily_loss_cap_pct: 0.02
""".strip(),
        encoding="utf-8",
    )
    config = load_strategy_execution_config(path)
    assert config.bar_size == ""
```

**Step 2 — Run, expect failure.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/risk/test_disable_conditions.py::test_loader_reads_bar_size_from_tempo tests/milodex/risk/test_disable_conditions.py::test_loader_bar_size_defaults_empty_when_tempo_absent` — fails with `AttributeError: 'StrategyExecutionConfig' object has no attribute 'bar_size'` (and a `TypeError` if construction is reached first). Confirms the field is genuinely absent.

**Step 3 — Implement.** In `src/milodex/execution/config.py`, add the field as the last default-valued field of the frozen dataclass (after `disable_conditions_additional` at `config.py:37`):

```python
    family: str = ""
    disable_conditions_additional: tuple[str, ...] = ()
    bar_size: str = ""
```

Then in `load_strategy_execution_config` (`config.py:40-63`), read `tempo` None-safely and pass `bar_size`. Add, immediately before the `return StrategyExecutionConfig(` at `config.py:53`:

```python
    raw_tempo = strategy.get("tempo")
    tempo = raw_tempo if isinstance(raw_tempo, dict) else {}
    bar_size = str(tempo.get("bar_size") or "")
```

and add the kwarg to the constructor call (after `disable_conditions_additional=additional,` at `config.py:62`):

```python
        bar_size=bar_size,
```

Do NOT use `_mapping(strategy.get("tempo"), ...)` — that raises on a missing `tempo`, which would break every legacy config. The `isinstance(..., dict)` guard is the None-safe path. `str(... or "")` collapses both a missing key and an explicit `null` to `""`.

**Step 4 — Run, expect pass.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/risk/test_disable_conditions.py` — both new tests pass AND the pre-existing `test_existing_strategy_configs_load_with_family_and_additional` (line 331, loads every `configs/*.yaml`) stays green, proving no real config regressed. Then lint: `.venv/Scripts/python.exe -m ruff check src/ tests/`.

**Step 5 — Risk-review + commit.** Dispatch risk-invariant-reviewer (Opus) on this diff (config.py feeds the sacred risk evaluator). Then commit: `feat(execution-config): add bar_size from strategy.tempo to StrategyExecutionConfig`.

---

### Task 2 — `_check_data_staleness` tempo-aware budget (daily-only loosening)

**Step 1 — Write the failing tests.** Append to `tests/milodex/risk/test_risk_rules.py`, after `test_data_staleness_fails_just_over_max_age` (ends line 1517). These reuse the in-file `make_context`, `check_result`, `StrategyExecutionConfig`, and the `_FrozenDateTime` monkeypatch idiom already established at lines 1453/1496.

```python
def _exec_config(bar_size: str) -> StrategyExecutionConfig:
    return StrategyExecutionConfig(
        name="tempo_demo",
        enabled=True,
        stage="paper",
        max_position_pct=0.20,
        max_positions=3,
        daily_loss_cap_pct=0.02,
        path=None,  # type: ignore[arg-type]
        bar_size=bar_size,
    )


def test_data_staleness_daily_config_allows_bar_older_than_300s(monkeypatch):
    """A 1D-tempo config gets the one-session budget: a bar ~1h old
    (well past the global 300s) must still pass."""
    from milodex.risk import evaluator as evaluator_module

    fixed_now = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(evaluator_module, "datetime", _FrozenDateTime)
    bar = Bar(
        timestamp=fixed_now - timedelta(seconds=3600),  # 1h old
        open=100.0, high=101.0, low=99.0, close=100.0, volume=1_000, vwap=100.0,
    )
    decision = RiskEvaluator().evaluate(
        make_context(latest_bar=bar, strategy_config=_exec_config("1D"))
    )
    assert check_result(decision, "data_staleness").passed is True


def test_data_staleness_intraday_config_keeps_300s_budget(monkeypatch):
    """CRITICAL: a non-1D resolved config must NOT inherit the daily budget.
    A bar one microsecond past 300s fails exactly as it does with no config."""
    from milodex.risk import evaluator as evaluator_module

    fixed_now = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(evaluator_module, "datetime", _FrozenDateTime)
    bar = Bar(
        timestamp=fixed_now - timedelta(seconds=300) - timedelta(microseconds=1),
        open=100.0, high=101.0, low=99.0, close=100.0, volume=1_000, vwap=100.0,
    )
    decision = RiskEvaluator().evaluate(
        make_context(latest_bar=bar, strategy_config=_exec_config("5Min"))
    )
    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"


def test_data_staleness_none_config_keeps_300s_budget(monkeypatch):
    """CRITICAL: strategy_config is None (operator manual / legacy caller)
    -> 300s, never the daily budget. Bar just past 300s must fail."""
    from milodex.risk import evaluator as evaluator_module

    fixed_now = datetime(2026, 5, 6, 18, 0, 0, tzinfo=UTC)

    class _FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(evaluator_module, "datetime", _FrozenDateTime)
    bar = Bar(
        timestamp=fixed_now - timedelta(seconds=300) - timedelta(microseconds=1),
        open=100.0, high=101.0, low=99.0, close=100.0, volume=1_000, vwap=100.0,
    )
    decision = RiskEvaluator().evaluate(make_context(latest_bar=bar))  # strategy_config=None
    result = check_result(decision, "data_staleness")
    assert result.passed is False
    assert result.reason_code == "stale_market_data"
```

**Step 2 — Run, expect failure.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/risk/test_risk_rules.py::test_data_staleness_daily_config_allows_bar_older_than_300s` fails (current code caps every path at 300s, so a 1h-old bar is rejected). The two `_keeps_300s_budget` tests PASS already against current code — that is intentional: they are the regression fence that must STAY green after Step 3, proving the loosening didn't leak into the intraday/None paths.

**Step 3 — Implement.** In `src/milodex/risk/evaluator.py`, define the module-level constant near the top of the module (after the imports, before the `EvaluationContext` dataclass at `evaluator.py:35`):

```python
# One trading session of slack for daily (1D) strategies. A daily runner locks
# in on the prior session's close and submits at the next open, so its freshest
# bar is legitimately ~a session old — the global 300s intraday budget would
# false-veto it. Applies ONLY when the resolved strategy config is 1D; every
# other path (intraday, operator manual / None config) keeps the global budget.
DAILY_STALENESS_BUDGET_SECONDS = 23 * 60 * 60  # 23h
```

Then in `_check_data_staleness` (`evaluator.py:440-465`), replace the single `max_age` line at `evaluator.py:457`:

```python
        max_age = timedelta(seconds=context.risk_defaults.max_data_staleness_seconds)
```

with the tempo-aware budget resolution:

```python
        bar_size = getattr(context.strategy_config, "bar_size", None)
        if bar_size == "1D":
            budget_seconds = DAILY_STALENESS_BUDGET_SECONDS
        else:
            budget_seconds = context.risk_defaults.max_data_staleness_seconds
        max_age = timedelta(seconds=budget_seconds)
```

Constraints, non-negotiable:
- Use `getattr(context.strategy_config, "bar_size", None)` — NEVER `context.strategy_config.bar_size` directly (raises `AttributeError` when `strategy_config is None`), and NEVER read tempo off `context.intent` (the intent has no tempo; the resolved config is the sole authority).
- The branch is strictly `== "1D"`. Any other value (`"5Min"`, `""`, `None`) falls to the global budget. Do not normalize/uppercase — `1D` is the canonical literal the loader passes through verbatim.
- Leave the comparison at `evaluator.py:458` (`if age > max_age:`) and the reason code `stale_market_data` untouched. Only the budget magnitude changes; the strict-`>` boundary semantics that `test_data_staleness_passes_exactly_at_max_age` pins stay intact.

**Step 4 — Run, expect pass.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/risk/test_risk_rules.py` — the new daily test now passes, and CRITICALLY the two `_keeps_300s_budget` tests plus the pre-existing `test_data_staleness_passes_exactly_at_max_age` (1438) and `test_data_staleness_fails_just_over_max_age` (1479) all stay green. Then run the broader staleness surface to catch any caller depending on the old uniform 300s: `.venv/Scripts/python.exe -m pytest -q tests/milodex/risk/ tests/milodex/execution/`. Then lint: `.venv/Scripts/python.exe -m ruff check src/ tests/`.

**Step 5 — Risk-review + commit.** Dispatch risk-invariant-reviewer (Opus) on this diff — this is a SACRED risk-check edit. The reviewer must confirm: (a) the loosening reaches ONLY the `bar_size == "1D"` branch; (b) `strategy_config is None` and every non-1D value still resolve to 300s; (c) no read of tempo off the intent; (d) the strict-`>` staleness boundary and `stale_market_data` reason code are unchanged. Then commit: `feat(risk): tempo-aware data-staleness budget — one-session slack for 1D strategies only`.

---

### Section exit check

Run the full suite once: `.venv/Scripts/python.exe -m pytest -q` — expect the baseline plus the 5 new tests, i.e. **3299 passed, 1 skipped, 4 xfailed** (2 loader + 3 staleness). Report the count only off a clean command result.


---

# Phase 3: execution: idempotency CAS in _submit_locked

## Section: Idempotency CAS in execution (`_submit_locked`)

**SACRED PATH (execution/).** Every task in this section ends with a risk-invariant-reviewer dispatch. The CAS sits between risk-allow and the broker call while holding the per-account submit lock; getting the ordering wrong either double-submits or silently drops a deserving order.

**Grounded anchors (re-grep before editing — line numbers drift):**
- `submit_paper` — `src/milodex/execution/service.py:113` (kwargs today: `session_id`, `reasoning`).
- `_submit` — `service.py:148`; per-account lock acquire at `service.py:178`; lock-held call to `_submit_locked` at `service.py:194`; lock-free fast path at `service.py:169`.
- `_submit_locked` — `service.py:304`; risk-allow gate at `service.py:314` (`if not result.risk_decision.allowed:` returns early); the durable outbox `append_execution_attempt` at `service.py:337`; the single `self._broker.submit_order(...)` at `service.py:357`.
- `_record_execution` — `service.py:800` (builds the `ExplanationEvent`; `status` comes from `result.status.value`).
- Fail-closed precedent to mirror for the suppressed result: `_declined_for_serialization` — `service.py:247` (previews read-only, then `replace(preview, status=ExecutionStatus.BLOCKED, risk_decision=<synthetic RiskDecision>, ...)`, then `_record_execution(..., decision_type="submit")`).
- `ExecutionStatus` has no SUPPRESSED member (`execution/models.py:19-26`: PREVIEW/BLOCKED/SUBMITTED/REJECTED/CANCELLED). **Reuse `BLOCKED`** with a distinguishing `reason_code="idempotency_suppressed"` — same shape `_declined_for_serialization` already uses. Do NOT add an enum member.
- CONSUMED FROM the data-layer section: `event_store.mark_queued_intent_consumed(idempotency_key, *, consumed_by, consumed_at) -> int(rowcount)` — the single-statement CAS (`UPDATE queued_intents SET status='consumed', consumed_at=?, consumed_by=? WHERE idempotency_key=? AND status='queued'`). Caller submits to broker **only if rowcount == 1**.

**Threading rule:** `idempotency_key: str | None = None` is an *additive* trailing kwarg, threaded exactly like `session_id`/`reasoning` thread today: `submit_paper -> _submit -> _submit_locked` (and pass-through on both `_submit_locked` call sites in `_submit`: the lock-free `service.py:169` and the lock-held `service.py:194`). `submit_backtest` does NOT gain the kwarg (backtests never queue/drain). When `idempotency_key is None`, behavior is byte-for-byte unchanged — the CAS block is skipped entirely.

---

### Task 1 — Thread `idempotency_key` kwarg end-to-end (no CAS yet)

Pure plumbing. Proves the kwarg reaches `_submit_locked` and that `None` is a transparent no-op.

1. **Write failing test.** Append to `tests/milodex/execution/test_service.py`:
```python
def test_idempotency_key_threads_to_submit_locked_without_changing_behavior(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """An explicit idempotency_key still submits when no CAS row gates it (None-equivalent path off)."""
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )

    seen: dict[str, object] = {}
    original = service._submit_locked

    def _spy(intent, **kwargs):
        seen.update(kwargs)
        return original(intent, **kwargs)

    service._submit_locked = _spy  # type: ignore[method-assign]

    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET),
        idempotency_key="rsi2.2026-06-22.BUY.SPY",
    )

    assert result.status == ExecutionStatus.SUBMITTED
    assert seen["idempotency_key"] == "rsi2.2026-06-22.BUY.SPY"
    assert broker.submit_calls
```
2. **Run, watch it fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/execution/test_service.py::test_idempotency_key_threads_to_submit_locked_without_changing_behavior` — fails with `TypeError: submit_paper() got an unexpected keyword argument 'idempotency_key'`.
3. **Implement.** In `src/milodex/execution/service.py`:
   - `submit_paper` (`:113`): add trailing `idempotency_key: str | None = None,` to the signature and pass `idempotency_key=idempotency_key` into the `self._submit(...)` call (`:121`).
   - `_submit` (`:148`): add trailing `idempotency_key: str | None = None,` to the signature; pass `idempotency_key=idempotency_key` into BOTH `_submit_locked` calls (the lock-free one at `:169` and the lock-held one at `:194`). (Do NOT thread it into `_declined_for_serialization` — a lock-acquire failure short-circuits before any broker attempt, so no intent is consumed; leave it unconsumed for the next drain cycle.)
   - `_submit_locked` (`:304`): add trailing `idempotency_key: str | None = None,` to the signature. No body change yet.
4. **Run, watch it pass:** same pytest command.
5. **Commit:** `git commit -m "feat(execution): thread optional idempotency_key kwarg submit_paper->_submit->_submit_locked"`.
6. **Dispatch risk-invariant-reviewer (Opus) on this diff.**

---

### Task 2 — CAS gate inside `_submit_locked` (consume-or-suppress, before the broker call)

The load-bearing task. After risk allows and while holding the per-account lock, attempt the CAS; submit only on `rowcount == 1`, otherwise record a suppressed explanation and return WITHOUT touching the broker or the outbox.

1. **Write failing test.** Append to `tests/milodex/execution/test_service.py`. This test seeds a `queued` row, then drives two `_submit_locked` calls for the same key and asserts exactly one broker submit:
```python
def test_idempotency_cas_admits_exactly_one_broker_submit_for_repeated_key(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """Repeated _submit_locked for one idempotency_key -> exactly one broker call (CAS rowcount==1 once)."""
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )
    key = "rsi2.2026-06-22.BUY.SPY"
    # Seed the queued intent the runner would have persisted (data-layer section helper).
    _append_queued_intent(service._event_store, idempotency_key=key)

    intent = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)

    first = service._submit_locked(intent, source="paper", idempotency_key=key)
    second = service._submit_locked(intent, source="paper", idempotency_key=key)

    assert first.status == ExecutionStatus.SUBMITTED
    assert second.status == ExecutionStatus.BLOCKED
    assert "idempotency_suppressed" in second.risk_decision.reason_codes
    # The CAS, not the broker, is the gate: exactly one order left the building.
    assert len(broker.submit_calls) == 1
    # And the second call wrote a suppressed explanation, not an outbox attempt.
    attempts = service._event_store.recent_execution_attempts()  # re-grep exact reader name
    assert len([a for a in attempts if a.symbol == "SPY"]) == 1
```
   - NOTE for the synthesizer: `_append_queued_intent` is a shared test helper that belongs with the data-layer section (it wraps `event_store.append_queued_intent(QueuedIntentEvent(...))` with `status='queued'`, a future `expires_at`, and `idempotency_key=key`). If the data-layer section already defines it, import/reuse it; do not redefine. The `recent_execution_attempts()` reader name is illustrative — re-grep `event_store.py` for the actual execution-attempt reader and adjust the assertion (or assert via `broker.submit_calls` count alone if no public reader exists).
2. **Run, watch it fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/execution/test_service.py::test_idempotency_cas_admits_exactly_one_broker_submit_for_repeated_key` — fails: both calls submit, `len(broker.submit_calls) == 2`.
3. **Implement.** In `_submit_locked` (`src/milodex/execution/service.py:304`), insert the CAS block AFTER the risk-allow early-return (`:314`) and BEFORE the outbox `append_execution_attempt` (`:337`) — so a suppressed intent writes neither an outbox row nor a broker order:
```python
        # Idempotency CAS (queued-intent drain path). Risk has ALREADY allowed
        # this intent and we hold the per-account submit lock. The single-
        # statement CAS in the event store flips the queued row to 'consumed'
        # iff it is still 'queued'; rowcount==1 means THIS caller won the race
        # and may submit. rowcount==0 means a concurrent/duplicate drain already
        # consumed it -> suppress: no broker call, no outbox row, an auditable
        # explanation, session continues. Skipped entirely when no key is
        # supplied (legacy direct submit_paper callers).
        if idempotency_key is not None:
            consumed = self._event_store.mark_queued_intent_consumed(
                idempotency_key,
                consumed_by=session_id,
                consumed_at=datetime.now(tz=UTC),
            )
            if consumed != 1:
                return self._suppressed_for_idempotency(
                    intent,
                    result,
                    source=source,
                    session_id=session_id,
                    backtest_run_id=backtest_run_id,
                    idempotency_key=idempotency_key,
                )
```
   Add the `_suppressed_for_idempotency` helper, modeled directly on `_declined_for_serialization` (`:247`) but reusing the already-computed `result` (no second `_evaluate` — risk already passed, this is purely a race-loser, not a risk block):
```python
    def _suppressed_for_idempotency(
        self,
        intent: TradeIntent,
        result: ExecutionResult,
        *,
        source: str,
        session_id: str | None,
        backtest_run_id: int | None,
        idempotency_key: str,
    ) -> ExecutionResult:
        """No-op result when the idempotency CAS lost the race (rowcount != 1).

        A concurrent/duplicate drain already consumed this queued intent. We do
        NOT submit and do NOT write an outbox row. Recorded for audit; the
        runner treats it like any other non-submitted decision and continues.
        """
        _logger.info(
            "Idempotency CAS suppressed duplicate submit for %s (key=%s); no order sent.",
            intent.normalized_symbol(),
            idempotency_key,
        )
        decision = RiskDecision(
            allowed=False,
            summary=(
                "Submit suppressed: queued intent already consumed "
                "(idempotency CAS lost the race). No order was sent."
            ),
            checks=[
                RiskCheckResult(
                    name="idempotency_cas",
                    passed=False,
                    message=f"queued intent {idempotency_key} already consumed",
                    reason_code="idempotency_suppressed",
                )
            ],
            reason_codes=["idempotency_suppressed"],
        )
        suppressed = replace(
            result,
            status=ExecutionStatus.BLOCKED,
            risk_decision=decision,
            order=None,
            message="Submit suppressed: idempotency CAS lost the race (no order sent).",
            recorded_at=datetime.now(tz=UTC),
        )
        self._record_execution(
            intent,
            suppressed,
            decision_type="submit",
            session_id=session_id,
            source=source,
            backtest_run_id=backtest_run_id,
        )
        return suppressed
```
4. **Run, watch it pass:** the Task-2 test, then the existing submit tests as a no-regression check: `.venv/Scripts/python.exe -m pytest -q tests/milodex/execution/test_service.py`.
5. **Implement guard + lint.** Confirm `RiskCheckResult`/`RiskDecision`/`replace`/`ExecutionStatus`/`datetime`/`UTC` are already imported (they are: `service.py:7,8,33,47`). Run `.venv/Scripts/python.exe -m ruff check src/ tests/`.
6. **Commit:** `git commit -m "feat(execution): idempotency CAS in _submit_locked — consume-or-suppress before broker submit"`.
7. **Dispatch risk-invariant-reviewer (Opus) on this diff.** Reviewer must confirm: (a) CAS is strictly after risk-allow and before BOTH the outbox write and `_broker.submit_order`; (b) `rowcount != 1` reaches NO broker call AND NO outbox row; (c) `idempotency_key is None` is byte-for-byte the legacy path; (d) the suppressed branch holds the same per-account lock as the submit branch (no early lock release); (e) a suppressed result cannot trip the kill switch (it must NOT call `_maybe_activate_kill_switch` — only genuine risk-blocks do, `:315`).

---

### Task 3 — Regression fence: `None` key path is unchanged; explicit non-queued key still submits once

Cheap belt-and-suspenders so a future refactor can't silently make the CAS mandatory or double-fire.

1. **Write failing test** (will pass immediately once Tasks 1–2 land — this is a characterization lock, so write it and confirm green, then it guards forever):
```python
def test_submit_paper_without_idempotency_key_is_unchanged(
    tmp_path,
    risk_defaults_file,
    latest_bar,
    sample_account,
    submitted_order,
):
    """Legacy callers (no key) never touch the queued-intents CAS and submit exactly once."""
    service, broker = build_service(
        tmp_path,
        risk_defaults_file,
        latest_bar,
        sample_account,
        submitted_order,
    )
    result = service.submit_paper(
        TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=5, order_type=OrderType.MARKET)
    )
    assert result.status == ExecutionStatus.SUBMITTED
    assert len(broker.submit_calls) == 1
```
2. **Run:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/execution/test_service.py::test_submit_paper_without_idempotency_key_is_unchanged` — passes (proves no-key path skips the CAS).
3. **Implement:** none required if green.
4. **Run full module + lint:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/execution/` and `.venv/Scripts/python.exe -m ruff check src/ tests/`. Confirm against the green baseline (3294 passed, 1 skipped, 4 xfailed) at suite-level integration time.
5. **Commit:** `git commit -m "test(execution): pin no-key submit path bypasses idempotency CAS"`.
6. **Dispatch risk-invariant-reviewer (Opus) on this diff.**

---

**Synthesizer notes (cross-section):**
- This section CONSUMES `mark_queued_intent_consumed` + the `QueuedIntentEvent`/`append_queued_intent` from the data-layer (event-store) section — Task 2's test cannot run until that section's migration + event-store quad exist. Order the data-layer section BEFORE this one.
- The `_append_queued_intent` test helper should be defined ONCE in the data-layer section's test module (or a shared conftest) and reused here; do not duplicate.
- The runner-side persist (Phase-1 at lockin) and drain (Phase-3 splitting `runner.py:272`) sections supply the *real* `idempotency_key = f"{strategy_id}|{trading_session}|{side}|{symbol}"` and call `submit_paper(..., idempotency_key=key)`. This section only guarantees the chokepoint honors the key; it does not compose the key.


---

# Phase 4: HALT/TRADABLE broker read + conservative drain-time DROP helper

## Section: HALT/TRADABLE broker read + conservative drain-time DROP

Two seams: (1) a tradability read on the broker boundary (`is_symbol_tradable(symbol) -> bool | None`), and (2) a drain-time helper `tradable_drop_decision(...)` that maps the read to PROCEED/DROP and **never lets a broker exception reach the runner loop**. Conservative bias: anything but a clear `True` → DROP. This is SACRED-adjacent (broker boundary + drain policy that decides whether a queued intent reaches the submit path), so the broker and helper tasks each end with a risk-invariant-reviewer dispatch.

Design decisions grounded in the read:
- `is_symbol_tradable` is a **concrete default method on the `BrokerClient` ABC** returning `None` (status unknown), NOT a new `@abstractmethod`. Adding an abstractmethod would break every existing subclass and force a change to the parametrized `_ABSTRACT_METHODS` tuple in `tests/milodex/broker/test_client_abc.py:22`. A concrete default keeps all current subclasses instantiable; only `AlpacaBrokerClient` and `SimulatedBroker` override it.
- Alpaca asset shape is already proven in `src/milodex/data/alpaca_provider.py:352-358`: `asset.tradable` (bool) and `asset.status` (enum, `.value` == `"active"`). The read uses `TradingClient.get_asset(symbol)` wrapped in `call_with_retry_on_transient` (same retry helper `get_account`/`get_orders` use, `alpaca_client.py:43`). `is_symbol_tradable` returns `True` only when `asset.tradable is True AND status == "active"`; `False` when the asset exists but is not tradable/active; the method **lets exceptions propagate** (the drain helper, not the broker, owns the catch — keeps the broker boundary a thin read).
- `SimulatedBroker.is_symbol_tradable` returns `True` for any symbol that has a current-day close (`self._current_closes`, `simulated.py:55/74`), else `None`. This makes the fake controllable in tests: inject a close → tradable; clear it → unknown. A dedicated test-only override hook is added so tests can force `False`/raise without depending on close state.

### Task 1 — Broker boundary: `is_symbol_tradable` default on the ABC (concrete, returns None)

5 steps. SACRED-adjacent (broker boundary).

1. **Write failing test.** Append to `tests/milodex/broker/test_client_abc.py` (it already defines `_FullBroker` implementing only the abstract surface):
```python
def test_default_is_symbol_tradable_returns_none() -> None:
    """R-BRK-001 (extension): is_symbol_tradable is concrete on the ABC.

    A subclass that implements only the abstract surface (no override) inherits
    the conservative default: status-unknown -> None. The drain policy treats
    None as DROP, so the default fails safe.
    """
    broker = _FullBroker()
    assert broker.is_symbol_tradable("AAPL") is None
```
2. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/broker/test_client_abc.py::test_default_is_symbol_tradable_returns_none` — expect `AttributeError: 'BrokerClient' has no attribute 'is_symbol_tradable'` (or the `_FullBroker` lacks it). The existing parametrized `_ABSTRACT_METHODS` tests MUST stay green (we are NOT adding an abstractmethod).
3. **Implement** in `src/milodex/broker/client.py`, after `is_market_open` (`client.py:73-75`), as a concrete (non-`@abstractmethod`) method on `BrokerClient`:
```python
    def is_symbol_tradable(self, symbol: str) -> bool | None:
        """Whether ``symbol`` is currently tradable at this broker.

        Returns ``True`` only when the broker affirmatively reports the asset
        as tradable AND active; ``False`` when it reports the asset as halted /
        not tradable / inactive; ``None`` when tradability cannot be
        determined (broker has no opinion, or the subclass does not override).

        Concrete-by-default ON PURPOSE: a new abstractmethod would break every
        existing BrokerClient subclass and the _ABSTRACT_METHODS ABC contract
        test. Subclasses that can answer (Alpaca, Simulated) override it; all
        others inherit the conservative ``None`` (status-unknown). The drain
        policy maps both ``None`` and ``False`` to DROP, so the default fails
        safe. This method MUST NOT swallow exceptions — the drain-time helper
        owns the try/except so a raise still produces a DROP.
        """
        return None
```
4. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/broker/test_client_abc.py` — the new test passes AND all 9 `_ABSTRACT_METHODS` parametrizations still pass (no method added to the abstract set).
5. **Commit** `feat(broker): is_symbol_tradable concrete default on BrokerClient ABC`. **Then: Dispatch risk-invariant-reviewer (Opus) on this diff.**

### Task 2 — `AlpacaBrokerClient.is_symbol_tradable` (asset-status read)

5 steps. SACRED-adjacent (broker boundary).

1. **Write failing test** in new file `tests/milodex/broker/test_tradable_read.py` (mirror the SDK-mock fixture from `tests/milodex/broker/test_alpaca_client.py:33-43` — patch `get_alpaca_credentials`/`get_trading_mode`/`TradingClient`, then `instance._client = mock_cls.return_value`):
```python
from unittest.mock import MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

from milodex.broker.alpaca_client import AlpacaBrokerClient


@pytest.fixture()
def client():
    with patch("milodex.broker.alpaca_client.get_alpaca_credentials") as creds:
        creds.return_value = ("k", "s")
        with patch("milodex.broker.alpaca_client.get_trading_mode") as mode:
            mode.return_value = "paper"
            with patch("milodex.broker.alpaca_client.TradingClient") as cls:
                inst = AlpacaBrokerClient()
                inst._client = cls.return_value
                yield inst


def _asset(tradable=True, status="active"):
    a = MagicMock()
    a.tradable = tradable
    a.status = MagicMock()
    a.status.value = status
    return a


class TestAlpacaIsSymbolTradable:
    def test_tradable_active_returns_true(self, client):
        client._client.get_asset.return_value = _asset(tradable=True, status="active")
        assert client.is_symbol_tradable("AAPL") is True

    def test_not_tradable_returns_false(self, client):
        client._client.get_asset.return_value = _asset(tradable=False, status="active")
        assert client.is_symbol_tradable("AAPL") is False

    def test_inactive_status_returns_false(self, client):
        client._client.get_asset.return_value = _asset(tradable=True, status="inactive")
        assert client.is_symbol_tradable("AAPL") is False

    def test_status_plain_string_active(self, client):
        a = _asset(tradable=True)
        a.status = "active"  # no .value attribute
        client._client.get_asset.return_value = a
        assert client.is_symbol_tradable("AAPL") is True

    def test_api_error_propagates(self, client):
        client._client.get_asset.side_effect = APIError("not found")
        with pytest.raises(Exception):  # broker does NOT swallow; drain helper does
            client.is_symbol_tradable("ZZZZ")
```
2. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/broker/test_tradable_read.py::TestAlpacaIsSymbolTradable` — `AttributeError`/`AssertionError` (method not overridden, inherits `None`).
3. **Implement** in `src/milodex/broker/alpaca_client.py`, after `is_market_open` (`alpaca_client.py:287-290`). Status read mirrors `alpaca_provider.py:357` exactly (`.value if hasattr(...) else`):
```python
    def is_symbol_tradable(self, symbol: str) -> bool | None:
        """Read Alpaca's asset status for ``symbol``.

        ``True`` iff Alpaca reports ``asset.tradable`` AND status == "active".
        ``False`` if the asset exists but is halted/inactive/not tradable.
        Exceptions (APIError for unknown symbol, transient network) are NOT
        caught here — the drain-time helper wraps this call and maps any raise
        to a conservative DROP. Keeping the broker boundary a thin read keeps
        the catch policy in one place (the drain policy), not duplicated here.
        """
        asset = call_with_retry_on_transient(lambda: self._client.get_asset(symbol))
        status = asset.status.value if hasattr(asset.status, "value") else asset.status
        return bool(asset.tradable) and str(status) == "active"
```
4. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/broker/test_tradable_read.py::TestAlpacaIsSymbolTradable`.
5. **Commit** `feat(broker): AlpacaBrokerClient.is_symbol_tradable asset-status read`. **Then: Dispatch risk-invariant-reviewer (Opus) on this diff.**

### Task 3 — `SimulatedBroker.is_symbol_tradable` (test-controllable fake)

5 steps. Not sacred-path (fake used only in tests/backtests), no reviewer step.

1. **Write failing test** — append a class to `tests/milodex/broker/test_tradable_read.py`:
```python
from datetime import UTC, datetime

from milodex.broker.simulated import SimulatedBroker


def _sim():
    return SimulatedBroker(slippage_pct=0.0, commission_per_trade=0.0)


class TestSimulatedIsSymbolTradable:
    def test_symbol_with_close_is_tradable(self):
        b = _sim()
        b.set_simulation_day(datetime(2025, 1, 2, tzinfo=UTC), {"AAPL": 190.0})
        assert b.is_symbol_tradable("AAPL") is True
        assert b.is_symbol_tradable("aapl") is True  # case-insensitive

    def test_symbol_without_close_is_unknown(self):
        b = _sim()
        b.set_simulation_day(datetime(2025, 1, 2, tzinfo=UTC), {"AAPL": 190.0})
        assert b.is_symbol_tradable("MSFT") is None

    def test_forced_override_wins(self):
        b = _sim()
        b.set_simulation_day(datetime(2025, 1, 2, tzinfo=UTC), {"AAPL": 190.0})
        b.set_tradable_override("AAPL", False)
        assert b.is_symbol_tradable("AAPL") is False
        b.set_tradable_override("AAPL", RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            b.is_symbol_tradable("AAPL")
```
2. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/broker/test_tradable_read.py::TestSimulatedIsSymbolTradable`.
3. **Implement** in `src/milodex/broker/simulated.py`. Add `self._tradable_overrides: dict[str, bool | BaseException] = {}` to `__init__` (after `self._orders = []`, `simulated.py:65`). Add a setter in the simulation-state-injection block and the override method after `is_market_open` (`simulated.py:187-191`):
```python
    def set_tradable_override(self, symbol: str, value: bool | BaseException) -> None:
        """Test hook: force is_symbol_tradable for ``symbol`` to a bool, or
        raise the given exception when read (to exercise the drain catch)."""
        self._tradable_overrides[symbol.strip().upper()] = value

    def is_symbol_tradable(self, symbol: str) -> bool | None:
        normalized = symbol.strip().upper()
        if normalized in self._tradable_overrides:
            forced = self._tradable_overrides[normalized]
            if isinstance(forced, BaseException):
                raise forced
            return forced
        # No override: tradable iff we have a current-day close for it,
        # else unknown (None) -> the drain policy treats that as DROP.
        return True if normalized in self._current_closes else None
```
4. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/broker/test_tradable_read.py`.
5. **Commit** `feat(broker): SimulatedBroker.is_symbol_tradable with test override hook`.

### Task 4 — Drain-time DROP helper `tradable_drop_decision` (catch-wrapped, fail-closed)

5 steps. **SACRED-adjacent (drain policy decides whether a queued intent reaches the submit path).**

1. **Write failing test** in new file `tests/milodex/runner/test_drain_policy_tradable.py` (drive it through the real `SimulatedBroker`, no mocks, to prove the catch is real not stubbed):
```python
from datetime import UTC, datetime

import pytest

from milodex.broker.simulated import SimulatedBroker
from milodex.runner.drain_policy import TradableDecision, tradable_drop_decision


def _broker():
    b = SimulatedBroker(slippage_pct=0.0, commission_per_trade=0.0)
    b.set_simulation_day(datetime(2025, 1, 2, tzinfo=UTC), {"AAPL": 190.0})
    return b


def test_tradable_proceeds():
    d = tradable_drop_decision(_broker(), "AAPL")
    assert d.drop is False
    assert d.reason is None


def test_not_tradable_drops():
    b = _broker()
    b.set_tradable_override("AAPL", False)
    d = tradable_drop_decision(b, "AAPL")
    assert d.drop is True
    assert d.reason == "not_tradable"


def test_unknown_status_drops():
    # MSFT has no close on the sim day -> read returns None -> DROP
    d = tradable_drop_decision(_broker(), "MSFT")
    assert d.drop is True
    assert d.reason == "tradability_unknown"


def test_read_raises_drops_and_does_not_propagate():
    b = _broker()
    b.set_tradable_override("AAPL", RuntimeError("alpaca down"))
    d = tradable_drop_decision(b, "AAPL")  # MUST NOT raise
    assert d.drop is True
    assert d.reason == "tradability_read_error"
    assert "alpaca down" in (d.detail or "")
```
2. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/runner/test_drain_policy_tradable.py` — `ModuleNotFoundError: milodex.runner.drain_policy`.
3. **Implement** new file `src/milodex/runner/drain_policy.py`:
```python
"""Drain-time gate helpers for the runner's queued-intent drain.

These decide whether a persisted queued intent may proceed to the submit
path. They are deliberately CONSERVATIVE and FAIL-CLOSED: any condition
that is not an affirmative go produces a DROP, and a broker read that
RAISES is caught here so the exception never reaches the runner loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from milodex.broker.client import BrokerClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TradableDecision:
    drop: bool
    reason: str | None = None  # machine tag: not_tradable | tradability_unknown | tradability_read_error
    detail: str | None = None  # human/exception text for logging/explanation


def tradable_drop_decision(broker: BrokerClient, symbol: str) -> TradableDecision:
    """DROP a queued intent unless the broker AFFIRMATIVELY reports tradable.

    PROCEED (drop=False) iff ``broker.is_symbol_tradable(symbol) is True``.
    DROP when the read returns False (halted/inactive), None (unknown), OR
    raises. The raise is caught and converted to a DROP here so a flaky
    broker read can never propagate into the drain loop and crash the runner.
    """
    try:
        tradable = broker.is_symbol_tradable(symbol)
    except Exception as exc:  # noqa: BLE001 - fail-closed: any read error -> DROP
        logger.warning("tradability read failed for %s; dropping intent: %s", symbol, exc)
        return TradableDecision(drop=True, reason="tradability_read_error", detail=str(exc))
    if tradable is True:
        return TradableDecision(drop=False)
    if tradable is False:
        return TradableDecision(drop=True, reason="not_tradable", detail=f"{symbol} not tradable")
    return TradableDecision(
        drop=True, reason="tradability_unknown", detail=f"{symbol} tradability unknown"
    )
```
4. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/runner/test_drain_policy_tradable.py`.
5. **Commit** `feat(runner): conservative tradable_drop_decision drain gate (fail-closed)`. **Then: Dispatch risk-invariant-reviewer (Opus) on this diff.**

### Task 5 — Lint + suite checkpoint

3 steps.
1. **Lint:** `.venv/Scripts/python.exe -m ruff check src/ tests/` — clean. (Watch the broad-except: `# noqa: BLE001` is intentional and already inline.)
2. **Suite:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/broker tests/milodex/runner/test_drain_policy_tradable.py` — all green. Confirm the full-suite baseline is unchanged (3294 passed, 1 skipped, 4 xfailed) by running `.venv/Scripts/python.exe -m pytest -q` if time permits; this section adds tests/methods only and must not move existing counts except by the net-new tests above.
3. **Commit** any lint fixups if needed: `chore(broker,runner): lint`.

**Handoff to the runner drain section:** the drain section imports `from milodex.runner.drain_policy import tradable_drop_decision` and, for each candidate queued intent in the Phase-3 drain (after rollover reconcile, the split at `runner.py:272`), calls `tradable_drop_decision(self._broker, intent.symbol)`. On `decision.drop is True` it MUST mark the intent obsolete via `event_store.mark_queued_intent_obsolete(intent.id)` (do NOT consume — DROP is not a consume) and skip submit; `decision.reason`/`decision.detail` feed the drop explanation. The drain section MUST NOT touch `_last_processed_bar_at` (`runner.py:296-299/511`).


---

# Phase 5: CLEAN-EXIT HANDOFF FENCE + config_hash guard (I-4)

## Section: CLEAN-EXIT HANDOFF FENCE + config_hash guard (I-4)

This section owns the drain-authority safety logic that `get_active_queued_intents` (data-layer task) calls: the originating-session exit-reason lookup, the clean-handoff fence predicate, and the config_hash re-verification guard. It does NOT define `get_active_queued_intents`, the `QueuedIntentEvent` dataclass, the `queued_intents` table, or migration `016` — those are the data-layer task's. This section assumes those exist (or are landed in the same PR train ahead of these tasks) and wires the safety predicate into the query body.

**Coordination contract (do not re-derive — match these EXACTLY):**
- The fence lives INSIDE `get_active_queued_intents(strategy_id, *, now, running_session_id)` in `src/milodex/core/event_store.py`. Its base filter is `status='queued' AND datetime(expires_at) > now`. This section adds the clean-handoff conjunct.
- Clean-handoff (I-4) holds iff: `row.session_id == running_session_id` **OR** the originating `strategy_runs` row for `row.session_id` has `exit_reason == 'controlled_stop'` (literal string equality — NOT `IS NOT NULL`). Every other terminal state (`interrupted`, `crashed:*`, `kill_switch`, `orphan_recovered`, NULL) DROPS the row.
- `compute_config_hash(path)` already exists at `src/milodex/strategies/loader.py:429` (YAML-parse → canonicalize → SHA-256; CRLF-insensitive by construction because the YAML loader normalizes line endings before serialization). It RAISES `ValueError` on a missing path. The config_hash guard MUST drop (not raise) on a missing/unreadable path, so this section adds a non-raising `compute_config_hash_or_none(path)` wrapper and uses that.

**SACRED PATH.** `event_store.py` is the durable source of truth and the sole drain authority; a fence that lets a poisoned/foreign-session intent through is a capital-safety hole. Every task here ends with a risk-invariant-reviewer dispatch. Do NOT widen the fence to `exit_reason IS NOT NULL` "for robustness" — an interrupted/crashed session leaving a partial lock-in MUST drop.

Test/lint after each task:
`.venv/Scripts/python.exe -m pytest -q <path>` then `.venv/Scripts/python.exe -m ruff check src/ tests/`.
Green baseline: `3294 passed, 1 skipped, 4 xfailed` (this section adds tests; the new total is the baseline + the new test count).

---

### Task 1 — `EventStore.get_session_exit_reason(session_id) -> str | None`

Single-row lookup of the originating run's terminal `exit_reason`, keyed by `session_id`. Returns the stored string (which may itself be `None` for a still-open or never-closed run), or `None` when no `strategy_runs` row matches. Mirrors the bounded single-row pattern of `get_latest_open_session_id` (`event_store.py:1481`). `strategy_runs.exit_reason` is `TEXT` nullable (`migrations/001_initial.sql:65`); `_strategy_run_from_row` already round-trips it as `str | None` (`event_store.py:2517`).

**Step 1 — write failing test.** Create `tests/milodex/core/test_event_store_session_exit_reason.py`:
```python
from datetime import UTC, datetime

from milodex.core.event_store import EventStore, StrategyRunEvent


def _open_run(store: EventStore, session_id: str, strategy_id: str = "mom.atr.v1") -> None:
    store.append_strategy_run(
        StrategyRunEvent(
            id=None,
            session_id=session_id,
            strategy_id=strategy_id,
            started_at=datetime(2026, 6, 22, 14, 0, tzinfo=UTC),
            ended_at=None,
            exit_reason=None,
            metadata={},
        )
    )


def test_returns_none_when_no_row(tmp_path):
    store = EventStore(tmp_path / "e.db")
    assert store.get_session_exit_reason("missing-sid") is None


def test_returns_none_for_open_run(tmp_path):
    store = EventStore(tmp_path / "e.db")
    _open_run(store, "sid-open")
    assert store.get_session_exit_reason("sid-open") is None


def test_returns_controlled_stop_after_close(tmp_path):
    store = EventStore(tmp_path / "e.db")
    _open_run(store, "sid-cs")
    store.update_strategy_run_end(
        session_id="sid-cs",
        ended_at=datetime(2026, 6, 22, 21, 0, tzinfo=UTC),
        exit_reason="controlled_stop",
    )
    assert store.get_session_exit_reason("sid-cs") == "controlled_stop"


def test_returns_verbatim_crash_reason(tmp_path):
    store = EventStore(tmp_path / "e.db")
    _open_run(store, "sid-crash")
    store.update_strategy_run_end(
        session_id="sid-crash",
        ended_at=datetime(2026, 6, 22, 21, 0, tzinfo=UTC),
        exit_reason="crashed:ValueError('boom')",
    )
    assert store.get_session_exit_reason("sid-crash") == "crashed:ValueError('boom')"
```
(Confirm `StrategyRunEvent`'s actual field set / `append_strategy_run` signature against `event_store.py:~870-895` before running — if `id` is not an accepted kwarg, drop it; the test's only load-bearing fields are `session_id`, `strategy_id`, `exit_reason`.)

**Step 2 — run, confirm red.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_event_store_session_exit_reason.py` → fails with `AttributeError: 'EventStore' object has no attribute 'get_session_exit_reason'`.

**Step 3 — implement.** In `src/milodex/core/event_store.py`, add immediately after `get_latest_open_session_id` (ends `event_store.py:1500`):
```python
    def get_session_exit_reason(self, session_id: str) -> str | None:
        """Return the terminal ``exit_reason`` of the run for ``session_id``.

        Clean-handoff fence (I-4) input: ``get_active_queued_intents`` consults
        this to decide whether a queued intent whose originating session is no
        longer the running one may still be drained. Only ``'controlled_stop'``
        authorizes a cross-session drain; every other terminal state
        (``interrupted`` / ``crashed:*`` / ``kill_switch`` / ``orphan_recovered``)
        and an open run (``exit_reason IS NULL``) must drop.

        Returns the stored ``exit_reason`` (itself ``None`` for an open or
        never-closed run), or ``None`` when no ``strategy_runs`` row matches.
        Bounded single-row read — safe in a drain loop. ``ORDER BY id DESC``
        breaks the (by-design impossible) duplicate-session case toward the
        most recent row, matching ``update_strategy_run_end``.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT exit_reason FROM strategy_runs
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return None if row is None else row[0]
```

**Step 4 — run, confirm green.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_event_store_session_exit_reason.py` → all pass. Run `.venv/Scripts/python.exe -m ruff check src/ tests/` → clean.

**Step 5 — risk review + commit.** Dispatch risk-invariant-reviewer (Opus) on this diff (SACRED event-store path; verify the query cannot mis-attribute one session's reason to another and that a NULL exit_reason is never coerced to a truthy/clean value). Then `git commit -m "feat(event-store): get_session_exit_reason for clean-exit fence (I-4)"`.

---

### Task 2 — clean-handoff fence predicate inside `get_active_queued_intents`

Add the I-4 conjunct to the drain authority. Depends on Task 1 (`get_session_exit_reason`) and on the data-layer task having landed `get_active_queued_intents` with its base `status='queued' AND datetime(expires_at) > now` filter and `_queued_intent_from_row`. If the base method is not yet present, this task is the one that adds the clean-handoff branch to it; coordinate so the predicate below is the SOLE place the handoff rule is expressed.

**Step 1 — write failing test.** Create `tests/milodex/core/test_queued_intent_clean_exit_fence.py`. Build a small helper that appends a `strategy_runs` row, closes it with a given `exit_reason`, appends one `queued_intent` (status `'queued'`, `expires_at` in the future) tagged with that `session_id`, then asserts drainability via `get_active_queued_intents`:
```python
from datetime import UTC, datetime, timedelta

import pytest

from milodex.core.event_store import EventStore, QueuedIntentEvent, StrategyRunEvent

NOW = datetime(2026, 6, 22, 21, 30, tzinfo=UTC)


def _make(store: EventStore, *, session_id: str, exit_reason: str | None, close: bool):
    store.append_strategy_run(
        StrategyRunEvent(
            id=None, session_id=session_id, strategy_id="mom.atr.v1",
            started_at=NOW - timedelta(hours=8), ended_at=None,
            exit_reason=None, metadata={},
        )
    )
    if close:
        store.update_strategy_run_end(
            session_id=session_id, ended_at=NOW - timedelta(minutes=5),
            exit_reason=exit_reason,
        )
    store.append_queued_intent(
        QueuedIntentEvent(
            id=None,
            idempotency_key=f"mom.atr.v1|2026-06-22|buy|SPY|{session_id}",
            strategy_id="mom.atr.v1",
            strategy_config_path="configs/mom.yaml",
            config_hash="h0",
            session_id=session_id,
            trading_session="2026-06-22",
            locked_in_bar_timestamp=(NOW - timedelta(minutes=10)).isoformat(),
            symbol="SPY", side="buy", intent_class="entry",
            notional_pct=0.1, expected_stage="paper",
            expected_max_positions=3, expected_max_position_pct=0.2,
            expected_daily_loss_cap_pct=0.03,
            intent_payload_json="{}", reasoning_json="{}",
            created_at=(NOW - timedelta(minutes=10)).isoformat(),
            expires_at=(NOW + timedelta(hours=12)).isoformat(),
            status="queued", consumed_at=None, consumed_by=None,
        )
    )


def _drainable(store, *, running_session_id):
    rows = store.get_active_queued_intents(
        "mom.atr.v1", now=NOW, running_session_id=running_session_id
    )
    return [r.session_id for r in rows]


def test_same_running_session_drainable(tmp_path):
    store = EventStore(tmp_path / "e.db")
    _make(store, session_id="sid-run", exit_reason=None, close=False)
    assert _drainable(store, running_session_id="sid-run") == ["sid-run"]


def test_controlled_stop_other_session_drainable(tmp_path):
    store = EventStore(tmp_path / "e.db")
    _make(store, session_id="sid-old", exit_reason="controlled_stop", close=True)
    assert _drainable(store, running_session_id="sid-new") == ["sid-old"]


@pytest.mark.parametrize(
    "reason",
    ["interrupted", "crashed:ValueError('x')", "kill_switch", "orphan_recovered"],
)
def test_dirty_exit_other_session_dropped(tmp_path, reason):
    store = EventStore(tmp_path / "e.db")
    _make(store, session_id="sid-old", exit_reason=reason, close=True)
    assert _drainable(store, running_session_id="sid-new") == []


def test_open_run_other_session_dropped(tmp_path):
    store = EventStore(tmp_path / "e.db")
    _make(store, session_id="sid-old", exit_reason=None, close=False)
    assert _drainable(store, running_session_id="sid-new") == []
```
(Match `QueuedIntentEvent` / `append_queued_intent` field names to whatever the data-layer task froze — the shared contract column list is authoritative; adjust kwargs to the dataclass exactly.)

**Step 2 — run, confirm red.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intent_clean_exit_fence.py` → `test_controlled_stop_other_session_drainable` and the dirty/open cases fail (base method without the fence returns the row for any session, or drops all cross-session rows — either way the parametrized expectations don't all hold).

**Step 3 — implement the predicate.** In `get_active_queued_intents` (`event_store.py`), keep the SQL base filter as `status='queued' AND datetime(expires_at) > :now`, then apply the clean-handoff fence in Python over the candidate rows (a Python filter keeps the literal-equality rule in one readable place and reuses Task 1's helper rather than a correlated subquery that would re-encode the rule in SQL):
```python
        candidates = [self._queued_intent_from_row(r) for r in rows]
        active: list[QueuedIntentEvent] = []
        for intent in candidates:
            if intent.session_id == running_session_id:
                active.append(intent)
                continue
            # I-4 clean-exit fence: a foreign originating session is drainable
            # ONLY if it terminated via controlled_stop. interrupted / crashed:* /
            # kill_switch / orphan_recovered / still-open (NULL) all DROP — a
            # dirty or partial lock-in must never be replayed by a sibling.
            if self.get_session_exit_reason(intent.session_id) == "controlled_stop":
                active.append(intent)
        return active
```
Do NOT collapse the two arms into `exit_reason IS NOT NULL` — the `running_session_id` match is the live-handoff path (its run is still open, exit_reason NULL) and `controlled_stop` is the clean-shutdown path; every other value drops.

**Step 4 — run, confirm green.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intent_clean_exit_fence.py` → all pass. Ruff clean.

**Step 5 — risk review + commit.** Dispatch risk-invariant-reviewer (Opus) on this diff (SACRED — this is the drain gate; verify no path admits a `kill_switch`/`crashed`/`interrupted`/`orphan_recovered`/NULL foreign session, and that the same-running-session arm cannot be spoofed by a row carrying an arbitrary `session_id`). Then `git commit -m "feat(event-store): I-4 clean-exit handoff fence in get_active_queued_intents"`.

---

### Task 3 — `compute_config_hash_or_none` (non-raising, CRLF-insensitive)

The config_hash guard re-verifies, at drain time, that the strategy config a queued intent was locked in against is byte-equivalent (modulo line endings) to the config on disk now — and drops the intent on any mismatch OR on a missing/unreadable path. `compute_config_hash` (`loader.py:429`) gives the CRLF-insensitive semantic hash but RAISES `ValueError` on a missing path; the guard must drop, not raise. Add a thin non-raising wrapper.

**Step 1 — write failing test.** Create `tests/milodex/core/test_queued_intent_config_hash_guard.py`:
```python
from pathlib import Path

from milodex.strategies.loader import compute_config_hash, compute_config_hash_or_none


def test_missing_path_returns_none(tmp_path):
    assert compute_config_hash_or_none(tmp_path / "nope.yaml") is None


def test_matches_strict_hash_for_existing(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    assert compute_config_hash_or_none(p) == compute_config_hash(p)


def test_crlf_insensitive(tmp_path):
    lf = tmp_path / "lf.yaml"
    crlf = tmp_path / "crlf.yaml"
    lf.write_bytes(b"strategy:\n  id: mom.atr.swing.v1\n")
    crlf.write_bytes(b"strategy:\r\n  id: mom.atr.swing.v1\r\n")
    assert compute_config_hash_or_none(lf) == compute_config_hash_or_none(crlf)
```

**Step 2 — run, confirm red.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intent_config_hash_guard.py` → `ImportError: cannot import name 'compute_config_hash_or_none'`.

**Step 3 — implement.** In `src/milodex/strategies/loader.py`, immediately after `compute_config_hash` (`loader.py:441`):
```python
def compute_config_hash_or_none(path: Path) -> str | None:
    """CRLF-insensitive config hash that returns ``None`` instead of raising.

    Drain-time guard helper (I-4): a queued intent is dropped when its config
    can no longer be hashed (path deleted/moved/unreadable) — those are exactly
    the cases :func:`compute_config_hash` raises on. Hashing is CRLF-insensitive
    because the underlying YAML load normalizes line endings before
    canonicalization, so a checkout that flipped LF<->CRLF does not spuriously
    invalidate an otherwise-identical config.
    """
    try:
        return compute_config_hash(path)
    except (ValueError, OSError):
        return None
```
(The `crlf_insensitive` test pins the line-ending property at the helper boundary — if a future refactor moves hashing off the YAML-load path, this test fails loudly rather than silently dropping valid intents.)

**Step 4 — run, confirm green.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intent_config_hash_guard.py` → all pass. Ruff clean.

**Step 5 — commit.** Not a sacred-runtime path (pure helper in `strategies/loader.py`), so no risk-reviewer gate here — the gate moves to Task 4 where the helper is wired into the drain. `git commit -m "feat(loader): compute_config_hash_or_none non-raising guard helper"`.

---

### Task 4 — config_hash re-verification in the drain (drop on mismatch/missing)

Extend the active-intent filter so a row whose stored `config_hash` no longer matches the on-disk config (or whose path is gone) is DROPPED before it can reach the idempotency CAS. This composes with the Task 2 fence: a row must pass BOTH the clean-handoff fence AND the config-hash check to be returned. Depends on Task 2 (the Python filter loop in `get_active_queued_intents`) and Task 3 (`compute_config_hash_or_none`).

**Step 1 — write failing test.** Append to `tests/milodex/core/test_queued_intent_clean_exit_fence.py` (reuse `_make`/`_drainable`, parameterize the queued row's `config_hash` and `strategy_config_path`). Add a variant of `_make` that takes `config_path` and `config_hash`, plus:
```python
def test_config_hash_match_drainable(tmp_path):
    store = EventStore(tmp_path / "e.db")
    cfg = tmp_path / "mom.yaml"
    cfg.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    from milodex.strategies.loader import compute_config_hash
    h = compute_config_hash(cfg)
    _make_with_cfg(store, session_id="sid-run", config_path=str(cfg), config_hash=h)
    assert _drainable(store, running_session_id="sid-run") == ["sid-run"]


def test_config_hash_mismatch_dropped(tmp_path):
    store = EventStore(tmp_path / "e.db")
    cfg = tmp_path / "mom.yaml"
    cfg.write_text("strategy:\n  id: mom.atr.swing.v1\n", encoding="utf-8")
    _make_with_cfg(store, session_id="sid-run", config_path=str(cfg),
                   config_hash="STALE_HASH")
    assert _drainable(store, running_session_id="sid-run") == []


def test_config_path_missing_dropped(tmp_path):
    store = EventStore(tmp_path / "e.db")
    _make_with_cfg(store, session_id="sid-run",
                   config_path=str(tmp_path / "gone.yaml"), config_hash="anything")
    assert _drainable(store, running_session_id="sid-run") == []
```
Note these use the SAME-running-session row (`sid-run`, open) so the test isolates the config-hash check from the exit-reason fence — a hash mismatch must drop even on the live-handoff path.

**Step 2 — run, confirm red.** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intent_clean_exit_fence.py` → the new mismatch/missing cases fail (current filter returns the row regardless of config_hash).

**Step 3 — implement.** Extend the Task 2 loop body in `get_active_queued_intents` so config-hash verification gates BOTH arms. Add an import of `compute_config_hash_or_none` at the top of `event_store.py` (alongside any existing `milodex.strategies.loader` import — if none exists, add a local import inside the method to avoid a module-load cycle, matching how `event_store.py` keeps strategy imports lazy):
```python
        from milodex.strategies.loader import compute_config_hash_or_none

        candidates = [self._queued_intent_from_row(r) for r in rows]
        active: list[QueuedIntentEvent] = []
        for intent in candidates:
            current_hash = (
                compute_config_hash_or_none(Path(intent.strategy_config_path))
                if intent.strategy_config_path is not None
                else None
            )
            if current_hash is None or current_hash != intent.config_hash:
                # Config moved/deleted/unreadable, or drifted since lock-in.
                # Drop: a queued intent must replay against the EXACT config it
                # was evaluated under, or not at all.
                continue
            if intent.session_id == running_session_id:
                active.append(intent)
                continue
            if self.get_session_exit_reason(intent.session_id) == "controlled_stop":
                active.append(intent)
        return active
```
Verify `Path` is imported in `event_store.py` (it is used elsewhere in the file; if not, add `from pathlib import Path`).

**Step 4 — run, confirm green.** Run the full fence test file: `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_queued_intent_clean_exit_fence.py` → all pass (Task 2 cases still green, new config-hash cases green). Ruff clean.

**Step 5 — risk review + commit.** Dispatch risk-invariant-reviewer (Opus) on this diff (SACRED — verify: (a) a missing path drops rather than raises out of the drain loop and stalls all draining; (b) a hash mismatch drops even when `session_id == running_session_id`; (c) the config-hash check runs BEFORE the exit-reason branch so a stale config can never be admitted via either arm; (d) the lazy import does not introduce an import cycle that breaks event-store construction). Then `git commit -m "feat(event-store): drain-time config_hash re-verification (I-4 guard)"`.

---

### Section close

After Task 4: run the broader core suite to confirm no regression in the surrounding event-store contract —
`.venv/Scripts/python.exe -m pytest -q tests/milodex/core/` — and the full ruff gate. The three new test files add to the green baseline (`3294 passed` + the new cases). Hand the wired `get_active_queued_intents` back to the data-layer / runner-drain tasks: Phase-3 drain (runner.py split at `runner.py:272`) calls this method as its SOLE source of drainable intents and MUST treat an empty return as "nothing to drain," never as an error.


---

# Phase 6: RUNNER Phase-1 persist + Phase-3 drain (TOCTOU integration core)

## Section R — RUNNER Phase-1 persist + Phase-3 drain (integration core)

**SACRED PATH (runner).** The runner is the single intent→trade orchestrator; this section rewires *when* it persists vs. submits. Every task ends with a risk-invariant-reviewer dispatch. The watermark (`_last_processed_bar_at`) is the integrity anchor: it advances **exactly once** at the authoritative post-close evaluation and the drain path **MUST NOT** touch it.

**Depends on (consumed from earlier sections — do not redefine):**
- `EventStore.append_queued_intent(event) -> int`, `EventStore.get_active_queued_intents(strategy_id, *, now, running_session_id) -> list[QueuedIntentEvent]`, `EventStore.mark_queued_intent_obsolete(id)` and the frozen `QueuedIntentEvent` dataclass (event-store section).
- `compute_config_hash(path) -> str` (`milodex.strategies.loader`, line 429).
- The broker tradable/halt **DROP helper** (broker section) — assume importable as `milodex.broker.tradability.intent_is_tradable(broker, symbol) -> bool` (returns `False` for halted / non-tradable / ambiguous-asset).
- `submit_paper(..., idempotency_key=...)` additive kwarg threading (execution-service section) — the idempotency CAS lives inside `_submit_locked`; the runner only **passes** the key, it never calls `mark_queued_intent_consumed`.

**Defines (this section):**
- `runner.py` Phase-1 branch: `_persist_queued_intent(intent, latest_bar, decision)` + the rewired lockin-confirmed cycle.
- `runner.py` Phase-3 branch: `_drain_queued_intents()` + the split of `runner.py:272`.
- Helpers: `_idempotency_key(intent)`, `_trading_session_label(latest_bar)`, `_intent_class(intent)`, `_intent_notional_pct(intent)`.

---

### Task R.1 — `_idempotency_key` + intent-classification helpers (pure, no I/O)

Bite-sized, no event store touched yet. Establishes the exact key composition the contract pins: `f"{strategy_id}|{trading_session}|{side}|{symbol}"`.

1. **Write failing test** — `tests/milodex/strategies/test_runner_queued_intent_persist.py`:
```python
from datetime import UTC, datetime
from milodex.broker.models import OrderSide, OrderType, TimeInForce
from milodex.execution.models import TradeIntent


def test_idempotency_key_composition(daily_runner):
    runner = daily_runner  # fixture: regime daily runner, market closed
    intent = TradeIntent(
        symbol="spy", side=OrderSide.BUY, quantity=3.0, order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    bar_ts = datetime(2026, 6, 19, 20, 0, tzinfo=UTC)  # a Friday RTH close
    key = runner._idempotency_key(intent, runner._trading_session_label(bar_ts))
    assert key == f"{runner._strategy_id}|2026-06-19|BUY|SPY"


def test_intent_class_entry_vs_exit(daily_runner):
    runner = daily_runner
    buy = TradeIntent(symbol="SPY", side=OrderSide.BUY, quantity=1.0, order_type=OrderType.MARKET)
    sell = TradeIntent(symbol="SPY", side=OrderSide.SELL, quantity=1.0, order_type=OrderType.MARKET)
    # entry = a side that opens/extends against a flat strategy ledger; exit = reduces an open lot.
    runner._current_positions = lambda: {}  # flat
    assert runner._intent_class(buy) == "entry"
    assert runner._intent_class(sell) == "exit"  # SELL against flat is still classed exit-side
```
   The `daily_runner` fixture mirrors `_build_lockin_runner` (tests/milodex/strategies/test_runner.py:1409) with `market_open=False`; add it to the new file's module-level fixtures (copy the wiring, don't import the private builder).

2. **Run, see fail** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner_queued_intent_persist.py` (AttributeError: `_idempotency_key`).

3. **Implement** in `src/milodex/strategies/runner.py` (add near `_intent_key`, runner.py:571):
```python
    def _trading_session_label(self, bar_timestamp: datetime) -> str:
        """ISO date (UTC) of the bar's session — the trading_session component
        of the idempotency key and the queued_intents.trading_session column.
        Daily bars are session-dated; UTC date is the stable session label."""
        return bar_timestamp.date().isoformat()

    def _idempotency_key(self, intent: TradeIntent, trading_session: str) -> str:
        # Contract: f"{strategy_id}|{trading_session}|{side}|{symbol}" (UNIQUE).
        return f"{self._strategy_id}|{trading_session}|{intent.side.value}|{intent.normalized_symbol()}"

    def _intent_class(self, intent: TradeIntent) -> str:
        """'entry' if the order opens/extends exposure for this strategy on a
        flat-or-same-side ledger, else 'exit'. BUY=entry, SELL=exit for the
        long-only Phase-1 universe; threaded so Phase-3 can route 0-share
        outcomes (entry->drop, exit-on-flat->obsolete)."""
        return "entry" if intent.side == OrderSide.BUY else "exit"

    def _intent_notional_pct(self, intent: TradeIntent) -> float | None:
        """Notional fraction this intent targets, if the strategy expressed one;
        None when the intent is share-quantified only. Persisted so Phase-3 can
        re-derive sizing context for the audit envelope (re-evaluation recomputes
        the authoritative share count)."""
        return getattr(intent, "notional_pct", None)
```
   Add `from milodex.broker.models import OrderSide` to the runner imports if not already present (it imports `TradeIntent` already at runner.py:22; confirm `OrderSide` is in scope — add to the existing `from milodex.execution.models import ...` is wrong, OrderSide lives in broker.models).

4. **Run, see pass** — same pytest path.

5. **Commit** — `feat(runner): idempotency-key + intent-class helpers for queued-intent TOCTOU`.

6. **Dispatch risk-invariant-reviewer (Opus) on this diff.**

---

### Task R.2 — Phase-1: persist a `queued_intent` at the lockin-confirmed cycle (instead of `submit_paper`)

The daily post-close cycle currently advances the watermark (runner.py:336-341) then submits via `submit_paper` (runner.py:377). Rewire so the **lockin-confirmed daily cycle persists a `QueuedIntentEvent` and does NOT submit**; the watermark still advances exactly once.

1. **Write failing test** — append to `test_runner_queued_intent_persist.py`:
```python
def test_post_close_cycle_persists_and_does_not_submit(daily_runner_persisting, event_store):
    runner, broker, provider = daily_runner_persisting
    # Drive the two-cycle lockin (mirror test_daily_post_close_current_bar_locks_in:1635).
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner._now = lambda: fake_now[0]
    runner.run_cycle()                                  # cycle 1: pending stability
    assert runner._last_processed_bar_at is None
    fake_now[0] += timedelta(seconds=30)
    results = runner.run_cycle()                        # cycle 2: lockin confirms

    # Watermark advanced exactly once.
    assert runner._last_processed_bar_at is not None
    # NO broker submit happened on the persist path.
    assert broker.submitted_orders == []
    assert results == []
    # Exactly one queued_intent row exists, status 'queued', key well-formed.
    rows = event_store.get_active_queued_intents(
        runner._strategy_id, now=fake_now[0], running_session_id=runner.session_id,
    )
    assert len(rows) == 1
    assert rows[0].idempotency_key == f"{runner._strategy_id}|{latest_ts.date().isoformat()}|BUY|SPY"
    assert rows[0].intent_class == "entry"
    assert rows[0].config_hash == runner._risk_config_hash()


def test_post_close_no_intents_persists_nothing(daily_runner_no_signal, event_store):
    runner, _, provider = daily_runner_no_signal  # strategy returns [] intents
    latest_ts = provider._bars_by_symbol["SPY"].latest().timestamp
    fake_now = [latest_ts.to_pydatetime().replace(hour=20, minute=5)]
    runner._now = lambda: fake_now[0]
    runner.run_cycle(); fake_now[0] += timedelta(seconds=30); runner.run_cycle()
    assert event_store.get_active_queued_intents(
        runner._strategy_id, now=fake_now[0], running_session_id=runner.session_id,
    ) == []
```
   `daily_runner_persisting` is `_build_lockin_runner` wiring where the seeded strategy emits a BUY intent at the post-close bar (use the existing regime config that rotates into SPY, or stub `runner._loaded.strategy.evaluate` to return a one-BUY decision). `StubBroker` must record `submitted_orders` (mirror the existing StubBroker in test_runner.py).

2. **Run, see fail** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner_queued_intent_persist.py` (still submits / no row).

3. **Implement** — in `runner.py`, first add the config-hash helper and the persist method:
```python
    def _risk_config_hash(self) -> str:
        """SHA-256 of the canonicalized active config — bound once at the
        lockin cycle and re-verified at drain (Phase-3) before re-evaluation.
        A mid-window config edit changes this hash, so a stale queued intent
        is dropped at drain rather than executed against new policy."""
        return compute_config_hash(self._loaded.config.path)

    def _persist_queued_intent(
        self, intent: TradeIntent, latest_bar, decision: DecisionReasoning
    ) -> None:
        """Phase-1: durably enqueue the intent recomputed at next-open drain.

        Builds the full TOCTOU envelope: idempotency_key (UNIQUE), config_hash,
        expires_at = one trading session out, intent_class, and the expected_*
        risk-envelope snapshot the runner was bound to at startup. Does NOT
        submit and does NOT advance the watermark (the caller already did)."""
        runner_intent = self._runner_intent(intent)
        trading_session = self._trading_session_label(latest_bar.timestamp)
        idempotency_key = self._idempotency_key(runner_intent, trading_session)
        now = self._now()
        event = QueuedIntentEvent(
            idempotency_key=idempotency_key,
            strategy_id=self._strategy_id,
            strategy_config_path=str(self._loaded.config.path),
            config_hash=self._risk_config_hash(),
            session_id=self._session_id,
            trading_session=trading_session,
            locked_in_bar_timestamp=latest_bar.timestamp,
            symbol=runner_intent.normalized_symbol(),
            side=runner_intent.side.value,
            intent_class=self._intent_class(runner_intent),
            notional_pct=self._intent_notional_pct(runner_intent),
            expected_stage=runner_intent.expected_stage,
            expected_max_positions=runner_intent.expected_max_positions,
            expected_max_position_pct=runner_intent.expected_max_position_pct,
            expected_daily_loss_cap_pct=runner_intent.expected_daily_loss_cap_pct,
            intent_payload_json={
                "symbol": runner_intent.symbol,
                "side": runner_intent.side.value,
                "quantity": runner_intent.quantity,
                "order_type": runner_intent.order_type.value,
                "time_in_force": runner_intent.time_in_force.value,
            },
            reasoning_json=asdict(decision) if decision is not None else None,
            created_at=now,
            expires_at=now + timedelta(days=1),  # one trading session; sweep expires beyond
            status="queued",
        )
        try:
            self._event_store.append_queued_intent(event)
        except sqlite3.IntegrityError:
            # UNIQUE(idempotency_key) collision = this session already queued
            # the same logical intent (a re-fired lockin after a crash mid-cycle).
            # Idempotent by construction — the existing row is authoritative.
            logger.info("queued_intent already present for %s; skipping re-enqueue", idempotency_key)
```
   Add imports: `from dataclasses import asdict, replace` (replace already imported — add `asdict`), `import sqlite3`, and `from milodex.core.event_store import EventStore, QueuedIntentEvent, StrategyRunEvent` (extend the existing import at runner.py:17).

   Now rewire the lockin-confirmed submit block. **Replace** the submit loop at runner.py:370-385:
```python
        # Phase-1 (TOCTOU): at the authoritative post-close lockin cycle, daily
        # strategies do NOT submit — the market is closed and a market order
        # would either be rejected (R-RISK market_closed) or queued by the
        # broker to fill at an unknown next-open price the strategy never
        # decided on. Instead PERSIST each intent; the next at-open drain
        # re-evaluates against a fresh context and submits through the
        # chokepoint. The watermark already advanced above (exactly once).
        if is_daily_bar and not market_open:
            for intent in intents:
                intent_key = self._intent_key(intent, latest_bar.timestamp)
                if intent_key in self._processed_intent_keys:
                    continue
                self._processed_intent_keys.add(intent_key)
                self._persist_queued_intent(intent, latest_bar, decision.reasoning)
            if self._on_cycle_result is not None:
                self._on_cycle_result([])
            return []

        results: list[ExecutionResult] = []
        for intent in intents:
            # ... existing intraday/open submit loop unchanged ...
```
   Keep the existing intraday submit loop (runner.py:370-385) intact below the new daily branch — only the daily-post-close path diverts to persist.

4. **Run, see pass** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner_queued_intent_persist.py` then the full runner regression: `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner.py`.

5. **Commit** — `feat(runner): persist queued_intent at lockin instead of submitting (Phase-1 TOCTOU)`.

6. **Dispatch risk-invariant-reviewer (Opus) on this diff.** Reviewer must confirm: watermark advances exactly once; no submit on the daily persist path; UNIQUE collision is idempotent not crashing; `expected_*` envelope is the runner-bound snapshot (TOCTOU-safe), never a per-cycle YAML re-read.

---

### Task R.3 — Phase-3: split runner.py:272 to drain queued intents at open (re-evaluate + submit through chokepoint)

Currently `if is_daily_bar and market_open: return []` (runner.py:272) is a hard no-op. Split it: while market is open, **after the rollover reconcile (runner.py:268-269)**, drain active queued intents, then return `[]`. The drain re-evaluates and submits; it **MUST NOT** touch `_last_processed_bar_at` (so the authoritative post-close eval still fires unchanged).

1. **Write failing test** — `tests/milodex/strategies/test_runner_queued_intent_drain.py`:
```python
def test_at_open_drain_reevaluates_and_submits_via_chokepoint(open_market_runner, event_store):
    runner, broker, provider = open_market_runner   # market_open=True
    # Pre-seed a queued entry from a prior session's lockin.
    _seed_queued_entry(event_store, runner, symbol="SPY", side="BUY")
    runner._now = lambda: _OPEN_NOW
    results = runner.run_cycle()

    # Submitted via the execution chokepoint with the queued idempotency_key.
    assert len(broker.submitted_orders) == 1
    submit_kwargs = runner._execution_service.submit_paper_calls[-1]
    assert submit_kwargs["idempotency_key"].endswith("|BUY|SPY")
    # Drain returns [] and the authoritative-eval watermark is untouched.
    assert results == []
    assert runner._last_processed_bar_at is None


def test_at_open_drain_does_not_suppress_authoritative_post_close_eval(open_then_closed_runner, event_store):
    """Watermark integrity: an at-open drain must not advance _last_processed_bar_at,
    so the SAME session's post-close lockin still produces the authoritative eval."""
    runner, broker, provider = open_then_closed_runner
    _seed_queued_entry(event_store, runner, symbol="SPY", side="BUY")
    # Open cycle: drain.
    runner._broker.set_market_open(True); runner._now = lambda: _OPEN_NOW
    runner.run_cycle()
    assert runner._last_processed_bar_at is None  # untouched by drain
    # Post-close cycles: lockin still advances the watermark exactly once.
    runner._broker.set_market_open(False); runner._now = lambda: _CLOSE_NOW_1
    runner.run_cycle()
    runner._now = lambda: _CLOSE_NOW_2
    runner.run_cycle()
    assert runner._last_processed_bar_at is not None


def test_drain_verifies_config_hash_and_drops_on_mismatch(open_market_runner, event_store):
    runner, broker, _ = open_market_runner
    _seed_queued_entry(event_store, runner, symbol="SPY", side="BUY", config_hash="STALE")
    runner._now = lambda: _OPEN_NOW
    runner.run_cycle()
    assert broker.submitted_orders == []  # hash mismatch -> drop, never submit


def test_drain_zero_share_entry_dropped(open_market_runner, event_store):
    runner, broker, _ = open_market_runner
    _seed_queued_entry(event_store, runner, symbol="SPY", side="BUY")
    # Force re-evaluation to size 0 shares (equity too small for unit price).
    runner._loaded.strategy.evaluate = lambda *a, **k: _zero_share_buy_decision()
    runner._now = lambda: _OPEN_NOW
    runner.run_cycle()
    assert broker.submitted_orders == []  # 0-share entry -> drop, no submit, no obsolete


def test_drain_zero_share_exit_on_flat_ledger_marked_obsolete(open_market_runner, event_store):
    runner, broker, _ = open_market_runner
    intent_id = _seed_queued_entry(event_store, runner, symbol="SPY", side="SELL", intent_class="exit")
    runner._current_positions = lambda: {}                 # flat ledger
    runner._loaded.strategy.evaluate = lambda *a, **k: _zero_share_sell_decision()
    runner._now = lambda: _OPEN_NOW
    runner.run_cycle()
    assert broker.submitted_orders == []
    # Obsolete, not silently dropped — the exit can never apply to a flat lot.
    row = event_store.get_queued_intent(intent_id)
    assert row.status == "obsolete"


def test_drain_halted_symbol_dropped(open_market_runner, event_store, monkeypatch):
    runner, broker, _ = open_market_runner
    _seed_queued_entry(event_store, runner, symbol="SPY", side="BUY")
    monkeypatch.setattr("milodex.strategies.runner.intent_is_tradable", lambda *a, **k: False)
    runner._now = lambda: _OPEN_NOW
    runner.run_cycle()
    assert broker.submitted_orders == []  # halt/ambiguous -> drop (exit alert handled in alerts section)
```
   `open_market_runner` wires `_build_lockin_runner(market_open=True)`; `StubBroker` gains `set_market_open` and records `submitted_orders`; the stub `ExecutionService` records `submit_paper_calls` (kwargs incl. `idempotency_key`). `_seed_queued_entry` inserts a `QueuedIntentEvent` whose `session_id == runner.session_id` (clean-handoff fence I-4) and returns its id.

2. **Run, see fail** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner_queued_intent_drain.py`.

3. **Implement** — split runner.py:272 and add `_drain_queued_intents`:
```python
        if is_daily_bar and market_open:
            # Phase-3 (TOCTOU): the post-close lockin enqueued today's intent;
            # at the next open, re-evaluate it against a fresh context and
            # submit through the chokepoint. MUST run AFTER the rollover
            # reconcile (above) and MUST NOT advance _last_processed_bar_at —
            # the authoritative post-close evaluation still owns the watermark.
            self._drain_queued_intents()
            return []
```
   (Replace the bare `return []` at runner.py:272-273 with the above; the rollover reconcile at runner.py:268-269 already ran.)
```python
    def _drain_queued_intents(self) -> None:
        """Re-evaluate and submit each active queued intent at market open.

        get_active_queued_intents is the SOLE drain authority — it returns only
        status='queued', unexpired, clean-handoff rows (this session, or an
        originating controlled_stop). For each: verify config_hash, apply the
        tradable/halt DROP helper, then RE-RUN evaluate() against a fresh
        context to recompute sizing + signal, and resubmit via submit_paper with
        the queued idempotency_key (the CAS in _submit_locked makes the submit
        exactly-once). 0-share entry -> drop; 0-share exit on a flat ledger ->
        mark_obsolete; halt/ambiguous -> drop."""
        intents = self._event_store.get_active_queued_intents(
            self._strategy_id, now=self._now(), running_session_id=self._session_id,
        )
        if not intents:
            return
        live_config_hash = self._risk_config_hash()
        bars_by_symbol = self._fetch_bars_by_symbol()
        primary_bars = bars_by_symbol[self._evaluation_symbol()]
        account = self._broker.get_account()
        context = replace(
            self._loaded.context,
            positions=self._current_positions(),
            equity=account.equity,
            bars_by_symbol=bars_by_symbol,
            entry_state=self._build_entry_state(),
        )
        for queued in intents:
            if queued.config_hash != live_config_hash:
                # Config changed since lockin — never execute against new policy.
                logger.warning("drain: config_hash mismatch for %s; dropping",
                               queued.idempotency_key)
                continue
            if not intent_is_tradable(self._broker, queued.symbol):
                # Halted / non-tradable / ambiguous asset -> drop. The exit-side
                # operator alert is raised in the alerts section, not here.
                logger.warning("drain: %s not tradable; dropping", queued.symbol)
                continue
            decision = self._loaded.strategy.evaluate(primary_bars, context)
            match = self._match_drain_intent(decision.intents, queued)
            if match is None or match.quantity <= 0:
                if queued.intent_class == "exit" and not self._current_positions().get(queued.symbol):
                    # 0-share exit against a flat ledger can never apply.
                    self._event_store.mark_queued_intent_obsolete(queued.id)
                # 0-share entry (or no re-derived match) -> drop, leave queued
                # to expire via the sweep; never submit a 0-share order.
                continue
            self._execution_service.submit_paper(
                self._runner_intent(match),
                session_id=self._session_id,
                reasoning=decision.reasoning,
                idempotency_key=queued.idempotency_key,
            )

    def _match_drain_intent(self, intents, queued):
        """Re-derived intent matching the queued symbol+side, or None."""
        for intent in intents:
            if (intent.normalized_symbol() == queued.symbol
                    and intent.side.value == queued.side):
                return intent
        return None
```
   Add import `from milodex.broker.tradability import intent_is_tradable` (the DROP helper from the broker section).

4. **Run, see pass** — `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner_queued_intent_drain.py` then the full daily-runner regression `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner.py` and lint `.venv/Scripts/python.exe -m ruff check src/ tests/`.

5. **Commit** — `feat(runner): drain queued intents at open via chokepoint (Phase-3 TOCTOU)`.

6. **Dispatch risk-invariant-reviewer (Opus) on this diff.** Reviewer must confirm: drain runs after rollover reconcile; `_last_processed_bar_at` is never written on the drain path (watermark integrity — the authoritative post-close eval is not suppressed); `get_active_queued_intents` is the only row source (no ad-hoc status query); config_hash verified before any submit; 0-share entry drops (no submit, no obsolete) while 0-share exit-on-flat marks obsolete; submit always carries the queued `idempotency_key` so the `_submit_locked` CAS enforces exactly-once; halt/ambiguous drops without submitting.

---

### Task R.4 — Integration regression: full daily lifecycle (persist→drain) single-session

End-to-end across both phases in one runner, proving the watermark advances exactly once and the at-open drain submits exactly once without re-firing the post-close eval.

1. **Write failing test** — append to `test_runner_queued_intent_drain.py`:
```python
def test_full_daily_lifecycle_persist_then_drain_single_submit(lifecycle_runner, event_store):
    runner, broker, provider = lifecycle_runner
    # Day 1 post-close: lockin -> persist (no submit).
    runner._broker.set_market_open(False); runner._now = lambda: _DAY1_CLOSE_1
    runner.run_cycle(); runner._now = lambda: _DAY1_CLOSE_2; runner.run_cycle()
    assert broker.submitted_orders == []
    assert runner._last_processed_bar_at is not None
    wm_after_persist = runner._last_processed_bar_at
    # Day 2 open: drain -> single submit via chokepoint; watermark unchanged.
    runner._broker.set_market_open(True); runner._now = lambda: _DAY2_OPEN
    runner.run_cycle()
    assert len(broker.submitted_orders) == 1
    assert runner._last_processed_bar_at == wm_after_persist  # drain did NOT touch it
    # Re-draining the same row is a no-op (CAS already consumed it).
    runner.run_cycle()
    assert len(broker.submitted_orders) == 1
```
   `_DAY2_OPEN` is the next-session open; the queued row's `expires_at` (one session) must still be valid at `_DAY2_OPEN` for the first drain (verify the contract's one-session budget is generous enough — if `expires_at = created_at + 1 day` and the open is ~17h later, it is valid).

2. **Run, see fail / then pass** after R.2+R.3 land — `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner_queued_intent_drain.py::test_full_daily_lifecycle_persist_then_drain_single_submit`.

3. **Implement** — no new production code expected; if the re-drain no-op fails, the gap is the `_submit_locked` CAS (other section) not this runner code — file it back to the execution-service section, do not patch the runner to dedupe.

4. **Run full gate** — `.venv/Scripts/python.exe -m pytest -q` (expect the green baseline plus the new tests: 3294→3294+N passed, 1 skipped, 4 xfailed) and `.venv/Scripts/python.exe -m ruff check src/ tests/`.

5. **Commit** — `test(runner): end-to-end daily persist→drain lifecycle (TOCTOU integration)`.

6. **Dispatch risk-invariant-reviewer (Opus) on this diff.**

---

**Cross-section reconciliation notes for the synthesizer:**
- This section must land **after** the event-store quad section (`QueuedIntentEvent`, `append_queued_intent`, `get_active_queued_intents`, `mark_queued_intent_obsolete`, `_queued_intent_from_row`, migration 016) and **after** the broker tradable/halt DROP helper section and the `submit_paper(idempotency_key=...)` threading + `_submit_locked` CAS section. The runner imports all three.
- The runner deliberately does **not** call `mark_queued_intent_consumed` / `mark_queued_intent_expired` — `consumed` is the `_submit_locked` CAS's job, `expired` is the operations-sweep section's job. The runner only enqueues (`append`) and marks `obsolete` (flat-ledger exit).
- `test_runner.py` existing daily tests (`test_daily_post_close_current_bar_locks_in`:1635, the in-progress-bar regressions at 1663+) must stay green — the watermark-advance behavior they pin is unchanged; only the *submit* leg of the daily post-close path moved to persist. If any of those tests asserted a broker submit on the daily post-close path, they need updating to assert a persisted row instead — flag, do not silently delete.


---

# Phase 7: Expiry sweep + exit-drop operator alert + end-to-end drill + D-6 evidence matrix

## Section E — Expiry sweep, exit-drop operator alert, end-to-end drill, D-6 evidence

This section folds the four cross-cutting closures on top of the queued-intent persist/drain mechanism shipped in the earlier sections. It assumes migration `016_queued_intents.sql`, the `QueuedIntentEvent` quad, the `idempotency_key`-threaded submit, and the runner Phase-1-persist / Phase-3-drain rewiring already exist and are green. Test/lint after every task:
`.venv/Scripts/python.exe -m pytest -q <path>` and `.venv/Scripts/python.exe -m ruff check src/ tests/`.
Green baseline before starting: `3294 passed, 1 skipped, 4 xfailed`.

---

### Task 1 — `expire_stale_queued_intents` on the event store (single-statement sweep + audit count)

The sweep is a bulk CAS, mirroring the single-statement discipline of `mark_queued_intent_consumed`. It flips only `status='queued'` rows whose `expires_at` has passed, never touching `consumed`/`obsolete`. It returns the count swept so the runner can decide whether to write an audit row.

1. **Write failing test** — append to `tests/milodex/core/test_event_store_operator_alerts.py` (new file; shares the module with Task 4):
```python
from datetime import UTC, datetime, timedelta

from milodex.core.event_store import EventStore, QueuedIntentEvent


def _queued(store, *, key, expires_at, status="queued"):
    return store.append_queued_intent(
        QueuedIntentEvent(
            idempotency_key=key,
            strategy_id="rsi2.mr.etf.v1",
            strategy_config_path="configs/rsi2.yaml",
            config_hash="abc",
            session_id="sess-1",
            trading_session="2026-06-22",
            locked_in_bar_timestamp="2026-06-22T20:00:00+00:00",
            symbol="SPY",
            side="buy",
            intent_class="entry",
            notional_pct=0.1,
            expected_stage="paper",
            expected_max_positions=3,
            expected_max_position_pct=0.2,
            expected_daily_loss_cap_pct=0.03,
            intent_payload_json="{}",
            reasoning_json="{}",
            created_at=datetime(2026, 6, 22, 19, tzinfo=UTC).isoformat(),
            expires_at=expires_at,
            status=status,
        )
    )


def test_expire_stale_queued_intents_flips_only_expired_queued_rows(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 6, 23, 15, tzinfo=UTC)
    fresh = _queued(store, key="k-fresh", expires_at=(now + timedelta(hours=1)).isoformat())
    stale = _queued(store, key="k-stale", expires_at=(now - timedelta(hours=1)).isoformat())

    swept = store.expire_stale_queued_intents(now=now, audited_by="rsi2.mr.etf.v1")

    assert swept == 1
    by_status = {q.idempotency_key: q.status for q in store.list_queued_intents_by_status("expired")}
    assert by_status == {"k-stale": "expired"}
    still_queued = {q.idempotency_key for q in store.list_queued_intents_by_status("queued")}
    assert still_queued == {"k-fresh"}


def test_expire_stale_queued_intents_never_touches_consumed_or_obsolete(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    now = datetime(2026, 6, 23, 15, tzinfo=UTC)
    past = (now - timedelta(hours=1)).isoformat()
    _queued(store, key="k-consumed", expires_at=past, status="consumed")
    _queued(store, key="k-obsolete", expires_at=past, status="obsolete")

    swept = store.expire_stale_queued_intents(now=now, audited_by="op")

    assert swept == 0
    assert store.list_queued_intents_by_status("expired") == []
```
2. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_event_store_operator_alerts.py` — fails (`expire_stale_queued_intents` / `list_queued_intents_by_status` not defined).
3. **Implement** in `src/milodex/core/event_store.py`, placed next to the queued-intent quad (mirror the `mark_queued_intent_consumed` single-statement CAS):
```python
    def expire_stale_queued_intents(self, *, now: datetime, audited_by: str) -> int:
        """Flip every still-``'queued'`` row whose ``expires_at`` has passed to
        ``'expired'`` in one statement; return the number swept.

        Mirrors the single-statement CAS of :meth:`mark_queued_intent_consumed`:
        the ``status='queued'`` predicate guarantees a ``'consumed'`` or
        ``'obsolete'`` row is never re-touched, so the sweep is idempotent and
        cannot race a concurrent drain (a row consumed between read and sweep
        no longer matches the predicate). ``audited_by`` is recorded by the
        caller's audit row, not here — this method only mutates status.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE queued_intents SET status='expired' "
                "WHERE status='queued' AND datetime(expires_at) <= datetime(?)",
                (now.astimezone(UTC).isoformat(),),
            )
            connection.commit()
            return int(cursor.rowcount)

    def list_queued_intents_by_status(self, status: str) -> list[QueuedIntentEvent]:
        """Return queued-intent rows in ``status`` ordered by id (test/audit use)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM queued_intents WHERE status = ? ORDER BY id ASC",
                (status,),
            ).fetchall()
        return [self._queued_intent_from_row(row) for row in rows]
```
4. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_event_store_operator_alerts.py`.
5. **Commit:** `feat(event-store): bulk expiry sweep + status listing for queued intents`.

---

### Task 2 — Fold the expiry sweep into the runner's startup/rollover reconcile (SACRED-adjacent)

The sweep runs at the same two launch-time / day-rollover moments reconciliation does — NOT on a daemon (I-9-safe). It is invoked from a single new private method called immediately after `_maybe_rollover_reconciliation()`, so it fires on first cycle (startup reconcile path) and on each NY-day rollover. It writes one durable audit explanation row only when rows were swept, and MUST NOT touch `_last_processed_bar_at` or the lockin watermark.

1. **Write failing test** — `tests/milodex/strategies/test_runner_expiry_sweep.py` (new file; reuse the runner-construction harness from `tests/milodex/strategies/test_runner.py`):
```python
from datetime import UTC, datetime, timedelta

from milodex.core.event_store import QueuedIntentEvent


def test_run_cycle_sweeps_expired_queued_intents_at_startup(make_runner, event_store):
    """A queued row that expired before launch is flipped to 'expired' on the
    first cycle, and an audit explanation row records the sweep."""
    now = datetime(2026, 6, 23, 15, tzinfo=UTC)
    event_store.append_queued_intent(
        QueuedIntentEvent(
            idempotency_key="rsi2.mr.etf.v1|2026-06-22|buy|SPY",
            strategy_id="rsi2.mr.etf.v1",
            strategy_config_path="configs/rsi2.yaml",
            config_hash="abc",
            session_id="old-sess",
            trading_session="2026-06-22",
            locked_in_bar_timestamp="2026-06-22T20:00:00+00:00",
            symbol="SPY",
            side="buy",
            intent_class="entry",
            notional_pct=0.1,
            expected_stage="paper",
            expected_max_positions=3,
            expected_max_position_pct=0.2,
            expected_daily_loss_cap_pct=0.03,
            intent_payload_json="{}",
            reasoning_json="{}",
            created_at=(now - timedelta(days=1)).isoformat(),
            expires_at=(now - timedelta(hours=2)).isoformat(),
            status="queued",
        )
    )
    runner = make_runner(now=now)  # daily, market-closed -> early-out cycle

    watermark_before = runner._last_processed_bar_at
    runner.run_cycle()

    assert [q.idempotency_key for q in event_store.list_queued_intents_by_status("expired")] == [
        "rsi2.mr.etf.v1|2026-06-22|buy|SPY"
    ]
    # I-9 / lockin invariant: the sweep is reconcile-time bookkeeping only.
    assert runner._last_processed_bar_at == watermark_before
    audits = [
        e for e in event_store.list_explanations()
        if e.reason_code == "queued_intent_expiry_sweep"
    ]
    assert len(audits) == 1 and audits[0].submitted_by == "strategy_runner"


def test_run_cycle_writes_no_audit_when_nothing_expired(make_runner, event_store):
    runner = make_runner(now=datetime(2026, 6, 23, 15, tzinfo=UTC))
    runner.run_cycle()
    assert not [
        e for e in event_store.list_explanations()
        if e.reason_code == "queued_intent_expiry_sweep"
    ]
```
2. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner_expiry_sweep.py`.
3. **Implement** in `src/milodex/strategies/runner.py`. Insert the sweep call at `run_cycle` line 269, immediately after `self._maybe_rollover_reconciliation()` and BEFORE the `market_open` read at line 270:
```python
        self._ensure_startup_reconciliation()
        self._maybe_rollover_reconciliation()
        self._sweep_expired_queued_intents()
        market_open = self._broker.is_market_open()
```
Then add the method next to `_maybe_rollover_reconciliation` (around line 492):
```python
    def _sweep_expired_queued_intents(self) -> None:
        """Flip expired ``'queued'`` rows to ``'expired'`` at the same launch /
        day-rollover cadence reconciliation runs (I-9: NOT a daemon — driven by
        the run_cycle reconcile path, no background thread).

        Writes one durable audit explanation only when rows were actually swept,
        so a quiet fleet generates no audit noise. This method deliberately
        does NOT read or write ``_last_processed_bar_at`` / the lockin
        watermark: it is reconcile-time bookkeeping over the queued-intent
        ledger, entirely independent of bar processing.
        """
        swept = self._event_store.expire_stale_queued_intents(
            now=self._now(), audited_by=self._loaded.strategy_id
        )
        if swept == 0:
            return
        self._event_store.append_explanation(
            ExplanationEvent(
                session_id=self._session_id,
                submitted_by="strategy_runner",
                recorded_at=self._now(),
                symbol=self._evaluation_symbol(),
                decision_type="no_action",
                summary=f"Expired {swept} stale queued intent(s) at reconcile.",
                reason_code="queued_intent_expiry_sweep",
                context_json={"swept": swept, "strategy_id": self._loaded.strategy_id},
            )
        )
```
   (Confirm `ExplanationEvent` is already imported in `runner.py`; if not, add it to the existing `from milodex.core.event_store import` line.)
4. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/strategies/test_runner_expiry_sweep.py`.
5. **Commit:** `feat(runner): fold queued-intent expiry sweep into startup/rollover reconcile`.
6. **Dispatch risk-invariant-reviewer (Opus) on this diff.** Reviewer must confirm: sweep is launch/rollover-triggered only (no daemon — I-9); never touches `_last_processed_bar_at` or the lockin watermark; flips only `queued`→`expired`; audit row is observational and does not submit or alter risk state.

---

### Task 3 — `OperatorAlertEvent` + migration `017_operator_alerts.sql` + event-store quad

A durable operator-alert ledger row, mirroring the `ExecutionAttemptEvent` quad pattern (frozen dataclass + `append_*` + `list_*` + `_*_from_row`). `MIN_COMPATIBLE_SCHEMA_VERSION` stays 12 (additive table, no existing reader).

1. **Write failing test** — append to `tests/milodex/core/test_event_store_operator_alerts.py`:
```python
from milodex.core.event_store import MIN_COMPATIBLE_SCHEMA_VERSION, OperatorAlertEvent


def test_min_compatible_schema_unchanged_after_operator_alerts_migration():
    assert MIN_COMPATIBLE_SCHEMA_VERSION == 12


def test_append_and_list_operator_alert_roundtrip(tmp_path):
    store = EventStore(tmp_path / "milodex.db")
    alert = OperatorAlertEvent(
        alert_type="exit_intent_dropped",
        severity="warning",
        summary="EXIT intent for SPY dropped: clean-handoff ambiguity.",
        strategy_id="rsi2.mr.etf.v1",
        session_id="sess-1",
        symbol="SPY",
        side="sell",
        context_json={"reason": "no_clean_handoff", "idempotency_key": "rsi2.mr.etf.v1|2026-06-22|sell|SPY"},
        recorded_at=datetime(2026, 6, 23, 15, tzinfo=UTC),
    )
    new_id = store.append_operator_alert(alert)
    assert isinstance(new_id, int)

    rows = store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(rows) == 1
    assert rows[0].symbol == "SPY" and rows[0].severity == "warning"
    assert rows[0].context_json["reason"] == "no_clean_handoff"
```
2. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_event_store_operator_alerts.py`.
3. **Implement:**
   a. New migration `src/milodex/core/migrations/017_operator_alerts.sql`:
```sql
-- Operator-alert ledger (D-6 durable-state clause).
--
-- An append-only record of operator-visible anomalies emitted outside the
-- explanation lane — e.g. an EXIT intent dropped for clean-handoff ambiguity
-- (I-4 fence). Durable so a relaunch / audit can reconstruct WHY an exit did
-- not drain. Additive only: no existing code reads this table, so the minimum
-- compatible schema version is unchanged.
--
--   alert_type    machine key, e.g. 'exit_intent_dropped'
--   severity      'info' | 'warning' | 'critical'
--   summary       human-readable one-line operator message
--   context_json  structured detail (reason code, idempotency_key, etc.)

CREATE TABLE operator_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    strategy_id TEXT,
    session_id TEXT,
    symbol TEXT,
    side TEXT,
    context_json TEXT
);

CREATE INDEX idx_operator_alerts_alert_type ON operator_alerts(alert_type);
```
   b. Frozen dataclass in `event_store.py` (next to `ExecutionAttemptEvent`, ~line 119):
```python
@dataclass(frozen=True)
class OperatorAlertEvent:
    """Durable operator-visible anomaly row (append-only).

    Emitted alongside a ``logger.warning`` so the alert is both visible in the
    moment (log) and reconstructable after the fact (this ledger). The first
    consumer is the dropped-EXIT-intent alert (clean-handoff ambiguity, I-4).
    """

    alert_type: str
    severity: str
    summary: str
    recorded_at: datetime
    strategy_id: str | None = None
    session_id: str | None = None
    symbol: str | None = None
    side: str | None = None
    context_json: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
```
   c. `append_operator_alert` + `list_operator_alerts` (mirror `append_execution_attempt` / `list_*` style) and `_operator_alert_from_row` (mirror `_execution_attempt_from_row` at line 2482), serializing `context_json` via the existing `_dump_json` / `_load_json` helpers.
4. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_event_store_operator_alerts.py`.
5. **Commit:** `feat(event-store): durable operator-alert ledger (migration 017)`.

---

### Task 4 — Emit the dropped-EXIT-intent operator alert (event row + `logger.warning`)

When the drain path declines to enqueue/submit an EXIT intent because the clean-handoff fence (I-4) does not hold — a stranded `exit` row whose originating session is not the running session and whose `strategy_runs.exit_reason` is not the literal `'controlled_stop'` — the runner must emit a durable operator alert AND a `logger.warning`. This is the asymmetry guard: silently dropping an entry is acceptable (it just doesn't fire); silently dropping an EXIT can strand a position.

1. **Write failing test** — `tests/milodex/execution/test_exit_drop_operator_alert.py`:
```python
import logging
from datetime import UTC, datetime


def test_dropped_exit_intent_emits_durable_alert_and_warning(make_runner, event_store, caplog):
    """An EXIT intent that fails the clean-handoff fence emits both a
    logger.warning and a durable operator_alerts row."""
    runner = make_runner(now=datetime(2026, 6, 23, 15, tzinfo=UTC))
    dropped = _exit_intent(symbol="SPY", side="sell")  # helper builds a runner intent

    with caplog.at_level(logging.WARNING):
        runner._emit_exit_drop_alert(dropped, reason="no_clean_handoff")

    alerts = event_store.list_operator_alerts(alert_type="exit_intent_dropped")
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "warning" and a.symbol == "SPY" and a.side == "sell"
    assert a.context_json["reason"] == "no_clean_handoff"
    assert any("EXIT intent" in r.message and "SPY" in r.message for r in caplog.records)


def test_entry_intent_drop_does_not_emit_alert(make_runner, event_store):
    """Dropping an entry intent is silent — only EXIT drops alert."""
    runner = make_runner(now=datetime(2026, 6, 23, 15, tzinfo=UTC))
    # _emit_exit_drop_alert is only ever called on the exit branch; assert the
    # ledger stays empty when no exit drop occurs in a normal cycle.
    runner.run_cycle()
    assert event_store.list_operator_alerts(alert_type="exit_intent_dropped") == []
```
2. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/execution/test_exit_drop_operator_alert.py`.
3. **Implement** `_emit_exit_drop_alert` in `runner.py` and call it from the drain path's exit-drop branch (the branch that decides an `intent_class='exit'` queued row is NOT drainable because `get_active_queued_intents` excluded it / the fence failed):
```python
    def _emit_exit_drop_alert(self, intent, *, reason: str) -> None:
        """Durably record + warn when an EXIT intent is dropped for ambiguity.

        Asymmetry guard: an undrained entry is benign (no fire); an undrained
        exit can strand a live position, so it must be operator-visible. This
        is observational only — it does NOT submit, retry, or mutate risk state.
        """
        symbol = intent.normalized_symbol()
        logger.warning(
            "EXIT intent dropped for %s (%s): %s. Position may remain open; "
            "operator review required.",
            symbol,
            intent.side,
            reason,
        )
        self._event_store.append_operator_alert(
            OperatorAlertEvent(
                alert_type="exit_intent_dropped",
                severity="warning",
                summary=f"EXIT intent for {symbol} dropped: {reason}.",
                strategy_id=self._loaded.strategy_id,
                session_id=self._session_id,
                symbol=symbol,
                side=str(intent.side),
                context_json={"reason": reason},
                recorded_at=self._now(),
            )
        )
```
   Wire the call into the Phase-3 drain branch: when iterating candidate stranded rows, for any row with `intent_class == "exit"` that `get_active_queued_intents` did not return (fence failed), call `self._emit_exit_drop_alert(...)`. Add `OperatorAlertEvent` to the `event_store` import line.
4. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/execution/test_exit_drop_operator_alert.py`.
5. **Commit:** `feat(runner): durable operator alert when an EXIT intent is dropped for ambiguity`.
6. **Dispatch risk-invariant-reviewer (Opus) on this diff.** Reviewer must confirm the alert is observational only (no submit / retry / risk mutation), fires on the EXIT branch only, and reads the I-4 fence (`exit_reason == 'controlled_stop'` literal, never `IS NOT NULL`).

---

### Task 5 — End-to-end operational drill: persist → controlled-stop → relaunch → clean-handoff → drain → fill

The capstone integration test. It exercises the full lifecycle across a session boundary and asserts the entire explanation chain (decision → persist → drain → submit → fill) is reconstructable from the durable store. Lives in a new `tests/milodex/integration/` package.

1. **Create package marker** — `tests/milodex/integration/__init__.py` (empty).
2. **Write failing test** — `tests/milodex/integration/test_persist_relaunch_drain_drill.py`:
```python
"""End-to-end operational drill: a lockin-confirmed intent persists at close,
the session is controlled-stopped, a NEW session relaunches, the clean-handoff
fence passes, the intent drains, submits, and fills — and the full explanation
chain is reconstructable from the durable event store."""

from datetime import UTC, datetime


def test_persist_controlled_stop_relaunch_drain_fill_chain(
    make_runner, fake_broker, event_store
):
    close = datetime(2026, 6, 22, 20, tzinfo=UTC)

    # --- Phase 1: persist at close (NOT submit) ---
    runner_a = make_runner(now=close, session_id="sess-A", at_close_with_signal=True)
    runner_a.run_cycle()
    queued = event_store.list_queued_intents_by_status("queued")
    assert len(queued) == 1
    key = queued[0].idempotency_key
    assert fake_broker.submitted_orders == []  # persisted, not submitted

    # --- Controlled stop on session A ---
    runner_a.shutdown(mode="controlled_stop")
    run_a = event_store.get_strategy_run("sess-A")
    assert run_a.exit_reason == "controlled_stop"  # I-4 fence will pass

    # --- Phase 3: relaunch as a NEW session, market open next day ---
    nextday = datetime(2026, 6, 23, 14, tzinfo=UTC)
    runner_b = make_runner(now=nextday, session_id="sess-B", market_open=True)
    fake_broker.fill_next_submit = True
    runner_b.run_cycle()

    # Clean-handoff held (originating exit_reason == 'controlled_stop'); drained.
    drained = event_store.list_queued_intents_by_status("consumed")
    assert [q.idempotency_key for q in drained] == [key]
    assert len(fake_broker.submitted_orders) == 1  # exactly one submit (CAS)

    # --- Full chain reconstructable from durable state ---
    consumed_row = drained[0]
    assert consumed_row.consumed_by == "sess-B"          # drain -> submit attribution
    trades = event_store.list_trades_for_session("sess-B")
    assert len(trades) == 1 and trades[0].status == "filled"   # fill
    # decision -> persist: the persisted reasoning_json is non-empty
    assert consumed_row.reasoning_json not in (None, "", "{}")
    # idempotency: a second relaunch drain is a no-op (CAS rowcount 0)
    runner_c = make_runner(now=nextday, session_id="sess-C", market_open=True)
    runner_c.run_cycle()
    assert len(fake_broker.submitted_orders) == 1  # still one — no double-submit


def test_relaunch_without_controlled_stop_drops_exit_intent(
    make_runner, fake_broker, event_store
):
    """Fence negative: an 'interrupted' originating session is NOT a clean
    handoff — an EXIT intent is dropped and an operator alert is emitted."""
    close = datetime(2026, 6, 22, 20, tzinfo=UTC)
    runner_a = make_runner(now=close, session_id="sess-A", at_close_with_exit=True)
    runner_a.run_cycle()
    runner_a.shutdown(mode="interrupted")  # exit_reason='interrupted' -> fence fails

    runner_b = make_runner(
        now=datetime(2026, 6, 23, 14, tzinfo=UTC), session_id="sess-B", market_open=True
    )
    runner_b.run_cycle()

    assert fake_broker.submitted_orders == []  # exit intent NOT drained
    assert event_store.list_queued_intents_by_status("consumed") == []
    assert len(event_store.list_operator_alerts(alert_type="exit_intent_dropped")) == 1
```
   (Build the `make_runner` / `fake_broker` fixtures by reusing/extending the existing harness in `tests/milodex/strategies/test_runner.py`; add the `at_close_with_signal` / `at_close_with_exit` / `fill_next_submit` knobs the drill needs. Mirror the cross-module fixture style of `tests/milodex/cli/test_research_screen_persistence.py`.)
3. **Run-fail:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/integration/test_persist_relaunch_drain_drill.py`.
4. **Implement:** no production code is expected to change — the drill validates the Phase-1/Phase-3 mechanism shipped earlier plus Tasks 1–4. If any assertion fails on a real gap (e.g. `consumed_by` not threaded), fix the minimal production seam, re-run, and note it. Do NOT loosen an assertion to make it pass.
5. **Run-pass:** `.venv/Scripts/python.exe -m pytest -q tests/milodex/integration/test_persist_relaunch_drain_drill.py`.
6. **Commit:** `test(integration): end-to-end persist->controlled-stop->relaunch->drain->fill drill`.

---

### Task 6 — D-6 assurance-evidence matrix (doc-only, written LAST)

A short matrix mapping each D-6 assurance clause to the test id that proves it. Written last so every cited id is real and green (global rule #6: report only verified results).

1. **Run the full suite first** to confirm green and collect exact node ids:
   `.venv/Scripts/python.exe -m pytest -q tests/milodex/core/test_event_store_operator_alerts.py tests/milodex/strategies/test_runner_expiry_sweep.py tests/milodex/execution/test_exit_drop_operator_alert.py tests/milodex/integration/test_persist_relaunch_drain_drill.py`
2. **Write** `docs/assurance/D6_QUEUED_INTENT_EVIDENCE_MATRIX.md`:
```markdown
# D-6 Assurance Evidence — Queued-Intent Persist/Drain Mechanism

Each D-6 clause maps to the test id(s) that exercise it. All cited tests pass
on the green baseline (`3294+ passed`). Citing by clause keeps this durable
when test counts shift.

| Clause | What it asserts | Test id |
|---|---|---|
| Positive | A clean-handoff intent persists, drains, submits, and fills end-to-end | `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_persist_controlled_stop_relaunch_drain_fill_chain` |
| Refusal | A non-`controlled_stop` originating session refuses to drain the intent | `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_relaunch_without_controlled_stop_drops_exit_intent` |
| Boundary | Clean-exit fence keys on literal `exit_reason == 'controlled_stop'`, not `IS NOT NULL` | (drain-authority test from the earlier section) + `..._drops_exit_intent` |
| Fail-closed | A dropped EXIT intent emits a durable operator alert + `logger.warning` | `tests/milodex/execution/test_exit_drop_operator_alert.py::test_dropped_exit_intent_emits_durable_alert_and_warning` |
| Idempotency | A second relaunch drain is a no-op (single-statement CAS, rowcount 0) | `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_persist_controlled_stop_relaunch_drain_fill_chain` (final assertion) |
| Durable-state | Operator alerts + queued intents survive in the event store across sessions | `tests/milodex/core/test_event_store_operator_alerts.py::test_append_and_list_operator_alert_roundtrip` |
| Drill | Full decision→persist→drain→submit→fill chain reconstructable from durable store | `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_persist_controlled_stop_relaunch_drain_fill_chain` |
| Expiry/I-9 | Expiry sweep runs at reconcile cadence only (no daemon), never touches the lockin watermark | `tests/milodex/strategies/test_runner_expiry_sweep.py::test_run_cycle_sweeps_expired_queued_intents_at_startup` |
```
   (Fill the "Boundary" row's first id with the actual drain-authority/`get_active_queued_intents` fence test node id from the earlier section once known.)
3. **Verify** the matrix cites only ids that exist: re-run the four files; confirm each cited node id appears in pytest's collection.
4. **Commit:** `docs(assurance): D-6 queued-intent evidence matrix (clause -> test id)`.

---

**Section-level done check:** full suite green (`.venv/Scripts/python.exe -m pytest -q`) with the new tests added (baseline `3294 passed` grows by the new count; still `1 skipped, 4 xfailed`), `ruff check src/ tests/` clean, and both sacred-path diffs (Tasks 2, 4) cleared by the risk-invariant-reviewer.