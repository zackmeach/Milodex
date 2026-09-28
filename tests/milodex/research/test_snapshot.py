"""Offline snapshot rejects missing sessions and never builds Alpaca clients."""

import hashlib
import json
import os
import shutil
import stat
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from milodex.cli._shared import CommandContext
from milodex.cli.commands.research import _snapshot_context
from milodex.data.models import Timeframe
from milodex.research.snapshot import (
    SYMBOLS,
    SnapshotDataProvider,
    candidate_strategy_ids,
    create_matched_snapshot,
    create_snapshot,
    measured_candidate_rates,
    preflight_snapshot,
    required_strategy_ids,
    verify_cells,
    verify_oos_boundaries,
    verify_random_match,
    verify_snapshot,
)


def test_snapshot_preflight_and_offline_provider(tmp_path, monkeypatch):
    symbols = (
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
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "universe_liquid_etf_core_v1.yaml").write_text(
        "universe:\n  id: universe.liquid_etf_core.v1\n  etfs:\n"
        + "".join(f"    - {symbol}\n" for symbol in symbols),
        encoding="utf-8",
    )
    project_configs = Path(__file__).resolve().parents[3] / "configs"
    for name in (
        "gap_continuation_intraday_spy_v1.yaml",
        "universe_spy_only_v1.yaml",
        "risk_defaults.yaml",
    ):
        shutil.copy2(project_configs / name, configs / name)
    start, end, fetched_at = date(2022, 1, 1), date(2022, 1, 3), datetime(2022, 1, 4, tzinfo=UTC)
    calendar = {
        "source": "alpaca_exchange_calendar",
        "fetched_at": fetched_at.isoformat(),
        "timezone": "America/New_York",
        "window_start": start.isoformat(),
        "window_end": "2022-01-03",
        "sessions": [{"date": end.isoformat(), "open": "09:30", "close": "16:00"}],
    }

    class CalendarBroker:
        def research_calendar(self, requested_start, requested_end, now):
            assert requested_start == start
            assert requested_end == date(2022, 1, 3)
            assert now == fetched_at
            return calendar

    calendar_broker = CalendarBroker()
    cache = tmp_path / "cache" / "v3" / "5Min"
    cache.mkdir(parents=True)
    timestamps = pd.date_range("2022-01-03 09:30", periods=78, freq="5min", tz="America/New_York")
    frame = pd.DataFrame(
        {
            "timestamp": timestamps.tz_convert("UTC"),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1,
        }
    )
    for symbol in symbols:
        frame.to_parquet(cache / f"{symbol}.parquet")
    root = tmp_path / "snapshot"
    create_snapshot(
        cache_dir=tmp_path / "cache",
        config_dir=configs,
        calendar_broker=calendar_broker,
        out_dir=root,
        start=start,
        end=end,
        fetched_at=fetched_at,
        today=date(2022, 1, 4),
    )
    monkeypatch.setattr(
        "milodex.data.alpaca_provider.AlpacaDataProvider.__init__",
        lambda self: pytest.fail("Alpaca provider constructed"),
    )
    provider = SnapshotDataProvider(root)
    assert len(provider.get_bars(["SPY"], Timeframe.MINUTE_5, end, end)["SPY"]) == 78
    assert preflight_snapshot(root, today=date(2022, 1, 4))["coverage"]["SPY"] == 1

    def unavailable(*args, **kwargs):
        pytest.fail("unexpected live factory")

    ctx = CommandContext(
        get_execution_service=unavailable,
        get_strategy_runner=unavailable,
        get_backtest_engine=unavailable,
        get_event_store=unavailable,
        broker_factory=unavailable,
        data_provider_factory=unavailable,
        get_trading_mode=unavailable,
        config_dir=configs,
        locks_dir=tmp_path,
    )
    snapshot_id = preflight_snapshot(root, today=date(2022, 1, 4))["snapshot_id"]
    scratch_db = str((tmp_path / f"research-{snapshot_id[:16]}.sqlite").resolve())
    offline = _snapshot_context(ctx, root, snapshot_id, scratch_db)
    engine = offline.get_backtest_engine("gap.gap_continuation.intraday.spy.v1")
    assert len(engine.prefetch_bars(end, end, timeframe=Timeframe.MINUTE_5)["SPY"]) == 78
    with pytest.raises(ValueError, match="7 calendar days"):
        preflight_snapshot(root, today=date(2022, 1, 11))

    frame.iloc[:69].to_parquet(cache / "SPY.parquet")
    rejected = tmp_path / "rejected"
    with pytest.raises(ValueError, match="snapshot preflight held") as held:
        create_snapshot(
            cache_dir=tmp_path / "cache",
            config_dir=configs,
            calendar_broker=calendar_broker,
            out_dir=rejected,
            start=start,
            end=end,
            fetched_at=fetched_at,
            today=date(2022, 1, 4),
        )
    assert str(rejected) in str(held.value)
    assert "aggregate on-grid coverage" in str(held.value)
    assert verify_snapshot(rejected)["calendar_hash"]
    assert not (rejected / "manifest.json").stat().st_mode & stat.S_IWRITE
    with pytest.raises(ValueError, match="aggregate on-grid coverage"):
        preflight_snapshot(rejected, today=date(2022, 1, 4))

    frame.iloc[1:-1].to_parquet(cache / "SPY.parquet")
    warned = tmp_path / "warned"
    create_snapshot(
        cache_dir=tmp_path / "cache",
        config_dir=configs,
        calendar_broker=calendar_broker,
        out_dir=warned,
        start=start,
        end=end,
        fetched_at=fetched_at,
        today=date(2022, 1, 4),
    )
    issue_codes = preflight_snapshot(warned, today=date(2022, 1, 4))["readiness"]["issue_codes"]
    assert "intraday_missing_session_open_bar" in issue_codes
    assert "intraday_missing_session_close_bar" in issue_codes


