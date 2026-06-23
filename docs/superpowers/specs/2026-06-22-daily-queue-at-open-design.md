# Design Spec — Daily Queue-at-Open (D-1 Option A)

**Date:** 2026-06-22 · **Milestone:** M1 (Executable paper-fleet truth) · **Status:** design
(awaiting founder review) · **Decision:** D-1 = Option A, founder-decided 2026-06-22.
**Adversarially reviewed twice** (Opus, code-grounded) 2026-06-22 — seven must-fix
corrections folded in (see §12 review log).

> This spec is the engineering design for the **queue-at-open** mechanism. The governance
> record is a separate **D-1 ADR** (written before any code, per the sacred-path doctrine
> "decide → ADR → code"). Every diff that touches `risk/`, `execution/`, `promotion/`, or
> the runner goes through the `risk-invariant-reviewer` agent. **Any PR that touches
> `_check_market_open` or `_check_data_staleness` without the preceding D-1 ADR is a
> doctrine violation.**

---

## 1. The decision and its criteria

A daily (`1D`) strategy structurally **cannot submit an order today**: the runner only
evaluates a daily bar *post-close* (`strategies/runner.py:272` — `if is_daily_bar and
market_open: return []`), and the risk layer vetoes every post-close submit
(`risk/evaluator.py:431-437` — `market_closed`). The founder chose **Option A
(queue-at-open)** to keep the six frozen, gate-relevant daily strategies executable rather
than permanently reclassifying them as non-executing analytics.

**Founder's binding acceptance criteria (the spec's contract):**

1. Persist only an **inert, expiring** decision overnight.
2. At the next open, **rebuild sizing** and **rerun the complete risk evaluator battery**
   (not a gap/halt subset).
3. **Idempotency** — never double-submit the same intent.
4. **Halt / LULD** handling.
5. **Reconciliation** of prior-session state.
6. Lands behind an **ADR** and a **`risk-invariant-reviewer`** pass.

**Scope decision (founder, 2026-06-22):** for criteria 4 and 5, M1 ships the
**conservative fail-closed drop** (operator resolves ambiguity), not a full
detection/reconciliation subsystem. M1's bar is *one named fill* + the daily branch-proof,
not a high daily fill-rate.

---

## 2. The spine (what already exists and is reused unchanged)

The risk/execution chokepoint is intact and is the design's backbone. A queued intent is a
**proposal**, never a pre-approved order:

```
submit_paper (service.py:113)
  → fresh EvaluationContext built at submit time (service.py:571-573, :643)
  → RiskEvaluator.evaluate — fixed 17-check battery, allowed = all(passed)
        (evaluator.py:99-126); NO gap/halt subset
  → per-account advisory lock submit.{trading_mode}, fail-closed (service.py:178, :235)
  → single broker call _broker.submit_order inside _submit_locked (service.py:357)
  → atomic explanation+trade pair keyed by session_id (service.py:921)
```

Re-entering `submit_paper` (not `RiskEvaluator.evaluate` directly) is **mandatory** — it is
the only path that re-fetches positions, `recent_orders`, `kill_switch_state`,
`account.daily_pnl`, `latest_bar`, reconciliation readiness, and the active risk profile,
then runs all 17 checks. A direct evaluator call would hand-assemble a stale/partial context
and silently defeat staleness, daily-loss, duplicate, and manifest-drift checks.

The single check that legitimately flips BLOCK→ALLOW across the overnight boundary is
`_check_market_open` (`evaluator.py:428`): `market_closed` overnight, passes at open. **No
other check is relaxed.** Everything else must independently re-pass against next-morning
state.

> **Anchor provenance.** Line anchors were verified against HEAD `625d46e` by two independent
> Opus passes that opened each file. Safety-load-bearing anchors a future edit must not
> silently move are marked **🔒**: `runner.py:272` (the only daily open-market path),
> `runner.py:511` (lockin watermark advance), `service.py:357` (the single broker call under
> the lock), `service.py:643` (fresh-context build), `evaluator.py:99-126` (the 17-check
> battery), `evaluator.py:707/833` (the silently-skipping checks).

---

## 3. Lifecycle (queue-at-open threaded through real seams)

