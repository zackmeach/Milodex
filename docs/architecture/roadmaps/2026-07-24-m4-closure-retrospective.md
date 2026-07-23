# M4 — Recovery & failure-mode proof — CLOSURE RETROSPECTIVE (DRAFT for founder signature)

> **Status: DRAFT.** Prepared 2026-07-22 for the M4 gate review. This document
> does **not** close M4 — the founder closes the gate after the remaining OPEN
> items below (founder GUI walks, F2 rehearsal, boundary-review signature) are
> walked or explicitly waived. On closure, the §11 retrospective block in
> [`docs/CURRENT_ROADMAP.md`](../../CURRENT_ROADMAP.md) is appended from this
> draft and this file becomes its evidence annex.
>
> Every event-store fact below was re-verified read-only against
> `data/milodex.db` on 2026-07-22 (§4 addendum facts: 2026-07-23) before being
> written down. Nothing here is asserted from memory.

---

## 1. What M4 set out to prove

From the roadmap (M4, opened 2026-07-11 at the M2 close):

> A drill matrix with PASS evidence for: stale market data, locked/corrupt
> SQLite DB, broker outage/API error, dead/wedged runner; plus first-launch/
> clean-room, controlled-stop-on-wedged, and kill-switch trip→reset — each an
> injected fault + observable operator-facing message + durable log (unit-test
> coverage is not sufficient). Absorbs usage-burn F1/F2/F3 and the
> D-7-adjudicated fault-mode docs.

Plus two items deferred *into* M4 by earlier decisions: the ADR 0058
lifecycle-criteria enforcement (D-4: "split now, enforce at M4") and the
HR-4/HR-5 supervised kill-switch walks carried from the 2026-06-10 hardening
roadmap. The gate also requires the autonomy-boundary review (companion draft:
[`2026-07-24-m4-boundary-review.md`](2026-07-24-m4-boundary-review.md)).

M4's thesis, in FOUNDER_INTENT terms: trust under failure is where justified
trust is won or lost. M2 proved the operator can see the truth; M4 proves the
system tells the truth — and stays governed — while things are breaking.

## 2. What the week actually proved

M4 got two bodies of evidence, one planned and one not.

