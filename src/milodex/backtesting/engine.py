"""Backtest engine: replays a strategy day-by-day over historical bars.

The engine rides the **same** execution path the live paper runner uses:
every ``TradeIntent`` emitted by ``Strategy.evaluate()`` is submitted
through :class:`milodex.execution.service.ExecutionService`. Two
dependencies are swapped for the backtest:

- :class:`milodex.broker.simulated.SimulatedBroker` fills at the next
  bar's open (with slippage and commission applied).
- The configured :class:`milodex.risk.RiskPolicy` selects the risk path:
  ``BYPASS`` (the default raw-research mode) injects
  :class:`milodex.risk.NullRiskEvaluator`, while ``ENFORCE`` injects the
  backtest structural evaluator for sizing and exposure constraints.

Order timing: decisions made on bar ``T``'s close are queued and fill at
bar ``T+1``'s open, removing the look-ahead bias of same-bar fills.
Orders pending at the end of the trading window are dropped — there is
no T+1 to execute against — and recorded as skipped backtest audit events.
See PR 2.1 in docs/reviews/backtest-rejection-analysis.md §6 for the
rationale behind not filling them.

The engine still owns cash / position / equity bookkeeping — it
snapshot-injects the broker's reported account and positions at the
top of each day's loop so that intents submitted through
``ExecutionService`` observe consistent state. This is the
data-layer counterpart to the architectural "same strategy code runs
historical and live with no branches" guarantee.
"""

from __future__ import annotations

import bisect
import math
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd
import yaml

from milodex.analytics.snapshots import record_backtest_equity_snapshot
from milodex.backtesting.run_manifest import (
    BacktestRunManifestInput,
    build_backtest_run_manifest,
)
from milodex.broker.models import AccountInfo, OrderSide, Position
from milodex.broker.simulated import SimulatedBroker
from milodex.core.event_store import BacktestRunEvent, EventStore, ExplanationEvent, TradeEvent
from milodex.data.bar_quality import DataQualityError, scan_backtest_bars
from milodex.data.models import BarSet, Timeframe
from milodex.data.simulated import SimulatedDataProvider
from milodex.execution.models import ExecutionStatus, TradeIntent
from milodex.execution.service import ExecutionService
from milodex.execution.state import KillSwitchStateStore
from milodex.risk import (
    BacktestStructuralRiskEvaluator,
    NullRiskEvaluator,
    RiskPolicy,
    load_backtesting_defaults,
)
from milodex.strategies.base import StrategyDecision
from milodex.strategies.loader import LoadedStrategy

if TYPE_CHECKING:
    from milodex.data.provider import DataProvider


class UniverseCoverageError(RuntimeError):
    """Raised by :meth:`BacktestEngine.prefetch_bars` when fewer than the configured
    fraction of declared-universe symbols have bars in the requested window.

    Prevents silent results computed over a tiny subset of the intended universe
    (e.g. NR7/52w-high running on 20 of 97 declared SP100 symbols).
    """


@dataclass
class BacktestResult:
    """Summary returned by :meth:`BacktestEngine.run`."""

    run_id: str
    strategy_id: str
    start_date: date
    end_date: date
    initial_equity: float
    final_equity: float
    total_return_pct: float
    trade_count: int
    buy_count: int
    sell_count: int
    slippage_pct: float
    commission_per_trade: float
    trading_days: int
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    db_id: int | None = None
    round_trip_count: int = 0
    risk_policy: RiskPolicy = RiskPolicy.BYPASS
    skipped_count: int = 0
    data_quality: dict = field(default_factory=dict)
    run_manifest: dict = field(default_factory=dict)


@dataclass
class _SimulationOutput:
    """Raw outputs from a single simulation sweep over a list of trading days.

    Intentionally narrower than :class:`BacktestResult`: it carries only the
    window-local bookkeeping so the walk-forward runner can stitch multiple
    sweeps together without the engine pre-computing a full ``BacktestResult``
    per window.
    """

    equity_curve: list[tuple[date, float]]
    trade_count: int
    buy_count: int
    sell_count: int
    final_equity: float
    round_trip_count: int = 0
    skipped_count: int = 0


@dataclass
class _PendingOrder:
    """An intent emitted on bar T close, awaiting fill at bar T+1's open."""

    intent: TradeIntent
    decision_day: date
    reasoning: object


@dataclass
class _IntradayPendingOrder:
    """An intent emitted at intraday decision time T, awaiting fill at the next bar's open.

    Sibling to :class:`_PendingOrder` for intraday simulation. Uses a full
    UTC timestamp instead of a date so the drain loop can match against
    per-timestamp open-price maps.
    """

    intent: TradeIntent
    decision_timestamp: pd.Timestamp  # full UTC timestamp, not a date
    reasoning: object  # carried for audit / no-action recording


def _barset_has_bar_in_range(barset: BarSet, start_date: date, end_date: date) -> bool:
    """Return whether ``barset`` contains at least one row in the requested window."""
    if len(barset) == 0:
        return False

    df = barset._df_view()  # noqa: SLF001 — read-only internal helper
    if df.empty or "timestamp" not in df.columns:
        return False

    timestamps = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    in_range = timestamps.dt.date.between(start_date, end_date)
    return bool(in_range.any())


