# Milodex GUI Maintainability Hardening — Scope

*Status: **proposed scope**, pending operator approval. No code changes are authorized by this document.*
*Date: 2026-06-01. Produced by a 7-agent fresh-eyes audit (4 structural lenses + 3 change-journey probes) → synthesis → adversarial critique. File:line citations verified against `master` where load-bearing. Critique revisions are folded in (see §6 provenance).*

## Why this exists

The operator reports that routine work — implementing features, adjusting pages, adding strategies, running/monitoring strategies — is more painful and riskier than a robust, scalable system should make it. This scope traces *where change amplifies* and proposes a friction-reducing hardening sequence. The lens throughout is **cost-of-change**, not aesthetics.

Operations taxed are tagged: **(1)** features/pages · **(2)** adding strategies · **(3)** run/monitor · **(4)** changing safely.

## 1. Executive read

The GUI is **structurally sound at the seams but rotting in the middle**. The boundaries that matter are real and enforced: the risk layer is untouched, the ADR-0051 bench facade is test-enforced, and `PollingReadModel` (the lifecycle base every `*_state.py` inherits) is genuinely good — one canonical timer/threadpool/error-state contract with in-flight drop and last-known-data-on-error.

The problem is that **two read-model architectures coexist and never reconciled**: eight clean, uniform `*_state.py` modules on one side, and a 1654-line `read_models.py` god-module on the other that predates the base, opens read-write connections in violation of the layer's own read-only invariant, and re-authors schema knowledge every other module also re-authors.

The dominant source of change-amplification is **the absence of a shared query / contract / registry layer**:
- backtest-metric extraction is rewritten in 4 files / 6+ sites;
- the gate-failure rule exists 3 times and **has already silently diverged** (`read_models.py:1459` uses `sharpe <= MIN_SHARPE`; `strategy_bank_state.py:185` uses `sharpe < MIN_SHARPE` — verified);
- wiring a new read model costs ~13 hand-edited sites across two files with three parallel start/stop enumerations;
- the Python→QML contract is ~40 untyped string keys that fail to `undefined` on rename.

Every "add a strategy / adjust a page / expose a field" pays a ceremony tax and risks a silent, invisible-until-you-look failure. The friction is real and mechanical.

## 2. Ranked problems

Ranked by friction-reduction ÷ effort.

