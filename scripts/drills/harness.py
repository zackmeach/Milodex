"""Shared infrastructure for the M4 fault-injection drill harness.

Provides:

- :class:`ScratchEnv` — a tempfile-backed throwaway environment (data / logs /
  locks / cache dirs + a scratch ``milodex.db``). Drills NEVER touch the real
  ``data/``, ``logs/``, ``market_cache/``, or ``.env`` — every path routes here
  via the ``MILODEX_*`` env overrides (``src/milodex/config.py``).
- :func:`run_cli` — invoke the real CLI in a subprocess with the environment
  pointed at a scratch env, ``.env``-free (cwd is a throwaway dir with no
  ``.env`` anywhere up the tree, and all ``ALPACA_*`` keys are stripped from the
  inherited environment). Bogus credentials are injected explicitly per cell.
- :class:`DrillResult` — a per-cell PASS/FAIL record with the verbatim operator
  output and the durable record that was queried.
- small in-process stubs (broker / provider / bar builder) mirroring
  ``tests/milodex/strategies/test_runner.py`` so the stale-data cell can drive a
  real :class:`~milodex.strategies.runner.StrategyRunner` poll.

Safety invariants enforced here so no individual cell can violate them:

* Every scratch path is under ``tempfile.mkdtemp`` — never the repo tree.
* The subprocess environment strips ``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY``
  / ``TRADING_MODE`` and every ``MILODEX_*`` before re-injecting only what the
  cell asked for, so a real credential in the parent process can never leak
  into a drill.
* ``PYTHONPATH`` is prepended with the worktree ``src`` so the subprocess runs
  the worktree's code, matching the parent harness's shadowed install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# --- worktree layout -------------------------------------------------------

_HARNESS_FILE = Path(__file__).resolve()
REPO_ROOT = _HARNESS_FILE.parents[2]
SRC_DIR = REPO_ROOT / "src"


# --- scratch environment ---------------------------------------------------


@dataclass
class ScratchEnv:
    """A throwaway environment: isolated data / logs / locks / cache + a DB path.

    All directories are created eagerly. ``db_path`` is where the CLI (and the
    in-process cells) resolve ``milodex.db`` via ``MILODEX_DATA_DIR``.
    """

    root: Path
    data_dir: Path
    logs_dir: Path
    locks_dir: Path
    cache_dir: Path
    cwd: Path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "milodex.db"

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def provision_scratch(prefix: str = "milodex-drill-") -> ScratchEnv:
    """Create an isolated scratch env under the system temp dir.

    The ``cwd`` subdir is where drill subprocesses run so that
    ``dotenv.load_dotenv`` (called at ``milodex.config`` import) walks up from a
    location with no ``.env`` on any ancestor — the real repo ``.env`` is never
    loaded.
    """
    root = Path(tempfile.mkdtemp(prefix=prefix))
    data_dir = root / "data"
    logs_dir = root / "logs"
    locks_dir = root / "data" / "locks"
    cache_dir = root / "market_cache"
    cwd = root / "cwd"
    for path in (data_dir, logs_dir, locks_dir, cache_dir, cwd):
        path.mkdir(parents=True, exist_ok=True)
    return ScratchEnv(
        root=root,
        data_dir=data_dir,
        logs_dir=logs_dir,
        locks_dir=locks_dir,
        cache_dir=cache_dir,
        cwd=cwd,
    )


# --- CLI subprocess runner -------------------------------------------------

# Well-formed but deliberately-bogus Alpaca credentials. Long enough to look
# real to the client constructor; guaranteed never to authenticate.
BOGUS_API_KEY = "PKDRILLBOGUSKEY000000"
BOGUS_SECRET_KEY = "drillBogusSecret0000000000000000000000AAA"


def _build_subprocess_env(
    scratch: ScratchEnv,
    *,
    creds: str,
    trading_mode: str | None,
) -> dict[str, str]:
    """Assemble a hermetic subprocess environment for a scratch env.

    ``creds`` is one of ``"bogus"`` (inject the well-formed bogus keys),
    ``"none"`` (leave ``ALPACA_*`` unset — the missing-credentials path), or
    ``"blank"`` (set them to empty strings). ``trading_mode`` sets
    ``TRADING_MODE`` when provided.
    """
    env = dict(os.environ)
    # Strip anything that could leak the real account or redirect paths.
    for key in (
        "ALPACA_API_KEY",
        "ALPACA_SECRET_KEY",
        "TRADING_MODE",
        "MILODEX_DATA_DIR",
        "MILODEX_LOG_DIR",
        "MILODEX_LOCKS_DIR",
        "MILODEX_CACHE_DIR",
    ):
        env.pop(key, None)

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{SRC_DIR}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(SRC_DIR)
    )
    env["MILODEX_DATA_DIR"] = str(scratch.data_dir)
    env["MILODEX_LOG_DIR"] = str(scratch.logs_dir)
    env["MILODEX_LOCKS_DIR"] = str(scratch.locks_dir)
    env["MILODEX_CACHE_DIR"] = str(scratch.cache_dir)

    if creds == "bogus":
        env["ALPACA_API_KEY"] = BOGUS_API_KEY
        env["ALPACA_SECRET_KEY"] = BOGUS_SECRET_KEY
    elif creds == "blank":
        env["ALPACA_API_KEY"] = ""
        env["ALPACA_SECRET_KEY"] = ""
    # creds == "none": leave unset.

    if trading_mode is not None:
        env["TRADING_MODE"] = trading_mode
    return env


@dataclass
class CliRun:
    """Result of one CLI subprocess invocation."""

    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()


def run_cli(
    args: list[str],
    scratch: ScratchEnv,
    *,
    creds: str = "bogus",
    trading_mode: str | None = "paper",
    timeout: float = 60.0,
) -> CliRun:
    """Invoke ``python -m milodex.cli.main <args>`` against a scratch env.

    Runs in ``scratch.cwd`` (no ``.env`` up-tree) with the environment pointed at
    the scratch data/logs/locks/cache dirs and the requested credential posture.
    Never raises on a nonzero exit — the caller asserts on ``returncode``.
    """
    argv = [sys.executable, "-m", "milodex.cli.main", *args]
    env = _build_subprocess_env(scratch, creds=creds, trading_mode=trading_mode)
    completed = subprocess.run(
        argv,
        cwd=str(scratch.cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return CliRun(
        argv=args,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


# --- dead / live PID helpers ----------------------------------------------


def spawn_dead_pid() -> int:
    """Spawn a Python process, wait for it to exit, and return its (now-dead) PID.

    The advisory-lock liveness check (``advisory_lock.holder_is_live``) verifies
    PID existence, so a genuinely-exited PID reads as *not live* — the phantom /
    moot / dead-runner drills need exactly that.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    proc.wait(timeout=30)
    return proc.pid