**Planned: the injected-fault drill matrix.** 8/8 cells PASS
([`docs/drills/2026-07-11-m4-drill-matrix.md`](../../drills/2026-07-11-m4-drill-matrix.md),
harness `scripts/drills/run_drills.py`, #355), each an injected fault in a
scratch environment asserted against the real operator surface (CLI output +
durable event-store rows), with the `clean_room` hermeticity defect found and
fixed on the founder machine (#362, `MILODEX_SKIP_DOTENV`) and re-verified PASS
with the real `.env` present.

**Unplanned: eight trading days of real failure and recovery (2026-07-13 →
07-22).** The live-fire window handed M4 exactly the failure modes it was
designed around — schedule death, orphaned runners, a broker-connectivity
crash, and a five-blocker gauntlet on the drain path — and the system's
observables, fail-closed posture, and recovery paths carried all of it without
one unsafe order, one phantom, or one kill-switch event
(`kill_switch_events`: zero rows since 2026-05-04; verified 2026-07-22).

### 2.1 The drain-blocker gauntlet — five distinct blockers, all peeled

The single most valuable output of the window. Entries had been proven at M1;
the drain path turned out to be guarded by five separate latent blockers —
four on the exit side, one a fleet-wide entry deadlock — each found live,
each root-caused, each fixed through a reviewed PR (or confirmed by-design)
— never by weakening a gate in place:

| # | Blocker | Observed live | Fix | Merged |
|---|---|---|---|---|
| 1 | Single-position cap vetoed exposure-reducing exits (missing DC-1 exemption): a 364sh over-cap XLF long was unexitable | 07-14: 52 × `max_single_position_exceeded` blocked submits (~60s retries × 26 min × 2 strategies) | #363 — DC-1 exemption on the single-position cap | 2026-07-15 |
| 2 | Concurrent-positions cap mis-scoped (spec≠code vs ADR 0024/0029): a `min()` clamp to the proposing strategy's own `max_positions` deadlocked every fleet **entry** while any position was open | 07-13/07-14: 9 × `max_concurrent_positions_exceeded` false vetoes ("Projected open positions 4 exceeds limit 1") | #364 — account-scope the cap to the global value; per-strategy ceiling stays a separate additive check | 2026-07-15 |
| 3 | `no_clean_handoff` — drain authority's clean-handoff fence (I-4) retired exits after the orphaned 07-14/07-15 sessions | 07-16 16:47 UTC: 4 × `exit_intent_dropped` alerts (XLF/XLV, donchian + atr_channel) | No code change — fail-closed by design; alert emitted, exit re-locked at the next post-close (`runner.py` `_alert_stranded_exit_intents`) | — |
| 4 | `no_fresh_price` — IEX-thin symbols (~17s post-bell) had no current-session minute bar at the open's first drain poll; a first-miss drop stranded live positions | 07-14 (2 alerts) and 07-20 13:30 UTC (5 alerts: XLF ×2, XLV ×2, JNJ) | #374 — bounded ~60s-poll retry inside a 30-minute window (`_DRAIN_EXIT_NO_FRESH_PRICE_RETRY_WINDOW_SECONDS`, `runner.py:82`); fail-closed preserved, one alert on final drop only | 2026-07-20 (same morning) |
| 5 | Total-exposure cap vetoed covered exits — the one cap that missed #363's DC-1 exemption; an over-cap account made every position unexitable | 07-21 13:31–17:14 UTC: **1,072** × `max_total_exposure_exceeded` blocked exit submits (5 intents × ~60s polls) | #378 — DC-1 exemption on `_check_total_exposure` (`risk/evaluator.py:639`), risk-reviewed pre-merge | 2026-07-21 13:14 ET (mid-session) |

Blocker 5 closed with the full recovery loop inside one session: veto firehose
observed at the 09:31 ET open drain → root-caused → 7-line fix risk-reviewed
and merged 13:14 ET → fleet controlled-stopped 17:15–17:16 UTC and redeployed
17:16 UTC → all five queued exits consumed, submitted through the chokepoint,
and **filled** (XLF 182 ×2, XLV 61 ×2, JNJ 38; fill sync explanation `1036979`,
"5 order(s) closed from broker truth", 17:18:35 UTC) → reconcile **CLEAN**
17:18:37 UTC (run `f1b13d53`) and again post-close 20:39:46 UTC (run
`7a0766a8`). The important governance fact: during the ~3h45m the false veto
stood, **no override was used and no gate was touched at runtime** — 1,072
vetoes stood, positions stayed safe, and the fix went through review. That is
the harness behaving exactly as FOUNDER_INTENT demands under its least
flattering failure mode.

*(Honest wrinkle, recorded: the 17:16 UTC per-runner launch-guard reconciles
raced the sibling runners' in-flight exit submits — four transient `dirty` runs
with incidents recorded (ids 209–212, `order_local_only_recent` /
`position_qty_mismatch`) that were true statements about a mid-flight instant,
resolved CLEAN two minutes later. Mid-session redeploy reconciles racing an
active drain are noise-prone; noted for M5's multi-session definition, not a
defect.)*

### 2.2 The full round trip — entries and exits, including same-symbol co-run

- **Mon 07-20** (scheduled one-shot launch 13:00 UTC, 6 runners, 6 launch
  reconciles clean): the open drain re-validated 8 queued entry intents —
  the 5 locked at Friday's close **submitted and filled** (SPY 11 regime;
  **QQQ 13 bbands + QQQ 13 rsi2 — the ADR 0055/0056 same-symbol co-run path
  live with real fills**; DIA 18 rsi2; SMH 17 rsi2), and 3 stale 07-15
  leftovers (GOOGL/INTC ×2) vetoed fail-closed
  (`disable_condition_active` + `stale_market_data`). Fills were recorded **in-session** by the
  #342 session-close fill sync (explanations `1035891`/`1035893`/`1035895`,
  20:25 UTC) — the sync's first non-vacuous live exercise, closing the M2
  deviation that left it proven only by a manual run. Scheduled controlled
  stops 20:25 UTC, 6/6.
