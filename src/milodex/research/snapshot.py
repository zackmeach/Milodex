"""Offline, hash-verified IEX research snapshot and fail-closed preflight."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import warnings
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from milodex.data.alpaca_provider import CACHE_VERSION
from milodex.data.models import BarSet, Timeframe
from milodex.data.provider import DataProvider
from milodex.strategies._session_intraday import ET_TZ

UNIVERSE_REF = "universe.liquid_etf_core.v1"
TIMEFRAME = Timeframe.MINUTE_5
SYMBOLS = tuple(
    sorted(
        (
            "SPY",
            "QQQ",
            "IWM",
            "DIA",
            "XLB",
            "XLC",
            "XLE",
            "XLF",
            "XLI",
            "XLK",
            "XLP",
            "XLRE",
            "XLU",
            "XLV",
            "XLY",
            "TLT",
            "GLD",
        )
    )
)
MATCH_PARAMETERS = (
    "opening_range_minutes",
    "entry_window_minutes",
    "exit_minutes_before_close",
    "per_position_notional_pct",
)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _code_hash() -> str:
    source_root = Path(__file__).resolve().parents[1]
    return _hash_json(
        {
            str(path.relative_to(source_root)).replace("\\", "/"): _hash(path)
            for path in sorted(source_root.rglob("*.py"))
        }
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_snapshot(root: Path, *, expected_id: str | None = None) -> dict:
    """Reject missing, changed, or extra frozen inputs before and after a run."""
    root = Path(root)
    manifest = _read_json(root / "manifest.json")
    snapshot_id = _hash(root / "manifest.json")
    if expected_id is not None and snapshot_id != expected_id:
        raise ValueError("snapshot manifest hash changed")
    files = manifest["files"]
    actual = {
        str(path.relative_to(root)).replace("\\", "/") for path in root.rglob("*") if path.is_file()
    }
    if actual != set(files) | {"manifest.json"}:
        raise ValueError("snapshot file set changed")
    for name, expected in files.items():
        if _hash(root / name) != expected:
            raise ValueError(f"snapshot hash changed: {name}")
    return manifest


def create_snapshot(
    *,
    cache_dir: Path,
    config_dir: Path,
    calendar_broker: object,
    out_dir: Path,
    start: date,
    end: date,
    fetched_at: datetime,
    today: date | None = None,
) -> dict:
    """Copy local inputs only; never fetch or modify the source cache."""
    from milodex.strategies.loader import resolve_universe_ref

    if start != date(2022, 1, 1) or end < start:
        raise ValueError("official research window must begin 2022-01-01")
    symbols = tuple(resolve_universe_ref(UNIVERSE_REF, Path(config_dir) / "snapshot.yaml"))
    if symbols != SYMBOLS:
        raise ValueError("liquid ETF universe differs from canonical 17-symbol set")
    out_dir = Path(out_dir)
    if any(
        out_dir.resolve().is_relative_to(Path(source).resolve())
        for source in (cache_dir, config_dir)
    ):
        raise ValueError("snapshot output must be outside source cache and config directories")
    calendar_end = fetched_at.astimezone(ZoneInfo(ET_TZ)).date()
    calendar = calendar_broker.research_calendar(start, calendar_end, fetched_at)
    closed = [
        date.fromisoformat(item["date"])
        for item in calendar["sessions"]
        if datetime.combine(
            date.fromisoformat(item["date"]),
            datetime.strptime(item["close"], "%H:%M").time(),
            tzinfo=ZoneInfo(ET_TZ),
        )
        <= fetched_at
    ]
    if not closed or end != max(closed):
        raise ValueError("--end must equal latest completed Alpaca exchange session")
    out_dir.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copytree(config_dir, out_dir / "configs")
        (out_dir / "calendar.json").write_text(
            json.dumps(calendar, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        bar_dir = out_dir / "bars"
        bar_dir.mkdir()
        for symbol in symbols:
            source = Path(cache_dir) / CACHE_VERSION / TIMEFRAME.value / f"{symbol}.parquet"
            if not source.is_file():
                raise ValueError(f"missing cached bars: {symbol}")
            shutil.copy2(source, bar_dir / f"{symbol}.parquet")
        files = {
            str(path.relative_to(out_dir)).replace("\\", "/"): _hash(path)
            for path in sorted(out_dir.rglob("*"))
            if path.is_file()
        }
        manifest = {
            "schema_version": 1,
            "feed": "iex",
            "timeframe": TIMEFRAME.value,
            "universe_ref": UNIVERSE_REF,
            "symbols": list(symbols),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "settings": {"initial_equity": 100_000.0, "parallel": 1, "fail_fast": True},
            "code_hash": _code_hash(),
            "files": files,
        }
        manifest["symbol_hash"] = _hash_json(manifest["symbols"])
        manifest["calendar_hash"] = files["calendar.json"]
        manifest["window_hash"] = _hash_json({"start": manifest["start"], "end": manifest["end"]})
        manifest["settings_hash"] = _hash_json(manifest["settings"])
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
    except BaseException:
        shutil.rmtree(out_dir)
        raise
    preflight_error = None
    try:
        preflight_snapshot(out_dir, today=today)
    except Exception as exc:
        preflight_error = exc
    for path in out_dir.rglob("*"):
        if path.is_file():
            os.chmod(path, stat.S_IREAD)
    if preflight_error is not None:
        message = f"snapshot preflight held at {out_dir.resolve()}: {preflight_error}"
        raise ValueError(message) from preflight_error
    return manifest


def preflight_snapshot(root: Path, *, today: date | None = None) -> dict:
    """Require every frozen market session, every symbol, and aggregate 90% grids."""
    root = Path(root)
    manifest = verify_snapshot(root)
    if manifest["feed"] != "iex" or manifest["timeframe"] != TIMEFRAME.value:
        raise ValueError("snapshot feed/timeframe mismatch")
    if manifest["code_hash"] != _code_hash():
        raise ValueError("research code revision changed since snapshot creation")
    if (
        manifest["symbol_hash"] != _hash_json(manifest["symbols"])
        or manifest["window_hash"]
        != _hash_json({"start": manifest["start"], "end": manifest["end"]})
        or manifest["settings_hash"] != _hash_json(manifest["settings"])
        or manifest["calendar_hash"] != manifest["files"]["calendar.json"]
    ):
        raise ValueError("snapshot symbol/calendar/window/settings hash mismatch")
    symbols = manifest["symbols"]
    if tuple(symbols) != SYMBOLS:
        raise ValueError("snapshot differs from canonical 17-symbol set")
    start, end = date.fromisoformat(manifest["start"]), date.fromisoformat(manifest["end"])
    if start != date(2022, 1, 1):
        raise ValueError("official research window must begin 2022-01-01")
    calendar = _read_json(root / "calendar.json")
    if calendar.get("source") != "alpaca_exchange_calendar":
        raise ValueError("frozen Alpaca exchange calendar source required")
    if calendar.get("timezone") != ET_TZ or not calendar.get("fetched_at"):
        raise ValueError("calendar timezone and fetch timestamp required")
    fetched_at = datetime.fromisoformat(calendar["fetched_at"].replace("Z", "+00:00"))
    if fetched_at.tzinfo is None:
        raise ValueError("calendar fetch timestamp must include timezone")
    if calendar.get("window_start") != start.isoformat():
        raise ValueError("calendar requested window mismatch")
    if calendar.get("window_end") != fetched_at.astimezone(ZoneInfo(ET_TZ)).date().isoformat():
        raise ValueError("calendar requested end differs from fetch date")
    full_sessions = calendar["sessions"]
    full_dates = [date.fromisoformat(item["date"]) for item in full_sessions]
    if full_dates != sorted(set(full_dates)) or any(day < start for day in full_dates):
        raise ValueError("frozen exchange calendar is unordered or outside window")
    closed_dates = [
        date.fromisoformat(item["date"])
        for item in full_sessions
        if datetime.combine(
            date.fromisoformat(item["date"]),
            datetime.strptime(item["close"], "%H:%M").time(),
            tzinfo=ZoneInfo(ET_TZ),
        )
        <= fetched_at
    ]
    if not closed_dates or end != max(closed_dates):
        raise ValueError("snapshot end is not the latest completed exchange session")
    sessions = [item for item in full_sessions if date.fromisoformat(item["date"]) <= end]
    dates = [date.fromisoformat(item["date"]) for item in sessions]
    if dates != sorted(set(dates)) or not dates or dates[0] < start or dates[-1] != end:
        raise ValueError("calendar must list unique ordered sessions within the window")
    expected = {}
    for item, day in zip(sessions, dates, strict=True):
        if item.get("open") != "09:30" or item["close"] not in ("13:00", "16:00"):
            raise ValueError(f"unsupported close time: {day}")
        expected[day] = 42 if item["close"] == "13:00" else 78
    reference_day = today or datetime.now(ZoneInfo(ET_TZ)).date()
    if reference_day < dates[-1] or (reference_day - dates[-1]).days > 7:
        raise ValueError("latest complete common session is not within 7 calendar days")
    total_expected = sum(expected.values())
    coverage = {}
    bars_by_symbol = {}
    for symbol in symbols:
        df = pd.read_parquet(root / "bars" / f"{symbol}.parquet")
        bars_by_symbol[symbol] = BarSet(df)
        timestamp = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(ET_TZ)
        offsets = (timestamp.dt.hour - 9) * 60 + timestamp.dt.minute - 30
        grid_bars: dict[date, set[int]] = {}
        for day, offset in zip(timestamp.dt.date, offsets, strict=True):
            grid_size = expected.get(day)
            if grid_size is not None and 0 <= offset < grid_size * 5 and offset % 5 == 0:
                grid_bars.setdefault(day, set()).add(offset)
        observed = 0
        for day in expected:
            on_grid = grid_bars.get(day)
            if not on_grid:
                raise ValueError(f"{symbol} has no on-grid bars on {day}")
            observed += len(on_grid)
        coverage[symbol] = observed / total_expected
        if coverage[symbol] < 0.90:
            raise ValueError(f"{symbol} aggregate on-grid coverage {coverage[symbol]:.1%} < 90%")
    from milodex.data.intraday_readiness import scan_intraday_readiness

    readiness = scan_intraday_readiness(
        bars_by_symbol,
        timeframe_minutes=5,
        requested_start=start,
        requested_end=end,
        feed_label="iex",
    )
    return {
        "snapshot_id": _hash(root / "manifest.json"),
        "latest_session": dates[-1].isoformat(),
        "coverage": coverage,
        "sessions": len(dates),
        "session_dates": tuple(dates),
        "readiness": readiness.to_dict(),
    }


def candidate_strategy_ids(symbols: list[str], family: str, template: str) -> tuple[str, ...]:
    """The 17 candidate cells used to measure this hypothesis's null rates."""
    if tuple(symbols) != SYMBOLS:
        raise ValueError("candidate rate roster differs from canonical 17-symbol set")
    return tuple(f"{family}.{template}.{symbol.lower()}.v1" for symbol in symbols)