class BacktestEngine:
    """Replay a loaded strategy over historical bar data.

    Args:
        loaded: Strategy + config produced by :class:`~milodex.strategies.loader.StrategyLoader`.
        data_provider: Market data source (used only to prefetch bars for the run window).
        event_store: Persistent ledger for backtest runs and simulated trades.
        initial_equity: Starting simulated account equity in USD.
        slippage_pct: Per-trade fill slippage as a fraction (e.g. ``0.0005`` = 5 bps).
            Defaults to the value in the strategy config's ``backtest.slippage_pct``.
        commission_per_trade: Fixed commission deducted per executed trade in USD.
            Defaults to the value in the strategy config's ``backtest.commission_per_trade``.
        risk_defaults_path: Path to the global ``risk_defaults.yaml``.  Used as
            tier-2 fallback for ``min_universe_coverage_pct`` when the
            per-strategy config does not specify an override.  Defaults to
            ``configs/risk_defaults.yaml`` relative to the current working
            directory.  The file is read once and the result cached on first
            call to :meth:`prefetch_bars`.
    """

    def __init__(
        self,
        *,
        loaded: LoadedStrategy,
        data_provider: DataProvider,
        event_store: EventStore,
        initial_equity: float = 100_000.0,
        slippage_pct: float | None = None,
        commission_per_trade: float | None = None,
        risk_defaults_path: Path | None = None,
        risk_policy: RiskPolicy = RiskPolicy.BYPASS,
    ) -> None:
        self._loaded = loaded
        self._data_provider = data_provider
        self._event_store = event_store
        self._initial_equity = initial_equity
        self._risk_defaults_path: Path = risk_defaults_path or Path("configs/risk_defaults.yaml")
        self._risk_policy = risk_policy
        # Populated lazily on first call to _load_backtesting_defaults; avoids
        # re-reading on every walk-forward window.
        self._backtesting_defaults: dict | None = None
        self._slippage_pct = self._resolve_slippage_pct(slippage_pct)
        self._commission = (
            commission_per_trade
            if commission_per_trade is not None
            else float(loaded.config.backtest.get("commission_per_trade", 0.0))
        )

    @property
    def risk_policy(self) -> RiskPolicy:
        """Return the risk policy used for simulated order submissions."""
        return self._risk_policy

    @property
    def walk_forward_windows(self) -> int:
        """Number of OOS windows configured for walk-forward runs.

        Reads ``backtest.walk_forward_windows`` from the strategy config,
        defaulting to 4. Callers that need to derive window spans should use
        :func:`milodex.backtesting.walk_forward_runner.derive_walk_forward_spans`
        rather than reading this property and calling the splitter directly.
        """
        return int(self._loaded.config.backtest.get("walk_forward_windows", 4))

    def run(
        self,
        start_date: date,
        end_date: date,
        *,
        run_id: str | None = None,
    ) -> BacktestResult:
        """Run the backtest and return a :class:`BacktestResult`.

        Writes ``backtest_runs``, ``explanations``, and ``trades`` rows to the
        event store as it executes.  The status is set to ``'running'`` at the
        start and updated to ``'completed'`` or ``'failed'`` at the end.
        """
        if end_date < start_date:
            msg = "end_date must be on or after start_date"
            raise ValueError(msg)

        effective_run_id = run_id or str(uuid.uuid4())
        started_at = datetime.now(tz=UTC)

        # Reconcile any prior backtest_runs row for this strategy still left
        # in status='running' with ended_at=NULL by an engine that died
        # without writing its close-out (parquet 0-byte cache crash, OOM,
        # kill -9, machine sleep). Must precede the append below — otherwise
        # the WHERE clause would sweep up our own freshly-inserted row.
        self._event_store.reconcile_orphan_backtest_runs(
            strategy_id=self._loaded.config.strategy_id,
            ended_at=started_at,
            status="orphan_recovered",
        )

        db_run_id = self._event_store.append_backtest_run(
            BacktestRunEvent(
                run_id=effective_run_id,
                strategy_id=self._loaded.config.strategy_id,
                config_path=str(self._loaded.config.path),
                config_hash=self._loaded.context.config_hash,
                start_date=datetime.combine(start_date, datetime.min.time(), tzinfo=UTC),
                end_date=datetime.combine(end_date, datetime.min.time(), tzinfo=UTC),
                started_at=started_at,
                status="running",
                slippage_pct=self._slippage_pct,
                commission_per_trade=self._commission,
                metadata={"risk_policy": self._risk_policy.value},
            )
        )

        try:
            result = self._execute(
                start_date=start_date,
                end_date=end_date,
                run_id=effective_run_id,
                db_run_id=db_run_id,
            )
        except DataQualityError as exc:
            data_quality = exc.report.to_dict()
            self._event_store.update_backtest_run_metadata(
                effective_run_id,
                metadata=self._metadata_with_run_manifest(
                    effective_run_id,
                    start_date=start_date,
                    end_date=end_date,
                    initial_equity=self._initial_equity,
                    data_quality=data_quality,
                ),
            )
            self._event_store.update_backtest_run_status(
                effective_run_id,
                status="failed",
                ended_at=datetime.now(tz=UTC),
            )
            raise
        except Exception:
            self._event_store.update_backtest_run_status(
                effective_run_id,
                status="failed",
                ended_at=datetime.now(tz=UTC),
            )
            raise

        self._event_store.update_backtest_run_status(
            effective_run_id,
            status="completed",
            ended_at=datetime.now(tz=UTC),
        )
        self._event_store.update_backtest_run_metadata(
            effective_run_id,
            metadata={
                "initial_equity": result.initial_equity,
                "final_equity": result.final_equity,
                "total_return_pct": result.total_return_pct,
                "trade_count": result.trade_count,
                "skipped_count": result.skipped_count,
                "trading_days": result.trading_days,
                "equity_curve": [[d.isoformat(), v] for d, v in result.equity_curve],
                "risk_policy": self._risk_policy.value,
                "data_quality": result.data_quality,
                "run_manifest": result.run_manifest,
            },
        )
        return result

    # ------------------------------------------------------------------
    # Private execution core
    # ------------------------------------------------------------------

    def prefetch_bars(
        self,
        start_date: date,
        end_date: date,
        *,
        timeframe: Timeframe = Timeframe.DAY_1,
    ) -> dict[str, BarSet]:
        """Fetch bars for the universe over ``[start_date - warmup, end_date]``.

        Exposed so the walk-forward runner can fetch once and re-use across
        windows, avoiding N×warmup fetches for N windows.

        Raises :class:`UniverseCoverageError` when fewer than the configured
        fraction of declared-universe symbols have bars inside the requested
        run window.  An empty barset, absent symbol, or warmup-only barset
        counts as missing for coverage purposes.

        Threshold resolution order (first match wins):
        1. ``loaded.config.risk["min_universe_coverage_pct"]`` — per-strategy
           override in the strategy YAML's ``risk:`` section.
        2. ``configs/risk_defaults.yaml`` ``backtesting.min_universe_coverage_pct``
           — global default read once and cached.
        3. Hardcoded fallback ``0.80``.

        Args:
            timeframe: bar granularity to fetch. Defaults to DAY_1 for
                backwards compatibility with daily-strategy callers. Intraday
                backtests pass MINUTE_5 / MINUTE_15 / HOUR_1 etc.
        """
        universe = list(self._loaded.context.universe)
        if not universe:
            msg = "Strategy must resolve a non-empty universe before backtesting."
            raise ValueError(msg)
        warmup_start = start_date - timedelta(days=self._warmup_calendar_days())
        bars = self._data_provider.get_bars(
            symbols=universe,
            timeframe=timeframe,
            start=warmup_start,
            end=end_date,
        )

        covered = [
            s
            for s in universe
            if s in bars and _barset_has_bar_in_range(bars[s], start_date, end_date)
        ]
        coverage = len(covered) / len(universe)
        threshold = self._resolve_coverage_threshold()
        if coverage < threshold:
            missing = sorted(set(universe) - set(covered))
            shown = missing[:10]
            suffix = "..." if len(missing) > 10 else ""
            msg = (
                f"Universe coverage {coverage:.1%} < {threshold:.1%} "
                f"({len(covered)}/{len(universe)} symbols available in requested window). "
                f"Missing: {shown}{suffix}"
            )
            raise UniverseCoverageError(msg)

        return bars

    def _resolve_coverage_threshold(self) -> float:
        """Return the effective ``min_universe_coverage_pct`` threshold.

        Checks tiers in order: per-strategy risk config → global risk_defaults.yaml
        backtesting section → hardcoded 0.80 fallback.
        """
        # Tier 1: per-strategy override.
        strategy_value = self._loaded.config.risk.get("min_universe_coverage_pct")
        if strategy_value is not None:
            return float(strategy_value)

        # Tier 2: global risk_defaults.yaml (read once, cached).
        defaults = self._load_backtesting_defaults()
        global_value = defaults.get("min_universe_coverage_pct")
        if global_value is not None:
            return float(global_value)

        # Tier 3: hardcoded fallback.
        return 0.80

    def _load_backtesting_defaults(self) -> dict:
        """Return the ``backtesting`` section of ``risk_defaults.yaml`` (cached)."""
        if self._backtesting_defaults is None:
            if self._risk_defaults_path.exists():
                self._backtesting_defaults = load_backtesting_defaults(self._risk_defaults_path)
            else:
                self._backtesting_defaults = {}
        return self._backtesting_defaults

    def _resolve_slippage_pct(self, override: float | None) -> float:
        """Return the effective slippage fraction using a 4-tier resolution.

        Resolution order (first defined value wins):

        1. Call-site override passed to ``__init__`` as ``slippage_pct``.
        2. Per-strategy config: ``strategy.backtest.slippage_pct`` in the YAML.
        3. Universe manifest: ``universe.slippage_pct`` in the matching
           ``universe_*.yaml`` (resolved via the strategy's ``universe_ref``).
        4. Global default: ``backtesting.slippage_pct_default`` in
           ``risk_defaults.yaml``.
        5. Hardcoded fallback: 0.0005 (5 bps).
        """
        # Tier 1: explicit call-site override.
        if override is not None:
            return float(override)

        # Tier 2: per-strategy config value.
        strat_value = self._loaded.config.backtest.get("slippage_pct")
        if strat_value is not None:
            return float(strat_value)

        # Tier 3: universe manifest value.
        universe_value = self._resolve_universe_slippage()
        if universe_value is not None:
            return float(universe_value)

        # Tier 4: global risk_defaults.yaml value.
        defaults = self._load_backtesting_defaults()
        global_value = defaults.get("slippage_pct_default")
        if global_value is not None:
            return float(global_value)

        # Tier 5: hardcoded fallback.
        return 0.0005

    def _resolve_universe_slippage(self) -> float | None:
        """Look up ``slippage_pct`` from the universe manifest referenced by this strategy.

        Scans ``universe_*.yaml`` files in the same directory as the strategy
        config, matching on ``universe.id == context.universe_ref``.  Returns
        ``None`` when the strategy has no ``universe_ref`` (inline universe) or
        when the matched manifest carries no ``slippage_pct`` field.
        """
        universe_ref = self._loaded.context.universe_ref
        if not universe_ref:
            return None

        config_path = Path(self._loaded.context.config_path)
        configs_dir = config_path.parent
        for manifest_path in sorted(configs_dir.glob("universe_*.yaml")):
            try:
                with manifest_path.open("r", encoding="utf-8") as handle:
                    data = yaml.safe_load(handle)
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict):
                continue
            universe = data.get("universe")
            if not isinstance(universe, dict):
                continue
            if str(universe.get("id", "")) != universe_ref:
                continue
            slippage = universe.get("slippage_pct")
            if slippage is not None:
                return float(slippage)
            return None  # matched but no slippage_pct field
        return None

    def simulate_window(
        self,
        *,
        all_bars: dict[str, BarSet],
        trading_days: list[date],
        db_run_id: int,
        session_id: str,
        initial_equity: float | None = None,
    ) -> _SimulationOutput:
        """Run the simulation loop on ``trading_days`` using ``all_bars``.

        Intended for the walk-forward runner, which owns bar prefetch and
        window splitting. Each call resets equity, positions, and entry-state
        to a fresh start; persistence (trades, explanations) flows through the
        caller-provided ``db_run_id`` so all windows from one walk-forward
        invocation land under the same parent ``BacktestRunEvent``.
        ``session_id`` distinguishes windows within a single parent run.
        """
        from milodex.data.timeframes import timeframe_from_bar_size

        equity = initial_equity if initial_equity is not None else self._initial_equity
        _timeframe = timeframe_from_bar_size(self._loaded.config.tempo["bar_size"])
        return self._simulate(
            all_bars=all_bars,
            trading_days=trading_days,
            db_run_id=db_run_id,
            session_id=session_id,
            initial_equity=equity,
            timeframe=_timeframe,
        )

    def _execute(
        self,
        *,
        start_date: date,
        end_date: date,
        run_id: str,
        db_run_id: int,
    ) -> BacktestResult:
        from milodex.data.timeframes import timeframe_from_bar_size

        _bar_size = self._loaded.config.tempo["bar_size"]
        _timeframe = timeframe_from_bar_size(_bar_size)
        all_bars = self.prefetch_bars(start_date, end_date, timeframe=_timeframe)
        data_quality = self._scan_data_quality(all_bars, start_date, end_date)
        run_manifest = self._build_run_manifest(
            start_date=start_date,
            end_date=end_date,
            initial_equity=self._initial_equity,
            data_quality=data_quality,
        )

        trading_days = _trading_days_in_range(all_bars, start_date, end_date)
        if not trading_days:
            return BacktestResult(
                run_id=run_id,
                strategy_id=self._loaded.config.strategy_id,
                start_date=start_date,
                end_date=end_date,
                initial_equity=self._initial_equity,
                final_equity=self._initial_equity,
                total_return_pct=0.0,
                trade_count=0,
                buy_count=0,
                sell_count=0,
                slippage_pct=self._slippage_pct,
                commission_per_trade=self._commission,
                trading_days=0,
                equity_curve=[],
                db_id=db_run_id,
                risk_policy=self._risk_policy,
                skipped_count=0,
                data_quality=data_quality,
                run_manifest=run_manifest,
            )

        output = self._simulate(
            all_bars=all_bars,
            trading_days=trading_days,
            db_run_id=db_run_id,
            session_id=run_id,
            initial_equity=self._initial_equity,
            timeframe=_timeframe,
        )
        total_return = (output.final_equity - self._initial_equity) / self._initial_equity
        return BacktestResult(
            run_id=run_id,
            strategy_id=self._loaded.config.strategy_id,
            start_date=start_date,
            end_date=end_date,
            initial_equity=self._initial_equity,
            final_equity=output.final_equity,
            total_return_pct=total_return * 100.0,
            trade_count=output.trade_count,
            buy_count=output.buy_count,
            sell_count=output.sell_count,
            slippage_pct=self._slippage_pct,
            commission_per_trade=self._commission,
            trading_days=len(trading_days),
            equity_curve=output.equity_curve,
            db_id=db_run_id,
            round_trip_count=output.round_trip_count,
            risk_policy=self._risk_policy,
            skipped_count=output.skipped_count,
            data_quality=data_quality,
            run_manifest=run_manifest,
        )

    def _scan_data_quality(
        self, all_bars: dict[str, BarSet], start_date: date, end_date: date
    ) -> dict:
        report = scan_backtest_bars(
            all_bars,
            requested_start=start_date,
            requested_end=end_date,
        )
        if report.blocker_count:
            raise DataQualityError(report)
        return report.to_dict()

    def _build_run_manifest(
        self,
        *,
        start_date: date,
        end_date: date,
        initial_equity: float,
        data_quality: dict,
    ) -> dict:
        return build_backtest_run_manifest(
            BacktestRunManifestInput(
                loaded=self._loaded,
                data_provider=self._data_provider,
                requested_start=start_date,
                requested_end=end_date,
                warmup_start=start_date - timedelta(days=self._warmup_calendar_days()),
                risk_policy=self._risk_policy.value,
                slippage_pct=self._slippage_pct,
                commission_per_trade=self._commission,
                initial_equity=initial_equity,
                data_quality=data_quality,
                coverage_threshold=self._resolve_coverage_threshold(),
            )
        )

    def _metadata_with_run_manifest(
        self,
        run_id: str,
        *,
        start_date: date,
        end_date: date,
        initial_equity: float,
        data_quality: dict,
    ) -> dict:
        run_record = self._event_store.get_backtest_run(run_id)
        metadata = dict(run_record.metadata) if run_record is not None else {}
        metadata["data_quality"] = data_quality
        metadata["run_manifest"] = self._build_run_manifest(
            start_date=start_date,
            end_date=end_date,
            initial_equity=initial_equity,
            data_quality=data_quality,
        )
        return metadata

    def _simulate(
        self,
        *,
        all_bars: dict[str, BarSet],
        trading_days: list[date],
        db_run_id: int,
        session_id: str,
        initial_equity: float,
        timeframe: Timeframe,
    ) -> _SimulationOutput:
        """Dispatch to the daily or intraday simulation path based on timeframe.

        Branches on the Timeframe enum so the dispatch is consistent with the
        value already passed to prefetch_bars — no risk of string-literal drift.
        """
        if timeframe == Timeframe.DAY_1:
            return self._simulate_daily(
                all_bars=all_bars,
                trading_days=trading_days,
                db_run_id=db_run_id,
                session_id=session_id,
                initial_equity=initial_equity,
                timeframe=timeframe,
            )
        return self._simulate_intraday(
            all_bars=all_bars,
            trading_days=trading_days,
            db_run_id=db_run_id,
            session_id=session_id,
            initial_equity=initial_equity,
            timeframe=timeframe,
        )

    def _simulate_daily(
        self,
        *,
        all_bars: dict[str, BarSet],
        trading_days: list[date],
        db_run_id: int,
        session_id: str,
        initial_equity: float,
        timeframe: Timeframe,  # accepted for signature symmetry; unused in body
    ) -> _SimulationOutput:
        universe = list(self._loaded.context.universe)
        if not universe:
            msg = "Strategy must resolve a non-empty universe before backtesting."
            raise ValueError(msg)
        if not trading_days:
            return _SimulationOutput(
                equity_curve=[],
                trade_count=0,
                buy_count=0,
                sell_count=0,
                final_equity=initial_equity,
                round_trip_count=0,
                skipped_count=0,
            )

        sim_broker = SimulatedBroker(
            slippage_pct=self._slippage_pct,
            commission_per_trade=self._commission,
        )
        sim_data_provider = SimulatedDataProvider(all_bars)
        execution_service = ExecutionService(
            broker_client=sim_broker,
            data_provider=sim_data_provider,
            kill_switch_store=KillSwitchStateStore(event_store=self._event_store),
            risk_defaults_path=self._risk_defaults_path,
            risk_evaluator=self._build_risk_evaluator(),
            event_store=self._event_store,
            # Explicit backtest marker so the ENFORCE path
            # (BacktestStructuralRiskEvaluator) is recognised as a backtest —
            # it must not bind the historical replay to live
            # trading-mode/env or a live frozen-manifest lookup. The BYPASS
            # path (NullRiskEvaluator) already short-circuits regardless.
            is_backtest=True,
        )

        # Fix #1: pre-parse all timestamps to date objects once (O(symbols×bars));
        # subsequent per-day slicing uses binary search (O(log bars)) instead of
        # O(bars) re-parse.
        ts_index = _build_ts_date_index(all_bars)

        cash = initial_equity
        positions: dict[str, tuple[float, float]] = {}
        entry_state: dict[str, dict] = {}

        equity_curve: list[tuple[date, float]] = []
        buy_count = 0
        sell_count = 0
        trade_count = 0
        skipped_count = 0
        # Per-symbol fill counters used to compute round_trip_count at end.
        # round_trip_count = sum(min(s["buys"], s["sells"]) for s in _sym_fills.values())
        _sym_fills: dict[str, dict[str, int]] = {}
        pending: list[_PendingOrder] = []

        for day in trading_days:
            for sym in entry_state:
                entry_state[sym]["held_days"] = int(entry_state[sym]["held_days"]) + 1

            bars_by_symbol = _slice_bars_to_day(all_bars, day, ts_index)
            fill_opens = _opens_on_day(bars_by_symbol, day, ts_index)
            latest_closes = _latest_closes(bars_by_symbol)

            # Drain any orders enqueued on the previous decision day. Fills
            # happen at TODAY's open — see module docstring. The drain runs
            # BEFORE the primary-symbol bar check so pending orders for any
            # symbol with bars on `day` fill on schedule, even when the
            # primary universe symbol has no bars (multi-symbol universes
            # with mismatched calendars).
            if pending:
                cash, drained_buys, drained_sells, drained_skips = self._drain_pending(
                    pending=pending,
                    opens=fill_opens,
                    cash=cash,
                    positions=positions,
                    entry_state=entry_state,
                    sim_broker=sim_broker,
                    sim_data_provider=sim_data_provider,
                    execution_service=execution_service,
                    day=day,
                    session_id=session_id,
                    db_run_id=db_run_id,
                    sym_fills=_sym_fills,
                )
                buy_count += drained_buys
                sell_count += drained_sells
                trade_count += drained_buys + drained_sells
                skipped_count += drained_skips
                pending = []

            # Gate on whether ANY universe symbol has a bar today, not just
            # universe[0].  Multi-symbol universes can have mismatched holiday
            # calendars: a day where universe[0] is absent but another symbol
            # is active must still be evaluated so the equity curve has no
            # holes and signals on the active symbols are not silently skipped.
            # Single-symbol behaviour is unchanged: if the only symbol has no
            # bar, none of the universe symbols do, so we still skip.
            any_symbol_active = any(
                bars_by_symbol.get(sym) is not None and len(bars_by_symbol[sym]) > 0
                for sym in universe
            )
            if not any_symbol_active:
                continue

            # For strategy.evaluate() the primary_bars argument is the bars
            # for universe[0] up to today.  When universe[0] has no bars yet
            # (e.g. newly listed symbol, or mismatched holiday calendars) the
            # day is still kept live so strategies that read
            # ``context.bars_by_symbol`` (e.g. meanrev, breakout) still
            # evaluate and equity is still recorded.  Strategies that consume
            # ``bars`` DIRECTLY (e.g. the regime strategy:
            # ``bars.to_dataframe()`` → moving average → position sizing) must
            # NOT be handed a different symbol's barset as a stand-in: doing so
            # would make them compute a confidently-wrong risk-on/off decision
            # and position size from the wrong symbol's prices (e.g. SHY, a
            # bond ETF, substituting for an absent SPY), corrupting the equity
            # curve and every gate/analytics number derived from it.  Instead
            # feed an empty primary barset so a direct-``bars`` consumer
            # correctly returns no_signal (its insufficient-history guard
            # trips), exactly as it would for genuinely missing primary
            # history.
            primary_symbol_bars = bars_by_symbol.get(universe[0])
            primary_symbol_present = (
                primary_symbol_bars is not None and len(primary_symbol_bars) > 0
            )
            if primary_symbol_present:
                primary_bars = primary_symbol_bars
            else:
                primary_bars = _empty_barset()

            equity = _compute_equity(cash, positions, latest_closes)
            self._sync_broker_state(
                sim_broker=sim_broker,
                sim_data_provider=sim_data_provider,
                day=day,
                closes=latest_closes,
                cash=cash,
                equity=equity,
                positions=positions,
            )

            decision = self._evaluate_strategy(
                primary_bars=primary_bars,
                bars_by_symbol=bars_by_symbol,
                equity=equity,
                positions=positions,
                entry_state=entry_state,
            )
            intents = decision.intents

            if not intents:
                # The no-action audit row is anchored on universe[0]'s own
                # bar (symbol + close).  When universe[0] is absent there is
                # no honest primary bar to quote, and borrowing another
                # symbol's close onto a row labelled ``symbol=universe[0]``
                # would be a misleading audit entry — so skip the no-action
                # record for that day.  This matches master's behaviour: on
                # master a universe[0]-absent day was skipped entirely and
                # produced no record either.  The day still stays live:
                # equity is recorded below and strategies that DO emit
                # intents (via context.bars_by_symbol) still queue them.
                if primary_symbol_present:
                    latest_bar = primary_bars.latest()
                    execution_service.record_no_action(
                        strategy_name=self._loaded.config.strategy_id,
                        strategy_stage=self._loaded.config.stage,
                        strategy_config_path=self._loaded.config.path,
                        config_hash=self._loaded.context.config_hash,
                        symbol=universe[0],
                        latest_bar_timestamp=latest_bar.timestamp,
                        latest_bar_close=latest_bar.close,
                        session_id=session_id,
                        reasoning=decision.reasoning,
                        submitted_by="backtest_engine",
                        backtest_run_id=db_run_id,
                    )
            else:
                for intent in intents:
                    pending.append(
                        _PendingOrder(
                            intent=intent,
                            decision_day=day,
                            reasoning=decision.reasoning,
                        )
                    )

            # Mark-to-market at end of day. Equity reflects the post-decision
            # state, but since fills are deferred to the next bar, no cash or
            # position movement happens between decision and end of day.
            equity_curve.append((day, equity))

        final_equity = equity_curve[-1][1] if equity_curve else initial_equity

        if pending and trading_days:
            last_day = trading_days[-1]
            last_bars = _slice_bars_to_day(all_bars, last_day, ts_index)
            last_closes = _latest_closes(last_bars)
            final_equity_for_skips = _compute_equity(cash, positions, last_closes)
            for p in pending:
                sym = p.intent.normalized_symbol()
                latest_price = last_closes.get(sym)
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_no_next_bar",
                    message=(
                        f"Skipped backtest {p.intent.side.value} for {sym}: "
                        "no next bar available before run end."
                    ),
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=last_day,
                    cash=cash,
                    equity=final_equity_for_skips,
                    latest_price=latest_price,
                    unit_price=latest_price,
                    context={
                        "decision_day": p.decision_day.isoformat(),
                        "reason_code": "backtest_no_next_bar",
                    },
                )
                skipped_count += 1
            pending = []

        # Final broker re-sync so the simulated account reflects post-buy state,
        # then record one backtest_equity_snapshots row per simulation (ADR 0053).
        # The snapshot is keyed on `session_id` (= run_id for whole-period,
        # window-id for walk-forward), so analytics can read snapshots
        # independently of the trade ledger. Closes the runner/engine half of
        # the `analytics/snapshots.py` scaffolded surface (R-XC-016).
        if trading_days:
            last_day = trading_days[-1]
            last_bars = _slice_bars_to_day(all_bars, last_day, ts_index)
            last_closes = _latest_closes(last_bars)
            self._sync_broker_state(
                sim_broker=sim_broker,
                sim_data_provider=sim_data_provider,
                day=last_day,
                closes=last_closes,
                cash=cash,
                equity=final_equity,
                positions=positions,
            )
            try:
                record_backtest_equity_snapshot(
                    event_store=self._event_store,
                    broker=sim_broker,
                    session_id=session_id,
                    strategy_id=self._loaded.config.strategy_id,
                    backtest_run_id=db_run_id,
                    recorded_at=_day_to_dt(last_day),
                )
            except Exception:  # noqa: BLE001 — snapshot is best-effort, see ENGINEERING_STANDARDS.md
                pass

        round_trip_count = sum(min(s["buys"], s["sells"]) for s in _sym_fills.values())
        return _SimulationOutput(
            equity_curve=equity_curve,
            trade_count=trade_count,
            buy_count=buy_count,
            sell_count=sell_count,
            final_equity=final_equity,
            round_trip_count=round_trip_count,
            skipped_count=skipped_count,
        )

    def _simulate_intraday(
        self,
        *,
        all_bars: dict[str, BarSet],
        trading_days: list[date],
        db_run_id: int,
        session_id: str,
        initial_equity: float,
        timeframe: Timeframe,  # accepted for signature symmetry; may be used in Phase E
    ) -> _SimulationOutput:
        """Intraday simulation path. Implemented in Phase E.

        See docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md.
        """
        raise NotImplementedError(
            "Intraday backtest engine is not yet implemented for this branch. "
            "See docs/superpowers/plans/2026-05-20-intraday-backtest-engine.md."
        )

    def _drain_pending(
        self,
        *,
        pending: list[_PendingOrder],
        opens: dict[str, float],
        cash: float,
        positions: dict[str, tuple[float, float]],
        entry_state: dict[str, dict],
        sim_broker: SimulatedBroker,
        sim_data_provider: SimulatedDataProvider,
        execution_service: ExecutionService,
        day: date,
        session_id: str,
        db_run_id: int,
        sym_fills: dict[str, dict[str, int]],
    ) -> tuple[float, int, int, int]:
        """Fill ``pending`` orders at today's opens. SELLs first to free cash.

        Mutates ``positions`` and ``entry_state`` in place; returns the new
        cash balance, per-side fill counts, and skipped-order count.

        The simulated broker's ``set_simulation_day`` carries the prices used
        for fills, so we pass ``opens`` as the broker's "current closes" for
        the duration of the drain. The strategy phase later overwrites the
        broker's state with actual closes for mark-to-market.
        """
        sells = [p for p in pending if p.intent.side is OrderSide.SELL]
        buys = [p for p in pending if p.intent.side is OrderSide.BUY]

        equity_pre_drain = _compute_equity(cash, positions, opens)
        self._sync_broker_state(
            sim_broker=sim_broker,
            sim_data_provider=sim_data_provider,
            day=day,
            closes=opens,
            cash=cash,
            equity=equity_pre_drain,
            positions=positions,
        )

        sell_count = 0
        skipped_count = 0
        for p in sells:
            sym = p.intent.symbol.upper()
            if sym not in positions:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_sell_without_position",
                    message=f"Skipped backtest sell for {sym}: no open position.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=equity_pre_drain,
                    latest_price=opens.get(sym),
                    unit_price=opens.get(sym),
                    context={"reason_code": "backtest_sell_without_position"},
                )
                skipped_count += 1
                continue
            latest_open = opens.get(sym)
            if latest_open is None:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_missing_next_open",
                    message=f"Skipped backtest sell for {sym}: missing next open price.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=equity_pre_drain,
                    latest_price=None,
                    unit_price=None,
                    context={"reason_code": "backtest_missing_next_open"},
                )
                skipped_count += 1
                continue
            if not math.isfinite(latest_open) or latest_open <= 0:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_invalid_next_open",
                    message=f"Skipped backtest sell for {sym}: invalid next open price.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=equity_pre_drain,
                    latest_price=latest_open,
                    unit_price=latest_open,
                    context={"reason_code": "backtest_invalid_next_open"},
                )
                skipped_count += 1
                continue

            qty, _ = positions[sym]
            decorated = self._decorate_intent(p.intent, quantity_override=qty)
            result = execution_service.submit_backtest(
                decorated,
                session_id=session_id,
                backtest_run_id=db_run_id,
                reasoning=p.reasoning,
            )
            if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                continue

            fill_price = float(result.order.filled_avg_price or 0.0)
            proceeds = fill_price * qty - self._commission
            cash += proceeds
            del positions[sym]
            entry_state.pop(sym, None)
            sell_count += 1
            sym_fills.setdefault(sym, {"buys": 0, "sells": 0})["sells"] += 1

        # Re-sync after sells so BUY affordability checks reflect freed cash.
        intermediate_equity = _compute_equity(cash, positions, opens)
        self._sync_broker_state(
            sim_broker=sim_broker,
            sim_data_provider=sim_data_provider,
            day=day,
            closes=opens,
            cash=cash,
            equity=intermediate_equity,
            positions=positions,
        )

        buy_count = 0
        for p in buys:
            sym = p.intent.symbol.upper()
            if sym in positions:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_duplicate_position",
                    message=f"Skipped backtest buy for {sym}: position already open.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=intermediate_equity,
                    latest_price=opens.get(sym),
                    unit_price=opens.get(sym),
                    context={"reason_code": "backtest_duplicate_position"},
                )
                skipped_count += 1
                continue
            qty = float(p.intent.quantity)
            if qty <= 0:
                continue
            latest_open = opens.get(sym)
            if latest_open is None:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_missing_next_open",
                    message=f"Skipped backtest buy for {sym}: missing next open price.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=intermediate_equity,
                    latest_price=None,
                    unit_price=None,
                    context={"reason_code": "backtest_missing_next_open"},
                )
                skipped_count += 1
                continue
            if not math.isfinite(latest_open) or latest_open <= 0:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_invalid_next_open",
                    message=f"Skipped backtest buy for {sym}: invalid next open price.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=intermediate_equity,
                    latest_price=latest_open,
                    unit_price=latest_open,
                    context={"reason_code": "backtest_invalid_next_open"},
                )
                skipped_count += 1
                continue
            projected_fill = latest_open * (1.0 + self._slippage_pct)
            cost = projected_fill * qty + self._commission
            if cash < cost:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_insufficient_cash",
                    message=f"Skipped backtest buy for {sym}: insufficient cash.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=intermediate_equity,
                    latest_price=latest_open,
                    unit_price=projected_fill,
                    context={
                        "reason_code": "backtest_insufficient_cash",
                        "cash": cash,
                        "projected_cost": cost,
                    },
                )
                skipped_count += 1
                continue

            decorated = self._decorate_intent(p.intent)
            result = execution_service.submit_backtest(
                decorated,
                session_id=session_id,
                backtest_run_id=db_run_id,
                reasoning=p.reasoning,
            )
            if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                continue

            fill_price = float(result.order.filled_avg_price or 0.0)
            realized_cost = fill_price * qty + self._commission
            cash -= realized_cost
            positions[sym] = (qty, fill_price)
            entry_state[sym] = {"entry_price": fill_price, "held_days": 0}
            buy_count += 1
            sym_fills.setdefault(sym, {"buys": 0, "sells": 0})["buys"] += 1

        return cash, buy_count, sell_count, skipped_count

    def _drain_pending_at_timestamp(
        self,
        *,
        pending: list[_IntradayPendingOrder],
        opens: dict[str, float],
        cash: float,
        positions: dict[str, tuple[float, float]],
        entry_state: dict[str, dict],
        sim_broker: SimulatedBroker,
        sim_data_provider: SimulatedDataProvider,
        execution_service: ExecutionService,
        timestamp: pd.Timestamp,
        session_id: str,
        db_run_id: int,
        sym_fills: dict[str, dict[str, int]],
    ) -> tuple[float, int, int, int, list[_IntradayPendingOrder]]:
        """Drain pending intraday orders that have an open price available at this timestamp.

        **Category 1 — missing-bar (new intraday semantic):** if a symbol is absent from
        ``opens`` at this timestamp the order stays in ``remaining_pending``.
        ``skipped`` is NOT incremented and ``_record_skipped_order`` is NOT called.

        **Category 2 — true rejection (mirrors daily path):** every other failure
        (sell without position, invalid open price, insufficient cash, execution-service
        rejection) is counted as skipped and audited via ``_record_skipped_order``,
        exactly as ``_drain_pending`` does.

        Returns:
            (cash, buy_count, sell_count, skipped_count, remaining_pending)
        """
        day = timestamp.date()

        # Separate Category-1 orders (symbol not yet in opens) from those we must process.
        remaining: list[_IntradayPendingOrder] = []
        to_process: list[_IntradayPendingOrder] = []
        for order in pending:
            if order.intent.symbol.upper() not in opens:
                remaining.append(order)
            else:
                to_process.append(order)

        if not to_process:
            return cash, 0, 0, 0, remaining

        # Mirror daily _drain_pending: SELLs first to free cash, then BUYs.
        sells = [p for p in to_process if p.intent.side is OrderSide.SELL]
        buys = [p for p in to_process if p.intent.side is OrderSide.BUY]

        equity_pre_drain = _compute_equity(cash, positions, opens)
        self._sync_broker_state(
            sim_broker=sim_broker,
            sim_data_provider=sim_data_provider,
            day=day,
            closes=opens,
            cash=cash,
            equity=equity_pre_drain,
            positions=positions,
        )

        sell_count = 0
        skipped_count = 0
        for p in sells:
            sym = p.intent.symbol.upper()
            if sym not in positions:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_sell_without_position",
                    message=f"Skipped backtest sell for {sym}: no open position.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=equity_pre_drain,
                    latest_price=opens.get(sym),
                    unit_price=opens.get(sym),
                    context={"reason_code": "backtest_sell_without_position"},
                )
                skipped_count += 1
                continue
            latest_open = opens[sym]  # partition guarantees key exists
            if not math.isfinite(latest_open) or latest_open <= 0:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_invalid_next_open",
                    message=f"Skipped backtest sell for {sym}: invalid next open price.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=equity_pre_drain,
                    latest_price=latest_open,
                    unit_price=latest_open,
                    context={"reason_code": "backtest_invalid_next_open"},
                )
                skipped_count += 1
                continue

            qty, _ = positions[sym]
            decorated = self._decorate_intent(p.intent, quantity_override=qty)
            result = execution_service.submit_backtest(
                decorated,
                session_id=session_id,
                backtest_run_id=db_run_id,
                reasoning=p.reasoning,
            )
            if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                continue

            fill_price = float(result.order.filled_avg_price or 0.0)
            proceeds = fill_price * qty - self._commission
            cash += proceeds
            del positions[sym]
            entry_state.pop(sym, None)
            sell_count += 1
            sym_fills.setdefault(sym, {"buys": 0, "sells": 0})["sells"] += 1

        # Re-sync after sells so BUY affordability checks reflect freed cash.
        intermediate_equity = _compute_equity(cash, positions, opens)
        self._sync_broker_state(
            sim_broker=sim_broker,
            sim_data_provider=sim_data_provider,
            day=day,
            closes=opens,
            cash=cash,
            equity=intermediate_equity,
            positions=positions,
        )

        buy_count = 0
        for p in buys:
            sym = p.intent.symbol.upper()
            if sym in positions:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_duplicate_position",
                    message=f"Skipped backtest buy for {sym}: position already open.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=intermediate_equity,
                    latest_price=opens.get(sym),
                    unit_price=opens.get(sym),
                    context={"reason_code": "backtest_duplicate_position"},
                )
                skipped_count += 1
                continue
            qty = float(p.intent.quantity)
            if qty <= 0:
                # Silent drop: mirrors daily _drain_pending behavior. Not counted as skipped.
                continue
            latest_open = opens[sym]  # partition guarantees key exists
            if not math.isfinite(latest_open) or latest_open <= 0:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_invalid_next_open",
                    message=f"Skipped backtest buy for {sym}: invalid next open price.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=intermediate_equity,
                    latest_price=latest_open,
                    unit_price=latest_open,
                    context={"reason_code": "backtest_invalid_next_open"},
                )
                skipped_count += 1
                continue
            projected_fill = latest_open * (1.0 + self._slippage_pct)
            cost = projected_fill * qty + self._commission
            if cash < cost:
                self._record_skipped_order(
                    intent=p.intent,
                    reason_code="backtest_insufficient_cash",
                    message=f"Skipped backtest buy for {sym}: insufficient cash.",
                    session_id=session_id,
                    db_run_id=db_run_id,
                    day=day,
                    cash=cash,
                    equity=intermediate_equity,
                    latest_price=latest_open,
                    unit_price=projected_fill,
                    context={
                        "reason_code": "backtest_insufficient_cash",
                        "cash": cash,
                        "projected_cost": cost,
                    },
                )
                skipped_count += 1
                continue

            decorated = self._decorate_intent(p.intent)
            result = execution_service.submit_backtest(
                decorated,
                session_id=session_id,
                backtest_run_id=db_run_id,
                reasoning=p.reasoning,
            )
            if result.status is not ExecutionStatus.SUBMITTED or result.order is None:
                continue

            fill_price = float(result.order.filled_avg_price or 0.0)
            realized_cost = fill_price * qty + self._commission
            cash -= realized_cost
            positions[sym] = (qty, fill_price)
            entry_state[sym] = {"entry_price": fill_price, "held_days": 0}
            buy_count += 1
            sym_fills.setdefault(sym, {"buys": 0, "sells": 0})["buys"] += 1

        return cash, buy_count, sell_count, skipped_count, remaining

    def _record_skipped_order(
        self,
        *,
        intent: TradeIntent,
        reason_code: str,
        message: str,
        session_id: str,
        db_run_id: int,
        day: date,
        cash: float,
        equity: float,
        latest_price: float | None,
        unit_price: float | None,
        context: dict[str, object],
    ) -> None:
        decorated = self._decorate_intent(intent)
        recorded_at = _day_to_dt(day)
        estimated_unit_price = 0.0 if unit_price is None else float(unit_price)
        estimated_order_value = estimated_unit_price * decorated.quantity
        explanation_id = self._event_store.append_explanation(
            ExplanationEvent(
                recorded_at=recorded_at,
                decision_type="backtest_skip",
                status="skipped",
                strategy_name=self._loaded.config.strategy_id,
                strategy_stage=self._loaded.config.stage,
                strategy_config_path=str(self._loaded.config.path),
                config_hash=self._loaded.context.config_hash,
                symbol=decorated.normalized_symbol(),
                side=decorated.side.value,
                quantity=decorated.quantity,
                order_type=decorated.order_type.value,
                time_in_force=decorated.time_in_force.value,
                submitted_by="backtest_engine",
                market_open=True,
                latest_bar_timestamp=recorded_at,
                latest_bar_close=latest_price,
                account_equity=equity,
                account_cash=cash,
                account_portfolio_value=equity,
                account_daily_pnl=0.0,
                risk_allowed=True,
                risk_summary="Backtest order skipped before execution.",
                reason_codes=[reason_code],
                risk_checks=[],
                context={"message": message, **context},
                session_id=session_id,
                backtest_run_id=db_run_id,
            )
        )
        self._event_store.append_trade(
            TradeEvent(
                explanation_id=explanation_id,
                recorded_at=recorded_at,
                status="skipped",
                source="backtest",
                symbol=decorated.normalized_symbol(),
                side=decorated.side.value,
                quantity=decorated.quantity,
                order_type=decorated.order_type.value,
                time_in_force=decorated.time_in_force.value,
                estimated_unit_price=estimated_unit_price,
                estimated_order_value=estimated_order_value,
                strategy_name=self._loaded.config.strategy_id,
                strategy_stage=self._loaded.config.stage,
                strategy_config_path=str(self._loaded.config.path),
                submitted_by="backtest_engine",
                broker_order_id=None,
                broker_status=None,
                message=message,
                session_id=session_id,
                backtest_run_id=db_run_id,
            )
        )

    def _decorate_intent(
        self, intent: TradeIntent, *, quantity_override: float | None = None
    ) -> TradeIntent:
        """Attach strategy + submitter metadata to a bare strategy intent.

        Mirrors what :class:`milodex.strategies.runner.StrategyRunner`
        does for the paper path, so the recorded trade rows carry the
        same provenance whether they came from a live session or a
        backtest replay.
        """
        return TradeIntent(
            symbol=intent.symbol,
            side=intent.side,
            quantity=float(quantity_override if quantity_override is not None else intent.quantity),
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            strategy_config_path=self._loaded.config.path,
            submitted_by="backtest_engine",
            expected_stage=self._loaded.config.stage,
            expected_max_positions=self._strategy_risk_int("max_positions"),
            expected_max_position_pct=self._strategy_risk_float("max_position_pct"),
            expected_daily_loss_cap_pct=self._strategy_risk_float("daily_loss_cap_pct"),
        )

    def _build_risk_evaluator(self):
        if self._risk_policy is RiskPolicy.BYPASS:
            return NullRiskEvaluator()
        if self._risk_policy is RiskPolicy.ENFORCE:
            return BacktestStructuralRiskEvaluator()
        msg = f"Unsupported backtest risk policy: {self._risk_policy}"
        raise ValueError(msg)

    def _strategy_risk_float(self, key: str) -> float | None:
        risk = getattr(self._loaded.config, "risk", None)
        if not isinstance(risk, dict):
            return None
        value = risk.get(key)
        return None if value is None else float(value)

    def _strategy_risk_int(self, key: str) -> int | None:
        risk = getattr(self._loaded.config, "risk", None)
        if not isinstance(risk, dict):
            return None
        value = risk.get(key)
        return None if value is None else int(value)

    def _sync_broker_state(
        self,
        *,
        sim_broker: SimulatedBroker,
        sim_data_provider: SimulatedDataProvider,
        day: date,
        closes: dict[str, float],
        cash: float,
        equity: float,
        positions: dict[str, tuple[float, float]],
    ) -> None:
        day_dt = _day_to_dt(day)
        sim_broker.set_simulation_day(day=day_dt, closes=closes)
        sim_data_provider.set_simulation_day(day)
        sim_broker.update_account(
            AccountInfo(
                equity=equity,
                cash=cash,
                buying_power=cash,
                portfolio_value=equity,
                daily_pnl=0.0,
            )
        )
        reported_positions = []
        for sym, (qty, entry_price) in positions.items():
            current_price = closes.get(sym, entry_price)
            reported_positions.append(
                Position(
                    symbol=sym,
                    quantity=qty,
                    avg_entry_price=entry_price,
                    current_price=current_price,
                    market_value=current_price * qty,
                    unrealized_pnl=(current_price - entry_price) * qty,
                    unrealized_pnl_pct=(
                        0.0 if entry_price == 0 else (current_price - entry_price) / entry_price
                    ),
                )
            )
        sim_broker.set_positions(reported_positions)

    def _evaluate_strategy(
        self,
        *,
        primary_bars: BarSet,
        bars_by_symbol: dict[str, BarSet],
        equity: float,
        positions: dict[str, tuple[float, float]],
        entry_state: dict[str, dict],
    ) -> StrategyDecision:
        """Build the strategy context, call evaluate(), and return the full StrategyDecision.

        Shared between _simulate_daily and (in Phase E) _simulate_intraday. Pure
        extraction — no behavior change.

        ``equity`` is the total portfolio mark-to-market value (cash + open positions),
        not raw cash balance.
        """
        context = replace(
            self._loaded.context,
            positions={sym: qty for sym, (qty, _) in positions.items()},
            equity=equity,
            bars_by_symbol=bars_by_symbol,
            entry_state=entry_state,
        )
        return self._loaded.strategy.evaluate(primary_bars, context)

    def _warmup_calendar_days(self) -> int:
        """Return the number of calendar days to prepend before the run window.

        Resolution order:

        1. ``strategy.max_lookback_periods()`` — if the concrete strategy
           declares a non-zero value, convert it to calendar days using a
           1.4× trading-to-calendar multiplier (5 trading days / 7 calendar,
           rounded up generously) plus a 30-day buffer for holiday variation.
           This covers strategies whose lookback is expressed as a float param
           or nested in a sub-dict — cases that the integer-param heuristic
           (step 2) cannot reach.
        2. Integer-param heuristic — scan ``config.parameters`` values, take
           the largest *numeric whole-number* value (int or float-that-is-whole),
           multiply by 3 to convert trading periods to calendar days, floor at
           365.  ``bool`` values are excluded (they are ``int`` subclasses but
           not lookback periods).
        """
        # Step 1: strategy-declared maximum lookback.
        declared = self._loaded.strategy.max_lookback_periods()
        if declared > 0:
            # 1.4 calendar days per trading day (= 7/5), rounded up, plus 30-day buffer.
            return math.ceil(declared * 1.4) + 30

        # Step 2: heuristic — scan parameter values for the largest numeric
        # whole-number (covers both int and float like 200.0).  Exclude booleans
        # (bool is a subclass of int in Python) and negative/zero values.
        # Whole-number constraint keeps fractional multipliers (e.g. 2.0 meaning
        # "2× ATR") from being mis-interpreted as lookback periods; the 365-day
        # floor is the safety net for strategies with small integer params.
        numeric_params = [
            int(v)
            for v in self._loaded.config.parameters.values()
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and v > 0
            and float(v) == int(v)
        ]
        largest = max(numeric_params, default=30)
        return max(365, largest * 3)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_ts_date_index(all_bars: dict[str, BarSet]) -> dict[str, list[date]]:
    """Pre-parse each symbol's timestamps into a sorted list of ``date`` objects.

    Computed once per simulation run; shared across all per-day slicing calls
    so that timestamp parsing is O(symbols × total_bars) rather than
    O(days × symbols × total_bars).
    """
    index: dict[str, list[date]] = {}
    for sym, barset in all_bars.items():
        df = barset._df_view()  # noqa: SLF001 — read-only internal helper
        if df.empty or "timestamp" not in df.columns:
            index[sym] = []
            continue
        dates = pd.to_datetime(df["timestamp"], utc=True).dt.date.tolist()
        index[sym] = dates  # already monotone-ascending from cache
    return index


