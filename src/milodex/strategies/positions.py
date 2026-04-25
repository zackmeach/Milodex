"""Per-strategy position derivation from the trade ledger.

Rationale (ADR 0021): the broker account is shared across strategies in
paper mode, so ``BrokerClient.get_positions()`` reports the account-wide
view — it does not distinguish which strategy opened which position.
Strategies must instead reconstruct their own open positions from the
``trades`` ledger, filtering by ``strategy_name``. This closes the
cross-strategy contamination class of bugs (see 2026-04-24 meanrev
incident, commit 27cfcce).

Scope kept narrow: Phase 1 is long-only and market-orders-only
(ADR 0013), so a position's open quantity is simply
``sum(buy_qty) - sum(sell_qty)`` over ``submitted`` paper trades for the
strategy, emitted per symbol. Fill-reconciliation gaps (submitted but
rejected-by-broker, partial fills) are out of scope here and handled by
``milodex reconcile``.
"""

from __future__ import annotations

from collections import defaultdict

from milodex.core.event_store import EventStore


def compute_ledger_positions(
    event_store: EventStore,
    strategy_name: str,
) -> dict[str, float]:
    """Return ``{symbol: net_long_quantity}`` for the strategy's open paper positions.

    Sums signed quantities (BUY positive, SELL negative) over submitted
    paper trades for ``strategy_name``. Only symbols with a strictly
    positive net remain in the result — Phase 1 strategies are long-only,
    so a zero or negative net means "no open position" here.
    """
    nets: dict[str, float] = defaultdict(float)
    for trade in event_store.list_trades_for_strategy(strategy_name):
        symbol = trade.symbol.upper()
        side = trade.side.lower()
        if side == "buy":
            nets[symbol] += float(trade.quantity)
        elif side == "sell":
            nets[symbol] -= float(trade.quantity)
        # Other sides (e.g. hypothetical 'short') are ignored for Phase 1.
    return {symbol: qty for symbol, qty in nets.items() if qty > 0}
