# Live-fire findings — 2026-06-22

One-day live-fire of the paper fleet (Monday). Fleet launched **mid-session
(~10:46 ET)** after a weekend down, run through the close, stopped clean at
16:05 ET. Four findings; the daily-execution gap (Finding 3) is the headline.

**Bottom line:** the strategy *brains* work — every strategy made sensible
decisions under live data — but **nothing in the paper fleet could actually
submit an order today**. Intraday is gated by missing freezes (one command
away); daily is gated by an architectural gap (a feature away).

---

## Finding 1 — Launch timing dominates intraday (operational, not a bug)

Launching at 10:46 ET missed essentially the whole day's intraday signal:

- **Open-anchored strategies are dead if launched after the opening range.**
  The unconditional benchmark enters on exactly one tick — the post-opening-range
  bar (10:00 ET with `opening_range_minutes=30`) — then exits 15:55 ET
  (`strategies/bench_unconditional_intraday_long.py:116`). ORB, opening_range_retest,
  gap_continuation share this shape. Miss the open → dead for the session.
- **rsi2 mean-reversion: 6 of 8 deployed symbols were locked out at launch.**
  SPY/QQQ/IWM/DIA/XLF/TLT each printed an oversold RSI(2) bar in the first ~75 min
  *before the fleet was up*. The one-entry-per-session rule
  (`strategies/meanrev_rsi2_intraday.py:193`, `_already_entered_this_session`)
  keys off **whether an oversold bar printed in the session history**, not whether
  *this runner* entered — so a mid-session launch is locked out of any symbol that
  already dipped. This is faithful to backtest/live parity (in a from-open run the
  two are equivalent); the divergence is purely an artifact of mid-session launch.

**Implication:** the intraday fleet must launch at/before the open to behave as
designed. There is **no auto-launch mechanism** in the repo — nothing starts the
fleet at the open (confirmed: no scheduled task, no launch script; the Phase 2
gate doc even notes "nothing auto-launches them"). Decision taken this session:
**manual pre-open deploy tomorrow** to get one clean full-session intraday test
before deciding whether to automate.

## Finding 2 — Phase 2 intraday candidates are unfrozen → blocked

The Phase 2 multi-symbol intraday candidates carry `stage: paper` in YAML but were
**never `promotion freeze`d**, so the risk layer blocks every order they submit
with `manifest_drift: no_frozen_manifest` (`risk/evaluator.py:321`).

- Confirmed live, twice: GLD (BUY 26 @ 11:20 ET) and XLE (BUY 188 @ 13:20 ET) both
  fired correct oversold signals; every risk check passed **except** the missing
  manifest. `order_value` and `single_position` passed on the 188-share order, so
  sizing/caps are fine — only the freeze gate stops it.
- Scope: **11 strategies are frozen** (all 6 daily + the 5 original *SPY* intraday).
  The **16 non-SPY ETF replicas** added in Phase 2 are all unfrozen.

This is the **safe-by-default observe-first gate** working as intended: `stage: paper`
makes a strategy deployable/observable; it can't *submit* until an operator explicitly
freezes. **Fix is one `promotion freeze` per strategy** — a deliberate governance call,
deferred (scope TBD). Prerequisite for tomorrow's intraday test producing any fills.

## Finding 3 — Daily fleet is structurally unable to execute (HEADLINE)

At 16:01:40 ET (close + lock-in) the daily fleet evaluated and produced **real,
correct signals**:

- `regime.daily.sma200_rotation` → SPY BUY 12 (200-DMA risk-on)
- `meanrev.daily.pullback_rsi2` → BUY XLP 123 / WMT 86 / VZ 223 (oversold names
  across the curated basket — multi-name daily mean-reversion working)
- `breakout.daily.atr_channel` → no signal (no XLB breakout)

**Every order was blocked by `market_closed`** (`risk/evaluator.py:431`,
`_check_market_open`). `manifest_drift` *passed* ("Runtime config matches frozen
manifest") — these are properly frozen. The block is solely the market-hours gate.

The contradiction is intrinsic to the current design:

> Daily (1D) strategies **must** decide post-close — they need the finalized daily
> bar, and the runner no-ops during the open (`strategies/runner.py` market-hours
> gate). But `_check_market_open` unconditionally blocks submits when the market is
> closed, with **no daily exemption and no decision-only/queue path**. Decide
> post-close → submit post-close → `market_closed` veto. **No 1D strategy can reach
> the broker — not even the lifecycle-proof regime strategy.**

A queue-at-open fix was proposed in `docs/architecture/roadmaps/2026-06-10-hardening-roadmap.md`
("lock in at close, submit TIF=day pre-open next session, with a mandatory
re-validation pass at submit time") but **was never built**. Today's live fire is
what surfaced that it's load-bearing.

### Options (decision deferred)

1. **Queue-at-open (higher fidelity).** Lock in the decision at close, persist it,
   submit TIF=day at next open with a mandatory re-validation pass (positions, risk,
   staleness re-checked against morning state). Real feature: touches execution +
   risk + runner. Write the design/plan first.
2. **Relax `market_hours` for daily (smaller).** Let 1D strategies submit post-close
   and let Alpaca queue the order for next open. Simpler, but skips the re-validation
   safety option 1 provides — the decision could be stale by next open. Needs
   risk-invariant review.
3. **Status quo.** Daily strategies remain decision-only / non-executing in paper.

## Finding 4 — Stability soak: clean (positive)

10 runners, ~5 h continuous (10:46–16:05 ET):

- All `on schedule` the whole session; **no leak, no crash, no wedge.** Per-runner
  memory flat/sawtooth (intraday ~165–180 MB, daily ~47 MB — daily runners *shed*
  ~70 MB post-eval). Machine comfortable throughout (13–16 GB free of 32).
- The fleet **survived the monitoring loop being stopped** — runners are detached
  processes, independent of the driver.
- Controlled-stop drained all 10 cleanly (`controlled_stop`); `verify` CLEAN.

This is a good reliability result on the merged code — the crash-class defects from
prior sessions did not recur.

---

## Next session

- **Manual pre-open intraday deploy** (before ~09:30 ET) for one clean full-session
  test — the only way to exercise open-anchored entry + a real concurrent-submit
  test (#270/#262) without the mid-session lockout confound.
- **Freeze decision (Finding 2)** is the prerequisite for that test producing fills.
- **Daily-execution gap (Finding 3)** awaits a design decision.