def _trading_days_in_range(
    all_bars: dict[str, BarSet], start_date: date, end_date: date
) -> list[date]:
    """Return sorted unique trading days present in bar data within [start, end].

    Vectorized: parses each symbol's ts column once via dt.date, no Python loop
    over individual timestamps.
    """
    days: set[date] = set()
    for barset in all_bars.values():
        df = barset._df_view()  # noqa: SLF001 — read-only internal helper
        if df.empty or "timestamp" not in df.columns:
            continue
        dates = pd.to_datetime(df["timestamp"], utc=True).dt.date
        days.update(d for d in dates if start_date <= d <= end_date)
    return sorted(days)


def _empty_barset() -> BarSet:
    """An empty, schema-valid BarSet.

    Handed to ``strategy.evaluate()`` as ``primary_bars`` on a day where
    ``universe[0]`` has no bar.  A direct-``bars`` consumer (e.g. the regime
    strategy) hits its insufficient-history guard and returns no_signal
    rather than computing a decision from a substituted wrong symbol's
    prices.  Strategies that read ``context.bars_by_symbol`` ignore this
    argument and still evaluate normally.
    """
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": pd.Series([], dtype="datetime64[ns, UTC]"),
                "open": pd.Series([], dtype="float64"),
                "high": pd.Series([], dtype="float64"),
                "low": pd.Series([], dtype="float64"),
                "close": pd.Series([], dtype="float64"),
                "volume": pd.Series([], dtype="int64"),
                "vwap": pd.Series([], dtype="float64"),
            }
        )
    )


