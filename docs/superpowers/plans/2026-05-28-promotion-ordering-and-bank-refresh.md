# Promotion-Ordering Fix + Strategy-Bank Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the promotion-stage read path so it returns the most recent event by wall-clock time (not insertion order), correcting a live wrong answer the risk-layer report currently shows for `pullback_rsi2`; then refresh `docs/STRATEGY_BANK.md` to match the true current bank state.

**Architecture:** Two independent, separately-shippable changes.
- **Plan C (code, critical path):** `EventStore.get_latest_promotion_for_strategy` orders `promotions` by `id DESC`. Insertion order ≠ event order when an event is backdated (the `audit_backfill` demotion id=9 carries `recorded_at=2026-05-06T20:00`, which sorts *before* the real id=7 paper promotion at 2026-05-07T13:34). Change the ordering to `recorded_at DESC, id DESC` (wall-clock primary, insertion-order tiebreak for determinism). One read path, two callers, both improved by the change.
- **Plan D (doc, no code risk):** `docs/STRATEGY_BANK.md` reflects 2026-05-20 (`feat/intraday-orb-spy-v1`). The DB has moved on: ORB + intraday benchmark promoted to paper 2026-05-28 (ids 24/25), and three strategies demoted to a new `idle` stage on 05-19 (ids 14/15/16) that the doc's two-category schema doesn't model. Regenerate from `data/milodex.db` and add an `idle` category.

**Tech Stack:** Python 3.11+, SQLite (event store), pytest, ruff.

**Why these two and not #4/#5/#6:** Investigation (2026-05-28) reclassified the rest of the backlog:
- **#4 (fill detection)** is unbuilt by design — `src/milodex/operations/reconciliation.py:38` marks `filled_since_last_sync` as a *deferred* R-OPS-004 v1.2 check. There is no broker poll, no `UPDATE trades.broker_status`, and no event-store update method. Building it is a multi-PR feature requiring its own brainstorm, not a backlog patch.
- **#5 (sparse Order/Signal Tape)** is strictly downstream of #4: `src/milodex/gui/activity_feed_state.py:139` renders fills only `WHERE broker_status='filled'`, and that column is never advanced past submission. Nothing to fix here until #4 writes fills.
- **#6 (cross-strategy P&L attribution)** is a real architecture gap (P&L from broker position at `analytics/snapshots.py:81` / `metrics.py:234`, not from the intent log) that needs a design decision / ADR. `risk/attribution.py` already reconstructs intent-ownership pre-trade but is not used post-trade for P&L. Out of scope for a quick fix.

---

## Background: the ordering bug (grounded)

`src/milodex/core/event_store.py:1221-1228`:

```python
def get_latest_promotion_for_strategy(self, strategy_id: str) -> PromotionEvent | None:
    """Return the most recent promotion for ``strategy_id``, or None."""
    with self._connect() as connection:
        row = connection.execute(
            "SELECT * FROM promotions WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()
    return None if row is None else _promotion_from_row(row)
```

**Schema (migrations `004_promotions.sql` + `007_promotion_evidence.sql`):** `promotions` has `id INTEGER PK AUTOINCREMENT`, `recorded_at TEXT NOT NULL` (ISO-8601), `strategy_id`, `to_stage`, `promotion_type` (`statistical` | `lifecycle_exempt` | `demotion` | `stage_return`), `reverses_event_id INTEGER` (FK → `promotions(id)`).

**Live divergence (verified against `data/milodex.db` 2026-05-28):**

| strategy | `ORDER BY id DESC` (current) | `ORDER BY recorded_at DESC` (correct) |
|----------|------------------------------|----------------------------------------|
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | id=9 → **backtest** (recorded_at 2026-05-06T20:00, an `audit_backfill` demotion) | id=7 → **paper** (recorded_at 2026-05-07T13:34) |

All ten other strategies agree under both orderings; `pullback_rsi2` is the sole live divergence, caused by the ADR-0032 `audit_backfill` event being intentionally backdated.

**Two callers (both improved, neither broken):**
- `src/milodex/cli/commands/report.py:604` (`_resolve_runtime_stage`) — feeds the risk-layer trust/strategy report. Currently reports `pullback_rsi2` as `backtest`; after the fix reports `paper` (correct; matches `to_stage='paper'`-filtered queries elsewhere).
- `src/milodex/promotion/state_machine.py:287` (`demote`) — uses the result as `prior` to set `reverses_event_id` (only when `prior.promotion_type != "demotion"`). After the fix, a new demotion of `pullback_rsi2` would reverse the latest *paper* promotion (id=7, `statistical`) instead of the older backdated demotion (id=9) — strictly more correct.

