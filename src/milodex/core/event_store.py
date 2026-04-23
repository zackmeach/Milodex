"""SQLite-backed event store for durable execution records."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExplanationEvent:
    """Explanation record for a preview or submit decision."""

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
    """Daily portfolio snapshot row (equity, cash, positions)."""

    recorded_at: datetime
    session_id: str
    strategy_id: str
    equity: float
    cash: float
    portfolio_value: float
    daily_pnl: float
    positions: list[dict[str, Any]]
    id: int | None = None


class EventStore:
    """Append-only SQLite event store with forward-only migrations."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._apply_migrations()

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

    def append_explanation(self, event: ExplanationEvent) -> int:
        with self._connect() as connection:
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
                    session_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def append_trade(self, event: TradeEvent) -> int:
        with self._connect() as connection:
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
                    event.explanation_id,
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
            connection.commit()
            return int(cursor.lastrowid)

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

    def list_explanations(self) -> list[ExplanationEvent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM explanations ORDER BY id ASC").fetchall()
        return [_explanation_from_row(row) for row in rows]

    def list_trades(self) -> list[TradeEvent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM trades ORDER BY id ASC").fetchall()
        return [_trade_from_row(row) for row in rows]

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

    def update_backtest_run_metadata(self, run_id: str, *, metadata: dict[str, Any]) -> None:
        """Replace the metadata JSON blob for a backtest run."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE backtest_runs SET metadata_json = ? WHERE run_id = ?",
                (_dump_json(metadata), run_id),
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

    def get_latest_promotion_for_strategy(self, strategy_id: str) -> PromotionEvent | None:
        """Return the most recent promotion for ``strategy_id``, or None."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM promotions WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
                (strategy_id,),
            ).fetchone()
        return None if row is None else _promotion_from_row(row)

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

    def _apply_migrations(self) -> None:
        migrations = self._load_migrations()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_version (
                    version INTEGER NOT NULL
                )
                """
            )
            current_version = self._get_schema_version(connection)
            for version, sql in migrations:
                if version <= current_version:
                    continue
                connection.executescript(sql)
                connection.execute("DELETE FROM _schema_version")
                connection.execute(
                    "INSERT INTO _schema_version(version) VALUES (?)",
                    (version,),
                )
                current_version = version
            connection.commit()

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
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _explanation_from_row(row: sqlite3.Row) -> ExplanationEvent:
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


def _dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _dump_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _load_json(value: str) -> Any:
    return json.loads(value)