def _slice_bars_to_day(
    all_bars: dict[str, BarSet],
    day: date,
    ts_index: dict[str, list[date]] | None = None,
) -> dict[str, BarSet]:
    """Return a dict of BarSets each truncated to bars on or before ``day``.

    When ``ts_index`` is supplied (pre-parsed date lists from
    :func:`_build_ts_date_index`), slicing is O(log(total_bars)) per symbol
    via binary search rather than O(total_bars) per-day re-parse.  Falls back
    to the parse-each-call path when ``ts_index`` is absent.
    """
    result: dict[str, BarSet] = {}
    for sym, barset in all_bars.items():
        df = barset._df_view()  # noqa: SLF001 — read-only internal helper
        if df.empty:
            continue
        if ts_index is not None:
            dates = ts_index.get(sym, [])
            # bisect_right gives the insertion point *after* all dates == day.
            cut = bisect.bisect_right(dates, day)
            if cut == 0:
                continue
            sliced = df.iloc[:cut]
        else:
            timestamps = pd.to_datetime(df["timestamp"], utc=True)
            mask = timestamps.dt.date <= day
            sliced = df.loc[mask]
        if not sliced.empty:
            result[sym] = BarSet(sliced.reset_index(drop=True))
    return result


def _latest_closes(bars_by_symbol: dict[str, BarSet]) -> dict[str, float]:
    closes: dict[str, float] = {}
    for sym, barset in bars_by_symbol.items():
        df = barset._df_view()  # noqa: SLF001 — read-only internal helper
        if not df.empty:
            closes[sym] = float(df["close"].iloc[-1])
    return closes


