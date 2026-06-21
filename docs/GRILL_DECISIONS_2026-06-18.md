# Milodex Grill Decisions - 2026-06-18

## Purpose

This document captures a founder/Codex strategy conversation about Milodex at a high level.
It is not an implementation spec, an ADR, or a replacement for existing product documents.
It is a durable record of the decisions locked during the conversation so future product,
strategy, UX, and architecture work can stay aligned.

The conversation frame was: Codex acting as a senior software engineer in fintech, the
founder acting as CEO, and the goal being to clarify where Milodex should go next.

## Core Strategic Direction

Milodex should optimize the next phase around proving the trustworthiness of the harness,
not around prematurely chasing the highest possible profit.

Profit still matters. A system that never finds edge is not useful. But the near-term
proof should be that the harness behaves consistently, is observable, governs automation
correctly, and gives the operator justified trust before real capital is expanded.

Locked direction:

- The next 30-60 day goal is proof of harness trustworthiness.
- Milodex should avoid presenting itself as a magic profit machine.
- Evidence quality, operational consistency, and risk-governed automation are the product's
  credibility base.
- Profitability is validation, not the only measure of success.

## Product North Star

Milodex should remain accessible to operators who are not financially sophisticated.

The founder explicitly does not want the user to have to know what "AAPL" is, choose
tickers manually, understand Sharpe ratios, or manage trading universes as a prerequisite
for using the product.

Locked direction:

- Milodex abstracts the operator away from the financial-literacy burden where possible.
- The operator should choose strategies, risk posture, and whether to launch runners.
- The system should own symbol selection through governed universes, pools, evidence, and
  strategy configuration.
- Depth must exist for users who want to inspect it, but depth should not be required for
  ordinary operation.
- The basic user path should feel simple: open Milodex, review what is available today,
  pick what to run, choose a risk profile, launch, observe, and leave.

## Bench Is a Strategy Control Surface

Bench should be understood as a control surface for strategies, not primarily as a hidden
roster builder.

The user should be able to see the automated investing strategies available to them and
decide what to do with those strategies: backtest, paper trade, launch, stop, or review.

Locked direction:

- Bench shows the operator what automated strategies are available.
- Bench lets the operator start individual runners.
- Bench lets the operator backtest strategies and help them move through the evidence
  lifecycle.
- Bench should not abstract away control over which strategies are being run.
- Backend systems may organize launches into sessions or plans for audit, recovery, and
  lifecycle management, but the product experience remains runner-level control.

## Strategy Roster Stability

The strategy roster should not change based on the selected risk profile.

The same strategies should be visible across Conservative, Standard, and Aggressive modes.
Risk posture may change whether a strategy is runnable, what warnings are displayed, how it
is sized, or how suitable it appears, but it should not cause the core roster itself to
appear or disappear.

Locked direction:

- The core strategy roster is stable across risk profiles.
- Risk profiles may change runability, sizing, warnings, and fit.
- Risk profiles should not hide or swap out the strategy roster.
- The user should be able to understand how the same strategy behaves under different
  risk postures.

## Promotion Is Profile-Independent

A strategy that would not be safe enough under Conservative should not become promotable
merely because the operator selected Aggressive.

Aggressive can be more permissive at runtime, but it cannot lower the evidence standard
required to promote a strategy through the lifecycle.

Locked direction:

- Promotion gates are profile-independent.
- A strategy must clear a global evidence floor before promotion.
- Conservative, Standard, and Aggressive do not change whether the strategy is credible.
- Aggressive does not mean "50/50 profit or loss is acceptable."
- Aggressive means the system may allow more size, concurrency, exposure, or drawdown
  tolerance inside bounded policy after evidence has already been earned.

## Risk Profiles

Risk profiles are runtime postures, not truth engines.

They should express the operator's risk tolerance inside bounded, auditable policy. They
should not rewrite strategy evidence, weaken the risk layer, or allow a strategy to pass
promotion gates it could not otherwise pass.

Locked direction:

- Conservative, Standard, and Aggressive are runtime policy overlays.
- Risk profiles can change sizing, exposure, concurrent runners, stop/review thresholds,
  and confirmation friction.
- Risk profiles can affect Profile Fit and runability.
- Risk profiles do not change Evidence Score.
- Risk profiles do not lower promotion gates.
- Risk profiles do not give strategies control over risk.

Useful product language:

> Aggressive widens allowed risk. It does not lower evidence gates.

## Milodex Score

The founder wants a Milodex Score that helps non-expert operators understand which
strategies have earned trust.

