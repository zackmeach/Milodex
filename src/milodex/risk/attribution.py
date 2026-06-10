"""Per-strategy position attribution helpers (ADR 0029).

The risk evaluator needs to answer "which strategy owns the current
position in symbol X?" without storing a parallel ``position_attribution``
table — option (a) in ADR 0024 was rejected in ADR 0029 Decision 2.
Attribution is reconstructed on demand from the durable ``trades``
history.

Decision 1: A broker position belongs to the strategy whose runner
submitted its opening fill — the BUY that took the symbol from zero
shares to a non-zero balance. Subsequent increases preserve attribution.
Full liquidation followed by a fresh BUY creates a new attribution.

Decision 2 (CRITICAL FILTER): Only rows with ``status="submitted"``
count as fills. Rows with ``status`` in ``{"preview", "blocked",
"cancelled"}`` are NOT fills — a ``side="buy"`` row with
``status="blocked"`` is a rejected intent, not an opening fill, and
counting it would misattribute the symbol to whichever strategy
proposed the rejected trade. The reconstruction walk MUST exclude
those rows.

Source scoping (R-P0-1): only ``source="paper"`` rows participate in
any fold or walk here. Backtest fills are written to the same
``trades`` table with the same ``strategy_name``/``status`` vocabulary;
without the source predicate every backtest run contaminates the live
position view. The per-strategy fold additionally applies
latest-status-per-``broker_order_id`` reversal (mirroring
``fold_positions`` in operations/reconciliation.py) so corrective
terminal rows appended by ``sync_local_only_orders`` close ledger lots.

Decision 3: Pre-attribution positions (no recoverable submitted opening
fill, or one whose ``strategy_name IS NULL``) are attributed to the
reserved pseudo-strategy id ``"operator"``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from milodex.core.event_store import EventStore


# Mirror of operations/reconciliation.py POSITION_AFFECTING_STATUSES.
# Duplicated (not imported) because reconciliation.py imports
# strategy_positions from this module — the edge already runs the other way.
_POSITION_AFFECTING_STATUSES = frozenset({"submitted", "accepted", "filled"})

OPERATOR_ATTRIBUTION = "operator"
"""Reserved pseudo-strategy id for positions opened outside any runner.

A current broker position whose opening fill was placed by the operator
(`strategy_name IS NULL`), or whose symbol has no recoverable submitted
opening fill in the event store at all, is treated as belonging to this
pseudo-strategy. Operator-attributed positions count toward the
account-scoped cap (ADR 0024 unchanged) but do not count toward any
runner-strategy's per-strategy cap.