def _latest_opens(bars_by_symbol: dict[str, BarSet]) -> dict[str, float]:
    """Return the latest bar's *open* price per symbol.

    Mirror of :func:`_latest_closes` for the T+1 fill model: pending orders
    enqueued on bar T's close need the open of the next bar (already the
    last bar in ``bars_by_symbol`` once it has been sliced to the current
    simulation day).
    """
    opens: dict[str, float] = {}
    for sym, barset in bars_by_symbol.items():
        df = barset._df_view()  # noqa: SLF001 — read-only internal helper
        if not df.empty:
            opens[sym] = float(df["open"].iloc[-1])
    return opens


def _opens_on_day(
    bars_by_symbol: dict[str, BarSet],
    day: date,
    ts_index: dict[str, list[date]] | None = None,
) -> dict[str, float]:
    """Return each symbol's open only when it has a bar exactly on ``day``."""
    opens: dict[str, float] = {}
    for sym, barset in bars_by_symbol.items():
        df = barset._df_view()  # noqa: SLF001 — read-only internal helper
        if df.empty or "timestamp" not in df.columns:
            continue
        if ts_index is not None:
            dates = ts_index.get(sym, [])
            # Find the last occurrence of ``day`` via binary search.
            hi = bisect.bisect_right(dates, day)
            lo = bisect.bisect_left(dates, day)
            if lo == hi:
                continue  # no bar on this exact day
            opens[sym] = float(df["open"].iloc[hi - 1])
        else:
            timestamps = pd.to_datetime(df["timestamp"], utc=True)
            mask = timestamps.dt.date == day
            rows = df.loc[mask]
            if not rows.empty:
                opens[sym] = float(rows["open"].iloc[-1])
    return opens