The score should not be a simplistic "expected profit" number. It should communicate
justified trust: how much evidence the strategy has earned, how durable that evidence is,
how it behaved under stress, and how well it fits the selected runtime risk posture.

Locked direction:

- Milodex needs a clear strategy score.
- The score should be legible to less financially literate operators.
- The score should distinguish durable evidence from short-term P/L.
- Sharpe and similar technical metrics belong in details, not as the primary user-facing
  row metric.
- A short recent gain should not dominate the trust story.

Proposed score split:

- Evidence Score: profile-independent measure of earned trust.
- Profile Fit: risk-profile-dependent measure of suitability under the current posture.

Example:

- Evidence 84 / Conservative Fit 62
- Evidence 84 / Standard Fit 78
- Evidence 84 / Aggressive Fit 91

## Durable P/L Metric

The founder wants a more durable P/L number analogous to soccer's "per 90 minutes" stats:
a normalized performance measure rather than a raw count.

Locked direction:

- Milodex should avoid over-weighting raw short-window P/L.
- Strategy rows should include a normalized performance metric that is easier to compare.
- The preferred concept is something like Typical Edge per $1k per 100 market sessions.
- This should be treated as a product metric to refine, not a final formula yet.

Candidate language:

> Typical Edge / $1k / 100 market sessions

This metric is meant to help answer: "If this strategy ran at a normalized size over a
meaningful number of market sessions, what did it typically produce?"

## Intraday Direction

The founder wants Milodex to lean more heavily into intraday automation.

The current product has leaned toward daily strategies because that is what was available
and safe earlier, especially under the old day-trading constraints. That should not define
the long-term direction.

Locked direction:

- Intraday should become a major product lane.
- The product should support a user hopping on, launching runners, watching them work, and
  closing the app when done.
- Milodex should not require the user to sit at the computer all day.
- Daily strategies can remain, but they should not dominate the product identity.
- Intraday requires stronger data fidelity, better sample size, and better operational
  consistency before live confidence can be claimed.

## Symbol Selection And Universes

The founder does not want to pick symbols manually.

The current small sample size around SPY, AAPL, and a few others is not enough to build
confidence. Milodex needs larger governed pools so strategies can flex across more
opportunities and earn stronger evidence without the operator manually choosing tickers.

Locked direction:

- Symbol selection should primarily belong to the strategy/research layer, not the user.
- Milodex should maintain curated pools, candidate universes, and approved universes.
- Users should be able to inspect which symbols a strategy trades if they want to.
- Symbol details should be discoverable, not required configuration.
- The system should create enough opportunity surface that canary promotion is not needed
  just to make the product feel alive.

## Canaries And Harness Validation

The existing intraday canaries are useful for proving the harness can run, observe, reject,
and account for behavior. They are not proof of trading edge.

Locked direction:

- Canaries can help validate the harness.
- Canaries should not be confused with promoted edge strategies.
- Having to promote canaries to make Milodex feel active is a sign the real strategy
  discovery and evidence pipeline needs expansion.
- The goal is a pipeline where strategies rise because they earned promotion, not because
  the operator needs something to test.

## Runner Control

Users are starting individual runners.

Even if the backend organizes multiple launches into a session or launch plan for audit,
the user should retain direct control over individual strategy runners.

Locked direction:

- The operator can start one runner.
- The operator can start several runners.
- The operator can stop one runner while leaving others active.
- The operator can stop all runners.
- The operator can relaunch runners later.
- The backend may maintain a session/launch ledger, but this cannot remove runner-level
  control from the user.

## Clean Evaluation Boundary

When a user starts an intraday runner after the market has already opened, it should begin
at the next clean evaluation boundary.

Example: if a five-minute strategy is started at 1:32 PM, it should wait for the next
completed five-minute bar before making a decision. It should not make a partial-bar
decision, and it should not backfill missed morning signals as if the runner had been
active all day.

Locked direction:

- Late-start intraday runners begin at the next clean completed-bar boundary.
- No partial-bar decisioning.
- No catch-up trades from earlier missed signals.
- The behavior should be explainable in the UI because this is about consistency and trust.

## App Close And Quit Behavior

The current ambiguity around closing Milodex is unacceptable for trust.

If the user closes the app while runners are active, Milodex must make the consequence
explicit. This applies both to clicking the window X and to quitting from inside the UI.

Locked direction:

- If no runners are active, Milodex may close normally.
- If runners are active, the same confirmation popup appears for window X and in-app quit.
- There is no silent shutdown.
- There is no silent background continuation.
- The popup must state how many runners are active and what will happen.

Recommended popup actions:

- Keep running and close
- Stop selected runners
- Cancel

## Runner Lifecycle Policies

