# ADR 0059 — The paper runner enforces `same_session` with a pre-close flatten

**Status:** Accepted
**Date:** 2026-10-06
**Related:** [issue #396](https://github.com/zackmeach/Milodex/issues/396) (C1, PR 2), [2026-10-05 architecture-deepening audit](../reviews/2026-10-05-architecture-deepening-audit.md) (V5, V10, C3), [ADR 0008](0008-risk-layer-veto-architecture.md) (risk-layer veto), [ADR 0055](0055-event-store-per-strategy-position-ledger.md) (strategy ledger), [`src/milodex/data/sessions.py`](../../src/milodex/data/sessions.py), [`src/milodex/strategies/runner.py`](../../src/milodex/strategies/runner.py)

## Context

`tempo.position_lifecycle: same_session` promises that a position never outlives its session. Only the backtest engine kept that promise: it masks bars to regular hours and force-flattens open positions at the day's last regular-hours close (`backtest.intraday_session_end_flatten`). The paper runner relied on each strategy's own 15:55 time-stop bar. That bar completes at 16:00, so its exit was decided after the bell and vetoed `market_closed`; positions carried overnight (audit V5). The runner also evaluated pre- and post-market bars the backtest never sees.

## Decision

The paper runner enforces the lifecycle the strategy was promoted under. This is system enforcement: the strategy declares the lifecycle in its config, and the runner and the risk layer enforce it. The strategy has no say in when, or whether, the flatten happens.

1. **Deadline.** For each ET session the flatten deadline is `min(table close, broker next_close) − 5 min`. The table is the committed XNYS calendar (`SessionCalendar.xnys()`). `next_close` is a new `BrokerClient.next_close()` read: Alpaca reads its clock, and other brokers return `None`, so the table alone decides. The earlier close wins, so an unscheduled early close is honoured. On a half-day the deadline is 12:55 ET.
2. **Flatten.** On every cycle where the broker reports the market open, and before any fetch, each open lot in the strategy's ledger is sold whole if it is due. A lot is due once now reaches the deadline, or when it is **overdue**, i.e. opened in an earlier ET session. The sale is a market DAY SELL submitted through `ExecutionService.submit_paper` with rule `paper.session_end_flatten`. It gets the full risk evaluation: no exemption, no override and no idempotency key.
3. **Evaluation gate.** A `same_session` cycle fetches and evaluates only while the market is open and now is in `[session open, deadline)`. Evaluation sees only completed regular-hours bars (`SessionPolicy.visible`). It never evaluates a bar from a previous session: in the first bar-width after the open, the newest visible bar is yesterday's last one, which paper never evaluated.
4. **Veto and failure.**
   - The ledger closes a lot only when a SELL is submitted. A vetoed, rejected or raising flatten therefore leaves the lot open, and it is retried on every open-market cycle.
   - Nothing is submitted while the market is closed.
   - Each lot raises one `session_end_flatten_blocked` operator alert (severity warning) per ET session. The alert is deduplicated in memory.
   - A lot still open after the close is overdue, and it is sold at the next open, before evaluation.
   - A kill-switch veto simply leaves the lot open and raises the alert.
5. **Fail closed without a trustworthy deadline.** Today may fall outside the table's coverage, or the table may say today is closed while the broker reports the market open. In either case nothing evaluates, every open lot is flattened with basis `calendar_fail_closed`, and one `session_calendar_fail_closed` alert is written per ET day.

Daily and `multi_session` runners are unchanged. They never read `next_close` and never load the calendar.

## Residual basis vs the backtest

- **Flatten fill.** Paper flattens with a market SELL at about 15:55 ET. The backtest fills at the 16:00 close (the last regular-hours bar's close). This gap is accepted.
- **Recorded basis.** Every flatten explanation records its basis in `triggering_values`:
  - `basis` (`deadline`, `overdue` or `calendar_fail_closed`);
  - `deadline_utc`, `table_close_utc` and `broker_next_close_utc`;
  - `flatten_lead_minutes`;
  - `opened_on`.

  The residual can therefore be measured per config against the backtest's close.
- **Boundary entries.** Paper never evaluates the bars that complete at or after the deadline: the 15:50 and 15:55 bars for 5Min tempos. Entries the backtest takes on those bars do not happen in paper. That includes the engine's boundary BUY, which fills at the next open and is an accepted residual of the engine itself.

## Out of scope

- **Market-on-close orders.** Exact parity at the 16:00 close would need a new time-in-force through risk and execution.
- **Flatten on controlled stop or kill switch.** Neither stop flattens positions today, and this ADR does not change that.

## Consequences

- A carried `same_session` lot is flattened at the first open cycle after deploy.
- **The flatten can be vetoed.** The V10 concurrent-cap exit deadlock (audit C3) can veto it, and so can the kill switch, which blocks reducing orders. Exempting exits from those checks is an owner decision, not part of this ADR. Until then, the result is an open lot, one alert per session, and a retry every cycle. Each retry writes a blocked explanation row.