def _compute_equity(
    cash: float,
    positions: dict[str, tuple[float, float]],
    latest_closes: dict[str, float],
) -> float:
    market_value = sum(
        qty * latest_closes.get(sym, entry_p) for sym, (qty, entry_p) in positions.items()
    )
    return cash + market_value


def _day_to_dt(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)


def _build_intraday_event_timeline(
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    day: date,
    bar_size_minutes: int,
) -> list[tuple[pd.Timestamp, dict[str, Any]]]:
    """Return the chronological event timeline for one trading day.

    Each entry is ``(timestamp, metadata)`` where ``metadata`` carries:
    - ``fill_symbols``: list of symbols with a bar at ``bar_timestamp == timestamp``
    - ``decision_symbols``: list of symbols with a bar at ``decision_time == timestamp``

    The timeline is the chronological union of fill events (bar starts) and
    decision events (bar completions) for all universe symbols whose bars
    fall in ``day``. See spec §3 component #3.

    Args:
        per_symbol_ts_utc: precomputed UTC-tz-aware DatetimeIndex per symbol.
            The Phase E ``_simulate_intraday`` builds these once at the top
            of the simulation to avoid redundant ``to_dataframe()`` calls
            inside the event loop (Correction 6).
        day: the trading day to scope the timeline to (UTC dates may differ
            from ET dates due to timezone; the helper filters on the UTC
            timestamp's ``.date()`` value).
        bar_size_minutes: bar size in minutes, used to compute
            ``decision_time = bar_timestamp + bar_size``.
    """
    bar_size = pd.Timedelta(minutes=bar_size_minutes)
    fill_map: dict[pd.Timestamp, list[str]] = {}
    decision_map: dict[pd.Timestamp, list[str]] = {}

    for symbol, ts_index in per_symbol_ts_utc.items():
        for bar_ts in ts_index:
            if bar_ts.date() != day:
                continue
            fill_map.setdefault(bar_ts, []).append(symbol)
            decision_ts = bar_ts + bar_size
            decision_map.setdefault(decision_ts, []).append(symbol)

    all_event_times = sorted(set(fill_map.keys()) | set(decision_map.keys()))
    return [
        (
            t,
            {
                "fill_symbols": fill_map.get(t, []),
                "decision_symbols": decision_map.get(t, []),
            },
        )
        for t in all_event_times
    ]


