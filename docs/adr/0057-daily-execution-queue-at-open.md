# ADR 0057 — Daily Execution Resolves via Queue-at-Open (D-1)

**Status:** Accepted
**Date:** 2026-06-22
**Related:** [design spec](../superpowers/specs/2026-06-22-daily-queue-at-open-design.md) (mechanism, nine invariants I-1..I-9, conservative scope, two-round review log), [D-1 decision brief](../reviews/2026-06-22-D1-daily-execution-fork-brief.md) (options A/B/C/D, independent review), [CURRENT_ROADMAP.md](../CURRENT_ROADMAP.md) M1 (keystone milestone; §8 decision-ownership map), [ADR 0008](0008-risk-layer-veto-architecture.md) (risk-layer veto / execution chokepoint), [ADR 0012](0012-runtime-and-dual-stop.md) (manual-launch runtime model, no daemon in Phase 1), [ADR 0056](0056-cross-process-submit-serialization-per-account-advisory-lock.md) (per-account submit lock the resubmit fires under), [ADR 0055](0055-event-store-per-strategy-position-ledger.md) (strategy-scoped ledger exits size off), [ADR 0011](0011-sqlite-event-store.md) (event store the queue table lives in), [`src/milodex/strategies/runner.py`](../../src/milodex/strategies/runner.py), [`src/milodex/risk/evaluator.py`](../../src/milodex/risk/evaluator.py), [`src/milodex/execution/service.py`](../../src/milodex/execution/service.py)

## Context

A daily (`1D`) strategy structurally **cannot submit an order today**, and the cause is a catch-22 spanning the runner and the risk layer:

- The runner **no-ops while the market is open** ([`strategies/runner.py:272`](../../src/milodex/strategies/runner.py) — `if is_daily_bar and market_open: return []`): a 1D runner makes no evaluation and no fetch during RTH, because the in-progress daily bar shares its timestamp with the post-close finalized bar.
- A 1D strategy therefore only ever evaluates its close bar **post-close** (after the lock-in stability window), where it may emit a BUY/SELL intent.
- The risk layer then **vetoes that intent**: `_check_market_open` ([`risk/evaluator.py:431-437`](../../src/milodex/risk/evaluator.py)) blocks every closed-market submit with `reason_code="market_closed"`. Post-close is exactly when a 1D strategy evaluates, so every daily intent is vetoed.

The blocker is not the manifest. All **6 daily strategies are frozen at paper** (`regime.daily.sma200_rotation`, `breakout.daily.atr_channel`, `breakout.daily.donchian_20_10`, `meanrev.daily.bbands_lowerband`, `meanrev.daily.pullback_rsi2`, `momentum.daily.tsmom`); they pass `no_frozen_manifest`, so `market_closed` is their **sole** blocker. These are also the only cohort that can produce promotion-grade statistical evidence (intraday IEX verdicts are exploratory / non-durable per ADR 0017). The contradiction was framed as decision **D-1** in the D-1 brief, owned and decided at milestone **M1** ("Executable paper-fleet truth") per the CURRENT_ROADMAP §8 map.

## Decision

Daily execution is resolved by **Option A — queue-at-open**, chosen by the founder (2026-06-22) to keep the six frozen, gate-relevant daily strategies executable rather than permanently reclassifying them as non-executing analytics.

The daily lifecycle, today a single post-close decide-and-submit phase, is **split** into two phases bridged by a durable, inert, expiring intent record:

1. **Persist at close.** At the post-close cycle where the lock-in watermark advances, the runner persists the locked-in intent to a new append-only `queued_intents` table **instead of** calling `submit_paper`. The watermark still advances exactly once per close bar; the runner goes inert for the rest of the closed-market period.
2. **Drain at next open.** While the market is open — after the unconditional rollover reconcile, before returning — the runner drains its own active (non-consumed, non-expired, clean-handoff) queued intents, **rebuilds sizing against fresh state**, and re-routes each through `submit_paper`, where the **complete 17-check risk battery** re-runs. The single check that legitimately flips BLOCK→ALLOW across the overnight boundary is `_check_market_open`; **no other check is relaxed**.

