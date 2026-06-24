# D-6 Assurance Evidence — Queued-Intent Persist/Drain Mechanism (queue-at-open, ADR 0057)

Per the founder's **D-6 assurance gate**, every clause M1 touches on the trust-critical
requirement classes (R-EXE / risk-check / R-PRM) owes contract-appropriate, independently
reviewed, per-clause evidence — a happy-path test does not satisfy it. This matrix maps each
assurance clause of the queue-at-open mechanism to the test id(s) that exercise it. Citing by
**clause** (not by count) keeps the matrix durable as the suite grows.

**Scope.** Covers the Phase 6 (runner persist + drain) and Phase 7 (expiry sweep, operator-alert
ledger, exit-drop alert, end-to-end drill) work on `m1/daily-queue-drain`. The merged Phase 1–5
substrate (migration 016, the `QueuedIntentEvent` quad, the session-aware staleness gate, the
idempotency CAS, the `latest_bar_override` threading) carries its own evidence in the
already-merged PR #289 and the spec §9 / §12 review log.

**Authoritative invariants:** `docs/superpowers/specs/2026-06-22-daily-queue-at-open-design.md`
§7 (I-1..I-9) and §9 (assurance categories). **ADR:** `docs/adr/0057-daily-execution-queue-at-open.md`.

All cited tests passed on the verified branch baseline (see "Baseline" at the foot). Run them with
`.venv/Scripts/python.exe -m pytest -q <path>`.

