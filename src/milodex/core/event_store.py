"""SQLite-backed event store for durable execution records."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExplanationEvent:
    """Explanation record for a preview or submit decision.

    Carries one of two parent ancestors (the dual-ancestor model — see
    migration 008): ``session_id`` for live paper-runner rows (references
    ``strategy_runs.session_id``) or ``backtest_run_id`` for backtest engine
    rows (references ``backtest_runs.id``). At least one must be set going
    forward — :meth:`EventStore.append_explanation` rejects writes with
    neither, closing the data-integrity gap that produced the 2026-05-07
    "orphan evaluations" audit finding.
    """

    recorded_at: datetime
    decision_type: str
    status: str
    strategy_name: str | None
    strategy_stage: str | None
    strategy_config_path: str | None
    config_hash: str | None
    symbol: str
    side: str
    quantity: float
    order_type: str
    time_in_force: str
    submitted_by: str
    market_open: bool
    latest_bar_timestamp: datetime | None
    latest_bar_close: float | None
    account_equity: float
    account_cash: float
    account_portfolio_value: float
    account_daily_pnl: float
    risk_allowed: bool
    risk_summary: str
    reason_codes: list[str]
    risk_checks: list[dict[str, Any]]
    context: dict[str, Any]
    session_id: str | None = None
    backtest_run_id: int | None = None
    id: int | None = None


@dataclass(frozen=True)
class TradeEvent:
    """Recorded trade attempt linked to an explanation row.

    ``source`` is ``'paper'`` for live paper-session trades and
    ``'backtest'`` for trades produced by the backtest engine. Backtest
    rows additionally carry a ``backtest_run_id`` linking them to the
    originating ``BacktestRunEvent``.
    """

    explanation_id: int
    recorded_at: datetime
    status: str
    source: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    time_in_force: str
    estimated_unit_price: float
    estimated_order_value: float
    strategy_name: str | None
    strategy_stage: str | None
    strategy_config_path: str | None
    submitted_by: str
    broker_order_id: str | None
    broker_status: str | None
    message: str | None
    session_id: str | None = None
    backtest_run_id: int | None = None
    id: int | None = None


@dataclass(frozen=True)
class ExecutionAttemptEvent:
    """Durable pre-submit outbox row for a broker order attempt (P1-02).

    Written with ``status='pending'`` BEFORE the broker call, then finalized
    to ``'submitted'`` (+ ``broker_order_id``), ``'rejected'``, or ``'error'``
    (+ ``failure_detail``) after the broker returns. ``client_order_id`` is
    generated pre-submit and passed to the broker so a crashed attempt can be
    reconciled exactly against the broker's order list. Rows stuck at
    ``'pending'`` indicate a crash mid-submit — see migration 014.
    """

    client_order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    created_at: datetime
    status: str
    strategy_name: str | None = None
    strategy_config_path: str | None = None
    session_id: str | None = None
    broker_order_id: str | None = None
    finalized_at: datetime | None = None
    failure_detail: str | None = None
    id: int | None = None


@dataclass(frozen=True)
class KillSwitchEvent:
    """Kill-switch activation or reset event."""

    event_type: str
    recorded_at: datetime
    reason: str | None
    id: int | None = None


@dataclass(frozen=True)
class StrategyRunEvent:
    """Lifecycle record for a long-running strategy session."""

    session_id: str
    strategy_id: str
    started_at: datetime
    ended_at: datetime | None
    exit_reason: str | None
    metadata: dict[str, Any]
    id: int | None = None


@dataclass(frozen=True)
class PromotionEvent:
    """Immutable record of a strategy stage promotion.

    ``promotion_type`` is ``'statistical'`` when the standard Sharpe / drawdown /
    trade-count thresholds were applied, or ``'lifecycle_exempt'`` when the
    strategy is exempt from those thresholds (see SRS R-PRM-004).
    """

    strategy_id: str
    from_stage: str
    to_stage: str
    promotion_type: str
    approved_by: str
    recorded_at: datetime
    backtest_run_id: str | None = None
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    trade_count: int | None = None
    notes: str | None = None
    manifest_id: int | None = None
    reverses_event_id: int | None = None
    evidence_json: dict[str, Any] | None = None
    id: int | None = None


@dataclass(frozen=True)
class ExperimentEvent:
    """Append-only experiment-registry record (R-PRM-011).

    One row captures the terminal state of a strategy *idea* (keyed by the
    stable ``experiment_id``), per PROMOTION_GOVERNANCE.md "Experiment
    Registry": the hypothesis under test, the stage it reached, why it ended
    there, the supporting evidence, and whether it is worth revisiting.

    Like :class:`PromotionEvent`, the table is append-only — a row is never
    updated or deleted in place. :meth:`EventStore.update_experiment` records a
    change by appending a *new* row that carries the prior fields forward, so
    the version history of an ``experiment_id`` is its row sequence and
    ``get_experiment`` returns the newest.

    ``terminal_status`` is one of ``'promoted'``, ``'rejected'``, ``'failed'``,
    ``'inconclusive'``, ``'abandoned'``, or ``'active'``. ``stage_reached`` is a
    promotion stage (``'backtest'`` … ``'live'``). ``strategy_id`` and
    ``config_hash`` are null until a concrete instance is frozen.
    """

    experiment_id: str
    hypothesis: str
    stage_reached: str
    terminal_status: str
    rationale: str
    recorded_at: datetime
    strategy_id: str | None = None
    config_hash: str | None = None
    evidence_json: dict[str, Any] | None = None
    lessons: str | None = None
    revisitable: bool = False
    id: int | None = None


@dataclass(frozen=True)
class BacktestRunEvent:
    """Lifecycle record for a backtest engine run.

    ``status`` is one of ``'running'``, ``'completed'``, ``'failed'``, or
    ``'cancelled'``. Trades produced by the run reference the row by id
    through ``trades.backtest_run_id``.
    """

    run_id: str
    strategy_id: str
    config_path: str | None
    config_hash: str | None
    start_date: datetime
    end_date: datetime
    started_at: datetime
    status: str
    slippage_pct: float | None
    commission_per_trade: float | None
    metadata: dict[str, Any]
    ended_at: datetime | None = None
    id: int | None = None


@dataclass(frozen=True)
class StrategyManifestEvent:
    """Frozen snapshot of a strategy's YAML config at a promoted stage.

    The ``config_hash`` is SHA-256 over the canonicalized YAML (matching
    :func:`milodex.strategies.loader.compute_config_hash`). ``config_json`` is
    the canonicalized form that was fed into the hash — "what you hashed is
    what you stored" — so slice 2's evidence package can reproduce the exact
    config that was frozen.
    """

    strategy_id: str
    stage: str
    config_hash: str
    config_json: dict[str, Any]
    config_path: str
    frozen_at: datetime
    frozen_by: str
    id: int | None = None


@dataclass(frozen=True)
class PortfolioSnapshotEvent:
    """Daily portfolio snapshot row (equity, cash, positions).

    Broker-side account state only (ADR 0053). Written by
    analytics.snapshots.record_daily_snapshot called from
    StrategyRunner.shutdown. Do NOT write backtest simulation points here.
    """

    recorded_at: datetime
    session_id: str
    strategy_id: str
    equity: float
    cash: float
    portfolio_value: float
    daily_pnl: float
    positions: list[dict[str, Any]]
    id: int | None = None


@dataclass(frozen=True)
class BacktestEquitySnapshotEvent:
    """Simulated equity point from a backtest run (ADR 0053).

    Written by analytics.snapshots.record_backtest_equity_snapshot called
    from BacktestEngine._simulate. Stored in backtest_equity_snapshots,
    never in portfolio_snapshots.
    """

    recorded_at: datetime
    session_id: str
    strategy_id: str
    equity: float
    cash: float
    portfolio_value: float
    daily_pnl: float | None  # nullable — backtests don't track this
    positions: list[dict[str, Any]]
    backtest_run_id: int | None = None  # FK to backtest_runs.id; None for legacy rows
    id: int | None = None


@dataclass(frozen=True)
class OrchestrationBatchEvent:
    """Operator-requested bulk orchestration batch from ADR 0040."""

    batch_id: str
    action_type: str
    requested_by: str
    requested_at: datetime
    status: str
    metadata: dict[str, Any]
    id: int | None = None


@dataclass(frozen=True)
class OrchestrationJobEvent:
    """One strategy/action item inside an orchestration batch."""

    job_id: str
    batch_id: str
    strategy_id: str
    action_type: str
    requested_stage: str
    status: str
    queued_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    cancel_requested_at: datetime | None
    execution_ref_type: str | None
    execution_ref: str | None
    progress_current: int | None
    progress_total: int | None
    progress_label: str | None
    error_code: str | None
    error_message: str | None
    metadata: dict[str, Any]
    id: int | None = None


@dataclass(frozen=True)
class ReconciliationRunEvent:
    """Durable broker/local reconciliation verdict (R-OPS-004)."""

    run_id: str
    recorded_at: datetime
    as_of: datetime
    local_trading_day: str
    status: str
    broker_connected: bool
    market_open: bool | None
    checked_dimensions_version: str
    checked_dimensions: list[str]
    deferred_checks: list[str]
    incident_hash: str | None
    incident_recorded: bool
    incident_deduplicated: bool
    reason_codes: list[str]
    summary: dict[str, Any]
    id: int | None = None


@dataclass(frozen=True)
class ReconciliationAdjustmentEvent:
    """Append-only compensating position adjustment for reconciliation drift."""

    adjustment_id: str
    recorded_at: datetime
    effective_at: datetime
    approved_by: str
    symbol: str
    local_qty_before: float
    broker_qty: float
    delta_qty: float
    reason: str
    source_incident_hash: str
    context: dict[str, Any]
    id: int | None = None


_STRATEGY_RUN_SUBMITTERS: frozenset[str] = frozenset({"strategy_runner", "backtest_engine"})
"""Submitter labels whose explanation rows must carry a run ancestor.

Any explanation written with ``submitted_by`` in this set is enforced by
:meth:`EventStore.append_explanation` to have a non-NULL ``session_id`` or
``backtest_run_id``. System-emitted rows (``operator``, ``reconcile``, etc.)
have no run ancestry by design and pass through unchecked.
"""


_BUFFERED_EXPLANATION_ID = -1
"""Sentinel returned by ``append_explanation`` while inside ``EventStore.batched()``.