def spawn_live_process(sleep_seconds: int = 600) -> subprocess.Popen[bytes]:
    """Spawn a long-lived dummy Python process the caller must terminate.

    Used by the wedged-stop drill to make the runner lock read as genuinely
    live. ALWAYS call ``.terminate()`` / ``.wait()`` in a ``finally``.
    """
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({sleep_seconds})"])


def write_lock_file(
    locks_dir: Path,
    strategy_id: str,
    *,
    pid: int,
    started_at: datetime,
    holder_name: str = "milodex strategy run drill",
) -> Path:
    """Write an advisory-lock file in the exact identity format the runner expects.

    Mirrors ``AdvisoryLock.acquire`` (``core/advisory_lock.py``): a JSON blob
    keyed by ``pid`` / ``hostname`` / ``holder_name`` / ``started_at`` at
    ``{locks_dir}/{runner_lock_name(strategy_id)}.lock``.
    """
    import json
    import platform

    from milodex.strategies.paper_runner_control import runner_lock_name

    path = locks_dir / f"{runner_lock_name(strategy_id)}.lock"
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "hostname": platform.node(),
                "holder_name": holder_name,
                "started_at": started_at.isoformat(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


# --- in-process runner stubs (mirror tests/milodex/strategies/test_runner.py) ---


def build_barset(closes: list[float]):
    """Build a daily :class:`BarSet` ending today at 21:00 UTC (post-close)."""
    import pandas as pd

    from milodex.data.models import BarSet

    end = datetime.now(tz=UTC).replace(hour=21, minute=0, second=0, microsecond=0)
    timestamps = pd.date_range(end=end, periods=len(closes), freq="D", tz=UTC)
    return BarSet(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": [1_000_000] * len(closes),
                "vwap": closes,
            }
        )
    )