def test_verify_cells_holds_on_missing_or_failed_baseline():
    expected = ("candidate", "baseline")
    with pytest.raises(ValueError, match="roster"):
        verify_cells((SimpleNamespace(strategy_id="candidate", run_id="1", error=None),), expected)
    with pytest.raises(ValueError, match="failed"):
        verify_cells(
            (
                SimpleNamespace(strategy_id="candidate", run_id="1", error=None),
                SimpleNamespace(strategy_id="baseline", run_id=None, error="broken"),
            ),
            expected,
        )


def test_oos_boundaries_must_match_across_cells():
    first = {
        "train_start": "2022-01-03",
        "train_end": "2022-01-04",
        "test_start": "2022-01-05",
        "test_end": "2022-01-06",
    }
    second = {**first, "test_start": "2022-01-06"}
    runs = {
        "1": SimpleNamespace(status="completed", metadata={"windows": [first]}),
        "2": SimpleNamespace(status="completed", metadata={"windows": [second]}),
    }

    class Store:
        def get_backtest_run(self, run_id):
            return runs[run_id]

    rows = (
        SimpleNamespace(strategy_id="candidate", run_id="1"),
        SimpleNamespace(strategy_id="baseline", run_id="2"),
    )
    with pytest.raises(ValueError, match="divergent OOS"):
        verify_oos_boundaries(Store(), rows)