def _oos_window_provenance(windows: list[dict], total_days: int) -> list[dict]:
    """Retain test boundaries and ensure their persisted day counts add up."""
    if not windows:
        raise ValueError("candidate lacks OOS windows")
    normalized = []
    for window in windows:
        start = date.fromisoformat(window["test_start"])
        end = date.fromisoformat(window["test_end"])
        days = window["trading_days"]
        if start > end or not isinstance(days, int) or isinstance(days, bool) or days <= 0:
            raise ValueError("candidate has invalid OOS window provenance")
        normalized.append(
            {"test_start": start.isoformat(), "test_end": end.isoformat(), "trading_days": days}
        )
    if sum(window["trading_days"] for window in normalized) != total_days:
        raise ValueError("candidate OOS window days differ from aggregate")
    return normalized


def measured_candidate_rates(
    rows: tuple,
    event_store,
    expected_ids: tuple[str, ...],
    *,
    expected_snapshot_id: str | None = None,
) -> dict:
    """Measure round trips per OOS trading day, including skipped half-days.

    The approved Tier-1 null accepts the slight half-day under-match and
    clamps rates above one. A zero-trade candidate emits a warning.
    """
    verify_cells(rows, expected_ids)
    verify_oos_boundaries(event_store, rows)
    measured = {}
    for row in rows:
        persisted = event_store.get_backtest_run(row.run_id)
        if persisted is None or persisted.status != "completed":
            raise ValueError(f"candidate lacks completed run: {row.strategy_id}")
        if expected_snapshot_id and persisted.metadata.get("snapshot_id") != expected_snapshot_id:
            raise ValueError(f"candidate lacks source snapshot identity: {row.strategy_id}")
        aggregate = persisted.metadata.get("oos_aggregate", {})
        trips, days = aggregate.get("round_trip_count"), aggregate.get("trading_days")
        if (
            not isinstance(trips, int)
            or isinstance(trips, bool)
            or not isinstance(days, int)
            or isinstance(days, bool)
            or trips < 0
            or days <= 0
        ):
            raise ValueError(f"candidate lacks valid OOS rate inputs: {row.strategy_id}")
        windows = _oos_window_provenance(persisted.metadata["windows"], days)
        symbol = row.strategy_id.rsplit(".", 2)[-2].upper()
        if trips == 0:
            warnings.warn(
                f"{symbol} has zero candidate OOS round trips; random null will always stay flat",
                stacklevel=2,
            )
        measured[symbol] = {
            "candidate_id": row.strategy_id,
            "run_id": row.run_id,
            "round_trips": trips,
            "trading_days": days,
            "oos_windows": windows,
            "session_entry_rate": min(1.0, trips / days),
        }
    return measured