In buffer mode the real autoincrement id is not known until the single flush at
context exit, so a placeholder is returned. This is only sound because the
buffered (backtest no-action) path discards the id —
``ExecutionService.record_no_action`` returns ``None`` and the simulation kernel
calls it as a bare statement. No code path that consumes the returned id may
enter ``batched()``.
"""


STALE_PENDING_ATTEMPT_MINUTES = 15
"""Age threshold (minutes) past which a 'pending' execution attempt is stale.

A healthy attempt finalizes within seconds of the broker round-trip; one
still 'pending' after this window almost certainly died between the outbox
write and the broker call/finalize (P1-02). Consumed by
:meth:`EventStore.list_stale_pending_execution_attempts` and surfaced as an
informational reconciliation warning.
"""


MIN_COMPATIBLE_SCHEMA_VERSION = 12
"""Minimum event-store schema version this build can safely operate on.

Bumped whenever a migration introduces a column or table that older code
paths would silently mis-read. After ``_apply_migrations`` runs we assert
the resulting version is at least this — older fixture databases or a
binary older than its migration set fail loudly at construction rather
than producing surprising read results downstream.
"""


_UNSCOPED_STRATEGY = object()
"""Sentinel for :meth:`EventStore.count_recent_submitted_orders` ``strategy_name``.