def test_candidate_rate_uses_round_trips_and_warns_on_zero():
    strategy_id = "gap.gap_continuation.intraday.spy.v1"
    row = SimpleNamespace(strategy_id=strategy_id, run_id="1", error=None, trade_count=20)
    # July 3 is a half-day, retained in the approved all-OOS-days denominator.
    run = SimpleNamespace(
        status="completed",
        metadata={
            "snapshot_id": "source-id",
            "windows": [
                {
                    "train_start": "2023-06-30",
                    "train_end": "2023-06-30",
                    "test_start": "2023-06-30",
                    "test_end": "2023-07-05",
                    "trading_days": 3,
                }
            ],
            "oos_aggregate": {"round_trip_count": 0, "trading_days": 3},
        },
    )
    store = SimpleNamespace(get_backtest_run=lambda _: run)
    with pytest.warns(UserWarning, match="zero candidate OOS round trips"):
        measured = measured_candidate_rates(
            (row,), store, (strategy_id,), expected_snapshot_id="source-id"
        )
    assert measured["SPY"]["session_entry_rate"] == 0.0
    run.metadata["oos_aggregate"]["round_trip_count"] = 2
    measured = measured_candidate_rates((row,), store, (strategy_id,))
    assert measured["SPY"]["session_entry_rate"] == 2 / 3
    assert measured["SPY"]["trading_days"] == 3
    run.metadata["windows"][0]["trading_days"] = 2
    with pytest.raises(ValueError, match="window days differ"):
        measured_candidate_rates((row,), store, (strategy_id,))
    run.metadata["windows"][0]["trading_days"] = 3
    run.metadata["oos_aggregate"]["round_trip_count"] = 4
    measured = measured_candidate_rates((row,), store, (strategy_id,))
    assert measured["SPY"]["session_entry_rate"] == 1.0
    with pytest.raises(ValueError, match="source snapshot identity"):
        measured_candidate_rates((row,), store, (strategy_id,), expected_snapshot_id="wrong")


