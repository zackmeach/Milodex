# GUI ↔ Backend Wiring Audit — Python/QML Seams, Top to Bottom

> **Resolution status (post-hardening-roadmap, 2026-06-13):** P1 #1 (kill-switch reset had no reachable GUI path; invoked only from AnchorSurface.qml) is FIXED — #220 removed AnchorSurface.qml and extracted KillSwitchResetModal, reachable from the RiskStrip + drawer. P1 #2 (ledger contamination) FIXED — HR-1 #221. Kill-switch-wedge item → HR-5 #219. Findings retained for history; closed.

**Date:** 2026-06-10
**Scope:** Every seam between backend code and the frontend: composition root (`app.py`), QML singleton registration, the polling read-model layer (14 models), the command bridges (Bench, RiskProfile, OrphanReaper, AppController), the snapshot/query/formatter pipeline, the QML surfaces and components that consume them, threading/shutdown discipline, and the workflow-readiness gates that decide what GUI actions are admissible.
**Method:** Single-context read of the wiring layer (`app.py`, `qml_setup.py`, `polling_lifecycle.py`, `bench_command_bridge.py`, all `*_state.py` read models, `snapshot_builders.py`, `query_helpers.py`, `bench_actions.py`, `strategy_row.py`, `bench_v1.py` + fixtures, `_dashboard_scope.py`, `_event_queries.py`, bridges, `Main.qml`, surfaces, modal/components, `qmldir`), then an **exhaustive mechanical cross-check** of every `<Singleton>.<member>` reference in QML against the Python Q_PROPERTY/Slot/Signal declarations, plus payload-key tracing through the bridge round-trip. Ground truth: the full GUI test suite (`tests/milodex/gui`, **802 passed, 2 skipped, 4 xfailed**, 2026-06-10).
**Posture:** Read-only. This report is the only artifact created.
**Prior art:** [2026-06-10 runner-process audit](2026-06-10-runner-process-audit.md) (the backend half of several findings here); [2026-05-29 truth & direction audit](2026-05-29-milodex-truth-and-direction-audit.md). Deferred items already on record (BenchSurface shell recompose, BenchEvidenceModal de-dupe, no read-model caching layer) are not re-flagged.

---

## Executive summary

The wiring *fabric* is excellent — better than typical for a solo project. Every Python↔QML property/slot/signal reference resolves (verified exhaustively, not sampled); heavy actions ride async bridge variants; the single ordered registry drives registration, polling lifecycle, and Windows teardown with a snapshot test pinning it; read models are uniformly read-only (`mode=ro`), paper-scoped, and bounded post-OOM-incident. What's broken is **reachability and admissibility** — wiring that exists but can never fire:

1. **(P1) The kill-switch reset has no reachable GUI path.** `reset_kill_switch` is invoked only from `AnchorSurface.qml`, and nothing in the running app can navigate there since the FRONT/BENCH/LEDGER/DESK nav rework. ADR 0005's manual-reset flow is CLI-only in practice.
2. **(P1) GUI promote-to-paper can never succeed.** The default workflow-readiness evaluator hard-fails the `data_freshness` dimension ("cannot prove freshness; fail closed"), the dimension is in promote's *required* set, and no real implementation is wired in production. Every promote proposal is born blocked `data_stale`.
3. **(P2) During an active kill-switch event the GUI is fully wedged**: stop-runner proposals *require the kill switch to be inactive* (a de-risking action gated on the thing it would help with), and the reset path is unreachable per #1. Every recovery action requires the CLI.
4. **(P2) GUI runner-start depends on a same-day clean reconciliation that the GUI itself cannot produce** — there is no reconcile affordance anywhere in the GUI, so the morning fleet deploy has a mandatory CLI ritual (or the operator bypasses the Bench entirely, which the 2026-06-10 deploy pattern suggests is what actually happens).