**Tiebreak:** `recorded_at` is TEXT and synthetic/backfilled or test-injected events can collide. Order by `recorded_at DESC, id DESC` so equal timestamps fall back to insertion order deterministically.

**Lexical-sort caveat:** `recorded_at` is TEXT, so `ORDER BY recorded_at DESC` is a lexical string sort. This equals chronological order only because every current row serializes with the same `+00:00` UTC offset (the backdated `audit_backfill` uses a shorter sub-format but its date prefix differs first, so it still sorts correctly). All event-store writes are UTC by construction; a future non-UTC-offset timestamp would break the lexical=chronological equivalence. Acceptable today; noted so a future tz change doesn't silently regress.

**Not touched:** `list_promotions_for_strategy` (event_store.py:1208, also `ORDER BY id DESC`) is out of scope — it returns a full list and no current caller depends on its head being the wall-clock latest. Note it in the plan but do not change it (YAGNI; surgical).

---

## Plan C — Promotion-ordering fix (critical path)

**Files:**
- Modify: `src/milodex/core/event_store.py:1225` (the `ORDER BY` clause inside `get_latest_promotion_for_strategy`)
- Test: `tests/milodex/core/test_event_store.py`

### Task C1: Failing test — backdated demotion must not mask a newer promotion

- [ ] **Step 1: Write the failing test**

Add to `tests/milodex/core/test_event_store.py`. Follow the existing promotion-test style in that file (see `test_promotion_event_roundtrips_evidence_fields` at L763 for `PromotionEvent` construction + `append_promotion`). Construct the real divergence: a later-`recorded_at` paper promotion inserted *before* an earlier-`recorded_at` backdated demotion, so `id` order and `recorded_at` order disagree.

```python
def test_get_latest_promotion_orders_by_recorded_at_not_id(tmp_path):
    """A backdated demotion inserted AFTER a newer promotion must not be
    returned as 'latest'. Mirrors the live pullback_rsi2 / audit_backfill case:
    id-order says backtest, wall-clock order says paper (correct)."""
    store = EventStore(tmp_path / "test.db")
    sid = "meanrev.daily.pullback_rsi2.curated_largecap.v1"

    # id=1 (lower id) but LATER wall-clock: the real paper promotion.
    store.append_promotion(
        PromotionEvent(
            strategy_id=sid,
            from_stage="backtest",
            to_stage="paper",
            promotion_type="statistical",
            approved_by="operator",
            recorded_at=datetime(2026, 5, 7, 13, 34, tzinfo=UTC),
        )
    )
    # id=2 (higher id) but EARLIER wall-clock: the backdated audit_backfill demotion.
    store.append_promotion(
        PromotionEvent(
            strategy_id=sid,
            from_stage="micro_live",
            to_stage="backtest",
            promotion_type="demotion",
            approved_by="audit_backfill",
            recorded_at=datetime(2026, 5, 6, 20, 0, tzinfo=UTC),
        )
    )

    latest = store.get_latest_promotion_for_strategy(sid)
    assert latest is not None
    assert latest.to_stage == "paper"  # wall-clock latest, NOT the higher-id demotion
    assert latest.promotion_type == "statistical"
```