| # | Problem | Evidence (verified ✓) | Taxes | Sev |
|---|---------|------------------------|-------|-----|
| **P1** | **Gate-failure rule diverged** — Sharpe exactly == `MIN_SHARPE` is gate-*failing* on Bench/FrontPage, gate-*passing* on Bank/Attention | `read_models.py:1459` (`<=`) vs `strategy_bank_state.py:185` (`<`) ✓; regime exemption only in `read_models.py:1456-1457` ✓ | 2,4 | high |
| **P2** | **No shared event-store query layer** — `oos_aggregate` metric extraction + `MAX(id) WHERE completed` join re-authored 6+ sites | `strategy_bank_state.py:102-122,133-142`; `attention_state.py:143-153`; `activity_feed_state.py:160-162`; `read_models.py:900-922,1095-1097` | 1,2,4 | high |
| **P3** | **`read_models.py` is a 1654-line god-module** — 4 hand-wired models + 6 query fns + ledger builders + bench-action engine + evidence shaper + formatters | `read_models.py:128,152,188,222`; `:891,927,1247,1307,1341`; `:510,760,805`; `as_qml` `:87-125` | 1,4 | high |
| **P4** | **`read_models.py` uses read-write connections** — violates the layer's own RO guarantee; 6 connections/refresh | `read_models.py:894,930,1223,1250,1310,1344` RW ✓; 6 siblings use `mode=ro` ✓ | 4 | med |
| **P5** | **Strategy registry is a hand-maintained 21-entry list** — omission fails silently: card renders healthy, runner subprocess dies "No strategy registered" | `loader.py:322-376` ✓; raise `loader.py:76-82`; bench renders via `load_strategy_config` not registry ✓ | 2 | high |
| **P6** | **Value formatting copy-pasted across 3–5 QML files** — time/money/sharpe/dd, byte-for-byte | `BenchConfirmationModal.qml:829-846`==`BenchEvidenceModal.qml:244-261`; `Main.qml:93-106`; `DeskSurface.qml:131-143`; `LedgerSurface.qml:59-71`; `FrontSurface.qml:67-78` | 1 | high |
| **P7** | **~13-site read-model wiring ceremony** — three parallel hand-maintained lists; miss one → thread leak or empty first frame | `app.py` 14 `.stop()` ✓; `:380-393` start, `:443-455` fail-stop, `:411-425` controller list; `qml_setup.py:75-92,152-324` | 1 | high |
| **P8** | **Monitoring shows phantom runners as live** — running = `ended_at IS NULL` only, not lock-verified; Desk paints RUNNING green, Bench offers "Stop Trading" on a corpse | `read_models.py:1278-1280`; `active_ops_state.py:213` (unverified) vs `:196-198` (lock-verified) ✓; `DeskSurface.qml:658-660`; hard gate `bench.py:1866-1879` ✓ | 3 | high |
| **P9** | **No shared surface shell** — every surface re-rolls Flickable + manual scroll + centered column + header (~80 lines each) | `FrontSurface.qml:104-154`; `LedgerSurface.qml:114-185`; `DeskSurface.qml:234-301`; `BenchSurface.qml:166-290` | 1 | high |
| **P10** | **`BenchConfirmationModal.qml` is a 1564-line god-component** — 7 near-identical submit-dispatch fns, ~15 `_isXxx` props, 6 inline sub-components; new action = 5 coordinated in-file edits | `:295-560`,`:79-99`,`:599-613`,`:1403-1536`,`:1346-1382` | 1,2 | high |
| **P11** | **QML smoke test asserts ~180–410 raw-source substrings** — reword/rename breaks tests; behavior regressions pass silently | `test_qml_load_smoke.py:356-358,661-722,827-841,1423-1428`; CLAUDE.md `"initial_equity": 100000` trap | 4 | high |
| **P12** | **Read-model tests hand-roll the schema in 8 files** (65 CREATE-TABLE in `test_read_models.py`) — schema drift won't propagate; migration-replay pattern already exists in a sibling | `test_read_models.py:54-151,1350-1402`; correct pattern `test_performance_state.py:879-890,966`; migrations `event_store.py:328-331` | 4 | high |
| **P13** | **Heartbeat off poll interval, not bar period** — healthy daily (1D) runners read "overdue" all day; real stalls indistinguishable from idle | `active_ops_state.py:97-104,62-68`; docstring admits `:28-29`; `DeskSurface.qml:670-676` | 3 | med |
| **P14** | **Tone/status→color mapping duplicated 4+ places** w/ standing "keep in sync" hazard | `RollupCell.qml:33-43`; `BenchSurface.qml:107-112`; `DeskSurface.qml:112-119,562-566`; `LedgerSurface.qml:374-394` | 1 | med |
| **P15** | **Per-strategy governance flags & regime exemption hardcoded** — frozensets keyed on exact id; version bump silently drops flags | `strategy_bank_state.py:152-164`; regime branch `read_models.py:1456`; `lifecycle_exempt` `bench_command_bridge.py:415` | 2 | med |
| **P16** | **Python→QML contract is ~40 untyped camelCase keys** — rename → silent `undefined` | `read_models.py:87-125`,`:760-870`; bare lookups `BenchConfirmationModal.qml:122,168,784,810` | 1 | med |
| **P17** | **Kill-switch reset lives inline in off-nav AnchorSurface** — sole GUI path to `OperationalState.reset_kill_switch`, 130-line inline modal, no nav entry | `Main.qml:374`; `AnchorSurface.qml:467-599,592` | 1,3 | med |
| **P18** | **Async start/stop results dropped if modal closes mid-spawn** — operator thinks it started, sees nothing (start can take 15s) | `BenchConfirmationModal.qml:244-247`; `bench_command_bridge.py:224-251`; `paper_runner_control.py:16` | 3 | med |
| **P19** | **Surface identity duplicated across ~5 unsynchronized lists** — smoke test's `register_qml_types` copy already lags prod (`orphan_reaper`) | `Main.qml:284-287,366-377`; `qmldir:29-33`; `test_qml_load_smoke.py:41-48`; `test_tnum_enforcement.py:67-72` | 1,4 | med |
| **P20** | **Display name derived by dot-position** (`parts[2]`) — wrong/truncated under dotted templates, no error | `read_models.py:1559-1565`; `meanrev_ibs_lowclose.py:55` `template='daily.ibs_lowclose'` | 2 | low |
| **P21** | **Dead components shipped in qmldir** (StrategyRow/GateTable/StatusPill) reachable only by dev showcase | `qmldir`; `DesignSystemShowcase.qml:684-687,719-747` | 1 | low |