Plus a dead-weight cluster: two registered read models (`KanbanState`, `StrategyBankState`) poll every 30 s with **zero QML consumers**, and a handful of cosmetic contract gaps (freeze-manifest intent copy, the FRONT page's permanently-placeholder market panel).

---

## Wiring map (how the GUI actually talks to the backend)

### Composition root and registration

- `run_app` ([app.py:246-581](../../src/milodex/gui/app.py)) constructs everything, then `_build_qml_registry` ([app.py:102-190](../../src/milodex/gui/app.py)) produces the **single ordered list** that drives (a) `qmlRegisterSingletonInstance` order, (b) polling start order, (c) teardown order (`aboutToQuit` + `AppController.quitRequested` + the load-failure early return). 16 singletons: 13 lifecycle-bearing pollers + ThemeManager + two non-polling bridges. Instances are pinned against GC ([qml_setup.py:88-93](../../src/milodex/gui/qml_setup.py)).
- Bootstrap orphan reconcile runs before any read model renders ([app.py:394-411](../../src/milodex/gui/app.py)); the periodic reaper (`OrphanReaperController`, main-thread QTimer) continues it at a persisted interval.
- Shutdown contract is unusually careful: lifecycle stop loop → global-pool drain (3 s) → explicit Bench-bridge drain (it owns a private pool excluded from the lifecycle filter) → quit; the bridge's `_stopped` flag guards against Qt delivering already-queued metacalls after disconnect ([bench_command_bridge.py:190-228, 335-354, 417-430](../../src/milodex/gui/bench_command_bridge.py)).

### Read-model layer

- `PollingReadModel` ([polling_lifecycle.py:70-187](../../src/milodex/gui/polling_lifecycle.py)) is the canonical base: per-instance `QThreadPool(max=1)`, in-flight drop, `QueuedConnection` back to the main thread, error state that preserves last-known data, reconnect-on-restart. All 13 pollers ride it (OperationalState composes a private `_BrokerPoller` for its broker half and keeps the 1 s kill-switch poll on the main thread).
- Builders are read-only by construction: `mode=ro` URI connections everywhere ([query_helpers.py:28-36](../../src/milodex/gui/query_helpers.py)), paper-scope SQL constants ([_dashboard_scope.py](../../src/milodex/gui/_dashboard_scope.py)) with the `backtest_run_id IS NULL` discriminator — notably *more* disciplined than the risk layer's attribution queries (see the runner audit's P0-1).
- The Phase-5/6 surfaces consume four snapshot builders composed from shared helpers ([snapshot_builders.py](../../src/milodex/gui/snapshot_builders.py)); `_StrategyRow.as_qml()` ([strategy_row.py:63-103](../../src/milodex/gui/strategy_row.py)) is the single row contract, carrying the bench action menu + intent previews + evidence packet computed per refresh on the worker thread.

### Command bridges (write path)

- `BenchCommandBridge` ([bench_command_bridge.py](../../src/milodex/gui/bench_command_bridge.py)) is the only GUI module allowed to import the facade (ADR 0051). Propose/submit pairs for six action families; proposals cached by id and consumed exactly once; operator identity resolved backend-side. QML uses **async** variants for the heavy families (backtest, start-runner, stop-runner) and sync for the DB-write-sized ones (demote, freeze, promote) — verified against actual QML call sites. The `recentCompletions` sink guarantees a completion outcome survives the operator closing the modal mid-spawn.
- `BenchConfirmationModal` correlates async completions by `proposal_id` ([BenchConfirmationModal.qml:273-293](../../src/milodex/gui/qml/Milodex/components/BenchConfirmationModal.qml)), renders **all** blockers (the 2026-05-29 "nothing happened" fix), and handles the `bridge_status: "queued"` intermediate payload distinctly.

### Contract verification (the mechanical sweep)

Every `<Singleton>.<member>` reference across all 44 QML files was extracted and checked against Python declarations. **Result: zero dangling references.** Spot-traces of the dict-payload seams (ActiveOps runner keys, ActivityFeed row keys → ActivityTable normalization, `FrontPageState.summary` keys, `_StrategyRow.as_qml()` camelCase keys → BenchSurface/BenchRow, ledger filter slot arity) all match. The reverse direction is where the findings live: Python surface that QML never reaches.

---

## Findings

### P1-1 — Kill-switch reset is unreachable in the GUI