Today the daily lifecycle is a **single post-close phase**: decide *and* submit happen in the
same closed-market cycle (`runner.py:336-382`). Option A **splits** it into two phases bridged
by a durable, expiring intent record.

- **Phase 1 — Decide + persist at close (split point).** At the cycle where
  `_maybe_advance_lockin_watermark(latest_bar)` returns True (`runner.py:336-341`, body
  `:511` 🔒), **persist** the locked-in intent to the new `queued_intents` table **instead
  of** calling `submit_paper` (`runner.py:377`). The lockin watermark still advances
  **exactly once** per close bar, so the runner goes inert for the rest of the closed-market
  period.

- **Phase 2 — Overnight inert.** The intent lives in `data/milodex.db`, surviving both a
  long-lived idle runner *and* a controlled-stop + operator relaunch. The in-memory
  `_processed_intent_keys` cannot serve as the overnight dedup (lost on restart).

- **Phase 3 — Next-open launch + drain (net-new path).** Split `runner.py:272` 🔒: while the
  market is open, **after** the unconditional rollover reconcile (`runner.py:268-269`) and
  **before** returning, drain this strategy's active (non-consumed, non-expired,
  clean-handoff) queued intents and route each through resubmit. The drain **must not touch
  `_last_processed_bar_at`** (Invariant I-3).

- **Phase 4 — Rebuild sizing (recompute, not replay).** Re-run `evaluate()` against a fresh
  context (fresh `account.equity` + freshly-fetched current-session bar), reusing the exact
  context-assembly the post-close path already uses (`runner.py:326-334`). This recomputes
  the share count via `shares_for_notional_pct` (`execution/sizing.py:17`) **and**
  re-validates the entry signal against the open's data in one pass — satisfying criterion 2.

- **Phase 5 — Rerun the full battery.** Resubmit through `submit_paper`; all 17 checks
  re-run against fresh state.

- **Phase 6 — Submit / expire / reject / drop.** On `allowed`, the single broker call fires
  inside `_submit_locked` 🔒 under the per-account lock, gated by the row-scoped idempotency
  consume (I-5). Every outcome (submit / veto / expired / idempotency-suppressed / dropped)
  records an explanation row — the resubmit can never silently no-op.

- **Phase 7 — Reconcile.** The runner's existing startup + rollover reconcile
  (`runner.py:268-269`) gives the drain a guaranteed-fresh reconciliation row. The
  conservative scope (§5) handles the *gaps* in that reconcile by **dropping** (+ alerting on
  exits), not by building new reconciliation machinery.

---

## 4. Net-new components