37 findings total (12 high / 17 medium / 8 low); lower-leverage items folded into PRs or §5.

## 3. Target shape

Grounded in what exists today:

- **A shared event-store read layer** (`gui/_event_queries.py` / small repository): one `latest_backtest_metrics(conn) -> {strategy_id: {sharpe,max_dd,trade_count,run_id}}`, one `latest_session_states(conn, locks_dir)`, etc. Every read model calls these instead of re-authoring `json_extract`+`MAX(id)`. All connections `mode=ro`; a refresh threads **one** shared connection, not six.
- **One read-model registry**: an ordered `(name, instance)` descriptor list built once. `register_qml_types` iterates it; `app.py` iterates it for `start()`, load-failure `stop()`, AppController membership, and `aboutToQuit`. New model = one append.
- **`read_models.py` decomposed along its seams**: `FrontPageState`/`BenchState`/`KanbanState`/`LedgerState` become their own `*_state.py` on the existing base; ledger builders and the bench-action/intent engine move to dedicated modules; `_StrategyRow`+`as_qml` becomes a shared row module.
- **One liveness resolver** returning `running`/`phantom`/`stopped`/`failed` from `ended_at` + `live_lock_holder` + `exit_reason`, consumed by `active_ops_state`, the session-state reader, and the bench menu. Display trust decoupled from reaper cadence.
- **QML shared primitives**: a `Formatters` singleton (money/time/sharpe/pct/int + tone→color resolver), a `ScrollSurface` shell, an `EditorialHeader`, a `SurfaceBase` declaring the duck-typed contract. New surfaces compose, not copy.
- **A test net split by intent**: keep doctrine perimeters (no mutation tokens, no DropArea, bridge-only command path) as text checks; convert cosmetic/structural pins to behavioral assertions. One shared `EventStore(tmp_path)` conftest fixture replacing 8 hand-rolled schemas; one shared engine-bootstrap builder replacing 4 copied harnesses; **plus a behavioral DeskSurface composition assertion** (see PR9).

**Untouched by design:** the risk layer, the ADR-0051 facade boundary, the `PollingReadModel` lifecycle contract. These are the parts that work.

## 4. PR decomposition (revised per adversarial critique)

Ordered mechanics-before-UI, lowest-risk-highest-leverage first.

**PR1 — Reconcile the gate-failure rule to one implementation** · *tiny* · risk: **medium (highest-scrutiny small PR)** · deps: none
Delete `read_models._gate_failures`; route through `strategy_bank_state._compute_gate_failures`; fold the `family=='regime'` exemption in as a parameter. Removes P1.
⚠ This is the **one verdict-flipping** change in the set. Verify the `<`/`<=` boundary against the authoritative rule in `promotion/policy.py` — **do not** inherit the bank module's boundary just because it's the shared callee. A strategy at exactly `Sharpe == MIN_SHARPE` flips pass/fail.

**PR2 — Shared event-store query helper** · *small* · risk: **low** · deps: none
Extract `latest_backtest_metrics` + the `MAX(id) WHERE completed` join into `gui/_event_queries.py`. Repoint `strategy_bank_state`, `attention_state`, `activity_feed_state`, `read_models`. Removes P2; foundation for PR4, PR6, PR12.

**PR3 — Read-model registry** · *decent* (re-sized from "small" per critique) · risk: **low-medium** · deps: none
Build read models into one ordered `(name, instance)` list driving `register_qml_types`, `start()`, load-failure `stop()`, controller membership, `aboutToQuit`. Removes P7 + P19's prod side.
⚠ Collapsing the 15 `qmlRegisterSingletonInstance` blocks (`qml_setup.py:152-324`) touches **QML-singleton registration order and GC-pin lifetime** — both substring-asserted by the smoke test. Snapshot and assert current start/stop **order** (Windows-shutdown contract, per module docstrings). The test-harness `register_qml_types` duplication (`test_qml_load_smoke.py:41-48`, already lagging prod by `orphan_reaper`) is **in scope** for this PR or it breaks.

**PR4 — `read_models.py` read-only connections + single-connection refresh** · *small* · risk: **low** · deps: PR2
Switch all 6 connections to `mode=ro` (matching siblings); thread one open connection through the `_latest_*` helpers per refresh. Removes P4. RO mode surfaces any accidental write as an error — that's the point.