- **Tue 07-21**: pre-open reconcile CLEAN diff=0 (run `25e13a50`, 12:09 UTC)
  after the stray 07-15 operator-CLI SPY order synced; the gauntlet-5 session
  above; manual EOD close-out (the 16:22 ET stop cron died with Monday's
  session) — controlled stop ×6 at 20:38–20:39 UTC, post-close reconcile
  CLEAN.
- **Net effect:** decision → queue → drain → re-validation → submit → fill →
  session-close fill sync → controlled stop → session-scoped reconcile is now
  proven **in both directions** (entries Mon, exits Tue), on the real fleet,
  with the event store carrying every hop.

### 2.3 Recovery in the wild — the schedule died and the state survived

The one-shot launch/stop scheduled tasks are **not self-renewing**: they are
re-created by the driving session, so a session death (usage exhaustion,
observed twice this window) silently leaves no task for the next day. Observed
consequences, all recovered clean:

- **07-14**: launch fired, stop never did; the fleet died with the machine —
  6 runs closed `orphaned_no_live_runner` by the reaper 07-15 15:16 UTC. The
  dead-runner drill's mechanism, exercised for real, ×6.
- **07-15**: manual mid-session relaunch; fleet **crashed** 23:24 UTC on
  `BrokerConnectionError` (connectivity loss, pre-#366) — the real-world
  motivation for #366's bounded connectivity retry (merged 07-16).
- **07-16**: manual relaunch; exits dropped `no_clean_handoff` (gauntlet #3);
  runs orphaned again at 23:21 UTC (second session death).
- **07-17, 07-20**: clean scheduled full sessions, controlled stops 6/6.
- **07-21**: manual launch + manual close-out (schedule dead again after
  Monday's session death).
- **Wed 07-22**: **fleet no-op day** — zero `strategy_runs`, zero open rows
  (no phantoms), zero non-backtest trades or explanations; 10 queued intents
  alive and intact awaiting the next launch (7 entries incl. XLE ×2 / AAPL /
  XOM / CVX from Tuesday's close; 3 rsi2 exits DIA/QQQ/SMH). Clean trading
  state preserved across an unattended day. *(Research lane only: Bench/CLI
  gap-continuation backtests began 23:26 UTC and were still writing
  `submitted_by='backtest_engine'` rows while this draft's queries ran —
  below the risk layer, no broker path, and outside the fleet claim.)*

The posture held through all of it — **fail-closed, durably observed, no
unsafe action** — but the scheduling substrate itself is now the
proven-weakest link. The founder decision on rebuilding self-renewing
launch/stop tasks (vs. the current session-cron driving) is pending and is an
M5 continuity concern, not an M4 gate item (ADR 0012 still forbids a
daemon/scheduler in Phase 1; D-3 owns the fork at M5).

### 2.4 The M4 observables, live

The wave-1/2/4 observables (#352 stale-bar operator alert, #354 unconsumed
controlled-stop age, #353 GUI attention surface) did real work this window:
41 durable `operator_alerts` rows 07-13 → 07-21 — 30 × `stale_market_data_idle`
(every pre-open launch correctly announced why the fleet was idling before
fresh bars, naming the heal) and 11 × `exit_intent_dropped` (every gauntlet
#3/#4 drop surfaced with symbol, reason, and durable row). Zero silent drops:
every exit that failed to submit this week is accounted for by either a
durable alert or a durable veto explanation.

## 3. Gate-item table

Verdicts: **PROVEN** (acceptance evidence exists per the M4 spec — injected
fault or live event + operator-facing message + durable record), **PARTIAL**
(materially evidenced, a named remainder is outstanding), **OPEN** (not yet
performed).

| Gate item | Verdict | Evidence |
|---|---|---|
| Drill: stale market data | **PROVEN** | Drill PASS (matrix); live: 30 `stale_market_data_idle` alerts 07-13→07-21 + 3 stale-intent vetoes 07-20 (#352 observable) |
| Drill: locked SQLite DB | **PROVEN** | Drill PASS — fail-closed exit, lock message, TROUBLESHOOTING pointer |
| Drill: corrupt SQLite DB | **PROVEN** | Drill PASS — fail-closed exit, `.pre-compact-*.bak` restore pointer |
| Drill: broker outage / API error | **PROVEN** | Drill PASS (auth-classified fail-closed); live: 07-15 fleet crash on `BrokerConnectionError` → #366 bounded retry (the retry path itself has not yet seen a live outage post-merge — noted, not gating: the drill is the spec) |
| Drill: dead runner | **PROVEN** | Drill PASS; live ×12: 07-14 and 07-16 orphans closed `orphaned_no_live_runner` by the reaper |
| Drill: wedged runner / controlled-stop-on-wedged | **PROVEN** | Drill PASS (wedged + moot variants); #354 unconsumed-stop-age observable live on DESK; no live wedge occurred this window (drill is the spec) |
| Kill-switch trip→reset (mechanism) | **PROVEN** | Drill PASS — `halt --confirm` fail-soft trip on failed cancel, durable `activated`/`reset` rows, reset refuses without `--confirm` |
| Kill-switch HR-4/HR-5 founder GUI walk | **OPEN** | Un-walked. Script refreshed for the post-cleanup GUI: [`docs/drills/2026-07-24-founder-gui-walk-script.md`](../../drills/2026-07-24-founder-gui-walk-script.md) §3 |
| F1 clean-room / first-launch | **PARTIAL** | CLI side PROVEN (drill `clean_room` PASS incl. founder-machine re-verification post-#362: fail-closed on missing creds, fresh DB auto-migrated schema_version=17). GUI first-run empty-state walk remains founder-manual (walk script §1) |
| F2 end-to-end lifecycle rehearsal | **OPEN** | Not run on current code (LAUNCH_READINESS §0's walk is 2026-05-15, pre hundreds of merges). Scheduled next market session |
| F3 failure/recovery drills | **PROVEN** | The drill matrix *is* F3 — 8/8 PASS with operator-facing output cross-checked against TROUBLESHOOTING |
| D-7 fault-mode docs absorbed | **PROVEN** | #281 merged (M0 adjudication); runbook additions #373 |
| ADR 0058 lifecycle-criteria enforcement (D-4 "enforce at M4") | **PROVEN** | #356 — R-PRM-004 criteria (a)(b)(c) ENFORCED fail-closed in `prepare_and_record_promotion`; synthetic fault-injection veto (`promotion fault-check`) records the risk layer refusing an over-cap order |
| Live failure-mode proof (unplanned but load-bearing) | **PROVEN** | §2.1–§2.3: five-blocker drain gauntlet peeled under governance; entries+exits round trip filled; reconcile CLEAN diff=0 both days; recovery from schedule death / orphans / crash with state intact; kill switch untouched |
| Autonomy-boundary review | **PARTIAL** | Drafted: [`2026-07-24-m4-boundary-review.md`](2026-07-24-m4-boundary-review.md) — awaiting founder review/signature |
| Founder GUI walks (first-run, creds, observables, HR-4/HR-5) | **OPEN** | Refreshed 20–30 min script ready (link above) |

## 4. What remains open, honestly

1. **The founder GUI walks** (script §1–§3): first-run empty state, negative
   credentials, the M4 observables spot-check, and the HR-4/HR-5 kill-switch
   trip→reset + stop-while-active renders. ~20–30 minutes, scratch-state-safe.
2. **F2 lifecycle rehearsal** on current code — the one gate item with no
   partial evidence. One market session.
3. **Boundary-review signature** (companion draft).
4. Named non-gating residuals for M5: scheduling substrate not self-renewing
   (D-3 territory); #366 retry path unexercised by a live outage; fleet-down
   position management (rsi2's DIA/QQQ/SMH exits sat un-drained on the 07-22
   no-op day — known M5 item); mid-session-redeploy reconcile noise (§2.1
   wrinkle); the D-8 hardcoded-FRESH menu gap review scheduled for this
   M4/M5 boundary (roadmap §10).

**Addendum — observed 2026-07-23 (post-draft, pre-signature; verified against
the event store):**

- (a) The 8 intents locked at the 07-21 close were re-validated at the 07-23
  open drain, after the 07-22 no-op day, and vetoed `stale_market_data`
  (+`disable_condition_active`); all 8 rows left `queued`. The one-session-
  staleness rule held across a skipped day.
- (b) The three rsi2 exit intents (DIA/QQQ/SMH) re-drained and re-vetoed on a
  ~90s cadence all morning — 69/70/70 blocked explanations 13:30→15:14 UTC and
  continuing, versus exactly one veto per entry intent (#346's veto dedup
  covers the ENTRY class only). Candidate follow-up, not fixed: obsolete a
  stale-locked intent on a `stale_market_data` veto instead of retrying it for
  the rest of the session.
- (c) SPY intraday canary `manifest_drift` veto observed 15:11 UTC (2026-05-29
  manifests vs evolved configs); operator re-froze the five SPY canary
  manifests at 15:14 UTC (manifest ids 22–26); subsequent canary vetoes no
  longer carry `manifest_drift`.

## 5. Verification record

- Event store `data/milodex.db`, read-only, 2026-07-22: `strategy_runs` ids
  200–247; allowed submit explanations `1035878`–`1035885` (07-20) and
  `1036969`–`1036977` (07-21); blocked-reason groupings per day 07-13→07-22;
  `queued_intents` ids 40–62; `operator_alerts` ids 1–41;
  `reconciliation_runs` ids 194–214; `kill_switch_events` count-since-07-01
  = 0; fill-sync explanations `1035891/1035893/1035895/1035896/1036979`;
  `promotions` (no rows this window beyond the 07-19 Bench
  `turn_of_month` idle→backtest stage_return); `experiment_registry` = 0 rows
  (M3 runs in its isolated store — unchanged here, as designed).
- Event store re-queried read-only 2026-07-23 for the §4 addendum: blocked
  submit reasons + per-intent veto counts 07-23, `strategy_manifests` ids
  22–26, `queued_intents` ids 55–62 still `queued`.
- Git: #352–#357 (M4 waves), #358–#362 (test-gap + clean-room fix),
  #363/#364 (2026-07-15), #366 + one-shot wrapper `11d5b40` (07-16),
  #367–#371 (audit waves), #372–#377 (week PRs), #374 (07-20),
  #378 (07-21) — dates read off `git log`.
- Code: `risk/evaluator.py:639` (`_check_total_exposure` DC-1 exemption),
  `strategies/runner.py:82,1440–1478` (bounded no-fresh-price exit retry),
  `strategies/runner.py:1254–1269` (`no_clean_handoff` fence) — re-read at
  HEAD before citation.

## 6. Founder sign-off

```
M4 gate decision:   CLOSE / HOLD (circle one)
Walks completed:    §1 ☐   §2 ☐   §3 (HR-4/HR-5) ☐   observables ☐
F2 rehearsal:       PASS / FAIL / WAIVED  — date: ____________
Boundary review:    signed ☐  (2026-07-24-m4-boundary-review.md)
Signature:          ____________________     Date: ____________
Notes:
```