**Where:** `OperationalState.reset_kill_switch` (token-gated slot, [operational_state.py:425-466](../../src/milodex/gui/operational_state.py)) is referenced in exactly one QML file: [AnchorSurface.qml](../../src/milodex/gui/qml/Milodex/surfaces/AnchorSurface.qml). AnchorSurface is a *hidden* surface — the primary nav is FRONT/BENCH/LEDGER/DESK ([Main.qml:284-287](../../src/milodex/gui/qml/Milodex/Main.qml)), and the only programmatic navigation in the entire QML tree is FrontSurface → "bench" ([FrontSurface.qml:683](../../src/milodex/gui/qml/Milodex/surfaces/FrontSurface.qml)). Main.qml's own comment still calls AnchorSurface the "kill-switch reset modal (sole GUI path; ADR 0035)" ([Main.qml:372](../../src/milodex/gui/qml/Milodex/Main.qml)) — sole, and stranded.

**Effect:** when the kill switch trips, the GUI shows the red RiskStrip badge and the Risk Office drawer, but offers no reset flow. The operator must use the CLI. ADR 0005's "deliberate manual reset" affordance — the thing AnchorSurface was *kept alive for* (PR #201 nearly deleted it; the standing recommendation was to extract `KillSwitchResetModal.qml` first) — was effectively lost anyway when the nav rework removed its tab. Tests load AnchorSurface in isolation ([test_app.py:473-568](../../tests/milodex/gui/test_app.py)) so it *renders*; no test asserts it is *reachable*, which is exactly the property that broke.

**Fix shape:** the already-recommended extraction — pull the reset modal out of AnchorSurface and open it from the RiskStrip badge / Risk Office drawer when `killSwitchActive` — plus a reachability test (assert some user-triggerable path sets `activeSurface = "anchor"` or opens the modal). Then AnchorSurface can finally be deleted.

### P1-2 — GUI promote-to-paper is structurally inadmissible (permanent `data_stale` blocker)

**Where:** `propose_promote_to_paper` requires `_WORKFLOW_REQUIRED_FULL` ([bench.py:97-104, 751-757](../../src/milodex/commands/bench.py)), which includes `READINESS_DATA_FRESHNESS`. The default evaluator returns a **hardcoded blocking failure** for that dimension — "Workflow readiness cannot prove data freshness; submit-capable workflow actions fail closed" ([bench.py:301-313](../../src/milodex/commands/bench.py)) — and `run_app` constructs the facade without a `workflow_readiness` override ([app.py:455-465](../../src/milodex/gui/app.py)); repo-wide grep confirms no real implementation exists anywhere.

**Effect:** every GUI promote-to-paper proposal carries a blocking `data_stale` issue → `admissible=False` → submit refuses. The operator *sees* the blocker (the modal renders it), but the Bench's promote affordance — modal, recommendation/known-risks inputs, evidence wiring, the whole Phase D3 surface — can never complete. Promotion remains CLI-only in practice. Tests pin the blocked behavior as expected ([test_bench_facade.py:1060-1063](../../tests/milodex/commands/test_bench_facade.py)), so this is a known fail-closed scaffold — but nothing in docs or UI copy says "not yet operable," and the modal's input fields invite a workflow that always dead-ends.

**Fix shape:** implement the freshness dimension — a real measure already exists in the CLI (`_data_freshness`, [report.py:259](../../src/milodex/cli/commands/report.py): latest-bar age / trading-days-behind from the event store) — or move `data_freshness` to `inspected_checks` for promote (warning, not blocker), matching how demote treats it ([bench.py:917-923](../../src/milodex/commands/bench.py)).

### P2-1 — Kill-switch-active wedges the entire GUI control surface

**Where:** `propose_stop_paper_runner` puts `READINESS_KILL_SWITCH` in its **required** set ([bench.py:1141-1152](../../src/milodex/commands/bench.py)); an active kill switch is a blocking issue ([bench.py:395-419](../../src/milodex/commands/bench.py)) → stop proposals are inadmissible precisely when the operator most wants runners down.