| Clause (spec §9) | What it asserts | Test id(s) |
|---|---|---|
| **Positive — end-to-end** | A clean-handoff intent persists at close, survives a controlled-stop + relaunch, drains, submits through the chokepoint, and **fills** (named fill = `broker_status='filled'`); the decision→persist→drain→submit→fill chain is reconstructable from the durable store. | `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_persist_controlled_stop_relaunch_drain_fill_chain` |
| **Positive — single-session lifecycle** | One runner: day-1 post-close persists (no submit), day-2 open drains exactly once via the chokepoint, watermark untouched, re-drain is a no-op. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_full_daily_lifecycle_persist_then_drain_single_submit` |
| **Positive — session-bar feeding (I-2 carrier)** | The drain feeds the gate the **locked-in session bar** as `latest_bar_override` (override timestamp + close == the persisted bar), not the live intraday bar. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_at_open_drain_reevaluates_and_submits_via_chokepoint` (+ the lifecycle test's override assertions) |
| **Refusal — config drift (I-7)** | A row whose on-disk `config_hash` no longer matches is dropped by `get_active_queued_intents`; never submitted. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_config_hash_mismatch_drops` |
| **Refusal — full battery re-run** | Every drained intent re-enters `submit_paper` → the fixed 17-check battery (no gap/halt subset). The drain owns no bespoke broker path. | Enforced structurally (the drain's only submit is `submit_paper`); the battery itself is covered by the existing risk-evaluator suite (`tests/milodex/risk/`). |
| **Boundary — clean-exit fence (I-4), literal `controlled_stop`** | Drainable iff `session_id == running_session_id` OR originating `strategy_runs.exit_reason == 'controlled_stop'` — **literal equality, NOT `IS NOT NULL`**: `interrupted` / `crashed` / `kill_switch` / `orphan_recovered` / NULL / no-row all DROP; only a deliberate controlled stop hands off. | `tests/milodex/core/test_queued_intents.py::test_cross_session_controlled_stop_is_active`, `::test_cross_session_dirty_exit_is_dropped` (parametrized: interrupted, crashed, kill_switch, orphan_recovered, None), `::test_cross_session_no_run_row_is_dropped`; end-to-end: `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_relaunch_without_controlled_stop_drops_exit_intent` |
| **Boundary — fence re-asserted at consume (CAS, P1-1)** | The consume CAS re-asserts the SAME fence (not just `status='queued'`): an unclean-handoff row cannot be consumed even if a caller bypassed `get_active`. | `tests/milodex/core/test_queued_intents.py::test_consume_drops_unclean_handoff_row`, `::test_consume_allows_cross_session_controlled_stop_row` |
| **Fail-closed — halt / not-tradable** | A symbol that is not clearly tradable (False / unknown / broker read raises) → DROP, never submit. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_halted_symbol_dropped` |
| **Fail-closed — 0-share entry** | A re-evaluated entry sized to 0 (or no match) → DROP, no submit, row left queued (NOT obsoleted). | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_zero_share_entry_dropped` |
| **Fail-closed — 0-share exit on a flat ledger** | A re-evaluated exit on an already-flat strategy ledger → `obsolete` (the obligation is moot), no submit. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_zero_share_exit_on_flat_ledger_marked_obsolete` |
| **Fail-closed — signal side-flip** | A queued BUY that re-evaluates to a SELL (signal flipped) → DROP (never fire the wrong direction). | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_side_flip_dropped` |
| **Idempotency — key contract** | `idempotency_key == f"{strategy_id}|{trading_session}|{side}|{symbol}"` byte-for-byte (side lowercase, symbol uppercase) — the UNIQUE/CAS dedup key. | `tests/milodex/strategies/test_runner_queued_intent_persist.py::test_idempotency_key_composition` |
| **Idempotency — exactly-once submit** | A second relaunch/drain of the same row submits nothing (the single-statement consume CAS already claimed it). | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_full_daily_lifecycle_persist_then_drain_single_submit` (re-drain) + `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_persist_controlled_stop_relaunch_drain_fill_chain` (session-C relaunch) |
| **Idempotency — UNIQUE collision on persist** | Re-persisting the same logical intent collides on `UNIQUE(idempotency_key)` and is swallowed idempotently (one row, no crash). | `tests/milodex/strategies/test_runner_queued_intent_persist.py::test_persist_unique_collision_is_idempotent` |
| **Watermark integrity (I-3)** | The at-open drain never advances `_last_processed_bar_at`, so the authoritative post-close evaluation is not suppressed. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_at_open_drain_does_not_suppress_post_close_eval` |
| **Exit safety — dropped-exit alert (fence-failed)** | A queued EXIT excluded by the clean-handoff fence raises a durable `exit_intent_dropped` operator alert + `WARN`, and the row is retired `obsolete` (surfaced, never silently stranded). | `tests/milodex/execution/test_exit_drop_operator_alert.py::test_drain_fence_failed_exit_alerts_and_obsoletes`; end-to-end: `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_relaunch_without_controlled_stop_drops_exit_intent` |
| **Exit safety — dropped-exit alert (halt)** | A drainable EXIT dropped at the halt gate alerts + obsoletes. | `tests/milodex/execution/test_exit_drop_operator_alert.py::test_drain_halted_exit_alerts_and_obsoletes` |
| **Exit safety — asymmetry** | Only EXIT drops alert; an entry drop is silent and a normal drainable exit does NOT alert. | `tests/milodex/execution/test_exit_drop_operator_alert.py::test_drain_entry_drop_does_not_alert`, `::test_drainable_exit_does_not_alert`, `::test_emit_exit_drop_alert_writes_durable_row_and_warns` |
| **Durable-state — operator-alert ledger** | Append/list round-trips across the durable store; migration 017 lands at schema 17; `MIN_COMPATIBLE_SCHEMA_VERSION` stays 12 (additive). | `tests/milodex/core/test_event_store_operator_alerts.py::test_append_and_list_operator_alert_roundtrip`, `::test_schema_version_is_17_after_operator_alerts_migration`, `::test_min_compatible_schema_unchanged`, `::test_list_operator_alerts_filters_by_type` |
| **Expiry — weekend-safe TTL** | The persisted `expires_at` outlasts a Friday→Monday (≥3-day) gap to the next session open (so a weekend/holiday intent is not silently killed before its drain). | `tests/milodex/strategies/test_runner_queued_intent_persist.py::test_expiry_window_spans_a_weekend` |
| **Expiry / I-9 — reconcile-cadence sweep, no daemon** | The expiry sweep runs at the manual run-loop / reconcile cadence (NOT a timer/daemon), flips only `queued`→`expired`, never `consumed`/`obsolete`, never touches the watermark, and writes an audit row only when rows were swept. | `tests/milodex/strategies/test_runner_expiry_sweep.py::test_run_cycle_sweeps_expired_queued_intents_at_startup`, `::test_run_cycle_writes_no_audit_when_nothing_expired`; event-store: `tests/milodex/core/test_queued_intents.py` sweep cases (`expire_stale_queued_intents` flips only expired-queued; never consumed/obsolete) |
| **Persist discipline** | The post-close lock-in persists (no submit) and the watermark advances exactly once; no intent → no persist. | `tests/milodex/strategies/test_runner_queued_intent_persist.py::test_post_close_cycle_persists_and_does_not_submit`, `::test_post_close_no_intents_persists_nothing`, `::test_intent_class_entry_vs_exit` |

## Baseline

Cited tests verified green on `m1/daily-queue-drain`. Full-suite result on this branch:
**`3431 passed, 1 skipped, 4 xfailed`** (verified `.venv/Scripts/python.exe -m pytest -q`,
73 s) — the queue-at-open persist/drain/sweep/alert evidence above adds 30 tests on top of the
M1 / PR #289 merge baseline (`3401 passed, 1 skipped, 4 xfailed`). The lone skip is the documented
design-system-showcase quarantine; the lone lint blemish is a pre-existing `I001` in
`tests/milodex/strategies/test_gap_continuation_intraday.py` (outside this change).

## What this evidence deliberately does NOT cover (conservative M1 scope, ADR 0057)

- A full halt/LULD detection subsystem and async partial-fill reconciliation fold (fast-follow):
  M1 **drops** on halt/ambiguity rather than resolving. The dropped-exit alert surfaces the residual.
- A hard system-guaranteed standing-exit obligation: M1 relies on the operator alert + the
  strategy's natural re-emission next post-close.
- Auto-launch / scheduler (that is D-3): the drain fires only on the operator's manual pre-open
  relaunch (I-9, ADR-0012-clean).
- Profitability of any daily strategy: trust over profit; M1's bar is one named fill + the daily
  branch-proof.
