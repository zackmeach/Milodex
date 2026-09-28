# M4 F2 current-code rehearsal — 2026-09-28

**Status: PARTIAL.** This is source-build evidence on the code in PR #388
(`5f483d7`, squash-merged as `488764c`). It does not close F2 or M4.

## Boundary and preflight

- `TRADING_MODE=paper` was asserted before each command. No live-capital path was used.
- Runtime state and cache were isolated under
  `%TEMP%\milodex-m4-f2-ledger-20260928`; the source strategy YAML was in the
  managed M4 worktree. Credentials were loaded in process from the local `.env`
  and were not copied into the worktree.
- A blank scratch ledger correctly detected the paper broker's six existing
  positions as drift and blocked exposure-increasing starts. A consistent
  SQLite backup of the existing ledger was used instead. Before start, its
  reconciliation was `CLEAN`, broker open orders were zero, and the SPY/SHY
  strategy had zero queued intents. The original checkout's runner roster had
  no running strategy.

## Observed lifecycle

1. Fetched SPY and SHY daily bars into the isolated cache. The SPY fetch
   reported a start-date warning: first available 2018-11-01 for the requested
   2018-01-01 start.
2. Ran the SPY/SHY walk-forward backtest for 2020-01-01 through 2026-09-25.
   Run `1897a375-6c7c-42ee-9253-31efb8103a05` completed with 17 OOS trades,
   775 OOS trading days, and a manifest naming code commit `5f483d7`. Data
   quality was `pass_with_warnings`: the effective first bar was 2020-07-27
   for both symbols, so the result covers a shorter window than requested.
3. Ran the existing synthetic fault check. Explanation `2128518` recorded a
   veto for an oversized order. Demoted the strategy in the isolated ledger
   from paper to backtest, then promoted it to paper using that run and the
   policy-scoped lifecycle exemption. Promotion event `33` recorded all three
   enforced criteria as satisfied: fresh completed backtest; 783 explanation
   rows covering 17 simulated signals; and the synthetic risk veto. The
   promotion froze manifest `29` and returned the worktree YAML to paper.
4. Started the supervised source runner. Session
   `ae59489a-8034-4504-996d-9b7041569077` was reported running with an
   on-schedule heartbeat, then consumed a controlled-stop request and ended
   with `exit_reason='controlled_stop'`. Post-stop paper reconciliation was
   `CLEAN` with zero broker open orders. The session had zero rows in both
   `execution_attempts` and `trades`; the daily strategy idled during market
   hours without submitting a paper order.

The one session explanation was `no_action` for a queued-intent expiry sweep.
It is **not** evidence of a current strategy signal, risk decision on that
signal, paper submit, or fill. The historical F2 checklist's instruction to
freeze while at `backtest` is invalid on current code: that command refused
as designed; promotion itself performed the manifest freeze.

The sweep also expired three copied-ledger EXIT intents and emitted
`exit_intent_dropped` alerts 68–70 (DIA, QQQ twice) for other strategies. A
broker-sourced snapshot during the rehearsal still showed DIA 18 and QQQ 26
shares held. `CLEAN` reconciliation means account and ledger positions agree;
it does not adjudicate those strategy exits. A read-only check of the original
ledger found the three July 27 exits still `queued` despite their August 3
expiry. Review current paper positions and strategy intent before any operator
action. For these three exits, the sweep changed only their statuses in the
copied ledger; it placed no corrective orders.

## Remaining F2 and M4 proof

- Supervise a current after-close SPY/SHY evaluation and inspect its
  session-keyed strategy explanation. A paper submit and fill remain release
  acceptance work for the installed artifact.
- Founder GUI walks in `docs/drills/2026-07-24-founder-gui-walk-script.md` and
  the founder's autonomy-boundary review/signature remain open. Update the
  canonical roadmap only at the gate.

## M5/M6 handoff observations

- `PaperRunnerControl.start` returned Windows PID `58260`, while the live
  advisory lock and `strategy status` identified runner PID `50496`. Reopen
  recovery must identify the lock holder and session, not trust the initial
  spawn PID alone.
- Reconciliation called four historical orders `local_only` while returning
  `CLEAN` because the broker had zero open orders. Inspect and explain those
  rows before the final M6 broker/event-store closure.
- The copied ledger's latest risk-profile activation audit said `aggressive`,
  while the scratch risk-profile file was absent and runtime fell back to
  `conservative`; reconciliation treated this as informational. The
  installed-state work must not inherit that local configuration mismatch as
  promotion authority.
