# Phase 1 Paper Workflow

This is the normal safe operator path for Bench in Phase 1:

`backtest evidence -> promote to paper -> start paper runner -> controlled stop -> inspect paper evidence -> demote/walk back`

Phase 1 remains paper-only. Micro-live and live controls stay locked, and
future ML/LLM trader contracts are deferred until their decision mechanics and
audit artifacts are defined.

## Where to work

Use the Bench surface for lifecycle operations. Each row's action menu opens a
confirmation modal before a submit-capable action crosses into Python.

Submit-capable action families:

- `backtest`: run canonical walk-forward backtest evidence.
- `promote_to_paper`: advance a backtest-stage strategy to paper when evidence
  and required operator text pass governance checks.
- `start_paper_runner`: launch a non-blocking paper runner for a paper-stage
  strategy.
- `stop_paper_runner`: request a controlled stop for an active paper runner.
- `demote`: walk a strategy back to `backtest`.
- `freeze_manifest`: record the frozen manifest for paper-plus stages.

Read-only actions, such as Open Evidence, never mutate state.

## Backtest Evidence

In Bench, use Initiate Backtest or Refresh Backtest. The GUI uses the canonical
Phase 1 evidence shape:

- start: `2020-01-01`
- end: `2024-12-31`
- walk-forward: `true`
- initial equity: `$1,000`
- risk policy: `bypass`

Expected evidence:

- a completed `backtest_runs` row;
- run metadata including risk policy, data quality, skipped counts, and run
  manifest;
- Bench evidence packet referencing the latest run id.

If the backtest fails, treat the structured blocker as authoritative. Do not
promote from stale or missing evidence.

## Promote To Paper

Use Promote to Paper from a backtest-stage row. The confirmation modal requires
operator-authored evidence:

- a recommendation;
- at least one known risk;
- a backtest run id unless the strategy is explicitly lifecycle-exempt.

Expected evidence:

- a manifest event and promotion event in the event store;
- YAML stage updated to `paper`;
- Bench row moves to paper with the evidence run id and promotion type.

Promotion to paper means the strategy is credible enough for live-feed testing
without real capital. It is not approval for capital-bearing execution.

## Start Trading

Use Start Trading only on a paper-stage strategy. Bench launches:

```powershell
python -m milodex.cli.main strategy run <strategy_id>
```

through the paper-runner control boundary. The GUI does not run the infinite
strategy loop in-process.

Start is blocked unless:

- `TRADING_MODE=paper`;
- the strategy YAML stage is `paper`;
- the per-strategy advisory lock is free.

Expected evidence:

- an active `strategy_runs` row with `ended_at=NULL`;
- Bench session state changes to active/running;
- the GUI remains responsive.

## Stop Trading

Use Stop Trading to request a controlled paper-runner stop. This writes a
controlled-stop request file under the Milodex locks directory. The runner
checks that file between cycles and shuts down with:

```text
exit_reason = "controlled_stop"
```

Stop Trading is not the kill switch. It does not trip account-wide emergency
halt state and does not replace manual kill-switch reset rules.

Expected evidence:

- controlled-stop request is consumed;
- `strategy_runs.ended_at` is populated;
- `strategy_runs.exit_reason="controlled_stop"`;
- Bench paper evidence shows completed status, timestamps, exit reason, and
  paper trade count.

## Common Blockers

`wrong_source_stage`: the action is being submitted from the wrong lifecycle
stage. Refresh Bench and use the action that matches the current row.

`missing_recommendation` or `missing_known_risks`: promotion requires operator
evidence text.

`gate_check_failed`: the backtest evidence did not pass the promotion gate.
Do not override this for ordinary strategies.

`advisory_lock_held`: a runner for this strategy is already active. Inspect
Bench paper evidence or the lock holder before trying again.

`no_active_runner`: Stop Trading was requested for a strategy without an active
paper runner.

`unknown_proposal_id`: the modal tried to submit a consumed or stale proposal.
Close the modal, reopen the action, and submit the fresh proposal.

`event_store_unavailable`: the GUI command facade cannot access the event
store. Stop and fix the local data path before retrying.

## Recovery

If a lifecycle action succeeds but Bench looks stale, refresh the surface or
restart the GUI. The event store is the source of truth.

If a paper runner crashes, Bench should show interrupted or warning paper
evidence rather than a clean controlled stop. Review logs and demote the
strategy if the failure affects trust.

If a strategy should leave paper, use Demote to Backtest with a concrete
reason. Demotion is a governance action, not an implicit side effect of stop.