| # | Component | Detail |
|---|---|---|
| 1 | `queued_intents` table | `core/migrations/016_queued_intents.sql` (016 is next; 015 is highest shipped). Append-only, read by no existing code → `MIN_COMPATIBLE_SCHEMA_VERSION` stays **12** (`event_store.py:401`). Columns: `id` PK; `idempotency_key TEXT NOT NULL UNIQUE`; `strategy_id`; **`strategy_config_path`** (the carry-critical durable field — see I-7); **`config_hash`** (frozen at lock-in; drain refuses on mismatch); `session_id` (the originating session, for the clean-handoff fence I-4); `trading_session`; `locked_in_bar_timestamp`; `symbol`; `side`; `intent_class` (`entry`/`exit`, for audit + the exit-drop alert §5); `notional_pct`; the TOCTOU envelope fields (`expected_stage`, `expected_max_positions`, `expected_max_position_pct`, `expected_daily_loss_cap_pct`); `intent_payload_json`; `reasoning_json`; `created_at`; `expires_at`; `status` (`queued`→`consumed`/`expired`/`obsolete`); `consumed_at`; `consumed_by`. ISO-8601 TEXT timestamps, JSON TEXT payloads. Frozen `QueuedIntentEvent` dataclass + a **single `get_active_queued_intents()` read method** (the sole authority for "drainable"; expiry + clean-handoff predicates baked in) + append/mark-consumed/mark-expired/mark-obsolete methods + `_queued_intent_from_row` helper in `event_store.py`, mirroring the `experiment_registry` quad (`event_store.py:1957`). |
| 2 | Morning-drain hook | Split `runner.py:272` 🔒; per-strategy runner drains its own rows via `get_active_queued_intents()`. |
| 3 | **`bar_size` on `StrategyExecutionConfig`** (BLOCKER enabler) | `StrategyExecutionConfig` (`execution/config.py:17-37`) currently has **no `tempo`/`bar_size`** — so the staleness fix (#4) is *infeasible until this is added*. Add a `bar_size: str = ""` field + read `strategy.tempo.bar_size` in `load_strategy_execution_config` (`config.py:53-63`). Additive, defaults leniently (matches the loader's existing permissive contract for legacy/manual paths). This is what makes `context.strategy_config.bar_size` available to the evaluator. |
| 4 | **Session-aware staleness** (BLOCKER; shipped — see §12) | `_check_data_staleness` (`evaluator.py:440`) was `now() − bar_ts` vs the global `max_data_staleness_seconds: 300`. At the open a daily strategy's latest 1D bar is the *prior* session's close (≫ 300s) → every daily resubmit vetoed `stale_market_data`. **Implementation found a SECOND gate** that must not diverge: `_evaluate_data_quality` (`disable_conditions.py:137`, the `data_quality_issue` **ALL_FAMILIES** disable-condition) also used the global 300s. **Fix:** both gates delegate to ONE shared `risk/staleness.py` helper keyed on the resolved config's `bar_size`. For `1D`: **session-aware** — fresh iff the bar's session date == the exchange calendar's **latest completed session** AND age ≤ a **7-calendar-day** defense-in-depth ceiling; **fail closed** if the session can't resolve. Resolved via a new broker `latest_completed_session(now) -> date \| None` (Alpaca `get_calendar`, no new dependency) threaded into `EvaluationContext.latest_completed_session` by the service. `None` config / non-`1D` → unchanged **300s**. The selector is the resolved config's `bar_size`, never a field on the intent — an intraday intent provably cannot reach the daily path. |
| 5 | **Overnight idempotency key** (BLOCKER, capital) | `client_order_id = str(uuid.uuid4())` per attempt (`service.py:336`); the duplicate-order window is 60s (counted `event_store.py:1219`). Neither catches an overnight re-fire. **Fix:** a stable key `(strategy_id, trading_session, side, symbol)`, `UNIQUE` on the table, **threaded into the resubmit path** so `_submit_locked` can run a **row-scoped atomic compare-and-set** — `UPDATE queued_intents SET status='consumed', consumed_at=? WHERE idempotency_key=? AND status='queued'`, proceed **only if `rowcount == 1`**, inside the per-account lock and **before** `_broker.submit_order` (`service.py:357` 🔒). Per-account serialization + row-scoped CAS together close the double-launch/crash-retry race; the lock alone does not (I-5). |
| 6 | Conservative halt/async-fill handling | §5. |
| 7 | Expiry (single authority) | Absolute `expires_at` on the row. The **read-filter in `get_active_queued_intents()` is the SOLE authority** (every drain path goes through it; a unit test asserts an expired row is never returned). A terminal-state **sweep is folded into the existing startup/rollover reconcile** (`runner.py:268-269`) — which runs on every manual relaunch, so it is **not** a scheduler/daemon (I-9-safe) — writing `expired` audit rows. Default expiry: **one trading session** for both entries and exits (§6). |

---

## 5. Conservative fail-closed handling (the founder's scope choice)

Neither halt/LULD nor async partial-fill reconciliation exists today (`halt_incident_status`
at `reconciliation.py:34` is a dead label — verified; the ledger folds *submitted* qty not
*filled* qty at `attribution.py:241`; `filled_since_last_sync` at `reconciliation.py:40` is
DEFERRED). M1 does **not** build either subsystem. Instead, at drain time:

- **Halt / LULD:** one broker tradable/asset-status read. If the symbol is **not clearly
  tradable**, **DROP** the intent with an audit row. **Fail-closed on every uncertain
  branch:** an *unknown* status → DROP; the read **raising** (timeout / 5xx, common at the
  open spike) is treated identically → DROP + audit row, wrapped so it can never propagate
  out of the drain into the runner loop.
- **Unreconciled prior-session partial fill / ledger-divergence signal:** treated as
  **duplicate-order uncertainty = hard-stop DROP** — `RISK_POLICY.md` #5 already mandates
  this. The resubmit does **not** attempt to size against an ambiguous lot.
- **A dropped *exit* (SELL) raises a durable operator alert** (a distinct event-store row +
  a `WARN` log; operator-GUI surfacing is M2) so a divergent/ambiguous position is
  **surfaced, never silently stranded**. The operator resolves it (manual flatten) —
  consistent with the conservative scope (operator owns ambiguity resolution; the system
  does not auto-reconcile in M1). Exits are **not** a special non-expiring queue class: a
  daily strategy that still holds the position re-emits its exit decision on the next
  post-close cycle, re-persisting a fresh per-session exit intent (same key/expiry rules as
  any intent). *Design note for founder review: M1 relies on the operator alert + the
  strategy's natural re-emission to prevent a stranded position, NOT on a non-expiring
  standing-exit obligation (deliberately removed for simplicity — see §12). If you want a
  hard system-guaranteed standing exit instead of operator-resolves, say so and it becomes a
  scoped addition.*

Daily entries fill on clean mornings (no halt, clean prior fill or flat). A dropped intent is
a *safe non-fill*, fully explained; a dropped exit additionally alerts. The full detection +
reconciliation subsystem (and the consequent higher fill-rate through halts/partials) is an
explicit **fast-follow**, out of M1 scope.

---

## 6. Persist-vs-recompute boundary (load-bearing)

**Persist (durable, overnight):** the signal *shape* — `strategy_id`,
**`strategy_config_path` + a frozen `config_hash`** (the carry-critical fields; see I-7),
`symbol`, `side`, `intent_class` (entry/exit), the config `notional_pct`, the TOCTOU risk
envelope captured at lock-in, the originating `session_id` + close-bar timestamp /
`trading_session`, the `DecisionReasoning` blob (any *new* field on it must use
`field(metadata={"omit_if_default": True})` or it breaks the pinned golden test
`tests/milodex/strategies/test_base_reasoning.py`), `created_at`, `expires_at`, the stable
`idempotency_key`.

**Recompute fresh at open (never persist-and-reuse):** the share **count**
(`shares_for_notional_pct` needs fresh `equity` + fresh `unit_price`; only `notional_pct` is
durable — a persisted count is stale dollar exposure and floors to **0** when
`equity × notional_pct < unit_price`, `sizing.py:32-47`), and the **entire**
`EvaluationContext` (positions, `recent_orders`, session-reset `daily_pnl`,
`kill_switch_state`, `latest_bar`, reconciliation readiness, active risk profile,
`runtime_config_hash`).

**Exits (SELL) — sized off the submitted-fill ledger, stated honestly.** The system has **no
"reconciled-fresh broker-held quantity" primitive**. A daily SELL sizes off
`context.positions` (e.g. `breakout_atr_channel.py:172-199` → `float(context.positions.get(
symbol, 0.0))`), which the runner populates from `strategy_positions(...)` — the
**strategy-scoped *submitted*-fill ledger** (`runner.py:651-652`, `attribution.py:241`), not
a fresh broker reconciliation. The recompute-at-open (Phase 4) re-derives the SELL size off
this *fresh* ledger read, so:

- **Position already flat in the ledger** → SELL sizes to **0 shares** → dropped before
  `_normalize_intent` (I-6); the row is marked `obsolete` (the obligation is moot — the
  position is gone). No re-fire.
- **Ledger shows the lot but broker net diverges** (prior submit didn't fill / partial /
  ADR-0055 sibling flattened net) → the **ambiguity hard-stop DROP + operator alert** (§5)
  fires; the operator resolves. This is the one residual the conservative scope intentionally
  hands to the operator rather than auto-reconciling.

Daily exits **are** in scope (entry-without-exit is unsafe to run).

---

## 7. Hard invariants (must hold; each is a risk-invariant-reviewer checkpoint)

- **I-1 Risk layer never bypassed.** Every queued intent re-enters `submit_paper` →
  `_evaluate` → full 17-check battery → `_broker.submit_order`. No side channel.
- **I-2 Fresh context + config-derived staleness, never replay/spoof.** Build a fresh
  `EvaluationContext` at the open; `_check_data_staleness`, `_check_daily_loss`, and the
  duplicate window are all wall-clock-relative and replay-WRONG. The staleness policy (shipped
  **session-aware** — §12) is derived **inside the evaluator from the resolved config's
  `bar_size`** and applies to **both** staleness gates via one shared `risk/staleness.py`
  helper: for `1D`, fresh iff the bar's session date == the broker calendar's latest completed
  session AND age ≤ a 7-day ceiling, **fail-closed** if the session can't resolve; `None` /
  non-`1D` → 300s. The selector is the resolved config, never a field on the intent — an
  intraday intent cannot inherit the daily path. Required test: a non-`1D`/None-config intent
  gets 300s regardless of any other field.
- **I-3 Never touch `_last_processed_bar_at` at open. *(proof checked, both reviews.)*** The
  drain insertion point (`runner.py:272` 🔒, before fetch) never reaches
  `_maybe_advance_lockin_watermark` (`runner.py:511`, only on the closed-market post-close
  path) nor the `already_seen` / `_last_processed_bar_at` logic (`runner.py:296-299`).
- **I-4 Clean-exit handoff fence (positive token, not absent-kill-switch).** A queued intent
  is drainable **only if** EITHER (a) the *same live session* that persisted it is draining
  it (`session_id` matches the running session — no death occurred), OR (b) the originating
  session closed with **`exit_reason == "controlled_stop"`** (`runner.py:405-406`, a durable
  `strategy_runs.exit_reason` column queryable by `session_id`,
  `event_store.update_strategy_run_end`). The SQL predicate is the **literal**
  `exit_reason = 'controlled_stop'` — **not** `IS NOT NULL`: `interrupted` /`crashed:*`
  (`runner.py:420-424`, neither sets the kill switch), a `kill_switch` exit,
  `orphan_recovered` (bootstrap reconcile), or a SIGKILL/power-loss that wrote **no**
  `exit_reason` at all are all **ambiguous → DROP**. This replaces the false premise
  "force-close ⇒ kill-switch." In-battery `_check_kill_switch` (check 1) remains the backstop.
- **I-5 Idempotency = row-scoped atomic CAS inside the submit lock.** The submit lock is
  **per-account** (`submit.{trading_mode}`, `service.py:235`), so it serializes the two
  `_submit_locked` bodies but does **not** by itself bind the consume to a specific row. The
  `idempotency_key` (+ row id) is threaded onto the resubmit path so `_submit_locked` runs a
  single-statement CAS (`UPDATE … WHERE idempotency_key=? AND status='queued'`, proceed only
  if `rowcount == 1`) before `_broker.submit_order` (`service.py:357` 🔒). Per-account
  serialization **plus** the row-scoped CAS together close the double-launch / crash-retry
  race; neither alone does.
- **I-6 MARKET-only, drop zero-share.** `_normalize_intent` raises `ValueError` on
  `quantity <= 0` (`service.py:709`) and `UnsupportedOrderTypeError` on non-MARKET
  (`service.py:713`). A 0-share recompute must be **dropped** before it reaches
  `_normalize_intent`, never submitted (an entry: drop; an exit sized to 0 because the ledger
  is flat: mark `obsolete`).
- **I-7 `strategy_config_path` + frozen `config_hash` are carry-critical (not
  `strategy_name`).** `strategy_name` is **not** a field on `TradeIntent` (`models.py:48-76`);
  it is re-derived at submit time from the YAML at `strategy_config_path` (`service.py:755`),
  and `_check_strategy_concurrent_positions` (`evaluator.py:707` 🔒) + `_check_duplicate_order`
  (`evaluator.py:833` 🔒) silently **skip** (return passing) when the resulting `strategy_name`
  or `event_store` is None. Overnight failure mode: the YAML at `strategy_config_path` is
  edited/moved/renamed between persist and drain → `strategy_name`/`stage`/`notional_pct`
  silently change, or both checks skip. Defense: persist a frozen `config_hash` at lock-in;
  the drain **re-resolves the config and refuses (drops) on hash mismatch or missing path**,
  and asserts `strategy_name`/`event_store` non-None, failing closed. (Hash over normalized
  content so CRLF/format-only churn does not false-drop.)
- **I-8 Additive schema only.** `016` only; never edit a shipped migration;
  `MIN_COMPATIBLE_SCHEMA_VERSION` stays 12.
- **I-9 Manual-launch only (ADR-0012-clean).** A's trigger is the operator's pre-open
  relaunch draining the durable queue. The moment "resubmit at open" is wired to any
  scheduler / daemon / wake-timer it becomes the **D-3 auto-launch** decision and requires an
  ADR 0012 amendment. The ADR states this assumption explicitly. (The expiry sweep folded
  into the runner's existing reconcile is launch-time, not a timer — I-9-safe.)

---

## 8. Error handling / fail-closed policy

Every uncertain path **blocks or drops**, never retries-around:

- A blocked resubmit (any of the 17 checks fails) is a real veto — recorded, not retried.
- Halt / not-tradable status → drop + audit row. The tradable read **raising** → drop + audit
  row (wrapped; never propagates into the runner loop).
- Unreconciled prior partial fill / ledger-divergence → hard-stop drop; if an **exit**, also
  a durable operator alert (event row + `WARN`) so a position is never silently stranded.
- 0-share recompute → drop (entry) / mark `obsolete` (exit on a flat ledger) before
  `_normalize_intent` (`service.py:709`).
- Non-clean overnight handoff (not `controlled_stop`, or no `exit_reason` record) → drop (I-4).
- `config_hash` mismatch / missing config path at drain → drop (I-7).
- Expired intent (read-filter authority) → never returned by `get_active_queued_intents()`.
- Advisory-lock failure → fail-closed (`service.py:178`).

---

## 9. Testing & assurance evidence (D-6 gate)

M1 enters R-EXE / risk-check / R-PRM territory — trust-critical requirement classes. Per the
founder's D-6 assurance gate, anything M1 touches on the versioned critical-requirement
allowlist owes **contract-appropriate, independently-reviewed per-clause evidence** — a
happy-path test does not satisfy it; code references do not satisfy it. Build evidence **as**
the feature, TDD-first:

- **Positive:** clean morning → queued BUY drains, recomputes, passes all 17, submits, fills.
- **Refusal:** kill-switch active → drain fence + check 1 refuse; each of the 17 checks blocks.
- **Boundary (staleness):** a 1D bar at the open passes the config-derived session budget; an
  intraday bar > 300s blocks; **prove an intent whose resolved config `bar_size ≠ "1D"` (or
  None) gets 300s regardless of any field** (I-2 carrier-safety).
- **Fail-closed:** halt/not-tradable → drop; tradable-read-raises → drop; unreconciled
  partial → hard-stop drop; 0-share entry → drop; 0-share exit on flat ledger → `obsolete`;
  lock failure → fail-closed; expired → never returned; `config_hash` mismatch → drop;
  non-`controlled_stop` handoff → drop.
- **Idempotency / durable-state integration:** double-launch + crash-retry → **exactly one**
  submit (row-scoped CAS under the lock, asserted via `rowcount`); intent survives
  controlled-stop + relaunch and drains exactly once.
- **Clean-exit fence (I-4):** an intent from an `interrupted` / `crashed` / SIGKILL / `kill_switch`
  / `orphan_recovered` session is **dropped**; an intent from the same live session or a
  `controlled_stop` session drains.
- **Watermark integrity (I-3):** the at-open drain does not suppress that night's
  authoritative post-close evaluation.
- **Exit safety:** ledger flat between sessions → next open recomputes 0 shares → `obsolete`,
  never fires; ledger-vs-broker divergence (ADR-0055) → hard-stop drop + operator alert.
- **Operational drill:** controlled-stop after persist → relaunch pre-open → drain → fill;
  durable logs + operator-visible explanation chain (decision → persist → drain → submit →
  fill → reconcile).

**Green baseline to regress against** (M0 close, `625d46e`): `3294 passed, 1 skipped,
4 xfailed`. The lone skip is the design-system-showcase quarantine. Run tests/lint via
`.venv\Scripts\python.exe -m pytest -q` / `-m ruff check src/ tests/ scripts/`.

---

## 10. Governance & sequencing

- **D-1 ADR** written before any code (records the decision, the mechanism, the
  idempotency-key composition + row-scoped CAS, the config-derived staleness budget, the
  clean-exit handoff fence, the conservative halt/async-fill policy + exit-drop operator
  alert, and the explicit manual-launch / ADR-0012-clean assumption).
- **`risk-invariant-reviewer`** (Opus) on every diff touching the runner, `evaluator.py`
  (staleness + any new check), `service.py` (idempotency guard), the execution config loader,
  and the kill-switch / clean-exit suppression path.
- **Distinct from D-3 (auto-launch)** — A assumes manual pre-open relaunch; auto-launch is a
  separate founder decision still to be framed.
- **Distinct from D-2** — the intraday freeze governance work (demote the 32 divergent YAMLs,
  load/promotion preflight validation, SPY-canary pre-open launch, no new non-SPY freezes) is
  a sibling M1 deliverable tracked separately, not in this spec.

## 11. Out of scope (explicit)

- Full halt/LULD detection subsystem and async partial-fill reconciliation fold (fast-follow).
- A hard system-guaranteed standing-exit obligation (M1 uses operator-alert + strategy
  re-emission; §5 design note).
- Any scheduler / daemon / auto-launch (that is D-3).
- New non-SPY intraday freezes (that is D-2 Option B, deferred behind D-4).
- Profitability of any daily strategy (trust over profit; M1 needs one named fill).

---

## 12. Review log

**2026-06-22 — Review round 1 (independent Opus, code-grounded).** Spine + I-3 verified
sound. Five must-fix corrections folded in: (1) staleness budget derived from
`context.strategy_config` not a spoofable intent field; (2) row-scoped idempotency CAS (lock
is per-account, not per-intent); (3) clean-exit positive-token fence (force-close ⇒
kill-switch was false for `interrupted`/`crashed`/SIGKILL); (4) exits honestly size off the
submitted-fill ledger; (5) single `get_active_queued_intents()` expiry authority. Anchor
fixes: `strategy_config_path`+`config_hash` carry-critical (not `strategy_name`, I-7);
`service.py:713` is the order-type raise.

**2026-06-22 — Review round 2 (fresh independent Opus, verified the round-1 fixes).** Two
further must-fix items: (6) **BLOCKER** — `StrategyExecutionConfig` (`execution/config.py:17-37`)
has **no `tempo`/`bar_size`**, so the round-1 staleness fix was infeasible as written → added
§4 #3 (add `bar_size` to the dataclass + loader) and the None-safe selector (I-2). (7)
**MAJOR** — the round-1 non-expiring standing-exit class contradicted the per-session
consume-once idempotency key (stuck exit, or stale re-fire against a flat account) →
**removed the non-expiring class**; exits now use the same per-session key + expiry, with a
`0-share → obsolete` terminal on a flat ledger, an ambiguity hard-stop-drop + operator alert
on divergence, and reliance on the strategy's natural re-emission (§5, §6). Confirmed feasible
on real seams: `exit_reason` is a durable `strategy_runs` column; SIGKILL leaves
`exit_reason` NULL (queryable DROP state); `_submit_locked` threading is a clean additive
kwarg.

**2026-06-23 — Implementation (Phase 2) found a deeper staleness issue; founder re-decided.**
Two corrections to the staleness design (§4 #4, I-2), both shipped (commits `eecc38e` +
`cfe19d1`, double-gated): (8) **a SECOND staleness gate** — `_evaluate_data_quality` (the
`data_quality_issue` ALL_FAMILIES disable-condition, `disable_conditions.py:137`) also used the
global 300s and is docstring-mandated to never diverge from `_check_data_staleness`; widening
one alone just relocated the veto. Both now delegate to one shared `risk/staleness.py` helper
(non-divergence is structural, parity-tested). (9) **the "one-session / 23h budget" was wrong**
— a Friday-close daily intent drains at Monday's open against a ~65-85h-old bar, so 23h
silently killed every Monday/post-holiday daily resubmit. Founder re-decided (2026-06-23):
**session-aware** — bar's session date == the broker calendar's latest completed session
(Alpaca `get_calendar`, no new dep, threaded via a new `EvaluationContext.latest_completed_session`),
with a 7-day defense-in-depth ceiling, **fail-closed** if the calendar can't resolve, 300s for
non-1D/manual. Cross-phase contract for Phase 6: the drain must feed the gate the **locked-in
session bar**, not the live intraday `get_latest_bar` (today-dated during RTH), or the
session-identity check fails-closed-stale.
