# Hardening Roadmap — Runner Truth + GUI Wiring

**Date:** 2026-06-10
**Inputs:** [Runner process audit](../../reviews/2026-06-10-runner-process-audit.md) (cited below as **R-Px**) and [GUI wiring audit](../../reviews/2026-06-10-gui-wiring-audit.md) (cited as **G-Px**). Findings are not restated here — each PR references its finding ID; read the audit section before starting the PR.
**Shape:** 12 PRs + 1 design doc, in 6 phases across 3 largely-independent tracks. Two operator decision checkpoints (DC-1, DC-2) gate specific PRs; everything else is executable straight through.

> **STATUS (as of 2026-06-13): all 12 executable PRs merged (#219–#230).** PARKED: DC-2 + HR-8 (post-close gate), HR-4 manual trip+reset verify, HR-2 supervised intraday session. See the Execution log below.

---

## Ordering rationale (the load-bearing constraints)

1. **HR-1 before anything that relaxes `market_closed` or staleness.** The phantom exits (R-P0-1) are currently held back *only* by the market-hours check. Relax execution timing first and the contaminated ledger sends naked sells to a margin account. This is the one hard sequencing rule in the whole roadmap.
2. **Position truth before execution semantics.** Phase D (daily execution design, R-P0-2) is the only decent-sized PR and the only risk-policy change. It is deliberately *last on its track*: after HR-1 lands and one post-close cycle confirms daily strategies emit sane signals, the design decision is made against real data instead of phantoms.
3. **Active defects before latent ones.** The contamination and intraday cadence burn are happening on the running fleet right now (Track 1 first). The GUI kill-switch wedge (Track 2) is latent — it only bites when the switch trips — but it is the safety surface, so it runs as the parallel second track, not after everything.
4. **Decisions are batched, not scattered.** DC-1 (reducing-order policy) and DC-2 (daily execution semantics) are the only two stops where the work pauses for an operator call. All PRs not behind a checkpoint can proceed at autonomous cadence.
5. **Hygiene is one sweep at the end**, not sprinkled — every P3 item is a magnet for scope creep if attached to a correctness PR.

```
Track 1 (runner truth):   HR-1 ──► HR-2 ──► HR-3 ──► [verify] ──► DC-2 ──► HR-8
Track 2 (GUI safety):     HR-4 ──► HR-5                                    (independent)
Track 3 (policy + ops):   DC-1 ──► HR-6 ──► HR-7      HR-9 ► HR-10 ► HR-11 ► HR-12
Final:                    HR-13 (hygiene sweep, both audits' P3 clusters)
```

---

## Phase A — Stop the bleeding (Track 1)

> Restores truthful position state and sane cadence for the fleet that is running today. No policy decisions, no design docs. **If you only run one session, run this phase.**

### HR-1 — Paper-source scoping for the strategy ledger + duplicate backstop · **small** · R-P0-1, R-P1-4
- `risk/attribution.py`: add `AND source = 'paper'` to both fetch queries (`:255`, `:284`); add latest-status-per-`broker_order_id` reversal to the strategy fold, mirroring `fold_positions` ([reconciliation.py:627-670](../../../src/milodex/operations/reconciliation.py)) so corrective terminal rows close ledger lots.
- `core/event_store.py:count_recent_submitted_orders`: add the source filter and push the time window into SQL.
- Tests: backtest-row contamination regression (a backtest fill must not appear in `strategy_positions`/`strategy_open_lots`/`attribute_position`); submitted→cancelled reversal; concurrent-backtest dedup non-veto.
- **Scope guard:** the `meanrev.rsi2.intraday.spy.v1` SPY net=13 is the *documented* ADR 0055 sibling-sell divergence, not this bug — the fix must not silently "solve" it. If it should be cleared, that's an operator `reconcile resolve-position`-style action, not code.
- **Verify (before merging):** re-run the audit's net-ledger probe —
  `SELECT strategy_name, symbol, SUM(CASE WHEN side='buy' THEN quantity ELSE -quantity END) net FROM trades WHERE status='submitted' AND source='paper' GROUP BY 1,2 HAVING net > 0;`
  against what the *new* fold returns. Expected: daily strategies collapse to zero/near-zero; rsi2's 13 SPY remains (by design).

### HR-2 — Intraday bar watermark + completed-bar evaluation · **small** · R-P1-1, R-P1-2
- `strategies/runner.py`: advance `_last_processed_bar_at` on the intraday path; evaluate only bars whose window has closed (`timestamp + bar_size ≤ now`); arm the closed-market early-out for intraday once the session's last bar is processed.
- Keeps `_processed_intent_keys` as the submission backstop. Daily path untouched.
- **Verify:** explanations growth drops from ~10k/runner-day to ~bar-count/day; runner log shows no overnight Alpaca fetches; one supervised intraday session confirms entries fire on completed bars only.

### HR-3 — NY-day rollover re-reconciliation · **tiny** · R-P1-3
- Runner re-runs reconciliation when the NY trading day of the last persisted run differs from today (cheap check at cycle top; reuse `_ensure_startup_reconciliation` machinery).
- This is also a soft prerequisite for Phase D: queued-at-open submissions on day 2 must not die `reconciliation_stale`.

---

## Phase B — Safety-surface recovery (Track 2, parallel with Phase A)

> The GUI must function *during* a kill-switch event. Both PRs are pure GUI/facade; no risk-layer changes.

### HR-4 — Kill-switch reset modal extraction · **small** · G-P1-1
- Extract `KillSwitchResetModal.qml` from AnchorSurface; open it from the RiskStrip badge / Risk Office drawer when `killSwitchActive`. Token contract (`resetKillSwitchToken` → `reset_kill_switch`) unchanged.
- Then delete AnchorSurface, the `"anchor"` route in `Main.qml`, and update the iteration tuples in `test_qml_load_smoke.py` + `test_tnum_enforcement.py` (per the standing memory note).
- Add the missing test class: a **reachability** test (a user-triggerable path opens the reset flow), not just a load-smoke.

### HR-5 — Allow controlled stop while the kill switch is active · **tiny** · G-P2-1
- `commands/bench.py:1144`: move `READINESS_KILL_SWITCH` from `required_checks` to `inspected_checks` for the stop family. A controlled stop submits no trades; the risk layer independently blocks anything a still-running runner attempts.

---

## Phase C — Doctrine ↔ code reconciliation (Track 3)

### DC-1 — Decision checkpoint: reducing-order asymmetry · **operator call**
The YAML promises (`reducing_allowed_during_kill_switch: true`, `reducing_orders_permissive`) that the evaluator does not keep (R-P2-2), and the order-value cap blocks large legitimate exits. Decide, per check:
- kill switch: absolute halt (fix the YAML) **or** reducing-sell exemption (fix the code)?
- `order_value` / `market_hours`: do exits get the reducing exemption?
Recommendation in the audit leans "kill switch stays absolute, fix YAML; order_value gets the reducing exemption" — but it's a risk-policy call, so it's yours. Output: RISK_POLICY.md + risk_defaults.yaml + evaluator agree.

### HR-6 — Implement DC-1 · **tiny–small** · R-P2-2, R-P2-5
- Whichever direction DC-1 goes: evaluator changes (reducing-sell exemption plumbing via `is_exposure_increasing` already exists for the reconciliation gate, [evaluator.py:188-193](../../../src/milodex/risk/evaluator.py)) and/or YAML+doc strikes. Include the risk-triggered kill-switch open-order cancellation asymmetry (R-P2-5) in the same policy pass.

### HR-7 — Risk-layer dead plumbing · **tiny** · R-P2-1, R-P2-3, R-P2-4
- `max_trades_per_day`: enforce (one bounded `COUNT(*)` on paper trades today) **or** add to RISK_POLICY known-limitations. Enforcing is cheap and the "runaway logic" rationale is exactly what R-P1-1 made plausible — recommend enforce.
- `risk.stop_loss_pct`: consume it (envelope cross-check against `parameters.stop_loss_pct`) or drop from required keys + configs; either way document bar-cadence stop semantics.
- Document per-strategy `daily_loss_cap_pct`'s actual account-level semantics (R-P2-3); per-strategy P&L attribution stays deferred (see "Not now").
- Remove `_normalize_intent`'s unreachable limit/stop branches.

---

## Phase D — Daily execution semantics (Track 1, gated)

### DC-2 — Decision checkpoint: how do post-close daily decisions execute? · **operator call + design doc**
R-P0-2. The one genuine design problem in the roadmap. Write the design doc first (`docs/superpowers/specs/`), choosing between:
- **(a) Queue-at-open:** lock in at close, submit TIF=day pre-open next session, with a mandatory re-validation pass at submit time (positions, risk, staleness re-checked against the morning state).
- **(b) Near-close evaluation:** evaluate ~15:50 ET on the forming daily bar, accept close-approximation risk, submit while the market is open.
Either choice requires a **tempo-aware staleness policy** (the 300 s cap is an intraday number) and touches `_check_market_open` — an operator-approved risk-policy change, never a convenience bypass.
**Hard gate:** does not start until HR-1 is merged *and* one post-close cycle has been observed with sane (non-phantom) daily signals.

### HR-8 — Implement DC-2 · **decent** · R-P0-2
- Runner + evaluator + risk_defaults changes per the design doc; include the UTC-midnight session-window fix (R-P3-3) here since it's the same timing surface.
- **Verify:** supervised post-close → next-open cycle on one daily strategy before declaring the fleet's daily leg operational; check `explanations` for `submitted` + broker fill, not just absence of blocks.

---

## Phase E — GUI operability (Track 3, anytime after Phase B)

### HR-9 — Real `data_freshness` readiness · **small** · G-P1-2
- Implement the dimension in `_DefaultWorkflowReadiness` reusing the CLI's `_data_freshness` measure ([report.py:259](../../../src/milodex/cli/commands/report.py)), or demote to `inspected_checks` for promote. Implementing is preferred — it makes the Bench promote flow *real* rather than softening the gate.
- While in the file: reuse one `EventStore` across the per-dimension checks (G-P3-5).

### HR-10 — GUI reconcile affordance · **small** · G-P2-2
- A "Run reconciliation" action (drawer button → facade → `run_reconciliation`, async via the bridge pattern). Kills the morning CLI ritual that currently pushes fleet ops around the Bench. Decide whether it's a seventh action family (full propose/submit ceremony) or a lighter direct slot — recommend the lighter path; it's read-mostly and writes only its own run row.

### HR-11 — Start audit-link bounded retry · **tiny** · R-P2-6 / G-P2-4
- `submit_start_paper_runner`: retry `_latest_open_session_id` up to ~15 s (the interpreter-probe budget) before returning `runner_audit_link_missing`. Update the two pinning tests.

### HR-12 — Deregister dead read models · **tiny** · G-P2-3
- Remove `KanbanState` + `StrategyBankState` from the registry, `register_qml_types`, and the order-snapshot test (or park without lifecycle start if a future surface is genuinely planned). Note `BenchState.selectStrategy` is also QML-dead.

---

## Phase F — Hygiene sweep (one batch PR, last)

### HR-13 — P3 clusters from both audits · **small (wide, shallow)** · R-P3, G-P3
- Config comment rot (rsi2pullback "demoted" block, intraday rsi2 "backtest-only" header).
- `freeze_manifest` keys in `_ACTION_INTENT_COPY` / `_ACTION_FUTURE_RECORD`.
- FRONT market panel: wire `MarketTapeState` data or hide the panel.
- Delete `bench_v1_fixtures.py` (or correct its docstring if kept for tests).
- `ActiveOpsState._load_config` slug fast-path fix.
- Local-vs-UTC `date.today()` consistency in runner helpers (whatever HR-8 didn't absorb).
- Stale test comment (`test_app.py:233`); IEX data-fidelity caveat added to STRATEGY_BANK/RISK_POLICY for intraday promotion cases.
- Optional: mtime-cached YAML/profile reads in `ExecutionService._evaluate` (only worth it if intraday tempo stays).

---

## Verification checkpoints (non-negotiable)

| After | Check | Pass condition |
|-------|-------|----------------|
| HR-1 | Net-ledger probe (SQL above) vs new fold | Daily phantom nets gone; rsi2 +13 SPY remains (documented) |
| HR-2 | `SELECT COUNT(*) FROM explanations WHERE recorded_at >= datetime('now','-1 day')` during a fleet day | ~bar-count per intraday runner, not ~8,640 |
| Phase A | One post-close daily cycle observed | Daily strategies emit entry-shaped signals or clean no-signals; blocks are `market_closed` only |
| HR-4 | Reachability test + manual: trip kill switch (paper), reset from GUI | Reset completes without CLI |
| HR-8 | Supervised daily execution cycle | A real fill recorded in `trades` with `broker_status='filled'` |
| End | Full suite + the 1 known env-failure only | Green per CLAUDE.md gotcha |

---

## Explicitly not now (protect the scope)

Unchanged from the 2026-05-29 audit's "don't build yet" list, plus this round's additions:
- Per-strategy P&L attribution for the daily-loss cap (capital-gate work; HR-7 documents semantics instead).
- Cross-process submit serialization / per-symbol locks (ADR 0026 addendum, deferred — launch-gate suffices for paper).
- Broker-side stop orders (Phase 1 is market-only, ADR 0013; revisit at capital gate).
- Read-model consolidation/caching for the 30 s pollers (HR-12 removes the dead ones; the live ones are fine).
- SIP data feed upgrade (IEX caveat is documented in HR-13; pay for data only when an intraday promotion case demands it).

---

## Suggested batch boundaries

- **Session 1:** HR-1 → HR-2 → HR-3 (Phase A complete, verified). The system is *truthful* after this session.
- **Session 2:** HR-4 → HR-5 (Phase B), then DC-1 + HR-6 + HR-7 if the policy call is quick.
- **Session 3:** DC-2 design doc → review → HR-8. The only session that needs a supervised market window (post-close, and next-open if option (a)).
- **Session 4:** HR-9 → HR-12 (Phase E) + HR-13. The GUI becomes operationally self-sufficient.

---

## Execution log

**Status at 2026-06-10 23:20 ET: 12 of 12 executable PRs merged (HR-1..7, 9..13). Parked with preconditions: DC-2 design doc + HR-8 (gate: one post-close daily cycle observed with sane signals — fleet restarted onto merged code 22:08 ET, observation window 2026-06-11 ~16:15+ ET); HR-4 manual GUI kill-switch trip+reset (any supervised window — note it halts the fleet, schedule before a planned restart); HR-2 supervised intraday session (next market hours).**

- HR-5 — merged #219 (2026-06-10) — verified: `tests/milodex/commands` 111 passed including new kill-switch-active stop-admissibility test; risk-invariant reviewer traced the controlled stop end-to-end (request-file write only, never broker-facing; START family gate unchanged). Advisory (documented in PR): real-evaluator integration test for warning propagation not added.
- HR-4 — merged #220 (2026-06-10) — verified: GUI suite 830 passed / 1 skipped / 4 xfailed (count independently reproduced by the spec reviewer); reset reachability pinned by `test_kill_switch_reset_reachability.py` (RiskStrip + Risk Office drawer paths → `killSwitchResetModal.open = true`); review round added failure feedback (modal stays open with inline error when `reset_kill_switch` returns false). **Queued:** manual verify — trip kill switch (paper), reset from GUI — needs a supervised window; structural reachability is test-pinned meanwhile.
- DC-1 — decided (2026-06-10, operator): kill switch = absolute halt (fix YAML + RISK_POLICY doctrine, keep code); order_value cap = reducing-sell exemption (implement in evaluator); market_hours = stays symmetric, execution timing owned by DC-2. HR-6 implements.
- HR-13 — merged #230 (2026-06-10) — verified: full suite 2496/0; targeted 257 (reviewer-reproduced). All 15 items landed (both audits' P3 clusters + 8 accumulated review advisories): SIGINT kill-switch fail-open closed; sustained-breach guard; SRS amended to DC-1 (doctrine now consistent across YAML/RISK_POLICY/SRS/code); runner UTC day-anchors; bounded session query; FRONT market panel wired; config comments fixed (claims verified TRUE against live DB: both manifest hashes MATCH; lifecycle_exempt promotion row exists). Optional mtime-cache item skipped by direction.
- HR-2 LIVE VERIFICATION (2026-06-10 23:20 ET) — intraday runner explanation count frozen at exactly 1 for 70+ minutes post-restart (pre-HR-2: ~1 per 10 s ≈ 420 by now). Drained flag armed and held. Remaining HR-2 verify: supervised intraday session (entries on completed bars) during next market hours.
- HR-11 — merged #229 (2026-06-10) — verified: 971 passed/1 skipped/4 xfailed (reviewer-reproduced); both gates approved. Audit-link retry bounded by the imported interpreter-probe constant; pure post-spawn reporting (pre-spawn gates untouched, no synthetic success, no drain deadlock). Advisories → HR-13: bounded `_latest_open_session_id` query; sync-slot doc note.
- HR-10 — merged #228 (2026-06-10) — verified: full suite 2476/0; 969 targeted. Reconcile affordance via the lighter direct path (same audited `run_reconciliation(persist=True)`); ADR 0051 boundary intact; readiness gate observes the new run uncached. Advisories → HR-13: `_FakeBroker` stub completion; `to_dict()` single-key extraction.
- Fleet restart (2026-06-10 22:08 ET, operator-authorized) — controlled-stop ×3 (`controlled_stop` exits, verify CLEAN), redeploy ×3 from master 70d72cc (HR-1+2+3 live). 3 open runs (111-113), 3 fresh clean reconciliations stamped the correct ET trading day. Intraday runner wrote exactly 1 launch-time explanation (pre-HR-2: one per 10 s). Overnight-quiet delta probe queued; post-close daily observation queued for 2026-06-11 ~16:00+ ET.
- HR-3 — merged #227 (2026-06-10) — verified: full suite 2452/0; 427 targeted; both gates approved with zero findings. Rollover check sits before ALL early-outs (drained-flag ordering test-pinned); trigger and gate share `local_trading_day`/ET_TZ so they cannot disagree; failure posture symmetric with startup. **Phase A code complete** — the post-close observation now needs the fleet restarted onto merged code.
- HR-7 — merged #226 (2026-06-10) — verified: full suite 2450/0 (quality reviewer's independent worktree run); 593 targeted. `max_trades_per_day` enforced (account-wide, UTC-day, N/N+1 strict, double fail-closed, backtest-immune); `stop_loss_pct` divergence refuses to load (14/22 configs have both fields, all matching); `_normalize_intent` dead branches removed; RISK_POLICY documents daily-loss-cap account semantics + bar-cadence stops + the trade limit. Reviewer split on exits-count-toward-budget adjudicated SYMMETRIC (halt-style breaker precedent: daily-loss + kill switch; a budget-exhausted fleet's exits are not trusted) and documented. F-1 (config-scan loops swallow the new ValueError at some surfaces — pre-existing pattern) → HR-13.
- HR-2 — merged #224 (2026-06-10) — verified: `tests/milodex/strategies` 395 passed; full suite 2442/0 on the round-2 tree (round-3 delta = one condition + one test in runner.py/test_runner.py only). THREE review rounds: quality round 1 caught the drained-flag publication-lag race (final bar silently skipped with recovery polling removed); round 2 caught the margin anchored at the wrong bar (1 bar-width real tolerance, not 3) AND that the wall-clock clause was deletable without test failure. Final shape: 2 quiet cycles + (2 bar-widths + max(3 bar-widths, 10 min)) margin. Residuals in PR (>10-min stragglers skipped; outage gaps skip intermediate bars — pre-existing; 24/7 crypto would idle on the RTH flag). **Queued:** supervised intraday session (entries fire on completed bars only; explanations ~bar-count/day; overnight quiet starts ~20:00 ET after extended-hours bars stop) — needs fleet restart.
- HR-9 — merged #225 (2026-06-10) — verified: full suite 2444/0 (rebased); targeted 420 passed. GUI promote gate now real: bounded latest-bar-age measure shared with the CLI trust report, single EventStore per evaluate (G-P3-5), F-1 review fix excludes backtest rows from the freshness signal (newly load-bearing instance of the R-P0-1 contamination family). Inherited gaps documented in PR: flat 24 h threshold false-blocks Mondays; explanations-as-proxy false-blocks an idle fleet; unreadable-store raises. Trading-day-aware threshold is the named follow-on.
- HR-6 — merged #223 (2026-06-10) — verified: full suite 2442 passed / 0 failed (rebased onto post-HR-12 master); 196 targeted risk+execution tests incl. the oversized-sell-beyond-held pin. Three gates clean (spec exact-match on all four DC-1 decisions; quality 8-case adversarial trace; risk-invariant 10-attack log all failed — exemption provably unreachable for exposure-increasing orders; `reducing_allowed_during_kill_switch` confirmed declarative/dead config). Deferred advisories in PR: sustained-breach repeat-cancel guard, SIGINT-path unguarded cancel (pre-existing fail-open), SRS R-EXE-015(f)/016 amendment — all HR-13 candidates.
- HR-12 — merged #222 (2026-06-10) — verified: `tests/milodex/gui` 830 passed / 1 skipped / 4 xfailed (reviewer-reproduced); teardown/lifecycle order traced clean; zero production constructors of the deregistered classes survive (deregister-only — modules retained for imported helpers + shim contract). Noted: `BenchState.selectStrategy` QML-dead, disposition deferred.
- HR-1 — merged #221 (2026-06-10) — verified: net-ledger probe run three times (worktree author, spec reviewer, post-merge from master code against a read-only live-DB backup) — all daily-strategy phantom nets collapse to empty; `meanrev.rsi2.intraday.spy.v1 {'SPY': 13.0}` remains per the scope guard. Full suite 2409 passed / 0 failed (round-2 reviewer's independent run). Round 1 caught a real consumer break: ENFORCE-backtest per-strategy cap was silently disabled by paper-only scoping — fixed via `attribute_position(source=...)` keyed off `context.is_backtest`. Residuals documented in the PR (walk-reversal gap; source-not-run-id backtest scoping; reconciliation WARN-path noise). **Note: live runners still run pre-fix code until restarted — the fleet picks up the truthful ledger at next deploy.**