def test_hypothesis_snapshot_freezes_measured_random_rates(tmp_path):
    project_configs = Path(__file__).resolve().parents[3] / "configs"
    configs = tmp_path / "configs"
    shutil.copytree(project_configs, configs)
    start, end = date(2022, 1, 1), date(2022, 1, 3)
    fetched_at = datetime(2022, 1, 4, tzinfo=UTC)
    calendar = {
        "source": "alpaca_exchange_calendar",
        "fetched_at": fetched_at.isoformat(),
        "timezone": "America/New_York",
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "sessions": [{"date": end.isoformat(), "open": "09:30", "close": "16:00"}],
    }
    broker = SimpleNamespace(research_calendar=lambda *_: calendar)
    cache = tmp_path / "cache" / "v3" / "5Min"
    cache.mkdir(parents=True)
    timestamps = pd.date_range("2022-01-03 09:30", periods=78, freq="5min", tz="America/New_York")
    frame = pd.DataFrame(
        {
            "timestamp": timestamps.tz_convert("UTC"),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1,
        }
    )
    for symbol in SYMBOLS:
        frame.to_parquet(cache / f"{symbol}.parquet")
    source = tmp_path / "source"
    create_snapshot(
        cache_dir=tmp_path / "cache",
        config_dir=configs,
        calendar_broker=broker,
        out_dir=source,
        start=start,
        end=end,
        fetched_at=fetched_at,
        today=date(2022, 1, 4),
    )
    source_id = preflight_snapshot(source, today=date(2022, 1, 4))["snapshot_id"]
    expected = candidate_strategy_ids(list(SYMBOLS), "gap", "gap_continuation.intraday")
    windows = [
        {
            "train_start": "2022-01-03",
            "train_end": "2022-01-03",
            "test_start": "2022-01-03",
            "test_end": "2022-01-03",
            "trading_days": 1,
        }
    ]
    rows = tuple(
        SimpleNamespace(strategy_id=strategy_id, run_id=str(index), error=None)
        for index, strategy_id in enumerate(expected)
    )
    runs = {
        row.run_id: SimpleNamespace(
            status="completed",
            metadata={
                "snapshot_id": source_id,
                "windows": windows,
                "oos_aggregate": {"round_trip_count": 1, "trading_days": 1},
            },
        )
        for row in rows
    }
    store = SimpleNamespace(get_backtest_run=lambda run_id: runs[run_id])
    rates = measured_candidate_rates(rows, store, expected, expected_snapshot_id=source_id)
    assert rates["SPY"]["session_entry_rate"] == 1.0
    assert rates["SPY"]["round_trips"] == 1
    assert rates["SPY"]["trading_days"] == 1
    matched = tmp_path / "matched"
    create_matched_snapshot(
        source_root=source,
        out_dir=matched,
        family="gap",
        template="gap_continuation.intraday",
        candidate_rows=rows,
        event_store=store,
        today=date(2022, 1, 4),
    )
    assert (
        len(
            required_strategy_ids(
                matched / "configs", list(SYMBOLS), "gap", "gap_continuation.intraday"
            )
        )
        == 69
    )
    spy_random = yaml.safe_load(
        (matched / "configs" / "bench_random_matched_exposure_long_spy_v1.yaml").read_text(
            encoding="utf-8"
        )
    )["strategy"]
    assert spy_random["baseline_ref"] == "gap.gap_continuation.intraday.spy.v1"
    assert spy_random["parameters"]["session_entry_rate"] == 1.0
    assert spy_random["parameters"]["opening_range_minutes"] == 15
    assert spy_random["parameters"]["entry_window_minutes"] == 60
    assert spy_random["parameters"]["seed"] == 20260619
    with pytest.raises(ValueError, match="candidate-matched"):
        verify_random_match(matched, "meanrev", "rsi2.intraday")

    manifest_path = matched / "manifest.json"
    os.chmod(manifest_path, stat.S_IWRITE | stat.S_IREAD)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["random_match"]["rates"]["SPY"]["trading_days"] = 2
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="OOS window days differ from aggregate"):
        verify_random_match(matched, "gap", "gap_continuation.intraday")
    manifest["random_match"]["rates"]["SPY"]["trading_days"] = 1

    for name, other_symbol in (
        ("gap_continuation_intraday_xlf_v1.yaml", "SPY"),
        ("bench_unconditional_intraday_long_xlf_v1.yaml", "SPY"),
        ("bench_time_of_day_null_xlf_v1.yaml", "SPY"),
        ("bench_no_trade_spy_v1.yaml", "XLF"),
    ):
        config_path = matched / "configs" / name
        original = config_path.read_bytes()
        os.chmod(config_path, stat.S_IWRITE | stat.S_IREAD)
        config = yaml.safe_load(original)
        config["strategy"].pop("universe_ref", None)
        config["strategy"]["universe"] = [other_symbol]
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        key = f"configs/{name}"
        manifest["files"][key] = hashlib.sha256(config_path.read_bytes()).hexdigest()
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        with pytest.raises(ValueError, match="symbol mismatch"):
            required_strategy_ids(
                matched / "configs", list(SYMBOLS), "gap", "gap_continuation.intraday"
            )
        config_path.write_bytes(original)
        manifest["files"][key] = hashlib.sha256(original).hexdigest()

    random_path = matched / "configs" / "bench_random_matched_exposure_long_xlf_v1.yaml"
    os.chmod(random_path, stat.S_IWRITE | stat.S_IREAD)
    data = yaml.safe_load(random_path.read_text(encoding="utf-8"))
    data["strategy"]["baseline_ref"] = "meanrev.rsi2.intraday.xlf.v1"
    random_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    manifest["files"]["configs/bench_random_matched_exposure_long_xlf_v1.yaml"] = hashlib.sha256(
        random_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="baseline_ref mismatch: XLF"):
        verify_random_match(matched, "gap", "gap_continuation.intraday")
    data["strategy"]["baseline_ref"] = "gap.gap_continuation.intraday.xlf.v1"
    data["strategy"]["universe"] = ["SPY"]
    random_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    manifest["files"]["configs/bench_random_matched_exposure_long_xlf_v1.yaml"] = hashlib.sha256(
        random_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="symbol mismatch: XLF"):
        verify_random_match(matched, "gap", "gap_continuation.intraday")