def create_matched_snapshot(
    *,
    source_root: Path,
    out_dir: Path,
    family: str,
    template: str,
    candidate_rows: tuple,
    event_store,
    today: date | None = None,
) -> dict:
    """Derive one hypothesis snapshot after measuring its 17 candidate OOS rates."""
    from milodex.research.fanout import generate_per_symbol_configs
    from milodex.strategies.loader import (
        load_strategy_config,
        resolve_config_path,
        resolve_universe_ref,
    )

    source_root, out_dir = Path(source_root), Path(out_dir)
    ready = preflight_snapshot(source_root, today=today)
    source = verify_snapshot(source_root, expected_id=ready["snapshot_id"])
    if out_dir.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("matched snapshot must be outside source snapshot")
    expected = candidate_strategy_ids(source["symbols"], family, template)
    rates = measured_candidate_rates(
        candidate_rows,
        event_store,
        expected,
        expected_snapshot_id=ready["snapshot_id"],
    )
    if set(rates) != set(SYMBOLS):
        raise ValueError("candidate rates must cover all 17 symbols")
    parameter_overrides = {}
    for symbol in SYMBOLS:
        candidate = load_strategy_config(
            resolve_config_path(rates[symbol]["candidate_id"], source_root / "configs")
        )
        resolved = candidate.universe or resolve_universe_ref(
            candidate.universe_ref, candidate.path
        )
        if resolved != (symbol,):
            raise ValueError(f"candidate config symbol mismatch: {symbol}")
        if not all(key in candidate.parameters for key in MATCH_PARAMETERS):
            raise ValueError(f"candidate lacks matched-exposure parameters: {symbol}")
        parameter_overrides[symbol] = {
            **{key: candidate.parameters[key] for key in MATCH_PARAMETERS},
            "session_entry_rate": rates[symbol]["session_entry_rate"],
        }
    out_dir.mkdir(parents=True, exist_ok=False)
    try:
        for name in source["files"]:
            target = out_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / name, target)
        configs = out_dir / "configs"
        base_id = "benchmark.random_matched_exposure.intraday.spy.v1"
        base_path = resolve_config_path(base_id, configs)
        with base_path.open(encoding="utf-8") as handle:
            base = yaml.safe_load(handle)
        baseline = base["strategy"]
        baseline["baseline_ref"] = rates["SPY"]["candidate_id"]
        baseline["parameters"].update(parameter_overrides["SPY"])
        with base_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(base, handle, sort_keys=False)
        generate_per_symbol_configs(
            base_config_path=base_path,
            universe_ref=UNIVERSE_REF,
            out_dir=configs,
            param_overrides=parameter_overrides,
        )
        for symbol in SYMBOLS:
            random_id = f"benchmark.random_matched_exposure.intraday.{symbol.lower()}.v1"
            config = load_strategy_config(resolve_config_path(random_id, configs))
            if config.baseline_ref != rates[symbol]["candidate_id"]:
                raise ValueError(f"random baseline_ref mismatch: {symbol}")
            if config.parameters["session_entry_rate"] != rates[symbol]["session_entry_rate"]:
                raise ValueError(f"random rate mismatch: {symbol}")
            if any(
                config.parameters[key] != parameter_overrides[symbol][key]
                for key in MATCH_PARAMETERS
            ):
                raise ValueError(f"random exposure parameters mismatch: {symbol}")
            resolved = config.universe or resolve_universe_ref(config.universe_ref, config.path)
            if resolved != (symbol,):
                raise ValueError(f"random baseline symbol mismatch: {symbol}")
        files = {
            str(path.relative_to(out_dir)).replace("\\", "/"): _hash(path)
            for path in sorted(out_dir.rglob("*"))
            if path.is_file()
        }
        manifest = {
            **source,
            "files": files,
            "calendar_hash": files["calendar.json"],
            "random_match": {
                "source_snapshot_id": ready["snapshot_id"],
                "candidate_family": family,
                "candidate_template": template,
                "rates": rates,
            },
        }
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        preflight_snapshot(out_dir, today=today)
        verify_random_match(out_dir, family, template)
        verify_snapshot(source_root, expected_id=ready["snapshot_id"])
        for path in out_dir.rglob("*"):
            if path.is_file():
                os.chmod(path, stat.S_IREAD)
        return manifest
    except BaseException:
        shutil.rmtree(out_dir)
        raise