Per ADR 0029 Decision 3, ``configs/`` MUST NOT define a strategy whose
id is the literal string ``"operator"``.
"""


def attribute_position(
    *,
    symbol: str,
    event_store: EventStore,
) -> str:
    """Return the strategy_id that owns the current position in ``symbol``.

    Walks the symbol-indexed ``trades`` rows backwards (newest-first),
    filters to rows with ``status="submitted"`` only (Decision 2), and
    locates the most recent zero -> non-zero opening fill. Returns the
    ``strategy_name`` from that row, or :data:`OPERATOR_ATTRIBUTION`
    when (a) the row's ``strategy_name`` is NULL, or (b) the symbol has
    no submitted fills in the event store at all.

    The "opening fill" is the BUY that made the running submitted-share
    balance non-zero. Subsequent BUYs on top of an open position do not
    re-attribute. A SELL that fully liquidates breaks the chain — a
    later BUY starts a new attribution from zero shares.

    Args:
        symbol: The position symbol to attribute. Compared case-insensitively
            (uppercase) since ``ExecutionRequest`` already normalizes.
        event_store: Source of truth for trade history.

    Returns:
        The owning ``strategy_id`` as a string, or
        :data:`OPERATOR_ATTRIBUTION` per Decision 3.
    """
    normalized = symbol.strip().upper()
    rows = _fetch_submitted_trade_rows(event_store, normalized)
    if not rows:
        return OPERATOR_ATTRIBUTION

    # Walk forward through the submitted-fills timeline and track the
    # running share balance. We need forward order (oldest-first) to
    # detect zero -> non-zero transitions correctly. Each transition
    # captures the strategy_name of the row that did the opening BUY;
    # the result is the most recent such transition that's still open.
    rows_sorted = sorted(rows, key=lambda r: r["id"])
    running_qty = 0.0
    opening_strategy_name: str | None = None
    opening_recorded = False
    for row in rows_sorted:
        side = str(row["side"]).lower()
        qty = float(row["quantity"])
        prior_qty = running_qty
        if side == "buy":
            running_qty += qty
        elif side == "sell":
            # Invariant: a broker position cannot be net-short in this
            # system, so clamp at 0. A SELL exceeding prior submitted BUYs
            # is a data artifact (partial-fill mismatch / out-of-system
            # manual sell), not a real short; letting it drive the running
            # balance negative would require the phantom debt to be repaid
            # before a re-buy could re-open the chain — silently dropping a
            # held position from its strategy's ADR-0029 cap (fail-open).
            running_qty = max(0.0, running_qty - qty)
        else:
            # Unknown side — defensive: don't change the running total.
            continue
        # Detect zero -> non-zero opening transition.
        if prior_qty <= 0 and running_qty > 0:
            opening_strategy_name = row["strategy_name"]
            opening_recorded = True
        # Detect full liquidation: any time we drop to <= 0, the next
        # opening BUY starts a fresh attribution.
        if running_qty <= 0:
            opening_recorded = False
            opening_strategy_name = None

    if not opening_recorded:
        # No open chain in the durable history (everything liquidated,
        # or we only saw exits). Treat as operator-owned per Decision 3.
        return OPERATOR_ATTRIBUTION

    if opening_strategy_name is None or opening_strategy_name == "":
        return OPERATOR_ATTRIBUTION
    return str(opening_strategy_name)


def count_positions_by_strategy(
    *,
    positions: dict[str, float],
    event_store: EventStore,
) -> dict[str, int]:
    """Return ``{strategy_id: count}`` for the given current positions.

    Uses :func:`attribute_position` per symbol. Only symbols with
    non-zero quantity are counted; the broker is the position arbiter
    per [ADR 0010](../../docs/adr/0010-hybrid-source-of-truth.md), so
    a symbol absent from ``positions`` is absent from the result.

    The ``"operator"`` pseudo-strategy is included as a key when any
    symbol resolves to operator attribution (per Decision 3).

    Args:
        positions: Mapping of symbol to current quantity (broker truth).
            Symbols with zero or negative quantity are ignored.
        event_store: Source of truth for trade history.

    Returns:
        Dict mapping each attributed ``strategy_id`` (or
        :data:`OPERATOR_ATTRIBUTION`) to the number of held symbols
        attributed to it.
    """
    counts: dict[str, int] = {}
    for symbol, qty in positions.items():
        if qty <= 0:
            continue
        owner = attribute_position(symbol=symbol, event_store=event_store)
        counts[owner] = counts.get(owner, 0) + 1
    return counts


def strategy_positions(strategy_id: str, event_store: EventStore) -> dict[str, float]:
    """Per-strategy submitted-fill ledger: symbol -> quantity (buys minus sells, clamped)."""
    balances = _fold_strategy_balances(strategy_id, event_store)
    return {symbol: qty for symbol, qty in balances.items() if qty > 0}


def strategy_position_quantity(
    strategy_id: str,
    symbol: str,
    event_store: EventStore,
) -> float:
    """Quantity for one symbol from :func:`strategy_positions`, or ``0.0``."""
    normalized = symbol.strip().upper()
    return strategy_positions(strategy_id, event_store).get(normalized, 0.0)


def strategy_open_lots(strategy_id: str, event_store: EventStore) -> dict[str, dict[str, Any]]:
    """Open lots per symbol for entry_state: quantity, avg_entry_price, opened_at."""
    rows = _fetch_submitted_trade_rows_for_strategy(event_store, strategy_id)
    lots: dict[str, dict[str, Any]] = {}
    state: dict[str, dict[str, Any]] = {}

    for row in rows:
        symbol = str(row["symbol"]).strip().upper()
        side = str(row["side"]).lower()
        qty = float(row["quantity"])
        recorded_at = _coerce_recorded_at(row["recorded_at"])
        unit_price = float(row.get("estimated_unit_price") or 0.0)
        sym_state = state.setdefault(
            symbol,
            {
                "running_qty": 0.0,
                "avg_entry_price": 0.0,
                "opened_at": None,
            },
        )
        prior_qty = float(sym_state["running_qty"])
        if side == "buy":
            if prior_qty <= 0:
                sym_state["running_qty"] = qty
                sym_state["avg_entry_price"] = unit_price
                sym_state["opened_at"] = recorded_at
            else:
                new_qty = prior_qty + qty
                sym_state["avg_entry_price"] = (
                    sym_state["avg_entry_price"] * prior_qty + unit_price * qty
                ) / new_qty
                sym_state["running_qty"] = new_qty
        elif side == "sell":
            sym_state["running_qty"] = max(0.0, prior_qty - qty)
            if sym_state["running_qty"] <= 0:
                sym_state["avg_entry_price"] = 0.0
                sym_state["opened_at"] = None
        if sym_state["running_qty"] > 0 and sym_state["opened_at"] is not None:
            lots[symbol] = {
                "quantity": float(sym_state["running_qty"]),
                "avg_entry_price": float(sym_state["avg_entry_price"]),
                "opened_at": sym_state["opened_at"],
            }
        else:
            lots.pop(symbol, None)

    return lots


def _fold_strategy_balances(strategy_id: str, event_store: EventStore) -> dict[str, float]:
    rows = _fetch_submitted_trade_rows_for_strategy(event_store, strategy_id)
    running: dict[str, float] = {}
    for row in rows:
        symbol = str(row["symbol"]).strip().upper()
        side = str(row["side"]).lower()
        qty = float(row["quantity"])
        prior = running.get(symbol, 0.0)
        if side == "buy":
            running[symbol] = prior + qty
        elif side == "sell":
            running[symbol] = max(0.0, prior - qty)
    return running


def _fetch_submitted_trade_rows_for_strategy(
    event_store: EventStore,
    strategy_id: str,
) -> list[dict]:
    """Effective paper-fill rows for one strategy, oldest-first (indexed by strategy_name).

    Mirrors ``fold_positions`` (operations/reconciliation.py) semantics:

    - Only ``source = 'paper'`` rows participate. Backtest fills share the
      ``trades`` table with the same ``strategy_name`` and ``status``; without
      this predicate every backtest run pollutes the live ledger (R-P0-1).
    - The latest status per ``broker_order_id`` wins, so a corrective terminal
      row appended by ``sync_local_only_orders`` reverses the original
      optimistic ``submitted`` contribution: submitted→filled counts once,
      submitted→cancelled counts zero. Corrective rows carry
      ``strategy_name=None``, so they are looked up by order id in a second
      query — a strategy-name-filtered fetch alone can never see them. The
      original strategy row (its id-position, price, timestamp) is what the
      fold consumes; the corrective row only decides whether it counts.
    - Rows without a ``broker_order_id`` are counted individually, as before.
    - Only position-affecting statuses count as fills (Decision 2's exclusion
      of preview/blocked/cancelled rows, extended to the corrective-row
      vocabulary).
    """
    with event_store._connect() as connection:  # noqa: SLF001 — see ADR 0029 §Open questions
        rows = [
            dict(r)
            for r in connection.execute(
                """
                SELECT id, recorded_at, side, quantity, symbol, estimated_unit_price,
                       strategy_name, status, broker_order_id
                FROM trades
                WHERE strategy_name = ? AND source = 'paper'
                  AND status NOT IN ('preview', 'blocked')
                ORDER BY id ASC
                """,
                (strategy_id,),
            ).fetchall()
        ]
        latest_status_by_order: dict[str, str] = {}
        if any(row["broker_order_id"] is not None for row in rows):
            status_rows = connection.execute(
                """
                SELECT broker_order_id, status
                FROM trades
                WHERE source = 'paper'
                  AND broker_order_id IS NOT NULL
                  AND status NOT IN ('preview', 'blocked')
                  AND broker_order_id IN (
                      SELECT broker_order_id FROM trades
                      WHERE strategy_name = ? AND source = 'paper'
                        AND broker_order_id IS NOT NULL
                        AND status NOT IN ('preview', 'blocked')
                  )
                ORDER BY id ASC
                """,
                (strategy_id,),
            ).fetchall()
            for status_row in status_rows:  # id ASC — last write wins
                latest_status_by_order[status_row["broker_order_id"]] = status_row["status"]
    effective: list[dict] = []
    seen_orders: set[str] = set()
    for row in rows:
        order_id = row["broker_order_id"]
        if order_id is None:
            if row["status"] in _POSITION_AFFECTING_STATUSES:
                effective.append(row)
            continue
        if order_id in seen_orders:
            continue
        seen_orders.add(order_id)
        if latest_status_by_order.get(order_id) in _POSITION_AFFECTING_STATUSES:
            effective.append(row)
    return effective


def _coerce_recorded_at(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _fetch_submitted_trade_rows(event_store: EventStore, symbol: str) -> list[dict]:
    """Fetch submitted-status trade rows for ``symbol`` from the event store.

    Uses the ``idx_trades_symbol`` index. Filters to ``status="submitted"``
    in SQL — Decision 2's requirement that blocked, preview, and
    cancelled rows are excluded from the walk — and to ``source="paper"``
    so backtest fills sharing the ``trades`` table never drive the
    attribution walk (R-P0-1).

    Returns a list of dicts (column -> value), ordered ascending by id.
    """
    # Reach into the event store's connection. We do this through a
    # private helper to keep the attribution module self-contained
    # without bloating EventStore's public surface.
    with event_store._connect() as connection:  # noqa: SLF001 — see ADR 0029 §Open questions
        rows = connection.execute(
            """
            SELECT id, recorded_at, side, quantity, strategy_name, submitted_by, status
            FROM trades
            WHERE symbol = ? AND status = 'submitted' AND source = 'paper'
            ORDER BY id ASC
            """,
            (symbol,),
        ).fetchall()
    return [dict(row) for row in rows]
