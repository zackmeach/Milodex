# ADR 0012 — Runtime Model & Dual-Stop Shutdown Semantics

**Status:** Accepted
**Date:** 2026-04-16

## Context

A strategy needs a runtime — something that subscribes to bar updates, evaluates the strategy, produces intents, and routes them through `ExecutionService`. Three runtime models were viable: manual one-shot invocation, a long-lived foreground process the operator keeps running, or a background daemon triggered by an external scheduler (Task Scheduler, cron, supervisord).

Shutdown also needs a decision. An operator who wants to stop trading has two very different goals: (a) "stop at the next convenient moment, don't start anything new, finish what's in flight, then exit cleanly," and (b) "abort right now, cancel open orders, persist the halt state, don't trust whatever is running." Conflating these into a single "stop" action leaves no graceful path — or worse, makes the graceful path the default and silently swallows the abort semantics.

## Decision

### Runtime model
The strategy engine runs as a **manually-invoked, long-running foreground process**, started by `milodex strategy run <strategy_name>`. The operator launches it when they want trading to begin and leaves the terminal open while markets are open. There is no daemon, no external scheduler integration, and no auto-start in Phase 1. Only one strategy runs at a time (R-EXE-013).

### Shutdown semantics — two distinct operations
Milodex distinguishes **controlled stop** from **kill switch**:

- **Controlled stop** (graceful) — requests shutdown at the next safe boundary. The runtime: (a) stops accepting new trade intents, (b) lets the current evaluation cycle finish, (c) flushes strategy state to `state/strategies/<name>.json`, (d) exits with status 0. Open orders at the broker are left untouched; positions are left untouched; `KillSwitchStateStore` is left untouched. Next launch resumes cleanly.
- **Kill switch** (immediate abort) — triggers R-EXE-012 semantics: refuses all pending orders, calls `BrokerClient.cancel_all_orders()`, persists `active: true` to `KillSwitchStateStore`, exits non-zero. Next launch refuses to run until the operator explicitly resets the kill switch. This is the same state and the same state file whether the kill switch was invoked by a rule (drawdown, data staleness, broker disconnect, rejected-order spike — R-EXE-010) or by the operator intentionally via the close dialog. One mechanism, two triggers.

### Shutdown dialog
On SIGINT (Ctrl+C), the runtime intercepts the signal and displays an interactive three-option prompt on stdout/stdin:

```
  [c] controlled stop — finish current cycle, exit cleanly
  [k] kill switch     — cancel open orders, halt, require manual reset
  [n] nevermind       — keep running
```

Operator picks one. `c` and `k` proceed to their respective exit paths. `n` resumes normal execution as if the signal never arrived.

### Hard fallback
If the dialog itself fails — a second SIGINT arrives while the dialog is open, or the OS sends a forced-close event (CTRL_CLOSE_EVENT on Windows, SIGTERM on POSIX) — the runtime defaults to **kill switch**. This is the safer-when-in-doubt choice: a cancelled-and-halted trading session is recoverable; an uncancelled-and-forgotten open order is not.

## Rationale

- **Manual foreground invocation matches the operator's mental model.** The operator is the only user, they already plan to be at the machine when trading, and a visible running process is easier to reason about than a daemon that could be running without them realizing. This is consistent with the "Full Transparency" and "Earned Autonomy" principles in VISION.md.
- **No supervisor surface means no supervisor surface to secure, debug, or misconfigure.** Daemons invite a long list of new problems (auto-restart semantics, wake-from-sleep handling, lockfile races) that Phase 1 doesn't need.
- **Controlled stop and kill switch are genuinely different actions.** A single "stop" command forces the operator to pick either "always graceful" (can't abort a misbehaving strategy) or "always abort" (hostile to the common case). Two commands, one safe default, one explicit emergency — matches how the operator actually thinks about it.
- **Kill-switch-by-choice reuses kill-switch-by-rule.** The operator intentionally invoking the kill switch is operationally identical to the system firing it on a breached threshold: orders cancel, state persists, next start requires reset. This preserves the ADR 0005 invariant that the kill switch is always manually reset; the operator who just invoked it will reset it on next start when they're ready to trade again.
- **Safer-when-in-doubt fallback.** If the dialog breaks or the OS force-closes the terminal, defaulting to the abort path means no open paper orders survive a crash unsupervised. The cost of a false kill-switch trip is a one-command reset on next start; the cost of a missed abort could be open orders that fill against stale intent.
- **Positions are deliberately not force-closed** by either shutdown mode. Milodex is a swing-trading system; positions are expected to persist across sessions. Closing them on shutdown would corrupt the strategy logic. The kill switch cancels *open orders*, not held positions — a deliberate scope distinction.
- **Reversible if daemon runtime becomes justified.** The Phase 2+ appendix already contemplates a daemon/GUI future; the current runtime model does not foreclose it. The shutdown dialog semantics carry forward; only the trigger surface changes.