**Effect, compounding with P1-1:** kill switch active → GUI cannot stop runners (blocked), cannot start them (same gate — correct), and cannot reset the switch (unreachable surface). The GUI degrades to a read-only dashboard during the exact event it exists to manage; every recovery action is CLI. Gating a *de-risking* action on the kill switch being inactive also inverts the reducing-vs-increasing asymmetry principle (risk_defaults.yaml `exposure_policy`; same doctrine family as the runner audit's P2-2).

**Fix shape:** move `READINESS_KILL_SWITCH` to `inspected_checks` for the stop family (warn, don't block). A controlled stop writes a request file and closes a session — it submits no trades; the risk layer independently blocks any trade a still-running runner attempts.

### P2-2 — GUI runner-start depends on a same-day clean reconciliation the GUI cannot produce

**Where:** start-runner requires `READINESS_RECONCILIATION` + `READINESS_BROKER_REACHABILITY` ([bench.py:105-111, 1052-1058](../../src/milodex/commands/bench.py)), both derived from `latest_readiness` — which demands a *persisted, clean, today-NY-dated* reconciliation run ([reconciliation.py:534-603](../../src/milodex/operations/reconciliation.py)). No GUI surface or bridge action runs a reconciliation (the six action families don't include it; the drawer has none). The runner's own startup reconcile happens *after* spawn — i.e., the gate demands a precondition that the gated action itself would satisfy.

**Effect:** a fresh morning GUI session cannot start its first runner until the operator runs `milodex reconcile` in a terminal (or starts one runner via CLI, whose startup reconcile then unblocks the GUI for the rest). The live DB shows the pattern: on 2026-06-10 the three reconciliation runs and the three runner starts land in the same 12:58 minute — consistent with CLI-driven deployment that sidesteps the Bench gate entirely. A fail-closed gate that pushes the operator *around* the governed surface is working against its own purpose.

**Fix shape:** smallest: a "Run reconciliation" affordance (drawer button → facade action → `run_reconciliation`, async). Alternative: let the start family accept "no clean run today" with a warning when the broker-reachability probe itself succeeds, on the grounds that the spawned runner performs the authoritative reconcile pre-trade.

### P2-3 — Two registered read models poll forever with zero consumers

**Where:** `KanbanState` and `StrategyBankState` are constructed, registered, and lifecycle-started ([app.py:426, 429, 166-170](../../src/milodex/gui/app.py)) — and a full-tree grep of all 44 QML files finds **no reference to either**. Each 30 s tick of KanbanState runs the full `_strategy_rows` pipeline: parse all ~31 config YAMLs through the real loader, four SQL projections, and per-open-strategy advisory-lock liveness probes (`_latest_session_states` → `runner_lock_live` → `OpenProcess`); StrategyBankState runs its own bank queries. Their former surfaces (strategy-bank tab, kanban view) were dropped from the nav; the models were not.

Also unused from QML: `BenchState.selectStrategy`/`selectedStrategyId` ([bench_state.py:52-65](../../src/milodex/gui/bench_state.py)) — selection moved into QML-local state.

**Fix shape:** drop both from the registry (and the corresponding `register_qml_types` kwargs / order-snapshot test entries), or park them behind the hidden-surface pattern without lifecycle start. Pure deletion-shaped PR; the registry design makes it a two-line change per model.

### P2-4 — Bench start's audit-link race surfaces in the GUI as a false error

Backend finding (runner audit P2-6), restated here because the *experience* is a wiring one: `submit_start_paper_runner` returns `status="error", runner_audit_link_missing` whenever the spawned child hasn't yet written its `strategy_runs` row ([bench.py:1795-1826](../../src/milodex/commands/bench.py)) — a window that real child boot (pandas/alpaca imports) makes seconds wide. The modal then renders "Blocked — not submitted" / the completion banner records an error for a runner that is in fact coming up; the ActiveOps badge contradicts it ~30 s later. Bounded retry before declaring the linkage missing (matching the 15 s interpreter-probe budget) fixes both the result and the operator's trust in it.

### P3 — Hygiene cluster

1. **Freeze-manifest intent copy missing.** `_action_kind` classifies the label ([bench_actions.py:236-237](../../src/milodex/gui/bench_actions.py)) but `_ACTION_INTENT_COPY` and `_ACTION_FUTURE_RECORD` ([bench_actions.py:134-188](../../src/milodex/gui/bench_actions.py)) have no `freeze_manifest` key — the confirmation modal for a *submit-capable* action renders "Action not recognised by the intent preview." and futureRecord "—".
2. **FRONT page market panel is a permanent placeholder.** `build_front_page_snapshot` hardcodes `_market_placeholder()` — "regime UNKNOWN / Market tape not wired yet" ([snapshot_builders.py:67,88](../../src/milodex/gui/snapshot_builders.py), [row_formatters.py:~200](../../src/milodex/gui/row_formatters.py)) — even though `MarketTapeState` exists and feeds DESK's tape from the same cache. Wire it or hide the panel.
3. **`bench_v1_fixtures.py` is orphaned** (444 lines, no production or surface consumer — only its own test) and its docstring still claims "The Bench surface renders these rows directly when no real data is available," which is no longer true. Delete or correct.
4. **`ActiveOpsState._load_config` fast path never hits.** The slug `strategy_id.replace(".", "_")` ([active_ops_state.py:260-262](../../src/milodex/gui/active_ops_state.py)) doesn't match any real config filename (`momentum_daily_tsmom_v1.yaml` vs derived `momentum_daily_tsmom_curated_largecap_v1.yaml`), so every runner row falls through to a full `configs/*.yaml` glob-parse per refresh.
5. **Per-propose EventStore churn.** Each `_DefaultWorkflowReadiness.evaluate` constructs a fresh `EventStore` per dimension (up to 3 per propose, each paying WAL/migration setup) on the GUI thread ([bench.py:343, 376, 407](../../src/milodex/commands/bench.py)). Propose paths are synchronous QML slots; fine today, but it stacks with the config-scan + lock-probe work already done per propose.
6. **Stale comments:** [test_app.py:233](../../tests/milodex/gui/test_app.py) still says "Main.qml's default surface is AnchorSurface" (it's "front"); Main.qml:372's "sole GUI path" comment is true but now describes an unreachable path (see P1-1).

---

## What's solid (verified, leave alone)

- **The contract sweep came back clean**: every QML→Python member reference resolves; payload keys match through the bridge round-trip (`proposal_id` correlation, `bridge_status` queued shape, blocker lists); `qmldir` registration is complete and the Theme→ThemeManager fallback is tolerant of standalone loads.
- **Threading and shutdown discipline**: per-instance bounded pools, in-flight drop, queued connections, last-known-data error states, the double-path bridge drain with the `_stopped` late-metacall guard, registry-derived teardown order with a snapshot test. The Windows-shutdown contract is engineered, not hoped for.
- **Write-path governance**: propose→revalidate→submit with structured blockers; identity resolved backend-side; async variants for anything that spawns or simulates; `recentCompletions` fallback sink; all six families introspectable via `submitCapableActionFamilies()`.
- **Read-model hygiene**: `mode=ro` everywhere, paper-scope discriminators (`backtest_run_id IS NULL` — the GUI learned the lesson the risk-attribution queries haven't yet), bounded per-source `LIMIT`s post-OOM, identity-verified liveness shared with the CLI (`runner_status` re-exports), heartbeat semantics gated on PID-verified liveness so phantom runners can't read "on schedule."
- **Test ground truth**: 802 GUI tests green, including registry-order snapshot, modal behavior, per-model lifecycle, and QML load smoke.

---

## Recommended action order

| # | Action | Size | Finding |
|---|--------|------|---------|
| 1 | Extract kill-switch reset modal; open from RiskStrip/drawer; reachability test; delete AnchorSurface | small | P1-1 |
| 2 | Move `READINESS_KILL_SWITCH` to inspected for stop-runner | tiny | P2-1 |
| 3 | Real `data_freshness` readiness (reuse CLI `_data_freshness`) or demote to inspected for promote | small | P1-2 |
| 4 | GUI "Run reconciliation" affordance (async facade action) | small | P2-2 |
| 5 | Bounded retry on start audit-link | tiny | P2-4 |
| 6 | Deregister KanbanState + StrategyBankState (or wire surfaces) | tiny | P2-3 |
| 7 | Hygiene sweep: freeze-manifest copy keys, FRONT market panel, fixtures module, slug fast-path, stale comments | tiny | P3 |

Items 1–2 restore the GUI's ability to function *during* the safety event it is built around; item 3 makes the Bench's flagship governance flow actually completable; item 4 removes the daily CLI ritual that currently pushes fleet operations around the governed surface.
