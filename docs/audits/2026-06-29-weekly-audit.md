# Milodex Weekly Audit — 2026-06-29

_15-vector fan-out (Opus), adversarial verify on every P1 / risk-touching ticket, then dedup + synthesis. 57 raw tickets → 46 real (4 false positives dropped, 1 cross-vector merge). No P0._

---

## PR Plan & Tracking

Living work list. The 46 tickets below are the **evidence record** (don't edit them); this section is the **mutable tracker** — 46 tickets clustered into 11 PR units, ordered safest-fastest first. Check the box when a PR lands and append its number.

**Wave 1 — zero-risk fast wins (no risk layer; doc/CI/deps)**
- [x] **PR-1 · CI hardening** ([#294](https://github.com/zackmeach/Milodex/pull/294)) (small) — add `ruff format --check` gate + reformat the 15 drifted files (atomic: reformat *then* gate) + pin install to `uv.lock` (`uv sync --frozen`) + job `timeout-minutes`. Touches `.github/workflows/ci.yml`, `tests/**`.
- [x] **PR-2 · Doc-drift reconciliation** ([#296](https://github.com/zackmeach/Milodex/pull/296)) (small, doc-only) — OPERATIONS daily-exec note · STRATEGY_BANK co-run contradiction · ADR 0055 "code-enforced" contradiction · ADR 0057 added to index · README "By the Numbers" + coverage figure · README/CLAUDE.md `uv venv` warning · `.env` contract note. **Excludes CURRENT_ROADMAP §2 banner** — that edit is gate-only; do it at the next M1 gate update (see PR-11).
- [x] **PR-3 · Dependency hygiene** ([#295](https://github.com/zackmeach/Milodex/pull/295), supersedes #283) (small) — alpaca-py + heavyweight upper bounds · pandas floor →2.2 · remove `pytz`. Touches `pyproject.toml` + `uv lock`.

**Wave 2 — RISK layer (each behind `risk-invariant-reviewer` before merge)**
- [x] **PR-4 · Runner/data crash-resistance** ([#301](https://github.com/zackmeach/Milodex/pull/301)) (RISK, small) — per-intent submit isolation in the intraday loop (mirrors the drain's entry/exit asymmetry; EXIT raise → durable operator alert, ENTRY raise → log-only) · `get_latest_bar` now uses the shared transient-retry helper. Typed data-exception SKIPPED (YAGNI: no caller distinguishes transient-vs-permanent; `_fresh_pricing_bar` + cap-pricing both already fail closed). risk-invariant-reviewer: APPROVE.
- [x] **PR-5 · Broker status/error mapping** ([#284](https://github.com/zackmeach/Milodex/pull/284) read-path + submit classifier; [#300](https://github.com/zackmeach/Milodex/pull/300) PR-5b status map + swallow-logging) (RISK, small) — explicit Alpaca status map + WARN-on-unknown · `stopped`/`suspended`/`pending_review` → PENDING (not terminal) · order_type WARN · log swallowed `get_position`/`cancel_order`. risk-invariant-reviewer: APPROVE after one CONCERN fix (`stopped`→CANCELLED undercount). **Residual:** `pending_cancel`→CANCELLED is the same undercount class, pre-existing — follow-up task queued.
- [x] **PR-6 · Surface operator_alerts** ([#299](https://github.com/zackmeach/Milodex/pull/299)) (RISK, small) — `list_operator_alerts` wired into `strategy status`; severity-aware so an above-info stranded-exit alert is never hidden behind the info cap. Closes the D-6 write-only gap. GUI rail = follow-up. risk-invariant-reviewer: APPROVE after one CONCERN fix (silent `[-10:]` truncation).
- [ ] **PR-7 · EventStore WAL once** — **DEFERRED** (2026-06-30): on an already-WAL DB, `PRAGMA journal_mode=WAL` per-connection is a fast near-no-op read; the measured saving is nil and the cost is touching the concurrent-construction race tolerance on the sacred event store. Re-open only if connection churn is ever shown to matter.

**Wave 3 — polish / debt (no risk layer)**
- [ ] **PR-8 · GUI surface honesty** (small) — bind `dataStatus`/`dataErrorMessage` on FRONT/BENCH/LEDGER (extract `SectionStatus.qml` first) · LedgerSurface empty state + footer fix · delete dead "strategy detail" link.
- [x] **PR-9 · Strategy dedup** — **done via #275** (consolidate RSI/ATR/EMA onto `_indicators`, parity-tested; merged 2026-06-30). Residual `_no_signal`/`_entry_price` hoist + bound-recheck removal still open if wanted.
- [x] **PR-10 · Test-coverage fill** — decider feature-kit edge branches **done via #275** (`test_decider_features.py` + `test_indicator_parity.py`). `resolve_position` guard tests still open.

**Wave 4 — opportunistic (P3 grab-bag; fold into adjacent PRs or one sweep)**
- [~] **PR-11 · Cleanup sweep** — *partial.* **Done:** gitignore `.cursor/` + commit this audit tracker into `docs/audits/` (chore PR, 2026-06-30). **Deferred (P3 / judgment):** `collapse resolve_strategy_config` chain (risk-adjacent config refactor, no functional bug → surgical rule says skip) · CLI per-strategy log filename · 0o600 file perms (near-no-op on Windows) · SPY parquet memoize · `experiment_registry` enum validation. **Operator call:** `RESUME_EXTRACT.md` keep/delete — left untracked, surfaced to operator. **Gate-only:** CURRENT_ROADMAP §2 D-1 banner (do at next M1 gate update).

**Deferred — already-tracked / no-action:** a11y keyboard nav (ADR 0045, marginal value for single-operator tool) · LLM eval track (roadmap §537) · GUI current-stage + S/D/N consolidations (roadmap §10, audit PR-K / #13).

---

## Summary
46 real tickets after dropping 4 false positives and merging 1 cross-vector duplicate. Breakdown: 11 P1, 18 P2, 17 P3 (no P0). Top 3 themes: (1) **crash-resistance asymmetry on the live trade path** — the intraday submit loop and `get_latest_bar` lack the per-intent isolation the drain path already has (RISK); (2) **write-only safety surfaces** — `operator_alerts` are durably recorded but never read by any CLI/GUI, undermining the D-6 stranded-exit story (RISK); (3) **CI/doc drift** — CI ignores the tracked uv.lock, omits `ruff format --check` (15 files already drifted), and several authoritative docs (OPERATIONS, STRATEGY_BANK, ADR 0055/0057, roadmap §2) still describe daily-execution and the co-run guard as they were before ADR 0057 landed.

## Do This Week
- [P1][tiny] Add `ruff format --check` to CI — `.github/workflows/ci.yml:34`, `README.md:147` — add the one-line gate; 15 files already drift past it
- [P1][tiny] Reformat the 15 ruff-drifted files — `tests/milodex/**` (7 named + 8 more) — run `ruff format src tests`, commit; pair with/precede the CI gate
- [P1][small] Pin CI install to uv.lock — `.github/workflows/ci.yml:29-32` — wire `uv sync --frozen` (or lock-matched bounds) so the tracked lockfile stops being inert
- [P1][small] Fix STRATEGY_BANK.md co-run self-contradiction — `docs/STRATEGY_BANK.md:251,23` — rewrite the stale line-251 callout to match the guard-removed concurrency section
- [P1][small] Refresh OPERATIONS.md daily-execution holding note — `docs/OPERATIONS.md:137` — state daily now resolves via queue-at-open (ADR 0057); fix evaluator cite to :437/:445
- [P1][small] Surface operator_alerts to the operator (RISK) — `core/event_store.py:981`, `cli/commands/strategy.py:128` — wire `list_operator_alerts` into `strategy status`; alerts are currently write-only
- [P1][small] Wrap intraday submit loop so one submit exception can't kill the runner (RISK) — `strategies/runner.py:437-448` — try/except per-intent, mirror the drain path; add a re-raise-path test
- [P1][tiny] Harden `get_latest_bar` against empty response + transient timeout (RISK) — `data/alpaca_provider.py:332-348` — switch to `call_with_retry_on_transient`, typed error on missing symbol
- [P1][tiny] Warn against `uv venv` at the README/CLAUDE.md install step — `README.md:92`, `CLAUDE.md` — one-line trampoline caution at the decision point
- [P2][tiny] Add ADR 0057 to the ADR README index — `docs/adr/README.md:87` — append one row for the keystone M1 decision
- [P1][small] Add `submit_order` APIError→typed-exception tests (RISK) — `tests/milodex/broker/test_alpaca_client.py` — assert all four terminal mappings; tests-only
- [P2][tiny] Delete the verbatim `wilder_rsi_series` copy in meanrev_rsi2_intraday — `strategies/meanrev_rsi2_intraday.py:314`, `_indicators.py:30` — import the canonical one, ~35 lines deleted
- [P3][tiny] Untrack + gitignore `.cursor/` Ralph scratchpad — `.cursor/ralph/scratchpad.md`, `.gitignore` — loop is complete; gitignore so scratch can't return
- [P2][tiny] Add LedgerSurface empty state — `gui/qml/.../LedgerSurface.qml:260` — mirror ActivityTable filter-aware empty copy (already-tracked, ADR 0038)
- [P2][tiny] Remove unused `pytz` runtime dep — `pyproject.toml:18` — zero first-party imports; stays as yfinance transitive

## Full Backlog

### P1

**[test-coverage] Add tests for `AlpacaClient.submit_order` APIError→typed-exception translation** (RISK)
- Files: `src/milodex/broker/alpaca_client.py:206-216`, `tests/milodex/broker/test_alpaca_client.py`
- Problem: The four-branch terminal-error classifier (forbidden/auth→BrokerAuthError, insufficient→InsufficientFundsError, else OrderRejectedError; connect/timeout→BrokerConnectionError) is completely untested. `execution/service.py:554` catches exactly `(OrderRejectedError, InsufficientFundsError)`; a misclassification breaks the chokepoint's error handling silently.
- Action: Monkeypatch `self._client.submit_order` to raise each representative message; assert exact translated type plus catch-all. No source change.
- Effort: small | Confidence: 0.92 | Verdict: real

**[performance] Stop running `PRAGMA journal_mode=WAL` on every EventStore connection open** (RISK)
- Files: `src/milodex/core/event_store.py:2966,3325`, `src/milodex/backtesting/simulation_kernel.py:784`
- Problem: `_connect()` runs a write pragma (`journal_mode=WAL`) on every one of ~84 connect sites; WAL is persistent/db-level (the function's own docstring says so). Backtest per-row writes drive thousands of redundant re-assertions.
- Action: Set WAL once on the `__init__`/migration setup connection; drop `_set_wal_mode` from the per-statement hot path. Keep `busy_timeout`/`foreign_keys` per-connection. Don't touch the race logic. Verify the concurrent-construction test.
- Effort: small | Confidence: 0.78 | Verdict: real (deletion/simplification, no txn-semantics change)

**[reliability-errors] Wrap the intraday submit loop so one `submit_paper` exception cannot kill the runner** (RISK)
- Files: `src/milodex/strategies/runner.py:437-448,241-244`, `src/milodex/execution/service.py:581-596`
- Problem: The intraday submit loop has no try/except. Any unexpected broker failure is finalized as `error` and re-raised by the service, propagates through the unguarded loop into `run()`, sets `exit_mode='crashed:...'`, and terminates the process (no supervisor restart per ADR 0026). The drain path already isolates per-intent — asymmetric crash-resistance.
- Action: try/except per-intent (log, alert, continue), matching the drain path. Add a test with `submit_paper` raising `BrokerConnectionError`.
- Effort: small | Confidence: 0.85 | Verdict: real

**[reliability-errors] Guard `AlpacaDataProvider.get_latest_bar` against empty/missing-symbol response and transient timeouts** (RISK)
- Files: `src/milodex/data/alpaca_provider.py:332-348`
- Problem: `response[symbol]` is unguarded (raw KeyError on a no-data symbol) and uses `call_with_retry_on_429`, not `_on_transient` — a ReadTimeout on this read is neither retried nor caught. On the live submit path (`service.py:787`, no try/except) it kills the runner. Drain path already guards it; every other broker read was migrated to transient-retry after the 2026-06-17 soak — this read was missed.
- Action: Switch to `call_with_retry_on_transient` (idempotent read); raise a typed error on missing symbol instead of KeyError.
- Effort: tiny | Confidence: 0.8 | Verdict: real

**[observability] Surface `operator_alerts` to the operator (CLI + GUI); currently write-only** (RISK)
- Files: `src/milodex/core/event_store.py:981`, `src/milodex/strategies/runner.py:858,893`, `src/milodex/cli/commands/strategy.py:128`, `docs/assurance/D6_QUEUED_INTENT_EVIDENCE_MATRIX.md:40`
- Problem: `list_operator_alerts` has zero src/ callers. `exit_intent_dropped` / `queued_intent_persist_failed` alerts are written durably but only echoed to the rotating log. The D-6 stranded-exit safety claim ("surfaced, never silently stranded") rests on a surface that does not exist; reconcile incidents, by contrast, do surface (`rich_views.py:1023`).
- Action: Include recent/unresolved `operator_alerts` in `strategy status` data + formatted block (reuse the existing reader); GUI follow-up into `attention_state` rail.
- Effort: small | Confidence: 0.88 | Verdict: real

**[dependency-health] Cap the broker/data heavyweight runtime deps with upper bounds (alpaca-py first)** (RISK)
- Files: `pyproject.toml:13-24`, `src/milodex/broker/alpaca_client.py:15`, `src/milodex/data/alpaca_provider.py:15`, `.github/workflows/ci.yml:32`
- Problem: alpaca-py/pandas/pyarrow/rich/yfinance are floor-only; CI + installer both `pip install -e ".[dev]"` ignoring uv.lock. A future alpaca-py 1.x relocating submodule paths the broker binds to would install silently and break the only path to the broker.
- Action: Add conservative caps (`alpaca-py>=0.35.0,<1`, `pandas>=2.2,<4`, `pyarrow>=15.0,<26`, `rich>=13.7,<16`, `yfinance>=0.2,<2`); bump alpaca-py floor to the exercised 0.43.2. Single pyproject edit + `uv lock`. Bound-tightening only.
- Effort: small | Confidence: 0.82 | Verdict: real

**[build-ci-release] Pin CI dependency install to uv.lock**
- Files: `.github/workflows/ci.yml:29-32`, `pyproject.toml:31-47`, `uv.lock`
- Problem: CI installs bare dev deps (ruff/pytest/pytest-cov/pytest-xdist, no floors) and never references the maintained uv.lock — a new ruff/pytest release can break CI on a no-code-change PR. The lockfile is inert.
- Action: `uv sync --frozen` (via astral-sh/setup-uv) or lock-matched bounds in pyproject; switch the pip cache to uv's.
- Effort: small | Confidence: 0.85 | Verdict: real

**[build-ci-release] Add `ruff format --check` to CI**
- Files: `.github/workflows/ci.yml:34-35`, `README.md:147`
- Problem: CI runs only `ruff check`; README documents both and claims "formatted, and clean", but `ruff format --check` reports 15 files would be reformatted (exit 1). Drift landed because the gate is absent.
- Action: Add `ruff format --check src/ tests/` (one line); reformat the 15 files (paired ticket) so it goes green.
- Effort: tiny | Confidence: 0.97 | Verdict: real

**[developer-experience] Warn against `uv venv` at the install step**
- Files: `uv.lock`, `README.md:89-97`, `CLAUDE.md`, `docs/TROUBLESHOOTING.md:18-64`
- Problem: Tracked uv.lock baits `uv venv`/`uv sync`, which produces the documented trampoline footgun (silent phantom runners). The warning lives only in TROUBLESHOOTING, read only after the dev is burned.
- Action: One-line caution at the README Quick Start venv step and CLAUDE.md Commands. Doc edit.
- Effort: tiny | Confidence: 0.88 | Verdict: real (P1 looks inflated for a solo-repo doc nudge, but the gap is real)

**[documentation] Update OPERATIONS.md daily-execution holding note** — _already-tracked (roadmap drift)_
- Files: `docs/OPERATIONS.md:137`
- Problem: Holding note still calls daily execution a structural contradiction ("cannot submit or fill... see D-1") and cites `evaluator.py:428`; D-1 is decided (ADR 0057, Accepted) and queue-at-open is built on this branch (migrations 016/017, persist+drain in runner). `_check_market_open` is now `evaluator.py:437` (`market_closed` :445).
- Action: Rewrite to reflect queue-at-open per ADR 0057; fix the line citation; don't overclaim fill-rate (fail-closed-drop scope).
- Effort: small | Confidence: 0.9 | Verdict: real

**[documentation] Fix STRATEGY_BANK.md self-contradiction on the same-symbol co-run guard** — _already-tracked (roadmap §301 flags it)_
- Files: `docs/STRATEGY_BANK.md:251,23`
- Problem: Lines 23-38 correctly say the launch guard was removed (2026-06-15) and same-symbol co-run is allowed; line 251 still asserts the canaries are "mutually exclusive at runtime." Code has no active eval-symbol refusal.
- Action: Rewrite the line-251 callout to match; cross-ref the concurrency section.
- Effort: small | Confidence: 0.95 | Verdict: real

### P2

**[test-coverage / ai-evals-prompts] Cover the shared decider feature kit (`_decider_features.py`)** _(merged: spans test-coverage + ai-evals-prompts)_
- Files: `src/milodex/strategies/_decider_features.py`, `tests/milodex/strategies/test_scored_linear_features.py`, `tests/milodex/strategies/test_tree_bucketed_lookup.py`
- Problem: The shared axis-3 feature kit has no direct test (a prior `test_decider_features.py` from commit 5fefdda is gone from disk and index; only a stale orphan .pyc survives). Edge branches are exercised only transitively: `cross_sectional_zscore` zero-std neutral path, `wilder_rsi` avg_loss==0 split (50.0 vs 100.0), `realized_vol`/`trailing_return`/`ma_distance` short-history None guards. The 89% line-coverage ratchet doesn't pin these branches.
- Action: Re-add `tests/milodex/strategies/test_decider_features.py` with hand-computed assertions for each edge branch (recover from `git show 5fefdda:...` if it still applies). Pure-function tests, no deps.
- Effort: small (consolidated) | Confidence: 0.88 | Verdict: null (both source tickets unverified)

**[test-coverage] Test `resolve_position` safety guards (broker-unreachable, no-incident, zero-delta)**
- Files: `src/milodex/operations/reconciliation.py:273-317`, `tests/milodex/cli/test_reconcile.py:401-450`
- Problem: Operator-invoked position-correction path has 7 guard branches; only 2 tested. Untested includes the consequential broker-unreachable guard (prevents writing a delta against a stale snapshot), no-incident-hash, zero-delta, empty-symbol/reason.
- Action: Add CLI tests reusing `_StubBroker`/`_append_local_trade`; assert exit_code 1 + message substring per guard. No source change.
- Effort: tiny | Confidence: 0.83 | Verdict: null

**[refactor] Hoist duplicated `_no_signal` / `_entry_price` / `_exit_decision` helpers into a shared module**
- Files: `src/milodex/strategies/meanrev_rsi2_intraday.py:299,373,380` + 10 sibling strategy files, `_session_intraday.py`, `base.py`
- Problem: `_no_signal` copy-pasted byte-identical into 13 modules, `_entry_price` into 8 — pure boilerplate; any fix must be hand-applied across a dozen files.
- Action: Move `_no_signal`/`_entry_price` to `base.py`; delete per-strategy copies (~20 bodies). Roadmap-sanctioned consolidation (CURRENT_ROADMAP.md:450).
- Effort: small | Confidence: 0.9 | Verdict: null

**[refactor] Delete verbatim `wilder_rsi_series` copy in meanrev_rsi2_intraday; import the canonical `_indicators` one**
- Files: `src/milodex/strategies/meanrev_rsi2_intraday.py:314,344`, `src/milodex/strategies/_indicators.py:30,58`
- Problem: `_wilder_rsi_series` + `_rsi_from` are byte-for-byte the canonical `_indicators` versions (only name/docstring/one astype differ). `meanrev_crypto_rsi2.py` already imports the canonical one; two copies can drift.
- Action: Replace with `from milodex.strategies._indicators import wilder_rsi_series`; ~35 lines deleted. Run the strategy tests.
- Effort: tiny | Confidence: 0.85 | Verdict: null

**[performance] Avoid per-day full-window DataFrame copies in the daily backtest loop**
- Files: `src/milodex/backtesting/engine.py:1654`, `src/milodex/data/models.py:77,86`, `strategies/regime_spy_shy_200dma.py:33`, `strategies/momentum_xsec_rotation.py:188`
- Problem: `_slice_bars_to_day` wraps each iloc view as `BarSet(...)`, whose `__init__` forces `df.copy()` → O(N²) rows copied; `evaluate()` copies again via `to_dataframe()`. Compounds across walk-forward folds.
- Action: **Measure first** with a representative daily backtest. Then add a read-only no-copy BarSet view for the internal slice (keep the public `to_dataframe()` copy contract). Don't touch the intraday path.
- Effort: decent | Confidence: 0.62 | Verdict: null

**[architecture-boundaries] Replace QML per-family stage-routing predicates with a Python-owned bridge-family field**
- Files: `gui/qml/Milodex/components/BenchConfirmationModal.qml:90-102,541-555`, `gui/bench_actions.py:178-258,382-422`
- Problem: QML re-derives the submit bridge socket via inline stage predicates (`=== "paper"`/`"idle"`) that `bench_actions._is_submit_capable_action` already evaluates from `ACTION_KIND_SPECS`. The gate itself is Python-owned (not a safety hole), but routing duplicates the rules — the ADR-0051 "QML grows business rules" drift.
- Action: Stamp the resolved `submitBridgeFamily` onto the Python preview; `_dispatchSubmit` switches on it; delete the QML `_is*Submit` predicates.
- Effort: tiny | Confidence: 0.78 | Verdict: null

**[architecture-boundaries] Route GUI 'current-stage=paper' membership through `EventStore.get_latest_promotion_for_strategy`** — _already-tracked (audit PR K, roadmap §10)_
- Files: `gui/strategy_bank_state.py:143-176`, `gui/_event_queries.py:98`, `gui/query_helpers.py:57`, `gui/active_ops_state.py:120`, `core/event_store.py:1776`
- Problem: `_SQL_PAPER_CURRENT_STAGE` hand-reimplements the latest-promotion ordering the risk layer reads back; a GUI edit can show a different current stage than risk enforces. Three divergent latest-row encodings across 6 GUI files.
- Action: Express the ordering once via the EventStore method; collapse read models to one projection call. Consolidation, not new abstraction.
- Effort: decent | Confidence: 0.82 | Verdict: null

**[observability] Log + record when a daily strategy signal input is non-finite (interior cache-gap NaN)**
- Files: `strategies/regime_spy_shy_200dma.py:60`, `strategies/runner.py:351`, `data/alpaca_provider.py:152`
- Problem: The documented daily-cache interior-gap incident is silent: enough-rows-but-NaN-mean isn't guarded, so a NaN MA compares False → silent risk_off with no diagnostic.
- Action: Non-finite guard at the strategy decision boundary returning a `no_signal` reasoning naming `non_finite_signal` + `logger.warning` once. Stdlib math.
- Effort: small | Confidence: 0.78 | Verdict: null

**[observability] Log broker read failures swallowed by `get_position` / `cancel_order`** (RISK)
- Files: `src/milodex/broker/alpaca_client.py:265,233`
- Problem: `get_position` `except Exception: return None` and `cancel_order` `except APIError: return False` — both swallow silently, erasing the diagnostic trail; `service.py:646` already logs the analogous case.
- Action: `logger.warning(..., exc_info=True)` before each sentinel return; contract unchanged. (Caveat: `get_position` has no prod callers — diagnostic-only.)
- Effort: tiny | Confidence: 0.8 | Verdict: real

**[dependency-health] Remove unused `pytz` from runtime dependencies**
- Files: `pyproject.toml:18`, `strategies/_session_intraday.py:28`, `docs/RESUME_EXTRACT.md:15`
- Problem: `pytz>=2024.1` declared as direct dep; zero `import pytz` anywhere. All tz work uses pandas + stdlib zoneinfo. Stays installed as a yfinance transitive.
- Action: Delete the line, `uv lock`. Correct RESUME_EXTRACT.md if kept.
- Effort: tiny | Confidence: 0.92 | Verdict: null

**[build-ci-release] Reformat the 15 ruff-format-drifted files**
- Files: 7 named under `tests/milodex/**` + 8 unlisted
- Problem: `ruff format --check` reports 15 files; README:62 "formatted, and clean" is currently false.
- Action: `ruff format src tests`, commit. Pair with the CI-gate ticket.
- Effort: tiny | Confidence: 0.95 | Verdict: null

**[build-ci-release] Derive installer version from pyproject** — _already-tracked (in-code TODO)_
- Files: `installer/milodex.iss:21-24`, `pyproject.toml:7`
- Problem: pyproject 0.1.0 vs milodex.iss hardcoded 0.5.0 (feeds AppVersion + output filename); no `__version__`. Per-release manual edits are a footgun.
- Action: Single-source: bump pyproject, have build_installer.ps1 pass `/D MyAppVersion=` from pyproject. Closes the existing TODO.
- Effort: small | Confidence: 0.9 | Verdict: null

**[build-ci-release] Add a job timeout to the CI workflow**
- Files: `.github/workflows/ci.yml:17-19`
- Problem: `test` job has no `timeout-minutes`; a wedged Qt subprocess can hang to GitHub's 360-min ceiling. Suite runs ~1m50s.
- Action: `timeout-minutes: 20` under `jobs.test`. One line.
- Effort: tiny | Confidence: 0.8 | Verdict: null

**[ux-product-polish] Surface read-model errors on FRONT/BENCH/LEDGER the way DESK already does**
- Files: `gui/qml/.../FrontSurface.qml:42`, `LedgerSurface.qml:47`, `BenchSurface.qml:52`, `gui/polling_lifecycle.py:185`, `DeskSurface.qml:430`
- Problem: All three are PollingReadModels exposing `dataStatus`/`dataErrorMessage`, but the three surfaces never bind them — a thrown builder (DB locked, disk full) shows stale data with no indication. Contradicts ADR 0038 truthful-observability.
- Action: Bind a status component to their `dataStatus`/`dataErrorMessage`. **Caveat (per verdict): `SectionStatus` is an inline `component` in DeskSurface, not a shared file — extract to `components/SectionStatus.qml` first.**
- Effort: small | Confidence: 0.9 | Verdict: real

**[ux-product-polish] Add an honest empty state to LedgerSurface when entries is empty** — _already-tracked (ADR 0038:72)_
- Files: `gui/qml/.../LedgerSurface.qml:260`, `gui/ledger_state.py:102`
- Problem: No empty-state fallback; fresh DB or empty filter renders a blank gap — can't tell "nothing yet" from "filter excluded all" from "load failed." Both sibling surfaces handle it.
- Action: Filter-aware empty copy mirroring `ActivityTable.qml:146`. One small QML block.
- Effort: tiny | Confidence: 0.95 | Verdict: null

**[ux-product-polish] Fix LedgerSurface footer that advertises filters the UI does not expose**
- Files: `gui/qml/.../LedgerSurface.qml:403,208`, `gui/ledger_state.py:63`
- Problem: Footer promises "stage, strategy, outcome, date range" but only group + stage chips exist; strategy/outcome/time filters are read-only and permanently "all" — reads as broken/unfinished.
- Action: Rewrite footer to describe only the filters that exist (prefer this) or wire the missing chips.
- Effort: tiny | Confidence: 0.85 | Verdict: null

**[data-quality-schema] Map all Alpaca order statuses explicitly; fail loud on unknown instead of coercing to PENDING** (RISK)
- Files: `broker/alpaca_client.py:61,116`, `operations/reconciliation.py:1123`, `broker/models.py:43`
- Problem: `_STATUS_MAP` covers 9 statuses; `.get(..., PENDING)` silently maps terminal `done_for_day`/`replaced`/etc. to PENDING, which the risk caps treat as in-flight → over-counts toward position caps. (Caveat: reconciliation queries `status="open"`, so the reconciliation-divergence impact is overstated; the risk-cap misclassification is the genuine bug.)
- Action: Add missing statuses with correct terminal/open classification; change `.get` default to a WARN on fallthrough (keep conservative PENDING fallback); add a coverage test.
- Effort: small | Confidence: 0.85 | Verdict: real

**[data-quality-schema] Drop the 100_000.0 `initial_equity` default in `metrics_for_run`**
- Files: `src/milodex/analytics/metrics.py:250`
- Problem: Missing `initial_equity` silently substitutes $100k — wildly off-scale for sub-$1k Phase 1 capital; distorts total_return/cagr/final_equity in the trust report. (Gate path unaffected — Sharpe/DD are scale-invariant.)
- Action: Raise a clear ValueError at the read boundary, or derive from the equity curve's first value and tag as imputed. No fixed dollar base. One line + test.
- Effort: tiny | Confidence: 0.7 | Verdict: null

**[architecture-boundaries] Map `evaluate_research_target.failures` to S/D/N codes at a thin GUI seam** — _already-tracked (audit #13, roadmap §10)_
- Files: `gui/strategy_bank_state.py:220-244`, `gui/attention_state.py:273`, `gui/snapshot_builders.py:224`, `gui/bench_actions.py:103`, `promotion/policy.py:95-135`
- Problem: `_compute_gate_failures` hand-writes the three gate comparison operators that `PromotionPolicy.evaluate_research_target` owns; values propagate via aliases (not stale-number), but the operators/None-handling live in two places.
- Action: Add a read-only adapter mapping `.failures` to ADR-0009 S/D/N display codes at one GUI seam. Keep S/D/N at the GUI, not in policy.
- Effort: small | Confidence: 0.7 | Verdict: null

**[documentation] Add ADR 0057 to the ADR README index table**
- Files: `docs/adr/README.md:87`, `docs/adr/0057-daily-execution-queue-at-open.md`
- Problem: Index table ends at 0056; ADR 0057 (the keystone M1 decision, Accepted) is absent.
- Action: Append one index row. Additive doc edit.
- Effort: tiny | Confidence: 0.97 | Verdict: null

**[documentation] Resolve ADR 0055 internal contradiction: body still says eval-symbol guard 'is now code-enforced'**
- Files: `docs/adr/0055-...md:52,19`
- Problem: The 2026-06-15 amendment records the launch-time refusal was removed, but body line 52 still says "is now code-enforced." Highest-authority doc drift (internal contradiction in an Accepted ADR).
- Action: Edit line 52 to past tense + point to the amendment / ADR 0026 addendum. Keep the original per ADR convention (qualify, don't delete).
- Effort: tiny | Confidence: 0.88 | Verdict: null

**[documentation] Reconcile CURRENT_ROADMAP §2 banner — D-1 is decided (ADR 0057)** — _already-tracked (roadmap is gate-only)_
- Files: `docs/CURRENT_ROADMAP.md:90,490,506`
- Problem: §2 pending-decisions, §7 paused, and §8 decision-map still present D-1 as open/future; ADR 0057 records the binding Option-A choice and it's building on this branch.
- Action: At the next gate update, move D-1 to "decided → ADR 0057" and soften the §2 "no queue path" blocker text. Flag so it isn't missed at M1 gate close.
- Effort: small | Confidence: 0.78 | Verdict: null

### P3

**[cleanup-dead-code] Untrack and gitignore the Ralph-loop scratchpad**
- Files: `.cursor/ralph/scratchpad.md`, `.gitignore`
- Problem: Completed Ralph-loop artifact sits as permanent `??` clutter; `.cursor/` is not gitignored — one stray `git add .` from being committed.
- Action: Delete the stale scratchpad; add `.cursor/` to `.gitignore`.
- Effort: tiny | Confidence: 0.85 | Verdict: null

**[cleanup-dead-code] Decide fate of untracked `docs/RESUME_EXTRACT.md`**
- Files: `docs/RESUME_EXTRACT.md`, `.gitignore`
- Problem: 98-line personal resume extract with self-acknowledged approximate stats living under tracked `docs/`. Committing it as-is would read as project doc.
- Action: Operator decision — move out of repo or gitignore; don't commit into `docs/` as-is.
- Effort: tiny | Confidence: 0.7 | Verdict: null

**[cleanup-dead-code] Collapse the redundant `resolve_strategy_config` re-export chain in CLI**
- Files: `cli/main.py:281`, `cli/commands/promote.py:56`
- Problem: 4-link wrapper chain (`main._resolve_strategy_config` → `promote.resolve_strategy_config` → loader) all reaching the same canonical resolver with identical signature; main.py:206 is the sole caller of the main shim.
- Action: Call canonical `loader.resolve_config_path` directly; delete the main.py shim. Keep `promote.resolve_strategy_config` only if a test import still needs it.
- Effort: tiny | Confidence: 0.8 | Verdict: null

**[test-coverage] Cover remaining `_fresh_pricing_bar` fail-closed sub-branches** (RISK)
- Files: `strategies/runner.py:1159-1195`, `tests/milodex/strategies/test_runner_drain_fresh_price.py`
- Problem: The not-current-session (`:1188`) and close<=0/NaN (`:1193`) branches are untested; existing fail-closed tests use equal-timestamp bars that hit `:1190` instead. EXIT-safety sensitive.
- Action: Add a prior-session-dated-bar case and a close=0.0/NaN case asserting ENTRY stays queued / EXIT alerts+obsoletes. No source change.
- Effort: tiny | Confidence: 0.78 | Verdict: real

**[refactor] Remove redundant bound re-checks in strategy `_validated_parameters`; keep only coercion/extraction**
- Files: `strategies/meanrev_rsi2_intraday.py:241`, `momentum_vwap_trend_intraday.py:283`, `loader.py:406`, `base.py:17`
- Problem: Every numeric bound is declared twice — `parameter_specs` + loader enforcement, then re-hardcoded as manual if-checks in ~22 strategies' `_validated_parameters`. Source-of-truth-in-two-places.
- Action: Drop the if/raise bound arms (loader guarantees them for config-loaded strategies); keep coercion + missing-key guard. Multi-PR by family; verify each strategy's tests (some may pin the strategy-level check).
- Effort: decent | Confidence: 0.7 | Verdict: null

**[performance] Cache the SPY benchmark parquet read in PerformanceState**
- Files: `gui/performance_state.py:278,295`, `data/cache.py:98`
- Problem: 30s poll re-reads the entire SPY parquet + re-runs column-wide `to_datetime` + per-slice masks; series changes at most once/day. Pure waste, correctness fine.
- Action: Memoize the parsed SPY frame keyed by parquet mtime/size; reuse the parsed index across the 4 slice masks. Stdlib functools.
- Effort: tiny | Confidence: 0.7 | Verdict: null

**[security-privacy] Harden permissions (0o600) on the SQLite event store and runner log files**
- Files: `config.py:151-198`, `_logging.py:64-69`, `paper_runner_control.py:252-255`
- Problem: DB + logs created with default mode; `advisory_lock.py:164` already sets 0o600 (the pattern). Defense-in-depth nit; NTFS ACLs make Windows exposure low.
- Action: `os.chmod` 0o600 guarded by `os.name` (no-op on Windows). Reuse the existing pattern, no new abstraction. Or close won't-fix with recorded rationale.
- Effort: small | Confidence: 0.5 | Verdict: null

**[security-privacy] Document the .env handling contract in .env.example/README**
- Files: `.env.example`, `config.py:28-31`
- Problem: Mechanics sound (gitignored, history clean); only gap is .env.example not stating the never-commit + user-readable-only contract.
- Action: 2-line comment in .env.example. Skip if judged redundant.
- Effort: tiny | Confidence: 0.45 | Verdict: null

**[observability] Give CLI-launched runners a per-strategy log filename**
- Files: `_logging.py:55`, `cli/main.py:151`, `paper_runner_control.py:236`
- Problem: GUI launch names logs `runner.<sid>.<ts>.log`; CLI launch keys only by PID (`milodex-<pid>.log`) — CLAUDE.md's diagnostic note points at a per-runner log that only exists for GUI launches.
- Action: Include `strategy_id` in the installed filename for `strategy run <id>` invocations. Stdlib only.
- Effort: tiny | Confidence: 0.7 | Verdict: null

**[dependency-health] Raise the pandas floor to match the tested major (>=2.2)**
- Files: `pyproject.toml:14`
- Problem: `pandas>=2.0` permits a fresh install to resolve old 2.x with different CoW/chained-assignment semantics never tested; code is locked against 3.0.2.
- Action: `pandas>=2.2` (or `>=3.0`). One line + `uv lock`. Fold into the upper-bound ticket.
- Effort: tiny | Confidence: 0.7 | Verdict: null

**[build-ci-release] Reconcile README '90%+ coverage' claim with `fail_under=89`**
- Files: `README.md:61`, `pyproject.toml:89`
- Problem: README claims "90%+"; enforced floor is 89.
- Action: Change README to "89% coverage floor" (or raise fail_under to 90 only if coverage sustains it per the ratchet rule). Cheapest fix is the doc edit.
- Effort: tiny | Confidence: 0.85 | Verdict: null

**[developer-experience] Add a seed-data / first-run step to the README Quick Start**
- Files: `README.md:87-148`, `docs/OPERATIONS.md:305`, `.gitignore:47-60`
- Problem: Fresh clone ships no market_cache or event store; Quick Start jumps straight to a 9-year backtest with no warmup step. The `fetch-universe` path exists but README never points to it.
- Action: Insert one `milodex data fetch-universe ...` sub-step (or a pointer to OPERATIONS.md) before the backtest examples. README edit.
- Effort: tiny | Confidence: 0.78 | Verdict: null

**[developer-experience] Refresh stale 'By the Numbers' / module-count / ADR-count figures in README**
- Files: `README.md:25,53,59-62`
- Problem: README says "Thirteen modules" (actual 14; list omits operations/, runner/), "56 ADRs / 0001-0056" (actual 57 through 0057), "90%+ coverage" (floor 89).
- Action: Update module count + list, ADR count to 57 / 0001-0057, soften coverage claim. Doc edit.
- Effort: tiny | Confidence: 0.92 | Verdict: null

**[data-quality-schema] Default unknown Alpaca order_type to a logged/UNKNOWN value, not silently to MARKET** (RISK)
- Files: `broker/alpaca_client.py:113,73`
- Problem: `.get(order_type_str, OrderType.MARKET)` silently relabels e.g. trailing_stop as MARKET. Near-nil impact today (system submits only MARKET; coercion only bites on externally-placed orders read back). OrderType enum has no UNKNOWN member.
- Action: Fold into the status-map ticket — WARN on fallthrough, keep MARKET fallback.
- Effort: tiny | Confidence: 0.6 | Verdict: real (observability-only)

**[data-quality-schema] Validate `experiment_registry` terminal_status/stage_reached against documented enums at the write boundary**
- Files: `core/event_store.py:2092,2173`, `core/migrations/015_experiment_registry.sql:37`
- Problem: Free TEXT with a documented enum but no validation; a typo'd `terminal_status` silently never matches the exact-string filter → invisible experiment. ADR 0017 bounds blast radius (IEX research is non-durable) → P3.
- Action: Python-side enum check in append/update raising on out-of-set values (on-pattern per migration 008's code-path-over-CHECK stance) + test.
- Effort: tiny | Confidence: 0.55 | Verdict: null

**[ux-product-polish] Remove the dead 'strategy detail' link from FrontSurface**
- Files: `gui/qml/.../FrontSurface.qml:710`
- Problem: Permanently-disabled "strategy detail →" link next to a working link; the promised strategy-detail surface doesn't exist and isn't on the roadmap. Dead affordance.
- Action: Delete the disabled Text block; re-add when the surface ships.
- Effort: tiny | Confidence: 0.8 | Verdict: null

**[ux-product-polish] No keyboard navigation or accessibility on any GUI control (mouse-only)**
- Files: `gui/qml/.../Main.qml:249`, `SegmentedToggle.qml:48`, `Button.qml`, `StatusPill.qml`
- Problem: Every control is a bare MouseArea — no `activeFocusOnTab`/`Accessible`/`Keys` handlers. ADR 0045:35 already flags this.
- Action: Add Accessible role/name + focus handling to shared primitives (multi-PR). **Honest sizing: real but marginal-value for a single-operator Windows tool with no compliance driver — file, don't prioritize over error/empty-state work.**
- Effort: large | Confidence: 0.85 | Verdict: null

**[ai-evals-prompts] LLM/non-rule decision-layer eval track (axis-3) is unscoped** — _already-tracked (roadmap §537)_
- Files: `docs/architecture/2026-05-30-harness-capability-axes.md:123`, `docs/CURRENT_ROADMAP.md:537`
- Problem: No LLM runtime target exists (zero LLM deps/imports); the two deciders are deterministic. Eval harness / shadow-mode / cost metering are deliberately unscoped. Filing implementation now would be premature scaffolding (PONYTAIL).
- Action: **No code action.** Logged so the audit trail records this is deferred, not overlooked; design the forward-only shadow evidence track + frozen eval set + cost metering before any API wiring.
- Effort: large | Confidence: 0.9 | Verdict: null

## Excluded — False Positives & Already-Tracked

**False positives (verdict=false_positive, dropped by adversarial verify):**
- **Eliminate redundant per-strategy ledger fetches / per-symbol attribution connections** (performance) — Queries exist as described, but ADR 0029 deliberately left query-shape to profiling-gated choice and made the per-call fetch a self-contained seam; ticket concedes the queries are "fast"/"gratuitous" (unmeasured micro-opt), pattern-3 `count_positions_by_strategy` has zero prod callers (test-only), and it's a no-behavior-change refactor of risk-cap-feeding code.
- **Shield third-party HTTP loggers so DEBUG can't write the Alpaca secret to logs** (security-privacy) — Mechanism fails: alpaca uses `requests.Session`; urllib3's DEBUG connectionpool log emits only method/url/status, not headers. Header dumping is `http.client.debuglevel` via `print()`, not the logging framework, and is never enabled in src/. The P1 leak is not real; the fix adds code against a non-existent path.
- **Harden `_translate_position`/`_translate_order` against missing Alpaca numeric fields** (reliability-errors) — Core claim wrong: `runner.py:1338 _current_positions()` reads the strategy-scoped event ledger (ADR 0055), not `broker.get_positions()`, so the named "unguarded runner path" doesn't exist. The one genuine live caller (`service.py:928`) feeds account-level caps and fails closed (blocks submit — safe). Position model declares all 5 fields required-float, so None would be a contract violation; the "Alpaca returns None" premise is uncited. Speculative.
- **Fail loud when broker daily P&L base equity is missing** (data-quality-schema) — Misreads getattr semantics: `equity_previous_close` is not a TradeAccount field (getattr always None), and `last_equity` is a declared `Optional[str]=None`, so the `equity` default never applies — `float(None)` raises TypeError, propagating unhandled. The claimed silent collapse to `daily_pnl=0.0` cannot occur; a degraded response already fails loud, the direction requested.

**Already-tracked (kept in backlog, flagged):**
- Route GUI current-stage through `get_latest_promotion_for_strategy` — audit PR K / roadmap §10.
- Map `evaluate_research_target.failures` to S/D/N at a GUI seam — audit #13 / roadmap §10.
- Add an honest empty state to LedgerSurface — ADR 0038:72.
- Derive installer version from pyproject — in-code TODO at milodex.iss:21.
- Update OPERATIONS.md daily-execution note — roadmap drift.
- Fix STRATEGY_BANK.md co-run contradiction — roadmap §301.
- Reconcile CURRENT_ROADMAP §2 D-1 banner — roadmap is gate-only.
- LLM decision-layer eval track — roadmap §537 (no-action log).

## Counts

| Vector | Ticket count |
|---|---|
| cleanup-dead-code | 3 |
| test-coverage | 3 (one merged with ai-evals-prompts) |
| refactor | 3 |
| performance | 3 (1 excluded FP) |
| security-privacy | 2 (1 excluded FP) |
| reliability-errors | 2 (1 excluded FP) |
| observability | 4 |
| dependency-health | 3 |
| build-ci-release | 6 |
| developer-experience | 3 |
| architecture-boundaries | 3 |
| ux-product-polish | 5 |
| data-quality-schema | 4 (1 excluded FP) |
| ai-evals-prompts | 2 (1 merged into test-coverage) |
| documentation | 5 |
| **Total real tickets** | **46** (51 input − 4 FP − 1 merged) |

**Priority histogram:** P0 = 0 · P1 = 11 · P2 = 18 · P3 = 17

**11 RISK-tagged tickets** (extra scrutiny before action): submit_order tests, WAL-per-connect, intraday submit loop, get_latest_bar, operator_alerts surfacing, alpaca-py caps, get_position/cancel_order logging, Alpaca status map, _fresh_pricing_bar branches, order_type default.
