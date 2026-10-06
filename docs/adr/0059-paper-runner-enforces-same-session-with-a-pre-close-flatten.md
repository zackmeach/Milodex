# ADR 0059 — The paper runner enforces `same_session` with a pre-close flatten

**Status:** Accepted
**Date:** 2026-10-06
**Related:** [issue #396](https://github.com/zackmeach/Milodex/issues/396) (C1, PR 2), [2026-10-05 architecture-deepening audit](../reviews/2026-10-05-architecture-deepening-audit.md) (V5, V9, V10, C3, C5), [ADR 0008](0008-risk-layer-veto-architecture.md) (risk-layer veto), [ADR 0055](0055-event-store-per-strategy-position-ledger.md) (strategy ledger), [`src/milodex/data/sessions.py`](../../src/milodex/data/sessions.py), [`src/milodex/strategies/runner.py`](../../src/milodex/strategies/runner.py)

## Context

`tempo.position_lifecycle: same_session` promises that a position never outlives its session. Only the backtest engine kept that promise: it masks bars to regular hours and force-flattens open positions at the day's last regular-hours close (`backtest.intraday_session_end_flatten`). The paper runner relied on each strategy's own 15:55 time-stop bar. That bar completes at 16:00, so its exit was decided after the bell and vetoed `market_closed`; positions carried overnight (audit V5). The runner also evaluated pre- and post-market bars the backtest never sees.

## Decision

The paper runner enforces the lifecycle the strategy was promoted under. This is system enforcement: the strategy declares the lifecycle in its config, and the runner and the risk layer enforce it. The strategy has no say in when, or whether, the flatten happens.

1. **Deadline.** For each ET session the flatten deadline is `min(table close, broker next_close) − 5 min`.
   - The table is the committed XNYS calendar (`SessionCalendar.xnys()`).
   - `next_close` is a new `BrokerClient.next_close()` read. Alpaca reads its clock; other brokers return `None`, and the table alone decides. A failed read is logged and treated as `None`: the table owns scheduled closes, and `is_market_open()` already covers unscheduled ones.
   - The earlier close wins, so an unscheduled early close is honoured. On a half-day the deadline is 12:55 ET.
2. **Flatten.** On every cycle where the broker reports the market open, and before any fetch, each open lot in the strategy's ledger is sold whole if it is due. A lot is due once now reaches the deadline, or when it is **overdue**, i.e. opened in an earlier ET session. The sale is a market DAY SELL submitted through `ExecutionService.submit_paper` with rule `paper.session_end_flatten`. It gets the full risk evaluation: no exemption, no override and no idempotency key.
3. **Evaluation gate.** A `same_session` cycle fetches and evaluates only while the market is open, now is in `[session open, deadline)`, and the strategy is not delivery-frozen (rule 5). Evaluation sees only completed regular-hours bars (`SessionPolicy.visible`). It never evaluates a bar from a previous session: in the first bar-width after the open, the newest visible bar is yesterday's last one, which paper never evaluated.
4. **Outcomes.** The ledger closes a lot only when a SELL is submitted.
   - **Not sent** — a risk veto, or a raise before any broker call (no attempt row). The lot stays open and is retried:
     - inside the deadline window `[deadline, close)`, on every cycle (at most about 30 polls for a 5Min tempo), since a transient veto can clear there;
     - for an `overdue` or `calendar_fail_closed` lot, at most once per bar (at most about 78 attempts per 5Min session). A persistent veto would otherwise write a blocked explanation and a blocked trade row on every ~10 s poll, all session.

     Each lot that is not sent raises one `session_end_flatten_blocked` alert (severity warning) per ET session, deduplicated in memory.
   - **Anything else** — REJECTED, or a raise once an attempt row exists. The durable record is checked at once, and the delivery freeze (rule 5) applies in the same cycle.
   - Nothing is submitted while the broker reports the market closed.
   - A lot still open after the close is overdue, and its flatten is attempted at the next open, before evaluation.
5. **Delivery freeze.** Before the flatten pass on every open-market cycle, each open lot is checked against `execution_attempts` (`EventStore.unresolved_sell_attempt_ids`).
   - **What freezes.** A SELL attempt by this strategy on the lot's symbol, created since the lot opened (less a five-minute margin for a backward host-clock step; widening the window can only freeze more), whose status is one of:
     - `pending`, `error` or `rejected`;
     - `submitted`, with no submitted paper `trades` row carrying its `broker_order_id`.

     Any of them may already have sold the lot, whichever of the flatten or the strategy sent it. Alpaca reports a 5xx or 504 as a rejection even when the order landed (audit V9). The duplicate-order veto does not cover these attempts: it counts an error attempt only for 60 s, and a rejected one never.
   - **What does not freeze.** A risk veto writes no attempt row. A submitted SELL with its trade row, such as a partial exit, is definitive.
   - **Effect.** That lot is not flattened, and the cycle neither fetches nor evaluates, so the strategy cannot send its own SELL either. Lots in other symbols still flatten. If the check itself fails, the whole cycle is frozen: no flatten and no evaluation, and one `session_delivery_check_failed` alert (severity error) is written per ET day.
   - **Alert.** One `session_end_flatten_delivery_unknown` alert is written per frozen lot per ET session. It lists the attempts' `client_order_id`s. Its severity is error, because a frozen strategy has no automated exits, stop-loss included.
   - **Resolution is manual today.** Check each broker order by `client_order_id`, then record the truth in the strategy ledger:
     - a filled SELL becomes its submitted paper `trades` row, with the attempt `submitted` under that `broker_order_id`;
     - an order that never reached the broker has its attempt status set to `resolved_not_sent`, which is not an unresolved status.

     No tool converts a misclassified attempt; automating it is audit C5. Restarting does not clear the freeze, because it is read from the event store on every cycle.
6. **Fail closed without a trustworthy deadline.** Today may fall outside the table's coverage, or the table may say today is closed while the broker reports the market open. In either case nothing evaluates, a flatten is attempted for every open lot (basis `calendar_fail_closed`), and one `session_calendar_fail_closed` alert is written per ET day.

Daily and `multi_session` runners are unchanged. They never read `next_close` and never load the calendar.

## Vetoes that can block a flatten

The flatten gets no exemption, so every check that does not exempt exposure-reducing orders can veto it:

- **Kill switch:** `kill_switch_active`. The kill switch blocks reducing orders too.
- **Daily loss:** `daily_loss_cap_exceeded`, `kill_switch_threshold_breached`.
- **Manifest:** `manifest_drift`, `no_frozen_manifest`.
- **Strategy and mode:** `strategy_disabled`, `strategy_stage_ineligible`, `paper_mode_required`.
- **Disable conditions:** `disable_condition_active`.
- **Trade count:** `max_trades_per_day_exceeded` (20 per day, account-wide).
- **Data:** `stale_market_data` (the 300 s intraday budget), `no_latest_bar`.
- **Reconciliation:** `reconciliation_drift`, `reconciliation_stale`, `reconciliation_incomplete` and `reconciliation_required`. These bind only when the SELL exceeds the broker-held quantity, e.g. when a sibling strategy nets the account flat (ADR 0055).
- **Concurrent caps (V10):** `max_concurrent_positions_exceeded`, `max_strategy_positions_exceeded`.
- **Order book:** `duplicate_order_window`, `opposite_side_order_open`.
- **Execution:**
  - `market_closed`, if the broker clock flips between the runner's read and the evaluator's;
  - `submit_serialization_unavailable`, when the ADR 0056 lock times out;
  - `risk_check_error`, when a check fails closed.

Order value, single-position and total-exposure caps already exempt a covered reducing order. Exempting exits from any of the checks above, the daily-loss check included, is an owner decision under audit C3 and is not part of this ADR.

## Residuals

- **Flatten fill.** Paper flattens with a market SELL at about 15:55 ET; the backtest fills at the 16:00 close (the last regular-hours bar's close). This gap is accepted. Every flatten explanation records its basis in `triggering_values`, so the residual can be measured per config against the backtest's close:
  - `basis` (`deadline`, `overdue` or `calendar_fail_closed`);
  - `deadline_utc`, `table_close_utc` and `broker_next_close_utc`;
  - `flatten_lead_minutes`;
  - `opened_on`.
- **Boundary entries.** Paper never evaluates the bars that complete at or after the deadline: the 15:50 and 15:55 bars for 5Min tempos. Entries the backtest takes on those bars do not happen in paper. That includes the engine's boundary BUY, which fills at the next open and is an accepted residual of the engine itself.
- **Half-days in the engine.** The engine's fixed 09:30–16:00 mask means the backtest evaluates and flattens through post-13:00 IEX bars on half-days. In the cached SPY 5Min data, the last masked bar is 13:05 on 2025-11-28 and 13:40 on 2025-07-03. Paper flattens at 12:55.
- **Unknown-delivery BUY.** An entry whose delivery is unknown never shows in the ledger as a lot, so a BUY that landed is not flattened (audit C5).
- **Optimistic ledger.**
  - An accepted flatten that later cancels or expires at the broker has already closed the ledger lot.
  - The lot reopens only when `sync_local_only_orders` appends the corrective row (RISK_POLICY known limitation #5). That happens at a controlled or interrupted runner shutdown, or on an operator `reconcile sync-orders`. Until then the runner does not retry that lot.
  - If the flatten had partially filled before it cancelled, that reversal reopens the whole lot, and a re-flatten can oversell by the filled part.
- **Clocks.**
  - The deadline and the evaluation window run on the host clock (`now`); the broker clock only answers whether the market is open.
  - A deadline flatten can go out at 16:00:0x while Alpaca still reports the market open. The broker clock is the acceptance authority, so there is deliberately no local-clock guard on the send: host skew would skip valid flattens.
  - Alpaca's handling of such an order is unverified.

## Out of scope

- **Market-on-close orders.** Exact parity at the 16:00 close would need a new time-in-force through risk and execution.
- **Flatten on controlled stop or kill switch.** Neither stop flattens positions today, and this ADR does not change that.
- **Resolving unknown delivery automatically.** Resolving by `client_order_id`, or no longer recording 5xx/504 as rejected, is audit C5.

## Consequences

- A carried `same_session` lot is flattened at the first open cycle after deploy.
- A vetoed flatten leaves an open lot and one alert per session. The bounded retries above each write a blocked explanation and a blocked trade row.
- An unresolved SELL attempt freezes the strategy durably, with no evaluation and no flatten, until the operator resolves it. A restart does not clear it.