Ensure `datetime`, `UTC`, `PromotionEvent`, and `EventStore` are imported at the top of the test module (check existing imports first; add only what's missing).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/milodex/core/test_event_store.py::test_get_latest_promotion_orders_by_recorded_at_not_id -v`
Expected: FAIL — current `ORDER BY id DESC` returns the demotion (`to_stage == "backtest"`).

- [ ] **Step 3: Apply the fix**

In `src/milodex/core/event_store.py:1225`, change:
```python
"SELECT * FROM promotions WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
```
to:
```python
"SELECT * FROM promotions WHERE strategy_id = ? ORDER BY recorded_at DESC, id DESC LIMIT 1",
```
Also update the docstring (L1222) to: `"""Return the most recent promotion for ``strategy_id`` by wall-clock time (``recorded_at``), with ``id`` as a deterministic tiebreak. or None."""`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/milodex/core/test_event_store.py::test_get_latest_promotion_orders_by_recorded_at_not_id -v`
Expected: PASS

- [ ] **Step 5: Add a tiebreak determinism test**

```python
def test_get_latest_promotion_tiebreaks_on_id_when_recorded_at_equal(tmp_path):
    """Equal recorded_at -> higher id wins (deterministic, insertion order)."""
    store = EventStore(tmp_path / "test.db")
    sid = "tie.daily.example.v1"
    ts = datetime(2026, 5, 7, 13, 34, tzinfo=UTC)
    store.append_promotion(PromotionEvent(strategy_id=sid, from_stage="backtest",
        to_stage="paper", promotion_type="statistical", approved_by="op", recorded_at=ts))
    store.append_promotion(PromotionEvent(strategy_id=sid, from_stage="paper",
        to_stage="backtest", promotion_type="demotion", approved_by="op", recorded_at=ts))
    latest = store.get_latest_promotion_for_strategy(sid)
    assert latest.to_stage == "backtest"  # higher id breaks the tie
```

Run: `python -m pytest tests/milodex/core/test_event_store.py::test_get_latest_promotion_tiebreaks_on_id_when_recorded_at_equal -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/milodex/core/event_store.py tests/milodex/core/test_event_store.py
git commit -m "fix(event-store): order latest-promotion read by recorded_at, not insertion id"
```

### Task C2: Regression guard — demote() and report stay green; verify live DB answer flips

- [ ] **Step 1: Run the promotion + event-store + report suites**

Run: `python -m pytest tests/milodex/core/test_event_store.py tests/milodex/promotion/ -q`
Expected: all PASS. `test_demote.py` must stay green — its multi-row chaining test (`test_demote.py:207-242`) inserts all rows with an *identical* `recorded_at=_NOW`, so the new `id DESC` tiebreak picks the same row the old `id DESC` did (no behavior change). `test_state_machine.py` is unaffected by construction — it tests the promotion gate thresholds, not `get_latest_promotion`/`append_promotion`. The fix only changes which row is `prior` in the live *backdated-event* case (distinct timestamps), which no fixture reproduces.

- [ ] **Step 2: Run the report command tests**

Run: `python -m pytest tests/milodex/cli/ -q -k "report or runtime"`
Expected: all PASS (or "no tests ran" if none match — then run the full `tests/milodex/cli/` quickly to confirm nothing regressed).

- [ ] **Step 3: Verify the live DB answer flips (read-only sanity check, not a test)**

Run a one-off read against `data/milodex.db` through the fixed method and confirm `pullback_rsi2` now resolves to `paper`. Use a throwaway script (do NOT commit it). NOTE: `EventStore.__init__` expects a `Path` (it calls `self._path.parent.mkdir(...)`); a bare `str` raises `AttributeError`.
```python
from pathlib import Path
from milodex.core.event_store import EventStore
s = EventStore(Path("data/milodex.db"))
p = s.get_latest_promotion_for_strategy("meanrev.daily.pullback_rsi2.curated_largecap.v1")
print(p.to_stage, p.promotion_type, p.recorded_at)  # expect: paper statistical 2026-05-07...
```
Expected stdout: `paper statistical 2026-05-07T13:34:...`

- [ ] **Step 4: Lint**

Run: `python -m ruff check src/milodex/core/event_store.py tests/milodex/core/test_event_store.py`
Expected: clean.

- [ ] **Step 5: Commit (only if lint auto-fixed something)**

---

## Plan D — Strategy-Bank refresh (doc, no code risk)

**Files:**
- Modify: `docs/STRATEGY_BANK.md`

**Current true state (verified against `data/milodex.db` 2026-05-28, latest promotion per strategy by `recorded_at`):**

| strategy_id | current stage | latest promo id |
|---|---|---|
| `regime.daily.sma200_rotation.spy_shy.v1` | paper (lifecycle_exempt) | 12 |
| `breakout.daily.atr_channel.sector_etfs.v1` | paper (statistical) | 19 |
| `breakout.daily.donchian_20_10.sector_etfs.v1` | paper (statistical) | 21 |
| `meanrev.daily.bbands_lowerband.curated_largecap.v1` | paper (statistical) | 6 |
| `meanrev.daily.pullback_rsi2.curated_largecap.v1` | paper (statistical) | 7 |
| `momentum.daily.tsmom.curated_largecap.v1` | paper (statistical) | 8 |
| `breakout.orb.intraday.spy.v1` | **paper (lifecycle_exempt)** — NEW 05-28 | 25 |
| `benchmark.unconditional_intraday_long.spy.v1` | **paper (lifecycle_exempt)** — NEW 05-28 | 24 |
| `momentum.daily.xsec_rotation.sector_etfs.v1` | **idle (demotion)** — NEW stage | 14 |
| `seasonality.daily.turn_of_month.spy.v1` | **idle (demotion)** | 15 |
| `momentum.daily.52w_high_proximity.largecap.v1` | **idle (demotion)** | 16 |
| `breakout.daily.nr7_inside.liquid_largecap.v1` | backtest (no promotion) | — |
| `meanrev.daily.ibs_lowclose.index_etfs.v1` | backtest (no promotion) | — |
| `momentum.daily.dual_absolute.gem_weekly.v1` | backtest (no promotion) | — |

> **Open question for the operator (surface, do not assume):** ORB and the intraday benchmark were promoted to paper via `lifecycle_exempt` on 05-28, but the existing STRATEGY_BANK callout explicitly says ORB "stays at backtest — does not beat its benchmark and neither candidate meets the capital-readiness gate." A `lifecycle_exempt` paper promotion of a *non-lifecycle-proof* edge candidate is unusual (the exemption is documented as being for the regime strategy). The doc refresh must report the DB truth (paper) but should **flag this contradiction for the operator** rather than silently rewriting the verdict — it may indicate an erroneous promotion that warrants a demotion rather than a doc edit. **Do not resolve this autonomously.**

### Task D1: Verify the live state and regenerate the tables

- [ ] **Step 1: Re-run the bank's own refresh queries against `data/milodex.db`**

Run the "Paper-stage" and "Backtest-stage" SQL blocks from the doc's "How to refresh" section. Capture the output. Note: the paper-stage query (`MAX(id)` filtered `WHERE to_stage='paper'`) returns `pullback_rsi2` id=7 correctly *because* of the `to_stage='paper'` filter — independent of the Plan C fix.

- [ ] **Step 2: Add an `idle`-stage query**

The doc has no `idle` category. Add and run:
```sql
SELECT p.strategy_id, p.recorded_at, p.notes
FROM promotions p
INNER JOIN (SELECT strategy_id, MAX(recorded_at) AS mx FROM promotions GROUP BY strategy_id) l
  ON p.strategy_id = l.strategy_id AND p.recorded_at = l.mx
WHERE p.to_stage = 'idle'
ORDER BY p.strategy_id;
```
Expected: `xsec_rotation`, `seasonality.turn_of_month`, `52w_high_proximity`.

### Task D2: Rewrite the doc sections

- [ ] **Step 1: Update the header and "What can I run today?" intro**

Change the as-of line to reflect the current commit/date. Update the runnable list to the **8** paper strategies above (was 6). Add ORB + benchmark with the operator-flag callout from the Open Question above — explicitly note these are `lifecycle_exempt` paper entries pending operator review, NOT statistical promotions.

- [ ] **Step 2: Add an "Idle" section**

New section between paper and backtest listing the three idle strategies, each with the demotion `recorded_at` and `notes`. Explain `idle` = demoted out of active rotation (distinct from `backtest`, which is pre-promotion).

- [ ] **Step 3: Update the "Backtest-stage — blocked" table**

Remove ORB (now paper) and the three idle strategies from the blocked table. Keep only `nr7_inside`, `ibs_lowclose`, `dual_absolute` (the strategies with no promotion event, still genuinely at backtest). Preserve the `dual_absolute` structural-gate-tension callout. Move/adapt the ORB callout to reflect its new (flagged) paper status rather than deleting the analysis.

- [ ] **Step 4: Update "Doc maintenance" + as-of provenance**

Note the new `idle` stage in the maintenance triggers. Confirm all metric numbers are re-derived from queries, not hand-edited.

- [ ] **Step 5: Commit**

```bash
git add docs/STRATEGY_BANK.md
git commit -m "docs(bank): refresh to current DB state — idle stage, ORB/benchmark paper (flagged)"
```

---

## Out-of-scope (explicitly deferred, with rationale)

| ID | Why deferred |
|----|--------------|
| #4 fill detection | Unbuilt feature (R-OPS-004 v1.2, deferred at `reconciliation.py:38`). Needs its own brainstorm + design: broker poll loop, `EventStore` trade-status UPDATE, reconciliation enforcement, tests. Not a backlog patch. |
| #5 Order/Signal Tape | Strictly downstream of #4 (`activity_feed_state.py:139` keys on `broker_status='filled'`, never written). Re-verify after #4 lands. |
| #6 P&L attribution | Architecture gap needing a design decision/ADR (per-strategy P&L from intent log vs broker position). `risk/attribution.py` has the intent-ownership primitive but it's pre-trade only. |

---

## Notes for the executor

- **Branch first:** repo convention is no direct commits to `master`. Create `fix/promotion-ordering-and-bank-refresh` before Task C1.
- **Plan C and Plan D are independent.** C is the critical-path code fix; D is a doc refresh. They can land as separate commits on the same branch (or separate branches if preferred).
- **Do NOT touch `list_promotions_for_strategy`** (event_store.py:1208) — out of scope, no caller needs its ordering changed (YAGNI / surgical).
- **Do NOT resolve the ORB/benchmark lifecycle_exempt-paper contradiction autonomously** — surface it to the operator (see Plan D Open Question). It may be an erroneous promotion, not a doc-staleness issue.
- **CLI entry:** `python -m milodex.cli.main ...` (not `python -m milodex`).
- **Tests:** `python -m pytest` (bare `pytest` may not be on PATH).
- **System state:** all paper runners cleanly stopped; account flat. No live process depends on these reads at edit time.