def _opens_at_timestamp(
    per_symbol_open_by_ts: dict[str, dict[pd.Timestamp, float]],
    timestamp: pd.Timestamp,
) -> dict[str, float]:
    """Return ``{symbol: open_price}`` for symbols with a bar at ``timestamp``.

    Symbols without a bar at ``timestamp`` are not in the result. This means
    the caller can safely iterate the returned dict and trust each key has
    a fill price.

    Args:
        per_symbol_open_by_ts: precomputed nested dict mapping symbol →
            {bar_timestamp: open_price}. The Phase E ``_simulate_intraday``
            builds this once at simulation start to avoid DataFrame scans
            inside the event loop (Correction 6).
        timestamp: UTC-tz-aware Timestamp to look up.
    """
    opens: dict[str, float] = {}
    for symbol, open_by_ts in per_symbol_open_by_ts.items():
        if timestamp in open_by_ts:
            opens[symbol] = open_by_ts[timestamp]
    return opens


def _mark_to_market_at_day_end(
    positions: dict[str, tuple[float, float]],
    per_symbol_df: dict[str, pd.DataFrame],
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    day: date,
    cash: float,
) -> float:
    """Return end-of-day equity = cash + sum(qty * latest_close_for_symbol_on_day).

    Uses each symbol's latest available close at or before the day's final
    timestamp. Critical for multi-symbol universes where one symbol may
    be missing the final bar of the day — falls back to prior day's last
    close.

    Args:
        positions: {symbol: (qty, avg_cost)} for open positions.
        per_symbol_df: precomputed OHLCV DataFrame per symbol. Must contain
            a "close" column.
        per_symbol_ts_utc: precomputed UTC-tz-aware DatetimeIndex per symbol,
            aligned row-for-row with ``per_symbol_df``.
        day: trading day to mark to.
        cash: current cash balance.

    Returns:
        Equity = cash + sum of (qty × latest close on day, or prior close
        if no bars on day).
    """
    equity = cash
    for symbol, (qty, _avg_cost) in positions.items():
        if symbol not in per_symbol_df:
            continue
        df = per_symbol_df[symbol]
        ts_utc = per_symbol_ts_utc[symbol]
        date_array = ts_utc.date  # numpy array of date objects
        day_indices = np.flatnonzero(date_array == day)
        if len(day_indices) > 0:
            latest_close = float(df["close"].iloc[day_indices[-1]])
        else:
            prior_indices = np.flatnonzero(date_array < day)
            if len(prior_indices) == 0:
                continue
            latest_close = float(df["close"].iloc[prior_indices[-1]])
        equity += qty * latest_close
    return equity