**PR5 — Strategy registry self-discovery + guard test** · *small* · risk: **low-medium** · deps: none
Replace the 21-entry hand list (`loader.py:322-376`) with `__init_subclass__` self-registration or a package scan. **Minimum viable (ships first if uncertain):** a test asserting every `configs/*.yaml` `(family,template)` resolves in `build_default_registry()` — omission fails at test time, not runner launch. Removes P5.
⚠ Self-registration changes import-time behavior; if anything depends on order, scan-then-sort.

**PR6 — Liveness resolver; gate monitoring on lock verification** · *decent* · risk: **medium** · deps: **PR2** (corrected from PR4; PR4 optional, non-blocking)
One resolver → `running`/`phantom`/`stopped`/`failed` from `ended_at` + `live_lock_holder` + `exit_reason`, living in the shared query layer. Thread `locks_dir` into the session-state reader + `active_ops`; consume in the bench Stop-Trading menu. Removes P8; makes P21's reaper latency moot.
⚠ Touches the run/monitor path. **Display trust and which actions the UI offers only** — submit-time enforcement (`bench.py:1866-1879`) stays the hard gate, unchanged.

**PR7 — Heartbeat threshold off bar period** · *tiny* · risk: **low** · deps: PR6
Threshold off actual cadence from `tempo.bar_size` (or advisory-lock mtime heartbeated per poll), not explanation recency. Removes P13's permanent false-alarm on daily runners.

**PR8 — Async completion sink** · *small* · risk: **medium** (raised from "low-medium" per critique) · deps: PR6
Route async start/stop completions to a persistent sink (activity feed / surface banner) keyed by `proposal_id`, not the transient modal. Removes P18.
⚠ Touches `bench_command_bridge.py:224-251` (fronts the ADR-0051 facade). **Invariant to state in the PR: the sink is read-only display — it never re-issues or acks a command.** Keep the open-modal handler as happy path, sink as fallback; guarantee no double-dispatch (modal + sink both firing could desync a stop-runner result from lock truth).

**PR9 — Shared `EventStore` test fixture + Desk composition net; migrate read-model tests** · *decent* · risk: **low** · deps: none (must precede PR11/PR12)
Promote the `test_performance_state.py:879-890` migration-replay pattern to a conftest `event_store(tmp_path)` fixture; migrate `test_read_models.py` + per-section state tests off the 65 hand-rolled schemas. Removes P12.
**Added per critique:** also add the behavioral **DeskSurface composition assertion** (objectName-based section walk) the audit recommends — the structural desk-snapshot tests were deleted in a prior PR with no behavioral replacement, so Desk section composition is currently under-pinned and the load-smoke gate reports green on a collapsed layout. This net **must exist before PR11 recomposes Desk.**

**PR10 — QML `Formatters` singleton (+ tone→color resolver)** · *small* · risk: **low** · deps: none
Add `Formatters` singleton; repoint every surface/modal. Removes P6 + P14. Visual diff is the verification.

**PR11 — `ScrollSurface` + `EditorialHeader` + `SurfaceBase` shells** · *decent* · risk: **medium** · deps: PR9 (Desk net), PR10
Extract scroll/header/contract scaffold; recompose the four surfaces onto them. Removes P9; addresses P16/Main.qml contract on the QML side.
⚠ Re-deriving scroll mechanics per surface is where behavior drifts. Gated by PR9's Desk composition net and the brittle smoke test (PR11) until PR13's re-aim.

**PR12 — Decompose `read_models.py`** · *large* · risk: **medium-broad** · deps: PR2, PR3, PR4, PR9
Move `FrontPageState`/`BenchState`/`KanbanState`/`LedgerState` onto the base as their own `*_state.py`; extract ledger builders + bench-action/intent machinery; shared `_StrategyRow` module. Removes P3.
⚠ **Critique-flagged churn:** PR1/PR2/PR4 all edit these same call sites first; PR12 then *relocates* them. Budget for re-touching every site PR1/PR2/PR4 touched. Once PR2 has extracted the queries, PR12's remaining job is **moving the 4 read-model classes + ledger/bench-action machinery** — sharpen this boundary in the PR description so PR2 and PR12 don't re-litigate the same code. Land behind PR9's fixture, not before.