A queued intent is a **proposal, never a pre-approved order**. Re-entering `submit_paper` (not `RiskEvaluator.evaluate` directly) is mandatory — it is the only path that re-fetches positions, `recent_orders`, kill-switch state, session-reset `daily_pnl`, the current-session bar, reconciliation readiness, and the active risk profile before running all 17 checks. Mechanism detail lives in the [design spec](../superpowers/specs/2026-06-22-daily-queue-at-open-design.md); this ADR records the decision.

### Options rejected

- **Option B — relax `market_hours` for 1D.** Smallest change, but it **weakens a sacred check for convenience** — the exact anti-pattern the risk doctrine forbids. A post-close submit also skips the next-morning re-validation, firing an overnight signal blind into a gapped open with kill-switch, staleness, disable-condition, and reconciliation checks never re-run. Rejected.
- **Options C / D — reclassify daily as decision-only (C discards the intent; D keeps an auditable would-submit trail).** Both satisfy the *lifecycle* clause honestly and at minimal sacred-layer risk, but contribute **zero** to M1's required named fill event and leave the six frozen daily strategies **permanently non-executing** — D-3 (auto-launch, the only forcing gate for A) may itself defer, so nothing would ever require A. Rejected as the daily-execution resolution; reclassification is not the goal.

## Founder's binding acceptance criteria

The decision is conditioned on six criteria, each a `risk-invariant-reviewer` checkpoint:

1. Persist only an **inert, expiring** decision overnight.
2. At the next open, **rebuild sizing** (recompute share count from fresh equity + fresh unit price; only `notional_pct` is durable) **and rerun the complete 17-check evaluator battery** — not a gap/halt subset.
3. **Idempotency** — never double-submit the same intent.
4. **Halt / LULD** handling.
5. **Reconciliation** of prior-session state.
6. Lands behind **this ADR** and a **`risk-invariant-reviewer`** pass on every diff touching the runner, `evaluator.py`, `service.py`, the execution-config loader, and the kill-switch / clean-exit path.

## Conservative fail-closed-drop scope (founder, 2026-06-22)

For criteria 4 and 5, M1 ships the **conservative fail-closed drop** — it does **not** build a full halt/LULD detection subsystem or an async partial-fill reconciliation fold (neither exists today; `halt_incident_status` and `filled_since_last_sync` are dead/deferred labels, and the ledger folds *submitted* qty, not *filled* qty). At drain time:

- **Halt / LULD:** one broker tradable/asset-status read. Any uncertain branch — not-clearly-tradable, unknown status, or the read **raising** (timeout / 5xx, common at the open spike) — **DROPs** the intent with an audit row, wrapped so it never propagates into the runner loop.
- **Unreconciled prior-session partial fill / ledger divergence:** treated as duplicate-order uncertainty → **hard-stop DROP** (`RISK_POLICY.md` #5 already mandates this); the resubmit never sizes against an ambiguous lot.
- **A dropped *exit* (SELL) raises a durable operator alert** (event-store row + `WARN` log) so a divergent/ambiguous position is **surfaced, never silently stranded**. The operator resolves it (manual flatten); the strategy re-emits its exit on the next post-close cycle. M1 deliberately relies on operator-resolves + natural re-emission, **not** a hard system-guaranteed standing-exit obligation.

A dropped intent is a *safe non-fill*, fully explained; every outcome (submit / veto / expired / idempotency-suppressed / dropped) records an explanation row, so the resubmit can never silently no-op. M1's bar is **one named fill plus the daily branch-proof**, not a high daily fill-rate. The full detection + reconciliation subsystem is an explicit **fast-follow**, out of M1 scope.

## Mechanism in brief and the sacred-check touches

The net-new durable state is a single append-only `queued_intents` table (additive migration `016`; `MIN_COMPATIBLE_SCHEMA_VERSION` stays 12), read by a single `get_active_queued_intents()` authority whose expiry + clean-handoff predicates are baked in. The full mechanism and the **nine hard invariants (I-1..I-9)** — risk layer never bypassed, fresh-context-not-replay, watermark untouched at open, clean-exit fence, row-scoped idempotency CAS, MARKET-only zero-share drop, carry-critical config path + hash, additive schema, manual-launch-only — are specified in the [design spec §7](../superpowers/specs/2026-06-22-daily-queue-at-open-design.md). Beyond the queue table and the drain hook, the decision touches three load-bearing seams that a future edit must not silently move:

1. **Session-aware staleness** (config-derived, evaluator-side; shipped). At the open a daily strategy's latest 1D bar is the *prior* session's close (≫ the global 300s `max_data_staleness_seconds`), so every daily resubmit would veto `stale_market_data`. Implementation found a **second** staleness gate that must not diverge — `_evaluate_data_quality` (the `data_quality_issue` ALL_FAMILIES disable-condition) also used the global 300s — so both gates now delegate to one shared `risk/staleness.py` helper keyed on the resolved config's `bar_size`. For `1D` the policy is **session-aware** (founder, 2026-06-23): fresh iff the bar's session date == the exchange calendar's latest completed session AND age ≤ a 7-calendar-day defense-in-depth ceiling; **fail closed** if the session can't resolve (the rejected alternative, a wall-clock budget, can't distinguish a closed market from a dead feed). Resolved via a new broker `latest_completed_session` (Alpaca `get_calendar`, no new dependency) threaded into `EvaluationContext`. `None` config / non-`1D` → 300s. The selector is the resolved config, **never a field on the intent** (I-2). (Also required adding a `bar_size` field to `StrategyExecutionConfig`, which had none.)
2. **Row-scoped idempotency CAS.** The per-account submit lock (ADR 0056) serializes the two `_submit_locked` bodies but does **not** by itself bind a consume to a specific row. A stable `idempotency_key (strategy_id, trading_session, side, symbol)`, `UNIQUE` on the table, is threaded onto the resubmit path so `_submit_locked` runs a single-statement compare-and-set (`UPDATE … WHERE idempotency_key=? AND status='queued'`, proceed only if `rowcount == 1`) **before** the single broker call. Per-account serialization **plus** the row-scoped CAS together close the double-launch / crash-retry race; neither alone does (I-5).
3. **Clean-exit (`controlled_stop`) handoff fence.** A queued intent is drainable **only if** either the same live session that persisted it is draining it, **or** the originating session closed with `exit_reason == 'controlled_stop'` (a durable `strategy_runs` column). The SQL predicate is the literal `exit_reason = 'controlled_stop'`, **not** `IS NOT NULL`: `interrupted` / `crashed:*` / `kill_switch` / `orphan_recovered` / a SIGKILL that wrote no `exit_reason` are all ambiguous → DROP. This replaces the false premise "force-close ⇒ kill-switch" (false for `interrupted`/`crashed`/SIGKILL per ADR 0012's hard-fallback wording); in-battery `_check_kill_switch` remains the backstop (I-4).

## Manual-launch assumption (ADR-0012-clean; distinct from D-3)

Option A's trigger is the operator's **manual pre-open relaunch** draining the durable queue — fully consistent with [ADR 0012](0012-runtime-and-dual-stop.md)'s manually-invoked foreground runtime with **no daemon, no scheduler, no auto-start in Phase 1**. The expiry sweep is folded into the runner's existing startup/rollover reconcile (launch-time, not a timer), so it introduces no scheduling surface (I-9).

**The moment "resubmit at open" is wired to any scheduler, daemon, or wake-timer, it becomes the D-3 (auto-launch) decision and requires an amendment to ADR 0012.** This ADR records that assumption explicitly so the boundary is unambiguous: A is ADR-0012-clean precisely because the operator, not a timer, supplies the open-time launch.

## Consequences

**Positive.**
- Daily becomes a genuine fill path: the six frozen, gate-relevant daily strategies — the cohort that produces promotion-grade statistical evidence — can actually transact. M1's named-fill exit criterion no longer rests entirely on IEX-non-durable intraday.
- Honest: daily really does execute, with the full risk battery re-run against fresh open-time state. No claim is weakened to make it true.
- The overnight intent is inert and expiring; nothing persists that can fire blind into a gapped open.

**Negative / cost.**
- This is the **largest sacred-layer surface** of the D-1 options: a new durable table, a split runner lifecycle, and **two net-new risk-check touches** (tempo-aware staleness + the idempotency CAS) on top of the clean-exit fence — the most ways to get subtly wrong.
- Submit-path complexity grows: the resubmit threads an idempotency key and a frozen `config_hash` through `_submit_locked`, and the drain must fail closed on every uncertain branch (halt-unknown, read-raises, ledger divergence, hash mismatch, non-`controlled_stop` handoff, zero-share recompute).
- M1 ships the conservative scope: halt/LULD and async partial-fill reconciliation **drop** rather than resolve, so daily fill-rate through halts/partials is intentionally low until the fast-follow subsystem lands.

**Risks and mitigations.** The headline risk is that an overnight intent bypasses the full evaluator or fires against stale state. Mitigations are the nine invariants (each a reviewer checkpoint), re-entry through `submit_paper` rather than a direct evaluator call, the config-derived staleness budget that cannot be spoofed by an intent field, the row-scoped CAS that survives double-launch and crash-retry, the positive-token clean-exit fence, and a mandatory `risk-invariant-reviewer` pass on every touched diff. Per the D-6 assurance gate, every clause M1 touches on the critical-requirement allowlist owes contract-appropriate, independently-reviewed per-clause evidence built TDD-first as the feature — a happy-path test does not satisfy it.

## Relationship to sibling and downstream decisions

- **Distinct from D-2** (intraday freeze governance) — a sibling M1 deliverable (demote the divergent intraday YAMLs, load/promotion preflight validation, SPY-canary pre-open launch, no new non-SPY freezes), tracked separately and not in scope here.
- **Distinct from D-3 (auto-launch)** — A assumes manual pre-open relaunch; auto-launch is a separate founder decision still to be framed, and wiring resubmit to a scheduler crosses into it (see the manual-launch assumption above).
- **D-4** (lifecycle-proof gate enforce-vs-document) remains to be framed.

## Addendum — drain queued-intent veto hygiene (2026-07-10)

Two drain behaviors observed in the M1-retro that spammed the audit trail without changing any risk outcome, both fixed here (runner + event store only; no risk/execution/veto touch):

1. **Per-session ENTRY-veto dedup.** A queued ENTRY whose drain submit returns `BLOCKED` (pre-CAS risk veto — the row stays `status='queued'` by design) was re-evaluated and re-submitted on every ~60s open poll, writing a fresh blocked explanation each time (up to ~390 rows/intent/day). The drain now records the vetoed row id in an in-memory per-session set and skips it on subsequent polls. The set is in-memory only, so a runner restart retries the veto once (one extra explanation per restart — acceptable). **EXITs are deliberately exempt** and keep retrying every poll: a blocked exit guards an open position and its veto (e.g. an opposite-side resting order) can clear mid-session.

2. **Supersede-at-lock-in.** The idempotency key embeds the trading session, so the next session's post-close lock-in mints a NEW row for the same logical intent while an older vetoed row is still `queued` under its 7-day TTL; at the next open BOTH drained (the second hit the duplicate-order veto and spammed until TTL). The lock-in persist now retires older `queued` rows for the same `(strategy_id, symbol, side, intent_class)` to `obsolete` (`supersede_queued_intents`), keeping only the just-persisted row — regardless of why the older row survived (risk veto, no-fresh-price retry, pre-open launch). Fail-soft: a supersede failure never aborts the persist. Side-flips are not superseded here (the drain's re-eval match already terminally drops a stale opposite-side entry as `reeval_no_match`).

**Adjudication.** A risk-vetoed queued intent keeps `status='queued'` for its natural lifecycle; it is retired by supersession at the next lock-in of the same logical intent, or by TTL expiry — **no terminal-veto taxonomy is introduced**, and both changes only reduce submits (fail-safe). The nine invariants and the risk battery are untouched.