class StubProvider:
    """Data provider stub returning fixed bars (mirrors the runner-test double)."""

    def __init__(self, bars_by_symbol: dict[str, object]) -> None:
        self._bars_by_symbol = bars_by_symbol

    def get_bars(self, symbols, timeframe, start, end):  # noqa: ARG002
        return {symbol: self._bars_by_symbol[symbol] for symbol in symbols}

    def get_latest_bar(self, symbol: str):
        return self._bars_by_symbol[symbol].latest()


class StubBroker:
    """Broker stub for the in-process runner poll (mirrors the runner-test double)."""

    def __init__(self, *, account, market_open: bool = False) -> None:
        self.account = account
        self._market_open = market_open
        self._symbol_tradable = True

    def get_account(self):
        return self.account

    def get_positions(self):
        return []

    def get_position(self, symbol: str):  # noqa: ARG002
        return None

    def get_orders(self, status: str = "all", limit: int = 100):  # noqa: ARG002
        return []

    def is_market_open(self) -> bool:
        return self._market_open

    def is_symbol_tradable(self, symbol: str) -> bool:  # noqa: ARG002
        return self._symbol_tradable

    def latest_completed_session(self, now):
        return now.date()

    def cancel_all_orders(self):
        return []


REGIME_CONFIG_YAML = """
strategy:
  id: "regime.daily.sma200_rotation.spy_shy.v1"
  family: "regime"
  template: "daily.sma200_rotation"
  variant: "spy_shy"
  version: 1
  description: "Drill stale-data strategy"
  enabled: true
  universe:
    - "SPY"
    - "SHY"
  parameters:
    ma_filter_length: 3
    risk_on_symbol: "SPY"
    risk_off_symbol: "SHY"
    allocation_pct: 1.0
  tempo:
    bar_size: "1D"
    min_hold_days: 1
    max_hold_days: 5
  risk:
    max_position_pct: 1.0
    max_positions: 1
    daily_loss_cap_pct: 0.05
    stop_loss_pct: 0.10
  stage: "paper"
  backtest:
    slippage_pct: 0.001
    commission_per_trade: 0.0
    min_trades_required: 30
  disable_conditions_additional: []
""".strip()

RISK_DEFAULTS_YAML = """
kill_switch:
  enabled: true
  max_drawdown_pct: 0.10
  require_manual_reset: true
portfolio:
  max_single_position_pct: 1.00
  max_concurrent_positions: 3
  max_total_exposure_pct: 0.85
daily_limits:
  max_daily_loss_pct: 0.03
  max_trades_per_day: 20
order_safety:
  max_order_value_pct: 1.00
  duplicate_order_window_seconds: 60
  max_data_staleness_seconds: 999999
""".strip()


def write_regime_config(config_dir: Path) -> Path:
    """Write the drill regime strategy config + return the config dir path."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "regime_runner.yaml").write_text(REGIME_CONFIG_YAML, encoding="utf-8")
    return config_dir


# --- drill result ----------------------------------------------------------


@dataclass
class DrillResult:
    """Per-cell verdict with the verbatim evidence it was judged on."""

    name: str
    status: str  # "PASS" | "FAIL" | "ERROR"
    fault: str = ""
    operator_output: str = ""
    durable_record: str = ""
    detail: str = ""
    slow: bool = False
    subcases: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "PASS"


def _trim(text: str, *, keep: tuple[str, ...] = (), max_lines: int = 24) -> str:
    """Trim verbatim CLI output to the load-bearing lines for the report.

    Keeps lines containing any of ``keep`` (case-insensitive) plus a small head,
    so the evidence stays verbatim but readable.
    """
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if not keep:
        return "\n".join(lines[:max_lines])
    lowered = tuple(k.lower() for k in keep)
    picked = [ln for ln in lines if any(k in ln.lower() for k in lowered)]
    return "\n".join(picked[:max_lines]) if picked else "\n".join(lines[:max_lines])
