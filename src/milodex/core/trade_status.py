"""Shared trade-status vocabulary for position folds.

Single home for the status set that both per-strategy attribution
(``risk/attribution.py``) and broker/local reconciliation
(``operations/reconciliation.py``) treat as position-affecting when
folding the durable ``trades`` history. Previously each module carried
its own copy because reconciliation imports from attribution (the edge
already ran risk -> operations) and neither could import the other;
``core`` imports from neither, so it can host the constant without a
cycle (P2-10).

This module must stay dependency-free within milodex: importing from
``risk``, ``operations``, or ``execution`` here would re-create the
cycle this module exists to break.
"""

from __future__ import annotations

POSITION_AFFECTING_STATUSES = frozenset({"submitted", "accepted", "filled"})
"""``trades.status`` values that move a position fold.

A row in one of these statuses contributes its signed quantity to the
running balance. Everything else (``preview``, ``blocked``,
``cancelled``, ``rejected``) is a non-fill: a rejected or cancelled
intent never moved shares, and counting it would misattribute or
double-count positions (ADR 0029 Decision 2, extended to the
corrective-row vocabulary written by ``sync_local_only_orders``).
"""
