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
| **I-2 — staleness gate fed the locked session bar (GATE ONLY)** | The drain feeds the **locked-in session bar** as `latest_bar_override` to the session-aware 1D staleness gate (override timestamp + close == the persisted bar), not the live intraday bar — and **only** the gate consumes it; sizing and cap pricing do not (ADR 0057 §2). A stale/wrong locked bar still BLOCKS even under the fresh-price split. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_at_open_drain_reevaluates_and_submits_via_chokepoint`; gate-not-regressed: `tests/milodex/strategies/test_runner_drain_fresh_price.py::test_drain_stale_locked_bar_still_blocks_with_fresh_price` |
| **I-2 — fresh-price sizing + cap pricing (ADR 0057 §2)** | An entry's share count is recomputed on a **fresh open-time price** (live IEX minute bar via `get_latest_bar`), not the stale locked close, so an overnight gap cannot push an over-cap order through; the exposure cap prices on the fresh open (`pricing_unit_price`). No usable fresh price fails CLOSED (entry re-queued silent / exit alert + retire). Exits are not resized but still price on the fresh open. | `tests/milodex/strategies/test_runner_drain_fresh_price.py::test_drain_entry_gap_up_resizes_to_fresh_price`, `::test_drain_entry_resize_matches_floor_formula`, `::test_drain_entry_resize_to_zero_is_dropped`, `::test_drain_exit_not_resized_but_priced_on_fresh`, `::test_drain_entry_no_fresh_price_drops_and_stays_queued`, `::test_drain_entry_fresh_price_raises_drops_and_stays_queued`, `::test_drain_exit_fresh_price_raises_alerts_and_obsoletes`, `::test_drain_exit_no_fresh_price_alerts_and_obsoletes` |
| **I-2 — cross-symbol rescale uses the traded symbol's own close** | The fresh-price rescale numerator is the **traded symbol's** own locked close (not universe[0]'s `decision_bar.close`), so a cross-sectional 1D strategy (traded symbol ≠ evaluation symbol) is sized correctly; missing/empty sizing bars fail CLOSED (entry re-queued / exit alert + retire). | `tests/milodex/strategies/test_runner_drain_cross_symbol_rescale.py::test_drain_cross_symbol_entry_rescales_on_traded_symbol_close`, `::test_drain_cross_symbol_entry_resize_matches_traded_close_formula`, `::test_drain_cross_symbol_entry_no_sizing_price_stays_queued`, `::test_drain_cross_symbol_exit_no_sizing_price_alerts_and_obsoletes` |
| **Refusal — config drift (I-7)** | A row whose on-disk `config_hash` no longer matches is dropped by `get_active_queued_intents`; never submitted. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_config_hash_mismatch_drops` |
| **Refusal — full battery re-run (I-1)** | Every drained intent re-enters `submit_paper` → the full risk battery (no gap/halt subset); the drain owns no bespoke broker path. Basis is **structural** (the drain's only submit call is `submit_paper` in `runner.py::_drain_queued_intents`) + **integration** (the lifecycle/drill drains submit through the chokepoint; a risk-blocked drain submits nothing). The battery's own checks carry their coverage in `tests/milodex/risk/` — this matrix does not re-prove them per-clause. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_full_daily_lifecycle_persist_then_drain_single_submit`; `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_persist_controlled_stop_relaunch_drain_fill_chain` |
| **Boundary — clean-exit fence (I-4), literal `controlled_stop`** | Drainable iff `session_id == running_session_id` OR originating `strategy_runs.exit_reason == 'controlled_stop'` — **literal equality, NOT `IS NOT NULL`**: `interrupted` / `crashed` / `kill_switch` / `orphan_recovered` / NULL / no-row all DROP; only a deliberate controlled stop hands off. | `tests/milodex/core/test_queued_intents.py::test_cross_session_controlled_stop_is_active`, `::test_cross_session_dirty_exit_is_dropped` (parametrized: interrupted, crashed, kill_switch, orphan_recovered, None), `::test_cross_session_no_run_row_is_dropped`; end-to-end: `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_relaunch_without_controlled_stop_drops_exit_intent` |
| **Boundary — fence re-asserted at consume (CAS, P1-1)** | The consume CAS re-asserts the SAME fence (not just `status='queued'`): an unclean-handoff row cannot be consumed even if a caller bypassed `get_active`. | `tests/milodex/core/test_queued_intents.py::test_consume_drops_unclean_handoff_row`, `::test_consume_allows_cross_session_controlled_stop_row` |
| **Fail-closed — not-tradable drop** | A symbol the broker reports as not tradable → DROP at the drain, never submit (via `tradable_drop_decision`). This cell proves the drain honors a drop verdict; the False/unknown/read-raises sub-cases are the helper's own concern, not re-proved here. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_halted_symbol_dropped` |
| **Fail-closed — 0-share entry** | A re-evaluated entry sized to 0 (or no match) → DROP, no submit, row left queued (NOT obsoleted) — benign for entries (re-vetoed each open, expires by TTL). The retry-until-expiry semantics is a documented residual (see below). | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_zero_share_entry_dropped` |
| **Fail-closed — 0-share exit on a flat ledger** | A re-evaluated exit on an already-flat strategy ledger → `obsolete` (the obligation is moot), no submit. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_zero_share_exit_on_flat_ledger_marked_obsolete` |
| **Fail-closed — signal side-flip** | A queued BUY that re-evaluates to a SELL (signal flipped) → DROP (never fire the wrong direction). | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_drain_side_flip_dropped` |
| **Idempotency — key contract** | `idempotency_key == f"{strategy_id}|{trading_session}|{side}|{symbol}"` byte-for-byte (side lowercase, symbol uppercase) — the UNIQUE/CAS dedup key. | `tests/milodex/strategies/test_runner_queued_intent_persist.py::test_idempotency_key_composition` |
| **Idempotency — exactly-once submit** | A second relaunch/drain of the same row submits nothing (the single-statement consume CAS already claimed it). | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_full_daily_lifecycle_persist_then_drain_single_submit` (re-drain) + `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_persist_controlled_stop_relaunch_drain_fill_chain` (session-C relaunch) |
| **Idempotency — UNIQUE collision on persist** | Re-persisting the same logical intent collides on `UNIQUE(idempotency_key)` and is swallowed idempotently (one row, no crash). | `tests/milodex/strategies/test_runner_queued_intent_persist.py::test_persist_unique_collision_is_idempotent` |
| **Watermark integrity (I-3)** | The at-open drain never advances `_last_processed_bar_at`, so the authoritative post-close evaluation is not suppressed. | `tests/milodex/strategies/test_runner_queued_intent_drain.py::test_at_open_drain_does_not_suppress_post_close_eval` |
| **Exit safety — dropped-exit alert (fence-failed)** | A queued EXIT excluded by the clean-handoff fence raises a durable `exit_intent_dropped` operator alert + `WARN`, and the row is retired `obsolete` (surfaced, never silently stranded). | `tests/milodex/execution/test_exit_drop_operator_alert.py::test_drain_fence_failed_exit_alerts_and_obsoletes`; end-to-end: `tests/milodex/integration/test_persist_relaunch_drain_drill.py::test_relaunch_without_controlled_stop_drops_exit_intent` |
| **Exit safety — dropped-exit alert (halt)** | A drainable EXIT dropped at the halt gate alerts + obsoletes. | `tests/milodex/execution/test_exit_drop_operator_alert.py::test_drain_halted_exit_alerts_and_obsoletes` |
| **Exit safety — broker-reject strand (Major 3)** | A routine broker rejection (`OrderRejected`/`InsufficientFunds`) is caught in the service and RETURNED as `status=REJECTED` AFTER the consume CAS committed the row to `consumed` — a consumed-but-unsubmitted EXIT. The drain inspects the returned result and alerts (`submit_rejected`). `BLOCKED` (idempotency race-loss / pre-CAS risk block) does NOT alert; an entry reject stays silent. | `tests/milodex/strategies/test_runner_drain_submit_reject_alert.py::test_drain_exit_broker_reject_emits_alert`, `::test_drain_entry_broker_reject_does_not_alert`, `::test_drain_exit_idempotency_suppressed_does_not_alert` |
| **Exit safety — asymmetry** | Only EXIT drops alert; an entry drop is silent and a normal drainable exit does NOT alert. | `tests/milodex/execution/test_exit_drop_operator_alert.py::test_drain_entry_drop_does_not_alert`, `::test_drainable_exit_does_not_alert`, `::test_emit_exit_drop_alert_writes_durable_row_and_warns` |
| **Durable-state — operator-alert ledger** | Append/list round-trips across the durable store; migration 017 lands at schema 17; `MIN_COMPATIBLE_SCHEMA_VERSION` stays 12 (additive). | `tests/milodex/core/test_event_store_operator_alerts.py::test_append_and_list_operator_alert_roundtrip`, `::test_schema_version_is_17_after_operator_alerts_migration`, `::test_min_compatible_schema_unchanged`, `::test_list_operator_alerts_filters_by_type` |
| **Expiry — weekend-safe TTL** | The persisted `expires_at` outlasts a Friday→Monday (≥3-day) gap to the next session open (so a weekend/holiday intent is not silently killed before its drain). | `tests/milodex/strategies/test_runner_queued_intent_persist.py::test_expiry_window_spans_a_weekend` |
| **Expiry / I-9 — reconcile-cadence sweep, no daemon** | The expiry sweep runs at the manual run-loop / reconcile cadence (NOT a timer/daemon), flips only `queued`→`expired`, never `consumed`/`obsolete`, never touches the watermark, and writes an audit row only when rows were swept. | `tests/milodex/strategies/test_runner_expiry_sweep.py::test_run_cycle_sweeps_expired_queued_intents_at_startup`, `::test_run_cycle_writes_no_audit_when_nothing_expired`; event-store: `tests/milodex/core/test_queued_intents.py` sweep cases (`expire_stale_queued_intents` flips only expired-queued; never consumed/obsolete) |
| **Persist discipline** | The post-close lock-in persists (no submit) and the watermark advances exactly once; no intent → no persist. | `tests/milodex/strategies/test_runner_queued_intent_persist.py::test_post_close_cycle_persists_and_does_not_submit`, `::test_post_close_no_intents_persists_nothing`, `::test_intent_class_entry_vs_exit` |

## Baseline

Cited tests verified green on `m1/daily-queue-drain`. Full-suite result on this branch:
**`3455 passed, 1 skipped, 4 xfailed`** (verified `.venv/Scripts/python.exe -m pytest -q`, ~75 s),
**ruff clean** across `src/` and `tests/`. The queue-at-open persist/drain/sweep/alert evidence
above, plus the review-remediation tests (fresh-price sizing + cap pricing, cross-symbol rescale,
the broker-reject strand alert), sit on top of the M1 / PR #289 merge baseline
(`3401 passed, 1 skipped, 4 xfailed`). The lone skip is the documented design-system-showcase
quarantine (`docs/KNOWN_FLAKY_TESTS.md`); the prior `I001` lint blemish in
`tests/milodex/strategies/test_gap_continuation_intraday.py` has been fixed (lint is now clean).

## What this evidence deliberately does NOT cover (conservative M1 scope, ADR 0057)

- A full halt/LULD detection subsystem and async partial-fill reconciliation fold (fast-follow):
  M1 **drops** on halt/ambiguity rather than resolving. The dropped-exit alert surfaces the residual.
- **Fresh-but-wrong price.** `_fresh_pricing_bar` accepts any current-session, strictly-newer,
  positive IEX minute bar with no halt / crossed-quote / outlier bound — a halted symbol's last
  print or a bad pre-market bar could price the cap and (now) size the entry rescale. Same
  conservative-deferred class as halt/LULD detection above; the fresh-price fix widened its blast
  radius from cap-pricing to entry-sizing.
- A hard system-guaranteed standing-exit obligation: M1 relies on the operator alert + the
  strategy's natural re-emission next post-close.
- Auto-launch / scheduler (that is D-3): the drain fires only on the operator's manual pre-open
  relaunch (I-9, ADR-0012-clean).
- Profitability of any daily strategy: trust over profit; M1's bar is one named fill + the daily
  branch-proof.

## Known residuals identified in review, deferred by founder decision (not closed in M1)

These were surfaced by the 2026-06-23 outside review and consciously deferred (founder scoped this
branch to fresh-price sizing/caps + the broker-reject alert + this matrix). Each is a missed-exit /
silent-strand or sizing-drift edge, NOT a wrong/double-order fail-open (those are closed above):

- **Persist-fail after watermark advance.** The post-close lock-in advances `_last_processed_bar_at`
  before `append_queued_intent`; a non-`IntegrityError` persist failure (transiently unreadable
  config, locked DB) after the watermark moved drops the day's intent with no retry — for an EXIT, a
  silent strand. Persist-before-watermark or a durable retry/alert would close it.
- **CAS→outbox crash window.** The consume CAS commits the row to `consumed` in a separate
  transaction from the execution-attempt outbox row; a crash between them leaves a consumed intent
  with no order and no `pending` attempt for reconciliation — for an EXIT, a silent strand.
- **Drop/veto retry semantics.** Halted / unmatched / 0-share and risk-vetoed drains leave the row
  `queued` and retry every open until TTL expiry; there is no explicit terminal `vetoed`/`dropped`
  state. Benign for entries (re-vetoed each open); the only sharp edge is that a risk-block on a
  retried drain re-runs `_maybe_activate_kill_switch`.
- **Same-day crash + same-day relaunch key collision.** The `idempotency_key` omits `session_id`, so
  a same-day re-persist after an un-clean crash collides on `UNIQUE` and the prior-session row is
  fence-excluded → stranded till expiry (an entry, so not surfaced). The fix is non-trivial: adding
  `session_id` to the key would break the cross-session clean-handoff drain (I-5).
- **Override↔fresh-price coupling (latent, unreachable today).** Cap-pricing-fresh depends on the
  drain always pairing `pricing_unit_price` with `latest_bar_override`; if a future caller ever set
  the override for a 1D config WITHOUT a fresh price, `_evaluate` would silently fall back to the
  stale override close. No current caller does this. Gold-standard hardening: derive the fresh price
  inside `_submit_locked` from the queued row so the pairing is enforced, not conventional.