def verify_random_match(root: Path, family: str, template: str) -> None:
    """Hold official cells when rate provenance, candidate, or symbol differs."""
    from milodex.strategies.loader import (
        load_strategy_config,
        resolve_config_path,
        resolve_universe_ref,
    )

    root = Path(root)
    manifest = verify_snapshot(root)
    match = manifest.get("random_match")
    if not match or (match["candidate_family"], match["candidate_template"]) != (family, template):
        raise ValueError("snapshot lacks candidate-matched random rate provenance")
    rates = match["rates"]
    if set(rates) != set(SYMBOLS):
        raise ValueError("random rates differ from canonical 17-symbol set")
    common_windows = None
    for symbol in SYMBOLS:
        item = rates[symbol]
        candidate_id = f"{family}.{template}.{symbol.lower()}.v1"
        if item["candidate_id"] != candidate_id:
            raise ValueError(f"random candidate mismatch: {symbol}")
        trips, days = item["round_trips"], item["trading_days"]
        windows = _oos_window_provenance(item["oos_windows"], days)
        if (
            not isinstance(trips, int)
            or isinstance(trips, bool)
            or not isinstance(days, int)
            or isinstance(days, bool)
            or days <= 0
            or item["oos_windows"] != windows
            or trips < 0
            or item["session_entry_rate"] != min(1.0, trips / days)
        ):
            raise ValueError(f"random rate provenance mismatch: {symbol}")
        if common_windows is None:
            common_windows = windows
        elif windows != common_windows:
            raise ValueError(f"random OOS windows differ across symbols: {symbol}")
        random_id = f"benchmark.random_matched_exposure.intraday.{symbol.lower()}.v1"
        config = load_strategy_config(resolve_config_path(random_id, root / "configs"))
        if config.baseline_ref != candidate_id:
            raise ValueError(f"random baseline_ref mismatch: {symbol}")
        if config.parameters["session_entry_rate"] != item["session_entry_rate"]:
            raise ValueError(f"random configured rate mismatch: {symbol}")
        candidate = load_strategy_config(resolve_config_path(candidate_id, root / "configs"))
        candidate_symbols = candidate.universe or resolve_universe_ref(
            candidate.universe_ref, candidate.path
        )
        if candidate_symbols != (symbol,):
            raise ValueError(f"candidate config symbol mismatch: {symbol}")
        if any(
            key not in candidate.parameters or config.parameters[key] != candidate.parameters[key]
            for key in MATCH_PARAMETERS
        ):
            raise ValueError(f"random exposure parameters mismatch: {symbol}")
        resolved = config.universe or resolve_universe_ref(config.universe_ref, config.path)
        if resolved != (symbol,):
            raise ValueError(f"random baseline symbol mismatch: {symbol}")