Runner lifecycle should be policy-driven, not hardcoded to U.S. equity market close.

This matters because Milodex may expand into crypto and other assets with different market
hours and operating assumptions.

Locked direction:

- Each runner or asset/session policy should own its init and shutdown behavior.
- Intraday equities can default to regular-session operation and market-close shutdown.
- Daily equities can evaluate on their own daily boundary without implying all-day runtime.
- Crypto can support 24/7 policies, fixed-duration policies, or user-defined sessions.
- Future assets should plug into explicit lifecycle/session policies rather than inheriting
  equity assumptions.

Default examples:

- Intraday equities paper runner: initialize only during regular market session or queue
  until open; stop at market close.
- Daily equities runner: evaluate on scheduled daily boundary.
- Crypto runner: support 24/7, fixed-duration, or user-defined session policy.

## Continuity After Reopen

When Milodex reopens after runners were left active, the product should not silently
reattach as if nothing happened.

Locked direction:

- Reopen should show a lightweight Continuity Check panel by default.
- The Continuity Check should say exactly what happened while the user was away: which
  runners kept running, which stopped by policy, which trades were placed, which
  rejects/errors occurred, current broker reconciliation status, and whether anything
  needs attention.
- A blocking review modal should appear only when capital, broker, risk, open-order, or
  runner-liveness state is unresolved.
- Normal continuation should feel calm rather than alarming.
- Uncertain capital state must be impossible to miss.
- Technical reattachment can happen automatically, but the product experience must still
  make continuity visible.

Product posture:

- No silent reattach.
- No scary modal unless capital, broker, risk, open-order, or runner-liveness state is
  unresolved.
- The user should immediately understand what happened while they were away.

## Screenshots Discussed

The founder provided images from `C:\Users\zdm80\OneDrive\Documents\tmp` showing the
current product flow:

1. Open Milodex and see high-level finances and results above the fold.
2. Go to Bench to decide what to launch.
3. Scroll to Paper and see strategies with recent positive-looking metrics.
4. Set Risk Profile to Aggressive because the operator is only running strategies they
   currently trust.
5. Launch selected strategies and go to Desk to observe system and market activity.
6. Close Milodex and leave; Milodex should manage the rest according to explicit runner
   lifecycle policy.

Important observation:

- The screenshot rows currently surface Sharpe-like metrics in a way a less financially
  literate operator may read as simple P/L. That confusion is product-important and
  supports the need for Milodex Score and normalized edge metrics.

## Broker And Risk Posture

The high-level architecture remains:

- Strategies propose trade intents.
- Risk evaluates and can veto.
- Execution is the chokepoint before broker submission.
- Broker integration owns actual order placement.
- Milodex owns explanation, audit, promotion, runner lifecycle, and operator control.

Locked direction from the conversation:

- The risk layer stays sacred.
- The operator chooses risk posture inside bounded policy.
- Strategies do not control risk.
- Broker/account truth and Milodex audit truth should both be visible where it matters.
- Consistency and explainability are product features, not implementation details.

## Regulatory Context

The old pattern day trader constraint was a major historical reason daily strategies were
more central. The conversation clarified that this should no longer dominate product
direction.

Locked direction:

- Intraday can now become more central to the product roadmap.
- Any day-trading regulatory or broker constraint should be treated as a current external
  requirement and verified before it drives product strategy.
- The product should not remain daily-first merely because of an older constraint.

## Strategic Summary

The strongest product shape that emerged:

Milodex is a trustworthy automation harness where a less financially literate operator can
open the app, see which strategies have earned evidence, choose a risk posture, launch the
runners they want, and leave the system to operate under explicit lifecycle and risk
policies. The operator controls deployment; the system owns evidence, symbol selection,
risk enforcement, lifecycle consistency, broker interaction, and auditability.

The future should expand toward:

- broader symbol universes chosen by the system,
- stronger intraday strategy discovery,
- durable scoring and normalized performance metrics,
- profile-independent promotion gates,
- runner-level operator control,
- policy-driven lifecycle handling across equities, crypto, and future assets,
- continuity and close/quit behavior that removes ambiguity.

## Open Items

These were not fully decided and should be resumed later:

- Exact Milodex Score formula and component weights.
- Exact durable P/L formula and label.
- Exact placement and visual design for the Continuity Check panel.
- Whether active-runner close behavior should include "stop all" in addition to "stop
  selected runners."
- How strategy details expose symbol pools without making them feel like required user
  configuration.
- How Bench should distinguish recommended paper strategies, still-collecting-evidence
  strategies, canaries/benchmarks, and blocked strategies.
- How crypto lifecycle policies should be represented before crypto execution is real.