def _advance_cursors(
    cursors: dict[str, int],
    per_symbol_ts_utc: dict[str, pd.DatetimeIndex],
    timestamp: pd.Timestamp,
    bar_size_minutes: int,
) -> bool:
    """Advance ``cursors[symbol]`` for each symbol whose next-unconsumed bar
    has ``decision_time <= timestamp``. Return True if any cursor advanced.

    Cursor invariant: ``cursor[symbol]`` is the EXCLUSIVE end index of the
    symbol's visible bar history. Visible = ``df.iloc[:cursor[symbol]]``.

    Args:
        cursors: per-symbol exclusive-end-index map. MUTATED IN PLACE.
        per_symbol_ts_utc: precomputed UTC-tz-aware DatetimeIndex per symbol
            (Correction 6).
        timestamp: the event timestamp to advance cursors up to.
        bar_size_minutes: bar size in minutes (decision_time = bar_ts + bar_size).

    Returns:
        True if any cursor advanced; False otherwise. Phase E uses this to
        decide whether to call _evaluate_strategy.
    """
    bar_size = pd.Timedelta(minutes=bar_size_minutes)
    advanced = False
    for symbol, ts_index in per_symbol_ts_utc.items():
        idx = cursors.get(symbol, 0)
        n = len(ts_index)
        while idx < n:
            bar_ts = ts_index[idx]
            decision_time = bar_ts + bar_size
            if decision_time <= timestamp:
                idx += 1
                advanced = True
            else:
                break
        cursors[symbol] = idx
    return advanced