def required_strategy_ids(
    config_dir: Path, symbols: list[str], family: str, template: str
) -> tuple[str, ...]:
    """Resolve the complete 17-candidate/52-null configuration roster."""
    from milodex.strategies.loader import (
        load_strategy_config,
        resolve_config_path,
        resolve_universe_ref,
    )

    verify_random_match(Path(config_dir).parent, family, template)
    if tuple(symbols) != SYMBOLS:
        raise ValueError("screen roster differs from canonical 17-symbol set")

    spy_path = resolve_config_path(f"{family}.{template}.spy.v1", config_dir)
    version = load_strategy_config(spy_path).version
    result = []
    for symbol in symbols:
        suffix = f"{symbol.lower()}.v1"
        result.append(f"{family}.{template}.{symbol.lower()}.v{version}")
        result.extend(
            f"benchmark.{kind}.{suffix}"
            for kind in (
                "unconditional_intraday_long",
                "time_of_day_null",
                "random_matched_exposure.intraday",
            )
        )
        if symbol == "SPY":
            result.append("benchmark.no_trade.spy.v1")
    for strategy_id in result:
        config = load_strategy_config(resolve_config_path(strategy_id, config_dir))
        symbol = strategy_id.rsplit(".", 2)[-2].upper()
        resolved = config.universe or resolve_universe_ref(config.universe_ref, config.path)
        if resolved != (symbol,):
            raise ValueError(f"snapshot cell symbol mismatch: {strategy_id}")
        if config.tempo.get("bar_size") != TIMEFRAME.value:
            raise ValueError(f"snapshot cell uses a different timeframe: {strategy_id}")
        if int(config.backtest.get("walk_forward_windows", 4)) != 4:
            raise ValueError(f"snapshot cell uses different walk-forward windows: {strategy_id}")
    if len(result) != 69:
        raise ValueError("expected exactly 69 candidate and baseline cells")
    return tuple(result)


