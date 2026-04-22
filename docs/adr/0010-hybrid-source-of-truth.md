# ADR 0010 — Hybrid Source of Truth

**Status:** Accepted
**Date:** 2026-04-16

## Context

Two systems know something about Milodex's trading activity. Alpaca knows what the account actually looks like — positions, open orders, buying power, cash — because Alpaca is the system that moves money and fills orders. Milodex knows things Alpaca cannot: *why* a trade was submitted (which strategy, what signal, what reasoning), what stage the originating strategy is at, and what the operator has promoted or killed.

When two systems can answer related questions, the design has to name which one wins when they disagree. That moment will arrive: a crash mid-submission, an operator manually trading on Alpaca's web UI, a paper-account reset, a partial fill that happened while Milodex was asleep.

## Decision

Milodex adopts a **hybrid source-of-truth** model with strict, non-overlapping ownership:

- **Alpaca is authoritative for account state:** current positions, open orders, buying power, cash, account status. Every risk check, status display, and portfolio query queries Alpaca live. There is no local mirror of this state.
- **Milodex is authoritative for decisions and governance:** every trade intent, its reasoning blob, per-check risk verdicts, the link from intent to the resulting Alpaca order ID, promotion-log transitions, kill-switch state, strategy state (rolling features, etc.).
- When the two disagree on overlapping facts (e.g., an order's status), **Alpaca wins for what-exists-now**, and the mismatch is logged as a reconciliation WARN surfaced in `milodex status`. Milodex does not silently "fix" either side.

## Rationale

- **Only Alpaca can move money.** Any local mirror of positions or orders is a cache that will go stale the moment real fills happen or the operator touches the web UI. A cache treated as authoritative is a latent correctness bug.
- **Only Milodex knows the "why."** Alpaca does not store strategy names, signal values, promotion stages, or operator reasoning. Those records have no competitor, so Milodex owns them absolutely.
- **No reconciliation skew.** Because neither side mirrors the other's authoritative slice, there's no drift to fix. The trade log is append-only history; the Alpaca state is queried fresh every time.
- **Crash recovery is free.** A crash mid-submission leaves no corrupted local mirror to repair. On restart, Milodex queries Alpaca for current reality and compares it against the trade log to surface any gap. The gap itself is diagnostic, not corrective.
- **Risk checks read live account state.** R-EXE-004 checks like "total portfolio exposure" and "single position size limit" refresh from Alpaca rather than a stored snapshot. Slightly slower, but honest. The alternative invites the exact class of bug the risk layer exists to prevent.
- **Concentrates the system's durable state** in one small, well-defined place (the SQLite trade log and a handful of JSON state files), which is much easier to reason about, back up, and migrate than a sprawling mirror.
