# Requirements Traceability — Open Questions for the Operator

> Created 2026-06-21 from the batch-1 traceability backfill + independent review.
> Companion to [`REQUIREMENTS_COVERAGE.md`](REQUIREMENTS_COVERAGE.md) and [`SRS.md`](SRS.md).
> Corrections committed in `39cda6b`. **Nothing here blocks the merge** — these are
> decisions to make when you have the appetite. Answer inline (`Answer:` lines) and
> they become actionable.

## Decisions that need your call

### Q1 — R-EXE-009: duplicate-order dedup key is narrower than the SRS
- **Impl today:** `_check_duplicate_order` keys on `(strategy_instance, symbol, side, window)`.
- **SRS wants:** `(strategy_instance, symbol, side, action_type, target_quantity_or_exposure, window)`
  **plus** block-on-uncertainty (if duplicate status can't be determined, block & require review).
- **Options:** (a) implement the missing key fields + block-on-uncertainty; (b) amend the SRS to
  match the current narrower key and document why action_type/quantity aren't needed in Phase 1.
- **Lean:** (a) is the safer trade-integrity choice, but it's real risk-layer work. Not urgent in the
  paper regime.
- **Answer:**

### Q2 — R-BRK-009: client_order_id is not the deterministic format the SRS specifies
- **Impl today:** bare `uuid.uuid4()` at `execution/service.py:336`.
- **SRS wants:** `{strategy_name}-{YYYYMMDD}-{uuid4[:8]}` so crash-recovery reconciliation can match a
  submission to its broker-side state by ID.
- **Options:** (a) implement the format (has real value: ID-based reconciliation after a crash);
  (b) amend the SRS to accept an opaque UUID and rely on event-store correlation instead.
- **Lean:** (a) — the reconciliation benefit is concrete.
- **Answer:**

### Q3 — R-DAT-011: sub-98% coverage *warns* but does not *exclude* (possible impl gap)
- **Impl today:** a symbol below 98% requested-window coverage produces a `pass_with_warnings`
  warning and the symbol stays in the run.
- **SRS wants:** the symbol **excluded with a manifest entry**, or the **entire run refused**.
- **This may be a real gap, not just a test gap.** Needs a look at the backtest eligibility gate to
  see whether exclusion happens downstream of the warning.
- **Options:** (a) make the eligibility gate exclude/refuse per spec; (b) if warn-only is the
  intended Phase-1 behavior, amend the SRS.
- **Lean:** verify the gate first, then decide.
- **Answer:**

### Q4 — R-BRK-002 / R-DAT-002: integration-test ACs satisfied by mocked unit tests
- **Situation:** both ACs say "integration test against the Alpaca paper API." We satisfy them with
  mocked unit tests that exercise the impl's translation logic (Alpaca object → domain type). These
  are currently counted as covered.
- **Options:** (a) accept the mocked proxy as coverage (status quo); (b) reclassify as
  integration-only — i.e. uncovered until a real paper-API integration test exists.
- **Lean:** (a) until an integration harness exists; the impl logic *is* exercised.
- **Answer:**

## Cleanup / scheduling (lower stakes)

- **Stale branch `chore/reqs-traceability-batch1`** points at an old commit (`51e470f`, the batch-1
  DAT commit) and is checked out in the main working tree. Delete or repoint it once the main tree
  is free. The real batch-1 work is on `reqs-coverage-backfill-batch1` (merged to master).
- **Batch 2.** After this batch, 95 of 138 requirements remain orphaned (most in the untouched
  domains: strategy engine, analytics, promotion, CLI, cross-cutting). Same link-first method.
  Schedule when ready.

## Reference — full deferred inventory (20 across the 3 batch-1 domains)

Genuinely deferred because the capability or integration is unimplemented (no decision needed now —
these are future implementation work; the listed Q-items above are the ones that need a *decision*):

- **R-BRK:** 004 (5s empty-orders postcondition — integration), 005 (clock tuple — impl drift, see
  note), 008 (boundary share-only refusal), **009 (Q2)**.
- **R-DAT:** 004 (sidecar metadata model), 007 (three-named-roles surface, ADR 0017), 008, 009, 010,
  **011 (Q3)**, 013 (run-manifest snapshot versioning), 014, 015.
- **R-EXE:** **009 (Q1)**, 010 (per-trigger-class coverage), 012 (operator-path cancel — verified a
  test gap only; runner cancels at `runner.py:412` then activates, so the safety invariant holds),
  014, 015 (incident-record fields b/c/d/e), 017, 018.