Distinguishes "no strategy predicate — count account-wide" (the default) from
``strategy_name=None`` ("scope to operator-attributed rows, ``strategy_name IS
NULL``"). The duplicate-order veto passes the proposing strategy so the count is
per-strategy (concurrent-intraday PR5); other callers may omit it for the
account-wide mechanics.
"""


class EventStore:
    """Append-only SQLite event store with forward-only migrations."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Backtest-scoped explanation buffer (see ``batched``). ``None`` outside
        # a ``batched()`` context — every ``append_explanation`` then commits
        # immediately (the live/paper path). A list while batching — buffered
        # rows are held in memory and flushed once at context exit. ``_batch_depth``
        # tracks nesting so only the outermost ``batched()`` owns the flush.
        self._explanation_buffer: list[ExplanationEvent] | None = None
        self._batch_depth: int = 0
        self._apply_migrations()
        sv = self.schema_version
        if sv < MIN_COMPATIBLE_SCHEMA_VERSION:
            raise ValueError(
                f"Event store at {self._path} has schema version {sv}, "
                f"below minimum compatible version {MIN_COMPATIBLE_SCHEMA_VERSION}. "
                "The store may have been opened with an older build that did "
                "not finish migrations."
            )

    @property
    def schema_version(self) -> int:
        with self._connect() as connection:
            return self._get_schema_version(connection)

    def list_table_names(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        return [str(row["name"]) for row in rows]

    def append_explanation(self, event: ExplanationEvent, *, bufferable: bool = False) -> int:
        """Insert an explanation row and return its autoincrement id.

        Enforces the dual-ancestor rule (migration 008) for explanations
        emitted by a strategy run: when ``submitted_by`` is ``'strategy_runner'``
        or ``'backtest_engine'``, at least one of ``session_id`` or
        ``backtest_run_id`` must be set, or :class:`ValueError` is raised
        before the write reaches the database. System-emitted explanations
        (operator-initiated CLI smoke tests, reconciliation incidents,
        kill-switch events) carry no run ancestry by design and pass through
        without the check.

        This is the code-path enforcement that closes the orphan-evaluation
        gap surfaced by the 2026-05-07 EOD audit — see migration 008's
        header for the full rationale, including why we do not use a SQLite
        CHECK constraint here.

        ``bufferable`` (default ``False``): when ``True`` AND a ``batched()``
        context is active, the INSERT is deferred to the single flush at
        context exit and a SENTINEL id is returned (the real id is unknown
        until flush). A caller may set this ONLY if it discards the returned
        id — the backtest per-bar no-action path
        (``ExecutionService.record_no_action``) is the sole such caller. Every
        other caller (e.g. the skip-audit path that links a trade to the
        returned explanation id) leaves it ``False`` and commits immediately
        even inside ``batched()``, so its id is real and its trade FK holds.
        """
        self._require_explanation_ancestor(event)
        if bufferable and self._explanation_buffer is not None:
            # Buffer mode (inside ``batched()``, backtest no-action only): defer
            # the INSERT to the single flush at context exit. NO connection is
            # opened or held here, so concurrent same-process writes that use
            # their own connection (equity snapshots, skip-audit trades) never
            # contend with a held batch transaction. The returned id is a
            # sentinel — sound only because this caller discards it.
            self._explanation_buffer.append(event)
            return _BUFFERED_EXPLANATION_ID
        with self._connect() as connection:
            explanation_id = self._insert_explanation(connection, event)
            connection.commit()
            return explanation_id

    @contextmanager
    def batched(self) -> Iterator[None]:
        """Buffer ``append_explanation`` writes in memory, flush once at exit.

        OPT-IN and BACKTEST-SCOPED. While this context is active,
        :meth:`append_explanation` validates the dual-ancestor rule (unchanged)
        then appends the event to an in-memory buffer and returns a sentinel id
        WITHOUT opening or holding any connection. At context exit a single
        connection is opened, every buffered event is inserted in order, one
        ``commit()`` is issued, and the connection closes.

        Why buffer-and-flush rather than hold one open connection: SQLite is a
        single writer. The backtest sim also writes equity snapshots and trades
        on their own connections mid-loop; holding an uncommitted batch
        transaction across the whole sim would collide with those
        (``database is locked``). Buffering in memory holds NO lock during the
        sim, so those immediate writes proceed with zero contention.

        Durability: the commit lives in ``finally``, so a mid-sim exception
        still flushes everything buffered so far, then the exception re-raises.
        This matches today's per-bar-commit behaviour (rows up to a failure
        already persist). The buffered rows order by insertion, identical to the
        per-bar commit order.

        Re-entrancy: nested ``batched()`` is a no-op inner — the outermost
        context owns the buffer and the single flush.

        The LIVE/paper runner must NEVER enter this context: it relies on
        per-decision durability and (unlike the no-action path) may consume the
        returned explanation id via :meth:`append_explanation_and_trade`, which
        is deliberately NOT routed through the buffer.
        """
        self._batch_depth += 1
        if self._batch_depth > 1:
            # Inner no-op: the outermost context owns the buffer and the flush.
            try:
                yield
            finally:
                self._batch_depth -= 1
            return

        self._explanation_buffer = []
        try:
            yield
        finally:
            buffered = self._explanation_buffer
            self._explanation_buffer = None
            self._batch_depth -= 1
            if buffered:
                # Buffered no-action rows get higher ids than trade explanations committed
                # mid-run. list_explanations() repairs that insertion-order skew at the
                # query boundary with ORDER BY (recorded_at, id).
                with self._connect() as connection:
                    for event in buffered:
                        self._insert_explanation(connection, event)
                    connection.commit()

    @staticmethod
    def _require_explanation_ancestor(event: ExplanationEvent) -> None:
        """Raise unless the dual-ancestor rule (migration 008) is satisfied."""
        if (
            event.submitted_by in _STRATEGY_RUN_SUBMITTERS
            and event.session_id is None
            and event.backtest_run_id is None
        ):
            raise ValueError(
                "ExplanationEvent emitted by "
                f"submitted_by={event.submitted_by!r} must carry an ancestor: "
                "either session_id (referencing strategy_runs.session_id for "
                "paper-runner rows) or backtest_run_id (referencing "
                "backtest_runs.id for backtest engine rows). Refusing to "
                "write an unparented strategy-run explanation."
            )

    @staticmethod
    def _insert_explanation(connection: sqlite3.Connection, event: ExplanationEvent) -> int:
        """Execute the explanation INSERT on ``connection``; no commit."""
        cursor = connection.execute(
            """
            INSERT INTO explanations (
                recorded_at,
                decision_type,
                status,
                strategy_name,
                strategy_stage,
                strategy_config_path,
                config_hash,
                symbol,
                side,
                quantity,
                order_type,
                time_in_force,
                submitted_by,
                market_open,
                latest_bar_timestamp,
                latest_bar_close,
                account_equity,
                account_cash,
                account_portfolio_value,
                account_daily_pnl,
                risk_allowed,
                risk_summary,
                reason_codes_json,
                risk_checks_json,
                context_json,
                session_id,
                backtest_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _dt(event.recorded_at),
                event.decision_type,
                event.status,
                event.strategy_name,
                event.strategy_stage,
                event.strategy_config_path,
                event.config_hash,
                event.symbol,
                event.side,
                event.quantity,
                event.order_type,
                event.time_in_force,
                event.submitted_by,
                int(event.market_open),
                _dt(event.latest_bar_timestamp),
                event.latest_bar_close,
                event.account_equity,
                event.account_cash,
                event.account_portfolio_value,
                event.account_daily_pnl,
                int(event.risk_allowed),
                event.risk_summary,
                _dump_json(event.reason_codes),
                _dump_json(event.risk_checks),
                _dump_json(event.context),
                event.session_id,
                event.backtest_run_id,
            ),
        )
        return int(cursor.lastrowid)

    @staticmethod
    def _insert_trade(
        connection: sqlite3.Connection,
        event: TradeEvent,
        *,
        explanation_id: int | None = None,
    ) -> int:
        """Execute the trade INSERT on ``connection``; no commit.

        ``explanation_id`` overrides ``event.explanation_id`` when supplied —
        used by :meth:`append_explanation_and_trade`, where the parent id is
        only known mid-transaction.
        """
        cursor = connection.execute(
            """
            INSERT INTO trades (
                explanation_id,
                recorded_at,
                status,
                source,
                symbol,
                side,
                quantity,
                order_type,
                time_in_force,
                estimated_unit_price,
                estimated_order_value,
                strategy_name,
                strategy_stage,
                strategy_config_path,
                submitted_by,
                broker_order_id,
                broker_status,
                message,
                session_id,
                backtest_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.explanation_id if explanation_id is None else explanation_id,
                _dt(event.recorded_at),
                event.status,
                event.source,
                event.symbol,
                event.side,
                event.quantity,
                event.order_type,
                event.time_in_force,
                event.estimated_unit_price,
                event.estimated_order_value,
                event.strategy_name,
                event.strategy_stage,
                event.strategy_config_path,
                event.submitted_by,
                event.broker_order_id,
                event.broker_status,
                event.message,
                event.session_id,
                event.backtest_run_id,
            ),
        )
        return int(cursor.lastrowid)

    def append_trade(self, event: TradeEvent) -> int:
        with self._connect() as connection:
            trade_id = self._insert_trade(connection, event)
            connection.commit()
            return trade_id

    def append_explanation_and_trade(
        self,
        *,
        explanation: ExplanationEvent,
        trade: TradeEvent,
    ) -> tuple[int, int]:
        """Insert an explanation row and its trade row in a single transaction.

        The trade is written with ``explanation_id`` set to the newly-inserted
        explanation's id (``trade.explanation_id`` is ignored — the parent id
        does not exist until mid-transaction). Both inserts share one
        ``_connect()`` context so a failure on either side rolls the whole
        thing back: the execution audit trail can never hold an explanation
        whose trade row was lost to a crash between commits, or vice versa
        (P1-02; same precedent as :meth:`append_manifest_and_promotion` and
        :meth:`finalize_backtest_run`). The dual-ancestor rule (migration 008)
        is enforced exactly as in :meth:`append_explanation`. Returns
        ``(explanation_id, trade_id)``.
        """
        self._require_explanation_ancestor(explanation)
        with self._connect() as connection:
            try:
                explanation_id = self._insert_explanation(connection, explanation)
                trade_id = self._insert_trade(connection, trade, explanation_id=explanation_id)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return explanation_id, trade_id

    def append_execution_attempt(self, event: ExecutionAttemptEvent) -> int:
        """Insert a pre-submit outbox row (migration 014) and return its id.

        Must commit BEFORE the broker call it covers — the row is the durable
        evidence that an order may exist at the broker even if every later
        write fails (P1-02).
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO execution_attempts (
                    client_order_id,
                    strategy_name,
                    strategy_config_path,
                    session_id,
                    symbol,
                    side,
                    quantity,
                    order_type,
                    created_at,
                    status,
                    broker_order_id,
                    finalized_at,
                    failure_detail
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.client_order_id,
                    event.strategy_name,
                    event.strategy_config_path,
                    event.session_id,
                    event.symbol,
                    event.side,
                    event.quantity,
                    event.order_type,
                    _dt(event.created_at),
                    event.status,
                    event.broker_order_id,
                    _dt(event.finalized_at),
                    event.failure_detail,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def finalize_execution_attempt(
        self,
        *,
        client_order_id: str,
        status: str,
        finalized_at: datetime,
        broker_order_id: str | None = None,
        failure_detail: str | None = None,
    ) -> None:
        """Record the broker outcome on a 'pending' execution attempt.

        ``status`` is ``'submitted'`` (broker accepted; ``broker_order_id``
        set), ``'rejected'`` (broker rejection), or ``'error'`` (unexpected
        exception). Only a ``'pending'`` row may be finalized — finalizing an
        unknown or already-finalized ``client_order_id`` raises, because the
        outbox protocol writes each attempt exactly once and a mismatch means
        a code bug or external tampering, not a recoverable state.
        """
        if status not in {"submitted", "rejected", "error"}:
            raise ValueError(f"Invalid execution-attempt terminal status: {status!r}")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE execution_attempts
                SET status = ?, broker_order_id = ?, finalized_at = ?, failure_detail = ?
                WHERE client_order_id = ? AND status = 'pending'
                """,
                (status, broker_order_id, _dt(finalized_at), failure_detail, client_order_id),
            )
            connection.commit()
            if cursor.rowcount != 1:
                raise ValueError(
                    f"No pending execution attempt found for "
                    f"client_order_id={client_order_id!r}; refusing to finalize."
                )

    def list_execution_attempts(self) -> list[ExecutionAttemptEvent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM execution_attempts ORDER BY id ASC").fetchall()
        return [_execution_attempt_from_row(row) for row in rows]

    def list_stale_pending_execution_attempts(
        self,
        *,
        older_than_minutes: int = STALE_PENDING_ATTEMPT_MINUTES,
    ) -> list[ExecutionAttemptEvent]:
        """List attempts stuck at 'pending' for more than ``older_than_minutes``.

        A healthy attempt finalizes within seconds; a stale 'pending' row
        means the process died between the outbox write and the broker
        call/finalize — the order may or may not exist at the broker.
        Surfaced by reconciliation as an informational warning so the
        operator can verify against the broker's order list (the
        ``client_order_id`` makes the lookup exact).
        """
        cutoff = datetime.now(tz=UTC) - timedelta(minutes=older_than_minutes)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM execution_attempts
                WHERE status = 'pending' AND datetime(created_at) < datetime(?)
                ORDER BY id ASC
                """,
                (cutoff.isoformat(),),
            ).fetchall()
        return [_execution_attempt_from_row(row) for row in rows]

    def append_kill_switch_event(self, event: KillSwitchEvent) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO kill_switch_events (event_type, recorded_at, reason)
                VALUES (?, ?, ?)
                """,
                (event.event_type, _dt(event.recorded_at), event.reason),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def append_strategy_run(self, event: StrategyRunEvent) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO strategy_runs (
                    session_id,
                    strategy_id,
                    started_at,
                    ended_at,
                    exit_reason,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    event.strategy_id,
                    _dt(event.started_at),
                    _dt(event.ended_at),
                    event.exit_reason,
                    _dump_json(event.metadata),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def update_strategy_run_end(
        self,
        *,
        session_id: str,
        ended_at: datetime,
        exit_reason: str,
    ) -> None:
        """Close the open strategy_runs row for ``session_id``.

        Updates the latest row matching ``session_id`` with ``ended_at IS NULL``.
        UPDATE-by-session because the runner only knows its session id; the
        ORDER BY id DESC + LIMIT 1 guards against the (impossible-by-design but
        defensible) case of duplicate open rows by closing only the most recent.
        """
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE strategy_runs
                SET ended_at = ?, exit_reason = ?
                WHERE id = (
                    SELECT id FROM strategy_runs
                    WHERE session_id = ? AND ended_at IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (_dt(ended_at), exit_reason, session_id),
            )
            connection.commit()

    def reconcile_orphan_strategy_runs(
        self,
        *,
        strategy_id: str,
        ended_at: datetime,
        exit_reason: str = "orphan_recovered",
    ) -> int:
        """Close stale ``strategy_runs`` rows for ``strategy_id``.

        Any row with ``ended_at IS NULL`` is leftover from a runner that died
        without writing its close-out (kill -9, machine sleep, OS crash, OOM).
        The advisory lock for that runner has long since been released, so a
        fresh runner can start; this method is the database-side counterpart —
        it closes the dangling row so reports that count active sessions by
        ``WHERE ended_at IS NULL`` no longer see a phantom.

        Scope is intentionally per-``strategy_id``: an orphan for a different
        strategy is that strategy's next-startup responsibility, not this
        runner's. Returns the number of rows reconciled.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE strategy_runs
                SET ended_at = ?, exit_reason = ?
                WHERE strategy_id = ? AND ended_at IS NULL
                """,
                (_dt(ended_at), exit_reason, strategy_id),
            )
            connection.commit()
            return cursor.rowcount

    def latest_explanation_recorded_at(self, session_id: str) -> str | None:
        """Most recent ``explanations.recorded_at`` for *session_id*, or ``None``.

        Single-row aggregate — bounded by construction, safe on the status
        hot path (unlike :meth:`list_explanations`, which loads every row).
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(recorded_at) AS last_eval FROM explanations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        value = row["last_eval"]
        return str(value) if value is not None else None

    def list_explanations(self) -> list[ExplanationEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM explanations ORDER BY recorded_at ASC, id ASC"
            ).fetchall()
        return [_explanation_from_row(row) for row in rows]

    def get_latest_bar_timestamp(self) -> datetime | None:
        """Return the most recent non-null ``latest_bar_timestamp`` across
        live (non-backtest) explanation rows, or ``None`` if none exists.

        Single-row aggregate — bounded by construction, safe to call on the
        propose path. Avoids the full-table :meth:`list_explanations` load that
        OOM-froze the workstation under concurrent runner launch
        (docs/incidents/2026-05-29-runner-fleet-oom-freeze.md).

        Backtest rows are excluded (``backtest_run_id IS NULL``): the backtest
        engine writes explanations with *historical* bar timestamps into the
        same table, and a backtest run after the last live evaluation would
        otherwise dominate ``ORDER BY id DESC`` and report a years-old bar
        as the freshness signal (same contamination family as R-P0-1).

        Used by workflow-readiness data-freshness checks (bench.py) and the
        CLI trust report (report.py) to derive bar age without loading all rows.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT latest_bar_timestamp
                FROM explanations
                WHERE latest_bar_timestamp IS NOT NULL
                  AND backtest_run_id IS NULL
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return _parse_datetime(row["latest_bar_timestamp"])

    def latest_reconcile_incident_hash(self) -> str | None:
        """Return the ``config_hash`` of the most recently recorded
        ``reconcile_incident`` explanation, or ``None`` if none exists.

        Used by the startup-reconciliation idempotency check, which every runner
        runs on launch. Reads a single row (``ORDER BY id DESC LIMIT 1``) so memory
        stays flat regardless of how large the ``explanations`` table grows — unlike
        :meth:`list_explanations`, whose unbounded full-table load OOM-froze the
        workstation when the fleet launched concurrently (see
        ``docs/incidents/2026-05-29-runner-fleet-oom-freeze.md``).
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT config_hash
                FROM explanations
                WHERE decision_type = 'reconcile_incident'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return None if row is None else row["config_hash"]

    def count_paper_trades(self, strategy_id: str) -> int:
        """Count paper-source trades for ``strategy_id``.

        A bounded ``COUNT(*)`` replacing a full-table ``list_trades()`` load in
        promotion-evidence assembly. Predicate matches the comprehension it
        replaced exactly: ``source = 'paper' AND strategy_name = ?`` (stage is
        intentionally not filtered). Memory stays flat regardless of table size
        — unlike the unbounded load that OOM-froze the workstation when the
        runner fleet launched concurrently
        (docs/incidents/2026-05-29-runner-fleet-oom-freeze.md).
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM trades
                WHERE source = 'paper' AND strategy_name = ?
                """,
                (strategy_id,),
            ).fetchone()
        return int(row[0])

    def count_paper_rejections(self, strategy_id: str) -> int:
        """Count paper-stage risk rejections for ``strategy_id``.

        A bounded ``COUNT(*)`` replacing a full-table ``list_explanations()``
        load in promotion-evidence assembly. Predicate matches the comprehension
        it replaced exactly: ``strategy_name = ? AND strategy_stage = 'paper' AND
        risk_allowed = 0`` (``risk_allowed`` is stored as INTEGER 0/1). See
        :meth:`count_paper_trades` for the OOM context.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM explanations
                WHERE strategy_name = ?
                  AND strategy_stage = 'paper'
                  AND risk_allowed = 0
                """,
                (strategy_id,),
            ).fetchone()
        return int(row[0])

    def list_trades(self) -> list[TradeEvent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
        return [_trade_from_row(row) for row in rows]

    def iter_trades(self) -> Iterator[TradeEvent]:
        """Stream trades in id-ASC order, one row at a time.

        Unlike :meth:`list_trades`, which materializes the entire ``trades``
        table into a list, this yields one ``TradeEvent`` at a time from a
        lazy cursor so memory stays flat regardless of table size. Startup
        reconciliation's full-history position and open-order folds consume
        this to avoid the unbounded ``SELECT *`` load that OOM-froze the
        workstation when the runner fleet launched concurrently
        (docs/incidents/2026-05-29-runner-fleet-oom-freeze.md).
        """
        connection = self._connect()
        try:
            for row in connection.execute("SELECT * FROM trades ORDER BY id ASC"):
                yield _trade_from_row(row)
        finally:
            connection.close()

    def append_reconciliation_run(self, event: ReconciliationRunEvent) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reconciliation_runs (
                    run_id,
                    recorded_at,
                    as_of,
                    local_trading_day,
                    status,
                    broker_connected,
                    market_open,
                    checked_dimensions_version,
                    checked_dimensions_json,
                    deferred_checks_json,
                    incident_hash,
                    incident_recorded,
                    incident_deduplicated,
                    reason_codes_json,
                    summary_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    _dt(event.recorded_at),
                    _dt(event.as_of),
                    event.local_trading_day,
                    event.status,
                    int(event.broker_connected),
                    None if event.market_open is None else int(event.market_open),
                    event.checked_dimensions_version,
                    _dump_json(event.checked_dimensions),
                    _dump_json(event.deferred_checks),
                    event.incident_hash,
                    int(event.incident_recorded),
                    int(event.incident_deduplicated),
                    _dump_json(event.reason_codes),
                    _dump_json(event.summary),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_reconciliation_runs(self) -> list[ReconciliationRunEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reconciliation_runs ORDER BY id ASC"
            ).fetchall()
        return [_reconciliation_run_from_row(row) for row in rows]

    def get_latest_reconciliation_run(self) -> ReconciliationRunEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reconciliation_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else _reconciliation_run_from_row(row)

    def append_reconciliation_adjustment(
        self,
        event: ReconciliationAdjustmentEvent,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reconciliation_adjustments (
                    adjustment_id,
                    recorded_at,
                    effective_at,
                    approved_by,
                    symbol,
                    local_qty_before,
                    broker_qty,
                    delta_qty,
                    reason,
                    source_incident_hash,
                    context_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.adjustment_id,
                    _dt(event.recorded_at),
                    _dt(event.effective_at),
                    event.approved_by,
                    event.symbol,
                    event.local_qty_before,
                    event.broker_qty,
                    event.delta_qty,
                    event.reason,
                    event.source_incident_hash,
                    _dump_json(event.context),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_reconciliation_adjustments(
        self,
        *,
        symbol: str | None = None,
    ) -> list[ReconciliationAdjustmentEvent]:
        query = "SELECT * FROM reconciliation_adjustments"
        params: tuple[Any, ...] = ()
        if symbol is not None:
            query += " WHERE symbol = ?"
            params = (symbol.strip().upper(),)
        query += " ORDER BY id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_reconciliation_adjustment_from_row(row) for row in rows]

    def count_recent_submitted_orders(
        self,
        *,
        symbol: str,
        side: str,
        since: datetime,
        strategy_name: str | None | object = _UNSCOPED_STRATEGY,
    ) -> int:
        """Count submitted trade rows for ``symbol``/``side`` since ``since``.

        The authoritative, untruncated backstop for the risk layer's
        duplicate-order veto: ``broker.get_orders(limit=100)`` silently
        drops the matching prior order when order volume inside the dedup
        window exceeds 100. This consults the durable trade history
        instead.

        Only ``status="submitted"`` rows count — ``preview``/``blocked``
        rows never reached the broker, and ``cancelled`` is excluded to
        mirror the broker-side filter (which excludes CANCELLED/REJECTED).
        Only ``source="paper"`` rows count: backtest fills are stamped
        ``recorded_at = wall-clock now``, so a concurrent backtest on the
        same symbol would otherwise spuriously veto live paper submissions
        (R-P1-4). The planner may drive from ``idx_trades_symbol`` or
        ``idx_trades_source``; either way the predicates filter a narrow
        partition. The time window is pushed into SQL via ``datetime()``
        normalization. Two deliberate semantic shifts from the prior
        Python comparison: (a) ``datetime()`` truncates to whole seconds,
        so the window is up to one second wider — more-veto, the
        fail-safe direction for a dedup backstop; (b) an unparseable
        ``recorded_at`` is excluded (NULL) where the prior code raised
        from ``fromisoformat`` and the evaluator's fail-closed wrapper
        vetoed — reachable only via DB corruption (0 such rows observed
        live), accepted to keep the count total-function.

        P1-02 hardening: the count also includes recent ``execution_attempts``
        outbox rows (written BEFORE the broker call, paper path only) in
        states ``'pending'``/``'submitted'``/``'error'``, so a crash after
        broker success but before the trade row committed still blocks a
        duplicate. Double-count avoidance: a fully-recorded submit produces
        BOTH an attempt (``status='submitted'``, ``broker_order_id`` set) and
        a ``trades`` row carrying the same ``broker_order_id`` — the trades
        subquery already counts those, so the attempts subquery excludes any
        attempt whose ``broker_order_id`` matches an existing submitted paper
        trade. What remains countable is exactly the unknown-delivery
        surface: ``'pending'`` attempts (in-flight, or crashed mid-submit),
        ``'submitted'`` attempts with no trade row (crash between broker ack
        and the atomic explanation+trade write), and ``'error'`` attempts (an
        unexpected broker exception — e.g. a timeout — can fire AFTER the
        order reached the broker, so delivery is unknown and the fail-safe is
        to veto). Only ``'rejected'`` is excluded: a broker rejection is a
        definitive no-order outcome. Over-counting can only widen the veto,
        never narrow it — the check blocks on count > 0.
        """
        normalized_symbol = symbol.strip().upper()
        normalized_side = side.strip().lower()
        since_utc = since.astimezone(UTC) if since.tzinfo else since.replace(tzinfo=UTC)
        params: dict[str, object] = {
            "symbol": normalized_symbol,
            "side": normalized_side,
            "since": since_utc.isoformat(),
        }
        # Strategy scoping (PR5): the duplicate-order veto is per-strategy
        # (RISK_POLICY "Duplicate-Order Policy"). The proposing strategy is
        # passed through so two *different* strategies' legitimate same-side
        # entries on one symbol do not false-veto each other under same-symbol
        # co-run. The clauses are fixed SQL literals (no interpolated user
        # data); the strategy value is bound. Default (sentinel) = no predicate,
        # the account-wide mechanics retained for other callers.
        if strategy_name is _UNSCOPED_STRATEGY:
            trades_clause = ""
            attempts_clause = ""
        elif strategy_name is None:
            trades_clause = " AND strategy_name IS NULL"
            attempts_clause = " AND a.strategy_name IS NULL"
        else:
            trades_clause = " AND strategy_name = :strategy_name"
            attempts_clause = " AND a.strategy_name = :strategy_name"
            params["strategy_name"] = strategy_name
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM trades
                        WHERE symbol = :symbol AND status = 'submitted' AND source = 'paper'
                          AND lower(side) = :side
                          AND datetime(recorded_at) >= datetime(:since){trades_clause}
                    )
                    +
                    (
                        SELECT COUNT(*)
                        FROM execution_attempts a
                        WHERE a.symbol = :symbol AND lower(a.side) = :side
                          AND a.status IN ('pending', 'submitted', 'error')
                          AND datetime(a.created_at) >= datetime(:since){attempts_clause}
                          AND (
                              a.broker_order_id IS NULL
                              OR NOT EXISTS (
                                  SELECT 1 FROM trades t
                                  WHERE t.broker_order_id = a.broker_order_id
                                    AND t.status = 'submitted' AND t.source = 'paper'
                              )
                          )
                    )
                """,
                params,
            ).fetchone()
        return int(row[0])

    def count_submitted_trades_today(self) -> int:
        """Count account-wide paper submitted trades since UTC midnight today.

        Used by the risk layer's ``max_trades_per_day`` guard to detect
        runaway submission logic. Counts ACCOUNT-WIDE (not per-strategy):
        the YAML rationale is "prevents runaway logic" — an account-level
        count is the conservative, attribution-free interpretation, and
        errs toward blocking when concurrent strategies each approach
        the limit independently.

        Day boundary: **UTC midnight** (simplest, unambiguous, no DST).
        Only ``status='submitted'`` rows count (``preview``/``blocked``
        never reached the broker). Only ``source='paper'`` rows count:
        backtest fills must not veto live paper submissions (same
        scoping rationale as :meth:`count_recent_submitted_orders`).
        Query-error is handled by the evaluator's fail-closed wrapper —
        this method raises normally on DB error.
        """
        now_utc = datetime.now(tz=UTC)
        # UTC midnight of the current day — simple date truncation.
        midnight_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM trades
                WHERE status = 'submitted' AND source = 'paper'
                  AND datetime(recorded_at) >= datetime(?)
                """,
                (midnight_utc.isoformat(),),
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # Maintenance / compaction (operator-run; see operations/maintenance.py)
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        """Filesystem path to the SQLite event store (read-only)."""
        return self._path

    def count_prunable_backtest_explanations(self) -> int:
        """Count cascade-safe backtest explanations: rows with a backtest_run_id
        and NO linked trade. Deleting these can never cascade-delete a trade and
        never touches live (NULL backtest_run_id) rows.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM explanations
                WHERE backtest_run_id IS NOT NULL
                  AND id NOT IN (SELECT explanation_id FROM trades)
                """
            ).fetchone()
        return int(row[0]) if row else 0

    def prune_backtest_explanations_without_trades(self, *, batch_size: int = 20_000) -> int:
        """Delete cascade-safe backtest explanations in batches; return total deleted.

        Safety is structural: ``backtest_run_id IS NOT NULL`` excludes every live
        (NULL) row, and ``id NOT IN (SELECT explanation_id FROM trades)`` means the
        ``trades.explanation_id ON DELETE CASCADE`` can never remove a trade.
        ``trades.explanation_id`` is NOT NULL, so the subquery never contains NULL
        (no NOT-IN-with-NULL pitfall).

        Deleting ~1M rows as one transaction balloons the WAL and is slow /
        non-resumable, so the prune runs in committed batches (bounded WAL,
        incremental progress). Each batch re-evaluates the same safe predicate.
        """
        total = 0
        with self._connect() as connection:
            while True:
                ids = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT id FROM explanations
                        WHERE backtest_run_id IS NOT NULL
                          AND id NOT IN (SELECT explanation_id FROM trades)
                        LIMIT ?
                        """,
                        (batch_size,),
                    )
                ]
                if not ids:
                    break
                placeholders = ",".join("?" * len(ids))
                connection.execute(f"DELETE FROM explanations WHERE id IN ({placeholders})", ids)
                connection.commit()
                total += len(ids)
        return total

    def vacuum(self) -> None:
        """Rewrite the DB file to reclaim freed pages.

        VACUUM cannot run inside a transaction, so use a dedicated autocommit
        connection (``isolation_level=None``) rather than ``_connect`` (whose
        context manager opens a transaction around DML).
        """
        connection = sqlite3.connect(self._path)
        try:
            connection.isolation_level = None  # autocommit; VACUUM/checkpoint can't run in a txn
            # In WAL mode freed pages live in the -wal sidecar; checkpoint+truncate
            # flushes them into the main file and truncates the WAL, then VACUUM
            # rebuilds the main file compactly. The trailing checkpoint flushes
            # VACUUM's own WAL writes so the on-disk footprint actually shrinks.
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()

    def get_last_paper_buy_date_by_symbol(self) -> dict[str, str]:
        """Return the most-recent ``recorded_at`` ISO string per symbol for paper BUY trades.

        Performs a targeted indexed query (filter source+side, aggregate per symbol)
        instead of a full-table scan followed by a Python reduction loop.
        Used by :class:`milodex.strategies.runner.StrategyRunner` to build
        ``entry_state`` without loading every trade row.

        Returns a dict mapping ``SYMBOL.upper()`` → ISO-format ``recorded_at`` string
        (the raw TEXT stored in the DB so the caller can parse to ``date``).
        """
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT symbol, MAX(recorded_at) AS last_at
                FROM trades
                WHERE source = 'paper' AND side = 'buy'
                GROUP BY symbol
                """,
            ).fetchall()
        return {row[0].upper(): row[1] for row in rows}

    def list_kill_switch_events(self) -> list[KillSwitchEvent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM kill_switch_events ORDER BY id ASC").fetchall()
        return [_kill_switch_from_row(row) for row in rows]

    def get_latest_kill_switch_event(self) -> KillSwitchEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM kill_switch_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else _kill_switch_from_row(row)

    def list_strategy_runs(self) -> list[StrategyRunEvent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM strategy_runs ORDER BY id ASC").fetchall()
        return [_strategy_run_from_row(row) for row in rows]

    def get_latest_open_session_id(self, strategy_id: str) -> str | None:
        """Return the session_id of the most-recently-opened open run for *strategy_id*.

        Bounded single-row query — safe to call in the polling retry loop of
        ``BenchCommandFacade._latest_open_session_id`` (up to ~60 calls per
        start attempt) without loading the full strategy_runs table.

        Returns ``None`` when no open run exists for the strategy.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id FROM strategy_runs
                WHERE strategy_id = ? AND ended_at IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (strategy_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def create_orchestration_batch(self, event: OrchestrationBatchEvent) -> int:
        """Insert an ADR 0040 orchestration batch row and return its id."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO orchestration_batches (
                    batch_id,
                    action_type,
                    requested_by,
                    requested_at,
                    status,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.batch_id,
                    event.action_type,
                    event.requested_by,
                    _dt(event.requested_at),
                    event.status,
                    _dump_json(event.metadata),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_orchestration_batches(self) -> list[OrchestrationBatchEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM orchestration_batches ORDER BY id ASC"
            ).fetchall()
        return [_orchestration_batch_from_row(row) for row in rows]

    def get_orchestration_batch(self, batch_id: str) -> OrchestrationBatchEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM orchestration_batches WHERE batch_id = ? LIMIT 1",
                (batch_id,),
            ).fetchone()
        return None if row is None else _orchestration_batch_from_row(row)

    def update_orchestration_batch_status(
        self,
        batch_id: str,
        *,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update batch status, optionally replacing its metadata JSON."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE orchestration_batches
                SET status = ?,
                    metadata_json = COALESCE(?, metadata_json)
                WHERE batch_id = ?
                """,
                (status, None if metadata is None else _dump_json(metadata), batch_id),
            )
            connection.commit()

    def create_orchestration_job(self, event: OrchestrationJobEvent) -> int:
        """Insert an ADR 0040 orchestration job row and return its id."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO orchestration_jobs (
                    job_id,
                    batch_id,
                    strategy_id,
                    action_type,
                    requested_stage,
                    status,
                    queued_at,
                    started_at,
                    ended_at,
                    cancel_requested_at,
                    execution_ref_type,
                    execution_ref,
                    progress_current,
                    progress_total,
                    progress_label,
                    error_code,
                    error_message,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.job_id,
                    event.batch_id,
                    event.strategy_id,
                    event.action_type,
                    event.requested_stage,
                    event.status,
                    _dt(event.queued_at),
                    _dt(event.started_at),
                    _dt(event.ended_at),
                    _dt(event.cancel_requested_at),
                    event.execution_ref_type,
                    event.execution_ref,
                    event.progress_current,
                    event.progress_total,
                    event.progress_label,
                    event.error_code,
                    event.error_message,
                    _dump_json(event.metadata),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_orchestration_jobs(
        self,
        *,
        batch_id: str | None = None,
    ) -> list[OrchestrationJobEvent]:
        query = "SELECT * FROM orchestration_jobs"
        params: tuple[Any, ...] = ()
        if batch_id is not None:
            query += " WHERE batch_id = ?"
            params = (batch_id,)
        query += " ORDER BY id ASC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_orchestration_job_from_row(row) for row in rows]

    def get_orchestration_job(self, job_id: str) -> OrchestrationJobEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM orchestration_jobs WHERE job_id = ? LIMIT 1",
                (job_id,),
            ).fetchone()
        return None if row is None else _orchestration_job_from_row(row)

    def update_orchestration_job_status(
        self,
        job_id: str,
        *,
        status: str,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        execution_ref_type: str | None = None,
        execution_ref: str | None = None,
        progress_current: int | None = None,
        progress_total: int | None = None,
        progress_label: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update job ledger fields without creating execution evidence rows."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE orchestration_jobs
                SET status = ?,
                    started_at = COALESCE(?, started_at),
                    ended_at = COALESCE(?, ended_at),
                    execution_ref_type = COALESCE(?, execution_ref_type),
                    execution_ref = COALESCE(?, execution_ref),
                    progress_current = COALESCE(?, progress_current),
                    progress_total = COALESCE(?, progress_total),
                    progress_label = COALESCE(?, progress_label),
                    error_code = COALESCE(?, error_code),
                    error_message = COALESCE(?, error_message),
                    metadata_json = COALESCE(?, metadata_json)
                WHERE job_id = ?
                """,
                (
                    status,
                    _dt(started_at),
                    _dt(ended_at),
                    execution_ref_type,
                    execution_ref,
                    progress_current,
                    progress_total,
                    progress_label,
                    error_code,
                    error_message,
                    None if metadata is None else _dump_json(metadata),
                    job_id,
                ),
            )
            connection.commit()

    def request_orchestration_job_cancellation(
        self,
        job_id: str,
        *,
        cancel_requested_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE orchestration_jobs
                SET cancel_requested_at = ?
                WHERE job_id = ?
                """,
                (_dt(cancel_requested_at), job_id),
            )
            connection.commit()

    def list_non_terminal_orchestration_jobs(self) -> list[OrchestrationJobEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM orchestration_jobs
                WHERE status NOT IN (
                    'completed',
                    'failed',
                    'cancelled',
                    'blocked',
                    'orphan_recovered'
                )
                ORDER BY id ASC
                """
            ).fetchall()
        return [_orchestration_job_from_row(row) for row in rows]

    def append_backtest_run(self, event: BacktestRunEvent) -> int:
        """Insert a new backtest run row and return its autoincrement id."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO backtest_runs (
                    run_id,
                    strategy_id,
                    config_path,
                    config_hash,
                    start_date,
                    end_date,
                    started_at,
                    ended_at,
                    status,
                    slippage_pct,
                    commission_per_trade,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.run_id,
                    event.strategy_id,
                    event.config_path,
                    event.config_hash,
                    _dt(event.start_date),
                    _dt(event.end_date),
                    _dt(event.started_at),
                    _dt(event.ended_at),
                    event.status,
                    event.slippage_pct,
                    event.commission_per_trade,
                    _dump_json(event.metadata),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def update_backtest_run_status(
        self,
        run_id: str,
        *,
        status: str,
        ended_at: datetime | None = None,
    ) -> None:
        """Update the status (and optionally ``ended_at``) of a backtest run."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE backtest_runs
                SET status = ?, ended_at = COALESCE(?, ended_at)
                WHERE run_id = ?
                """,
                (status, _dt(ended_at), run_id),
            )
            connection.commit()

    def reconcile_orphan_backtest_runs(
        self,
        *,
        strategy_id: str,
        ended_at: datetime,
        status: str = "orphan_recovered",
    ) -> int:
        """Close stale ``backtest_runs`` rows for ``strategy_id``.

        Mirrors :meth:`reconcile_orphan_strategy_runs` (PR #44) for the
        backtest table. Any row matching ``status='running' AND ended_at IS
        NULL`` is leftover from a backtest process that died without writing
        its close-out — exactly the failure mode that produced yesterday's
        three stuck rows when the parquet 0-byte bug killed the runner before
        fold-1 execution.

        Both halves of the WHERE clause are required defensively:
        ``status='running'`` is the lifecycle verdict, and ``ended_at IS NULL``
        guards against any partial-write where status was updated but
        ``ended_at`` was not (or vice versa). Terminal-status rows
        (completed / failed) are never swept.

        Scope is intentionally per-``strategy_id``: an orphan for a different
        strategy is that strategy's next-startup responsibility, not this
        engine's. ``backtest_runs`` uses ``status`` itself as the verdict
        column (no ``exit_reason``), so the orphan marker is the new
        ``status='orphan_recovered'``. Returns the number of rows reconciled.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE backtest_runs
                SET status = ?, ended_at = ?
                WHERE strategy_id = ?
                  AND status = 'running'
                  AND ended_at IS NULL
                """,
                (status, _dt(ended_at), strategy_id),
            )
            connection.commit()
            return cursor.rowcount

    def update_backtest_run_metadata(self, run_id: str, *, metadata: dict[str, Any]) -> None:
        """Replace the metadata JSON blob for a backtest run."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE backtest_runs SET metadata_json = ? WHERE run_id = ?",
                (_dump_json(metadata), run_id),
            )
            connection.commit()

    def finalize_backtest_run(
        self,
        run_id: str,
        *,
        status: str,
        metadata: dict[str, Any],
        ended_at: datetime,
    ) -> None:
        """Write terminal status, ``ended_at``, and metadata in one transaction.

        A two-step close-out (status flip then metadata write as separate
        commits) can die between commits and leave a terminal-status row with
        no final metadata — invisible to
        :meth:`reconcile_orphan_backtest_runs`, which only sweeps
        ``status='running'`` rows, while ``metadata_json`` is the sole home of
        the evidence metrics. Serializing the metadata up front and sharing a
        single ``_connect()`` context makes the close-out all-or-nothing: on
        any failure the row stays ``'running'`` and remains sweepable.
        """
        metadata_json = _dump_json(metadata)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE backtest_runs
                SET status = ?, ended_at = ?, metadata_json = ?
                WHERE run_id = ?
                """,
                (status, _dt(ended_at), metadata_json, run_id),
            )
            connection.commit()

    def list_backtest_runs(self) -> list[BacktestRunEvent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM backtest_runs ORDER BY id ASC").fetchall()
        return [_backtest_run_from_row(row) for row in rows]

    def append_promotion(self, event: PromotionEvent) -> int:
        """Insert a promotion record and return its autoincrement id."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO promotions (
                    recorded_at,
                    strategy_id,
                    from_stage,
                    to_stage,
                    promotion_type,
                    approved_by,
                    backtest_run_id,
                    sharpe_ratio,
                    max_drawdown_pct,
                    trade_count,
                    notes,
                    manifest_id,
                    reverses_event_id,
                    evidence_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _dt(event.recorded_at),
                    event.strategy_id,
                    event.from_stage,
                    event.to_stage,
                    event.promotion_type,
                    event.approved_by,
                    event.backtest_run_id,
                    event.sharpe_ratio,
                    event.max_drawdown_pct,
                    event.trade_count,
                    event.notes,
                    event.manifest_id,
                    event.reverses_event_id,
                    None if event.evidence_json is None else _dump_json(event.evidence_json),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_promotions(self) -> list[PromotionEvent]:
        """Return all promotion records ordered by id ascending."""
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM promotions ORDER BY id ASC").fetchall()
        return [_promotion_from_row(row) for row in rows]

    def get_promotion(self, promotion_id: int) -> PromotionEvent | None:
        """Return a single promotion row by id, or ``None`` if absent."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM promotions WHERE id = ? LIMIT 1",
                (promotion_id,),
            ).fetchone()
        return None if row is None else _promotion_from_row(row)

    def list_promotions_for_strategy(
        self, strategy_id: str, limit: int | None = None
    ) -> list[PromotionEvent]:
        """Return promotions for ``strategy_id`` newest-first, optionally limited."""
        query = "SELECT * FROM promotions WHERE strategy_id = ? ORDER BY id DESC"
        params: tuple[Any, ...] = (strategy_id,)
        if limit is not None:
            query += " LIMIT ?"
            params = (strategy_id, int(limit))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_promotion_from_row(row) for row in rows]

    def get_latest_promotion_for_strategy(self, strategy_id: str) -> PromotionEvent | None:
        """Return the most recent promotion for ``strategy_id`` by wall-clock time
        (``recorded_at``), with ``id`` as a deterministic tiebreak, or None.

        Ordering by ``recorded_at`` (not insertion ``id``) is required because a
        backdated event -- e.g. the ADR-0032 ``audit_backfill`` demotion whose
        ``recorded_at`` precedes a later real promotion -- would otherwise be
        returned as 'latest', mis-reporting the strategy's stage to the risk
        layer. ``id DESC`` breaks ties when two events share a ``recorded_at``.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM promotions WHERE strategy_id = ? "
                "ORDER BY recorded_at DESC, id DESC LIMIT 1",
                (strategy_id,),
            ).fetchone()
        return None if row is None else _promotion_from_row(row)

    def append_experiment(self, event: ExperimentEvent) -> int:
        """Insert an experiment-registry record and return its autoincrement id.

        Append-only: callers never update or delete; a change is a new row via
        :meth:`update_experiment`.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO experiment_registry (
                    recorded_at,
                    experiment_id,
                    strategy_id,
                    config_hash,
                    hypothesis,
                    stage_reached,
                    terminal_status,
                    rationale,
                    evidence_json,
                    lessons,
                    revisitable
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _dt(event.recorded_at),
                    event.experiment_id,
                    event.strategy_id,
                    event.config_hash,
                    event.hypothesis,
                    event.stage_reached,
                    event.terminal_status,
                    event.rationale,
                    None if event.evidence_json is None else _dump_json(event.evidence_json),
                    event.lessons,
                    1 if event.revisitable else 0,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def get_experiment(self, experiment_id: str) -> ExperimentEvent | None:
        """Return the latest registry row for ``experiment_id``, or ``None``.

        "Latest" is the highest-id row — the append-only sequence's newest
        version (see :meth:`update_experiment`).
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM experiment_registry WHERE experiment_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (experiment_id,),
            ).fetchone()
        return None if row is None else _experiment_from_row(row)

    def list_experiments(self, *, terminal_status: str | None = None) -> list[ExperimentEvent]:
        """Return the latest row per ``experiment_id``, newest experiment first.

        De-duplicates the append-only sequence to one row per ``experiment_id``
        (the highest id) so each experiment appears once at its current state.
        When ``terminal_status`` is given, filters to experiments whose *latest*
        row has that status. Ordering is deterministic (latest row id DESC).
        """
        query = """
            SELECT er.* FROM experiment_registry AS er
            JOIN (
                SELECT experiment_id, MAX(id) AS max_id
                FROM experiment_registry
                GROUP BY experiment_id
            ) AS latest
            ON er.id = latest.max_id
        """
        params: tuple[Any, ...] = ()
        if terminal_status is not None:
            query += " WHERE er.terminal_status = ?"
            params = (terminal_status,)
        query += " ORDER BY er.id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [_experiment_from_row(row) for row in rows]

    def update_experiment(self, experiment_id: str, **changes: Any) -> int:
        """Record a change to ``experiment_id`` by appending a NEW row.

        Append-only (R-PRM-011): the latest row for ``experiment_id`` is read,
        its fields are carried forward with ``changes`` applied, a fresh
        ``recorded_at`` is stamped, and the result is INSERTed as a new row. The
        prior row is left untouched — there is no in-place UPDATE or DELETE; the
        row sequence is the version history. Returns the new row's id.

        Raises ``KeyError`` if no row exists for ``experiment_id``, or
        ``TypeError`` if ``changes`` names a field that is not a mutable
        ``ExperimentEvent`` column.
        """
        current = self.get_experiment(experiment_id)
        if current is None:
            raise KeyError(f"no experiment registered for experiment_id={experiment_id!r}")

        mutable = {
            "experiment_id",
            "strategy_id",
            "config_hash",
            "hypothesis",
            "stage_reached",
            "terminal_status",
            "rationale",
            "evidence_json",
            "lessons",
            "revisitable",
        }
        unknown = set(changes) - mutable
        if unknown:
            raise TypeError(f"unknown experiment field(s): {sorted(unknown)}")

        carried = {field: getattr(current, field) for field in mutable}
        carried.update(changes)
        return self.append_experiment(ExperimentEvent(recorded_at=datetime.now(tz=UTC), **carried))

    def get_backtest_run(self, run_id: str) -> BacktestRunEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM backtest_runs WHERE run_id = ? LIMIT 1",
                (run_id,),
            ).fetchone()
        return None if row is None else _backtest_run_from_row(row)

    def list_trades_for_backtest_run(self, backtest_run_id: int) -> list[TradeEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM trades WHERE backtest_run_id = ? ORDER BY id ASC",
                (backtest_run_id,),
            ).fetchall()
        return [_trade_from_row(row) for row in rows]

    def append_portfolio_snapshot(self, event: PortfolioSnapshotEvent) -> int:
        """Insert a portfolio snapshot row and return its autoincrement id."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO portfolio_snapshots (
                    recorded_at,
                    session_id,
                    strategy_id,
                    equity,
                    cash,
                    portfolio_value,
                    daily_pnl,
                    positions_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _dt(event.recorded_at),
                    event.session_id,
                    event.strategy_id,
                    event.equity,
                    event.cash,
                    event.portfolio_value,
                    event.daily_pnl,
                    _dump_json(event.positions),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_portfolio_snapshots_for_session(self, session_id: str) -> list[PortfolioSnapshotEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM portfolio_snapshots WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [_portfolio_snapshot_from_row(row) for row in rows]

    def append_strategy_manifest(self, event: StrategyManifestEvent) -> int:
        """Insert a frozen manifest row and return its autoincrement id."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO strategy_manifests (
                    strategy_id,
                    stage,
                    config_hash,
                    config_json,
                    config_path,
                    frozen_at,
                    frozen_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.strategy_id,
                    event.stage,
                    event.config_hash,
                    _dump_json(event.config_json),
                    event.config_path,
                    _dt(event.frozen_at),
                    event.frozen_by,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def append_manifest_and_promotion(
        self,
        *,
        manifest: StrategyManifestEvent,
        promotion: PromotionEvent,
    ) -> tuple[int, int]:
        """Insert a manifest row and a promotion row in a single transaction.

        The returned promotion carries ``manifest_id`` set to the newly-inserted
        manifest's id. Both inserts share one ``_connect()`` context so a
        failure on either side rolls the whole thing back — promotion evidence
        and the manifest it references are always written together or not at
        all (slice-2 plan AD-5).
        """
        with self._connect() as connection:
            manifest_cursor = connection.execute(
                """
                INSERT INTO strategy_manifests (
                    strategy_id,
                    stage,
                    config_hash,
                    config_json,
                    config_path,
                    frozen_at,
                    frozen_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest.strategy_id,
                    manifest.stage,
                    manifest.config_hash,
                    _dump_json(manifest.config_json),
                    manifest.config_path,
                    _dt(manifest.frozen_at),
                    manifest.frozen_by,
                ),
            )
            manifest_id = int(manifest_cursor.lastrowid)
            promotion_cursor = connection.execute(
                """
                INSERT INTO promotions (
                    recorded_at,
                    strategy_id,
                    from_stage,
                    to_stage,
                    promotion_type,
                    approved_by,
                    backtest_run_id,
                    sharpe_ratio,
                    max_drawdown_pct,
                    trade_count,
                    notes,
                    manifest_id,
                    reverses_event_id,
                    evidence_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _dt(promotion.recorded_at),
                    promotion.strategy_id,
                    promotion.from_stage,
                    promotion.to_stage,
                    promotion.promotion_type,
                    promotion.approved_by,
                    promotion.backtest_run_id,
                    promotion.sharpe_ratio,
                    promotion.max_drawdown_pct,
                    promotion.trade_count,
                    promotion.notes,
                    manifest_id,
                    promotion.reverses_event_id,
                    (
                        None
                        if promotion.evidence_json is None
                        else _dump_json(promotion.evidence_json)
                    ),
                ),
            )
            promotion_id = int(promotion_cursor.lastrowid)
            connection.commit()
        return manifest_id, promotion_id

    def list_strategy_manifests(self) -> list[StrategyManifestEvent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM strategy_manifests ORDER BY id ASC").fetchall()
        return [_strategy_manifest_from_row(row) for row in rows]

    def get_active_manifest_for_strategy(
        self, strategy_id: str, stage: str
    ) -> StrategyManifestEvent | None:
        """Return the most recent frozen manifest for ``(strategy_id, stage)``.

        ``None`` when the strategy has never been frozen at that stage.
        """
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM strategy_manifests
                WHERE strategy_id = ? AND stage = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (strategy_id, stage),
            ).fetchone()
        return None if row is None else _strategy_manifest_from_row(row)

    def list_portfolio_snapshots_for_strategy(
        self, strategy_id: str
    ) -> list[PortfolioSnapshotEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM portfolio_snapshots WHERE strategy_id = ? ORDER BY id ASC",
                (strategy_id,),
            ).fetchall()
        return [_portfolio_snapshot_from_row(row) for row in rows]

    def append_backtest_equity_snapshot(self, event: BacktestEquitySnapshotEvent) -> int:
        """Insert a backtest equity snapshot row and return its autoincrement id."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO backtest_equity_snapshots (
                    recorded_at,
                    session_id,
                    strategy_id,
                    equity,
                    cash,
                    portfolio_value,
                    daily_pnl,
                    positions_json,
                    backtest_run_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _dt(event.recorded_at),
                    event.session_id,
                    event.strategy_id,
                    event.equity,
                    event.cash,
                    event.portfolio_value,
                    event.daily_pnl,
                    _dump_json(event.positions),
                    event.backtest_run_id,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def list_backtest_equity_snapshots_for_strategy(
        self, strategy_id: str
    ) -> list[BacktestEquitySnapshotEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM backtest_equity_snapshots WHERE strategy_id = ? ORDER BY id ASC",
                (strategy_id,),
            ).fetchall()
        return [_backtest_equity_snapshot_from_row(row) for row in rows]

    def _apply_migrations(self) -> None:
        """Apply forward-only migrations atomically and concurrency-safely.

        Invariant: for any migration, its DDL **and** the matching
        ``_schema_version`` bump commit as a single unit, and the version
        is re-read *inside* the serialized critical section so a process
        that lost the startup race never re-applies an already-applied
        migration.

        Why this shape:

        * ``sqlite3.executescript`` issues an implicit ``COMMIT`` before it
          runs, which would split each migration's DDL from its version
          bump into separate transactions — exactly the partial-schema /
          stale-version corruption hazard under concurrent construction
          (``ExecutionService``, ``KillSwitchStateStore``, the runner and
          the backtest engine each build their own ``EventStore``). So we
          do *not* use ``executescript``; we split the migration file into
          statements and execute them inside one explicit
          ``BEGIN EXCLUSIVE`` transaction together with the version write.
        * ``BEGIN EXCLUSIVE`` takes SQLite's write lock for the whole
          critical section: the first constructor migrates while every
          other constructor blocks (up to ``busy_timeout``) and then, on
          entering its own ``BEGIN EXCLUSIVE``, re-reads the now-current
          version and finds nothing to do — exactly-once application.
        * Statement splitting uses :func:`sqlite3.complete_statement`
          (SQLite's own tokenizer) so ``;`` inside string literals or SQL
          comments is handled correctly. WAL mode and normal
          single-process startup are unchanged (one quick EXCLUSIVE txn,
          no contention) and remain idempotent.
        """
        migrations = self._load_migrations()
        with self._connect() as connection:
            # _connect() already set PRAGMA busy_timeout so losers of the
            # startup race wait for the EXCLUSIVE write lock instead of
            # failing fast with "database is locked".
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_version (
                    version INTEGER NOT NULL
                )
                """
            )
            connection.commit()
            for version, sql in migrations:
                # Fast pre-check outside the lock (optimisation only — the
                # authoritative re-check is inside the EXCLUSIVE txn).
                if version <= self._get_schema_version(connection):
                    continue
                connection.execute("BEGIN EXCLUSIVE")
                try:
                    # Re-read INSIDE the critical section: a process that
                    # lost the race sees the winner's committed version
                    # and must not re-apply.
                    if version <= self._get_schema_version(connection):
                        connection.execute("ROLLBACK")
                        continue
                    for statement in _split_sql_statements(sql):
                        connection.execute(statement)
                    connection.execute("DELETE FROM _schema_version")
                    connection.execute(
                        "INSERT INTO _schema_version(version) VALUES (?)",
                        (version,),
                    )
                    connection.execute("COMMIT")
                except BaseException:
                    connection.execute("ROLLBACK")
                    raise

    def _load_migrations(self) -> list[tuple[int, str]]:
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        migrations: list[tuple[int, str]] = []
        for path in sorted(migrations_dir.glob("*.sql")):
            version = int(path.stem.split("_", maxsplit=1)[0])
            migrations.append((version, path.read_text(encoding="utf-8")))
        return migrations

    def _get_schema_version(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT version FROM _schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return 0 if row is None else int(row["version"])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        # busy_timeout FIRST: it sets a lock-wait policy without itself
        # taking a lock, so every subsequent statement on this connection
        # (including the WAL-mode pragma, which is a write under
        # contention, and the migration EXCLUSIVE transaction) blocks and
        # retries instead of failing fast with "database is locked" when
        # several EventStore instances are constructed concurrently
        # (ExecutionService / KillSwitchStateStore / runner / engine).
        connection.execute("PRAGMA busy_timeout=30000")
        _set_wal_mode(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _explanation_from_row(row: sqlite3.Row) -> ExplanationEvent:
    backtest_run_id = row["backtest_run_id"] if "backtest_run_id" in row.keys() else None
    return ExplanationEvent(
        id=int(row["id"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        decision_type=str(row["decision_type"]),
        status=str(row["status"]),
        strategy_name=row["strategy_name"],
        strategy_stage=row["strategy_stage"],
        strategy_config_path=row["strategy_config_path"],
        config_hash=row["config_hash"],
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        quantity=float(row["quantity"]),
        order_type=str(row["order_type"]),
        time_in_force=str(row["time_in_force"]),
        submitted_by=str(row["submitted_by"]),
        market_open=bool(row["market_open"]),
        latest_bar_timestamp=_parse_datetime(row["latest_bar_timestamp"]),
        latest_bar_close=(
            None if row["latest_bar_close"] is None else float(row["latest_bar_close"])
        ),
        account_equity=float(row["account_equity"]),
        account_cash=float(row["account_cash"]),
        account_portfolio_value=float(row["account_portfolio_value"]),
        account_daily_pnl=float(row["account_daily_pnl"]),
        risk_allowed=bool(row["risk_allowed"]),
        risk_summary=str(row["risk_summary"]),
        reason_codes=list(_load_json(row["reason_codes_json"])),
        risk_checks=list(_load_json(row["risk_checks_json"])),
        context=dict(_load_json(row["context_json"])),
        session_id=row["session_id"],
        backtest_run_id=int(backtest_run_id) if backtest_run_id is not None else None,
    )


def _trade_from_row(row: sqlite3.Row) -> TradeEvent:
    backtest_run_id = row["backtest_run_id"] if "backtest_run_id" in row.keys() else None
    return TradeEvent(
        id=int(row["id"]),
        explanation_id=int(row["explanation_id"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        status=str(row["status"]),
        source=str(row["source"]),
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        quantity=float(row["quantity"]),
        order_type=str(row["order_type"]),
        time_in_force=str(row["time_in_force"]),
        estimated_unit_price=float(row["estimated_unit_price"]),
        estimated_order_value=float(row["estimated_order_value"]),
        strategy_name=row["strategy_name"],
        strategy_stage=row["strategy_stage"],
        strategy_config_path=row["strategy_config_path"],
        submitted_by=str(row["submitted_by"]),
        broker_order_id=row["broker_order_id"],
        broker_status=row["broker_status"],
        message=row["message"],
        session_id=row["session_id"],
        backtest_run_id=int(backtest_run_id) if backtest_run_id is not None else None,
    )


def _execution_attempt_from_row(row: sqlite3.Row) -> ExecutionAttemptEvent:
    return ExecutionAttemptEvent(
        id=int(row["id"]),
        client_order_id=str(row["client_order_id"]),
        strategy_name=row["strategy_name"],
        strategy_config_path=row["strategy_config_path"],
        session_id=row["session_id"],
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        quantity=float(row["quantity"]),
        order_type=str(row["order_type"]),
        created_at=_parse_datetime(row["created_at"]),
        status=str(row["status"]),
        broker_order_id=row["broker_order_id"],
        finalized_at=_parse_datetime(row["finalized_at"]),
        failure_detail=row["failure_detail"],
    )


def _kill_switch_from_row(row: sqlite3.Row) -> KillSwitchEvent:
    return KillSwitchEvent(
        id=int(row["id"]),
        event_type=str(row["event_type"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        reason=row["reason"],
    )


def _strategy_run_from_row(row: sqlite3.Row) -> StrategyRunEvent:
    return StrategyRunEvent(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        strategy_id=str(row["strategy_id"]),
        started_at=_parse_datetime(row["started_at"]),
        ended_at=_parse_datetime(row["ended_at"]),
        exit_reason=row["exit_reason"],
        metadata=dict(_load_json(row["metadata_json"])),
    )


def _orchestration_batch_from_row(row: sqlite3.Row) -> OrchestrationBatchEvent:
    return OrchestrationBatchEvent(
        id=int(row["id"]),
        batch_id=str(row["batch_id"]),
        action_type=str(row["action_type"]),
        requested_by=str(row["requested_by"]),
        requested_at=_parse_datetime(row["requested_at"]),
        status=str(row["status"]),
        metadata=dict(_load_json(row["metadata_json"])),
    )


def _orchestration_job_from_row(row: sqlite3.Row) -> OrchestrationJobEvent:
    return OrchestrationJobEvent(
        id=int(row["id"]),
        job_id=str(row["job_id"]),
        batch_id=str(row["batch_id"]),
        strategy_id=str(row["strategy_id"]),
        action_type=str(row["action_type"]),
        requested_stage=str(row["requested_stage"]),
        status=str(row["status"]),
        queued_at=_parse_datetime(row["queued_at"]),
        started_at=_parse_datetime(row["started_at"]),
        ended_at=_parse_datetime(row["ended_at"]),
        cancel_requested_at=_parse_datetime(row["cancel_requested_at"]),
        execution_ref_type=row["execution_ref_type"],
        execution_ref=row["execution_ref"],
        progress_current=(
            None if row["progress_current"] is None else int(row["progress_current"])
        ),
        progress_total=None if row["progress_total"] is None else int(row["progress_total"]),
        progress_label=row["progress_label"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        metadata=dict(_load_json(row["metadata_json"])),
    )


def _reconciliation_run_from_row(row: sqlite3.Row) -> ReconciliationRunEvent:
    return ReconciliationRunEvent(
        id=int(row["id"]),
        run_id=str(row["run_id"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        as_of=_parse_datetime(row["as_of"]),
        local_trading_day=str(row["local_trading_day"]),
        status=str(row["status"]),
        broker_connected=bool(row["broker_connected"]),
        market_open=None if row["market_open"] is None else bool(row["market_open"]),
        checked_dimensions_version=str(row["checked_dimensions_version"]),
        checked_dimensions=list(_load_json(row["checked_dimensions_json"])),
        deferred_checks=list(_load_json(row["deferred_checks_json"])),
        incident_hash=row["incident_hash"],
        incident_recorded=bool(row["incident_recorded"]),
        incident_deduplicated=bool(row["incident_deduplicated"]),
        reason_codes=list(_load_json(row["reason_codes_json"])),
        summary=dict(_load_json(row["summary_json"])),
    )


def _reconciliation_adjustment_from_row(row: sqlite3.Row) -> ReconciliationAdjustmentEvent:
    return ReconciliationAdjustmentEvent(
        id=int(row["id"]),
        adjustment_id=str(row["adjustment_id"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        effective_at=_parse_datetime(row["effective_at"]),
        approved_by=str(row["approved_by"]),
        symbol=str(row["symbol"]),
        local_qty_before=float(row["local_qty_before"]),
        broker_qty=float(row["broker_qty"]),
        delta_qty=float(row["delta_qty"]),
        reason=str(row["reason"]),
        source_incident_hash=str(row["source_incident_hash"]),
        context=dict(_load_json(row["context_json"])),
    )


def _promotion_from_row(row: sqlite3.Row) -> PromotionEvent:
    return PromotionEvent(
        id=int(row["id"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        strategy_id=str(row["strategy_id"]),
        from_stage=str(row["from_stage"]),
        to_stage=str(row["to_stage"]),
        promotion_type=str(row["promotion_type"]),
        approved_by=str(row["approved_by"]),
        backtest_run_id=row["backtest_run_id"],
        sharpe_ratio=None if row["sharpe_ratio"] is None else float(row["sharpe_ratio"]),
        max_drawdown_pct=(
            None if row["max_drawdown_pct"] is None else float(row["max_drawdown_pct"])
        ),
        trade_count=None if row["trade_count"] is None else int(row["trade_count"]),
        notes=row["notes"],
        manifest_id=None if row["manifest_id"] is None else int(row["manifest_id"]),
        reverses_event_id=(
            None if row["reverses_event_id"] is None else int(row["reverses_event_id"])
        ),
        evidence_json=None if row["evidence_json"] is None else _load_json(row["evidence_json"]),
    )


def _experiment_from_row(row: sqlite3.Row) -> ExperimentEvent:
    return ExperimentEvent(
        id=int(row["id"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        experiment_id=str(row["experiment_id"]),
        strategy_id=row["strategy_id"],
        config_hash=row["config_hash"],
        hypothesis=str(row["hypothesis"]),
        stage_reached=str(row["stage_reached"]),
        terminal_status=str(row["terminal_status"]),
        rationale=str(row["rationale"]),
        evidence_json=None if row["evidence_json"] is None else _load_json(row["evidence_json"]),
        lessons=row["lessons"],
        revisitable=bool(row["revisitable"]),
    )


def _backtest_run_from_row(row: sqlite3.Row) -> BacktestRunEvent:
    return BacktestRunEvent(
        id=int(row["id"]),
        run_id=str(row["run_id"]),
        strategy_id=str(row["strategy_id"]),
        config_path=row["config_path"],
        config_hash=row["config_hash"],
        start_date=_parse_datetime(row["start_date"]),
        end_date=_parse_datetime(row["end_date"]),
        started_at=_parse_datetime(row["started_at"]),
        ended_at=_parse_datetime(row["ended_at"]),
        status=str(row["status"]),
        slippage_pct=(None if row["slippage_pct"] is None else float(row["slippage_pct"])),
        commission_per_trade=(
            None if row["commission_per_trade"] is None else float(row["commission_per_trade"])
        ),
        metadata=dict(_load_json(row["metadata_json"])),
    )


def _strategy_manifest_from_row(row: sqlite3.Row) -> StrategyManifestEvent:
    return StrategyManifestEvent(
        id=int(row["id"]),
        strategy_id=str(row["strategy_id"]),
        stage=str(row["stage"]),
        config_hash=str(row["config_hash"]),
        config_json=dict(_load_json(row["config_json"])),
        config_path=str(row["config_path"]),
        frozen_at=_parse_datetime(row["frozen_at"]),
        frozen_by=str(row["frozen_by"]),
    )


def _portfolio_snapshot_from_row(row: sqlite3.Row) -> PortfolioSnapshotEvent:
    return PortfolioSnapshotEvent(
        id=int(row["id"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        session_id=str(row["session_id"]),
        strategy_id=str(row["strategy_id"]),
        equity=float(row["equity"]),
        cash=float(row["cash"]),
        portfolio_value=float(row["portfolio_value"]),
        daily_pnl=float(row["daily_pnl"]),
        positions=list(_load_json(row["positions_json"])),
    )


def _backtest_equity_snapshot_from_row(row: sqlite3.Row) -> BacktestEquitySnapshotEvent:
    return BacktestEquitySnapshotEvent(
        id=int(row["id"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        session_id=str(row["session_id"]),
        strategy_id=str(row["strategy_id"]),
        equity=float(row["equity"]),
        cash=float(row["cash"]),
        portfolio_value=float(row["portfolio_value"]),
        daily_pnl=(None if row["daily_pnl"] is None else float(row["daily_pnl"])),
        positions=list(_load_json(row["positions_json"])),
        backtest_run_id=(None if row["backtest_run_id"] is None else int(row["backtest_run_id"])),
    )


def _set_wal_mode(connection: sqlite3.Connection) -> None:
    """Put the database in WAL mode, tolerating a concurrent-setup race.

    ``PRAGMA journal_mode=WAL`` does **not** honour ``busy_timeout``: it
    needs a moment with no other connection touching the database, so
    when several ``EventStore`` instances are constructed at once on a
    fresh DB they collide and all but one get ``database is locked``.

    Journal mode is a *persistent, database-level* setting (stored in the
    file header) — once any single connection sets WAL it stays WAL for
    every connection thereafter. So a transient failure here is benign as
    long as *someone* succeeds: we retry briefly, and if the mode is
    already WAL (a peer won the race) we stop. We only raise if we can
    neither set nor observe WAL after the bounded retry, which would
    indicate a genuinely stuck database rather than a startup race.
    """
    deadline = time.monotonic() + 30.0
    last_error: sqlite3.OperationalError | None = None
    while True:
        try:
            row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if row is not None and str(row[0]).lower() == "wal":
                return
        except sqlite3.OperationalError as exc:
            last_error = exc
        # Did a peer already switch the persistent mode to WAL?
        try:
            current = connection.execute("PRAGMA journal_mode").fetchone()
            if current is not None and str(current[0]).lower() == "wal":
                return
        except sqlite3.OperationalError as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            if last_error is not None:
                raise last_error
            raise sqlite3.OperationalError(
                "Could not establish WAL journal mode within the startup retry window."
            )
        time.sleep(0.05)


def _split_sql_statements(sql: str) -> list[str]:
    """Split a migration file into individual executable statements.

    Uses :func:`sqlite3.complete_statement` — SQLite's own tokenizer — to
    find statement boundaries, so a ``;`` inside a string literal, a
    ``--`` line comment, or a ``/* */`` block comment does **not** split a
    statement incorrectly (the naive ``str.split(';')`` approach is wrong
    for the existing migrations, several of which have ``;`` and ``'`` in
    SQL comments). Empty / comment-only fragments are dropped so
    ``connection.execute`` is never handed a no-op statement.
    """
    statements: list[str] = []
    buffer = ""
    for char in sql:
        buffer += char
        if char == ";" and sqlite3.complete_statement(buffer):
            stripped = buffer.strip()
            if stripped and stripped != ";":
                statements.append(stripped)
            buffer = ""
    tail = buffer.strip()
    if tail and tail != ";":
        # A trailing statement with no terminating ';' (still valid DDL).
        statements.append(tail)
    return statements


def _dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _dump_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _load_json(value: str) -> Any:
    return json.loads(value)
