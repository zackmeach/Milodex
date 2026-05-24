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

The simulation kernel owns cash / position / equity bookkeeping and
snapshot-injects the broker's reported account and positions during
historical replay so that intents submitted through ``ExecutionService``
observe consistent state. This is the data-layer counterpart to the
architectural "same strategy code runs historical and live with no branches"
guarantee.
"""

from __future__ import annotations

import bisect
import math
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import yaml

from milodex.backtesting.intraday_simulation import (
    _advance_cursors,
    _build_intraday_event_timeline,
    _build_visible_bars,
    _latest_close_at_ts,
    _mark_to_market_at_day_end,
    _opens_at_timestamp,
)
from milodex.backtesting.run_manifest import (
    BacktestRunManifestInput,
    build_backtest_run_manifest,
)
from milodex.backtesting.simulation_kernel import (
    BacktestSimulationKernel,
    IntradayPendingOrder,
    MissingOpenPolicy,
    PendingOrder,
)
from milodex.backtesting.simulation_kernel import (
    compute_equity as _compute_equity,
)
from milodex.backtesting.simulation_kernel import (
    day_to_dt as _day_to_dt,
)
from milodex.core.event_store import BacktestRunEvent, EventStore
from milodex.data.bar_quality import DataQualityError, scan_backtest_bars
from milodex.data.models import BarSet, Timeframe
from milodex.data.timeframes import (
    bar_size_minutes_from_timeframe,
    timeframe_from_bar_size,
)
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


@dataclass(frozen=True)
class BacktestRunHandle:
    """Durable parent-run identifiers returned by engine lifecycle helpers."""

    run_id: str
    db_id: int
    started_at: datetime


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

    The engine owns the strategy/data/equity lifecycle and the daily vs
    intraday simulation dispatch. Shared simulation mechanics — pending-order
    drain, broker sync, fill bookkeeping, skipped-order audit, and equity
    snapshot policy — are delegated to
    :class:`~milodex.backtesting.simulation_kernel.BacktestSimulationKernel`
    (RM-013) so daily and intraday paths cannot drift on those rules. The
    public ``simulate_window()`` entrypoint is the contract walk-forward
    orchestration calls (RM-005a); private engine attributes are no longer
    reached into from walk-forward modules.

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

    @property
    def strategy_id(self) -> str:
        """Strategy identifier for public orchestration helpers."""
        return self._loaded.config.strategy_id

    @property
    def strategy_family(self) -> str:
        """Strategy family for screening and lifecycle-exemption logic."""
        return self._loaded.context.family

    @property
    def universe(self) -> tuple[str, ...]:
        """Declared strategy universe."""
        return tuple(self._loaded.context.universe)

    @property
    def universe_ref(self) -> str | None:
        """Optional universe manifest reference from the strategy config."""
        return self._loaded.context.universe_ref

    @property
    def config_path(self) -> Path:
        """Path to the loaded strategy config."""
        return self._loaded.config.path

    @property
    def bar_size(self) -> str:
        """Configured strategy bar size, e.g. ``"1D"`` or ``"5Min"``."""
        return str(self._loaded.config.tempo["bar_size"])

    @property
    def initial_equity(self) -> float:
        """Initial equity configured for this backtest engine."""
        return self._initial_equity

    def warmup_calendar_days(self) -> int:
        """Calendar days of warmup data needed before the requested start."""
        return self._warmup_calendar_days()

    def min_trades_required(self) -> int:
        """Configured paper-promotion trade floor for this strategy."""
        return int(self._loaded.config.backtest.get("min_trades_required", 30))

    def start_walk_forward_parent_run(
        self,
        *,
        run_id: str | None,
        start_date: date,
        end_date: date,
        windows_planned: int,
    ) -> BacktestRunHandle:
        """Append the durable parent row for a walk-forward invocation."""
        effective_run_id = run_id or str(uuid.uuid4())
        started_at = datetime.now(tz=UTC)

        self._event_store.reconcile_orphan_backtest_runs(
            strategy_id=self.strategy_id,
            ended_at=started_at,
            status="orphan_recovered",
        )

        db_run_id = self._event_store.append_backtest_run(
            BacktestRunEvent(
                run_id=effective_run_id,
                strategy_id=self.strategy_id,
                config_path=str(self._loaded.config.path),
                config_hash=self._loaded.context.config_hash,
                start_date=datetime.combine(start_date, datetime.min.time(), tzinfo=UTC),
                end_date=datetime.combine(end_date, datetime.min.time(), tzinfo=UTC),
                started_at=started_at,
                status="running",
                slippage_pct=self._slippage_pct,
                commission_per_trade=self._commission,
                metadata={
                    "walk_forward": True,
                    "windows_planned": windows_planned,
                    "risk_policy": self._risk_policy.value,
                },
            )
        )
        return BacktestRunHandle(
            run_id=effective_run_id,
            db_id=db_run_id,
            started_at=started_at,
        )

    def scan_backtest_data_quality(
        self,
        all_bars: dict[str, BarSet],
        start_date: date,
        end_date: date,
    ) -> dict:
        """Return the backtest data-quality report for a prefetched bar set."""
        return self._scan_data_quality(all_bars, start_date, end_date)

    def build_backtest_run_manifest(
        self,
        *,
        start_date: date,
        end_date: date,
        initial_equity: float,
        data_quality: dict,
    ) -> dict:
        """Build the reproducibility manifest for a backtest invocation."""
        return self._build_run_manifest(
            start_date=start_date,
            end_date=end_date,
            initial_equity=initial_equity,
            data_quality=data_quality,
        )

    def backtest_run_metadata_with_manifest(
        self,
        run_id: str,
        *,
        start_date: date,
        end_date: date,
        initial_equity: float,
        data_quality: dict,
    ) -> dict:
        """Merge current run metadata with data-quality and manifest fields."""
        return self._metadata_with_run_manifest(
            run_id,
            start_date=start_date,
            end_date=end_date,
            initial_equity=initial_equity,
            data_quality=data_quality,
        )

    def update_backtest_run_metadata(self, run_id: str, *, metadata: dict[str, Any]) -> None:
        """Replace the metadata blob for an existing backtest run."""
        self._event_store.update_backtest_run_metadata(run_id, metadata=metadata)

    def merge_backtest_run_metadata(self, run_id: str, *, updates: dict[str, Any]) -> None:
        """Merge small metadata updates into a backtest run if the row exists."""
        persisted = self._event_store.get_backtest_run(run_id)
        if persisted is None:
            return
        merged_metadata = {**persisted.metadata, **updates}
        self._event_store.update_backtest_run_metadata(run_id, metadata=merged_metadata)

    def mark_backtest_run_failed(self, run_id: str, *, ended_at: datetime | None = None) -> None:
        """Mark a durable backtest run as failed."""
        self._event_store.update_backtest_run_status(
            run_id,
            status="failed",
            ended_at=ended_at or datetime.now(tz=UTC),
        )

    def complete_walk_forward_run(
        self,
        run_id: str,
        *,
        metadata: dict[str, Any],
        ended_at: datetime | None = None,
    ) -> None:
        """Mark a walk-forward parent run complete and persist final metadata."""
        self._event_store.update_backtest_run_status(
            run_id,
            status="completed",
            ended_at=ended_at or datetime.now(tz=UTC),
        )
        self._event_store.update_backtest_run_metadata(run_id, metadata=metadata)

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

    def _new_simulation_kernel(
        self,
        *,
        all_bars: dict[str, BarSet],
        initial_equity: float,
    ) -> BacktestSimulationKernel:
        return BacktestSimulationKernel(
            event_store=self._event_store,
            all_bars=all_bars,
            strategy_id=self._loaded.config.strategy_id,
            strategy_stage=self._loaded.config.stage,
            strategy_config_path=self._loaded.config.path,
            config_hash=self._loaded.context.config_hash,
            risk_defaults_path=self._risk_defaults_path,
            risk_evaluator=self._build_risk_evaluator(),
            slippage_pct=self._slippage_pct,
            commission_per_trade=self._commission,
            initial_cash=initial_equity,
            max_positions=self._strategy_risk_int("max_positions"),
            max_position_pct=self._strategy_risk_float("max_position_pct"),
            daily_loss_cap_pct=self._strategy_risk_float("daily_loss_cap_pct"),
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

        kernel = self._new_simulation_kernel(
            all_bars=all_bars,
            initial_equity=initial_equity,
        )

        # Fix #1: pre-parse all timestamps to date objects once (O(symbols×bars));
        # subsequent per-day slicing uses binary search (O(log bars)) instead of
        # O(bars) re-parse.
        ts_index = _build_ts_date_index(all_bars)

        equity_curve: list[tuple[date, float]] = []
        buy_count = 0
        sell_count = 0
        trade_count = 0
        skipped_count = 0
        pending: list[PendingOrder] = []

        for day in trading_days:
            for sym in kernel.entry_state:
                kernel.entry_state[sym]["held_days"] = int(kernel.entry_state[sym]["held_days"]) + 1

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
                drain = kernel.drain_pending_orders(
                    pending=pending,
                    opens=fill_opens,
                    day=day,
                    session_id=session_id,
                    db_run_id=db_run_id,
                    missing_open_policy=MissingOpenPolicy.SKIP,
                )
                buy_count += drain.buy_count
                sell_count += drain.sell_count
                trade_count += drain.buy_count + drain.sell_count
                skipped_count += drain.skipped_count
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

            equity = _compute_equity(kernel.cash, kernel.positions, latest_closes)
            kernel.sync_broker_state(
                day=day,
                closes=latest_closes,
                equity=equity,
            )

            decision = self._evaluate_strategy(
                primary_bars=primary_bars,
                bars_by_symbol=bars_by_symbol,
                equity=equity,
                positions=kernel.positions,
                entry_state=kernel.entry_state,
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
                    kernel.record_no_action(
                        symbol=universe[0],
                        latest_bar_timestamp=latest_bar.timestamp,
                        latest_bar_close=latest_bar.close,
                        session_id=session_id,
                        reasoning=decision.reasoning,
                        db_run_id=db_run_id,
                    )
            else:
                for intent in intents:
                    pending.append(
                        PendingOrder(
                            intent=intent,
                            reasoning=decision.reasoning,
                            decision_day=day,
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
            skipped_count += kernel.record_stranded_orders(
                pending=pending,
                day=last_day,
                latest_closes=last_closes,
                session_id=session_id,
                db_run_id=db_run_id,
            )
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
            kernel.sync_broker_state(
                day=last_day,
                closes=last_closes,
                equity=final_equity,
            )
            kernel.record_final_snapshot(
                session_id=session_id,
                db_run_id=db_run_id,
                recorded_at=_day_to_dt(last_day),
            )

        round_trip_count = kernel.round_trip_count()
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
        timeframe: Timeframe,
    ) -> _SimulationOutput:
        """Intraday simulation path (Phase E).

        Decision/fill model: a strategy decision made at bar T's close is
        queued as a pending order and fills at bar T+1's open.  Within a day
        the event timeline merges fill events (bar starts) and decision events
        (bar completions) in strict chronological order.  Across days, a SELL
        queued at the final bar of day N fills at day N+1's first fill event
        (the opening bar of day N+1).

        See docs/superpowers/specs/2026-05-20-intraday-backtest-engine-design.md.
        """
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

        bar_size_minutes = bar_size_minutes_from_timeframe(timeframe)

        kernel = self._new_simulation_kernel(
            all_bars=all_bars,
            initial_equity=initial_equity,
        )

        # ------------------------------------------------------------------
        # Correction 6: precompute per-symbol lookup maps once.
        # Never call to_dataframe() or pd.to_datetime() inside the day/event loops.
        # ------------------------------------------------------------------
        per_symbol_df: dict[str, pd.DataFrame] = {}
        per_symbol_ts_utc: dict[str, pd.DatetimeIndex] = {}
        per_symbol_open_by_ts: dict[str, dict[pd.Timestamp, float]] = {}

        for symbol, barset in all_bars.items():
            df = barset.to_dataframe()
            per_symbol_df[symbol] = df
            ts_utc = pd.to_datetime(df["timestamp"], utc=True)
            dti = pd.DatetimeIndex(ts_utc)
            per_symbol_ts_utc[symbol] = dti
            per_symbol_open_by_ts[symbol] = dict(
                zip(dti, df["open"].astype(float).values, strict=True)
            )

        # Initialise cursors: exclusive-end index into each symbol's bar array.
        # cursor[sym] == 0 → no bars visible yet (iloc[:0] is empty).
        cursors: dict[str, int] = {sym: 0 for sym in all_bars}

        equity_curve: list[tuple[date, float]] = []
        buy_count = 0
        sell_count = 0
        trade_count = 0
        skipped_count = 0
        pending: list[IntradayPendingOrder] = []

        for day in trading_days:
            # ------------------------------------------------------------------
            # 1. Held-days accounting (mirror daily path).
            # ------------------------------------------------------------------
            for sym in kernel.entry_state:
                kernel.entry_state[sym]["held_days"] = int(kernel.entry_state[sym]["held_days"]) + 1

            # ------------------------------------------------------------------
            # 2. Build the event timeline for this day.
            # ------------------------------------------------------------------
            timeline = _build_intraday_event_timeline(
                per_symbol_ts_utc=per_symbol_ts_utc,
                day=day,
                bar_size_minutes=bar_size_minutes,
            )

            # ------------------------------------------------------------------
            # 3. Event loop.
            #
            # Ordering: advance → evaluate → drain.
            #
            # When a timestamp T is simultaneously a decision event (bar N-1
            # completes) AND a fill event (bar N opens), advancing cursors and
            # evaluating FIRST ensures that intents emitted at T are eligible
            # to fill at T's opening price — i.e., at bar N's open, which is
            # exactly bar (N-1)'s "T+1 open" as required by the fill model.
            # Draining before evaluation would defer those fills to bar N+1's
            # open (one bar too late), breaking the T+1 fill guarantee.
            # ------------------------------------------------------------------
            for ts, meta in timeline:
                # 3a. Advance cursors: mark newly-completed bars visible.
                advanced = _advance_cursors(cursors, per_symbol_ts_utc, ts, bar_size_minutes)

                # 3b. Evaluate strategy only when at least one cursor advanced.
                if advanced:
                    bars_by_symbol_visible = _build_visible_bars(
                        per_symbol_df=per_symbol_df,
                        cursors=cursors,
                        universe=universe,
                    )
                    if bars_by_symbol_visible:
                        # Primary barset — must not be substituted (see _simulate_daily).
                        primary_bs = bars_by_symbol_visible.get(universe[0])
                        primary_symbol_present = primary_bs is not None and len(primary_bs) > 0
                        if primary_symbol_present:
                            primary_bars = primary_bs
                        else:
                            primary_bars = _empty_barset()

                        # Equity at evaluation time: latest visible close per symbol.
                        latest_closes_at_ts = _latest_close_at_ts(
                            per_symbol_df=per_symbol_df,
                            per_symbol_ts_utc=per_symbol_ts_utc,
                            ts=ts,
                        )
                        equity_at_eval = _compute_equity(
                            kernel.cash,
                            kernel.positions,
                            latest_closes_at_ts,
                        )

                        kernel.sync_broker_state(
                            day=day,
                            closes=latest_closes_at_ts,
                            equity=equity_at_eval,
                        )

                        decision = self._evaluate_strategy(
                            primary_bars=primary_bars,
                            bars_by_symbol=bars_by_symbol_visible,
                            equity=equity_at_eval,
                            positions=kernel.positions,
                            entry_state=kernel.entry_state,
                        )

                        if not decision.intents:
                            # Audit no-action only when primary symbol has visible bars
                            # (same guard as _simulate_daily: no honest primary bar → no record).
                            if primary_symbol_present:
                                latest_bar = primary_bars.latest()
                                kernel.record_no_action(
                                    symbol=universe[0],
                                    latest_bar_timestamp=latest_bar.timestamp,
                                    latest_bar_close=latest_bar.close,
                                    session_id=session_id,
                                    reasoning=decision.reasoning,
                                    db_run_id=db_run_id,
                                )
                        else:
                            for intent in decision.intents:
                                pending.append(
                                    IntradayPendingOrder(
                                        intent=intent,
                                        reasoning=decision.reasoning,
                                        decision_timestamp=ts,
                                    )
                                )

                # 3c. Drain pending orders that have a fill price available at T.
                # Runs after evaluate so that newly-queued intents can fill at T's
                # open (the bar that just started = bar T+1 relative to the decision bar).
                if meta["fill_symbols"] and pending:
                    opens = _opens_at_timestamp(per_symbol_open_by_ts, ts)
                    drain = kernel.drain_pending_orders(
                        pending=pending,
                        opens=opens,
                        day=ts.date(),
                        session_id=session_id,
                        db_run_id=db_run_id,
                        missing_open_policy=MissingOpenPolicy.RETAIN,
                    )
                    buy_count += drain.buy_count
                    sell_count += drain.sell_count
                    trade_count += drain.buy_count + drain.sell_count
                    skipped_count += drain.skipped_count  # Category 2 only
                    pending = drain.remaining

            # ------------------------------------------------------------------
            # 4. Day-end: mark-to-market (equity_curve records EOD value).
            # ------------------------------------------------------------------
            eod_equity = _mark_to_market_at_day_end(
                positions=kernel.positions,
                per_symbol_df=per_symbol_df,
                per_symbol_ts_utc=per_symbol_ts_utc,
                day=day,
                cash=kernel.cash,
            )
            equity_curve.append((day, eod_equity))

        # ------------------------------------------------------------------
        # Post-loop: handle stranded pending orders (mirrors daily path).
        # Per Correction 5: every stranded order is a skipped-audit row.
        # ------------------------------------------------------------------
        final_equity = equity_curve[-1][1] if equity_curve else initial_equity

        if pending and trading_days:
            last_day = trading_days[-1]
            latest_closes_end = _latest_close_at_ts(
                per_symbol_df=per_symbol_df,
                per_symbol_ts_utc=per_symbol_ts_utc,
                ts=None,  # None sentinel → latest close across all time
            )
            skipped_count += kernel.record_stranded_orders(
                pending=pending,
                day=last_day,
                latest_closes=latest_closes_end,
                session_id=session_id,
                db_run_id=db_run_id,
            )
            pending = []

        # ------------------------------------------------------------------
        # Per Correction 5: final broker sync + ADR 0053 snapshot policy.
        # ------------------------------------------------------------------
        if trading_days:
            last_day = trading_days[-1]
            latest_closes_end = _latest_close_at_ts(
                per_symbol_df=per_symbol_df,
                per_symbol_ts_utc=per_symbol_ts_utc,
                ts=None,
            )
            kernel.sync_broker_state(
                day=last_day,
                closes=latest_closes_end,
                equity=final_equity,
            )
            kernel.record_final_snapshot(
                session_id=session_id,
                db_run_id=db_run_id,
                recorded_at=_day_to_dt(last_day),
            )

        round_trip_count = kernel.round_trip_count()
        return _SimulationOutput(
            equity_curve=equity_curve,
            trade_count=trade_count,
            buy_count=buy_count,
            sell_count=sell_count,
            final_equity=final_equity,
            round_trip_count=round_trip_count,
            skipped_count=skipped_count,
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