def verify_cells(rows: tuple, expected_ids: tuple[str, ...]) -> None:
    """Hold registry append unless every exact cell completed durably."""
    if len(rows) != len(expected_ids) or {row.strategy_id for row in rows} != set(expected_ids):
        raise ValueError("screen roster differs from frozen candidate and baseline cells")
    for row in rows:
        if row.error or not row.run_id:
            raise ValueError(f"screen cell failed: {row.strategy_id}")


def verify_oos_boundaries(event_store, rows: tuple) -> None:
    """Require all 69 persisted runs to use identical walk-forward date splits."""
    common = None
    for row in rows:
        persisted = event_store.get_backtest_run(row.run_id)
        if persisted is None or persisted.status != "completed":
            raise ValueError(f"screen cell lacks completed run: {row.strategy_id}")
        windows = persisted.metadata.get("windows")
        if not windows:
            raise ValueError(f"screen cell lacks OOS windows: {row.strategy_id}")
        boundaries = tuple(
            (w["train_start"], w["train_end"], w["test_start"], w["test_end"]) for w in windows
        )
        if common is None:
            common = boundaries
        elif boundaries != common:
            raise ValueError(f"screen cell has divergent OOS boundaries: {row.strategy_id}")


class SnapshotDataProvider(DataProvider):
    """Backtest source that can only read frozen snapshot bars."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.manifest = verify_snapshot(self.root)

    def get_bars(
        self, symbols: list[str], timeframe: Timeframe, start: date, end: date
    ) -> dict[str, BarSet]:
        if timeframe != TIMEFRAME or set(symbols) - set(self.manifest["symbols"]):
            raise ValueError("request outside frozen snapshot")
        result = {}
        for symbol in symbols:
            df = pd.read_parquet(self.root / "bars" / f"{symbol}.parquet")
            days = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(ET_TZ).dt.date
            result[symbol] = BarSet(df.loc[days.between(start, end)])
        return result

    def get_latest_bar(self, symbol: str):
        raise ValueError("snapshot provider is for historical backtests only")