**PR13 — Decompose `BenchConfirmationModal.qml` + re-aim the smoke guard** · *large* · risk: **medium-high (highest in set)** · deps: PR10, PR12
Parameterize the propose→submit→blocker skeleton; promote the 6 inline sub-components to the shared library; collapse the TextInput blocks into `LabeledTextField`. **In the same PR**, re-aim P11's assertions: keep doctrine perimeters as text checks, convert cosmetic/structural pins to behavioral checks. Removes P10 + P11.
⚠ The 1564-line file is the single highest-risk GUI file; ~410 substring assertions break on touch. Test re-aim **in step**, never after, or cleanup is paid twice. The `"initial_equity": 100000` trap lives here.

## 5. Deferred / fold-in, and explicit non-deferrals

**Deferred with rationale (not silently dropped):**
- **Cross-model polling fan-out** (dim-3 finding 3, low): ~10 pollers each open a per-query SQLite connection every 30s and rebuild+diff the whole snapshot. PR4 fixes `read_models.py`'s 6 connections; the cross-model story is **deferred because N is small (single operator, ~11 strategies)**. *Revisit trigger:* if poller count or refresh frequency grows materially, or monitoring latency becomes user-visible, introduce a shared connection/snapshot broker.
- **P15** (governance flags → `audit_notes` table; regime exemption → config-declared `lifecycle_proof`). Note the seam with PR1: PR1 parameterizes the regime exemption but leaves it `family=="regime"` string-matched; P15 is the natural completion. Don't re-litigate — it's a known half-step.
- **P16** (typed contract / round-trip key test), **P17** (`KillSwitchResetModal.qml` extraction — *presentational chrome only; the `reset_kill_switch` call stays inside untouched*), **P20** (display name from explicit `variant`/`template`), **P21** (confirm `DesignSystemShowcase` / ADR-0035 integration-smoke dependency before any move — **do not delete**).

## 6. Out of scope / risks

**Do not touch:**
- **The risk layer is sacred.** No PR weakens, parameterizes, or bypasses risk gating. PR6 changes only *display trust* and *which actions the UI offers*; submit enforcement (`bench.py:1866-1879`, the facade, `OperationalState.reset_kill_switch`) is unchanged.
- **The ADR-0051 bench-facade boundary** — read models stay read-only; the GUI reaches commands only through the facade/bridge. Decompositions move code *within* layers, never across the facade.
- **The `PollingReadModel` lifecycle contract** — the registry PR drives it; it does not rewrite it. **Preserve start/stop ordering** (Windows-shutdown contract).
- **The kill-switch reset call** (P17) — extraction is chrome only.
- **`STRATEGY_BANK.md` prose** — operator-facing canon; only drop the "copied verbatim" coupling claim.

**Risks of the pass itself:**
1. **PR1 is a verdict-flip, not cleanup** — verify the boundary against `promotion/policy.py` before merging.
2. **The brittle smoke test (P11) is load-bearing scaffolding for the wrong reasons** — until PR13 re-aims it, PR11/PR12 trip dozens of substring assertions. Re-aim *with* the decomposition; resist the "just update the strings" shortcut that re-pins implementation detail.
3. **PR12 + PR13 are the two large, broad-blast PRs** — land last, behind PR9's real-schema fixture + Desk net and PR3's registry. Resist merging early.
4. **Registry self-discovery (PR5)** changes import-time behavior — scan-then-sort if order matters; ship the guard-test fallback first if uncertain.
5. **`test_app.py` font failure is environmental** (PySide6 ships no bundled fonts in this venv — CLAUDE.md). A full-suite run reporting exactly `1 failed` is almost certainly this, not a regression.

**Net:** 13 PRs — ~3 large / ~4 decent / the rest small-or-tiny. **PR1–PR5 are the highest leverage-per-effort and lowest risk** — they remove the two silent-divergence bugs (P1, P5), the duplicated query knowledge (P2), the read-only violation (P4), and the wiring ceremony (P7) before any UI atom moves. Recommended first milestone: **PR1–PR5 + PR9**, which establishes the shared query layer, the registry, and a trustworthy test net — the foundation every later PR leans on.

---

## Provenance

Method: 7 parallel fresh-eyes agents (read-model layer, QML architecture, Py↔QML boundary, change-safety net, + journey probes for add-strategy / add-surface / run-and-monitor) → synthesis → adversarial critique. ~1.25M tokens, 9 agents. The critique verified all load-bearing citations against `master`, returned **SOUND with revisions**, and its five must-fix items (PR6 dependency, PR2/PR12 boundary, PR3 sizing, polling-deferral rationale, Desk composition net) are folded into §4–§5 above.
